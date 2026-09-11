"""
Redis cache service.

Provides a centralized, configurable, enterprise-ready caching layer.

Features:
- Type-safe get/set/delete with automatic JSON serialization
- TTL strategies per data type
- Structured logging for cache hits/misses
- Graceful fallback when Redis is unavailable
- Key namespacing to prevent collisions
- Cache invalidation by pattern
- Distributed-safe (works in multi-instance setup)
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import redis.asyncio as aioredis

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ── TTL Strategy ──────────────────────────────────────────────────────


class CacheTTL(int, Enum):
    """TTL strategies in seconds for different data types."""

    SHORT = 60            # Volatile data: overview, live locations
    MEDIUM = 300          # Semi-stable: task summaries, metrics
    LONG = 3600           # Stable: computed routes, distance matrices
    GEOCODE = 86400       # Very stable: reverse geocoded addresses (24h)
    ROUTE_GEOMETRY = 7200 # Route geometries (2h)
    DISTANCE_MATRIX = 3600  # OSRM distance matrices (1h)
    JOB_STATUS = 60       # Job status polling (1min)
    OVERVIEW = 120        # Overview data (2min — reduced DB load at scale)
    TASK_SUMMARY = 300    # Task summary (5min)
    VROOM_SOLUTION = 1800 # VROOM optimization results (30min)


# ── Cache Key Namespace ───────────────────────────────────────────────

CACHE_PREFIX = "vrp:cache"


def _build_key(*parts: str) -> str:
    """Build a namespaced cache key from parts."""
    return f"{CACHE_PREFIX}:{':'.join(parts)}"


def _hash_params(*args: Any) -> str:
    """Create a deterministic hash from parameters for cache keys."""
    serialized = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# ── Cache Service ─────────────────────────────────────────────────────


class CacheService:
    """
    Enterprise-ready Redis cache service.

    All operations are fault-tolerant — cache failures never break
    the application. Returns None on miss or error, and logs accordingly.
    """

    def __init__(self, redis_client: Optional[aioredis.Redis] = None) -> None:
        self._redis = redis_client

    @property
    def available(self) -> bool:
        """Check if Redis client is configured."""
        return self._redis is not None

    # ── Core Operations ───────────────────────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        """
        Get a cached value by key. Returns None on miss or error.
        Logs cache hit/miss for observability.
        """
        if not self.available:
            return None
        try:
            raw = await self._redis.get(key)
            if raw is None:
                logger.debug("Cache MISS: %s", key)
                return None
            logger.debug("Cache HIT: %s", key)
            return json.loads(raw)
        except (aioredis.RedisError, json.JSONDecodeError) as exc:
            logger.warning("Cache GET error for key=%s: %s", key, exc)
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = CacheTTL.MEDIUM,
    ) -> bool:
        """
        Set a cached value with TTL (seconds). Returns True on success.
        """
        if not self.available:
            return False
        try:
            serialized = json.dumps(value, default=str)
            await self._redis.set(key, serialized, ex=int(ttl))
            logger.debug("Cache SET: %s (ttl=%ds)", key, ttl)
            return True
        except (aioredis.RedisError, TypeError) as exc:
            logger.warning("Cache SET error for key=%s: %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a specific cache entry."""
        if not self.available:
            return False
        try:
            result = await self._redis.delete(key)
            logger.debug("Cache DELETE: %s (removed=%d)", key, result)
            return bool(result)
        except aioredis.RedisError as exc:
            logger.warning("Cache DELETE error for key=%s: %s", key, exc)
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a pattern (e.g., 'vrp:cache:overview:*').
        Uses SCAN for distributed safety — no KEYS command.
        Batches deletes via pipeline for performance.
        """
        if not self.available:
            return 0
        try:
            keys: list = []
            async for key in self._redis.scan_iter(match=pattern, count=100):
                keys.append(key)
            if not keys:
                return 0
            pipe = self._redis.pipeline(transaction=False)
            for key in keys:
                pipe.delete(key)
            await pipe.execute()
            logger.info("Cache INVALIDATE: pattern=%s deleted=%d", pattern, len(keys))
            return len(keys)
        except aioredis.RedisError as exc:
            logger.warning("Cache INVALIDATE error for pattern=%s: %s", pattern, exc)
            return 0

    async def exists(self, key: str) -> bool:
        """Check if a key exists in cache."""
        if not self.available:
            return False
        try:
            return bool(await self._redis.exists(key))
        except aioredis.RedisError:
            return False

    # ── High-Level Cache Helpers ──────────────────────────────────────

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Any,
        ttl: int = CacheTTL.MEDIUM,
    ) -> Any:
        """
        Get from cache or compute + store. The compute_fn must be an
        async callable that returns the value to cache.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached
        result = await compute_fn()
        await self.set(key, result, ttl=ttl)
        return result

    # ── Typed Cache Key Builders ──────────────────────────────────────

    @staticmethod
    def key_overview() -> str:
        """Cache key for the overview endpoint."""
        return _build_key("overview")

    @staticmethod
    def key_task_summary() -> str:
        """Cache key for task summary."""
        return _build_key("task_summary")

    @staticmethod
    def key_job_status(job_id: str) -> str:
        """Cache key for job status."""
        return _build_key("job", job_id, "status")

    @staticmethod
    def key_job_metrics(job_id: str) -> str:
        """Cache key for job metrics."""
        return _build_key("job", job_id, "metrics")

    @staticmethod
    def key_job_preview(job_id: str) -> str:
        """Cache key for job preview."""
        return _build_key("job", job_id, "preview")

    @staticmethod
    def key_job_assignments(job_id: str, include_geometry: bool) -> str:
        """Cache key for job assignments."""
        return _build_key("job", job_id, "assignments", str(include_geometry))

    @staticmethod
    def key_all_assignments(include_geometry: bool, include_overview: bool) -> str:
        """Cache key for all assignments."""
        return _build_key("assignments", "all", str(include_geometry), str(include_overview))

    @staticmethod
    def key_distance_matrix(coords_hash: str) -> str:
        """Cache key for OSRM distance matrix."""
        return _build_key("osrm", "matrix", coords_hash)

    @staticmethod
    def key_route_geometry(coords_hash: str) -> str:
        """Cache key for OSRM route geometry."""
        return _build_key("osrm", "route", coords_hash)

    @staticmethod
    def key_geocode(lat: float, lon: float) -> str:
        """Cache key for reverse geocoding."""
        return _build_key("geocode", f"{lat:.6f}", f"{lon:.6f}")

    @staticmethod
    def key_route_distance(start_hash: str, end_hash: str) -> str:
        """Cache key for route distance/duration."""
        return _build_key("osrm", "distance", start_hash, end_hash)

    # ── Cache Invalidation Helpers ────────────────────────────────────

    async def invalidate_overview(self) -> None:
        """Invalidate overview cache when tasks/fieldmen change."""
        await self.delete(self.key_overview())
        await self.delete(self.key_task_summary())
        await self.invalidate_h3()

    async def invalidate_h3(self) -> None:
        """Invalidate all H3 grid caches."""
        await self.delete_pattern("h3:grid:*")

    async def invalidate_job(self, job_id: str) -> None:
        """Invalidate all caches related to a specific job."""
        await self.delete_pattern(f"{CACHE_PREFIX}:job:{job_id}:*")
        # Also invalidate all-assignments since it aggregates jobs
        await self.delete_pattern(f"{CACHE_PREFIX}:assignments:*")

    async def invalidate_all_jobs(self) -> None:
        """Invalidate all job-related caches."""
        await self.delete_pattern(f"{CACHE_PREFIX}:job:*")
        await self.delete_pattern(f"{CACHE_PREFIX}:assignments:*")

    async def invalidate_routing(self) -> None:
        """Invalidate OSRM routing caches."""
        await self.delete_pattern(f"{CACHE_PREFIX}:osrm:*")

    async def invalidate_all(self) -> None:
        """Nuclear option: clear the entire cache namespace."""
        await self.delete_pattern(f"{CACHE_PREFIX}:*")


# ── Singleton Access ──────────────────────────────────────────────────

_cache_service: Optional[CacheService] = None


async def get_cache_service() -> CacheService:
    """
    Return a shared CacheService instance.
    Gracefully handles Redis unavailability.
    """
    global _cache_service
    if _cache_service is not None:
        return _cache_service

    try:
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        # Quick connectivity check
        await redis_client.ping()
        _cache_service = CacheService(redis_client)
        logger.info("Cache service initialized (Redis connected)")
    except Exception as exc:
        logger.warning("Cache service unavailable (Redis down): %s — running without cache", exc)
        _cache_service = CacheService(None)

    return _cache_service


async def close_cache_service() -> None:
    """Close the cache service Redis connection."""
    global _cache_service
    if _cache_service and _cache_service._redis:
        await _cache_service._redis.close()
        _cache_service = None
        logger.info("Cache service closed")
