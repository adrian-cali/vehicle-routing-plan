"""
Audit trail repository.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import asyncpg

from core.logging import get_logger

logger = get_logger(__name__)


class AuditRepository:
    """Persist audit log entries."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_table(self) -> None:
        """Create the audit_log table if it doesn't exist."""
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                before_state JSONB,
                after_state JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

    async def record(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor_id: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert an audit log entry."""
        await self.ensure_table()
        await self._pool.execute(
            "INSERT INTO audit_log "
            "(entity_type, entity_id, action, actor_id, before_state, after_state) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            entity_type,
            entity_id,
            action,
            actor_id,
            json.dumps(before_state) if before_state else None,
            json.dumps(after_state) if after_state else None,
        )
        logger.info(
            "Audit: %s %s on %s/%s",
            actor_id or "system",
            action,
            entity_type,
            entity_id,
        )
