"""Tests for :class:`DataConnectorService`.

Everything runs against :class:`tests.fakes.FakeDB`; live drivers are replaced
through the service's dispatch tables so no network is touched.
"""
from __future__ import annotations

import importlib
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pandas as pd
import pytest
from cryptography.fernet import Fernet

from squirrel.constants.connector import ConnectorType, get_connector_type_spec
from squirrel.schemas.connector import (
    ConnectorInUseError,
    ConnectorNotFoundError,
    ConnectorValidationError,
)
from squirrel.services.data_connector.DataConnectorService import (
    MAX_ROWS,
    DataConnectorService,
    _clamp_limit,
    _jsonable,
    _strip_literals_and_comments,
    validate_identifier,
    validate_read_query,
)
from tests.fakes import FakeDB

dc_module = importlib.import_module(
    "squirrel.services.data_connector.DataConnectorService"
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

PG_CONFIG = {
    "host": "db.example.com",
    "port": "5432",
    "database": "analytics",
    "username": "squirrel",
    "password": "s3cret",
}


def _row(**overrides):
    row = {
        "connector_id": "conn_1",
        "owner_user_id": "user-1",
        "name": "Warehouse",
        "type": ConnectorType.POSTGRES,
        "status": "untested",
        "config": dict(PG_CONFIG),
        "error": None,
        "last_tested_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _service(db: FakeDB | None = None) -> DataConnectorService:
    fake = db or FakeDB()
    service = DataConnectorService(db=fake)
    fake.executes.clear()
    return service


@pytest.fixture()
def fernet_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(dc_module.settings, "SQUIRREL_CONNECTOR_SECRET_KEY", key)
    return key


# ——————————————————————————————————————————————————————————————
# Schema bootstrap


def test_ensure_table_creates_table_and_indexes() -> None:
    db = FakeDB()

    DataConnectorService(db=db)

    assert len(db.executes) == 4
    assert "CREATE TABLE IF NOT EXISTS data_connections" in db.executes[0].sql
    assert "owner_user_id" in db.executes[1].sql
    assert "idx_data_connections_owner_created" in db.executes[2].sql
    assert "idx_data_connections_type" in db.executes[3].sql


def test_ensure_table_swallows_errors(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(
        db, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )

    DataConnectorService(db=db)  # does not raise


# ——————————————————————————————————————————————————————————————
# Validation helpers


def test_validate_config_rejects_unknown_fields() -> None:
    with pytest.raises(ConnectorValidationError, match="Unknown field"):
        DataConnectorService._validate_config(
            ConnectorType.POSTGRES, {**PG_CONFIG, "sslmode": "require"}
        )


def test_validate_config_requires_mandatory_fields() -> None:
    with pytest.raises(ConnectorValidationError, match="Missing required field"):
        DataConnectorService._validate_config(
            ConnectorType.POSTGRES, {"host": "db", "database": "x"}
        )


def test_validate_config_accepts_optional_port() -> None:
    DataConnectorService._validate_config(
        ConnectorType.POSTGRES,
        {"host": "db", "database": "x", "username": "u", "password": "p"},
    )


def test_validate_config_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unknown connector type"):
        DataConnectorService._validate_config("oracle", {})


# ——————————————————————————————————————————————————————————————
# CRUD


def test_create_connection_encrypts_secret(fernet_key: str) -> None:
    db = FakeDB(insert_results=[[_row()]])
    service = _service(db)

    row = service.create_connection("Warehouse", ConnectorType.POSTGRES, PG_CONFIG, "user-1")

    assert row["connector_id"] == "conn_1"
    params = db.inserts[0].params
    assert params["owner_user_id"] == "user-1"
    assert params["status"] == "untested"
    assert params["config"] != PG_CONFIG["password"]
    assert '"password": "fernet:' in params["config"]
    assert params["connector_id"].startswith("conn_")


def test_create_connection_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unknown connector type"):
        _service().create_connection("X", "oracle", {})


def test_create_connection_rejects_invalid_config() -> None:
    with pytest.raises(ConnectorValidationError):
        _service().create_connection("X", ConnectorType.POSTGRES, {"host": "db"})


def test_create_connection_raises_when_insert_fails() -> None:
    service = _service(FakeDB(insert_results=[[]]))

    with pytest.raises(RuntimeError, match="Failed to create connection row"):
        service.create_connection("X", ConnectorType.POSTGRES, PG_CONFIG)


def test_list_connections_filters_and_sorts() -> None:
    db = FakeDB(
        retrieve_results=[
            {
                "rows": [
                    _row(connector_id="old", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
                    _row(connector_id="new", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                ]
            }
        ]
    )
    service = _service(db)

    rows = service.list_connections(owner_user_id="user-1", limit=1)

    assert [r["connector_id"] for r in rows] == ["new"]
    assert db.retrieves[0].filters == {"owner_user_id": "user-1"}


def test_list_connections_without_owner_returns_all() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)

    assert len(service.list_connections()) == 1
    assert db.retrieves[0].filters == {}


def test_list_connections_handles_empty_results() -> None:
    assert _service().list_connections() == []


def test_get_connection_filters_by_owner() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)

    assert service.get_connection("conn_1", owner_user_id="user-1")["name"] == "Warehouse"
    assert db.retrieves[0].filters == {
        "connector_id": "conn_1",
        "owner_user_id": "user-1",
    }


def test_get_connection_raises_when_missing() -> None:
    with pytest.raises(ConnectorNotFoundError):
        _service().get_connection("conn_missing")


def test_update_connection_without_fields_returns_row() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)

    row = service.update_connection("conn_1")

    assert row["name"] == "Warehouse"
    assert db.executes == []


def test_update_connection_rejects_blank_name() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)

    with pytest.raises(ValueError, match="cannot be empty"):
        service.update_connection("conn_1", name="   ")


def test_update_connection_keeps_existing_secret(fernet_key: str) -> None:
    existing = {**PG_CONFIG, "password": "fernet:stored"}
    db = FakeDB(
        retrieve_results=[
            {"rows": [_row(config=existing)]},
            {"rows": [_row(config=existing, name="Renamed")]},
        ]
    )
    service = _service(db)

    service.update_connection(
        "conn_1",
        name="Renamed",
        config={
            "host": "db.example.com",
            "database": "analytics",
            "username": "squirrel",
            "password": "",
        },
    )

    params = db.executes[0].params
    assert '"password": "fernet:stored"' in params["config"]
    assert params["status"] == "untested"
    assert params["error"] is None


def test_update_connection_encrypts_new_secret(fernet_key: str) -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_row()]},
            {"rows": [_row()]},
        ]
    )
    service = _service(db)

    service.update_connection("conn_1", config={**PG_CONFIG, "password": "new-secret"})

    assert "fernet:" in db.executes[0].params["config"]
    assert "new-secret" not in db.executes[0].params["config"]


def test_update_connection_validates_merged_config() -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)

    with pytest.raises(ConnectorValidationError):
        service.update_connection("conn_1", config={"host": "db"})


def test_update_connection_row_serializes_json_columns() -> None:
    db = FakeDB()
    service = _service(db)

    service._update_connection_row("conn_1", {"config": {"a": "b"}, "name": "N"})

    params = db.executes[0].params
    assert params["config"] == '{"a": "b"}'
    assert "config = %(config)s::jsonb" in db.executes[0].sql
    assert "name = %(name)s" in db.executes[0].sql
    assert "updated_at = %(updated_at)s" in db.executes[0].sql


# ——————————————————————————————————————————————————————————————
# Usage / delete


def test_connector_usage_collects_notebooks_and_workspaces() -> None:
    db = FakeDB(
        results=[
            {"rows": [{"name": "Churn notebook"}]},
            {"rows": [{"name": "Churn workspace"}]},
        ]
    )
    service = _service(db)

    usage = service._get_connector_usage("conn_1")

    assert usage == {"notebooks": ["Churn notebook"], "workspaces": ["Churn workspace"]}


def test_connector_usage_tolerates_missing_tables() -> None:
    db = FakeDB()
    db.query = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("missing"))
    service = _service(db)

    assert service._get_connector_usage("conn_1") == {"notebooks": [], "workspaces": []}


def test_delete_connection_rejects_in_use_connectors(monkeypatch) -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)
    monkeypatch.setattr(
        service,
        "_get_connector_usage",
        lambda connector_id: {"notebooks": ["Churn"], "workspaces": []},
    )

    with pytest.raises(ConnectorInUseError, match=r"notebook\(s\) \[Churn\]"):
        service.delete_connection("conn_1")


def test_delete_connection_removes_row(monkeypatch) -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)
    monkeypatch.setattr(
        service, "_get_connector_usage", lambda connector_id: {"notebooks": [], "workspaces": []}
    )

    service.delete_connection("conn_1", owner_user_id="user-1")

    assert "DELETE FROM data_connections" in db.executes[0].sql
    assert db.executes[0].params == {"connector_id": "conn_1"}


def test_delete_connection_reraises_database_errors(monkeypatch) -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}])
    service = _service(db)
    monkeypatch.setattr(
        service, "_get_connector_usage", lambda connector_id: {"notebooks": [], "workspaces": []}
    )
    monkeypatch.setattr(
        db, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )

    with pytest.raises(RuntimeError, match="down"):
        service.delete_connection("conn_1")


# ——————————————————————————————————————————————————————————————
# Introspection


def test_resolve_returns_type_and_decrypted_config(fernet_key: str) -> None:
    encrypted = dc_module._encrypt_secret("s3cret")
    db = FakeDB(retrieve_results=[{"rows": [_row(config={"password": encrypted})]}])
    service = _service(db)

    connector_type, config = service._resolve("conn_1")

    assert connector_type == ConnectorType.POSTGRES
    assert config["password"] == "s3cret"


def test_list_tables_uses_dispatch(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setitem(
        DataConnectorService._TABLE_LIST_DISPATCH,
        ConnectorType.POSTGRES,
        lambda config: ["public.orders"],
    )

    assert service.list_tables("conn_1") == ["public.orders"]


def test_list_tables_rejects_unregistered_types(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setitem(DataConnectorService._TABLE_LIST_DISPATCH, ConnectorType.POSTGRES, None)

    with pytest.raises(NotImplementedError, match="isn't wired up yet"):
        service.list_tables("conn_1")


def test_describe_table_builds_information_schema_query(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    def fake_runner(config, query, limit):
        captured["query"] = query
        captured["limit"] = limit
        return ["column_name", "data_type"], [("id", "integer"), ("name", "text")]

    monkeypatch.setitem(DataConnectorService._QUERY_DISPATCH, ConnectorType.POSTGRES, fake_runner)

    columns = service.describe_table("conn_1", "analytics.orders")

    assert columns == [
        {"column_name": "id", "data_type": "integer"},
        {"column_name": "name", "data_type": "text"},
    ]
    assert "FROM information_schema.columns" in captured["query"]
    assert "table_name = 'orders'" in captured["query"]
    assert "table_schema = 'analytics'" in captured["query"]
    assert captured["limit"] == MAX_ROWS


def test_describe_table_defaults_to_public_schema(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    def fake_runner(config, query, limit):
        captured["query"] = query
        return ["column_name", "data_type"], []

    monkeypatch.setitem(DataConnectorService._QUERY_DISPATCH, ConnectorType.POSTGRES, fake_runner)

    service.describe_table("conn_1", "orders")

    assert "table_schema = 'public'" in captured["query"]


def test_describe_table_validates_identifiers() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))

    with pytest.raises(ValueError, match="qualified SQL identifier"):
        service.describe_table("conn_1", "orders; DROP TABLE x")


def test_build_table_query_selects_everything(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setattr(service, "list_tables", lambda *a, **k: ["public.orders"])

    assert service.build_table_query("conn_1", "public.orders") == 'SELECT * FROM "public"."orders"'


def test_build_table_query_rejects_unknown_tables(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setattr(service, "list_tables", lambda *a, **k: ["public.orders"])

    with pytest.raises(ValueError, match="not a known table"):
        service.build_table_query("conn_1", "public.secret")


def test_build_table_query_quotes_selected_columns(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setattr(service, "list_tables", lambda *a, **k: ["public.orders"])
    monkeypatch.setattr(service, "_known_columns", lambda *a, **k: ["id", "total"])

    query = service.build_table_query("conn_1", "public.orders", ["id", "total"])

    assert query == 'SELECT "id", "total" FROM "public"."orders"'


def test_build_table_query_rejects_unknown_columns(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    monkeypatch.setattr(service, "list_tables", lambda *a, **k: ["public.orders"])
    monkeypatch.setattr(service, "_known_columns", lambda *a, **k: ["id"])

    with pytest.raises(ValueError, match="Unknown column"):
        service.build_table_query("conn_1", "public.orders", ["secret"])


def test_preview_table_wraps_query_with_limit(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    monkeypatch.setattr(service, "build_table_query", lambda *a, **k: "SELECT * FROM t")
    monkeypatch.setattr(
        service,
        "preview_dataframe",
        lambda connector_id, query, limit=50, owner_user_id=None: captured.update(
            query=query, limit=limit
        )
        or pd.DataFrame(),
    )

    service.preview_table("conn_1", "t", limit=3)

    assert captured == {"query": "SELECT * FROM t", "limit": 3}


def test_preview_dataframe_caps_rows() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}
    service.fetch_dataframe = lambda connector_id, query, owner_user_id=None: captured.update(
        query=query
    ) or pd.DataFrame()

    service.preview_dataframe("conn_1", "SELECT 1", limit=7)

    assert captured["query"].endswith("AS _squirrel_preview LIMIT 7")


# ——————————————————————————————————————————————————————————————
# Connection testing


def test_test_connection_marks_success(monkeypatch) -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}, {"rows": [_row(status="connected")]}])
    service = _service(db)
    calls = []
    monkeypatch.setitem(
        DataConnectorService._TEST_DISPATCH,
        ConnectorType.POSTGRES,
        lambda config: calls.append(config),
    )

    row = service.test_connection("conn_1")

    assert row["status"] == "connected"
    assert calls[0]["password"] == "s3cret"
    assert db.executes[0].params["status"] == "connected"
    assert db.executes[0].params["error"] is None


def test_test_connection_marks_failure(monkeypatch) -> None:
    db = FakeDB(retrieve_results=[{"rows": [_row()]}, {"rows": [_row(status="error")]}])
    service = _service(db)
    monkeypatch.setitem(
        DataConnectorService._TEST_DISPATCH,
        ConnectorType.POSTGRES,
        lambda config: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    service.test_connection("conn_1")

    assert db.executes[0].params["status"] == "error"
    assert db.executes[0].params["error"] == "connection refused"


def test_test_connection_without_tester_reports_error() -> None:
    db = FakeDB(
        retrieve_results=[
            {"rows": [_row(type="snowflake")]},
            {"rows": [_row(type="snowflake", status="error")]},
        ]
    )
    service = _service(db)

    service.test_connection("conn_1")

    assert db.executes[0].params["status"] == "error"
    assert "No tester registered" in db.executes[0].params["error"]


# ——————————————————————————————————————————————————————————————
# Data retrieval


def test_fetch_query_bounds_and_jsonifies_rows(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    def fake_runner(config, query, limit):
        captured["query"] = query
        captured["limit"] = limit
        return ["id", "when", "amount"], [(1, datetime(2026, 1, 1), Decimal("1.50"))]

    monkeypatch.setitem(DataConnectorService._QUERY_DISPATCH, ConnectorType.POSTGRES, fake_runner)

    result = service.fetch_query("conn_1", "SELECT * FROM orders", limit=5)

    assert result["columns"] == ["id", "when", "amount"]
    assert result["rows"] == [{"id": 1, "when": "2026-01-01T00:00:00", "amount": 1.5}]
    assert result["row_count"] == 1
    assert captured["query"].endswith("AS source_data LIMIT 5")


def test_fetch_query_rejects_write_statements() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))

    with pytest.raises(ValueError, match="Only SELECT or WITH"):
        service.fetch_query("conn_1", "DELETE FROM orders")


def test_fetch_query_rejects_unsupported_types(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row(type="snowflake")]}]))

    with pytest.raises(NotImplementedError, match="not supported"):
        service.fetch_query("conn_1", "SELECT 1")


def test_run_wraps_driver_errors() -> None:
    def boom(config, query, limit):
        raise RuntimeError("driver exploded")

    with pytest.raises(RuntimeError, match="Query failed: driver exploded"):
        DataConnectorService._run(boom, {}, "SELECT 1", 1)


def test_run_reraises_validation_errors() -> None:
    def boom(config, query, limit):
        raise ValueError("bad query")

    with pytest.raises(ValueError, match="bad query"):
        DataConnectorService._run(boom, {}, "SELECT 1", 1)


def test_fetch_dataframe_postgres_uses_sqlalchemy(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    class FakeEngine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()

    def fake_create_engine(url):
        captured["url"] = url
        return engine

    def fake_read_sql(query, engine_):
        captured["query"] = query
        return pd.DataFrame({"id": [1]})

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    monkeypatch.setattr(dc_module.pd, "read_sql_query", fake_read_sql)

    df = service.fetch_dataframe("conn_1", "SELECT * FROM orders")

    assert list(df.columns) == ["id"]
    assert captured["query"] == "SELECT * FROM orders"
    assert captured["url"].drivername == "postgresql+psycopg"
    assert captured["url"].database == "analytics"
    assert engine.disposed is True


def test_fetch_dataframe_requires_a_query() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))

    with pytest.raises(NotImplementedError, match="requires a 'query'"):
        service.fetch_dataframe("conn_1")


def test_fetch_dataframe_databricks_builds_dataframe(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row(type=ConnectorType.DATABRICKS)]}]))
    monkeypatch.setitem(
        DataConnectorService._QUERY_DISPATCH,
        ConnectorType.DATABRICKS,
        lambda config, query, limit: (["id"], [(1,), (2,)]),
    )

    df = service.fetch_dataframe("conn_1", "SELECT id FROM t")

    assert df.to_dict("list") == {"id": [1, 2]}


def test_fetch_dataframe_rejects_unwired_types() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row(type="snowflake")]}]))

    with pytest.raises(NotImplementedError, match="isn't wired up yet"):
        service.fetch_dataframe("conn_1", "SELECT 1")


# ——————————————————————————————————————————————————————————————
# Secret helpers / public serialization


def test_encrypt_decrypt_round_trip_with_fernet(fernet_key: str) -> None:
    token = dc_module._encrypt_secret("hunter2")

    assert token.startswith("fernet:")
    assert dc_module._decrypt_secret(token) == "hunter2"


def test_encrypt_falls_back_to_base64(monkeypatch) -> None:
    monkeypatch.setattr(dc_module.settings, "SQUIRREL_CONNECTOR_SECRET_KEY", "")

    token = dc_module._encrypt_secret("hunter2")

    assert token.startswith("b64:")
    assert dc_module._decrypt_secret(token) == "hunter2"


def test_decrypt_fernet_without_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(dc_module.settings, "SQUIRREL_CONNECTOR_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="no usable key"):
        dc_module._decrypt_secret("fernet:whatever")


def test_decrypt_invalid_fernet_token_raises(fernet_key: str) -> None:
    with pytest.raises(RuntimeError, match="invalid token"):
        dc_module._decrypt_secret("fernet:not-a-real-token")


def test_decrypt_passes_through_plain_values() -> None:
    assert DataConnectorService._decrypt_config({"host": "db", "port": "5432"}) == {
        "host": "db",
        "port": "5432",
    }


def test_encrypt_config_only_touches_secret_keys(fernet_key: str) -> None:
    stored = DataConnectorService._encrypt_config(ConnectorType.POSTGRES, PG_CONFIG)

    assert stored["host"] == "db.example.com"
    assert stored["password"].startswith("fernet:")


def test_to_public_dict_redacts_secrets() -> None:
    public = DataConnectorService.to_public_dict(_row())

    assert public["config"]["password"] == ""
    assert public["config"]["host"] == "db.example.com"
    assert public["connector_id"] == "conn_1"


def test_to_public_dict_handles_unknown_types() -> None:
    public = DataConnectorService.to_public_dict(
        _row(type="snowflake", config={"token": "abc"})
    )

    assert public["config"] == {"token": "abc"}


# ——————————————————————————————————————————————————————————————
# Postgres service factory


def test_get_postgres_service_builds_connection_string(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row()]}]))
    captured = {}

    def fake_postgres(connection_string):
        captured["dsn"] = connection_string
        return "postgres-service"

    monkeypatch.setattr(dc_module, "PostgresService", fake_postgres)

    assert service.get_postgres_service("conn_1") == "postgres-service"
    assert captured["dsn"] == "postgresql://squirrel:s3cret@db.example.com:5432/analytics"


def test_get_postgres_service_requires_ssl_for_lakebase(monkeypatch) -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row(type=ConnectorType.LAKEBASE)]}]))
    captured = {}
    monkeypatch.setattr(
        dc_module,
        "PostgresService",
        lambda connection_string: captured.update(dsn=connection_string) or "service",
    )

    assert service.get_postgres_service("conn_1") == "service"
    assert captured["dsn"].endswith("?sslmode=require")


def test_get_postgres_service_rejects_other_types() -> None:
    service = _service(FakeDB(retrieve_results=[{"rows": [_row(type=ConnectorType.DATABRICKS)]}]))

    with pytest.raises(NotImplementedError, match="only supports Postgres-compatible"):
        service.get_postgres_service("conn_1")


# ——————————————————————————————————————————————————————————————
# SQL safety helpers


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM orders",
        "  select id from orders;  ",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT 'delete' AS word",
        "SELECT * FROM orders -- drop table x",
        "SELECT \"update\" FROM t",
    ],
)
def test_validate_read_query_accepts_reads(query: str) -> None:
    assert validate_read_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "DELETE FROM orders",
        "UPDATE orders SET x = 1",
        "SELECT 1; SELECT 2",
        "INSERT INTO orders VALUES (1)",
        "SELECT * FROM orders FOR UPDATE",
    ],
)
def test_validate_read_query_rejects_writes(query: str) -> None:
    with pytest.raises(ValueError):
        validate_read_query(query)


def test_strip_literals_and_comments() -> None:
    cleaned = _strip_literals_and_comments(
        "SELECT 'delete' /* drop */ FROM \"update\" -- truncate\nWHERE x = 1"
    )

    assert "delete" not in cleaned
    assert "drop" not in cleaned
    assert "truncate" not in cleaned


def test_validate_identifier_accepts_qualified_names() -> None:
    assert validate_identifier("analytics.orders") == "analytics.orders"
    assert validate_identifier("public.my_table$1") == "public.my_table$1"


@pytest.mark.parametrize("value", ["", "1table", "schema..table", "orders; drop", "a b"])
def test_validate_identifier_rejects_unsafe_names(value: str) -> None:
    with pytest.raises(ValueError, match="qualified SQL identifier"):
        validate_identifier(value)


def test_clamp_limit_bounds() -> None:
    assert _clamp_limit(10) == 10
    assert _clamp_limit(MAX_ROWS * 2) == MAX_ROWS
    with pytest.raises(ValueError, match="limit must be >= 1"):
        _clamp_limit(0)


def test_jsonable_converts_supported_types() -> None:
    assert _jsonable(Decimal("2.5")) == 2.5
    assert _jsonable(datetime(2026, 1, 1, 12, 30)) == "2026-01-01T12:30:00"
    assert _jsonable(date(2026, 1, 1)) == "2026-01-01"
    assert _jsonable(time(12, 30)) == "12:30:00"
    assert _jsonable(b"\x01\xff") == "01ff"
    assert _jsonable([Decimal("1.5"), (2, 3)]) == [1.5, [2, 3]]
    assert _jsonable({"a": Decimal("1")}) == {"a": 1.0}
    assert _jsonable("plain") == "plain"
