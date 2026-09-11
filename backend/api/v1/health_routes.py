"""
Health check endpoints (v1).
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.config import get_settings
from core.database import get_db_pool, get_redis_client
from core.logging import get_logger
from core.responses import success_response, error_response

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    """Liveness + readiness probe. Always responds fast (<1s)."""
    db_status = "ok"
    try:
        pool = await get_db_pool()
        await asyncio.wait_for(pool.execute("SELECT 1"), timeout=2.0)
    except asyncio.TimeoutError:
        db_status = "slow"
    except Exception as exc:
        logger.error("Health check DB failed: %s", exc)
        db_status = "error"
        return JSONResponse(
            status_code=503,
            content=error_response(
                "HEALTH_CHECK_FAILED",
                "Service degraded",
                {"db": db_status, "detail": str(exc)},
            ),
        )
    return JSONResponse(
        content=success_response({"status": "ok", "db": db_status})
    )


@router.get("/health/deep")
async def deep_health_check() -> JSONResponse:
    """
    Deep health check — verifies connectivity to all external dependencies:
    PostgreSQL, Redis, OSRM, and VROOM.

    Returns 200 if all are healthy, 503 if any are degraded.
    """
    results = {
        "db": "unknown",
        "redis": "unknown",
        "osrm": "unknown",
        "vroom": "unknown",
    }
    overall = "ok"

    # Check PostgreSQL
    try:
        pool = await get_db_pool()
        version = await pool.fetchval("SELECT version()")
        results["db"] = "ok"
        results["db_version"] = version.split(",")[0] if version else "unknown"
    except Exception as exc:
        results["db"] = "error"
        results["db_detail"] = str(exc)
        overall = "degraded"

    # Check Redis
    try:
        redis_client = await get_redis_client()
        pong = await redis_client.ping()
        results["redis"] = "ok" if pong else "error"
    except Exception as exc:
        results["redis"] = "error"
        results["redis_detail"] = str(exc)
        overall = "degraded"

    # Check OSRM
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Simple nearest query to verify OSRM is responding
            resp = await client.get(
                f"{settings.osrm_url}/nearest/v1/driving/121.0,14.6",
                params={"number": "1"},
            )
            if resp.status_code == 200:
                results["osrm"] = "ok"
            else:
                results["osrm"] = "degraded"
                results["osrm_detail"] = f"HTTP {resp.status_code}"
                overall = "degraded"
    except Exception as exc:
        results["osrm"] = "error"
        results["osrm_detail"] = str(exc)
        overall = "degraded"

    # Check VROOM
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.vroom_url}/health")
            if resp.status_code == 200:
                results["vroom"] = "ok"
            else:
                # VROOM may not have /health — try a minimal solve
                resp2 = await client.post(
                    f"{settings.vroom_url}/",
                    json={"vehicles": [], "jobs": []},
                    timeout=5.0,
                )
                results["vroom"] = "ok" if resp2.status_code in (200, 400) else "degraded"
                if results["vroom"] == "degraded":
                    results["vroom_detail"] = f"HTTP {resp2.status_code}"
                    overall = "degraded"
    except Exception as exc:
        results["vroom"] = "error"
        results["vroom_detail"] = str(exc)
        overall = "degraded"

    results["status"] = overall
    status_code = 200 if overall == "ok" else 503

    return JSONResponse(
        status_code=status_code,
        content=success_response(results) if overall == "ok" else error_response(
            "HEALTH_DEGRADED",
            "One or more dependencies are unhealthy",
            results,
        ),
    )
