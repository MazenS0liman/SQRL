"""Tests for :class:`PostgresService`.

The service is exercised against a fake psycopg connection so the tests cover
statement building, fetch detection, retries and rollbacks without a database.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pandas as pd
import psycopg
import pytest

pg_module = importlib.import_module(
    "squirrel.services.storage.database.PostgresService"
)
from squirrel.services.storage.database.PostgresService import PostgresService

DSN = "postgresql://user:pass@localhost:5432/squirrel"


class FakeCursor:
    def __init__(self, rows=None, rowcount: int = 1) -> None:
        self.rows = list(rows or [])
        self.rowcount = rowcount
        self.executed: list[tuple] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, statement, params=None) -> None:
        self.executed.append((statement, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor | None = None, closed: bool = False) -> None:
        self.cursor_obj = cursor or FakeCursor()
        self.closed = closed
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.cursor_kwargs: list[dict] = []

    def cursor(self, *args, **kwargs):
        self.cursor_kwargs.append(kwargs)
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1
        self.closed = True


def _service(connection: FakeConnection | None = None) -> PostgresService:
    service = PostgresService(connection_string=DSN, autoconnect=False)
    service._connection = connection or FakeConnection()
    return service


def _sql(statement) -> str:
    return statement.as_string() if hasattr(statement, "as_string") else str(statement)


def _operational_error(sqlstate: str | None = None) -> psycopg.OperationalError:
    error = psycopg.OperationalError("connection lost")
    if sqlstate is not None:
        error.sqlstate = sqlstate
    return error


# ——————————————————————————————————————————————————————————————
# Lifecycle


def test_requires_a_connection_string(monkeypatch) -> None:
    import squirrel.core.config as config_module

    monkeypatch.setattr(
        type(config_module.settings), "database_url", property(lambda self: "")
    )

    with pytest.raises(ValueError, match="connection string"):
        PostgresService(connection_string=None, autoconnect=False)


def test_connect_opens_connection_with_dict_rows(monkeypatch) -> None:
    connection = FakeConnection()
    captured = {}

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return connection

    monkeypatch.setattr(pg_module.psycopg, "connect", fake_connect)
    service = PostgresService(connection_string=DSN, autoconnect=False)

    assert service.connect() is connection
    assert captured["dsn"] == DSN
    assert captured["kwargs"]["row_factory"] is pg_module.dict_row


def test_connect_is_a_noop_when_open(monkeypatch) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(
        pg_module.psycopg,
        "connect",
        lambda *a, **k: pytest.fail("should not reconnect"),
    )
    service = _service(connection)

    assert service.connect() is connection


def test_disconnect_closes_and_clears_connection() -> None:
    connection = FakeConnection()
    service = _service(connection)

    service.disconnect()
    service.disconnect()

    assert connection.closes == 1
    assert service._connection is None


def test_disconnect_handles_already_closed_connections() -> None:
    connection = FakeConnection(closed=True)
    service = _service(connection)

    service.disconnect()

    assert connection.closes == 0
    assert service._connection is None


def test_is_closed_detects_missing_and_closed_connections() -> None:
    service = PostgresService(connection_string=DSN, autoconnect=False)

    assert service._is_closed() is True
    service._connection = FakeConnection(closed=True)
    assert service._is_closed() is True
    service._connection = FakeConnection(closed=False)
    assert service._is_closed() is False


def test_ensure_connection_reconnects(monkeypatch) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(pg_module.psycopg, "connect", lambda *a, **k: connection)
    service = PostgresService(connection_string=DSN, autoconnect=False)

    assert service._ensure_connection() is connection


# ——————————————————————————————————————————————————————————————
# execute


def test_execute_fetches_rows_and_columns() -> None:
    cursor = FakeCursor(rows=[{"id": 1}, {"id": 2}], rowcount=2)
    connection = FakeConnection(cursor)
    service = _service(connection)

    result = service.execute("SELECT * FROM t", fetch=True)

    assert result == {
        "success": True,
        "rowcount": 2,
        "rows": [{"id": 1}, {"id": 2}],
        "columns": ["id"],
    }
    assert connection.commits == 1
    assert connection.cursor_kwargs[0]["row_factory"] is pg_module.dict_row


def test_execute_without_fetch_commits() -> None:
    cursor = FakeCursor(rowcount=3)
    connection = FakeConnection(cursor)

    result = _service(connection).execute("DELETE FROM t", params={"a": 1})

    assert result == {"success": True, "rowcount": 3}
    assert cursor.executed == [("DELETE FROM t", {"a": 1})]
    assert connection.commits == 1


def test_execute_retries_reconnect_errors(monkeypatch) -> None:
    first = FakeConnection()
    second = FakeConnection(FakeCursor(rowcount=1))
    attempts = {"count": 0}

    def fake_connect(*args, **kwargs):
        attempts["count"] += 1
        return second

    class FlakyConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise _operational_error("08006")

    service = _service(FlakyConnection())
    monkeypatch.setattr(pg_module.psycopg, "connect", fake_connect)
    monkeypatch.setattr(pg_module.time, "sleep", lambda seconds: None)

    result = service.execute("SELECT 1", fetch=False)

    assert result["success"] is True
    assert attempts["count"] == 1
    assert service._connection is second


def test_execute_reraises_non_reconnect_errors() -> None:
    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise _operational_error("42P01")

    connection = FailingConnection()
    service = _service(connection)

    with pytest.raises(psycopg.OperationalError):
        service.execute("SELECT * FROM missing")

    assert connection.rollbacks == 1


def test_execute_rolls_back_generic_errors() -> None:
    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise RuntimeError("boom")

    connection = FailingConnection()
    service = _service(connection)

    with pytest.raises(RuntimeError, match="boom"):
        service.execute("SELECT 1")

    assert connection.rollbacks == 1


def test_safe_rollback_swallows_errors() -> None:
    class Broken(FakeConnection):
        def rollback(self) -> None:
            raise RuntimeError("already gone")

    PostgresService._safe_rollback(Broken())  # does not raise


# ——————————————————————————————————————————————————————————————
# Statement builders


def test_build_insert_statement_quotes_identifiers() -> None:
    statement = PostgresService._build_insert_statement("my table", {"a": 1, "b": 2})

    text = _sql(statement)
    assert 'INSERT INTO "my table" ("a", "b") VALUES' in text
    assert "%(a)s" in text and "%(b)s" in text


def test_build_insert_statement_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        PostgresService._build_insert_statement("t", {})


def test_build_update_statement_adds_where_prefix() -> None:
    text = _sql(PostgresService._build_update_statement("t", {"a": 1}, "id = 1"))

    assert text == 'UPDATE "t" SET "a" = %(a)s WHERE id = 1'


def test_build_update_statement_keeps_existing_where_keyword() -> None:
    text = _sql(PostgresService._build_update_statement("t", {"a": 1}, "WHERE id = 1"))

    assert text.endswith("WHERE id = 1")
    assert "WHERE WHERE" not in text


def test_build_update_statement_allows_empty_where() -> None:
    text = _sql(PostgresService._build_update_statement("t", {"a": 1}, "  "))

    assert text == 'UPDATE "t" SET "a" = %(a)s'


def test_build_update_statement_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        PostgresService._build_update_statement("t", {}, "id = 1")


def test_extract_values_filters_reserved_keys() -> None:
    data = {"table": "t", "values": {}, "sql": "", "params": {}, "a": 1, "b": 2}

    assert PostgresService._extract_values(data, {"table", "values", "sql", "params"}) == {
        "a": 1,
        "b": 2,
    }


# ——————————————————————————————————————————————————————————————
# query / fetch detection


@pytest.mark.parametrize(
    ("sql_text", "expected_fetch"),
    [
        ("SELECT * FROM t", True),
        ("select 1", True),
        ("SHOW search_path", True),
        ("EXPLAIN SELECT 1", True),
        ("DESCRIBE t", True),
        ("INSERT INTO t (a) VALUES (1) RETURNING id", True),
        ("UPDATE t SET a = 1", False),
        ("DELETE FROM t", False),
        ("-- comment\nSELECT 1", True),
        ("/* block */\nSELECT 1", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x RETURNING id", True),
        ("WITH x AS (SELECT 1) DELETE FROM t WHERE a IN (SELECT 1)", False),
        ("", False),
    ],
)
def test_query_detects_fetch(sql_text: str, expected_fetch: bool) -> None:
    service = _service()
    calls: list[bool] = []

    def fake_execute(statement, params=None, fetch=False):
        calls.append(fetch)
        return {"success": True}

    service.execute = fake_execute  # type: ignore[method-assign]

    service.query(sql_text)

    assert calls == [expected_fetch]


def test_query_honours_explicit_fetch_flag() -> None:
    cursor = FakeCursor(rows=[{"id": 1}])
    connection = FakeConnection(cursor)

    result = _service(connection).query("UPDATE t SET a = 1", fetch=True)

    assert result["rows"] == [{"id": 1}]


def test_statement_returns_rows_helper() -> None:
    assert PostgresService._statement_returns_rows("(SELECT 1)") is True
    assert PostgresService._statement_returns_rows("VALUES (1)") is True
    assert PostgresService._statement_returns_rows("TABLE t") is True
    assert PostgresService._statement_returns_rows("INSERT ... RETURNING id") is True
    assert PostgresService._statement_returns_rows("INSERT ...") is False
    assert PostgresService._statement_returns_rows("") is False


# ——————————————————————————————————————————————————————————————
# retrieve / insert / update / delete / execute_many


def test_retrieve_without_filters() -> None:
    connection = FakeConnection(FakeCursor(rows=[{"id": 1}]))
    service = _service(connection)

    result = service.retrieve("auth_users")

    assert result["rows"] == [{"id": 1}]
    assert 'SELECT * FROM "auth_users"' in _sql(connection.cursor_obj.executed[0][0])


def test_retrieve_with_filters() -> None:
    connection = FakeConnection(FakeCursor(rows=[{"id": 1}]))
    service = _service(connection)

    service.retrieve("auth_users", {"id": "user-1"})

    statement, params = connection.cursor_obj.executed[0]
    assert 'WHERE "id" = %(id)s' in _sql(statement)
    assert params == {"id": "user-1"}


def test_retrieve_returns_none_on_failure() -> None:
    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert _service(FailingConnection()).retrieve("t") is None


def test_insert_raw_sql_returns_true() -> None:
    connection = FakeConnection()
    service = _service(connection)

    assert service.insert({"sql": "INSERT INTO t (a) VALUES (1)", "params": {"a": 1}}) is True
    assert connection.commits == 1


def test_insert_raw_sql_fetches_returning_rows() -> None:
    connection = FakeConnection(FakeCursor(rows=[{"id": 7}]))
    service = _service(connection)

    result = service.insert({"sql": "INSERT INTO t (a) VALUES (1) RETURNING id"})

    assert result == [{"id": 7}]


def test_insert_structured_values() -> None:
    connection = FakeConnection()
    service = _service(connection)

    assert service.insert({"table": "t", "values": {"a": 1}}) is True
    statement, params = connection.cursor_obj.executed[0]
    assert 'INSERT INTO "t" ("a")' in _sql(statement)
    assert params == {"a": 1}


def test_insert_flat_shorthand() -> None:
    connection = FakeConnection()
    service = _service(connection)

    assert service.insert({"table": "t", "a": 1, "b": 2}) is True
    _, params = connection.cursor_obj.executed[0]
    assert params == {"a": 1, "b": 2}


def test_insert_requires_table_or_sql() -> None:
    assert _service().insert({"values": {"a": 1}}) is False


def test_insert_returns_false_on_failure() -> None:
    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise RuntimeError("boom")

    connection = FailingConnection()
    assert _service(connection).insert({"sql": "INSERT INTO t VALUES (1)"}) is False
    # ``execute`` rolls back, then ``insert`` rolls back again in its handler.
    assert connection.rollbacks == 2


def test_update_raw_sql() -> None:
    connection = FakeConnection()
    service = _service(connection)

    assert service.update("", {"sql": "UPDATE t SET a = 1", "params": {"a": 1}}) is True
    assert connection.commits == 1


def test_update_structured_uses_where_clause() -> None:
    connection = FakeConnection()
    service = _service(connection)

    assert service.update("id = 1", {"table": "t", "values": {"a": 2}}) is True
    statement, params = connection.cursor_obj.executed[0]
    assert 'UPDATE "t" SET "a" = %(a)s WHERE id = 1' == _sql(statement)
    assert params == {"a": 2}


def test_update_structured_prefers_where_key() -> None:
    connection = FakeConnection()
    service = _service(connection)

    service.update("ignored = 1", {"table": "t", "values": {"a": 2}, "where": "id = 9"})

    assert _sql(connection.cursor_obj.executed[0][0]).endswith("WHERE id = 9")


def test_update_requires_table_or_sql() -> None:
    assert _service().update("", {"values": {"a": 1}}) is False


def test_update_returns_false_on_failure() -> None:
    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert _service(FailingConnection()).update("", {"sql": "UPDATE t SET a = 1"}) is False


def test_delete_success_and_failure() -> None:
    connection = FakeConnection()
    assert _service(connection).delete("DELETE FROM t") is True
    assert connection.commits == 1

    class FailingConnection(FakeConnection):
        def cursor(self, *args, **kwargs):
            raise RuntimeError("boom")

    failing = FailingConnection()
    assert _service(failing).delete("DELETE FROM t") is False
    # ``execute`` rolls back, then ``delete`` rolls back again in its handler.
    assert failing.rollbacks == 2


def test_execute_many_commits_all_rows() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    ok = _service(connection).execute_many(
        "INSERT INTO t (a) VALUES (%(a)s)", [{"a": 1}, {"a": 2}]
    )

    assert ok is True
    assert len(cursor.executed) == 2
    assert connection.commits == 1


def test_execute_many_rolls_back_on_failure() -> None:
    class FailingCursor(FakeCursor):
        def execute(self, statement, params=None) -> None:
            raise RuntimeError("boom")

    connection = FakeConnection(FailingCursor())

    assert _service(connection).execute_many("INSERT INTO t VALUES (1)", [{}]) is False
    assert connection.rollbacks == 1


# ——————————————————————————————————————————————————————————————
# load / schema metadata


def test_generate_schema_metadata_reads_information_schema() -> None:
    cursor = FakeCursor(rows=[("id", "integer", "NO")])
    connection = FakeConnection(cursor)

    metadata = _service(connection)._generate_schema_metadata("t")

    assert metadata == {"columns": [("id", "integer", "NO")]}
    assert cursor.executed[0][1] == ("t",)


def test_load_rejects_missing_files(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        _service().load(str(tmp_path / "missing.csv"), "t")


def test_load_rejects_non_csv_files(tmp_path) -> None:
    path = tmp_path / "data.parquet"
    path.write_bytes(b"data")

    with pytest.raises(ValueError, match="Only CSV is supported"):
        _service().load(str(path), "t")


def test_load_normalizes_columns_and_uses_copy(tmp_path, monkeypatch) -> None:
    path = tmp_path / "data.csv"
    path.write_text("Customer ID,Order-Date\n1,2026-01-01\n")
    captured = {}

    def fake_to_sql(self, table_name, engine, **kwargs):
        captured["columns"] = list(self.columns)
        captured["table"] = table_name
        captured["kwargs"] = kwargs
        return len(self)

    monkeypatch.setattr(pg_module.pd.DataFrame, "to_sql", fake_to_sql)
    monkeypatch.setattr(pg_module, "create_engine", lambda url: ("engine", url))
    service = _service()
    monkeypatch.setattr(
        service, "_generate_schema_metadata", lambda table: {"columns": [("id", "int")]}
    )

    result = service.load(str(path), "orders", if_exists="append")

    assert result == {"orders": {"columns": [("id", "int")]}}
    assert captured["columns"] == ["customer_id", "order_date"]
    assert captured["table"] == "orders"
    assert captured["kwargs"]["if_exists"] == "append"
    assert captured["kwargs"]["index"] is False
    assert captured["kwargs"]["method"] == service._copy


def test_copy_streams_csv_through_copy_protocol() -> None:
    writes: list[str] = []

    class CopyContext:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def write(self, payload: str) -> None:
            writes.append(payload)

    class RawCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def copy(self, statement: str):
            writes.append(statement)
            return CopyContext()

    class RawConnection:
        def cursor(self):
            return RawCursor()

    connection = SimpleNamespace(connection=RawConnection())
    table = SimpleNamespace(schema="public", name="orders")

    PostgresService._copy(table, connection, ["id", "name"], [(1, "a"), (2, "b")])

    assert writes[0] == 'COPY "public"."orders" ("id", "name") FROM STDIN WITH (FORMAT csv)'
    assert "1,a" in writes[1]
    assert "2,b" in writes[1]
