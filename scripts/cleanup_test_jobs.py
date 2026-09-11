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
        # Find test jobs (created by scripts/enqueue_test_job.py with requested_by 'test-run')
        rows = await conn.fetch("SELECT id FROM vrp_jobs WHERE request_payload::text ILIKE $1", '%test-run%')
        ids = [str(r['id']) for r in rows]
        if not ids:
            print('No test VRP jobs found')
            await pool.close()
            return

        print('Found test job ids:', ids)

        # Delete related assignments and routes, then jobs
        await conn.execute("DELETE FROM vrp_assignments WHERE job_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM vrp_job_routes WHERE job_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM vrp_jobs WHERE id = ANY($1::uuid[])", ids)
        print('Deleted test jobs and related rows')

    await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
