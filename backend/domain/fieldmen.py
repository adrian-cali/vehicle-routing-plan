"""
Fieldman domain schemas.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class FieldmanRecord(BaseModel):
    """A fieldman as stored in the database."""

    user_id: str
    address: Optional[str] = None
    home_lat: float
    home_long: float
    area_ids: List[str] = []
    current_lat: Optional[float] = None
    current_long: Optional[float] = None


class FieldmanOverview(BaseModel):
    """Lightweight fieldman representation for overview endpoints."""

    fieldman_id: str
    address: Optional[str] = None
    latitude: float
    longitude: float
