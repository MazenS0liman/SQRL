#!/usr/bin/python
"""
Inspect Prompt Generator Module
===============================

Generates prompts for the TabularDataInspectorAgent across three stages:

    1. GENERATE_PLAN     — ask the LLM to choose and order inspection strategies.
    2. GENERATE_SUMMARY  — ask the LLM to interpret execution results into findings.
    3. EXTRACT_KEY_INSIGHTS — ask the LLM to surface patterns and recommendations.

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
class InspectPromptType(Enum):
    """
    Discriminator for the three prompt stages in the Inspect pipeline.

    - ``GENERATE_PLAN``: Given a dataset profile and inspection catalog,
      produce an ordered EDA execution plan (no data is read yet).
    - ``GENERATE_SUMMARY``: Given a completed execution report, interpret
      results into findings, anomalies, and recommended next steps.
    - ``EXTRACT_KEY_INSIGHTS``: Given findings and anomalies, surface the
      most actionable patterns and produce a narrative for stakeholders.
    
    """
    GENERATE_PLAN = "generate_plan"
    GENERATE_SUMMARY = "generate_summary"
    GENERATE_STEP = "generate_step"
    EXTRACT_KEY_INSIGHTS = "extract_key_insights"


# —— Prompt generator implementation —————————————————————————————————————————
class InspectPromptGenerator(IPromptGenerator):
    """
    Prompt generator for data inspection agents.

    Each prompt type has a dedicated system prompt (role + constraints) and
    a dedicated user prompt (task-specific context injection). The two halves
    are kept separate so callers can pass the system prompt to the provider
    once at session start and regenerate only the user prompt per call.

    **Methods:**
        - ``generate_system_prompt(prompt_type)`` — role + rules for this stage.
        - ``generate_user_prompt(**kwargs)``       — context-injected task prompt.
    
    """

    def __init__(self):
        super().__init__()

    # ── System prompts ────────────────────────────────────────────────────────
    @override
    def generate_system_prompt(
        self, 
        prompt_type: InspectPromptType | None = None
    ) -> str:
        """
        Generate stage-specific system-level instructions.

        :param prompt_type: Which pipeline stage to generate instructions for.
        :type prompt_type: InspectPromptType
        
        :return: System prompt string.
        :rtype: str
        """
        dispatch = {
            InspectPromptType.GENERATE_PLAN: self._system_plan,
            InspectPromptType.GENERATE_SUMMARY: self._system_summary,
            InspectPromptType.GENERATE_STEP: self._system_step,
            InspectPromptType.EXTRACT_KEY_INSIGHTS: self._system_insights,
        }

        if prompt_type is not None and prompt_type not in dispatch:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in InspectPromptType]}"
            )

        handler = dispatch.get(prompt_type, lambda: "You are a helpful assistant.")
        return handler()

    def _system_plan(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data analyst designing an EDA pipeline.
            Your role in this stage is PLANNING ONLY — you must not read,
            compute, or invent any statistics about the data.
            
            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Choose inspection strategies exclusively from the provided catalog.
            - Order steps so that cheap, structural inspections (data types,
              summary statistics) precede expensive or dependent ones
              (correlation, outlier detection).
            - Include an `arguments` object for strategies that accept parameters
              (e.g. column names, thresholds, method). Leave it as {} when none
              are needed.
            - Skip strategies that are irrelevant for the dataset's column types
              (e.g. correlation when there is only one numeric column).
            - Each step must have a clear, single objective.
        """).strip()
        
    def _system_step(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data analyst regenerating a single EDA step.
            Your role in this stage is REGENERATION ONLY — fix the specified
            issue with the step while adhering to the same planning rules.

            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Regenerate only the single step specified in the prompt.
            - Ensure the `inspection` field matches one of the available strategies.
            - Ensure all required arguments for that strategy are present and
              correctly formatted. Use optional arguments if they add value.
            - Use only column names that appear in the dataset profile.
            - Do not invent column names or statistics that are not in the profile.
        """).strip()

    def _system_summary(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data analyst interpreting completed EDA results.
            Your role is INTERPRETATION — translate raw inspection outputs into
            human-readable findings an engineer or stakeholder can act on.

            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Ground every finding in the provided execution output. Do not invent
              statistics, percentages, or column names that are not in the data.
            - Assign severity (low / medium / high) based on the magnitude of the
              issue (e.g. >30% missingness = high, <5% = low).
            - Use `recommended_action` strings from strategy outputs when present.
            - Be concise: one finding per column per issue type.
            - Produce a `data_quality_score` between 0 and 1:
                1.0 = no issues detected
                0.0 = severe, pervasive issues across most columns.
        """).strip()

    def _system_insights(self) -> str:
        return textwrap.dedent("""
            # Role:
            You are a senior data scientist surfacing patterns for a business audience.
            Your role is SYNTHESIS — take structured findings and anomalies and
            produce a concise, prioritised narrative of what matters most and why.

            # Rules:
            - Output valid JSON only. No markdown, no code fences, no prose.
            - Prioritise insights by business impact, not technical severity.
            - Each insight must include a plain-language explanation requiring no
              statistical background to understand.
            - Proposed actions must be specific and implementable (not "investigate further").
            - Group related column-level findings into dataset-level themes where possible.
            - Do not repeat findings verbatim — synthesise and interpret.
        """).strip()

    # ── User prompts ──────────────────────────────────────────────────────────
    @override
    def generate_user_prompt(
        self,
        prompt_type: InspectPromptType | None = None,
        **kwargs
    ) -> str:
        """
        Dispatch to the correct user prompt builder based on ``prompt_type``.

        :param kwargs: Must include ``prompt_type`` (InspectPromptType).
            Additional keys depend on the stage — see each builder's docstring.
            
        :return: User prompt string ready to send to the LLM.
        :rtype: str
        
        :raises ValueError: If ``prompt_type`` is missing or unrecognised.
        """
        dispatch = {
            InspectPromptType.GENERATE_PLAN: self._user_plan,
            InspectPromptType.GENERATE_SUMMARY: self._user_summary,
            InspectPromptType.GENERATE_STEP: self._user_step,
            InspectPromptType.EXTRACT_KEY_INSIGHTS: self._user_insights,
        }
        handler = dispatch.get(prompt_type)
        if handler is None:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in InspectPromptType]}"
            )
        return handler(**kwargs)
    
    def _user_step(self, **kwargs) -> str:
        profile = kwargs.get("dataset_profile", {})
        objective = kwargs.get("objective", "Regenerate the step with correct arguments based on the dataset profile and strategy requirements.")
        inspection_catalog = kwargs.get("inspection_catalog", [])

        return textwrap.dedent(f"""
            ## Regeneration objective
            {objective}
            
            ## Dataset shape
            - Rows: {profile.get('rows', 'unknown')}
            - Columns: {profile.get('columns', 'unknown')}
            
            ## Column names: {', '.join(profile.get('column_names', []))}
            
            ## Column types
            {self._column_type_hints(profile)}

            ## Missing value counts
            {self._missing_hint(profile)}
            
            ## Available inspection strategies
            Study the catalog carefully before regenerating. Each entry shows:
            - What the strategy does
            - Which arguments it accepts and what they mean
                - A complete example step you can adapt
            {self._render_catalog(inspection_catalog)}
            
            ## Task
            Regenerate the step with correct arguments based on the dataset profile and strategy requirements.
            - Ensure to generate arguments in the correct format and with the right keys as shown in the
                strategy specification.
            - Use only column names that appear in the dataset above.
            - Do not invent column names or statistics.
        """).strip()

    def _user_plan(self, **kwargs) -> str:
        profile = kwargs.get("dataset_profile", {})
        objective = kwargs.get("objective", "Perform comprehensive exploratory data analysis.")

        catalog_block = self._render_catalog(
            profile.get("available_inspections", [])
        )

        return textwrap.dedent(f"""
            ## Analysis objective
            {objective}

            ## Dataset shape
            - Rows: {profile.get('rows', 'unknown')}
            - Columns: {profile.get('columns', 'unknown')}
            - Column names: {', '.join(profile.get('column_names', []))}

            ## Column types
            {self._column_type_hints(profile)}

            ## Missing value counts
            {self._missing_hint(profile)}

            ## Available inspection strategies
            Study each strategy carefully before planning. Each entry shows:
            - What the strategy does
            - Which arguments it accepts and what they mean
            - A complete example step you can adapt

            {catalog_block}

            ## Task
            Produce an ordered EDA plan. Each step must include:
            - step            (integer, 1-based)
            - name            (short title, ≤ 6 words)
            - inspection      (exact name from the catalog above)
            - objective       (one sentence: why this step is needed for THIS dataset)
            - actions         (list of concrete sub-tasks the strategy will perform)
            - arguments       (dict — copy the argument structure from the example and
                                substitute real column names from this dataset;
                                use {{}} only for strategies that take no arguments)
            - expected_output (list of insight types or artifacts this step produces)

            Rules:
            - Use only column names that appear in the dataset above.
            - Do not invent column names or statistics.
                        - If a strategy has any entries in its argument schema, populate the
                            `arguments` object with every required field and any optional fields
                            that have defaults. Only use `{{}}` for strategies whose catalog shows
                            no arguments.
            - Skip strategies irrelevant to this dataset
                (e.g. skip CorrelationInspectionStrategy if there is only one numeric column).
            - For MissingValuesInspectionStrategy and CorrelationInspectionStrategy,
                produce one step per column pair you want to examine.
            - Ensure to generate arguments in the correct format and with the right keys as shown in the examples above.
        """).strip()

    @staticmethod
    def _render_catalog(available: list[dict]) -> str:
        """
        Render the inspection catalog as a structured block.
        Each entry shows name, description, accepted arguments, and a full example step.
        Falls back to a name+description-only block for any strategy not in INSPECTION_CATALOG.
        """
        # The planner passes a full catalog (built by build_inspection_catalog()),
        # but this function is defensive: accept minimal entries (name+description)
        # as well as rich entries produced by DataInspectStrategy.to_catalog_entry().
        blocks: list[str] = []

        for entry in available:
            name = entry.get("name", "(unnamed)")
            description = entry.get("description", entry.get("doc", "(no description)"))

            # Build argument lines from either a pre-rendered "arguments" dict
            # or from the canonical "arguments_schema" produced by the catalog builder.
            args_lines = "      (none)"
            if entry.get("arguments") and isinstance(entry.get("arguments"), dict):
                args_lines = "\n".join(f"      {k}: {v}" for k, v in entry["arguments"].items()) or args_lines
            elif entry.get("arguments_schema") and isinstance(entry.get("arguments_schema"), list):
                parts = []
                for spec in entry["arguments_schema"]:
                    name_s = spec.get("name")
                    typ = spec.get("type")
                    default = spec.get("default")
                    req = spec.get("required")
                    desc = spec.get("description")
                    part = f"{name_s}: type={typ}, required={req}, default={default}"
                    if desc:
                        part += f", {desc}"
                    parts.append(f"      {part}")
                if parts:
                    args_lines = "\n".join(parts)

            example = entry.get("example") or {}
            try:
                example_json = json.dumps(example, indent=4, default=str)
            except Exception:
                example_json = str(example)

            args_description = entry.get("arguments_description", "No arguments required.")

            # Also include a full-spec JSON block so any extra keys are visible
            try:
                full_spec = json.dumps(entry, indent=2, default=str)
            except Exception:
                full_spec = str(entry)

            blocks.append(textwrap.dedent(f"""
                ### {name}
                {description}

                Arguments:
                {args_lines}
                Note: {args_description}

                Example step:
                {example_json}

                Full spec:
                {full_spec}
            """).strip())

        return "\n\n".join(blocks)

    def _user_summary(self, **kwargs) -> str:
        """
        Prompt for the GENERATE_SUMMARY stage.

        Expected kwargs:
            - ``dataset_profile`` (dict): sanitized execution report from ``execute()``.
            - ``objective`` (str, optional): interpretation goal.
        """
        report = kwargs.get("dataset_profile", {})
        objective = kwargs.get("objective", "Interpret the EDA execution results.")

        step_summaries = self._execution_step_summaries(report)

        return textwrap.dedent(f"""
            ## Interpretation objective
            {objective}

            ## Execution overview
            - Status : {report.get('status', 'unknown')}
            - Steps completed : {report.get('summary', {}).get('completed_steps', '?')}
            - Steps failed    : {report.get('summary', {}).get('failed_steps', '?')}
            - Steps skipped   : {report.get('summary', {}).get('skipped_steps', '?')}

            ## Dataset
            - Rows    : {report.get('dataset', {}).get('rows', 'unknown')}
            - Columns : {report.get('dataset', {}).get('columns', 'unknown')}

            ## Inspection results
            {step_summaries}

            ## Task
            Produce a structured summary with:
              - overall_assessment     (one paragraph describing dataset health)
              - data_quality_score     (float 0–1; 1 = no issues)
              - findings               (per-column issues with severity)
              - anomalies              (unexpected patterns worth flagging)
              - recommended_next_steps (ordered list of concrete actions)
        """).strip()

    def _user_insights(self, **kwargs) -> str:
        """
        Prompt for the EXTRACT_KEY_INSIGHTS stage.

        Expected kwargs:
            - ``dataset_profile`` (dict): summary report from ``summarize()``.
            - ``objective`` (str, optional): audience or framing for the narrative.
            - ``audience`` (str, optional): "technical" | "business" (default "business").
        """
        summary = kwargs.get("dataset_profile", {})
        objective = kwargs.get("objective", "Surface the most actionable patterns in the data.")
        audience = kwargs.get("audience", "business")

        findings_block = json.dumps(summary.get("findings", []), indent=2, default=str)
        anomalies_block = json.dumps(summary.get("anomalies", []), indent=2, default=str)
        next_steps_block = "\n".join(
            f"  {i+1}. {s}"
            for i, s in enumerate(summary.get("recommended_next_steps", []))
        )

        return textwrap.dedent(f"""
            ## Synthesis objective
            {objective}

            ## Target audience
            {audience}

            ## Data quality score
            {summary.get('data_quality_score', 'not computed')} / 1.0

            ## Overall assessment
            {summary.get('overall_assessment', 'not provided')}

            ## Per-column findings
            {findings_block}

            ## Anomalies
            {anomalies_block}

            ## Suggested next steps (from previous stage)
            {next_steps_block}

            ## Task
            Synthesise the above into a business-ready insight report with:
              - executive_summary   (2–3 sentences for a non-technical reader)
              - key_insights        (list; each has: theme, explanation, impacted_columns,
                                     proposed_action)
              - risk_flags          (list of high-severity items that block modelling or
                                     reporting)
              - quick_wins          (list of low-effort, high-impact fixes)
              - open_questions      (list of questions the data alone cannot answer)
        """).strip()

    # ── Prompt construction helpers ───────────────────────────────────────────

    @staticmethod
    def _column_type_hints(profile: dict) -> str:
        """Render a compact dtype table from the dataset profile."""
        dtypes = profile.get("dtypes", {})
        if not dtypes:
            return "  (not provided)"
        lines = [f"  {col}: {dtype}" for col, dtype in dtypes.items()]
        return "\n".join(lines)

    @staticmethod
    def _missing_hint(profile: dict) -> str:
        """Render only columns that have at least one missing value."""
        missing = profile.get("missing_counts", {})
        non_zero = {col: count for col, count in missing.items() if count}
        if not non_zero:
            return "  (no missing values detected)"
        return "\n".join(f"  {col}: {count} missing" for col, count in non_zero.items())

    @staticmethod
    def _execution_step_summaries(report: dict) -> str:
        """
        Render completed step outputs as compact blocks.
        Failed and skipped steps are included with their error message so the
        LLM knows what information is absent from the interpretation.
        """
        steps = report.get("steps", [])
        if not steps:
            return "  (no steps recorded)"

        blocks: list[str] = []
        for step in steps:
            status = step.get("status", "unknown")
            header = f"### Step {step.get('step')}: {step.get('name')} [{status}]"

            if status == "completed":
                output_json = json.dumps(step.get("output", {}), indent=4, default=str)
                blocks.append(f"{header}\nObjective: {step.get('objective')}\n{output_json}")
            elif status == "failed":
                blocks.append(f"{header}\nError: {step.get('error')}")
            else:
                blocks.append(f"{header}\nReason: {step.get('error', 'strategy not found')}")

        return "\n\n".join(blocks)
    