"""
Audit trail domain schema.

Every auditable action is recorded with actor, timestamp, action type,
and before/after state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """A single audit log entry."""

    id: Optional[str] = None
    entity_type: str = Field(..., description="e.g. 'vrp_job', 'assignment', 'task'")
    entity_id: str
    action: str = Field(..., description="e.g. 'created', 'updated', 'finalized', 'deleted'")
    actor_id: Optional[str] = None
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
