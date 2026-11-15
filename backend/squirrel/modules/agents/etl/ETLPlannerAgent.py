#!/usr/bin/python
"""
ETL Planner Agent
=================

Turns a natural-language request into a validated
:class:`~squirrel.schemas.etl.PipelineDefinition`.

The agent is the only place LLM output enters the ETL feature. Its output is
immediately parsed into strict Pydantic models and structurally validated by
:class:`~squirrel.services.pipelines.compiler.PipelineCompiler`, so a
hallucinated operation, unknown key, or malformed DAG is rejected before it
can be stored or executed. The agent never executes anything itself.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from __future__ import annotations

# Standard Libraries
import json
import re
from typing import Any, Dict, List, Optional

# Abstract classes
from squirrel.modules.agents.abstract.IAgent import IAgent
from squirrel.modules.providers.abstract.IProvider import Provider

# Prompts
from squirrel.modules.prompts.etl.ETLPlannerPromptGenerator import (
    ETLPlannerPromptGenerator,
    ETLPlannerPromptType,
)

# Schemas / compiler
from squirrel.schemas.etl import PipelineDefinition
from squirrel.services.pipelines.compiler import (
    PipelineCompiler,
    PipelineValidationError,
)

# Logging
from loguru import logger

try:  # pydantic v2
    from pydantic import ValidationError
except ImportError:  # pragma: no cover - pydantic v1 fallback
    from pydantic.error_wrappers import ValidationError  # type: ignore


# ——————————————————————————————————————————————————————————————
# ETL planner agent


class ETLPlannerAgent(IAgent):
    """Plan a constrained ETL pipeline from a natural-language request."""

    def __init__(
        self,
        api_key: Optional[str] = "GEMINI_API_KEY_V1",
        provider: Optional[Provider] = Provider.GEMINI,
        model_name: Optional[str] = "models/gemini-2.5-flash",
        prompt_generator: Optional[ETLPlannerPromptGenerator] = None,
        config: Optional[Dict[str, Any]] = None,
        compiler: Optional[PipelineCompiler] = None,
    ) -> None:
        super().__init__(
            api_key,
            provider,
            model_name,
            prompt_generator=prompt_generator or ETLPlannerPromptGenerator(),
            config=config or {},
        )
        self._compiler = compiler or PipelineCompiler()

    # ── Public API ────────────────────────────────────────────────────────

    def plan(
        self,
        query: str,
        *,
        connector_id: Optional[str] = None,
        table: Optional[str] = None,
        file_urls: Optional[List[str]] = None,
        sink_table: Optional[str] = None,
        notebook_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        source_schemas: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Ask the LLM for a pipeline and return the validated proposal.

        :param query: User's natural-language request.
        :param connector_id: Connector the user explicitly referenced.
        :param table: Connector table the user explicitly referenced.
        :param file_urls: Attached file URLs the user explicitly supplied.
        :param sink_table: Requested Postgres destination, if any.
        :param notebook_id: Notebook to materialise a dashboard into.
        :param context: Extra grounding metadata (schemas, descriptions, ...).
        :param source_schemas: Optional columns per source step. When supplied,
            the definition is fully compiled (not just structurally validated).
        :raises PipelineValidationError: If the LLM output cannot be parsed or
            does not satisfy the DSL.
        :return: ``{name, description, definition, validation}``.
        """
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
            query=query,
            connector_id=connector_id,
            table=table,
            file_urls=file_urls or [],
            sink_table=sink_table,
            notebook_id=notebook_id,
            context=context or {},
        )

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._plan_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                "llama-3.3-70b-versatile",
                "models/gemini-2.5-flash",
            ],
        )

        parsed = self._parse_response(response)
        raw_definition = self._extract_definition(parsed)

        try:
            definition = PipelineDefinition.model_validate(raw_definition)
        except ValidationError as exc:
            raise PipelineValidationError(
                "The planner produced a pipeline that does not match the ETL "
                f"schema: {exc}"
            ) from exc

        try:
            self._compiler.validate_structure(definition)
            if source_schemas is not None:
                self._compiler.compile(definition, source_schemas)
        except PipelineValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface compiler failures clearly
            raise PipelineValidationError(
                f"The planner produced an invalid pipeline: {exc}"
            ) from exc

        sink_step = next(step for step in definition.steps if step.type == "sink")
        assert sink_step.sink is not None

        name = self._clean_name(parsed.get("name"), query)
        description = self._clean_description(parsed.get("description"))

        logger.info(
            "ETLPlannerAgent: planned pipeline '{}' with {} step(s)",
            name,
            len(definition.steps),
        )
        return {
            "name": name,
            "description": description,
            "definition": definition,
            "validation": {
                "valid": True,
                "steps": [
                    {"id": step.id, "type": step.type}
                    for step in definition.steps
                ],
                "sink": sink_step.sink.model_dump(),
                "schema_checked": source_schemas is not None,
            },
        }

    # ── Response parsing ──────────────────────────────────────────────────

    @staticmethod
    def _plan_schema() -> Dict[str, Any]:
        """Loose envelope schema; strict validation happens in Pydantic."""
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "definition": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {"type": "object"},
                            "minItems": 1,
                            "maxItems": 100,
                        },
                        "materialize": {"type": ["object", "null"]},
                    },
                    "required": ["steps"],
                },
            },
            "required": ["name", "definition"],
        }

    @staticmethod
    def _parse_response(response: Any) -> Dict[str, Any]:
        if response is None:
            raise PipelineValidationError("The planner returned no response.")
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            text = response.strip()
            fence = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
            if fence:
                text = fence.group(1)
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PipelineValidationError(
                    "The planner response was not valid JSON."
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise PipelineValidationError(
            "The planner response was not a JSON object."
        )

    @staticmethod
    def _extract_definition(parsed: Dict[str, Any]) -> Dict[str, Any]:
        definition = parsed.get("definition")
        if isinstance(definition, dict):
            return definition
        # Tolerate a model that returns the definition at the top level.
        if "steps" in parsed:
            return parsed
        raise PipelineValidationError(
            "The planner response is missing a 'definition' object."
        )

    @staticmethod
    def _clean_name(raw: Any, query: str) -> str:
        name = str(raw or "").strip()
        if not name:
            name = (query or "etl_pipeline").strip()[:120]
        return name[:200] or "etl_pipeline"

    @staticmethod
    def _clean_description(raw: Any) -> Optional[str]:
        if raw is None:
            return None
        description = str(raw).strip()
        return description[:2000] or None
