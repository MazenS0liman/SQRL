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
import inspect
import time
from typing import Any
from pathlib import Path

# Third-Party Libraries
import numpy as np
import pandas as pd
from loguru import logger
from squirrel.core.logging import log_stage

# scikit-learn
from sklearn.base import clone
from sklearn.model_selection import (
    cross_validate,
    learning_curve,
    StratifiedKFold,
    KFold,
    train_test_split,
)
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    roc_auc_score,
    mean_squared_error,
    mean_absolute_error,
    r2_score,
)
# Abstract Base Class
from squirrel.modules.agents.abstract import IAgent
from squirrel.modules.providers import Provider

# Prompt generator
from squirrel.modules.prompts.builder.TabularDataPromptGenerator import (
    ModelBuilderPromptGenerator,
    ModelBuilderPromptType
)

# Models
try:
    from squirrel.models.classification import XGBClassifierModel, XGBRegressorModel
    _HAS_XGB = XGBClassifierModel is not None and XGBRegressorModel is not None
except ImportError:
    _HAS_XGB = False

try:
    from squirrel.models.classification import LGBMClassifierModel, LGBMRegressorModel
    _HAS_LGBM = LGBMClassifierModel is not None and LGBMRegressorModel is not None
except ImportError:
    _HAS_LGBM = False

from squirrel.models.classification import (
    AdaBoostClassifierModel,
    AdaBoostRegressorModel,
    DecisionTreeClassifierModel,
    DecisionTreeRegressorModel,
    ExtraTreesClassifierModel,
    ExtraTreesRegressorModel,
    GradientBoostingClassifierModel,
    GradientBoostingRegressorModel,
    KNeighborsClassifierModel,
    KNeighborsRegressorModel,
    LassoModel,
    LogisticRegressionModel,
    RandomForestClassifierModel,
    RandomForestRegressorModel,
    RidgeModel,
    SVCModel,
    SVRModel,
)
from squirrel.models.time_series import BiLSTMPredictor, LSTMAttentionPredictor
_HAS_TENSORFLOW = True

# User-facing hyperparameter metadata (kept separate from the estimator
# registry so the API can serve a catalog without importing TensorFlow).
from squirrel.models.hyperparameters import (
    HYPERPARAMETER_SPECS,
    build_model_catalog as build_model_catalog_with_params,
    normalize_hyperparameters,
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
            "cls": LogisticRegressionModel,
            "task": "classification",
            "description": "L2-regularised linear classifier; strong baseline for linearly separable data.",
            "required_params": [],
            "optional_params": {"C": 1.0, "max_iter": 1000, "solver": "lbfgs", "class_weight": None},
        },
        "ridge": {
            "cls": RidgeModel,
            "task": "regression",
            "description": "L2-regularised linear regressor; efficient and interpretable baseline.",
            "required_params": [],
            "optional_params": {"alpha": 1.0},
        },
        "lasso": {
            "cls": LassoModel,
            "task": "regression",
            "description": "L1-regularised linear regressor; performs implicit feature selection.",
            "required_params": [],
            "optional_params": {"alpha": 1.0, "max_iter": 1000},
        },
        # ── Tree ensembles ────────────────────────────────────────────────────
        "random_forest_classifier": {
            "cls": RandomForestClassifierModel,
            "task": "classification",
            "description": "Bagged decision-tree ensemble; robust to noisy features.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "max_depth": None, "class_weight": None, "random_state": 42, "n_jobs": -1},
        },
        "random_forest_regressor": {
            "cls": RandomForestRegressorModel,
            "task": "regression",
            "description": "Bagged decision-tree ensemble for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "max_depth": None, "random_state": 42, "n_jobs": -1},
        },
        "gradient_boosting_classifier": {
            "cls": GradientBoostingClassifierModel,
            "task": "classification",
            "description": "Stage-wise boosted trees; excellent accuracy on medium-sized tabular data.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "learning_rate": 0.1, "max_depth": 4, "random_state": 42},
        },
        "gradient_boosting_regressor": {
            "cls": GradientBoostingRegressorModel,
            "task": "regression",
            "description": "Stage-wise boosted trees for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "learning_rate": 0.1, "max_depth": 4, "random_state": 42},
        },
        "extra_trees_classifier": {
            "cls": ExtraTreesClassifierModel,
            "task": "classification",
            "description": "Extremely randomised trees; faster than RF with comparable accuracy.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "random_state": 42},
        },
        "extra_trees_regressor": {
            "cls": ExtraTreesRegressorModel,
            "task": "regression",
            "description": "Extremely randomised trees for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "random_state": 42},
        },
        "adaboost_classifier": {
            "cls": AdaBoostClassifierModel,
            "task": "classification",
            "description": "Adaptive boosting of weak classifiers; effective on clean, balanced data.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "learning_rate": 1.0, "random_state": 42},
        },
        "adaboost_regressor": {
            "cls": AdaBoostRegressorModel,
            "task": "regression",
            "description": "Adaptive boosting for regression.",
            "required_params": [],
            "optional_params": {"n_estimators": 50, "learning_rate": 1.0, "random_state": 42},
        },
        # ── Single-tree ───────────────────────────────────────────────────────
        "decision_tree_classifier": {
            "cls": DecisionTreeClassifierModel,
            "task": "classification",
            "description": "Single decision tree; highly interpretable but prone to overfitting.",
            "required_params": [],
            "optional_params": {"max_depth": 6, "random_state": 42},
        },
        "decision_tree_regressor": {
            "cls": DecisionTreeRegressorModel,
            "task": "regression",
            "description": "Single decision tree for regression.",
            "required_params": [],
            "optional_params": {"max_depth": 6, "random_state": 42},
        },
        # ── SVM ───────────────────────────────────────────────────────────────
        "svc": {
            "cls": SVCModel,
            "task": "classification",
            "description": "Support-vector classifier; effective in high-dimensional spaces.",
            "required_params": [],
            "optional_params": {"C": 1.0, "kernel": "rbf", "probability": True},
        },
        "svr": {
            "cls": SVRModel,
            "task": "regression",
            "description": "Support-vector regressor.",
            "required_params": [],
            "optional_params": {"C": 1.0, "kernel": "rbf"},
        },
        # ── KNN ───────────────────────────────────────────────────────────────
        "knn_classifier": {
            "cls": KNeighborsClassifierModel,
            "task": "classification",
            "description": "k-nearest-neighbours classifier; non-parametric, no training phase.",
            "required_params": [],
            "optional_params": {"n_neighbors": 5},
        },
        "knn_regressor": {
            "cls": KNeighborsRegressorModel,
            "task": "regression",
            "description": "k-nearest-neighbours regressor.",
            "required_params": [],
            "optional_params": {"n_neighbors": 5},
        },
    }

    # ── Optional heavy boosters ───────────────────────────────────────────────
    if _HAS_XGB:
        registry["xgb_classifier"] = {
            "cls": XGBClassifierModel,
            "task": "classification",
            "description": "XGBoost gradient-boosted trees; state-of-the-art on tabular data.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "max_depth": 6,
                "subsample": 0.8, "colsample_bytree": 0.8,
                "use_label_encoder": False, "eval_metric": "logloss",
                "random_state": 42, "tree_method": "hist"
            },
        }
        registry["xgb_regressor"] = {
            "cls": XGBRegressorModel,
            "task": "regression",
            "description": "XGBoost gradient-boosted trees for regression.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "max_depth": 6,
                "subsample": 0.8, "colsample_bytree": 0.8, "random_state": 42,
                "n_jobs": -1, "tree_method": "hist",
            },
        }

    if _HAS_LGBM:
        registry["lgbm_classifier"] = {
            "cls": LGBMClassifierModel,
            "task": "classification",
            "description": "LightGBM gradient-boosted trees; fast and memory-efficient.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "num_leaves": 63,
                "random_state": 42, "verbose": -1, "n_jobs": -1,
            },
        }
        registry["lgbm_regressor"] = {
            "cls": LGBMRegressorModel,
            "task": "regression",
            "description": "LightGBM gradient-boosted trees for regression.",
            "required_params": [],
            "optional_params": {
                "n_estimators": 300, "learning_rate": 0.05, "num_leaves": 63,
                "random_state": 42, "verbose": -1, "n_jobs": -1,
            },
        }

    if _HAS_TENSORFLOW:
        registry["bilstm"] = {
            "cls": BiLSTMPredictor,
            "task": "regression",
            "sequential": True,
            "description": "Bidirectional LSTM for ordered numeric observations and multi-step forecasting.",
            "required_params": [],
            "optional_params": {"window_size": 30, "difference_order": 1},
        }
        registry["lstm_attention"] = {
            "cls": LSTMAttentionPredictor,
            "task": "regression",
            "sequential": True,
            "description": "LSTM with attention for ordered numeric observations and multi-step forecasting.",
            "required_params": [],
            "optional_params": {"lstm_units": 64, "window_size": 30, "difference_order": 1},
        }

    return registry


# Built once at import time; the agent references this constant.
_MODEL_REGISTRY: dict[str, dict] = _build_model_registry()


def build_model_catalog(include_hyperparameters: bool = False) -> list[dict]:
    """
    Serialise ``_MODEL_REGISTRY`` into a list of catalog entries.

    The default shape keeps the prompt compact for the LLM planner. With
    ``include_hyperparameters=True`` each entry also carries the typed,
    user-facing hyperparameter specs used by the workspace UI.
    """
    if include_hyperparameters:
        return build_model_catalog_with_params(_MODEL_REGISTRY)

    catalog = []
    for key, meta in _MODEL_REGISTRY.items():
        catalog.append({
            "model_key":       key,
            "task":            meta["task"],
            "description":     meta["description"],
            "required_params": meta["required_params"],
            "optional_params": list(meta["optional_params"].keys()),
            "sequential":      meta.get("sequential", False),
        })
    return catalog


def build_action_catalog() -> list[dict]:
    """
    Return the catalog of post-fit actions the agent understands.
    """
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
    _MODEL_SELECTION_ROWS: int = 100_000
    _SLOW_MODEL_ROWS: int = 50_000

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
        started_at = time.perf_counter()
        log_stage(
            "MODEL",
            "Model build started: target={}, rows={}, columns={}, objective={!r}",
            self.target_column,
            len(data),
            len(data.columns),
            objective,
        )
        # Generate the initial plan, execute it, and summarize the results.
        plan = self.plan(
            data,
            preprocessing_summary=preprocessing_summary or {},
            objective=objective
        )

        # Execute the plan and summarize the results.
        fitted_models, execution = self.execute(data, plan)
        summary_dict = self._with_model_details(
            self._parse_summary(self.summarize(execution)),
            execution,
            preprocessing_summary,
        )

        best = (fitted_models, summary_dict)
        attempt = 0
        while attempt < max_refinements and self._needs_refinement(best[1]):
            attempt += 1
            feedback = self._refinement_feedback(best[1])
            log_stage("MODEL", "Refinement attempt {}: {}", attempt, feedback)

            plan = self.plan(
                data, preprocessing_summary=preprocessing_summary or {},
                objective=f"{objective}\n\n{feedback}".strip(),
            )
            candidate_models, execution = self.execute(data, plan)
            candidate_summary = self._with_model_details(
                self._parse_summary(self.summarize(execution)),
                execution,
                preprocessing_summary,
            )

            if self._is_better(candidate_summary, best[1]):
                best = (candidate_models, candidate_summary)

        best[1]["refinement_attempts"] = attempt
        logger.info(
            "Model build finished: target={}, trained={}, failed={}, best={}, elapsed_seconds={:.2f}",
            self.target_column,
            best[1].get("models_trained", []),
            best[1].get("models_failed", []),
            best[1].get("best_model"),
            time.perf_counter() - started_at,
        )
        return best

    def run_selected(
        self,
        data: pd.DataFrame,
        model_key: str,
        hyperparameters: dict | None = None,
        epochs: int | None = None,
        preprocessing_summary: dict | None = None,
        *,
        refit: bool = False,
    ) -> tuple[dict[str, Any], dict]:
        """
        Train one explicitly chosen model instead of asking the LLM to plan.

        Used both by ``POST /build/structured`` when the user picks a model at
        the start and by ``POST /models/{key}/retrain``. Values are validated
        against the public hyperparameter catalog before they reach the
        estimator, and neural models receive ``epochs``/``batch_size`` as fit
        arguments rather than constructor arguments.

        :raises ValueError: if the model is unknown, incompatible with the
            target task, or its hyperparameters are invalid.
        """
        plan = self._selected_plan(
            data, model_key, hyperparameters, epochs, preprocessing_summary
        )
        fitted_models, execution = self.execute(data, plan)
        report = self._parse_plan(execution)
        steps = report.get("steps") or []
        step = steps[0] if steps else {}
        if step.get("status") != "completed":
            raise ValueError(
                step.get("error") or f"Model '{model_key}' could not be trained."
            )

        summary = self._selected_summary(
            report, step, model_key, preprocessing_summary, refit=refit
        )
        return fitted_models, summary

    def _selected_plan(
        self,
        data: pd.DataFrame,
        model_key: str,
        hyperparameters: dict | None = None,
        epochs: int | None = None,
        preprocessing_summary: dict | None = None,
    ) -> dict:
        """Build a single-step plan for a user-selected model."""
        meta = _MODEL_REGISTRY.get(model_key)
        if not meta:
            raise ValueError(f"Unknown model '{model_key}'.")

        if self.target_column not in data.columns:
            raise ValueError(f"Target column '{self.target_column}' not found in data.")

        task_type = self._infer_task_type(data[self.target_column])
        if meta["task"] not in ("both", task_type):
            raise ValueError(
                f"Model '{model_key}' trains a {meta['task']} model, but the target "
                f"column looks like a {task_type} task."
            )
        if meta.get("sequential") and not self._is_sequential_data(
            data, preprocessing_summary or {}
        ):
            raise ValueError(
                f"Model '{model_key}' needs ordered (time-series) data, but this "
                "workspace does not look sequential."
            )

        overrides = dict(hyperparameters or {})
        if epochs is not None:
            overrides["epochs"] = epochs
        normalised = normalize_hyperparameters(model_key, overrides)

        # ``normalize_hyperparameters`` returns constructor + fit params; split
        # them using the catalog metadata so Keras-only values never reach
        # sklearn constructors.
        fit_param_names = {
            spec["name"]
            for spec in HYPERPARAMETER_SPECS.get(model_key, [])
            if spec.get("fit_param")
        }
        fit_params = {
            name: normalised.pop(name)
            for name in list(normalised)
            if name in fit_param_names
        }

        return {
            "task": "model_building",
            "task_type": task_type,
            "rationale": f"User-selected model: {model_key}.",
            "steps": [{
                "step": 1,
                "name": model_key,
                "model_key": model_key,
                "objective": "Train the model chosen by the user with custom hyperparameters.",
                "hyperparameters": normalised,
                "fit_params": fit_params,
                "actions": [],
                "status": "valid",
            }],
        }

    def _selected_summary(
        self,
        report: dict,
        step: dict,
        model_key: str,
        preprocessing_summary: dict | None,
        *,
        refit: bool,
    ) -> dict:
        """Turn a selected-model execution report into a workspace summary."""
        comparison: list[dict[str, Any]] = []
        for action in step.get("actions") or []:
            if action.get("status") != "completed":
                continue
            for name, metric in (action.get("metrics") or {}).items():
                if isinstance(metric, dict) and metric.get("mean") is not None:
                    comparison.append({
                        "model_key": model_key,
                        "metric_name": name,
                        "mean": metric["mean"],
                        "std": metric.get("std"),
                    })

        head = "Retrained" if refit else "Trained"
        summary: dict[str, Any] = {
            "task_type": report.get("task_type"),
            "overall_assessment": (
                f"{head} {model_key} on {report.get('n_samples', 0)} rows and "
                f"{report.get('n_features', 0)} features."
            ),
            "models_trained": [model_key],
            "models_failed": [],
            "best_model": model_key,
            "best_model_rationale": (
                "Retrained with your chosen hyperparameters."
                if refit else "You selected this model for the initial build."
            ),
            "model_comparison": comparison,
            "recommendations": [],
            "warnings": [
                f"Dropped non-numeric columns: {', '.join(map(str, cols))}"
                for cols in [report.get("dropped_non_numeric_columns") or []]
                if cols
            ],
            "refit": refit,
        }
        summary = self._with_model_details(summary, report, preprocessing_summary)
        details = summary.get("model_details") or {}
        details["refit"] = refit
        summary["hyperparameters_by_model"] = {
            model_key: step.get("hyperparameters") or {}
        }
        if step.get("fit_params"):
            summary["hyperparameters_by_model"][model_key].update(step["fit_params"])
        return summary

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
        log_stage(
            "MODEL",
            "Generating model plan for {} rows and {} columns",
            len(data),
            len(data.columns),
        )
        dataset_profile = self._dataset_profile(data)

        # Infer task type up front from the target column dtype so the model
        # catalog sent to the planner can be filtered to relevant models only.
        # Previously every registered model
        inferred_task: str | None = None
        if self.target_column in data.columns:
            try:
                inferred_task = self._infer_task_type(data[self.target_column])
            except Exception:
                inferred_task = None

        full_model_catalog = build_model_catalog()
        model_catalog = (
            [m for m in full_model_catalog if m["task"] in (inferred_task, "both")]
            if inferred_task else full_model_catalog
        )
        is_sequential = self._is_sequential_data(data, preprocessing_summary or {})
        if is_sequential and not _HAS_TENSORFLOW:
            logger.warning(
                "Sequential data detected but TensorFlow is unavailable; "
                "LSTM candidates will be omitted from the model portfolio."
            )
        if not is_sequential:
            model_catalog = [m for m in model_catalog if not m.get("sequential", False)]
        if len(data) > self._SLOW_MODEL_ROWS:
            model_catalog = [
                model for model in model_catalog
                if not (
                    model["model_key"] in {"svc", "svr"}
                    or model["model_key"].startswith("knn")
                    or model["model_key"].startswith("gradient_boosting_")
                )
            ]

        logger.info(
            "Model plan context: task={}, sequential={}, candidates={}",
            inferred_task or "unknown",
            is_sequential,
            [model["model_key"] for model in model_catalog],
        )

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
            model_catalog=model_catalog,
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
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-3.8-flash",
                "models/gemini-2.5-flash"
            ]
        )

        if response is None:
            return self._error_json("plan", "Provider did not return a response.")

        plan_obj = self._parse_plan(response) if isinstance(response, str) else response
        if "error" in plan_obj:
            return self._error_json("plan", plan_obj.get("error", "Unknown error parsing plan."))

        # NOTE: _validate_and_repair_plan / _regenerate_step still validate
        # against the FULL _MODEL_REGISTRY (unfiltered), so a wrong-task model
        # key is correctly flagged as invalid rather than silently accepted —
        # filtering only shrinks what's shown to the planner, not what's valid.
        plan_obj = self._validate_and_repair_plan(plan_obj, dataset_profile)
        plan_obj = self._ensure_plan_coverage(
            plan_obj,
            inferred_task=inferred_task,
            is_sequential=is_sequential,
            n_rows=len(data),
        )
        return json.dumps(plan_obj, indent=2)

    def execute(
        self,
        data: pd.DataFrame,
        plan: str | dict,
    ) -> tuple[dict[str, Any], str]:
        """
        Execute the model-building plan.

        For each planned model step the agent:
          * Instantiates the model with the specified hyperparameters.
          * Fits it on the bounded selection sample (minus the target column).
          * Runs any attached actions (cv_evaluate, feature_importance,
            holdout_evaluate).

        After comparison, the winning non-sequential model is refit on the
        complete dataset.

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
        is_sequential = self._is_sequential_data(data) or any(
            _MODEL_REGISTRY.get(step.get("model_key"), {}).get("sequential", False)
            for step in plan_obj.get("steps", [])
        )

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
        selection_models: dict[str, Any] = {}
        executed_steps: list[dict[str, Any]] = []
        X_selection, y_selection = self._selection_data(X, y, task_type)

        logger.info(
            "Executing model plan: task={}, sequential={}, steps={}, features={}, samples={}",
            task_type,
            is_sequential,
            len(plan_obj.get("steps", [])),
            X.shape[1],
            X.shape[0],
        )

        for index, step in enumerate(plan_obj.get("steps", []), start=1):
            step_started_at = time.perf_counter()
            log_stage(
                "MODEL",
                "Starting model {}/{}: {}",
                index,
                len(plan_obj.get("steps", [])),
                step.get("model_key", step.get("name")),
            )
            step_result, fitted_model = self._run_step(
                step, X_selection, y_selection, task_type, is_sequential=is_sequential
            )
            executed_steps.append(step_result)
            if fitted_model is not None:
                model_key = step.get("model_key", step.get("name", f"model_{len(fitted_models)}"))
                fitted_models[model_key] = fitted_model
                selection_models[model_key] = fitted_model
            logger.info(
                "Model step finished: model={}, status={}, elapsed_seconds={:.2f}, errors={}",
                step_result.get("model_key"),
                step_result.get("status"),
                time.perf_counter() - step_started_at,
                step_result.get("error"),
            )

        winner_key = self._best_step_key(executed_steps, task_type)
        if winner_key:
            winner_step = next(
                step for step in plan_obj["steps"]
                if step.get("model_key") == winner_key
            )
            final_model = self._fit_full_model(winner_step, X, y)
            if final_model is not None:
                fitted_models = {winner_key: final_model}
                log_stage(
                    "MODEL",
                    "Refit winning model on full dataset: {} ({} samples)",
                    winner_key,
                    len(X),
                )

        # For sequential plans the metric-based winner is unavailable; use the
        # first completed candidate so the UI still has an algorithm + curve.
        algorithm_key = winner_key or next(
            (s.get("model_key") for s in executed_steps if s.get("status") == "completed"),
            None,
        )
        winner_result = next(
            (s for s in executed_steps if s.get("model_key") == algorithm_key),
            None,
        )
        training_history = self._training_history(
            estimator=selection_models.get(algorithm_key),
            X=X_selection,
            y=y_selection,
            task_type=task_type,
            step_result=winner_result,
        )

        report = {
            "task":          "model_building",
            "status":        "completed" if all(s.get("status") != "failed" for s in executed_steps) else "completed_with_errors",
            "task_type":     task_type,
            "target_column": self.target_column,
            "algorithm":     algorithm_key,
            "n_features":    int(X.shape[1]),
            "n_samples":     int(X.shape[0]),
            "n_selection_samples": int(X_selection.shape[0]),
            "dropped_non_numeric_columns": non_numeric_cols,
            "is_sequential": is_sequential,
            "input_schema":  self._input_schema(X),
            "training_history": training_history,
            "dataset_profile": self._dataset_profile(data),
            "steps":         executed_steps,
        }

        return fitted_models, json.dumps(report, indent=2, default=str)

    def _selection_data(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """Return a deterministic, representative dataset for model selection."""
        if len(X) <= self._MODEL_SELECTION_ROWS:
            return X, y

        stratify = y if task_type == "classification" else None
        try:
            X_sample, _, y_sample, _ = train_test_split(
                X,
                y,
                train_size=self._MODEL_SELECTION_ROWS,
                random_state=42,
                stratify=stratify,
            )
        except ValueError:
            logger.warning(
                "Could not stratify model-selection sample; using deterministic random sample."
            )
            sample_indices = X.sample(
                n=self._MODEL_SELECTION_ROWS,
                random_state=42,
            ).index
            X_sample = X.loc[sample_indices]
            y_sample = y.loc[sample_indices]
        logger.info(
            "Using {} of {} rows for model selection.",
            len(X_sample),
            len(X),
        )
        return X_sample, y_sample

    @staticmethod
    def _best_step_key(
        executed_steps: list[dict[str, Any]],
        task_type: str,
    ) -> str | None:
        """Choose the best completed candidate from its validation metrics."""
        candidates: list[tuple[float, str]] = []
        for step in executed_steps:
            if step.get("status") != "completed":
                continue
            metrics: dict[str, dict] = {}
            for action in step.get("actions", []):
                if action.get("status") != "completed":
                    continue
                metrics.update(action.get("metrics") or {})
            metric_entries = []
            for metric_name, metric in metrics.items():
                if isinstance(metric, dict):
                    if metric.get("mean") is not None:
                        metric_entries.append(
                            {**metric, "metric_name": metric_name}
                        )
                elif isinstance(metric, (int, float)):
                    metric_entries.append(
                        {"mean": float(metric), "metric_name": metric_name}
                    )
            if task_type == "classification":
                score = next(
                    (
                        metric.get("mean")
                        for metric in metric_entries
                        if metric.get("mean") is not None
                        and metric.get("metric_name") in {"roc_auc", "accuracy"}
                    ),
                    None,
                )
                if score is None:
                    for action in step.get("actions", []):
                        score = (action.get("metrics") or {}).get("accuracy")
                        if score is not None:
                            break
            else:
                score = next(
                    (
                        metric.get("mean")
                        for metric in metric_entries
                        if metric.get("mean") is not None
                        and metric.get("metric_name") == "r2"
                    ),
                    None,
                )
            if score is not None:
                candidates.append((float(score), step.get("model_key", "")))
        return max(candidates, default=(0.0, None))[1]

    def _fit_full_model(
        self,
        step: dict[str, Any],
        X: pd.DataFrame,
        y: pd.Series,
    ) -> Any:
        """Instantiate and fit the selected model on the complete dataset."""
        model_key = step.get("model_key", "")
        meta = _MODEL_REGISTRY.get(model_key)
        if not meta or meta.get("sequential"):
            return None
        try:
            hyperparams = {
                **meta["optional_params"],
                **(step.get("hyperparameters") or step.get("params") or {}),
            }
            estimator = meta["cls"](**hyperparams)
            estimator.fit(X, y)
            return estimator
        except Exception as exc:
            logger.exception("Failed to refit winning model {}: {}", model_key, exc)
            return None

    def _with_model_details(
        self,
        summary: dict,
        execution: str | dict,
        preprocessing_summary: dict | None,
    ) -> dict:
        """Attach the deterministic model card (schema, preprocessing, curve)."""
        if not isinstance(summary, dict):
            return summary
        try:
            report = json.loads(execution) if isinstance(execution, str) else dict(execution)
        except (TypeError, json.JSONDecodeError):
            report = {}
        algorithm = report.get("algorithm") or summary.get("best_model")
        base_details = {
            "task_type": report.get("task_type") or summary.get("task_type"),
            "algorithm": algorithm,
            "target": report.get("target_column") or self.target_column,
            "input_schema": report.get("input_schema") or [],
            "preprocessing": self._preprocessing_steps(
                preprocessing_summary or {}, report
            ),
            "training_history": report.get("training_history"),
            "n_features": report.get("n_features"),
            "n_samples": report.get("n_samples"),
            "refit": bool(summary.get("refit")),
        }
        summary["model_details"] = base_details

        # Per-model cards let the UI show a card for any candidate, not just
        # the winner. Only the winner has a measured learning curve.
        by_model: dict[str, dict] = {algorithm: base_details} if algorithm else {}
        hyper_by_model: dict[str, dict] = dict(summary.get("hyperparameters_by_model") or {})
        for step in report.get("steps") or []:
            key = step.get("model_key")
            if not key or step.get("status") != "completed":
                continue
            by_model.setdefault(key, {
                **base_details,
                "algorithm": key,
                "training_history": None,
            })
            if key not in hyper_by_model and step.get("hyperparameters"):
                hyper_by_model[key] = dict(step["hyperparameters"])
        if by_model:
            summary["model_details_by_model"] = by_model
        if hyper_by_model:
            summary["hyperparameters_by_model"] = hyper_by_model
        return summary

    @staticmethod
    def _input_schema(X: pd.DataFrame) -> list[dict[str, Any]]:
        """
        Describe the numeric feature matrix the estimator expects.

        ``example`` is included so a model card can show a realistic value
        without shipping the training data itself.
        """
        schema: list[dict[str, Any]] = []
        for column in X.columns:
            series = X[column]
            example: Any = None
            non_null = series.dropna()
            if not non_null.empty:
                try:
                    value = non_null.iloc[0]
                    example = value.item() if hasattr(value, "item") else value
                    if not isinstance(example, (int, float, str, bool)):
                        example = str(example)[:80]
                except Exception:
                    example = None
            schema.append(
                {
                    "name": str(column),
                    "dtype": str(series.dtype),
                    "required": True,
                    "example": example,
                }
            )
        return schema

    @staticmethod
    def _preprocessing_steps(
        preprocessing_summary: dict,
        report: dict,
    ) -> list[str]:
        """Turn the preprocessing summary into short, human-readable steps."""
        steps: list[str] = []
        for item in preprocessing_summary.get("quality_improvements") or []:
            text = str(item).strip()
            if text:
                steps.append(text)

        added = [str(c) for c in preprocessing_summary.get("columns_added") or []]
        dropped = [str(c) for c in preprocessing_summary.get("columns_dropped") or []]
        if added:
            steps.append(f"Added columns: {', '.join(added[:12])}")
        if dropped:
            steps.append(f"Dropped columns: {', '.join(dropped[:12])}")

        requested_task = preprocessing_summary.get("requested_task")
        if requested_task:
            steps.append(f"Task: {requested_task}")
        requested_algorithm = preprocessing_summary.get("requested_algorithm")
        if requested_algorithm:
            steps.append(f"Preferred algorithm: {requested_algorithm}")
        features = preprocessing_summary.get("features") or []
        if features:
            steps.append(f"Features used: {', '.join(str(f) for f in features[:12])}")

        non_numeric = report.get("dropped_non_numeric_columns") or []
        if non_numeric:
            steps.append(
                "Dropped non-numeric columns before fitting: "
                + ", ".join(str(c) for c in non_numeric[:12])
            )
        if not steps:
            steps.append("Numeric feature coercion; the target column is excluded.")
        return steps

    def _training_history(
        self,
        *,
        estimator: Any,
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
        step_result: dict | None,
    ) -> dict[str, Any] | None:
        """
        Build a bounded curve for the winning model.

        Sequential (Keras) models already expose epoch-level loss. Everything
        else gets a small learning curve — four training-set sizes, three
        folds — which is cheap because only the winner is measured.
        """
        # ── Keras / sequential: use the recorded epoch history ─────────────
        validation = (step_result or {}).get("validation") or {}
        keras_history = validation.get("history")
        if keras_history:
            return self._keras_history_payload(keras_history)

        history_obj = getattr(estimator, "history", None)
        if history_obj is not None:
            payload = self._keras_history_payload(
                getattr(history_obj, "history", history_obj)
            )
            if payload:
                return payload

        if estimator is None or len(X) < 12:
            return None

        try:
            sample_X, sample_y = X, y
            if len(X) > 4000:
                try:
                    sample_X, _, sample_y, _ = train_test_split(
                        X,
                        y,
                        train_size=4000,
                        random_state=42,
                        stratify=y if task_type == "classification" else None,
                    )
                except ValueError:
                    indices = X.sample(n=4000, random_state=42).index
                    sample_X, sample_y = X.loc[indices], y.loc[indices]

            cv = 3
            if task_type == "classification":
                smallest_class = int(sample_y.value_counts().min())
                cv = min(cv, smallest_class)
            if cv < 2:
                return None

            scoring = "accuracy" if task_type == "classification" else "neg_mean_squared_error"
            sizes, train_scores, validation_scores = learning_curve(
                clone(estimator),
                sample_X,
                sample_y,
                cv=cv,
                scoring=scoring,
                train_sizes=np.linspace(0.3, 1.0, 4),
                shuffle=True,
                random_state=42,
                n_jobs=1,
            )

            train_means = train_scores.mean(axis=1)
            validation_means = validation_scores.mean(axis=1)
            if task_type == "classification":
                train_values = 1.0 - train_means
                validation_values = 1.0 - validation_means
                y_label = "Error (1 - accuracy)"
            else:
                train_values = -train_means
                validation_values = -validation_means
                y_label = "Mean squared error"

            return {
                "kind": "learning_curve",
                "x_label": "Training examples",
                "y_label": y_label,
                "series": [
                    {
                        "name": "Training",
                        "points": [
                            {"x": int(size), "y": round(float(value), 6)}
                            for size, value in zip(sizes, train_values)
                        ],
                    },
                    {
                        "name": "Validation",
                        "points": [
                            {"x": int(size), "y": round(float(value), 6)}
                            for size, value in zip(sizes, validation_values)
                        ],
                    },
                ],
            }
        except Exception as exc:  # pragma: no cover - model-specific
            logger.warning("Could not build learning curve for model card: {}", exc)
            return None

    @staticmethod
    def _keras_history_payload(history: Any) -> dict[str, Any] | None:
        """Convert a Keras ``History.history`` mapping into chart series."""
        if not isinstance(history, dict):
            return None
        loss = history.get("loss")
        if not loss:
            return None
        epochs = list(range(1, len(loss) + 1))
        series = [
            {
                "name": "Training loss",
                "points": [
                    {"x": epoch, "y": round(float(value), 6)}
                    for epoch, value in zip(epochs, loss)
                ],
            }
        ]
        val_loss = history.get("val_loss")
        if val_loss:
            series.append(
                {
                    "name": "Validation loss",
                    "points": [
                        {"x": epoch, "y": round(float(value), 6)}
                        for epoch, value in zip(epochs, val_loss)
                    ],
                }
            )
        return {
            "kind": "epochs",
            "x_label": "Epoch",
            "y_label": "Loss (MSE)",
            "series": series,
        }

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
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-3.8-flash",
                "models/gemini-2.5-flash"
            ]
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
            logger.info("Model plan step {} needs regeneration: {}", idx, reason)

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
                    logger.info("Successfully regenerated model step {}", idx)
                    regenerated = True
                    break

            if not regenerated:
                logger.warning("Failed to regenerate model step {}", idx)
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
            preference_model_names=[
                # GROQ
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "meta-llama/llama-4-scout-17b-16e-instruct",
                # GEMINI
                "models/gemini-3.8-flash",
                "models/gemini-2.5-flash"
            ]
        )

    # ── Step execution ────────────────────────────────────────────────────────

    def _run_step(
        self,
        step: dict[str, Any],
        X: pd.DataFrame,
        y: pd.Series,
        task_type: str,
        is_sequential: bool = False,
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
            "hyperparameters": {
                **(step.get("hyperparameters") or {}),
                **(step.get("fit_params") or {}),
            },
        }

        # ── Unknown key ───────────────────────────────────────────────────────
        if model_key not in _MODEL_REGISTRY:
            logger.warning("Unknown model key: {}", model_key)
            return {**base, "status": "skipped", "error": f"Unknown model key: {model_key}"}, None

        meta = _MODEL_REGISTRY[model_key]

        if meta.get("sequential") and not is_sequential:
            msg = f"Model '{model_key}' requires sequential data."
            logger.warning(msg)
            return {**base, "status": "skipped", "error": msg}, None

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
            logger.exception("Failed to instantiate {}: {}", model_key, exc)
            return {**base, "status": "failed", "error": f"Instantiation error: {exc}"}, None

        # ── Fit ───────────────────────────────────────────────────────────────
        try:
            if meta.get("sequential"):
                fit_report = self._fit_time_series_model(
                    estimator, y, **(step.get("fit_params") or {})
                )
            else:
                estimator.fit(X, y)
        except Exception as exc:
            logger.exception("Failed to fit {}: {}", model_key, exc)
            return {**base, "status": "failed", "error": f"Fit error: {exc}"}, None

        # ── Run attached actions ──────────────────────────────────────────────
        action_results: list[dict] = []
        if not meta.get("sequential"):
            action_keys = {action.get("action_key") for action in actions}
            if not action_keys.intersection({"cv_evaluate", "holdout_evaluate"}):
                actions = [*actions, {"action_key": "cv_evaluate", "params": {}}]
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
            "validation": fit_report if meta.get("sequential") else None,
        }, estimator

    @staticmethod
    def _fit_time_series_model(
        estimator: Any,
        target: pd.Series,
        epochs: int = 50,
        batch_size: int = 32,
    ) -> dict[str, Any]:
        """Fit a sequence predictor on the target in its existing row order."""
        values = pd.to_numeric(target, errors="raise").to_numpy(dtype=float)
        if len(values) <= estimator.window_size + estimator.difference_order:
            raise ValueError(
                "Sequential data must contain more rows than window_size + difference_order."
            )

        differences = np.diff(values, n=estimator.difference_order)
        scaled = estimator.scaler.fit_transform(differences.reshape(-1, 1)).flatten()
        prepare_parameters = inspect.signature(estimator.prepare_data).parameters
        if len(prepare_parameters) >= 2:
            X_diff, y_diff, base_values, target_values = estimator.prepare_data(values, scaled)
            split = estimator.split_data(X_diff, y_diff, base_values, target_values)
            (X_train, y_train, _, _), (X_test, y_test, base_test, target_test) = split
        else:
            X_diff, y_diff = estimator.prepare_data(scaled)
            split = estimator.split_data(X_diff, y_diff)
            (X_train, y_train), (X_test, y_test) = split
            base_test = target_test = None

        if len(X_train) == 0 or len(X_test) == 0:
            raise ValueError("Sequential data does not provide both training and validation rows.")
        estimator.fit(
            X_train, y_train, X_test, y_test,
            epochs=epochs, batch_size=batch_size, verbose=0,
        )

        if base_test is not None:
            mse, mae = estimator.eval(X_test, y_test, base_test, target_test)
        else:
            mse, mae = estimator.eval(X_test, y_test)

        history_obj = getattr(estimator, "history", None)
        history = getattr(history_obj, "history", history_obj)
        history_payload = None
        if isinstance(history, dict):
            history_payload = {
                key: [float(value) for value in values]
                for key, values in history.items()
                if key in {"loss", "val_loss", "mae", "val_mae"} and values
            }

        return {
            "validation_metrics": {"mse": float(mse), "mae": float(mae)},
            "n_train": int(len(X_train)),
            "n_validation": int(len(X_test)),
            "history": history_payload,
        }

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
        requested_folds = int(params.get("cv", 3))
        if task_type == "classification":
            smallest_class = int(y.value_counts().min())
            cv_folds = min(requested_folds, smallest_class)
        else:
            cv_folds = min(requested_folds, len(y))
        if cv_folds < 2:
            return {
                "action_key": "cv_evaluate",
                "status": "skipped",
                "error": "At least two samples per class are required for cross-validation.",
            }
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
                        "metric_name": metric_name,
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
            logger.warning("cv_evaluate failed: {}", exc)
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
            logger.warning("holdout_evaluate failed: {}", exc)
            return {"action_key": "holdout_evaluate", "status": "failed", "error": str(exc)}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _model_family(model_key: str) -> str:
        if model_key.startswith(("logistic_regression", "ridge", "lasso")):
            return "linear"
        if model_key.startswith(("random_forest", "extra_trees", "gradient_boosting", "adaboost", "decision_tree", "xgb", "lgbm")):
            return "tree"
        if model_key.startswith(("svc", "svr")):
            return "kernel"
        if model_key.startswith("knn"):
            return "neighbors"
        if model_key in {"bilstm", "lstm_attention"}:
            return "sequence"
        return "other"

    def _ensure_plan_coverage(
        self,
        plan_obj: dict,
        inferred_task: str | None,
        is_sequential: bool,
        n_rows: int,
    ) -> dict:
        """Add a small, deterministic model portfolio when the LLM plan is narrow."""
        if inferred_task not in {"classification", "regression"}:
            return plan_obj

        steps = plan_obj.setdefault("steps", [])
        if n_rows > self._SLOW_MODEL_ROWS:
            slow_keys = {"svc", "svr", "knn_classifier", "knn_regressor",
                         "gradient_boosting_classifier", "gradient_boosting_regressor"}
            removed = [
                step.get("model_key")
                for step in steps
                if step.get("model_key") in slow_keys
            ]
            if removed:
                plan_obj["steps"] = steps = [
                    step for step in steps if step.get("model_key") not in slow_keys
                ]
                logger.info(
                    "Removed slow models for large dataset: {}",
                    removed,
                )
        selected = {
            step.get("model_key")
            for step in steps
            if step.get("model_key") in _MODEL_REGISTRY
        }
        families = {self._model_family(key) for key in selected}

        if is_sequential:
            candidates = [
                "ridge",
                "random_forest_regressor",
                "gradient_boosting_regressor",
                "bilstm",
                "lstm_attention",
            ] if inferred_task == "regression" else []
        elif inferred_task == "classification":
            candidates = [
                "logistic_regression",
                "random_forest_classifier",
                "gradient_boosting_classifier",
                "svc",
            ]
        else:
            candidates = [
                "ridge",
                "random_forest_regressor",
                "gradient_boosting_regressor",
                "svr",
            ]

        for model_key in candidates:
            meta = _MODEL_REGISTRY.get(model_key)
            if not meta or model_key in selected:
                continue
            if n_rows > self._SLOW_MODEL_ROWS and (
                model_key in {"svc", "svr"}
                or model_key.startswith("knn")
                or model_key.startswith("gradient_boosting_")
            ):
                continue
            if meta["task"] != inferred_task:
                continue
            if meta.get("sequential") and (not is_sequential or n_rows < 80):
                continue
            if not meta.get("sequential") and is_sequential:
                # Sequence data still benefits from non-sequential baselines,
                # but the portfolio should not be dominated by them.
                if families and len(families) >= 2:
                    continue

            steps.append({
                "step": len(steps) + 1,
                "name": f"{model_key} baseline",
                "model_key": model_key,
                "objective": "Provide an independent model-family baseline for comparison.",
                "hyperparameters": {},
                "actions": [],
                "status": "valid",
            })
            selected.add(model_key)
            families.add(self._model_family(model_key))
            logger.info("Added deterministic portfolio candidate: model={}, family={}", model_key, self._model_family(model_key))

            if not is_sequential and len(families) >= 3:
                break

        return plan_obj

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
    def _is_sequential_data(
        data: pd.DataFrame,
        preprocessing_summary: dict | None = None,
    ) -> bool:
        """Identify ordered data using datetime columns or preprocessing metadata."""
        summary = preprocessing_summary or {}
        if summary.get("sequential") is True:
            return True
        datetime_columns = summary.get("datetime_columns") or summary.get("temporal_columns")
        if datetime_columns:
            return True

        for column in data.columns:
            series = data[column]
            if pd.api.types.is_datetime64_any_dtype(series):
                return series.is_monotonic_increasing and series.is_unique
            if any(token in str(column).lower() for token in ("date", "time", "timestamp")):
                parsed = pd.to_datetime(series, errors="coerce")
                if parsed.notna().all() and parsed.is_monotonic_increasing and parsed.is_unique:
                    return True
        return False

    @staticmethod
    def _dataset_profile(data: pd.DataFrame) -> dict[str, Any]:
        sample = data.head(5).where(pd.notna(data.head(5)), None)
        sample_rows = json.loads(json.dumps(
            sample.to_dict(orient="records"), default=str
        ))
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
            "sample_rows": sample_rows,
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
