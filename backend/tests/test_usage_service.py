"""Tests for per-user LLM usage recording and aggregation."""
from __future__ import annotations

from datetime import datetime

import pytest

import squirrel.services.usage.UsageService as usage_module
from squirrel.constants.usage import estimate_cost, resolve_pricing
from squirrel.services.usage.UsageService import UsageService
from squirrel.services.usage.context import (
    current_usage_source,
    current_usage_user_id,
    get_usage_source,
    get_usage_user_id,
    set_usage_source,
    set_usage_user_id,
    source_for_path,
    usage_context,
)
from tests.fakes import FakeDB


# ——————————————————————————————————————————————————————————————
# Recording


def test_record_inserts_normalised_event(fake_db: FakeDB) -> None:
    service = UsageService(db=fake_db)

    service.record(
        user_id="user-1",
        provider="GEMINI",
        model="models/gemini-2.5-flash",
        prompt_tokens=1_000,
        completion_tokens=2_000,
        source="Chat",
    )

    # First access bootstraps the ledger table.
    assert len(fake_db.executes) == 1
    assert "CREATE TABLE IF NOT EXISTS llm_usage_events" in fake_db.executes[0].sql

    assert len(fake_db.queries) == 1
    insert = fake_db.queries[0]
    assert "INSERT INTO llm_usage_events" in insert.sql
    assert insert.fetch is False
    assert insert.params["user_id"] == "user-1"
    assert insert.params["provider"] == "gemini"
    assert insert.params["model"] == "models/gemini-2.5-flash"
    assert insert.params["source"] == "chat"
    assert insert.params["prompt_tokens"] == 1_000
    assert insert.params["completion_tokens"] == 2_000
    assert insert.params["total_tokens"] == 3_000
    assert insert.params["request_count"] == 1
    assert insert.params["cost"] == pytest.approx(estimate_cost("gemini", "gemini-2.5-flash", 1_000, 2_000))


def test_record_falls_back_to_total_tokens_and_unknowns(fake_db: FakeDB) -> None:
    service = UsageService(db=fake_db)

    service.record(
        user_id=None,
        provider="",
        model="",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=0,
        request_count=0,
    )

    params = fake_db.last_query.params
    assert params["total_tokens"] == 15
    assert params["request_count"] == 1
    assert params["provider"] == "unknown"
    assert params["model"] == "unknown"
    assert params["source"] == "agent"
    assert params["cost"] == 0.0


def test_record_uses_context_source(fake_db: FakeDB) -> None:
    service = UsageService(db=fake_db)

    with usage_context(source="workspace"):
        service.record(
            user_id="user-1",
            provider="gemini",
            model="gemini-2.5-pro",
            prompt_tokens=1,
        )

    assert fake_db.last_query.params["source"] == "workspace"


def test_record_ignores_empty_events(fake_db: FakeDB) -> None:
    service = UsageService(db=fake_db)

    service.record(
        user_id="user-1",
        provider="gemini",
        model="gemini-2.5-pro",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        request_count=0,
    )

    assert fake_db.queries == []
    assert fake_db.executes == []


def test_record_llm_usage_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def record(self, **_kwargs) -> None:
            raise RuntimeError("database is down")

    monkeypatch.setattr(usage_module, "_get_service", lambda: Boom())

    # Must swallow the failure rather than break the caller's LLM call.
    usage_module.record_llm_usage(
        provider="gemini",
        model="gemini-2.5-pro",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )


def test_record_llm_usage_uses_context_user(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Recorder:
        def record(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(usage_module, "_get_service", lambda: Recorder())

    with usage_context(user_id="ctx-user", source="notebook"):
        usage_module.record_llm_usage(
            provider="groq",
            model="llama-3.3-70b",
            prompt_tokens=3,
            completion_tokens=4,
            total_tokens=7,
        )

    assert captured["user_id"] == "ctx-user"
    # ``record_llm_usage`` forwards the explicit source (None here); the
    # service-level fallback to context is covered separately.
    assert captured["source"] is None
    assert captured["total_tokens"] == 7


# ——————————————————————————————————————————————————————————————
# Aggregation


def _overview_results() -> list[dict]:
    return [
        {
            "rows": [
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "requests": 2,
                    "estimated_cost_usd": 0.0123456,
                }
            ]
        },
        {
            "rows": [
                {
                    "provider": "gemini",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "requests": 2,
                    "estimated_cost_usd": 0.0123456,
                }
            ]
        },
        {
            "rows": [
                {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "requests": 2,
                    "estimated_cost_usd": 0.0123456,
                }
            ]
        },
        {
            "rows": [
                {
                    "day": "2026-11-18",
                    "provider": "gemini",
                    "total_tokens": 150,
                    "estimated_cost_usd": 0.0123456,
                }
            ]
        },
        {
            "rows": [
                {
                    "source": "chat",
                    "total_tokens": 150,
                    "requests": 2,
                    "estimated_cost_usd": 0.0123456,
                }
            ]
        },
        {
            "rows": [
                {
                    "id": 7,
                    "user_id": "user-1",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "source": "chat",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "request_count": 1,
                    "estimated_cost_usd": 0.0123456,
                    "created_at": datetime(2026, 11, 18, 12, 0, 0),
                }
            ]
        },
    ]


def test_overview_normalises_aggregates(fake_db: FakeDB) -> None:
    fake_db.queue(*_overview_results())
    service = UsageService(db=fake_db)

    overview = service.overview("user-1", days=7)

    assert len(fake_db.queries) == 6
    assert overview["window_days"] == 7
    assert overview["totals"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "requests": 2,
        "estimated_cost_usd": 0.012346,
    }
    assert overview["by_provider"][0]["provider"] == "gemini"
    assert overview["by_model"][0]["model"] == "gemini-2.5-flash"
    assert overview["by_source"][0]["source"] == "chat"
    assert overview["daily"] == [
        {
            "day": "2026-11-18",
            "provider": "gemini",
            "total_tokens": 150,
            "estimated_cost_usd": 0.0123456,
        }
    ]
    assert overview["recent_events"][0]["id"] == 7
    assert overview["recent_events"][0]["created_at"] == "2026-11-18T12:00:00"

    # Every aggregate is scoped to the signed-in user.
    for query in fake_db.queries[:5]:
        assert query.params["user_id"] == "user-1"
        assert "since" in query.params
    assert fake_db.queries[5].params == {"user_id": "user-1"}


def test_overview_tolerates_empty_results(fake_db: FakeDB) -> None:
    service = UsageService(db=fake_db)

    overview = service.overview("user-1")

    assert overview["totals"]["total_tokens"] == 0
    assert overview["by_provider"] == []
    assert overview["by_model"] == []
    assert overview["by_source"] == []
    assert overview["daily"] == []
    assert overview["recent_events"] == []


# ——————————————————————————————————————————————————————————————
# Events


def test_events_applies_filters_and_pagination(fake_db: FakeDB) -> None:
    fake_db.queue(
        {"rows": [{"count": 12}]},
        {
            "rows": [
                {
                    "id": 1,
                    "user_id": "user-1",
                    "provider": "groq",
                    "model": "llama-3.3-70b",
                    "source": "chat",
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "total_tokens": 10,
                    "request_count": 1,
                    "estimated_cost_usd": 0.0,
                    "created_at": None,
                }
            ]
        },
    )
    service = UsageService(db=fake_db)

    rows, total = service.events(
        "user-1",
        limit=25,
        offset=50,
        provider="GROQ",
        source="Chat",
        days=30,
    )

    assert total == 12
    assert rows[0]["provider"] == "groq"
    count_query, page_query = fake_db.queries
    assert "user_id = %(user_id)s" in count_query.sql
    assert "provider = %(provider)s" in count_query.sql
    assert "source = %(source)s" in count_query.sql
    assert "created_at >= %(since)s" in count_query.sql
    assert count_query.params["provider"] == "groq"
    assert count_query.params["source"] == "chat"
    assert "since" in count_query.params
    assert page_query.params["limit"] == 25
    assert page_query.params["offset"] == 50


def test_events_without_filters(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"count": 0}]}, {"rows": []})
    service = UsageService(db=fake_db)

    rows, total = service.events("user-1")

    assert rows == []
    assert total == 0
    count_query, page_query = fake_db.queries
    assert count_query.sql.count("AND") == 0
    assert "limit" in page_query.params
    assert page_query.params["offset"] == 0


# ——————————————————————————————————————————————————————————————
# Context + pricing


def test_source_for_path_maps_features() -> None:
    assert source_for_path("/api/chat/message") == "chat"
    assert source_for_path("/api/workspace/abc/build/structured") == "workspace"
    assert source_for_path("/api/notebooks/abc/run") == "notebook"
    assert source_for_path("/api/pipelines/abc/runs") == "pipeline"
    assert source_for_path("/api/files/upload") == "files"
    assert source_for_path("/api/connectors") == "connector"
    assert source_for_path("/api/health") == "api"


def test_usage_context_restores_previous_values() -> None:
    set_usage_user_id("outer-user")
    set_usage_source("outer-source")

    try:
        with usage_context(user_id="inner-user", source="inner-source"):
            assert get_usage_user_id() == "inner-user"
            assert get_usage_source() == "inner-source"

        assert get_usage_user_id() == "outer-user"
        assert get_usage_source() == "outer-source"
    finally:
        # Context variables are process-wide for this thread; reset so other
        # tests do not inherit this one's attribution.
        current_usage_user_id.set(None)
        current_usage_source.set("agent")


def test_resolve_pricing_prefers_longest_match() -> None:
    # ``gpt-4o-mini`` must not resolve to the shorter ``gpt-4o`` entry.
    assert resolve_pricing("models/gpt-4o-mini") == (0.15, 0.60)
    assert resolve_pricing("GPT-4O") == (2.50, 10.00)
    assert resolve_pricing("some-local-model") == (0.0, 0.0)


def test_estimate_cost_rounds_to_six_places() -> None:
    cost = estimate_cost("gemini", "gemini-2.5-pro", 1234, 5678)
    assert cost == pytest.approx(
        (1234 / 1_000_000) * 1.25 + (5678 / 1_000_000) * 10.0,
        abs=1e-6,
    )
