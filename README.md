# VRP Console — H3 + OSRM + VROOM Vehicle Routing

A full-stack Vehicle Routing Problem (VRP) solver built with **FastAPI**, **Celery**, **PostgreSQL**, and **Redis**. Uses **VROOM** for combinatorial optimization, **OSRM** for road-network routing, and **H3** hexagonal indexing for spatial clustering and load balancing.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [OSRM Map Data](#osrm-map-data)
- [Accessing the Application](#accessing-the-application)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Utility Scripts](#utility-scripts)
- [Troubleshooting](#troubleshooting)

---

## Architecture

| Component    | Technology          | Purpose                                   |
| ------------ | ------------------- | ----------------------------------------- |
| **API**      | FastAPI + Uvicorn   | REST API + WebSocket + static frontend    |
| **Worker**   | Celery              | Async VRP job processing                  |
| **Database** | PostgreSQL 16       | Tasks, fieldmen, jobs, and assignments    |
| **Broker**   | Redis 7             | Celery broker/backend + WebSocket pub/sub |
| **Router**   | OSRM                | Road-network distance/duration matrices   |
| **Solver**   | VROOM v1.14         | Vehicle routing optimization              |
| **Spatial**  | H3 (Uber)           | Hexagonal grid clustering & partitioning  |

All components run as Docker containers orchestrated by the root `docker-compose.yml`.

---

## Prerequisites

- **Docker Desktop** (v4.x+ recommended)
- **Docker Compose** v2+
- **Git**
- *(Optional for local dev)* Python 3.12+

---

## Quick Start (Docker)

This is the recommended way to run the full system.

### 1. Clone the repository

```bash
git clone https://github.com/adrian-cali/vehicle-routing-plan.git
cd vrp
```

### 2. Configure environment

```bash
cp .env.example .env
```

> Defaults in `.env.example` work out of the box for Docker. No edits required for a first run.

### 3. Ensure OSRM map data is present

The pre-processed Philippines OSRM files must exist in `vroom_ors/osrm-philippines/`.
See [OSRM Map Data](#osrm-map-data) if they are missing.

### 4. Start all services

```bash
docker compose up -d --build
```

This starts **6 containers**: `postgres`, `redis`, `osrm`, `vroom`, `api`, and `celery`.

```bash
docker compose ps        # verify all containers are Up/healthy
docker compose logs -f   # stream all logs
```

### 5. Seed sample data (first run only)

```bash
docker compose exec api python backend/scripts/seed_data.py
```

This populates Metro Manila, Cebu, and Davao with ~300 tasks and ~20 fieldmen snapped to OSRM roads.

---

## Local Development Setup

Use this if you want hot-reload and direct Python access during development.

### 1. Create a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Start infrastructure containers only

```bash
docker compose up -d postgres redis osrm vroom
```

### 3. Start the FastAPI server

```bash
cd backend
uvicorn app:app --host 0.0.0.0 --port 4000 --reload
```

### 4. Start the Celery worker

Open a second terminal:

```bash
cd backend
celery -A celery_app worker --loglevel=info --pool=solo -Q vrp_queue,geocode_queue
```

> **Windows:** `--pool=solo` is required. On Linux/macOS use `--pool=prefork` or `--pool=gevent`.

---

## Environment Variables

Copy `.env.example` to `.env` and adjust as needed. The full list of options:

```env
# ── Database ──────────────────────────────────────────────────────────
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/vrp

# ── Redis (Celery broker + cache) ─────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ── OSRM (routing engine) ─────────────────────────────────────────────
OSRM_URL=http://localhost:5001
OSRM_TIMEOUT=120.0
OSRM_MAX_LOCATIONS=250

# ── VROOM (optimization engine) ───────────────────────────────────────
VROOM_URL=http://localhost:3000
VROOM_TIMEOUT=300.0

# ── H3 (hexagonal spatial grid) ───────────────────────────────────────
H3_DEFAULT_RESOLUTION=7
H3_MAX_K=5
H3_ADAPTIVE_RESOLUTION=true
H3_COVERAGE_K=2
H3_PARTITION_THRESHOLD=500
H3_MAX_PARTITION_SIZE=400
H3_LOAD_BALANCE_WEIGHT=0.3

# ── VRP Settings ──────────────────────────────────────────────────────
GEOMETRY_ROUTE_LIMIT=50
OSRM_CONCURRENCY=40
MAX_TASKS_PER_PARTITION=800

# ── Application ───────────────────────────────────────────────────────
APP_NAME=VRP Service
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=*
ENABLE_LEGACY_ROUTES=true
```

> **Docker:** Replace `localhost` with Docker service names — `postgres`, `redis`, `osrm`, `vroom`.
> See `docker-compose.yml` `x-common-env` block for the Docker-specific defaults.

---

## OSRM Map Data

The OSRM container requires pre-processed road network files for the Philippines.

If `vroom_ors/osrm-philippines/philippines-latest.osrm` does not exist:

```bash
cd vroom_ors/osrm-philippines

# 1. Download the raw OSM data
# https://download.geofabrik.de/asia/philippines.html

# 2. Extract
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/philippines-latest.osm.pbf

# 3. Partition (MLD algorithm)
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-partition /data/philippines-latest.osrm

# 4. Customize
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-customize /data/philippines-latest.osrm

cd ../..
```

> This only needs to be done **once**. The processed `.osrm.*` files are large and gitignored.

---

## Accessing the Application

| URL                              | Description                        |
| -------------------------------- | ---------------------------------- |
| **http://localhost:8000**        | **Frontend** — VRP Console SPA     |
| **http://localhost:8000/docs**   | **Backend** — Swagger UI           |
| **http://localhost:8000/redoc**  | **Backend** — ReDoc                |

---

## Project Structure

```
vrp/
├── backend/                     # Python backend (FastAPI + Celery)
│   ├── app.py                   # FastAPI application entry point
│   ├── celery_app.py            # Celery configuration
│   ├── alembic/                 # Database migrations
│   ├── api/                     # Route handlers
│   │   ├── v1/                  #   Versioned API (current)
│   │   │   ├── data_routes.py   #     Seed / reset / randomize
│   │   │   ├── health_routes.py #     Health check
│   │   │   ├── vrp_routes.py    #     VRP jobs & assignments
│   │   │   └── ws_routes.py     #     WebSocket job updates
│   │   ├── health_routes.py     #   Legacy health (compat)
│   │   ├── vrp_routes.py        #   Legacy VRP (compat)
│   │   └── ws_routes.py         #   Legacy WebSocket (compat)
│   ├── core/                    # Shared infrastructure
│   │   ├── config.py            #   Centralized settings (env vars)
│   │   ├── database.py          #   DB pool + Redis client
│   │   ├── exceptions.py        #   Custom exception hierarchy
│   │   ├── logging.py           #   Structured JSON logging
│   │   ├── middleware.py        #   Correlation ID + error handlers
│   │   └── responses.py         #   API response envelope
│   ├── domain/                  # Pydantic schemas (data models)
│   │   ├── audit.py
│   │   ├── fieldmen.py
│   │   ├── tasks.py
│   │   └── vrp.py
│   ├── repositories/            # Database access layer
│   │   ├── audit_repository.py
│   │   ├── fieldman_repository.py
│   │   ├── task_repository.py
│   │   └── vrp_repository.py
│   ├── services/                # Business logic layer
│   │   ├── cache_service.py     #   Redis cache wrapper
│   │   ├── data_service.py      #   Seed / reset operations
│   │   ├── geocode_service.py   #   Nominatim geocoding
│   │   ├── h3_service.py        #   H3 grid endpoint logic
│   │   ├── h3_utils.py          #   H3 clustering utilities
│   │   ├── osrm_service.py      #   OSRM HTTP client
│   │   ├── vroom_service.py     #   VROOM payload builder + client
│   │   └── vrp_service.py       #   VRP orchestration
│   ├── workers/                 # Celery task definitions
│   │   ├── vrp_tasks.py         #   process_vrp_job task
│   │   └── background_tasks.py  #   Geocoding + cache warming
│   └── scripts/                 # One-off utility scripts
│       ├── seed_data.py
│       └── clear_data.py
│
├── frontend/                    # Web UI (served by FastAPI)
│   ├── index.html               # SPA shell
│   └── static/
│       ├── app.js               # Vanilla JS frontend (no build step)
│       └── styles.css
│
├── vroom_ors/                   # OSRM map data & VROOM config
│   ├── osrm-philippines/        #   Pre-processed OSRM files (gitignored)
│   └── vroom_conf/              #   VROOM server config
│
├── scripts/                     # Dev/debug helper scripts
├── tests/                       # Integration tests
├── docker-compose.yml           # Full-stack compose (all 6 services)
├── Dockerfile                   # API + Celery image
├── .env.example                 # Environment variable reference
├── requirements.txt             # Production Python dependencies
└── requirements-dev.txt         # Development/testing dependencies
```

---

## API Reference

Interactive docs available at **http://localhost:8000/docs**.

### v1 Endpoints (`/api/v1/`)

| Method | Endpoint                              | Description                           |
| ------ | ------------------------------------- | ------------------------------------- |
| GET    | `/api/v1/health`                      | Health check (DB + Redis + OSRM)      |
| POST   | `/api/v1/vrp/plan-ahead`              | Dry-run: count matching resources     |
| POST   | `/api/v1/vrp/jobs`                    | Create & enqueue a VRP job            |
| GET    | `/api/v1/vrp/jobs/{job_id}`           | Get job status                        |
| GET    | `/api/v1/vrp/jobs/{job_id}/metrics`   | Route distance/duration metrics       |
| GET    | `/api/v1/vrp/jobs/{job_id}/preview`   | Preview routes with geometry          |
| GET    | `/api/v1/vrp/jobs/{job_id}/assignments` | Assignments with route geometry     |
| POST   | `/api/v1/vrp/jobs/{job_id}/finalize`  | Finalize and commit a job             |
| DELETE | `/api/v1/vrp/jobs/{job_id}`           | Delete a job                          |
| GET    | `/api/v1/vrp/overview`                | All tasks & fieldmen for map view     |
| GET    | `/api/v1/vrp/task-summary`            | Task counts by type / bank            |
| POST   | `/api/v1/data/randomize`              | Generate random tasks & fieldmen      |
| POST   | `/api/v1/data/reset`                  | Clear all data                        |
| GET    | `/api/v1/vrp/h3-grid`                 | H3 hexagonal density grid             |
| WS     | `/ws/vrp/{job_id}`                    | Real-time job progress updates        |

> Legacy routes (`/vrp/...`, `/health`) remain available for backward compatibility when `ENABLE_LEGACY_ROUTES=true`.

---

## Utility Scripts

### Seed sample data

```bash
# Via Docker
docker compose exec api python backend/scripts/seed_data.py

# Local dev
cd backend && python scripts/seed_data.py
```

### Clear all data

```bash
# Via Docker
docker compose exec api python backend/scripts/clear_data.py

# Local dev
cd backend && python scripts/clear_data.py
```

### Stop all services

```bash
docker compose down
```

### Stop and remove volumes (full reset)

```bash
docker compose down -v
```

---

## Troubleshooting

| Issue | Solution |
| ----- | -------- |
| `OSRM nearest returned no waypoints` | OSRM map data missing — see [OSRM Map Data](#osrm-map-data) |
| Container stuck in `health: starting` | Run `docker compose logs <service>` to inspect |
| `Connection refused` on port 5432 | `vrp-postgres` container not healthy yet |
| `Connection refused` on port 6379 | `vrp-redis` container not healthy yet |
| Celery not picking up jobs | Ensure worker consumes `vrp_queue`: `--queues vrp_queue,geocode_queue` |
| Frontend shows "Connecting…" | API container not running on port 8000 |
| `ModuleNotFoundError` (local dev) | Run Python from `backend/` directory |
| Port 8000 already in use | Change host port in `docker-compose.yml` or stop the conflicting process |
