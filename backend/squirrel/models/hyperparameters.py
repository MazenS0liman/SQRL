#!/usr/bin/python
"""
Model Hyperparameter Catalog
============================

A single, typed description of the hyperparameters that users are allowed to
tune for every model in the tabular model-builder registry.

The registry in ``TabularDataModelBuilderAgent`` owns the estimator classes and
their *internal* defaults (``random_state``, ``n_jobs``, ...). This module owns
the smaller, user-facing surface: which parameters matter, their types, their
safe ranges, and how a value coming from the UI should be validated before it
is handed to scikit-learn / XGBoost / Keras.

Nothing here imports the agent, so the API layer can serve the catalog without
pulling TensorFlow into the request path.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ——————————————————————————————————————————————————————————————
# Spec helpers

def _p(
    name: str,
    label: str,
    kind: str,
    default: Any,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    step: Optional[float] = None,
    choices: Optional[List[Any]] = None,
    nullable: bool = False,
    fit_param: bool = False,
    description: str = "",
) -> Dict[str, Any]:
    """Build one hyperparameter spec entry."""
    return {
        "name": name,
        "label": label,
        "type": kind,
        "default": default,
        "min": minimum,
        "max": maximum,
        "step": step,
        "choices": choices,
        "nullable": nullable,
        "fit_param": fit_param,
        "description": description,
    }


_EPOCHS = _p(
    "epochs", "Epochs", "int", 50,
    minimum=1, maximum=1000, step=1, fit_param=True,
    description="Full passes over the training data. Neural models only.",
)
_BATCH_SIZE = _p(
    "batch_size", "Batch size", "int", 32,
    minimum=1, maximum=1024, step=1, fit_param=True,
    description="Rows per gradient update. Neural models only.",
)


# ——————————————————————————————————————————————————————————————
# Per-model tunables

HYPERPARAMETER_SPECS: Dict[str, List[Dict[str, Any]]] = {
    # Linear / regularised
    "logistic_regression": [
        _p("C", "Regularization (C)", "float", 1.0, minimum=0.0001, maximum=10000.0,
           description="Inverse L2 strength. Smaller values regularize more."),
        _p("max_iter", "Max iterations", "int", 1000, minimum=1, maximum=100000, step=1,
           description="Solver iterations before stopping."),
        _p("solver", "Solver", "enum", "lbfgs",
           choices=["lbfgs", "liblinear", "saga", "sag", "newton-cg"],
           description="Optimization algorithm."),
        _p("class_weight", "Class weight", "enum", None, choices=[None, "balanced"],
           nullable=True, description="Balance imbalanced classes automatically."),
    ],
    "ridge": [
        _p("alpha", "Alpha", "float", 1.0, minimum=0.0, maximum=10000.0,
           description="L2 regularization strength."),
    ],
    "lasso": [
        _p("alpha", "Alpha", "float", 1.0, minimum=0.0001, maximum=10000.0,
           description="L1 regularization strength."),
        _p("max_iter", "Max iterations", "int", 1000, minimum=1, maximum=100000, step=1,
           description="Solver iterations before stopping."),
    ],
    # Tree ensembles
    "random_forest_classifier": [
        _p("n_estimators", "Trees", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of trees in the forest (training rounds)."),
        _p("max_depth", "Max depth", "int", None, minimum=1, maximum=100, step=1,
           nullable=True, description="Maximum tree depth. Empty means unlimited."),
        _p("class_weight", "Class weight", "enum", None, choices=[None, "balanced"],
           nullable=True, description="Balance imbalanced classes automatically."),
    ],
    "random_forest_regressor": [
        _p("n_estimators", "Trees", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of trees in the forest (training rounds)."),
        _p("max_depth", "Max depth", "int", None, minimum=1, maximum=100, step=1,
           nullable=True, description="Maximum tree depth. Empty means unlimited."),
    ],
    "gradient_boosting_classifier": [
        _p("n_estimators", "Boosting rounds", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of boosted stages (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.1, minimum=0.0001, maximum=10.0,
           description="How much each stage corrects the previous one."),
        _p("max_depth", "Max depth", "int", 4, minimum=1, maximum=100, step=1,
           description="Maximum depth of each stage's tree."),
    ],
    "gradient_boosting_regressor": [
        _p("n_estimators", "Boosting rounds", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of boosted stages (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.1, minimum=0.0001, maximum=10.0,
           description="How much each stage corrects the previous one."),
        _p("max_depth", "Max depth", "int", 4, minimum=1, maximum=100, step=1,
           description="Maximum depth of each stage's tree."),
    ],
    "extra_trees_classifier": [
        _p("n_estimators", "Trees", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of trees in the forest (training rounds)."),
    ],
    "extra_trees_regressor": [
        _p("n_estimators", "Trees", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of trees in the forest (training rounds)."),
    ],
    "adaboost_classifier": [
        _p("n_estimators", "Boosting rounds", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of boosted weak learners (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 1.0, minimum=0.0001, maximum=10.0,
           description="Contribution of each weak learner."),
    ],
    "adaboost_regressor": [
        _p("n_estimators", "Boosting rounds", "int", 50, minimum=1, maximum=5000, step=10,
           description="Number of boosted weak learners (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 1.0, minimum=0.0001, maximum=10.0,
           description="Contribution of each weak learner."),
    ],
    # Single tree
    "decision_tree_classifier": [
        _p("max_depth", "Max depth", "int", 6, minimum=1, maximum=100, step=1,
           description="Maximum tree depth. Deeper trees fit more but can overfit."),
    ],
    "decision_tree_regressor": [
        _p("max_depth", "Max depth", "int", 6, minimum=1, maximum=100, step=1,
           description="Maximum tree depth. Deeper trees fit more but can overfit."),
    ],
    # SVM
    "svc": [
        _p("C", "Regularization (C)", "float", 1.0, minimum=0.0001, maximum=10000.0,
           description="Penalty for misclassified samples."),
        _p("kernel", "Kernel", "enum", "rbf", choices=["rbf", "linear", "poly", "sigmoid"],
           description="Kernel used to map samples into a higher-dimensional space."),
    ],
    "svr": [
        _p("C", "Regularization (C)", "float", 1.0, minimum=0.0001, maximum=10000.0,
           description="Penalty for errors outside the epsilon tube."),
        _p("kernel", "Kernel", "enum", "rbf", choices=["rbf", "linear", "poly", "sigmoid"],
           description="Kernel used to map samples into a higher-dimensional space."),
    ],
    # KNN
    "knn_classifier": [
        _p("n_neighbors", "Neighbors", "int", 5, minimum=1, maximum=500, step=1,
           description="Number of nearby samples used for each prediction."),
    ],
    "knn_regressor": [
        _p("n_neighbors", "Neighbors", "int", 5, minimum=1, maximum=500, step=1,
           description="Number of nearby samples averaged for each prediction."),
    ],
    # XGBoost (registered when the optional dependency is installed)
    "xgb_classifier": [
        _p("n_estimators", "Boosting rounds", "int", 300, minimum=1, maximum=5000, step=10,
           description="Number of boosted trees (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.05, minimum=0.0001, maximum=10.0,
           description="Step-size shrinkage after each boosting round."),
        _p("max_depth", "Max depth", "int", 6, minimum=1, maximum=100, step=1,
           description="Maximum depth of each tree."),
        _p("subsample", "Subsample", "float", 0.8, minimum=0.01, maximum=1.0,
           description="Fraction of rows sampled per boosting round."),
        _p("colsample_bytree", "Columns per tree", "float", 0.8, minimum=0.01, maximum=1.0,
           description="Fraction of features sampled per tree."),
    ],
    "xgb_regressor": [
        _p("n_estimators", "Boosting rounds", "int", 300, minimum=1, maximum=5000, step=10,
           description="Number of boosted trees (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.05, minimum=0.0001, maximum=10.0,
           description="Step-size shrinkage after each boosting round."),
        _p("max_depth", "Max depth", "int", 6, minimum=1, maximum=100, step=1,
           description="Maximum depth of each tree."),
        _p("subsample", "Subsample", "float", 0.8, minimum=0.01, maximum=1.0,
           description="Fraction of rows sampled per boosting round."),
        _p("colsample_bytree", "Columns per tree", "float", 0.8, minimum=0.01, maximum=1.0,
           description="Fraction of features sampled per tree."),
    ],
    # LightGBM
    "lgbm_classifier": [
        _p("n_estimators", "Boosting rounds", "int", 300, minimum=1, maximum=5000, step=10,
           description="Number of boosted trees (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.05, minimum=0.0001, maximum=10.0,
           description="Step-size shrinkage after each boosting round."),
        _p("num_leaves", "Leaves", "int", 63, minimum=2, maximum=4096, step=1,
           description="Maximum number of leaves per tree."),
    ],
    "lgbm_regressor": [
        _p("n_estimators", "Boosting rounds", "int", 300, minimum=1, maximum=5000, step=10,
           description="Number of boosted trees (analogous to epochs)."),
        _p("learning_rate", "Learning rate", "float", 0.05, minimum=0.0001, maximum=10.0,
           description="Step-size shrinkage after each boosting round."),
        _p("num_leaves", "Leaves", "int", 63, minimum=2, maximum=4096, step=1,
           description="Maximum number of leaves per tree."),
    ],
    # Neural / sequential
    "bilstm": [
        _p("window_size", "Window size", "int", 30, minimum=2, maximum=365, step=1,
           description="Number of past observations fed to the network."),
        _p("difference_order", "Differencing order", "int", 1, minimum=1, maximum=5, step=1,
           description="How many times the series is differenced before training."),
        _EPOCHS,
        _BATCH_SIZE,
    ],
    "lstm_attention": [
        _p("lstm_units", "LSTM units", "int", 64, minimum=4, maximum=2048, step=4,
           description="Width of each LSTM layer."),
        _p("window_size", "Window size", "int", 30, minimum=2, maximum=365, step=1,
           description="Number of past observations fed to the network."),
        _p("difference_order", "Differencing order", "int", 1, minimum=1, maximum=5, step=1,
           description="How many times the series is differenced before training."),
        _EPOCHS,
        _BATCH_SIZE,
    ],
}


MODEL_LABELS: Dict[str, str] = {
    "logistic_regression": "Logistic Regression",
    "ridge": "Ridge Regression",
    "lasso": "Lasso Regression",
    "random_forest_classifier": "Random Forest (Classifier)",
    "random_forest_regressor": "Random Forest (Regressor)",
    "gradient_boosting_classifier": "Gradient Boosting (Classifier)",
    "gradient_boosting_regressor": "Gradient Boosting (Regressor)",
    "extra_trees_classifier": "Extra Trees (Classifier)",
    "extra_trees_regressor": "Extra Trees (Regressor)",
    "adaboost_classifier": "AdaBoost (Classifier)",
    "adaboost_regressor": "AdaBoost (Regressor)",
    "decision_tree_classifier": "Decision Tree (Classifier)",
    "decision_tree_regressor": "Decision Tree (Regressor)",
    "svc": "Support Vector Classifier",
    "svr": "Support Vector Regressor",
    "knn_classifier": "k-Nearest Neighbours (Classifier)",
    "knn_regressor": "k-Nearest Neighbours (Regressor)",
    "xgb_classifier": "XGBoost (Classifier)",
    "xgb_regressor": "XGBoost (Regressor)",
    "lgbm_classifier": "LightGBM (Classifier)",
    "lgbm_regressor": "LightGBM (Regressor)",
    "bilstm": "Bidirectional LSTM",
    "lstm_attention": "LSTM with Attention",
}


# ——————————————————————————————————————————————————————————————
# Public helpers


def build_model_catalog(registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Serialise the model registry plus its tunable hyperparameters."""
    catalog: List[Dict[str, Any]] = []
    for key, meta in registry.items():
        specs = HYPERPARAMETER_SPECS.get(key, [])
        catalog.append({
            "model_key": key,
            "label": MODEL_LABELS.get(key, key.replace("_", " ").title()),
            "task": meta.get("task", "both"),
            "description": meta.get("description", ""),
            "sequential": bool(meta.get("sequential", False)),
            "supports_epochs": any(spec["name"] == "epochs" for spec in specs),
            "hyperparameters": [dict(spec) for spec in specs],
        })
    return catalog


def get_hyperparameter(specs: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    return next((spec for spec in specs if spec["name"] == name), None)


def normalize_hyperparameters(
    model_key: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Validate and coerce user-supplied hyperparameters for ``model_key``.

    Only keys declared in :data:`HYPERPARAMETER_SPECS` are accepted; unknown
    keys are rejected so a typo never silently changes a training run.
    ``fit_param`` entries (epochs, batch_size) are returned as well — the
    caller splits them out before instantiating the estimator.

    :raises ValueError: on unknown keys, invalid types, or out-of-range values.
    """
    specs = HYPERPARAMETER_SPECS.get(model_key)
    if specs is None:
        raise ValueError(f"Unknown model '{model_key}'.")

    overrides = overrides or {}
    unknown = sorted(set(overrides) - {spec["name"] for spec in specs})
    if unknown:
        raise ValueError(
            f"Unsupported hyperparameter(s) for {model_key}: {', '.join(unknown)}."
        )

    result: Dict[str, Any] = {}
    for spec in specs:
        name = spec["name"]
        value = overrides.get(name, spec["default"])
        label = spec["label"]

        # An explicit empty string from the UI is always a validation error,
        # even for nullable parameters. A genuine ``null`` is only valid for
        # specs that declare ``nullable=True`` (or whose default is None).
        if value == "":
            raise ValueError(f"{label} cannot be empty.")
        if value is None:
            if spec["nullable"]:
                result[name] = None
                continue
            raise ValueError(f"{label} cannot be empty.")

        kind = spec["type"]
        try:
            if kind == "int":
                if isinstance(value, bool):
                    raise ValueError
                value = int(value)
            elif kind == "float":
                value = float(value)
            elif kind == "bool":
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered not in {"true", "false", "1", "0"}:
                        raise ValueError
                    value = lowered in {"true", "1"}
                else:
                    value = bool(value)
            elif kind == "enum" and spec.get("choices"):
                expected = spec["choices"]
                # ``None`` is a valid choice for nullable params; strings are
                # compared exactly so enum names remain predictable.
                if value not in expected:
                    raise ValueError
        except (TypeError, ValueError):
            allowed = (
                ", ".join(str(choice) for choice in spec["choices"])
                if spec.get("choices") else kind
            )
            raise ValueError(f"{label} must be one of: {allowed}.")

        if kind in {"int", "float"}:
            minimum, maximum = spec.get("min"), spec.get("max")
            if minimum is not None and value < minimum:
                raise ValueError(f"{label} must be at least {minimum}.")
            if maximum is not None and value > maximum:
                raise ValueError(f"{label} must be at most {maximum}.")

        result[name] = value

    return result
