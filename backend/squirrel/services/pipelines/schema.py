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
    schedule_mode   TEXT             NOT NULL DEFAULT 'manual',
    schedule_enabled BOOLEAN         NOT NULL DEFAULT FALSE,
    schedule_time   TEXT,
    schedule_day    INTEGER,
    schedule_month  INTEGER,
    schedule_interval_seconds INTEGER,
    next_run_at     TIMESTAMPTZ,
    last_scheduled_at TIMESTAMPTZ,
    dashboard_notebook_id TEXT,
    dashboard_cell_id TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipelines_owner_created
    ON pipelines (owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipelines_status
    ON pipelines (status);
"""


#: Idempotent upgrades for deployments that already created ``pipelines``
#: before scheduling and pipeline-owned dashboards existed.
PIPELINE_ALTER_DDL = """
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_mode TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_time TEXT;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_day INTEGER;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_month INTEGER;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS schedule_interval_seconds INTEGER;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS last_scheduled_at TIMESTAMPTZ;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS dashboard_notebook_id TEXT;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS dashboard_cell_id TEXT;
CREATE INDEX IF NOT EXISTS idx_pipelines_due
    ON pipelines (next_run_at) WHERE schedule_enabled;
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


#: Registry of models trained by pipeline runs. Artifacts stay in MinIO; this
#: table is the lookup the retrain/inference modes use.
ETL_MODELS_DDL = """
CREATE TABLE IF NOT EXISTS etl_models (
    id              TEXT             PRIMARY KEY,
    pipeline_id     TEXT             NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    owner_user_id   TEXT,
    name            TEXT             NOT NULL,
    task            TEXT             NOT NULL,
    algorithm       TEXT,
    target          TEXT,
    features        JSONB            NOT NULL DEFAULT '[]'::jsonb,
    metrics         JSONB,
    details         JSONB,
    model_key       TEXT             NOT NULL,
    file_url        TEXT,
    run_id          TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_etl_models_pipeline_created
    ON etl_models (pipeline_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_etl_models_owner_created
    ON etl_models (owner_user_id, created_at DESC);
"""


#: Idempotent upgrade for ``etl_models`` rows created before model cards
#: (input schema / preprocessing / training history) were persisted.
ETL_MODELS_ALTER_DDL = """
ALTER TABLE etl_models ADD COLUMN IF NOT EXISTS details JSONB;
"""


PIPELINE_DDL_STATEMENTS = (
    PIPELINES_DDL,
    PIPELINE_ALTER_DDL,
    PIPELINE_RUNS_DDL,
    ETL_MODELS_DDL,
    ETL_MODELS_ALTER_DDL,
)
