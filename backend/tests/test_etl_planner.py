"""Tests for the constrained ETL planner agent."""
from __future__ import annotations

import json

import pytest

from squirrel.modules.agents.etl.ETLPlannerAgent import ETLPlannerAgent
from squirrel.modules.prompts.etl.ETLPlannerPromptGenerator import (
    ETLPlannerPromptGenerator,
    ETLPlannerPromptType,
)
from squirrel.schemas.etl import PipelineDefinition
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


def test_plan_extracts_definition_json_responses() -> None:
    response = _valid_response()
    encoded = {
        "name": response["name"],
        "description": response["description"],
        "definition_json": json.dumps(response["definition"]),
    }

    plan = _agent(encoded).plan("copy etl_orders")

    assert plan["definition"].steps[-1].sink.table == "etl_orders_out"


def test_plan_compiles_when_source_resolver_is_supplied() -> None:
    plan = _agent(_valid_response()).plan(
        "copy etl_orders",
        source_resolver=lambda spec: ["id", "amount"],
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


def test_plan_repairs_after_a_validation_error() -> None:
    rejected = _valid_response()
    rejected["definition"]["steps"][1]["sink"]["table"] = "jobs"  # reserved
    responses = [rejected, _valid_response()]

    agent = _agent(responses[0])
    calls = {"count": 0}

    def next_response(**_: object) -> dict:
        response = responses[min(calls["count"], len(responses) - 1)]
        calls["count"] += 1
        return response

    agent.get_response = next_response  # type: ignore[method-assign]
    plan = agent.plan("copy etl_orders", max_attempts=2)

    assert calls["count"] == 2
    assert plan["validation"]["attempts"] == 2
    assert plan["validation"]["sink"]["table"] == "etl_orders_out"


def test_system_prompt_decomposes_operations_into_separate_steps() -> None:
    prompt = ETLPlannerPromptGenerator().generate_system_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_PIPELINE
    )

    assert "# Decomposition" in prompt
    assert "separate aggregate step" in prompt


def test_user_prompt_includes_conversation_history() -> None:
    prompt = ETLPlannerPromptGenerator().generate_user_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
        query="now aggregate by day",
        history=[
            {"role": "user", "content": "keep only completed orders"},
            {"role": "assistant", "content": "Drafted a filter step."},
        ],
    )

    assert "# CONVERSATION SO FAR" in prompt
    assert "keep only completed orders" in prompt
    assert "now aggregate by day" in prompt


def test_user_prompt_includes_previous_definition_for_refinement() -> None:
    prompt = ETLPlannerPromptGenerator().generate_user_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
        query="add a limit of 100",
        previous_definition={"steps": [{"id": "src", "type": "source"}]},
    )

    assert "CURRENT DEFINITION" in prompt
    assert '"id": "src"' in prompt


# ——————————————————————————————————————————————————————————————
# Stage insertion


def _stage_base() -> dict:
    return {
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


def _stage_response(definition: dict, reply: str = "Added the stage.") -> dict:
    return {"reply": reply, "definition_json": json.dumps(definition)}


def _plan_stage(
    agent: ETLPlannerAgent,
    base: dict | None = None,
    **kwargs: object,
):
    return agent.plan_stage(
        str(kwargs.pop("description", "keep amount over 100")),
        definition=PipelineDefinition.model_validate(base or _stage_base()),
        insert_after=str(kwargs.pop("insert_after", "src")),
        insert_before=str(kwargs.pop("insert_before", "out")),
        **kwargs,  # type: ignore[arg-type]
    )


def _insert_limited(updated: dict) -> dict:
    updated["steps"].insert(
        1,
        {
            "id": "limited",
            "type": "transform",
            "input": "src",
            "transform": {"op": "limit", "count": 10},
        },
    )
    updated["steps"][-1]["input"] = "limited"
    return updated


def test_plan_stage_inserts_between_two_steps_and_rewires_the_sink() -> None:
    updated = _stage_base()
    updated["steps"].insert(
        1,
        {
            "id": "filtered",
            "type": "transform",
            "input": "src",
            "transform": {
                "op": "filter",
                "column": "amount",
                "comparison": "gt",
                "value": 100,
            },
        },
    )
    updated["steps"][-1]["input"] = "filtered"

    plan = _plan_stage(
        _agent(_stage_response(updated)),
        source_resolver=lambda spec: ["id", "amount"],
    )

    assert [step.id for step in plan["steps"]] == ["filtered"]
    assert plan["definition"].steps[-1].input == "filtered"
    assert plan["validation"]["insert_after"] == "src"
    assert plan["validation"]["insert_before"] == "out"
    assert plan["validation"]["schema_checked"] is True
    assert plan["reply"] == "Added the stage."


def test_plan_stage_rejects_a_changed_sink_payload() -> None:
    updated = _insert_limited(_stage_base())
    updated["steps"][-1]["sink"]["table"] = "etl_somewhere_else"

    with pytest.raises(PipelineValidationError, match="changed sink step"):
        _plan_stage(_agent(_stage_response(updated)))


def test_plan_stage_rejects_a_stage_that_is_not_connected() -> None:
    updated = _stage_base()
    updated["steps"].insert(
        1,
        {
            "id": "limited",
            "type": "transform",
            "input": "src",
            "transform": {"op": "limit", "count": 10},
        },
    )
    # The sink still reads the source, so the new step is dangling.

    with pytest.raises(PipelineValidationError, match="still reads"):
        _plan_stage(_agent(_stage_response(updated)))


def test_plan_stage_rejects_rewiring_an_unrelated_step() -> None:
    base = {
        "steps": [
            {
                "id": "src",
                "type": "source",
                "source": {"kind": "table", "table_name": "etl_orders"},
            },
            {
                "id": "limited",
                "type": "transform",
                "input": "src",
                "transform": {"op": "limit", "count": 100},
            },
            {
                "id": "out",
                "type": "sink",
                "input": "limited",
                "sink": {"kind": "postgres", "table": "etl_orders_out"},
            },
        ]
    }
    updated = json.loads(json.dumps(base))
    updated["steps"].insert(
        1,
        {
            "id": "filtered",
            "type": "transform",
            "input": "src",
            "transform": {
                "op": "filter",
                "column": "amount",
                "comparison": "gt",
                "value": 0,
            },
        },
    )
    updated["steps"][1]["input"] = "filtered"
    # Skip the existing limit step entirely.
    updated["steps"][-1]["input"] = "filtered"

    with pytest.raises(PipelineValidationError, match="rewired step 'out'"):
        _plan_stage(
            _agent(_stage_response(updated)),
            base=base,
            insert_before="limited",
        )


def test_plan_stage_rejects_removing_an_existing_step() -> None:
    updated = _insert_limited(_stage_base())
    updated["steps"] = [step for step in updated["steps"] if step["id"] != "out"]

    with pytest.raises(PipelineValidationError, match="removed step"):
        _plan_stage(_agent(_stage_response(updated)))


def test_stage_prompt_documents_the_insertion_envelope() -> None:
    generator = ETLPlannerPromptGenerator()
    system = generator.generate_system_prompt(prompt_type=ETLPlannerPromptType.PLAN_STAGE)
    user = generator.generate_user_prompt(
        prompt_type=ETLPlannerPromptType.PLAN_STAGE,
        description="keep completed orders",
        definition=_stage_base(),
        insert_after="src",
        insert_before="out",
        context={"input_columns": ["id", "status"]},
    )

    assert "definition_json" in system
    assert "insert_after" in system
    assert "# INSERT POSITION" in user
    assert "keep completed orders" in user
    assert "input_columns" in user
