"""
Background Celery tasks for heavy operations.

Offloads expensive computations from the request cycle:
- Bulk geocoding
- Metrics aggregation
- Cache warming

All tasks are idempotent and safe for retry.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import redis.asyncio as aioredis

from celery_app import celery_app
from core.config import get_settings
from core.logging import get_logger, setup_logging
from services.cache_service import CacheTTL, CacheService

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)


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


# ── Geocoding Background Task ────────────────────────────────────────


@celery_app.task(name="background_geocode", bind=True, max_retries=2)
def background_geocode(self, entity_type: str, entity_id: str, lat: float, lon: float) -> Dict[str, Any]:
    """
    Background geocoding for a single entity (task or fieldman).
    Caches the result and updates the database.

    Idempotent: safe to retry without side effects.
    """
    loop = _get_event_loop()
    return loop.run_until_complete(
        _background_geocode_async(entity_type, entity_id, lat, lon)
    )


async def _background_geocode_async(
    entity_type: str, entity_id: str, lat: float, lon: float
) -> Dict[str, Any]:
    """Async implementation of background geocoding."""
    redis_client = await _get_redis()
    cache = CacheService(redis_client)

    try:
        # Check cache first
        cache_key = cache.key_geocode(lat, lon)
        cached_address = await cache.get(cache_key)
        if cached_address:
            logger.info("Geocode cache hit for %s %s", entity_type, entity_id)
            address = cached_address
        else:
            from services.geocode_service import reverse_geocode
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                address = await reverse_geocode(lat, lon, client=client)
            # Cache the geocoded address
            await cache.set(cache_key, address, ttl=CacheTTL.GEOCODE)

        # Update database
        pool = await _get_pool()
        try:
            if entity_type == "task":
                await pool.execute(
                    "UPDATE tasks SET address = $1 WHERE id = $2",
                    address, entity_id,
                )
            elif entity_type == "fieldman":
                await pool.execute(
                    "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
                    address, entity_id,
                )
            # Invalidate overview cache since data changed
            await cache.invalidate_overview()
            return {"status": "ok", "address": address}
        finally:
            await pool.close()
    except Exception as exc:
        logger.warning("Background geocode failed for %s %s: %s", entity_type, entity_id, exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        await redis_client.close()


# ── Bulk Geocoding Task ──────────────────────────────────────────────


@celery_app.task(name="background_bulk_geocode", bind=True, max_retries=1,
                soft_time_limit=120, time_limit=180)
def background_bulk_geocode(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Background bulk geocoding for multiple entities.
    Each item: {"type": "task"|"fieldman", "id": "uuid", "lat": float, "lon": float}

    Respects rate limits and caches all results.
    Soft limit: 120s. Hard limit: 180s.
    """
    loop = _get_event_loop()
    return loop.run_until_complete(_background_bulk_geocode_async(items))


async def _background_bulk_geocode_async(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Async implementation of bulk geocoding with parallel batches."""
    import asyncio
    import httpx
    from services.geocode_service import reverse_geocode

    redis_client = await _get_redis()
    cache = CacheService(redis_client)
    pool = await _get_pool()
    geocoded = 0
    cached_hits = 0
    errors = 0
    BATCH_SIZE = 3  # Nominatim allows ~1 req/s; 3 concurrent is aggressive but safe with delay

    async def process_item(item: Dict, client: httpx.AsyncClient, sem: asyncio.Semaphore) -> None:
        nonlocal geocoded, cached_hits, errors
        async with sem:
            lat, lon = item["lat"], item["lon"]
            entity_type = item["type"]
            entity_id = item["id"]

            cache_key = cache.key_geocode(lat, lon)
            cached_address = await cache.get(cache_key)

            if cached_address:
                address = cached_address
                cached_hits += 1
            else:
                try:
                    address = await reverse_geocode(lat, lon, client=client)
                    await cache.set(cache_key, address, ttl=CacheTTL.GEOCODE)
                    geocoded += 1
                    # Rate limit between Nominatim calls
                    await asyncio.sleep(0.4)
                except Exception as exc:
                    exc_str = str(exc)
                    if "429" in exc_str:
                        errors += 1
                        raise  # Propagate 429 to trigger circuit breaker
                    logger.warning("Bulk geocode error for %s: %s", entity_id, exc)
                    errors += 1
                    return

            # Update database
            try:
                if entity_type == "task":
                    await pool.execute(
                        "UPDATE tasks SET address = $1 WHERE id = $2",
                        address, entity_id,
                    )
                elif entity_type == "fieldman":
                    await pool.execute(
                        "UPDATE fm_home_locations SET address = $1 WHERE user_id = $2",
                        address, entity_id,
                    )
            except Exception as exc:
                logger.warning("DB update failed for %s: %s", entity_id, exc)
                errors += 1

    try:
        sem = asyncio.Semaphore(BATCH_SIZE)
        consecutive_429 = 0
        MAX_CONSECUTIVE_429 = 5  # Stop after 5 consecutive 429 errors

        async with httpx.AsyncClient(timeout=10.0) as client:
            for batch_start in range(0, len(items), 10):
                batch = items[batch_start:batch_start + 10]
                tasks = [process_item(item, client, sem) for item in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check for 429 circuit breaker
                batch_429s = sum(1 for r in results if isinstance(r, Exception) and "429" in str(r))
                if batch_429s > 0:
                    consecutive_429 += batch_429s
                    if consecutive_429 >= MAX_CONSECUTIVE_429:
                        logger.warning(
                            "Bulk geocode: %d consecutive 429 errors — aborting to free worker. "
                            "Geocoded %d/%d so far.", consecutive_429, geocoded, len(items)
                        )
                        break
                    # Back off on 429s
                    await asyncio.sleep(5.0)
                else:
                    consecutive_429 = 0

                if (batch_start + 10) % 50 == 0:
                    logger.info(
                        "Bulk geocode progress: %d/%d (geocoded=%d cached=%d errors=%d)",
                        min(batch_start + 10, len(items)), len(items), geocoded, cached_hits, errors,
                    )

        # Invalidate overview cache after all geocoding done
        await cache.invalidate_overview()

        result = {
            "status": "ok",
            "total": len(items),
            "geocoded": geocoded,
            "cached_hits": cached_hits,
            "errors": errors,
        }
        logger.info("Bulk geocode completed: %s", result)
        return result
    finally:
        await pool.close()
        await redis_client.close()


# ── Cache Warming Task ────────────────────────────────────────────────


@celery_app.task(name="warm_overview_cache")
def warm_overview_cache() -> Dict[str, str]:
    """Pre-warm the overview cache in the background."""
    loop = _get_event_loop()
    return loop.run_until_complete(_warm_overview_cache_async())


async def _warm_overview_cache_async() -> Dict[str, str]:
    """Async implementation of cache warming."""
    redis_client = await _get_redis()
    cache = CacheService(redis_client)
    pool = await _get_pool()

    try:
        # Warm overview
        task_rows = await pool.fetch(
            "SELECT id, address, latitude, longitude FROM tasks ORDER BY id"
        )
        fm_rows = await pool.fetch(
            "SELECT user_id, address, home_lat, home_long FROM fm_home_locations ORDER BY user_id"
        )
        overview = {
            "tasks": [
                {
                    "task_id": str(r["id"]),
                    "address": r["address"],
                    "latitude": float(r["latitude"]),
                    "longitude": float(r["longitude"]),
                }
                for r in task_rows
            ],
            "fieldmen": [
                {
                    "fieldman_id": str(r["user_id"]),
                    "address": r["address"],
                    "latitude": float(r["home_lat"]),
                    "longitude": float(r["home_long"]),
                }
                for r in fm_rows
            ],
        }
        await cache.set(cache.key_overview(), overview, ttl=CacheTTL.OVERVIEW)
        logger.info("Overview cache warmed: %d tasks, %d fieldmen", len(task_rows), len(fm_rows))
        return {"status": "ok"}
    finally:
        await pool.close()
        await redis_client.close()
