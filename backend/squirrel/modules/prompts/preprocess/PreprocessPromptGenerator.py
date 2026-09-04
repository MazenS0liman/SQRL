#!/usr/bin/python
"""
Explore Prompt Generator Module
================================

Generates prompts for the TabularDataProcessorAgent across two stages:

    1. GENERATE_PLAN  — ask the LLM to choose and order cleaning/transformation
                        strategies tailored to a given dataset and objective.
    2. GENERATE_STEP  — ask the LLM to regenerate a single invalid or failed
                        plan step, fixing argument errors or unknown strategy names.

The module intentionally mirrors the architecture of InspectPromptGenerator so
that both generators share the same caller contract:
    ``generate_system_prompt(prompt_type)``  →  stage-scoped role + rules
    ``generate_user_prompt(prompt_type, **kwargs)``  →  context-injected task

"""

# ——————————————————————————————————————————————————————————————
# Imports

import json
import textwrap
from enum import Enum
from typing import Any
from typing_extensions import override

# Abstract Class
from squirrel.modules.prompts.abstract import IPromptGenerator


# —— Prompt type discriminator ——————————————————————————————————————————

class PreprocessPromptType(Enum):
    """
    Discriminator for the two prompt stages in the preprocessing pipeline.

    - ``GENERATE_PLAN``: Given a dataset profile, an EDA summary, and the full
      cleaning + transformation catalogs, produce an ordered preprocessing plan.
    - ``GENERATE_STEP``: Given a single invalid step and the relevant strategy
      spec, regenerate that step with corrected arguments or strategy name.
    """
    GENERATE_PLAN = "generate_plan"
    GENERATE_STEP = "generate_step"


# —— Prompt generator implementation —————————————————————————————————————————

class PreprocessPromptGenerator(IPromptGenerator):
    """
    Prompt generator for the tabular data preprocessing (clean + transform) agent.

    **Description:**
        Each prompt type has a dedicated system prompt (role + constraints) and a
        dedicated user prompt (task-specific context injection).  The two halves are
        kept separate so callers can pass the system prompt to the provider once at
        session start and regenerate only the user prompt per call.

    **Methods:**
        - ``generate_system_prompt(prompt_type)`` — role + rules for this stage.
        - ``generate_user_prompt(prompt_type, **kwargs)`` — context-injected task.
    """

    def __init__(self):
        super().__init__()

    # ── System prompts ────────────────────────────────────────────────────────

    @override
    def generate_system_prompt(
        self,
        prompt_type: PreprocessPromptType | None = None,
    ) -> str:
        """
        Generate stage-specific system-level instructions.

        :param prompt_type: Which pipeline stage to generate instructions for.
        :type prompt_type: ExplorePromptType

        :return: System prompt string.
        :rtype: str

        :raises ValueError: If ``prompt_type`` is not a recognised ExplorePromptType.
        """
        dispatch = {
            PreprocessPromptType.GENERATE_PLAN: self._system_plan,
            PreprocessPromptType.GENERATE_STEP: self._system_step,
        }

        if prompt_type is not None and prompt_type not in dispatch:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in PreprocessPromptType]}"
            )

        handler = dispatch.get(prompt_type, lambda: "You are a helpful assistant.")
        return handler()

    def _system_plan(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data engineer designing a preprocessing pipeline.
            Your role in this stage is PLANNING ONLY — you must not read,
            compute, or invent any statistics about the data.

            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Choose strategies exclusively from the provided cleaning and
              transformation catalogs. Never invent strategy names.
            - Order steps logically:
                1. Structural fixes first  (drop columns, rename, cast dtypes).
                2. Data quality next       (drop duplicates, impute missing, clip outliers).
                3. Feature engineering     (bin, datetime parts, interactions, polynomial).
                4. Encoding last           (one-hot, ordinal, target, frequency, binary).
                5. Normalisation last      (scale, log-transform, power-transform).
            - Include an ``arguments`` object for every strategy; populate ALL
              required fields and any optional fields that add value for this
              dataset.  Use ``{}`` only for strategies that accept no arguments.
            - Use only column names that exist in the dataset profile.
            - Skip strategies that are irrelevant (e.g. do not encode columns
              that will be dropped; do not normalise columns that are already
              on a bounded scale).
            - Each step must have a single, clearly stated objective.
            - Prefer conservative defaults unless the EDA summary indicates a
              specific issue (e.g. use robust scaling only when outliers are
              flagged as high-severity).
        """).strip()

    def _system_step(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data engineer regenerating a single preprocessing step.
            Your role is REGENERATION ONLY — fix the specified issue while
            adhering to the same planning rules.

            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Regenerate only the single step specified in the prompt.
            - The ``strategy`` field must exactly match one of the available
              strategy names listed in the catalog (case-sensitive).
            - Populate all required arguments and any optional arguments that
              are relevant for this dataset.
            - Use only column names that appear in the dataset profile.
            - Do not invent column names, categories, or statistics.
            - If the original step used the wrong strategy for the operation,
              pick the closest correct one from the catalog.
        """).strip()

    # ── User prompts ──────────────────────────────────────────────────────────

    @override
    def generate_user_prompt(
        self,
        prompt_type: PreprocessPromptType | None = None,
        **kwargs,
    ) -> str:
        """
        Dispatch to the correct user prompt builder based on ``prompt_type``.

        :param prompt_type: Which pipeline stage to build a user prompt for.
        :type prompt_type: ExplorePromptType

        :param kwargs: Stage-dependent context — see each builder's docstring.

        :return: User prompt string ready to send to the LLM.
        :rtype: str

        :raises ValueError: If ``prompt_type`` is missing or unrecognised.
        """
        dispatch = {
            PreprocessPromptType.GENERATE_PLAN: self._user_plan,
            PreprocessPromptType.GENERATE_STEP: self._user_step,
        }

        handler = dispatch.get(prompt_type)
        if handler is None:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in PreprocessPromptType]}"
            )
        return handler(**kwargs)

    def _user_plan(self, **kwargs) -> str:
        """
        Prompt for the GENERATE_PLAN stage.

        Expected kwargs:
            - ``dataset_profile``  (dict): shape, column names, dtypes, missing counts.
            - ``eda_summary``       (dict, optional): structured EDA findings from the
            inspector agent (findings, anomalies, recommended_next_steps).
            - ``objective``         (str, optional): caller-supplied preprocessing goal.
            - ``cleaning_catalog``  (list[dict]): entries from DataCleanStrategy subclasses.
            - ``transform_catalog`` (list[dict]): entries from DataTransformStrategy subclasses.
            - ``target_column``     (str, optional): the column the downstream model
            will predict. When given, the plan must not drop, encode away, or
            otherwise destroy it.
        """
        profile: dict = kwargs.get("dataset_profile", {})
        eda_summary: dict = kwargs.get("eda_summary", {})
        objective: str = kwargs.get(
            "objective",
            "Prepare the dataset for downstream modelling by cleaning and transforming all relevant columns.",
        )
        cleaning_catalog: list = kwargs.get("cleaning_catalog", [])
        transform_catalog: list = kwargs.get("transform_catalog", [])
        target_column: str | None = kwargs.get("target_column")

        eda_block = self._render_eda_summary(eda_summary)
        cleaning_block = self._render_catalog(cleaning_catalog, kind="cleaning", slim=True)
        transform_block = self._render_catalog(transform_catalog, kind="transform", slim=True)

        target_block = (
            f"## Prediction target\n"
            f"Column '{target_column}' is the label the downstream model will predict. "
            f"Do NOT drop it, rename it away, bin/encode/scale it, or otherwise remove "
            f"or transform it out of the frame — even if it looks redundant or highly "
            f"correlated with another feature. If a feature is highly correlated with "
            f"'{target_column}', drop the OTHER feature, not the target.\n"
            if target_column else
            "## Prediction target\n(not specified — no column is protected from dropping)\n"
        )

        return textwrap.dedent(f"""
            ## Preprocessing objective
            {objective}

            {target_block}
            ## Dataset shape
            - Rows    : {profile.get('rows', 'unknown')}
            - Columns : {profile.get('columns', 'unknown')}

            ## Column names
            {', '.join(profile.get('column_names', []))}

            ## Column types
            {self._column_type_hints(profile)}

            ## Missing value counts
            {self._missing_hint(profile)}

            ## EDA findings (from inspector agent)
            {eda_block}

            ## Available CLEANING strategies
            Study each strategy carefully before planning. Each entry shows what
            the strategy does, which arguments it accepts, and a complete example
            step you can adapt.

            {cleaning_block}

            ## Available TRANSFORMATION strategies
            {transform_block}

            ## Task
            Produce an ordered preprocessing plan. Each step must include:
            - step            (integer, 1-based)
            - name            (short title, ≤ 6 words)
            - strategy_type   ("cleaning" or "transform")
            - strategy        (exact name from the relevant catalog above)
            - objective       (one sentence: why this step is needed for THIS dataset)
            - actions         (list of concrete sub-tasks the strategy will perform)
            - arguments       (dict — copy the argument structure from the example and
                            substitute real column names; use {{}} only for strategies
                            that genuinely accept no arguments)
            - expected_output (list of artifact or quality types this step produces)

            Rules:
            - Use only column names that appear in the dataset above.
            - Do not invent column names, dtypes, or statistics.
            - Populate ALL required arguments and relevant optional arguments.
            - Skip strategies that are not applicable to this dataset.
            - Ground every decision in the EDA findings where possible.
            - Never drop, rename away, or transform the prediction target column
            named above, if one is specified.
        """).strip()

    def _user_step(self, **kwargs) -> str:
        """
        Prompt for the GENERATE_STEP stage.

        Expected kwargs:
            - ``dataset_profile``   (dict): shape, dtypes, missing counts.
            - ``objective``          (str): reason for regeneration + fix required.
            - ``step``               (dict): the original invalid step dict.
            - ``cleaning_catalog``   (list[dict]): full cleaning catalog.
            - ``transform_catalog``  (list[dict]): full transform catalog.
        """
        profile: dict = kwargs.get("dataset_profile", {})
        objective: str = kwargs.get(
            "objective",
            "Regenerate the step with correct arguments based on the dataset profile and strategy requirements.",
        )
        step: dict = kwargs.get("step", {})
        cleaning_catalog: list = kwargs.get("cleaning_catalog", [])
        transform_catalog: list = kwargs.get("transform_catalog", [])

        # Only render the catalog half relevant to this step's type. A
        # regeneration call is fixing exactly one step, so it never needs both
        # the full cleaning AND full transform catalogs (~24 strategies with
        # complete examples) — that was the main driver of oversized regen
        # prompts on datasets with several invalid steps. Fall back to slim
        # renderings of both halves only if the type is missing/unrecognised.
        strategy_type = str(step.get("strategy_type", ""))
        if strategy_type == "cleaning":
            catalog_block = "## Available CLEANING strategies\n" + self._render_catalog(cleaning_catalog, kind="cleaning")
        elif strategy_type == "transform":
            catalog_block = "## Available TRANSFORMATION strategies\n" + self._render_catalog(transform_catalog, kind="transform")
        else:
            catalog_block = (
                "## Available CLEANING strategies\n"
                + self._render_catalog(cleaning_catalog, kind="cleaning", slim=True)
                + "\n\n## Available TRANSFORMATION strategies\n"
                + self._render_catalog(transform_catalog, kind="transform", slim=True)
            )

        try:
            step_json = json.dumps(step, indent=4, default=str)
        except Exception:
            step_json = str(step)

        return textwrap.dedent(f"""
            ## Regeneration objective
            {objective}

            ## Dataset shape
            - Rows    : {profile.get('rows', 'unknown')}
            - Columns : {profile.get('columns', 'unknown')}

            ## Column names
            {', '.join(profile.get('column_names', []))}

            ## Column types
            {self._column_type_hints(profile)}

            ## Missing value counts
            {self._missing_hint(profile)}

            ## Original (invalid) step
            {step_json}

            {catalog_block}

            ## Task
            Regenerate the step with corrected arguments and/or strategy name.
            - Ensure ``strategy`` exactly matches an entry in the relevant catalog.
            - Populate all required arguments in the correct format.
            - Use only column names from the dataset above.
            - Do not invent column names or statistics.
            - Return a single step object (not wrapped in a list or plan).
        """).strip()

    # ── Rendering helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _render_catalog(
        catalog: list[dict], 
        kind: str = "strategy", 
        slim: bool = False
    ) -> str:
        """
        Render a cleaning or transformation catalog as structured text blocks.

        :param slim: When True (used for GENERATE_PLAN), emit a compact one-line-per-arg
                     format plus the example ``arguments`` dict only — no full example step
                     JSON.  This keeps the plan prompt well within token limits even when
                     24 strategies are listed.  When False (used for GENERATE_STEP), emit
                     the full example step so the LLM has maximum detail for repair.
        """
        if not catalog:
            return f"  (no {kind} strategies available)"

        blocks: list[str] = []
        for entry in catalog:
            name = entry.get("name", "(unnamed)")
            description = entry.get("description", "(no description)")
            schema: list = entry.get("arguments_schema") or []

            # Build compact argument lines: one line per argument, required first.
            req_parts: list[str] = []
            opt_parts: list[str] = []
            example_arguments: dict = {}

            for spec in schema:
                sname    = spec.get("name", "?")
                stype    = spec.get("type", "?")
                required = spec.get("required", False)
                default  = spec.get("default")
                desc     = spec.get("description", "")
                possible = spec.get("possible_values", "")
                # Truncate long possible_values strings to keep lines short
                if possible and len(possible) > 80:
                    possible = possible[:77] + "..."
                line = f"  {sname} ({stype}, {'required' if required else 'optional'})"
                if desc:
                    # Keep description to ≤60 chars to avoid line bloat
                    short_desc = desc if len(desc) <= 60 else desc[:57] + "..."
                    line += f": {short_desc}"
                if possible:
                    line += f" [{possible}]"
                if required:
                    req_parts.append(line)
                    example_arguments[sname] = f"<{stype}>"
                else:
                    opt_parts.append(line)

            args_section = ""
            if req_parts:
                args_section += "  Required:\n" + "\n".join(req_parts)
            if opt_parts:
                args_section += ("\n" if args_section else "") + "  Optional:\n" + "\n".join(opt_parts)
            if not args_section:
                args_section = "  (none)"

            if slim:
                # Compact format: no full example step JSON
                try:
                    args_json = json.dumps(example_arguments, indent=2)
                except Exception:
                    args_json = str(example_arguments)
                block = textwrap.dedent(f"""
                    ### {name}
                    {description}
                    Arguments:
                    {args_section}
                    Example arguments: {args_json}
                """).strip()
            else:
                # Full format: include complete example step for repair prompts
                example: dict = entry.get("example") or {}
                try:
                    example_json = json.dumps(example, indent=4, default=str)
                except Exception:
                    example_json = str(example)
                args_description: str = entry.get("arguments_description", "No arguments required.")
                block = textwrap.dedent(f"""
                    ### {name}
                    {description}

                    Arguments:
                    {args_section}
                    Note: {args_description}

                    Example step:
                    {example_json}
                """).strip()

            blocks.append(block)

        return "\n\n".join(blocks)

    @staticmethod
    def _render_eda_summary(eda_summary: dict) -> str:
        """
        Render the EDA summary dict as a concise, LLM-readable block.
        Caps findings/anomalies at 10 entries and truncates the overall_assessment
        to 300 characters so the block stays well within token budgets.
        """
        if not eda_summary:
            return "  (no EDA summary provided — plan conservatively)"

        lines: list[str] = []

        overall = eda_summary.get("overall_assessment") or ""
        if overall:
            # Truncate to 300 chars to avoid bloating the prompt
            if len(overall) > 300:
                overall = overall[:297] + "..."
            lines.append(f"Overall: {overall}")

        score = eda_summary.get("data_quality_score")
        if score is not None:
            lines.append(f"Quality score: {score:.2f}/1.0")

        findings: list[dict] = eda_summary.get("findings") or []
        if findings:
            lines.append("Key findings (high/medium severity):")
            # Show at most 10, prioritise high severity
            sorted_f = sorted(findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("severity", "low"), 3))
            for f in sorted_f[:10]:
                col      = f.get("column", "?")
                finding  = f.get("finding", "")
                severity = f.get("severity", "?")
                # Truncate long finding strings
                if len(finding) > 100:
                    finding = finding[:97] + "..."
                lines.append(f"  [{severity.upper()}] {col}: {finding}")

        anomalies: list[dict] = eda_summary.get("anomalies") or []
        if anomalies:
            lines.append("Anomalies:")
            for a in anomalies[:5]:
                col     = a.get("column", "?")
                anomaly = a.get("anomaly", "")
                if len(anomaly) > 100:
                    anomaly = anomaly[:97] + "..."
                lines.append(f"  {col}: {anomaly}")

        next_steps: list[str] = eda_summary.get("recommended_next_steps") or []
        if next_steps:
            lines.append("Recommended steps (from EDA):")
            for i, step in enumerate(next_steps[:5], 1):
                lines.append(f"  {i}. {step}")

        return "\n".join(lines) if lines else "  (EDA summary contained no actionable content)"

    @staticmethod
    def _column_type_hints(profile: dict) -> str:
        """Render a compact dtype table from the dataset profile."""
        dtypes: dict = profile.get("dtypes", {})
        if not dtypes:
            return "  (not provided)"
        return "\n".join(f"  {col}: {dtype}" for col, dtype in dtypes.items())

    @staticmethod
    def _missing_hint(profile: dict) -> str:
        """Render only columns that have at least one missing value."""
        missing: dict = profile.get("missing_counts", {})
        non_zero = {col: count for col, count in missing.items() if count}
        if not non_zero:
            return "  (no missing values detected)"
        return "\n".join(f"  {col}: {count} missing" for col, count in non_zero.items())