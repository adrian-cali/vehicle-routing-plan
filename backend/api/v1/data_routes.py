"""
Data management routes (v1).

Endpoints for randomize, reset, optimize, picker, and geocode.
Thin controllers — delegate to DataService.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header

from celery_app import celery_app
from core.database import get_db_pool
from core.logging import get_logger
from core.responses import success_response
from domain.vrp import (
    CreateJobResponse,
    GeocodeResponse,
    OptimizeSettingsRequest,
    PickerFieldmanRequest,
    PickerItemResponse,
    PickerTaskRequest,
    RandomizeRequest,
    RandomizeResponse,
    UpdateFieldmanLocationRequest,
    UpdateTaskPriorityRequest,
)
from repositories.fieldman_repository import FieldmanRepository
from repositories.task_repository import TaskRepository
from repositories.vrp_repository import VRPJobRepository
from services.cache_service import get_cache_service
from services.data_service import DataService

logger = get_logger(__name__)

router = APIRouter(prefix="/vrp", tags=["data"])


async def _get_data_service() -> DataService:
    """Build DataService with all dependencies."""
    pool = await get_db_pool()
    cache = await get_cache_service()
    return DataService(
        task_repo=TaskRepository(pool),
        fieldman_repo=FieldmanRepository(pool),
        vrp_repo=VRPJobRepository(pool),
        cache=cache,
    )


# ── Randomize ─────────────────────────────────────────────────────────


@router.post("/randomize", response_model=RandomizeResponse)
async def randomize_data(payload: RandomizeRequest) -> RandomizeResponse:
    """Generate random tasks and fieldmen in the database."""
    service = await _get_data_service()
    return await service.randomize(payload, celery_app=celery_app)


# ── Data Reset ────────────────────────────────────────────────────────


@router.post("/data/reset")
async def reset_all_data() -> Dict[str, str]:
    """Truncate all task, fieldman, and job data."""
    service = await _get_data_service()
    return await service.reset_all_data()


# ── Optimize ──────────────────────────────────────────────────────────


@router.post("/optimize", response_model=CreateJobResponse)
async def optimize_routes(
    payload: OptimizeSettingsRequest,
) -> CreateJobResponse:
    """Create an optimization job with extended settings."""
    service = await _get_data_service()
    return await service.optimize(payload, celery_app=celery_app)


# ── Geocode ───────────────────────────────────────────────────────────


@router.post("/geocode-addresses", response_model=GeocodeResponse)
async def geocode_existing_addresses() -> GeocodeResponse:
    """
    Dispatch bulk geocoding to Celery worker (non-blocking).
    Returns immediately with estimated count. Actual geocoding happens in background.
    """
    service = await _get_data_service()
    return await service.geocode_addresses_async(celery_app=celery_app)


# ── Map Picker ────────────────────────────────────────────────────────


@router.post("/picker/task", response_model=PickerItemResponse)
async def picker_create_task(
    payload: PickerTaskRequest,
) -> PickerItemResponse:
    """Create a single task from a map-picked location."""
    service = await _get_data_service()
    return await service.picker_create_task(payload)


@router.post("/picker/fieldman", response_model=PickerItemResponse)
async def picker_create_fieldman(
    payload: PickerFieldmanRequest,
) -> PickerItemResponse:
    """Create a single fieldman from a map-picked location."""
    service = await _get_data_service()
    return await service.picker_create_fieldman(payload)


@router.delete("/picker/task/{task_id}")
async def picker_delete_task(task_id: str) -> Dict[str, str]:
    """Remove a single picked task."""
    service = await _get_data_service()
    return await service.picker_delete_task(task_id)


@router.delete("/picker/fieldman/{fieldman_id}")
async def picker_delete_fieldman(fieldman_id: str) -> Dict[str, str]:
    """Remove a single picked fieldman."""
    service = await _get_data_service()
    return await service.picker_delete_fieldman(fieldman_id)


# ── FM Location Update ──────────────────────────────────────────────────


@router.put("/fieldman/{fieldman_id}/location", response_model=PickerItemResponse)
async def update_fieldman_location(
    fieldman_id: str,
    payload: UpdateFieldmanLocationRequest,
) -> PickerItemResponse:
    """Update a fieldman's home location."""
    service = await _get_data_service()
    return await service.update_fieldman_location(
        fieldman_id, payload.latitude, payload.longitude,
    )


# ── Task Priority Update ────────────────────────────────────────────────


@router.put("/picker/task/{task_id}/priority")
async def update_task_priority(
    task_id: str,
    payload: UpdateTaskPriorityRequest,
) -> Dict[str, Any]:
    """Update or clear the manual priority for a task."""
    service = await _get_data_service()
    return await service.update_task_priority(task_id, payload.manual_priority)
