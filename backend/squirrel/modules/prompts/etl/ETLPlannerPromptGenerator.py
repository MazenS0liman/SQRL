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
    """The single task this prompt generator supports."""

    PLAN_PIPELINE = "plan_pipeline"


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
            '  "definition": { "steps": [ ... ], "materialize": {...}? }\n'
            "}\n"
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

            "# Materialisation (optional)\n"
            "If a notebook id is supplied and the sink is Postgres, include:\n"
            '  "materialize": {"notebook_id": "...", "create_dashboard_cell": true}\n'
            "This exposes the output table as a notebook dashboard after a "
            "successful run.\n\n"

            "# Rules\n"
            "- Use only the user's supplied source identifiers and file URLs.\n"
            "- Keep transformations within the listed ops; never emit SQL or code.\n"
            "- Every non-source step must reference an existing step id via `input`.\n"
            "- Do not create cycles.\n"
            "- Do not include comments, markdown fences, or extra keys.\n"
            "- Respond only with the JSON object."
        )

    # ── User prompt ───────────────────────────────────────────────────────

    @override
    def generate_user_prompt(
        self,
        prompt_type: Optional[ETLPlannerPromptType] = None,
        **kwargs: Any,
    ) -> str:
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
        context: Dict[str, Any] = input.get("context", {}) or {}

        hints = {
            "connector_id": connector_id,
            "table": table,
            "file_urls": file_urls,
            "requested_sink_table": sink_table,
            "notebook_id": notebook_id,
        }
        hints = {key: value for key, value in hints.items() if value}

        prompt = f"""
        # USER REQUEST:
        {query or "(empty)"}

        # KNOWN SOURCE / DESTINATION VALUES:
        {json.dumps(hints, default=str, indent=2) if hints else "none supplied"}

        # EXTRA CONTEXT:
        {json.dumps(context, default=str, indent=2) if context else "none"}

        # TASK:
        Produce the JSON envelope described in the system prompt. Use only the
        supplied identifiers. If the request cannot be expressed with the
        allowed operations, return a definition whose sink step explains the
        limitation in its `table` or `object_key` name rather than inventing
        new operations.
        """
        return prompt
