"""
VRP Job domain schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Request / Input schemas ───────────────────────────────────────────


class PlanAheadRequest(BaseModel):
    """Input for the plan-ahead (dry-run) endpoint."""

    task_ids: Optional[List[str]] = None
    fieldman_ids: Optional[List[str]] = None
    area_ids: Optional[List[str]] = None
    assignment_strategy: str = Field("h3", pattern="^(manual_area|h3)$")
    h3_resolution: int = Field(default=9, ge=1, le=15)
    h3_auto_resolution: bool = Field(
        default=True,
        description="When True, auto-detect optimal H3 resolution from data spread/density.",
    )
    task_area_map: Optional[Dict[str, str]] = None
    task_limit: Optional[int] = Field(default=None, ge=1)
    task_offset: Optional[int] = Field(default=None, ge=0)
    fieldman_limit: Optional[int] = Field(default=None, ge=1)
    fieldman_offset: Optional[int] = Field(default=None, ge=0)


class CreateJobRequest(PlanAheadRequest):
    """Input for creating a new VRP job."""

    requested_by: Optional[str] = None
    priority_min: Optional[float] = Field(default=None, ge=0, le=100)
    priority_max: Optional[float] = Field(default=None, ge=0, le=100)


# ── Response / Output schemas ─────────────────────────────────────────


class PlanAheadResponse(BaseModel):
    """Plan-ahead result showing matching counts."""

    tasks: int
    fieldmen: int


class CreateJobResponse(BaseModel):
    """Response after creating a VRP job."""

    job_id: str


class JobStatusResponse(BaseModel):
    """Current status of a VRP job."""

    job_id: str
    status: str
    status_detail: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finalized_at: Optional[str] = None
    h3_resolution_info: Optional["H3ResolutionInfo"] = None


class H3ResolutionInfo(BaseModel):
    """Metadata about the H3 resolution used for a VRP job."""

    resolution: int = Field(..., description="The H3 resolution used for this job.")
    mode: str = Field(
        ...,
        pattern="^(auto|manual)$",
        description="Whether the resolution was auto-detected or manually specified.",
    )
    auto_suggested: Optional[int] = Field(
        default=None,
        description="The resolution suggested by auto-detection (present even when mode=manual).",
    )
    bbox_diagonal_km: Optional[float] = Field(
        default=None,
        description="Bounding-box diagonal of input coordinates in km.",
    )


class JobMetricsResponse(BaseModel):
    """Aggregate metrics for a completed VRP job."""

    job_id: str
    tasks_assigned: int
    fieldmen_used: int
    total_distance: float
    total_duration: float


class AssignmentTask(BaseModel):
    """A single task within a route assignment."""

    task_id: str
    sequence: int
    address: Optional[str] = None
    latitude: float
    longitude: float
    distance: float = 0
    duration: float = 0
    service: int = 0
    priority: float = 1.0
    manual_priority: Optional[float] = None


class AssignmentRoute(BaseModel):
    """A complete route assigned to one fieldman."""

    job_id: Optional[str] = None
    fieldman_id: str
    start_lat: float
    start_long: float
    tasks: List[AssignmentTask]
    geometry: Optional[Any] = None


class OverviewResponse(BaseModel):
    """Overview of all tasks and fieldmen."""

    tasks: List["TaskOverviewItem"]
    fieldmen: List["FieldmanOverviewItem"]


class TaskOverviewItem(BaseModel):
    task_id: str
    address: Optional[str] = None
    latitude: float
    longitude: float
    task_type: Optional[str] = None
    bank: Optional[str] = None
    priority: Optional[float] = None
    manual_priority: Optional[float] = None


class FieldmanOverviewItem(BaseModel):
    fieldman_id: str
    address: Optional[str] = None
    latitude: float
    longitude: float


class AssignmentResponse(BaseModel):
    """Full assignment response with routes and optional overview."""

    routes: List[AssignmentRoute]
    meta: Optional[Dict[str, Any]] = None
    overview: Optional[OverviewResponse] = None


# ── Task Summary ──────────────────────────────────────────────────────


class TaskSummaryResponse(BaseModel):
    """Aggregated task counts by type and bank."""

    total_tasks: int
    total_fieldmen: int
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_bank: Dict[str, int] = Field(default_factory=dict)


# ── Randomize ─────────────────────────────────────────────────────────


class RandomizeRequest(BaseModel):
    """Input for generating random tasks and fieldmen."""

    tasks_ci: int = Field(100, ge=0, description="Credit investigation tasks")
    tasks_sc: int = Field(50, ge=0, description="Skips & collect tasks")
    tasks_dl: int = Field(30, ge=0, description="Demand letter tasks")
    num_fieldmen: int = Field(20, ge=0)
    task_area: str = Field("AREA_NORTH")
    fieldman_areas: List[str] = Field(default_factory=lambda: ["AREA_NORTH"])
    scatterness: int = Field(50, ge=0, le=100)
    area_radius_km: float = Field(15.0, ge=1, le=100)
    service_time_minutes: int = Field(30, ge=1, le=480)
    task_banks: Optional[str] = None
    center_lat: Optional[float] = None
    center_lng: Optional[float] = None
    num_jobs: int = Field(1, ge=1, le=100)


class RandomizeResponse(BaseModel):
    """Result from randomize endpoint."""

    tasks_created: int
    fieldmen_created: int
    task_types: Dict[str, int]
    jobs_generated: int
    overview: Optional[OverviewResponse] = None


# ── Optimize ──────────────────────────────────────────────────────────


class OptimizeSettingsRequest(BaseModel):
    """Input for creating an optimization job with settings."""

    task_ids: Optional[List[str]] = None
    fieldman_ids: Optional[List[str]] = None
    area_ids: Optional[List[str]] = None
    assignment_strategy: str = Field("h3", pattern="^(manual_area|h3|km)$")
    h3_resolution: int = Field(default=9, ge=1, le=15)
    h3_auto_resolution: bool = Field(
        default=True,
        description="When True, auto-detect optimal H3 resolution from data. "
        "When False, use the provided h3_resolution value.",
    )
    task_area_map: Optional[Dict[str, str]] = None
    task_limit: Optional[int] = Field(default=None, ge=1)
    task_offset: Optional[int] = Field(default=None, ge=0)
    fieldman_limit: Optional[int] = Field(default=None, ge=1)
    fieldman_offset: Optional[int] = Field(default=None, ge=0)
    priority_min: Optional[float] = Field(default=None, ge=0, le=100)
    priority_max: Optional[float] = Field(default=None, ge=0, le=100)
    requested_by: Optional[str] = None
    areas: Optional[str] = None
    max_tasks_per_fieldman: Optional[int] = None
    max_distance_km: Optional[float] = None
    # Legacy fields (used by legacy optimizer route)
    bank_quota_enabled: bool = False
    bank_quota_distribution: Optional[str] = None
    service_time_minutes: int = Field(30, ge=1, le=480)
    max_radius_km: float = Field(5.0, ge=1, le=50)
    nearby_filter: bool = False


# ── Map Picker ────────────────────────────────────────────────────────


class PickerTaskRequest(BaseModel):
    """Input for creating a task from map click."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    task_type: str = Field(
        "credit_investigation",
        pattern="^(credit_investigation|skips_collect|demand_letter)$",
    )
    bank: Optional[str] = None
    service_time_minutes: int = Field(30, ge=1, le=480)
    manual_priority: Optional[float] = Field(default=None, ge=0, le=100, description="Manual priority override. If set, ignores route settings priority.")


class PickerFieldmanRequest(BaseModel):
    """Input for creating a fieldman from map click."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    area: str = Field("AREA_NORTH")


class UpdateFieldmanLocationRequest(BaseModel):
    """Input for updating a fieldman's home location."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class UpdateTaskPriorityRequest(BaseModel):
    """Input for updating a task's manual priority."""

    manual_priority: Optional[float] = Field(default=None, ge=0, le=100, description="Manual priority override. Set to null to clear.")


class PickerItemResponse(BaseModel):
    """Response for a picker-created item."""

    id: str
    latitude: float
    longitude: float
    address: Optional[str] = None
    type: str  # "task" or "fieldman"
    task_type: Optional[str] = None
    bank: Optional[str] = None
    manual_priority: Optional[float] = None


# ── Geocode ───────────────────────────────────────────────────────────


class GeocodeResponse(BaseModel):
    """Result from batch geocoding."""

    updated: int
    total: int


# ── Area Config ───────────────────────────────────────────────────────


class AreaConfigItem(BaseModel):
    """A single area configuration."""

    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    center_lat: float
    center_lng: float


# ── H3 Grid ──────────────────────────────────────────────────────────


class H3CellInfo(BaseModel):
    """A single H3 hexagonal cell with boundary and metadata."""

    cell: str
    boundary: List[List[float]]  # [[lat, lng], ...]
    task_count: int = 0
    fieldman_count: int = 0
    center_lat: float
    center_lng: float


class H3GridRequest(BaseModel):
    """Input for computing H3 grid from backend."""

    resolution: int = Field(default=9, ge=1, le=15)
    include_tasks: bool = True
    include_fieldmen: bool = True


class H3GridResponse(BaseModel):
    """Server-computed H3 grid with cell metadata."""

    resolution: int
    cells: List[H3CellInfo]
    total_tasks: int = 0
    total_fieldmen: int = 0


# ── Fieldman Task Management ─────────────────────────────────────────


class FieldmanTaskItem(BaseModel):
    """A single task in a fieldman's task list."""

    task_id: str
    sequence: Optional[int] = None
    status: str = "pending"
    completed_at: Optional[str] = None
    address: Optional[str] = None
    latitude: float
    longitude: float
    distance: float = 0
    duration: float = 0
    service: int = 0
    priority: float = 1.0
    manual_priority: Optional[float] = None
    task_type: Optional[str] = None
    bank: Optional[str] = None
    job_id: Optional[str] = None


class FieldmanTaskListResponse(BaseModel):
    """Response for GET /fieldmen/{fieldman_id}/tasks."""

    fieldman_id: str
    tasks: List[FieldmanTaskItem]
    total: int
    pending: int
    completed: int
    message: Optional[str] = None


class CompleteTaskResponse(BaseModel):
    """Response for POST /fieldmen/{fieldman_id}/tasks/{task_id}/complete."""

    fieldman_id: str
    task_id: str
    message: str
    tasks: List[FieldmanTaskItem]
    pending: int
    completed: int
