"""Behavioural tests for :mod:`squirrel.services.workspace.WorkspaceService`.

The service is exercised against :class:`tests.fakes.FakeDB` and small
in-memory stand-ins for MinIO / connectors / the preprocessing agent, so the
tests cover SQL construction, source bookkeeping, artifact uploads and the
prediction state machine without a live Postgres, MinIO or LLM call.
"""
from __future__ import annotations

import importlib
import io
import json
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

from squirrel.constants.workspace import DataType, SourceKind, WorkspaceStatus
from squirrel.schemas.error import SourceNotFoundError, WorkspaceNotFoundError
from squirrel.services.workspace.WorkspaceService import WorkspaceService
from tests.fakes import FakeDB

workspace_module = importlib.import_module("squirrel.services.workspace.WorkspaceService")


# ——————————————————————————————————————————————————————————————
# Fakes and builders


class _Minio:
    """Records uploads/deletes and replays queued retrieve results."""

    def __init__(self, uploads=None, retrieves=None, delete_fails=False):
        self.upload_results = list(uploads or [])
        self.retrieve_results = list(retrieves or [])
        self.delete_fails = delete_fails
        self.uploads = []
        self.payloads = []
        self.deleted = []

    def upload_file(self, file_path, object_name):
        self.uploads.append((file_path, object_name))
        try:
            with open(file_path, "rb") as handle:
                self.payloads.append(handle.read())
        except OSError:
            self.payloads.append(None)
        if self.upload_results:
            return self.upload_results.pop(0)
        return SimpleNamespace(fileUrl=f"s3://bucket/{object_name}")

    def retrieve_file(self, url):
        if self.retrieve_results:
            return self.retrieve_results.pop(0)
        return None

    def delete_file(self, url):
        self.deleted.append(url)
        if self.delete_fails:
            raise RuntimeError("minio down")


class _FileService:
    def __init__(self):
        self.recorded = []

    def record_upload(self, result, **kwargs):
        self.recorded.append({"result": result, **kwargs})


class _Connector:
    def __init__(self, tables=None, preview=None, connection=None):
        self.tables = tables or []
        self.preview = preview
        self.connection = connection or {"name": "Warehouse", "type": "postgres"}
        self.calls = []

    def get_connection(self, connector_id, owner_user_id=None):
        self.calls.append(("get_connection", connector_id))
        return self.connection

    def build_table_query(self, connector_id, table_name, columns=None, owner_user_id=None):
        self.calls.append(("build_table_query", table_name, columns))
        return f"SELECT * FROM {table_name}"

    def list_tables(self, connector_id, owner_user_id=None):
        self.calls.append(("list_tables", connector_id))
        return list(self.tables)

    def preview_table(self, connector_id, table_name, limit=5, owner_user_id=None):
        self.calls.append(("preview_table", table_name))
        if self.preview is None:
            raise RuntimeError("no preview")
        return self.preview

    def fetch_dataframe(self, connector_id, query, owner_user_id=None):
        self.calls.append(("fetch_dataframe", query))
        return pd.DataFrame({"id": [1, 2]})


class _Estimator:
    def __init__(self, predictions=None, proba=None, classes=None):
        self.predictions = predictions if predictions is not None else [1, 0]
        self.proba = proba
        self.classes_ = classes or [0, 1]
        self.seen = None

    def predict(self, X):
        self.seen = X
        return self.predictions

    def predict_proba(self, X):
        if self.proba is None:
            raise RuntimeError("no proba")
        return self.proba


class _FakeLSTM:
    """Module-level so joblib can pickle it; behaves like a sequential model."""

    window_size = 2
    difference_order = 1

    def __init__(self, expected=None, predictions=None):
        self.expected = expected
        self.predictions = predictions or [4.0, 5.0]

    def predict(self, X, n_steps):
        if self.expected is not None:
            assert X == self.expected
        return self.predictions


class _FakeProcessorAgent:
    """Stand-in for ``TabularDataProcessorAgent`` used by ``predict``."""

    merged = None
    transformed = None

    @staticmethod
    def merge_sources(dataframes, target_column=None):
        if _FakeProcessorAgent.merged is not None:
            return _FakeProcessorAgent.merged
        frames = list(dataframes.values())
        return frames[0] if frames else pd.DataFrame()

    def apply_fitted_pipeline(self, df, execution_report):
        if _FakeProcessorAgent.transformed is not None:
            return _FakeProcessorAgent.transformed
        return df


def _service(db=None, minio=None, connector=None) -> WorkspaceService:
    service = WorkspaceService.__new__(WorkspaceService)
    service._db = db or FakeDB()
    service._minio = minio or _Minio()
    service._file_service = _FileService()
    service._connector = connector or _Connector()
    return service


def _workspace(**overrides):
    row = {
        "workspace_id": "ws_1",
        "owner_user_id": "user-1",
        "name": "Churn",
        "status": WorkspaceStatus.UPLOADED,
        "data_type": DataType.STRUCTURED,
        "target_column": "churn",
        "input_sources": [],
        "error": None,
    }
    row.update(overrides)
    return row


def _upload_source(**overrides):
    source = {
        "source_id": "src_1",
        "kind": SourceKind.UPLOAD,
        "name": "orders.csv",
        "file_type": "csv",
        "file_url": "s3://bucket/orders.csv",
        "columns": ["a", "b"],
        "all_columns": ["a", "b"],
        "row_count": 2,
    }
    source.update(overrides)
    return source


def _connector_source(**overrides):
    source = {
        "source_id": "src_c",
        "kind": SourceKind.CONNECTOR,
        "name": "orders",
        "connector_id": "conn_1",
        "table": "orders",
        "query": "SELECT * FROM orders",
        "columns": ["id"],
    }
    source.update(overrides)
    return source


def _file(file_bytes=b"a,b\n1,2\n"):
    return SimpleNamespace(fileByte=file_bytes, fileUrl="s3://bucket/file.csv")


def _model_bytes(value=None):
    buffer = io.BytesIO()
    joblib.dump(value if value is not None else {"kind": "model"}, buffer)
    return buffer.getvalue()


# ——————————————————————————————————————————————————————————————
# Pure helpers


def test_workspace_filters_include_the_owner_when_given() -> None:
    assert WorkspaceService._workspace_filters("ws_1") == {"workspace_id": "ws_1"}
    assert WorkspaceService._workspace_filters("ws_1", "user-1") == {
        "workspace_id": "ws_1",
        "owner_user_id": "user-1",
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("s3://b/ws_1/models/random_forest_20260101T120000.joblib", "random_forest"),
        ("s3://b/ws_1/models/linear.joblib", "linear"),
        ("s3://b/ws_1/models/linear", "linear"),
        ("plain.joblib", "plain"),
    ],
)
def test_model_key_from_url(url: str, expected: str) -> None:
    assert WorkspaceService._model_key_from_url(url) == expected


# ——————————————————————————————————————————————————————————————
# Table bootstrap


def test_ensure_tables_creates_tables_and_swallows_errors() -> None:
    db = FakeDB()
    service = _service(db)

    service._ensure_tables()

    assert len(db.executes) == 4
    assert any("CREATE TABLE IF NOT EXISTS workspaces" in e.sql for e in db.executes)
    assert any("ADD COLUMN IF NOT EXISTS owner_user_id" in e.sql for e in db.executes)

    broken = FakeDB()
    broken.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    _service(broken)._ensure_tables()  # must not raise


# ——————————————————————————————————————————————————————————————
# Workspace CRUD


def test_create_workspace_inserts_and_returns_the_row() -> None:
    db = FakeDB(insert_results=[[_workspace()]])
    service = _service(db)

    row = service.create_workspace("Churn", owner_user_id="user-1")

    assert row["workspace_id"] == "ws_1"
    insert = db.inserts[0]
    assert "INSERT INTO workspaces" in insert.sql
    assert insert.params["name"] == "Churn"
    assert insert.params["owner_user_id"] == "user-1"
    assert insert.params["status"] == WorkspaceStatus.CREATED


def test_create_workspace_rejects_unknown_data_types() -> None:
    with pytest.raises(ValueError, match="Unknown data_type"):
        _service().create_workspace("Churn", data_type="spreadsheet")


def test_create_workspace_raises_when_the_insert_returns_nothing() -> None:
    with pytest.raises(RuntimeError, match="Failed to create"):
        _service(FakeDB()).create_workspace("Churn")


def test_ensure_workspace_creates_when_no_id_is_given() -> None:
    db = FakeDB(insert_results=[[_workspace(workspace_id="ws_new")]])
    service = _service(db)

    row = service.ensure_workspace(None, name="Chat", owner_user_id="user-1")

    assert row["workspace_id"] == "ws_new"
    assert db.inserts[0].params["name"] == "Chat"


def test_ensure_workspace_returns_an_existing_row() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    service = _service(db)

    row = service.ensure_workspace("ws_1", owner_user_id="user-1")

    assert row["workspace_id"] == "ws_1"
    assert db.inserts == []


def test_ensure_workspace_rejects_a_different_owner() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(owner_user_id="other")]}])
    service = _service(db)

    with pytest.raises(WorkspaceNotFoundError):
        service.ensure_workspace("ws_1", owner_user_id="user-1")


def test_ensure_workspace_creates_a_missing_id_then_reads_it_back() -> None:
    db = FakeDB(
        retrieve_results=[{"rows": []}, {"rows": [_workspace(workspace_id="ws_client")]}]
    )
    service = _service(db)

    row = service.ensure_workspace("ws_client", name="Named", owner_user_id="user-1")

    assert row["workspace_id"] == "ws_client"
    assert "ON CONFLICT (workspace_id) DO NOTHING" in db.inserts[0].sql
    assert db.inserts[0].params["workspace_id"] == "ws_client"


def test_get_workspace_raises_when_missing() -> None:
    with pytest.raises(WorkspaceNotFoundError):
        _service(FakeDB()).get_workspace("ws_missing")


def test_get_workspace_scopes_the_lookup_to_the_owner() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    service = _service(db)

    service.get_workspace("ws_1", owner_user_id="user-1")

    assert db.retrieves[0].filters == {
        "workspace_id": "ws_1",
        "owner_user_id": "user-1",
    }


def test_list_workspaces_sorts_newest_first_and_limits() -> None:
    db = FakeDB(
        retrieve_results=[
            {
                "rows": [
                    {"workspace_id": "old", "created_at": "2026-01-01"},
                    {"workspace_id": "new", "created_at": "2026-02-01"},
                ]
            }
        ]
    )
    service = _service(db)

    rows = service.list_workspaces(limit=1, owner_user_id="user-1")

    assert [r["workspace_id"] for r in rows] == ["new"]
    assert db.retrieves[0].filters == {"owner_user_id": "user-1"}


def test_update_workspace_is_a_noop_without_fields() -> None:
    db = FakeDB()
    _service(db).update_workspace("ws_1", {})

    assert db.executes == []


def test_update_workspace_serializes_jsonb_columns() -> None:
    db = FakeDB()
    service = _service(db)

    service.update_workspace(
        "ws_1", {"input_sources": [_upload_source()], "status": "uploaded"}, owner_user_id="user-1"
    )

    execute = db.executes[0]
    assert "input_sources = %(input_sources)s::jsonb" in execute.sql
    assert json.loads(execute.params["input_sources"])[0]["source_id"] == "src_1"
    assert execute.params["status"] == "uploaded"
    assert execute.params["owner_user_id"] == "user-1"


def test_update_workspace_reraises_database_errors() -> None:
    db = FakeDB()
    db.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    service = _service(db)

    with pytest.raises(RuntimeError):
        service.update_workspace("ws_1", {"name": "x"})


def test_set_status_forwards_optional_fields() -> None:
    db = FakeDB()
    service = _service(db)

    service.set_status("ws_1", WorkspaceStatus.FAILED, target_column="churn", error="boom")

    params = db.executes[0].params
    assert params["status"] == WorkspaceStatus.FAILED
    assert params["target_column"] == "churn"
    assert params["error"] == "boom"


def test_rename_workspace_strips_and_reads_back() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(name="Renamed")]}])
    service = _service(db)

    row = service.rename_workspace("ws_1", "  Renamed  ")

    assert row["name"] == "Renamed"
    assert db.executes[0].params["name"] == "Renamed"


def test_rename_workspace_rejects_blank_names() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        _service().rename_workspace("ws_1", "   ")


def test_delete_workspace_purges_artifacts_and_rows() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_upload_source()])]},
            {"rows": [{"output_file_urls": ["s3://bucket/out.csv"]}]},
        ]
    )
    minio = _Minio()
    service = _service(db, minio=minio)

    service.delete_workspace("ws_1", owner_user_id="user-1")

    assert sorted(minio.deleted) == ["s3://bucket/orders.csv", "s3://bucket/out.csv"]
    assert len(db.executes) == 2
    assert "DELETE FROM workspace_runs" in db.executes[0].sql
    assert "DELETE FROM workspaces" in db.executes[1].sql
    assert db.executes[0].params["owner_user_id"] == "user-1"


def test_delete_workspace_can_skip_the_artifact_purge() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    minio = _Minio()
    service = _service(db, minio=minio)

    service.delete_workspace("ws_1", purge_artifacts=False)

    assert minio.deleted == []
    assert len(db.executes) == 2


def test_purge_workspace_artifacts_swallows_delete_failures() -> None:
    db = FakeDB(retrieve_results=[{"rows": [{"output_file_urls": ["s3://bucket/out.csv"]}]}])
    minio = _Minio(delete_fails=True)
    service = _service(db, minio=minio)

    service._purge_workspace_artifacts(
        "ws_1", _workspace(input_sources=[_upload_source(), _connector_source()])
    )

    assert sorted(minio.deleted) == ["s3://bucket/orders.csv", "s3://bucket/out.csv"]  # connector source is not purged


# ——————————————————————————————————————————————————————————————
# Input sources


def test_add_file_source_uploads_and_marks_the_workspace_uploaded() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(status=WorkspaceStatus.CREATED)]}])
    minio = _Minio()
    service = _service(db, minio=minio)

    source = service.add_file_source(
        "ws_1",
        "C:/tmp/orders.csv",
        "orders.csv",
        "csv",
        columns=["a", "b"],
        row_count=2,
        owner_user_id="user-1",
    )

    assert source["kind"] == SourceKind.UPLOAD
    assert source["file_url"].endswith("_orders.csv")
    assert source["all_columns"] == ["a", "b"]
    assert source["row_count"] == 2
    update = db.executes[0]
    assert update.params["status"] == WorkspaceStatus.UPLOADED
    assert json.loads(update.params["input_sources"])[0]["source_id"] == source["source_id"]
    assert service._file_service.recorded[0]["workspace_id"] == "ws_1"


def test_add_file_source_keeps_a_non_created_status() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(status=WorkspaceStatus.COMPLETED)]}])
    service = _service(db)

    service.add_file_source("ws_1", "C:/tmp/a.csv", "a.csv", "csv")

    assert "status" not in db.executes[0].params


def test_add_file_source_raises_when_the_upload_fails() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    service = _service(db, minio=_Minio(uploads=[None]))

    with pytest.raises(RuntimeError, match="Failed to upload"):
        service.add_file_source("ws_1", "C:/tmp/a.csv", "a.csv", "csv")


def test_preview_upload_source_returns_columns_and_rows() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(input_sources=[_upload_source()])]}])
    service = _service(db, minio=_Minio(retrieves=[_file(b"a,b\n1,2\n3,4\n")]))

    preview = service.preview_upload_source("ws_1", "src_1", limit=1)

    assert preview["table"] == "orders.csv"
    assert preview["columns"] == ["a", "b"]
    assert preview["preview"] == [{"a": 1, "b": 2}]


def test_preview_upload_source_validates_the_source() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_connector_source()])]},
            {"rows": [_workspace(input_sources=[_connector_source()])]},
            {"rows": [_workspace(input_sources=[_upload_source(file_type="parquet")])]},
        ]
    )
    service = _service(db)

    with pytest.raises(SourceNotFoundError):
        service.preview_upload_source("ws_1", "missing")
    with pytest.raises(ValueError, match="uploaded file sources"):
        service.preview_upload_source("ws_1", "src_c")
    with pytest.raises(NotImplementedError, match="parquet"):
        service.preview_upload_source("ws_1", "src_1")


def test_update_source_columns_narrows_and_resets() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_upload_source()])]},
            {"rows": [_workspace(input_sources=[_upload_source()])]},
        ]
    )
    service = _service(db)

    narrowed = service.update_source_columns("ws_1", "src_1", ["a"])
    assert narrowed["columns"] == ["a"]

    reset = service.update_source_columns("ws_1", "src_1", None)
    assert reset["columns"] == ["a", "b"]


def test_update_source_columns_validates_columns() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_upload_source()])]},
            {"rows": [_workspace(input_sources=[_upload_source()])]},
            {"rows": [_workspace(input_sources=[{"source_id": "src_x", "kind": "upload"}])]},
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="Unknown column"):
        service.update_source_columns("ws_1", "src_1", ["nope"])
    with pytest.raises(SourceNotFoundError):
        service.update_source_columns("ws_1", "missing", ["a"])
    with pytest.raises(ValueError, match="no known column list"):
        service.update_source_columns("ws_1", "src_x", ["a"])


def test_add_connector_source_attaches_a_live_table() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(status=WorkspaceStatus.CREATED)]}])
    connector = _Connector(preview=pd.DataFrame({"id": [1], "total": [2]}))
    service = _service(db, connector=connector)

    source = service.add_connector_source("ws_1", "conn_1", "orders")

    assert source["kind"] == SourceKind.CONNECTOR
    assert source["query"] == "SELECT * FROM orders"
    assert source["columns"] == ["id", "total"]
    assert db.executes[0].params["status"] == WorkspaceStatus.UPLOADED


def test_add_connector_source_uses_explicit_columns_and_name() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    service = _service(db)

    source = service.add_connector_source(
        "ws_1", "conn_1", "orders", name="Orders", columns=["id"]
    )

    assert source["name"] == "Orders"
    assert source["columns"] == ["id"]
    assert ("preview_table", "orders") not in service._connector.calls


def test_add_connector_source_rejects_a_blank_name() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])
    service = _service(db)

    with pytest.raises(ValueError, match="cannot be empty"):
        service.add_connector_source("ws_1", "conn_1", "orders", name="  ")


def test_add_connector_sources_attaches_every_table() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace()]},
            {"rows": [_workspace()]},
            {"rows": [_workspace()]},
        ]
    )
    connector = _Connector(tables=["a", "b"])
    service = _service(db, connector=connector)

    sources = service.add_connector_sources(
        "ws_1",
        "conn_1",
        [{"table": "a", "columns": ["id"]}, {"table": "b", "columns": ["id"]}],
    )

    assert [s["table"] for s in sources] == ["a", "b"]


def test_add_connector_sources_validates_the_request() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_connector_source(table="a")])]},
        ]
    )
    service = _service(db, connector=_Connector(tables=["a"]))

    with pytest.raises(ValueError, match="at least one table"):
        service.add_connector_sources("ws_1", "conn_1", [])
    with pytest.raises(ValueError, match="Unknown table"):
        service.add_connector_sources("ws_1", "conn_1", [{"table": "missing"}])
    with pytest.raises(ValueError, match="Already attached"):
        service.add_connector_sources("ws_1", "conn_1", [{"table": "a"}])


def test_remove_source_deletes_the_blob_and_resets_an_empty_workspace() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(input_sources=[_upload_source()])]}])
    minio = _Minio()
    service = _service(db, minio=minio)

    service.remove_source("ws_1", "src_1")

    assert minio.deleted == ["s3://bucket/orders.csv"]
    update = db.executes[0]
    assert update.params["status"] == WorkspaceStatus.CREATED
    assert json.loads(update.params["input_sources"]) == []


def test_remove_source_keeps_the_status_when_sources_remain() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(input_sources=[_upload_source(), _connector_source()])]}
        ]
    )
    service = _service(db)

    service.remove_source("ws_1", "src_1")

    assert "status" not in db.executes[0].params


def test_remove_source_swallows_blob_deletion_errors() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace(input_sources=[_upload_source()])]}])
    service = _service(db, minio=_Minio(delete_fails=True))

    service.remove_source("ws_1", "src_1")  # must not raise

    assert db.executes


def test_remove_source_raises_when_missing() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_workspace()]}])

    with pytest.raises(SourceNotFoundError):
        _service(db).remove_source("ws_1", "missing")


# ——————————————————————————————————————————————————————————————
# Loading source data


def test_load_csv_parses_bytes_from_minio() -> None:
    service = _service(minio=_Minio(retrieves=[_file(b"a,b\n1,2\n")]))

    df = service.load_csv("s3://bucket/x.csv")

    assert list(df.columns) == ["a", "b"]
    assert df.iloc[0].tolist() == [1, 2]


def test_load_csv_raises_when_the_file_is_missing() -> None:
    with pytest.raises(RuntimeError, match="Could not retrieve"):
        _service().load_csv("s3://bucket/missing.csv")


def test_fetch_data_delegates_to_the_connector() -> None:
    service = _service()

    df = service.fetch_data(_connector_source())

    assert list(df.columns) == ["id"]
    assert ("fetch_dataframe", "SELECT * FROM orders") in service._connector.calls


def test_fetch_data_requires_a_recorded_query() -> None:
    with pytest.raises(RuntimeError, match="has no query"):
        _service().fetch_data(_connector_source(query=None))


def test_upload_loaders_register_csv_only() -> None:
    loaders = _service()._upload_loaders()

    assert set(loaders) == {"csv"}


def test_load_source_dataframes_loads_uploads_and_connectors() -> None:
    service = _service(minio=_Minio(retrieves=[_file(b"a,b\n1,2\n")]))

    frames = service.load_source_dataframes([_upload_source(), _connector_source()])

    assert set(frames) == {"src_1", "src_c"}
    assert list(frames["src_1"].columns) == ["a", "b"]


def test_load_source_dataframes_narrows_selected_columns() -> None:
    source = _upload_source(columns=["a"], all_columns=["a", "b"])
    service = _service(minio=_Minio(retrieves=[_file(b"a,b\n1,2\n")]))

    frames = service.load_source_dataframes([source])

    assert list(frames["src_1"].columns) == ["a"]


def test_load_source_dataframes_rejects_unsupported_file_types() -> None:
    with pytest.raises(NotImplementedError, match="no registered loader"):
        _service().load_source_dataframes([_upload_source(file_type="zip")])


def test_load_source_dataframes_requires_a_file_url() -> None:
    with pytest.raises(RuntimeError, match="no file_url"):
        _service().load_source_dataframes([_upload_source(file_url=None)])


# ——————————————————————————————————————————————————————————————
# Run persistence


def test_save_run_uploads_a_processed_dataframe_and_pipeline() -> None:
    db = FakeDB(insert_results=[[{"id": 7}]])
    service = _service(db)

    result = service.save_run(
        "ws_1",
        "preprocessing",
        ["s3://bucket/in.csv"],
        pd.DataFrame({"a": [1]}),
        {"summary": "ok"},
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        plan={"steps": []},
        execution_report={"steps": []},
    )

    assert result["record_id"] == 7
    assert len(result["output_file_urls"]) == 2  # processed CSV + pipeline artifact
    assert result["output_file_urls"][0].endswith(".csv")
    assert result["output_file_urls"][1].endswith(".json")
    summary = json.loads(db.inserts[0].params["agent_summary"])
    assert summary["pipeline_artifact_url"] == result["output_file_urls"][1]
    assert db.inserts[0].params["total_tokens"] == 3


def test_save_run_skips_the_pipeline_without_an_execution_report() -> None:
    db = FakeDB(insert_results=[[{"id": 1}]])
    service = _service(db)

    result = service.save_run(
        "ws_1", "preprocessing", [], pd.DataFrame({"a": [1]}), {}
    )

    assert len(result["output_file_urls"]) == 1


def test_save_run_logs_a_failed_dataframe_upload() -> None:
    db = FakeDB(insert_results=[[{"id": 1}]])
    service = _service(db, minio=_Minio(uploads=[None]))

    result = service.save_run(
        "ws_1", "preprocessing", [], pd.DataFrame({"a": [1]}), {}
    )

    assert result["output_file_urls"] == []


def test_save_run_uploads_every_fitted_model() -> None:
    db = FakeDB(insert_results=[[{"id": 2}]])
    service = _service(db)

    result = service.save_run(
        "ws_1",
        "model_building",
        [],
        {"random_forest": {"kind": "rf"}, "linear": {"kind": "linear"}},
        {"best_model": "random_forest"},
    )

    assert len(result["output_file_urls"]) == 2
    assert all(url.endswith(".joblib") for url in result["output_file_urls"])


def test_save_run_records_runs_without_artifacts() -> None:
    db = FakeDB(insert_results=[[{"id": 3}]])
    service = _service(db)

    result = service.save_run("ws_1", "other", ["s3://bucket/in"], "raw", {})

    assert result["output_file_urls"] == []
    assert db.inserts[0].params["input_file_urls"] == ["s3://bucket/in"]


def test_insert_run_returns_none_without_an_id_or_on_error() -> None:
    service = _service(FakeDB(insert_results=[[]]))
    assert (
        service._insert_run("ws_1", None, "other", [], [], {}, 0, 0, 0, 0) is None
    )

    broken = FakeDB()
    broken.insert = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    service = _service(broken)
    assert service._insert_run("ws_1", None, "other", [], [], {}, 0, 0, 0, 0) is None


def test_get_record_returns_the_first_row_or_none() -> None:
    db = FakeDB(retrieve_results=[{"rows": [{"id": 1}]}, {"rows": []}])
    service = _service(db)

    assert service.get_record(1) == {"id": 1}
    assert service.get_record(2) is None


def test_get_records_by_workspace_id_sorts_and_filters() -> None:
    db = FakeDB(
        retrieve_results=[
            {
                "rows": [
                    {"id": 1, "created_at": "2026-01-01"},
                    {"id": 2, "created_at": "2026-02-01"},
                ]
            }
        ]
    )
    service = _service(db)

    rows = service.get_records_by_workspace_id(
        "ws_1", agent_type="preprocessing", limit=1, owner_user_id="user-1"
    )

    assert [r["id"] for r in rows] == [2]
    assert db.retrieves[0].filters == {
        "workspace_id": "ws_1",
        "owner_user_id": "user-1",
        "agent_type": "preprocessing",
    }


def test_download_output_file_returns_bytes_or_none() -> None:
    service = _service(minio=_Minio(retrieves=[_file(b"abc"), None]))
    assert service.download_output_file("s3://bucket/x") == b"abc"
    assert service.download_output_file("s3://bucket/y") is None

    broken = _service(minio=_Minio(retrieves=[SimpleNamespace(fileUrl="s3://bucket/z")]))
    assert broken.download_output_file("s3://bucket/z") is None


# ——————————————————————————————————————————————————————————————
# Artifact uploads


def test_upload_dataframe_uploads_and_records_the_file() -> None:
    minio = _Minio()
    service = _service(minio=minio)

    url = service._upload_dataframe(pd.DataFrame({"a": [1]}), "ws_1", owner_user_id="user-1")

    assert url.startswith("s3://bucket/ws_1/preprocessed/")
    assert service._file_service.recorded[0]["user_uploaded"] is False
    assert minio.uploads[0][1].endswith(".csv")


def test_upload_dataframe_returns_none_on_upload_failure() -> None:
    service = _service(minio=_Minio(uploads=[None]))
    assert service._upload_dataframe(pd.DataFrame({"a": [1]}), "ws_1") is None

    broken = _service(minio=_Minio())
    broken._minio.upload_file = lambda **k: (_ for _ in ()).throw(RuntimeError("down"))
    assert broken._upload_dataframe(pd.DataFrame({"a": [1]}), "ws_1") is None


def test_upload_model_pickles_the_estimator() -> None:
    service = _service()
    url = service._upload_model({"kind": "rf"}, "random_forest", "ws_1")

    assert url.startswith("s3://bucket/ws_1/models/random_forest_")
    assert url.endswith(".joblib")


def test_upload_model_returns_none_on_failure() -> None:
    service = _service(minio=_Minio(uploads=[None]))
    assert service._upload_model({"kind": "rf"}, "rf", "ws_1") is None


def test_save_pipeline_artifact_parses_json_strings() -> None:
    minio = _Minio()
    service = _service(minio=minio)

    url = service.save_pipeline_artifact(
        "ws_1", json.dumps({"steps": [1]}), json.dumps({"steps": [2]})
    )

    assert url.endswith(".json")
    payload = json.loads(minio.payloads[0])
    assert payload == {"plan": {"steps": [1]}, "execution_report": {"steps": [2]}}


def test_save_pipeline_artifact_tolerates_invalid_json() -> None:
    minio = _Minio()
    service = _service(minio=minio)

    url = service.save_pipeline_artifact("ws_1", "not-json", "also-not-json")

    assert url.endswith(".json")
    payload = json.loads(minio.payloads[0])
    assert payload == {"plan": {}, "execution_report": {}}


def test_save_pipeline_artifact_returns_none_on_upload_failure() -> None:
    service = _service(minio=_Minio(uploads=[None]))
    assert service.save_pipeline_artifact("ws_1", {}, {}) is None


def test_load_pipeline_artifact_parses_bytes() -> None:
    service = _service(
        minio=_Minio(retrieves=[_file(json.dumps({"plan": {}, "execution_report": {}}).encode())])
    )

    assert service.load_pipeline_artifact("s3://bucket/p.json") == {
        "plan": {},
        "execution_report": {},
    }


def test_load_pipeline_artifact_validates_the_payload() -> None:
    with pytest.raises(RuntimeError, match="Could not retrieve"):
        _service().load_pipeline_artifact("s3://bucket/missing.json")

    service = _service(minio=_Minio(retrieves=[_file(b"not json")]))
    with pytest.raises(ValueError, match="not valid JSON"):
        service.load_pipeline_artifact("s3://bucket/bad.json")


def test_get_latest_pipeline_artifact_url_reads_the_summary() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [{"agent_summary": {"pipeline_artifact_url": "s3://p.json"}}]},
            {"rows": []},
        ]
    )
    service = _service(db)

    assert service.get_latest_pipeline_artifact_url("ws_1") == "s3://p.json"
    assert service.get_latest_pipeline_artifact_url("ws_2") is None


def test_get_latest_model_url_resolves_keys() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {"best_model": "rf"}}]},
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {}}]},
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {}}]},
        ]
    )
    service = _service(db)

    assert service.get_latest_model_url("ws_1") == "s3://b/rf_20260101T120000.joblib"
    assert service.get_latest_model_url("ws_1", model_key="rf") == "s3://b/rf_20260101T120000.joblib"
    assert service.get_latest_model_url("ws_1") == "s3://b/rf_20260101T120000.joblib"


def test_get_latest_model_url_raises_for_missing_runs_artifacts_or_keys() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": []},
            {"rows": [{"output_file_urls": [], "agent_summary": {}}]},
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {}}]},
        ]
    )
    service = _service(db)

    with pytest.raises(RuntimeError, match="no completed model-building run"):
        service.get_latest_model_url("ws_1")
    with pytest.raises(RuntimeError, match="no artifacts"):
        service.get_latest_model_url("ws_1")
    with pytest.raises(RuntimeError, match="not found"):
        service.get_latest_model_url("ws_1", model_key="linear")


def test_load_model_unpickles_the_artifact() -> None:
    service = _service(minio=_Minio(retrieves=[_file(_model_bytes({"kind": "rf"}))]))

    assert service._load_model("s3://b/m.joblib") == {"kind": "rf"}


def test_load_model_raises_when_the_artifact_is_missing() -> None:
    with pytest.raises(RuntimeError, match="Could not retrieve model"):
        _service()._load_model("s3://b/m.joblib")


# ——————————————————————————————————————————————————————————————
# Prediction


def test_load_target_history_requires_sources_target_and_enough_values(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "TabularDataProcessorAgent", _FakeProcessorAgent)
    _FakeProcessorAgent.merged = None
    service = _service(minio=_Minio(retrieves=[_file(b"a,b\n1,2\n"), _file(b"a,b\n1,2\n")]))

    with pytest.raises(RuntimeError, match="no input sources"):
        service._load_target_history(_workspace(input_sources=[]), "churn", 2)

    _FakeProcessorAgent.merged = pd.DataFrame({"other": [1, 2]})
    with pytest.raises(RuntimeError, match="not found"):
        service._load_target_history(_workspace(input_sources=[_upload_source()]), "churn", 2)

    _FakeProcessorAgent.merged = pd.DataFrame({"churn": [1]})
    with pytest.raises(RuntimeError, match="Not enough historical"):
        service._load_target_history(_workspace(input_sources=[_upload_source()]), "churn", 2)


def test_load_target_history_returns_numeric_values(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "TabularDataProcessorAgent", _FakeProcessorAgent)
    _FakeProcessorAgent.merged = pd.DataFrame({"churn": ["1", "x", "3"]})
    service = _service(minio=_Minio(retrieves=[_file(b"a,b\n1,2\n")]))

    values = service._load_target_history(
        _workspace(input_sources=[_upload_source()]), "churn", 2
    )

    assert values == [1.0, 3.0]


def test_predict_replays_the_pipeline_for_tabular_models(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "TabularDataProcessorAgent", _FakeProcessorAgent)
    _FakeProcessorAgent.transformed = pd.DataFrame({"x": [1.0, 2.0]})
    estimator = _Estimator(
        predictions=[1, 0], proba=np.array([[0.2, 0.8], [0.9, 0.1]])
    )
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace()]},
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {"best_model": "rf"}}]},
            {"rows": [{"agent_summary": {"pipeline_artifact_url": "s3://b/p.json"}}]},
        ]
    )
    service = _service(
        db,
        minio=_Minio(
            retrieves=[
                _file(_model_bytes(estimator)),
                _file(json.dumps({"plan": {}, "execution_report": {}}).encode()),
            ]
        ),
    )

    result = service.predict("ws_1", pd.DataFrame({"x": [1, 2]}))

    assert result["model_key"] == "rf"
    assert result["predictions"] == [1, 0]
    assert result["probabilities"] == [[0.2, 0.8], [0.9, 0.1]]
    assert result["classes"] == ["0", "1"]


def test_predict_requires_a_saved_pipeline_for_tabular_models(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "TabularDataProcessorAgent", _FakeProcessorAgent)
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace()]},
            {"rows": [{"output_file_urls": ["s3://b/rf_20260101T120000.joblib"], "agent_summary": {}}]},
            {"rows": []},
        ]
    )
    service = _service(db, minio=_Minio(retrieves=[_file(_model_bytes(_Estimator()))]))

    with pytest.raises(RuntimeError, match="no saved preprocessing pipeline"):
        service.predict("ws_1", pd.DataFrame({"x": [1]}))


def test_predict_forwards_sequential_models_to_the_history_path(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "BiLSTMPredictor", _FakeLSTM)
    monkeypatch.setattr(workspace_module, "LSTMAttentionPredictor", _FakeLSTM)
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace()]},
            {"rows": [{"output_file_urls": ["s3://b/bilstm_20260101T120000.joblib"], "agent_summary": {}}]},
        ]
    )
    model = _FakeLSTM(expected=[1.0, 2.0, 3.0], predictions=[4.0, 5.0])
    service = _service(db, minio=_Minio(retrieves=[_file(_model_bytes(model))]))
    service._load_target_history = lambda *a, **k: [1.0, 2.0, 3.0]

    result = service.predict("ws_1", pd.DataFrame({"x": [1, 2]}))

    assert result == {"model_key": "bilstm", "predictions": [4.0, 5.0]}


def test_predict_sequential_models_require_a_target_column(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "BiLSTMPredictor", _FakeLSTM)
    monkeypatch.setattr(workspace_module, "LSTMAttentionPredictor", _FakeLSTM)
    db = FakeDB(
        retrieve_results=[
            {"rows": [_workspace(target_column=None)]},
            {"rows": [{"output_file_urls": ["s3://b/bilstm_20260101T120000.joblib"], "agent_summary": {}}]},
        ]
    )
    service = _service(db, minio=_Minio(retrieves=[_file(_model_bytes(_FakeLSTM()))]))

    with pytest.raises(RuntimeError, match="no recorded target_column"):
        service.predict("ws_1", pd.DataFrame({"x": [1]}))
