"""
Task repository – all database access for the ``tasks`` table.
"""

from __future__ import annotations

import math
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

try:
    from global_land_mask import globe
except Exception:  # pragma: no cover
    globe = None

from core.logging import get_logger

_logger_init = get_logger(__name__)
if globe is None:
    _logger_init.warning(
        "global_land_mask is NOT installed — task water validation DISABLED. "
        "Install with: pip install global-land-mask"
    )
from domain.tasks import TaskOverview, TaskRecord

logger = get_logger(__name__)

COASTAL_EXCLUSION_ZONES: List[Dict[str, float]] = [
    {"lat_min": 14.44, "lat_max": 14.53, "lon_min": 120.985, "lon_max": 121.015},
]


def _is_land_point(lat: float, lon: float) -> bool:
    if globe is None:
        return True
    try:
        return bool(globe.is_land(float(lat), float(lon)))
    except Exception:
        return True


def _is_excluded_coastal_zone(lat: float, lon: float) -> bool:
    for zone in COASTAL_EXCLUSION_ZONES:
        if zone["lat_min"] <= lat <= zone["lat_max"] and zone["lon_min"] <= lon <= zone["lon_max"]:
            return True
    return False


def _offset_point_meters(lat: float, lon: float, meters: float, bearing_deg: float) -> Tuple[float, float]:
    bearing_rad = math.radians(bearing_deg)
    dlat = (meters * math.cos(bearing_rad)) / 111_000.0
    dlng = (meters * math.sin(bearing_rad)) / (111_000.0 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlng


def _is_stable_land(lat: float, lon: float, sample_m: float = 180.0) -> bool:
    if _is_excluded_coastal_zone(lat, lon):
        return False
    if not _is_land_point(lat, lon):
        return False
    if globe is None:
        return True
    bearings = (0, 45, 90, 135, 180, 225, 270, 315)
    land_neighbors = 0
    for bearing in bearings:
        nlat, nlon = _offset_point_meters(lat, lon, sample_m, bearing)
        if _is_land_point(nlat, nlon):
            land_neighbors += 1
    return land_neighbors >= 7


class TaskRepository:
    """Encapsulates task-related DB queries."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def count(self, task_ids: Optional[List[str]] = None) -> int:
        """Return total tasks, optionally filtered by IDs."""
        if task_ids:
            result = await self._pool.fetchval(
                "SELECT COUNT(*) FROM tasks WHERE id = ANY($1::uuid[])",
                task_ids,
            )
        else:
            result = await self._pool.fetchval("SELECT COUNT(*) FROM tasks")
        return int(result)

    async def list_tasks(
        self,
        *,
        task_ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        priority_min: Optional[float] = None,
        priority_max: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch task rows with optional filters."""
        params: List[Any] = []
        where: List[str] = []
        if task_ids:
            params.append(task_ids)
            where.append(f"id = ANY(${len(params)}::uuid[])")
        if priority_min is not None:
            params.append(priority_min)
            where.append(f"priority >= ${len(params)}")
        if priority_max is not None:
            params.append(priority_max)
            where.append(f"priority <= ${len(params)}")

        sql = "SELECT id, address, latitude, longitude, priority, manual_priority FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"

        if limit is not None:
            params.append(limit)
            sql += f" LIMIT ${len(params)}"
        if offset is not None:
            params.append(offset)
            sql += f" OFFSET ${len(params)}"

        rows = await self._pool.fetch(sql, *params)
        result: List[Dict[str, Any]] = []
        for row in rows:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            if not _is_stable_land(lat, lon):
                continue
            result.append(dict(row))
        return result

    async def list_overview(self) -> List[TaskOverview]:
        """Return lightweight task list for overview."""
        rows = await self._pool.fetch(
            "SELECT id, address, latitude, longitude, task_type, bank, priority, manual_priority FROM tasks ORDER BY id"
        )
        result: List[TaskOverview] = []
        for row in rows:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            if not _is_stable_land(lat, lon):
                continue
            result.append(
                TaskOverview(
                    task_id=str(row["id"]),
                    address=row["address"],
                    latitude=lat,
                    longitude=lon,
                    task_type=row.get("task_type"),
                    bank=row.get("bank"),
                    priority=float(row["priority"]) if row.get("priority") is not None else None,
                    manual_priority=float(row["manual_priority"]) if row.get("manual_priority") is not None else None,
                )
            )
        return result

    # ── Task Summary ──────────────────────────────────────────────────

    async def get_task_summary(self) -> Dict[str, Any]:
        """Return aggregated task counts by type and bank (parallelized)."""
        import asyncio
        total_tasks, total_fieldmen, type_rows, bank_rows = await asyncio.gather(
            self._pool.fetchval("SELECT COUNT(*) FROM tasks"),
            self._pool.fetchval("SELECT COUNT(*) FROM fm_home_locations"),
            self._pool.fetch(
                "SELECT COALESCE(task_type, 'unknown') AS task_type, COUNT(*) AS cnt "
                "FROM tasks GROUP BY task_type"
            ),
            self._pool.fetch(
                "SELECT COALESCE(bank, 'unassigned') AS bank, COUNT(*) AS cnt "
                "FROM tasks GROUP BY bank"
            ),
        )
        by_type = {row["task_type"]: int(row["cnt"]) for row in type_rows}

        bank_rows = await self._pool.fetch(
            "SELECT COALESCE(bank, 'unassigned') AS bank, COUNT(*) AS cnt "
            "FROM tasks GROUP BY bank"
        )
        by_bank = {row["bank"]: int(row["cnt"]) for row in bank_rows}

        return {
            "total_tasks": int(total_tasks),
            "total_fieldmen": int(total_fieldmen),
            "by_type": by_type,
            "by_bank": by_bank,
        }

    # ── Schema migration ─────────────────────────────────────────────

    async def ensure_columns(self) -> None:
        """Add task_type, bank, service, manual_priority columns to tasks if missing."""
        async with self._pool.acquire() as conn:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tasks'"
            )
            existing = {row["column_name"] for row in cols}
            if "task_type" not in existing:
                await conn.execute(
                    "ALTER TABLE tasks ADD COLUMN task_type TEXT "
                    "DEFAULT 'credit_investigation'"
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
                    "ALTER TABLE tasks ADD COLUMN manual_priority "
                    "DOUBLE PRECISION DEFAULT NULL"
                )
            if "deleted_at" not in existing:
                await conn.execute(
                    "ALTER TABLE tasks ADD COLUMN deleted_at "
                    "TIMESTAMPTZ DEFAULT NULL"
                )

    # ── Picker create / delete ────────────────────────────────────────

    async def create_task(
        self,
        *,
        task_id: str,
        address: str,
        latitude: float,
        longitude: float,
        priority: float,
        service: int,
        task_type: str,
        bank: Optional[str] = None,
        manual_priority: Optional[float] = None,
    ) -> None:
        """Insert a single task row."""
        await self._pool.execute(
            "INSERT INTO tasks (id, address, latitude, longitude, "
            "priority, service, task_type, bank, manual_priority) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            task_id, address, latitude, longitude,
            priority, service, task_type, bank, manual_priority,
        )

    async def update_address(self, task_id: str, address: str) -> None:
        """Update the address for a task."""
        await self._pool.execute(
            "UPDATE tasks SET address = $1 WHERE id = $2", address, task_id,
        )

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task. Returns True if deleted."""
        result = await self._pool.execute(
            "DELETE FROM tasks WHERE id = $1", task_id,
        )
        return result != "DELETE 0"

    async def bulk_insert(self, rows: List[Tuple]) -> None:
        """Bulk insert task rows using COPY for maximum throughput at scale."""
        if not rows:
            return
        # Use copy_records_to_table for 10x faster bulk loading vs executemany
        has_manual_priority = rows and len(rows[0]) >= 9
        columns = ['id', 'address', 'latitude', 'longitude', 'priority', 'service', 'task_type', 'bank']
        if has_manual_priority:
            columns.append('manual_priority')
        async with self._pool.acquire() as conn:
            await conn.copy_records_to_table(
                'tasks',
                records=rows,
                columns=columns,
            )

    async def update_manual_priority(
        self, task_id: str, manual_priority: Optional[float],
    ) -> bool:
        """Update the manual_priority for a task. Set to None to clear."""
        result = await self._pool.execute(
            "UPDATE tasks SET manual_priority = $1 WHERE id = $2",
            manual_priority, task_id,
        )
        return result != "UPDATE 0"

    async def fetch_all_coords(self) -> List[Dict[str, Any]]:
        """Return id, latitude, longitude for all tasks. Uses Record objects directly for speed."""
        rows = await self._pool.fetch(
            "SELECT id, latitude, longitude FROM tasks"
        )
        # Return asyncpg Records directly — they support dict-like access
        # Avoids creating 20k+ dicts which is expensive
        return rows  # type: ignore[return-value]
