"""HTTP-level tests for the FastAPI routers.

The app is exercised through :class:`fastapi.testclient.TestClient` with the
authentication dependency overridden, so every request is treated as coming
from one fixed user. Route-level service factories are replaced per test with
small doubles, which keeps these tests about the HTTP contract (status codes,
validation, serialization, error mapping) rather than the service internals
that are covered elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app
from squirrel.api.routes import (
    auth as auth_route,
    chat as chat_route,
    connector as connector_route,
    files as files_route,
    jobs as jobs_route,
    logs as logs_route,
    notebook as notebook_route,
    notifications as notifications_route,
    pipelines as pipelines_route,
    token as token_route,
    usage as usage_route,
    workspace as workspace_route,
)
from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.error import (
    CellNotFoundError,
    NotebookNotFoundError,
    NotebookNotReadyError,
    WorkspaceNotFoundError,
)
from squirrel.schemas.connector import ConnectorInUseError, ConnectorNotFoundError
from squirrel.schemas.etl import PipelineDefinition
from squirrel.schemas.file import File
from squirrel.schemas.notebook import Notebook
from squirrel.services.jobs.JobService import JobNotFoundError
from squirrel.services.notifications.NotificationService import NotificationNotFoundError
from squirrel.services.pipelines.PipelineService import (
    PipelineNotApprovedError,
    PipelineNotFoundError,
    PipelineRunNotFoundError,
)
from squirrel.services.pipelines.compiler import PipelineValidationError

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

TEST_USER = AuthUserRead(
    id="user-1",
    username="mazen",
    email="mazen@example.com",
    full_name="Mazen",
    created_at=NOW,
)


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def _fake(**attributes):
    """A tiny service double exposing exactly the methods a route calls."""
    return SimpleNamespace(**attributes)


# ——————————————————————————————————————————————————————————————
# Health / OpenAPI


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body


def test_openapi_schema_builds() -> None:
    schema = app.openapi()

    assert schema["info"]["title"]
    assert "/api/pipelines" in schema["paths"]
    assert "/api/usage/overview" in schema["paths"]


# ——————————————————————————————————————————————————————————————
# Auth


def _stub_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_route.auth_service, "serialize_user", lambda user: TEST_USER
    )
    monkeypatch.setattr(
        auth_route.auth_service,
        "register_user",
        lambda **kwargs: SimpleNamespace(id=TEST_USER.id),
    )
    monkeypatch.setattr(
        auth_route.auth_service,
        "authenticate",
        lambda identifier, password: SimpleNamespace(id=TEST_USER.id),
    )
    monkeypatch.setattr(
        auth_route.auth_service,
        "issue_token",
        lambda user_id: SimpleNamespace(token="token-123", expires_at=NOW),
    )


def test_register_returns_token(client: TestClient, monkeypatch) -> None:
    _stub_auth(monkeypatch)

    response = client.post(
        "/api/auth/register",
        json={
            "username": "mazen",
            "email": "mazen@example.com",
            "password": "supersecret",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["access_token"] == "token-123"
    assert body["user"]["id"] == TEST_USER.id


def test_register_conflict_maps_to_409(client: TestClient, monkeypatch) -> None:
    _stub_auth(monkeypatch)
    monkeypatch.setattr(
        auth_route.auth_service,
        "register_user",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("email already used")),
    )

    response = client.post(
        "/api/auth/register",
        json={
            "username": "mazen",
            "email": "mazen@example.com",
            "password": "supersecret",
        },
    )

    assert response.status_code == 409
    assert "email already used" in response.json()["detail"]


def test_login_returns_token(client: TestClient, monkeypatch) -> None:
    _stub_auth(monkeypatch)

    response = client.post(
        "/api/auth/login",
        json={"identifier": "mazen", "password": "supersecret"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "token-123"


def test_login_failure_maps_to_401(client: TestClient, monkeypatch) -> None:
    _stub_auth(monkeypatch)
    monkeypatch.setattr(
        auth_route.auth_service,
        "authenticate",
        lambda identifier, password: (_ for _ in ()).throw(ValueError("bad credentials")),
    )

    response = client.post(
        "/api/auth/login",
        json={"identifier": "mazen", "password": "supersecret"},
    )

    assert response.status_code == 401


def test_me_returns_current_user(client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "mazen"


def test_logout_requires_a_bearer_token(client: TestClient) -> None:
    assert client.post("/api/auth/logout").status_code == 401


def test_logout_revokes_the_token(client: TestClient, monkeypatch) -> None:
    revoked: list[str] = []
    monkeypatch.setattr(
        auth_route.auth_service, "revoke_token", lambda token: revoked.append(token)
    )

    response = client.post(
        "/api/auth/logout", headers={"Authorization": "Bearer abc.def"}
    )

    assert response.status_code == 200
    assert revoked == ["abc.def"]


# ——————————————————————————————————————————————————————————————
# Usage


def test_usage_overview(client: TestClient, monkeypatch) -> None:
    service = _fake(
        overview=lambda user_id, days: {
            "window_days": days,
            "totals": {"total_tokens": 120, "requests": 2},
            "by_provider": [
                {"provider": "gemini", "total_tokens": 120, "requests": 2}
            ],
        }
    )
    monkeypatch.setattr(usage_route, "_svc", lambda: service)

    response = client.get("/api/usage/overview", params={"days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 7
    assert body["totals"]["total_tokens"] == 120
    assert body["by_provider"][0]["provider"] == "gemini"


def test_usage_overview_rejects_out_of_range_window(client: TestClient) -> None:
    assert client.get("/api/usage/overview", params={"days": 0}).status_code == 422


def test_usage_events(client: TestClient, monkeypatch) -> None:
    event = {
        "id": 1,
        "provider": "gemini",
        "model": "models/gemini-2.5-flash",
        "source": "chat",
        "total_tokens": 102,
    }
    service = _fake(events=lambda user_id, **kwargs: ([event], 1))
    monkeypatch.setattr(usage_route, "_svc", lambda: service)

    response = client.get("/api/usage/events", params={"provider": "gemini"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["model"] == "models/gemini-2.5-flash"


def test_usage_pricing_lists_known_models(client: TestClient) -> None:
    response = client.get("/api/usage/pricing")

    assert response.status_code == 200
    body = response.json()
    assert body["pricing"]
    assert "gemini" in body["providers"]


# ——————————————————————————————————————————————————————————————
# Jobs


def _job_row(**overrides):
    row = {
        "id": "job_1",
        "owner_user_id": TEST_USER.id,
        "type": "etl_pipeline",
        "status": "queued",
        "progress": 0,
        "payload": {},
    }
    row.update(overrides)
    return row


def test_list_jobs(client: TestClient, monkeypatch) -> None:
    service = _fake(list_jobs=lambda *a, **k: [_job_row()])
    monkeypatch.setattr(jobs_route, "_svc", lambda: service)

    response = client.get("/api/jobs", params={"status": "queued"})

    assert response.status_code == 200
    assert response.json()[0]["id"] == "job_1"


def test_list_jobs_rejects_unknown_status(client: TestClient) -> None:
    response = client.get("/api/jobs", params={"status": "nope"})

    assert response.status_code == 400


def test_get_job(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        jobs_route, "_svc", lambda: _fake(get_job=lambda job_id, user_id: _job_row())
    )

    assert client.get("/api/jobs/job_1").status_code == 200


def test_get_job_missing(client: TestClient, monkeypatch) -> None:
    def boom(job_id, user_id):
        raise JobNotFoundError(job_id)

    monkeypatch.setattr(jobs_route, "_svc", lambda: _fake(get_job=boom))

    assert client.get("/api/jobs/missing").status_code == 404


def test_list_job_events(client: TestClient, monkeypatch) -> None:
    event = {"id": 1, "job_id": "job_1", "level": "info", "message": "started"}
    monkeypatch.setattr(
        jobs_route,
        "_svc",
        lambda: _fake(list_events=lambda *a, **k: [event]),
    )

    response = client.get("/api/jobs/job_1/events")

    assert response.status_code == 200
    assert response.json()[0]["message"] == "started"


def test_list_job_events_missing_job(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise JobNotFoundError("missing")

    monkeypatch.setattr(jobs_route, "_svc", lambda: _fake(list_events=boom))

    assert client.get("/api/jobs/missing/events").status_code == 404


# ——————————————————————————————————————————————————————————————
# Notifications


def _notification_row(**overrides):
    row = {
        "id": "ntf_1",
        "user_id": TEST_USER.id,
        "type": "job",
        "title": "Run finished",
        "body": "done",
        "created_at": NOW,
    }
    row.update(overrides)
    return row


def test_list_notifications(client: TestClient, monkeypatch) -> None:
    service = _fake(
        list_for_user=lambda *a, **k: [_notification_row()],
        unread_count=lambda user_id: 1,
    )
    monkeypatch.setattr(notifications_route, "_svc", lambda: service)

    response = client.get("/api/notifications")

    assert response.status_code == 200
    body = response.json()
    assert body["unread_count"] == 1
    assert body["items"][0]["title"] == "Run finished"


def test_unread_count(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notifications_route, "_svc", lambda: _fake(unread_count=lambda user_id: 4)
    )

    assert client.get("/api/notifications/unread-count").json() == {"count": 4}


def test_mark_all_read(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notifications_route, "_svc", lambda: _fake(mark_all_read=lambda user_id: 3)
    )

    assert client.post("/api/notifications/read-all").json() == {"updated": 3}


def test_get_preferences(client: TestClient, monkeypatch) -> None:
    service = _fake(
        get_preferences=lambda user_id: {
            "user_id": user_id,
            "email_enabled": True,
            "digest_mode": "immediate",
        }
    )
    monkeypatch.setattr(notifications_route, "_svc", lambda: service)

    response = client.get("/api/notifications/preferences")

    assert response.status_code == 200
    assert response.json()["digest_mode"] == "immediate"


def test_update_preferences(client: TestClient, monkeypatch) -> None:
    service = _fake(
        update_preferences=lambda user_id, **kwargs: {
            "user_id": user_id,
            "email_enabled": kwargs["email_enabled"],
            "digest_mode": kwargs["digest_mode"],
        }
    )
    monkeypatch.setattr(notifications_route, "_svc", lambda: service)

    response = client.patch(
        "/api/notifications/preferences",
        json={"email_enabled": False, "digest_mode": "daily"},
    )

    assert response.status_code == 200
    assert response.json()["digest_mode"] == "daily"


def test_update_preferences_rejects_unknown_digest(client: TestClient) -> None:
    response = client.patch(
        "/api/notifications/preferences", json={"digest_mode": "hourly"}
    )

    assert response.status_code == 422


def test_mark_notification_read(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notifications_route,
        "_svc",
        lambda: _fake(mark_read=lambda notification_id, user_id: _notification_row(read_at=NOW)),
    )

    response = client.post("/api/notifications/ntf_1/read")

    assert response.status_code == 200
    assert response.json()["id"] == "ntf_1"


def test_mark_notification_read_missing(client: TestClient, monkeypatch) -> None:
    def boom(notification_id, user_id):
        raise NotificationNotFoundError(notification_id)

    monkeypatch.setattr(notifications_route, "_svc", lambda: _fake(mark_read=boom))

    assert client.post("/api/notifications/nope/read").status_code == 404


# ——————————————————————————————————————————————————————————————
# Tokens


def _token_read(**overrides):
    row = {
        "id": "tok_1",
        "provider": "gemini",
        "label": "work",
        "token_preview": "AIza...abcd",
        "created_at": NOW,
        "last_used_at": None,
    }
    row.update(overrides)
    return row


def test_add_token(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        token_route.token_service, "add_token", lambda **kwargs: _token_read()
    )

    response = client.post(
        "/api/token", json={"provider": "gemini", "token": "AIza-super-secret"}
    )

    assert response.status_code == 201
    assert response.json()["token"]["id"] == "tok_1"


def test_add_token_maps_runtime_errors_to_500(client: TestClient, monkeypatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("vault down")

    monkeypatch.setattr(token_route.token_service, "add_token", boom)

    response = client.post(
        "/api/token", json={"provider": "gemini", "token": "AIza-super-secret"}
    )

    assert response.status_code == 500


def test_add_token_rejects_unknown_provider(client: TestClient) -> None:
    response = client.post(
        "/api/token", json={"provider": "not-a-provider", "token": "long-enough-token"}
    )

    assert response.status_code == 422


def test_list_tokens(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        token_route.token_service, "list_tokens", lambda user_id: [_token_read()]
    )

    response = client.get("/api/token")

    assert response.status_code == 200
    assert response.json()["tokens"][0]["provider"] == "gemini"


def test_delete_token(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        token_route.token_service, "delete_token", lambda user_id, token_id: True
    )

    assert client.delete("/api/token/tok_1").status_code == 200


def test_delete_token_missing(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        token_route.token_service, "delete_token", lambda user_id, token_id: False
    )

    assert client.delete("/api/token/missing").status_code == 404


# ——————————————————————————————————————————————————————————————
# Logs


def test_request_log_context(client: TestClient) -> None:
    response = client.get("/api/logs/request")

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "GET"
    assert body["path"] == "/api/logs/request"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_recent_logs_without_a_log_file(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(logs_route, "_latest_log_file", lambda: None)

    response = client.get("/api/logs/recent", params={"limit": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["limit"] == 5


# ——————————————————————————————————————————————————————————————
# Chat


def _agent_request() -> dict:
    return {
        "query": "hello",
        "projectId": "proj-1",
        "conversationId": "conv-1",
        "fileUrls": [],
    }


def _agent_response() -> dict:
    return {
        "reply": "hi",
        "action": "chat",
        "projectId": "proj-1",
        "conversationId": "conv-1",
        "responseTime": 0.1,
        "llmTokens": {"promptTokens": 1, "completionTokens": 2, "totalTokens": 3},
    }


def test_chat_success(client: TestClient, monkeypatch) -> None:
    saved: list[dict] = []
    orchestrator = _fake(handle_request=_async(lambda request, **kwargs: _agent_response()))
    monkeypatch.setattr(chat_route, "OrchestratorService", lambda: orchestrator)
    monkeypatch.setattr(
        chat_route,
        "MessagingService",
        lambda: _fake(save_chat_turn=lambda **kwargs: saved.append(kwargs)),
    )

    response = client.post("/api/chat", json=_agent_request())

    assert response.status_code == 200
    assert response.json()["reply"] == "hi"
    assert saved and saved[0]["file_urls"] == []


def test_chat_orchestrator_failure_maps_to_500(client: TestClient, monkeypatch) -> None:
    async def boom(request, **kwargs):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(
        chat_route, "OrchestratorService", lambda: _fake(handle_request=boom)
    )
    monkeypatch.setattr(chat_route, "MessagingService", lambda: _fake())

    response = client.post("/api/chat", json=_agent_request())

    assert response.status_code == 500
    assert "error occurred" in response.json()["detail"]


def test_chat_persistence_failure_still_returns_reply(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        chat_route,
        "OrchestratorService",
        lambda: _fake(handle_request=_async(lambda request, **kwargs: _agent_response())),
    )

    def boom(**kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(chat_route, "MessagingService", lambda: _fake(save_chat_turn=boom))

    response = client.post("/api/chat", json=_agent_request())

    assert response.status_code == 200
    assert response.json()["reply"] == "hi"


def test_chat_websocket_rejects_missing_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/chat/ws"):
            pass

    assert excinfo.value.code == 1008


def _async(fn):
    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


# ——————————————————————————————————————————————————————————————
# Files


def _stored_file(**overrides) -> File:
    payload = {"fileUrl": "s3://sqrl/uploads/orders.csv", "fileName": "orders.csv"}
    payload.update(overrides)
    return File(**payload)


def _stub_files(monkeypatch, *, storage=None, service=None):
    monkeypatch.setattr(files_route, "MinIOService", lambda: storage)
    monkeypatch.setattr(files_route, "FileService", lambda: service)


def test_retrieve_file(client: TestClient, monkeypatch) -> None:
    storage = _fake(retrieve_file=lambda url: _stored_file())
    service = _fake(is_owned_by=lambda url, user_id: True)
    _stub_files(monkeypatch, storage=storage, service=service)

    response = client.post(
        "/api/file/retrieve",
        json={"file": [{"fileUrl": "s3://sqrl/uploads/orders.csv"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["outputFiles"]


def test_retrieve_file_rejects_unowned_files(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(retrieve_file=lambda url: _stored_file()),
        service=_fake(is_owned_by=lambda url, user_id: False),
    )

    response = client.post(
        "/api/file/retrieve",
        json={"file": [{"fileUrl": "s3://sqrl/uploads/orders.csv"}]},
    )

    assert response.status_code == 404


def test_upload_file(client: TestClient, monkeypatch) -> None:
    recorded: list[File] = []
    storage = _fake(upload_file=lambda **kwargs: _stored_file())
    service = _fake(record_upload=lambda file, **kwargs: recorded.append(file))
    _stub_files(monkeypatch, storage=storage, service=service)

    response = client.post(
        "/api/file/upload",
        files={"file": ("orders.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert recorded and recorded[0].fileName == "orders.csv"


def test_upload_file_failure_returns_success_false(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(upload_file=lambda **kwargs: None),
        service=_fake(record_upload=lambda file, **kwargs: None),
    )

    response = client.post(
        "/api/file/upload",
        files={"file": ("orders.csv", b"a,b\n", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_list_files(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(),
        service=_fake(list_files=lambda user_id: [_stored_file()]),
    )

    response = client.get("/api/file/")

    assert response.status_code == 200
    assert response.json()["files"][0]["fileName"] == "orders.csv"


def test_rename_file(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(),
        service=_fake(rename_file=lambda url, user_id, name: _stored_file(fileName=name)),
    )

    response = client.patch(
        "/api/file/by-url",
        params={"file_url": "s3://sqrl/uploads/orders.csv"},
        json={"fileName": "renamed.csv"},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "renamed.csv"


def test_rename_file_rejects_blank_name(client: TestClient, monkeypatch) -> None:
    _stub_files(monkeypatch, storage=_fake(), service=_fake())

    response = client.patch(
        "/api/file/by-url",
        params={"file_url": "s3://sqrl/uploads/orders.csv"},
        json={"fileName": "   "},
    )

    assert response.status_code == 400


def test_rename_file_missing(client: TestClient, monkeypatch) -> None:
    from squirrel.schemas.file import FileRecordNotFound

    def boom(url, user_id, name):
        raise FileRecordNotFound(url)

    _stub_files(monkeypatch, storage=_fake(), service=_fake(rename_file=boom))

    response = client.patch(
        "/api/file/by-url",
        params={"file_url": "s3://sqrl/uploads/missing.csv"},
        json={"fileName": "x.csv"},
    )

    assert response.status_code == 404


def test_delete_file(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(delete_file=lambda url: True),
        service=_fake(
            get_owned_file=lambda url, user_id: SimpleNamespace(
                workspaceIds=[], notebookIds=[]
            ),
            delete_file=lambda url, user_id: True,
        ),
    )

    response = client.delete(
        "/api/file/by-url", params={"file_url": "s3://sqrl/uploads/orders.csv"}
    )

    assert response.status_code == 200


def test_delete_file_in_use(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(delete_file=lambda url: True),
        service=_fake(
            get_owned_file=lambda url, user_id: SimpleNamespace(
                workspaceIds=["ws_1"], notebookIds=[]
            )
        ),
    )

    response = client.delete(
        "/api/file/by-url", params={"file_url": "s3://sqrl/uploads/orders.csv"}
    )

    assert response.status_code == 409


def test_delete_file_storage_failure(client: TestClient, monkeypatch) -> None:
    _stub_files(
        monkeypatch,
        storage=_fake(delete_file=lambda url: False),
        service=_fake(
            get_owned_file=lambda url, user_id: SimpleNamespace(
                workspaceIds=[], notebookIds=[]
            )
        ),
    )

    response = client.delete(
        "/api/file/by-url", params={"file_url": "s3://sqrl/uploads/orders.csv"}
    )

    assert response.status_code == 500


# ——————————————————————————————————————————————————————————————
# Connectors


def _connector_row(**overrides):
    row = {
        "connector_id": "conn_1",
        "owner_user_id": TEST_USER.id,
        "name": "Warehouse",
        "type": "postgres",
        "status": "connected",
        "error": None,
        "last_tested_at": None,
        "config": {"host": "db", "port": "5432", "database": "analytics", "password": "secret"},
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def test_list_connector_types(client: TestClient) -> None:
    response = client.get("/api/connector/types")

    assert response.status_code == 200
    ids = {entry["id"] for entry in response.json()}
    assert "postgres" in ids


def test_list_connections(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "_connector_service",
        lambda: _fake(list_connections=lambda **kwargs: [_connector_row()]),
    )

    response = client.get("/api/connector")

    assert response.status_code == 200
    assert response.json()[0]["connector_id"] == "conn_1"
    assert response.json()[0]["config"]["password"] == ""


def test_create_connection(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "_connector_service",
        lambda: _fake(create_connection=lambda *a, **k: _connector_row()),
    )

    response = client.post(
        "/api/connector",
        json={"name": "Warehouse", "type": "postgres", "config": {"host": "db"}},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Warehouse"


def test_create_connection_validation_error(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("Missing required field: host")

    monkeypatch.setattr(connector_route, "_connector_service", lambda: _fake(create_connection=boom))

    response = client.post(
        "/api/connector",
        json={"name": "Warehouse", "type": "postgres", "config": {}},
    )

    assert response.status_code == 400


def test_update_connection_missing(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ConnectorNotFoundError("conn_missing")

    monkeypatch.setattr(connector_route, "_connector_service", lambda: _fake(update_connection=boom))

    response = client.patch("/api/connector/conn_missing", json={"name": "Renamed"})

    assert response.status_code == 404


def test_delete_connection_in_use(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ConnectorInUseError("used by 1 notebook")

    monkeypatch.setattr(connector_route, "_connector_service", lambda: _fake(delete_connection=boom))

    assert client.delete("/api/connector/conn_1").status_code == 409


def test_test_connection(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "_connector_service",
        lambda: _fake(test_connection=lambda *a, **k: _connector_row(status="error", error="refused")),
    )

    response = client.post("/api/connector/conn_1/test")

    assert response.status_code == 200
    assert response.json()["status"] == "error"


def test_query_connection(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "_connector_service",
        lambda: _fake(
            fetch_query=lambda *a, **k: {
                "columns": ["id"],
                "rows": [{"id": 1}],
                "row_count": 1,
            }
        ),
    )

    response = client.post("/api/connector/conn_1/query", json={"query": "SELECT 1"})

    assert response.status_code == 200
    assert response.json()["rows"] == [{"id": 1}]


def test_query_connection_rejects_writes(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("Only SELECT or WITH queries are allowed.")

    monkeypatch.setattr(connector_route, "_connector_service", lambda: _fake(fetch_query=boom))

    response = client.post("/api/connector/conn_1/query", json={"query": "DELETE FROM t"})

    assert response.status_code == 400


def test_list_connector_tables(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "DataConnectorService",
        lambda: _fake(list_tables=lambda *a, **k: ["public.orders"]),
    )

    response = client.get("/api/connector/conn_1/tables")

    assert response.status_code == 200
    assert response.json() == ["public.orders"]


def test_list_connector_tables_not_implemented(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise NotImplementedError("not wired")

    monkeypatch.setattr(
        connector_route, "DataConnectorService", lambda: _fake(list_tables=boom)
    )

    assert client.get("/api/connector/conn_1/tables").status_code == 501


def test_preview_connector_tables(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        connector_route,
        "DataConnectorService",
        lambda: _fake(
            list_tables=lambda *a, **k: ["orders"],
            preview_table=lambda *a, **k: pd.DataFrame({"id": [1, 2]}),
        ),
    )

    response = client.get("/api/connector/conn_1/tables/preview")

    assert response.status_code == 200
    table = response.json()["tables"][0]
    assert table["table"] == "orders"
    assert table["preview"] == [{"id": 1}, {"id": 2}]


# ——————————————————————————————————————————————————————————————
# Pipelines


def _definition() -> dict:
    return {
        "steps": [
            {"id": "src", "type": "source", "source": {"kind": "table", "table_name": "etl_orders"}},
            {"id": "out", "type": "sink", "input": "src", "sink": {"kind": "postgres", "table": "etl_out"}},
        ]
    }


def _pipeline_row(**overrides):
    row = {
        "id": "pl_1",
        "owner_user_id": TEST_USER.id,
        "name": "Orders",
        "description": "Nightly orders",
        "status": "draft",
        "version": 1,
        "definition": _definition(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _run_row(**overrides):
    row = {
        "id": "run_1",
        "pipeline_id": "pl_1",
        "owner_user_id": TEST_USER.id,
        "job_id": "job_1",
        "status": "queued",
        "dry_run": False,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _dashboard_row(**overrides):
    row = {
        "pipeline_id": "pl_1",
        "notebook_id": "nb_1",
        "cell_id": "cell_1",
        "table": "etl_out",
        "charts": [],
    }
    row.update(overrides)
    return row


def test_pipelines_disabled_returns_403(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(pipelines_route.settings, "ETL_ENABLED", False)

    response = client.get("/api/pipelines/sources/tables")

    assert response.status_code == 403


def test_list_source_tables(client: TestClient, monkeypatch) -> None:
    service = _fake(
        list_source_tables=lambda: [
            {"table_name": "etl_orders", "columns": [{"name": "id", "data_type": "text"}]}
        ]
    )
    monkeypatch.setattr(pipelines_route, "_svc", lambda: service)

    response = client.get("/api/pipelines/sources/tables")

    assert response.status_code == 200
    assert response.json()[0]["table_name"] == "etl_orders"


def test_resolve_source(client: TestClient, monkeypatch) -> None:
    service = _fake(resolve_source_columns=lambda spec, user_id: ["id", "total"])
    monkeypatch.setattr(pipelines_route, "_svc", lambda: service)

    response = client.post(
        "/api/pipelines/sources/resolve",
        json={"source": {"kind": "table", "table_name": "etl_orders"}},
    )

    assert response.status_code == 200
    assert response.json()["columns"] == ["id", "total"]


def test_resolve_source_reports_validation_errors(client: TestClient, monkeypatch) -> None:
    def boom(spec, user_id):
        raise PipelineValidationError("unknown table")

    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake(resolve_source_columns=boom))

    response = client.post(
        "/api/pipelines/sources/resolve",
        json={"source": {"kind": "table", "table_name": "nope"}},
    )

    assert response.status_code == 400


def test_plan_pipeline(client: TestClient, monkeypatch) -> None:
    definition = PipelineDefinition(**_definition())
    planner = _fake(
        plan=lambda *a, **k: {
            "definition": definition,
            "name": "Orders",
            "description": "Nightly orders",
            "validation": {"ok": True},
        }
    )
    monkeypatch.setattr(pipelines_route, "ETLPlannerAgent", lambda: planner)
    monkeypatch.setattr(
        pipelines_route, "PlannerContextBuilder", lambda svc: _fake(build=lambda *a, **k: {})
    )
    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake())

    response = client.post("/api/pipelines/plan", json={"query": "build a nightly orders pipeline"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Orders"
    assert "writes to" in body["reply"]


def test_plan_stage(client: TestClient, monkeypatch) -> None:
    definition = PipelineDefinition(**_definition())
    planner = _fake(
        plan_stage=lambda *a, **k: {
            "definition": definition,
            "steps": [],
            "validation": {"ok": True},
            "reply": "Added a filter.",
        }
    )
    monkeypatch.setattr(pipelines_route, "ETLPlannerAgent", lambda: planner)
    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake())

    response = client.post(
        "/api/pipelines/stages/plan",
        json={
            "description": "keep only completed orders",
            "definition": _definition(),
            "insert_after": "src",
            "insert_before": "out",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Added a filter."


def test_create_pipeline(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(create_pipeline=lambda *a, **k: _pipeline_row())
    )

    response = client.post(
        "/api/pipelines",
        json={"name": "Orders", "description": "Nightly", "definition": _definition()},
    )

    assert response.status_code == 201
    assert response.json()["id"] == "pl_1"


def test_create_pipeline_validation_error(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise PipelineValidationError("missing sink")

    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake(create_pipeline=boom))

    response = client.post(
        "/api/pipelines", json={"name": "Orders", "definition": _definition()}
    )

    assert response.status_code == 400


def test_list_pipelines(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(list_pipelines=lambda *a, **k: [_pipeline_row()]),
    )

    response = client.get("/api/pipelines")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Orders"


def test_get_pipeline_missing(client: TestClient, monkeypatch) -> None:
    def boom(pipeline_id, user_id):
        raise PipelineNotFoundError(pipeline_id)

    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake(get_pipeline=boom))

    assert client.get("/api/pipelines/pl_missing").status_code == 404


def test_update_pipeline(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(update_pipeline=lambda *a, **k: _pipeline_row(name="Renamed")),
    )

    response = client.patch("/api/pipelines/pl_1", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_delete_pipeline(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(delete_pipeline=lambda *a, **k: None)
    )

    assert client.delete("/api/pipelines/pl_1").status_code == 204


def test_approve_pipeline(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(approve_pipeline=lambda *a, **k: _pipeline_row(status="approved")),
    )

    response = client.post("/api/pipelines/pl_1/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_run_pipeline(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(create_run=lambda *a, **k: _run_row())
    )

    response = client.post("/api/pipelines/pl_1/runs", json={"dry_run": True})

    assert response.status_code == 202
    assert response.json()["id"] == "run_1"


def test_run_pipeline_requires_approval(client: TestClient, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise PipelineNotApprovedError("approve first")

    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake(create_run=boom))

    assert client.post("/api/pipelines/pl_1/runs").status_code == 409


def test_list_pipeline_runs(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(list_runs=lambda *a, **k: [_run_row()])
    )

    response = client.get("/api/pipelines/pl_1/runs")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "run_1"


def test_get_pipeline_run_missing(client: TestClient, monkeypatch) -> None:
    def boom(run_id, user_id):
        raise PipelineRunNotFoundError(run_id)

    monkeypatch.setattr(pipelines_route, "_svc", lambda: _fake(get_run=boom))

    assert client.get("/api/pipelines/runs/missing").status_code == 404


def test_list_run_events(client: TestClient, monkeypatch) -> None:
    event = {"id": 1, "job_id": "job_1", "level": "info", "message": "queued"}
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(get_run=lambda *a, **k: _run_row())
    )
    monkeypatch.setattr(
        pipelines_route, "_jobs", lambda: _fake(list_events=lambda *a, **k: [event])
    )

    response = client.get("/api/pipelines/runs/run_1/events")

    assert response.status_code == 200
    assert response.json()[0]["message"] == "queued"


def test_list_run_events_without_a_job(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(get_run=lambda *a, **k: _run_row(job_id=None)),
    )

    response = client.get("/api/pipelines/runs/run_1/events")

    assert response.status_code == 200
    assert response.json() == []


def test_list_models(client: TestClient, monkeypatch) -> None:
    model = {
        "id": "model_1",
        "pipeline_id": "pl_1",
        "name": "Churn",
        "task": "classification",
        "model_key": "random_forest",
        "features": "[]",
        "metrics": "{}",
        "details": "{}",
    }
    monkeypatch.setattr(
        pipelines_route, "_svc", lambda: _fake(list_models=lambda *a, **k: [model])
    )

    response = client.get("/api/pipelines/models")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Churn"
    assert response.json()[0]["features"] == []


def test_get_pipeline_dashboard(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(get_dashboard=lambda *a, **k: _dashboard_row()),
    )

    response = client.get("/api/pipelines/pl_1/dashboard")

    assert response.status_code == 200
    assert response.json()["notebook_id"] == "nb_1"


def test_build_pipeline_dashboard(client: TestClient, monkeypatch) -> None:
    service = _fake(
        materialize_pipeline=lambda *a, **k: None,
        get_dashboard=lambda *a, **k: _dashboard_row(),
    )
    monkeypatch.setattr(pipelines_route, "_svc", lambda: service)

    response = client.post("/api/pipelines/pl_1/dashboard", json={"regenerate": True})

    assert response.status_code == 200
    assert response.json()["pipeline_id"] == "pl_1"


def test_save_pipeline_dashboard_layout(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(
            save_dashboard_layout=lambda *a, **k: _dashboard_row(layout={"order": ["c1"]})
        ),
    )

    response = client.put(
        "/api/pipelines/pl_1/dashboard/layout", json={"layout": {"order": ["c1"]}}
    )

    assert response.status_code == 200
    assert response.json()["layout"] == {"order": ["c1"]}


def test_update_pipeline_schedule(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines_route,
        "_svc",
        lambda: _fake(
            update_schedule=lambda *a, **k: _pipeline_row(
                schedule_mode="daily", schedule_enabled=True
            )
        ),
    )

    response = client.put(
        "/api/pipelines/pl_1/schedule",
        json={"schedule": {"mode": "daily", "enabled": True}},
    )

    assert response.status_code == 200
    assert response.json()["schedule_mode"] == "daily"


# ——————————————————————————————————————————————————————————————
# Workspace


def _workspace_row(**overrides):
    row = {
        "workspace_id": "ws_1",
        "name": "Churn",
        "status": "created",
        "data_type": "structured",
        "target_column": None,
        "input_sources": [],
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def test_create_workspace(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(create_workspace=lambda *a, **k: _workspace_row()),
    )

    response = client.post("/api/workspace", json={"name": "Churn"})

    assert response.status_code == 201
    assert response.json()["workspace_id"] == "ws_1"


def test_create_workspace_rejects_unknown_data_type(client: TestClient) -> None:
    response = client.post(
        "/api/workspace", json={"name": "Churn", "data_type": "video"}
    )

    assert response.status_code == 400


def test_list_workspaces(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(list_workspaces=lambda *a, **k: [_workspace_row()]),
    )

    response = client.get("/api/workspace")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Churn"


def test_get_workspace_missing(client: TestClient, monkeypatch) -> None:
    def boom(workspace_id, user_id):
        raise WorkspaceNotFoundError(workspace_id)

    monkeypatch.setattr(
        workspace_route, "_workspace_service", lambda: _fake(get_workspace=boom)
    )

    assert client.get("/api/workspace/ws_missing").status_code == 404


def test_update_workspace(client: TestClient, monkeypatch) -> None:
    service = _fake(
        get_workspace=lambda *a, **k: _workspace_row(),
        rename_workspace=lambda *a, **k: _workspace_row(name="Renamed"),
    )
    monkeypatch.setattr(workspace_route, "_workspace_service", lambda: service)

    response = client.patch("/api/workspace/ws_1", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_update_workspace_requires_a_name(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(get_workspace=lambda *a, **k: _workspace_row()),
    )

    assert client.patch("/api/workspace/ws_1", json={}).status_code == 400


def test_delete_workspace(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(delete_workspace=lambda *a, **k: None),
    )

    assert client.delete("/api/workspace/ws_1").status_code == 204


def test_build_rejects_non_structured_workspaces(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(get_workspace=lambda *a, **k: _workspace_row(data_type="image")),
    )

    response = client.post(
        "/api/workspace/ws_1/build/structured", json={"target_column": "y"}
    )

    assert response.status_code == 400


def test_build_requires_input_sources(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_route,
        "_workspace_service",
        lambda: _fake(get_workspace=lambda *a, **k: _workspace_row()),
    )

    response = client.post(
        "/api/workspace/ws_1/build/structured", json={"target_column": "y"}
    )

    assert response.status_code == 400
    assert "data source" in response.json()["detail"].lower()


def test_get_models_without_runs(client: TestClient, monkeypatch) -> None:
    service = _fake(
        get_workspace=lambda *a, **k: _workspace_row(),
        get_records_by_workspace_id=lambda *a, **k: [],
    )
    monkeypatch.setattr(workspace_route, "_workspace_service", lambda: service)

    response = client.get("/api/workspace/ws_1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "ws_1"
    assert body["best_model"] is None


def test_get_models_includes_details(client: TestClient, monkeypatch) -> None:
    summary = {
        "best_model": "random_forest",
        "model_details": {"best_model": "random_forest", "training_history": None},
    }
    model_record = {"agent_summary": summary, "output_file_urls": ["s3://sqrl/models/rf.joblib"]}

    def records(workspace_id, agent_type, limit, user_id):
        return [model_record] if agent_type == "model_building" else []

    service = _fake(
        get_workspace=lambda *a, **k: _workspace_row(status="completed", target_column="y"),
        get_records_by_workspace_id=records,
    )
    monkeypatch.setattr(workspace_route, "_workspace_service", lambda: service)

    response = client.get("/api/workspace/ws_1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["best_model"] == "random_forest"
    assert body["model_details"]["training_history"] is None
    assert body["model_files"][0]["model_key"] == "rf"


# ——————————————————————————————————————————————————————————————
# Notebook


def _notebook(**overrides):
    row = {
        "id": "nb_1",
        "name": "Churn notebook",
        "description": None,
        "status": "ready",
        "data_sources": [],
        "cell_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _cell(**overrides):
    row = {
        "id": "cell_1",
        "notebook_id": "nb_1",
        "type": "dashboard",
        "status": "complete",
        "created_at": NOW,
    }
    row.update(overrides)
    return row


def test_list_notebooks(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(list_notebooks=lambda **kwargs: [_notebook()])
    )

    response = client.get("/api/notebook")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "nb_1"


def test_create_notebook(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(create_notebook=lambda **kwargs: _notebook())
    )

    response = client.post("/api/notebook", json={"name": "Churn notebook"})

    assert response.status_code == 201
    assert response.json()["name"] == "Churn notebook"


def test_get_notebook_missing(client: TestClient, monkeypatch) -> None:
    def boom(notebook_id, **kwargs):
        raise NotebookNotFoundError(notebook_id)

    monkeypatch.setattr(notebook_route, "_svc", lambda: _fake(get_notebook=boom))

    assert client.get("/api/notebook/nb_missing").status_code == 404


def test_rename_notebook(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(rename_notebook=lambda *a, **k: _notebook(name="Renamed")),
    )

    response = client.patch("/api/notebook/nb_1", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_delete_notebook(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(delete_notebook=lambda *a, **k: None)
    )

    assert client.delete("/api/notebook/nb_1").status_code == 204


def test_duplicate_notebook(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(duplicate_notebook=lambda *a, **k: _notebook(id="nb_2")),
    )

    response = client.post("/api/notebook/nb_1/duplicate")

    assert response.status_code == 201
    assert response.json()["id"] == "nb_2"


def test_list_cells(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(list_cells=lambda *a, **k: [_cell()])
    )

    response = client.get("/api/notebook/nb_1/cells")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "cell_1"


def test_run_cell(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(run_cell=lambda **kwargs: _cell())
    )

    response = client.post("/api/notebook/nb_1/cells", json={"type": "question", "query": "why?"})

    assert response.status_code == 201


def test_run_cell_not_ready(client: TestClient, monkeypatch) -> None:
    def boom(**kwargs):
        raise NotebookNotReadyError("add a data source first")

    monkeypatch.setattr(notebook_route, "_svc", lambda: _fake(run_cell=boom))

    response = client.post("/api/notebook/nb_1/cells", json={"type": "eda"})

    assert response.status_code == 409


def test_update_cell_missing(client: TestClient, monkeypatch) -> None:
    def boom(**kwargs):
        raise CellNotFoundError("cell_missing")

    monkeypatch.setattr(notebook_route, "_svc", lambda: _fake(update_cell=boom))

    response = client.put("/api/notebook/nb_1/cells/cell_missing", json={"query": "again"})

    assert response.status_code == 404


def test_delete_cell(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route, "_svc", lambda: _fake(delete_cell=lambda *a, **k: None)
    )

    assert client.delete("/api/notebook/nb_1/cells/cell_1").status_code == 204


def test_save_dashboard_layout(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(save_dashboard_layout=lambda *a, **k: _cell()),
    )

    response = client.patch(
        "/api/notebook/nb_1/cells/cell_1/dashboard/layout",
        json={"order": ["chart_1"], "hidden_ids": []},
    )

    assert response.status_code == 200


def test_list_dashboard_versions(client: TestClient, monkeypatch) -> None:
    version = {
        "id": "ver_1",
        "cell_id": "cell_1",
        "notebook_id": "nb_1",
        "version_number": 1,
        "created_at": NOW,
    }
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(list_dashboard_versions=lambda *a, **k: [version]),
    )

    response = client.get("/api/notebook/nb_1/cells/cell_1/dashboard/versions")

    assert response.status_code == 200
    assert response.json()[0]["version_number"] == 1


def test_get_dashboard_version(client: TestClient, monkeypatch) -> None:
    version = {
        "id": "ver_1",
        "cell_id": "cell_1",
        "notebook_id": "nb_1",
        "version_number": 1,
        "created_at": NOW,
    }
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(get_dashboard_version=lambda *a, **k: version),
    )

    response = client.get("/api/notebook/nb_1/cells/cell_1/dashboard/versions/ver_1")

    assert response.status_code == 200
    assert response.json()["id"] == "ver_1"


def test_export_notebook_ipynb(client: TestClient, monkeypatch) -> None:
    service = _fake(
        get_notebook=lambda *a, **k: Notebook(**_notebook()),
        export_notebook_ipynb=lambda *a, **k: b'{"cells": []}',
    )
    monkeypatch.setattr(notebook_route, "_svc", lambda: service)

    response = client.get("/api/notebook/nb_1/export/ipynb")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ipynb+json")
    assert "churn-notebook.ipynb" in response.headers["content-disposition"]


def test_reorder_cells(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        notebook_route,
        "_svc",
        lambda: _fake(reorder_cells=lambda **kwargs: [_cell()]),
    )

    response = client.patch("/api/notebook/nb_1/cells/reorder", json=["cell_1"])

    assert response.status_code == 200
    assert response.json()[0]["id"] == "cell_1"
