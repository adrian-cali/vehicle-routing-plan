"""
Centralized application configuration.

All settings are loaded from environment variables with sensible defaults.
No hardcoded secrets. Every external dependency URL is configurable.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables."""

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/postgres",
        description="PostgreSQL connection string.",
    )
    db_pool_min: int = Field(default=2, description="Minimum DB pool connections.")
    db_pool_max: int = Field(default=25, description="Maximum DB pool connections.")

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string (broker + backend).",
    )

    # ── OSRM ──────────────────────────────────────────────────────────
    osrm_url: str = Field(
        default="http://localhost:5001",
        description="OSRM HTTP endpoint.",
    )
    osrm_timeout: float = Field(default=120.0, description="OSRM request timeout (s).")
    osrm_max_locations: int = Field(
        default=250,
        description="Max locations per OSRM table request.",
    )

    # ── VROOM ─────────────────────────────────────────────────────────
    vroom_url: str = Field(
        default="http://localhost:3000",
        description="VROOM HTTP endpoint.",
    )
    vroom_timeout: float = Field(default=300.0, description="VROOM request timeout (s).")

    # ── H3 ────────────────────────────────────────────────────────────
    h3_default_resolution: int = Field(
        default=7,
        description="Default H3 hexagonal resolution (7 ≈ 1.4 km edge).",
    )
    h3_max_k: int = Field(
        default=5,
        description="Maximum k-ring expansion for H3 nearest search.",
    )
    h3_adaptive_resolution: bool = Field(
        default=True,
        description="Auto-select H3 resolution based on task spread/density.",
    )
    h3_coverage_k: int = Field(
        default=2,
        description="K-ring radius for fieldman multi-cell coverage.",
    )
    h3_partition_threshold: int = Field(
        default=500,
        description="Partition VRP into sub-problems above this task count.",
    )
    h3_max_partition_size: int = Field(
        default=400,
        description="Max tasks per partition when problem is split.",
    )
    h3_load_balance_weight: float = Field(
        default=0.3,
        description="Weight for load-balancing penalty in FM-task assignment (0-1).",
    )

    # ── VRP ────────────────────────────────────────────────────────────
    geometry_route_limit: int = Field(
        default=50,
        description="Max routes to attach OSRM geometry to.",
    )
    osrm_concurrency: int = Field(
        default=40,
        description="Max concurrent OSRM requests during VRP processing.",
    )
    max_tasks_per_partition: int = Field(
        default=800,
        description="Max tasks per VROOM partition. Oversized partitions are sub-split.",
    )

    # ── Application ──────────────────────────────────────────────────
    app_name: str = Field(default="VRP Service", description="Application name.")
    debug: bool = Field(default=False, description="Enable debug mode.")
    log_level: str = Field(default="INFO", description="Logging level.")
    cors_origins: str = Field(
        default="*",
        description="Comma-separated allowed CORS origins.",
    )

    # Toggle to enable or disable legacy (unversioned) API routes.
    enable_legacy_routes: bool = Field(
        default=True,
        description="Enable legacy unversioned routes for backward compatibility.",
    )

    model_config = {"env_prefix": "", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def _resolve_env_file() -> str:
    """Find .env in the backend dir or project root."""
    backend_dir = Path(__file__).resolve().parent.parent
    candidates = [
        backend_dir / ".env",
        backend_dir.parent / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ".env"


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings(_env_file=_resolve_env_file())
