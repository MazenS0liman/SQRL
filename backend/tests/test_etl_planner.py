"""Tests for the constrained ETL planner agent."""
from __future__ import annotations

import json

import pytest

from squirrel.modules.agents.etl.ETLPlannerAgent import ETLPlannerAgent
from squirrel.modules.prompts.etl.ETLPlannerPromptGenerator import (
    ETLPlannerPromptGenerator,
    ETLPlannerPromptType,
)
from squirrel.services.pipelines.compiler import (
    PipelineCompiler,
    PipelineValidationError,
)


def _valid_response() -> dict:
    return {
        "name": "orders_export",
        "description": "Copy filtered orders into a reporting table.",
        "definition": {
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
        },
    }


def _agent(response: object) -> ETLPlannerAgent:
    """Build an agent without constructing a real LLM provider."""
    agent = ETLPlannerAgent.__new__(ETLPlannerAgent)
    agent._prompt_generator = ETLPlannerPromptGenerator()
    agent._compiler = PipelineCompiler()
    agent.get_response = lambda **kwargs: response  # type: ignore[method-assign]
    return agent


def test_plan_returns_a_validated_proposal() -> None:
    plan = _agent(_valid_response()).plan(
        "copy etl_orders into etl_orders_out",
        table="etl_orders",
    )

    assert plan["name"] == "orders_export"
    assert plan["description"].startswith("Copy filtered")
    assert plan["definition"].steps[0].id == "src"
    assert plan["validation"]["valid"] is True
    assert plan["validation"]["schema_checked"] is False
    assert plan["validation"]["sink"]["table"] == "etl_orders_out"


def test_plan_compiles_when_source_schemas_are_supplied() -> None:
    plan = _agent(_valid_response()).plan(
        "copy etl_orders",
        source_schemas={"src": ["id", "amount"]},
    )

    assert plan["validation"]["schema_checked"] is True


def test_plan_rejects_schema_violations() -> None:
    response = _valid_response()
    response["definition"]["steps"].append(
        {
            "id": "bad",
            "type": "transform",
            "input": "src",
            "transform": {"op": "explode"},
        }
    )

    with pytest.raises(PipelineValidationError, match="does not match the ETL schema"):
        _agent(response).plan("do something")


def test_plan_rejects_unknown_keys() -> None:
    response = _valid_response()
    response["definition"]["steps"][0]["source"]["raw_sql"] = "SELECT 1"

    with pytest.raises(PipelineValidationError, match="does not match the ETL schema"):
        _agent(response).plan("do something")


def test_plan_requires_a_definition_object() -> None:
    with pytest.raises(PipelineValidationError, match="missing a 'definition'"):
        _agent({"name": "no_definition"}).plan("do something")


def test_plan_rejects_reserved_sink_table() -> None:
    response = _valid_response()
    response["definition"]["steps"][1]["sink"]["table"] = "jobs"

    with pytest.raises(PipelineValidationError, match="platform-owned"):
        _agent(response).plan("write to jobs")


def test_plan_parses_markdown_fenced_json() -> None:
    response = "```json\n" + json.dumps(_valid_response()) + "\n```"

    plan = _agent(response).plan("copy etl_orders")

    assert plan["definition"].steps[-1].type == "sink"


def test_plan_rejects_non_json_responses() -> None:
    with pytest.raises(PipelineValidationError, match="not valid JSON"):
        _agent("I cannot help with that.").plan("copy etl_orders")


def test_plan_rejects_empty_responses() -> None:
    with pytest.raises(PipelineValidationError, match="no response"):
        _agent(None).plan("copy etl_orders")


def test_plan_falls_back_to_the_query_for_the_name() -> None:
    response = _valid_response()
    response["name"] = "   "

    plan = _agent(response).plan("copy etl_orders")

    assert plan["name"] == "copy etl_orders"


def test_system_prompt_documents_the_closed_dsl() -> None:
    prompt = ETLPlannerPromptGenerator().generate_system_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_PIPELINE
    )

    for token in ("source", "transform", "sink", "etl_", "aggregate", "join"):
        assert token in prompt
    assert "never write SQL" in prompt
    assert "```" not in prompt


def test_user_prompt_includes_supplied_identifiers() -> None:
    prompt = ETLPlannerPromptGenerator().generate_user_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
        query="join orders and customers",
        connector_id="connector-1",
        table="orders",
        sink_table="etl_customer_orders",
    )

    assert "join orders and customers" in prompt
    assert "connector-1" in prompt
    assert "orders" in prompt
    assert "etl_customer_orders" in prompt


def test_user_prompt_requires_the_plan_prompt_type() -> None:
    with pytest.raises(ValueError, match="Invalid prompt type"):
        ETLPlannerPromptGenerator().generate_user_prompt(prompt_type=None)
