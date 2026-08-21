#!/usr/bin/python
"""
Tabular Data Model Builder Agent
=================================

Plans, fits, evaluates, and summarises one or more scikit-learn / XGBoost /
LightGBM models on a preprocessed tabular DataFrame produced by
``TabularDataProcessorAgent``.

Lifecycle
---------
1. ``plan``     — asks the LLM to pick models, hyperparameters, and evaluation
                  actions grounded in the dataset profile and preprocessing summary.
2. ``execute``  — instantiates each planned model, fits it, runs cross-validation,
                  and collects structured results.
3. ``summarize``— asks the LLM to interpret the execution report and emit a concise
                  model comparison with recommendations.
4. ``run``      — convenience wrapper that chains all three stages.

"""

# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
import joblib
from typing import Any
from pathlib import Path

# Third-Party Libraries
import numpy as np
import pandas as pd
from loguru import logger

# scikit-learn
from sklearn.linear_model import LogisticRegression, Ridge, Lasso
from sklearn.ensemble import (
    RandomForestClassifier,
    RandomForestRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    AdaBoostClassifier,
    AdaBoostRegressor
)
from sklearn.svm import SVC, SVR
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.model_selection import cross_validate, StratifiedKFold, KFold
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    roc_auc_score,
    mean_squared_error,
    mean_absolute_error,
    r2_score,
)

# Optional heavy boosters (graceful fallback if not installed)
try:
    from xgboost import XGBClassifier, XGBRegressor
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

try:
    from lightgbm import LGBMClassifier, LGBMRegressor
    _HAS_LGBM = True
except ImportError:
    _HAS_LGBM = False

# Abstract Base Class
from squirrel.modules.agents.abstract import IAgent
from squirrel.modules.providers import Provider

# Prompt generator
from squirrel.modules.prompts.builder.TabularDataPromptGenerator import (
    ModelBuilderPromptGenerator,
    ModelBuilderPromptType,
)


# ——————————————————————————————————————————————————————————————
# Model registry


def _build_model_registry() -> dict[str, dict]:
    """
    Build the unified model registry: short_key → {cls, task, description}.

    Each entry carries the constructor class and whether it targets
    "classification", "regression", or "both".
    """
    registry: dict[str, dict] = {
        # ── Linear / regularised ──────────────────────────────────────────────
        "logistic_regression": {
            "cls": LogisticRegression,
            "task": "classification",
            "description": "L2-regularised linear classifier; strong baseline for linearly separable data.",
            "required_params": [],
            "optional_params": {"C": 1.0, "max_iter": 1000, "solver": "lbfgs", "class_weight": None},
        },
        "ridge": {
            "cls": Ridge,
            "task": "regression",
            "description": "L2-regularised linear regressor; efficient and interpretable baseline.",
            "required_params": [],
            "optional_params": {"alpha": 1.0},
        },
        "lasso": {
            "cls": Lasso,
            "task": "regression",
            "description": "L1-regularised linear regressor; performs implicit feature selection.",
            "required_params": [],
            "optional_params": {"alpha": 1.0, "max_iter": 1000},
        },
        # ── Tree ensembles ────────────────────────────────────────────────────
        "random_forest_classifier": {
            "cls": RandomForestClassifier,
            "task": "classification",
            "description": "Bagged decision-tree ensemble; robust to noisy features.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "max_depth": None, "class_weight": None, "random_state": 42},
        },
        "random_forest_regressor": {
            "cls": RandomForestRegressor,
            "task": "regression",
            "description": "Bagged decision-tree ensemble for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "max_depth": None, "random_state": 42},
        },
        "gradient_boosting_classifier": {
            "cls": GradientBoostingClassifier,
            "task": "classification",
            "description": "Stage-wise boosted trees; excellent accuracy on medium-sized tabular data.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "learning_rate": 0.1, "max_depth": 4, "random_state": 42},
        },
        "gradient_boosting_regressor": {
            "cls": GradientBoostingRegressor,
            "task": "regression",
            "description": "Stage-wise boosted trees for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "learning_rate": 0.1, "max_depth": 4, "random_state": 42},
        },
        "extra_trees_classifier": {
            "cls": ExtraTreesClassifier,
            "task": "classification",
            "description": "Extremely randomised trees; faster than RF with comparable accuracy.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "random_state": 42},
        },
        "extra_trees_regressor": {
            "cls": ExtraTreesRegressor,
            "task": "regression",
            "description": "Extremely randomised trees for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 200, "random_state": 42},
        },
        "adaboost_classifier": {
            "cls": AdaBoostClassifier,
            "task": "classification",
            "description": "Adaptive boosting of weak classifiers; effective on clean, balanced data.",
            "required_params": [],
            "optional_params": {"n_estimators": 100, "learning_rate": 1.0, "random_state": 42},
        },
        "adaboost_regressor": {
            "cls": AdaBoostRegressor,
            "task": "regression",
            "description": "Adaptive boosting for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 100, "learning_rate": 1.0, "random_state": 42},
        },
        # ── Single-tree ───────────────────────────────────────────────────────
        "decision_tree_classifier": {
            "cls": DecisionTreeClassifier,
            "task": "classification",
            "description": "Single decision tree; highly interpretable but prone to overfitting.",
            "required_params": [],
            "optional_params": {"max_depth": 6, "random_state": 42},
        },
        "decision_tree_regressor": {
            "cls": DecisionTreeRegressor,
            "task": "regression",
            "description": "Single decision tree for regression.",
            "required_params": [],
            "optional_params": {"max_depth": 6, "random_state": 42},
        },
        # ── SVM ───────────────────────────────────────────────────────────────
        "svc": {
            "cls": SVC,
            "task": "classification",
            "description": "Support-vector classifier; effective in high-dimensional spaces.",
            "required_params": [],
            "optional_params": {"C": 1.0, "kernel": "rbf", "probability": True},
        },
        "svr": {
            "cls": SVR,
            "task": "regression",
            "description": "Support-vector regressor.",
            "required_params": [],
            "optional_params": {"C": 1.0, "kernel": "rbf"},
        },
        # ── KNN ───────────────────────────────────────────────────────────────
        "knn_classifier": {
            "cls": KNeighborsClassifier,
            "task": "classification",
            "description": "k-nearest-neighbours classifier; non-parametric, no training phase.",
            "required_params": [],
            "optional_params": {"n_neighbors": 5},
        },
        "knn_regressor": {
            "cls": KNeighborsRegressor,
            "task": "regression",
            "description": "k-nearest-neighbours regressor.",
            "required_params": [],
            "optional_params": {"n_neighbors": 5},
        },
    }

    # ── Optional heavy boosters ───────────────────────────────────────────────
    if _HAS_XGB:
        registry["xgb_classifier"] = {
            "cls": XGBClassifier,
            "task": "classification",
            "description": "XGBoost gradient-boosted trees; state-of-the-art on tabular data.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "max_depth": 6,
                "subsample": 0.8, "colsample_bytree": 0.8,
                "use_label_encoder": False, "eval_metric": "logloss",
                "random_state": 42,
            },
        }
        registry["xgb_regressor"] = {
            "cls": XGBRegressor,
            "task": "regression",
            "description": "XGBoost gradient-boosted trees for regression.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "max_depth": 6,
                "subsample": 0.8, "colsample_bytree": 0.8, "random_state": 42,
            },
        }

    if _HAS_LGBM:
        registry["lgbm_classifier"] = {
            "cls": LGBMClassifier,
            "task": "classification",
            "description": "LightGBM gradient-boosted trees; fast and memory-efficient.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "num_leaves": 63,
                "random_state": 42, "verbose": -1,
            },
        }
        registry["lgbm_regressor"] = {
            "cls": LGBMRegressor,
            "task": "regression",
            "description": "LightGBM gradient-boosted trees for regression.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "num_leaves": 63,
                "random_state": 42, "verbose": -1,
            },
        }

    return registry


# Built once at import time; the agent references this constant.
_MODEL_REGISTRY: dict[str, dict] = _build_model_registry()


def build_model_catalog() -> list[dict]:
    """
    Serialise ``_MODEL_REGISTRY`` into a list of catalog entries for the LLM.

    Each entry contains the model key, task type, description, required params,
    and the keys of optional params (without their defaults, to keep the prompt
    concise).
    """
    catalog = []
    for key, meta in _MODEL_REGISTRY.items():
        catalog.append({
            "model_key":       key,
            "task":            meta["task"],
            "description":     meta["description"],
            "required_params": meta["required_params"],
            "optional_params": list(meta["optional_params"].keys()),
        })
    return catalog


def build_action_catalog() -> list[dict]:
    """Return the catalog of post-fit actions the agent understands."""
    return [
        {
            "action_key":  "cv_evaluate",
            "description": "Run k-fold cross-validation and report mean ± std of scoring metrics.",
            "required_params": ["cv"],
            "optional_params": ["scoring"],
        },
        {
            "action_key":  "feature_importance",
            "description": "Extract feature importances from tree-based models.",
            "required_params": [],
            "optional_params": ["top_n"],
        },
        {
            "action_key":  "holdout_evaluate",
            "description": "Evaluate a fitted model on a held-out split and return all metrics.",
            "required_params": ["test_size"],
            "optional_params": [],
        },
    ]


# ——————————————————————————————————————————————————————————————
# Agent


class TabularDataModelBuilderAgent(IAgent):
    """
    Agent for building, evaluating, and comparing ML models on tabular data.

    **Description:**

        Accepts the processed ``pd.DataFrame`` and the structured summary dict
        from ``TabularDataProcessorAgent`` and orchestrates the full modelling
        lifecycle:

        1. ``plan``     — the LLM selects models and actions grounded in the
                          dataset profile and preprocessing summary.
        2. ``execute``  — each planned model is instantiated, optionally fitted,
                          cross-validated, and feature importances extracted.
        3. ``summarize``— the LLM interprets results and recommends the best model.
        4. ``run``      — chains all three stages for convenience.

    **Example usage:**

    .. code-block:: python

        agent = TabularDataModelBuilderAgent(target_column="label")

        processed_df, summary = processor_agent.run(raw_df)

        fitted_models, report = agent.run(
            data=processed_df,
            preprocessing_summary=summary,
            objective="binary classification — maximise ROC-AUC",
        )
    """

    _MAX_REGEN_ATTEMPTS: int = 2

    def __init__(
        self,
        target_column: str,
        prompt_generator: Any = ModelBuilderPromptGenerator(),
    ) -> None:
        """
        :param target_column: Name of the label / target column in the DataFrame.
        :param prompt_generator: Optional custom prompt generator; defaults to
            ``ModelBuilderPromptGenerator``.
        """
        super().__init__(
            prompt_generator=prompt_generator
        )
        self.target_column = target_column

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        data: pd.DataFrame,
        preprocessing_summary: dict | None = None,
        objective: str = "",
        max_refinements: int = 1,
    ) -> tuple[dict[str, Any], dict]:
        plan = self.plan(data, preprocessing_summary=preprocessing_summary or {}, objective=objective)
        fitted_models, execution = self.execute(data, plan)
        summary_dict = self._parse_summary(self.summarize(execution))

        best = (fitted_models, summary_dict)
        attempt = 0
        while attempt < max_refinements and self._needs_refinement(best[1]):
            attempt += 1
            feedback = self._refinement_feedback(best[1])
            logger.info("Model-building refinement attempt %s: %s", attempt, feedback)

            plan = self.plan(
                data, preprocessing_summary=preprocessing_summary or {},
                objective=f"{objective}\n\n{feedback}".strip(),
            )
            candidate_models, execution = self.execute(data, plan)
            candidate_summary = self._parse_summary(self.summarize(execution))

            if self._is_better(candidate_summary, best[1]):
                best = (candidate_models, candidate_summary)

        best[1]["refinement_attempts"] = attempt
        return best

    def plan(
        self,
        data: pd.DataFrame,
        preprocessing_summary: dict | None = None,
        objective: str = "",
    ) -> str:
        """
        Ask the LLM to produce an ordered model-building plan.

        The planner receives the dataset profile, preprocessing summary, and
        both catalogs (models + actions).  Invalid steps are flagged and
        regenerated up to ``_MAX_REGEN_ATTEMPTS`` times.

        :param data: Processed DataFrame.
        :param preprocessing_summary: Structured summary from the preprocessing agent.
        :param objective: Optional caller-supplied modelling goal.
        :return: JSON plan string.
        """
        dataset_profile = self._dataset_profile(data)

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ModelBuilderPromptType.GENERATE_PLAN,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=ModelBuilderPromptType.GENERATE_PLAN,
            objective=objective or (
                "Select the most appropriate models for this dataset and task type."
            ),
            dataset_profile=dataset_profile,
            preprocessing_summary=preprocessing_summary or {},
            model_catalog=build_model_catalog(),
            action_catalog=build_action_catalog(),
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

        plan_obj = self._parse_plan(response) if isinstance(response, str) else response
        if "error" in plan_obj:
            return self._error_json("plan", plan_obj.get("error", "Unknown error parsing plan."))

        plan_obj = self._validate_and_repair_plan(plan_obj, dataset_profile)
        return json.dumps(plan_obj, indent=2)

    def execute(
        self,
        data: pd.DataFrame,
        plan: str | dict,
    ) -> tuple[dict[str, Any], str]:
        """
        Execute the model-building plan.

        For each planned model step the agent:
          - Instantiates the model with the specified hyperparameters.
          - Fits it on the full dataset (minus the target column).
          - Runs any attached actions (cv_evaluate, feature_importance,
            holdout_evaluate).

        Unknown model keys and runtime exceptions are captured in the report
        without aborting the pipeline.

        :param data: Processed DataFrame.
        :param plan: JSON plan string or dict from ``plan()``.
        
        :return: ``(fitted_models_dict, json_execution_report_string)``
        """
        plan_obj = self._parse_plan(plan)
        if "error" in plan_obj:
            return {}, json.dumps(
                {"task": "model_building", "status": "failed", **plan_obj}, indent=2
            )

        if self.target_column not in data.columns:
            return {}, json.dumps({
                "task": "model_building",
                "status": "failed",
                "error": f"Target column '{self.target_column}' not found in DataFrame.",
            }, indent=2)

        X = data.drop(columns=[self.target_column])
        y = data[self.target_column]
        task_type = self._infer_task_type(y)

        # Defensively drop any non-numeric columns (e.g. datetime, object,
        # category) that survived preprocessing. sklearn/xgboost/lightgbm
        # estimators cannot fit on a DataFrame containing a datetime64 column
        # alongside float64 columns — numpy has no common dtype to promote to
        # and raises DTypePromotionError inside check_array/_data_from_pandas.
        # This has been observed even when the preprocessing summary claims
        # the offending column (e.g. 'Date') was already dropped.
        non_numeric_cols = X.select_dtypes(exclude=["number", "bool"]).columns.tolist()
        if non_numeric_cols:
            logger.warning(
                "Dropping non-numeric columns before model fitting (preprocessing "
                "should have handled these): %s", non_numeric_cols
            )
            X = X.drop(columns=non_numeric_cols)

        fitted_models: dict[str, Any] = {}
        executed_steps: list[dict[str, Any]] = []

        for step in plan_obj.get("steps", []):
            step_result, fitted_model = self._run_step(step, X, y, task_type)
            executed_steps.append(step_result)
            if fitted_model is not None:
                model_key = step.get("model_key", step.get("name", f"model_{len(fitted_models)}"))
                fitted_models[model_key] = fitted_model

        report = {
            "task":          "model_building",
            "status":        "completed" if all(s.get("status") != "failed" for s in executed_steps) else "completed_with_errors",
            "task_type":     task_type,
            "target_column": self.target_column,
            "n_features":    int(X.shape[1]),
            "n_samples":     int(X.shape[0]),
            "dropped_non_numeric_columns": non_numeric_cols,
            "dataset_profile": self._dataset_profile(data),
            "steps":         executed_steps,
        }

        return fitted_models, json.dumps(report, indent=2, default=str)

    def summarize(
        self,
        execution_report: str | dict,
    ) -> str:
        """
        Ask the LLM to interpret the execution report and produce a structured
        model-comparison summary with a recommended model.

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

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ModelBuilderPromptType.GENERATE_PLAN,
        )
        user_prompt = (
            "You are interpreting a completed model-building execution report.\n\n"
            "Produce a structured JSON summary with:\n"
            "  - task_type               (string: 'classification' or 'regression')\n"
            "  - overall_assessment      (one paragraph describing what was done)\n"
            "  - models_trained          (list of model keys that were successfully fitted)\n"
            "  - models_failed           (list of model keys that failed)\n"
            "  - best_model              (key of the model with the best CV performance)\n"
            "  - best_model_rationale    (one sentence explaining the choice)\n"
            "  - model_comparison        (list of dicts: {model_key, metric_name, mean, std})\n"
            "  - recommendations         (list of strings with actionable next steps)\n"
            "  - warnings                (list of strings for any issues encountered)\n\n"
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
            preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
        )

        if response is None:
            return self._error_json("summarize", "Provider did not return a response.")
        return response if isinstance(response, str) else json.dumps(response, indent=2)

    # ── Plan validation & repair ──────────────────────────────────────────────

    def _validate_and_repair_plan(
        self,
        plan_obj: dict,
        dataset_profile: dict,
    ) -> dict:
        """
        Walk every step in *plan_obj* and attempt regeneration for invalid ones.
        Mirrors the pattern used in ``TabularDataProcessorAgent``.
        """
        available_keys = set(_MODEL_REGISTRY.keys()) | {a["action_key"] for a in build_action_catalog()}

        for idx, step in enumerate(plan_obj.get("steps", [])):
            reason = self._validate_step(step, available_keys)
            if reason is None:
                plan_obj["steps"][idx]["status"] = "valid"
                continue

            plan_obj["steps"][idx]["status"] = "invalid"
            plan_obj["steps"][idx]["error"] = reason
            logger.info("Model plan step %s needs regeneration: %s", idx, reason)

            regenerated = False
            for _ in range(self._MAX_REGEN_ATTEMPTS):
                regen_resp = self._regenerate_step(step, reason, dataset_profile)
                if regen_resp is None:
                    continue

                try:
                    new_step = (
                        regen_resp if isinstance(regen_resp, dict)
                        else json.loads(regen_resp)
                    )
                except Exception:
                    continue

                # Unwrap envelope if needed
                if isinstance(new_step, dict) and "steps" in new_step and isinstance(new_step["steps"], list):
                    new_step = new_step["steps"][0] if new_step["steps"] else new_step

                if not isinstance(new_step, dict):
                    continue

                if self._validate_step(new_step, available_keys) is None:
                    plan_obj["steps"][idx] = new_step
                    logger.info("Successfully regenerated model step %s", idx)
                    regenerated = True
                    break

            if not regenerated:
                logger.warning("Failed to regenerate model step %s", idx)
                plan_obj["steps"][idx]["status"] = "invalid"
                plan_obj["steps"][idx]["error"] = (
                    f"Failed to regenerate after {self._MAX_REGEN_ATTEMPTS} attempts: {reason}"
                )

        return plan_obj

    def _validate_step(
        self,
        step: dict,
        available_keys: set[str],
    ) -> str | None:
        """
        Validate a single plan step.

        A step is valid when:
          - ``model_key`` (or ``action_key``) is a known registry/catalog entry.
          - All params listed as required are present.

        :return: Error string if invalid, ``None`` if valid.
        """
        key = step.get("model_key") or step.get("action_key") or step.get("name", "")
        if not key or key not in available_keys:
            return f"Unknown model or action key: '{key}'"

        if key in _MODEL_REGISTRY:
            required = _MODEL_REGISTRY[key].get("required_params", [])
            params: dict = step.get("hyperparameters") or step.get("params") or {}
            missing = [p for p in required if p not in params]
            if missing:
                return f"Missing required hyperparameters: {', '.join(missing)}"

        return None

    def _regenerate_step(
        self,
        step: dict,
        reason: str,
        dataset_profile: dict,
    ) -> dict | None:
        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=ModelBuilderPromptType.GENERATE_STEP,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=ModelBuilderPromptType.GENERATE_STEP,
            objective=f"Fix the invalid step. Reason: {reason}. Return a single corrected step object.",
            dataset_profile=dataset_profile,
            step=step,
            model_catalog=build_model_catalog(),
            action_catalog=build_action_catalog(),
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
            preference_model_names=["llama-3.3-70b-versatile", "models/gemini-2.5-pro"],
        )

    # ── Step execution ────────────────────────────────────────────────────────

    def _run_step(
        self,
        step: dict[str, Any],
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
    ) -> tuple[dict[str, Any], Any]:
        """
        Execute a single plan step.

        Model steps  → instantiate, fit, run any attached actions.
        Action-only steps (cv_evaluate on a previously fitted model) are not
        currently supported standalone and are recorded as skipped.

        :return: ``(step_result_dict, fitted_estimator_or_None)``
        """
        model_key: str = step.get("model_key", "")
        step_name: str = step.get("name", model_key)
        actions: list[dict] = step.get("actions", [])

        base: dict[str, Any] = {
            "step":      step.get("step"),
            "name":      step_name,
            "model_key": model_key,
            "objective": step.get("objective", ""),
        }

        # ── Unknown key ───────────────────────────────────────────────────────
        if model_key not in _MODEL_REGISTRY:
            logger.warning("Unknown model key: %s", model_key)
            return {**base, "status": "skipped", "error": f"Unknown model key: {model_key}"}, None

        meta = _MODEL_REGISTRY[model_key]

        # ── Verify task compatibility ─────────────────────────────────────────
        model_task = meta["task"]
        if model_task != "both" and task_type != model_task:
            msg = f"Model '{model_key}' is for {model_task} but task is {task_type}."
            logger.warning(msg)
            return {**base, "status": "skipped", "error": msg}, None

        # ── Instantiate ───────────────────────────────────────────────────────
        hyperparams: dict = {
            **meta["optional_params"],
            **(step.get("hyperparameters") or step.get("params") or {}),
        }

        try:
            estimator = meta["cls"](**hyperparams)
        except Exception as exc:
            logger.exception("Failed to instantiate %s: %s", model_key, exc)
            return {**base, "status": "failed", "error": f"Instantiation error: {exc}"}, None

        # ── Fit ───────────────────────────────────────────────────────────────
        try:
            estimator.fit(X, y)
        except Exception as exc:
            logger.exception("Failed to fit %s: %s", model_key, exc)
            return {**base, "status": "failed", "error": f"Fit error: {exc}"}, None

        # ── Run attached actions ──────────────────────────────────────────────
        action_results: list[dict] = []
        for action_spec in actions:
            action_key = action_spec.get("action_key", "")
            action_params: dict = action_spec.get("params") or {}

            if action_key == "cv_evaluate":
                result = self._action_cv_evaluate(estimator, X, y, task_type, action_params)
            elif action_key == "feature_importance":
                result = self._action_feature_importance(estimator, X.columns.tolist(), action_params)
            elif action_key == "holdout_evaluate":
                result = self._action_holdout_evaluate(estimator, X, y, task_type, action_params)
            else:
                result = {"action_key": action_key, "status": "skipped", "error": f"Unknown action: {action_key}"}

            action_results.append(result)

        return {
            **base,
            "status":   "completed",
            "actions":  action_results,
        }, estimator

    # ── Actions ───────────────────────────────────────────────────────────────

    def _action_cv_evaluate(
        self,
        estimator: Any,
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
        params: dict,
    ) -> dict:
        """
        Run stratified (classification) or plain (regression) k-fold CV and
        return mean ± std for each scoring metric.
        """
        cv_folds: int = int(params.get("cv", 5))
        scoring: list[str] | str = params.get("scoring") or (
            ["accuracy", "f1_weighted", "roc_auc_ovr_weighted"]
            if task_type == "classification"
            else ["r2", "neg_mean_squared_error", "neg_mean_absolute_error"]
        )

        cv_splitter = (
            StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=42)
            if task_type == "classification"
            else KFold(n_splits=cv_folds, shuffle=True, random_state=42)
        )

        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cv_scores = cross_validate(
                    estimator, X, y,
                    cv=cv_splitter,
                    scoring=scoring if isinstance(scoring, list) else [scoring],
                    return_train_score=False,
                    error_score="raise",
                )

            metrics: dict[str, dict] = {}
            for key, values in cv_scores.items():
                if key.startswith("test_"):
                    metric_name = key[len("test_"):]
                    metrics[metric_name] = {
                        "mean": float(np.mean(values)),
                        "std":  float(np.std(values)),
                        "values": [float(v) for v in values],
                    }

            return {
                "action_key": "cv_evaluate",
                "status":     "completed",
                "cv_folds":   cv_folds,
                "metrics":    metrics,
            }
        except Exception as exc:
            logger.warning("cv_evaluate failed: %s", exc)
            return {"action_key": "cv_evaluate", "status": "failed", "error": str(exc)}

    def _action_feature_importance(
        self,
        estimator: Any,
        feature_names: list[str],
        params: dict,
    ) -> dict:
        """
        Extract ``feature_importances_`` or ``coef_`` from the estimator.
        Returns the top-N features sorted by importance descending.
        """
        top_n: int = int(params.get("top_n", 20))

        importances: np.ndarray | None = None
        source: str = ""

        if hasattr(estimator, "feature_importances_"):
            importances = np.asarray(estimator.feature_importances_)
            source = "feature_importances_"
        elif hasattr(estimator, "coef_"):
            coef = np.asarray(estimator.coef_)
            importances = np.abs(coef).mean(axis=0) if coef.ndim > 1 else np.abs(coef)
            source = "coef_"
        else:
            return {
                "action_key": "feature_importance",
                "status":     "skipped",
                "error":      "Estimator has no feature_importances_ or coef_ attribute.",
            }

        if importances.shape[0] != len(feature_names):
            return {
                "action_key": "feature_importance",
                "status":     "failed",
                "error":      "Mismatch between importance array length and number of features.",
            }

        order = np.argsort(importances)[::-1][:top_n]
        ranked = [
            {"feature": feature_names[i], "importance": float(importances[i])}
            for i in order
        ]

        return {
            "action_key": "feature_importance",
            "status":     "completed",
            "source":     source,
            "top_features": ranked,
        }

    def _action_holdout_evaluate(
        self,
        estimator: Any,
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
        params: dict,
    ) -> dict:
        """
        Re-split the data, refit on the train split, and evaluate on the hold-out.

        .. note::
            The estimator passed in is already fitted on the full dataset.
            This action *clones* it, refits on the train split, and evaluates on
            the test split — so the fitted estimator stored in ``fitted_models``
            remains trained on the full dataset.
        """
        from sklearn.base import clone
        from sklearn.model_selection import train_test_split

        test_size: float = float(params.get("test_size", 0.2))

        try:
            stratify = y if task_type == "classification" else None
            X_tr, X_te, y_tr, y_te = train_test_split(
                X, y, test_size=test_size, random_state=42, stratify=stratify
            )
            cloned = clone(estimator)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cloned.fit(X_tr, y_tr)
            y_pred = cloned.predict(X_te)

            if task_type == "classification":
                metrics = {
                    "accuracy": float(accuracy_score(y_te, y_pred)),
                    "f1_weighted": float(f1_score(y_te, y_pred, average="weighted", zero_division=0)),
                }
                if hasattr(cloned, "predict_proba"):
                    try:
                        proba = cloned.predict_proba(X_te)
                        n_classes = proba.shape[1]
                        if n_classes == 2:
                            metrics["roc_auc"] = float(roc_auc_score(y_te, proba[:, 1]))
                        else:
                            metrics["roc_auc_ovr"] = float(
                                roc_auc_score(y_te, proba, multi_class="ovr", average="weighted")
                            )
                    except Exception:
                        pass
            else:
                metrics = {
                    "r2":   float(r2_score(y_te, y_pred)),
                    "mse":  float(mean_squared_error(y_te, y_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_te, y_pred))),
                    "mae":  float(mean_absolute_error(y_te, y_pred)),
                }

            return {
                "action_key": "holdout_evaluate",
                "status":     "completed",
                "test_size":  test_size,
                "n_test":     int(len(y_te)),
                "metrics":    metrics,
            }

        except Exception as exc:
            logger.warning("holdout_evaluate failed: %s", exc)
            return {"action_key": "holdout_evaluate", "status": "failed", "error": str(exc)}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _infer_task_type(y: pd.Series) -> str:
        """
        Infer whether the task is classification or regression from the target.

        Rules (applied in order):
          1. object / bool / category dtype → classification.
          2. integer dtype with ≤ 20 unique values → classification.
          3. Otherwise → regression.
        """
        if y.dtype == object or y.dtype.name in ("bool", "category"):
            return "classification"
        if pd.api.types.is_integer_dtype(y) and y.nunique() <= 20:
            return "classification"
        return "regression"

    @staticmethod
    def _dataset_profile(data: pd.DataFrame) -> dict[str, Any]:
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
                "task_type": {"type": "string", "enum": ["classification", "regression"]},
                "rationale": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": TabularDataModelBuilderAgent._step_schema(),
                },
            },
            "required": ["task", "task_type", "steps"],
        }

    @staticmethod
    def _step_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "step":      {"type": "integer"},
                "name":      {"type": "string"},
                "model_key": {"type": "string"},
                "objective": {"type": "string"},
                "hyperparameters": {"type": "object"},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_key": {"type": "string"},
                            "params":     {"type": "object"},
                        },
                        "required": ["action_key"],
                    },
                },
            },
            "required": ["step", "name", "model_key", "objective"],
        }

    @staticmethod
    def _summary_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "task_type":            {"type": "string"},
                "overall_assessment":   {"type": "string"},
                "models_trained":       {"type": "array", "items": {"type": "string"}},
                "models_failed":        {"type": "array", "items": {"type": "string"}},
                "best_model":           {"type": "string"},
                "best_model_rationale": {"type": "string"},
                "model_comparison": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "model_key":   {"type": "string"},
                            "metric_name": {"type": "string"},
                            "mean":        {"type": "number"},
                            "std":         {"type": "number"},
                        },
                        "required": ["model_key", "metric_name", "mean"],
                    },
                },
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "warnings":        {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "task_type", "overall_assessment",
                "models_trained", "models_failed",
                "best_model", "best_model_rationale",
                "model_comparison", "recommendations", "warnings",
            ],
        }
        

    @staticmethod
    def save_models(
        fitted_models: dict[str, Any],
        output_dir: str | Path = "models",
    ) -> None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for name, model in fitted_models.items():
            joblib.dump(model, output_dir / f"{name}.joblib")
    
    def _parse_summary(self, summary_str):
        try:
            return json.loads(summary_str) if isinstance(summary_str, str) else summary_str
        except json.JSONDecodeError:
            return {"raw": summary_str}

    @staticmethod
    def _needs_refinement(summary: dict) -> bool:
        return bool(summary.get("models_failed")) or not summary.get("best_model")

    @staticmethod
    def _refinement_feedback(summary: dict) -> str:
        return (
            f"A previous attempt failed to fit: {summary.get('models_failed') or []}. "
            "Pick different models or hyperparameters, and avoid repeating these failures."
        )

    @staticmethod
    def _best_metric_mean(summary: dict) -> float:
        best_key = summary.get("best_model")
        for entry in summary.get("model_comparison") or []:
            if entry.get("model_key") == best_key:
                return float(entry.get("mean") or 0.0)
        return 0.0

    @staticmethod
    def _is_better(candidate: dict, current: dict) -> bool:
        if not candidate.get("best_model"):
            return False
        if not current.get("best_model"):
            return True
        return TabularDataModelBuilderAgent._best_metric_mean(candidate) > \
            TabularDataModelBuilderAgent._best_metric_mean(current)
