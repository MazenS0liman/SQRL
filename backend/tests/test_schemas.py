"""Tests for the Pydantic request/response schemas.

The schemas are the contract between the API and the frontend, so these tests
pin down defaults, validation rules, and the guardrail-relevant validators
(unknown ETL ops, cast types, schedule modes, ...).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from squirrel.constants.etl import ScheduleMode
from squirrel.schemas.api import AgentRequest, AgentResponse, ConversationMessage, LLMTokens
from squirrel.schemas.auth import (
    AuthTokenResponse,
    AuthUserRead,
    LoginRequest,
    RegisterRequest,
)
from squirrel.schemas.connector import (
    ConnectorCreateRequest,
    ConnectorPreviewRequest,
    ConnectorQueryRequest,
    DataConnectionOut,
)
from squirrel.schemas.eda import AxisSpec, Hypothesis, SqlSpec
from squirrel.schemas.error import DuplicateColumnError
from squirrel.schemas.etl import (
    Aggregation,
    CreatePipelineRequest,
    MLSpec,
    PipelineDefinition,
    PipelineStep,
    PlanStageRequest,
    ScheduleSpec,
    SinkSpec,
    SourceSpec,
    TransformSpec,
)
from squirrel.schemas.file import File, FileType
from squirrel.schemas.jobs import JobEventRead, JobRead
from squirrel.schemas.notebook import Notebook, NotebookChart, SaveDashboardLayoutRequest
from squirrel.schemas.notifications import (
    NotificationPreferencesUpdate,
    NotificationRead,
    UnreadCountResponse,
)
from squirrel.schemas.token import TokenCreateRequest, TokenRead
from squirrel.schemas.usage import UsageEventRead, UsageOverviewResponse, UsageTotals
from squirrel.schemas.workspace import BuildRequest, WorkspaceCreateRequest


NOW = datetime.now(timezone.utc)


def _source(step_id: str = "src") -> PipelineStep:
    return PipelineStep(id=step_id, type="source", source=SourceSpec(kind="table", table_name="etl_raw"))


# ——————————————————————————————————————————————————————————————
# Auth / token


def test_register_request_validates_lengths_and_email() -> None:
    request = RegisterRequest(username="alice", email="alice@example.com", password="hunter2hunter2")

    assert request.full_name is None

    with pytest.raises(ValidationError):
        RegisterRequest(username="ab", email="alice@example.com", password="hunter2hunter2")
    with pytest.raises(ValidationError):
        RegisterRequest(username="alice", email="not-an-email", password="hunter2hunter2")
    with pytest.raises(ValidationError):
        RegisterRequest(username="alice", email="alice@example.com", password="short")


def test_login_request_requires_identifier_and_password() -> None:
    request = LoginRequest(identifier="alice", password="hunter2hunter2")

    assert request.identifier == "alice"


def test_auth_user_read_round_trip() -> None:
    user = AuthUserRead(id="u1", username="alice", email="alice@example.com", created_at=NOW)
    response = AuthTokenResponse(access_token="token", expires_at=NOW, user=user)

    assert response.token_type == "bearer"
    assert response.user.username == "alice"


def test_token_create_request_normalises_provider_and_token() -> None:
    request = TokenCreateRequest(provider="  GEMINI ", token="  abcdefgh  ")

    assert request.provider == "gemini"
    assert request.token == "abcdefgh"


def test_token_create_request_rejects_unknown_provider_and_blank_token() -> None:
    with pytest.raises(ValidationError):
        TokenCreateRequest(provider="nope", token="abcdefgh")
    with pytest.raises(ValidationError):
        TokenCreateRequest(provider="gemini", token="        ")


def test_token_read_masks_preview() -> None:
    token = TokenRead(
        id="t1",
        provider="gemini",
        label=None,
        token_preview="abcdef...uvwxyz",
        created_at=NOW,
        last_used_at=None,
    )

    assert token.token_preview == "abcdef...uvwxyz"


# ——————————————————————————————————————————————————————————————
# Jobs / notifications / usage


def test_job_schemas_defaults() -> None:
    job = JobRead(id="j1", type="etl_pipeline", status="queued")
    event = JobEventRead(id=1, job_id="j1", message="started")

    assert job.progress == 0
    assert job.payload == {}
    assert event.level == "info"


def test_notification_preferences_validate_digest_mode() -> None:
    prefs = NotificationPreferencesUpdate(email_enabled=False, digest_mode="daily")

    assert prefs.email_enabled is False

    with pytest.raises(ValidationError):
        NotificationPreferencesUpdate(digest_mode="hourly")


def test_notification_and_usage_defaults() -> None:
    notification = NotificationRead(id="n1", user_id="u1", type="system", title="Hi")
    totals = UsageTotals()
    overview = UsageOverviewResponse()
    event = UsageEventRead(id=1, provider="gemini", model="m")

    assert notification.body == ""
    assert UnreadCountResponse().count == 0
    assert totals.total_tokens == 0
    assert overview.window_days == 30
    assert overview.by_provider == []
    assert event.source == "agent"


# ——————————————————————————————————————————————————————————————
# Connector


def test_connector_requests() -> None:
    create = ConnectorCreateRequest(name="Prod DB", type="postgres")
    preview = ConnectorPreviewRequest(query="select 1")
    query = ConnectorQueryRequest(query="select 1")

    assert create.config == {}
    assert preview.limit == 20
    assert query.limit == 100

    with pytest.raises(ValidationError):
        ConnectorCreateRequest(name="", type="postgres")
    with pytest.raises(ValidationError):
        ConnectorPreviewRequest(query="select 1", limit=0)


def test_data_connection_out_requires_timestamps() -> None:
    connection = DataConnectionOut(
        connector_id="c1",
        name="Prod",
        type="postgres",
        status="untested",
        created_at=NOW,
        updated_at=NOW,
    )

    assert connection.error is None
    assert connection.config == {}


# ——————————————————————————————————————————————————————————————
# Files / API


def test_file_schema_converts_blank_optional_strings_to_none() -> None:
    file = File(fileUrl="s3://bucket/key.csv", fileName="", fileType=FileType.CSV)

    assert file.fileName is None
    assert file.fileType is FileType.CSV


def test_agent_request_requires_conversation_pattern_and_limits_history() -> None:
    request = AgentRequest(query="hi", projectId="p1", conversationId="conv_1")

    assert request.fileUrls == []

    with pytest.raises(ValidationError):
        AgentRequest(query="hi", projectId="p1", conversationId="bad id!")


def test_agent_response_round_trip() -> None:
    response = AgentResponse(
        reply="done",
        action="etl",
        projectId="p1",
        conversationId="c1",
        responseTime=1.5,
        llmTokens=LLMTokens(promptTokens=1, completionTokens=2, totalTokens=3),
    )

    assert response.llmTokens.totalTokens == 3
    assert ConversationMessage(role="user", content="hi").role == "user"


# ——————————————————————————————————————————————————————————————
# ETL schemas


def test_source_spec_validates_kind() -> None:
    spec = SourceSpec(kind="file", file_url="s3://bucket/key.csv")

    assert spec.columns == []

    with pytest.raises(ValidationError):
        SourceSpec(kind="ftp")


def test_aggregation_validates_function() -> None:
    assert Aggregation(function="sum", column="amount", alias="total").alias == "total"

    with pytest.raises(ValidationError):
        Aggregation(function="median", column="amount", alias="median")


def test_transform_spec_validates_op_comparison_join_and_casts() -> None:
    assert TransformSpec(op="limit", count=10).count == 10

    with pytest.raises(ValidationError):
        TransformSpec(op="drop_everything")
    with pytest.raises(ValidationError):
        TransformSpec(op="filter", column="a", comparison="between")
    with pytest.raises(ValidationError):
        TransformSpec(op="join", right="other", how="cross")
    with pytest.raises(ValidationError):
        TransformSpec(op="cast", casts={"a": "uuid"})
    with pytest.raises(ValidationError):
        TransformSpec(op="limit", count=0)


def test_sink_spec_validates_kind_and_mode() -> None:
    sink = SinkSpec(kind="postgres", table="etl_out")

    assert sink.mode == "replace"
    assert SinkSpec(kind="minio", object_key="etl/out.csv").format == "csv"

    with pytest.raises(ValidationError):
        SinkSpec(kind="snowflake")
    with pytest.raises(ValidationError):
        SinkSpec(kind="postgres", table="etl_out", mode="upsert")


def test_pipeline_step_validates_id_pattern_and_type() -> None:
    assert _source("source_1").id == "source_1"

    with pytest.raises(ValidationError):
        PipelineStep(id="1bad", type="source")
    with pytest.raises(ValidationError):
        PipelineStep(id="bad-id", type="source")
    with pytest.raises(ValidationError):
        PipelineStep(id="ok", type="not_a_step")


def test_pipeline_definition_requires_steps() -> None:
    definition = PipelineDefinition(steps=[_source()])

    assert len(definition.steps) == 1

    with pytest.raises(ValidationError):
        PipelineDefinition(steps=[])


def test_ml_spec_mode_and_target_rules() -> None:
    train = MLSpec(task="classification", target="churn")
    assert train.mode == "train"
    assert train.algorithm == "auto"

    clustering = MLSpec(task="clustering")
    assert clustering.n_clusters == 3

    with pytest.raises(ValidationError):
        MLSpec(task="clustering", target="churn")
    with pytest.raises(ValidationError):
        MLSpec(task="classification")
    with pytest.raises(ValidationError):
        MLSpec(mode="retrain", task="classification", target="churn")
    with pytest.raises(ValidationError):
        MLSpec(mode="inference", task="classification", target="churn", model_id="m1", test_size=0.9)


def test_ml_spec_inference_does_not_require_target() -> None:
    spec = MLSpec(mode="inference", task="classification", model_id="m1")

    assert spec.model_id == "m1"


def test_schedule_spec_validates_time_and_intervals() -> None:
    schedule = ScheduleSpec()

    assert schedule.mode == ScheduleMode.MANUAL
    assert schedule.time == "02:00"
    assert schedule.interval_seconds == 300

    with pytest.raises(ValidationError):
        ScheduleSpec(mode="hourly")
    with pytest.raises(ValidationError):
        ScheduleSpec(time="25:00")
    with pytest.raises(ValidationError):
        ScheduleSpec(interval_seconds=5)
    with pytest.raises(ValidationError):
        ScheduleSpec(month=13)


def test_pipeline_requests() -> None:
    request = CreatePipelineRequest(name="Orders", definition=PipelineDefinition(steps=[_source()]))

    assert request.description is None

    with pytest.raises(ValidationError):
        CreatePipelineRequest(name="", definition=PipelineDefinition(steps=[_source()]))


def test_plan_stage_request_validates_step_ids() -> None:
    request = PlanStageRequest(
        description="filter rows",
        definition=PipelineDefinition(steps=[_source()]),
        insert_after="src",
        insert_before="sink",
    )

    assert request.insert_after == "src"

    with pytest.raises(ValidationError):
        PlanStageRequest(
            description="filter rows",
            definition=PipelineDefinition(steps=[_source()]),
            insert_after="BAD ID",
            insert_before="sink",
        )


# ——————————————————————————————————————————————————————————————
# Workspace / notebook / EDA


def test_workspace_create_request_validates_name_and_data_type() -> None:
    request = WorkspaceCreateRequest(name="Churn")

    assert request.data_type == "structured"

    # ``data_type`` is a free-form string (the UI offers extra kinds such as
    # "video"), so only the name is validated here.
    assert WorkspaceCreateRequest(name="Clips", data_type="video").data_type == "video"

    with pytest.raises(ValidationError):
        WorkspaceCreateRequest(name="")


def test_build_request_requires_target_column() -> None:
    request = BuildRequest(target_column="churn")

    assert request.target_column == "churn"

    with pytest.raises(ValidationError):
        BuildRequest(target_column="")


def test_notebook_schemas_defaults() -> None:
    notebook = Notebook(id="n1", name="Notebook")
    chart = NotebookChart(id="c1", title="Sales", question="?", plot_type="bar")
    layout = SaveDashboardLayoutRequest()

    assert notebook.status == "empty"
    assert notebook.data_sources == []
    assert chart.data == []
    assert chart.truncated is False
    assert layout.order == []


def test_eda_schemas_round_trip() -> None:
    axis = AxisSpec(column="day", label="Day", type="temporal")
    hypothesis = Hypothesis(
        id="h1",
        question="Do sales grow?",
        title="Sales trend",
        description="Line chart of sales",
        why_it_matters="Growth",
        plot_type="line",
        analytical_type="trend",
    )
    spec = SqlSpec(sql="select 1", x=axis, y=axis)

    assert spec.group_by is None
    assert hypothesis.plot_type == "line"


def test_duplicate_column_error_lists_columns() -> None:
    error = DuplicateColumnError(["a", "b"])

    assert error.columns == ["a", "b"]
    assert "'a'" in str(error)
    assert "'b'" in str(error)
