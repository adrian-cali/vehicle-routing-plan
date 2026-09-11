import asyncio
import json
import os
import sys

# Ensure backend package is importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings
from celery_app import celery_app

import asyncpg


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        payload = {
            "requested_by": "test-run",
            "assignment_strategy": "h3",
            "h3_resolution": settings.h3_default_resolution,
            "task_limit": 1,
        }
        job_id = await conn.fetchval(
            "INSERT INTO vrp_jobs (status, request_payload) VALUES($1,$2) RETURNING id",
            "queued",
            json.dumps(payload),
        )
        print("Created test VRP job:", job_id)
    # Enqueue for processing
    celery_app.send_task("process_vrp_job", args=[str(job_id)])
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
