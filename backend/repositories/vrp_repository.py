"""
VRP Job repository – all database access for VRP jobs, assignments, and routes.
"""

from __future__ import annotations

import json
import uuid as _uuid
from typing import Any, Dict, List, Optional, Union

import asyncpg

from core.logging import get_logger

logger = get_logger(__name__)


def _stringify_uuids(d: Dict[str, Any]) -> Dict[str, Any]:
    """Convert UUID and datetime values in a dict to strings for Pydantic compatibility."""
    from datetime import datetime as _dt
    for key, value in d.items():
        if isinstance(value, _uuid.UUID):
            d[key] = str(value)
        elif isinstance(value, _dt):
            d[key] = value.isoformat()
    return d


# ── Standalone helpers (usable outside the class / in transactions) ────


async def resequence_fieldman_tasks(
    fieldman_id: str,
    conn: Union[asyncpg.Connection, asyncpg.Pool],
) -> None:
    """Resequence all pending tasks for a fieldman.

    Pulls all incomplete assignments ordered by their current sequence
    (falling back to priority + distance when sequence is NULL) and
    reassigns sequence values starting from 1.

    This function is intentionally standalone so it can be called from
    any context (inside a transaction, from a Celery worker, etc.) and
    tested independently.
    """
    rows = await conn.fetch(
        "SELECT id FROM vrp_assignments "
        "WHERE fieldman_id = $1 AND COALESCE(status, 'pending') != 'completed' "
        "ORDER BY sequence ASC NULLS LAST, "
        "distance ASC NULLS LAST",
        fieldman_id,
    )
    for idx, row in enumerate(rows, start=1):
        await conn.execute(
            "UPDATE vrp_assignments SET sequence = $1 WHERE id = $2",
            idx, row["id"],
        )


async def _fetch_fieldman_tasks(
    fieldman_id: str,
    conn: Union[asyncpg.Connection, asyncpg.Pool],
) -> List[Dict[str, Any]]:
    """Fetch all tasks for a fieldman (both pending and completed)."""
    rows = await conn.fetch(
        "SELECT a.job_id, a.fieldman_id, a.task_id, a.sequence, "
        "a.distance, a.duration, "
        "COALESCE(a.status, 'pending') AS status, a.completed_at, "
        "t.address, t.latitude, t.longitude, "
        "COALESCE(t.service, 0) AS service, "
        "COALESCE(t.priority, 1.0) AS priority, "
        "t.manual_priority, "
        "COALESCE(t.task_type, 'credit_investigation') AS task_type, "
        "t.bank "
        "FROM vrp_assignments a "
        "JOIN tasks t ON t.id = a.task_id "
        "WHERE a.fieldman_id = $1 "
        "ORDER BY a.sequence ASC NULLS LAST",
        fieldman_id,
    )
    return [_stringify_uuids(dict(row)) for row in rows]


class VRPJobRepository:
    """Encapsulates VRP job-related DB queries."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── Job CRUD ──────────────────────────────────────────────────────

    async def create_job(self, status: str, request_payload: str) -> str:
        """Insert a new VRP job and return its UUID."""
        job_id = await self._pool.fetchval(
            "INSERT INTO vrp_jobs (status, request_payload) "
            "VALUES ($1, $2) RETURNING id",
            status,
            request_payload,
        )
        return str(job_id)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a job by ID."""
        row = await self._pool.fetchrow(
            "SELECT id, status, status_detail, created_at, updated_at, finalized_at "
            "FROM vrp_jobs WHERE id = $1",
            job_id,
        )
        return dict(row) if row else None

    async def get_job_status(self, job_id: str) -> Optional[str]:
        """Return just the status string for a job."""
        return await self._pool.fetchval(
            "SELECT status FROM vrp_jobs WHERE id = $1",
            job_id,
        )

    async def get_job_payload(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Load the original request payload for a job."""
        row = await self._pool.fetchrow(
            "SELECT request_payload FROM vrp_jobs WHERE id = $1",
            job_id,
        )
        if not row:
            return None
        return json.loads(row["request_payload"])

    async def update_status(
        self,
        job_id: str,
        status: str,
        detail: Optional[str] = None,
    ) -> None:
        """Update job status and detail."""
        await self._pool.execute(
            "UPDATE vrp_jobs SET status = $1, status_detail = $2, "
            "updated_at = NOW() WHERE id = $3",
            status,
            detail,
            job_id,
        )

    async def finalize_job(self, job_id: str) -> bool:
        """
        Mark a job as finalized.
        Returns True if updated, False if not found or already finalized.
        """
        result = await self._pool.execute(
            "UPDATE vrp_jobs SET status = $1, finalized_at = NOW() "
            "WHERE id = $2 AND status != $1",
            "finalized",
            job_id,
        )
        return not result.startswith("UPDATE 0")

    async def job_exists(self, job_id: str) -> bool:
        """Check whether a job exists."""
        return bool(
            await self._pool.fetchval(
                "SELECT 1 FROM vrp_jobs WHERE id = $1", job_id
            )
        )

    async def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List jobs with optional status filter and pagination."""
        params: List[Any] = []
        where = ""
        if status:
            params.append(status)
            where = f"WHERE status = ${len(params)}"

        params.append(limit)
        limit_clause = f"LIMIT ${len(params)}"
        params.append(offset)
        offset_clause = f"OFFSET ${len(params)}"

        sql = (
            "SELECT id, status, status_detail, created_at, updated_at, finalized_at "
            f"FROM vrp_jobs {where} "
            f"ORDER BY created_at DESC {limit_clause} {offset_clause}"
        )
        rows = await self._pool.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def truncate_all(self) -> None:
        """Truncate task and fieldman data, preserving VRP jobs."""
        async with self._pool.acquire() as conn:
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
                "TRUNCATE fm_assigned_areas, "
                "fm_home_locations, tasks RESTART IDENTITY CASCADE"
            )

    async def delete_job(self, job_id: str) -> bool:
        """Delete a specific job and its assignments/routes."""
        async with self._pool.acquire() as conn:
            # Delete assignments
            await conn.execute(
                "DELETE FROM vrp_assignments WHERE job_id = $1", job_id
            )
            # Delete routes if table exists
            table_exists = await conn.fetchval(
                "SELECT to_regclass('public.vrp_job_routes')"
            )
            if table_exists:
                await conn.execute(
                    "DELETE FROM vrp_job_routes WHERE job_id = $1", job_id
                )
            # Delete the job row
            deleted = await conn.fetchval(
                "DELETE FROM vrp_jobs WHERE id = $1 RETURNING id", job_id
            )
            return deleted is not None

    # ── Metrics ───────────────────────────────────────────────────────

    async def get_metrics(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Aggregate assignment metrics for a job."""
        row = await self._pool.fetchrow(
            "SELECT COUNT(*) AS tasks_assigned, "
            "COUNT(DISTINCT fieldman_id) AS fieldmen_used, "
            "COALESCE(SUM(distance), 0) AS total_distance, "
            "COALESCE(SUM(duration), 0) AS total_duration "
            "FROM vrp_assignments WHERE job_id = $1",
            job_id,
        )
        if row is None:
            return None
        return dict(row)

    # ── Assignments ───────────────────────────────────────────────────

    async def get_assignments(self, job_id: str) -> List[Dict[str, Any]]:
        """Return assignment rows joined with task + fieldman data."""
        rows = await self._pool.fetch(
            "SELECT a.job_id, a.fieldman_id, a.task_id, a.sequence, "
            "a.distance AS task_distance, a.duration AS task_duration, "
            "t.address, t.latitude, t.longitude, "
            "COALESCE(t.service, 0) AS service, "
            "COALESCE(t.priority, 1.0) AS priority, "
            "f.home_lat, f.home_long "
            "FROM vrp_assignments a "
            "JOIN tasks t ON t.id = a.task_id "
            "JOIN fm_home_locations f ON f.user_id = a.fieldman_id "
            "WHERE a.job_id = $1 "
            "ORDER BY a.fieldman_id, a.sequence",
            job_id,
        )
        return [dict(row) for row in rows]

    async def get_all_assignments(self) -> List[Dict[str, Any]]:
        """Return all assignment rows across all jobs."""
        rows = await self._pool.fetch(
            "SELECT a.job_id, a.fieldman_id, a.task_id, a.sequence, "
            "a.distance AS task_distance, a.duration AS task_duration, "
            "t.address, t.latitude, t.longitude, "
            "COALESCE(t.service, 0) AS service, "
            "COALESCE(t.priority, 1.0) AS priority, "
            "f.home_lat, f.home_long "
            "FROM vrp_assignments a "
            "JOIN tasks t ON t.id = a.task_id "
            "JOIN fm_home_locations f ON f.user_id = a.fieldman_id "
            "ORDER BY a.job_id, a.fieldman_id, a.sequence"
        )
        return [dict(row) for row in rows]

    async def store_assignments(
        self,
        job_id: str,
        assignment_rows: List[Dict[str, Any]],
    ) -> None:
        """Replace all assignments for a job."""
        await self._pool.execute(
            "DELETE FROM vrp_assignments WHERE job_id = $1", job_id
        )
        records = []
        for assignment in assignment_rows:
            records.extend(assignment["rows"])
        if not records:
            return
        await self._pool.executemany(
            "INSERT INTO vrp_assignments "
            "(job_id, fieldman_id, task_id, sequence, distance, duration) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            records,
        )

    # ── Ensure assignment columns ─────────────────────────────────────

    async def ensure_assignment_columns(self) -> None:
        """Add status and completed_at columns to vrp_assignments if missing."""
        async with self._pool.acquire() as conn:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'vrp_assignments'"
            )
            existing = {row["column_name"] for row in cols}
            if "status" not in existing:
                await conn.execute(
                    "ALTER TABLE vrp_assignments ADD COLUMN status "
                    "VARCHAR(20) DEFAULT 'pending'"
                )
            if "completed_at" not in existing:
                await conn.execute(
                    "ALTER TABLE vrp_assignments ADD COLUMN completed_at "
                    "TIMESTAMPTZ DEFAULT NULL"
                )

    # ── Fieldman Task Completion & Resequencing ───────────────────────

    async def get_assignment_by_fieldman_and_task(
        self,
        fieldman_id: str,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single assignment by fieldman + task combination."""
        row = await self._pool.fetchrow(
            "SELECT a.id, a.job_id, a.fieldman_id, a.task_id, a.sequence, "
            "a.distance, a.duration, "
            "COALESCE(a.status, 'pending') AS status, a.completed_at "
            "FROM vrp_assignments a "
            "WHERE a.fieldman_id = $1 AND a.task_id = $2 "
            "LIMIT 1",
            fieldman_id, task_id,
        )
        return dict(row) if row else None

    async def complete_task_and_resequence(
        self,
        fieldman_id: str,
        task_id: str,
    ) -> List[Dict[str, Any]]:
        """Mark a task as completed.

        Original sequence numbers are preserved — no renumbering.
        Completed tasks keep their sequence for reference.

        Runs inside a single transaction.
        Returns the updated task list for the fieldman.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # 1. Mark the task as completed (keep original sequence)
                await conn.execute(
                    "UPDATE vrp_assignments "
                    "SET status = 'completed', completed_at = NOW() "
                    "WHERE fieldman_id = $1 AND task_id = $2",
                    fieldman_id, task_id,
                )

                # 2. Return updated task list (no resequencing)
                return await _fetch_fieldman_tasks(fieldman_id, conn)

    async def get_fieldman_tasks(
        self,
        fieldman_id: str,
    ) -> List[Dict[str, Any]]:
        """Return all tasks for a fieldman ordered by sequence ascending."""
        await self.ensure_assignment_columns()
        rows = await self._pool.fetch(
            "SELECT a.job_id, a.fieldman_id, a.task_id, a.sequence, "
            "a.distance, a.duration, "
            "COALESCE(a.status, 'pending') AS status, a.completed_at, "
            "t.address, t.latitude, t.longitude, "
            "COALESCE(t.service, 0) AS service, "
            "COALESCE(t.priority, 1.0) AS priority, "
            "t.manual_priority, "
            "COALESCE(t.task_type, 'credit_investigation') AS task_type, "
            "t.bank "
            "FROM vrp_assignments a "
            "JOIN tasks t ON t.id = a.task_id "
            "WHERE a.fieldman_id = $1 "
            "ORDER BY a.sequence ASC NULLS LAST",
            fieldman_id,
        )
        return [_stringify_uuids(dict(row)) for row in rows]

    async def fieldman_exists(self, fieldman_id: str) -> bool:
        """Check whether a fieldman exists."""
        return bool(
            await self._pool.fetchval(
                "SELECT 1 FROM fm_home_locations WHERE user_id = $1",
                fieldman_id,
            )
        )

    async def task_exists(self, task_id: str) -> bool:
        """Check whether a task exists."""
        return bool(
            await self._pool.fetchval(
                "SELECT 1 FROM tasks WHERE id = $1", task_id
            )
        )

    # ── Routes ────────────────────────────────────────────────────────

    async def ensure_routes_table(self) -> None:
        """Create vrp_job_routes table if it doesn't exist."""
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS vrp_job_routes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID NOT NULL,
                fieldman_id UUID NOT NULL,
                route_index INT NOT NULL,
                distance DOUBLE PRECISION,
                duration DOUBLE PRECISION,
                start_lat DOUBLE PRECISION,
                start_long DOUBLE PRECISION,
                geometry JSONB,
                tasks JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

    async def store_routes(
        self,
        job_id: str,
        routes: List[Dict[str, Any]],
    ) -> None:
        """Replace all route rows for a job."""
        await self.ensure_routes_table()
        await self._pool.execute(
            "DELETE FROM vrp_job_routes WHERE job_id = $1", job_id
        )
        if not routes:
            return
        await self._pool.executemany(
            "INSERT INTO vrp_job_routes "
            "(job_id, fieldman_id, route_index, distance, duration, "
            "start_lat, start_long, geometry, tasks) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            [
                (
                    job_id,
                    route["fieldman_id"],
                    route["route_index"],
                    route.get("distance", 0.0),
                    route.get("duration", 0.0),
                    route.get("start_lat"),
                    route.get("start_long"),
                    json.dumps(route.get("geometry", {})),
                    json.dumps(route.get("tasks", [])),
                )
                for route in routes
            ],
        )

    async def get_preview_routes(
        self,
        job_id: str,
    ) -> Dict[str, Any]:
        """Load route + assignment data for job preview."""
        table_exists = await self._pool.fetchval(
            "SELECT to_regclass('public.vrp_job_routes')"
        )
        if not table_exists:
            return {"route_rows": [], "task_rows": []}

        route_rows = await self._pool.fetch(
            "SELECT fieldman_id, route_index, distance, duration, "
            "start_lat, start_long, geometry, tasks "
            "FROM vrp_job_routes WHERE job_id = $1 ORDER BY route_index",
            job_id,
        )
        task_rows = await self._pool.fetch(
            "SELECT a.fieldman_id, a.task_id, a.sequence, "
            "a.distance, a.duration, "
            "COALESCE(a.status, 'pending') AS status, a.completed_at, "
            "t.address, t.latitude, t.longitude, "
            "COALESCE(t.service, 0) AS service, "
            "COALESCE(t.priority, 1.0) AS priority, "
            "t.manual_priority, "
            "COALESCE(t.task_type, 'credit_investigation') AS task_type, "
            "t.bank "
            "FROM vrp_assignments a "
            "JOIN tasks t ON t.id = a.task_id "
            "WHERE a.job_id = $1 ORDER BY a.fieldman_id, a.sequence",
            job_id,
        )
        return {
            "route_rows": [dict(r) for r in route_rows],
            "task_rows": [dict(r) for r in task_rows],
        }
