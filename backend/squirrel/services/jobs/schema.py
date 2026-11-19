#!/usr/bin/python
"""
Job Schema
==========

DDL for the durable job tables. Kept in one place so both the service's
idempotent bootstrap and the Alembic migration create exactly the same schema.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT             PRIMARY KEY,
    owner_user_id   TEXT,
    type            TEXT             NOT NULL,
    status          TEXT             NOT NULL DEFAULT 'queued',
    progress        INTEGER          NOT NULL DEFAULT 0,
    payload         JSONB            NOT NULL DEFAULT '{}'::jsonb,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner_created
    ON jobs (owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs (status);
"""


JOB_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS job_events (
    id          BIGSERIAL        PRIMARY KEY,
    job_id      TEXT             NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    level       TEXT             NOT NULL DEFAULT 'info',
    message     TEXT             NOT NULL,
    data        JSONB,
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_job_events_job
    ON job_events (job_id, id);
"""


JOB_DDL_STATEMENTS = (JOBS_DDL, JOB_EVENTS_DDL)
