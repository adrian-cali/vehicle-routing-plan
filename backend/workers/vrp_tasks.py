"""
Celery task: process_vrp_job.

Loads tasks and fieldmen from the database, builds VROOM payloads,
calls the OSRM distance matrix, submits to VROOM, stores results,
and publishes progress via Redis pub/sub.

Uses repositories for all DB access and centralized config.
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import redis.asyncio as aioredis

from celery_app import celery_app
from core.config import get_settings
from core.logging import get_logger, setup_logging
from repositories.fieldman_repository import FieldmanRepository
from repositories.task_repository import TaskRepository
from repositories.vrp_repository import VRPJobRepository
from services.h3_utils import (
    group_tasks_by_h3,
    latlng_to_h3,
    choose_adaptive_resolution,
    suggest_h3_resolution,
    build_fieldman_coverage_map,
    build_cell_to_fieldman_index,
    partition_problem_h3,
    h3_spatial_sort,
    assign_tasks_to_fieldmen_h3,
    _haversine_km,
)
from services.osrm_service import OSRMService
from services.vroom_service import VROOMService
from services.cache_service import get_cache_service, CacheService

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Return a running or new event loop for the Celery worker."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


async def _get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )


async def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _create_cache_service() -> CacheService:
    """Create a fresh CacheService for the current event loop."""
    try:
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await redis_client.ping()
        return CacheService(redis_client)
    except Exception:
        logger.warning("Cache unavailable for this VRP job, proceeding without cache")
        return CacheService(None)  # type: ignore[arg-type]


async def _notify_job(
    redis_client: aioredis.Redis,
    job_id: str,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Publish a job event to Redis for WebSocket consumers."""
    message: Dict[str, Any] = {"event": event, "job_id": job_id}
    if payload:
        message.update(payload)
    await redis_client.publish(f"vrp:job:{job_id}", json.dumps(message))


async def _compute_route_leg_metrics(
    osrm: OSRMService,
    coordinates: List[Tuple[float, float]],
    semaphore: asyncio.Semaphore,
) -> List[Dict[str, float]]:
    """Compute distance/duration for each consecutive coordinate pair."""

    async def _leg(start: Tuple[float, float], end: Tuple[float, float]) -> Dict[str, float]:
        async with semaphore:
            return await osrm.get_distance_duration(start, end)

    coros = [_leg(coordinates[i], coordinates[i + 1]) for i in range(len(coordinates) - 1)]
    results = await asyncio.gather(*coros)
    return list(results)


# ── Celery Task ───────────────────────────────────────────────────────


@celery_app.task(name="process_vrp_job")
def process_vrp_job(job_id: str) -> None:
    """Synchronous entry point called by Celery."""
    # Reset cache singleton so each task gets a fresh Redis client
    # bound to the current event loop (fixes Windows --pool=solo).
    import services.cache_service as _cs
    _cs._cache_service = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_process_vrp_job_async(job_id))
    finally:
        loop.close()


async def _process_vrp_job_async(job_id: str) -> None:
    """Core async VRP processing pipeline."""
    pool = await _get_pool()
    redis_client = await _get_redis()

    vrp_repo = VRPJobRepository(pool)
    task_repo = TaskRepository(pool)
    fieldman_repo = FieldmanRepository(pool)
    # Create a fresh cache per job to avoid cross-event-loop issues
    cache = await _create_cache_service()
    osrm = OSRMService(
        settings.osrm_url,
        timeout=settings.osrm_timeout,
        max_locations=settings.osrm_max_locations,
        cache=cache,
    )
    vroom = VROOMService(settings.vroom_url, timeout=settings.vroom_timeout)

    try:
        t_job_start = _time.monotonic()
        # ── 1. Mark job as started ────────────────────────────────────
        await vrp_repo.update_status(job_id, "started")
        await _notify_job(redis_client, job_id, "job_started")
        logger.info("VRP job %s started", job_id)

        # ── 2. Load request payload ───────────────────────────────────
        request = await vrp_repo.get_job_payload(job_id)
        if not request:
            raise ValueError("Job not found")

        # ── 3. Load tasks & fieldmen via repositories ─────────────────
        tasks = await task_repo.list_tasks(
            task_ids=request.get("task_ids"),
            limit=request.get("task_limit"),
            offset=request.get("task_offset"),
            priority_min=request.get("priority_min"),
            priority_max=request.get("priority_max"),
        )
        fieldmen = await fieldman_repo.list_fieldmen(
            fieldman_ids=request.get("fieldman_ids"),
            limit=request.get("fieldman_limit"),
            offset=request.get("fieldman_offset"),
        )
        await fieldman_repo.resolve_current_locations(redis_client, fieldmen)

        if not tasks or not fieldmen:
            await vrp_repo.update_status(job_id, "failed", "no tasks or fieldmen")
            await _notify_job(redis_client, job_id, "job_failed", {"reason": "no tasks or fieldmen"})
            return

        # ── 4. Validate strategy parameters ───────────────────────────
        assignment_strategy = request.get("assignment_strategy", "h3")
        h3_resolution = int(request.get("h3_resolution", settings.h3_default_resolution))
        h3_auto = request.get("h3_auto_resolution", True)  # Default: auto-detect
        task_area_map = request.get("task_area_map")
        area_ids = request.get("area_ids")

        if area_ids and not task_area_map:
            await vrp_repo.update_status(job_id, "failed", "task_area_map required for area filtering")
            await _notify_job(redis_client, job_id, "job_failed", {"reason": "task_area_map required for area filtering"})
            return

        if area_ids and task_area_map:
            allowed = set(area_ids)
            tasks = [t for t in tasks if task_area_map.get(str(t["id"])) in allowed]

        if assignment_strategy == "manual_area" and not task_area_map:
            await vrp_repo.update_status(job_id, "failed", "task_area_map required")
            await _notify_job(redis_client, job_id, "job_failed", {"reason": "task_area_map required"})
            return

        # ── 5. Adaptive H3 resolution ────────────────────────────────
        h3_resolution_info: Dict[str, Any] = {}
        if assignment_strategy == "h3":
            # Build coordinate list for bbox-based suggestion
            task_coords = [
                (float(t["latitude"]), float(t["longitude"]))
                for t in tasks
            ]
            suggested_res = suggest_h3_resolution(task_coords)

            # Also run density-based adaptive selection for comparison
            adaptive_res = choose_adaptive_resolution(
                tasks,
                min_res=7,
                max_res=10,
                target_tasks_per_cell=max(3, len(tasks) // max(1, len(fieldmen))),
            )

            # Compute bbox diagonal for metadata
            if len(task_coords) >= 2:
                lats = [c[0] for c in task_coords]
                lngs = [c[1] for c in task_coords]
                bbox_diag = _haversine_km(min(lats), min(lngs), max(lats), max(lngs))
            else:
                bbox_diag = 0.0

            # Use suggested resolution if auto-detect is enabled
            if h3_auto:
                h3_resolution = suggested_res
                h3_resolution_info = {
                    "resolution": h3_resolution,
                    "mode": "auto",
                    "auto_suggested": suggested_res,
                    "bbox_diagonal_km": round(bbox_diag, 2),
                }
                logger.info(
                    "Using auto H3 resolution: %d (suggest=%d, adaptive=%d, bbox=%.1fkm)",
                    h3_resolution, suggested_res, adaptive_res, bbox_diag,
                )
            else:
                h3_resolution_info = {
                    "resolution": h3_resolution,
                    "mode": "manual",
                    "auto_suggested": suggested_res,
                    "bbox_diagonal_km": round(bbox_diag, 2),
                }
                logger.info(
                    "Using manual H3 resolution: %d (auto would suggest %d, bbox=%.1fkm)",
                    h3_resolution, suggested_res, bbox_diag,
                )

            group_tasks_by_h3(tasks, h3_resolution)

        # ── 5b. Build fieldman H3 coverage maps ──────────────────────
        fieldman_h3_map: Dict[str, List[str]] = {}
        cell_to_fm_index: Optional[Dict[str, List[str]]] = None
        coverage_k = min(settings.h3_max_k, 3)  # k-ring coverage radius

        if assignment_strategy == "h3":
            for fieldman in fieldmen:
                cell = latlng_to_h3(
                    float(fieldman["current_lat"]),
                    float(fieldman["current_long"]),
                    h3_resolution,
                )
                fieldman_h3_map.setdefault(cell, []).append(fieldman["user_id"])

            # Build multi-cell coverage map
            coverage_map = build_fieldman_coverage_map(
                fieldmen, resolution=h3_resolution, coverage_k=coverage_k,
            )
            cell_to_fm_index = build_cell_to_fieldman_index(coverage_map)
            logger.info(
                "H3 coverage: %d fieldmen cover %d unique cells (k=%d)",
                len(fieldmen), len(cell_to_fm_index), coverage_k,
            )

        # ── 5c. Build partitions ──────────────────────────────────────
        partitions = None
        if assignment_strategy == "h3":
            # ── H3 pre-assignment: distribute tasks to fieldmen ──
            # Uses H3 grid distance + load balancing to assign tasks
            # to fieldmen, then VROOM only optimizes route ORDER.
            h3_assignments = assign_tasks_to_fieldmen_h3(
                tasks, fieldmen,
                resolution=h3_resolution,
                max_k=settings.h3_max_k,
                load_balance_weight=0.3,
            )

            fm_map = {str(fm["user_id"]): fm for fm in fieldmen}
            partitions = []
            for fm_id, fm_tasks in h3_assignments.items():
                if fm_tasks:
                    partitions.append({
                        "tasks": fm_tasks,
                        "fieldmen": [fm_map[fm_id]],
                        "region": f"fm:{fm_id[:8]}",
                        "h3_preassigned": True,
                    })

            logger.info(
                "H3 pre-assigned %d tasks to %d fieldmen: %s",
                len(tasks), len(partitions),
                {p["region"]: len(p["tasks"]) for p in partitions},
            )

            # ── Sub-split oversized partitions ──────────────────────────
            # If any FM got more tasks than max_tasks_per_partition (e.g. dense
            # urban area), split into sub-partitions so VROOM doesn't choke.
            max_per_part = settings.max_tasks_per_partition
            split_partitions: List[Dict[str, Any]] = []
            for part in partitions:
                if len(part["tasks"]) <= max_per_part:
                    split_partitions.append(part)
                else:
                    # Sub-split using spatial sort to keep nearby tasks together
                    sorted_chunk = h3_spatial_sort(part["tasks"], h3_resolution)
                    for chunk_idx in range(0, len(sorted_chunk), max_per_part):
                        chunk = sorted_chunk[chunk_idx:chunk_idx + max_per_part]
                        split_partitions.append({
                            "tasks": chunk,
                            "fieldmen": part["fieldmen"],
                            "region": f"{part['region']}:sub{chunk_idx // max_per_part}",
                            "h3_preassigned": True,
                        })
                    logger.info(
                        "Sub-split partition %s (%d tasks) into %d chunks of max %d",
                        part["region"], len(part["tasks"]),
                        -(-len(part["tasks"]) // max_per_part),  # ceil division
                        max_per_part,
                    )
            partitions = split_partitions
        else:
            # Non-H3: optionally partition large problems by region to bound matrix size
            if len(tasks) > settings.h3_partition_threshold:
                partitions = partition_problem_h3(
                    tasks,
                    fieldmen,
                    resolution=h3_resolution,
                    max_partition_size=settings.h3_max_partition_size,
                )
            else:
                partitions = [{"tasks": list(tasks), "fieldmen": list(fieldmen), "region": "single"}]

        # ── Process each partition (in parallel) ───────────────────────
        all_assignment_rows: List[Dict[str, Any]] = []
        all_route_rows: List[Dict[str, Any]] = []

        async def _process_partition(
            part_idx: int,
            partition: Dict[str, Any],
        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            """Process a single partition: OSRM matrix → VROOM solve → route geometry.
            
            Returns (assignment_rows, route_rows).
            """
            part_tasks = partition["tasks"]
            part_fieldmen = partition["fieldmen"]
            part_region = partition["region"]

            if not part_tasks or not part_fieldmen:
                logger.warning("Skipping empty partition %d (region=%s)", part_idx, part_region)
                return [], []

            t0 = _time.monotonic()
            logger.info(
                "Processing partition %d/%d: region=%s, tasks=%d, fieldmen=%d",
                part_idx + 1, len(partitions), part_region,
                len(part_tasks), len(part_fieldmen),
            )

            # Spatially sort tasks within partition
            if assignment_strategy == "h3":
                part_tasks = h3_spatial_sort(part_tasks, h3_resolution)

            # ── 6. Build location index for this partition ────────────
            location_list: List[Tuple[float, float]] = []
            location_index_map: Dict[Tuple[float, float], int] = {}

            def _index_location(lat: float, lon: float) -> int:
                key = (float(lat), float(lon))
                if key not in location_index_map:
                    location_index_map[key] = len(location_list)
                    location_list.append(key)
                return location_index_map[key]

            for fieldman in part_fieldmen:
                _index_location(fieldman["current_lat"], fieldman["current_long"])
            for task in part_tasks:
                _index_location(task["latitude"], task["longitude"])

            # ── 7. Build VROOM payloads ───────────────────────────────
            part_strategy = "route_only" if partition.get("h3_preassigned") else assignment_strategy

            jobs_payload = vroom.build_vroom_jobs_from_tasks(
                part_tasks,
                assignment_strategy=part_strategy,
                h3_resolution=h3_resolution,
                task_area_map=task_area_map,
                fieldman_h3_map=fieldman_h3_map,
                location_index_map=location_index_map,
                max_h3_k=settings.h3_max_k,
                cell_to_fm_index=cell_to_fm_index,
            )
            vehicles_payload = vroom.build_vroom_vehicles_from_fieldmen(
                part_fieldmen,
                assignment_strategy=part_strategy,
                h3_resolution=h3_resolution,
                location_index_map=location_index_map,
                coverage_k=coverage_k,
            )

            vroom.normalize_skills(jobs_payload, vehicles_payload)

            # Map string IDs → integer IDs (VROOM requires int)
            job_id_map = {job["id"]: idx + 1 for idx, job in enumerate(jobs_payload)}
            vehicle_id_map = {v["id"]: idx + 1 for idx, v in enumerate(vehicles_payload)}
            job_id_reverse = {v: k for k, v in job_id_map.items()}
            vehicle_id_reverse = {v: k for k, v in vehicle_id_map.items()}

            for job in jobs_payload:
                job["id"] = job_id_map[job["id"]]
            for vehicle in vehicles_payload:
                vehicle["id"] = vehicle_id_map[vehicle["id"]]

            # ── 8. OSRM distance matrix ──────────────────────────────
            t_matrix = _time.monotonic()
            matrix_data = await osrm.get_distance_matrix(location_list)
            logger.info("  Partition %d matrix: %.1fs (%d locations)", part_idx, _time.monotonic() - t_matrix, len(location_list))

            # ── 9. Submit to VROOM ────────────────────────────────────
            MAX_COST = 999999
            raw_durations = matrix_data.get("durations") or matrix_data.get("distances") or []
            durations_matrix = [
                [int(round(v)) if v is not None else MAX_COST for v in row]
                for row in raw_durations
            ]
            vroom_payload: Dict[str, Any] = {
                "jobs": jobs_payload,
                "vehicles": vehicles_payload,
                "matrix": durations_matrix,
            }
            t_vroom = _time.monotonic()
            solution = await vroom.submit_vroom_request(vroom_payload)
            logger.info("  Partition %d VROOM solve: %.1fs", part_idx, _time.monotonic() - t_vroom)
            assignments = vroom.parse_solution(
                solution,
                job_id_map=job_id_reverse,
                vehicle_id_map=vehicle_id_reverse,
            )

            # ── 9b. Enforce strict priority ordering (chained greedy) ──
            #
            # BEFORE (BUG): All distances were computed from the fieldman's
            # fixed start location, so the tiebreaker always picked the task
            # closest to _home_, not closest to the _current position_ in
            # the route chain.  Result: start→task_N for every leg.
            #
            # AFTER (FIX): Greedy chained approach.  At each step we:
            #   1) Find the highest priority among remaining tasks.
            #   2) Among those equal-priority candidates, pick the one
            #      nearest to the CURRENT position (starts at FM location,
            #      then moves to the last-assigned task).
            #   3) Advance current position → chosen task's location.
            #
            # Result: start→task_1→task_2→task_3 … (true sequential chain).
            # Priority still dominates — a higher-priority task always comes
            # before a lower-priority one regardless of distance.  Distance
            # is only the tiebreaker within the same priority level.
            #
            import math as _math
            task_priority_map = {}
            task_coord_map: Dict[str, Tuple[float, float]] = {}
            for t in part_tasks:
                tid = str(t["id"])
                mp = t.get("manual_priority")
                p = float(mp) if mp is not None else float(t.get("priority", 1))
                task_priority_map[tid] = p
                task_coord_map[tid] = (float(t["latitude"]), float(t["longitude"]))

            def _sq_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
                """Approximate squared planar distance (meters²). Good enough for ranking."""
                cos_lat = _math.cos(_math.radians((lat1 + lat2) / 2.0))
                dlat = (lat2 - lat1) * 111_000.0
                dlon = (lon2 - lon1) * 111_000.0 * max(0.2, cos_lat)
                return dlat * dlat + dlon * dlon

            fieldman_map_tmp = {str(f["user_id"]): f for f in part_fieldmen}
            for route in assignments.get("routes", []):
                task_ids = route.get("tasks", [])
                if len(task_ids) <= 1:
                    continue
                fm = fieldman_map_tmp.get(route["vehicle_id"])
                if fm:
                    cur_lat = float(fm["current_lat"])
                    cur_lon = float(fm["current_long"])
                else:
                    cur_lat, cur_lon = 0.0, 0.0

                # Greedy chained ordering: pick nearest among highest-priority remaining
                remaining = set(task_ids)
                ordered: List[str] = []
                while remaining:
                    # 1. Find the highest priority among remaining tasks
                    max_prio = max(task_priority_map.get(tid, 1.0) for tid in remaining)
                    # 2. Among candidates with that priority, pick nearest to current pos
                    best_tid: Optional[str] = None
                    best_dist = 1e18
                    for tid in remaining:
                        if task_priority_map.get(tid, 1.0) < max_prio:
                            continue
                        tc = task_coord_map.get(tid)
                        if tc:
                            d = _sq_dist(cur_lat, cur_lon, tc[0], tc[1])
                        else:
                            d = 1e18
                        if d < best_dist:
                            best_dist = d
                            best_tid = tid
                    if best_tid is None:
                        # Shouldn't happen, but safety fallback
                        best_tid = next(iter(remaining))
                    ordered.append(best_tid)
                    remaining.discard(best_tid)
                    # 3. Advance current position to chosen task's location
                    tc = task_coord_map.get(best_tid)
                    if tc:
                        cur_lat, cur_lon = tc[0], tc[1]

                route["tasks"] = ordered

            # ── 10. Compute per-leg metrics & geometry ────────────────
            task_map = {str(t["id"]): t for t in part_tasks}
            fieldman_map = {str(f["user_id"]): f for f in part_fieldmen}
            semaphore = asyncio.Semaphore(settings.osrm_concurrency)

            async def _process_route(route_index: int, route: Dict[str, Any]) -> Optional[Tuple[Dict, Dict]]:
                vehicle_id = route["vehicle_id"]
                task_ids_in_route = route["tasks"]
                fieldman = fieldman_map.get(vehicle_id)
                if not fieldman or not task_ids_in_route:
                    return None

                coords: List[Tuple[float, float]] = [
                    (fieldman["current_lat"], fieldman["current_long"])
                ]
                for tid in task_ids_in_route:
                    task = task_map.get(tid)
                    if task:
                        coords.append((float(task["latitude"]), float(task["longitude"])))
                coords.append((fieldman["current_lat"], fieldman["current_long"]))

                async with semaphore:
                    route_data = await osrm.get_route_with_legs(coords)

                leg_metrics = route_data.get("legs", [])
                rows = []
                for idx, tid in enumerate(task_ids_in_route):
                    metrics = leg_metrics[idx] if idx < len(leg_metrics) else {"distance": 0.0, "duration": 0.0}
                    rows.append((
                        job_id,
                        vehicle_id,
                        tid,
                        idx + 1,
                        float(metrics.get("distance", 0.0)),
                        float(metrics.get("duration", 0.0)),
                    ))

                assignment_entry = {"vehicle_id": vehicle_id, "rows": rows}
                # Store full task snapshot so previews survive data resets
                task_snapshots = []
                for idx, tid in enumerate(task_ids_in_route):
                    task = task_map.get(tid)
                    metrics = leg_metrics[idx] if idx < len(leg_metrics) else {"distance": 0.0, "duration": 0.0}
                    task_snapshots.append({
                        "task_id": tid,
                        "sequence": idx + 1,
                        "distance": float(metrics.get("distance", 0.0)),
                        "duration": float(metrics.get("duration", 0.0)),
                        "latitude": float(task["latitude"]) if task else 0.0,
                        "longitude": float(task["longitude"]) if task else 0.0,
                        "address": task.get("address", "") if task else "",
                        "service": int(task.get("service", 0)) if task else 0,
                        "priority": float(task.get("priority", 1.0)) if task else 1.0,
                        "manual_priority": float(task["manual_priority"]) if task and task.get("manual_priority") is not None else None,
                        "task_type": task.get("task_type", "credit_investigation") if task else "credit_investigation",
                        "bank": task.get("bank") if task else None,
                    })
                route_entry = {
                    "fieldman_id": vehicle_id,
                    "route_index": route_index,
                    "distance": float(route_data.get("distance", 0.0)),
                    "duration": float(route_data.get("duration", 0.0)),
                    "start_lat": float(fieldman["current_lat"]),
                    "start_long": float(fieldman["current_long"]),
                    "geometry": route_data.get("geometry", {"type": "LineString", "coordinates": []}),
                    "tasks": task_snapshots,
                }
                return assignment_entry, route_entry

            t_routes = _time.monotonic()
            results = await asyncio.gather(*[
                _process_route(part_idx * 100 + idx + 1, route)
                for idx, route in enumerate(assignments.get("routes", []))
            ])
            logger.info("  Partition %d routes: %.1fs", part_idx, _time.monotonic() - t_routes)

            part_assignments = []
            part_routes = []
            for result in results:
                if result:
                    part_assignments.append(result[0])
                    part_routes.append(result[1])

            logger.info(
                "Partition %d/%d complete: %d routes in %.1fs total",
                part_idx + 1, len(partitions),
                len(part_routes), _time.monotonic() - t0,
            )
            return part_assignments, part_routes

        # ── Run all partitions in parallel (bounded concurrency) ─────
        t_all = _time.monotonic()
        partition_sem = asyncio.Semaphore(10)  # Limit concurrent partitions to avoid pool exhaustion

        async def _bounded_partition(idx: int, partition: Dict[str, Any]):
            async with partition_sem:
                return await _process_partition(idx, partition)

        partition_results = await asyncio.gather(*[
            _bounded_partition(idx, partition)
            for idx, partition in enumerate(partitions)
        ], return_exceptions=True)

        failed_count = 0
        for idx, result in enumerate(partition_results):
            if isinstance(result, Exception):
                failed_count += 1
                logger.error(
                    "Partition %d/%d failed (region=%s, tasks=%d): %s",
                    idx + 1, len(partitions),
                    partitions[idx].get("region", "?"),
                    len(partitions[idx].get("tasks", [])),
                    result,
                )
            else:
                part_assignments, part_routes = result
                all_assignment_rows.extend(part_assignments)
                all_route_rows.extend(part_routes)

        if failed_count > 0:
            logger.warning(
                "%d/%d partitions failed — persisting %d successful routes",
                failed_count, len(partitions), len(all_route_rows),
            )
        if not all_route_rows and failed_count == len(partitions):
            raise RuntimeError(f"All {len(partitions)} partitions failed")

        logger.info(
            "All %d partitions processed in %.1fs (parallel)",
            len(partitions), _time.monotonic() - t_all,
        )

        # ── 11. Persist results ───────────────────────────────────────
        await vrp_repo.store_assignments(job_id, all_assignment_rows)
        await vrp_repo.store_routes(job_id, all_route_rows)
        if cache:
            try:
                await cache.invalidate_job(job_id)
            except Exception as exc:
                logger.warning("Cache invalidation failed for job %s: %s", job_id, exc)

        # Store resolution info as status detail JSON
        status_detail = None
        if h3_resolution_info:
            status_detail = json.dumps({"h3_resolution_info": h3_resolution_info})
        await vrp_repo.update_status(job_id, "ready", status_detail)
        await _notify_job(
            redis_client,
            job_id,
            "job_ready",
            {"h3_resolution_info": h3_resolution_info} if h3_resolution_info else None,
        )
        logger.info(
            "VRP job %s completed: %d routes (H3-optimized) in %.1fs total",
            job_id, len(all_route_rows), _time.monotonic() - t_job_start,
        )

    except Exception as exc:
        logger.exception("VRP job %s failed: %s", job_id, exc)
        await vrp_repo.update_status(job_id, "failed", str(exc))
        await _notify_job(redis_client, job_id, "job_failed", {"reason": str(exc)})
    finally:
        await redis_client.close()
        await pool.close()
