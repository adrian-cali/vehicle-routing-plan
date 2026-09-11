import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings

settings = get_settings()
print("redis_url=", settings.redis_url)
print("database_url=", settings.database_url)
