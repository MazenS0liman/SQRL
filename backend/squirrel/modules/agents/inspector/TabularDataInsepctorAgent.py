#!/usr/bin/python
"""

Tabular Data Inspector Agent — plans, executes, and summarizes EDA over a DataFrame.

"""

# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from copy import deepcopy
import json
import pandas as pd
from typing import Any, Dict

# Abstract Base Class
from squirrel.modules.agents.abstract import IAgent
from squirrel.modules.providers import Provider

# Prompt Generators
from squirrel.modules.prompts.inspect.InspectPromptGenerator import (
    InspectPromptGenerator,
    InspectPromptType
)

# Data Inspector Classes
from squirrel.modules.inspectors.tabular import (
    DataInspectStrategy
)

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Catalog builder function

def build_inspection_catalog(
    base: type[DataInspectStrategy] = DataInspectStrategy,
) -> list[Dict]:
    """
    Dynamically build the inspection catalog by walking all registered
    DataInspectStrategy subclasses and calling ``to_catalog_entry()`` on each.

    Adding a new strategy requires only:
        1. Subclass DataInspectStrategy.
        2. Declare ``argument_specs``.
        3. Override ``_example()`` and optionally ``_arguments_description()``.

    No changes to this function, the agent registry, or the prompt generator.
    """
    return [cls.to_catalog_entry() for cls in base.__subclasses__()]

# ——————————————————————————————————————————————————————————————
# Tabular Data Inspector Agent
class TabularDataInspectorAgent(IAgent):
    """
    Agent for inspecting tabular data and generating insights based on the analysis.

    Lifecycle:
        1. ``plan``    — asks the LLM to choose and order inspection strategies.
        2. ``execute`` — runs each planned strategy and collects structured outputs.
        3. ``summarize``— asks the LLM to interpret execution results and emit
                         findings, anomalies, and recommended next steps.
        4. ``run``     — convenience wrapper that chains all three stages.
    """

    # Single source of truth for all registered strategies.
    _REGISTRY: Dict[str, type[DataInspectStrategy]] = {
        cls.__name__: cls
        for cls in DataInspectStrategy.__subclasses__()
    }

    def __init__(self, prompt_generator: Any = InspectPromptGenerator()):
        super().__init__(prompt_generator=prompt_generator)

    def visualize(
        self, 
        data: pd.DataFrame
    ) -> str:
        """
        Generate visualizations for the given DataFrame.

        **Description:**

            This method generates visualizations for the given DataFrame. It can be used to create charts, graphs, or other visual representations of the data to help with analysis and interpretation.

        :param data: Input dataset.
        :type data: pd.DataFrame

        :return: A string representation of the generated visualizations.
        :rtype: str
        """
        # Placeholder implementation - replace with actual visualization logic
        return "Visualization generation is not yet implemented."

    def inspect(
        self, 
        data: pd.DataFrame, 
        objective: str = ""
    ) -> str:
        """
        Inspect the given DataFrame and return a JSON summary report with findings,
        
        **Description:**

            Inspect the given DataFrame and return a JSON summary report with findings,
            anomalies, and recommended next steps. The agent will first ask the LLM to
            plan an ordered sequence of inspection strategies, then execute each strategy
            while capturing structured outputs and any errors, and finally ask the LLM
            to interpret the execution results and produce a final summary.

        :param data: Input dataset.
        :type data: pd.DataFrame
        :param objective: Optional free-text goal passed to the planner.
        :type objective: str

        :return: JSON summary report.
        :rtype: str
        """
        plan = self.plan(data, objective=objective)
        execution = self.execute(data, plan)
        return self.summarize(execution)

    def plan(
        self, 
        data: pd.DataFrame, 
        objective: str = ""
    ) -> str:
        """
        Ask the LLM to build an ordered inspection plan from the available
        inspection strategies, tailored to the shape and column types of *data*.
        
        **Description:**
        
            The agent sends a dataset profile and the catalog of available inspections to the LLM, 
            which responds with a JSON plan specifying which inspections to run, in what order
            and with what arguments. The agent validates the plan against the registry and, if any
            steps specify unknown inspections or are missing required arguments, 
            the agent will ask the LLM to regenerate those specific steps, up to a max number of attempts per step.

        :param data: Input dataset.
        :type data: pd.DataFrame
        :param objective: Optional caller-supplied goal.
        :type objective: str

        :return: JSON plan string.
        :rtype: str
        """
        dataset_profile = self._dataset_profile(data)
        dataset_profile["available_inspections"] = self._available_inspections()

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=InspectPromptType.GENERATE_PLAN,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=InspectPromptType.GENERATE_PLAN,
            objective=objective or "Choose and order the best inspection strategies for this dataset.",
            dataset_profile=dataset_profile,
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
            preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
        )

        if response is None:
            return self._error_json("plan", "Provider did not return a response.")

        # Validate and regenerate any steps with unknown inspections or missing required arguments, up to a max number of attempts per step.
        plan_obj = self._parse_plan(response) if isinstance(response, str) else response
        if "error" in plan_obj:
            return self._error_json("plan", plan_obj.get("error", "Unknown error from plan parsing."))

        step_schema = self._plan_schema()["properties"]["steps"]["items"]
        available_inspections = set(self._REGISTRY.keys())
        max_regen_attempts = 2

        for idx, step in enumerate(plan_obj.get("steps", [])):
            need_regen = False
            inspection_name = str(step.get("inspection", ""))
            reason = ""
            if inspection_name not in available_inspections:
                need_regen = True
                reason = f"Unknown inspection '{inspection_name}'"
            else:
                cls = self._REGISTRY.get(inspection_name)
                required_specs = getattr(cls, "argument_specs", []) or []
                arguments = step.get("arguments") or {}
                missing_required = [
                    spec.name for spec in required_specs
                    if getattr(spec, "required", False) and spec.name not in arguments
                ]
                if missing_required:
                    need_regen = True
                    reason = f"Missing required arguments: {', '.join(missing_required)}"

            if not need_regen:
                plan_obj["steps"][idx]["status"] = "valid"
                continue
            else:
                plan_obj["steps"][idx]["status"] = "invalid"
                plan_obj["steps"][idx]["error"] = reason

            logger.info("Plan step %s needs regeneration: %s", idx, reason)

            regenerated = False
            for attempt in range(max_regen_attempts):                
                # Create a regeneration prompt for this specific step, including the reason for regeneration and any relevant inspection specs.
                regen_step_schema = deepcopy(step_schema)
                regen_inspection = self._REGISTRY.get(inspection_name)
                if regen_inspection is not None:
                    regen_step_schema["properties"]["arguments"] = regen_inspection.to_json_schema()

                system_prompt = self.prompt_generator.generate_system_prompt(
                    prompt_type=InspectPromptType.GENERATE_STEP,
                )
                user_prompt = self.prompt_generator.generate_user_prompt(
                    prompt_type=InspectPromptType.GENERATE_STEP,
                    objective=(
                        f"Regenerate step {idx}. Fix the issue: {reason}. "
                        "Return a single step object matching the step schema."
                    ),
                    dataset_profile=dataset_profile,
                    step=step,
                    inspection_catalog=self._available_inspections()
                )
                
                regen_resp = self.get_response(
                    fn_name="generate_json",
                    model_input={
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "schema": regen_step_schema,
                    },
                    strict=False,
                    provider_order=[Provider.GROQ, Provider.GEMINI],
                    preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
                )

                if regen_resp is None:
                    continue

                try:
                    new_step = regen_resp if isinstance(regen_resp, Dict) else json.loads(regen_resp)
                except Exception:
                    continue

                # If the LLM returned an object with 'steps', extract the first.
                if isinstance(new_step, Dict) and "steps" in new_step and isinstance(new_step["steps"], list):
                    new_step = new_step["steps"][0] if new_step["steps"] else new_step

                if not isinstance(new_step, Dict):
                    continue

                plan_obj["steps"][idx] = new_step

                # Re-validate the regenerated step
                inspection_name = str(new_step.get("inspection", ""))
                if inspection_name in available_inspections:
                    cls = self._REGISTRY.get(inspection_name)
                    required_specs = getattr(cls, "argument_specs", []) or []
                    arguments = new_step.get("arguments") or {}
                    missing_required = [
                        spec.name for spec in required_specs
                        if getattr(spec, "required", False) and spec.name not in arguments
                    ]
                    if not missing_required:
                        logger.info("Successfully regenerated step %s", idx)
                        regenerated = True
                        break

            if not regenerated:
                logger.warning("Failed to regenerate valid step %s after %s attempts", idx, max_regen_attempts)
                plan_obj["steps"][idx]["status"] = "invalid"
                plan_obj["steps"][idx]["error"] = f"Failed to regenerate valid step after {max_regen_attempts} attempts: {reason}"

        return json.dumps(plan_obj, indent=2)

    def execute(
        self, 
        data: pd.DataFrame, 
        plan: str | Dict
    ) -> str:
        """
        Execute the inspection plan produced by ``plan()``.

        Unknown or failed strategies are recorded in the report rather than
        raising so the pipeline always produces a complete result.

        :param data: Input dataset.
        :param plan: JSON plan string or dict from ``plan()``.
        :return: JSON execution report string.
        :rtype: str
        """
        plan_obj = self._parse_plan(plan)
        if "error" in plan_obj:
            return json.dumps(
                {
                    "task": "inspection", 
                    "status": "failed", 
                    **plan_obj
                }, 
                indent=2
            )

        registry = {name: cls() for name, cls in self._REGISTRY.items()}
        executed_steps: list[Dict[str, Any]] = []

        for step in plan_obj.get("steps", []):
            executed_steps.append(self._run_step(data, step, registry))

        return json.dumps(self._execution_report(data, executed_steps), indent=2, default=str)

    def summarize(
        self, 
        execution_report: str | Dict
    ) -> str:
        """
        Ask the LLM to interpret the execution results and return a structured
        summary with findings, anomalies, and recommended next steps.

        :param execution_report: JSON execution report from ``execute()``.
        
        :return: JSON summary string.
        :rtype: str
        """
        if isinstance(execution_report, str):
            try:
                report_obj = json.loads(execution_report)
            except json.JSONDecodeError:
                report_obj = {"raw": execution_report}
        else:
            report_obj = execution_report

        # Strip raw outlier index lists — they bloat the prompt without adding
        # interpretive value; the LLM needs counts and severities, not indices.
        report_obj = self._sanitize_for_prompt(report_obj)

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=InspectPromptType.GENERATE_SUMMARY,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=InspectPromptType.GENERATE_SUMMARY,
            objective=(
                "Interpret the EDA execution results. Identify key findings, "
                "data quality issues, anomalies, and recommend concrete next steps."
            ),
            dataset_profile=report_obj,
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
            preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
        )

        if response is None:
            return self._error_json("summarize", "Provider did not return a response.")
        return response if isinstance(response, str) else json.dumps(response, indent=2)

    def inspect_multi(
        self,
        dataframes: Dict[str, pd.DataFrame],
        objective: str = ""
    ) -> str:
        """
        Inspect every input source individually, then reason about how the
        sources relate to each other, grounded in the caller's objective/query.
        Must run BEFORE TabularDataProcessorAgent so that merging/preprocessing
        is informed by real findings instead of shape-only guessing.
        """
        if not dataframes:
            return self._error_json("inspect_multi", "No input sources provided.")

        if len(dataframes) == 1:
            only_df = next(iter(dataframes.values()))
            single = json.loads(self.inspect(only_df, objective=objective))
            single["n_sources"] = 1
            single["relationship"] = {
                "strategy": "single_source",
                "rationale": "Only one input source.",
                "confidence": "high",
            }
            return json.dumps(single, indent=2)

        # 1. Inspect every source on its own — reuses the existing
        #    plan -> execute -> summarize lifecycle per source.
        per_source_reports: Dict[str, Dict] = {
            source_id: json.loads(self.inspect(df, objective=objective))
            for source_id, df in dataframes.items()
        }

        # 2. Cheap deterministic signals (schema/row-count overlap) — computed
        #    first so the LLM call below is grounded in facts, not just samples.
        signals = self._relationship_signals(dataframes)

        # 3. Ask the LLM how the sources relate, using the objective/query,
        #    the deterministic signals, and each source's own EDA findings.
        relationship = self._infer_relationship(
            dataframes=dataframes,
            per_source_reports=per_source_reports,
            signals=signals,
            objective=objective,
        )

        return json.dumps({
            "task": "multi_source_inspection",
            "status": "completed",
            "n_sources": len(dataframes),
            "sources": per_source_reports,
            "relationship_signals": signals,
            "relationship": relationship,   # {strategy, join_keys?, join_type?, rationale, confidence}
        }, indent=2, default=str)

    @staticmethod
    def _relationship_signals(
        dataframes: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        column_sets = {sid: set(df.columns) for sid, df in dataframes.items()}
        row_counts = {sid: int(df.shape[0]) for sid, df in dataframes.items()}
        identical_schema = len({frozenset(c) for c in column_sets.values()}) == 1
        same_row_count = len(set(row_counts.values())) == 1

        overlap: Dict[str, list] = {}
        ids = list(column_sets.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                shared = sorted(column_sets[ids[i]] & column_sets[ids[j]])
                if shared:
                    overlap[f"{ids[i]}__{ids[j]}"] = shared

        return {
            "identical_schema": identical_schema,
            "same_row_count": same_row_count,
            "row_counts": row_counts,
            "shared_columns_by_pair": overlap,
        }
    

    def _infer_relationship(
        self,
        dataframes: Dict[str, pd.DataFrame],
        per_source_reports: Dict[str, Dict],
        signals: Dict[str, Any],
        objective: str
    ) -> Dict[str, Any]:
        profiles = {
            sid: {
                "row_count": int(df.shape[0]),
                "columns": list(df.columns),
                "sample_rows": df.head(3).where(pd.notnull(df.head(3)), None).to_dict(orient="records"),
                "eda_findings": per_source_reports.get(sid, {}).get("findings", []),   
            }
            for sid, df in dataframes.items()
        }
        
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=InspectPromptType.GENERATE_SUMMARY
        )
        user_prompt = (
            "Multiple input sources were provided. Decide how they should be "
            "combined into a single training frame before any preprocessing "
            "happens, grounded in the user's objective/query.\n\n"
            f"User objective/query: {objective or '(none given)'}\n\n"
            f"Deterministic signals:\n{json.dumps(signals, indent=2, default=str)}\n\n"
            "Choose exactly one strategy:\n"
            "  - 'union': sources are batches of the same kind of record — stack row-wise.\n"
            "  - 'join': sources describe related entities sharing a key column.\n"
            "  - 'unrelated': no defensible relationship — explain why in 'rationale'.\n\n"
            f"Per-source profiles:\n{json.dumps(profiles, indent=2, default=str)}"
        )
        
        schema = {
            "type": "object",
            "properties": {
                "strategy": {"type": "string", "enum": ["union", "join", "unrelated"]},
                "join_keys": {"type": "array", "items": {"type": "string"}},
                "join_type": {"type": "string", "enum": ["inner", "left", "right", "outer"]},
                "rationale": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["strategy", "rationale"]
        }

        response = self.get_response(
            fn_name="generate_json",
            model_input={"system_prompt": system_prompt, "user_prompt": user_prompt, "schema": schema},
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
        )

        if response is None:
            return {"strategy": "unrelated", "rationale": "Planner returned no response.", "confidence": "low"}
        try:
            return response if isinstance(response, Dict) else json.loads(response)
        except Exception:
            return {"strategy": "unrelated", "rationale": "Could not parse relationship response.", "confidence": "low"}


    # ── Internal helpers ──────────────────────────────────────────────────────

    def _available_inspections(self) -> list[Dict[str, str]]:
        return build_inspection_catalog()

    def _dataset_profile(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Lightweight dataset descriptor sent to the planner."""
        return {
            "rows": int(data.shape[0]),
            "columns": int(data.shape[1]),
            "column_names": list(data.columns),
            "dtypes": {col: str(dtype) for col, dtype in data.dtypes.items()},
            "missing_counts": data.isna().sum().to_dict(),
        }

    def _run_step(
        self,
        data: pd.DataFrame,
        step: Dict[str, Any],
        registry: Dict[str, DataInspectStrategy],
    ) -> Dict[str, Any]:
        """Execute a single plan step and return a structured step result."""
        inspection_name = str(step.get("inspection", ""))
        base = {
            "step": step.get("step"),
            "name": step.get("name"),
            "inspection": inspection_name,
            "objective": step.get("objective"),
            "actions": step.get("actions", []),
            "expected_output": step.get("expected_output", []),
        }

        strategy = registry.get(inspection_name)
        if strategy is None:
            logger.warning("Unknown inspection strategy: %s", inspection_name)
            return {**base, "status": "skipped", "error": f"Unknown strategy: {inspection_name}"}

        arguments = step.get("arguments") or {}
        if not isinstance(arguments, Dict):
            arguments = {}

        # Validate required arguments declared by the strategy before executing.
        required_specs = getattr(strategy.__class__, "argument_specs", []) or []
        missing_required = [
            spec.name for spec in required_specs if getattr(spec, "required", False)
            and spec.name not in arguments
        ]
        if missing_required:
            logger.warning(
                "Strategy %s missing required arguments: %s",
                inspection_name,
                missing_required,
            )
            return {
                **base,
                "status": "failed",
                "error": f"Missing required arguments: {', '.join(missing_required)}",
            }

        try:
            output = strategy.inspect(data, **arguments)
            return {**base, "status": "completed", "output": self._serialize(output)}
        except Exception as exc:
            logger.exception("Strategy %s failed: %s", inspection_name, exc)
            return {**base, "status": "failed", "error": str(exc)}

    def _execution_report(
        self, data: pd.DataFrame, executed_steps: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Assemble the top-level execution report dict."""
        counts = {"completed": 0, "failed": 0, "skipped": 0}
        for step in executed_steps:
            status = step.get("status", "skipped")
            if status in counts:
                counts[status] += 1

        overall = "completed" if counts["failed"] == 0 else "completed_with_errors"

        return {
            "task": "inspection",
            "status": overall,
            "dataset": self._dataset_profile(data),
            "summary": {
                "completed_steps": counts["completed"],
                "failed_steps": counts["failed"],
                "skipped_steps": counts["skipped"],
            },
            "steps": executed_steps,
        }

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _serialize(value: Any) -> Any:
        """Recursively convert strategy output to JSON-serializable primitives."""
        if isinstance(value, Dict):
            return {str(k): TabularDataInspectorAgent._serialize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [TabularDataInspectorAgent._serialize(v) for v in value]
        if hasattr(value, "to_dict"):
            try:
                return TabularDataInspectorAgent._serialize(value.to_dict())
            except Exception:
                pass
        if hasattr(value, "tolist"):
            try:
                return value.tolist()
            except Exception:
                pass
        if isinstance(value, float) and (value != value):  # NaN check
            return None
        return value

    @staticmethod
    def _sanitize_for_prompt(obj: Any, _depth: int = 0) -> Any:
        """
        Recursively trim the execution report before sending to the LLM:
        - Drop ``outlier_indices`` and ``missing_indices`` lists (too verbose).
        - Truncate any list longer than 20 items.
        - Cap recursion at depth 10 to guard against pathological nesting.
        """
        _SKIP_KEYS = {"outlier_indices", "missing_indices"}
        _MAX_LIST_LEN = 20
        _MAX_DEPTH = 10

        if _depth > _MAX_DEPTH:
            return "__truncated__"
        if isinstance(obj, Dict):
            return {
                k: TabularDataInspectorAgent._sanitize_for_prompt(v, _depth + 1)
                for k, v in obj.items()
                if k not in _SKIP_KEYS
            }
        if isinstance(obj, list):
            truncated = obj[:_MAX_LIST_LEN]
            return [
                TabularDataInspectorAgent._sanitize_for_prompt(v, _depth + 1)
                for v in truncated
            ]
        return obj

    # ── Parsing helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_plan(plan: str | Dict) -> Dict:
        if isinstance(plan, Dict):
            return plan
        if isinstance(plan, str):
            try:
                return json.loads(plan)
            except json.JSONDecodeError as exc:
                return {"error": f"Plan is not valid JSON: {exc}"}
        return {"error": f"Plan must be a JSON string or dict, got {type(plan).__name__}."}

    @staticmethod
    def _error_json(stage: str, message: str) -> str:
        return json.dumps({
            "stage": stage, 
            "status": "failed", 
            "error": message
        }, indent=2)

    # ── Response schemas ──────────────────────────────────────────────────────

    @staticmethod
    def _plan_schema() -> Dict:
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "dataset": {
                    "type": "object",
                    "properties": {
                        "rows": {"type": "integer"},
                        "columns": {"type": "integer"},
                        "column_names": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["rows", "columns", "column_names"],
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer"},
                            "name": {"type": "string"},
                            "inspection": {"type": "string"},
                            "objective": {"type": "string"},
                            "actions": {
                                "type": "array", 
                                "items": {
                                    "type": "string",
                                    "maxLength": 120
                                },
                                "maxItems": 5
                            },
                            "arguments": {"type": "object"},
                            "expected_output": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["step", "name", "inspection", "objective", "actions", "expected_output"],
                    },
                },
            },
            "required": ["task", "dataset", "steps"],
        }

    @staticmethod
    def _summary_schema() -> Dict:
        return {
            "type": "object",
            "properties": {
                "overall_assessment": {"type": "string"},
                "data_quality_score": {
                    "type": "number",
                    "description": "0–1 heuristic quality score based on missingness, outliers, and type issues.",
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "finding": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                        "required": ["column", "finding", "severity"],
                    },
                },
                "anomalies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "anomaly": {"type": "string"},
                        },
                        "required": ["column", "anomaly"],
                    },
                },
                "recommended_next_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "overall_assessment",
                "data_quality_score",
                "findings",
                "anomalies",
                "recommended_next_steps",
            ],
        }
