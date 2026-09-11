-- ============================================================
-- VRP Service — Database Initialization Script
-- Run this once on a fresh PostgreSQL database.
--
-- Usage:
--   docker exec -i postgres psql -U postgres < backend/scripts/init_db.sql
--
-- Or on PowerShell:
--   Get-Content backend/scripts/init_db.sql | docker exec -i postgres psql -U postgres
-- ============================================================

-- ── Tasks ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id               UUID PRIMARY KEY,
    address          TEXT,
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    priority         DOUBLE PRECISION DEFAULT 1.0,
    manual_priority  DOUBLE PRECISION DEFAULT NULL,
    service          INTEGER DEFAULT 0,
    task_type        TEXT DEFAULT 'credit_investigation',
    bank             TEXT,
    deleted_at       TIMESTAMPTZ DEFAULT NULL
);

-- ── Fieldmen ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fm_home_locations (
    user_id    UUID PRIMARY KEY,
    address    TEXT,
    home_lat   DOUBLE PRECISION,
    home_long  DOUBLE PRECISION,
    deleted_at TIMESTAMPTZ DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS fm_assigned_areas (
    user_id UUID REFERENCES fm_home_locations(user_id) ON DELETE CASCADE,
    area_id UUID,
    PRIMARY KEY (user_id, area_id)
);

-- ── VRP Jobs ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vrp_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status          TEXT,
    status_detail   TEXT,
    request_payload JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    finalized_at    TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ DEFAULT NULL
);

-- ── VRP Assignments ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vrp_assignments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID REFERENCES vrp_jobs(id) ON DELETE CASCADE,
    fieldman_id UUID REFERENCES fm_home_locations(user_id) ON DELETE SET NULL,
    task_id     UUID REFERENCES tasks(id) ON DELETE SET NULL,
    sequence    INTEGER,
    distance    DOUBLE PRECISION,
    duration    DOUBLE PRECISION,
    status      VARCHAR(20) DEFAULT 'pending',
    completed_at TIMESTAMPTZ DEFAULT NULL
);

-- ── VRP Job Routes ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vrp_job_routes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID NOT NULL REFERENCES vrp_jobs(id) ON DELETE CASCADE,
    fieldman_id UUID NOT NULL REFERENCES fm_home_locations(user_id) ON DELETE CASCADE,
    route_index INTEGER NOT NULL,
    distance    DOUBLE PRECISION,
    duration    DOUBLE PRECISION,
    start_lat   DOUBLE PRECISION,
    start_long  DOUBLE PRECISION,
    geometry    JSONB,
    tasks       JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Audit Log ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    action       TEXT NOT NULL,
    actor_id     TEXT,
    before_state JSONB,
    after_state  JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ═════════════════════════════════════════════════════════════
-- Indexes — added for enterprise-grade query performance
-- ═════════════════════════════════════════════════════════════

-- Tasks: frequently filtered/grouped by type, bank, and priority
CREATE INDEX IF NOT EXISTS idx_tasks_task_type    ON tasks (task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_bank         ON tasks (bank);
CREATE INDEX IF NOT EXISTS idx_tasks_priority     ON tasks (priority);
CREATE INDEX IF NOT EXISTS idx_tasks_coords       ON tasks (latitude, longitude);

-- Fieldmen: queried by user_id in joins
CREATE INDEX IF NOT EXISTS idx_fm_home_coords     ON fm_home_locations (home_lat, home_long);

-- FM assigned areas: join key
CREATE INDEX IF NOT EXISTS idx_fm_areas_user_id   ON fm_assigned_areas (user_id);
CREATE INDEX IF NOT EXISTS idx_fm_areas_area_id   ON fm_assigned_areas (area_id);

-- VRP Assignments: hot join columns\nCREATE INDEX IF NOT EXISTS idx_vrp_assign_job_id      ON vrp_assignments (job_id);
CREATE INDEX IF NOT EXISTS idx_vrp_assign_fieldman_id  ON vrp_assignments (fieldman_id);
CREATE INDEX IF NOT EXISTS idx_vrp_assign_task_id      ON vrp_assignments (task_id);

-- NOTE: indexes on status + sequence are created by the migration
-- scripts/migrations/add_task_sequence.sql after the columns are added.

-- VRP Jobs: status filter + ordering
CREATE INDEX IF NOT EXISTS idx_vrp_jobs_status     ON vrp_jobs (status);
CREATE INDEX IF NOT EXISTS idx_vrp_jobs_created_at ON vrp_jobs (created_at DESC);

-- VRP Job Routes: queried by job_id
CREATE INDEX IF NOT EXISTS idx_vrp_routes_job_id   ON vrp_job_routes (job_id);

-- Audit Log: queried by entity and time
CREATE INDEX IF NOT EXISTS idx_audit_entity        ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at    ON audit_log (created_at DESC);
