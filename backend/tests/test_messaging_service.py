"""Tests for the chat messaging persistence service."""
from __future__ import annotations

import pytest

from squirrel.schemas.api import AgentRequest, AgentResponse, LLMTokens
import squirrel.services.chat.MessagingService as messaging_module
from squirrel.services.chat.MessagingService import MessagingService
from tests.fakes import FakeDB


def _request(**overrides) -> AgentRequest:
    payload = {
        "query": "What is churn?",
        "projectId": "proj-1",
        "conversationId": "conv-1",
    }
    payload.update(overrides)
    return AgentRequest(**payload)


def _response(**overrides) -> AgentResponse:
    payload = {
        "reply": "Churn is the rate of customers leaving.",
        "action": "chat",
        "projectId": "proj-1",
        "conversationId": "conv-1",
        "responseTime": 1.25,
        "llmTokens": LLMTokens(promptTokens=10, completionTokens=20, totalTokens=30),
    }
    payload.update(overrides)
    return AgentResponse(**payload)


@pytest.fixture()
def db(monkeypatch) -> FakeDB:
    fake = FakeDB()
    monkeypatch.setattr(messaging_module, "PostgresService", lambda: fake)
    return fake


def test_init_creates_table_when_missing(db: FakeDB) -> None:
    service = MessagingService()

    assert service._db is db
    assert len(db.executes) == 1
    assert "CREATE TABLE IF NOT EXISTS chat_messages" in db.executes[0].sql


def test_init_skips_create_when_table_exists(monkeypatch) -> None:
    fake = FakeDB(retrieve_results=[{"rows": [{"id": 1}]}])
    monkeypatch.setattr(messaging_module, "PostgresService", lambda: fake)

    MessagingService()

    assert fake.executes == []


def test_save_chat_turn_persists_every_field(db: FakeDB) -> None:
    db.queue_insert(True)
    service = MessagingService()

    ok = service.save_chat_turn(
        _request(), _response(), ["s3://bucket/report.csv"]
    )

    assert ok is True
    params = db.inserts[0].params
    assert params["conversation_id"] == "conv-1"
    assert params["user_message"] == "What is churn?"
    assert params["file_urls"] == ["s3://bucket/report.csv"]
    assert params["assistant_reply"].startswith("Churn is")
    assert params["prompt_tokens"] == 10
    assert params["completion_tokens"] == 20
    assert params["total_tokens"] == 30
    assert params["response_time_ms"] == 1.25


def test_save_chat_turn_defaults_missing_metadata(db: FakeDB) -> None:
    db.queue_insert(True)
    service = MessagingService()

    service.save_chat_turn(
        _request(),
        _response(
            llmTokens=LLMTokens(promptTokens=0, completionTokens=0, totalTokens=0),
            responseTime=0,
        ),
        [],
    )

    params = db.inserts[0].params
    assert params["prompt_tokens"] == 0
    assert params["completion_tokens"] == 0
    assert params["total_tokens"] == 0
    assert params["response_time_ms"] == 0


def test_save_chat_turn_reports_failure(db: FakeDB) -> None:
    db.queue_insert(False)
    service = MessagingService()

    assert service.save_chat_turn(_request(), _response(), []) is False


def test_get_conversation_history_sorts_and_limits(db: FakeDB) -> None:
    db.queue_retrieve(
        {"rows": [{"id": 0}]},  # __init__ table-existence probe
        {
            "rows": [
                {"id": 2, "created_at": "2026-01-02"},
                {"id": 1, "created_at": "2026-01-01"},
                {"id": 3, "created_at": "2026-01-03"},
            ]
        },
    )
    service = MessagingService()

    rows = service.get_conversation_history("conv-1", limit=2)

    assert [row["id"] for row in rows] == [1, 2]
    assert db.retrieves[1].filters == {"conversation_id": "conv-1"}


def test_get_conversation_history_handles_missing_rows(db: FakeDB) -> None:
    service = MessagingService()

    assert service.get_conversation_history("conv-1") == []
