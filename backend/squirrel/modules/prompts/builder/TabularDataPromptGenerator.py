#!/usr/bin/python
"""
Model Builder Prompt Generator Module
=====================================

Generates system and user prompts for the two-stage model builder pipeline:
  1. GENERATE_PLAN  — ask the LLM to choose model architectures and hyperparameters.
  2. GENERATE_STEP  — ask the LLM to fix or regenerate a single invalid plan step.
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


class ModelBuilderPromptType(Enum):
    """
    Discriminator for the two prompt stages in the model builder pipeline.
    """
    GENERATE_PLAN = "generate_plan"
    GENERATE_STEP = "generate_step"


# —— Prompt generator implementation —————————————————————————————————————————


class ModelBuilderPromptGenerator(IPromptGenerator):
    """
    Prompt generator for the tabular data model builder agent.

    Produces stage-specific system instructions and grounded user prompts
    for plan generation and single-step regeneration.
    """

    def __init__(self) -> None:
        super().__init__()

    # ── System prompts ────────────────────────────────────────────────────────

    @override
    def generate_system_prompt(
        self,
        prompt_type: ModelBuilderPromptType | None = None,
    ) -> str:
        """
        Generate stage-specific system-level instructions.

        :param prompt_type: Which pipeline stage to generate instructions for.
        :type prompt_type: ModelBuilderPromptType

        :return: System prompt string.
        :rtype: str

        :raises ValueError: If ``prompt_type`` is not a recognised ModelBuilderPromptType.
        """
        dispatch = {
            ModelBuilderPromptType.GENERATE_PLAN: self._system_plan,
            ModelBuilderPromptType.GENERATE_STEP: self._system_step,
        }

        if prompt_type is not None and prompt_type not in dispatch:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in ModelBuilderPromptType]}"
            )

        handler = dispatch.get(prompt_type, lambda: "You are a helpful assistant.")
        return handler()

    # ── User prompts ──────────────────────────────────────────────────────────

    @override
    def generate_user_prompt(
        self,
        prompt_type: ModelBuilderPromptType | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Dispatch to the correct user prompt builder based on ``prompt_type``.

        :param prompt_type: Which pipeline stage to build a user prompt for.
        :type prompt_type: ModelBuilderPromptType

        :param kwargs: Stage-dependent context — see each builder's docstring.

        :return: User prompt string ready to send to the LLM.
        :rtype: str

        :raises ValueError: If ``prompt_type`` is missing or unrecognised.
        """
        dispatch = {
            ModelBuilderPromptType.GENERATE_PLAN: self._user_plan,
            ModelBuilderPromptType.GENERATE_STEP: self._user_step,
        }

        handler = dispatch.get(prompt_type)
        if handler is None:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}. "
                f"Expected one of: {[t.value for t in ModelBuilderPromptType]}"
            )
        return handler(**kwargs)

    # ── System prompt builders ────────────────────────────────────────────────

    def _system_plan(self) -> str:
        return textwrap.dedent("""\
            You are an expert machine learning engineer specialising in tabular data.

            Your task is to design a complete, ordered model-building plan for a
            preprocessed tabular dataset. You will receive:
              - A dataset profile (shape, column names, dtypes, missing-value counts).
              - A preprocessing summary from the upstream TabularDataProcessorAgent.
              - A model catalog listing every available model class with their
                hyperparameter specs.
              - An optional modelling objective (e.g. "binary classification", "regression").

            Guidelines
            ----------
            1. Infer the task type (regression / binary classification /
               multi-class classification) from the target column dtype and the
               preprocessing summary when no objective is supplied.
            2. Choose 1–4 models from the catalog that are well-suited to the
               inferred task and dataset size.  Prefer an ensemble (gradient
               boosting + linear baseline) over a single model when N > 500.
            3. For each model, specify every hyperparameter listed as "required"
               in the catalog entry.  Optional hyperparameters may be omitted.
            4. Include a cross-validation step (cv_evaluate) after each model fit
               to estimate generalisation performance.
            5. If the dataset has a clear class imbalance (visible in the
               preprocessing summary), note it in the plan rationale and set
               appropriate class-weight or sample-weight hyperparameters.
            6. Return **only** valid JSON that matches the plan schema — no prose,
               no markdown fences.
        """)

    def _system_step(self) -> str:
        return textwrap.dedent("""\
            You are an expert machine learning engineer specialising in tabular data.

            A model-building plan step has been flagged as invalid.  Your task is
            to correct the single step and return a replacement that passes
            validation.

            Rules
            -----
            - Use only model or action names that appear in the provided catalogs.
            - Supply every argument marked "required" in the catalog entry.
            - Return a **single** step object as valid JSON — no prose, no fences,
              no enclosing plan envelope.
            - Do not change the step number or the overall intent of the step.
        """)

    # ── User prompt builders ──────────────────────────────────────────────────

    def _user_plan(
        self,
        *,
        objective: str = "",
        dataset_profile: dict | None = None,
        preprocessing_summary: dict | None = None,
        model_catalog: list[dict] | None = None,
        action_catalog: list[dict] | None = None,
        **_: Any,
    ) -> str:
        """
        Build the user prompt for the GENERATE_PLAN stage.

        :param objective: Optional free-text modelling goal.
        :param dataset_profile: Dict from ``_dataset_profile()``.
        :param preprocessing_summary: Dict from ``TabularDataProcessorAgent.summarize()``.
        :param model_catalog: List of model catalog entries (should already be
            pre-filtered by task type by the caller where possible).
        :param action_catalog: List of action catalog entries (fit, evaluate, …).
        :return: Formatted user prompt string.
        """
        sections: list[str] = []

        if objective:
            sections.append(f"## Modelling Objective\n{objective}")
        else:
            sections.append(
                "## Modelling Objective\n"
                "Infer the task type from the dataset and preprocessing summary, "
                "then select the most appropriate models."
            )

        sections.append(
            "## Dataset Profile\n"
            + json.dumps(dataset_profile or {}, separators=(",", ":"))
        )

        sections.append(
            "## Preprocessing Summary\n"
            + json.dumps(preprocessing_summary or {}, separators=(",", ":"))
        )

        if model_catalog:
            sections.append(
                "## Available Models\n" + self._render_model_catalog(model_catalog)
            )

        if action_catalog:
            sections.append(
                "## Available Actions\n" + self._render_action_catalog(action_catalog)
            )

        sections.append(
            "## Instructions\n"
            "Return a JSON plan object that strictly matches the plan schema.  "
            "Each step must reference a model or action name that appears in the "
            "catalogs above, and must supply all required hyperparameters."
        )

        return "\n\n".join(sections)

    def _user_step(
        self,
        *,
        objective: str = "",
        dataset_profile: dict | None = None,
        step: dict | None = None,
        model_catalog: list[dict] | None = None,
        action_catalog: list[dict] | None = None,
        **_: Any,
    ) -> str:
        """
        Build the user prompt for the GENERATE_STEP (regeneration) stage.

        :param objective: Description of why the step is invalid and what to fix.
        :param dataset_profile: Dict from ``_dataset_profile()``.
        :param step: The invalid step dict to be replaced.
        :param model_catalog: List of model catalog entries.
        :param action_catalog: List of action catalog entries.
        :return: Formatted user prompt string.
        """
        sections: list[str] = []

        sections.append(f"## Task\n{objective or 'Fix the invalid step below.'}")

        sections.append(
            "## Invalid Step\n"
            + json.dumps(step or {}, separators=(",", ":"))
        )

        sections.append(
            "## Dataset Profile\n"
            + json.dumps(dataset_profile or {}, separators=(",", ":"))
        )

        if model_catalog:
            sections.append(
                "## Available Models\n" + self._render_model_catalog(model_catalog)
            )

        if action_catalog:
            sections.append(
                "## Available Actions\n" + self._render_action_catalog(action_catalog)
            )

        sections.append(
            "## Instructions\n"
            "Return a single corrected step object as valid JSON.  "
            "Do not wrap it in a plan envelope or add any prose."
        )

        return "\n\n".join(sections)

    # ── Rendering helpers ─────────────────────────────────────────────────────
    #
    # Replace raw json.dumps(..., indent=2) dumps of the catalogs — pretty
    # printing spends tokens on whitespace/newlines the model doesn't need to
    # "read" a catalog, and the original per-model dict repeated key names
    # ("model_key", "task", "description", ...) on every entry. A compact
    # one-line-per-entry format carries the same information for a fraction
    # of the tokens.

    @staticmethod
    def _render_model_catalog(catalog: list[dict]) -> str:
        lines = []
        for m in catalog:
            req = ",".join(m.get("required_params") or []) or "none"
            opt = ",".join(m.get("optional_params") or []) or "none"
            lines.append(
                f"- {m['model_key']} [{m['task']}]: {m['description']} "
                f"(required: {req}; optional: {opt})"
            )
        return "\n".join(lines)

    @staticmethod
    def _render_action_catalog(catalog: list[dict]) -> str:
        lines = []
        for a in catalog:
            req = ",".join(a.get("required_params") or []) or "none"
            opt = ",".join(a.get("optional_params") or []) or "none"
            lines.append(
                f"- {a['action_key']}: {a['description']} "
                f"(required: {req}; optional: {opt})"
            )
        return "\n".join(lines)
