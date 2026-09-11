"""
Celery application configuration.

Broker and backend both use Redis. Task modules are auto-discovered.
"""

from __future__ import annotations

from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "vrp",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.imports = ("workers.vrp_tasks", "workers.background_tasks")
celery_app.conf.task_default_queue = "vrp_queue"
celery_app.conf.task_routes = {
    "process_vrp_job": {"queue": "vrp_queue"},
    "background_geocode": {"queue": "geocode_queue"},
    "background_bulk_geocode": {"queue": "geocode_queue"},
    "warm_overview_cache": {"queue": "vrp_queue"},
}
# Ensure VRP tasks are never starved by long geocoding jobs
celery_app.conf.worker_prefetch_multiplier = 1
