"""Quick script to clear all task and fieldmen data."""
import asyncio
import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "TRUNCATE vrp_assignments, vrp_jobs, fm_assigned_areas, fm_home_locations, tasks RESTART IDENTITY CASCADE"
    )
    r = await conn.fetchval("SELECT to_regclass('public.vrp_job_routes')")
    if r:
        await conn.execute("TRUNCATE vrp_job_routes RESTART IDENTITY CASCADE")
    print("All data cleared successfully.")
    await conn.close()

asyncio.run(main())
