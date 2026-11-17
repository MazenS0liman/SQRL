#!/usr/bin/python
"""
ETL Planner Prompt Generator
============================

Prompts for :class:`~squirrel.modules.agents.etl.ETLPlannerAgent`.

The planner turns a natural-language request into a **constrained** pipeline
definition. The prompt therefore documents the DSL exhaustively: the model may
only emit the step types, transform ops, and destination rules listed here.
Anything else is rejected by
:class:`~squirrel.services.pipelines.compiler.PipelineCompiler` before it is
stored or executed.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
from enum import Enum
from typing import Any, Dict, List, Optional
from typing_extensions import override

# Abstract Classes
from squirrel.modules.prompts.abstract.IPromptGenerator import IPromptGenerator


# ——————————————————————————————————————————————————————————————
# Prompt type enum


class ETLPlannerPromptType(Enum):
    """The tasks this prompt generator supports."""

    PLAN_PIPELINE = "plan_pipeline"
    PLAN_STAGE = "plan_stage"


# ——————————————————————————————————————————————————————————————
# Prompt generator


class ETLPlannerPromptGenerator(IPromptGenerator):
    """Generate system/user prompts for the ETL planner agent."""

    # ── System prompt ─────────────────────────────────────────────────────

    @override
    def generate_system_prompt(
        self,
        prompt_type: Optional[ETLPlannerPromptType] = None,
    ) -> str:
        """Dispatch to the prompt for the requested planning task."""
        if prompt_type == ETLPlannerPromptType.PLAN_STAGE:
            return self._stage_system_prompt()
        return self._pipeline_system_prompt()

    def _pipeline_system_prompt(self) -> str:
        """
        Describe the DSL, its guardrails, and the required JSON envelope.

        The prompt is deliberately explicit about every allowed value because
        the downstream validator rejects unknown keys and unknown enum values.
        """
        return (
            "You are an ETL pipeline planner for a tabular-data platform. "
            "Translate the user's request into a single, declarative pipeline "
            "definition. You never write SQL, Python, shell commands, or prose "
            "outside the JSON object.\n\n"

            "# Output envelope\n"
            "Return exactly one JSON object with this shape:\n"
            "{\n"
            '  "name": "short_pipeline_name",\n'
            '  "description": "one or two sentences",\n'
            '  "definition_json": "<the pipeline definition object, serialised as a JSON string>"\n'
            "}\n"
            'The definition object inside `definition_json` has the form '
            '{ "steps": [ ... ], "materialize": {...}?, "outputs": {...}? }.\n'
            "`name` must be 1-200 characters. `materialize` is optional.\n\n"

            "# Pipeline definition\n"
            "`definition.steps` is a DAG with 1-100 steps. Every step has:\n"
            '  "id": lower_snake_case identifier (a-z, 0-9, _; starts with a letter)\n'
            '  "type": "source" | "transform" | "sink"\n'
            '  "input": id of the preceding step (required for transform/sink)\n'
            "Exactly one step must be a `sink`. Source steps have no `input`.\n\n"

            "# Source steps\n"
            'Source step: {"id": "...", "type": "source", "source": {...}}\n'
            "`source.kind` is one of:\n"
            '  - "connector": requires "connector_id" and "table". Optionally '
            '"columns" (list of column names to read).\n'
            '  - "file": requires "file_url" (an s3:// URL supplied by the user).\n'
            '  - "table": requires "table_name" (an existing app-database table).\n'
            'Optionally include "columns": [] to read only some columns. Use [] or '
            "omit the key for all columns. Never invent connector ids, file URLs, "
            "or table names: use only values supplied in the user message.\n\n"

            "# Transform steps\n"
            'Transform step: {"id": "...", "type": "transform", "input": "...", '
            '"transform": {"op": "..."}}\n'
            "`op` and its required fields:\n"
            '  - "select":    "columns": [list of column names]\n'
            '  - "filter":    "column": name, "comparison": one of '
            '"eq","ne","gt","gte","lt","lte","in","not_in","is_null","not_null",'
            '"like","contains"; plus "value" for scalar comparisons, "values" '
            "[list] for in/not_in, nothing for is_null/not_null\n"
            '  - "fill_null": "fill_values": {"column": replacement_value, ...}; '
            "replaces NULL values while preserving all columns\n"
            '  - "rename":    "mapping": {"old": "new", ...}\n'
            '  - "cast":      "casts": {"column": "type", ...}; type is one of '
            '"text","integer","bigint","numeric","double precision","boolean",'
            '"date","timestamptz"\n'
            '  - "dedupe":    optional "columns": [subset to deduplicate on]; omit '
            "for full-row distinct\n"
            '  - "limit":     "count": positive integer\n'
            '  - "aggregate": "group_by": [columns], "aggregations": '
            '[{"function": "count"|"sum"|"avg"|"min"|"max", "column": name or null '
            'for count(*), "alias": output_name}]\n'
            '  - "join":      "right": step id, "on": [{"left": col, "right": col}], '
            '"how": "inner"|"left"|"right"|"full"\n'
            "For joins, the right step must also be a source or transform in the "
            "same definition. Avoid duplicate output column names after a join by "
            "selecting or renaming columns first.\n\n"

            "# Sink steps\n"
            'Sink step: {"id": "...", "type": "sink", "input": "...", "sink": {...}}\n'
            '  - Postgres: {"kind": "postgres", "table": "etl_<name>", '
            '"mode": "replace"|"append"}. The table must start with "etl_" unless '
            "the user explicitly supplies another allowed destination. Prefer "
            '"replace" unless the user asks to append.\n'
            '  - MinIO CSV: {"kind": "minio", "object_key": "etl/<name>.csv", '
            '"format": "csv"}. The object key must start with "etl/".\n\n'

            "# Decomposition\n"
            "Translate every distinct operation in the request into its own step. "
            "A typical pipeline is source -> filter -> transform -> aggregate -> sink. "
            'For example, "keep completed orders and sum revenue by day" needs a '
            "filter step and a separate aggregate step, not one combined step. "
            "Chain steps with `input` in the order the user described, and use "
            "separate source steps when joining. Do not drop a requested operation "
            "and do not add operations the user did not ask for.\n\n"

            "# Materialisation (optional)\n"
            "If a notebook id is supplied and the sink is Postgres, include:\n"
            '  "materialize": {"notebook_id": "...", "create_dashboard_cell": true}\n'
            "This exposes the output table as a notebook dashboard after a "
            "successful run.\n\n"

            "# Outputs (optional)\n"
            "Only when the user message lists `requested_outputs`, add to the definition:\n"
            '  "outputs": {"dashboard": true, "ml": {...}}\n'
            '  - "dashboard": true when "dashboard" is requested. Omit otherwise.\n'
            '  - "ml": only when "ml" is requested. Shape: {"task": '
            '"classification"|"regression"|"clustering", "target": column, '
            '"features": [columns], "algorithm": "auto", "n_clusters": 3}.\n'
            "    * classification/regression REQUIRE a `target`; clustering must not have one.\n"
            "    * `target` and `features` must be columns the pipeline's FINAL step produces "
            "(after renames, aggregates and joins). `features` may be [] for all other columns.\n"
            "    * Pick the task from the request (predict a category -> classification, "
            "a number -> regression, group/segment -> clustering).\n"
            '- Outputs require a Postgres sink (table starting with "etl_"), never MinIO.\n'
            '- Never add `outputs` when `requested_outputs` is absent.\n\n'

            "# Rules\n"
            "- Use only the user's supplied source identifiers and file URLs.\n"
            "- Keep transformations within the listed ops; never emit SQL or code.\n"
            "- Every non-source step must reference an existing step id via `input`.\n"
            "- Do not create cycles.\n"
            "- Do not include comments, markdown fences, or extra keys.\n"
            "- Respond only with the JSON object.\n"
            "- Column names MUST come from available_sources in the extra context. "
            "Never invent columns.\n"
            '- If "unresolved_sources" is present, do not use those sources.\n'
            "- If a validation error is provided, return a corrected full definition."
        )

    def _stage_system_prompt(self) -> str:
        """Prompt for inserting one or more transform stages between two steps."""
        return (
            "You are an ETL stage planner for a tabular-data platform. The user "
            "describes one logical stage to insert between two existing steps of "
            "a pipeline DAG. Convert that description into one or more constrained "
            "transform steps. You never write SQL, Python, shell commands, or prose "
            "outside the JSON object.\n\n"

            "# Output envelope\n"
            "Return exactly one JSON object with this shape:\n"
            "{\n"
            '  "reply": "one or two sentences describing what the stage does",\n'
            '  "definition_json": "<the COMPLETE updated pipeline definition, serialised as a JSON string>"\n'
            "}\n\n"

            "# Insertion rules\n"
            "- Return the complete definition, not just the new steps.\n"
            "- Keep every existing step id. Never delete a step.\n"
            "- Source and sink steps must remain exactly as supplied (same id and payload).\n"
            "- Only add `transform` steps. Never add a source or sink.\n"
            "- The first new step's `input` MUST be the supplied `insert_after` id.\n"
            "- Each further new step chains from the previous new step via `input`.\n"
            "- The supplied `insert_before` step must consume the LAST new step: set its "
            "`input` to that step id (or, when it is a join whose `right` currently "
            "equals `insert_after`, set `transform.right` instead).\n"
            "- Do not reorder or rewrite unrelated steps. Only adjust downstream steps "
            "when the new stage changes the columns they reference.\n\n"

            "# Transform vocabulary\n"
            "Every transform step has the shape "
            '{"id": "...", "type": "transform", "input": "...", "transform": {"op": "..."}}.\n'
            "`op` and its required fields:\n"
            '  - "select":    "columns": [list of column names]\n'
            '  - "filter":    "column": name, "comparison": one of '
            '"eq","ne","gt","gte","lt","lte","in","not_in","is_null","not_null",'
            '"like","contains"; plus "value" for scalar comparisons, "values" '
            "[list] for in/not_in, nothing for is_null/not_null\n"
            '  - "fill_null": "fill_values": {"column": replacement_value, ...}\n'
            '  - "rename":    "mapping": {"old": "new", ...}\n'
            '  - "cast":      "casts": {"column": "type", ...}; type is one of '
            '"text","integer","bigint","numeric","double precision","boolean",'
            '"date","timestamptz"\n'
            '  - "dedupe":    optional "columns": [subset to deduplicate on]; omit '
            "for full-row distinct\n"
            '  - "limit":     "count": positive integer\n'
            '  - "aggregate": "group_by": [columns], "aggregations": '
            '[{"function": "count"|"sum"|"avg"|"min"|"max", "column": name or null '
            'for count(*), "alias": output_name}]\n'
            '  - "join":      "right": step id, "on": [{"left": col, "right": col}], '
            '"how": "inner"|"left"|"right"|"full". Use this when the description '
            "asks to join another step's output; `right` may reference any step in "
            "the definition, including a source that is not yet wired in.\n\n"

            "# Rules\n"
            "- Use only column names listed in `step_schemas` / `input_columns`.\n"
            "- Never invent columns, step ids, connector ids, file URLs, or table names.\n"
            "- Do not create cycles.\n"
            "- Do not include comments, markdown fences, or extra keys.\n"
            "- If a validation error is provided, return a corrected full definition.\n"
            "- Respond only with the JSON object."
        )

    # ── User prompt ───────────────────────────────────────────────────────

    @override
    def generate_user_prompt(
        self,
        prompt_type: Optional[ETLPlannerPromptType] = None,
        **kwargs: Any,
    ) -> str:
        if prompt_type == ETLPlannerPromptType.PLAN_STAGE:
            return self.plan_stage_prompt(kwargs)
        if prompt_type != ETLPlannerPromptType.PLAN_PIPELINE:
            raise ValueError("Invalid prompt type specified.")
        return self.plan_pipeline_prompt(kwargs)

    def plan_pipeline_prompt(self, input: Dict[str, Any]) -> str:
        """Render the user request plus every known source/destination hint."""
        query: str = input.get("query", "") or ""
        connector_id: Optional[str] = input.get("connector_id")
        table: Optional[str] = input.get("table")
        file_urls: List[str] = input.get("file_urls", []) or []
        sink_table: Optional[str] = input.get("sink_table")
        notebook_id: Optional[str] = input.get("notebook_id")
        output_targets = input.get("output_targets", []) or []
        context: Dict[str, Any] = input.get("context", {}) or {}

        hints = {
            "connector_id": connector_id,
            "table": table,
            "file_urls": file_urls,
            "requested_sink_table": sink_table,
            "notebook_id": notebook_id,
            "requested_outputs": output_targets,
        }
        hints = {key: value for key, value in hints.items() if value}

        previous_definition = input.get("previous_definition")
        validation_error = input.get("validation_error")
        history = input.get("history", []) or []

        history_block = ""
        if history:
            turns = []
            for turn in history:
                if not isinstance(turn, dict):
                    continue
                content = str(turn.get("content", "")).strip()
                if content:
                    role = str(turn.get("role", "user")).upper()
                    turns.append(f"{role}: {content}")
            if turns:
                history_block = (
                    "\n# CONVERSATION SO FAR (oldest first):\n"
                    + "\n".join(turns)
                    + "\n"
                )

        repair = ""
        if validation_error:
            repair = f"""
                # PREVIOUS ATTEMPT WAS REJECTED:
                {validation_error}

                # PREVIOUS DEFINITION (fix only what the error describes):
                {json.dumps(previous_definition, default=str, indent=2) if previous_definition else "none"}
                """
        elif previous_definition:
            repair = f"""
                # CURRENT DEFINITION (modify it according to the request, keep the rest):
                {json.dumps(previous_definition, default=str, indent=2)}
                """

        prompt = f"""
        # USER REQUEST:
        {query or "(empty)"}
        {history_block}
        # KNOWN SOURCE / DESTINATION VALUES:
        {json.dumps(hints, default=str, indent=2) if hints else "none supplied"}

        # EXTRA CONTEXT:
        {json.dumps(context, default=str, indent=2) if context else "none"}

        # TASK:
        Produce the JSON envelope described in the system prompt. The USER
        REQUEST is the latest turn: apply it to the CONVERSATION SO FAR and keep
        earlier constraints unless the latest request changes them. Use only the
        supplied identifiers. If the request cannot be expressed with the
        allowed operations, return a definition whose sink step explains the
        limitation in its `table` or `object_key` name rather than inventing
        new operations.

        {repair}
        """
        return prompt

    def plan_stage_prompt(self, input: Dict[str, Any]) -> str:
        """Render the stage description, insertion point, and known schemas."""
        description: str = input.get("description", "") or ""
        definition = input.get("definition")
        insert_after: str = input.get("insert_after", "") or ""
        insert_before: str = input.get("insert_before", "") or ""
        context: Dict[str, Any] = input.get("context", {}) or {}
        validation_error: Optional[str] = input.get("validation_error")

        repair = ""
        if validation_error:
            repair = f"""
            # PREVIOUS ATTEMPT WAS REJECTED:
            {validation_error}

            # PREVIOUS DEFINITION (fix only what the error describes):
            {json.dumps(definition, default=str, indent=2) if definition else "none"}
            """

        prompt = f"""
        # STAGE DESCRIPTION:
        {description or "(empty)"}

        # INSERT POSITION:
        insert_after: {insert_after}
        insert_before: {insert_before}

        # CURRENT DEFINITION:
        {json.dumps(definition, default=str, indent=2) if definition else "none"}

        # STEP SCHEMAS (columns produced by each existing step):
        {json.dumps(context, default=str, indent=2) if context else "none"}

        # TASK:
        Produce the JSON envelope described in the system prompt. Insert the
        described stage between `insert_after` and `insert_before`, return the
        complete updated definition, and keep every other step intact.

        {repair}
        """
        return prompt
