"""Tests for the Phase 1 ETL pipeline service."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from squirrel.constants.etl import PipelineRunStatus, PipelineStatus
from squirrel.core.config import settings
from squirrel.schemas.etl import PipelineDefinition, SourceSpec
from squirrel.services.pipelines.PipelineService import (
    PipelineNotApprovedError,
    PipelineNotFoundError,
    PipelineRunNotFoundError,
    PipelineService,
)
from squirrel.services.pipelines.compiler import PipelineValidationError
from tests.fakes import FakeDB


# ——————————————————————————————————————————————————————————————
# Helpers


def _definition(
    *,
    source_table: str = "etl_source",
    sink_table: str = "etl_out",
    sink_kind: str = "postgres",
    object_key: str | None = None,
    materialize: dict | None = None,
) -> PipelineDefinition:
    sink: dict = {"kind": sink_kind}
    if sink_kind == "postgres":
        sink["table"] = sink_table
    else:
        sink["object_key"] = object_key or "etl/out.csv"
    payload: dict = {
        "steps": [
            {
                "id": "src",
                "type": "source",
                "source": {"kind": "table", "table_name": source_table},
            },
            {"id": "out", "type": "sink", "input": "src", "sink": sink},
        ]
    }
    if materialize is not None:
        payload["materialize"] = materialize
    return PipelineDefinition.model_validate(payload)


def _pipeline_row(
    definition: PipelineDefinition | None = None,
    *,
    status: str = PipelineStatus.PENDING_APPROVAL,
    name: str = "nightly_orders",
    pipeline_id: str = "pipeline-1",
) -> dict:
    return {
        "id": pipeline_id,
        "owner_user_id": "user-1",
        "name": name,
        "description": None,
        "status": status,
        "version": 1,
        "definition": json.dumps((definition or _definition()).model_dump()),
        "approved_at": None,
        "approved_by": None,
        "created_at": None,
        "updated_at": None,
    }


def _run_row(
    *,
    dry_run: bool = False,
    status: str = PipelineRunStatus.QUEUED,
    result: dict | None = None,
) -> dict:
    return {
        "id": "run-1",
        "pipeline_id": "pipeline-1",
        "owner_user_id": "user-1",
        "job_id": "job-1",
        "status": status,
        "dry_run": dry_run,
        "row_count": None,
        "error": None,
        "result": result,
        "started_at": None,
        "finished_at": None,
        "created_at": None,
        "updated_at": None,
    }


class FakeJobService:
    """JobService double that records created jobs."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_job(
        self,
        job_type: str,
        owner_user_id: str | None = None,
        payload: dict | None = None,
        status: str | None = None,
    ) -> dict:
        self.created.append(
            {
                "job_type": job_type,
                "owner_user_id": owner_user_id,
                "payload": payload or {},
            }
        )
        return {"id": "job-1"}


def _service(db: FakeDB, **kwargs) -> PipelineService:
    return PipelineService(db=db, jobs=kwargs.pop("jobs", FakeJobService()), **kwargs)


# ——————————————————————————————————————————————————————————————
# CRUD


def test_create_pipeline_validates_and_stores_pending_approval() -> None:
    db = FakeDB()
    stored = _pipeline_row()
    db.queue({"rows": [stored]})

    result = _service(db).create_pipeline(
        "user-1", "nightly_orders", _definition(), "Nightly order export"
    )

    assert result == stored
    insert = db.last_query
    assert "INSERT INTO pipelines" in insert.sql
    assert insert.params["status"] == PipelineStatus.PENDING_APPROVAL
    assert insert.params["owner_user_id"] == "user-1"
    assert json.loads(insert.params["definition"])["steps"][0]["id"] == "src"


def test_create_pipeline_rejects_invalid_definition_before_touching_db() -> None:
    db = FakeDB()
    bad = PipelineDefinition.model_validate(
        {
            "steps": [
                {
                    "id": "src",
                    "type": "source",
                    "source": {"kind": "table", "table_name": "etl_source"},
                }
            ]
        }
    )

    with pytest.raises(PipelineValidationError, match="exactly one sink"):
        _service(db).create_pipeline("user-1", "bad", bad)

    assert db.queries == []


def test_get_pipeline_raises_when_missing() -> None:
    db = FakeDB().queue({"rows": []})

    with pytest.raises(PipelineNotFoundError):
        _service(db).get_pipeline("pipeline-1", "user-1")


def test_update_definition_bumps_version_and_resets_approval() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED)]},
        {"rows": [_pipeline_row()]},
    )

    _service(db).update_pipeline(
        "pipeline-1",
        "user-1",
        definition=_definition(sink_table="etl_out_v2"),
    )

    update = db.last_query
    assert "version = version + 1" in update.sql
    assert "approved_at = NULL" in update.sql
    assert "approved_by = NULL" in update.sql
    assert update.params["status"] == PipelineStatus.PENDING_APPROVAL
    assert "etl_out_v2" in update.params["definition"]


def test_update_without_changes_returns_existing_row() -> None:
    db = FakeDB().queue({"rows": [_pipeline_row()]})
    service = _service(db)

    result = service.update_pipeline("pipeline-1", "user-1")

    assert result["id"] == "pipeline-1"
    assert len(db.queries) == 1


def test_approve_pipeline_records_approver() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_pipeline_row()]},
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED)]},
    )

    _service(db).approve_pipeline("pipeline-1", "user-1", approved_by="reviewer-1")

    update = db.last_query
    assert "approved_at = NOW()" in update.sql
    assert update.params["status"] == PipelineStatus.APPROVED
    assert update.params["approved_by"] == "reviewer-1"


def test_list_pipelines_scopes_to_owner() -> None:
    db = FakeDB().queue({"rows": [_pipeline_row()]})

    rows = _service(db).list_pipelines("user-1", limit=10, offset=5)

    assert rows[0]["id"] == "pipeline-1"
    query = db.last_query
    assert "WHERE owner_user_id = %(owner_user_id)s" in query.sql
    assert query.params == {"limit": 10, "offset": 5, "owner_user_id": "user-1"}


def test_delete_pipeline_checks_ownership_then_deletes() -> None:
    db = FakeDB()
    db.queue({"rows": [_pipeline_row()]}, {"rows": []})

    _service(db).delete_pipeline("pipeline-1", "user-1")

    assert "SELECT * FROM pipelines" in db.queries[0].sql
    assert "DELETE FROM pipelines" in db.last_query.sql


# ——————————————————————————————————————————————————————————————
# Run creation / approval guard


def test_create_run_requires_approval_for_production() -> None:
    db = FakeDB().queue({"rows": [_pipeline_row()]})
    jobs = FakeJobService()
    service = _service(db, jobs=jobs)

    with pytest.raises(PipelineNotApprovedError):
        service.create_run("pipeline-1", "user-1", dry_run=False)

    assert jobs.created == []
    assert len(db.queries) == 1


def test_create_dry_run_is_allowed_before_approval() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_pipeline_row()]},
        {"rows": [_run_row(dry_run=True)]},
    )
    jobs = FakeJobService()

    run = _service(db, jobs=jobs).create_run(
        "pipeline-1", "user-1", dry_run=True, enqueue=False
    )

    assert run["dry_run"] is True
    assert jobs.created[0]["job_type"] == "etl_pipeline"
    assert jobs.created[0]["payload"]["pipeline_id"] == "pipeline-1"
    assert jobs.created[0]["payload"]["pipeline_name"] == "nightly_orders"
    assert jobs.created[0]["payload"]["dry_run"] is True
    insert = db.last_query
    assert "INSERT INTO pipeline_runs" in insert.sql
    assert jobs.created[0]["payload"]["run_id"] == insert.params["id"]
    assert insert.params["status"] == PipelineRunStatus.QUEUED
    assert insert.params["job_id"] == "job-1"


def test_create_production_run_when_approved() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED)]},
        {"rows": [_run_row()]},
    )
    jobs = FakeJobService()

    _service(db, jobs=jobs).create_run(
        "pipeline-1", "user-1", dry_run=False, enqueue=False
    )

    assert jobs.created[0]["payload"]["dry_run"] is False


def test_get_run_raises_when_missing() -> None:
    db = FakeDB().queue({"rows": []})

    with pytest.raises(PipelineRunNotFoundError):
        _service(db).get_run("run-1", "user-1")


# ——————————————————————————————————————————————————————————————
# Execution


def test_execute_dry_run_resolves_schema_without_writing() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_run_row(dry_run=True)]},
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED)]},
        {"rows": [_run_row(dry_run=True, status=PipelineRunStatus.RUNNING)]},
        {"rows": [{"column_name": "id"}, {"column_name": "amount"}]},
        {"rows": [_run_row(dry_run=True, status=PipelineRunStatus.SUCCEEDED)]},
    )

    result = _service(db).execute_run("run-1", job_id="job-1")

    assert result["dry_run"] is True
    assert result["columns"] == ["id", "amount"]
    assert result["sink"]["kind"] == "postgres"
    # Only information_schema was read; no sink DDL was issued.
    assert not any("CREATE TABLE" in query.sql for query in db.queries)
    assert not any("DROP TABLE" in query.sql for query in db.queries)
    assert db.queries[-1].params["status"] == PipelineRunStatus.SUCCEEDED


def test_execute_production_table_source_replaces_sink() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_run_row()]},
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED)]},
        {"rows": [_run_row(status=PipelineRunStatus.RUNNING)]},
        {"rows": [{"column_name": "id"}, {"column_name": "amount"}]},
        {"rows": []},
        {"rows": []},
        {"rows": [{"cnt": 3}]},
        {"rows": [_run_row(status=PipelineRunStatus.SUCCEEDED)]},
    )

    result = _service(db).execute_run("run-1", job_id="job-1")

    assert result["row_count"] == 3
    assert result["sink"]["table"] == "etl_out"
    sqls = [query.sql for query in db.queries]
    assert any('DROP TABLE IF EXISTS "etl_out"' in sql for sql in sqls)
    assert any('CREATE TABLE "etl_out" AS WITH' in sql for sql in sqls)
    assert any('SELECT COUNT(*) AS cnt FROM "etl_out"' in sql for sql in sqls)
    assert db.queries[-1].params["status"] == PipelineRunStatus.SUCCEEDED


def test_execute_marks_run_failed_when_source_table_is_reserved() -> None:
    db = FakeDB()
    db.queue(
        {"rows": [_run_row()]},
        {"rows": [_pipeline_row(status=PipelineStatus.APPROVED, )]},
        {"rows": [_run_row(status=PipelineRunStatus.RUNNING)]},
        {"rows": [_run_row(status=PipelineRunStatus.FAILED)]},
    )
    # Replace the definition with one that reads a platform table.
    db.results[1] = {
        "rows": [
            _pipeline_row(
                definition=_definition(source_table="jobs"),
                status=PipelineStatus.APPROVED,
            )
        ]
    }

    with pytest.raises(PipelineValidationError, match="platform-owned"):
        _service(db).execute_run("run-1")

    assert db.queries[-1].params["status"] == PipelineRunStatus.FAILED
    assert "platform-owned" in db.queries[-1].params["error"]


def test_execute_minio_sink_uploads_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    uploaded: dict = {}

    class FakeMinio:
        def upload_file(self, file_path: str, object_name: str):
            with open(file_path, encoding="utf-8") as handle:
                uploaded["csv"] = handle.read()
            uploaded["object_name"] = object_name
            return SimpleNamespace(fileUrl=f"s3://bucket/{object_name}")

    db = FakeDB()
    db.queue(
        {"rows": [_run_row()]},
        {
            "rows": [
                _pipeline_row(
                    definition=_definition(
                        sink_kind="minio", object_key="etl/out.csv"
                    ),
                    status=PipelineStatus.APPROVED,
                )
            ]
        },
        {"rows": [_run_row(status=PipelineRunStatus.RUNNING)]},
        {"rows": [{"column_name": "id"}]},
        {"rows": [{"id": 1}, {"id": 2}]},
        {"rows": [_run_row(status=PipelineRunStatus.SUCCEEDED)]},
    )

    result = _service(db, minio=FakeMinio()).execute_run("run-1")

    assert result["sink"]["kind"] == "minio"
    assert result["sink"]["file_url"] == "s3://bucket/etl/out.csv"
    assert result["row_count"] == 2
    assert uploaded["object_name"] == "etl/out.csv"
    assert "id" in uploaded["csv"]


# ——————————————————————————————————————————————————————————————
# Source guardrails / staging


def test_source_table_prefix_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(FakeDB())

    service._validate_source_table("etl_orders")

    with pytest.raises(PipelineValidationError, match="must start with"):
        service._validate_source_table("orders")


def test_source_table_allow_list_overrides_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ETL_ALLOWED_SOURCE_TABLES", "orders,customers")
    service = _service(FakeDB())

    service._validate_source_table("orders")

    with pytest.raises(PipelineValidationError, match="not in ETL_ALLOWED_SOURCE_TABLES"):
        service._validate_source_table("etl_orders")


def test_reserved_source_table_is_always_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="platform-owned"):
        _service(FakeDB())._validate_source_table("jobs")


def test_connector_source_is_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnector:
        def build_table_query(self, connector_id, table, *, columns=None, owner_user_id=None):
            return f"SELECT * FROM {table}"

        def fetch_dataframe(self, connector_id, query, *, owner_user_id=None):
            return pd.DataFrame({"A B": [1, 2]})

    written: dict = {}
    service = _service(FakeDB(), connector=FakeConnector())
    monkeypatch.setattr(
        service,
        "_write_dataframe",
        lambda frame, table: written.update(frame=frame, table=table),
    )

    table, is_staging = service._materialize_source(
        SimpleNamespace(
            id="src",
            source=SourceSpec(kind="connector", connector_id="c1", table="orders"),
        ),
        "run-1234",
        "user-1",
    )

    assert is_staging is True
    assert table.startswith("etl_stage_run-1234_src")
    assert written["table"] == table
    assert list(written["frame"].columns) == ["A B"]


def test_connector_source_row_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnector:
        def build_table_query(self, connector_id, table, *, columns=None, owner_user_id=None):
            return f"SELECT * FROM {table}"

        def fetch_dataframe(self, connector_id, query, *, owner_user_id=None):
            return pd.DataFrame({"id": [1, 2]})

    monkeypatch.setattr(settings, "ETL_MAX_SOURCE_ROWS", 1)
    service = _service(FakeDB(), connector=FakeConnector())

    with pytest.raises(PipelineValidationError, match="above the configured limit"):
        service._materialize_source(
            SimpleNamespace(
                id="src",
                source=SourceSpec(kind="connector", connector_id="c1", table="orders"),
            ),
            "run-1234",
            "user-1",
        )


def test_normalize_columns_makes_csv_headers_referenceable() -> None:
    assert PipelineService._normalize_columns(["First Name", "Order-ID", " x "]) == [
        "first_name",
        "order_id",
        "x",
    ]


# ——————————————————————————————————————————————————————————————
# Materialisation / serializers


class FakeNotebook:
    def __init__(self) -> None:
        self.sources: list[tuple] = []
        self.cells: list[tuple] = []

    def add_managed_table_data_source(self, notebook_id, table, *, label, owner_user_id=None):
        self.sources.append((notebook_id, table, label, owner_user_id))
        return {"id": "source-1"}

    def create_table_dashboard_cell(self, notebook_id, table, *, title, owner_user_id=None):
        self.cells.append((notebook_id, table, title, owner_user_id))
        return SimpleNamespace(id="cell-1")


def test_materialize_pipeline_uses_latest_successful_postgres_run() -> None:
    definition = _definition(
        materialize={"notebook_id": "notebook-1", "create_dashboard_cell": True}
    )
    pipeline = _pipeline_row(
        definition=definition, status=PipelineStatus.APPROVED, name="orders"
    )
    successful_run = _run_row(
        status=PipelineRunStatus.SUCCEEDED,
        result={"sink": {"kind": "postgres", "table": "etl_out"}},
    )
    db = FakeDB()
    db.queue(
        {"rows": [pipeline]},
        {"rows": [pipeline]},
        {"rows": [successful_run]},
    )
    notebook = FakeNotebook()

    result = _service(db, notebook=notebook).materialize_pipeline(
        "pipeline-1", "notebook-1", "user-1"
    )

    assert result == {
        "notebook_id": "notebook-1",
        "table": "etl_out",
        "dashboard_cell_id": "cell-1",
        "charts": [],
        "layout": None,
        "reply": None,
    }
    assert notebook.sources[0][1] == "etl_out"
    assert notebook.sources[0][2] == "orders — etl_out"
    assert notebook.cells[0][2] == "orders · etl_out"


def test_materialize_pipeline_requires_a_successful_run() -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    db = FakeDB()
    db.queue(
        {"rows": [pipeline]},
        {"rows": [pipeline]},
        {"rows": [_run_row(status=PipelineRunStatus.FAILED)]},
    )

    with pytest.raises(PipelineValidationError, match="no successful Postgres run"):
        _service(db, notebook=FakeNotebook()).materialize_pipeline(
            "pipeline-1", "notebook-1", "user-1"
        )


def test_to_pipeline_read_parses_json_definition() -> None:
    read = PipelineService.to_pipeline_read(_pipeline_row())

    assert read.id == "pipeline-1"
    assert read.definition.steps[0].id == "src"


def test_to_run_read_parses_json_result() -> None:
    row = _run_row(
        status=PipelineRunStatus.SUCCEEDED,
        result={"row_count": 3},
    )
    row["result"] = json.dumps(row["result"])

    read = PipelineService.to_run_read(row)

    assert read.result == {"row_count": 3}


def test_parse_definition_accepts_strings_and_models() -> None:
    definition = _definition()
    parsed = PipelineService._parse_definition(json.dumps(definition.model_dump()))

    assert parsed.model_dump() == definition.model_dump()
