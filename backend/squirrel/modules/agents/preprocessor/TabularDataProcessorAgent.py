#!/usr/bin/python
"""
Tabular Data Processor Agent
============================

Plans, executes, and summarises a two-phase preprocessing pipeline —
cleaning followed by feature engineering / encoding / normalisation — over
a pandas DataFrame.

Lifecycle
---------
1. ``plan``     — asks the LLM to choose and order cleaning + transformation
                  strategies, grounding decisions in an optional EDA summary.
2. ``execute``  — runs each planned step through the appropriate orchestrator,
                  captures outputs and errors, and assembles a structured report.
3. ``summarize``— asks the LLM to interpret the execution report and emit a
                  concise preprocessing summary with a before/after data profile.
4. ``run``      — convenience wrapper that chains all three stages. Now also
                  returns the raw ``plan``/``execution`` report alongside the
                  human-readable summary, so the caller can persist them as a
                  durable "fitted pipeline" artifact (see
                  ``WorkspaceService.save_pipeline_artifact``) and later replay
                  it against new data via :meth:`apply_fitted_pipeline`.

Multi-source support
---------------------
A workspace may hold more than one input source (several uploaded CSVs,
and/or one or more connected data sources). Before any of the four
lifecycle stages above run, callers with more than one source should first
call :meth:`TabularDataProcessorAgent.merge_sources` (or the
:meth:`run_multi` convenience wrapper) to fold them into a single
DataFrame — the rest of the pipeline is source-count agnostic by design,
so nothing else in this class changes for the multi-source case.

Sources don't always arrive with a schema simple enough to merge by
inspection alone. ``merge_sources`` tries cheap, deterministic heuristics
first (identical schema -> stack rows; same row count, disjoint columns ->
concat columns); when neither applies — e.g. two sources describe the same
entities through a shared key column the caller never told us about — it
falls back to asking the LLM to inspect each source's schema and a few
sample rows and propose a relationship (typically a join key), grounded in
the ``target_column`` being predicted and any free-text ``objective`` the
caller supplied. If the LLM can't find a defensible relationship, the
merge is refused with an explanation rather than guessing.

Inference-time replay
----------------------
``execute()``'s report captures, per completed step, exactly the
data-dependent parameters each strategy fit from the training frame (see
the ``fitted_state`` docs on ``TabularDataCleaner``/``TabularDataTransformer``).
:meth:`apply_fitted_pipeline` replays a saved plan + execution report
against a *new* DataFrame — same steps, same arguments, but every strategy
that has a fitted-state hook reuses its training-time parameters instead of
re-fitting from the (typically much smaller, sometimes single-row)
inference batch. This is the counterpart to ``execute()`` used by
``WorkspaceService.predict()``.
"""

# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
from copy import deepcopy
from typing import Any, Dict, Optional

# Third-Party Libraries
import pandas as pd

# Abstract Base Class
from squirrel.modules.agents.abstract import IAgent
from squirrel.modules.providers import Provider

# Orchestrators
from squirrel.modules.preprocessors.cleaning.tabular import (
    TabularDataCleaner,
    DataCleanStrategy,
)
from squirrel.modules.preprocessors.transformers.tabular import (
    TabularDataTransformer,
    DataTransformStrategy,
)

# Prompt generator
from squirrel.modules.prompts.preprocess.PreprocessPromptGenerator import (
    PreprocessPromptGenerator,
    PreprocessPromptType,
)

# Logging
from loguru import logger


# ——————————————————————————————————————————————————————————————
# Registry builder helpers

def _build_clean_registry() -> Dict[str, type[DataCleanStrategy]]:
    """
    Return the full cleaning strategy registry keyed by class name.
    Mirrors ``TabularDataCleaner._resolve`` but exposes the mapping for
    catalog building and argument validation.
    """
    return {cls.__name__: cls for cls in DataCleanStrategy.__subclasses__()}


def _build_transform_registry() -> Dict[str, type[DataTransformStrategy]]:
    """
    Return the full transformation strategy registry keyed by class name.
    """
    return {cls.__name__: cls for cls in DataTransformStrategy.__subclasses__()}


def build_cleaning_catalog(
    base: type[DataCleanStrategy] = DataCleanStrategy,
) -> list[Dict]:
    """
    Dynamically build the cleaning catalog from all registered
    DataCleanStrategy subclasses by calling ``to_catalog_entry()`` on each.
    """
    return [cls.to_catalog_entry() for cls in base.__subclasses__()]


def build_transform_catalog(
    base: type[DataTransformStrategy] = DataTransformStrategy,
) -> list[dict]:
    """
    Dynamically build the transformation catalog from all registered
    DataTransformStrategy subclasses.
    """
    return [cls.to_catalog_entry() for cls in base.__subclasses__()]


# ——————————————————————————————————————————————————————————————
# Agent

class TabularDataProcessorAgent(IAgent):
    """
    Agent for cleaning and transforming tabular datasets.

    **Description:**

        The agent accepts an optional EDA summary (from ``TabularDataInspectorAgent``)
        to ground its preprocessing plan in concrete findings rather than relying on
        generic heuristics.

    Lifecycle:
        1. ``plan``     — asks the LLM to build an ordered preprocessing plan.
        2. ``execute``  — runs each planned step and collects structured outputs.
        3. ``summarize``— asks the LLM to interpret results into a concise report.
        4. ``run``      — convenience wrapper chaining all three stages.

    """

    # ── Strategy registries ───────────────────────────────────────────────────

    # Class-name → strategy class  (used for argument validation)
    _CLEAN_REGISTRY: dict[str, type[DataCleanStrategy]] = _build_clean_registry()
    _TRANSFORM_REGISTRY: dict[str, type[DataTransformStrategy]] = _build_transform_registry()

    # Unified view: class name → "cleaning" | "transform"
    _ALL_REGISTRY: dict[str, str] = {
        **{name: "cleaning"  for name in _CLEAN_REGISTRY},
        **{name: "transform" for name in _TRANSFORM_REGISTRY},
    }

    # Reverse maps: class name → short orchestrator key
    # e.g. "ClipOutliersCleanStrategy" → "clip_outliers"

    # TabularDataCleaner and TabularDataTransformer store their registries as
    # local variables inside _resolve(), not as class attributes, so we cannot
    # introspect them at import time.  We hardcode the same mapping here,
    # mirroring the orchestrators' own dicts exactly.
    _CLEAN_KEY_MAP: dict[str, str] = {
        "DropDuplicatesCleanStrategy":      "drop_duplicates",
        "DropColumnsCleanStrategy":         "drop_columns",
        "ImputeMissingValuesCleanStrategy": "impute_missing",
        "CastDtypesCleanStrategy":          "cast_dtypes",
        "RenameColumnsCleanStrategy":       "rename_columns",
        "ClipOutliersCleanStrategy":        "clip_outliers",
        "FilterRowsCleanStrategy":          "filter_rows",
        "ScaleNumericCleanStrategy":        "scale_numeric",
    }
    _TRANSFORM_KEY_MAP: dict[str, str] = {
        "BinNumericTransformStrategy":          "bin_numeric",
        "DatetimePartsTransformStrategy":       "datetime_parts",
        "InteractionTermsTransformStrategy":    "interaction_terms",
        "PolynomialFeaturesTransformStrategy":  "polynomial_features",
        "AggregationFeaturesTransformStrategy": "aggregation_features",
        "RatioFeaturesTransformStrategy":       "ratio_features",
        "OneHotEncodeTransformStrategy":        "one_hot_encode",
        "OrdinalEncodeTransformStrategy":       "ordinal_encode",
        "TargetEncodeTransformStrategy":        "target_encode",
        "FrequencyEncodeTransformStrategy":     "frequency_encode",
        "BinaryEncodeTransformStrategy":        "binary_encode",
        "StandardScaleTransformStrategy":       "standard_scale",
        "MinMaxScaleTransformStrategy":         "minmax_scale",
        "RobustScaleTransformStrategy":         "robust_scale",
        "LogTransformTransformStrategy":        "log_transform",
        "PowerTransformTransformStrategy":      "power_transform",
    }

    def __init__(
        self,
        prompt_generator: Any = None,
    ) -> None:
        super().__init__(
            prompt_generator=prompt_generator or PreprocessPromptGenerator()
        )

    # ── Multi-source merge ───────────────────────────────────────────────────
    #
    # Requirement: a workspace can hold several input sources (multiple
    # uploads and/or connected data sources), and those sources won't always
    # share a schema simple enough to merge by inspection. Everything
    # downstream — plan / execute / summarize, and the model builder after
    # it — operates on a single DataFrame, so multi-source workspaces are
    # folded into one frame *before* the pipeline starts. This is the one
    # place that knows about "more than one source, possibly related in a
    # non-obvious way"; the rest of the class (and
    # TabularDataModelBuilderAgent) stays unchanged and simply receives
    # whatever single DataFrame it's given, whether that came from one
    # source, several identical-schema sources, or an LLM-inferred join.

    @staticmethod
    def detect_column_conflicts(
        dataframes: Dict[str, pd.DataFrame],
        target_column: str | None = None,
    ) -> list[str]:
        """
        Return the list of column names that appear in more than one of
        *dataframes*, excluding ``target_column`` (the predicted column is
        expected to live in exactly one source and is validated separately).

        This is used as a fast, deterministic signal — not a hard veto — for
        whether a column-wise merge would be ambiguous. See
        :meth:`merge_sources` for how it's used.

        :param dataframes: Mapping of source_id -> DataFrame, as returned by
            ``WorkspaceService.load_source_dataframes``.
        :param target_column: Column the user picked to predict, if any;
            excluded from the conflict check since the target is allowed (and
            expected) to be named the same as feature columns in spirit, but
            must exist in exactly one source — that's enforced by the caller.
        :return: Sorted list of conflicting column names. Empty if none.
        """
        seen: dict[str, int] = {}
        for df in dataframes.values():
            for col in df.columns:
                if col == target_column:
                    continue
                seen[col] = seen.get(col, 0) + 1
        return sorted(col for col, count in seen.items() if count > 1)


    @classmethod
    def merge_sources(
        cls,
        dataframes: Dict[str, pd.DataFrame],
        target_column: str | None = None,
        objective: str = "",
        agent: "TabularDataProcessorAgent | None" = None,
        merge_recommendation: Dict[str, Any] | None = None
    ) -> pd.DataFrame:
        """
        Fold two or more source DataFrames into a single DataFrame ready for
        the rest of the preprocessing pipeline.

        Merge strategy:
            - A single source is returned as-is (no-op — covers the common
              single-CSV workspace).
            - Multiple sources with an identical column set and no conflicts
              are merged row-wise (``pd.concat(axis=0)``) — the common case
              of several batches/exports of the same schema (e.g. monthly
              extracts).
            - Multiple sources with the same row count, disjoint columns,
              and no name conflicts are merged column-wise
              (``pd.concat(axis=1)``), on the assumption that row *i* in
              every source describes the same entity (e.g. one CSV of
              customer demographics and another of customer usage stats,
              both ordered by the same customer list).
            - Anything else — different schemas that don't obviously stack
              or align, and/or column-name collisions — is handed to
              :meth:`_merge_via_agent`, which asks the LLM to inspect each
              source's schema/sample rows and infer a relationship (most
              commonly a shared join key), grounded in ``target_column`` and
              ``objective``. If the LLM can't find a defensible
              relationship, the merge is refused with an explanation.

        :param objective: Optional free-text context from the caller (e.g.
            "join the two sources on customer_id", or a description of what
            the eventual model should optimize for). Passed to the LLM
            fallback path only — it doesn't affect the deterministic
            heuristics above.
        :param agent: Optional existing agent instance to reuse for the LLM
            fallback path (avoids constructing a second one when called from
            :meth:`run_multi`). A fresh instance is created if omitted.
        :raises DuplicateColumnError: if a column-wise merge would collide
            on a non-target column name and the LLM fallback can't resolve it.
        :raises ValueError: if no defensible merge strategy applies, including
            when the LLM fallback explicitly rejects the sources as unrelated.
        """
        from squirrel.services.workspace.WorkspaceService import DuplicateColumnError

        if not dataframes:
            raise ValueError("No input sources to merge.")
        if len(dataframes) == 1:
            return next(iter(dataframes.values())).copy()

        conflicts = cls.detect_column_conflicts(dataframes, target_column=target_column)
        row_counts = {len(df) for df in dataframes.values()}
        column_sets = [frozenset(df.columns) for df in dataframes.values()]
        same_schema = len(set(column_sets)) == 1

        # Deterministic fast paths unchanged (identical schema -> union;
        # same row count / disjoint columns -> column concat) ...
        if same_schema and not conflicts:
            return pd.concat(list(dataframes.values()), axis=0, ignore_index=True)
        if not conflicts and len(row_counts) == 1:
            aligned = [df.reset_index(drop=True) for df in dataframes.values()]
            return pd.concat(aligned, axis=1)

        # NEW: if the Inspector already reasoned about the relationship
        # (grounded in the user's query), act on it directly instead of
        # spending a second LLM call re-deriving the same decision.
        if merge_recommendation:
            strategy = merge_recommendation.get("strategy")
            rationale = merge_recommendation.get("rationale", "")
            if strategy == "union":
                logger.info("Merging via Inspector recommendation: union. %s", rationale)
                return pd.concat(list(dataframes.values()), axis=0, ignore_index=True)
            if strategy == "join":
                try:
                    return cls._execute_join_plan(dataframes, {
                        "join_keys": merge_recommendation.get("join_keys"),
                        "join_type": merge_recommendation.get("join_type", "inner"),
                        "rationale": rationale,
                    })
                except _AgentMergeRejected:
                    pass  # fall through to the agent-based fallback below
            if strategy == "unrelated":
                if conflicts:
                    raise DuplicateColumnError(conflicts)
                raise ValueError(f"Sources rejected as unrelated by inspection: {rationale}")

        # Fallback: no usable recommendation was supplied — ask the LLM here,
        # same as before.
        merger = agent or cls()
        try:
            return merger._merge_via_agent(dataframes, target_column=target_column, objective=objective)
        except _AgentMergeRejected as exc:
            if conflicts:
                raise DuplicateColumnError(conflicts) from exc
            raise ValueError(str(exc)) from exc


    def run_multi(
        self,
        dataframes: Dict[str, pd.DataFrame],
        target_column: str | None = None,
        eda_summary: dict | None = None,
        objective: str = "",
        merge_recommendation: Dict[str, Any] | None = None,   # NEW
        max_refinements: int = 1,
    ) -> tuple[pd.DataFrame, dict, str, str]:
        """
        Multi-source convenience wrapper: merge every source into one
        DataFrame (see :meth:`merge_sources`), then run the normal
        plan → execute → summarize pipeline on the result.

        :param dataframes: Mapping of source_id -> DataFrame.
        :param target_column: Column the user chose to predict; used both to
            exclude it from the duplicate-column check (see
            :meth:`detect_column_conflicts`) and to ground the LLM's
            relationship-finding when sources need to be joined.
        :param objective: Optional free-text instruction describing how the
            sources relate and/or what the model should optimize for.
            Forwarded to :meth:`merge_sources`'s LLM fallback, and to the
            preprocessing plan/summarize stages as the run's objective.
        :return: ``(processed_df, summary, plan_json, execution_report_json)``
            — the last two are the raw plan and execution report, returned
            so the caller can persist them as the "fitted pipeline" artifact
            needed by :meth:`apply_fitted_pipeline` at inference time.
        :raises DuplicateColumnError: propagated from :meth:`merge_sources`.
        :raises ValueError: propagated from :meth:`merge_sources` when no
            relationship between sources could be established.
        """
        merged = self.merge_sources(
            dataframes,
            target_column=target_column,
            objective=objective,
            agent=self,
            merge_recommendation=merge_recommendation,
        )
        processed_df, summary, plan, execution_report = self.run(
            merged,
            eda_summary=eda_summary,
            objective=objective,
            max_refinements=max_refinements,
            target_column=target_column
        )
        summary["n_sources"] = len(dataframes)
        if merge_recommendation:
            summary["merge_strategy_used"] = merge_recommendation.get("strategy")
        return processed_df, summary, plan, execution_report

    # ── LLM-grounded source relationship finding ─────────────────────────────

    def _merge_via_agent(
        self,
        dataframes: Dict[str, pd.DataFrame],
        target_column: str | None,
        objective: str,
    ) -> pd.DataFrame:
        """
        Ask the LLM to inspect every source's schema and propose how they
        relate, then execute that plan.

        :raises _AgentMergeRejected: if the LLM explicitly can't find a
            defensible relationship, or its response can't be parsed/used.
        """
        profiles = {
            source_id: {
                "row_count": int(df.shape[0]),
                "columns": list(df.columns),
                "dtypes": {c: str(t) for c, t in df.dtypes.items()},
                "sample_rows": df.head(3).where(pd.notnull(df.head(3)), None).to_dict(orient="records"),
            }
            for source_id, df in dataframes.items()
        }

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=PreprocessPromptType.GENERATE_PLAN,
        )
        user_prompt = (
            "Several input sources must be combined into a single training "
            "frame before preprocessing can continue, but they don't share "
            "an identical schema and can't simply be stacked or aligned by "
            "position. Inspect each source's columns, dtypes, and sample "
            "rows below and decide how they relate.\n\n"
            f"Target column to predict: {target_column!r}\n"
            f"User-provided context/objective: {objective or '(none given)'}\n\n"
            "Choose exactly one strategy:\n"
            "  - 'join': the sources describe the same entities and share "
            "one or more key columns (e.g. a customer id) that a pandas "
            "merge can use to combine them.\n"
            "  - 'concat_rows': despite schema differences (e.g. extra/"
            "missing optional columns), the sources are really batches of "
            "the same kind of record and should be stacked.\n"
            "  - 'reject': no defensible relationship can be inferred from "
            "the schemas/samples given; explain why in 'rationale'.\n\n"
            f"Source profiles:\n{json.dumps(profiles, indent=2, default=str)}"
        )

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._merge_plan_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash"
            ]
        )

        if response is None:
            raise _AgentMergeRejected(
                "Could not determine how to merge input sources: the "
                "planning provider did not return a response."
            )

        try:
            plan = response if isinstance(response, dict) else json.loads(response)
        except (TypeError, json.JSONDecodeError) as exc:
            raise _AgentMergeRejected(
                f"Could not parse the merge plan returned by the provider: {exc}"
            )

        strategy = plan.get("strategy")
        rationale = plan.get("rationale") or "no rationale given."

        if strategy == "reject":
            raise _AgentMergeRejected(
                f"Could not merge input sources: {rationale}"
            )

        if strategy == "concat_rows":
            logger.info("Agent chose to concat sources row-wise: %s", rationale)
            return pd.concat(list(dataframes.values()), axis=0, ignore_index=True)

        if strategy == "join":
            return self._execute_join_plan(dataframes, plan)

        raise _AgentMergeRejected(
            f"Agent returned an unrecognised merge strategy: {strategy!r}"
        )

    @staticmethod
    def _execute_join_plan(
        dataframes: Dict[str, pd.DataFrame],
        plan: dict,
    ) -> pd.DataFrame:
        """
        Execute a ``strategy == "join"`` merge plan by sequentially merging
        every source on the join key(s) the LLM identified.
        """
        join_keys = plan.get("join_keys") or []
        join_type = plan.get("join_type") or "inner"
        if join_type not in ("inner", "left", "right", "outer"):
            join_type = "inner"
        if not join_keys:
            raise _AgentMergeRejected("Agent proposed a join but specified no join_keys.")

        ordered_ids = list(dataframes.keys())
        merged = dataframes[ordered_ids[0]].copy()

        for source_id in ordered_ids[1:]:
            other = dataframes[source_id]
            missing = [
                k for k in join_keys
                if k not in merged.columns or k not in other.columns
            ]
            if missing:
                raise _AgentMergeRejected(
                    f"Agent proposed joining on {join_keys!r}, but column(s) "
                    f"{missing} are missing from one of the sources."
                )
            merged = merged.merge(
                other,
                on=join_keys,
                how=join_type,
                suffixes=("", f"_{source_id}"),
            )

        logger.info(
            "Merged %d sources via LLM-inferred join on %s (how=%s): %s",
            len(dataframes), join_keys, join_type, plan.get("rationale", ""),
        )
        return merged

    @staticmethod
    def _merge_plan_schema() -> Dict:
        return {
            "type": "object",
            "properties": {
                "strategy": {"type": "string", "enum": ["join", "concat_rows", "reject"]},
                "join_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Shared column name(s) present in every source to join on, when strategy == 'join'.",
                },
                "join_type": {"type": "string", "enum": ["inner", "left", "right", "outer"]},
                "rationale": {"type": "string"},
            },
            "required": ["strategy", "rationale"],
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        data: pd.DataFrame,
        eda_summary: dict | None = None,
        objective: str = "",
        target_column: str | None = None,
        max_refinements: int = 1,
    ) -> tuple[pd.DataFrame, dict, str, str]:
        """
        :param target_column: The column the downstream model will predict, if
            known. When given, the planner is told to preserve it and any step
            that would drop it is defensively stripped/skipped in execute() —
            preprocessing has no other way to know a column it judges
            "redundant" (e.g. highly collinear with another feature) is
            actually the label, and losing it here surfaces only much later
            as a hard failure in the model-builder stage.
        :return: ``(processed_df, summary_dict, plan_json, execution_report_json)``.
            ``plan_json`` and ``execution_report_json`` are the plan/execution
            report from whichever attempt was selected as ``best`` — this is
            the pair to persist (e.g. via
            ``WorkspaceService.save_pipeline_artifact``) if you want to be
            able to replay this exact preprocessing against new data later
            via :meth:`apply_fitted_pipeline`.
        """
        plan = self.plan(data, eda_summary=eda_summary or {}, objective=objective, target_column=target_column)
        processed_df, execution = self.execute(data, plan, target_column=target_column)
        summary_dict = self._parse_summary(self.summarize(data, processed_df, execution))

        best_df, best_summary, best_plan, best_execution = processed_df, summary_dict, plan, execution
        attempt = 0
        while attempt < max_refinements and self._needs_refinement(best_summary):
            attempt += 1
            feedback = self._refinement_feedback(best_summary)
            logger.info("Preprocessing refinement attempt %s: %s", attempt, feedback)

            plan = self.plan(
                data, eda_summary=eda_summary or {},
                objective=f"{objective}\n\n{feedback}".strip(),
                target_column=target_column,
            )
            candidate_df, execution = self.execute(data, plan, target_column=target_column)
            candidate_summary = self._parse_summary(self.summarize(data, candidate_df, execution))

            if self._is_better(candidate_summary, best_summary):
                best_df, best_summary = candidate_df, candidate_summary
                best_plan, best_execution = plan, execution

        best_summary["refinement_attempts"] = attempt
        return best_df, best_summary, best_plan, best_execution

    def plan(
        self,
        data: pd.DataFrame,
        eda_summary: dict | None = None,
        objective: str = "",
        target_column: str | None = None,
    ) -> str:
        """
        Ask the LLM to build an ordered preprocessing plan.

        :param data: Input DataFrame.
        :param eda_summary: Optional structured EDA findings.
        :param objective: Optional caller-supplied goal.
        :param target_column: The column the downstream model will predict, if
            known. Passed through to the prompt so the planner doesn't drop,
            encode-away, or otherwise destroy it while cleaning "redundant"
            columns (e.g. a feature highly correlated with the target itself).
        :return: JSON plan string.
        """
        dataset_profile = self._dataset_profile(data)

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=PreprocessPromptType.GENERATE_PLAN,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=PreprocessPromptType.GENERATE_PLAN,
            objective=objective or (
                "Clean and transform the dataset to prepare it for downstream modelling."
            ),
            dataset_profile=dataset_profile,
            eda_summary=eda_summary or {},
            cleaning_catalog=build_cleaning_catalog(),
            transform_catalog=build_transform_catalog(),
            target_column=target_column,
        )

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._plan_schema(),
            },
            strict=False,
            provider_order=[Provider.GEMINI, Provider.GROQ],
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash"
            ]
        )

        if response is None:
            return self._error_json("plan", "Provider did not return a response.")

        plan_obj = self._parse_plan(response) if isinstance(response, str) else response
        if "error" in plan_obj:
            return self._error_json("plan", plan_obj.get("error", "Unknown error parsing plan."))

        plan_obj = self._validate_and_repair_plan(plan_obj, dataset_profile)
        return json.dumps(plan_obj, indent=2)

    def execute(
        self,
        data: pd.DataFrame,
        plan: str | dict,
        target_column: str | None = None,
    ) -> tuple[pd.DataFrame, str]:
        """
        Execute the preprocessing plan produced by ``plan()`` in "fit" mode:
        every strategy computes its parameters (means, bounds, encoding
        maps, ...) fresh from *data*. This is always the training-time path.

        Cleaning steps are dispatched through ``TabularDataCleaner`` and
        transformation steps through ``TabularDataTransformer``.  Unknown or
        failed steps are recorded in the report rather than raising, so the
        pipeline always produces a complete result.

        :param data: Input DataFrame.
        :param plan: JSON plan string or dict from ``plan()``.
        :param target_column: The column the downstream model will predict, if
            known. Prompting the planner to preserve it (see :meth:`plan`) is
            not sufficient on its own — the LLM can and does drop it anyway
            (e.g. judging it "redundant" against a near-duplicate feature it
            doesn't recognise as the label). Every step is defensively
            checked here and the target column is stripped out of any
            drop-columns arguments before execution, independent of what the
            plan says.
        :return: ``(processed_df, json_execution_report_string)``
        """
        plan_obj = self._parse_plan(plan)
        if "error" in plan_obj:
            return data, json.dumps(
                {"task": "preprocessing", "status": "failed", **plan_obj}, indent=2
            )

        cleaner = TabularDataCleaner("drop_duplicates")   # seed with any valid strategy
        transformer = TabularDataTransformer("standard_scale")

        current_df = data.copy()
        executed_steps: list[dict[str, Any]] = []

        for step in plan_obj.get("steps", []):
            step, skip_result = self._protect_target_column(step, target_column)
            if skip_result is not None:
                executed_steps.append(skip_result)
                continue

            current_df, step_result = self._run_step(
                current_df, step, cleaner, transformer, fitted_state=None
            )
            executed_steps.append(step_result)

        report = self._execution_report(data, current_df, executed_steps)

        return current_df, json.dumps(report, indent=2, default=str)

    def apply_fitted_pipeline(
        self,
        new_data: pd.DataFrame,
        execution_report: str | dict,
    ) -> pd.DataFrame:
        """
        Inference-time counterpart to :meth:`execute`: replay a previously
        completed preprocessing run against *new_data*, reusing each step's
        already-fitted parameters instead of recomputing them.

        This walks ``execution_report["steps"]`` in the same order they ran
        at training time. For each step whose original status was
        ``"completed"``:
          - The same strategy + arguments are re-applied to *new_data*.
          - That step's recorded ``per_column`` block (the fitted state —
            means, bounds, encoding maps, fitted λ, dummy-column sets, ...)
            is passed through as ``fitted_state``, so every strategy with a
            data-dependent parameter reuses the training-time value instead
            of re-fitting from *new_data* (which, at inference time, may be
            a single row — meaningless to "fit" anything from).
        Steps that were ``"failed"`` or ``"skipped"`` during training are
        skipped here too, since they never touched the training frame either.

        :param new_data: Raw new data to transform into the same feature
            space the model was trained on.
        :param execution_report: The JSON execution report string or dict
            returned by :meth:`execute` (or, equivalently,
            ``run()``'s fourth return value) during training. This is what
            ``WorkspaceService.save_pipeline_artifact`` persists and
            ``WorkspaceService.predict`` loads back.
        :return: *new_data* transformed through the identical pipeline the
            training data went through, ready to feed to the fitted model
            (after dropping the target column, if present).
        :raises ValueError: if *execution_report* can't be parsed.
        """
        report_obj = (
            execution_report if isinstance(execution_report, dict)
            else json.loads(execution_report)
        )
        if report_obj.get("status") == "failed":
            raise ValueError(
                f"Cannot replay a failed preprocessing run: {report_obj.get('error')}"
            )

        cleaner = TabularDataCleaner("drop_duplicates")
        transformer = TabularDataTransformer("standard_scale")

        current_df = new_data.copy()

        for step in report_obj.get("steps", []):
            if step.get("status") != "completed":
                # This step never touched the training frame either
                # (unknown strategy / missing args / runtime failure) — skip
                # it identically at inference time.
                continue

            strategy_name = step.get("strategy", "")
            strategy_type = step.get("strategy_type", self._ALL_REGISTRY.get(strategy_name, ""))
            arguments = step.get("arguments") or {}
            fitted_state = (step.get("output") or {}).get("per_column") or {}

            fabricated_step = {
                "step": step.get("step"),
                "name": step.get("name"),
                "strategy": strategy_name,
                "strategy_type": strategy_type,
                "objective": step.get("objective"),
                "arguments": arguments,
            }

            current_df, step_result = self._run_step(
                current_df, fabricated_step, cleaner, transformer, fitted_state=fitted_state
            )
            if step_result.get("status") == "completed":
                per_col_errors = {
                    c: v.get("error") for c, v in (step_result.get("output") or {}).get("per_column", {}).items()
                    if isinstance(v, dict) and v.get("error")
                }
                if per_col_errors:
                    raise ValueError(
                        f"Replay of step '{step.get('name')}' had per-column failures: {per_col_errors}"
                    )

        return current_df

    def summarize(
        self,
        original_df: pd.DataFrame,
        processed_df: pd.DataFrame,
        execution_report: str | dict,
    ) -> str:
        """
        Ask the LLM to interpret the execution report and return a structured
        preprocessing summary.

        :param original_df: The unprocessed input DataFrame.
        :param processed_df: The DataFrame after all steps have been applied.
        :param execution_report: JSON execution report from ``execute()``.
        :return: JSON summary string.
        """
        if isinstance(execution_report, str):
            try:
                report_obj = json.loads(execution_report)
            except json.JSONDecodeError:
                report_obj = {"raw": execution_report}
        else:
            report_obj = dict(execution_report)

        # Attach before/after profiles for the LLM to compare.
        report_obj["before_profile"] = self._dataset_profile(original_df)
        report_obj["after_profile"] = self._dataset_profile(processed_df)

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=PreprocessPromptType.GENERATE_PLAN,
        )

        # Reuse the plan system prompt for summarization — both require careful
        # grounding in concrete data.  A dedicated GENERATE_SUMMARY type can be
        # added to ExplorePromptType if richer control is needed later.
        user_prompt = (
            "You are interpreting a completed preprocessing execution report.\n\n"
            "Produce a structured JSON summary with:\n"
            "  - overall_assessment  (one paragraph describing what was done)\n"
            "  - steps_completed     (integer)\n"
            "  - steps_failed        (integer)\n"
            "  - columns_added       (list of new column names)\n"
            "  - columns_dropped     (list of dropped column names)\n"
            "  - quality_improvements (list of strings describing key improvements)\n"
            "  - warnings            (list of steps that failed or were skipped)\n\n"
            f"Execution report:\n{json.dumps(report_obj, indent=2, default=str)}"
        )

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._summary_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash"
            ]
        )

        if response is None:
            return self._error_json("summarize", "Provider did not return a response.")
        return response if isinstance(response, str) else json.dumps(response, indent=2)

    # ── Plan validation & repair ──────────────────────────────────────────────

    _MAX_REGEN_ATTEMPTS: int = 2

    def _validate_and_repair_plan(
        self, 
        plan_obj: dict, 
        dataset_profile: dict
    ) -> dict:
        """
        Walk through every step in *plan_obj*, validate it, and attempt
        regeneration for any step that references an unknown strategy or is
        missing required arguments.

        Mirrors the validation loop in TabularDataInspectorAgent.plan().
        """
        # Accept both class names and short orchestrator keys in plan steps.
        available_strategies = set(self._ALL_REGISTRY.keys()) | set(self._CLEAN_KEY_MAP.values()) | set(self._TRANSFORM_KEY_MAP.values())

        for idx, step in enumerate(plan_obj.get("steps", [])):
            reason = self._validate_step(step, available_strategies)

            if reason is None:
                plan_obj["steps"][idx]["status"] = "valid"
                continue

            plan_obj["steps"][idx]["status"] = "invalid"
            plan_obj["steps"][idx]["error"] = reason
            logger.info("Plan step %s needs regeneration: %s", idx, reason)

            regenerated = False
            for attempt in range(self._MAX_REGEN_ATTEMPTS):
                regen_resp = self._regenerate_step(
                    step=step,
                    reason=reason,
                    dataset_profile=dataset_profile,
                )
                if regen_resp is None:
                    continue

                try:
                    new_step = (
                        regen_resp
                        if isinstance(regen_resp, dict)
                        else json.loads(regen_resp)
                    )
                except Exception:
                    continue

                # Unwrap if the LLM wrapped the step in a plan envelope.
                if (
                    isinstance(new_step, dict)
                    and "steps" in new_step
                    and isinstance(new_step["steps"], list)
                ):
                    new_step = new_step["steps"][0] if new_step["steps"] else new_step

                if not isinstance(new_step, dict):
                    continue

                recheck = self._validate_step(new_step, available_strategies)
                if recheck is None:
                    plan_obj["steps"][idx] = new_step
                    logger.info("Successfully regenerated step %s", idx)
                    regenerated = True
                    break

            if not regenerated:
                logger.warning(
                    "Failed to regenerate valid step %s after %s attempts",
                    idx,
                    self._MAX_REGEN_ATTEMPTS,
                )
                plan_obj["steps"][idx]["status"] = "invalid"
                plan_obj["steps"][idx]["error"] = (
                    f"Failed to regenerate after {self._MAX_REGEN_ATTEMPTS} attempts: {reason}"
                )

        return plan_obj

    def _validate_step(
        self,
        step: dict,
        available_strategies: set[str],
    ) -> str | None:
        """
        Validate a single plan step.

        Accepts both short orchestrator keys (``clip_outliers``) and full class
        names (``ClipOutliersCleanStrategy``); normalises to class name for the
        registry lookup so argument-spec validation always finds the right class.

        :return: Error reason string if invalid, ``None`` if valid.
        """
        raw_name: str = str(step.get("strategy", ""))

        # Normalise: if a short key was given, map it to the class name.
        # Build a reverse of the key maps once per call (cheap, only ~16 entries each).
        short_to_class: dict[str, str] = {
            **{v: k for k, v in self._CLEAN_KEY_MAP.items()},
            **{v: k for k, v in self._TRANSFORM_KEY_MAP.items()},
        }
        strategy_name = short_to_class.get(raw_name, raw_name)

        if strategy_name not in available_strategies:
            return f"Unknown strategy '{raw_name}'"

        strategy_type = self._ALL_REGISTRY[strategy_name]
        cls = (
            self._CLEAN_REGISTRY.get(strategy_name)
            if strategy_type == "cleaning"
            else self._TRANSFORM_REGISTRY.get(strategy_name)
        )

        if cls is None:
            return f"Strategy '{strategy_name}' not found in registry"

        required_specs = [
            s for s in getattr(cls, "argument_specs", [])
            if getattr(s, "required", False)
        ]
        arguments: dict = step.get("arguments") or {}
        missing = [s.name for s in required_specs if s.name not in arguments]
        if missing:
            return f"Missing required arguments: {', '.join(missing)}"

        return None

    def _regenerate_step(
        self,
        step: dict,
        reason: str,
        dataset_profile: dict,
    ) -> dict | None:
        """
        Ask the LLM to fix a single invalid plan step.
        """
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=PreprocessPromptType.GENERATE_STEP,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=PreprocessPromptType.GENERATE_STEP,
            objective=(
                f"Regenerate this step and fix the issue: {reason}. "
                "Return a single step object matching the step schema."
            ),
            dataset_profile=dataset_profile,
            step=step,
            cleaning_catalog=build_cleaning_catalog(),
            transform_catalog=build_transform_catalog(),
        )

        return self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._step_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-2.5-flash"
            ]
        )

    # ── Step execution ────────────────────────────────────────────────────────

    def _run_step(
        self,
        data: pd.DataFrame,
        step: dict[str, Any],
        cleaner: TabularDataCleaner,
        transformer: TabularDataTransformer,
        fitted_state: Optional[dict] = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Execute a single plan step and return ``(updated_df, step_result_dict)``.

        Routing logic:
            - ``strategy_type == "cleaning"``   → TabularDataCleaner
            - ``strategy_type == "transform"``  → TabularDataTransformer
            - strategy name found in clean registry but not type specified → cleaning
            - strategy name found in transform registry                     → transform

        :param fitted_state: Optional ``{column: {...params...}}`` to pass
            through to the strategy — ``None`` (the default) means "fit mode"
            (used by :meth:`execute`, i.e. training); a supplied dict means
            "apply mode" (used by :meth:`apply_fitted_pipeline`, i.e.
            inference). Strategies with no data-dependent parameters ignore
            this argument either way.
        """
        strategy_name: str = str(step.get("strategy", ""))
        strategy_type: str = str(
            step.get("strategy_type", self._ALL_REGISTRY.get(strategy_name, ""))
        )

        base: dict[str, Any] = {
            "step":            step.get("step"),
            "name":            step.get("name"),
            "strategy":        strategy_name,
            "strategy_type":   strategy_type,
            "objective":       step.get("objective"),
            "actions":         step.get("actions", []),
            "expected_output": step.get("expected_output", []),
            "arguments":       step.get("arguments") or {},
        }

        # ── Unknown strategy ──────────────────────────────────────────────────
        if strategy_name not in self._ALL_REGISTRY:
            logger.warning("Unknown strategy: %s", strategy_name)
            return data, {**base, "status": "skipped", "error": f"Unknown strategy: {strategy_name}"}

        arguments: dict = step.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        # ── Validate required arguments before execution ──────────────────────
        if strategy_type == "cleaning":
            cls = self._CLEAN_REGISTRY.get(strategy_name)
        else:
            cls = self._TRANSFORM_REGISTRY.get(strategy_name)

        if cls is None:
            return data, {**base, "status": "skipped", "error": f"Strategy class not found: {strategy_name}"}

        required_specs = [
            s for s in getattr(cls, "argument_specs", [])
            if getattr(s, "required", False)
        ]
        missing_required = [s.name for s in required_specs if s.name not in arguments]
        if missing_required:
            logger.warning(
                "Strategy %s missing required arguments: %s",
                strategy_name, missing_required,
            )
            return data, {
                **base,
                "status": "failed",
                "error": f"Missing required arguments: {', '.join(missing_required)}",
            }

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            if strategy_type == "cleaning":
                # Accept both short key ("clip_outliers") and class name
                # ("ClipOutliersCleanStrategy") — translate to short key for the orchestrator.
                orchestrator_key = self._CLEAN_KEY_MAP.get(strategy_name, strategy_name)
                cleaner.set_strategy(orchestrator_key)
                updated_df, report = cleaner.execute_strategy(data, fitted_state=fitted_state, **arguments)
            else:
                orchestrator_key = self._TRANSFORM_KEY_MAP.get(strategy_name, strategy_name)
                transformer.set_strategy(orchestrator_key)
                updated_df, report = transformer.execute_strategy(data, fitted_state=fitted_state, **arguments)

            return updated_df, {
                **base,
                "status": "completed",
                "output": self._serialize(report),
            }

        except Exception as exc:
            logger.exception("Strategy %s failed: %s", strategy_name, exc)
            return data, {**base, "status": "failed", "error": str(exc)}

    # ── Report assembly ───────────────────────────────────────────────────────

    def _execution_report(
        self,
        original_df: pd.DataFrame,
        processed_df: pd.DataFrame,
        executed_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Assemble the top-level execution report dict.
        """
        counts: dict[str, int] = {"completed": 0, "failed": 0, "skipped": 0}
        all_new_columns: list[str] = []
        all_dropped_columns: list[str] = []

        for step in executed_steps:
            status = step.get("status", "skipped")
            if status in counts:
                counts[status] += 1
            output = step.get("output") or {}
            all_new_columns.extend(output.get("new_columns") or [])
            all_dropped_columns.extend(output.get("dropped_columns") or [])
            all_dropped_columns.extend(output.get("columns_affected") or [])

        overall = "completed" if counts["failed"] == 0 else "completed_with_errors"

        return {
            "task": "preprocessing",
            "status": overall,
            "dataset_before": self._dataset_profile(original_df),
            "dataset_after": self._dataset_profile(processed_df),
            "summary": {
                "completed_steps":  counts["completed"],
                "failed_steps":     counts["failed"],
                "skipped_steps":    counts["skipped"],
                "columns_added":    list(dict.fromkeys(all_new_columns)),
                "columns_dropped":  list(dict.fromkeys(all_dropped_columns)),
            },
            "steps": executed_steps,
        }

    # ── Dataset profile ───────────────────────────────────────────────────────

    @staticmethod
    def _dataset_profile(
        data: pd.DataFrame
    ) -> dict[str, Any]:
        """
        Lightweight dataset descriptor sent to the planner and summarizer.
        """
        return {
            "rows":           int(data.shape[0]),
            "columns":        int(data.shape[1]),
            "column_names":   list(data.columns),
            "dtypes":         {col: str(dtype) for col, dtype in data.dtypes.items()},
            "missing_counts": {
                col: int(n)
                for col, n in data.isna().sum().items()
                if n > 0
            },
        }

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _serialize(value: Any) -> Any:
        """Recursively convert strategy output to JSON-serializable primitives."""
        if isinstance(value, dict):
            return {
                str(k): TabularDataProcessorAgent._serialize(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [TabularDataProcessorAgent._serialize(v) for v in value]
        if hasattr(value, "to_dict"):
            try:
                return TabularDataProcessorAgent._serialize(value.to_dict())
            except Exception:
                pass
        if hasattr(value, "tolist"):
            try:
                return value.tolist()
            except Exception:
                pass
        if isinstance(value, float) and value != value:   # NaN
            return None
        return value

    # ── Parsing helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_plan(plan: str | dict) -> dict:
        if isinstance(plan, dict):
            return plan
        if isinstance(plan, str):
            try:
                return json.loads(plan)
            except json.JSONDecodeError as exc:
                return {"error": f"Plan is not valid JSON: {exc}"}
        return {"error": f"Plan must be a JSON string or dict, got {type(plan).__name__}."}

    @staticmethod
    def _error_json(stage: str, message: str) -> str:
        return json.dumps(
            {"stage": stage, "status": "failed", "error": message}, indent=2
        )

    # ── JSON schemas ──────────────────────────────────────────────────────────

    @staticmethod
    def _plan_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "dataset": {
                    "type": "object",
                    "properties": {
                        "rows":         {"type": "integer"},
                        "columns":      {"type": "integer"},
                        "column_names": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["rows", "columns", "column_names"],
                },
                "steps": {
                    "type": "array",
                    "items": TabularDataProcessorAgent._step_schema(),
                },
            },
            "required": ["task", "dataset", "steps"],
        }

    @staticmethod
    def _step_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "step":          {"type": "integer"},
                "name":          {"type": "string"},
                "strategy_type": {"type": "string", "enum": ["cleaning", "transform"]},
                "strategy":      {"type": "string"},
                "objective":     {"type": "string"},
                "actions": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 120},
                    "maxItems": 5,
                },
                "arguments":       {"type": "object"},
                "expected_output": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "step", "name", "strategy_type", "strategy",
                "objective", "actions", "expected_output",
            ],
        }

    @staticmethod
    def _summary_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "overall_assessment":   {"type": "string"},
                "steps_completed":      {"type": "integer"},
                "steps_failed":         {"type": "integer"},
                "columns_added":        {"type": "array", "items": {"type": "string"}},
                "columns_dropped":      {"type": "array", "items": {"type": "string"}},
                "quality_improvements": {"type": "array", "items": {"type": "string"}},
                "warnings":             {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "overall_assessment",
                "steps_completed",
                "steps_failed",
                "columns_added",
                "columns_dropped",
                "quality_improvements",
                "warnings",
            ],
        }

    def _parse_summary(self, summary_str: str | dict) -> dict:
        try:
            return json.loads(summary_str) if isinstance(summary_str, str) else summary_str
        except json.JSONDecodeError:
            return {"raw": summary_str}

    @staticmethod
    def _needs_refinement(summary: dict) -> bool:
        return bool(summary.get("steps_failed")) or bool(summary.get("warnings"))

    @staticmethod
    def _refinement_feedback(summary: dict) -> str:
        return (
            f"A previous preprocessing attempt had {summary.get('steps_failed', 0)} "
            f"failed step(s) and warnings: {summary.get('warnings') or []}. "
            "Choose different strategies/arguments that avoid these failures."
        )

    @staticmethod
    def _is_better(candidate: dict, current: dict) -> bool:
        return (candidate.get("steps_failed", 0), len(candidate.get("warnings") or [])) < \
            (current.get("steps_failed", 0), len(current.get("warnings") or []))


    # ── Target-column protection ─────────────────────────────────────────────
    #
    # The planner is told (see PreprocessPromptGenerator._user_plan) which
    # column is the prediction target, but that's a soft signal — an LLM can
    # still drop it, e.g. when it looks "redundant" against a highly
    # correlated feature (an 0.998 Open/Close correlation genuinely does look
    # like a drop-one-of-them case unless you know one of them IS the label).
    # When that happens, preprocessing reports success, but the model builder
    # fails outright and only surfaces the real cause as a vague "target
    # column not found" warning several minutes and several retries later.
    # This is the hard backstop: independent of prompt compliance, no step is
    # allowed to remove target_column from the frame.

    @staticmethod
    def _protect_target_column(
        step: dict[str, Any],
        target_column: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """
        Inspect a single plan step and, if it would drop ``target_column``,
        rewrite it to exclude the target (or skip it outright if the target
        was its only column).

        :return: ``(possibly-rewritten step, skip_result)``. ``skip_result``
            is ``None`` if the step should still be executed normally, or a
            completed step-result dict (status "skipped") if nothing is left
            to run.
        """
        if not target_column:
            return step, None

        strategy_name = str(step.get("strategy", ""))
        if strategy_name not in ("DropColumnsCleanStrategy", "drop_columns"):
            return step, None

        arguments = dict(step.get("arguments") or {})
        changed = False
        for key, value in list(arguments.items()):
            if isinstance(value, list) and target_column in value:
                arguments[key] = [c for c in value if c != target_column]
                changed = True
            elif value == target_column:
                arguments[key] = None
                changed = True

        if not changed:
            return step, None

        logger.warning(
            "Preprocessing step %s (%s) would have dropped target column '%s'; "
            "stripped it from the step's arguments to protect it.",
            step.get("step"), strategy_name, target_column,
        )

        step = {**step, "arguments": arguments}

        # If every remaining column-list argument is now empty, there's
        # nothing left for this step to do — skip it rather than calling the
        # strategy with an empty/None column list.
        if all(not v for v in arguments.values()):
            skip_result = {
                "step": step.get("step"),
                "name": step.get("name"),
                "strategy": strategy_name,
                "strategy_type": step.get("strategy_type", "cleaning"),
                "objective": step.get("objective"),
                "status": "skipped",
                "error": (
                    f"Refused to execute: only column targeted for dropping was "
                    f"the prediction target '{target_column}'."
                ),
            }
            return step, skip_result

        return step, None

class _AgentMergeRejected(Exception):
    """
    Internal signal used within :meth:`TabularDataProcessorAgent.merge_sources`
    to distinguish "the LLM looked and found nothing usable" from a normal
    Python exception, so the caller can decide whether to surface it as a
    ``DuplicateColumnError`` (409, name collision) or a plain ``ValueError``
    (409, no relationship found). Never escapes ``merge_sources`` itself.
    """
    pass