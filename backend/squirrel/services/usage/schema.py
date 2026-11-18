#!/usr/bin/python
"""
Usage Schema
============

DDL for the append-only LLM usage ledger. Every successful provider call writes
one row so spend can be aggregated per user, provider, model, and feature.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


LLM_USAGE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS llm_usage_events (
    id                  BIGSERIAL        PRIMARY KEY,
    user_id             TEXT,
    provider            TEXT             NOT NULL,
    model               TEXT             NOT NULL,
    source              TEXT             NOT NULL DEFAULT 'agent',
    prompt_tokens       INTEGER          NOT NULL DEFAULT 0,
    completion_tokens   INTEGER          NOT NULL DEFAULT 0,
    total_tokens        INTEGER          NOT NULL DEFAULT 0,
    request_count       INTEGER          NOT NULL DEFAULT 1,
    estimated_cost_usd  NUMERIC(14, 6)   NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_created
    ON llm_usage_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider_created
    ON llm_usage_events (provider, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_source_created
    ON llm_usage_events (source, created_at DESC);
"""


USAGE_DDL_STATEMENTS = (LLM_USAGE_EVENTS_DDL,)
