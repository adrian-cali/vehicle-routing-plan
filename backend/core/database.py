"""
Database connection pool management.

Provides an async PostgreSQL connection pool (asyncpg) and a Redis
client, both managed as application lifespan resources.
"""

from __future__ import annotations

from typing import Optional

import asyncpg
import redis.asyncio as aioredis

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None
_redis: Optional[aioredis.Redis] = None


async def get_db_pool() -> asyncpg.Pool:
    """Return the shared asyncpg connection pool (lazy init)."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
        )
        logger.info("Database pool created")
    return _pool


async def ensure_tables() -> None:
    """Create all required tables if they don't exist.

    Reads ``init_db.sql`` from the scripts directory so that the schema
    definition stays in one place.  Safe to call on every startup because
    all statements use ``CREATE TABLE IF NOT EXISTS``.

    Also runs incremental migrations from ``scripts/migrations/``.
    """
    from pathlib import Path

    sql_path = Path(__file__).resolve().parent.parent / "scripts" / "init_db.sql"
    if not sql_path.exists():
        logger.warning("init_db.sql not found at %s — skipping auto-migration", sql_path)
        return

    ddl = sql_path.read_text(encoding="utf-8")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(ddl)
    logger.info("Database tables verified / created")

    # Run incremental migrations
    migrations_dir = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
    if migrations_dir.is_dir():
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            migration_sql = migration_file.read_text(encoding="utf-8")
            async with pool.acquire() as conn:
                await conn.execute(migration_sql)
            logger.info("Migration applied: %s", migration_file.name)


async def close_db_pool() -> None:
    """Gracefully close the database pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


async def get_redis_client() -> aioredis.Redis:
    """Return the shared Redis client (lazy init)."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis client created")
    return _redis


async def close_redis_client() -> None:
    """Gracefully close the Redis client."""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        logger.info("Redis client closed")
