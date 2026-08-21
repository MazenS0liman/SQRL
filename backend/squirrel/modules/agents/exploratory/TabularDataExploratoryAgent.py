#!/usr/bin/python
"""
Tabular Data Exploratory Agent
==============================

Overview
--------

Hypothesis-driven exploratory data analysis agent over a relational database.

The agent follows a structured analytical pipeline:

    1. **Schema reconciliation** — cross-check caller-supplied ``schema_metadata``
       against the live database's ``information_schema.columns`` and repair any
       drift *before* anything downstream ever sees it. This is the fix for the
       root cause of the "hallucinated column" failures: if the caller's metadata
       is missing/renaming a real column, no retry-with-error-message loop can
       recover, because the correct name was never presented to the LLM.
    2. **Schema inspection** — enrich the reconciled schema with column-level
       statistics (cardinality, nullability) fetched directly from the DB.
    3. **Domain inference** — infer business domain, main entities, fact/dimension
       tables, candidate KPIs, and analytical opportunities.
    4. **Entity & metric extraction** — resolve measurable metrics and grouping
       dimensions from the inferred domain context.
    5. **Hypothesis generation** — produce sophisticated, non-trivial analytical
       hypotheses (segmentation, cohort, anomaly, seasonality …).
    6. **SQL generation** — generate PostgreSQL for each hypothesis, with full
       chart axis metadata.
    7. **Query execution** — execute SQL through PostgresService, returning rows
       as DataFrames.
    8. **Observation generation** — LLM-generated plain-text insight per chart.
    9. **Executive summary** — synthesise all observations into a structured
       executive summary.

Data manipulation
------------------
This agent does not modify the underlying database tables. A request that
reads like a data-manipulation ask (e.g. "remove outliers from this data",
"give me a deduplicated list of customers") is still answered — it just goes
through the same question pipeline as any other question:
:meth:`classify_question_intent` decides whether the answer reads better as
a chart or as a table, and when a table fits, :meth:`process_view_request`
builds a named, read-only ``CREATE OR REPLACE VIEW`` that reshapes/filters/
aggregates the existing tables and hands back a preview. The user can
reference that view in later questions, but the original data source's table
itself is never altered in place.

Multi-source support
---------------------
``schema_metadata`` is a table-keyed dict — ``{table_name: {"columns": [...]}}``
— and this has always tolerated more than one entry. What was missing was any
awareness of *how* those tables relate: without it, the LLM had to guess join
keys from column names alone, with no deterministic signal to ground it.
:meth:`generate_table_relationships` now inspects every pair of tables for
shared column names and common ``<entity>_id``-style foreign-key naming, and
:meth:`generate_schema_summary` folds that into the schema text handed to
every downstream prompt (domain inference, hypothesis generation, and SQL
generation) — so multi-table requests get hypotheses and SQL that actually
JOIN across sources instead of only ever examining one table at a time. A
single-table ``schema_metadata`` is unaffected: the relationship section is
only appended when more than one table is present.

Robustness fixes
------------------
1. **New Stage 0 — ``reconcile_schema_with_database``.** Before enrichment,
   the agent queries ``information_schema.columns`` for every requested table
   and merges in any real column the caller's ``schema_metadata`` omitted (or
   named differently). This is what was silently missing before: a caller
   passing incomplete metadata (e.g. missing a ``close`` column) meant the
   LLM's ``AVAILABLE COLUMNS`` block never contained the ground truth, so
   every SQL-generation retry was still guessing blind.
2. **New Stage 0.5 — ``fix_column_types``.** Even with reconciliation, a
   CSV-derived column can be *present* under the right name but still typed
   ``TEXT`` in Postgres (``pandas.to_sql`` has no schema awareness), which is
   exactly what caused the repeated SQL-generation retries in production:
   the LLM had to guess a date format blind (``date_trunc`` on text, then a
   wrong-order cast that overflowed on "28-02-2024", before finally landing
   on the right ``TO_DATE`` format on the third attempt). This stage detects
   TEXT/VARCHAR columns whose sampled values represent a date or a number and
   repairs the column type in place with an ``ALTER TABLE``, before any LLM
   stage downstream ever sees the schema — so SQL generation gets a real
   ``DATE``/``DOUBLE PRECISION`` column instead of having to guess a cast.

   The type *decision* is made by an LLM call
   (:meth:`_infer_column_type_via_llm`): it shows the LLM a sample of real
   values and asks it to name the true Postgres type and (for dates) the
   exact ``TO_DATE``/``TO_TIMESTAMP`` format string, acting only on a "high
   confidence" verdict. A deterministic heuristic
   (:meth:`_infer_date_format` / :meth:`_is_numeric_column`) is kept as a
   fallback for when the LLM call fails outright or returns anything less
   than high confidence.
3. **Column-repair suggestions in the retry loop.** ``_find_hallucinated_columns``
   is unchanged in *detection*, but the error surfaced to the LLM on retry
   now includes a nearest-real-column suggestion (via ``difflib``) instead of
   just "this doesn't exist" — so retries converge instead of re-guessing the
   same wrong name.
4. **Fast-fail on non-progress.** If two consecutive SQL attempts hallucinate
   the *exact same* column(s), the retry loop gives up immediately instead of
   burning the remaining attempts.
5. **Non-repeating hypotheses.** ``generate_hypotheses`` now accepts an
   optional ``previous_questions`` list. Callers (e.g. re-running EDA on a
   notebook that already has cells against the same source set) pass in every
   question already answered, and the prompt explicitly instructs the LLM not
   to repeat or closely paraphrase them — so a second "run full EDA" on the
   same data doesn't regenerate near-duplicate charts.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import re
import json
import difflib
import textwrap
from datetime import datetime as _dt
from typing import Any, Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Third-Party Libraries
import pandas as pd

# Abstract Base Class
from squirrel.modules.agents.abstract import IAgent
from squirrel.modules.providers.abstract.IProvider import Provider
from squirrel.services.storage.database.PostgresService import PostgresService

# Prompts
from squirrel.modules.prompts import (
    IPromptGenerator,
    ExplorePromptType,
    ExploratoryPromptGenerator
)

# Schema
from squirrel.schemas.eda import AxisSpec, SqlSpec, Hypothesis, ChartResult, DOMAIN_CONTEXT_SCHEMA, ENTITIES_AND_METRICS_SCHEMA, HYPOTHESES_SCHEMA, SQL_SPEC_SCHEMA, SUMMARY_SCHEMA

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Tabular Data Exploratory Agent class

class TabularDataExploratoryAgent(IAgent):
    """
    Hypothesis-Driven Exploratory Data Analysis Agent
    --------------------------------------------------

    **Description:**

        Orchestrates a full EDA pipeline over a relational database schema.
        ``schema_metadata`` may describe one table or several — when several
        are given, the agent deterministically detects candidate join keys
        between them (see :meth:`generate_table_relationships`) and grounds
        every LLM stage in that information so hypotheses/SQL can span
        multiple sources. The schema is also reconciled against the live
        database (see :meth:`reconcile_schema_with_database`) and has its
        column types repaired (see :meth:`fix_column_types`) before any
        enrichment or prompting happens, so downstream stages never operate
        on stale, incomplete, or mistyped caller-supplied metadata.

    **Attributes:**

        - **postgres_service (PostgresService):** Live database connection used
          for schema introspection and query execution.
        - **max_hypotheses (int):** Cap on generated hypotheses (default: 10).
        - **max_sql_retries (int):** Times to ask the LLM to fix broken SQL
          before skipping a hypothesis (default: 2).
        - **row_sample_for_observation (int):** Rows sent to the LLM for
          observation generation (default: 100).

    **Pipeline:**

        ``explore()`` → ``_reconcile_schema()`` → ``fix_column_types()``
        → ``_inspect_schema()`` → ``_infer_domain()``
        → ``_extract_entities_and_metrics()`` → ``_generate_hypotheses()``
        → per-hypothesis: ``_generate_sql()`` → ``_execute_sql()``
        → ``_generate_observation()`` → ``_generate_summary()``

    **Example:**

    .. code-block:: python

        agent = TabularDataExploratoryAgent(
            provider=Provider.GROQ,
            postgres_service=PostgresService(),
        )

        # Single source
        result = agent.explore({"orders": {"columns": [...]}})

        # Multiple sources — relationships between them are inferred
        # automatically from shared / foreign-key-style column names.
        result = agent.explore({
            "orders": {"columns": [...]},
            "customers": {"columns": [...]},
        })
        print(result["summary"]["key_findings"])

    """

    # ── Analytical categories the LLM must choose from ──────────────────────
    _ANALYTICAL_TYPES = [
        "segmentation",
        "concentration",
        "seasonality",
        "outlier_detection",
        "anomaly_detection",
        "cohort_behavior",
        "change_point_detection",
        "growth_drivers",
        "retention",
        "funnel_analysis",
        "behavioral_clusters",
        "pareto_effect",
        "variance_decomposition",
        "correlation",
    ]

    # ── Plot types the LLM must choose from ─────────────────────────────────
    _PLOT_TYPES = ["line", "bar", "scatter", "boxplot", "heatmap", "pie", "radar", "bullet", "bump", "calendar", "chord", "circle-packing", "funnel", "geo", "marimekko", "network", "parallel-coordinates", "radial-bar", "sankey", "stream", "sunburst", "swarmplot", "treemap", "voronoi", "waffle"]

    # ── Column names ignored as join-key candidates (too generic / noisy) ────
    _JOIN_KEY_STOPWORDS = {
        "name", "type", "status", "description", "created_at", "updated_at",
        "created_date", "updated_date", "notes", "value", "amount", "label",
    }

    # ── SQL token allowlist (keywords / functions — not real column names) ───
    _SQL_KEYWORDS = {
        "select", "from", "where", "group", "by", "order", "limit", "having",
        "join", "left", "right", "inner", "outer", "on", "as", "and", "or",
        "not", "in", "is", "null", "true", "false", "case", "when", "then",
        "else", "end", "with", "cte", "over", "partition", "rows", "range",
        "between", "unbounded", "preceding", "following", "current", "row",
        "distinct", "all", "union", "intersect", "except",
        # aggregate / window functions
        "avg", "sum", "count", "min", "max", "stddev", "variance",
        "percentile_cont", "percentile_disc", "ntile", "lag", "lead",
        "rank", "dense_rank", "row_number", "first_value", "last_value",
        "nth_value", "cume_dist", "percent_rank",
        # type casts / conversions
        "cast", "coalesce", "nullif", "greatest", "least",
        "date_trunc", "date_part", "extract", "to_char", "to_date",
        "interval", "timestamp", "date", "time",
        # literals that regex may capture
        "x", "y", "asc", "desc",
    }

    # ── Date formats considered when repairing a mistyped TEXT column ────────
    # Each entry is (python strptime format, matching Postgres TO_DATE format).
    # This is now only the *fallback* path — see _infer_column_type_via_llm,
    # which is tried first. Only used when EVERY sampled value in a column
    # parses cleanly under one single candidate — ambiguous columns (e.g.
    # could be DD-MM or MM-DD) are deliberately left untouched rather than
    # risking a silently wrong cast.
    _DATE_FORMAT_CANDIDATES: List[tuple] = [
        ("%Y-%m-%d", "YYYY-MM-DD"),
        ("%Y/%m/%d", "YYYY/MM/DD"),
        ("%d-%m-%Y", "DD-MM-YYYY"),
        ("%m-%d-%Y", "MM-DD-YYYY"),
        ("%d/%m/%Y", "DD/MM/YYYY"),
        ("%m/%d/%Y", "MM/DD/YYYY"),
        ("%d-%b-%Y", "DD-Mon-YYYY"),
        ("%b %d, %Y", "Mon DD, YYYY"),
        ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH24:MI:SS"),
    ]

    # ── JSON schema for the LLM-driven column-type classification call ──────
    _COLUMN_TYPE_SCHEMA: Dict = {
        "type": "object",
        "properties": {
            "pg_type": {
                "type": "string",
                "enum": ["DATE", "TIMESTAMP", "DOUBLE PRECISION", "TEXT"],
            },
            "date_format": {
                "type": ["string", "null"],
                "description": (
                    "Postgres TO_DATE/TO_TIMESTAMP format string (e.g. "
                    "'YYYY-MM-DD', 'DD-Mon-YYYY'). Required when pg_type is "
                    "DATE or TIMESTAMP, null otherwise."
                ),
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reasoning": {"type": "string"},
        },
        "required": ["pg_type", "date_format", "confidence", "reasoning"],
    }

    _INTENT_SCHEMA: Dict = {
        "type": "object",
        "properties": {
            "needs_chart": {"type": "boolean"},
            "chart_type": {"type": ["string", "null"]},
            "reasoning": {"type": "string"},
        },
        "required": ["needs_chart", "chart_type", "reasoning"],
    }

    def __init__(
        self,
        postgres_service: PostgresService,
        prompt_generator: IPromptGenerator = ExploratoryPromptGenerator(),
        max_hypotheses: int = 6,
        max_sql_retries: int = 2,
        row_sample_for_observation: int = 100,
        user_id: Optional[str] = None,
    ) -> None:
        super().__init__(prompt_generator = prompt_generator, user_id=user_id)
        self.postgres_service = postgres_service
        self.max_hypotheses = max_hypotheses
        self.max_sql_retries = max_sql_retries
        self.row_sample_for_observation = row_sample_for_observation

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def explore(
        self, 
        schema_metadata: dict
    ) -> dict:
        """
        Run the full hypothesis-driven EDA pipeline.

        :param schema_metadata: Raw schema dict — ``{table: {columns: [...]}}``.
            May describe a single table or several; when more than one table
            is present, candidate relationships between them are detected
            deterministically (see :meth:`generate_table_relationships`) and
            surfaced to every LLM stage via :meth:`generate_schema_summary`,
            so hypotheses and SQL can join across sources.
        :type schema_metadata: dict

        :return: Result dict with keys ``dataset_context``, ``entities_and_metrics``,
            ``charts``, and ``summary``.
        :rtype: dict
        """
        logger.info(
            "EDA pipeline started for {} table(s): {}",
            len(schema_metadata), list(schema_metadata.keys()),
        )

        # Stage 0 — reconcile caller-supplied schema against the live DB.
        # This is the fix for the root cause of "hallucinated column" loops:
        # if a real column (e.g. the actual price column) was never present
        # in schema_metadata, no amount of retry-with-error-message can
        # recover it, because the LLM never saw the correct name.
        schema_metadata = self.reconcile_schema_with_database(schema_metadata)

        # Stage 0.5 — repair mistyped TEXT columns (dates/numbers stored as
        # text by a schema-naive CSV load) directly in Postgres, before any
        # LLM stage has to guess a cast for them.
        schema_metadata = self.fix_column_types(schema_metadata)

        # Stage 1 — enrich schema with DB-level statistics
        schema_details = self.generate_schema_details(schema_metadata, reconcile=False)

        # Stage 2 — infer business domain from enriched schema
        domain_context = self.generate_domain_context(schema_details)

        # Stage 3 — extract concrete entities and metrics
        entities_and_metrics = self.extract_entities_and_metrics(
            schema_details, domain_context
        )

        # Stage 4 — generate analytical hypotheses
        hypotheses = self.generate_hypotheses(
            schema_details, domain_context, entities_and_metrics
        )
        logger.info("Generated {} hypotheses.", len(hypotheses))

        # Stages 5–7 — SQL → execute → observe (per hypothesis)
        chart_results: List[Optional[ChartResult]] = [None] * len(hypotheses)
        with ThreadPoolExecutor(max_workers=min(len(hypotheses), 4)) as executor:
            future_to_idx = {
                executor.submit(
                    self.process_hypothesis, h, schema_details, domain_context, entities_and_metrics
                ): i
                for i, h in enumerate(hypotheses)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                result = future.result()
                chart_results[idx] = result
                status = "✓" if result.succeeded else "✗"
                logger.info("{} Hypothesis {}: {}", status, result.hypothesis.id, result.hypothesis.title)
                
        # Stage 8 — executive summary over succeeded charts only
        succeeded = [r for r in chart_results if r.succeeded]
        summary = self.generate_summary(succeeded)

        return {
            "dataset_context":      domain_context,
            "entities_and_metrics": entities_and_metrics,
            "charts":               [r.to_dict() for r in chart_results],
            "summary":              summary,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 0 — Schema reconciliation
    # ──────────────────────────────────────────────────────────────────────────

    def reconcile_schema_with_database(
        self,
        schema_metadata: Dict,
    ) -> Dict:
        """
        Cross-check caller-supplied ``schema_metadata`` against the live
        database's ``information_schema.columns`` and repair drift *before*
        anything downstream (enrichment, prompting, SQL generation) ever sees
        it.

        **Why this exists:**

            Every previous safeguard in this pipeline (the hallucination
            guard, the retry-with-error loop, the case-normalizer) assumes
            the *real* column name is somewhere in ``schema_metadata`` for
            the LLM to eventually land on. If the caller's metadata is
            stale, hand-written, or simply missing a column, that assumption
            is false — the LLM has nothing correct to converge toward, and
            every retry is an independent blind guess. This stage removes
            that failure mode by making the DB itself the source of truth
            for *which columns exist*, while still respecting any extra
            caller-supplied metadata (types, descriptions, etc.) for columns
            that do match.

        **Behavior:**

            - For each table, fetch the real column list from
              ``information_schema.columns``.
            - Any real column missing from the caller's ``columns`` list is
              appended (bare ``{"name": col}``), so it will show up in
              ``AVAILABLE COLUMNS`` for every LLM stage.
            - Any caller-supplied column that does *not* exist in the live
              table is dropped and logged — it would only mislead the LLM
              into "confirming" a name that will 500 at execution time.
            - Tables in ``schema_metadata`` that don't exist in the DB at all
              are logged and left as-is (nothing to reconcile against);
              downstream stages will simply get no stats for them.
            - Failures fetching live columns for a given table are logged and
              that table's metadata is passed through unchanged, so a single
              DB hiccup doesn't take down the whole pipeline.

        :param schema_metadata: Raw, caller-supplied schema dict.
        :type schema_metadata: dict

        :return: Reconciled schema dict, same shape as the input, with
            column lists corrected against the live database.
        :rtype: dict
        """
        reconciled: Dict = {}

        for table, meta in schema_metadata.items():
            raw_columns = meta.get("columns", [])
            supplied = {
                (c.get("name") or c.get("column_name")): c
                for c in raw_columns
                if c.get("name") or c.get("column_name")
            }

            try:
                result = self.postgres_service.query(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table}'"
                )
                live_columns = {
                    r["column_name"] for r in (result.get("rows") or []) if r.get("column_name")
                } if result else set()
            except Exception:
                logger.warning(
                    "Could not fetch live columns for table '{}' — passing "
                    "caller-supplied metadata through unreconciled.", table
                )
                reconciled[table] = meta
                continue

            if not live_columns:
                logger.warning(
                    "Table '{}' not found in information_schema (or has no "
                    "columns) — passing caller-supplied metadata through "
                    "unreconciled.", table
                )
                reconciled[table] = meta
                continue

            missing_from_metadata = live_columns - supplied.keys()
            stale_in_metadata = supplied.keys() - live_columns

            if missing_from_metadata:
                logger.warning(
                    "Table '{}': schema_metadata was missing {} real column(s) "
                    "not supplied by the caller — adding them: {}",
                    table, len(missing_from_metadata), sorted(missing_from_metadata),
                )
            if stale_in_metadata:
                logger.warning(
                    "Table '{}': schema_metadata referenced {} column(s) that "
                    "don't exist in the live table — dropping them: {}",
                    table, len(stale_in_metadata), sorted(stale_in_metadata),
                )

            fixed_columns = [
                c for name, c in supplied.items() if name in live_columns
            ]
            fixed_columns.extend({"name": name} for name in sorted(missing_from_metadata))

            reconciled[table] = {**meta, "columns": fixed_columns}

        return reconciled

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 0.5 — Column type repair
    # ──────────────────────────────────────────────────────────────────────────

    def _sample_column_values(
        self,
        table: str,
        column: str,
        limit: int = 200,
    ) -> List[str]:
        """
        Fetch up to *limit* distinct non-null values for a column, as strings,
        for use in type inference by :meth:`fix_column_types`.

        :param table: Table to sample from.
        :param column: Column to sample.
        :param limit: Maximum number of distinct values to fetch.

        :return: List of stringified sample values (possibly empty on error
            or if the column has no non-null values).
        :rtype: List[str]
        """
        try:
            result = self.postgres_service.query(
                f'SELECT DISTINCT "{column}" AS v FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL LIMIT {limit}'
            )
            return [
                str(r["v"]).strip()
                for r in (result.get("rows") or [])
                if r.get("v") is not None
            ]
        except Exception:
            logger.warning(
                "Could not sample column '{}.{}' for type inference.", table, column
            )
            return []

    def _infer_column_type_via_llm(
        self,
        table: str,
        column: str,
        samples: List[str],
    ) -> Optional[Dict]:
        """
        Ask the LLM to classify a TEXT/VARCHAR column's true type from a
        sample of its live values, and — for dates — the exact Postgres
        format string to cast with.

        This is the *primary* type-decision path (see module docstring):
        it replaces the previous purely-deterministic detection
        (:meth:`_infer_date_format` / :meth:`_is_numeric_column`, still kept
        as a fallback below) so a format outside the small hand-enumerated
        candidate list — a locale-specific month name, an unusual separator,
        anything the model can still resolve unambiguously from context —
        doesn't get stuck as TEXT just because nobody added it to
        :attr:`_DATE_FORMAT_CANDIDATES`.

        The model is instructed to only claim DATE/TIMESTAMP/DOUBLE
        PRECISION when *every* sample is unambiguous under one format, and
        to answer TEXT with low confidence otherwise. Callers only act on a
        "high" confidence verdict — anything else is treated the same as a
        failed call and falls through to the deterministic heuristic.

        :param table: Table the column belongs to (prompt context only).
        :param column: Column name being classified.
        :param samples: Stringified sample values (see
            :meth:`_sample_column_values`).

        :return: Parsed LLM response dict (``pg_type``, ``date_format``,
            ``confidence``, ``reasoning``), or ``None`` on any failure
            (no response, unparseable JSON) — callers fall back to the
            deterministic heuristic in that case.
        :rtype: Optional[Dict]
        """
        if not samples:
            return None

        system_prompt = textwrap.dedent("""
            You are a precise data-typing assistant for a PostgreSQL column-repair
            pipeline. You will be shown sample values from a column currently
            stored as TEXT and must decide its true underlying type.

            STRICT RULES:
            - Only answer DATE or TIMESTAMP if EVERY sample value unambiguously
              parses under the exact same format. If any value is ambiguous
              (e.g. could be DD-MM or MM-DD) or inconsistent with the others,
              answer TEXT with confidence "low" instead of guessing.
            - date_format must be a valid PostgreSQL to_date/to_timestamp format
              string (e.g. 'YYYY-MM-DD', 'DD-Mon-YYYY', 'MM/DD/YYYY HH24:MI:SS').
            - Only answer DOUBLE PRECISION if every sample is a plain number.
            - Never guess — a wrong cast corrupts every row of a live table.
        """).strip()

        user_prompt = textwrap.dedent(f"""
            Table: {table}
            Column: {column}
            Sample values ({min(len(samples), 200)} shown, distinct, non-null):
            {json.dumps(samples[:200], default=str)}

            Return JSON only:
            {{
                "pg_type": "DATE | TIMESTAMP | DOUBLE PRECISION | TEXT",
                "date_format": "Postgres format string if pg_type is DATE/TIMESTAMP, else null",
                "confidence": "high | medium | low",
                "reasoning": "one short sentence"
            }}
        """).strip()

        prompt = "\n\n".join([system_prompt, user_prompt])

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": self._COLUMN_TYPE_SCHEMA,
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1,
        )
        if response is None:
            logger.warning(
                "Column-type LLM call returned None for {}.{} — falling "
                "back to deterministic heuristic.", table, column,
            )
            return None
        if isinstance(response, str):
            try:
                response = self._parse_json(response)
            except ValueError:
                logger.warning(
                    "Column-type LLM response for {}.{} was not valid JSON "
                    "— falling back to deterministic heuristic.", table, column,
                )
                return None
        if not isinstance(response, dict):
            return None
        return response

    @classmethod
    def _infer_date_format(cls, samples: List[str]) -> Optional[tuple]:
        """
        Return ``(python_fmt, postgres_fmt)`` if *every* sample in *samples*
        parses cleanly under exactly one of :attr:`_DATE_FORMAT_CANDIDATES`,
        else ``None``.

        Requiring unanimous agreement across the whole sample (not just the
        first value) is deliberate: a column that is ambiguous between, say,
        ``DD-MM-YYYY`` and ``MM-DD-YYYY`` will only reveal that ambiguity once
        a day-of-month > 12 shows up somewhere in the sample, and we would
        rather leave such a column as TEXT than silently mis-cast it.

        This is only reached as a *fallback* — see
        :meth:`_infer_column_type_via_llm`, which is tried first in
        :meth:`fix_column_types`.

        :param samples: Stringified sample values for one column.

        :return: Matching format pair, or ``None`` if no single format fits
            every sample.
        :rtype: Optional[tuple]
        """
        if not samples:
            return None
        for py_fmt, pg_fmt in cls._DATE_FORMAT_CANDIDATES:
            try:
                if all(_dt.strptime(v, py_fmt) for v in samples):
                    return py_fmt, pg_fmt
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_numeric_column(samples: List[str]) -> bool:
        """
        Return ``True`` if every value in *samples* parses as a float.

        This is only reached as a *fallback* — see
        :meth:`_infer_column_type_via_llm`, which is tried first in
        :meth:`fix_column_types`.

        :param samples: Stringified sample values for one column.
        :rtype: bool
        """
        if not samples:
            return False
        try:
            for v in samples:
                float(v)
            return True
        except ValueError:
            return False

    def fix_column_types(
        self,
        schema_metadata: Dict,
    ) -> Dict:
        """
        Repair TEXT/VARCHAR columns that actually hold dates or numbers,
        directly in the live database, before any LLM stage ever sees the
        schema.

        **Why this exists:**

            ``PostgresService.load`` writes CSV-derived tables via
            ``pandas.DataFrame.to_sql``, which has no schema awareness — a
            date column with no explicit ``dtype=`` therefore lands in
            Postgres as plain ``TEXT``. Reconciliation (Stage 0) confirms the
            column *exists* under the right name, but says nothing about
            whether its type is usable. Every downstream SQL-generation
            attempt then has to guess a date format blind, because
            ``AVAILABLE COLUMNS`` only ever showed a name, not a type the LLM
            could trust.

        **Behavior (LLM-first):**

            - Only considers columns whose *live* Postgres type is
              ``text``/``character varying`` — already-typed columns
              (including ones this method fixed on a previous call) are
              skipped, making repeated calls cheap and idempotent.
            - Samples up to 200 distinct non-null values per candidate
              column.
            - **Primary path:** the sample is handed to
              :meth:`_infer_column_type_via_llm`, which returns a
              ``pg_type``/``date_format``/``confidence`` verdict. The column
              is altered only when the verdict's ``confidence`` is
              ``"high"``:
                - ``DATE``/``TIMESTAMP`` → ``ALTER TABLE ... USING
                  TO_DATE(...)``/``TO_TIMESTAMP(...)`` with the LLM-provided
                  format string.
                - ``DOUBLE PRECISION`` → ``ALTER TABLE ... USING col::double
                  precision``.
            - **Fallback path:** if the LLM call fails outright, returns
              malformed JSON, or answers with anything less than
              ``"high"`` confidence, the original deterministic heuristic
              (:meth:`_infer_date_format` / :meth:`_is_numeric_column`) is
              tried instead — so a flaky LLM call never regresses coverage
              versus the pre-LLM behavior.
            - Ambiguous or mixed columns (low confidence from the LLM *and*
              no deterministic match) are left untouched.
            - Any failure (sampling, LLM call, or the ``ALTER TABLE``
              itself) is logged and that column is left as-is; a single bad
              column never takes down the whole reconciliation pass.

        :param schema_metadata: Reconciled schema dict (same shape as
            :meth:`reconcile_schema_with_database`'s output — this is
            typically called immediately after it).
        :type schema_metadata: dict

        :return: The same schema dict (unchanged shape — this method's
            effect is on the live database, not on the metadata dict itself;
            callers should re-run :meth:`generate_schema_details` afterward
            to pick up the corrected types in ``_stats``).
        :rtype: dict
        """
        for table, meta in schema_metadata.items():
            try:
                result = self.postgres_service.query(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    f"WHERE table_name = '{table}'"
                )
                live_types = {
                    r["column_name"]: r["data_type"]
                    for r in (result.get("rows") or [])
                } if result else {}
            except Exception:
                logger.warning(
                    "Could not fetch column types for '{}' — skipping type repair.",
                    table,
                )
                continue

            for col_meta in meta.get("columns", []):
                col = col_meta.get("name") or col_meta.get("column_name")
                if not col:
                    continue
                if live_types.get(col) not in ("text", "character varying"):
                    # Already typed (or fixed on a prior call) — nothing to do.
                    continue

                samples = self._sample_column_values(table, col)
                if not samples:
                    continue

                # ── Primary path: LLM classification ────────────────────
                llm_result = self._infer_column_type_via_llm(table, col, samples)
                pg_type = (llm_result or {}).get("pg_type", "").strip().upper()
                date_format = (llm_result or {}).get("date_format")
                confidence = (llm_result or {}).get("confidence", "").strip().lower()

                if pg_type in ("DATE", "TIMESTAMP") and date_format and confidence == "high":
                    to_fn = "TO_DATE" if pg_type == "DATE" else "TO_TIMESTAMP"
                    try:
                        self.postgres_service.query(
                            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE {pg_type} '
                            f"USING {to_fn}(\"{col}\", '{date_format}')"
                        )
                        logger.info(
                            "Table '{}': repaired column '{}' TEXT -> {} "
                            "(format '{}', via LLM).",
                            table, col, pg_type, date_format,
                        )
                    except Exception:
                        logger.warning(
                            "Table '{}': failed to cast '{}' to {} using "
                            "LLM-inferred format — leaving as TEXT.",
                            table, col, pg_type,
                        )
                    continue

                if pg_type == "DOUBLE PRECISION" and confidence == "high":
                    try:
                        self.postgres_service.query(
                            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" '
                            f'TYPE DOUBLE PRECISION USING "{col}"::double precision'
                        )
                        logger.info(
                            "Table '{}': repaired column '{}' TEXT -> "
                            "DOUBLE PRECISION (via LLM).",
                            table, col,
                        )
                    except Exception:
                        logger.warning(
                            "Table '{}': failed to cast '{}' to numeric "
                            "(LLM-inferred) — leaving as TEXT.",
                            table, col,
                        )
                    continue

                # ── Fallback: deterministic heuristic ───────────────────
                # Reached when the LLM call failed outright, returned a
                # malformed payload, or wasn't confident — the original
                # strptime/float-based detection, so a flaky LLM call never
                # regresses coverage versus the pre-LLM behavior.
                date_match = self._infer_date_format(samples)
                if date_match:
                    _, pg_fmt = date_match
                    try:
                        self.postgres_service.query(
                            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE DATE '
                            f"USING TO_DATE(\"{col}\", '{pg_fmt}')"
                        )
                        logger.info(
                            "Table '{}': repaired column '{}' TEXT -> DATE "
                            "(format '{}', via heuristic fallback).",
                            table, col, pg_fmt,
                        )
                    except Exception:
                        logger.warning(
                            "Table '{}': failed to cast '{}' to DATE — "
                            "leaving as TEXT.",
                            table, col,
                        )
                    continue

                if self._is_numeric_column(samples):
                    try:
                        self.postgres_service.query(
                            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" '
                            f'TYPE DOUBLE PRECISION USING "{col}"::double precision'
                        )
                        logger.info(
                            "Table '{}': repaired column '{}' TEXT -> "
                            "DOUBLE PRECISION (via heuristic fallback).",
                            table, col,
                        )
                    except Exception:
                        logger.warning(
                            "Table '{}': failed to cast '{}' to numeric — "
                            "leaving as TEXT.",
                            table, col,
                        )

        return schema_metadata

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 1 — Schema inspection
    # ──────────────────────────────────────────────────────────────────────────

    def generate_schema_details(
        self, 
        schema_metadata: Dict,
        reconcile: bool = True,
    ) -> Dict:
        """
        Enrich raw schema metadata with DB-level statistics.

        **Description:**

            For each table, fetches approximate row count, column nullability,
            and cardinality hints for low-cardinality columns (≤ 50 distinct values).
            Failures are logged and skipped so the pipeline continues with partial
            enrichment.

        .. important::
            This method is a common integration seam — callers that don't go
            through :meth:`explore` (e.g. a caching layer that calls it
            directly) would otherwise silently skip schema reconciliation
            *and* column-type repair entirely, which defeats the whole point
            of Stages 0 and 0.5. So by default this method reconciles *and*
            repairs types for you on every call; pass ``reconcile=False``
            only if the caller has already reconciled and type-repaired
            *this exact* ``schema_metadata`` and wants to avoid a redundant
            ``information_schema`` round-trip.

        :param schema_metadata: Raw schema dict. Reconciled and type-repaired
            against the live DB internally unless ``reconcile=False``.
        :type schema_metadata: dict
        :param reconcile: Whether to run :meth:`reconcile_schema_with_database`
            and :meth:`fix_column_types` on *schema_metadata* before
            enriching. Defaults to ``True``.
        :type reconcile: bool
        
        :return: Enriched schema dict (original metadata + ``_stats`` per table).
        :rtype: dict
        """
        if reconcile:
            schema_metadata = self.reconcile_schema_with_database(schema_metadata)
            schema_metadata = self.fix_column_types(schema_metadata)

        enriched = {}
        for table, meta in schema_metadata.items():
            stats: Dict[str, Any] = {"row_count": None, "columns": {}}

            # Approximate row count via pg_class (fast, no sequential scan)
            try:
                result = self.postgres_service.query(
                    f"SELECT reltuples::bigint AS row_count "
                    f"FROM pg_class WHERE relname = '{table}'"
                )
                rows = result.get("rows", []) if result else []
                if rows:
                    stats["row_count"] = rows[0].get("row_count")
            except Exception:
                logger.warning("Could not fetch row count for table '{}'.", table)

            # Resolve column names tolerantly — accept both "name" and "column_name"
            raw_columns = meta.get("columns", [])
            columns = [
                c.get("name") or c.get("column_name")
                for c in raw_columns
                if c.get("name") or c.get("column_name")
            ]

            # Per-column: null fraction + distinct count
            for col in columns:
                col_stats: Dict[str, Any] = {}
                try:
                    result = self.postgres_service.query(
                        f'SELECT COUNT(*) FILTER (WHERE "{col}" IS NULL) AS nulls, '
                        f'COUNT(DISTINCT "{col}") AS distinct_count '
                        f'FROM "{table}"'
                    )
                    rows = result.get("rows", []) if result else []
                    if rows:
                        col_stats["null_count"]     = rows[0].get("nulls")
                        col_stats["distinct_count"] = rows[0].get("distinct_count")

                        # For low-cardinality columns, surface sample values
                        if (col_stats["distinct_count"] or 0) <= 50:
                            vals = self.postgres_service.query(
                                f'SELECT DISTINCT "{col}" FROM "{table}" LIMIT 50'
                            )
                            if vals:
                                col_stats["sample_values"] = [
                                    r[col] for r in (vals.get("rows") or [])
                                ]
                except Exception:
                    logger.warning("Could not fetch stats for {}.{}.", table, col)

                stats["columns"][col] = col_stats

            enriched[table] = {**meta, "_stats": stats}

        return enriched

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 2 — Domain inference
    # ──────────────────────────────────────────────────────────────────────────

    def generate_domain_context(
        self, 
        schema: Dict
    ) -> Dict | None:
        """
        Ask the LLM to infer the business domain and analytical opportunities.

        :param schema: Enriched schema from ``_inspect_schema``.
        :type schema: Dict
        
        :return: Domain context dict.
        :rtype: Dict | None
        """
        schema_summary: str = self.generate_schema_summary(schema)
        
        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_DOMAIN_CONTEXT
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_DOMAIN_CONTEXT,
            schema_details=schema_summary
        )
        prompt: str = "\n\n".join([system_prompt, user_prompt])

        response: str = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": DOMAIN_CONTEXT_SCHEMA
            },
            strict=False,
            provider_order=[ Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "domain_generation")
            return None
        if isinstance(response, str):            
            response = self._parse_json(response)
            if not isinstance(response, dict):
                logger.warning("Stage '{}': expected JSON object, got {}.", "domain_generation", type(response))
                return {}

        return response

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 3 — Entity & metric extraction
    # ──────────────────────────────────────────────────────────────────────────

    def extract_entities_and_metrics(
        self,
        schema_details: Dict, 
        domain_context: Dict
    ) -> Dict | None:
        """
        Resolve concrete measurable metrics and grouping dimensions.

        :param schema_details: Enriched schema.
        :param domain_context: Output of ``_infer_domain``.
        
        :return: Dict with ``metrics`` and ``dimensions`` lists.
        :rtype: Dict | None
        """
        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.EXTRACT_ENTITIES_AND_METRICS
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.EXTRACT_ENTITIES_AND_METRICS,
            domain_context=domain_context,
            schema_details=schema_details
        )
        prompt: str = "\n\n".join([system_prompt, user_prompt])
        
        response: str = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": ENTITIES_AND_METRICS_SCHEMA
            },
            strict=False,
            provider_order=[ Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "entity_extraction")
            return None
        if isinstance(response, str):
            response = self._parse_json(response)
            if not isinstance(response, dict):
                logger.warning("Stage '{}': expected JSON object, got {}.", "entity_extraction", type(response))
                return {}

        return response

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 4 — Hypothesis generation
    # ──────────────────────────────────────────────────────────────────────────

    def generate_hypotheses(
        self,
        schema_details: Dict,
        domain_context: Dict,
        entities_and_metrics: Dict,
        previous_questions: Optional[List[str]] = None,
    ) -> List[Hypothesis]:
        """
        Generate sophisticated, non-trivial analytical hypotheses.

        :param schema_details: Enriched schema.
        :param domain_context: Output of ``_infer_domain``.
        :param entities_and_metrics: Output of ``_extract_entities_and_metrics``.
        :param previous_questions: Questions already answered for this same
            source set in an earlier cell/EDA run (e.g. via
            :meth:`NotebookService._previous_eda_questions`). When provided,
            the prompt explicitly instructs the LLM to avoid repeating or
            closely paraphrasing them, so re-running EDA on the same data
            produces analytically distinct hypotheses instead of duplicates.
        :type previous_questions: Optional[List[str]]
        
        :return: List of :class:`Hypothesis` objects.
        :rtype: List[Hypothesis]
        """
        analytical_types_str = "\n".join(f"- {t}" for t in self._ANALYTICAL_TYPES)
        plot_types_str       = " | ".join(self._PLOT_TYPES)
        
        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_HYPOTHESES
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_HYPOTHESES,
            domain_context=domain_context,
            entities_and_metrics=entities_and_metrics,
            schema_summary=self.generate_schema_summary(schema_details),
            max_hypotheses=self.max_hypotheses,
            analytical_types=analytical_types_str,
            plot_types=plot_types_str,
            available_columns=self.generate_column_details(schema_details),
            previous_questions=previous_questions or [],
        )
        prompt: str = "\n\n".join([system_prompt, user_prompt])

        response: str = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": HYPOTHESES_SCHEMA
            },
            strict=False,
            provider_order=[ Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "hypothesis_generation")
            return []
        if isinstance(response, str):
            response = self._parse_json(response)
            if not isinstance(response, list):
                logger.warning("Stage '{}': expected JSON array, got {}.", "hypothesis_generation", type(response))
                return []

        return [self._parse_hypothesis(hypothesis) for hypothesis in response if isinstance(hypothesis, dict)]

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 5 — SQL generation (with retry on execution failure)
    # ──────────────────────────────────────────────────────────────────────────

    def generate_sql(
        self,
        hypothesis: Hypothesis,
        schema_details: Dict,
        domain_context: Dict,
        entities_and_metrics: Dict,
        previous_error: Optional[str] = None,
    ) -> SqlSpec:
        """
        Generate a PostgreSQL query for a hypothesis.

        If *previous_error* is provided the LLM is asked to fix the broken
        query rather than generating from scratch.

        :param hypothesis: The hypothesis to answer.
        :param schema_details: Enriched schema.
        :param domain_context: Domain context.
        :param entities_and_metrics: Entities and metrics.
        :param previous_error: Execution error from the previous attempt (if any).
        
        :return: :class:`SqlSpec` with SQL and axis metadata.
        :rtype: SqlSpec
        """
        error_block = ""
        if previous_error:
            error_block = f"""
            ⚠️ YOUR PREVIOUS SQL FAILED — YOU MUST FIX IT:

            {previous_error}

            Rules for the fix:
            - Only use column names listed in the AVAILABLE COLUMNS block below.
            - Do NOT invent, guess, abbreviate, or paraphrase column names.
            - If a suggested replacement column is given above, use it unless it is
            clearly analytically wrong for this question.
            - If the hypothesis cannot be answered with the available columns, pivot to a
            closely related question that CAN be answered with the real columns.
            - Keep the same analytical intent where possible.
            """
        
        columns_block = self.generate_column_details(schema_details)
        logger.debug("AVAILABLE COLUMNS for hypothesis {}:\n{}", hypothesis.id, columns_block)
        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_SQL
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_SQL,
            hypothesis=hypothesis,
            domain_context=domain_context,
            entities_and_metrics=entities_and_metrics,
            schema_summary=self.generate_schema_summary(schema_details),
            columns_details=self.generate_column_details(schema_details)
        )
        prompt = "\n\n".join([system_prompt, error_block, user_prompt])

        response: str = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": SQL_SPEC_SCHEMA
            },
            strict=False,
            provider_order=[ Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "sql_generation")
            response = {}
        if isinstance(response, str):
            response = self._parse_json(response)
            if not isinstance(response, dict):
                logger.warning("Stage '{}': expected JSON object, got {}.", "sql_generation", type(response))
                response = {}

        return self._parse_sql_spec(response)

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 6 — Query execution
    # ──────────────────────────────────────────────────────────────────────────

    def execute_sql(
        self, 
        sql: str
    ) -> pd.DataFrame:
        """
        Execute *sql* via PostgresService and return a DataFrame.

        :param sql: SELECT statement to execute.
        :type sql: str
        
        :return: Query result as a DataFrame (empty on no rows).
        :rtype: pd.DataFrame
        :raises Exception: Any database error is re-raised for the caller to handle.
        """
        logger.debug("Executing SQL:\n{}", sql)
        result = self.postgres_service.query(sql)
        if not result:
            return pd.DataFrame()
        rows = result.get("rows") or []
        return pd.DataFrame(rows)

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 7 — Observation generation
    # ──────────────────────────────────────────────────────────────────────────

    def generate_observation(
        self,
        hypothesis: Hypothesis,
        df: pd.DataFrame,
        domain_context: Optional[Dict] = None,
    ) -> str:
        """
        Generate a plain-text analytical observation from query results.

        :param hypothesis: The hypothesis being examined.
        :param df: Query results.
        :param domain_context: Domain context from Stage 2, used to ground the observation.

        :return: 2-3 sentence observation.
        :rtype: str
        """
        if df.empty:
            return "The query returned no data for this analysis."

        sample = df.head(self.row_sample_for_observation).to_dict("records")
        column_names = list(df.columns)

        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_OBSERVATION
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_OBSERVATION,
            hypothesis=hypothesis,
            num_of_samples=min(len(sample), self.row_sample_for_observation),
            sample=sample,
            domain_context=domain_context or {},
            column_names=column_names,
        )
        prompt = "\n\n".join([system_prompt, user_prompt])

        response: str = self.get_response(
            fn_name="generate_response",
            model_input={
                "user_prompt": prompt
            },
            strict=False,
            provider_order=[ Provider.GEMINI, Provider.GROQ, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "observation_generation")
            return "No observation could be generated."

        return (response or "No observation could be generated.").strip()

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 8 — Executive summary
    # ──────────────────────────────────────────────────────────────────────────

    def generate_summary(
        self, 
        chart_results: List[ChartResult]
    ) -> Dict:
        """
        Synthesise all succeeded chart observations into an executive summary.

        :param chart_results: Succeeded :class:`ChartResult` objects.

        :return: Summary dict with ``dataset_description``, ``key_findings``,
            and ``recommended_next_steps``.
        :rtype: dict
        """
        fallback = {
            "dataset_description": "No analyses succeeded.",
            "key_findings": [],
            "recommended_next_steps": [],
        }
        if not chart_results:
            return fallback

        system_prompt: str = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_SUMMARY
        )
        user_prompt: str = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_SUMMARY,
            chart_results=chart_results
        )
        prompt = "\n\n".join([system_prompt, user_prompt])

        response: str = self.get_response(
            fn_name="generate_json",
            model_input={
                "user_prompt": prompt,
                "schema": SUMMARY_SCHEMA
            },
            strict=False,
            provider_order=[ Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash",
                # OPENROUTER
                "meta-llama/llama-3.3-70b-instruct:free",
                "deepseek/deepseek-r1-distill-llama-70b:free",
            ],
            retry_attempt=1
        )
        if response is None:
            logger.warning("Stage '{}': LLM returned None.", "executive_summary")
            return fallback
        if isinstance(response, str):
            response = self._parse_json(response)
            if not isinstance(response, dict):
                logger.warning("Stage '{}': expected JSON object, got {}.", "executive_summary", type(response))
                return fallback
        return response

    # ──────────────────────────────────────────────────────────────────────────
    # Hypothesis processing pipeline (stages 5 → 7 with SQL retry)
    # ──────────────────────────────────────────────────────────────────────────

    def process_hypothesis(
        self,
        hypothesis: Hypothesis,
        enriched_schema: Dict,
        domain_context: Dict,
        entities_and_metrics: Dict,
    ) -> ChartResult:
        """
        Run stages 5–7 for a single hypothesis with SQL retry on failure.

        :param hypothesis: The hypothesis to process.
        :type: Hypthesis
        
        :return: Populated :class:`ChartResult`.
        :rtype: ChartResult

        .. note::
        
            On execution failure (DB error or hallucinated-column detection) the
            LLM is asked to fix the SQL up to ``self.max_sql_retries`` times before
            giving up and recording an error result. Two convergence safeguards
            were added:

            - The error fed back to the LLM now includes a nearest-real-column
              suggestion per hallucinated identifier (see
              :meth:`_suggest_column_fixes`), so retries have something new to
              act on instead of re-guessing.
            - If a retry hallucinates the *exact same* column set as the
              previous attempt, the loop gives up immediately rather than
              spending the remaining attempts on a guess that has already
              demonstrably not changed.
        
        """
        last_error: Optional[str] = None
        last_hallucinated: Optional[frozenset] = None
        sql_spec: Optional[SqlSpec] = None
        df: Optional[pd.DataFrame] = None

        for attempt in range(self.max_sql_retries + 1):
            try:
                sql_spec = self.generate_sql(
                    hypothesis=hypothesis,
                    schema_details=enriched_schema,
                    domain_context=domain_context,
                    entities_and_metrics=entities_and_metrics,
                    previous_error=last_error,
                )
                sql_spec.sql = self._normalize_identifier_case(sql_spec.sql, enriched_schema)

                # ── Pre-execution hallucination check ──────────────────────
                # Detect invented column names before hitting the DB so the
                # retry error message is specific enough for the LLM to fix.
                hallucinated = self._find_hallucinated_columns(sql_spec.sql, enriched_schema)
                if hallucinated:
                    current_set = frozenset(hallucinated)

                    # Fast-fail: identical hallucination twice in a row means
                    # the LLM isn't converging — stop burning retries on it.
                    if current_set == last_hallucinated:
                        logger.warning(
                            "Hypothesis {}: attempt {} hallucinated the same "
                            "column(s) as the previous attempt ({}) — giving "
                            "up early instead of repeating remaining retries.",
                            hypothesis.id, attempt + 1, sorted(current_set),
                        )
                        return ChartResult(
                            hypothesis=hypothesis,
                            sql_spec=sql_spec,
                            data=pd.DataFrame(),
                            observation="",
                            error=(
                                f"SQL generation stalled: repeated the same "
                                f"non-existent column(s) {sorted(current_set)} "
                                f"across consecutive attempts."
                            ),
                        )
                    last_hallucinated = current_set

                    raise ValueError(self._describe_hallucinated_columns(
                        hallucinated, enriched_schema
                    ))

                df = self.execute_sql(sql_spec.sql)
                logger.info("SQL execution succeeded for hypothesis {}", hypothesis.id)
                break  # success — exit retry loop

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "SQL attempt {}/{} failed for hypothesis {}: {}",
                    attempt + 1,
                    self.max_sql_retries + 1,
                    hypothesis.id,
                    last_error,
                )
                if attempt == self.max_sql_retries:
                    return ChartResult(
                        hypothesis=hypothesis,
                        sql_spec=sql_spec or SqlSpec(
                            sql="", x=AxisSpec("", "", ""), y=AxisSpec("", "", "")
                        ),
                        data=pd.DataFrame(),
                        observation="",
                        error=(
                            f"SQL generation/execution failed after "
                            f"{self.max_sql_retries + 1} attempts: {last_error}"
                        ),
                    )

        observation = self.generate_observation(hypothesis, df, domain_context=domain_context)
        return ChartResult(
            hypothesis=hypothesis,
            sql_spec=sql_spec,
            data=df,
            observation=observation,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Hallucination guard
    # ──────────────────────────────────────────────────────────────────────────

    def _find_hallucinated_columns(
        self, 
        sql: str, 
        enriched_schema: Dict
    ) -> List[str]:
        """
        Return column-like tokens in *sql* that do not exist in *enriched_schema*.

        Strategy:
        - Collect all ``AS <alias>`` tokens across the entire SQL and treat them
          as valid (they are output names, not input column references).
        - Collect all CTE names (``WITH cte_name AS``) as valid identifiers.
        - Inspect only the SELECT clause for bare identifiers, excluding anything
          that is a SQL keyword, aggregate function, known real column, known
          table name, user-defined alias, or CTE name.

        This avoids the previous false-positive bug where ``chol AS cholesterol``
        caused ``cholesterol`` to be flagged as hallucinated.

        .. note::
            With multiple tables, real columns and table names from *every*
            table are pooled together (not scoped per-table). This is
            intentionally permissive: it lets JOINs use qualified
            (``table.column``) or bare column references from any of the
            provided tables without false positives, at the cost of not
            catching a column used against the wrong table. That trade-off
            already existed for the single-table case and is unchanged here.

        :param sql: SQL string to inspect.
        :param enriched_schema: Enriched schema (source of truth for real columns).
        
        :return: List of suspected hallucinated identifiers (may be empty).
        :rtype: List[str]
        """
        sql_lower = sql.lower()

        # ── 1. Known real columns from schema ────────────────────────────────
        known_columns: set[str] = set()
        for meta in enriched_schema.values():
            for col_meta in meta.get("columns", []):
                col = col_meta.get("name") or col_meta.get("column_name") or ""
                if col:
                    known_columns.add(col.lower())

        # ── 2. Table names ────────────────────────────────────────────────────
        known_tables = {t.lower() for t in enriched_schema}

        # ── 3. All AS aliases in the entire SQL (output names, always valid) ──
        # Matches:  ... AS alias_name  (alias must be a plain identifier)
        as_aliases: set[str] = {
            m.group(1).lower()
            for m in re.finditer(r'\bAS\s+([a-z_][a-z0-9_]*)\b', sql, re.IGNORECASE)
        }

        # ── 4. CTE names  (WITH cte_name AS (...)) ───────────────────────────
        cte_names: set[str] = {
            m.group(1).lower()
            for m in re.finditer(
                r'\bWITH\s+([a-z_][a-z0-9_]*)\s+AS\s*\(', sql, re.IGNORECASE
            )
        }
        # CTEs may also be comma-separated:  , next_cte AS (
        cte_names.update(
            m.group(1).lower()
            for m in re.finditer(
                r',\s*([a-z_][a-z0-9_]*)\s+AS\s*\(', sql, re.IGNORECASE
            )
        )

        # Everything that is a valid identifier in this SQL
        all_valid = self._SQL_KEYWORDS | known_columns | known_tables | as_aliases | cte_names

        # ── 5. Isolate the outermost SELECT clause (before the first bare FROM) ──
        # Use the non-CTE part: strip WITH ... preamble first, then grab SELECT list.
        sql_body = re.sub(
            r'^\s*WITH\b.*?\)\s*(?=SELECT)', '', sql, count=1,
            flags=re.IGNORECASE | re.DOTALL
        )
        select_match = re.search(
            r'\bSELECT\b(.*?)\bFROM\b', sql_body, re.IGNORECASE | re.DOTALL
        )
        if not select_match:
            return []

        select_clause = select_match.group(1)

        # Strip string literals to avoid capturing words inside quotes
        select_clause = re.sub(r"'[^']*'", "", select_clause)
        # Strip AS aliases we already collected (remove "AS <word>") so the alias
        # token itself doesn't appear in the remaining token scan
        select_clause = re.sub(r'\bAS\s+[a-z_][a-z0-9_]*\b', '', select_clause,
                                flags=re.IGNORECASE)

        # ── 6. Scan remaining tokens for unknowns ────────────────────────────
        tokens = re.findall(r'\b([a-z_][a-z0-9_]*)\b', select_clause.lower())

        hallucinated: List[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in all_valid:
                continue
            if token.isdigit():
                continue
            if token not in seen:
                seen.add(token)
                hallucinated.append(token)

        return hallucinated

    def _suggest_column_fixes(
        self,
        hallucinated: List[str],
        enriched_schema: Dict,
    ) -> Dict[str, Optional[str]]:
        """
        For each hallucinated identifier, find the closest real column name
        (case-insensitive, across all tables) using :mod:`difflib`, so the
        retry prompt can offer a concrete correction instead of just a
        rejection.

        :param hallucinated: Identifiers flagged by :meth:`_find_hallucinated_columns`.
        :param enriched_schema: Enriched schema (source of truth for real columns).

        :return: Dict mapping each hallucinated identifier to its best-guess
            real column name, or ``None`` if nothing is close enough
            (similarity cutoff 0.5) to be a useful suggestion.
        :rtype: Dict[str, Optional[str]]
        """
        known_columns: List[str] = []
        for meta in enriched_schema.values():
            for col_meta in meta.get("columns", []):
                col = col_meta.get("name") or col_meta.get("column_name") or ""
                if col:
                    known_columns.append(col)

        suggestions: Dict[str, Optional[str]] = {}
        for token in hallucinated:
            matches = difflib.get_close_matches(
                token, known_columns, n=1, cutoff=0.5
            )
            suggestions[token] = matches[0] if matches else None
        return suggestions

    def _describe_hallucinated_columns(
        self,
        hallucinated: List[str],
        enriched_schema: Dict,
    ) -> str:
        """
        Build the retry-error message for hallucinated columns, including a
        nearest-real-column suggestion per identifier so the next LLM call
        has something concrete to correct toward instead of re-guessing.

        :param hallucinated: Identifiers flagged by :meth:`_find_hallucinated_columns`.
        :param enriched_schema: Enriched schema (source of truth for real columns).

        :return: Human-readable error string for the LLM retry prompt.
        :rtype: str
        """
        suggestions = self._suggest_column_fixes(hallucinated, enriched_schema)
        lines = [
            "The following column names in the SQL do not exist in the schema:"
        ]
        for token in hallucinated:
            suggestion = suggestions.get(token)
            if suggestion:
                lines.append(f"  - '{token}' — closest real column is '{suggestion}'; use it if it fits the question.")
            else:
                lines.append(f"  - '{token}' — no similarly-named real column exists; do not use this identifier at all.")
        lines.append("Replace every one of these with a real column name from the AVAILABLE COLUMNS list.")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────────
    # Parsing helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> Any:
        """
        Parse *raw* as JSON, stripping markdown fences if present.

        :param raw: Raw LLM output.
        
        :return: Parsed Python object.
        :raises ValueError: If the string cannot be parsed as JSON.
        """
        cleaned = raw.strip()

        # Strip ```json ... ``` or ``` ... ``` fences
        fence_match = re.search(
            r"```(?:json)?\s*([\[{].*?[\]}])\s*```", cleaned, re.DOTALL
        )
        if fence_match:
            cleaned = fence_match.group(1).strip()
        else:
            # Find the outermost { } or [ ] block
            bare_match = re.search(r"([\[{].*[\]}])", cleaned, re.DOTALL)
            if bare_match:
                cleaned = bare_match.group(1).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Could not parse LLM output as JSON: {exc}\n\nRaw:\n{raw[:500]}"
            )

    @staticmethod
    def _parse_hypothesis(raw: Dict) -> Hypothesis:
        """
        Construct a :class:`Hypothesis` from a raw LLM dict.
        """
        return Hypothesis(
            id=str(raw.get("id", "")),
            question=str(raw.get("question", "")),
            title=str(raw.get("title", "")),
            description=str(raw.get("description", "")),
            why_it_matters=str(raw.get("why_it_matters", "")),
            plot_type=str(raw.get("plot_type", "bar")),
            analytical_type=str(raw.get("analytical_type", "")),
        )

    @staticmethod
    def _parse_sql_spec(raw: Dict) -> SqlSpec:
        """Construct a :class:`SqlSpec` from a raw LLM dict."""
        x_raw = raw.get("x") or {}
        y_raw = raw.get("y") or {}
        return SqlSpec(
            sql=str(raw.get("sql", "")),
            x=AxisSpec(
                column=str(x_raw.get("column", "")),
                label=str(x_raw.get("label", "")),
                type=str(x_raw.get("type", "nominal")),
            ),
            y=AxisSpec(
                column=str(y_raw.get("column", "")),
                label=str(y_raw.get("label", "")),
                type=str(y_raw.get("type", "quantitative")),
            ),
            group_by=raw.get("group_by"),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Multi-table relationship detection
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def generate_table_relationships(
        cls,
        enriched_schema: Dict,
    ) -> Dict[str, List[str]]:
        """
        Deterministically detect candidate join keys between every pair of
        tables in *enriched_schema*, so multi-table hypotheses/SQL are
        grounded in real schema evidence rather than an LLM guess made from
        table names alone.

        Two signals are combined per table pair:

        - **Shared column names** — an exact (case-insensitive) column-name
          match in both tables, excluding generic/noisy names (see
          ``_JOIN_KEY_STOPWORDS``) that are common across unrelated tables
          and rarely usable as a join key on their own.
        - **Foreign-key-style naming** — a column in one table named
          ``"<singular of the other table>_id"`` (e.g. a ``customers`` table
          and an ``order.customer_id`` column), which is a very common
          convention even when the two tables don't share any other column
          name.

        This is a heuristic, not a guarantee — it surfaces *candidates* for
        the LLM to confirm/use, not a verified foreign-key constraint (which
        would require introspecting the database's actual constraints).

        :param enriched_schema: Table-keyed schema dict, each value carrying
            a ``columns`` list of ``{"name"|"column_name": str, ...}`` dicts.
        :return: Dict keyed by ``"{table_a}__{table_b}"`` (only pairs with at
            least one candidate key are included) mapping to a sorted list of
            candidate join-key column names.
        """
        tables = list(enriched_schema.keys())
        column_sets: Dict[str, set] = {}
        for table, meta in enriched_schema.items():
            cols = {
                (c.get("name") or c.get("column_name") or "").lower()
                for c in meta.get("columns", [])
            }
            cols.discard("")
            column_sets[table] = cols

        def _singularize(name: str) -> str:
            return name[:-1] if name.endswith("s") and len(name) > 1 else name

        relationships: Dict[str, List[str]] = {}
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                t1, t2 = tables[i], tables[j]

                shared = (column_sets[t1] & column_sets[t2]) - cls._JOIN_KEY_STOPWORDS

                fk_candidates: set = set()
                for owner_table, other_table in ((t1, t2), (t2, t1)):
                    fk_col = f"{_singularize(other_table)}_id"
                    if fk_col in column_sets[owner_table]:
                        fk_candidates.add(fk_col)
                    # Also try the un-singularized form, since not every
                    # table name follows plural-noun conventions.
                    fk_col_plain = f"{other_table}_id"
                    if fk_col_plain in column_sets[owner_table]:
                        fk_candidates.add(fk_col_plain)

                keys = sorted(shared | fk_candidates)
                if keys:
                    relationships[f"{t1}__{t2}"] = keys

        return relationships

    # ──────────────────────────────────────────────────────────────────────────
    # Schema formatting helpers
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def generate_schema_summary(cls, enriched_schema: Dict) -> str:
        """
        Render a compact, token-efficient schema summary for LLM prompts.
        Includes table row counts, column names, types, null counts, distinct
        counts, and sample values for low-cardinality columns.

        When *enriched_schema* describes more than one table, a "Table
        relationships" section is appended (see
        :meth:`generate_table_relationships`) so every prompt that consumes
        this summary — domain inference, hypothesis generation, and SQL
        generation — is grounded in how the tables can be joined, not just
        their individual shapes.

        :param enriched_schema: Enriched schema from ``_inspect_schema``.
        :type: Dict

        :return: Multi-line string representation.
        :rtype: str
        """
        lines: List[str] = []
        for table, meta in enriched_schema.items():
            stats     = meta.get("_stats", {})
            row_count = stats.get("row_count", "unknown")
            lines.append(f"Table: {table}  (~{row_count} rows)")

            for col_meta in meta.get("columns", []):
                # Tolerate both key conventions
                col_name = col_meta.get("name") or col_meta.get("column_name", "?")
                col_type = col_meta.get("type") or col_meta.get("data_type", "?")
                col_stats = stats.get("columns", {}).get(col_name, {})
                nulls     = col_stats.get("null_count", "?")
                distinct  = col_stats.get("distinct_count", "?")
                samples   = col_stats.get("sample_values")

                line = f"  {col_name} ({col_type})  nulls={nulls}  distinct={distinct}"
                if samples:
                    sample_str = ", ".join(str(v) for v in samples[:10])
                    line += f"  values=[{sample_str}]"
                lines.append(line)

            lines.append("")

        if len(enriched_schema) > 1:
            lines.append(
                f"NOTE: {len(enriched_schema)} tables are available in this "
                "request — hypotheses and SQL may JOIN across them where it "
                "makes analytical sense, not just query one table at a time."
            )
            relationships = cls.generate_table_relationships(enriched_schema)
            if relationships:
                lines.append(
                    "Candidate join keys between tables (detected from shared "
                    "or foreign-key-style column names — verify against the "
                    "column lists above before using):"
                )
                for pair, keys in relationships.items():
                    t1, t2 = pair.split("__", 1)
                    lines.append(f"  {t1} <-> {t2}: {', '.join(keys)}")
            else:
                lines.append(
                    "No shared or foreign-key-style column names were detected "
                    "between any pair of tables. Inspect the column lists above "
                    "to judge whether — and how — these tables relate before "
                    "writing any SQL that joins them."
                )
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def generate_column_details(enriched_schema: Dict) -> str:
        """
        Render a terse, unambiguous per-table column list for injection into
        SQL-generation prompts as a hard constraint.

        Format::

            table_name: col_a, col_b, col_c
            other_table: id, name, created_at

        :param enriched_schema: Enriched schema from ``_inspect_schema``.
        
        
        :return: Multi-line string, one table per line.
        :rtype: str
        """
        lines: List[str] = []
        for table, meta in enriched_schema.items():
            cols = []
            for col_meta in meta.get("columns", []):
                col = col_meta.get("name") or col_meta.get("column_name") or ""
                if col:
                    cols.append(col)
            if cols:
                lines.append(f"{table}: {', '.join(cols)}")
        return "\n".join(lines)
    
    @classmethod
    def _canonical_case_map(
        cls, 
        enriched_schema: Dict
    ) -> Dict[str, str]:
        """
        Map lowercased table/column name -> its real, schema-declared case."""
        mapping: Dict[str, str] = {}
        for table, meta in enriched_schema.items():
            mapping.setdefault(table.lower(), table)
            for col_meta in meta.get("columns", []):
                col = col_meta.get("name") or col_meta.get("column_name") or ""
                if col:
                    mapping.setdefault(col.lower(), col)
        return mapping

    @classmethod
    def _normalize_identifier_case(
        cls, 
        sql: str, 
        enriched_schema: Dict
    ) -> str:
        """
        Rewrite bare identifiers in *sql* to the real case of the underlying
        table/column, double-quoting them whenever that case isn't all-lowercase.
        Postgres folds unquoted identifiers to lowercase, so an LLM that writes
        `customerid` for a real `"CustomerID"` column will otherwise 500 at
        execution time even though it picked the *right* column.
        """
        case_map = cls._canonical_case_map(enriched_schema)
        if not case_map:
            return sql

        # Protect string literals so we never touch text inside quotes.
        literals: List[str] = []
        def _stash(m):
            literals.append(m.group(0))
            return f"\x00{len(literals) - 1}\x00"
        protected = re.sub(r"'[^']*'", _stash, sql)

        def _replace(m: "re.Match") -> str:
            token = m.group(0)
            start = m.start()
            if start > 0 and protected[start - 1] == '"':
                return token  # already quoted, leave it
            low = token.lower()
            if low in cls._SQL_KEYWORDS:
                return token
            canonical = case_map.get(low)
            if canonical is None:
                return token
            if protected[m.end():m.end() + 1] == "(":
                return token  # function call, not a column
            return canonical if canonical == low else f'"{canonical}"'

        fixed = re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', _replace, protected)
        fixed = re.sub(r"\x00(\d+)\x00", lambda m: literals[int(m.group(1))], fixed)
        return fixed

    def classify_question_intent(
        self,
        question: str
    ) -> Dict:
        """
        Decide whether *question* should be answered with a chart or with a
        plain tabular result (a saved view, no visualization forced onto it).

        This is also the entry point for manipulation-flavored requests
        ("dedupe this", "drop outliers from X", "give me a filtered list")
        — they aren't a distinct request type, they're just questions that
        this classifier is expected to route to a table/view rather than a
        chart. See :meth:`process_view_request` for what happens next.

        Never raises - any failures to classify defaults to ``needs_chart=True``
        so the pipeline degrades to its previous (chart-always) behavior rather
        than silently doing nothing.

        :param question: The user's question, verbatim.
        :return: ``{"needs_chart": bool, "chart_type": Optional[str], "reasoning": str}``
        :rtype: Dict
        """
        fallback = {"needs_chart": True, "chart_type": None, "reasoning": "Classifier unavailable — defaulting to chart."}

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.CLASSIFY_QUESTION_INTENT
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.CLASSIFY_QUESTION_INTENT, question=question
        )
        prompt = "\n\n".join([system_prompt, user_prompt])

        response = self.get_response(
            fn_name="generate_json",
            model_input={"user_prompt": prompt, "schema": self._INTENT_SCHEMA},
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                "qwen-qwq-32b",
                "openai/gpt-oss-20b",
                "models/gemini-2.5-flash",
                "meta-llama/llama-3.3-70b-instruct:free",
            ],
            retry_attempt=1,
        )
        if response is None:
            return fallback
        if isinstance(response, str):
            try:
                response = self._parse_json(response)
            except ValueError:
                return fallback
        if not isinstance(response, dict) or "needs_chart" not in response:
            return fallback
        return response

    def _generate_and_execute_with_retry(
        self,
        sql_generator: "Callable[[Optional[str]], str]",
        enriched_schema: Dict,
        executor: Optional["Callable[[str], Any]"] = None,
    ):
        """
        Shared retry loop used by chart SQL and view SQL generation:
        generate → check for hallucinated columns → execute → on failure feed
        the error back and retry, up to ``max_sql_retries`` times. Raises the
        last error if every attempt fails; two consecutive attempts
        hallucinating the exact same column(s) fail fast instead of burning
        the remaining retries (same rationale as :meth:`process_hypothesis`).

        :param sql_generator: Callable taking the previous error (or ``None``
            on the first attempt) and returning a fresh SQL string.
        :param enriched_schema: Schema used for the hallucination check.
        :param executor: Callable that executes the final SQL and returns
            whatever the caller wants back. Defaults to :meth:`execute_sql`.
        :return: ``(final_sql, executor_result)``
        """
        executor = executor or self.execute_sql
        last_error: Optional[str] = None
        last_hallucinated: Optional[frozenset] = None

        for attempt in range(self.max_sql_retries + 1):
            sql = sql_generator(last_error)
            sql = self._normalize_identifier_case(sql, enriched_schema)

            hallucinated = self._find_hallucinated_columns(sql, enriched_schema)
            if hallucinated:
                current_set = frozenset(hallucinated)
                if current_set == last_hallucinated:
                    raise ValueError(
                        f"SQL generation stalled: repeated the same non-existent "
                        f"column(s) {sorted(current_set)} across consecutive attempts."
                    )
                last_hallucinated = current_set
                last_error = self._describe_hallucinated_columns(hallucinated, enriched_schema)
                continue

            try:
                result = executor(sql)
                return sql, result
            except Exception as exc:
                last_error = str(exc)
                if attempt == self.max_sql_retries:
                    raise
        raise RuntimeError("unreachable")

    def generate_view_sql(
        self,
        question: str,
        schema_details: Dict,
        view_name: str,
        previous_error: Optional[str] = None,
    ) -> str:
        """
        Generate a single ``CREATE OR REPLACE VIEW ... AS SELECT ...`` statement.

        This is how a manipulation-flavored question ("dedupe by X", "drop
        rows where Y is null", "aggregate by Z") gets turned into something
        the user can actually use: a read-only view over the existing
        tables, reshaped/filtered/aggregated by the SELECT, never a mutation
        of the tables themselves.
        """
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ExplorePromptType.GENERATE_VIEW_SQL
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=ExplorePromptType.GENERATE_VIEW_SQL,
            question=question,
            view_name=view_name,
            schema_summary=self.generate_schema_summary(schema_details),
            columns_details=self.generate_column_details(schema_details),
            previous_error=previous_error,
        )
        prompt = "\n\n".join([system_prompt, user_prompt])

        response = self.get_response(
            fn_name="generate_response",
            model_input={"user_prompt": prompt},
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER],
            preference_model_names=[
                "qwen-qwq-32b",
                "openai/gpt-oss-120b",
                "models/gemini-2.5-flash",
                "meta-llama/llama-3.3-70b-instruct:free",
            ],
            retry_attempt=1,
        )
        sql = (response or "").strip()
        sql = re.sub(r"^```sql\s*|```\s*$", "", sql, flags=re.IGNORECASE | re.MULTILINE).strip()
        return sql

    def process_view_request(
        self,
        question: str,
        schema_details: Dict,
        view_name: str,
        preview_limit: int = 200,
    ) -> Dict:
        """
        Create a named, non-destructive view answering *question* and return a
        preview of its contents.

        This is the mechanism behind "ask a question that manipulates data,
        get a view back": the request is turned into a
        ``CREATE OR REPLACE VIEW`` (never ``ALTER``/``UPDATE``/``DELETE`` on
        the source table), so the user gets a reshaped/filtered/aggregated
        result to reference in later questions without the original data
        source ever changing underneath them.

        :return: ``{"sql": str, "view_name": str, "data": pd.DataFrame,
            "observation": str, "error": Optional[str]}``. On failure ``data``
            is empty and ``error`` is set; the caller decides how to surface
            that (mirrors :meth:`process_hypothesis`'s error handling).
        """
        try:
            final_sql, _ = self._generate_and_execute_with_retry(
                sql_generator=lambda prev_err: self.generate_view_sql(
                    question, schema_details, view_name, prev_err
                ),
                enriched_schema=schema_details,
                executor=lambda sql: self.postgres_service.query(sql),
            )
        except Exception as exc:
            return {"sql": "", "view_name": view_name, "data": pd.DataFrame(), "observation": "", "error": str(exc)}

        try:
            df = self.execute_sql(f'SELECT * FROM "{view_name}" LIMIT {preview_limit}')
        except Exception as exc:
            return {
                "sql": final_sql, "view_name": view_name, "data": pd.DataFrame(),
                "observation": "", "error": f"View created but preview failed: {exc}",
            }

        observation = self._generate_view_observation(question, df, view_name)
        
        return {"sql": final_sql, "view_name": view_name, "data": df, "observation": observation, "error": None}


    def _generate_view_observation(
        self, 
        question: str, 
        df: pd.DataFrame, 
        view_name: str
    ) -> str:
        if df.empty:
            return f'View "{view_name}" was created but currently returns no rows.'
        
        sample = df.head(self.row_sample_for_observation).to_dict("records")
        prompt = textwrap.dedent(f"""
            A database view named "{view_name}" was created to answer: {question}

            Sample rows ({min(len(sample), self.row_sample_for_observation)} shown):
            {json.dumps(sample, default=str)}

            Write 1-3 plain-text sentences describing what the view contains,
            citing at least one concrete value. No markdown, no bullet points.
        """).strip()
        
        response = self.get_response(
            fn_name="generate_response",
            model_input={"user_prompt": prompt},
            strict=False,
            provider_order=[Provider.GEMINI, Provider.GROQ, Provider.OPENROUTER],
            preference_model_names=["models/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct:free"],
            retry_attempt=1,
        )

        return (response or f'View "{view_name}" was created.').strip()