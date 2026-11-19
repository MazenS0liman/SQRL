"""Behavioural tests for :mod:`squirrel.services.notebook.NotebookService`.

The service is exercised against :class:`tests.fakes.FakeDB` and small
stand-ins for MinIO / connectors / the exploratory agent, so the tests cover
SQL construction, row conversion, cache invalidation and the cell-execution
state machine without a live Postgres or an LLM call.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import importlib

import pandas as pd
import pytest

from squirrel.modules.agents import TabularDataExploratoryAgent
from squirrel.schemas.eda import AxisSpec, ChartResult, Hypothesis, SqlSpec
from squirrel.schemas.error import (
    CellNotFoundError,
    DataSourceNotFoundError,
    NotebookNotFoundError,
    NotebookNotReadyError,
)
from squirrel.schemas.notebook import (
    DashboardLayout,
    NotebookCell,
    NotebookChart,
    NotebookDataSource,
)
from squirrel.services.notebook.NotebookService import NotebookService
from tests.fakes import FakeDB

notebook_module = importlib.import_module("squirrel.services.notebook.NotebookService")

NOW = datetime(2026, 1, 1, 12, 0)


class _DB(FakeDB):
    """``FakeDB`` that only consumes queued rows for ``SELECT`` statements.

    The notebook service issues a lot of INSERT/UPDATE statements between
    reads; making writes return an empty result keeps the queues in this file
    focused on the reads whose rows actually shape the returned models.
    """

    def query(self, sql: str, params: dict | None = None, fetch=None):
        self.queries.append(SimpleNamespace(sql=sql, params=params or {}, fetch=fetch))
        if sql.lstrip().upper().startswith("SELECT"):
            if self.results:
                return self.results.pop(0)
        return {"rows": []}


# ——————————————————————————————————————————————————————————————
# Builders


def _service(db: FakeDB | None = None, minio=None) -> NotebookService:
    service = NotebookService.__new__(NotebookService)
    service.postgres = db or _DB()
    service.minio = minio or SimpleNamespace()
    service.file_service = SimpleNamespace(record_upload=lambda *a, **k: None)
    service.connection_string = "postgresql://test"
    return service


def _notebook_row(**overrides):
    row = {
        "id": "nb_1",
        "name": "Churn",
        "description": "A notebook",
        "status": "ready",
        "created_at": NOW,
        "updated_at": NOW,
        "cell_count": 0,
    }
    row.update(overrides)
    return row


def _source_row(**overrides):
    row = {
        "id": "src_1",
        "kind": "upload",
        "table_name": "nb_orders",
        "connector_id": None,
        "connector_type": None,
        "source_file_url": None,
        "original_filename": "orders.csv",
        "row_count": 10,
        "column_count": 2,
        "label": "orders.csv",
        "raw_schema": None,
    }
    row.update(overrides)
    return row


def _source(**overrides) -> NotebookDataSource:
    payload = {
        "id": "src_1",
        "kind": "upload",
        "table_name": "nb_orders",
        "row_count": 10,
        "column_count": 2,
        "label": "orders.csv",
    }
    payload.update(overrides)
    return NotebookDataSource(**payload)


def _cell_row(**overrides):
    row = {
        "id": "cell_1",
        "notebook_id": "nb_1",
        "type": "question",
        "query": "why?",
        "status": "complete",
        "reply": "because",
        "charts": "[]",
        "dashboard_layout": None,
        "error": None,
        "data_source_ids": "[]",
        "created_at": NOW,
        "response_time_ms": 5,
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "order_index": 0,
    }
    row.update(overrides)
    return row


def _chart(**overrides) -> NotebookChart:
    payload = {
        "id": "c1",
        "title": "Revenue",
        "question": "revenue over time?",
        "plot_type": "line",
        "sql": "SELECT month, revenue FROM t",
        "x": {"column": "month", "label": "Month", "type": "temporal"},
        "y": {"column": "revenue", "label": "Revenue", "type": "quantitative"},
        "data": [{"month": "Jan", "revenue": 10}, {"month": "Feb", "revenue": 20}],
        "observation": "Revenue rose.",
    }
    payload.update(overrides)
    return NotebookChart(**payload)


def _hypothesis(**overrides) -> Hypothesis:
    payload = {
        "id": "h1",
        "question": "revenue over time?",
        "title": "Revenue",
        "description": "Trend",
        "why_it_matters": "Growth",
        "plot_type": "line",
        "analytical_type": "trend",
    }
    payload.update(overrides)
    return Hypothesis(**payload)


def _chart_result(**overrides) -> ChartResult:
    payload = {
        "hypothesis": _hypothesis(),
        "sql_spec": SqlSpec(
            sql="SELECT month, revenue FROM t",
            x=AxisSpec(column="month", label="Month", type="temporal"),
            y=AxisSpec(column="revenue", label="Revenue", type="quantitative"),
        ),
        "data": pd.DataFrame({"month": ["Jan", "Feb"], "revenue": [10, 20]}),
        "observation": "Revenue rose.",
        "error": None,
    }
    payload.update(overrides)
    return ChartResult(**payload)


# ——————————————————————————————————————————————————————————————
# Pure helpers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Orders 2026", "orders_2026"),
        ("__leading__", "leading"),
        ("café/naïve", "café_naïve"),
    ],
)
def test_safe_table_name_sanitizes(raw: str, expected: str) -> None:
    assert NotebookService._safe_table_name(raw) == expected


def test_safe_table_name_falls_back_when_nothing_survives() -> None:
    name = NotebookService._safe_table_name("___")

    assert name.startswith("nb_")
    assert len(name) <= 63


def test_safe_table_name_truncates_to_the_postgres_limit() -> None:
    name = NotebookService._safe_table_name("x" * 120)

    assert len(name) == 63
    assert name[:54] == "x" * 54


def test_cache_key_sorts_source_ids() -> None:
    assert NotebookService._cache_key(["b", "a", "c"]) == "a,b,c"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("  Why   Revenue? ", "why revenue?"), ("", ""), (None, "")],
)
def test_normalize_question(text, expected: str) -> None:
    assert NotebookService._normalize_question(text) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("revenue over time", "line"),
        ("monthly revenue", "line"),
        ("distribution of order value", "boxplot"),
        ("correlation between age and spend", "scatter"),
        ("heatmap of city vs product", "heatmap"),
        ("revenue by product", "bar"),
    ],
)
def test_infer_plot_type_from_keywords(question: str, expected: str) -> None:
    assert NotebookService._infer_plot_type_from_keywords(question) == expected


def test_parse_dashboard_layout_handles_none_and_json() -> None:
    assert NotebookService._parse_dashboard_layout(None) is None
    assert NotebookService._parse_dashboard_layout("") is None

    layout = NotebookService._parse_dashboard_layout('{"order": ["c1"]}')
    assert layout is not None
    assert layout.order == ["c1"]

    layout = NotebookService._parse_dashboard_layout({"hidden_ids": ["c2"]})
    assert layout is not None
    assert layout.hidden_ids == ["c2"]


def test_row_to_notebook_builds_the_model() -> None:
    notebook = NotebookService._row_to_notebook(_notebook_row(), [_source()])

    assert notebook.id == "nb_1"
    assert notebook.status == "ready"
    assert notebook.cell_count == 0
    assert notebook.data_sources[0].id == "src_1"


def test_row_to_cell_parses_json_columns() -> None:
    chart = _chart().model_dump()
    cell = NotebookService._row_to_cell(
        _cell_row(
            charts=json.dumps([chart]),
            data_source_ids='["src_1"]',
            dashboard_layout='{"order": ["c1"]}',
        )
    )

    assert cell.id == "cell_1"
    assert cell.data_source_ids == ["src_1"]
    assert cell.charts[0].title == "Revenue"
    assert cell.dashboard_layout is not None
    assert cell.dashboard_layout.order == ["c1"]


def test_row_to_cell_tolerates_null_json_columns() -> None:
    cell = NotebookService._row_to_cell(_cell_row(charts=None, data_source_ids=None))

    assert cell.charts == []
    assert cell.data_source_ids == []


def test_row_to_dashboard_version_builds_the_model() -> None:
    version = NotebookService._row_to_dashboard_version(
        {
            "id": "ver_1",
            "cell_id": "cell_1",
            "notebook_id": "nb_1",
            "version_number": 2,
            "reply": "old",
            "charts": json.dumps([_chart().model_dump()]),
            "dashboard_layout": None,
            "created_at": NOW,
        }
    )

    assert version.id == "ver_1"
    assert version.version_number == 2
    assert version.charts[0].id == "c1"


def test_md_and_code_cells_split_source_lines() -> None:
    md = NotebookService._md_cell("one\ntwo\n")
    code = NotebookService._code_cell("print(1)\n")

    assert md["cell_type"] == "markdown"
    assert md["source"] == ["one\n", "two\n"]
    assert code["cell_type"] == "code"
    assert code["source"] == ["print(1)\n"]


@pytest.mark.parametrize(
    ("plot_type", "fragment"),
    [
        ("line", "kind='line'"),
        ("scatter", "kind='scatter'"),
        ("heatmap", "plt.imshow"),
        ("boxplot", "df.boxplot"),
        ("bar", "kind='bar'"),
    ],
)
def test_chart_to_code_renders_each_plot_type(plot_type: str, fragment: str) -> None:
    code = NotebookService._chart_to_code(_chart(plot_type=plot_type))

    assert "pd.DataFrame" in code
    assert fragment in code
    assert "# SQL used to produce this chart" in code


def test_chart_to_code_without_axes_returns_the_dataframe() -> None:
    code = NotebookService._chart_to_code(_chart(x={}, y={}))

    assert code.strip().endswith("df")


def test_code_cell_with_image_embeds_png() -> None:
    cell = NotebookService._code_cell_with_image("df\n", b"png", 3)

    assert cell["execution_count"] == 3
    assert cell["outputs"][0]["data"]["image/png"] == "cG5n"


def test_view_result_to_chart_model_caps_rows() -> None:
    result = {
        "data": pd.DataFrame({"id": [1, 2]}),
        "sql": "SELECT * FROM t",
        "observation": "Two rows",
        "error": None,
    }

    chart = NotebookService._view_result_to_chart_model("show rows", "v1", result)

    assert chart.id == "view_v1"
    assert chart.plot_type == "table"
    assert chart.data == [{"id": 1}, {"id": 2}]


def test_chart_result_to_model_maps_axes_and_truncation() -> None:
    chart = NotebookService._chart_result_to_model(_chart_result())

    assert chart.id == "h1"
    assert chart.plot_type == "line"
    assert chart.x["column"] == "month"
    assert chart.y["column"] == "revenue"
    assert chart.total_row_count == 2
    assert chart.truncated is False


def test_chart_result_to_model_handles_missing_data() -> None:
    result = _chart_result(data=pd.DataFrame())
    result.sql_spec = None

    chart = NotebookService._chart_result_to_model(result)

    assert chart.data == []
    assert chart.x == {}
    assert chart.total_row_count == 0


def test_is_local_source() -> None:
    service = _service()

    assert service._is_local_source(_source(kind="upload")) is True
    assert (
        service._is_local_source(
            _source(kind="connector", connector_id="c1", connector_type="databricks")
        )
        is True
    )
    assert (
        service._is_local_source(
            _source(kind="connector", connector_id="c1", connector_type="postgres")
        )
        is False
    )


def test_resolve_selected_sources_defaults_to_all() -> None:
    service = _service()
    notebook = SimpleNamespace(data_sources=[_source()])

    assert service._resolve_selected_sources(notebook, None) == notebook.data_sources


def test_resolve_selected_sources_rejects_unknown_ids() -> None:
    service = _service()
    notebook = SimpleNamespace(data_sources=[_source()])

    with pytest.raises(ValueError, match="aren't attached"):
        service._resolve_selected_sources(notebook, ["missing"])


def test_resolve_postgres_service_uses_the_app_database_for_local_sources() -> None:
    service = _service()

    assert service._resolve_postgres_service_for_sources([_source()]) is service.postgres


def test_resolve_postgres_service_rejects_mixed_locality() -> None:
    service = _service()
    sources = [
        _source(kind="upload"),
        _source(kind="connector", connector_id="c1", connector_type="postgres"),
    ]

    with pytest.raises(ValueError, match="local data"):
        service._resolve_postgres_service_for_sources(sources)


def test_resolve_postgres_service_rejects_two_connectors(monkeypatch) -> None:
    service = _service()
    sources = [
        _source(kind="connector", connector_id="c1", connector_type="postgres"),
        _source(kind="connector", connector_id="c2", connector_type="postgres"),
    ]

    with pytest.raises(ValueError, match="two different"):
        service._resolve_postgres_service_for_sources(sources)


def test_resolve_postgres_service_requires_a_source() -> None:
    with pytest.raises(NotebookNotReadyError):
        _service()._resolve_postgres_service_for_sources([])


def test_connector_postgres_service_delegates_when_supported() -> None:
    expected = object()
    connector = SimpleNamespace(get_postgres_service=lambda *a, **k: expected)

    assert NotebookService._connector_postgres_service(connector, "c1") is expected


def test_connector_postgres_service_raises_when_unsupported() -> None:
    with pytest.raises(NotImplementedError):
        NotebookService._connector_postgres_service(SimpleNamespace(), "c1")


def test_table_row_count_handles_results_and_errors() -> None:
    service = _service(_DB([{"rows": [{"cnt": 42}]}]))
    assert service._table_row_count(service.postgres, "t") == 42

    service = _service(_DB([{"rows": []}]))
    assert service._table_row_count(service.postgres, "t") is None

    broken = _DB()
    broken.query = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    service = _service(broken)
    assert service._table_row_count(broken, "t") is None


# ——————————————————————————————————————————————————————————————
# Notebook CRUD


def test_create_notebook_inserts_then_reads_back() -> None:
    db = _DB([{"rows": [_notebook_row()]}, {"rows": []}])
    service = _service(db)

    notebook = service.create_notebook("Churn", "desc", owner_user_id="user-1")

    assert notebook.id == "nb_1"
    assert "INSERT INTO sqrl_notebooks" in db.queries[0].sql
    assert db.queries[0].params["owner_user_id"] == "user-1"


def test_list_notebooks_scopes_to_the_owner() -> None:
    db = _DB([{"rows": [_notebook_row()]}, {"rows": []}])
    service = _service(db)

    notebooks = service.list_notebooks(owner_user_id="user-1")

    assert [n.id for n in notebooks] == ["nb_1"]
    assert db.queries[0].params["owner_user_id"] == "user-1"


def test_get_notebook_raises_when_missing() -> None:
    service = _service(_DB([{"rows": []}]))

    with pytest.raises(NotebookNotFoundError):
        service.get_notebook("nb_missing")


def test_delete_notebook_checks_then_deletes() -> None:
    db = _DB([{"rows": [_notebook_row()]}, {"rows": []}])
    service = _service(db)

    service.delete_notebook("nb_1", owner_user_id="user-1")

    assert "DELETE FROM sqrl_notebooks" in db.last_query.sql


def test_list_cells_returns_models() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row()]},
        ]
    )
    service = _service(db)

    cells = service.list_cells("nb_1")

    assert [c.id for c in cells] == ["cell_1"]


def test_unique_label_appends_a_counter() -> None:
    db = _DB(
        [
            {
                "rows": [
                    {"id": "s1", "label": "orders.csv"},
                    {"id": "s2", "label": "orders.csv (2)"},
                ]
            },
            {"rows": [{"id": "s1", "label": "orders.csv"}]},
        ]
    )
    service = _service(db)

    assert service._unique_label("nb_1", "orders.csv") == "orders.csv (3)"
    assert service._unique_label("nb_1", "new.csv") == "new.csv"


def test_unique_label_ignores_the_source_being_renamed() -> None:
    db = _DB([{"rows": [{"id": "s1", "label": "orders.csv"}]}])
    service = _service(db)

    assert (
        service._unique_label("nb_1", "orders.csv", exclude_source_id="s1")
        == "orders.csv"
    )


def test_unique_notebook_name_appends_a_counter() -> None:
    db = _DB([{"rows": [{"name": "Churn (copy)"}]}])
    service = _service(db)

    assert service._unique_notebook_name("Churn (copy)") == "Churn (copy) (2)"
    assert service._unique_notebook_name("Fresh") == "Fresh"
    assert service._unique_notebook_name("") == "Untitled notebook"


def test_rename_notebook_without_changes_only_reads() -> None:
    db = _DB([{"rows": [_notebook_row()]}, {"rows": []}, {"rows": [_notebook_row()]}, {"rows": []}])
    service = _service(db)

    notebook = service.rename_notebook("nb_1")

    assert notebook.id == "nb_1"
    assert not any("UPDATE sqrl_notebooks" in q.sql for q in db.queries)


def test_rename_notebook_updates_name_and_description() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_notebook_row(name="Renamed")]},
            {"rows": []},
        ]
    )
    service = _service(db)

    notebook = service.rename_notebook("nb_1", name="Renamed", description="new")

    assert notebook.name == "Renamed"
    update = next(q for q in db.queries if "UPDATE sqrl_notebooks SET name" in q.sql)
    assert update.params["description"] == "new"


# ——————————————————————————————————————————————————————————————
# Data sources


def test_add_managed_table_data_source_binds_an_app_table() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},  # get_notebook
            {"rows": []},  # sources
            {"rows": []},  # existing binding
            {"rows": [{"column_name": "id", "data_type": "integer", "is_nullable": "NO"}]},
            {"rows": [{"cnt": 3}]},  # row count
            {"rows": []},  # unique label
            {"rows": [_notebook_row()]},  # read back
            {"rows": [_source_row()]},
        ]
    )
    service = _service(db)

    notebook = service.add_managed_table_data_source("nb_1", "etl_out", label="Out")

    assert notebook.id == "nb_1"
    insert = next(q for q in db.queries if "INSERT INTO sqrl_notebook_data_sources" in q.sql)
    assert insert.params["table_name"] == "etl_out"
    assert insert.params["label"] == "Out"
    assert insert.params["row_count"] == 3


def test_add_managed_table_data_source_is_idempotent() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [{"id": "src_1"}]},  # already bound
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
        ]
    )
    service = _service(db)

    notebook = service.add_managed_table_data_source("nb_1", "etl_out")

    assert notebook.id == "nb_1"
    assert not any("INSERT INTO sqrl_notebook_data_sources" in q.sql for q in db.queries)


def test_add_managed_table_data_source_rejects_unknown_tables() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},
            {"rows": []},
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="does not exist"):
        service.add_managed_table_data_source("nb_1", "missing")


def test_create_table_dashboard_cell_persists_a_table_preview() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [{"id": "src_1"}]},
            {"rows": [{"id": 1}]},
            {"rows": [{"cnt": 1}]},
        ]
    )
    service = _service(db)

    cell = service.create_table_dashboard_cell("nb_1", "etl_out", title="Output")

    assert cell.type == "dashboard"
    assert cell.charts[0].plot_type == "table"
    assert cell.charts[0].title == "Output"
    assert cell.data_source_ids == ["src_1"]
    assert any("INSERT INTO sqrl_notebook_cells" in q.sql for q in db.queries)


def test_add_csv_data_source_loads_the_file_and_records_the_source() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},  # unique label
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
        ]
    )
    db.queue_load({"row_count": 12, "columns": [{"name": "id"}]})
    service = _service(db)

    notebook = service.add_csv_data_source(
        "nb_1", "C:/tmp/orders.csv", "orders.csv", source_file_url="s3://sqrl/orders.csv"
    )

    assert notebook.id == "nb_1"
    assert db.loads[0].table_name == "nb_nb_1_orders"
    assert db.loads[0].if_exists == "replace"


def test_add_csv_data_source_uploads_to_minio_when_owner_is_known() -> None:
    uploaded = SimpleNamespace(fileUrl="s3://sqrl/uploads/orders.csv")
    minio = SimpleNamespace(upload_file=lambda **kwargs: uploaded)
    recorded: list[dict] = []

    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
        ]
    )
    db.queue_load({"row_count": 1, "columns": []})
    service = _service(db, minio=minio)
    service.file_service = SimpleNamespace(
        record_upload=lambda file, **kwargs: recorded.append(kwargs)
    )

    service.add_csv_data_source(
        "nb_1", "C:/tmp/orders.csv", "orders.csv", owner_user_id="user-1"
    )

    assert recorded and recorded[0]["notebook_id"] == "nb_1"
    insert = next(q for q in db.queries if "INSERT INTO sqrl_notebook_data_sources" in q.sql)
    assert insert.params["source_file_url"] == "s3://sqrl/uploads/orders.csv"


def test_add_existing_file_data_source_requires_ownership(monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_module,
        "PostgresService",
        lambda *a, **k: SimpleNamespace(retrieve=lambda *a, **k: {"rows": []}, disconnect=lambda: None),
    )
    service = _service(_DB([{"rows": [_notebook_row()]}, {"rows": []}]))

    with pytest.raises(PermissionError):
        service.add_existing_file_data_source(
            "nb_1", "s3://sqrl/orders.csv", owner_user_id="user-1"
        )


def test_add_existing_file_data_source_requires_stored_content(monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_module,
        "PostgresService",
        lambda *a, **k: SimpleNamespace(retrieve=lambda *a, **k: {"rows": [{"id": 1}]}, disconnect=lambda: None),
    )
    minio = SimpleNamespace(retrieve_file=lambda url: None)
    service = _service(
        _DB([{"rows": [_notebook_row()]}, {"rows": []}]), minio=minio
    )

    with pytest.raises(FileNotFoundError):
        service.add_existing_file_data_source(
            "nb_1", "s3://sqrl/orders.csv", owner_user_id="user-1"
        )


def test_add_existing_file_data_source_loads_the_downloaded_bytes(monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_module,
        "PostgresService",
        lambda *a, **k: SimpleNamespace(retrieve=lambda *a, **k: {"rows": [{"id": 1}]}, disconnect=lambda: None),
    )
    minio = SimpleNamespace(
        retrieve_file=lambda url: SimpleNamespace(fileByte=b"a,b\n1,2\n")
    )
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
        ]
    )
    db.queue_load({"row_count": 1, "columns": [{"name": "a"}]})
    service = _service(db, minio=minio)

    notebook = service.add_existing_file_data_source(
        "nb_1", "s3://sqrl/orders.csv", file_name="orders.csv", owner_user_id="user-1"
    )

    assert notebook.id == "nb_1"
    assert db.loads[0].table_name == "nb_nb_1_orders"
    assert any("SET notebook_ids" in q.sql for q in db.queries)


def test_file_owned_by_uses_the_file_table(monkeypatch) -> None:
    calls: list = []

    class FakePostgres:
        def retrieve(self, table, filters=None):
            calls.append((table, filters))
            return {"rows": [{"id": 1}]}

        def disconnect(self) -> None:
            calls.append("disconnect")

    monkeypatch.setattr(notebook_module, "PostgresService", FakePostgres)

    assert _service()._file_owned_by("s3://x", "user-1") is True
    assert calls[0] == ("file", {"file_url": "s3://x", "owner_user_id": "user-1"})
    assert calls[-1] == "disconnect"


def test_bind_and_unbind_upload_are_best_effort() -> None:
    db = _DB()
    service = _service(db)

    service._bind_upload_to_source("s3://x", "nb_1")
    service._unbind_upload_from_source("s3://x", "nb_1")
    service._unbind_upload_from_source(None, "nb_1")

    assert any("notebook_ids" in q.sql for q in db.queries)
    assert any("array_remove" in q.sql for q in db.queries)


def test_drop_table_cascade_swallows_errors() -> None:
    db = _DB()
    db.query = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    service = _service(db)

    service._drop_table_cascade("nb_orders")  # must not raise


def test_add_connector_data_source_binds_a_live_table(monkeypatch) -> None:
    connector_postgres = SimpleNamespace(
        _generate_schema_metadata=lambda table_name: {"columns": [["id", "int"]]},
        query=lambda sql, **kwargs: {"rows": [{"cnt": 7}]},
    )
    connector = SimpleNamespace(
        list_tables=lambda *a, **k: ["orders"],
        get_connection=lambda *a, **k: {"name": "Warehouse", "type": "postgres"},
        preview_table=lambda *a, **k: pd.DataFrame({"id": [1]}),
        get_postgres_service=lambda *a, **k: connector_postgres,
    )
    monkeypatch.setattr(notebook_module, "DataConnectorService", lambda: connector)

    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row(kind="connector")]},
        ]
    )
    service = _service(db)

    notebook = service.add_connector_data_source(
        "nb_1", "conn_1", "orders", owner_user_id="user-1"
    )

    assert notebook.id == "nb_1"
    insert = next(q for q in db.queries if "INSERT INTO sqrl_notebook_data_sources" in q.sql)
    assert insert.params["connector_id"] == "conn_1"
    assert insert.params["label"] == "Warehouse/orders"
    assert insert.params["row_count"] == 7


def test_add_connector_data_source_snapshots_warehouse_tables(monkeypatch) -> None:
    connector = SimpleNamespace(
        list_tables=lambda *a, **k: ["orders"],
        get_connection=lambda *a, **k: {"name": "Lake", "type": "databricks"},
        build_table_query=lambda *a, **k: "SELECT * FROM orders",
        preview_dataframe=lambda *a, **k: pd.DataFrame({"id": [1, 2]}),
    )
    monkeypatch.setattr(notebook_module, "DataConnectorService", lambda: connector)

    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row(kind="connector", connector_type="databricks")]},
        ]
    )
    db.queue_load({"row_count": 2, "columns": [{"name": "id"}]})
    service = _service(db)

    notebook = service.add_connector_data_source(
        "nb_1", "conn_abcdef", "orders", owner_user_id="user-1"
    )

    assert notebook.id == "nb_1"
    assert db.loads[0].table_name == "nb_nb_1_abcdef_orders"
    insert = next(q for q in db.queries if "INSERT INTO sqrl_notebook_data_sources" in q.sql)
    assert insert.params["original_filename"] == "orders"


def test_load_snapshot_rejects_empty_frames(monkeypatch) -> None:
    connector = SimpleNamespace(
        build_table_query=lambda *a, **k: "SELECT 1",
        preview_dataframe=lambda *a, **k: pd.DataFrame(),
    )
    service = _service()

    with pytest.raises(ValueError, match="no columns"):
        service._load_snapshot(connector, "conn_1", "orders", "nb_snapshot")


def test_refresh_data_source_reloads_a_snapshot(monkeypatch) -> None:
    connector = SimpleNamespace(
        build_table_query=lambda *a, **k: "SELECT * FROM orders",
        preview_dataframe=lambda *a, **k: pd.DataFrame({"id": [1]}),
    )
    monkeypatch.setattr(notebook_module, "DataConnectorService", lambda: connector)

    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {
                "rows": [
                    _source_row(
                        kind="connector",
                        connector_type="databricks",
                        connector_id="conn_1",
                        table_name="nb_snapshot",
                        original_filename="orders",
                    )
                ]
            },
            {"rows": []},  # schema cache lookup
            {"rows": [_notebook_row()]},
            {"rows": []},
        ]
    )
    db.queue_load({"row_count": 1, "columns": [{"name": "id"}]})
    service = _service(db)

    notebook = service.refresh_data_source("nb_1", "src_1")

    assert notebook.id == "nb_1"
    assert any("UPDATE sqrl_notebook_data_sources SET row_count" in q.sql for q in db.queries)


def test_refresh_data_source_rejects_live_sources() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_source_row(kind="upload")]},
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="warehouse/lake-backed"):
        service.refresh_data_source("nb_1", "src_1")


def test_refresh_data_source_requires_an_upstream_table() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {
                "rows": [
                    _source_row(
                        kind="connector",
                        connector_type="databricks",
                        connector_id="conn_1",
                        original_filename=None,
                    )
                ]
            },
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="no upstream table"):
        service.refresh_data_source("nb_1", "src_1")


def test_get_data_source_raises_when_missing() -> None:
    service = _service(_DB([{"rows": []}]))

    with pytest.raises(DataSourceNotFoundError):
        service._get_data_source("nb_1", "missing")


def test_remove_data_source_deletes_and_invalidates_cache() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_source_row(source_file_url="s3://x")]},
            {"rows": [{"schema_cache": {"src_1,src_2": {}, "src_2": {}}}]},
            {"rows": [_notebook_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    notebook = service.remove_data_source("nb_1", "src_1")

    assert notebook.id == "nb_1"
    assert any("DELETE FROM sqrl_notebook_data_sources" in q.sql for q in db.queries)
    cache_update = next(q for q in db.queries if "SET schema_cache" in q.sql)
    assert list(json.loads(cache_update.params["schema_cache"])) == ["src_2"]


def test_rename_data_source_updates_label_and_file_name() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_source_row(source_file_url="s3://x")]},
            {"rows": [{"id": "src_1", "label": "orders.csv"}]},
            {"rows": [_notebook_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    notebook = service.rename_data_source("nb_1", "src_1", "renamed.csv")

    assert notebook.id == "nb_1"
    assert any("UPDATE file SET file_name" in q.sql for q in db.queries)


def test_rename_data_source_falls_back_to_the_table_name() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_source_row()]},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    service.rename_data_source("nb_1", "src_1", "   ")

    update = next(q for q in db.queries if "SET label" in q.sql)
    assert update.params["label"] == "nb_orders"


def test_invalidate_schema_cache_entries_is_a_noop_without_a_cache() -> None:
    db = _DB([{"rows": []}])
    service = _service(db)

    service._invalidate_schema_cache_entries("nb_1", "src_1")

    assert not any("SET schema_cache" in q.sql for q in db.queries)


def test_invalidate_schema_cache_entries_leaves_unrelated_bundles() -> None:
    db = _DB([{"rows": [{"schema_cache": {"src_2": {}}}]}])
    service = _service(db)

    service._invalidate_schema_cache_entries("nb_1", "src_1")

    assert not any("SET schema_cache" in q.sql for q in db.queries)


# ——————————————————————————————————————————————————————————————
# Cell execution


def test_run_cell_persists_markdown_without_an_agent() -> None:
    db = _DB([{"rows": [_notebook_row(status="empty")]}, {"rows": []}])
    service = _service(db)

    cell = service.run_cell("nb_1", "markdown", query="## Notes")

    assert cell.type == "markdown"
    assert cell.query == "## Notes"
    assert any("INSERT INTO sqrl_notebook_cells" in q.sql for q in db.queries)


@pytest.mark.parametrize(
    ("cell_type", "query", "message"),
    [
        ("question", "  ", "A question is required"),
        ("bogus", "x", "Unsupported cell type"),
        ("markdown", "  ", "Markdown content is required"),
    ],
)
def test_run_cell_validates_input(cell_type: str, query: str, message: str) -> None:
    db = _DB([{"rows": [_notebook_row()]}, {"rows": []}])
    service = _service(db)

    with pytest.raises(ValueError, match=message):
        service.run_cell("nb_1", cell_type, query=query)


def test_run_cell_requires_a_data_source() -> None:
    db = _DB([{"rows": [_notebook_row(status="empty")]}, {"rows": []}])
    service = _service(db)

    with pytest.raises(NotebookNotReadyError):
        service.run_cell("nb_1", "eda")


def test_run_cell_reuses_a_duplicate_question() -> None:
    duplicate = _cell_row(data_source_ids='["src_1"]', query="Why?  ")
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [duplicate]},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "question", query="why?")

    assert cell.id == "cell_1"
    assert not any("INSERT INTO sqrl_notebook_cells" in q.sql for q in db.queries)


def _install_fake_agent(monkeypatch, agent) -> None:
    monkeypatch.setattr(notebook_module, "TabularDataExploratoryAgent", lambda **kwargs: agent)


def _fake_agent(**overrides):
    agent = SimpleNamespace(
        max_hypotheses=5,
        model=SimpleNamespace(last_input_tokens=11, last_output_tokens=7),
        reconcile_schema_with_database=lambda schema: schema,
        fix_column_types=lambda schema: None,
        generate_schema_details=lambda metadata: {"tables": list(metadata)},
        generate_domain_context=lambda schema: {"domain": "sales"},
        extract_entities_and_metrics=lambda schema, ctx: {"entities": []},
        generate_hypotheses=lambda *a, **k: [_hypothesis()],
        process_hypothesis=lambda *a, **k: _chart_result(),
        generate_summary=lambda succeeded: {
            "dataset_description": "desc",
            "key_findings": [{"finding": "f1"}],
        },
        classify_question_intent=lambda question: {"needs_chart": True, "chart_type": "line"},
        process_view_request=lambda *a, **k: {},
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def test_run_cell_eda_builds_charts_and_records_tokens(monkeypatch) -> None:
    agent = _fake_agent()
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},  # cache miss
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
            {"rows": [_notebook_row()]},  # previous-questions read
            {"rows": [_source_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "eda")

    assert cell.status == "complete"
    assert cell.charts[0].title == "Revenue"
    assert "desc" in cell.reply
    assert "f1" in cell.reply
    assert cell.prompt_tokens == 11
    assert cell.completion_tokens == 7


def test_run_cell_dashboard_uses_the_tile_override(monkeypatch) -> None:
    seen: dict = {}

    def generate_hypotheses(*args, **kwargs):
        seen["max"] = agent.max_hypotheses
        return [_hypothesis()]

    agent = _fake_agent(generate_hypotheses=generate_hypotheses)
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "dashboard")

    assert cell.type == "dashboard"
    assert seen["max"] == NotebookService._DASHBOARD_TILE_COUNT
    assert agent.max_hypotheses == 5


def test_run_cell_records_failures_as_error_cells(monkeypatch) -> None:
    agent = _fake_agent(
        generate_hypotheses=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down"))
    )
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "eda")

    assert cell.status == "error"
    assert cell.error == "llm down"


def test_run_question_creates_a_view_when_no_chart_is_needed(monkeypatch) -> None:
    agent = _fake_agent(
        classify_question_intent=lambda question: {"needs_chart": False},
        process_view_request=lambda *a, **k: {
            "data": pd.DataFrame({"id": [1]}),
            "sql": "SELECT * FROM t",
            "observation": "One row",
            "error": None,
        },
    )
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_notebook_row()]},  # duplicate-question scan
            {"rows": [_source_row()]},
            {"rows": []},  # no existing question cells
            {"rows": []},  # cache miss
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "question", query="show me the rows")

    assert cell.status == "complete"
    assert cell.charts[0].plot_type == "table"
    assert "Saved as view" in cell.reply


def test_run_question_reports_chart_failures(monkeypatch) -> None:
    agent = _fake_agent(
        process_hypothesis=lambda *a, **k: _chart_result(
            data=pd.DataFrame(), error="bad SQL"
        )
    )
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_notebook_row()]},  # duplicate-question scan
            {"rows": [_source_row()]},
            {"rows": []},  # no existing question cells
            {"rows": []},  # cache miss
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
        ]
    )
    service = _service(db)

    cell = service.run_cell("nb_1", "question", query="revenue?")

    assert "wasn't able to produce a chart" in cell.reply
    assert cell.charts[0].error == "bad SQL"


def test_repair_column_types_never_breaks_a_cell(monkeypatch) -> None:
    agent = _fake_agent(
        reconcile_schema_with_database=lambda schema: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    service = _service()

    service._repair_column_types(agent, [_source()])  # must not raise


def test_get_or_build_schema_cache_returns_a_memoized_bundle(monkeypatch) -> None:
    service = _service(_DB([{"rows": [{"schema_cache": {"src_1": {"cached": True}}}]}]))

    cache = service._get_or_build_schema_cache("nb_1", [_source()])

    assert cache == {"cached": True}


def test_get_or_build_schema_cache_computes_and_persists(monkeypatch) -> None:
    agent = _fake_agent()
    _install_fake_agent(monkeypatch, agent)
    db = _DB(
        [
            {"rows": []},
            {"rows": [{"table_name": "nb_orders", "raw_schema": '{"columns": []}'}]},
            {"rows": []},
        ]
    )
    service = _service(db)

    cache = service._get_or_build_schema_cache("nb_1", [_source()])

    assert cache["domain_context"] == {"domain": "sales"}
    assert "src_1" in json.loads(db.queries[-1].params["schema_cache"])


# ——————————————————————————————————————————————————————————————
# Cells: read / update / delete / order


def test_get_cell_raises_when_missing() -> None:
    service = _service(_DB([{"rows": []}]))

    with pytest.raises(CellNotFoundError):
        service.get_cell("nb_1", "missing")


def test_delete_cell_checks_then_deletes() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row()]},
        ]
    )
    service = _service(db)

    service.delete_cell("nb_1", "cell_1")

    assert any("DELETE FROM sqrl_notebook_cells" in q.sql for q in db.queries)


def test_update_cell_rewrites_markdown_in_place() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_cell_row(type="markdown", query="old")]},
        ]
    )
    service = _service(db)

    cell = service.update_cell("nb_1", "cell_1", query="new")

    assert cell.query == "new"
    assert any("UPDATE sqrl_notebook_cells SET query" in q.sql for q in db.queries)


def test_update_cell_requires_a_data_source() -> None:
    db = _DB([{"rows": [_notebook_row(status="empty")]}, {"rows": []}])
    service = _service(db)

    with pytest.raises(NotebookNotReadyError):
        service.update_cell("nb_1", "cell_1", query="x")


def test_update_cell_regenerates_and_archives_the_old_dashboard(monkeypatch) -> None:
    agent = _fake_agent()
    _install_fake_agent(monkeypatch, agent)
    existing = _cell_row(
        type="dashboard",
        charts=json.dumps([_chart().model_dump()]),
        data_source_ids='["src_1"]',
    )
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [existing]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [{"max_ver": 1}]},  # archive version number
            {"rows": []},  # cache miss
            {"rows": [{"table_name": "nb_orders", "raw_schema": None}]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": []},  # previous questions
        ]
    )
    service = _service(db)

    cell = service.update_cell("nb_1", "cell_1")

    assert cell.status == "complete"
    assert any("INSERT INTO sqrl_notebook_dashboard_versions" in q.sql for q in db.queries)
    assert any("UPDATE sqrl_notebook_cells SET query" in q.sql for q in db.queries)


def test_preview_data_source_returns_rows() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [{"id": 1, "total": 2}]},
        ]
    )
    service = _service(db)

    preview = service.preview_data_source("nb_1", limit=10)

    assert preview.table_name == "nb_orders"
    assert preview.columns == ["id", "total"]
    assert preview.rows == [{"id": 1, "total": 2}]


def test_preview_data_source_requires_a_ready_notebook() -> None:
    db = _DB([{"rows": [_notebook_row(status="empty")]}, {"rows": []}])
    service = _service(db)

    with pytest.raises(NotebookNotReadyError):
        service.preview_data_source("nb_1")


def test_reorder_cells_rejects_unknown_ids() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row()]},
        ]
    )
    service = _service(db)

    with pytest.raises(CellNotFoundError, match="not in notebook"):
        service.reorder_cells("nb_1", ["missing"])


def test_reorder_cells_rejects_missing_ids() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row()]},
        ]
    )
    service = _service(db)

    with pytest.raises(CellNotFoundError, match="missing these cell ids"):
        service.reorder_cells("nb_1", [])


def test_reorder_cells_writes_each_position() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_cell_row(), _cell_row(id="cell_2")]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_cell_row()]},
        ]
    )
    service = _service(db)

    cells = service.reorder_cells("nb_1", ["cell_2", "cell_1"])

    assert cells[0].id == "cell_1"
    positions = [q.params["order_index"] for q in db.queries if "SET order_index" in q.sql]
    assert positions == [0, 1]


# ——————————————————————————————————————————————————————————————
# Dashboard layout and versions


def test_save_dashboard_layout_requires_a_dashboard_cell() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row(type="question")]},
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="dashboard cells"):
        service.save_dashboard_layout("nb_1", "cell_1", DashboardLayout(order=["c1"]))


def test_save_dashboard_layout_persists_the_json() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_cell_row(type="dashboard")]},
            {"rows": [_cell_row(type="dashboard", dashboard_layout='{"order": ["c1"]}')]},
        ]
    )
    service = _service(db)

    cell = service.save_dashboard_layout("nb_1", "cell_1", DashboardLayout(order=["c1"]))

    assert cell.dashboard_layout is not None
    assert cell.dashboard_layout.order == ["c1"]


def test_list_dashboard_versions_requires_a_dashboard_cell() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row(type="question")]},
        ]
    )
    service = _service(db)

    with pytest.raises(ValueError, match="Version history"):
        service.list_dashboard_versions("nb_1", "cell_1")


def test_list_dashboard_versions_returns_newest_first() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row(type="dashboard")]},
            {
                "rows": [
                    {
                        "id": "ver_2",
                        "cell_id": "cell_1",
                        "notebook_id": "nb_1",
                        "version_number": 2,
                        "reply": "",
                        "charts": "[]",
                        "dashboard_layout": None,
                        "created_at": NOW,
                    }
                ]
            },
        ]
    )
    service = _service(db)

    versions = service.list_dashboard_versions("nb_1", "cell_1")

    assert versions[0].version_number == 2


def test_get_dashboard_version_raises_when_missing() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": []},
            {"rows": [_cell_row(type="dashboard")]},
            {"rows": []},
        ]
    )
    service = _service(db)

    with pytest.raises(CellNotFoundError):
        service.get_dashboard_version("nb_1", "cell_1", "ver_missing")


def test_next_dashboard_version_number_defaults_to_one() -> None:
    service = _service(_DB([{"rows": []}]))

    assert service._next_dashboard_version_number("cell_1") == 1


def test_archive_dashboard_version_skips_empty_or_non_dashboard_cells() -> None:
    db = _DB()
    service = _service(db)

    service._archive_dashboard_version(
        NotebookCell(id="c", notebook_id="nb", type="question")
    )
    service._archive_dashboard_version(
        NotebookCell(id="c", notebook_id="nb", type="dashboard")
    )

    assert not any("INSERT INTO sqrl_notebook_dashboard_versions" in q.sql for q in db.queries)


# ——————————————————————————————————————————————————————————————
# Duplication and export


def test_duplicate_notebook_copies_sources_and_remaps_cells() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [{"status": "ready"}]},
            {"rows": [{"name": "Churn (copy)"}]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_cell_row(data_source_ids='["src_1"]')]},
            {"rows": [_notebook_row(id="nb_2")]},
            {"rows": []},
        ]
    )
    service = _service(db)

    notebook = service.duplicate_notebook("nb_1", owner_user_id="user-1")

    assert notebook.id == "nb_2"
    cell_insert = next(q for q in db.queries if "INSERT INTO sqrl_notebook_cells" in q.sql)
    assert json.loads(cell_insert.params["data_source_ids"]) != ["src_1"]


def test_export_notebook_ipynb_embeds_charts_and_skips_errors() -> None:
    cells = [
        _cell_row(type="markdown", query="# Notes"),
        _cell_row(
            charts=json.dumps([_chart().model_dump(), _chart(id="c2", error="bad").model_dump()])
        ),
    ]
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {"rows": cells},
        ]
    )
    service = _service(db)

    payload = json.loads(service.export_notebook_ipynb("nb_1"))

    assert payload["nbformat"] == 4
    types = [cell["cell_type"] for cell in payload["cells"]]
    assert types.count("code") == 2  # setup cell + one rendered chart
    images = [
        output
        for cell in payload["cells"]
        for output in cell.get("outputs", [])
        if output.get("output_type") == "display_data"
    ]
    assert images


def test_render_chart_png_returns_none_without_axes() -> None:
    assert NotebookService._render_chart_png(_chart(x={})) is None
    assert NotebookService._render_chart_png(_chart(data=[])) is None


def test_render_chart_png_returns_none_when_columns_are_missing() -> None:
    chart = _chart(
        x={"column": "nope", "label": "Nope", "type": "nominal"},
        y={"column": "revenue", "label": "Revenue", "type": "quantitative"},
    )

    assert NotebookService._render_chart_png(chart) is None


@pytest.mark.parametrize("plot_type", ["line", "scatter", "heatmap", "boxplot", "bar"])
def test_render_chart_png_renders_supported_plot_types(plot_type: str) -> None:
    png = NotebookService._render_chart_png(_chart(plot_type=plot_type))

    assert png is not None
    assert png.startswith(b"\x89PNG")


def test_previous_eda_questions_only_counts_matching_source_sets() -> None:
    db = _DB(
        [
            {"rows": [_notebook_row()]},
            {"rows": [_source_row()]},
            {
                "rows": [
                    _cell_row(
                        type="eda",
                        data_source_ids='["src_1"]',
                        charts=json.dumps([_chart().model_dump()]),
                    ),
                    _cell_row(
                        id="cell_2",
                        type="eda",
                        data_source_ids='["src_2"]',
                        charts=json.dumps([_chart(id="c9", question="other?").model_dump()]),
                    ),
                ]
            },
        ]
    )
    service = _service(db)

    questions = service._previous_eda_questions("nb_1", ["src_1"])

    assert questions == ["revenue over time?"]
