#!/usr/bin/python
"""
Pipeline Schema
===============

DDL for the Phase 1 ETL tables. Kept beside the service so the idempotent
bootstrap and the Alembic migration create exactly the same schema.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


PIPELINES_DDL = """
CREATE TABLE IF NOT EXISTS pipelines (
    id              TEXT             PRIMARY KEY,
    owner_user_id   TEXT,
    name            TEXT             NOT NULL,
    description     TEXT,
    status          TEXT             NOT NULL DEFAULT 'pending_approval',
    definition      JSONB            NOT NULL,
    version         INTEGER          NOT NULL DEFAULT 1,
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipelines_owner_created
    ON pipelines (owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipelines_status
    ON pipelines (status);
"""


PIPELINE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT             PRIMARY KEY,
    pipeline_id     TEXT             NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    owner_user_id   TEXT,
    job_id          TEXT             REFERENCES jobs(id) ON DELETE SET NULL,
    status          TEXT             NOT NULL DEFAULT 'queued',
    dry_run         BOOLEAN          NOT NULL DEFAULT FALSE,
    row_count       INTEGER,
    error           TEXT,
    result          JSONB,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_created
    ON pipeline_runs (pipeline_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_owner_created
    ON pipeline_runs (owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
    ON pipeline_runs (status);
"""


PIPELINE_DDL_STATEMENTS = (PIPELINES_DDL, PIPELINE_RUNS_DDL)
