"""
Consistent API response envelope.

Every API response follows the shape:
  { "success": bool, "data": ..., "error": ... }
"""

from __future__ import annotations

from typing import Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Structured error payload."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response envelope."""

    success: bool
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None


def success_response(data: Any = None) -> Dict[str, Any]:
    """Build a successful response dict."""
    return {"success": True, "data": data, "error": None}


def error_response(
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an error response dict."""
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details},
    }
