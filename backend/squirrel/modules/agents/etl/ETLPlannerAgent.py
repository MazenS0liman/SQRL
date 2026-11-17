#!/usr/bin/python
"""
ETL Planner Agent
=================

Turns a natural-language request into a validated
:class:`~squirrel.schemas.etl.PipelineDefinition`.

The agent runs a **plan -> compile -> repair** loop:

1. Ask the LLM for a definition (grounded with real source schemas).
2. Parse it into strict Pydantic models and validate the DAG.
3. Resolve each source's real columns and fully compile the pipeline, so
   unknown columns/tables fail here instead of at run time.
4. If any step fails, feed the exact error (plus the previous attempt) back to
   the LLM and retry, up to ``max_attempts``.

The agent never executes anything and never writes SQL; LLM output only ever
becomes a stored definition after the compiler accepts it.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger

from squirrel.modules.agents.abstract.IAgent import IAgent
from squirrel.modules.prompts.etl.ETLPlannerPromptGenerator import (
    ETLPlannerPromptGenerator,
    ETLPlannerPromptType,
)
from squirrel.modules.providers.abstract.IProvider import Provider
from squirrel.constants.etl import (
    AggregateFn,
    CastType,
    FilterOp,
    JoinHow,
    SinkKind,
    SinkMode,
    SourceKind,
    StepType,
    TransformOp,
)
from squirrel.schemas.etl import PipelineDefinition, SourceSpec
from squirrel.services.pipelines.compiler import (
    PipelineCompiler,
    PipelineValidationError,
)

try:  # pydantic v2
    from pydantic import ValidationError
except ImportError:  # pragma: no cover
    from pydantic.error_wrappers import ValidationError  # type: ignore

# Resolves the columns a single source spec exposes, or raises.
SourceResolver = Callable[[SourceSpec], Sequence[str]]

DEFAULT_MAX_ATTEMPTS = 3


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
        source_resolver: Optional[SourceResolver] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        previous_definition: Optional[PipelineDefinition] = None,
        history: Optional[List[Dict[str, str]]] = None,
        output_targets: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Plan a pipeline, repairing it against compiler feedback.

        :param source_resolver: ``SourceSpec -> columns``. When supplied, the
            definition is fully compiled (schema-checked) on every attempt.
        :param previous_definition: Existing definition to modify (refinement).
        :param history: Earlier chat turns, oldest first, for follow-up requests.
        :raises PipelineValidationError: If no attempt produced a valid
            pipeline. The message is the *last* validation error.
        :return: ``{name, description, definition, validation}``.
        """
        max_attempts = max(1, int(max_attempts))
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
        )
        schema_cache: Dict[str, List[str]] = {}

        draft: Optional[Dict[str, Any]] = (
            previous_definition.model_dump(mode="json") if previous_definition else None
        )
        feedback: Optional[str] = None
        last_error: Optional[PipelineValidationError] = None

        for attempt in range(1, max_attempts + 1):
            user_prompt = self.prompt_generator.generate_user_prompt(
                prompt_type=ETLPlannerPromptType.PLAN_PIPELINE,
                query=query,
                connector_id=connector_id,
                table=table,
                file_urls=file_urls or [],
                sink_table=sink_table,
                notebook_id=notebook_id,
                context=context or {},
                previous_definition=draft,
                validation_error=feedback,
                history=history or [],
                output_targets=output_targets or [],
            )

            try:
                result = self._attempt(
                    system_prompt, user_prompt, query, source_resolver, schema_cache,
                    output_targets or [],
                )
            except PipelineValidationError as exc:
                last_error = exc
                feedback = str(exc)
                logger.info(
                    "ETLPlannerAgent: attempt {}/{} rejected: {}", attempt, max_attempts, exc
                )
                draft = getattr(exc, "raw_definition", None) or draft
                continue

            result["validation"]["attempts"] = attempt
            logger.info(
                "ETLPlannerAgent: planned '{}' with {} step(s) in {} attempt(s)",
                result["name"], len(result["definition"].steps), attempt,
            )
            return result

        assert last_error is not None
        raise PipelineValidationError(
            f"Could not produce a valid pipeline after {max_attempts} attempt(s). "
            f"Last error: {last_error}"
        ) from last_error

    def plan_stage(
        self,
        description: str,
        *,
        definition: PipelineDefinition,
        insert_after: str,
        insert_before: str,
        source_resolver: Optional[SourceResolver] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Dict[str, Any]:
        """
        Insert a natural-language stage between two existing DAG steps.

        The LLM returns a complete definition, which is checked so that:

        - every existing step id survives and source/sink payloads are unchanged;
        - the new transform chain starts at ``insert_after`` and feeds
          ``insert_before``;
        - the whole definition still compiles against real source columns.

        :return: ``{definition, steps, reply, validation}``.
        """
        max_attempts = max(1, int(max_attempts))
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ETLPlannerPromptType.PLAN_STAGE,
        )
        schema_cache: Dict[str, List[str]] = {}
        draft: Dict[str, Any] = definition.model_dump(mode="json")
        feedback: Optional[str] = None
        last_error: Optional[PipelineValidationError] = None

        for attempt in range(1, max_attempts + 1):
            context = self._stage_context(
                definition, insert_after, source_resolver, schema_cache
            )
            user_prompt = self.prompt_generator.generate_user_prompt(
                prompt_type=ETLPlannerPromptType.PLAN_STAGE,
                description=description,
                definition=draft,
                insert_after=insert_after,
                insert_before=insert_before,
                context=context,
                validation_error=feedback,
            )

            try:
                result = self._attempt_stage(
                    system_prompt,
                    user_prompt,
                    description,
                    definition,
                    insert_after,
                    insert_before,
                    source_resolver,
                    schema_cache,
                )
            except PipelineValidationError as exc:
                last_error = exc
                feedback = str(exc)
                logger.info(
                    "ETLPlannerAgent: stage attempt {}/{} rejected: {}",
                    attempt, max_attempts, exc,
                )
                draft = getattr(exc, "raw_definition", None) or draft
                continue

            result["validation"]["attempts"] = attempt
            logger.info(
                "ETLPlannerAgent: inserted {} stage step(s) between '{}' and '{}'",
                len(result["steps"]), insert_after, insert_before,
            )
            return result

        assert last_error is not None
        raise PipelineValidationError(
            f"Could not plan the stage after {max_attempts} attempt(s). "
            f"Last error: {last_error}"
        ) from last_error

    # ── One stage attempt ─────────────────────────────────────────────────

    def _attempt_stage(
        self,
        system_prompt: str,
        user_prompt: str,
        description: str,
        original: PipelineDefinition,
        insert_after: str,
        insert_before: str,
        source_resolver: Optional[SourceResolver],
        schema_cache: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._stage_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                "models/gemini-2.5-flash",
            ],
        )

        parsed = self._parse_response(response)
        raw_definition = self._extract_definition(parsed)

        try:
            updated = PipelineDefinition.model_validate(raw_definition)
        except ValidationError as exc:
            raise self._with_raw(
                PipelineValidationError(
                    f"The updated pipeline does not match the ETL schema: {self._short(exc)}"
                ),
                raw_definition,
            ) from exc

        try:
            self._validate_stage_insertion(
                original, updated, insert_after, insert_before
            )
            self._compiler.validate_structure(updated)
            schema_checked = False
            if source_resolver is not None:
                source_schemas = self._resolve_schemas(
                    updated, source_resolver, schema_cache
                )
                self._compiler.compile(updated, source_schemas)
                schema_checked = True
        except PipelineValidationError as exc:
            raise self._with_raw(exc, raw_definition)
        except Exception as exc:  # noqa: BLE001
            raise self._with_raw(
                PipelineValidationError(f"The updated pipeline is invalid: {exc}"),
                raw_definition,
            ) from exc

        original_ids = {step.id for step in original.steps}
        new_steps = [step for step in updated.steps if step.id not in original_ids]
        reply = str(parsed.get("reply") or "").strip() or (
            f"Added {len(new_steps)} step(s) between `{insert_after}` and "
            f"`{insert_before}`."
        )

        return {
            "definition": updated,
            "steps": new_steps,
            "reply": reply[:2000],
            "validation": {
                "valid": True,
                "inserted": [step.id for step in new_steps],
                "insert_after": insert_after,
                "insert_before": insert_before,
                "schema_checked": schema_checked,
                "description": description[:500],
            },
        }

    @staticmethod
    def _validate_stage_insertion(
        original: PipelineDefinition,
        updated: PipelineDefinition,
        insert_after: str,
        insert_before: str,
    ) -> None:
        """Reject changes that are not a clean insertion between two steps."""
        original_by_id = {step.id: step for step in original.steps}
        updated_by_id = {step.id: step for step in updated.steps}

        missing = sorted(set(original_by_id) - set(updated_by_id))
        if missing:
            raise PipelineValidationError(
                f"The stage planner removed step(s) {missing}. Existing steps must be kept."
            )

        for step_id, step in original_by_id.items():
            updated_step = updated_by_id.get(step_id)
            if updated_step is None:
                continue

            # The payload of a source/sink is immutable. Its ``input`` may change
            # only on ``insert_before``, which is exactly how the new chain gets
            # wired in front of it.
            if step.type == StepType.SOURCE:
                changed = (
                    updated_step.type != StepType.SOURCE
                    or updated_step.source != step.source
                    or updated_step.input is not None
                )
            elif step.type == StepType.SINK:
                changed = (
                    updated_step.type != StepType.SINK
                    or updated_step.sink != step.sink
                    or updated_step.transform is not None
                )
            else:
                changed = False

            if changed:
                raise PipelineValidationError(
                    f"The stage planner changed {step.type} step '{step_id}'. "
                    "Source and sink settings must stay exactly as they are."
                )

            # Only the downstream end of the insertion edge may be rewired.
            if step_id != insert_before:
                original_right = step.transform.right if step.transform else None
                updated_right = (
                    updated_step.transform.right if updated_step.transform else None
                )
                if (
                    updated_step.input != step.input
                    or updated_right != original_right
                ):
                    raise PipelineValidationError(
                        f"The stage planner rewired step '{step_id}'. Only "
                        f"'{insert_before}' may start reading the new stage."
                    )

        added = [step for step in updated.steps if step.id not in original_by_id]
        non_transform = [step.id for step in added if step.type != StepType.TRANSFORM]
        if non_transform:
            raise PipelineValidationError(
                f"A stage may only add transform steps, but got {non_transform}."
            )
        if not added:
            raise PipelineValidationError(
                "The planner returned the definition without inserting a new stage."
            )

        if insert_after not in updated_by_id:
            raise PipelineValidationError(
                f"Insertion point '{insert_after}' does not exist."
            )
        if insert_before not in updated_by_id:
            raise PipelineValidationError(
                f"Downstream step '{insert_before}' does not exist."
            )

        before = updated_by_id[insert_before]
        original_before = original_by_id[insert_before]

        if original_before.input == insert_after:
            entry = before.input
            if entry == insert_after:
                raise PipelineValidationError(
                    f"Step '{insert_before}' still reads '{insert_after}'; the new "
                    "stage is not connected."
                )
        elif (
            original_before.transform
            and original_before.transform.op == TransformOp.JOIN
            and original_before.transform.right == insert_after
        ):
            entry = before.transform.right if before.transform else None
            if entry == insert_after:
                raise PipelineValidationError(
                    f"Join step '{insert_before}' still reads '{insert_after}' on its "
                    "right branch; the new stage is not connected."
                )
        else:
            raise PipelineValidationError(
                f"Step '{insert_before}' does not currently read from '{insert_after}'."
            )

        if not entry:
            raise PipelineValidationError(
                f"Step '{insert_before}' does not read from the inserted stage."
            )

        added_ids = {step.id for step in added}
        seen: set[str] = set()
        current: Optional[str] = entry
        while current and current != insert_after:
            if current in seen:
                raise PipelineValidationError(
                    "The inserted stage contains a cycle."
                )
            seen.add(current)
            if current not in added_ids:
                raise PipelineValidationError(
                    f"Step '{insert_before}' reads '{current}', which is not a newly "
                    f"inserted step chained from '{insert_after}'."
                )
            current = updated_by_id[current].input

        if current != insert_after:
            raise PipelineValidationError(
                f"The inserted stage is not chained from '{insert_after}'."
            )

    def _stage_context(
        self,
        definition: PipelineDefinition,
        insert_after: str,
        source_resolver: Optional[SourceResolver],
        cache: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        """Columns per step plus the input schema, for grounding the stage LLM."""
        schemas: Dict[str, List[str]] = {}
        if source_resolver is not None:
            try:
                source_schemas = self._resolve_schemas(definition, source_resolver, cache)
                schemas = self._compiler.schema_map(definition, source_schemas)
            except PipelineValidationError:
                # A half-built editor DAG is still plannable: keep whatever
                # source schemas resolved and let the final compile be strict.
                schemas = {}
            except Exception as exc:  # noqa: BLE001 - infrastructure errors surface later
                logger.warning("ETLPlannerAgent: could not resolve stage schemas: {}", exc)

        return {
            "input_step": insert_after,
            "input_columns": schemas.get(insert_after, []),
            "step_schemas": {
                step.id: {
                    "type": step.type,
                    "input": step.input,
                    "op": step.transform.op if step.transform else None,
                    "columns": schemas.get(step.id, []),
                }
                for step in definition.steps
            },
        }

    # ── One attempt ───────────────────────────────────────────────────────

    def _attempt(
        self,
        system_prompt: str,
        user_prompt: str,
        query: str,
        source_resolver: Optional[SourceResolver],
        schema_cache: Dict[str, List[str]],
        output_targets: List[str],
    ) -> Dict[str, Any]:
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
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
            ],
        )

        parsed = self._parse_response(response)
        raw_definition = self._extract_definition(parsed)

        try:
            definition = PipelineDefinition.model_validate(raw_definition)
        except ValidationError as exc:
            raise self._with_raw(
                PipelineValidationError(
                    f"The pipeline does not match the ETL schema: {self._short(exc)}"
                ),
                raw_definition,
            ) from exc
        definition = self._reconcile_outputs(definition, output_targets, raw_definition)

        schema_checked = False
        try:
            self._compiler.validate_structure(definition)
            if source_resolver is not None:
                source_schemas = self._resolve_schemas(definition, source_resolver, schema_cache)
                self._compiler.compile(definition, source_schemas)
                schema_checked = True
        except PipelineValidationError as exc:
            raise self._with_raw(exc, raw_definition)
        except Exception as exc:  # noqa: BLE001
            raise self._with_raw(
                PipelineValidationError(f"The pipeline is invalid: {exc}"), raw_definition
            ) from exc

        sink_step = next(step for step in definition.steps if step.type == "sink")
        assert sink_step.sink is not None
        return {
            "name": self._clean_name(parsed.get("name"), query),
            "description": self._clean_description(parsed.get("description")),
            "definition": definition,
            "validation": {
                "valid": True,
                "steps": [{"id": s.id, "type": s.type} for s in definition.steps],
                "sink": sink_step.sink.model_dump(),
                "schema_checked": schema_checked,
            },
        }

    def _resolve_schemas(
        self,
        definition: PipelineDefinition,
        resolver: SourceResolver,
        cache: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        """Resolve columns per source step; cached across attempts."""
        schemas: Dict[str, List[str]] = {}
        for step in definition.steps:
            if step.type != "source" or step.source is None:
                continue
            key = json.dumps(step.source.model_dump(mode="json"), sort_keys=True, default=str)
            if key not in cache:
                try:
                    cache[key] = list(resolver(step.source))
                except PipelineValidationError:
                    raise
                except ValueError as exc:  # e.g. connector: unknown table/column
                    raise PipelineValidationError(
                        f"Source step '{step.id}' could not be resolved: {exc}"
                    ) from exc
                # Infrastructure failures (network, auth) propagate: retrying the
                # LLM cannot fix them.
            schemas[step.id] = cache[key]
        return schemas

    # ── Response parsing ──────────────────────────────────────────────────

    @staticmethod
    def _plan_schema() -> Dict[str, Any]:
        """
        Envelope only. The definition travels as a JSON string.

        Gemini only honours object fields declared under ``properties``; the
        step/transform shapes include free-form maps, so a nested object schema
        comes back as ``{}``. A string field is unconstrained, and Pydantic
        validates the parsed definition strictly afterwards.
        """
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "definition_json": {"type": "string"},
            },
            "required": ["name", "definition_json"],
        }

    @staticmethod
    def _stage_schema() -> Dict[str, Any]:
        """Envelope for a stage insertion; the definition travels as JSON text."""
        return {
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "definition_json": {"type": "string"},
            },
            "required": ["definition_json"],
        }

    @staticmethod
    def _extract_definition(parsed: Dict[str, Any]) -> Dict[str, Any]:
        raw = parsed.get("definition_json")
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
            if fence:
                text = fence.group(1)
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PipelineValidationError(
                    "`definition_json` was not valid JSON."
                ) from exc
            if isinstance(loaded, dict):
                return loaded
        definition = parsed.get("definition")  # tolerate models that ignore the schema
        if isinstance(definition, dict):
            return definition
        if "steps" in parsed:
            return parsed
        raise PipelineValidationError(
            "The planner response is missing a 'definition' object."
        )

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
        raise PipelineValidationError("The planner response was not a JSON object.")

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _with_raw(exc: PipelineValidationError, raw: Dict[str, Any]) -> PipelineValidationError:
        """Attach the rejected definition so the next attempt can repair it."""
        exc.raw_definition = raw  # type: ignore[attr-defined]
        return exc

    @staticmethod
    def _reconcile_outputs(
        definition: PipelineDefinition,
        targets: List[str],
        raw: Dict[str, Any],
    ) -> PipelineDefinition:
        """Make ``definition.outputs`` match what the user asked for."""
        from squirrel.schemas.etl import OutputsSpec

        wants_dashboard = "dashboard" in targets
        wants_ml = "ml" in targets
        outputs = definition.outputs or OutputsSpec()

        if wants_ml and outputs.ml is None:
            exc = PipelineValidationError(
                "A model was requested, so `definition.outputs.ml` is required "
                "(task, and a target column unless task is clustering)."
            )
            exc.raw_definition = raw  # type: ignore[attr-defined]
            raise exc

        outputs.dashboard = wants_dashboard
        if not wants_ml:
            outputs.ml = None
        definition.outputs = outputs if (outputs.dashboard or outputs.ml) else None
        return definition

    @staticmethod
    def _short(exc: Exception, limit: int = 1500) -> str:
        text = str(exc)
        return text if len(text) <= limit else text[:limit] + " ..."

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