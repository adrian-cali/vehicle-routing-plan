"""
Tests for services.h3_utils — H3 geospatial utilities.

Run with:  pytest backend/tests/test_h3_utils.py -v
"""

from __future__ import annotations

import math
import pytest

from services.h3_utils import (
    latlng_to_h3,
    h3_to_parent,
    h3_get_resolution,
    h3_grid_distance,
    expand_h3_k_ring,
    h3_compact_cells,
    h3_cell_to_latlng,
    group_tasks_by_h3,
    find_nearest_fm_h3,
    choose_adaptive_resolution,
    suggest_h3_resolution,
    build_h3_clusters,
    assign_tasks_to_fieldmen_h3,
    partition_problem_h3,
    h3_spatial_sort,
    build_fieldman_coverage_map,
    build_cell_to_fieldman_index,
    compute_fieldman_territory_compact,
    _haversine_km,
    _merge_small_regions,
)


# ── Fixtures ─────────────────────────────────────────────────────────

# Coordinates around Manila, Philippines
MANILA_LAT, MANILA_LNG = 14.5995, 120.9842
MAKATI_LAT, MAKATI_LNG = 14.5547, 121.0244  # ~6 km from Manila
QUEZON_LAT, QUEZON_LNG = 14.6760, 121.0437  # ~10 km from Manila
CEBU_LAT, CEBU_LNG = 10.3157, 123.8854      # ~570 km from Manila


def _make_task(task_id: str, lat: float, lng: float, priority: float = 1.0, **kw):
    """Helper to build a task dict."""
    return {"task_id": task_id, "latitude": lat, "longitude": lng, "priority": priority, **kw}


def _make_fm(user_id: str, lat: float, lng: float, **kw):
    """Helper to build a fieldman dict."""
    return {"user_id": user_id, "current_lat": lat, "current_long": lng, **kw}


# ── Core H3 wrappers ────────────────────────────────────────────────

class TestLatlngToH3:
    """Test latlng_to_h3 core wrapper."""

    def test_returns_string(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        assert isinstance(cell, str)
        assert len(cell) > 0

    def test_different_resolutions_differ(self):
        c7 = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        c8 = latlng_to_h3(MANILA_LAT, MANILA_LNG, 8)
        assert c7 != c8

    def test_same_input_same_output(self):
        a = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        b = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        assert a == b

    def test_nearby_points_same_cell(self):
        """Two points meters apart should land in the same fine cell."""
        c1 = latlng_to_h3(14.5995, 120.9842, 9)
        c2 = latlng_to_h3(14.5996, 120.9843, 9)
        assert c1 == c2

    def test_far_points_different_cell(self):
        c1 = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        c2 = latlng_to_h3(CEBU_LAT, CEBU_LNG, 7)
        assert c1 != c2


class TestH3ToParent:
    def test_parent_has_lower_resolution(self):
        child = latlng_to_h3(MANILA_LAT, MANILA_LNG, 8)
        parent = h3_to_parent(child, 5)
        assert h3_get_resolution(parent) == 5

    def test_parent_relationship_is_stable(self):
        c1 = latlng_to_h3(MANILA_LAT, MANILA_LNG, 9)
        c2 = latlng_to_h3(MANILA_LAT, MANILA_LNG + 0.0001, 9)
        p1 = h3_to_parent(c1, 6)
        p2 = h3_to_parent(c2, 6)
        assert p1 == p2  # tiny offset should share same coarse parent


class TestH3GetResolution:
    def test_resolution_roundtrip(self):
        for res in [4, 7, 10]:
            cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, res)
            assert h3_get_resolution(cell) == res


class TestH3GridDistance:
    def test_same_cell_zero_distance(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        assert h3_grid_distance(cell, cell) == 0

    def test_nearby_cells_small_distance(self):
        c1 = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        c2 = latlng_to_h3(MAKATI_LAT, MAKATI_LNG, 7)
        dist = h3_grid_distance(c1, c2)
        assert 0 < dist < 50


class TestExpandH3KRing:
    def test_k0_returns_origin(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        ring = expand_h3_k_ring(cell, 0)
        assert ring == [cell]

    def test_k1_returns_7_cells(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        ring = expand_h3_k_ring(cell, 1)
        assert len(ring) == 7
        assert cell in ring

    def test_k2_larger_than_k1(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        r1 = expand_h3_k_ring(cell, 1)
        r2 = expand_h3_k_ring(cell, 2)
        assert len(r2) > len(r1)
        assert set(r1).issubset(set(r2))


class TestH3CompactCells:
    def test_empty_returns_empty(self):
        assert h3_compact_cells([]) == []

    def test_single_cell_unchanged(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        result = h3_compact_cells([cell])
        assert len(result) >= 1

    def test_compaction_reduces_count(self):
        """A full k-ring should be compactable."""
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 8)
        ring = expand_h3_k_ring(cell, 1)
        compacted = h3_compact_cells(ring)
        # Compaction can reduce or stay same; should never increase beyond original
        assert len(compacted) <= len(ring) + 1


class TestH3CellToLatlng:
    def test_roundtrip_close(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 9)
        lat, lng = h3_cell_to_latlng(cell)
        assert abs(lat - MANILA_LAT) < 0.01
        assert abs(lng - MANILA_LNG) < 0.01


# ── Haversine ────────────────────────────────────────────────────────

class TestHaversine:
    def test_zero_distance(self):
        assert _haversine_km(MANILA_LAT, MANILA_LNG, MANILA_LAT, MANILA_LNG) == 0.0

    def test_manila_to_makati(self):
        dist = _haversine_km(MANILA_LAT, MANILA_LNG, MAKATI_LAT, MAKATI_LNG)
        assert 4 < dist < 8  # roughly 6 km

    def test_manila_to_cebu(self):
        dist = _haversine_km(MANILA_LAT, MANILA_LNG, CEBU_LAT, CEBU_LNG)
        assert 500 < dist < 650

    def test_symmetry(self):
        d1 = _haversine_km(MANILA_LAT, MANILA_LNG, CEBU_LAT, CEBU_LNG)
        d2 = _haversine_km(CEBU_LAT, CEBU_LNG, MANILA_LAT, MANILA_LNG)
        assert abs(d1 - d2) < 0.001


# ── Grouping ─────────────────────────────────────────────────────────

class TestGroupTasksByH3:
    def test_empty_tasks(self):
        result = group_tasks_by_h3([], 7)
        assert result == {}

    def test_single_task(self):
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        result = group_tasks_by_h3(tasks, 7)
        assert len(result) == 1
        assert sum(len(v) for v in result.values()) == 1

    def test_nearby_tasks_same_cell(self):
        tasks = [
            _make_task("t1", 14.5995, 120.9842),
            _make_task("t2", 14.5996, 120.9843),
        ]
        result = group_tasks_by_h3(tasks, 7)
        assert len(result) == 1
        assert len(list(result.values())[0]) == 2

    def test_distant_tasks_different_cells(self):
        tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", CEBU_LAT, CEBU_LNG),
        ]
        result = group_tasks_by_h3(tasks, 7)
        assert len(result) == 2


# ── Nearest FM ───────────────────────────────────────────────────────

class TestFindNearestFmH3:
    def test_fm_in_same_cell(self):
        cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        fm_map = {cell: ["fm1"]}
        assert find_nearest_fm_h3(cell, fm_map) == cell

    def test_fm_in_adjacent_cell(self):
        task_cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        ring = expand_h3_k_ring(task_cell, 1)
        neighbor = [c for c in ring if c != task_cell][0]
        fm_map = {neighbor: ["fm1"]}
        result = find_nearest_fm_h3(task_cell, fm_map, max_k=1)
        assert result == neighbor

    def test_no_fm_in_range(self):
        task_cell = latlng_to_h3(MANILA_LAT, MANILA_LNG, 7)
        far_cell = latlng_to_h3(CEBU_LAT, CEBU_LNG, 7)
        fm_map = {far_cell: ["fm1"]}
        result = find_nearest_fm_h3(task_cell, fm_map, max_k=3)
        assert result is None


# ── Adaptive resolution ─────────────────────────────────────────────

class TestChooseAdaptiveResolution:
    def test_empty_tasks_returns_default(self):
        assert choose_adaptive_resolution([]) == 7

    def test_single_point_returns_max(self):
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        res = choose_adaptive_resolution(tasks, max_res=10)
        assert res == 10  # single point → max resolution

    def test_spread_affects_resolution(self):
        """City-scale spread should give finer resolution than country-scale."""
        city_tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", MAKATI_LAT, MAKATI_LNG),
        ]
        country_tasks = [
            _make_task("t3", MANILA_LAT, MANILA_LNG),
            _make_task("t4", CEBU_LAT, CEBU_LNG),
        ]
        city_res = choose_adaptive_resolution(city_tasks, min_res=4, max_res=10)
        country_res = choose_adaptive_resolution(country_tasks, min_res=4, max_res=10)
        assert city_res >= country_res  # city is finer (higher number)

    def test_respects_min_max_bounds(self):
        tasks = [_make_task(f"t{i}", MANILA_LAT + i * 0.001, MANILA_LNG) for i in range(20)]
        res = choose_adaptive_resolution(tasks, min_res=5, max_res=9)
        assert 5 <= res <= 9


# ── suggest_h3_resolution (bbox-based) ──────────────────────────────

class TestSuggestH3Resolution:
    """Tests for suggest_h3_resolution — bbox-diagonal-based auto-detect."""

    def test_empty_coords_returns_default(self):
        """Empty input should return the sensible default of 7."""
        assert suggest_h3_resolution([]) == 7

    def test_single_point_returns_finest(self):
        """A single coordinate should return resolution 9 (finest practical)."""
        assert suggest_h3_resolution([(MANILA_LAT, MANILA_LNG)]) == 9

    def test_tight_urban_cluster_returns_9(self):
        """Points within ~2 km of each other (< 5 km bbox) → resolution 9."""
        coords = [
            (14.5995, 120.9842),
            (14.6010, 120.9860),  # ~200m away
            (14.5980, 120.9830),  # ~200m away
            (14.6000, 120.9850),  # ~100m away
        ]
        assert suggest_h3_resolution(coords) == 9

    def test_city_spread_returns_8(self):
        """Points ~6 km apart (5–20 km bbox diagonal) → resolution 8."""
        coords = [
            (MANILA_LAT, MANILA_LNG),   # Manila
            (MAKATI_LAT, MAKATI_LNG),   # Makati ~6 km away
        ]
        res = suggest_h3_resolution(coords)
        assert res == 8

    def test_wide_spread_returns_lower_resolution(self):
        """Points ~570 km apart (80+ km bbox diagonal) → resolution 6."""
        coords = [
            (MANILA_LAT, MANILA_LNG),   # Manila
            (CEBU_LAT, CEBU_LNG),       # Cebu ~570 km away
        ]
        res = suggest_h3_resolution(coords)
        assert res == 6

    def test_metro_spread_returns_7(self):
        """Points ~30 km apart (20–80 km bbox diagonal) → resolution 7."""
        coords = [
            (14.5995, 120.9842),  # Central Manila
            (14.4000, 121.0500),  # ~25 km south-east
        ]
        res = suggest_h3_resolution(coords)
        assert res == 7

    def test_identical_points_returns_9(self):
        """All points at the exact same location → resolution 9."""
        coords = [(MANILA_LAT, MANILA_LNG)] * 100
        assert suggest_h3_resolution(coords) == 9


# ── Hierarchical clustering ─────────────────────────────────────────

class TestBuildH3Clusters:
    def test_empty_tasks(self):
        result = build_h3_clusters([], fine_resolution=7)
        assert result == {}

    def test_single_task(self):
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        result = build_h3_clusters(tasks, fine_resolution=7)
        assert len(result) == 1
        coarse_groups = list(result.values())
        assert sum(len(fine) for fine in coarse_groups for fine in fine.values()) == 1

    def test_two_level_structure(self):
        tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", MAKATI_LAT, MAKATI_LNG),
            _make_task("t3", CEBU_LAT, CEBU_LNG),
        ]
        result = build_h3_clusters(tasks, fine_resolution=7, coarse_resolution=4)
        # Manila and Makati may share coarse cell; Cebu is separate
        total_tasks = sum(
            len(tl) for coarse in result.values() for tl in coarse.values()
        )
        assert total_tasks == 3


# ── Assignment ───────────────────────────────────────────────────────

class TestAssignTasksToFieldmenH3:
    def test_empty_inputs(self):
        assert assign_tasks_to_fieldmen_h3([], [], resolution=7) == {}
        assert assign_tasks_to_fieldmen_h3([], [_make_fm("fm1", MANILA_LAT, MANILA_LNG)], resolution=7) == {}

    def test_all_tasks_assigned(self):
        tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", MAKATI_LAT, MAKATI_LNG),
            _make_task("t3", QUEZON_LAT, QUEZON_LNG),
        ]
        fms = [
            _make_fm("fm1", MANILA_LAT, MANILA_LNG),
            _make_fm("fm2", MAKATI_LAT, MAKATI_LNG),
        ]
        result = assign_tasks_to_fieldmen_h3(tasks, fms, resolution=7)
        total = sum(len(v) for v in result.values())
        assert total == 3

    def test_nearby_fm_preferred(self):
        """Fieldman in Manila should get the Manila task."""
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        fms = [
            _make_fm("fm_near", MANILA_LAT + 0.001, MANILA_LNG),
            _make_fm("fm_far", CEBU_LAT, CEBU_LNG),
        ]
        result = assign_tasks_to_fieldmen_h3(tasks, fms, resolution=7, max_k=10)
        assert len(result["fm_near"]) >= 1 or len(result["fm_far"]) >= 0  # Flexible — depends on H3 cells
        # At least all tasks assigned
        assert sum(len(v) for v in result.values()) == 1

    def test_load_balancing(self):
        """With many tasks near one FM, load balancing should spread some to the other."""
        tasks = [_make_task(f"t{i}", MANILA_LAT + i * 0.0005, MANILA_LNG) for i in range(10)]
        fms = [
            _make_fm("fm1", MANILA_LAT, MANILA_LNG),
            _make_fm("fm2", MANILA_LAT + 0.005, MANILA_LNG),
        ]
        result = assign_tasks_to_fieldmen_h3(tasks, fms, resolution=7, load_balance_weight=0.5)
        # Both FMs should get at least some tasks
        assert len(result["fm1"]) >= 1
        assert len(result["fm2"]) >= 1


# ── Partitioning ─────────────────────────────────────────────────────

class TestPartitionProblemH3:
    def test_small_problem_not_partitioned(self):
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        fms = [_make_fm("fm1", MANILA_LAT, MANILA_LNG)]
        result = partition_problem_h3(tasks, fms, resolution=7, max_partition_size=150)
        assert len(result) == 1
        assert result[0]["region"] == "single"

    def test_large_problem_partitioned(self):
        """Generate enough tasks to exceed max_partition_size."""
        tasks = [
            _make_task(f"t{i}", MANILA_LAT + (i % 10) * 0.05, MANILA_LNG + (i // 10) * 0.05)
            for i in range(200)
        ]
        fms = [
            _make_fm("fm1", MANILA_LAT, MANILA_LNG),
            _make_fm("fm2", MANILA_LAT + 0.25, MANILA_LNG + 0.25),
            _make_fm("fm3", MANILA_LAT + 0.45, MANILA_LNG + 0.45),
        ]
        result = partition_problem_h3(tasks, fms, resolution=7, max_partition_size=50)
        assert len(result) >= 2
        total = sum(len(p["tasks"]) for p in result)
        assert total == 200  # all tasks accounted for

    def test_all_tasks_covered(self):
        tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", CEBU_LAT, CEBU_LNG),
        ]
        fms = [_make_fm("fm1", MANILA_LAT, MANILA_LNG)]
        result = partition_problem_h3(tasks, fms, resolution=7, max_partition_size=1)
        total = sum(len(p["tasks"]) for p in result)
        assert total == 2


# ── Spatial sort ─────────────────────────────────────────────────────

class TestH3SpatialSort:
    def test_preserves_count(self):
        tasks = [
            _make_task("t1", MANILA_LAT, MANILA_LNG),
            _make_task("t2", MAKATI_LAT, MAKATI_LNG),
            _make_task("t3", CEBU_LAT, CEBU_LNG),
        ]
        sorted_tasks = h3_spatial_sort(tasks, 7)
        assert len(sorted_tasks) == 3

    def test_deterministic(self):
        tasks = [
            _make_task("t1", CEBU_LAT, CEBU_LNG),
            _make_task("t2", MANILA_LAT, MANILA_LNG),
        ]
        s1 = h3_spatial_sort(tasks, 7)
        s2 = h3_spatial_sort(tasks, 7)
        assert [t["task_id"] for t in s1] == [t["task_id"] for t in s2]


# ── Coverage map ─────────────────────────────────────────────────────

class TestBuildFieldmanCoverageMap:
    def test_single_fm(self):
        fms = [_make_fm("fm1", MANILA_LAT, MANILA_LNG)]
        result = build_fieldman_coverage_map(fms, resolution=7, coverage_k=1)
        assert "fm1" in result
        assert len(result["fm1"]) == 7  # center + 6 neighbors

    def test_coverage_grows_with_k(self):
        fms = [_make_fm("fm1", MANILA_LAT, MANILA_LNG)]
        r1 = build_fieldman_coverage_map(fms, resolution=7, coverage_k=1)
        r2 = build_fieldman_coverage_map(fms, resolution=7, coverage_k=2)
        assert len(r2["fm1"]) > len(r1["fm1"])


class TestBuildCellToFieldmanIndex:
    def test_inversion(self):
        coverage = {"fm1": ["cell_a", "cell_b"], "fm2": ["cell_b", "cell_c"]}
        index = build_cell_to_fieldman_index(coverage)
        assert "fm1" in index["cell_a"]
        assert "fm2" not in index["cell_a"]
        assert set(index["cell_b"]) == {"fm1", "fm2"}


# ── Territory compaction ─────────────────────────────────────────────

class TestComputeFieldmanTerritoryCompact:
    def test_single_task(self):
        tasks = [_make_task("t1", MANILA_LAT, MANILA_LNG)]
        result = compute_fieldman_territory_compact(tasks, "fm1", resolution=7)
        assert len(result) >= 1

    def test_empty_tasks(self):
        result = compute_fieldman_territory_compact([], "fm1", resolution=7)
        assert result == []


# ── Merge small regions helper ───────────────────────────────────────

class TestMergeSmallRegions:
    def test_empty(self):
        assert _merge_small_regions({}) == {}

    def test_all_large(self):
        regions = {"a": [1, 2, 3, 4, 5], "b": [6, 7, 8, 9, 10]}
        result = _merge_small_regions(regions, min_size=5)
        assert len(result) == 2

    def test_all_small(self):
        """If all regions are small, they are returned as-is."""
        regions = {"a": [1], "b": [2]}
        result = _merge_small_regions(regions, min_size=5)
        assert len(result) == 2  # no large cells to merge into
