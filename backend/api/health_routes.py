"""Legacy health-check route (unversioned)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.database import get_db_pool
from core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", deprecated=True)
async def health_check() -> JSONResponse:
    """Basic liveness check. Use /api/v1/health for the versioned endpoint."""
    logger.warning("DEPRECATED: Legacy /health endpoint called. Migrate to /api/v1/health.")
    try:
        pool = await get_db_pool()
        await pool.execute("SELECT 1")
        return JSONResponse(content={"status": "ok", "db": "ok"})
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "error", "detail": str(exc)},
        )
