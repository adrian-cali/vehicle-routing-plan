"""
Task domain schemas.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TaskRecord(BaseModel):
    """A task as stored in the database."""

    id: str
    address: Optional[str] = None
    latitude: float
    longitude: float
    priority: float = Field(default=1.0, ge=0, le=100)
    manual_priority: Optional[float] = Field(default=None, ge=0, le=100, description="Manual priority set via map picker. Overrides route settings.")
    service: int = Field(default=0, ge=0, description="Service time in seconds.")


class TaskOverview(BaseModel):
    """Lightweight task representation for overview endpoints."""

    task_id: str
    address: Optional[str] = None
    latitude: float
    longitude: float
    task_type: Optional[str] = None
    bank: Optional[str] = None
    priority: Optional[float] = None
    manual_priority: Optional[float] = None
