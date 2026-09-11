"""
Fieldman repository – all database access for fieldman tables.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import redis.asyncio as aioredis

try:
    from global_land_mask import globe
except Exception:  # pragma: no cover
    globe = None

from core.logging import get_logger

_logger_init = get_logger(__name__)
if globe is None:
    _logger_init.warning(
        "global_land_mask is NOT installed — fieldman water validation DISABLED. "
        "Install with: pip install global-land-mask"
    )
from domain.fieldmen import FieldmanOverview, FieldmanRecord

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


class FieldmanRepository:
    """Encapsulates fieldman-related DB queries."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def count(self, fieldman_ids: Optional[List[str]] = None) -> int:
        """Return total fieldmen, optionally filtered by IDs."""
        if fieldman_ids:
            result = await self._pool.fetchval(
                "SELECT COUNT(*) FROM fm_home_locations WHERE user_id = ANY($1::uuid[])",
                fieldman_ids,
            )
        else:
            result = await self._pool.fetchval("SELECT COUNT(*) FROM fm_home_locations")
        return int(result)

    async def list_fieldmen(
        self,
        *,
        fieldman_ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch fieldman rows with area assignments."""
        params: List[Any] = []
        where: List[str] = []
        if fieldman_ids:
            params.append(fieldman_ids)
            where.append(f"user_id = ANY(${len(params)}::uuid[])")

        sql = "SELECT user_id, address, home_lat, home_long FROM fm_home_locations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY user_id"

        if limit is not None:
            params.append(limit)
            sql += f" LIMIT ${len(params)}"
        if offset is not None:
            params.append(offset)
            sql += f" OFFSET ${len(params)}"

        rows = await self._pool.fetch(sql, *params)

        area_rows = await self._pool.fetch(
            "SELECT user_id, array_agg(area_id) AS area_ids "
            "FROM fm_assigned_areas GROUP BY user_id"
        )
        area_map = {str(row["user_id"]): row["area_ids"] for row in area_rows}

        fieldmen: List[Dict[str, Any]] = []
        for row in rows:
            home_lat = float(row["home_lat"])
            home_lon = float(row["home_long"])
            if not _is_stable_land(home_lat, home_lon):
                continue
            fieldmen.append(
                {
                    "user_id": str(row["user_id"]),
                    "address": row["address"],
                    "home_lat": home_lat,
                    "home_long": home_lon,
                    "area_ids": area_map.get(str(row["user_id"]), []),
                }
            )
        return fieldmen

    async def list_overview(self) -> List[FieldmanOverview]:
        """Return lightweight fieldman list for overview."""
        rows = await self._pool.fetch(
            "SELECT user_id, address, home_lat, home_long "
            "FROM fm_home_locations ORDER BY user_id"
        )
        result: List[FieldmanOverview] = []
        for row in rows:
            lat = float(row["home_lat"])
            lon = float(row["home_long"])
            if not _is_stable_land(lat, lon):
                continue
            result.append(
                FieldmanOverview(
                    fieldman_id=str(row["user_id"]),
                    address=row["address"],
                    latitude=lat,
                    longitude=lon,
                )
            )
        return result

    async def resolve_current_locations(
        self,
        redis_client: aioredis.Redis,
        fieldmen: List[Dict[str, Any]],
    ) -> None:
        """
        Enrich fieldmen dicts with current GPS location from Redis.
        Falls back to home location when live data is unavailable.
        """
        keys = [f"fm:location:{f['user_id']}" for f in fieldmen]
        values = await redis_client.mget(keys)

        for fieldman, raw in zip(fieldmen, values):
            if raw:
                try:
                    data = json.loads(raw)
                    fieldman["current_lat"] = float(data["current_lat"])
                    fieldman["current_long"] = float(data["current_long"])
                    continue
                except (ValueError, KeyError, TypeError):
                    pass
            fieldman["current_lat"] = fieldman["home_lat"]
            fieldman["current_long"] = fieldman["home_long"]

    # ── Picker create / delete ────────────────────────────────────────

    async def create_fieldman(
        self,
        *,
        user_id: str,
        address: str,
        home_lat: float,
        home_long: float,
    ) -> None:
        """Insert a single fieldman."""
        await self._pool.execute(
            "INSERT INTO fm_home_locations (user_id, address, home_lat, home_long) "
            "VALUES ($1, $2, $3, $4)",
            user_id, address, home_lat, home_long,
        )

    async def update_address(self, user_id: str, address: str) -> None:
        """Update the address for a fieldman."""
        await self._pool.execute(
            "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
            address, user_id,
        )

    async def update_location(
        self, user_id: str, lat: float, lng: float,
    ) -> bool:
        """Update the home location for a fieldman. Returns True if updated."""
        result = await self._pool.execute(
            "UPDATE fm_home_locations SET home_lat = $1, home_long = $2 WHERE user_id = $3",
            lat, lng, user_id,
        )
        return result != "UPDATE 0"

    async def assign_area(self, user_id: str, area_id: str) -> None:
        """Assign a fieldman to an area."""
        await self._pool.execute(
            "INSERT INTO fm_assigned_areas (user_id, area_id) VALUES ($1, $2)",
            user_id, area_id,
        )

    async def delete_fieldman(self, fieldman_id: str) -> bool:
        """Delete a fieldman and their area assignments. Returns True if deleted."""
        await self._pool.execute(
            "DELETE FROM fm_assigned_areas WHERE user_id = $1", fieldman_id,
        )
        result = await self._pool.execute(
            "DELETE FROM fm_home_locations WHERE user_id = $1", fieldman_id,
        )
        return result != "DELETE 0"

    async def bulk_insert(self, rows: List[Tuple]) -> None:
        """Bulk insert fieldman rows: (user_id, address, home_lat, home_long)."""
        if not rows:
            return
        await self._pool.executemany(
            "INSERT INTO fm_home_locations (user_id, address, home_lat, home_long) "
            "VALUES ($1, $2, $3, $4)",
            rows,
        )

    async def bulk_assign_areas(self, rows: List[Tuple]) -> None:
        """Bulk insert area assignments: (user_id, area_id)."""
        if not rows:
            return
        await self._pool.executemany(
            "INSERT INTO fm_assigned_areas (user_id, area_id) VALUES ($1, $2)",
            rows,
        )
