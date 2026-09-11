import os
import sys
import asyncio

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from workers.vrp_tasks import _process_vrp_job_async

if __name__ == "__main__":
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not job_id:
        print("Usage: run_vrp_sync.py <job_id>")
        raise SystemExit(2)
    asyncio.run(_process_vrp_job_async(job_id))
