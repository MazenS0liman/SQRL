"""Tests for the shared constant vocabularies."""
from __future__ import annotations

import pytest

from squirrel.constants import connector as connector_constants
from squirrel.constants.connector import (
    CONNECTOR_TYPE_SPECS,
    ConnectorType,
    ConnectorTypeSpec,
    ConnectionStatus,
    get_connector_type_spec,
)
from squirrel.constants.etl import (
    AggregateFn,
    CastType,
    FilterOp,
    JoinHow,
    MLAlgorithm,
    MLMode,
    MLTask,
    PipelineRunStatus,
    PipelineStatus,
    RESERVED_SINK_TABLES,
    RESERVED_SOURCE_PREFIXES,
    ScheduleMode,
    SinkKind,
    SinkMode,
    SourceKind,
    StepType,
    TaskState,
    TransformOp,
    DASHBOARD_TASK_ID,
    ML_TASK_ID,
)
from squirrel.constants.jobs import JobStatus, JobType, NotificationChannel, NotificationType
from squirrel.constants.usage import (
    DEFAULT_PRICING,
    KNOWN_PROVIDERS,
    MODEL_PRICING,
    PROVIDER_LABELS,
    estimate_cost,
    resolve_pricing,
)
from squirrel.constants.workspace import DataType, WorkspaceStatus


# ——————————————————————————————————————————————————————————————
# ETL vocabulary


def test_pipeline_lifecycle_sets() -> None:
    assert PipelineStatus.ALL == {"draft", "pending_approval", "approved", "archived"}
    assert PipelineRunStatus.TERMINAL == {"succeeded", "failed", "cancelled"}
    assert TaskState.ALL == {"queued", "running", "success", "failed", "upstream_failed", "skipped"}
    assert DASHBOARD_TASK_ID == "__dashboard"
    assert ML_TASK_ID == "__ml"


def test_ml_and_schedule_vocabulary() -> None:
    assert MLTask.ALL == {"classification", "regression", "clustering"}
    assert MLAlgorithm.ALL == {"auto", "random_forest", "gradient_boosting", "linear"}
    assert MLMode.ALL == {"train", "retrain", "inference"}
    assert ScheduleMode.RECURRING == {"daily", "monthly", "yearly", "continuous"}
    assert ScheduleMode.MANUAL not in ScheduleMode.RECURRING


def test_step_and_transform_vocabulary() -> None:
    assert StepType.ALL == {"source", "transform", "sink"}
    assert SourceKind.ALL == {"connector", "file", "table"}
    assert SinkKind.ALL == {"postgres", "minio"}
    assert SinkMode.ALL == {"replace", "append"}
    assert TransformOp.ALL == {
        "select",
        "filter",
        "rename",
        "cast",
        "dedupe",
        "limit",
        "aggregate",
        "join",
        "fill_null",
    }
    assert JoinHow.ALL == {"inner", "left", "right", "full"}
    assert CastType.ALL == {
        "text",
        "integer",
        "bigint",
        "numeric",
        "double precision",
        "boolean",
        "date",
        "timestamptz",
    }


def test_filter_operator_partitions_cover_every_operator() -> None:
    partitions = FilterOp.SCALAR | FilterOp.LIST | FilterOp.NULLARY

    assert partitions == FilterOp.ALL
    assert FilterOp.SCALAR.isdisjoint(FilterOp.LIST)
    assert FilterOp.SCALAR.isdisjoint(FilterOp.NULLARY)
    assert FilterOp.LIST.isdisjoint(FilterOp.NULLARY)


def test_aggregate_functions_needing_a_column() -> None:
    assert AggregateFn.NEEDS_COLUMN == {"sum", "avg", "min", "max"}
    assert "count" not in AggregateFn.NEEDS_COLUMN


def test_reserved_tables_and_prefixes() -> None:
    assert "jobs" in RESERVED_SINK_TABLES
    assert "users" in RESERVED_SINK_TABLES
    assert "pipelines" in RESERVED_SINK_TABLES
    assert "users" in RESERVED_SOURCE_PREFIXES


# ——————————————————————————————————————————————————————————————
# Jobs


def test_job_and_notification_vocabulary() -> None:
    assert JobStatus.TERMINAL == {"succeeded", "failed", "cancelled"}
    assert JobStatus.ALL == {"queued", "running", "succeeded", "failed", "cancelled"}
    assert JobType.ALL == {"generic", "etl_pipeline", "model_retrain", "stream", "notification"}
    assert NotificationType.ALL == {
        "job_succeeded",
        "job_failed",
        "pipeline_run",
        "workspace_build",
        "notebook_run",
        "retrain",
        "stream",
        "system",
    }
    assert NotificationChannel.ALL == {"in_app", "email"}


# ——————————————————————————————————————————————————————————————
# Workspace


def test_workspace_vocabulary() -> None:
    assert WorkspaceStatus.CREATED == "created"
    assert WorkspaceStatus.COMPLETED == "completed"
    assert DataType.ALL == {"structured", "image", "text", "audio"}


# ——————————————————————————————————————————————————————————————
# Connectors


def test_connector_type_spec_lookup() -> None:
    postgres = get_connector_type_spec(ConnectorType.POSTGRES)

    assert postgres is CONNECTOR_TYPE_SPECS[ConnectorType.POSTGRES]
    assert postgres.field_keys() == ["host", "port", "database", "username", "password"]
    assert postgres.secret_keys() == ["password"]
    assert postgres.required_keys() == ["host", "database", "username", "password"]


def test_connector_type_spec_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown connector type"):
        get_connector_type_spec("not-a-connector")


def test_every_connector_type_has_a_spec() -> None:
    assert set(CONNECTOR_TYPE_SPECS) == ConnectorType.ALL
    for spec in CONNECTOR_TYPE_SPECS.values():
        assert isinstance(spec, ConnectorTypeSpec)
        assert spec.label
        assert spec.description


def test_connector_status_values() -> None:
    assert ConnectionStatus.ALL == {"untested", "connected", "error"}
    assert connector_constants.ConnectorType.MYSQL == "mysql"


# ——————————————————————————————————————————————————————————————
# Usage pricing


def test_pricing_resolves_longest_key_first() -> None:
    assert resolve_pricing("models/gemini-2.5-flash") == MODEL_PRICING["gemini-2.5-flash"]
    assert resolve_pricing("gpt-4o-mini-2024") == MODEL_PRICING["gpt-4o-mini"]
    assert resolve_pricing("GPT-4O") == MODEL_PRICING["gpt-4o"]


def test_pricing_unknown_or_empty_model_is_free() -> None:
    assert resolve_pricing(None) == DEFAULT_PRICING
    assert resolve_pricing("") == DEFAULT_PRICING
    assert resolve_pricing("some-unlisted-model") == DEFAULT_PRICING


def test_estimate_cost_uses_per_million_pricing() -> None:
    # 1M prompt tokens of gemini-2.5-flash at $0.30/M, 1M completion at $2.50/M.
    cost = estimate_cost("gemini", "gemini-2.5-flash", 1_000_000, 1_000_000)

    assert cost == pytest.approx(2.80)


def test_estimate_cost_clamps_negative_tokens_and_rounds() -> None:
    assert estimate_cost(None, "gemini-2.5-flash", -100, -100) == 0.0
    assert estimate_cost(None, "gemini-2.5-flash", 1, 0) == 0.0
    assert estimate_cost(None, "gpt-4o", 1_000, 1_000) == pytest.approx(0.0125)


def test_usage_metadata_is_consistent() -> None:
    assert set(KNOWN_PROVIDERS) == set(PROVIDER_LABELS)
    assert all(key == key.lower() for key in MODEL_PRICING)
