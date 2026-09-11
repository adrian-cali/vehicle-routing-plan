"""Seed sample data into the database.

Populates Metro Manila, Cebu, and Davao with tasks and fieldmen
snapped to OSRM-reachable road locations.
"""

import asyncio
import json
import math
import os
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
OSRM_URL = os.getenv("OSRM_URL", "http://localhost:5001")

REGIONS = [
    {
        "name": "metro_manila",
        "bounds": {"lat_min": 14.45, "lat_max": 14.85, "lon_min": 120.85, "lon_max": 121.15},
        "hub": (14.5995, 120.9842),
        "radius_km": 25.0,
        "cities": [
            "Manila",
            "Quezon City",
            "Makati",
            "Pasig",
            "Taguig",
            "Mandaluyong",
            "Pasay",
        ],
        "tasks": 150,
        "fieldmen": 10,
    },
    {
        "name": "cebu",
        "bounds": {"lat_min": 10.20, "lat_max": 10.45, "lon_min": 123.80, "lon_max": 124.00},
        "hub": (10.3157, 123.8854),
        "radius_km": 20.0,
        "cities": [
            "Cebu City",
            "Mandaue",
            "Lapu-Lapu",
        ],
        "tasks": 90,
        "fieldmen": 6,
    },
    {
        "name": "davao",
        "bounds": {"lat_min": 7.00, "lat_max": 7.30, "lon_min": 125.40, "lon_max": 125.75},
        "hub": (7.1907, 125.4553),
        "radius_km": 20.0,
        "cities": [
            "Davao City",
        ],
        "tasks": 60,
        "fieldmen": 4,
    },
]


def random_point(bounds: Dict[str, float]) -> Tuple[float, float]:
    lat = random.uniform(bounds["lat_min"], bounds["lat_max"])
    lon = random.uniform(bounds["lon_min"], bounds["lon_max"])
    return lat, lon


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    sin_dphi = math.sin(dphi / 2.0)
    sin_dlambda = math.sin(dlambda / 2.0)
    a_val = sin_dphi * sin_dphi + math.cos(phi1) * math.cos(phi2) * sin_dlambda * sin_dlambda
    c_val = 2 * math.atan2(math.sqrt(a_val), math.sqrt(1 - a_val))
    return r * c_val


async def nearest_road(client: httpx.AsyncClient, lat: float, lon: float) -> Tuple[float, float, float]:
    url = f"{OSRM_URL.rstrip('/')}/nearest/v1/driving/{lon},{lat}"
    response = await client.get(url, params={"number": 1})
    response.raise_for_status()
    data = response.json()
    waypoints = data.get("waypoints") or []
    if not waypoints:
        raise RuntimeError("OSRM nearest returned no waypoints")
    point = waypoints[0]
    location = point.get("location") or []
    if len(location) != 2:
        raise RuntimeError("OSRM waypoint missing location")
    snapped_lon, snapped_lat = location
    distance = float(point.get("distance", 999999.0))
    return float(snapped_lat), float(snapped_lon), distance


async def route_exists(client: httpx.AsyncClient, lat: float, lon: float, hub: Tuple[float, float]) -> bool:
    hub_lat, hub_lon = hub
    url = f"{OSRM_URL.rstrip('/')}/route/v1/driving/{lon},{lat};{hub_lon},{hub_lat}"
    response = await client.get(url, params={"overview": "false"})
    if response.status_code != 200:
        return False
    data = response.json()
    return bool(data.get("routes"))


async def sample_points(
    count: int,
    *,
    bounds: Dict[str, float],
    hub: Tuple[float, float],
    max_distance: float,
    max_radius_km: Optional[float],
    label: str,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    attempts = 0
    max_attempts = count * 80
    async with httpx.AsyncClient(timeout=10.0) as client:
        while len(points) < count and attempts < max_attempts:
            attempts += 1
            lat, lon = random_point(bounds)
            if max_radius_km is not None:
                if haversine_km((lat, lon), hub) > max_radius_km:
                    continue
            try:
                snapped_lat, snapped_lon, distance = await nearest_road(client, lat, lon)
            except Exception:
                continue
            if distance > max_distance:
                continue
            if not await route_exists(client, snapped_lat, snapped_lon, hub):
                continue
            points.append((snapped_lat, snapped_lon))
            if len(points) % 25 == 0:
                print(f"{label}: {len(points)}/{count}")
    if len(points) < count:
        raise RuntimeError(f"Unable to sample {count} {label} points (got {len(points)})")
    return points


async def fetch_columns(conn: asyncpg.Connection, table: str) -> List[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    return [row["column_name"] for row in rows]


async def main() -> None:
    print("Checking OSRM...")
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"{OSRM_URL.rstrip('/')}/nearest/v1/driving/121.0,14.5995",
            params={"number": 1},
        )
        response.raise_for_status()
    print("OSRM reachable.")

    region_points: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    for region in REGIONS:
        name = region["name"]
        print(f"Sampling {name} fieldmen...")
        fieldman_points = await sample_points(
            region["fieldmen"],
            bounds=region["bounds"],
            hub=region["hub"],
            max_distance=2000.0,
            max_radius_km=region.get("radius_km"),
            label=f"{name} fieldmen",
        )
        print(f"Sampling {name} tasks...")
        task_points = await sample_points(
            region["tasks"],
            bounds=region["bounds"],
            hub=region["hub"],
            max_distance=2000.0,
            max_radius_km=region.get("radius_km"),
            label=f"{name} tasks",
        )
        region_points[name] = {"fieldmen": fieldman_points, "tasks": task_points}

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        task_columns = await fetch_columns(conn, "tasks")
        fm_columns = await fetch_columns(conn, "fm_home_locations")
        area_columns = await fetch_columns(conn, "fm_assigned_areas")

        await conn.execute(
            "TRUNCATE vrp_assignments, vrp_jobs, fm_assigned_areas, fm_home_locations, tasks RESTART IDENTITY CASCADE"
        )

        task_rows: List[Dict[str, Any]] = []
        fm_rows: List[Dict[str, Any]] = []
        area_rows: List[Dict[str, Any]] = []
        region_map: Dict[str, Dict[str, List[str]]] = {}

        region_area_ids = {region["name"]: str(uuid.uuid4()) for region in REGIONS}

        task_index = 1
        fieldman_index = 1
        for region in REGIONS:
            name = region["name"]
            cities = region["cities"]
            tasks_for_region: List[str] = []
            fieldmen_for_region: List[str] = []
            for lat, lon in region_points[name]["tasks"]:
                task_id = str(uuid.uuid4())
                task_rows.append(
                    {
                        "id": task_id,
                        "address": f"Task {task_index} - {random.choice(cities)}",
                        "latitude": lat,
                        "longitude": lon,
                        "priority": random.randint(10, 100),
                        "service": random.randint(0, 600),
                    }
                )
                tasks_for_region.append(task_id)
                task_index += 1

            for lat, lon in region_points[name]["fieldmen"]:
                user_id = str(uuid.uuid4())
                fm_rows.append(
                    {
                        "user_id": user_id,
                        "address": f"Fieldman {fieldman_index} - {random.choice(cities)}",
                        "home_lat": lat,
                        "home_long": lon,
                    }
                )
                fieldmen_for_region.append(user_id)
                area_rows.append({"user_id": user_id, "area_id": region_area_ids[name]})
                fieldman_index += 1

            region_map[name] = {"tasks": tasks_for_region, "fieldmen": fieldmen_for_region}

        def build_insert(table: str, rows: List[Dict[str, Any]], columns: List[str]) -> Tuple[str, List[Tuple[Any, ...]]]:
            insert_cols = [col for col in columns if col in rows[0]]
            placeholders = ", ".join(f"${i}" for i in range(1, len(insert_cols) + 1))
            sql = f"INSERT INTO {table} ({', '.join(insert_cols)}) VALUES ({placeholders})"
            values = [tuple(row[col] for col in insert_cols) for row in rows]
            return sql, values

        if task_rows:
            sql, values = build_insert("tasks", task_rows, task_columns)
            await conn.executemany(sql, values)

        if fm_rows:
            sql, values = build_insert("fm_home_locations", fm_rows, fm_columns)
            await conn.executemany(sql, values)

        if area_rows and area_columns:
            sql, values = build_insert("fm_assigned_areas", area_rows, area_columns)
            await conn.executemany(sql, values)

        seed_path = os.path.join(os.path.dirname(__file__), "seed_regions.json")
        with open(seed_path, "w", encoding="utf-8") as handle:
            json.dump(region_map, handle, indent=2)

        print(f"Seeded {len(task_rows)} tasks, {len(fm_rows)} fieldmen, {len(area_rows)} area rows.")
        print(f"Region mapping saved to {seed_path}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
