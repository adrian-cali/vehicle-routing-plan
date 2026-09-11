import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings
import asyncpg


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT 1")
        print("db ok:", val)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
