-- ============================================================
-- Migration: Add task completion & sequencing columns
-- to vrp_assignments table.
--
-- Safe to re-run — uses ADD COLUMN IF NOT EXISTS.
--
-- Usage:
--   docker exec -i vrp-postgres psql -U postgres -d vrp < backend/scripts/migrations/add_task_sequence.sql
-- ============================================================

-- Task completion status: 'pending' | 'completed'
ALTER TABLE vrp_assignments
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending';

-- Timestamp when the task was marked complete
ALTER TABLE vrp_assignments
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ DEFAULT NULL;

-- Index for fast fieldman task lookups by status
CREATE INDEX IF NOT EXISTS idx_vrp_assign_status
    ON vrp_assignments (fieldman_id, status);

-- Index for ordering by sequence within a fieldman
CREATE INDEX IF NOT EXISTS idx_vrp_assign_fm_seq
    ON vrp_assignments (fieldman_id, sequence);
