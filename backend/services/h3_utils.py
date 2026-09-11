"""
H3 Geospatial Utilities for VRP Optimization.

Uses Uber H3 hexagonal hierarchical spatial index to:
  - Adaptively choose resolution based on geographic spread
  - Hierarchically cluster tasks (macro-region → micro-cluster)
  - Load-balance fieldman–task assignment via H3 grid distance
  - Partition large VRP instances into regional sub-problems
  - Spatially sort tasks within clusters for better initial solutions
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import h3
except Exception:  # pragma: no cover
    h3 = None

import logging

logger = logging.getLogger(__name__)

# ── Resolution table: avg hex edge length in km ──────────────────────
# From https://h3geo.org/docs/core-library/restable
_RES_EDGE_KM = {
    0: 1281.256, 1: 483.057, 2: 182.513, 3: 68.979, 4: 26.072,
    5: 9.854,   6: 3.725,  7: 1.406,  8: 0.531,  9: 0.201,
    10: 0.076, 11: 0.029, 12: 0.011, 13: 0.004, 14: 0.002, 15: 0.001,
}


# ═══════════════════════════════════════════════════════════════════════
# Core H3 wrappers (v3/v4 compatible)
# ═══════════════════════════════════════════════════════════════════════

def latlng_to_h3(lat: float, lng: float, resolution: int) -> str:
    """Convert lat/lng to H3 cell index."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "geo_to_h3"):
        return h3.geo_to_h3(lat, lng, resolution)
    if hasattr(h3, "latlng_to_cell"):
        return h3.latlng_to_cell(lat, lng, resolution)
    raise RuntimeError("h3 library does not expose a supported lat/lng API")


def h3_to_parent(cell: str, parent_res: int) -> str:
    """Get parent cell at a coarser resolution."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "h3_to_parent"):
        return h3.h3_to_parent(cell, parent_res)
    if hasattr(h3, "cell_to_parent"):
        return h3.cell_to_parent(cell, parent_res)
    raise RuntimeError("h3 library does not expose parent API")


def h3_get_resolution(cell: str) -> int:
    """Get resolution of an H3 cell."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "h3_get_resolution"):
        return h3.h3_get_resolution(cell)
    if hasattr(h3, "get_resolution"):
        return h3.get_resolution(cell)
    raise RuntimeError("h3 library does not expose resolution API")


def h3_grid_distance(a: str, b: str) -> int:
    """Grid distance between two H3 cells (same resolution)."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    try:
        if hasattr(h3, "h3_distance"):
            return h3.h3_distance(a, b)
        if hasattr(h3, "grid_distance"):
            return h3.grid_distance(a, b)
    except Exception:
        # Cells may be on different faces of the icosahedron — fall back to
        # large distance so that the caller treats them as far apart.
        return 9999
    return 9999


def expand_h3_k_ring(h3_index: str, k: int) -> List[str]:
    """Return all cells within k grid-steps of origin."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if k <= 0:
        return [h3_index]
    if hasattr(h3, "k_ring"):
        return list(h3.k_ring(h3_index, k))
    if hasattr(h3, "grid_disk"):
        return list(h3.grid_disk(h3_index, k))
    raise RuntimeError("h3 library does not expose k-ring helpers")


def h3_compact_cells(cells: List[str]) -> List[str]:
    """Compact a set of H3 cells into a mixed-resolution set."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if not cells:
        return []
    try:
        if hasattr(h3, "compact"):
            return list(h3.compact(set(cells)))
        if hasattr(h3, "compact_cells"):
            return list(h3.compact_cells(set(cells)))
    except Exception:
        return cells
    return cells


def h3_cell_to_latlng(cell: str) -> Tuple[float, float]:
    """Return (lat, lng) center of an H3 cell."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "h3_to_geo"):
        return h3.h3_to_geo(cell)
    if hasattr(h3, "cell_to_latlng"):
        return h3.cell_to_latlng(cell)
    raise RuntimeError("h3 library does not expose cell-to-latlng API")


# ═══════════════════════════════════════════════════════════════════════
# Grouping
# ═══════════════════════════════════════════════════════════════════════

def group_tasks_by_h3(
    tasks: Sequence[Dict[str, Any]],
    resolution: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group tasks into H3 cells at the given resolution."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for task in tasks:
        cell = latlng_to_h3(float(task["latitude"]), float(task["longitude"]), resolution)
        grouped.setdefault(cell, []).append(task)
    return grouped


# ═══════════════════════════════════════════════════════════════════════
# Nearest fieldman search (improved)
# ═══════════════════════════════════════════════════════════════════════

def find_nearest_fm_h3(
    task_h3: str,
    fm_h3_map: Dict[str, List[str]],
    *,
    max_k: int = 3,
) -> Optional[str]:
    """Return the H3 cell containing the nearest fieldman (k-ring search)."""
    if task_h3 in fm_h3_map:
        return task_h3

    for k in range(1, max_k + 1):
        ring = expand_h3_k_ring(task_h3, k)
        candidates = [cell for cell in ring if cell in fm_h3_map]
        if candidates:
            # Pick the cell with smallest grid distance to the task cell
            return min(candidates, key=lambda c: h3_grid_distance(task_h3, c))
    return None


# ═══════════════════════════════════════════════════════════════════════
# Adaptive resolution
# ═══════════════════════════════════════════════════════════════════════

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def suggest_h3_resolution(coords: List[Tuple[float, float]]) -> int:
    """
    Suggest an H3 resolution based on bounding-box diagonal distance
    and point density of the input coordinates.

    Thresholds (bbox diagonal km → resolution):
      < 5 km   → 9  (tight urban cluster)
      5–20 km  → 8  (city-wide)
      20–80 km → 7  (metro / regional)
      80+ km   → 6  (wide rural / inter-city)

    Returns:
        int: Suggested H3 resolution (6–9).
    """
    if not coords:
        return 7  # sensible default for empty input

    if len(coords) == 1:
        return 9  # single point → finest practical resolution

    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    diagonal_km = _haversine_km(min_lat, min_lng, max_lat, max_lng)

    if diagonal_km < 5:
        resolution = 9
    elif diagonal_km < 20:
        resolution = 8
    elif diagonal_km < 80:
        resolution = 7
    else:
        resolution = 6

    # Density refinement: for very dense clusters in a wide area, bump up
    # the resolution by 1 to avoid overcrowded cells.
    if len(coords) > 1 and diagonal_km > 0:
        # Approximate area from bbox (km²)
        width_km = _haversine_km(min_lat, min_lng, min_lat, max_lng)
        height_km = _haversine_km(min_lat, min_lng, max_lat, min_lng)
        area_km2 = max(width_km * height_km, 0.01)
        density = len(coords) / area_km2  # points per km²

        # If density is very high (>50 pts/km²) and resolution isn't already
        # at the fine end, bump up by 1.
        if density > 50 and resolution < 9:
            resolution += 1

    logger.info(
        "suggest_h3_resolution: %d coords, diagonal=%.2f km → resolution %d",
        len(coords), diagonal_km, resolution,
    )
    return resolution


def choose_adaptive_resolution(
    tasks: Sequence[Dict[str, Any]],
    *,
    min_res: int = 4,
    max_res: int = 10,
    target_tasks_per_cell: int = 8,
) -> int:
    """
    Choose the best H3 resolution based on the geographic spread and density
    of the task set.

    Strategy:
      1. Compute the bounding-box diagonal of all tasks (geographic spread).
      2. Pick the resolution whose hex edge length best matches the desired
         cluster radius so that, on average, each cell contains roughly
         ``target_tasks_per_cell`` tasks.
    """
    if not tasks:
        return 7  # safe default

    lats = [float(t["latitude"]) for t in tasks]
    lngs = [float(t["longitude"]) for t in tasks]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    spread_km = _haversine_km(min_lat, min_lng, max_lat, max_lng)
    if spread_km < 0.01:
        return max_res  # all tasks essentially at same point

    # Estimate how many cells we want:  n_tasks / target_tasks_per_cell
    desired_cells = max(1, len(tasks) / target_tasks_per_cell)
    # Each cell covers roughly (2*edge)^2 area in km²;
    # desired_cell_diameter ≈ spread / sqrt(desired_cells)
    desired_diameter_km = spread_km / math.sqrt(desired_cells)
    desired_edge_km = desired_diameter_km / 2.0

    # Find the resolution with the closest edge length
    best_res = min_res
    best_diff = float("inf")
    for res in range(min_res, max_res + 1):
        diff = abs(_RES_EDGE_KM[res] - desired_edge_km)
        if diff < best_diff:
            best_diff = diff
            best_res = res

    logger.info(
        "Adaptive H3 resolution: spread=%.1f km, %d tasks → resolution %d (edge %.3f km)",
        spread_km, len(tasks), best_res, _RES_EDGE_KM[best_res],
    )
    return best_res


# ═══════════════════════════════════════════════════════════════════════
# Hierarchical two-level clustering
# ═══════════════════════════════════════════════════════════════════════

def build_h3_clusters(
    tasks: Sequence[Dict[str, Any]],
    *,
    fine_resolution: int,
    coarse_resolution: Optional[int] = None,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Two-level hierarchical clustering:
      Level 1 (coarse): groups tasks into macro-regions
      Level 2 (fine):   groups tasks within each macro-region

    Returns:
      { coarse_cell: { fine_cell: [tasks…] } }
    """
    if coarse_resolution is None:
        coarse_resolution = max(0, fine_resolution - 3)

    result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for task in tasks:
        lat = float(task["latitude"])
        lng = float(task["longitude"])
        fine_cell = latlng_to_h3(lat, lng, fine_resolution)
        coarse_cell = h3_to_parent(fine_cell, coarse_resolution)
        if coarse_cell not in result:
            result[coarse_cell] = {}
        result[coarse_cell].setdefault(fine_cell, []).append(task)

    logger.info(
        "H3 hierarchical clustering: %d tasks → %d macro-regions, fine_res=%d, coarse_res=%d",
        len(tasks), len(result), fine_resolution, coarse_resolution,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════
# Load-balanced fieldman–task assignment
# ═══════════════════════════════════════════════════════════════════════

def _effective_priority(task: Dict[str, Any]) -> float:
    """Return effective priority: manual_priority > priority > 1."""
    mp = task.get("manual_priority")
    if mp is not None:
        return float(mp)
    return float(task.get("priority", 1))


def assign_tasks_to_fieldmen_h3(
    tasks: Sequence[Dict[str, Any]],
    fieldmen: Sequence[Dict[str, Any]],
    *,
    resolution: int,
    max_k: int = 5,
    load_balance_weight: float = 0.3,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Assign tasks to fieldmen using H3 grid distance + load balancing +
    haversine tiebreaking.

    For each task, score every fieldman:
      score = grid_distance + load_balance_weight * (current_load / avg_load)
             + haversine_km * 0.01  (tiebreaker for same grid distance)

    Lower score wins.  Higher-priority tasks get first pick of the nearest
    fieldman (Priority > Distance).

    Uses pre-built cell→fieldman index for O(1) lookups.

    Returns:
      { fieldman_user_id: [tasks…] }
    """
    if not tasks or not fieldmen:
        return {}

    # Build fieldman lookup: id → record, id → cell, cell → [fm_ids]
    fm_by_id: Dict[str, Dict[str, Any]] = {}
    fm_cells: Dict[str, str] = {}
    cell_to_fms: Dict[str, List[str]] = defaultdict(list)
    for fm in fieldmen:
        fm_id = str(fm["user_id"])
        cell = latlng_to_h3(float(fm["current_lat"]), float(fm["current_long"]), resolution)
        fm_by_id[fm_id] = fm
        fm_cells[fm_id] = cell
        cell_to_fms[cell].append(fm_id)

    # Assignment result and load counter
    assignment: Dict[str, List[Dict[str, Any]]] = {str(fm["user_id"]): [] for fm in fieldmen}
    loads: Dict[str, int] = {str(fm["user_id"]): 0 for fm in fieldmen}
    avg_load = max(1, len(tasks) / len(fieldmen))

    # Sort tasks by effective priority descending so higher-priority tasks
    # get first pick of the nearest fieldman (Priority > Distance).
    sorted_tasks = sorted(tasks, key=_effective_priority, reverse=True)

    for task in sorted_tasks:
        task_lat = float(task["latitude"])
        task_lon = float(task["longitude"])
        task_cell = latlng_to_h3(task_lat, task_lon, resolution)

        best_fm: Optional[str] = None
        best_score = float("inf")

        # ── Fast path: fieldmen in the exact same H3 cell ──
        same_cell_fms = cell_to_fms.get(task_cell, [])
        if same_cell_fms:
            # Among same-cell fieldmen, pick the one with smallest
            # haversine distance + slight load-balance penalty.
            for fid in same_cell_fms:
                fm = fm_by_id[fid]
                dist_km = _haversine_km(
                    task_lat, task_lon,
                    float(fm["current_lat"]), float(fm["current_long"]),
                )
                load_ratio = loads[fid] / avg_load if avg_load > 0 else 0
                score = dist_km + load_balance_weight * load_ratio
                if score < best_score:
                    best_score = score
                    best_fm = fid

            assignment[best_fm].append(task)
            loads[best_fm] += 1
            continue

        # ── Full search: haversine-primary + load-balanced ──────────────
        # Use grid distance only as a pre-filter (skip FMs beyond max_k
        # cells).  Score by actual haversine distance so that the truly
        # nearest fieldman wins, not just the one in the same grid ring.
        for fm_id, fm_cell in fm_cells.items():
            grid_dist = h3_grid_distance(task_cell, fm_cell)

            # Skip fieldmen beyond max_k hex cells (too far away).
            if grid_dist > max_k:
                continue

            fm = fm_by_id[fm_id]
            dist_km = _haversine_km(
                task_lat, task_lon,
                float(fm["current_lat"]), float(fm["current_long"]),
            )

            # Load-balance penalty: penalize overloaded fieldmen
            load_ratio = loads[fm_id] / avg_load if avg_load > 0 else 0
            score = dist_km + load_balance_weight * load_ratio

            if score < best_score:
                best_score = score
                best_fm = fm_id

        if best_fm is None:
            # Fallback: assign to **nearest** fieldman by actual haversine
            # distance.  This prevents isolated tasks from being dumped on a
            # far-away fieldman that just happens to have the fewest tasks.
            best_fm = min(
                fm_by_id.keys(),
                key=lambda fid: _haversine_km(
                    task_lat, task_lon,
                    float(fm_by_id[fid]["current_lat"]),
                    float(fm_by_id[fid]["current_long"]),
                ),
            )

        assignment[best_fm].append(task)
        loads[best_fm] += 1

    # Log assignment distribution
    dist = {fm_id: len(tsk_list) for fm_id, tsk_list in assignment.items() if tsk_list}
    logger.info("H3 load-balanced assignment: %s", dist)
    return assignment


# ═══════════════════════════════════════════════════════════════════════
# Problem partitioning for large instances
# ═══════════════════════════════════════════════════════════════════════

def partition_problem_h3(
    tasks: Sequence[Dict[str, Any]],
    fieldmen: Sequence[Dict[str, Any]],
    *,
    resolution: int,
    max_partition_size: int = 150,
) -> List[Dict[str, Any]]:
    """
    For large problem instances, partition into independent sub-problems
    by H3 region.  Each partition gets its own tasks and nearby fieldmen.

    This reduces the O(n²) OSRM matrix and VROOM solve time.

    Returns list of: { "tasks": [...], "fieldmen": [...], "region": cell }
    """
    if len(tasks) <= max_partition_size:
        return [{"tasks": list(tasks), "fieldmen": list(fieldmen), "region": "single"}]

    # Group tasks at a coarser resolution to form regions
    coarse_res = max(0, resolution - 2)
    region_tasks: Dict[str, List[Dict[str, Any]]] = group_tasks_by_h3(tasks, coarse_res)

    # Merge tiny regions into neighbors
    merged = _merge_small_regions(region_tasks, min_size=5)

    # Build fieldman index
    fm_cells: Dict[str, str] = {}
    for fm in fieldmen:
        cell = latlng_to_h3(float(fm["current_lat"]), float(fm["current_long"]), coarse_res)
        fm_cells[str(fm["user_id"])] = cell

    partitions: List[Dict[str, Any]] = []
    assigned_fm_ids: set = set()

    for region_cell, region_task_list in merged.items():
        # Find fieldmen in or near this region
        region_fms = []
        region_ring = set(expand_h3_k_ring(region_cell, 2))
        for fm in fieldmen:
            fm_id = str(fm["user_id"])
            if fm_id in assigned_fm_ids:
                continue
            if fm_cells.get(fm_id) in region_ring:
                region_fms.append(fm)
                assigned_fm_ids.add(fm_id)

        # Ensure at least one fieldman per region
        if not region_fms:
            # Find nearest unassigned FM
            for fm in fieldmen:
                fm_id = str(fm["user_id"])
                if fm_id not in assigned_fm_ids:
                    region_fms.append(fm)
                    assigned_fm_ids.add(fm_id)
                    break

        if not region_fms:
            # Fall back: assign closest FM even if already assigned elsewhere
            for fm in fieldmen:
                fm_cell = fm_cells.get(str(fm["user_id"]), "")
                if fm_cell in region_ring:
                    region_fms.append(fm)
                    break
            if not region_fms and fieldmen:
                region_fms.append(fieldmen[0])

        partitions.append({
            "tasks": region_task_list,
            "fieldmen": region_fms,
            "region": region_cell,
        })

    logger.info(
        "H3 problem partitioning: %d tasks, %d fieldmen → %d partitions",
        len(tasks), len(fieldmen), len(partitions),
    )
    return partitions


def _merge_small_regions(
    regions: Dict[str, List[Dict[str, Any]]],
    min_size: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """Merge regions with fewer than min_size tasks into their nearest neighbor."""
    if not regions:
        return regions

    large: Dict[str, List[Dict[str, Any]]] = {}
    small: Dict[str, List[Dict[str, Any]]] = {}

    for cell, task_list in regions.items():
        if len(task_list) >= min_size:
            large[cell] = task_list
        else:
            small[cell] = task_list

    if not large:
        # All regions are small — just return as-is
        return regions

    for s_cell, s_tasks in small.items():
        # Find nearest large region
        best_cell = min(large.keys(), key=lambda c: h3_grid_distance(s_cell, c))
        large[best_cell].extend(s_tasks)

    return large


# ═══════════════════════════════════════════════════════════════════════
# H3 spatial sort (for better initial VROOM solutions)
# ═══════════════════════════════════════════════════════════════════════

def h3_spatial_sort(
    tasks: Sequence[Dict[str, Any]],
    resolution: int,
) -> List[Dict[str, Any]]:
    """
    Sort tasks by their H3 cell index so geographically nearby tasks are
    adjacent in the list.  H3 is designed so that nearby cells tend to have
    numerically similar indices, providing a natural spatial ordering.
    """
    def _sort_key(task: Dict[str, Any]) -> str:
        return latlng_to_h3(float(task["latitude"]), float(task["longitude"]), resolution)

    return sorted(tasks, key=_sort_key)


# ═══════════════════════════════════════════════════════════════════════
# Multi-cell fieldman coverage (k-ring skills)
# ═══════════════════════════════════════════════════════════════════════

def build_fieldman_coverage_map(
    fieldmen: Sequence[Dict[str, Any]],
    *,
    resolution: int,
    coverage_k: int = 2,
) -> Dict[str, List[str]]:
    """
    Build a map of fieldman_id → list of H3 cells they can cover.
    Each fieldman covers their own cell plus a k-ring around it.

    Returns { fieldman_user_id: [cell1, cell2, …] }
    """
    coverage: Dict[str, List[str]] = {}
    for fm in fieldmen:
        fm_id = str(fm["user_id"])
        center_cell = latlng_to_h3(
            float(fm["current_lat"]),
            float(fm["current_long"]),
            resolution,
        )
        cells = expand_h3_k_ring(center_cell, coverage_k)
        coverage[fm_id] = cells
    return coverage


def build_cell_to_fieldman_index(
    coverage_map: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """
    Invert the coverage map to: { h3_cell: [fieldman_ids…] }.
    """
    index: Dict[str, List[str]] = defaultdict(list)
    for fm_id, cells in coverage_map.items():
        for cell in cells:
            index[cell].append(fm_id)
    return dict(index)


# ═══════════════════════════════════════════════════════════════════════
# Territory compaction
# ═══════════════════════════════════════════════════════════════════════

def compute_fieldman_territory_compact(
    tasks: Sequence[Dict[str, Any]],
    fieldman_id: str,
    *,
    resolution: int,
) -> List[str]:
    """
    Compute a compact H3 representation of a fieldman's territory
    (the tasks assigned to them).  Uses H3 compaction to represent
    the same area with fewer cells at mixed resolutions.
    """
    cells = set()
    for task in tasks:
        cell = latlng_to_h3(float(task["latitude"]), float(task["longitude"]), resolution)
        cells.add(cell)
    return h3_compact_cells(list(cells))


def cell_to_boundary(cell: str) -> List[List[float]]:
    """Return cell boundary as list of [lat, lng] pairs."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "cell_to_boundary"):
        return [list(pt) for pt in h3.cell_to_boundary(cell)]
    if hasattr(h3, "h3_to_geo_boundary"):
        return [list(pt) for pt in h3.h3_to_geo_boundary(cell)]
    raise RuntimeError("h3 library does not expose cell boundary API")


def cell_to_center(cell: str) -> tuple:
    """Return (lat, lng) center of cell."""
    if h3 is None:
        raise RuntimeError("h3 library is not available")
    if hasattr(h3, "cell_to_latlng"):
        return h3.cell_to_latlng(cell)
    if hasattr(h3, "h3_to_geo"):
        return h3.h3_to_geo(cell)
    raise RuntimeError("h3 library does not expose cell center API")
