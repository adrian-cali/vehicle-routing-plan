# VRP Service — H3 + OSRM Routing with VROOM Optimization

A full-stack Vehicle Routing Problem (VRP) solver using **VROOM** for optimization, **OSRM** for road-network routing, and **H3** hexagonal indexing for spatial clustering. Built with **FastAPI**, **Celery**, **PostgreSQL**, and **Redis**.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Clone & Setup](#clone--setup)
- [Environment Variables](#environment-variables)
- [Running the Services](#running-the-services)
- [Accessing the Application](#accessing-the-application)
- [Project Structure](#project-structure)
- [API Documentation](#api-documentation)
- [Utility Scripts](#utility-scripts)
- [Troubleshooting](#troubleshooting)

---

## Architecture

| Component    | Technology          | Purpose                                  |
| ------------ | ------------------- | ---------------------------------------- |
| **API**      | FastAPI + Uvicorn   | REST API + WebSocket + static frontend   |
| **Worker**   | Celery              | Async VRP job processing                 |
| **Database** | PostgreSQL          | Task, fieldman, job, and assignment data  |
| **Broker**   | Redis               | Celery broker/backend + WebSocket pub/sub |
| **Router**   | OSRM                | Road-network distance/duration matrices   |
| **Solver**   | VROOM               | Vehicle routing optimization              |
| **Spatial**  | H3                  | Hexagonal grid clustering                 |

---

## Prerequisites

- **Python 3.11+**
- **Docker & Docker Compose** (for OSRM, VROOM, PostgreSQL, Redis)
- **Git**

---

## Clone & Setup

### 1. Clone the repository

```bash
git clone https://github.com/jordannealexie/vrp.git
cd vrp
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Prepare OSRM map data

The OSRM service needs pre-processed road network data for the Philippines.

Download `philippines-latest.osm.pbf` from [Geofabrik](https://download.geofabrik.de/asia/philippines.html) and place it in `vroom_ors/osrm-philippines/`, then process it:

```bash
cd vroom_ors/osrm-philippines

docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/philippines-latest.osm.pbf

docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-partition /data/philippines-latest.osrm

docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-customize /data/philippines-latest.osrm

cd ../..
```

> **Note:** This only needs to be done once. The processed `.osrm.*` files are large and gitignored.

---

## Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
REDIS_URL=redis://localhost:6379/0
OSRM_URL=http://localhost:5001
VROOM_URL=http://localhost:3000
```

All settings can be overridden via environment variables. See `backend/core/config.py` for the full list of configurable options.

---

## Running the Services

You need **four services** running before starting the application:

### Step 1: Start Docker containers

**Start OSRM + VROOM** (using the included docker-compose):

```bash
docker compose -f vroom_ors/docker-compose.yml up -d
```

**Start PostgreSQL and Redis:**

```bash
docker run -d --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Verify all containers are running:

```bash
docker ps
```

You should see `postgres`, `redis`, `osrm`, and `vroom` all with status **Up**.

### Step 2: Start the Celery worker

Open a terminal and run:

```bash
cd backend
celery -A celery_app worker --loglevel=info --pool=solo
```

> **Windows users:** The `--pool=solo` flag is required. On Linux/macOS you can use `--pool=prefork`.

### Step 3: Start the FastAPI server

Open another terminal and run:

```bash
cd backend
uvicorn app:app --host 0.0.0.0 --port 4000 --reload
```

---

## Accessing the Application

| URL                                | Description                          |
| ---------------------------------- | ------------------------------------ |
| **http://localhost:4000**          | **Frontend** — VRP Console SPA       |
| **http://localhost:4000/docs**     | **Backend** — Swagger API docs       |
| **http://localhost:4000/redoc**    | **Backend** — ReDoc API docs         |

### API Routes

| Route prefix       | Description                              |
| ------------------- | ---------------------------------------- |
| `/vrp/...`          | Legacy VRP endpoints (used by frontend)  |
| `/health`           | Legacy health check                      |
| `/ws/vrp/{job_id}`  | WebSocket for real-time job updates      |
| `/api/v1/vrp/...`   | Versioned VRP endpoints (v1)             |
| `/api/v1/health`    | Versioned health check (v1)              |

---

## Project Structure

```
vrp/
├── backend/                  # Python backend (FastAPI + Celery)
│   ├── app.py                # FastAPI application entry point
│   ├── celery_app.py         # Celery configuration
│   ├── api/                  # Route handlers
│   │   ├── health_routes.py  #   Legacy health check
│   │   ├── vrp_routes.py     #   Legacy VRP endpoints (882 lines)
│   │   ├── ws_routes.py      #   Legacy WebSocket
│   │   └── v1/               #   Versioned API (v1)
│   │       ├── health_routes.py
│   │       ├── vrp_routes.py
│   │       └── ws_routes.py
│   ├── core/                 # Shared infrastructure
│   │   ├── config.py         #   Centralized settings (env vars)
│   │   ├── database.py       #   DB pool + Redis client management
│   │   ├── exceptions.py     #   Custom exception hierarchy
│   │   ├── logging.py        #   Structured JSON logging
│   │   ├── middleware.py     #   Correlation ID + error handlers
│   │   └── responses.py     #   API response envelope
│   ├── domain/               # Pydantic schemas (pure data models)
│   │   ├── audit.py
│   │   ├── fieldmen.py
│   │   ├── tasks.py
│   │   └── vrp.py
│   ├── repositories/         # Database access layer
│   │   ├── audit_repository.py
│   │   ├── fieldman_repository.py
│   │   ├── task_repository.py
│   │   └── vrp_repository.py
│   ├── services/             # Business logic layer
│   │   ├── h3_utils.py       #   H3 hexagonal grid utilities
│   │   ├── osrm_service.py   #   OSRM HTTP client
│   │   ├── vroom_service.py  #   VROOM payload builder + client
│   │   └── vrp_service.py    #   VRP orchestration logic
│   ├── workers/              # Celery task definitions
│   │   └── vrp_tasks.py      #   process_vrp_job async task
│   └── scripts/              # Utility scripts
│       ├── seed_data.py      #   Seed sample data
│       ├── clear_data.py     #   Clear all data
│       └── seed_regions.json #   Generated region mapping
│
├── frontend/                 # Web UI (served by FastAPI)
│   ├── index.html            # Single-page app shell
│   └── static/
│       ├── app.js            # React SPA (vanilla JS, no build step)
│       └── styles.css        # Stylesheet
│
├── vroom_ors/                # Docker / infrastructure config
│   ├── docker-compose.yml    # OSRM + VROOM compose file
│   ├── osrm-philippines/     # OSRM map data (gitignored)
│   └── vroom_conf/           # VROOM server configuration
│
├── .env                      # Environment variables (gitignored)
├── .gitignore
├── requirements.txt          # Pinned Python dependencies
└── README.md
```

---

## API Documentation

Once the server is running, interactive API documentation is available at:

- **Swagger UI:** http://localhost:4000/docs
- **ReDoc:** http://localhost:4000/redoc

### Key Endpoints

| Method | Endpoint                         | Description                        |
| ------ | -------------------------------- | ---------------------------------- |
| GET    | `/health`                        | Health check (DB connectivity)     |
| POST   | `/vrp/randomize`                 | Generate random tasks & fieldmen   |
| POST   | `/vrp/data/reset`                | Clear all data                     |
| GET    | `/vrp/task-summary`              | Summary of tasks by type/bank      |
| POST   | `/vrp/optimize`                  | Create & run VRP optimization job  |
| POST   | `/vrp/plan-ahead`                | Dry-run: count matching resources  |
| POST   | `/vrp/jobs`                      | Create a VRP job                   |
| GET    | `/vrp/jobs/{job_id}`             | Get job status                     |
| GET    | `/vrp/jobs/{job_id}/preview`     | Preview routes with geometry       |
| GET    | `/vrp/jobs/{job_id}/metrics`     | Route distance/duration metrics    |
| POST   | `/vrp/jobs/{job_id}/finalize`    | Finalize a job                     |
| GET    | `/vrp/assignments`               | All assignments with geometry      |
| GET    | `/vrp/overview`                  | All tasks & fieldmen for map view  |
| WS     | `/ws/vrp/{job_id}`               | Real-time job progress updates     |

---

## Utility Scripts

Run these from the `backend/` directory:

### Seed sample data

```bash
cd backend
python scripts/seed_data.py
```

### Clear all data

```bash
cd backend
python scripts/clear_data.py
```

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| `OSRM nearest returned no waypoints` | OSRM map data not loaded or coordinates outside Philippines |
| `Connection refused` on port 5432 | PostgreSQL Docker container not running |
| `Connection refused` on port 6379 | Redis Docker container not running |
| Celery worker not picking up jobs | Ensure worker is started from `backend/` directory |
| Frontend shows "Connecting..." | Backend server not running on port 4000 |
| `ModuleNotFoundError` | Make sure you `cd backend` before running Python commands |
