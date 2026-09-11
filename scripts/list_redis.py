import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from core.config import get_settings
import redis

settings = get_settings()
client = redis.from_url(settings.redis_url, decode_responses=True)
try:
    keys = client.keys('*vrp*')
    print('vrp keys:', keys)
    # Check Celery queues
    q_keys = client.keys('*queue*')
    print('queue-like keys:', q_keys)
except Exception as e:
    print('redis error', e)
