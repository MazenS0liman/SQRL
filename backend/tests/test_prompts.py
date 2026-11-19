"""Tests for every prompt generator.

The prompt generators are the only place where application state is turned into
LLM instructions, so these tests exercise every enum member (both the system and
user prompt), the fallbacks for a missing prompt type, and the guardrails that
reject an unknown prompt type.
"""
from __future__ import annotations

import pytest

from squirrel.modules.prompts.abstract.IPromptGenerator import IPromptGenerator
from squirrel.modules.prompts.builder.TabularDataPromptGenerator import (
    ModelBuilderPromptGenerator,
    ModelBuilderPromptType,
)
from squirrel.modules.prompts.etl.ETLPlannerPromptGenerator import (
    ETLPlannerPromptGenerator,
    ETLPlannerPromptType,
)
from squirrel.modules.prompts.exploratory.ExploratoryPromptGenerator import (
    ExplorePromptType,
    ExploratoryPromptGenerator,
)
from squirrel.modules.prompts.inspect.InspectPromptGenerator import (
    InspectPromptGenerator,
    InspectPromptType,
)
from squirrel.modules.prompts.orchestrator.OrchestratorPromptGenerator import (
    OrchestratorPromptGenerator,
    OrchestratorPromptType,
)
from squirrel.modules.prompts.preprocess.PreprocessPromptGenerator import (
    PreprocessPromptGenerator,
    PreprocessPromptType,
)
from squirrel.schemas.eda import AxisSpec, ChartResult, Hypothesis, SqlSpec


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        id="h1",
        question="Do sales grow month over month?",
        title="Sales trend",
        description="Line chart of monthly sales",
        why_it_matters="Growth tracking",
        plot_type="line",
        analytical_type="trend",
    )


def _chart_result() -> ChartResult:
    axis = AxisSpec(column="month", label="Month", type="temporal")
    return ChartResult(
        hypothesis=_hypothesis(),
        sql_spec=SqlSpec(sql="select month, total from sales", x=axis, y=axis),
        observation="Sales trend upwards.",
    )


def _exploratory_context() -> dict:
    """Every context key any exploratory stage may read."""

    return {
        "schema_details": "orders(id, total)",
        "schema_summary": "orders(id, total)",
        "domain_context": {"domain": "Retail sales", "main_entities": ["order"]},
        "entities_and_metrics": {"entities": ["order"], "metrics": ["total"]},
        "analytical_types": "trend, segmentation",
        "plot_types": "line, bar",
        "max_hypotheses": 3,
        "available_columns": "orders: id, total",
        "previous_questions": ["Earlier question"],
        "hypothesis": _hypothesis(),
        "columns_details": "orders: id, total",
        "num_of_samples": 1,
        "sample": [{"id": 1, "total": 10}],
        "column_names": ["id", "total"],
        "chart_results": [_chart_result()],
        "question": "How did sales change?",
        "view_name": "sales_view",
        "previous_error": None,
    }


def _assert_prompt(value: str) -> None:
    assert isinstance(value, str)
    assert value.strip()
    # Every generator returns de-dented prose, never a stray placeholder.
    assert "None" != value.strip()


# ——————————————————————————————————————————————————————————————
# Abstract base


def test_base_generator_is_abstract() -> None:
    with pytest.raises(TypeError):
        IPromptGenerator()  # type: ignore[abstract]


# ——————————————————————————————————————————————————————————————
# Exploratory


@pytest.mark.parametrize("prompt_type", list(ExplorePromptType))
def test_exploratory_system_prompts(prompt_type: ExplorePromptType) -> None:
    generator = ExploratoryPromptGenerator()

    _assert_prompt(generator.generate_system_prompt(prompt_type))


@pytest.mark.parametrize("prompt_type", list(ExplorePromptType))
def test_exploratory_user_prompts(prompt_type: ExplorePromptType) -> None:
    generator = ExploratoryPromptGenerator()

    _assert_prompt(generator.generate_user_prompt(prompt_type, **_exploratory_context()))


def test_exploratory_user_prompts_use_supplied_context() -> None:
    generator = ExploratoryPromptGenerator()

    prompt = generator.generate_user_prompt(
        ExplorePromptType.GENERATE_SQL,
        domain_context="Retail sales",
        hypothesis=_hypothesis(),
        schema_summary="orders(id, total)",
        columns_details="id integer",
    )

    assert "Retail sales" in prompt
    assert "orders(id, total)" in prompt


def test_exploratory_system_prompt_defaults_and_rejects_unknown() -> None:
    generator = ExploratoryPromptGenerator()

    assert generator.generate_system_prompt(None) == "You are a helpful assistant."

    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_system_prompt("not-a-type")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unsupported prompt:"):
        generator.generate_user_prompt(None)


# ——————————————————————————————————————————————————————————————
# Inspect


@pytest.mark.parametrize("prompt_type", list(InspectPromptType))
def test_inspect_system_prompts(prompt_type: InspectPromptType) -> None:
    _assert_prompt(InspectPromptGenerator().generate_system_prompt(prompt_type))


@pytest.mark.parametrize("prompt_type", list(InspectPromptType))
def test_inspect_user_prompts(prompt_type: InspectPromptType) -> None:
    _assert_prompt(InspectPromptGenerator().generate_user_prompt(prompt_type))


def test_inspect_user_prompt_renders_catalog() -> None:
    generator = InspectPromptGenerator()

    prompt = generator.generate_user_prompt(
        InspectPromptType.GENERATE_STEP,
        objective="Fix the strategy",
        dataset_profile={"rows": 10},
        inspection_catalog=[
            {
                "name": "summary_statistics",
                "description": "Numeric summary",
                "arguments": {"columns": ["age"]},
            }
        ],
    )

    assert "Fix the strategy" in prompt
    assert "summary_statistics" in prompt


def test_inspect_prompt_defaults_and_errors() -> None:
    generator = InspectPromptGenerator()

    assert generator.generate_system_prompt(None) == "You are a helpful assistant."

    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_system_prompt("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_user_prompt(None)


# ——————————————————————————————————————————————————————————————
# Preprocess


@pytest.mark.parametrize("prompt_type", list(PreprocessPromptType))
def test_preprocess_system_prompts(prompt_type: PreprocessPromptType) -> None:
    _assert_prompt(PreprocessPromptGenerator().generate_system_prompt(prompt_type))


@pytest.mark.parametrize("prompt_type", list(PreprocessPromptType))
def test_preprocess_user_prompts(prompt_type: PreprocessPromptType) -> None:
    _assert_prompt(PreprocessPromptGenerator().generate_user_prompt(prompt_type))


def test_preprocess_user_prompt_renders_catalogs() -> None:
    generator = PreprocessPromptGenerator()

    prompt = generator.generate_user_prompt(
        PreprocessPromptType.GENERATE_PLAN,
        objective="Clean the churn data",
        dataset_profile={"rows": 100},
        cleaning_catalog=[{"name": "drop_duplicates", "description": "Dedupe"}],
        transform_catalog=[{"name": "bin_numeric", "description": "Bins"}],
        target_column="churn",
    )

    assert "Clean the churn data" in prompt
    assert "drop_duplicates" in prompt
    assert "churn" in prompt


def test_preprocess_prompt_defaults_and_errors() -> None:
    generator = PreprocessPromptGenerator()

    assert generator.generate_system_prompt(None) == "You are a helpful assistant."

    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_system_prompt("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_user_prompt(None)


# ——————————————————————————————————————————————————————————————
# Orchestrator


@pytest.mark.parametrize("prompt_type", list(OrchestratorPromptType))
def test_orchestrator_system_prompts(prompt_type: OrchestratorPromptType) -> None:
    prompt = OrchestratorPromptGenerator().generate_system_prompt(prompt_type)

    _assert_prompt(prompt)
    assert "chat" in prompt and "etl" in prompt


@pytest.mark.parametrize("prompt_type", list(OrchestratorPromptType))
def test_orchestrator_user_prompts(prompt_type: OrchestratorPromptType) -> None:
    prompt = OrchestratorPromptGenerator().generate_user_prompt(
        prompt_type,
        query="Build a churn model",
        file_urls=["s3://bucket/churn.csv"],
        project_id="p1",
    )

    _assert_prompt(prompt)


def test_orchestrator_user_prompt_rejects_unknown() -> None:
    generator = OrchestratorPromptGenerator()

    with pytest.raises(ValueError, match="Invalid prompt type"):
        generator.generate_user_prompt(None)
    with pytest.raises(ValueError, match="Invalid prompt type"):
        generator.generate_user_prompt("nope")  # type: ignore[arg-type]


# ——————————————————————————————————————————————————————————————
# Model builder


@pytest.mark.parametrize("prompt_type", list(ModelBuilderPromptType))
def test_model_builder_system_prompts(prompt_type: ModelBuilderPromptType) -> None:
    _assert_prompt(ModelBuilderPromptGenerator().generate_system_prompt(prompt_type))


@pytest.mark.parametrize("prompt_type", list(ModelBuilderPromptType))
def test_model_builder_user_prompts(prompt_type: ModelBuilderPromptType) -> None:
    generator = ModelBuilderPromptGenerator()

    prompt = generator.generate_user_prompt(
        prompt_type,
        objective="Predict churn",
        dataset_profile={"rows": 10, "columns": ["age"]},
        preprocessing_summary={"steps": []},
        step={"step": 1, "model_key": "random_forest"},
        model_catalog=[
            {
                "model_key": "random_forest",
                "task": "classification",
                "description": "Forest",
                "required_params": ["n_estimators"],
                "optional_params": [],
            }
        ],
        action_catalog=[
            {
                "action_key": "fit",
                "description": "Fit the model",
                "required_params": [],
                "optional_params": [],
            }
        ],
    )

    _assert_prompt(prompt)
    assert "Predict churn" in prompt
    assert "random_forest" in prompt


def test_model_builder_prompt_defaults_and_errors() -> None:
    generator = ModelBuilderPromptGenerator()

    assert generator.generate_system_prompt(None) == "You are a helpful assistant."

    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_system_prompt("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unsupported prompt type"):
        generator.generate_user_prompt(None)


# ——————————————————————————————————————————————————————————————
# ETL planner


@pytest.mark.parametrize("prompt_type", list(ETLPlannerPromptType))
def test_etl_system_prompts(prompt_type: ETLPlannerPromptType) -> None:
    prompt = ETLPlannerPromptGenerator().generate_system_prompt(prompt_type)

    _assert_prompt(prompt)
    assert "definition_json" in prompt


def test_etl_pipeline_user_prompt_renders_request_and_hints() -> None:
    generator = ETLPlannerPromptGenerator()

    prompt = generator.generate_user_prompt(
        ETLPlannerPromptType.PLAN_PIPELINE,
        query="Load the orders CSV into etl_orders",
        connector_id="conn-1",
        table="raw_orders",
        file_urls=["s3://bucket/orders.csv"],
        sink_table="etl_orders",
        notebook_id="nb-1",
        output_targets=["dashboard"],
        context={"available_sources": [{"name": "raw_orders"}]},
        history=[{"role": "user", "content": "earlier turn"}, {"role": "tool"}],
    )

    assert "Load the orders CSV" in prompt
    assert "conn-1" in prompt
    assert "earlier turn" in prompt
    assert "available_sources" in prompt


def test_etl_pipeline_user_prompt_includes_repair_block() -> None:
    generator = ETLPlannerPromptGenerator()

    prompt = generator.generate_user_prompt(
        ETLPlannerPromptType.PLAN_PIPELINE,
        query="fix it",
        previous_definition={"steps": []},
        validation_error="cycle detected",
    )

    assert "PREVIOUS ATTEMPT WAS REJECTED" in prompt
    assert "cycle detected" in prompt


def test_etl_stage_user_prompt_renders_insertion_point() -> None:
    generator = ETLPlannerPromptGenerator()

    prompt = generator.generate_user_prompt(
        ETLPlannerPromptType.PLAN_STAGE,
        description="Drop cancelled orders",
        definition={"steps": [{"id": "src"}]},
        insert_after="src",
        insert_before="sink",
        context={"src": ["id", "status"]},
    )

    assert "Drop cancelled orders" in prompt
    assert "insert_after: src" in prompt
    assert "insert_before: sink" in prompt


def test_etl_stage_user_prompt_includes_repair_block() -> None:
    generator = ETLPlannerPromptGenerator()

    prompt = generator.generate_user_prompt(
        ETLPlannerPromptType.PLAN_STAGE,
        description="Drop cancelled orders",
        validation_error="unknown column",
    )

    assert "PREVIOUS ATTEMPT WAS REJECTED" in prompt
    assert "unknown column" in prompt


def test_etl_prompt_rejects_missing_or_unknown_type() -> None:
    generator = ETLPlannerPromptGenerator()

    # The pipeline system prompt is the documented fallback.
    assert generator.generate_system_prompt(None)

    with pytest.raises(ValueError, match="Invalid prompt type"):
        generator.generate_user_prompt(None)
    with pytest.raises(ValueError, match="Invalid prompt type"):
        generator.generate_user_prompt("nope")  # type: ignore[arg-type]
