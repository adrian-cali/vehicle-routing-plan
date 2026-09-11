"""Quick check: verify fieldman task sequence numbers are preserved."""
import urllib.request
import json

FM_ID = "14078599-895d-4414-be07-e5f28b4d8f00"
URL = f"http://localhost:4000/api/v1/vrp/fieldmen/{FM_ID}/tasks"

r = urllib.request.urlopen(URL)
data = json.loads(r.read())
tasks = data.get("tasks", [])
print(f"{len(tasks)} tasks for FM {FM_ID[:12]}...")
print(f"  pending={data.get('pending')}, completed={data.get('completed')}")
print()
for t in tasks:
    seq = t.get("sequence")
    status = t.get("status")
    addr = (t.get("address") or "Unknown")[:40]
    print(f"  seq={seq:>3}  status={status:<10}  {addr}")
