import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings
import redis

settings = get_settings()
try:
    r = redis.from_url(settings.redis_url)
    pong = r.ping()
    print("redis ping:", pong)
except Exception as e:
    print("redis error:", e)
