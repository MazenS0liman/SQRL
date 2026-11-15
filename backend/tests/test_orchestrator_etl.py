"""Tests for the orchestrator's ETL route."""
from __future__ import annotations

import asyncio
import importlib

import pytest

from squirrel.core.config import settings
from squirrel.modules.agents.orchestrator.OrchestratorAgent import OrchestratorAgent
from squirrel.modules.prompts.orchestrator.OrchestratorPromptGenerator import (
    OrchestratorPromptGenerator,
)
from squirrel.schemas.api import AgentRequest
from squirrel.schemas.etl import PipelineDefinition
orchestrator_module = importlib.import_module(
    "squirrel.services.orchestrator.OrchestratorService"
)
from squirrel.services.orchestrator.OrchestratorService import OrchestratorService
from squirrel.services.pipelines.compiler import PipelineValidationError


# ——————————————————————————————————————————————————————————————
# Helpers


def _definition() -> PipelineDefinition:
    return PipelineDefinition.model_validate(
        {
            "steps": [
                {
                    "id": "src",
                    "type": "source",
                    "source": {"kind": "table", "table_name": "etl_orders"},
                },
                {
                    "id": "out",
                    "type": "sink",
                    "input": "src",
                    "sink": {"kind": "postgres", "table": "etl_orders_out"},
                },
            ]
        }
    )


def _request() -> AgentRequest:
    return AgentRequest(
        query="copy etl_orders into etl_orders_out",
        projectId="project-1",
        conversationId="conversation-1",
        body={"table": "etl_orders", "sinkTable": "etl_orders_out"},
    )


class FakePlanner:
    """ETLPlannerAgent double."""

    def __init__(self, plan: dict | None = None, error: Exception | None = None) -> None:
        self.plan_result = plan or {
            "name": "orders_export",
            "description": "Copy orders.",
            "definition": _definition(),
            "validation": {"valid": True, "steps": [], "sink": {}},
        }
        self.error = error
        self.calls: list[tuple] = []
        self.model = None

    def plan(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if self.error is not None:
            raise self.error
        return self.plan_result


class FakeOrchestratorAgent:
    """OrchestratorAgent double returning a fixed route decision."""

    def __init__(self, route_key: str = "etl") -> None:
        self.route_key = route_key

    def orchestrate(self, **kwargs) -> dict:
        return {
            "route_key": self.route_key,
            "target_column": None,
            "confidence": 0.9,
            "reasoning": "The user wants to move tabular data.",
            "agent_name": "ETLPlannerAgent",
            "observations": [],
        }


def _service() -> OrchestratorService:
    return OrchestratorService.__new__(OrchestratorService)


# ——————————————————————————————————————————————————————————————
# Classifier


def test_decision_maps_etl_to_the_planner_agent() -> None:
    decision = OrchestratorAgent._decision("etl", None, 0.9, "move data")

    assert decision["route_key"] == "etl"
    assert decision["agent_name"] == "ETLPlannerAgent"
    assert decision["target_column"] is None
    assert decision["confidence"] == 0.9


def test_classification_schema_allows_etl() -> None:
    schema = OrchestratorAgent._classification_schema()

    assert "etl" in schema["properties"]["route"]["enum"]


def test_orchestrate_accepts_an_etl_classification() -> None:
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._prompt_generator = OrchestratorPromptGenerator()
    agent.get_response = lambda **kwargs: {  # type: ignore[method-assign]
        "route": "etl",
        "target_column": None,
        "confidence": 0.77,
        "reasoning": "Batch data movement.",
    }

    decision = agent.orchestrate("join orders and export them")

    assert decision["route_key"] == "etl"
    assert decision["agent_name"] == "ETLPlannerAgent"
    assert decision["confidence"] == 0.77


# ——————————————————————————————————————————————————————————————
# _plan_etl


def test_plan_etl_returns_a_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ETL_ENABLED", True)
    planner = FakePlanner()
    monkeypatch.setattr(orchestrator_module, "ETLPlannerAgent", lambda: planner)

    output = asyncio.run(_service()._plan_etl(_request(), owner_user_id="user-1"))

    assert "orders_export" in output["reply"]
    assert "etl_orders_out" in output["reply"]
    assert output["body"]["plan"]["name"] == "orders_export"
    assert output["metadata"]["agent"] == "ETLPlannerAgent"
    assert output["metadata"]["task"] == "etl"
    assert output["metadata"]["owner_user_id"] == "user-1"
    assert planner.calls[0][0] == "copy etl_orders into etl_orders_out"
    assert planner.calls[0][1]["table"] == "etl_orders"
    assert planner.calls[0][1]["sink_table"] == "etl_orders_out"


def test_plan_etl_is_disabled_by_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ETL_ENABLED", False)
    planner = FakePlanner()
    monkeypatch.setattr(orchestrator_module, "ETLPlannerAgent", lambda: planner)

    output = asyncio.run(_service()._plan_etl(_request()))

    assert "disabled" in output["reply"]
    assert planner.calls == []


def test_plan_etl_reports_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ETL_ENABLED", True)
    planner = FakePlanner(error=PipelineValidationError("unsafe sink table"))
    monkeypatch.setattr(orchestrator_module, "ETLPlannerAgent", lambda: planner)

    output = asyncio.run(_service()._plan_etl(_request()))

    assert "couldn't turn that request into a safe ETL pipeline" in output["reply"]
    assert "unsafe sink table" in output["reply"]
    assert output["body"]["error"] == "unsafe sink table"


def test_plan_etl_reports_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ETL_ENABLED", True)
    planner = FakePlanner(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr(orchestrator_module, "ETLPlannerAgent", lambda: planner)

    output = asyncio.run(_service()._plan_etl(_request()))

    assert "An error occurred while planning the ETL pipeline" in output["reply"]
    assert "provider unavailable" in output["reply"]


# ——————————————————————————————————————————————————————————————
# handle_request integration


def test_handle_request_dispatches_etl_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ETL_ENABLED", True)
    planner = FakePlanner()
    monkeypatch.setattr(orchestrator_module, "ETLPlannerAgent", lambda: planner)
    service = _service()
    service._agent = FakeOrchestratorAgent("etl")

    response = asyncio.run(
        service.handle_request(_request(), owner_user_id="user-1")
    )

    assert response.action == "etl"
    assert response.body["plan"]["name"] == "orders_export"
    assert response.metadata["route_key"] == "etl"
    assert response.metadata["agent_name"] == "ETLPlannerAgent"
    assert response.projectId == "project-1"
    assert response.conversationId == "conversation-1"
