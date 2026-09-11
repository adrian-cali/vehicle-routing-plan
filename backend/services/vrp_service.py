"""
VRP orchestration service.

Contains all business logic for plan-ahead, job creation,
assignment retrieval, and preview. No direct DB access — delegates
to repositories.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from core.config import get_settings
from core.exceptions import NotFoundError, ValidationError
from core.logging import get_logger
from domain.vrp import (
    AssignmentResponse,
    AssignmentRoute,
    AssignmentTask,
    CreateJobResponse,
    FieldmanOverviewItem,
    H3CellInfo,
    H3GridResponse,
    H3ResolutionInfo,
    JobMetricsResponse,
    JobStatusResponse,
    OverviewResponse,
    PlanAheadResponse,
    TaskOverviewItem,
    TaskSummaryResponse,
)
from repositories.audit_repository import AuditRepository
from repositories.fieldman_repository import FieldmanRepository
from repositories.task_repository import TaskRepository
from repositories.vrp_repository import VRPJobRepository
from services.cache_service import CacheService, CacheTTL
from services.osrm_service import OSRMService

logger = get_logger(__name__)
settings = get_settings()


class VRPService:
    """High-level VRP business logic."""

    def __init__(
        self,
        task_repo: TaskRepository,
        fieldman_repo: FieldmanRepository,
        vrp_repo: VRPJobRepository,
        audit_repo: AuditRepository,
        cache: Optional[CacheService] = None,
    ) -> None:
        self._tasks = task_repo
        self._fieldmen = fieldman_repo
        self._vrp = vrp_repo
        self._audit = audit_repo
        self._cache = cache

    # ── H3 Grid ──────────────────────────────────────────────────────

    async def compute_h3_grid(
        self,
        resolution: int = 9,
        include_tasks: bool = True,
        include_fieldmen: bool = True,
    ) -> H3GridResponse:
        """Compute H3 hexagonal grid with task/fieldman counts (server-side)."""
        from services.h3_utils import cell_to_boundary, cell_to_center, latlng_to_h3

        cache_key = f"h3:grid:{resolution}:{include_tasks}:{include_fieldmen}"
        if self._cache:
            cached = await self._cache.get(cache_key)
            if cached:
                return H3GridResponse(**cached)

        cell_data: Dict[str, Dict[str, Any]] = {}

        def _get(obj: Any, *keys: str) -> Any:
            """Handle dicts and Pydantic models uniformly."""
            if isinstance(obj, dict):
                getter = obj.get
            else:
                getter = lambda k: getattr(obj, k, None)
            for key in keys:
                val = getter(key)
                if val is not None:
                    return val
            return None

        if include_tasks:
            # Use a lightweight coordinate-only fetch to reduce DB payload
            try:
                tasks = await self._tasks.fetch_all_coords()
            except Exception:
                tasks = await self._tasks.list_tasks()
            for t in tasks:
                lat_raw = _get(t, "latitude", "lat", "home_lat")
                lng_raw = _get(t, "longitude", "lng", "home_long")
                if lat_raw is None or lng_raw is None:
                    continue
                try:
                    lat = float(lat_raw)
                    lng = float(lng_raw)
                except (TypeError, ValueError):
                    continue
                cell = latlng_to_h3(lat, lng, resolution)
                if cell not in cell_data:
                    boundary = cell_to_boundary(cell)
                    center = cell_to_center(cell)
                    cell_data[cell] = {
                        "cell": cell,
                        "boundary": boundary,
                        "task_count": 0,
                        "fieldman_count": 0,
                        "center_lat": center[0],
                        "center_lng": center[1],
                    }
                cell_data[cell]["task_count"] += 1

        if include_fieldmen:
            # Use overview (lightweight) if available to avoid full row load
            try:
                fieldmen = await self._fieldmen.list_overview()
            except Exception:
                fieldmen = await self._fieldmen.list_fieldmen()
            for fm in fieldmen:
                lat_raw = _get(fm, "latitude", "home_lat")
                lng_raw = _get(fm, "longitude", "home_long")
                if lat_raw is None or lng_raw is None:
                    continue
                try:
                    lat = float(lat_raw)
                    lng = float(lng_raw)
                except (TypeError, ValueError):
                    continue
                cell = latlng_to_h3(lat, lng, resolution)
                if cell not in cell_data:
                    boundary = cell_to_boundary(cell)
                    center = cell_to_center(cell)
                    cell_data[cell] = {
                        "cell": cell,
                        "boundary": boundary,
                        "task_count": 0,
                        "fieldman_count": 0,
                        "center_lat": center[0],
                        "center_lng": center[1],
                    }
                cell_data[cell]["fieldman_count"] += 1

        cells = [H3CellInfo(**v) for v in cell_data.values()]
        total_tasks = sum(c.task_count for c in cells)
        total_fieldmen = sum(c.fieldman_count for c in cells)

        response = H3GridResponse(
            resolution=resolution,
            cells=cells,
            total_tasks=total_tasks,
            total_fieldmen=total_fieldmen,
        )

        if self._cache:
            try:
                await self._cache.set(cache_key, response.model_dump(), ttl=120)
            except Exception as exc:
                logger.warning("H3 grid cache set failed: %s", exc)

        return response

    # ── Plan Ahead ────────────────────────────────────────────────────

    async def plan_ahead(
        self,
        *,
        task_ids: Optional[List[str]],
        fieldman_ids: Optional[List[str]],
        area_ids: Optional[List[str]],
        task_area_map: Optional[Dict[str, str]],
    ) -> PlanAheadResponse:
        """Count tasks/fieldmen that would be included in a job."""
        if area_ids:
            if not task_area_map:
                raise ValidationError("task_area_map required for area filtering")
            allowed_areas = set(area_ids)
            allowed_tasks = set(task_ids) if task_ids else None
            task_count = 0
            for task_id, area_id in task_area_map.items():
                if allowed_tasks and task_id not in allowed_tasks:
                    continue
                if area_id in allowed_areas:
                    task_count += 1
        else:
            task_count = await self._tasks.count(task_ids)

        fieldman_count = await self._fieldmen.count(fieldman_ids)
        return PlanAheadResponse(tasks=task_count, fieldmen=fieldman_count)

    # ── Job Creation ──────────────────────────────────────────────────

    async def create_job(
        self,
        payload: Dict[str, Any],
        *,
        celery_app: Any,
        actor_id: Optional[str] = None,
    ) -> CreateJobResponse:
        """Create a VRP job and enqueue it for processing."""
        if not payload.get("requested_by") and actor_id:
            payload["requested_by"] = actor_id

        job_id = await self._vrp.create_job("queued", json.dumps(payload))
        celery_app.send_task("process_vrp_job", args=[job_id])

        await self._audit.record(
            entity_type="vrp_job",
            entity_id=job_id,
            action="created",
            actor_id=actor_id,
            after_state={"status": "queued"},
        )
        logger.info("VRP job created: %s", job_id)
        # Invalidate aggregate caches since a new job was created
        if self._cache:
            await self._cache.invalidate_all_jobs()
        return CreateJobResponse(job_id=job_id)

    # ── Job Status ────────────────────────────────────────────────────

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Return current status of a VRP job."""
        if self._cache:
            cached = await self._cache.get(CacheService.key_job_status(job_id))
            if cached is not None:
                return JobStatusResponse(**cached)

        row = await self._vrp.get_job(job_id)
        if not row:
            raise NotFoundError("Job not found")

        # Parse h3_resolution_info from status_detail if available
        h3_res_info = None
        status_detail_raw = row["status_detail"]
        if status_detail_raw:
            try:
                detail_data = json.loads(status_detail_raw)
                if isinstance(detail_data, dict) and "h3_resolution_info" in detail_data:
                    h3_res_info = H3ResolutionInfo(**detail_data["h3_resolution_info"])
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # Not JSON or not resolution info — keep raw status_detail

        result = JobStatusResponse(
            job_id=str(row["id"]),
            status=row["status"],
            status_detail=status_detail_raw,
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
            finalized_at=row["finalized_at"].isoformat() if row["finalized_at"] else None,
            h3_resolution_info=h3_res_info,
        )
        if self._cache:
            await self._cache.set(CacheService.key_job_status(job_id), result.model_dump(), CacheTTL.JOB_STATUS)
        return result

    # ── Job Metrics ───────────────────────────────────────────────────

    async def get_job_metrics(self, job_id: str) -> JobMetricsResponse:
        """Return aggregate metrics for a job."""
        if self._cache:
            cached = await self._cache.get(CacheService.key_job_metrics(job_id))
            if cached is not None:
                return JobMetricsResponse(**cached)

        row = await self._vrp.get_metrics(job_id)
        if row is None:
            raise NotFoundError("Job not found")
        result = JobMetricsResponse(
            job_id=job_id,
            tasks_assigned=int(row["tasks_assigned"]),
            fieldmen_used=int(row["fieldmen_used"]),
            total_distance=float(row["total_distance"]),
            total_duration=float(row["total_duration"]),
        )
        if self._cache:
            await self._cache.set(CacheService.key_job_metrics(job_id), result.model_dump(), CacheTTL.MEDIUM)
        return result

    # ── Assignments ───────────────────────────────────────────────────

    async def get_job_assignments(
        self,
        job_id: str,
        *,
        include_geometry: bool = True,
    ) -> AssignmentResponse:
        """Return assignments for a specific job.

        Uses live task data from DB when available; falls back to
        stored task snapshots in vrp_job_routes when tasks table is empty
        (e.g. after a data reset).
        """
        if self._cache:
            cached = await self._cache.get(CacheService.key_job_assignments(job_id, include_geometry))
            if cached is not None:
                return AssignmentResponse(**cached)

        rows = await self._vrp.get_assignments(job_id)
        if not rows:
            # Fallback: try building assignments from preview route data
            if not await self._vrp.job_exists(job_id):
                raise NotFoundError("Job not found")
            # Use preview_job to get route data (includes snapshot fallback)
            try:
                preview = await self.preview_job(job_id)
                preview_routes = preview.get("routes", [])
                if preview_routes:
                    routes_list = []
                    for pr in preview_routes:
                        route = {
                            "fieldman_id": pr["fieldman_id"],
                            "start_lat": pr.get("start_lat"),
                            "start_long": pr.get("start_long"),
                            "tasks": pr.get("tasks", []),
                            "geometry": pr.get("geometry") if include_geometry else None,
                        }
                        routes_list.append(route)
                    result = AssignmentResponse(routes=routes_list)
                    return result
            except Exception:
                pass
            return AssignmentResponse(routes=[])

        routes_list, meta = self._build_routes(rows, include_geometry)
        await self._attach_geometry(routes_list, include_geometry, meta)

        # Attach H3 resolution info from the job's status_detail
        h3_res_info = await self._get_job_resolution_info(job_id)
        if h3_res_info:
            if meta is None:
                meta = {}
            meta["h3_resolution_info"] = h3_res_info

        result = AssignmentResponse(routes=routes_list, meta=meta or None)
        if self._cache:
            await self._cache.set(CacheService.key_job_assignments(job_id, include_geometry), result.model_dump(), CacheTTL.MEDIUM)
        return result

    async def get_all_assignments(
        self,
        *,
        include_geometry: bool = True,
        include_overview: bool = True,
    ) -> AssignmentResponse:
        """Return all assignments across all jobs.

        Falls back to stored task snapshots from vrp_job_routes when
        live task data is unavailable (e.g. after data reset).
        """
        if self._cache:
            cached = await self._cache.get(CacheService.key_all_assignments(include_geometry, include_overview))
            if cached is not None:
                return AssignmentResponse(**cached)

        rows = await self._vrp.get_all_assignments()
        overview: Optional[OverviewResponse] = None
        if include_overview:
            overview = await self._fetch_overview()

        if not rows:
            # Fallback: gather routes from all ready jobs via preview
            try:
                all_jobs = await self._vrp.list_jobs()
                all_routes: List[Dict[str, Any]] = []
                for job in all_jobs:
                    if job.get("status") not in ("ready", "completed"):
                        continue
                    try:
                        preview = await self.preview_job(str(job["id"]))
                        for pr in preview.get("routes", []):
                            route = {
                                "job_id": str(job["id"]),
                                "fieldman_id": pr["fieldman_id"],
                                "start_lat": pr.get("start_lat"),
                                "start_long": pr.get("start_long"),
                                "tasks": pr.get("tasks", []),
                                "geometry": pr.get("geometry") if include_geometry else None,
                            }
                            all_routes.append(route)
                    except Exception:
                        continue
                if all_routes:
                    result = AssignmentResponse(routes=all_routes, overview=overview)
                    return result
            except Exception:
                pass
            return AssignmentResponse(routes=[], overview=overview)

        routes_dict: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            job_id = str(row["job_id"])
            fieldman_id = str(row["fieldman_id"])
            key = f"{job_id}:{fieldman_id}"
            route = routes_dict.get(key)
            if route is None:
                route = {
                    "job_id": job_id,
                    "fieldman_id": fieldman_id,
                    "start_lat": float(row["home_lat"]),
                    "start_long": float(row["home_long"]),
                    "tasks": [],
                }
                routes_dict[key] = route
            route["tasks"].append(
                {
                    "task_id": str(row["task_id"]),
                    "sequence": int(row["sequence"]),
                    "address": row["address"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "distance": float(row["task_distance"] or 0),
                    "duration": float(row["task_duration"] or 0),
                    "service": int(row.get("service") or 0),
                    "priority": float(row.get("priority") or 1.0),
                }
            )

        routes_list = list(routes_dict.values())
        meta: Dict[str, Any] = {}
        if include_geometry and len(routes_list) > settings.geometry_route_limit:
            meta = {
                "geometry_skipped": True,
                "reason": "route_count_exceeded",
                "route_count": len(routes_list),
                "max_routes": settings.geometry_route_limit,
            }
            include_geometry = False
        await self._attach_geometry(routes_list, include_geometry, meta)
        result = AssignmentResponse(routes=routes_list, meta=meta or None, overview=overview)
        if self._cache:
            await self._cache.set(CacheService.key_all_assignments(include_geometry, include_overview), result.model_dump(), CacheTTL.SHORT)
        return result

    # ── Preview ───────────────────────────────────────────────────────

    async def preview_job(self, job_id: str) -> Dict[str, Any]:
        """Return preview data (routes + GeoJSON) for a job."""
        if self._cache:
            cached = await self._cache.get(CacheService.key_job_preview(job_id))
            if cached is not None:
                return cached

        status = await self._vrp.get_job_status(job_id)
        if status is None:
            raise NotFoundError("Job not found")

        data = await self._vrp.get_preview_routes(job_id)
        route_rows = data["route_rows"]
        task_rows = data["task_rows"]

        # Build task map from vrp_assignments JOIN tasks (live DB data)
        task_map: Dict[str, List[Dict[str, Any]]] = {}
        for row in task_rows:
            fieldman_id = str(row["fieldman_id"])
            task_map.setdefault(fieldman_id, []).append(
                {
                    "task_id": str(row["task_id"]),
                    "sequence": int(row["sequence"]),
                    "distance": float(row["distance"]),
                    "duration": float(row["duration"]),
                    "status": row.get("status") or "pending",
                    "completed_at": str(row["completed_at"]) if row.get("completed_at") else None,
                    "service": int(row.get("service") or 0),
                    "priority": float(row.get("priority") or 1.0),
                    "manual_priority": float(row["manual_priority"]) if row.get("manual_priority") is not None else None,
                    "task_type": row.get("task_type") or "credit_investigation",
                    "bank": row.get("bank") or None,
                    "address": row["address"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
            )

        features: List[Dict[str, Any]] = []
        routes: List[Dict[str, Any]] = []
        for row in route_rows:
            fieldman_id = str(row["fieldman_id"])
            raw_geom = row["geometry"]
            if isinstance(raw_geom, str):
                try:
                    raw_geom = json.loads(raw_geom)
                except (ValueError, TypeError):
                    raw_geom = None
            geometry = raw_geom or {"type": "LineString", "coordinates": []}

            # Use live task data if available; fall back to stored task snapshots
            tasks_for_route = task_map.get(fieldman_id, [])
            if not tasks_for_route:
                # Fallback: use task snapshots embedded in vrp_job_routes.tasks JSONB
                raw_tasks = row.get("tasks")
                if isinstance(raw_tasks, str):
                    try:
                        raw_tasks = json.loads(raw_tasks)
                    except (ValueError, TypeError):
                        raw_tasks = []
                if isinstance(raw_tasks, list) and raw_tasks and isinstance(raw_tasks[0], dict):
                    tasks_for_route = raw_tasks

            routes.append(
                {
                    "fieldman_id": fieldman_id,
                    "distance": float(row["distance"] or 0.0),
                    "duration": float(row["duration"] or 0.0),
                    "start_lat": float(row["start_lat"]) if row["start_lat"] is not None else None,
                    "start_long": float(row["start_long"]) if row["start_long"] is not None else None,
                    "tasks": tasks_for_route,
                    "geometry": geometry,
                }
            )
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "fieldman_id": fieldman_id,
                        "distance": float(row["distance"] or 0.0),
                        "duration": float(row["duration"] or 0.0),
                        "task_count": len(tasks_for_route),
                    },
                    "geometry": geometry,
                }
            )

        result = {
            "job_id": job_id,
            "status": status,
            "routes": routes,
            "geojson": {"type": "FeatureCollection", "features": features},
        }
        if self._cache:
            await self._cache.set(CacheService.key_job_preview(job_id), result, CacheTTL.MEDIUM)
        return result

    async def preview_job_html(self, job_id: str) -> str:
        """Return a self-contained HTML page for previewing job routes."""
        data = await self.preview_job(job_id)
        import json as _json

        routes_json = _json.dumps(data["routes"])
        geojson_json = _json.dumps(data["geojson"])
        job_status = data["status"]

        total_routes = len(data["routes"])
        total_tasks = sum(len(r.get("tasks", [])) for r in data["routes"])
        total_dist = sum(r.get("distance", 0) for r in data["routes"])
        total_dur = sum(r.get("duration", 0) for r in data["routes"])

        status_class = {
            "ready": "success", "finalized": "accent", "queued": "warning", "failed": "danger"
        }.get(job_status, "accent")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>VRP Preview — Job {job_id[:8]}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root {{
  --bg: #f4f5f7;
  --bg-surface: #ffffff;
  --bg-elevated: #f0f1f4;
  --border: rgba(0,0,0,0.08);
  --border-strong: rgba(0,0,0,0.15);
  --text-primary: #1a1d2e;
  --text-secondary: #5a5f72;
  --text-muted: #8b8fa3;
  --accent: #4f46e5;
  --accent-soft: rgba(79,70,229,0.1);
  --success: #16a34a;
  --success-soft: rgba(22,163,74,0.1);
  --warning: #d97706;
  --warning-soft: rgba(217,119,6,0.1);
  --danger: #dc2626;
  --danger-soft: rgba(220,38,38,0.1);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-md: 0 4px 16px rgba(0,0,0,0.1);
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text-primary);display:flex;height:100vh;-webkit-font-smoothing:antialiased}}
::-webkit-scrollbar{{width:6px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:#c5c8d0;border-radius:3px}}

#sidebar{{width:380px;overflow-y:auto;background:var(--bg-surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}}
#map{{flex:1}}

.sidebar-header{{padding:20px 20px 16px;border-bottom:1px solid var(--border)}}
.sidebar-header h1{{font-size:16px;font-weight:700;color:var(--text-primary);display:flex;align-items:center;gap:10px}}
.sidebar-header .logo{{width:32px;height:32px;border-radius:var(--radius-md);background:var(--accent);display:grid;place-items:center;color:#fff;font-weight:700;font-size:12px}}
.job-id{{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-muted);margin-top:4px}}

.badge{{display:inline-flex;align-items:center;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:0.02em}}
.badge-success{{background:var(--success-soft);color:var(--success)}}
.badge-accent{{background:var(--accent-soft);color:var(--accent)}}
.badge-warning{{background:var(--warning-soft);color:var(--warning)}}
.badge-danger{{background:var(--danger-soft);color:var(--danger)}}

.stats{{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:16px 20px;border-bottom:1px solid var(--border)}}
.stat-card{{background:var(--bg-elevated);border-radius:var(--radius-sm);padding:12px}}
.stat-label{{font-size:10px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em}}
.stat-value{{font-size:20px;font-weight:700;color:var(--text-primary);margin-top:2px}}

/* Filter bar */
.filter-bar{{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}}
.filter-bar .filter-label{{font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em}}
.filter-bar .active-filter{{flex:1;font-size:13px;font-weight:600;color:var(--accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.btn-reset{{border:none;background:var(--danger-soft);color:var(--danger);font-size:11px;font-weight:600;padding:5px 12px;border-radius:20px;cursor:pointer;transition:all .15s}}
.btn-reset:hover{{background:var(--danger);color:#fff}}

.route-list{{padding:12px;flex:1;overflow-y:auto}}
.route-card{{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:14px;margin-bottom:8px;cursor:pointer;transition:all .2s;position:relative}}
.route-card:hover{{border-color:var(--accent);box-shadow:var(--shadow-sm)}}
.route-card.active{{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft);background:rgba(79,70,229,0.03)}}
.route-card.dimmed{{opacity:0.35;pointer-events:auto}}
.route-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px}}
.color-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0;border:2px solid var(--bg-surface);box-shadow:0 0 0 1px var(--border)}}
.fm-id{{font-size:13px;font-weight:600;color:var(--text-primary)}}
.route-meta{{display:flex;gap:12px;font-size:12px;color:var(--text-secondary);margin-bottom:6px}}
.route-meta span{{display:flex;align-items:center;gap:3px}}

.task-list{{border-top:1px solid var(--border);padding-top:6px;margin-top:4px;max-height:180px;overflow-y:auto}}
.task-row{{display:flex;gap:6px;padding:4px 0;font-size:12px;border-bottom:1px solid var(--bg-elevated);align-items:center}}
.task-row:last-child{{border-bottom:none}}
.task-seq{{width:22px;height:22px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0}}
.task-addr{{flex:1;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.task-type{{font-size:10px;font-weight:600;padding:1px 6px;border-radius:8px;background:var(--bg-elevated);color:var(--text-muted);flex-shrink:0;text-transform:uppercase}}
.task-prio{{width:45px;text-align:right;color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px;flex-shrink:0}}
.task-prio.manual{{color:var(--warning);font-weight:600}}
.task-prio-bar{{width:40px;height:6px;border-radius:3px;background:var(--bg-elevated);flex-shrink:0;overflow:hidden;position:relative}}
.task-prio-bar .fill{{height:100%;border-radius:3px;transition:width .3s}}
.task-prio-cell{{display:flex;align-items:center;gap:4px;width:80px;flex-shrink:0;justify-content:flex-end}}
.task-dist{{width:55px;text-align:right;color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px;flex-shrink:0}}

/* Leaflet tooltip customization */
.preview-tooltip{{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 14px;box-shadow:var(--shadow-md);font-family:'Inter',sans-serif;font-size:12px;color:var(--text-primary);line-height:1.5;max-width:280px;width:max-content;white-space:normal;word-wrap:break-word;overflow:hidden}}
.preview-tooltip .tt-header{{font-weight:700;font-size:13px;margin-bottom:4px;display:flex;align-items:flex-start;gap:6px;white-space:normal;word-wrap:break-word;line-height:1.35}}
.preview-tooltip .tt-uuid{{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text-muted);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.preview-tooltip .tt-row{{display:flex;justify-content:space-between;gap:16px}}
.preview-tooltip .tt-label{{color:var(--text-muted);font-size:11px}}
.preview-tooltip .tt-value{{font-weight:600;font-size:12px}}
.preview-tooltip .tt-type{{display:inline-block;background:var(--accent-soft);color:var(--accent);font-size:10px;font-weight:600;padding:1px 7px;border-radius:8px;text-transform:uppercase;margin-right:4px}}
.preview-tooltip .tt-manual{{color:var(--warning);font-weight:700}}

.leaflet-tooltip.preview-tooltip {{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);box-shadow:var(--shadow-md);padding:10px 14px;max-width:280px;white-space:normal;word-wrap:break-word;overflow:hidden;opacity:1!important}}
.leaflet-tooltip.preview-tooltip::before{{display:none}}

/* Numbered sequence markers */
.seq-marker{{display:flex;align-items:center;justify-content:center;border:2px solid #fff;border-radius:50%;font-size:11px;font-weight:700;color:#fff;box-shadow:0 1px 4px rgba(0,0,0,0.3);text-shadow:0 1px 1px rgba(0,0,0,0.3)}}
.seq-marker.sm{{width:24px;height:24px;font-size:10px}}
.seq-marker.lg{{width:30px;height:30px;font-size:12px}}

/* Fieldman start marker */
.fm-start-marker{{display:flex;align-items:center;justify-content:center;background:#ef4444;border:2px solid #fff;border-radius:50%;width:32px;height:32px;box-shadow:0 2px 6px rgba(0,0,0,0.35)}}
.fm-start-marker svg{{width:16px;height:16px}}

.leaflet-interactive.animated{{animation:routeDash 2.8s linear infinite}}
@keyframes routeDash{{from{{stroke-dashoffset:0}}to{{stroke-dashoffset:-60}}}}
</style>
</head>
<body>
<div id="sidebar">
  <div class="sidebar-header">
    <h1><span class="logo">VRP</span> Route Preview</h1>
    <div class="job-id">{job_id}</div>
    <div style="margin-top:8px"><span class="badge badge-{status_class}">{job_status.upper()}</span></div>
  </div>
  <div class="stats">
    <div class="stat-card"><div class="stat-label">Routes</div><div class="stat-value">{total_routes}</div></div>
    <div class="stat-card"><div class="stat-label">Tasks</div><div class="stat-value">{total_tasks}</div></div>
    <div class="stat-card"><div class="stat-label">Distance</div><div class="stat-value">{total_dist/1000:.1f} km</div></div>
    <div class="stat-card"><div class="stat-label">Duration</div><div class="stat-value">{total_dur/60:.0f} min</div></div>
  </div>
  <div class="filter-bar" id="filter-bar" style="display:none">
    <span class="filter-label">Focused:</span>
    <span class="active-filter" id="active-filter-label"></span>
    <button class="btn-reset" id="btn-reset" onclick="resetFilter()">Reset</button>
  </div>
  <div class="route-list" id="route-cards"></div>
</div>
<div id="map"></div>
<script>
const ROUTES = {routes_json};
const GEOJSON = {geojson_json};

const map = L.map('map').setView([14.6,121.0],11);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png',{{
  attribution:'&copy; <a href="https://carto.com/">CARTO</a>',maxZoom:19
}}).addTo(map);

function hsl(i,n){{ return 'hsl('+(i*360/Math.max(n,1))%360+',65%,45%)'; }}

// Task type labels
const TYPE_LABELS = {{
  credit_investigation: 'CI',
  field_verification: 'FV',
  collection: 'COL',
  skip_trace: 'ST',
  repossession: 'REPO',
}};

// ── Layer management ──────────────────────────────────────────
// Each fieldman gets their own layerGroup for perfect isolation
const routeLayerGroups = {{}};   // fieldman_id -> L.layerGroup
const allBounds = [];
let activeFieldman = null;       // currently isolated fieldman_id

ROUTES.forEach((route, i) => {{
  const color = hsl(i, ROUTES.length);
  const tasks = route.tasks || [];
  const geom = route.geometry;
  const fid = route.fieldman_id;
  const lg = L.layerGroup().addTo(map);
  routeLayerGroups[fid] = {{ layer: lg, color: color, index: i, route: route }};

  // ── Fieldman position marker — moves to last completed task ──
  const doneTasks = tasks.filter(t => t.status === 'completed' && t.latitude && t.longitude);
  const lastDone = doneTasks.length > 0 ? doneTasks[doneTasks.length - 1] : null;
  const fmLat = lastDone ? lastDone.latitude : route.start_lat;
  const fmLng = lastDone ? lastDone.longitude : route.start_long;

  if (fmLat && fmLng) {{
    const fmIcon = L.divIcon({{
      className: '',
      html: '<div class="fm-start-marker"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 19V9l-3 3"/><path d="M9 12h6l3-6h-4l-2-3H7"/></svg></div>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    }});
    const fmMarker = L.marker([fmLat, fmLng], {{ icon: fmIcon }});
    const posLabel = lastDone ? 'Current Position (task #' + (lastDone.sequence || '?') + ')' : 'Start';
    const fmTT = '<div class="tt-header"><span style="color:' + color + '">&#9679;</span> Fieldman ' + posLabel + '</div>'
      + '<div class="tt-uuid">' + fid + '</div>'
      + '<div class="tt-row"><span class="tt-label">Tasks</span><span class="tt-value">' + tasks.length + '</span></div>'
      + (doneTasks.length > 0 ? '<div class="tt-row"><span class="tt-label">Completed</span><span class="tt-value" style="color:#22c55e">' + doneTasks.length + '/' + tasks.length + '</span></div>' : '')
      + '<div class="tt-row"><span class="tt-label">Distance</span><span class="tt-value">' + (route.distance/1000).toFixed(1) + ' km</span></div>'
      + '<div class="tt-row"><span class="tt-label">Duration</span><span class="tt-value">' + Math.round(route.duration/60) + ' min</span></div>';
    fmMarker.bindTooltip(fmTT, {{ direction: 'auto', offset: [0, -18], className: 'preview-tooltip' }});
    fmMarker.addTo(lg);
    allBounds.push([fmLat, fmLng]);
  }}

  // Show home as ghost marker if FM has moved
  if (lastDone && route.start_lat && route.start_long) {{
    const homeIcon = L.divIcon({{
      className: '',
      html: '<div style="background:rgba(239,68,68,0.3);color:#ef4444;border:2px dashed #ef4444;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;">H</div>',
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    }});
    L.marker([route.start_lat, route.start_long], {{ icon: homeIcon }})
      .bindTooltip('Home base: FM ' + fid.slice(0, 8), {{ direction: 'auto', offset: [0, -12], className: 'preview-tooltip' }})
      .addTo(lg);
    allBounds.push([route.start_lat, route.start_long]);
  }}

  // ── Task markers with sequence numbers ───────────────────
  tasks.forEach((t, idx) => {{
    const seq = t.sequence != null ? t.sequence : (idx + 1);
    if (!t.latitude || !t.longitude) return;
    const isDone = t.status === 'completed';
    const mkColor = isDone ? '#22c55e' : color;
    const mkLabel = isDone ? '✓' : seq;

    const taskIcon = L.divIcon({{
      className: '',
      html: '<div class="seq-marker sm" style="background:' + mkColor + ';opacity:' + (isDone ? '0.7' : '1') + '">' + mkLabel + '</div>',
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    }});
    const marker = L.marker([t.latitude, t.longitude], {{ icon: taskIcon }});

    // Rich hover tooltip
    const typeLabel = TYPE_LABELS[t.task_type] || (t.task_type || 'TASK').toUpperCase().slice(0, 4);
    const prioStr = t.manual_priority != null
      ? '<span class="tt-manual">★ ' + t.manual_priority.toFixed(1) + '</span> (manual)'
      : (t.priority != null ? t.priority.toFixed(1) : '-');
    const bankStr = t.bank ? '<div class="tt-row"><span class="tt-label">Bank</span><span class="tt-value">' + t.bank + '</span></div>' : '';
    const statusStr = '<div class="tt-row"><span class="tt-label">Status</span><span class="tt-value" style="color:' + (isDone ? '#22c55e' : '#6366f1') + ';font-weight:600">' + (isDone ? 'Completed ✓' : 'Pending') + '</span></div>';
    const doneStr = isDone && t.completed_at ? '<div class="tt-row"><span class="tt-label">Done</span><span class="tt-value">' + new Date(t.completed_at).toLocaleString() + '</span></div>' : '';
    const tt = '<div class="tt-header"><span class="tt-type">' + typeLabel + '</span> #' + seq + ' — ' + (t.address || 'Unknown') + '</div>'
      + '<div class="tt-uuid">' + t.task_id + '</div>'
      + statusStr
      + '<div class="tt-row"><span class="tt-label">Priority</span><span class="tt-value">' + prioStr + '</span></div>'
      + bankStr
      + '<div class="tt-row"><span class="tt-label">Distance</span><span class="tt-value">' + (t.distance/1000).toFixed(2) + ' km</span></div>'
      + '<div class="tt-row"><span class="tt-label">Duration</span><span class="tt-value">' + Math.round(t.duration/60) + ' min</span></div>'
      + '<div class="tt-row"><span class="tt-label">Service</span><span class="tt-value">' + Math.round((t.service || 0)/60) + ' min</span></div>'
      + doneStr;
    marker.bindTooltip(tt, {{ direction: 'auto', offset: [0, -14], className: 'preview-tooltip', sticky: false }});
    marker.addTo(lg);
    allBounds.push([t.latitude, t.longitude]);
  }});

  // ── Route polyline ───────────────────────────────────────
  if (geom && geom.coordinates && geom.coordinates.length > 1) {{
    const coords = geom.coordinates.map(c => [c[1], c[0]]);
    L.polyline(coords, {{
      color: color, weight: 4, opacity: 0.85,
      dashArray: '6 10', lineCap: 'round', lineJoin: 'round',
      className: 'animated'
    }}).addTo(lg);
  }}

  // ── Sidebar route card ───────────────────────────────────
  const card = document.createElement('div');
  card.className = 'route-card';
  card.id = 'card-' + fid;
  const totalSvc = tasks.reduce((s, t) => s + (t.service || 0), 0);
  const doneCount = tasks.filter(t => t.status === 'completed').length;
  const taskRows = tasks.map(t => {{
    const seq = t.sequence != null ? t.sequence : '?';
    const tl = TYPE_LABELS[t.task_type] || (t.task_type || '').slice(0, 3).toUpperCase();
    const mp = t.manual_priority != null;
    const pVal = mp ? t.manual_priority : (t.priority || 1);
    const pPct = Math.min(100, Math.max(0, (pVal / 100) * 100));
    const pColor = pVal >= 70 ? 'var(--danger)' : pVal >= 40 ? 'var(--warning)' : 'var(--accent)';
    const rowDone = t.status === 'completed';
    return '<div class="task-row" style="' + (rowDone ? 'opacity:0.55;' : '') + '">'
      + '<span class="task-seq" style="' + (rowDone ? 'background:#22c55e;color:#fff;' : '') + '">' + (rowDone ? '✓' : seq) + '</span>'
      + '<span class="task-addr" style="' + (rowDone ? 'text-decoration:line-through;' : '') + '">' + (t.address || t.task_id.slice(0, 8)) + '</span>'
      + '<span class="task-type">' + tl + '</span>'
      + '<span class="task-prio-cell">'
        + '<span class="task-prio' + (mp ? ' manual' : '') + '">' + (mp ? '★' + t.manual_priority.toFixed(1) : (t.priority != null ? t.priority.toFixed(1) : '?')) + '</span>'
        + '<span class="task-prio-bar"><span class="fill" style="width:' + pPct.toFixed(0) + '%;background:' + pColor + '"></span></span>'
      + '</span>'
      + '<span class="task-dist">' + (t.distance/1000).toFixed(2) + 'km</span>'
      + '</div>';
  }}).join('');

  card.innerHTML = '<div class="route-header">'
    + '<div class="color-dot" style="background:' + color + '"></div>'
    + '<span class="fm-id">FM ' + fid.slice(0, 8) + '...</span>'
    + (doneCount > 0 ? '<span style="margin-left:auto;font-size:11px;color:#22c55e;font-weight:600">' + doneCount + '/' + tasks.length + ' done</span>' : '')
    + '</div>'
    + '<div class="route-meta">'
    + '<span>' + tasks.length + ' tasks</span>'
    + '<span>' + (route.distance/1000).toFixed(1) + ' km</span>'
    + '<span>' + Math.round(route.duration/60) + ' min</span>'
    + '<span>' + Math.round(totalSvc/60) + ' min svc</span>'
    + '</div>'
    + (tasks.length ? '<div class="task-list">' + taskRows + '</div>' : '');

  card.onclick = () => {{ focusFieldman(fid); }};
  document.getElementById('route-cards').appendChild(card);
}});

// ── Fieldman Focus / Filter ──────────────────────────────────
function focusFieldman(fid) {{
  if (activeFieldman === fid) {{
    resetFilter();
    return;
  }}
  activeFieldman = fid;
  const info = routeLayerGroups[fid];
  if (!info) return;

  // Show/hide layers
  Object.keys(routeLayerGroups).forEach(id => {{
    const g = routeLayerGroups[id];
    if (id === fid) {{
      if (!map.hasLayer(g.layer)) map.addLayer(g.layer);
    }} else {{
      if (map.hasLayer(g.layer)) map.removeLayer(g.layer);
    }}
  }});

  // Update sidebar cards
  document.querySelectorAll('.route-card').forEach(el => {{
    const cardFid = el.id.replace('card-', '');
    if (cardFid === fid) {{
      el.classList.add('active');
      el.classList.remove('dimmed');
    }} else {{
      el.classList.remove('active');
      el.classList.add('dimmed');
    }}
  }});

  // Show filter bar
  document.getElementById('filter-bar').style.display = 'flex';
  document.getElementById('active-filter-label').textContent = 'FM ' + fid.slice(0, 12) + '...';

  // Zoom to fieldman's route
  const route = info.route;
  const bounds = [];
  if (route.start_lat && route.start_long) bounds.push([route.start_lat, route.start_long]);
  (route.tasks || []).forEach(t => {{
    if (t.latitude && t.longitude) bounds.push([t.latitude, t.longitude]);
  }});
  if (bounds.length) map.fitBounds(bounds, {{ padding: [50, 50], maxZoom: 15 }});
}}

function resetFilter() {{
  activeFieldman = null;
  // Show all layers
  Object.keys(routeLayerGroups).forEach(id => {{
    const g = routeLayerGroups[id];
    if (!map.hasLayer(g.layer)) map.addLayer(g.layer);
  }});
  // Reset sidebar
  document.querySelectorAll('.route-card').forEach(el => {{
    el.classList.remove('active', 'dimmed');
  }});
  document.getElementById('filter-bar').style.display = 'none';
  // Zoom to all
  if (allBounds.length) map.fitBounds(allBounds, {{ padding: [30, 30] }});
}}

if (allBounds.length) map.fitBounds(allBounds, {{ padding: [30, 30] }});
</script>
</body>
</html>"""
        return html

    # ── Finalize ──────────────────────────────────────────────────────

    async def finalize_job(
        self,
        job_id: str,
        *,
        actor_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Mark a job as finalized."""
        success = await self._vrp.finalize_job(job_id)
        if not success:
            raise NotFoundError("Job not found or already finalized")
        await self._audit.record(
            entity_type="vrp_job",
            entity_id=job_id,
            action="finalized",
            actor_id=actor_id,
        )
        # Invalidate caches for this job
        if self._cache:
            await self._cache.invalidate_job(job_id)
        return {"job_id": job_id, "status": "finalized"}

    async def delete_job(
        self,
        job_id: str,
        *,
        actor_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Delete a job and all associated data."""
        success = await self._vrp.delete_job(job_id)
        if not success:
            raise NotFoundError("Job not found")
        await self._audit.record(
            entity_type="vrp_job",
            entity_id=job_id,
            action="deleted",
            actor_id=actor_id,
        )
        if self._cache:
            await self._cache.invalidate_job(job_id)
            await self._cache.invalidate_all_jobs()
        return {"job_id": job_id, "status": "deleted"}

    # ── Overview ──────────────────────────────────────────────────────

    async def get_overview(self) -> OverviewResponse:
        """Return all tasks and fieldmen for the overview map."""
        return await self._fetch_overview()

    # ── Task Summary ──────────────────────────────────────────────────

    async def get_task_summary(self) -> TaskSummaryResponse:
        """Return aggregated task counts by type and bank (with cache)."""
        if self._cache:
            cached = await self._cache.get(CacheService.key_task_summary())
            if cached is not None:
                return TaskSummaryResponse(**cached)

        data = await self._tasks.get_task_summary()
        result = TaskSummaryResponse(**data)
        if self._cache:
            await self._cache.set(
                CacheService.key_task_summary(),
                result.model_dump(),
                CacheTTL.TASK_SUMMARY,
            )
        return result

    # ── Job Listing ───────────────────────────────────────────────────

    async def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List jobs with optional status filter and pagination."""
        rows = await self._vrp.list_jobs(
            status=status, limit=limit, offset=offset,
        )
        return [
            {
                "id": str(row["id"]),
                "status": row["status"],
                "status_detail": row.get("status_detail"),
                "created_at": row["created_at"].isoformat()
                if row.get("created_at") else None,
                "updated_at": row["updated_at"].isoformat()
                if row.get("updated_at") else None,
                "finalized_at": row["finalized_at"].isoformat()
                if row.get("finalized_at") else None,
            }
            for row in rows
        ]

    # ── Internals ─────────────────────────────────────────────────────

    async def _fetch_overview(self) -> OverviewResponse:
        """Build overview from repos (with cache). Uses model_construct for speed at scale."""
        if self._cache:
            cached = await self._cache.get(CacheService.key_overview())
            if cached is not None:
                return OverviewResponse(**cached)

        import asyncio
        task_items, fm_items = await asyncio.gather(
            self._tasks.list_overview(),
            self._fieldmen.list_overview(),
        )
        # Use model_construct to skip Pydantic validation for trusted DB data
        # This is ~10x faster than full validation for 20k+ records
        result = OverviewResponse.model_construct(
            tasks=[
                TaskOverviewItem.model_construct(
                    task_id=t.task_id,
                    address=t.address,
                    latitude=t.latitude,
                    longitude=t.longitude,
                    task_type=t.task_type,
                    bank=t.bank,
                    priority=t.priority,
                    manual_priority=t.manual_priority,
                )
                for t in task_items
            ],
            fieldmen=[
                FieldmanOverviewItem.model_construct(
                    fieldman_id=f.fieldman_id,
                    address=f.address,
                    latitude=f.latitude,
                    longitude=f.longitude,
                )
                for f in fm_items
            ],
        )
        if self._cache:
            await self._cache.set(CacheService.key_overview(), result.model_dump(), CacheTTL.OVERVIEW)
        return result

    async def _get_job_resolution_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Extract h3_resolution_info from a job's status_detail if available."""
        row = await self._vrp.get_job(job_id)
        if not row:
            return None
        detail = row.get("status_detail")
        if not detail:
            return None
        try:
            data = json.loads(detail)
            if isinstance(data, dict) and "h3_resolution_info" in data:
                return data["h3_resolution_info"]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    def _build_routes(
        self,
        rows: List[Dict[str, Any]],
        include_geometry: bool,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Group assignment rows into route dicts."""
        routes: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            fieldman_id = str(row["fieldman_id"])
            route = routes.get(fieldman_id)
            if route is None:
                route = {
                    "job_id": str(row["job_id"]),
                    "fieldman_id": fieldman_id,
                    "start_lat": float(row["home_lat"]),
                    "start_long": float(row["home_long"]),
                    "tasks": [],
                }
                routes[fieldman_id] = route
            route["tasks"].append(
                {
                    "task_id": str(row["task_id"]),
                    "sequence": int(row["sequence"]),
                    "address": row["address"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "distance": float(row.get("task_distance") or 0),
                    "duration": float(row.get("task_duration") or 0),
                    "service": int(row.get("service") or 0),
                }
            )

        routes_list = list(routes.values())
        meta: Dict[str, Any] = {}
        if include_geometry and len(routes_list) > settings.geometry_route_limit:
            meta = {
                "geometry_skipped": True,
                "reason": "route_count_exceeded",
                "route_count": len(routes_list),
                "max_routes": settings.geometry_route_limit,
            }
        return routes_list, meta

    async def _attach_geometry(
        self,
        routes: List[Dict[str, Any]],
        include_geometry: bool,
        meta: Dict[str, Any],
    ) -> None:
        """Attach OSRM route geometry to each route."""
        if not routes:
            return
        if not include_geometry or meta.get("geometry_skipped"):
            for route in routes:
                route["geometry"] = []
            return

        osrm = OSRMService(
            settings.osrm_url,
            timeout=5.0,
            max_locations=settings.osrm_max_locations,
            cache=self._cache,
        )

        semaphore = asyncio.Semaphore(settings.osrm_concurrency)

        async def build_geometry(route: Dict[str, Any]) -> List[List[float]]:
            coords = [(route["start_lat"], route["start_long"])]
            coords.extend(
                [(task["latitude"], task["longitude"]) for task in route["tasks"]]
            )
            if len(coords) < 2:
                return []
            await semaphore.acquire()
            try:
                try:
                    return await asyncio.wait_for(osrm.get_route_geometry(coords), timeout=5.0)
                except Exception:
                    return []
            finally:
                semaphore.release()

        results = await asyncio.gather(
            *[build_geometry(route) for route in routes],
            return_exceptions=True,
        )
        for route, result in zip(routes, results):
            route["geometry"] = result if isinstance(result, list) else []

    # ── Fieldman Task Completion & Listing ────────────────────────────

    async def complete_fieldman_task(
        self,
        fieldman_id: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """Mark a task as completed (original sequence numbers preserved).

        Returns a dict suitable for CompleteTaskResponse.

        Raises:
            NotFoundError: fieldman or task does not exist, or no assignment found
            ValidationError: task is already completed
            AuthorizationError: task does not belong to given fieldman
        """
        from core.exceptions import AuthorizationError

        # Ensure columns exist before any queries
        await self._vrp.ensure_assignment_columns()

        # Validate fieldman exists
        if not await self._vrp.fieldman_exists(fieldman_id):
            raise NotFoundError(f"Fieldman {fieldman_id} not found")

        # Validate task exists
        if not await self._vrp.task_exists(task_id):
            raise NotFoundError(f"Task {task_id} not found")

        # Check assignment exists
        assignment = await self._vrp.get_assignment_by_fieldman_and_task(
            fieldman_id, task_id
        )
        if assignment is None:
            raise AuthorizationError(
                f"Task {task_id} is not assigned to fieldman {fieldman_id}"
            )

        # Check if already completed
        if assignment.get("status") == "completed":
            raise ValidationError("Task already completed")

        # Complete task (sequence numbers stay the same)
        updated_tasks = await self._vrp.complete_task_and_resequence(
            fieldman_id, task_id
        )

        # Invalidate preview cache for the job so Map View and preview HTML refresh
        job_id = assignment.get("job_id")
        if job_id and self._cache:
            await self._cache.delete(CacheService.key_job_preview(str(job_id)))
            # Also clear the legacy cache key
            await self._cache.delete(f"vrp:preview:{job_id}")

        pending = [t for t in updated_tasks if t["status"] == "pending"]
        completed = [t for t in updated_tasks if t["status"] == "completed"]

        message = "Task completed"
        if not pending:
            message = "All tasks completed"

        return {
            "fieldman_id": fieldman_id,
            "task_id": task_id,
            "message": message,
            "tasks": updated_tasks,
            "pending": len(pending),
            "completed": len(completed),
        }

    async def get_fieldman_tasks(
        self,
        fieldman_id: str,
    ) -> Dict[str, Any]:
        """Return all tasks for a fieldman ordered by sequence.

        Returns a dict suitable for FieldmanTaskListResponse.

        Raises:
            NotFoundError: fieldman does not exist
        """
        # Ensure columns exist
        await self._vrp.ensure_assignment_columns()

        if not await self._vrp.fieldman_exists(fieldman_id):
            raise NotFoundError(f"Fieldman {fieldman_id} not found")

        tasks = await self._vrp.get_fieldman_tasks(fieldman_id)

        pending = [t for t in tasks if t["status"] == "pending"]
        completed = [t for t in tasks if t["status"] == "completed"]

        return {
            "fieldman_id": fieldman_id,
            "tasks": tasks,
            "total": len(tasks),
            "pending": len(pending),
            "completed": len(completed),
        }
