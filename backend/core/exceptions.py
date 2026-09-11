"""
Custom exception hierarchy.

All domain exceptions inherit from ``AppError`` so the global handler
can distinguish expected business errors from unexpected failures.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class AppError(Exception):
    """Base application error."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if message is not None:
            self.message = message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


# ── Domain-specific errors ────────────────────────────────────────────


class NotFoundError(AppError):
    """Requested resource does not exist."""

    status_code = 404
    error_code = "NOT_FOUND"
    message = "Resource not found."


class ValidationError(AppError):
    """Client provided invalid or incomplete data."""

    status_code = 400
    error_code = "VALIDATION_ERROR"
    message = "Validation failed."


class ConflictError(AppError):
    """Operation conflicts with current resource state."""

    status_code = 409
    error_code = "CONFLICT"
    message = "Resource conflict."


class ExternalServiceError(AppError):
    """An upstream service (OSRM, VROOM, etc.) failed."""

    status_code = 502
    error_code = "EXTERNAL_SERVICE_ERROR"
    message = "External service error."


class JobProcessingError(AppError):
    """VRP job processing failed."""

    status_code = 500
    error_code = "JOB_PROCESSING_ERROR"
    message = "Job processing failed."


class AuthenticationError(AppError):
    """Authentication required or failed."""

    status_code = 401
    error_code = "AUTHENTICATION_ERROR"
    message = "Authentication required."


class AuthorizationError(AppError):
    """Insufficient permissions."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"
    message = "Insufficient permissions."
