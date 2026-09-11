"""
Structured JSON logging with correlation ID support.

Replaces all ``print()`` usage. Every log entry includes a timestamp,
level, module, and optional correlation_id for request tracing.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = correlation_id_var.get()
        if cid:
            entry["correlation_id"] = cid
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
