"""
Application entry point.

Creates the FastAPI app with:
- Structured logging
- Correlation-ID middleware
- Centralized exception handlers
- Versioned API routes (/api/v1/...)
- Legacy unversioned routes (backward compatible)
- Static file serving for the frontend
- Graceful startup/shutdown via lifespan
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.v1.health_routes import router as health_router_v1
from api.v1.vrp_routes import router as vrp_router_v1
from api.v1.data_routes import router as data_router_v1
from api.v1.ws_routes import router as ws_router_v1

# Legacy routers (kept for backward compatibility)
from api.health_routes import router as health_router_legacy
from api.vrp_routes import router as vrp_router_legacy
from api.ws_routes import router as ws_router_legacy

from core.config import get_settings
from core.database import close_db_pool, close_redis_client, ensure_tables
from core.logging import setup_logging, get_logger
from core.middleware import CorrelationIdMiddleware, RequestTimingMiddleware, register_exception_handlers
from services.cache_service import close_cache_service

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    logger.info("Application starting up")
    await ensure_tables()
    yield
    logger.info("Application shutting down")
    await close_cache_service()
    await close_db_pool()
    await close_redis_client()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RequestTimingMiddleware)
register_exception_handlers(app)

# ── Static files ──────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Frontend ──────────────────────────────────────────────────────────
@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page frontend."""
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


# ── Versioned API routes (v1) ─────────────────────────────────────────
app.include_router(vrp_router_v1, prefix="/api/v1")
app.include_router(data_router_v1, prefix="/api/v1")
app.include_router(ws_router_v1, prefix="/api/v1")
app.include_router(health_router_v1, prefix="/api/v1")

# ── Legacy routes (backward compatibility — no /api/v1 prefix) ────────
if settings.enable_legacy_routes:
    app.include_router(vrp_router_legacy)
    app.include_router(ws_router_legacy)
    app.include_router(health_router_legacy)
else:
    logger.info("Legacy routes disabled by settings; only /api/v1 routes are mounted")
