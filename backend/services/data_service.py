"""
Data management service.

Encapsulates business logic for: randomize, reset, optimize, picker,
geocode, and area configuration. Follows the same layered pattern as
VRPService — all DB access goes through repositories.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.config import get_settings
from core.exceptions import ValidationError
from core.logging import get_logger
from domain.vrp import (
    AreaConfigItem,
    GeocodeResponse,
    OptimizeSettingsRequest,
    OverviewResponse,
    PickerFieldmanRequest,
    PickerItemResponse,
    PickerTaskRequest,
    RandomizeRequest,
    RandomizeResponse,
    TaskOverviewItem,
    FieldmanOverviewItem,
    CreateJobResponse,
)
from repositories.fieldman_repository import FieldmanRepository
from repositories.task_repository import TaskRepository
from repositories.vrp_repository import VRPJobRepository
from services.cache_service import CacheService
from services.osrm_service import OSRMService

try:
    from global_land_mask import globe
except Exception:  # pragma: no cover
    globe = None

logger = get_logger(__name__)

if globe is None:
    logger.warning(
        "global_land_mask is NOT installed — water/land validation is DISABLED. "
        "Install with: pip install global-land-mask"
    )
settings = get_settings()


# ── Area Configurations (same as legacy) ──────────────────────────────

AREA_CONFIGS: Dict[str, Dict[str, Any]] = {
    "AREA_NORTH": {
        "lat_min": 14.65, "lat_max": 14.78,
        "lon_min": 120.95, "lon_max": 121.05,
        "center": (14.715, 121.0),
    },
    "AREA_SOUTH": {
        "lat_min": 14.43, "lat_max": 14.55,
        "lon_min": 121.00, "lon_max": 121.09,
        "center": (14.49, 121.045),
    },
    "AREA_EAST": {
        "lat_min": 14.55, "lat_max": 14.68,
        "lon_min": 121.05, "lon_max": 121.15,
        "center": (14.615, 121.1),
    },
}

TASK_TYPES = {
    "credit_investigation": "CI",
    "skips_collect": "S&C",
    "demand_letter": "DL",
}

# Known coastal strips in AREA_SOUTH that still appear as water in map tiles.
COASTAL_EXCLUSION_ZONES: List[Dict[str, float]] = [
    {"lat_min": 14.44, "lat_max": 14.53, "lon_min": 120.985, "lon_max": 121.015},
]


def _generate_point(
    area: Dict[str, Any], scatterness_pct: int, radius_km: float,
) -> Tuple[float, float]:
    """Generate a random lat/lng within area bounds with scatterness."""
    center_lat, center_lon = area["center"]
    lat_range = area["lat_max"] - area["lat_min"]
    lon_range = area["lon_max"] - area["lon_min"]

    spread = max(0.05, scatterness_pct / 100.0)
    lat = random.gauss(center_lat, lat_range * spread * 0.35)
    lon = random.gauss(center_lon, lon_range * spread * 0.35)

    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * math.cos(math.radians(center_lat))
    dlat = (lat - center_lat) * km_per_deg_lat
    dlon = (lon - center_lon) * km_per_deg_lon
    dist = math.sqrt(dlat ** 2 + dlon ** 2)
    if dist > radius_km:
        scale = radius_km / dist
        lat = center_lat + (lat - center_lat) * scale
        lon = center_lon + (lon - center_lon) * scale

    lat = max(area["lat_min"], min(area["lat_max"], lat))
    lon = max(area["lon_min"], min(area["lon_max"], lon))
    return lat, lon


def _offset_point_meters(lat: float, lon: float, meters: float, bearing_deg: float) -> Tuple[float, float]:
    """Offset a coordinate by meters and bearing (approx, suitable for city scale)."""
    bearing_rad = math.radians(bearing_deg)
    dlat = (meters * math.cos(bearing_rad)) / 111_000.0
    dlng = (meters * math.sin(bearing_rad)) / (111_000.0 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlng


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate meter distance between two points (city-scale)."""
    mean_lat = math.radians((lat1 + lat2) * 0.5)
    m_per_deg_lat = 111_000.0
    m_per_deg_lon = 111_000.0 * max(0.2, math.cos(mean_lat))
    dlat = (lat2 - lat1) * m_per_deg_lat
    dlon = (lon2 - lon1) * m_per_deg_lon
    return math.hypot(dlat, dlon)


def _estimate_min_spacing_m(radius_km: float, point_count: int, scatterness: int) -> float:
    """Estimate target minimum spacing between generated points."""
    if point_count <= 1:
        return 0.0
    area_m2 = math.pi * (max(1.0, radius_km) * 1000.0) ** 2
    natural_spacing = math.sqrt(area_m2 / max(1, point_count))
    spread_factor = 0.35 + (max(0, min(100, scatterness)) / 100.0) * 0.55
    return max(35.0, min(450.0, natural_spacing * spread_factor))


def _is_land_point(lat: float, lon: float) -> bool:
    """Return True when a coordinate is on land according to global land mask."""
    if globe is None:
        return True
    try:
        return bool(globe.is_land(float(lat), float(lon)))
    except Exception:
        return True


def _is_excluded_coastal_zone(lat: float, lon: float) -> bool:
    for zone in COASTAL_EXCLUSION_ZONES:
        if zone["lat_min"] <= lat <= zone["lat_max"] and zone["lon_min"] <= lon <= zone["lon_max"]:
            return True
    return False


def _is_stable_land(lat: float, lon: float, sample_m: float = 180.0) -> bool:
    """Return True when coordinate is on interior/stable land, not thin bridge-like strips."""
    if _is_excluded_coastal_zone(lat, lon):
        return False
    if not _is_land_point(lat, lon):
        return False
    if globe is None:
        return True

    bearings = (0, 45, 90, 135, 180, 225, 270, 315)
    land_neighbors = 0
    for bearing in bearings:
        nlat, nlon = _offset_point_meters(lat, lon, sample_m, bearing)
        if _is_land_point(nlat, nlon):
            land_neighbors += 1
    return land_neighbors >= 7


def _parse_bank_distribution(raw: Optional[str]) -> Dict[str, float]:
    """Parse 'BPI:60,BDO:40' into a probability distribution."""
    if not raw:
        return {"BPI": 0.6, "BDO": 0.4}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    dist: Dict[str, float] = {}
    for part in parts:
        if ":" in part:
            bank, weight = part.split(":", 1)
            dist[bank.strip()] = float(weight.strip())
        else:
            dist[part.strip()] = 1.0
    total = sum(dist.values()) or 1.0
    return {k: v / total for k, v in dist.items()}


def _pick_bank(dist: Dict[str, float]) -> str:
    """Weighted random pick from bank distribution."""
    r = random.random()
    cumulative = 0.0
    for bank, prob in dist.items():
        cumulative += prob
        if r <= cumulative:
            return bank
    return list(dist.keys())[-1]


class DataService:
    """Business logic for data management operations."""

    def __init__(
        self,
        task_repo: TaskRepository,
        fieldman_repo: FieldmanRepository,
        vrp_repo: VRPJobRepository,
        cache: Optional[CacheService] = None,
    ) -> None:
        self._tasks = task_repo
        self._fieldmen = fieldman_repo
        self._vrp = vrp_repo
        self._cache = cache

    async def _resolve_land_point(
        self,
        area_cfg: Dict[str, Any],
        scatterness: int,
        area_radius_km: float,
        *,
        osrm: OSRMService,
        max_retries: int = 60,
        max_snap_distance_m: float = 150.0,
    ) -> Tuple[float, float]:
        """Generate a point and validate/snap it to land/road via OSRM nearest.

        Retries candidate generation when OSRM cannot snap reliably.
        This prevents random points from landing in bodies of water.
        """
        best_fallback: Optional[Tuple[float, float]] = None
        best_fallback_distance = float("inf")

        for _ in range(max_retries):
            lat, lon = _generate_point(area_cfg, scatterness, area_radius_km)
            if not _is_stable_land(lat, lon):
                continue
            try:
                nearest = await osrm.nearest((lat, lon), number=1)
            except Exception:
                continue

            waypoints = nearest.get("waypoints") or []
            if not waypoints:
                continue

            waypoint = waypoints[0]
            location = waypoint.get("location") or []
            if len(location) != 2:
                continue

            road_name = str(waypoint.get("name") or "").strip().lower()
            if not road_name:
                continue
            water_keywords = (
                "sea",
                "bay",
                "lake",
                "river",
                "ocean",
                "strait",
                "channel",
                "creek",
                "harbor",
                "harbour",
                "bridge",
                "causeway",
                "boardwalk",
                "island trail",
                "expressway",
                "skyway",
                "tollway",
                "highway",
                "forest",
                "national park",
                "nature reserve",
                "watershed",
                "reservoir",
            )
            if any(token in road_name for token in water_keywords):
                continue

            distance_m = float(waypoint.get("distance") or 0.0)
            snapped_lon, snapped_lat = float(location[0]), float(location[1])
            if not _is_stable_land(snapped_lat, snapped_lon):
                continue
            if distance_m <= max_snap_distance_m and (road_name or distance_m <= 75.0):
                return snapped_lat, snapped_lon

            if distance_m < best_fallback_distance:
                best_fallback_distance = distance_m
                best_fallback = (snapped_lat, snapped_lon)

        if best_fallback is not None and best_fallback_distance <= 500.0:
            logger.warning(
                "Randomize land-point fallback used (distance=%.1fm)",
                best_fallback_distance,
            )
            return best_fallback

        # Final safety fallback: snap the area center to nearest non-water road.
        center_lat, center_lon = area_cfg.get("center", (None, None))
        if center_lat is not None and center_lon is not None:
            try:
                nearest_center = await osrm.nearest((float(center_lat), float(center_lon)), number=1)
                waypoints_center = nearest_center.get("waypoints") or []
                if waypoints_center:
                    waypoint_center = waypoints_center[0]
                    location_center = waypoint_center.get("location") or []
                    road_name_center = str(waypoint_center.get("name") or "").strip().lower()
                    if len(location_center) == 2 and not any(token in road_name_center for token in water_keywords):
                        snapped_lon, snapped_lat = float(location_center[0]), float(location_center[1])
                        if not _is_stable_land(snapped_lat, snapped_lon):
                            raise RuntimeError("Center snap is not on land")
                        logger.warning("Randomize used area-center land fallback point")
                        return snapped_lat, snapped_lon
            except Exception:
                pass

        center_lat, center_lon = area_cfg.get("center", (None, None))
        if center_lat is not None and center_lon is not None:
            logger.warning("Randomize used deterministic center fallback for land point")
            return float(center_lat), float(center_lon)

        fallback_lat = float(area_cfg.get("lat_min", 0.0) + area_cfg.get("lat_max", 0.0)) / 2.0
        fallback_lon = float(area_cfg.get("lon_min", 0.0) + area_cfg.get("lon_max", 0.0)) / 2.0
        logger.warning("Randomize used deterministic bbox-center fallback for land point")
        return fallback_lat, fallback_lon

    async def _spread_from_road(
        self,
        *,
        lat: float,
        lon: float,
        area_cfg: Dict[str, Any],
        osrm: OSRMService,
        max_offset_m: float = 120.0,
        max_road_distance_m: float = 120.0,
        tries: int = 8,
    ) -> Tuple[float, float]:
        """Move a snapped-road point to nearby land so points are not all exactly on roads."""
        water_keywords = (
            "sea",
            "bay",
            "lake",
            "river",
            "ocean",
            "strait",
            "channel",
            "creek",
            "harbor",
            "harbour",
            "bridge",
            "causeway",
            "boardwalk",
            "island trail",
            "expressway",
            "skyway",
            "tollway",
            "highway",
            "forest",
            "national park",
            "nature reserve",
            "watershed",
            "reservoir",
        )
        for _ in range(tries):
            meters = random.uniform(40.0, max_offset_m)
            bearing = random.uniform(0.0, 360.0)
            cand_lat, cand_lon = _offset_point_meters(lat, lon, meters, bearing)
            if not _is_stable_land(cand_lat, cand_lon):
                continue

            # Keep candidate inside configured generation bounds
            if not (area_cfg["lat_min"] <= cand_lat <= area_cfg["lat_max"]):
                continue
            if not (area_cfg["lon_min"] <= cand_lon <= area_cfg["lon_max"]):
                continue

            try:
                nearest = await osrm.nearest((cand_lat, cand_lon), number=1)
            except Exception:
                continue

            waypoints = nearest.get("waypoints") or []
            if not waypoints:
                continue

            waypoint = waypoints[0]
            distance_m = float(waypoint.get("distance") or 0.0)
            road_name = str(waypoint.get("name") or "").strip().lower()
            if any(token in road_name for token in water_keywords):
                continue
            if distance_m <= max_road_distance_m:
                return cand_lat, cand_lon

        return lat, lon

    async def _generate_spaced_land_point(
        self,
        *,
        area_cfg: Dict[str, Any],
        scatterness: int,
        area_radius_km: float,
        osrm: OSRMService,
        existing_points: List[Tuple[float, float]],
        min_spacing_m: float,
        attempts: int = 5,
    ) -> Tuple[float, float]:
        """Generate land-safe point while maximizing spacing from existing points."""

        def nearest_distance_m(lat: float, lon: float) -> float:
            if not existing_points:
                return float("inf")
            return min(
                _distance_meters(lat, lon, prev_lat, prev_lon)
                for prev_lat, prev_lon in existing_points
            )

        best_candidate: Optional[Tuple[float, float]] = None
        best_distance = -1.0

        for _ in range(max(1, attempts)):
            lat, lon = await self._resolve_land_point(
                area_cfg,
                scatterness,
                area_radius_km,
                osrm=osrm,
            )
            lat, lon = await self._spread_from_road(
                lat=lat,
                lon=lon,
                area_cfg=area_cfg,
                osrm=osrm,
            )

            distance = nearest_distance_m(lat, lon)
            if distance >= min_spacing_m:
                return lat, lon

            if distance > best_distance:
                best_distance = distance
                best_candidate = (lat, lon)

        if best_candidate is not None:
            return best_candidate

        return await self._resolve_land_point(
            area_cfg,
            scatterness,
            area_radius_km,
            osrm=osrm,
        )

    # ── Randomize ─────────────────────────────────────────────────────

    async def randomize(
        self, payload: RandomizeRequest, *, celery_app: Any = None,
    ) -> RandomizeResponse:
        """Generate random tasks and fieldmen."""
        await self._tasks.ensure_columns()
        # Use shorter timeout for nearest calls during generation (they're fast)
        osrm = OSRMService(
            settings.osrm_url,
            timeout=10.0,  # 10s instead of 120s — nearest calls are sub-second
            max_locations=settings.osrm_max_locations,
            cache=self._cache,
        )
        # Bump connection pool for high-concurrency generation
        import httpx as _httpx
        osrm._client = _httpx.AsyncClient(
            base_url=osrm.base_url,
            timeout=osrm.timeout,
            limits=_httpx.Limits(max_connections=300, max_keepalive_connections=150),
        )

        # Resolve area config
        if payload.center_lat is not None and payload.center_lng is not None:
            radius_deg = payload.area_radius_km / 111.0
            area_cfg = {
                "lat_min": payload.center_lat - radius_deg,
                "lat_max": payload.center_lat + radius_deg,
                "lon_min": payload.center_lng - radius_deg,
                "lon_max": payload.center_lng + radius_deg,
                "center": (payload.center_lat, payload.center_lng),
            }
            area_label = f"CUSTOM({payload.center_lat:.4f},{payload.center_lng:.4f})"
        else:
            area_cfg = AREA_CONFIGS.get(payload.task_area)
            if not area_cfg:
                from core.exceptions import ValidationError
                raise ValidationError(f"Unknown area: {payload.task_area}")
            area_label = payload.task_area

        bank_dist = _parse_bank_distribution(payload.task_banks)
        service_seconds = payload.service_time_minutes * 60
        num_jobs = payload.num_jobs

        total_task_rows: List[Tuple] = []
        total_fm_rows: List[Tuple] = []
        all_points: List[Tuple[float, float]] = []
        total_type_counts: Dict[str, int] = {
            "credit_investigation": 0,
            "skips_collect": 0,
            "demand_letter": 0,
        }
        total_area_assignments: List[Tuple] = []

        # Track task/fieldman IDs per job batch for multi-job support
        batch_task_ids: Dict[int, List[str]] = {}
        batch_fm_ids: Dict[int, List[str]] = {}
        total_points_requested = num_jobs * (
            payload.tasks_ci + payload.tasks_sc + payload.tasks_dl + payload.num_fieldmen
        )
        min_spacing_m = _estimate_min_spacing_m(
            payload.area_radius_km,
            total_points_requested,
            payload.scatterness,
        )

        # ── TWO-PHASE POINT GENERATION ──────────────────────────────────
        # Phase 1 (CPU, instant): Generate candidate points using
        #   _is_stable_land — catches ocean, coast, thin strips.
        # Phase 2 (OSRM, parallel): Batch-validate all candidates via
        #   OSRM nearest.  Rejects forests, mountains, inland water,
        #   expressways by checking snap distance + road name.
        #
        # This is 5-10x faster than per-point OSRM retries because:
        #   • ~80%+ of _is_stable_land candidates pass OSRM on first try
        #   • Only failing points need replacement (OSRM cost is minimal)
        #   • All OSRM calls run in parallel (semaphore=200)
        import asyncio as _aio
        import time as _t

        _sem = _aio.Semaphore(300)

        # Road-name keywords that indicate water, expressways, or
        # non-residential zones.  Shared across OSRM-based generation.
        _EXCLUDE_ROAD_KEYWORDS = (
            "sea", "bay", "lake", "river", "ocean", "strait",
            "channel", "creek", "harbor", "harbour", "bridge",
            "causeway", "boardwalk", "island trail",
            "expressway", "skyway", "tollway", "highway",
            "forest", "national park", "nature reserve",
            "watershed", "reservoir",
        )

        def _gen_candidates_cpu(
            a_cfg: Dict[str, Any], count: int, *, extra_factor: float = 2.0,
        ) -> List[Tuple[float, float]]:
            """Phase 1: Generate land-safe candidates (CPU-only, instant)."""
            needed = int(count * extra_factor)  # over-generate to cover OSRM rejects
            candidates: List[Tuple[float, float]] = []
            for _ in range(needed * 3):  # generous retries, CPU is cheap
                lat, lon = _generate_point(a_cfg, payload.scatterness, payload.area_radius_km)
                if _is_stable_land(lat, lon):
                    candidates.append((lat, lon))
                    if len(candidates) >= needed:
                        break
            return candidates

        async def _validate_osrm(lat: float, lon: float) -> Optional[Tuple[float, float]]:
            """Phase 2: OSRM-validate a single candidate. Returns snapped point or None."""
            async with _sem:
                try:
                    nearest = await osrm.nearest((lat, lon), number=1)
                except Exception:
                    return None
                waypoints = nearest.get("waypoints") or []
                if not waypoints:
                    return None
                wp = waypoints[0]
                loc = wp.get("location") or []
                if len(loc) != 2:
                    return None
                road_name = str(wp.get("name") or "").strip().lower()
                if not road_name:
                    return None
                if any(kw in road_name for kw in _EXCLUDE_ROAD_KEYWORDS):
                    return None
                snap_dist = float(wp.get("distance") or 0)
                if snap_dist > 200:
                    return None
                snapped_lat, snapped_lon = float(loc[1]), float(loc[0])
                # Trust OSRM + road-name validation — skip expensive _is_stable_land
                # on snapped result (named road within 200m is on land).
                return (snapped_lat, snapped_lon)

        async def _gen_validated_points(
            a_cfg: Dict[str, Any], count: int,
        ) -> List[Tuple[float, float]]:
            """Generate exactly `count` OSRM-validated points (fast 2-phase)."""
            if count == 0:
                return []

            result: List[Tuple[float, float]] = []
            remaining = count
            max_rounds = 4  # safety cap on retry rounds

            for round_num in range(max_rounds):
                # Phase 1: generate CPU candidates
                candidates = _gen_candidates_cpu(a_cfg, remaining)
                if not candidates:
                    break

                # Phase 2: batch OSRM validate in parallel
                validated = await _aio.gather(
                    *[_validate_osrm(lat, lon) for lat, lon in candidates]
                )
                for v in validated:
                    if v is not None and len(result) < count:
                        result.append(v)

                remaining = count - len(result)
                if remaining <= 0:
                    break

                logger.debug(
                    "Point gen round %d: got %d/%d, need %d more",
                    round_num + 1, len(result), count, remaining,
                )

            # Fill any shortfall with area center (rare, only in extreme cases)
            center = a_cfg.get("center", (0.0, 0.0))
            while len(result) < count:
                result.append(center)

            return result[:count]

        _t0 = _t.monotonic()
        logger.info("Generating %d total points (2-phase: CPU + OSRM)...", total_points_requested)

        for job_idx in range(num_jobs):
            job_label = f"J{job_idx + 1}" if num_jobs > 1 else ""
            batch_task_ids[job_idx] = []
            batch_fm_ids[job_idx] = []

            # Build task list for this job
            task_specs: List[Tuple[str, str, int]] = []
            for task_type, count in [
                ("credit_investigation", payload.tasks_ci),
                ("skips_collect", payload.tasks_sc),
                ("demand_letter", payload.tasks_dl),
            ]:
                prefix = TASK_TYPES[task_type]
                for i in range(count):
                    task_specs.append((task_type, prefix, i))

            # Generate all task points (2-phase: CPU candidates → OSRM batch validation)
            task_points = await _gen_validated_points(area_cfg, len(task_specs))

            for (task_type, prefix, i), (lat, lon) in zip(task_specs, task_points):
                task_id = str(uuid.uuid4())
                bank = _pick_bank(bank_dist)
                label_parts = [f"{prefix}-{i + 1}"]
                if job_label:
                    label_parts.append(job_label)
                label_parts.append(f"({area_label})")
                address = " ".join(label_parts)
                priority = round(random.uniform(1.0, 10.0), 1)
                total_task_rows.append(
                    (task_id, address, lat, lon, priority,
                     service_seconds, task_type, bank)
                )
                all_points.append((lat, lon))
                total_type_counts[task_type] += 1
                batch_task_ids[job_idx].append(task_id)

            # Generate all fieldman points
            if payload.num_fieldmen > 0 and payload.fieldman_areas:
                fm_cfgs = []
                fm_names = []
                for j in range(payload.num_fieldmen):
                    fm_area_name = random.choice(payload.fieldman_areas)
                    fm_area_cfg = AREA_CONFIGS.get(fm_area_name, area_cfg)
                    if (payload.center_lat is not None
                            and payload.center_lng is not None):
                        fm_area_cfg = area_cfg
                        fm_area_name = area_label
                    fm_cfgs.append(fm_area_cfg)
                    fm_names.append(fm_area_name)

                # Generate fieldman points per-area (2-phase: CPU candidates → OSRM batch validation)
                # Group by area config to handle mixed-area fieldmen correctly
                from collections import defaultdict as _defaultdict
                _area_groups: Dict[int, List[int]] = _defaultdict(list)
                for idx, cfg in enumerate(fm_cfgs):
                    _area_groups[id(cfg)].append(idx)

                fm_points: List[Optional[Tuple[float, float]]] = [None] * len(fm_cfgs)
                for _cfg_id, indices in _area_groups.items():
                    cfg_for_group = fm_cfgs[indices[0]]
                    pts = await _gen_validated_points(cfg_for_group, len(indices))
                    for slot, pt in zip(indices, pts):
                        fm_points[slot] = pt

                for j, ((lat, lon), fm_area_name) in enumerate(zip(fm_points, fm_names)):
                    user_id = str(uuid.uuid4())
                    label = f"FM-{j + 1}"
                    if job_label:
                        label += f" {job_label}"
                    address = f"{label} ({fm_area_name})"
                    total_fm_rows.append((user_id, address, lat, lon))
                    all_points.append((lat, lon))
                    batch_fm_ids[job_idx].append(user_id)

                    for a_name in payload.fieldman_areas:
                        area_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, a_name))
                        total_area_assignments.append((user_id, area_uuid))

        logger.info("Generated %d points in %.1fs (2-phase validated)", total_points_requested, _t.monotonic() - _t0)

        # Close the generation-specific httpx client
        if osrm._client and not osrm._client.is_closed:
            await osrm._client.aclose()

        # Bulk insert
        await self._tasks.bulk_insert(total_task_rows)
        await self._fieldmen.bulk_insert(total_fm_rows)
        await self._fieldmen.bulk_assign_areas(total_area_assignments)

        # Auto-create VRP jobs when num_jobs > 1 and celery is available
        if num_jobs > 1 and celery_app:
            for job_idx in range(num_jobs):
                job_payload = {
                    "task_ids": batch_task_ids[job_idx],
                    "fieldman_ids": batch_fm_ids[job_idx],
                    "h3_resolution": 7,
                    "h3_auto_resolution": True,
                    "assignment_strategy": "h3",
                    "batch_label": f"J{job_idx + 1}",
                }
                job_id = await self._vrp.create_job("queued", json.dumps(job_payload))
                celery_app.send_task("process_vrp_job", args=[job_id])

        # Build overview for response (skip for large batches — frontend fetches separately)
        overview = None
        if total_points_requested <= 500:
            overview = await self._fetch_overview()

        # Invalidate caches
        if self._cache:
            await self._cache.invalidate_overview()
            await self._cache.invalidate_all_jobs()

        return RandomizeResponse(
            tasks_created=len(total_task_rows),
            fieldmen_created=len(total_fm_rows),
            task_types=total_type_counts,
            jobs_generated=num_jobs,
            overview=overview,
        )

    # ── Reset ─────────────────────────────────────────────────────────

    async def reset_all_data(self) -> Dict[str, str]:
        """Truncate all data tables."""
        await self._vrp.truncate_all()
        if self._cache:
            await self._cache.invalidate_all()
        return {"status": "ok", "message": "All data cleared"}

    # ── Optimize ──────────────────────────────────────────────────────

    async def optimize(
        self,
        payload: OptimizeSettingsRequest,
        *,
        celery_app: Any,
    ) -> CreateJobResponse:
        """Create an optimization job with extended area settings."""
        payload_data = payload.model_dump()

        if payload.areas:
            area_names = [a.strip() for a in payload.areas.split(",") if a.strip()]
            area_id_map = {
                n: str(uuid.uuid5(uuid.NAMESPACE_DNS, n)) for n in area_names
            }
            payload_data["area_ids"] = list(area_id_map.values())

            await self._tasks.ensure_columns()
            rows = await self._tasks.fetch_all_coords()
            task_area_map: Dict[str, str] = {}
            for row in rows:
                lat, lon = float(row["latitude"]), float(row["longitude"])
                for a_name, cfg in AREA_CONFIGS.items():
                    if a_name not in area_names:
                        continue
                    c = cfg["center"]
                    r_km = cfg.get("radius_km", 20)
                    dlat = abs(lat - c[0]) * 111.0
                    dlon = abs(lon - c[1]) * 111.0 * 0.9
                    if (dlat ** 2 + dlon ** 2) ** 0.5 <= r_km:
                        task_area_map[str(row["id"])] = area_id_map[a_name]
                        break
                else:
                    if area_names:
                        task_area_map[str(row["id"])] = area_id_map[area_names[0]]

            payload_data["task_area_map"] = task_area_map
            if not task_area_map:
                del payload_data["area_ids"]
                del payload_data["task_area_map"]

        job_id = await self._vrp.create_job("queued", json.dumps(payload_data))
        celery_app.send_task("process_vrp_job", args=[job_id])
        return CreateJobResponse(job_id=job_id)

    # ── Picker: create task ───────────────────────────────────────────

    async def picker_create_task(
        self,
        payload: PickerTaskRequest,
    ) -> PickerItemResponse:
        """Create a single task from a map-picked location."""
        await self._tasks.ensure_columns()

        task_id = str(uuid.uuid4())
        service_seconds = payload.service_time_minutes * 60
        # Use manual_priority as the effective priority if set, else random default
        priority = payload.manual_priority if payload.manual_priority is not None else round(random.uniform(1.0, 10.0), 1)
        prefix = TASK_TYPES.get(payload.task_type, "T")
        placeholder = (
            f"{prefix}-picked ({payload.latitude:.4f}, {payload.longitude:.4f})"
        )

        await self._tasks.create_task(
            task_id=task_id,
            address=placeholder,
            latitude=payload.latitude,
            longitude=payload.longitude,
            priority=priority,
            service=service_seconds,
            task_type=payload.task_type,
            bank=payload.bank,
            manual_priority=payload.manual_priority,
        )

        # Try geocode immediately
        address = placeholder
        try:
            import httpx
            from services.geocode_service import reverse_geocode

            async with httpx.AsyncClient(timeout=5.0) as client:
                resolved = await reverse_geocode(
                    payload.latitude, payload.longitude,
                    client=client, cache=self._cache,
                )
                if resolved and not resolved[0].isdigit():
                    address = resolved
                    await self._tasks.update_address(task_id, address)
        except Exception:
            pass

        if self._cache:
            await self._cache.invalidate_overview()

        return PickerItemResponse(
            id=task_id,
            latitude=payload.latitude,
            longitude=payload.longitude,
            address=address,
            type="task",
            task_type=payload.task_type,
            bank=payload.bank,
            manual_priority=payload.manual_priority,
        )

    # ── Picker: create fieldman ───────────────────────────────────────

    async def picker_create_fieldman(
        self,
        payload: PickerFieldmanRequest,
    ) -> PickerItemResponse:
        """Create a single fieldman from a map-picked location."""
        user_id = str(uuid.uuid4())
        placeholder = (
            f"FM-picked ({payload.latitude:.4f}, {payload.longitude:.4f})"
        )

        await self._fieldmen.create_fieldman(
            user_id=user_id,
            address=placeholder,
            home_lat=payload.latitude,
            home_long=payload.longitude,
        )

        area_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, payload.area))
        await self._fieldmen.assign_area(user_id, area_uuid)

        # Try geocode immediately
        address = placeholder
        try:
            import httpx
            from services.geocode_service import reverse_geocode

            async with httpx.AsyncClient(timeout=5.0) as client:
                resolved = await reverse_geocode(
                    payload.latitude, payload.longitude,
                    client=client, cache=self._cache,
                )
                if resolved and not resolved[0].isdigit():
                    address = resolved
                    await self._fieldmen.update_address(user_id, address)
        except Exception:
            pass

        if self._cache:
            await self._cache.invalidate_overview()

        return PickerItemResponse(
            id=user_id,
            latitude=payload.latitude,
            longitude=payload.longitude,
            address=address,
            type="fieldman",
        )

    # ── Picker: delete ────────────────────────────────────────────────

    async def picker_delete_task(self, task_id: str) -> Dict[str, str]:
        """Delete a single task."""
        deleted = await self._tasks.delete_task(task_id)
        if not deleted:
            from core.exceptions import NotFoundError
            raise NotFoundError("Task not found")
        if self._cache:
            await self._cache.invalidate_overview()
        return {"status": "ok", "deleted": task_id}

    async def picker_delete_fieldman(self, fieldman_id: str) -> Dict[str, str]:
        """Delete a single fieldman."""
        deleted = await self._fieldmen.delete_fieldman(fieldman_id)
        if not deleted:
            from core.exceptions import NotFoundError
            raise NotFoundError("Fieldman not found")
        if self._cache:
            await self._cache.invalidate_overview()
        return {"status": "ok", "deleted": fieldman_id}

    # ── Task Priority Update ──────────────────────────────────────────

    async def update_task_priority(
        self, task_id: str, manual_priority: Optional[float],
    ) -> Dict[str, Any]:
        """Update the manual priority for a task. Set to None to clear."""
        updated = await self._tasks.update_manual_priority(task_id, manual_priority)
        if not updated:
            from core.exceptions import NotFoundError
            raise NotFoundError("Task not found")
        if self._cache:
            await self._cache.invalidate_overview()
        return {
            "status": "ok",
            "task_id": task_id,
            "manual_priority": manual_priority,
        }

    # ── FM Location Update ────────────────────────────────────────────

    async def update_fieldman_location(
        self, fieldman_id: str, latitude: float, longitude: float,
    ) -> PickerItemResponse:
        """Update a fieldman's home location and re-geocode."""
        updated = await self._fieldmen.update_location(
            fieldman_id, latitude, longitude,
        )
        if not updated:
            from core.exceptions import NotFoundError
            raise NotFoundError("Fieldman not found")

        # Update address placeholder
        address = f"FM-moved ({latitude:.4f}, {longitude:.4f})"
        await self._fieldmen.update_address(fieldman_id, address)

        # Try geocode immediately
        try:
            import httpx
            from services.geocode_service import reverse_geocode

            async with httpx.AsyncClient(timeout=5.0) as client:
                resolved = await reverse_geocode(
                    latitude, longitude,
                    client=client, cache=self._cache,
                )
                if resolved and not resolved[0].isdigit():
                    address = resolved
                    await self._fieldmen.update_address(fieldman_id, address)
        except Exception:
            pass

        if self._cache:
            await self._cache.invalidate_overview()

        return PickerItemResponse(
            id=fieldman_id,
            latitude=latitude,
            longitude=longitude,
            address=address,
            type="fieldman",
        )

    # ── Geocode ───────────────────────────────────────────────────────

    async def geocode_addresses_async(self, celery_app=None) -> GeocodeResponse:
        """
        Dispatch bulk geocoding to Celery worker (non-blocking).
        Returns immediately with the total count. Actual geocoding happens in background.
        """
        task_rows = await self._tasks.fetch_all_coords()
        fm_rows = await self._fieldmen.list_overview()

        items = []
        for row in task_rows:
            items.append({
                "type": "task",
                "id": str(row["id"]),
                "lat": float(row["latitude"]),
                "lon": float(row["longitude"]),
            })
        for fm in fm_rows:
            items.append({
                "type": "fieldman",
                "id": fm.fieldman_id,
                "lat": fm.latitude,
                "lon": fm.longitude,
            })

        total = len(items)
        if total > 0 and celery_app:
            celery_app.send_task("background_bulk_geocode", args=[items])
            logger.info("Dispatched bulk geocode to Celery: %d items", total)

        return GeocodeResponse(updated=0, total=total)

    async def geocode_addresses(self) -> GeocodeResponse:
        """Batch-update task and fieldman addresses via Nominatim (inline, legacy)."""
        import asyncio
        import httpx
        from services.geocode_service import reverse_geocode

        updated = 0
        task_rows = await self._tasks.fetch_all_coords()
        logger.info("Geocoding %d tasks via Nominatim...", len(task_rows))

        async with httpx.AsyncClient(timeout=10.0) as client:
            for i, row in enumerate(task_rows):
                lat, lon = float(row["latitude"]), float(row["longitude"])
                address = await reverse_geocode(
                    lat, lon, client=client, cache=self._cache,
                )
                if address and not address[0].isdigit():
                    await self._tasks.update_address(str(row["id"]), address)
                    updated += 1
                if i < len(task_rows) - 1:
                    await asyncio.sleep(1.1)
                if (i + 1) % 20 == 0:
                    logger.info(
                        "Tasks geocoded: %d/%d (updated: %d)",
                        i + 1, len(task_rows), updated,
                    )

        fm_rows = await self._fieldmen.list_overview()
        logger.info("Geocoding %d fieldmen via Nominatim...", len(fm_rows))

        async with httpx.AsyncClient(timeout=10.0) as client:
            for i, fm in enumerate(fm_rows):
                address = await reverse_geocode(
                    fm.latitude, fm.longitude,
                    client=client, cache=self._cache,
                )
                if address and not address[0].isdigit():
                    await self._fieldmen.update_address(fm.fieldman_id, address)
                    updated += 1
                if i < len(fm_rows) - 1:
                    await asyncio.sleep(1.1)

        total = len(task_rows) + len(fm_rows)
        logger.info("Geocoding complete: %d/%d updated", updated, total)
        return GeocodeResponse(updated=updated, total=total)

    # ── Areas ─────────────────────────────────────────────────────────

    def get_areas(self) -> List[AreaConfigItem]:
        """Return all configured area definitions."""
        result = []
        for name, cfg in AREA_CONFIGS.items():
            result.append(
                AreaConfigItem(
                    name=name,
                    lat_min=cfg["lat_min"],
                    lat_max=cfg["lat_max"],
                    lon_min=cfg["lon_min"],
                    lon_max=cfg["lon_max"],
                    center_lat=cfg["center"][0],
                    center_lng=cfg["center"][1],
                )
            )
        return result

    # ── Internal helpers ──────────────────────────────────────────────

    async def _fetch_overview(self) -> OverviewResponse:
        """Build overview from repos."""
        task_items = await self._tasks.list_overview()
        fm_items = await self._fieldmen.list_overview()
        return OverviewResponse(
            tasks=[
                TaskOverviewItem(
                    task_id=t.task_id,
                    address=t.address,
                    latitude=t.latitude,
                    longitude=t.longitude,
                    task_type=t.task_type,
                    bank=t.bank,
                    priority=t.priority,
                    manual_priority=t.manual_priority,
                )
                for t in task_items
            ],
            fieldmen=[
                FieldmanOverviewItem(
                    fieldman_id=f.fieldman_id,
                    address=f.address,
                    latitude=f.latitude,
                    longitude=f.longitude,
                )
                for f in fm_items
            ],
        )
