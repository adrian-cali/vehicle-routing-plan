"""
VRP routes (v1).

Controllers are thin — validate input, delegate to VRPService,
return consistent response envelope.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse

from celery_app import celery_app
from core.database import get_db_pool
from core.logging import get_logger
from core.responses import success_response
from domain.vrp import (
    AssignmentResponse,
    CompleteTaskResponse,
    CreateJobRequest,
    CreateJobResponse,
    FieldmanTaskListResponse,
    H3GridRequest,
    H3GridResponse,
    JobMetricsResponse,
    JobStatusResponse,
    OverviewResponse,
    PlanAheadRequest,
    PlanAheadResponse,
    TaskSummaryResponse,
    AreaConfigItem,
)
from repositories.audit_repository import AuditRepository
from repositories.fieldman_repository import FieldmanRepository
from repositories.task_repository import TaskRepository
from repositories.vrp_repository import VRPJobRepository
from services.cache_service import get_cache_service
from services.vrp_service import VRPService

logger = get_logger(__name__)

router = APIRouter(prefix="/vrp", tags=["vrp"])


async def _get_service() -> VRPService:
    """Build VRPService with all dependencies including cache."""
    pool = await get_db_pool()
    cache = await get_cache_service()
    return VRPService(
        task_repo=TaskRepository(pool),
        fieldman_repo=FieldmanRepository(pool),
        vrp_repo=VRPJobRepository(pool),
        audit_repo=AuditRepository(pool),
        cache=cache,
    )


async def _current_user(
    x_user_id: Optional[str] = Header(default=None),
) -> Optional[str]:
    """Extract caller identity from header."""
    return x_user_id


# ── Plan Ahead ────────────────────────────────────────────────────────


@router.post("/plan-ahead", response_model=PlanAheadResponse)
async def plan_ahead(payload: PlanAheadRequest) -> PlanAheadResponse:
    """Dry-run: count tasks and fieldmen that match filters."""
    service = await _get_service()
    return await service.plan_ahead(
        task_ids=payload.task_ids,
        fieldman_ids=payload.fieldman_ids,
        area_ids=payload.area_ids,
        task_area_map=payload.task_area_map,
    )


# ── Job CRUD ──────────────────────────────────────────────────────────


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    payload: CreateJobRequest,
    current_user: Optional[str] = Depends(_current_user),
) -> CreateJobResponse:
    """Create a new VRP job and enqueue for processing."""
    service = await _get_service()
    return await service.create_job(
        payload.model_dump(),
        celery_app=celery_app,
        actor_id=current_user,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Return current status of a VRP job."""
    service = await _get_service()
    return await service.get_job_status(job_id)


@router.get("/jobs/{job_id}/metrics", response_model=JobMetricsResponse)
async def get_job_metrics(job_id: str) -> JobMetricsResponse:
    """Return aggregate metrics for a completed VRP job."""
    service = await _get_service()
    return await service.get_job_metrics(job_id)


# ── Assignments ───────────────────────────────────────────────────────


@router.get("/jobs/{job_id}/assignments")
async def get_job_assignments(
    job_id: str,
    include_geometry: bool = True,
) -> JSONResponse:
    """Return assignments for a specific job.

    Note: Response may be very large with geometry. Use curl for large results.
    """
    service = await _get_service()
    result = await service.get_job_assignments(
        job_id, include_geometry=include_geometry
    )
    return JSONResponse(content=result.model_dump())


@router.get("/assignments")
async def get_all_assignments(
    include_geometry: bool = True,
    include_overview: bool = True,
) -> JSONResponse:
    """Return all assignments across all jobs.

    Note: Response may be very large with geometry. Use curl for large results.
    """
    service = await _get_service()
    result = await service.get_all_assignments(
        include_geometry=include_geometry,
        include_overview=include_overview,
    )
    return JSONResponse(content=result.model_dump())


# ── Overview ──────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewResponse)
async def get_overview() -> OverviewResponse:
    """Return all tasks and fieldmen for the map overview."""
    service = await _get_service()
    return await service.get_overview()


# ── Preview & Finalize ────────────────────────────────────────────────


@router.get("/jobs/{job_id}/preview", response_class=JSONResponse, include_in_schema=False)
async def preview_job(job_id: str) -> JSONResponse:
    """Return preview data with route GeoJSON for a job.

    Response may be very large (route geometry); excluded from Swagger schema.
    Use the frontend or curl to view results.
    """
    service = await _get_service()
    data = await service.preview_job(job_id)
    return JSONResponse(content=data)


@router.get("/jobs/{job_id}/preview-page", response_class=HTMLResponse)
async def preview_job_html(job_id: str) -> HTMLResponse:
    """Return a self-contained HTML page for previewing job routes."""
    service = await _get_service()
    html = await service.preview_job_html(job_id)
    return HTMLResponse(content=html)


@router.post("/jobs/{job_id}/finalize")
async def finalize_job(
    job_id: str,
    current_user: Optional[str] = Depends(_current_user),
) -> Dict[str, Any]:
    """Mark a job as finalized."""
    service = await _get_service()
    return await service.finalize_job(job_id, actor_id=current_user)


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user: Optional[str] = Depends(_current_user),
) -> Dict[str, Any]:
    """Delete a job and all associated assignments/routes."""
    service = await _get_service()
    return await service.delete_job(job_id, actor_id=current_user)


# ── Task Summary ──────────────────────────────────────────────────────


@router.get("/task-summary", response_model=TaskSummaryResponse)
async def get_task_summary() -> TaskSummaryResponse:
    """Return aggregated task counts by type and bank."""
    service = await _get_service()
    return await service.get_task_summary()


# ── Job Listing ───────────────────────────────────────────────────────


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List jobs with optional status filter and pagination."""
    service = await _get_service()
    return await service.list_jobs(status=status, limit=limit, offset=offset)


# ── H3 Grid ───────────────────────────────────────────────────────


@router.post("/h3/grid", response_model=H3GridResponse)
async def get_h3_grid(payload: H3GridRequest) -> H3GridResponse:
    """Compute H3 hexagonal grid overlay with task/fieldman counts."""
    service = await _get_service()
    return await service.compute_h3_grid(
        resolution=payload.resolution,
        include_tasks=payload.include_tasks,
        include_fieldmen=payload.include_fieldmen,
    )


# ── Fieldman Task Management ──────────────────────────────────────────


@router.get("/fieldmen/{fieldman_id}/tasks", response_model=FieldmanTaskListResponse)
async def get_fieldman_tasks(fieldman_id: str) -> FieldmanTaskListResponse:
    """Return all tasks for a fieldman ordered by sequence.

    Includes both completed and pending tasks with status and sequence fields.
    """
    service = await _get_service()
    result = await service.get_fieldman_tasks(fieldman_id)
    return FieldmanTaskListResponse(**result)


@router.post(
    "/fieldmen/{fieldman_id}/tasks/{task_id}/complete",
    response_model=CompleteTaskResponse,
)
async def complete_fieldman_task(
    fieldman_id: str,
    task_id: str,
) -> CompleteTaskResponse:
    """Mark a task as completed for a fieldman.

    After marking the task done, the original sequence numbers are
    preserved — no renumbering occurs.

    Returns the updated task list so the frontend can immediately reflect
    the changes.
    """
    service = await _get_service()
    result = await service.complete_fieldman_task(fieldman_id, task_id)
    return CompleteTaskResponse(**result)


# ── Areas ─────────────────────────────────────────────────────────────


@router.get("/areas", response_model=List[AreaConfigItem])
async def get_areas() -> List[AreaConfigItem]:
    """Return all configured area definitions."""
    from services.data_service import DataService

    pool = await get_db_pool()
    cache = await get_cache_service()
    data_svc = DataService(
        task_repo=TaskRepository(pool),
        fieldman_repo=FieldmanRepository(pool),
        vrp_repo=VRPJobRepository(pool),
        cache=cache,
    )
    return data_svc.get_areas()
