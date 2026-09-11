"""VROOM payload builder and HTTP client.

Builds VROOM-compatible job payloads from tasks/fieldmen and
submits them to the VROOM optimization engine.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
import asyncio

from services.h3_utils import (
    find_nearest_fm_h3,
    latlng_to_h3,
    expand_h3_k_ring,
    build_fieldman_coverage_map,
    build_cell_to_fieldman_index,
    h3_spatial_sort,
)

Coord = Tuple[float, float]  # (lat, lon)


def _priority_weight(priority: float, manual_priority: float | None = None) -> int:
    """Map priority to VROOM priority (0-100, higher = more important).
    
    If manual_priority is set, it takes precedence over the default priority.
    This implements the task-level priority override system.
    """
    effective = manual_priority if manual_priority is not None else priority
    return max(0, min(100, int(round(effective))))


def _skill_for_area(area_id: str) -> str:
    return f"area:{area_id}"


def _skill_for_h3(cell: str) -> str:
    return f"h3:{cell}"


class VROOMService:
    """httpx client for VROOM — connection pooling within a job."""

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return (or create) a persistent httpx client for this instance."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=60, max_keepalive_connections=30),
            )
        return self._client

    def build_job_payloads(
        self,
        tasks: Sequence[Dict[str, Any]],
        *,
        assignment_strategy: str,
        h3_resolution: int,
        task_area_map: Optional[Dict[str, str]] = None,
        fieldman_h3_map: Optional[Dict[str, List[str]]] = None,
        location_index_map: Optional[Dict[Tuple[float, float], int]] = None,
        max_h3_k: int = 3,
        cell_to_fm_index: Optional[Dict[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build VROOM job payloads from tasks.

        When cell_to_fm_index is provided (multi-cell coverage mode),
        each task gets skills matching ALL fieldmen whose coverage
        includes the task's H3 cell.  This lets VROOM pick the best
        fieldman from multiple candidates instead of locking to one.
        """
        # Pre-sort tasks spatially for better VROOM initial solutions
        sorted_tasks = h3_spatial_sort(tasks, h3_resolution)

        jobs: List[Dict[str, Any]] = []
        for task in sorted_tasks:
            task_id = str(task["id"])
            lat = float(task["latitude"])
            lon = float(task["longitude"])
            priority = float(task.get("priority", 1))
            manual_priority = task.get("manual_priority")
            manual_priority = float(manual_priority) if manual_priority is not None else None
            job: Dict[str, Any] = {
                "id": task_id,
                "location": [lon, lat],
                "priority": _priority_weight(priority, manual_priority),
                "service": int(task.get("service", 0)),
            }

            skills: List[str] = []
            if assignment_strategy == "manual_area":
                if task_area_map and task_id in task_area_map:
                    skills.append(_skill_for_area(task_area_map[task_id]))
            elif assignment_strategy == "h3":
                task_cell = latlng_to_h3(lat, lon, h3_resolution)

                if cell_to_fm_index:
                    # ── Multi-cell coverage mode ──
                    # Use the task's own H3 cell as skill.
                    # Vehicles have all cells in their k-ring as skills,
                    # so any FM whose coverage includes this cell can serve it.
                    # NOTE: VROOM requires ALL skills, so we must use exactly
                    # ONE cell skill (not per-FM skills which would be AND-ed).
                    covering_fms = cell_to_fm_index.get(task_cell, [])
                    if covering_fms:
                        skills.append(_skill_for_h3(task_cell))
                    else:
                        # No FM covers this cell — find nearest covered cell
                        nearest_cell = find_nearest_fm_h3(
                            task_cell, fieldman_h3_map or {}, max_k=max_h3_k
                        ) or task_cell
                        skills.append(_skill_for_h3(nearest_cell))
                else:
                    # ── Legacy single-cell mode ──
                    nearest_cell = find_nearest_fm_h3(
                        task_cell, fieldman_h3_map or {}, max_k=max_h3_k
                    ) or task_cell
                    skills.append(_skill_for_h3(nearest_cell))

            if skills:
                job["skills"] = skills

            if location_index_map is not None:
                index = location_index_map.get((lat, lon))
                if index is not None:
                    job["location_index"] = index

            jobs.append(job)

        return jobs

    def build_vehicle_payloads(
        self,
        fieldmen: Sequence[Dict[str, Any]],
        *,
        assignment_strategy: str,
        h3_resolution: int,
        location_index_map: Optional[Dict[Tuple[float, float], int]] = None,
        coverage_k: int = 2,
        max_tasks_per_vehicle: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build VROOM vehicle payloads from fieldmen.

        Each fieldman gets skills for their k-ring of H3 cells (multi-cell
        coverage) so they can serve tasks in a wider geographic area.

        max_tasks_per_vehicle: if set, limits the number of tasks per FM
        for better load balancing.
        """
        vehicles: List[Dict[str, Any]] = []
        for fieldman in fieldmen:
            user_id = str(fieldman["user_id"])
            lat = float(fieldman["current_lat"])
            lon = float(fieldman["current_long"])
            vehicle: Dict[str, Any] = {
                "id": user_id,
                "start": [lon, lat],
                "end": [lon, lat],
                "capacity": fieldman.get("capacity", [100000]),
            }

            if max_tasks_per_vehicle is not None:
                vehicle["max_tasks"] = max_tasks_per_vehicle

            skills: List[str] = []
            if assignment_strategy == "manual_area":
                for area_id in fieldman.get("area_ids", []):
                    skills.append(_skill_for_area(str(area_id)))
            elif assignment_strategy == "h3":
                # ── Multi-cell coverage: FM gets all cells in their k-ring ──
                # Tasks use their own cell as skill; vehicles cover all k-ring cells.
                # VROOM matches when vehicle has the task's cell in its skill set.
                center_cell = latlng_to_h3(lat, lon, h3_resolution)
                k_ring_cells = expand_h3_k_ring(center_cell, coverage_k)
                for cell in k_ring_cells:
                    skills.append(_skill_for_h3(cell))

            if skills:
                vehicle["skills"] = skills

            if location_index_map is not None:
                index = location_index_map.get((lat, lon))
                if index is not None:
                    vehicle["start_index"] = index
                    vehicle["end_index"] = index

            vehicles.append(vehicle)

        return vehicles

    async def solve_vrp(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_client()
        attempts = 3
        backoff_base = 0.5
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post("/", json=payload)
                if response.status_code != 200:
                    import logging
                    matrix = payload.get("matrix", [])
                    matrix_desc = f"{len(matrix)}x{len(matrix[0]) if matrix else 0}" if isinstance(matrix, list) else str(type(matrix))
                    logging.getLogger(__name__).error(
                        "VROOM error %d: %s | jobs=%d vehicles=%d matrix=%s",
                        response.status_code,
                        response.text[:500],
                        len(payload.get("jobs", [])),
                        len(payload.get("vehicles", [])),
                        matrix_desc,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status and 500 <= status < 600 and attempt < attempts:
                    await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                    continue
                raise
            except (httpx.TransportError, httpx.ReadTimeout) as exc:
                if attempt < attempts:
                    await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                    continue
                raise

    def build_vroom_jobs_from_tasks(
        self,
        tasks: Sequence[Dict[str, Any]],
        *,
        assignment_strategy: str,
        h3_resolution: int,
        task_area_map: Optional[Dict[str, str]] = None,
        fieldman_h3_map: Optional[Dict[str, List[str]]] = None,
        location_index_map: Optional[Dict[Tuple[float, float], int]] = None,
        max_h3_k: int = 3,
        cell_to_fm_index: Optional[Dict[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        return self.build_job_payloads(
            tasks,
            assignment_strategy=assignment_strategy,
            h3_resolution=h3_resolution,
            task_area_map=task_area_map,
            fieldman_h3_map=fieldman_h3_map,
            location_index_map=location_index_map,
            max_h3_k=max_h3_k,
            cell_to_fm_index=cell_to_fm_index,
        )

    def build_vroom_vehicles_from_fieldmen(
        self,
        fieldmen: Sequence[Dict[str, Any]],
        *,
        assignment_strategy: str,
        h3_resolution: int,
        location_index_map: Optional[Dict[Tuple[float, float], int]] = None,
        coverage_k: int = 2,
        max_tasks_per_vehicle: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.build_vehicle_payloads(
            fieldmen,
            assignment_strategy=assignment_strategy,
            h3_resolution=h3_resolution,
            location_index_map=location_index_map,
            coverage_k=coverage_k,
            max_tasks_per_vehicle=max_tasks_per_vehicle,
        )

    async def submit_vroom_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self.solve_vrp(payload)

    @staticmethod
    def normalize_skills(jobs: List[Dict[str, Any]], vehicles: List[Dict[str, Any]]) -> None:
        skill_set = set()
        for job in jobs:
            for skill in job.get("skills", []):
                skill_set.add(skill)
        for vehicle in vehicles:
            for skill in vehicle.get("skills", []):
                skill_set.add(skill)

        if not skill_set:
            return

        skill_map = {skill: index + 1 for index, skill in enumerate(sorted(skill_set))}

        for job in jobs:
            if "skills" in job:
                job["skills"] = [skill_map[skill] for skill in job["skills"]]
        for vehicle in vehicles:
            if "skills" in vehicle:
                vehicle["skills"] = [skill_map[skill] for skill in vehicle["skills"]]

    @staticmethod
    def parse_solution(
        solution: Dict[str, Any],
        *,
        job_id_map: Optional[Dict[int, str]] = None,
        vehicle_id_map: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        routes = solution.get("routes") or []
        assignments: Dict[str, Any] = {
            "summary": solution.get("summary", {}),
            "routes": [],
        }
        for route in routes:
            raw_vehicle = route.get("vehicle")
            if vehicle_id_map and isinstance(raw_vehicle, int):
                vehicle_id = vehicle_id_map.get(raw_vehicle, str(raw_vehicle))
            else:
                vehicle_id = str(raw_vehicle)
            steps = route.get("steps") or []
            task_order: List[str] = []
            for step in steps:
                if step.get("type") == "job":
                    raw_job = step.get("job")
                    if job_id_map and isinstance(raw_job, int):
                        task_order.append(job_id_map.get(raw_job, str(raw_job)))
                    else:
                        task_order.append(str(raw_job))
            assignments["routes"].append(
                {
                    "vehicle_id": vehicle_id,
                    "distance": float(route.get("distance", 0.0)),
                    "duration": float(route.get("duration", 0.0)),
                    "tasks": task_order,
                }
            )
        return assignments
