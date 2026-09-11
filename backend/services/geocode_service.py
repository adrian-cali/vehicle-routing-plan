"""
Reverse geocoding service using OpenStreetMap Nominatim.

Provides real street addresses from lat/lon coordinates.
Includes rate limiting to respect Nominatim's usage policy (1 req/s).
Redis caching to avoid redundant external API calls.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.logging import get_logger

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "VRP-Console/1.0"


def _geocode_cache_key(lat: float, lon: float) -> str:
    """Deterministic cache key for a lat/lon pair (rounded to 6 decimals)."""
    return f"geocode:{lat:.6f}:{lon:.6f}"


async def reverse_geocode(
    lat: float,
    lon: float,
    *,
    client: Optional[httpx.AsyncClient] = None,
    cache=None,
) -> str:
    """Return a human-readable address for the given coordinates.
    
    If *cache* (CacheService) is provided, results are cached with GEOCODE TTL (24h).
    """
    # Check cache first
    cache_key = _geocode_cache_key(lat, lon)
    if cache is not None:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "addressdetails": 1,
        "zoom": 18,
    }
    headers = {"User-Agent": USER_AGENT}

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10.0)

    try:
        resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        address = _format_address(data)
        # Store in cache
        if cache is not None:
            from services.cache_service import CacheTTL
            await cache.set(cache_key, address, CacheTTL.GEOCODE)
        return address
    except Exception as e:
        logger.warning("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
        return f"{lat:.6f}, {lon:.6f}"
    finally:
        if own_client:
            await client.aclose()


def _format_address(data: Dict[str, Any]) -> str:
    """Extract a Grab/Angkas-style concise address from Nominatim response.
    
    Target format: "Street, Barangay/Neighbourhood, City"
    """
    addr = data.get("address", {})

    parts = []

    # Street-level detail (road + optional house number)
    road = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
    house = addr.get("house_number", "")
    if road:
        parts.append(f"{house} {road}".strip() if house else road)

    # Barangay / neighbourhood / subdivision
    # In PH context: quarter = barangay, neighbourhood = subdivision
    neighbourhood = addr.get("neighbourhood") or addr.get("suburb") or ""
    barangay = addr.get("quarter") or addr.get("village") or ""

    if barangay and barangay != road:
        parts.append(f"Brgy. {barangay}")
    elif neighbourhood and neighbourhood != road:
        parts.append(neighbourhood)

    # City / municipality
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("municipality")
        or addr.get("city_district")
        or ""
    )
    if city and city not in parts:
        parts.append(city)

    if parts:
        return ", ".join(parts)

    # Fallback to display_name (truncated)
    display = data.get("display_name", "")
    if display:
        # Take first 3 comma-separated parts
        segments = [s.strip() for s in display.split(",")][:3]
        return ", ".join(segments)

    return f"{data.get('lat', '?')}, {data.get('lon', '?')}"


async def batch_reverse_geocode(
    coordinates: List[Tuple[float, float]],
    *,
    delay: float = 1.1,
    max_concurrent: int = 1,
    cache=None,
) -> List[str]:
    """
    Reverse geocode a batch of coordinates.

    Respects Nominatim's rate limit of 1 request/second.
    Returns list of addresses in the same order as input coordinates.
    Uses cache to skip already-known coordinates.
    """
    results: List[str] = [""] * len(coordinates)

    # Pre-fill from cache
    uncached_indices: List[int] = []
    if cache is not None:
        for i, (lat, lon) in enumerate(coordinates):
            cached = await cache.get(_geocode_cache_key(lat, lon))
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
        if uncached_indices:
            logger.info("Geocode cache: %d/%d hits, %d to fetch", len(coordinates) - len(uncached_indices), len(coordinates), len(uncached_indices))
    else:
        uncached_indices = list(range(len(coordinates)))

    if not uncached_indices:
        return results

    async with httpx.AsyncClient(timeout=10.0) as client:
        for idx, i in enumerate(uncached_indices):
            lat, lon = coordinates[i]
            results[i] = await reverse_geocode(lat, lon, client=client, cache=cache)
            if idx < len(uncached_indices) - 1:
                await asyncio.sleep(delay)
            if (idx + 1) % 25 == 0:
                logger.info(
                    "Geocoded %d/%d addresses", idx + 1, len(uncached_indices)
                )

    return results
