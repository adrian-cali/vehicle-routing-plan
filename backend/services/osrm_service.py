"""OSRM HTTP client.

Provides road-network distance/duration matrices, route geometry
retrieval, and nearest-road snapping via the OSRM HTTP API.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TYPE_CHECKING

import httpx
import asyncio
import math

if TYPE_CHECKING:
    from services.cache_service import CacheService

from core.logging import get_logger

logger = get_logger(__name__)

Coord = Tuple[float, float]  # (lat, lon)


class OSRMService:
    """httpx client for OSRM — connection pooling within a job."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        max_locations: int = 100,
        cache: Optional["CacheService"] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_locations = max_locations
        self._cache = cache
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return (or create) a persistent httpx client for this instance."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=80, max_keepalive_connections=40),
            )
        return self._client

    @staticmethod
    def _format_coord(coord: Coord) -> str:
        lat, lon = coord
        return f"{lon},{lat}"

    def batch_coordinates(self, coordinates: Sequence[Coord], *, batch_size: Optional[int] = None) -> List[List[Coord]]:
        if not coordinates:
            return []
        size = batch_size or max(2, self.max_locations)
        return [list(coordinates[i : i + size]) for i in range(0, len(coordinates), size)]

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """HTTP GET with persistent client + retry/backoff for transient errors."""
        client = self._get_client()
        attempts = 3
        backoff_base = 0.5
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status and 500 <= status < 600 and attempt < attempts:
                    wait = backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(wait)
                    continue
                raise
            except (httpx.TransportError, httpx.ReadTimeout) as exc:
                if attempt < attempts:
                    wait = backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(wait)
                    continue
                raise

    async def get_route(
        self,
        start: Coord,
        end: Coord,
        *,
        overview: str = "full",
        geometries: str = "polyline",
        steps: bool = True,
    ) -> Dict[str, Any]:
        coords = f"{self._format_coord(start)};{self._format_coord(end)}"
        params = {
            "overview": overview,
            "geometries": geometries,
            "steps": "true" if steps else "false",
        }
        return await self._get(f"/route/v1/driving/{coords}", params=params)

    async def osrm_route(
        self,
        coordinates: Sequence[Coord],
        *,
        overview: str = "full",
        geometries: str = "geojson",
    ) -> Dict[str, Any]:
        if len(coordinates) < 2:
            return {"type": "LineString", "coordinates": []}
        return await self.get_route_geojson(coordinates, overview=overview, geometries=geometries)

    async def nearest(
        self,
        coord: Coord,
        *,
        number: int = 1,
    ) -> Dict[str, Any]:
        """Call OSRM nearest to snap a point to the road network and get the street name."""
        path = f"/nearest/v1/driving/{self._format_coord(coord)}"
        return await self._get(path, params={"number": str(number)})

    async def nearest_street_name(self, coord: Coord) -> str:
        """Return the street/road name for the nearest road segment."""
        try:
            data = await self.nearest(coord)
            waypoints = data.get("waypoints") or []
            if waypoints:
                name = waypoints[0].get("name", "")
                if name:
                    return name
        except Exception:
            pass
        return ""

    async def get_distance_duration(self, start: Coord, end: Coord) -> Dict[str, float]:
        # Check cache for distance/duration
        if self._cache:
            from services.cache_service import CacheTTL, _hash_params
            cache_key = self._cache.key_route_distance(
                _hash_params(start), _hash_params(end)
            )
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        data = await self.get_route(start, end, overview="false", steps=False)
        routes = data.get("routes") or []
        if not routes:
            return {"distance": 0.0, "duration": 0.0}
        route = routes[0]
        result = {"distance": float(route.get("distance", 0.0)), "duration": float(route.get("duration", 0.0))}

        # Cache the result
        if self._cache:
            await self._cache.set(cache_key, result, ttl=CacheTTL.LONG)

        return result

    async def osrm_distance_duration(self, start: Coord, end: Coord) -> Dict[str, float]:
        return await self.get_distance_duration(start, end)

    async def get_distance_matrix(
        self,
        coordinates: Sequence[Coord],
        *,
        sources: Optional[Sequence[int]] = None,
        destinations: Optional[Sequence[int]] = None,
        annotations: str = "distance,duration",
    ) -> Dict[str, Any]:
        if not coordinates:
            return {"distances": [], "durations": []}

        # Check cache for distance matrix
        if self._cache:
            from services.cache_service import CacheTTL, _hash_params
            cache_key = self._cache.key_distance_matrix(
                _hash_params(list(coordinates), sources, destinations)
            )
            cached = await self._cache.get(cache_key)
            if cached is not None:
                logger.info("OSRM matrix cache HIT (%d coords)", len(coordinates))
                return cached

        result: Dict[str, Any]
        if len(coordinates) <= self.max_locations:
            coords = ";".join(self._format_coord(c) for c in coordinates)
            params: Dict[str, Any] = {"annotations": annotations}
            if sources is not None:
                params["sources"] = ";".join(str(i) for i in sources)
            if destinations is not None:
                params["destinations"] = ";".join(str(i) for i in destinations)
            result = await self._get(f"/table/v1/driving/{coords}", params=params)

            # Cache the result
            if self._cache:
                await self._cache.set(cache_key, result, ttl=CacheTTL.DISTANCE_MATRIX)
            return result

        total = len(coordinates)
        distances = [[0.0 for _ in range(total)] for _ in range(total)]
        durations = [[0.0 for _ in range(total)] for _ in range(total)]

        batch_size = max(2, self.max_locations // 2)
        source_batches = [list(range(i, min(i + batch_size, total))) for i in range(0, total, batch_size)]
        dest_batches = [list(range(i, min(i + batch_size, total))) for i in range(0, total, batch_size)]

        # Build all sub-requests up front
        import asyncio
        async def _fetch_sub(
            source_batch: List[int], dest_batch: List[int]
        ) -> Tuple[List[int], List[int], Dict[str, Any]]:
            coords = [coordinates[i] for i in source_batch] + [coordinates[i] for i in dest_batch]
            params: Dict[str, Any] = {"annotations": annotations}
            params["sources"] = ";".join(str(i) for i in range(len(source_batch)))
            dest_offset = len(source_batch)
            params["destinations"] = ";".join(
                str(dest_offset + i) for i in range(len(dest_batch))
            )
            data = await self._get(
                f"/table/v1/driving/{';'.join(self._format_coord(c) for c in coords)}",
                params=params,
            )
            return source_batch, dest_batch, data

        # Execute all sub-requests in parallel (limited concurrency via httpx pool)
        tasks = [_fetch_sub(sb, db) for sb in source_batches for db in dest_batches]
        results = await asyncio.gather(*tasks)

        for source_batch, dest_batch, data in results:
            batch_distances = data.get("distances") or []
            batch_durations = data.get("durations") or []

            for row_index, row in enumerate(batch_distances):
                source_index = source_batch[row_index]
                for col_index, value in enumerate(row):
                    dest_index = dest_batch[col_index]
                    distances[source_index][dest_index] = float(value or 0.0)

            for row_index, row in enumerate(batch_durations):
                source_index = source_batch[row_index]
                for col_index, value in enumerate(row):
                    dest_index = dest_batch[col_index]
                    durations[source_index][dest_index] = float(value or 0.0)

        result = {"distances": distances, "durations": durations}

        # Cache the assembled matrix
        if self._cache:
            await self._cache.set(cache_key, result, ttl=CacheTTL.DISTANCE_MATRIX)

        return result

    async def osrm_distance_matrix(
        self,
        coordinates: Sequence[Coord],
        *,
        sources: Optional[Sequence[int]] = None,
        destinations: Optional[Sequence[int]] = None,
        annotations: str = "distance,duration",
    ) -> Dict[str, Any]:
        return await self.get_distance_matrix(
            coordinates,
            sources=sources,
            destinations=destinations,
            annotations=annotations,
        )

    async def get_route_with_legs(
        self,
        coordinates: Sequence[Coord],
        *,
        overview: str = "full",
        geometries: str = "geojson",
    ) -> Dict[str, Any]:
        """Single OSRM call that returns geometry + per-leg distance/duration.
        
        Returns: {"geometry": {...}, "legs": [{"distance": ..., "duration": ...}, ...], "distance": ..., "duration": ...}
        """
        if len(coordinates) < 2:
            return {"geometry": {"type": "LineString", "coordinates": []}, "legs": [], "distance": 0.0, "duration": 0.0}

        # Check cache
        if self._cache:
            from services.cache_service import CacheTTL, _hash_params
            cache_key = self._cache.key_route_geometry(_hash_params(list(coordinates), "legs"))
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        coords = ";".join(self._format_coord(c) for c in coordinates)
        params = {"overview": overview, "geometries": geometries}
        data = await self._get(f"/route/v1/driving/{coords}", params=params)
        routes = data.get("routes") or []
        if not routes:
            return {"geometry": {"type": "LineString", "coordinates": []}, "legs": [], "distance": 0.0, "duration": 0.0}

        route = routes[0]
        # Extract geometry
        raw_geom = route.get("geometry") or {}
        if geometries == "geojson" and isinstance(raw_geom, dict):
            raw_coords = raw_geom.get("coordinates") or []
            geometry = {"type": "LineString", "coordinates": [[float(lon), float(lat)] for lon, lat in raw_coords]}
        else:
            geometry = {"type": "LineString", "coordinates": []}

        # Extract per-leg metrics
        legs = [
            {"distance": float(leg.get("distance", 0.0)), "duration": float(leg.get("duration", 0.0))}
            for leg in (route.get("legs") or [])
        ]

        result = {
            "geometry": geometry,
            "legs": legs,
            "distance": float(route.get("distance", 0.0)),
            "duration": float(route.get("duration", 0.0)),
        }
        if self._cache:
            await self._cache.set(cache_key, result, CacheTTL.LONG)
        return result

    async def get_route_geometry(
        self,
        coordinates: Sequence[Coord],
        *,
        overview: str = "full",
        geometries: str = "geojson",
    ) -> List[List[float]]:
        data = await self.get_route_geojson(coordinates, overview=overview, geometries=geometries)
        return data.get("coordinates", []) if data else []

    async def get_route_geojson(
        self,
        coordinates: Sequence[Coord],
        *,
        overview: str = "full",
        geometries: str = "geojson",
    ) -> Dict[str, Any]:
        if len(coordinates) < 2:
            return {"type": "LineString", "coordinates": []}

        # Check cache
        cache_key = None
        if self._cache:
            from services.cache_service import CacheTTL, _hash_params
            cache_key = self._cache.key_route_geometry(_hash_params(list(coordinates), "geojson"))
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        coords_str = ";".join(self._format_coord(c) for c in coordinates)
        params = {
            "overview": overview,
            "geometries": geometries,
        }
        data = await self._get(f"/route/v1/driving/{coords_str}", params=params)
        routes = data.get("routes") or []
        if not routes:
            return {"type": "LineString", "coordinates": []}

        geometry = routes[0].get("geometry")
        if geometries != "geojson" or not geometry:
            return {"type": "LineString", "coordinates": []}

        raw_coords = geometry.get("coordinates") or []
        result = {"type": "LineString", "coordinates": [[float(lon), float(lat)] for lon, lat in raw_coords]}
        if self._cache and cache_key:
            await self._cache.set(cache_key, result, CacheTTL.LONG)
        return result

    async def osrm_route_geojson(
        self,
        coordinates: Sequence[Coord],
        *,
        overview: str = "full",
        geometries: str = "geojson",
    ) -> Dict[str, Any]:
        return await self.get_route_geojson(coordinates, overview=overview, geometries=geometries)
