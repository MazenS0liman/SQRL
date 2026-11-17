#!/usr/bin/python
"""
ETL Constants
=============

Shared vocabulary for the Phase 1 ETL pipeline feature.

The pipeline definition is a small, constrained DAG DSL. LLM output is only
ever accepted after it has been parsed into these types and validated by
:mod:`squirrel.services.pipelines.compiler`; nothing in a definition is ever
executed as arbitrary Python.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


# ——————————————————————————————————————————————————————————————
# Pipeline lifecycle

class PipelineStatus:
    """Lifecycle states for a stored pipeline definition."""

    DRAFT            = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED         = "approved"
    ARCHIVED         = "archived"

    ALL = {DRAFT, PENDING_APPROVAL, APPROVED, ARCHIVED}


class PipelineRunStatus:
    """Lifecycle states for one execution of a pipeline."""

    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    CANCELLED = "cancelled"

    ALL = {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED}
    TERMINAL = {SUCCEEDED, FAILED, CANCELLED}


class MLTask:
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    CLUSTERING = "clustering"

    ALL = {CLASSIFICATION, REGRESSION, CLUSTERING}


class MLAlgorithm:
    AUTO = "auto"
    RANDOM_FOREST = "random_forest"
    GRADIENT_BOOSTING = "gradient_boosting"
    LINEAR = "linear"

    ALL = {AUTO, RANDOM_FOREST, GRADIENT_BOOSTING, LINEAR}


class TaskState:
    """Per-step state reported in ``pipeline_runs.result['task_states']``."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    UPSTREAM_FAILED = "upstream_failed"
    SKIPPED = "skipped"

    ALL = {QUEUED, RUNNING, SUCCESS, FAILED, UPSTREAM_FAILED, SKIPPED}


DASHBOARD_TASK_ID = "__dashboard"
ML_TASK_ID = "__ml"


# ——————————————————————————————————————————————————————————————
# Step vocabulary

class StepType:
    """The three kinds of node in a pipeline DAG."""

    SOURCE    = "source"
    TRANSFORM = "transform"
    SINK      = "sink"

    ALL = {SOURCE, TRANSFORM, SINK}


class SourceKind:
    """Where a source step reads its rows from."""

    CONNECTOR = "connector"
    FILE      = "file"
    TABLE     = "table"

    ALL = {CONNECTOR, FILE, TABLE}


class SinkKind:
    """Where a pipeline writes its final result."""

    POSTGRES = "postgres"
    MINIO    = "minio"

    ALL = {POSTGRES, MINIO}


class SinkMode:
    """How a Postgres sink writes its target table."""

    REPLACE = "replace"
    APPEND  = "append"

    ALL = {REPLACE, APPEND}


class TransformOp:
    """The closed set of transformations the executor understands."""

    SELECT    = "select"
    FILTER    = "filter"
    FILL_NULL = "fill_null"
    RENAME    = "rename"
    CAST      = "cast"
    DEDUPE    = "dedupe"
    LIMIT     = "limit"
    AGGREGATE = "aggregate"
    JOIN      = "join"

    ALL = {SELECT, FILTER, RENAME, CAST, DEDUPE, LIMIT, AGGREGATE, JOIN, FILL_NULL}

class FilterOp:
    """Comparison operators accepted by the ``filter`` transform."""

    EQ        = "eq"
    NE        = "ne"
    GT        = "gt"
    GTE       = "gte"
    LT        = "lt"
    LTE       = "lte"
    IN        = "in"
    NOT_IN    = "not_in"
    IS_NULL   = "is_null"
    NOT_NULL  = "not_null"
    LIKE      = "like"
    CONTAINS  = "contains"

    ALL = {
        EQ, NE, GT, GTE, LT, LTE, IN, NOT_IN,
        IS_NULL, NOT_NULL, LIKE, CONTAINS,
    }

    #: Operators that require a scalar ``value``.
    SCALAR = {EQ, NE, GT, GTE, LT, LTE, LIKE, CONTAINS}
    #: Operators that require a non-empty ``values`` list.
    LIST = {IN, NOT_IN}
    #: Operators that take no value.
    NULLARY = {IS_NULL, NOT_NULL}


class AggregateFn:
    """Aggregate functions accepted by the ``aggregate`` transform."""

    COUNT = "count"
    SUM   = "sum"
    AVG   = "avg"
    MIN   = "min"
    MAX   = "max"

    ALL = {COUNT, SUM, AVG, MIN, MAX}
    #: Functions that require a numeric column.
    NEEDS_COLUMN = {SUM, AVG, MIN, MAX}


class JoinHow:
    """Join types accepted by the ``join`` transform."""

    INNER = "inner"
    LEFT  = "left"
    RIGHT = "right"
    FULL  = "full"

    ALL = {INNER, LEFT, RIGHT, FULL}


class CastType:
    """Whitelisted SQL target types for the ``cast`` transform."""

    TEXT        = "text"
    INTEGER     = "integer"
    BIGINT      = "bigint"
    NUMERIC     = "numeric"
    DOUBLE      = "double precision"
    BOOLEAN     = "boolean"
    DATE        = "date"
    TIMESTAMPTZ = "timestamptz"

    ALL = {TEXT, INTEGER, BIGINT, NUMERIC, DOUBLE, BOOLEAN, DATE, TIMESTAMPTZ}


# ——————————————————————————————————————————————————————————————
# Guardrails

#: Tables the ETL feature must never write to, even when an allow-list entry
#: is misconfigured. These back the platform itself.
RESERVED_SINK_TABLES = frozenset(
    {
        "jobs",
        "job_events",
        "notifications",
        "notification_preferences",
        "pipelines",
        "pipeline_runs",
        "users",
        "user_tokens",
        "file",
        "workspaces",
        "pipeline_runs",
    }
)

#: Prefixes of platform-owned tables that ETL sources must not read by default.
RESERVED_SOURCE_PREFIXES = (
    "jobs",
    "job_events",
    "notifications",
    "notification_preferences",
    "users",
    "user_tokens",
    "file",
)
