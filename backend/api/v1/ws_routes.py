"""
WebSocket routes (v1).

Publishes real-time VRP job events via Redis pub/sub.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket

from core.database import get_redis_client
from core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/vrp/{job_id}")
async def vrp_ws(websocket: WebSocket, job_id: str) -> None:
    """Stream VRP job events to the client via WebSocket."""
    await websocket.accept()
    client = await get_redis_client()
    pubsub = client.pubsub()
    channel = f"vrp:job:{job_id}"
    await pubsub.subscribe(channel)
    logger.info("WebSocket connected for job %s", job_id)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not data:
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"event": "message", "data": data}
            await websocket.send_json(payload)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        logger.info("WebSocket disconnected for job %s", job_id)
