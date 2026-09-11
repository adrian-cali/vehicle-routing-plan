import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings
import asyncpg


async def main(job_id: str):
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, status, status_detail FROM vrp_jobs WHERE id = $1", job_id)
        print(dict(row) if row else None)
    await pool.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: check_job_status.py <job_id>")
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
