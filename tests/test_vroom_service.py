import os
import sys

# Ensure backend package is importable during tests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from typing import Any, Dict, List, Tuple

import pytest

from services.vroom_service import VROOMService
import services.h3_utils as h3_utils
import services.vroom_service as vroom_mod


def test_build_job_payloads_manual_area():
    v = VROOMService("http://example.com")
    tasks = [{"id": 1, "latitude": 10.0, "longitude": 20.0, "priority": 5, "service": 300}]
    task_area_map = {"1": "A"}
    jobs = v.build_job_payloads(
        tasks, assignment_strategy="manual_area", h3_resolution=7, task_area_map=task_area_map
    )
    assert len(jobs) == 1
    assert jobs[0]["skills"] == ["area:A"]


def test_build_vehicle_payloads_manual_area():
    v = VROOMService("http://example.com")
    fieldmen = [{"user_id": 101, "current_lat": 0.0, "current_long": 0.0, "area_ids": ["A"]}]
    vehicles = v.build_vehicle_payloads(
        fieldmen, assignment_strategy="manual_area", h3_resolution=7
    )
    assert len(vehicles) == 1
    assert vehicles[0]["skills"] == ["area:A"]


def test_normalize_skills_and_parse_solution():
    jobs = [{"id": "j1", "skills": ["area:A"]}]
    vehicles = [{"id": "v1", "skills": ["area:A"]}]
    VROOMService.normalize_skills(jobs, vehicles)
    # skills should be integers after normalization
    assert isinstance(jobs[0]["skills"][0], int)
    assert isinstance(vehicles[0]["skills"][0], int)

    # Test parse_solution with id maps
    solution = {
        "routes": [
            {"vehicle": 1, "steps": [{"type": "job", "job": 1}], "distance": 123, "duration": 45}
        ],
        "summary": {"cost": 1},
    }
    job_id_map = {1: "j1"}
    vehicle_id_map = {1: "v1"}
    parsed = VROOMService.parse_solution(solution, job_id_map=job_id_map, vehicle_id_map=vehicle_id_map)
    assert parsed["routes"][0]["vehicle_id"] == "v1"
    assert parsed["routes"][0]["tasks"] == ["j1"]


def test_h3_builds_with_monkeypatch(monkeypatch):
    # Monkeypatch h3-related helpers to deterministic stubs. Note: vroom_service
    # imports functions at module import time, so patch both the h3_utils
    # module and the symbols inside services.vroom_service.
    monkeypatch.setattr(h3_utils, "latlng_to_h3", lambda lat, lng, res: "cell1")
    monkeypatch.setattr(h3_utils, "expand_h3_k_ring", lambda cell, k: ["cell1", "cell2"])
    monkeypatch.setattr(h3_utils, "find_nearest_fm_h3", lambda cell, fm_map, max_k=3: "cell1")
    monkeypatch.setattr(h3_utils, "h3_spatial_sort", lambda tasks, res: list(tasks))

    # Patch the names bound in services.vroom_service as well
    monkeypatch.setattr(vroom_mod, "latlng_to_h3", lambda lat, lng, res: "cell1")
    monkeypatch.setattr(vroom_mod, "expand_h3_k_ring", lambda cell, k: ["cell1", "cell2"])
    monkeypatch.setattr(vroom_mod, "find_nearest_fm_h3", lambda cell, fm_map, max_k=3: "cell1")
    monkeypatch.setattr(vroom_mod, "h3_spatial_sort", lambda tasks, res: list(tasks))

    v = VROOMService("http://example.com")
    tasks = [{"id": 2, "latitude": 1.0, "longitude": 2.0}]
    fieldmen = [{"user_id": 10, "current_lat": 1.0, "current_long": 2.0, "capacity": [100]}]

    # Build cell index for multi-cell coverage
    cell_to_fm_index = {"cell1": ["10"]}
    fieldman_h3_map = {"cell1": ["10"]}

    jobs = v.build_job_payloads(
        tasks,
        assignment_strategy="h3",
        h3_resolution=7,
        task_area_map=None,
        fieldman_h3_map=fieldman_h3_map,
        cell_to_fm_index=cell_to_fm_index,
    )
    assert jobs[0]["skills"] == ["h3:cell1"]

    vehicles = v.build_vehicle_payloads(
        fieldmen, assignment_strategy="h3", h3_resolution=7, coverage_k=1
    )
    assert "h3:cell1" in vehicles[0]["skills"]
    assert "h3:cell2" in vehicles[0]["skills"]
