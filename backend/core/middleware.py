"""
FastAPI middleware: correlation ID injection, request timing, global exception handler.
"""

from __future__ import annotations

import uuid
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from core.exceptions import AppError
from core.logging import correlation_id_var, get_logger
from core.responses import error_response

logger = get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID to every request for tracing."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        cid = request.headers.get("x-correlation-id", str(uuid.uuid4()))
        correlation_id_var.set(cid)
        response = await call_next(request)
        response.headers["x-correlation-id"] = cid
        return response


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Log per-request timing with method, path, status, and correlation id."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        cid = correlation_id_var.get(None)
        logger.info(
            "HTTP %s %s -> %s (%.1fms) cid=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            cid,
        )
        return response


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized error handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "AppError: %s (code=%s, status=%d)",
            exc.message,
            exc.error_code,
            exc.status_code,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(exc.error_code, exc.message, exc.details),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_response("INTERNAL_ERROR", "An unexpected error occurred."),
        )
