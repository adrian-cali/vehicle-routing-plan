"""Legacy VRP routes (unversioned, deprecated).

Kept for backward compatibility. New code should use the v1 API.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from celery_app import celery_app
from core.config import get_settings
from core.database import get_db_pool
from core.logging import get_logger
from services.cache_service import CacheService, CacheTTL, get_cache_service

_settings = get_settings()
from services.osrm_service import OSRMService

# Domain models â€” single source of truth (avoid duplicate definitions)
from domain.vrp import (
    PlanAheadRequest,
    PlanAheadResponse,
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    JobMetricsResponse,
    AssignmentTask,
    AssignmentRoute,
    AssignmentResponse,
    OverviewResponse,
    TaskOverviewItem,
    FieldmanOverviewItem,
    RandomizeRequest,
    RandomizeResponse,
    TaskSummaryResponse,
    OptimizeSettingsRequest,
    PickerTaskRequest,
    PickerFieldmanRequest,
    PickerItemResponse,
    UpdateTaskPriorityRequest,
    UpdateFieldmanLocationRequest,
    GeocodeResponse,
)

logger = get_logger(__name__)

# Legacy aliases for backward compatibility
OverviewTask = TaskOverviewItem
OverviewFieldman = FieldmanOverviewItem

# â”€â”€ Area Configurations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AREA_CONFIGS: Dict[str, Dict[str, Any]] = {
    "AREA_NORTH": {
        "lat_min": 14.65, "lat_max": 14.78,
        "lon_min": 120.95, "lon_max": 121.05,
        "center": (14.715, 121.0),
    },
    "AREA_SOUTH": {
        "lat_min": 14.43, "lat_max": 14.55,
        "lon_min": 121.00, "lon_max": 121.09,
        "center": (14.49, 121.045),
    },
    "AREA_EAST": {
        "lat_min": 14.55, "lat_max": 14.68,
        "lon_min": 121.05, "lon_max": 121.15,
        "center": (14.615, 121.1),
    },
}

TASK_TYPES = {
    "credit_investigation": "CI",
    "skips_collect": "S&C",
    "demand_letter": "DL",
}


# â”€â”€ Schema migration helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_columns_ensured = False

async def _ensure_columns(pool: asyncpg.Pool) -> None:
    """Add task_type and bank columns to tasks if they don't exist (cached after first success)."""
    global _columns_ensured
    if _columns_ensured:
        return
    async with pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'"
        )
        existing = {row["column_name"] for row in cols}
        if "task_type" not in existing:
            await conn.execute(
                "ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'credit_investigation'"
            )
        if "bank" not in existing:
            await conn.execute(
                "ALTER TABLE tasks ADD COLUMN bank TEXT DEFAULT NULL"
            )
        if "service" not in existing:
            await conn.execute(
                "ALTER TABLE tasks ADD COLUMN service INT DEFAULT 0"
            )
        if "manual_priority" not in existing:
            await conn.execute(
                "ALTER TABLE tasks ADD COLUMN manual_priority DOUBLE PRECISION DEFAULT NULL"
            )
    _columns_ensured = True


def _generate_point(
    area: Dict[str, Any], scatterness_pct: int, radius_km: float,
) -> Tuple[float, float]:
    """Generate a random lat/lng within area bounds with scatterness control."""
    center_lat, center_lon = area["center"]
    lat_range = area["lat_max"] - area["lat_min"]
    lon_range = area["lon_max"] - area["lon_min"]

    spread = max(0.05, scatterness_pct / 100.0)

    # Gaussian around center, std proportional to spread
    lat = random.gauss(center_lat, lat_range * spread * 0.35)
    lon = random.gauss(center_lon, lon_range * spread * 0.35)

    # Also limit by radius from center
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * math.cos(math.radians(center_lat))
    dlat = (lat - center_lat) * km_per_deg_lat
    dlon = (lon - center_lon) * km_per_deg_lon
    dist = math.sqrt(dlat ** 2 + dlon ** 2)
    if dist > radius_km:
        scale = radius_km / dist
        lat = center_lat + (lat - center_lat) * scale
        lon = center_lon + (lon - center_lon) * scale

    lat = max(area["lat_min"], min(area["lat_max"], lat))
    lon = max(area["lon_min"], min(area["lon_max"], lon))
    return round(lat, 6), round(lon, 6)


def _parse_bank_distribution(bank_str: Optional[str]) -> List[Tuple[str, int]]:
    """Parse 'BPI:60,BDO:40' into [('BPI', 60), ('BDO', 40)]."""
    if not bank_str:
        return []
    result = []
    for part in bank_str.split(","):
        part = part.strip()
        if ":" in part:
            name, weight = part.split(":", 1)
            result.append((name.strip(), int(weight.strip())))
    return result


def _pick_bank(distribution: List[Tuple[str, int]]) -> Optional[str]:
    """Pick a random bank based on weighted distribution."""
    if not distribution:
        return None
    total = sum(w for _, w in distribution)
    if total <= 0:
        return distribution[0][0] if distribution else None
    r = random.randint(1, total)
    cumulative = 0
    for name, weight in distribution:
        cumulative += weight
        if r <= cumulative:
            return name
    return distribution[-1][0]

router = APIRouter()


async def get_current_user(x_user_id: Optional[str] = Header(default=None)) -> Optional[str]:
    return x_user_id


def _deprecation_warning(endpoint: str) -> None:
    """Log a deprecation warning for legacy (unversioned) endpoints."""
    logger.warning(
        "DEPRECATED: Legacy endpoint '%s' called. Migrate to /api/v1/ version.",
        endpoint,
    )


# All model classes imported from domain.vrp above (single source of truth).
# Legacy aliases: OverviewTask = TaskOverviewItem, OverviewFieldman = FieldmanOverviewItem


GEOMETRY_ROUTE_LIMIT = 50


_ROUTE_GEOMETRY_SEMAPHORE = asyncio.Semaphore(6)


async def _attach_stored_geometry(
    pool: asyncpg.Pool,
    routes: List[Dict[str, Any]],
    include_geometry: bool,
) -> None:
    """Attach pre-computed geometry from vrp_job_routes (fast DB read).
    
    Falls back to live OSRM computation only for routes where stored
    geometry is unavailable.
    """
    if not routes or not include_geometry:
        for route in (routes or []):
            route["geometry"] = []
        return

    # Try loading stored geometry from vrp_job_routes
    table_exists = await pool.fetchval("SELECT to_regclass('public.vrp_job_routes')")
    stored: Dict[str, Any] = {}
    if table_exists:
        # Collect unique job_ids from routes
        job_ids = list({r.get("job_id") for r in routes if r.get("job_id")})
        if job_ids:
            geom_rows = await pool.fetch(
                """
                SELECT job_id, fieldman_id, geometry
                FROM vrp_job_routes
                WHERE job_id = ANY($1::text[])
                """,
                job_ids,
            )
            for row in geom_rows:
                key = f"{row['job_id']}:{row['fieldman_id']}"
                raw_geom = row["geometry"]
                if isinstance(raw_geom, str):
                    try:
                        geom = json.loads(raw_geom)
                    except (json.JSONDecodeError, TypeError):
                        geom = None
                else:
                    geom = raw_geom
                if geom:
                    coords = geom.get("coordinates", [])
                    # Convert [lon, lat] → [lat, lon] for frontend
                    stored[key] = [[c[1], c[0]] for c in coords] if coords else []

    # Apply stored geometry or fall back to OSRM
    needs_osrm: List[Dict[str, Any]] = []
    for route in routes:
        key = f"{route.get('job_id')}:{route.get('fieldman_id')}"
        if key in stored:
            route["geometry"] = stored[key]
        else:
            needs_osrm.append(route)

    # Only call OSRM for routes without stored geometry
    if needs_osrm:
        await _attach_route_geometry(needs_osrm, True)


async def _attach_route_geometry(routes: List[Dict[str, Any]], include_geometry: bool) -> None:
    if not routes:
        return

    if not include_geometry:
        for route in routes:
            route["geometry"] = []
        return

    cache = await get_cache_service()
    osrm = OSRMService(_settings.osrm_url, timeout=5.0, cache=cache)

    async def _build_geometry(route: Dict[str, Any]) -> List[List[float]]:
        coords = [(route["start_lat"], route["start_long"])]
        coords.extend([(task["latitude"], task["longitude"]) for task in route["tasks"]])
        if len(coords) < 2:
            return []
        try:
            async with _ROUTE_GEOMETRY_SEMAPHORE:
                return await asyncio.wait_for(osrm.get_route_geometry(coords), timeout=5.0)
        except Exception:
            return []

    results = await asyncio.gather(*[_build_geometry(route) for route in routes], return_exceptions=True)
    for route, result in zip(routes, results):
        route["geometry"] = result if isinstance(result, list) else []


async def _fetch_overview(pool: asyncpg.Pool) -> OverviewResponse:
    task_rows = await pool.fetch(
        """
        SELECT id, address, latitude, longitude, task_type, bank, priority, manual_priority
        FROM tasks
        ORDER BY id
        """
    )
    fieldman_rows = await pool.fetch(
        """
        SELECT user_id, address, home_lat, home_long
        FROM fm_home_locations
        ORDER BY user_id
        """
    )

    tasks = [
        OverviewTask(
            task_id=str(row["id"]),
            address=row["address"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            task_type=row.get("task_type"),
            bank=row.get("bank"),
            priority=float(row["priority"]) if row.get("priority") is not None else None,
            manual_priority=float(row["manual_priority"]) if row.get("manual_priority") is not None else None,
        )
        for row in task_rows
    ]
    fieldmen = [
        OverviewFieldman(
            fieldman_id=str(row["user_id"]),
            address=row["address"],
            latitude=float(row["home_lat"]),
            longitude=float(row["home_long"]),
        )
        for row in fieldman_rows
    ]
    return OverviewResponse(tasks=tasks, fieldmen=fieldmen)


@router.post("/vrp/plan-ahead", response_model=PlanAheadResponse, deprecated=True)
async def plan_ahead(payload: PlanAheadRequest) -> PlanAheadResponse:
    _deprecation_warning("/vrp/plan-ahead")
    try:
        pool = await get_db_pool()

        if payload.area_ids:
            if not payload.task_area_map:
                raise HTTPException(status_code=400, detail="task_area_map required for area filtering")
            allowed_areas = set(payload.area_ids)
            allowed_tasks = set(payload.task_ids) if payload.task_ids else None
            task_count = 0
            for task_id, area_id in payload.task_area_map.items():
                if allowed_tasks and task_id not in allowed_tasks:
                    continue
                if area_id in allowed_areas:
                    task_count += 1
        else:
            if payload.task_ids:
                task_count = await pool.fetchval(
                    "SELECT COUNT(*) FROM tasks WHERE id = ANY($1::uuid[])",
                    payload.task_ids,
                )
            else:
                task_count = await pool.fetchval("SELECT COUNT(*) FROM tasks")

        if payload.fieldman_ids:
            fieldman_count = await pool.fetchval(
                "SELECT COUNT(*) FROM fm_home_locations WHERE user_id = ANY($1::uuid[])",
                payload.fieldman_ids,
            )
        else:
            fieldman_count = await pool.fetchval("SELECT COUNT(*) FROM fm_home_locations")

        return PlanAheadResponse(tasks=int(task_count), fieldmen=int(fieldman_count))
    except Exception as e:
        logger.error("Error in plan_ahead: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vrp/jobs", response_model=CreateJobResponse, deprecated=True)
async def create_job(
    payload: CreateJobRequest,
    current_user: Optional[str] = Depends(get_current_user),
) -> CreateJobResponse:
    _deprecation_warning("/vrp/jobs")
    pool = await get_db_pool()
    payload_data = payload.model_dump()
    if not payload_data.get("requested_by") and current_user:
        payload_data["requested_by"] = current_user
    job_id = await pool.fetchval(
        "INSERT INTO vrp_jobs (status, request_payload) VALUES ($1, $2) RETURNING id",
        "queued",
        json.dumps(payload_data),
    )

    celery_app.send_task("process_vrp_job", args=[str(job_id)])
    return CreateJobResponse(job_id=str(job_id))


@router.get("/vrp/jobs")
async def list_jobs(status: Optional[str] = None, limit: int = 20):
    """List VRP jobs, optionally filtered by status, newest first."""
    pool = await get_db_pool()
    if status:
        rows = await pool.fetch(
            "SELECT id, status, created_at, updated_at FROM vrp_jobs WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
            status, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, status, created_at, updated_at FROM vrp_jobs ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [
        {
            "job_id": str(r["id"]),
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


@router.get("/vrp/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    cache: CacheService = Depends(get_cache_service),
) -> JobStatusResponse:
    cached = await cache.get(cache.key_job_status(job_id))
    if cached is not None:
        return JobStatusResponse(**cached)
    pool = await get_db_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, status_detail, created_at, updated_at, finalized_at
        FROM vrp_jobs
        WHERE id = $1
        """,
        job_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    result = JobStatusResponse(
        job_id=str(row["id"]),
        status=row["status"],
        status_detail=row["status_detail"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
        finalized_at=row["finalized_at"].isoformat() if row["finalized_at"] else None,
    )
    await cache.set(cache.key_job_status(job_id), result.model_dump(), ttl=CacheTTL.JOB_STATUS)
    return result


@router.get("/vrp/jobs/{job_id}/metrics", response_model=JobMetricsResponse)
async def get_job_metrics(
    job_id: str,
    cache: CacheService = Depends(get_cache_service),
) -> JobMetricsResponse:
    cached = await cache.get(cache.key_job_metrics(job_id))
    if cached is not None:
        return JobMetricsResponse(**cached)
    pool = await get_db_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS tasks_assigned,
            COUNT(DISTINCT fieldman_id) AS fieldmen_used,
            COALESCE(SUM(distance), 0) AS total_distance,
            COALESCE(SUM(duration), 0) AS total_duration
        FROM vrp_assignments
        WHERE job_id = $1
        """,
        job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    result = JobMetricsResponse(
        job_id=job_id,
        tasks_assigned=int(row["tasks_assigned"]),
        fieldmen_used=int(row["fieldmen_used"]),
        total_distance=float(row["total_distance"]),
        total_duration=float(row["total_duration"]),
    )
    await cache.set(cache.key_job_metrics(job_id), result.model_dump(), ttl=CacheTTL.MEDIUM)
    return result


@router.get("/vrp/jobs/{job_id}/assignments", response_model=AssignmentResponse, include_in_schema=False)
async def get_job_assignments(job_id: str, include_geometry: bool = True) -> AssignmentResponse:
    pool = await get_db_pool()
    rows = await pool.fetch(
        """
        SELECT
            a.job_id,
            a.fieldman_id,
            a.task_id,
            a.sequence,
            a.distance AS task_distance,
            a.duration AS task_duration,
            t.address,
            t.latitude,
            t.longitude,
            COALESCE(t.service, 0) AS service,
            COALESCE(t.priority, 1.0) AS priority,
            f.home_lat,
            f.home_long
        FROM vrp_assignments a
        JOIN tasks t ON t.id = a.task_id
        JOIN fm_home_locations f ON f.user_id = a.fieldman_id
        WHERE a.job_id = $1
        ORDER BY a.fieldman_id, a.sequence
        """,
        job_id,
    )
    if not rows:
        exists = await pool.fetchval("SELECT 1 FROM vrp_jobs WHERE id = $1", job_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Job not found")
        return AssignmentResponse(routes=[])

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
                "distance": float(row["task_distance"] or 0),
                "duration": float(row["task_duration"] or 0),
                "service": int(row.get("service") or 0),
                "priority": float(row.get("priority") or 1.0),
            }
        )

    routes_list = list(routes.values())
    meta: Dict[str, Any] = {}
    if include_geometry and len(routes_list) > GEOMETRY_ROUTE_LIMIT:
        meta = {
            "geometry_skipped": True,
            "reason": "route_count_exceeded",
            "route_count": len(routes_list),
            "max_routes": GEOMETRY_ROUTE_LIMIT,
        }
        include_geometry = False
    await _attach_stored_geometry(pool, routes_list, include_geometry)
    return AssignmentResponse(routes=routes_list, meta=meta or None)


@router.get("/vrp/assignments", response_model=AssignmentResponse, include_in_schema=False)
async def get_all_assignments(
    include_geometry: bool = True,
    include_overview: bool = True,
) -> AssignmentResponse:
    pool = await get_db_pool()
    rows = await pool.fetch(
        """
        SELECT
            a.job_id,
            a.fieldman_id,
            a.task_id,
            a.sequence,
            a.distance AS task_distance,
            a.duration AS task_duration,
            t.address,
            t.latitude,
            t.longitude,
            COALESCE(t.service, 0) AS service,
            COALESCE(t.priority, 1.0) AS priority,
            f.home_lat,
            f.home_long
        FROM vrp_assignments a
        JOIN tasks t ON t.id = a.task_id
        JOIN fm_home_locations f ON f.user_id = a.fieldman_id
        ORDER BY a.job_id, a.fieldman_id, a.sequence
        """
    )
    overview: Optional[OverviewResponse] = None
    if include_overview:
        overview = await _fetch_overview(pool)

    if not rows:
        return AssignmentResponse(routes=[], overview=overview)

    routes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        job_id = str(row["job_id"])
        fieldman_id = str(row["fieldman_id"])
        key = f"{job_id}:{fieldman_id}"
        route = routes.get(key)
        if route is None:
            route = {
                "job_id": job_id,
                "fieldman_id": fieldman_id,
                "start_lat": float(row["home_lat"]),
                "start_long": float(row["home_long"]),
                "tasks": [],
            }
            routes[key] = route
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

    routes_list = list(routes.values())
    meta: Dict[str, Any] = {}
    if include_geometry and len(routes_list) > GEOMETRY_ROUTE_LIMIT:
        meta = {
            "geometry_skipped": True,
            "reason": "route_count_exceeded",
            "route_count": len(routes_list),
            "max_routes": GEOMETRY_ROUTE_LIMIT,
        }
        include_geometry = False
    await _attach_stored_geometry(pool, routes_list, include_geometry)
    return AssignmentResponse(routes=routes_list, meta=meta or None, overview=overview)


@router.get("/vrp/overview", response_model=OverviewResponse, deprecated=True)
async def get_overview(
    cache: CacheService = Depends(get_cache_service),
) -> OverviewResponse:
    _deprecation_warning("/vrp/overview")
    pool = await get_db_pool()
    cached = await cache.get(cache.key_overview())
    if cached is not None:
        return OverviewResponse(**cached)
    result = await _fetch_overview(pool)
    await cache.set(cache.key_overview(), result.model_dump(), ttl=CacheTTL.OVERVIEW)
    return result


@router.get("/vrp/jobs/{job_id}/preview", include_in_schema=False)
async def preview_job(job_id: str) -> Dict[str, Any]:
    # Try cache first (preview data is immutable once job is ready)
    cache = await get_cache_service()
    cache_key = f"vrp:preview:{job_id}"
    cached = await cache.get(cache_key)
    if cached:
        return cached

    pool = await get_db_pool()
    job = await pool.fetchrow("SELECT status FROM vrp_jobs WHERE id = $1", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    table_exists = await pool.fetchval("SELECT to_regclass('public.vrp_job_routes')")
    if not table_exists:
        return {"job_id": job_id, "status": job["status"], "routes": [], "geojson": {"type": "FeatureCollection", "features": []}}

    route_rows = await pool.fetch(
        """
        SELECT fieldman_id, route_index, distance, duration, start_lat, start_long, geometry, tasks
        FROM vrp_job_routes
        WHERE job_id = $1
        ORDER BY route_index
        """,
        job_id,
    )

    task_rows = await pool.fetch(
        """
        SELECT a.fieldman_id, a.task_id, a.sequence, a.distance, a.duration,
               COALESCE(a.status, 'pending') AS status, a.completed_at,
               t.address, t.latitude, t.longitude, COALESCE(t.service, 0) AS service,
               COALESCE(t.priority, 1.0) AS priority,
               t.manual_priority,
               COALESCE(t.task_type, 'credit_investigation') AS task_type,
               t.bank
        FROM vrp_assignments a
        JOIN tasks t ON t.id = a.task_id
        WHERE a.job_id = $1
        ORDER BY a.fieldman_id, a.sequence
        """,
        job_id,
    )

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
                geometry = json.loads(raw_geom)
            except (json.JSONDecodeError, TypeError):
                geometry = {"type": "LineString", "coordinates": []}
        elif raw_geom:
            geometry = raw_geom
        else:
            geometry = {"type": "LineString", "coordinates": []}
        routes.append(
            {
                "fieldman_id": fieldman_id,
                "distance": float(row["distance"] or 0.0),
                "duration": float(row["duration"] or 0.0),
                "start_lat": float(row["start_lat"]) if row["start_lat"] is not None else None,
                "start_long": float(row["start_long"]) if row["start_long"] is not None else None,
                "tasks": task_map.get(fieldman_id, []),
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
                    "task_count": len(task_map.get(fieldman_id, [])),
                },
                "geometry": geometry,
            }
        )

    result = {
        "job_id": job_id,
        "status": job["status"],
        "routes": routes,
        "geojson": {"type": "FeatureCollection", "features": features},
    }

    # Cache completed job previews for 5 minutes (data is immutable)
    if job["status"] in ("completed", "ready"):
        await cache.set(cache_key, result, ttl=int(CacheTTL.MEDIUM))

    return result


@router.get("/vrp/jobs/{job_id}/preview-page", response_class=HTMLResponse)
async def preview_job_html(job_id: str) -> HTMLResponse:
    """Return a self-contained HTML page for previewing job routes."""
    from repositories.task_repository import TaskRepository
    from repositories.fieldman_repository import FieldmanRepository
    from repositories.vrp_repository import VRPJobRepository
    from repositories.audit_repository import AuditRepository
    from services.vrp_service import VRPService

    pool = await get_db_pool()
    cache = await get_cache_service()
    service = VRPService(
        task_repo=TaskRepository(pool),
        fieldman_repo=FieldmanRepository(pool),
        vrp_repo=VRPJobRepository(pool),
        audit_repo=AuditRepository(pool),
        cache=cache,
    )
    html = await service.preview_job_html(job_id)
    return HTMLResponse(content=html)


@router.post("/vrp/jobs/{job_id}/finalize")
async def finalize_job(
    job_id: str,
    current_user: Optional[str] = Depends(get_current_user),
) -> Dict[str, Any]:
    pool = await get_db_pool()
    updated = await pool.execute(
        "UPDATE vrp_jobs SET status = $1, finalized_at = NOW() WHERE id = $2 AND status != $1",
        "finalized",
        job_id,
    )
    if updated.startswith("UPDATE 0"):
        raise HTTPException(status_code=404, detail="Job not found or already finalized")
    return {"job_id": job_id, "status": "finalized"}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# GENERATOR & SETTINGS ENDPOINTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@router.get("/vrp/areas")
async def get_areas() -> Dict[str, Any]:
    """Return available area configurations."""
    return {
        "areas": {
            name: {
                "lat_min": cfg["lat_min"],
                "lat_max": cfg["lat_max"],
                "lon_min": cfg["lon_min"],
                "lon_max": cfg["lon_max"],
                "center_lat": cfg["center"][0],
                "center_lon": cfg["center"][1],
            }
            for name, cfg in AREA_CONFIGS.items()
        }
    }


@router.get("/vrp/task-summary", response_model=TaskSummaryResponse)
async def get_task_summary(
    cache: CacheService = Depends(get_cache_service),
) -> TaskSummaryResponse:
    """Return task counts grouped by task_type and bank."""
    cached = await cache.get(cache.key_task_summary())
    if cached is not None:
        return TaskSummaryResponse(**cached)
    pool = await get_db_pool()
    await _ensure_columns(pool)

    total_tasks = await pool.fetchval("SELECT COUNT(*) FROM tasks") or 0
    total_fieldmen = await pool.fetchval("SELECT COUNT(*) FROM fm_home_locations") or 0

    type_rows = await pool.fetch(
        "SELECT COALESCE(task_type, 'unknown') AS tt, COUNT(*) AS cnt FROM tasks GROUP BY tt"
    )
    by_type = {row["tt"]: int(row["cnt"]) for row in type_rows}

    bank_rows = await pool.fetch(
        "SELECT COALESCE(bank, 'unassigned') AS b, COUNT(*) AS cnt FROM tasks GROUP BY b"
    )
    by_bank = {row["b"]: int(row["cnt"]) for row in bank_rows}

    result = TaskSummaryResponse(
        total_tasks=int(total_tasks),
        total_fieldmen=int(total_fieldmen),
        by_type=by_type,
        by_bank=by_bank,
    )
    await cache.set(cache.key_task_summary(), result.model_dump(), ttl=CacheTTL.TASK_SUMMARY)
    return result


@router.post("/vrp/randomize", response_model=RandomizeResponse, deprecated=True)
async def randomize_data(payload: RandomizeRequest) -> RandomizeResponse:
    """Generate random tasks and fieldmen in the database.

    Supports multiple jobs (num_jobs > 1) and custom area center.
    """
    import traceback as _tb
    try:
        return await _randomize_impl(payload)
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Randomize failed")
        raise HTTPException(status_code=500, detail=f"Randomize error: {exc}")


async def _randomize_impl(payload: RandomizeRequest) -> RandomizeResponse:
    pool = await get_db_pool()
    await _ensure_columns(pool)

    # Resolve area config â€” custom center overrides predefined area
    if payload.center_lat is not None and payload.center_lng is not None:
        radius_deg = payload.area_radius_km / 111.0
        area_cfg = {
            "lat_min": payload.center_lat - radius_deg,
            "lat_max": payload.center_lat + radius_deg,
            "lon_min": payload.center_lng - radius_deg,
            "lon_max": payload.center_lng + radius_deg,
            "center": (payload.center_lat, payload.center_lng),
        }
        area_label = f"CUSTOM({payload.center_lat:.4f},{payload.center_lng:.4f})"
    else:
        area_cfg = AREA_CONFIGS.get(payload.task_area)
        if not area_cfg:
            raise HTTPException(status_code=400, detail=f"Unknown area: {payload.task_area}")
        area_label = payload.task_area

    bank_dist = _parse_bank_distribution(payload.task_banks)
    service_seconds = payload.service_time_minutes * 60
    num_jobs = payload.num_jobs

    total_task_rows: List[Tuple] = []
    total_fm_rows: List[Tuple] = []
    total_type_counts: Dict[str, int] = {"credit_investigation": 0, "skips_collect": 0, "demand_letter": 0}
    total_area_assignments: List[Tuple] = []

    for job_idx in range(num_jobs):
        job_label = f"J{job_idx + 1}" if num_jobs > 1 else ""

        # Build task rows with placeholder addresses
        for task_type, count in [
            ("credit_investigation", payload.tasks_ci),
            ("skips_collect", payload.tasks_sc),
            ("demand_letter", payload.tasks_dl),
        ]:
            prefix = TASK_TYPES[task_type]
            for i in range(count):
                lat, lon = _generate_point(area_cfg, payload.scatterness, payload.area_radius_km)
                task_id = str(uuid.uuid4())
                bank = _pick_bank(bank_dist)
                label_parts = [f"{prefix}-{i + 1}"]
                if job_label:
                    label_parts.append(job_label)
                label_parts.append(f"({area_label})")
                address = " ".join(label_parts)
                priority = round(random.uniform(1.0, 10.0), 1)
                total_task_rows.append((task_id, address, lat, lon, priority, service_seconds, task_type, bank))
                total_type_counts[task_type] += 1

        # Build fieldman rows
        if payload.num_fieldmen > 0 and payload.fieldman_areas:
            for j in range(payload.num_fieldmen):
                fm_area_name = random.choice(payload.fieldman_areas)
                fm_area_cfg = AREA_CONFIGS.get(fm_area_name, area_cfg)
                # If custom center, use it for fieldmen too
                if payload.center_lat is not None and payload.center_lng is not None:
                    fm_area_cfg = area_cfg
                    fm_area_name = area_label
                lat, lon = _generate_point(fm_area_cfg, payload.scatterness, payload.area_radius_km)
                user_id = str(uuid.uuid4())
                label = f"FM-{j + 1}"
                if job_label:
                    label += f" {job_label}"
                address = f"{label} ({fm_area_name})"
                total_fm_rows.append((user_id, address, lat, lon))

                for a_name in payload.fieldman_areas:
                    area_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, a_name))
                    total_area_assignments.append((user_id, area_uuid))

    # Insert all into DB in one transaction
    async with pool.acquire() as conn:
        if total_task_rows:
            await conn.executemany(
                "INSERT INTO tasks (id, address, latitude, longitude, priority, service, task_type, bank) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                total_task_rows,
            )
        if total_fm_rows:
            await conn.executemany(
                "INSERT INTO fm_home_locations (user_id, address, home_lat, home_long) "
                "VALUES ($1, $2, $3, $4)",
                total_fm_rows,
            )
        if total_area_assignments:
            await conn.executemany(
                "INSERT INTO fm_assigned_areas (user_id, area_id) VALUES ($1, $2)",
                total_area_assignments,
            )

    # Fetch overview for immediate map display
    overview = await _fetch_overview(pool)

    # Invalidate caches after data mutation
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
        await cache.invalidate_all_jobs()
    except Exception:
        pass

    return RandomizeResponse(
        tasks_created=len(total_task_rows),
        fieldmen_created=len(total_fm_rows),
        task_types=total_type_counts,
        jobs_generated=num_jobs,
        overview=overview,
    )


@router.post("/vrp/data/reset")
async def reset_all_data() -> Dict[str, str]:
    """Truncate task and fieldman data, preserving VRP jobs."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        routes_table_exists = await conn.fetchval(
            "SELECT to_regclass('public.vrp_job_routes')"
        )
        if routes_table_exists:
            legacy_fks = await conn.fetch(
                """
                SELECT conname
                FROM pg_constraint
                WHERE contype = 'f'
                  AND conrelid = 'public.vrp_job_routes'::regclass
                """
            )
            for fk in legacy_fks:
                name = fk["conname"]
                if not name:
                    continue
                await conn.execute(
                    f'ALTER TABLE public.vrp_job_routes DROP CONSTRAINT IF EXISTS "{name}"'
                )

            await conn.execute(
                """
                WITH task_snapshots AS (
                    SELECT
                        a.job_id,
                        a.fieldman_id,
                        jsonb_agg(
                            jsonb_build_object(
                                'task_id', a.task_id,
                                'sequence', a.sequence,
                                'distance', a.distance,
                                'duration', a.duration,
                                'address', t.address,
                                'latitude', t.latitude,
                                'longitude', t.longitude,
                                'service', COALESCE(t.service, 0),
                                'priority', COALESCE(t.priority, 1.0),
                                'manual_priority', t.manual_priority,
                                'task_type', COALESCE(t.task_type, 'credit_investigation'),
                                'bank', t.bank
                            )
                            ORDER BY a.sequence
                        ) AS tasks
                    FROM vrp_assignments a
                    JOIN tasks t ON t.id = a.task_id
                    GROUP BY a.job_id, a.fieldman_id
                )
                UPDATE vrp_job_routes r
                SET tasks = s.tasks
                FROM task_snapshots s
                WHERE r.job_id = s.job_id
                  AND r.fieldman_id = s.fieldman_id
                  AND (
                      r.tasks IS NULL
                      OR jsonb_typeof(r.tasks) <> 'array'
                      OR jsonb_array_length(r.tasks) = 0
                  )
                """
            )
        await conn.execute(
            "TRUNCATE fm_assigned_areas, fm_home_locations, tasks RESTART IDENTITY CASCADE"
        )
    # Invalidate all caches
    try:
        cache = await get_cache_service()
        await cache.invalidate_all()
    except Exception:
        pass
    return {"status": "ok", "message": "Task and fieldman data cleared (jobs preserved)"}


@router.post("/vrp/optimize", response_model=CreateJobResponse, deprecated=True)
async def optimize_routes(payload: OptimizeSettingsRequest) -> CreateJobResponse:
    """Create an optimization job with extended settings."""
    pool = await get_db_pool()
    payload_data = payload.model_dump()

    # â”€â”€ Rescale task priorities to user-specified range â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # When priority_min and/or priority_max are set from the Plan &
    # Dispatch page, rescale ALL existing task priorities into that
    # range so the displayed values match what the user configured.
    if payload.priority_min is not None or payload.priority_max is not None:
        rows = await pool.fetch("SELECT id, COALESCE(priority, 1.0) AS priority FROM tasks")
        if rows:
            old_vals = [float(r["priority"]) for r in rows]
            old_min_val = min(old_vals)
            old_max_val = max(old_vals)
            old_span = old_max_val - old_min_val if old_max_val != old_min_val else 1.0

            # Default bounds: keep existing if not specified
            p_min = float(payload.priority_min if payload.priority_min is not None else old_min_val)
            p_max = float(payload.priority_max if payload.priority_max is not None else old_max_val)
            if p_min > p_max:
                p_min, p_max = p_max, p_min
            new_span = p_max - p_min

            updates = []
            for r in rows:
                old_p = float(r["priority"])
                # Linear rescale from [old_min, old_max] â†’ [p_min, p_max]
                ratio = (old_p - old_min_val) / old_span
                new_p = round(p_min + ratio * new_span, 1)
                updates.append((new_p, str(r["id"])))

            await pool.executemany(
                "UPDATE tasks SET priority = $1 WHERE id = $2",
                updates,
            )
            logger.info(
                "Rescaled %d task priorities from [%.1f, %.1f] â†’ [%.1f, %.1f]",
                len(updates), old_min_val, old_max_val, p_min, p_max,
            )
        # Remove from payload so Celery worker doesn't also filter by range
        payload_data.pop("priority_min", None)
        payload_data.pop("priority_max", None)

    # Convert areas string to area_ids and auto-build task_area_map
    if payload.areas:
        area_names = [a.strip() for a in payload.areas.split(",") if a.strip()]
        area_id_map = {n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in area_names}
        payload_data["area_ids"] = list(area_id_map.values())

        # Build task_area_map: map each task to its area based on coordinates
        await _ensure_columns(pool)
        rows = await pool.fetch("SELECT id, latitude, longitude FROM tasks")
        task_area_map = {}
        for row in rows:
            lat, lon = float(row["latitude"]), float(row["longitude"])
            for a_name, cfg in AREA_CONFIGS.items():
                if a_name not in area_names:
                    continue
                c = cfg["center"]
                r_km = cfg.get("radius_km", 20)  # Generous default radius
                dlat = abs(lat - c[0]) * 111.0
                dlon = abs(lon - c[1]) * 111.0 * 0.9
                if (dlat**2 + dlon**2) ** 0.5 <= r_km:
                    task_area_map[str(row["id"])] = area_id_map[a_name]
                    break
            else:
                # Task doesn't match any area â€” assign to first area so it's included
                if area_names:
                    task_area_map[str(row["id"])] = area_id_map[area_names[0]]

        payload_data["task_area_map"] = task_area_map

        # If no tasks could be mapped, skip area filtering entirely
        if not task_area_map:
            del payload_data["area_ids"]
            del payload_data["task_area_map"]

    job_id = await pool.fetchval(
        "INSERT INTO vrp_jobs (status, request_payload) VALUES ($1, $2) RETURNING id",
        "queued",
        json.dumps(payload_data),
    )
    celery_app.send_task("process_vrp_job", args=[str(job_id)])
    return CreateJobResponse(job_id=str(job_id))


# â”€â”€ Map Picker Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PickerTaskRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    task_type: str = Field("credit_investigation", pattern="^(credit_investigation|skips_collect|demand_letter)$")
    bank: Optional[str] = None
    service_time_minutes: int = Field(30, ge=1, le=480)
    manual_priority: Optional[float] = Field(default=None, ge=0, le=100)


class PickerFieldmanRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    area: str = Field("AREA_NORTH")


class PickerItemResponse(BaseModel):
    id: str
    latitude: float
    longitude: float
    address: Optional[str] = None
    type: str  # "task" or "fieldman"
    task_type: Optional[str] = None
    bank: Optional[str] = None
    manual_priority: Optional[float] = None


@router.post("/vrp/picker/task", response_model=PickerItemResponse)
async def picker_create_task(payload: PickerTaskRequest) -> PickerItemResponse:
    """Create a single task from a map-picked location. Geocodes address in background."""
    pool = await get_db_pool()
    await _ensure_columns(pool)

    task_id = str(uuid.uuid4())
    service_seconds = payload.service_time_minutes * 60
    # Use manual_priority as effective priority if set, else random default (float)
    priority = payload.manual_priority if payload.manual_priority is not None else round(random.uniform(10.0, 100.0), 1)
    prefix = TASK_TYPES.get(payload.task_type, "T")
    placeholder = f"{prefix}-picked ({payload.latitude:.4f}, {payload.longitude:.4f})"

    await pool.execute(
        "INSERT INTO tasks (id, address, latitude, longitude, priority, service, task_type, bank, manual_priority) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        task_id, placeholder, payload.latitude, payload.longitude,
        priority, service_seconds, payload.task_type, payload.bank, payload.manual_priority,
    )

    # Try geocode immediately (fire-and-forget pattern with inline attempt)
    address = placeholder
    try:
        from services.geocode_service import reverse_geocode
        cache = await get_cache_service()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resolved = await reverse_geocode(payload.latitude, payload.longitude, client=client, cache=cache)
            if resolved and not resolved[0].isdigit():
                address = resolved
                await pool.execute("UPDATE tasks SET address = $1 WHERE id = $2", address, task_id)
    except Exception:
        pass  # Keep placeholder address

    # Invalidate overview cache
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass

    return PickerItemResponse(
        id=task_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=address,
        type="task",
        task_type=payload.task_type,
        bank=payload.bank,
        manual_priority=payload.manual_priority,
    )


@router.post("/vrp/picker/fieldman", response_model=PickerItemResponse)
async def picker_create_fieldman(payload: PickerFieldmanRequest) -> PickerItemResponse:
    """Create a single fieldman from a map-picked location. Geocodes address in background."""
    pool = await get_db_pool()

    user_id = str(uuid.uuid4())
    placeholder = f"FM-picked ({payload.latitude:.4f}, {payload.longitude:.4f})"

    await pool.execute(
        "INSERT INTO fm_home_locations (user_id, address, home_lat, home_long) "
        "VALUES ($1, $2, $3, $4)",
        user_id, placeholder, payload.latitude, payload.longitude,
    )

    # Assign area
    area_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, payload.area))
    await pool.execute(
        "INSERT INTO fm_assigned_areas (user_id, area_id) VALUES ($1, $2)",
        user_id, area_uuid,
    )

    # Try geocode immediately
    address = placeholder
    try:
        from services.geocode_service import reverse_geocode
        cache = await get_cache_service()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resolved = await reverse_geocode(payload.latitude, payload.longitude, client=client, cache=cache)
            if resolved and not resolved[0].isdigit():
                address = resolved
                await pool.execute(
                    "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
                    address, user_id,
                )
    except Exception:
        pass

    # Invalidate overview cache
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass

    return PickerItemResponse(
        id=user_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=address,
        type="fieldman",
    )


@router.delete("/vrp/picker/task/{task_id}")
async def picker_delete_task(task_id: str) -> Dict[str, str]:
    """Remove a single picked task."""
    pool = await get_db_pool()
    result = await pool.execute("DELETE FROM tasks WHERE id = $1", task_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Task not found")
    # Invalidate overview cache
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass
    return {"status": "ok", "deleted": task_id}


@router.delete("/vrp/picker/fieldman/{fieldman_id}")
async def picker_delete_fieldman(fieldman_id: str) -> Dict[str, str]:
    """Remove a single picked fieldman."""
    pool = await get_db_pool()
    await pool.execute("DELETE FROM fm_assigned_areas WHERE user_id = $1", fieldman_id)
    result = await pool.execute("DELETE FROM fm_home_locations WHERE user_id = $1", fieldman_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Fieldman not found")
    # Invalidate overview cache
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass
    return {"status": "ok", "deleted": fieldman_id}


# â”€â”€ Task Priority Update â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class UpdateTaskPriorityRequest(BaseModel):
    manual_priority: Optional[float] = Field(default=None, ge=0, le=100)


@router.put("/vrp/picker/task/{task_id}/priority")
async def update_task_priority(task_id: str, payload: UpdateTaskPriorityRequest) -> Dict[str, Any]:
    """Update or clear the manual priority for a task."""
    pool = await get_db_pool()
    result = await pool.execute(
        "UPDATE tasks SET manual_priority = $1 WHERE id = $2",
        payload.manual_priority, task_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass
    return {"status": "ok", "task_id": task_id, "manual_priority": payload.manual_priority}


# â”€â”€ FM Location Update â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class UpdateFieldmanLocationRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


@router.put("/vrp/fieldman/{fieldman_id}/location", response_model=PickerItemResponse)
async def update_fieldman_location(
    fieldman_id: str,
    payload: UpdateFieldmanLocationRequest,
) -> PickerItemResponse:
    """Update a fieldman's home location. Geocodes address in background."""
    pool = await get_db_pool()

    result = await pool.execute(
        "UPDATE fm_home_locations SET home_lat = $1, home_long = $2 WHERE user_id = $3",
        payload.latitude, payload.longitude, fieldman_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Fieldman not found")

    # Update address placeholder
    address = f"FM-moved ({payload.latitude:.4f}, {payload.longitude:.4f})"
    await pool.execute(
        "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
        address, fieldman_id,
    )

    # Try geocode immediately
    try:
        from services.geocode_service import reverse_geocode
        cache = await get_cache_service()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resolved = await reverse_geocode(
                payload.latitude, payload.longitude, client=client, cache=cache,
            )
            if resolved and not resolved[0].isdigit():
                address = resolved
                await pool.execute(
                    "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
                    address, fieldman_id,
                )
    except Exception:
        pass

    # Invalidate overview cache
    try:
        cache = await get_cache_service()
        await cache.invalidate_overview()
    except Exception:
        pass

    return PickerItemResponse(
        id=fieldman_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=address,
        type="fieldman",
    )


# â”€â”€ Geocode Existing Tasks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class GeocodeResponse(BaseModel):
    updated: int
    total: int


@router.post("/vrp/geocode-addresses", response_model=GeocodeResponse)
async def geocode_existing_addresses() -> GeocodeResponse:
    """
    Dispatch bulk geocoding to Celery worker (non-blocking).
    Returns immediately with estimated count. Actual geocoding happens in background.
    """
    from celery_app import celery_app as _celery

    pool = await get_db_pool()

    task_rows = await pool.fetch("SELECT id, latitude, longitude FROM tasks")
    fm_rows = await pool.fetch("SELECT user_id, home_lat, home_long FROM fm_home_locations")

    items = []
    for row in task_rows:
        items.append({
            "type": "task",
            "id": str(row["id"]),
            "lat": float(row["latitude"]),
            "lon": float(row["longitude"]),
        })
    for row in fm_rows:
        items.append({
            "type": "fieldman",
            "id": str(row["user_id"]),
            "lat": float(row["home_lat"]),
            "lon": float(row["home_long"]),
        })

    total = len(items)
    if total > 0:
        _celery.send_task("background_bulk_geocode", args=[items])
        logger.info("Dispatched bulk geocode to Celery: %d items", total)

    return GeocodeResponse(updated=0, total=total)
