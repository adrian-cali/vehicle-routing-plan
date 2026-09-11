# ──────────────────────────────────────────────────────────────────────
# Dockerfile for VRP Optimizer Backend (FastAPI + Celery)
# ──────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   Stage 1 (builder) — install Python dependencies into a venv
#   Stage 2 (runtime) — slim image with only the venv + source code
# ──────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Let Python find backend modules without PYTHONPATH hacks
ENV PYTHONPATH="/app/backend"

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8000/api/v1/health'); exit(0 if r.status_code==200 else 1)"

EXPOSE 8000

USER app

# Default: run the FastAPI server
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
