"""Tests for the cross-cutting core modules.

Covers configuration helpers, the download/cleanup utilities, request-scoped
context variables, the global exception handlers, the logging helpers, and the
request logging middleware.
"""
from __future__ import annotations

import asyncio
import io
import logging as std_logging
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

import squirrel.core.logging as logging_module
import squirrel.core.utils as utils_module
from squirrel.core import error_handlers, request_context
from squirrel.core.config import Colors, Settings, get_version
from squirrel.core.logging import ClassLogger, InterceptHandler, Loggable, log_stage
from squirrel.core.middleware import RequestLoggingMiddleware
from squirrel.core.utils import cleanup, download_files
from squirrel.services.usage.context import get_usage_source


# ——————————————————————————————————————————————————————————————
# config


def test_get_version_reads_pyproject() -> None:
    version = get_version()

    assert isinstance(version, str)
    assert version


def test_get_version_falls_back_when_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path).endswith("pyproject.toml"):
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    assert get_version() == "0.1.0"


def test_settings_resolves_database_and_cors() -> None:
    settings = Settings(
        DATABASE_URL="",
        POSTGRES_DSN="postgresql://legacy/db",
        CORS_ORIGINS=" http://a.test ,, http://b.test ",
    )

    assert settings.database_url == "postgresql://legacy/db"
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_settings_etl_lists_and_prefixes() -> None:
    settings = Settings(
        ETL_ALLOWED_SINK_TABLES=" etl_a , etl_b ,",
        ETL_ALLOWED_SOURCE_TABLES=" source_one ,source_two",
        ETL_SOURCE_TABLE_PREFIXES=" raw_ , staging_ ",
    )

    assert settings.etl_allowed_sink_tables == ["etl_a", "etl_b"]
    assert settings.etl_allowed_source_tables == ["source_one", "source_two"]
    assert settings.etl_source_table_prefixes == ["raw_", "staging_"]


def test_settings_source_prefixes_default_when_blank() -> None:
    settings = Settings(ETL_SOURCE_TABLE_PREFIXES=" , ")

    assert settings.etl_source_table_prefixes == ["etl_"]


def test_colors_are_ansi_escape_codes() -> None:
    for name in ("HEADER", "OKBLUE", "OKCYAN", "OKGREEN", "WARNING", "FAIL", "ENDC", "BOLD", "UNDERLINE", "RED"):
        value = getattr(Colors, name)
        assert value.startswith("\033[")
        assert value.endswith("m")


# ——————————————————————————————————————————————————————————————
# utils


def test_cleanup_removes_files_and_ignores_missing(tmp_path: Path) -> None:
    existing = tmp_path / "a.txt"
    existing.write_text("data")

    cleanup([str(existing), str(tmp_path / "missing.txt")])

    assert not existing.exists()


def test_cleanup_swallows_unlink_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", boom)

    # Must not raise.
    cleanup(["whatever.txt"])


def test_download_files_copies_local_file(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("a,b\n1,2\n")

    paths = asyncio.run(download_files([str(source)]))

    try:
        assert len(paths) == 1
        assert Path(paths[0]).read_text() == "a,b\n1,2\n"
    finally:
        cleanup(paths)


def test_download_files_fetches_http(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = b"col\nvalue\n"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            assert url == "https://example.test/data.csv"
            return FakeResponse()

    monkeypatch.setattr(utils_module.httpx, "AsyncClient", FakeClient)

    paths = asyncio.run(download_files(["https://example.test/data.csv"]))

    try:
        assert Path(paths[0]).read_bytes() == b"col\nvalue\n"
    finally:
        cleanup(paths)


def test_download_files_pulls_s3_object(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    class FakeS3:
        def download_file(self, bucket: str, key: str, destination: str) -> None:
            calls.update(bucket=bucket, key=key)
            Path(destination).write_bytes(b"s3-data")

    def fake_client(service: str, **kwargs):
        calls["service"] = service
        return FakeS3()

    monkeypatch.setattr(utils_module.boto3, "client", fake_client)

    paths = asyncio.run(download_files(["s3://my-bucket/path/file.csv"]))

    try:
        assert calls == {"service": "s3", "bucket": "my-bucket", "key": "path/file.csv"}
        assert Path(paths[0]).read_bytes() == b"s3-data"
    finally:
        cleanup(paths)


# ——————————————————————————————————————————————————————————————
# request context


def test_request_context_set_and_get() -> None:
    request_context.set_request_id("req-123")

    assert request_context.get_request_id() == "req-123"


def test_request_context_unknown_default() -> None:
    request_context.current_request_id.set(None)

    assert request_context.get_request_id() == "unknown"


def test_get_request_id_from_request_state() -> None:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    request = Request(scope)
    request.state.request_id = "abc"

    assert request_context.get_request_id_from_request(request) == "abc"


def test_get_request_id_from_request_without_state() -> None:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    request = Request(scope)

    assert request_context.get_request_id_from_request(request) == "unknown"


# ——————————————————————————————————————————————————————————————
# error handlers


def test_exception_handler_returns_500() -> None:
    response = asyncio.run(error_handlers.exception_handler(None, ValueError("kaboom")))

    assert response.status_code == 500
    assert b"kaboom" in response.body


def test_exception_handler_handles_empty_message() -> None:
    response = asyncio.run(error_handlers.exception_handler(None, ValueError()))

    assert response.status_code == 500
    assert b"An unexpected error occurred" in response.body


def test_app_exception_handler_uses_exception_status() -> None:
    exc = error_handlers.AppException(status_code=418, detail="teapot")

    response = asyncio.run(error_handlers.app_exception_handler(None, exc))

    assert response.status_code == 418
    assert b"teapot" in response.body


@pytest.mark.parametrize(
    ("factory", "status", "detail"),
    [
        (error_handlers.NotFoundException, 404, "Resource not found"),
        (error_handlers.BadRequestException, 400, "Invalid request"),
        (error_handlers.UnauthorizedException, 401, "Not authorized"),
    ],
)
def test_specific_exceptions_have_expected_status(factory, status, detail) -> None:
    exc = factory()

    assert isinstance(exc, error_handlers.AppException)
    assert exc.status_code == status
    assert exc.detail == detail

    custom = factory("custom message")
    assert custom.detail == "custom message"


# ——————————————————————————————————————————————————————————————
# logging


def test_log_stage_accepts_known_and_unknown_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    class FakeLogger:
        def opt(self, **kwargs):
            calls.append(("opt", kwargs))
            return self

        def log(self, level, message, *args):
            calls.append(("log", level, message, args))

    monkeypatch.setattr(logging_module, "logger", FakeLogger())

    log_stage("inspect", "hello {}", "world")
    log_stage("unknown-stage", "other", level="WARNING")

    assert calls[0] == ("opt", {"colors": True})
    assert calls[1][1] == "INFO"
    assert "<cyan>[INSPECT]</cyan>" in calls[1][2]
    assert calls[3][1] == "WARNING"
    assert "<white>[UNKNOWN-STAGE]</white>" in calls[3][2]


def test_class_logger_binds_owner_name() -> None:
    class Example:
        logger = ClassLogger()

    bound = Example.logger

    assert bound is not None


def test_loggable_subclass_exposes_logger() -> None:
    class Example(Loggable):
        pass

    assert Example().logger is not None


def test_intercept_handler_forwards_records(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list = []

    class FakeLogger:
        def level(self, name):
            if name == "BOGUS":
                raise ValueError(name)
            return types.SimpleNamespace(name=name)

        def opt(self, **kwargs):
            records.append(("opt", kwargs))
            return self

        def log(self, level, message):
            records.append(("log", level, message))

        def info(self, message):
            records.append(("info", message))

    monkeypatch.setattr(logging_module, "logger", FakeLogger())

    handler = InterceptHandler()
    handler.emit(std_logging.makeLogRecord({"levelname": "INFO", "msg": "hello %s", "args": ("world",)}))
    handler.emit(std_logging.makeLogRecord({"levelname": "BOGUS", "msg": "odd", "levelno": 35}))

    assert ("log", "INFO", "hello world") in records
    assert ("log", 35, "odd") in records

    handler.write("  message  ")
    handler.write("   ")
    assert ("info", "message") in records
    assert handler.isatty() is False
    assert isinstance(handler.fileno(), int)
    handler.flush()


def test_setup_logging_configures_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list = []
    removed: list = []

    class FakeLogger:
        def remove(self):
            removed.append(True)

        def add(self, sink, **kwargs):
            added.append((sink, kwargs))

        def info(self, message):
            added.append(("info", message))

    fake = FakeLogger()
    monkeypatch.setattr(logging_module, "logger", fake)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    result = logging_module.setup_logging()

    assert result is fake
    assert removed == [True]
    assert isinstance(sys.stdout, InterceptHandler)
    assert isinstance(sys.stderr, InterceptHandler)
    assert any(entry[0] == "info" for entry in added)


# ——————————————————————————————————————————————————————————————
# middleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/api/workspace/ping")
    async def ping() -> dict:
        return {"request_id": request_context.get_request_id(), "source": get_usage_source()}

    @app.get("/api/boom")
    async def boom() -> dict:
        raise RuntimeError("boom")

    return app


def test_middleware_sets_request_id_and_usage_source() -> None:
    client = TestClient(_build_app())

    response = client.get("/api/workspace/ping")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "workspace"
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_middleware_logs_and_reraises_errors() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/api/boom")

    assert response.status_code == 500
