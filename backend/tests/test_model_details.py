"""Tests for the model-card metadata attached to Workspace and ETL models."""
from __future__ import annotations

import json

import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from squirrel.models.hyperparameters import (
    build_model_catalog,
    normalize_hyperparameters,
)
from squirrel.modules.agents.builder.TabularDataModelBuilderAgent import (
    TabularDataModelBuilderAgent,
    _MODEL_REGISTRY,
)
from squirrel.modules.agents.etl.ETLModelTrainingAgent import ETLModelTrainingAgent
from squirrel.schemas.etl import MLSpec
from squirrel.services.pipelines.MLTrainer import MLTrainer


# ——————————————————————————————————————————————————————————————
# Workspace builder


def _builder() -> TabularDataModelBuilderAgent:
    return TabularDataModelBuilderAgent(target_column="target")


def test_keras_history_payload_builds_epoch_series() -> None:
    payload = TabularDataModelBuilderAgent._keras_history_payload(
        {"loss": [0.9, 0.5, 0.25], "val_loss": [1.0, 0.6, 0.3]}
    )

    assert payload is not None
    assert payload["kind"] == "epochs"
    assert payload["x_label"] == "Epoch"
    assert [series["name"] for series in payload["series"]] == [
        "Training loss",
        "Validation loss",
    ]
    assert payload["series"][0]["points"] == [
        {"x": 1, "y": 0.9},
        {"x": 2, "y": 0.5},
        {"x": 3, "y": 0.25},
    ]


def test_keras_history_payload_requires_loss() -> None:
    assert TabularDataModelBuilderAgent._keras_history_payload(None) is None
    assert TabularDataModelBuilderAgent._keras_history_payload({}) is None
    assert TabularDataModelBuilderAgent._keras_history_payload({"val_loss": [1.0]}) is None


def test_input_schema_describes_columns() -> None:
    frame = pd.DataFrame(
        {
            "age": [34, 51],
            "score": [0.5, 0.9],
            "label": ["a", "b"],
        }
    )

    schema = TabularDataModelBuilderAgent._input_schema(frame)

    assert [column["name"] for column in schema] == ["age", "score", "label"]
    assert schema[0]["required"] is True
    assert schema[0]["example"] == 34
    assert schema[1]["dtype"].startswith("float")


def test_preprocessing_steps_summarises_changes() -> None:
    steps = TabularDataModelBuilderAgent._preprocessing_steps(
        {
            "quality_improvements": ["Dropped 2 duplicate rows"],
            "columns_added": ["total"],
            "columns_dropped": ["notes"],
            "requested_task": "classification",
            "requested_algorithm": "random_forest",
            "features": ["age", "score"],
        },
        {"dropped_non_numeric_columns": ["city"]},
    )

    assert steps[0] == "Dropped 2 duplicate rows"
    assert "Added columns: total" in steps
    assert "Dropped columns: notes" in steps
    assert "Task: classification" in steps
    assert "Preferred algorithm: random_forest" in steps
    assert "Features used: age, score" in steps
    assert any("city" in step for step in steps)


def test_preprocessing_steps_has_default() -> None:
    steps = TabularDataModelBuilderAgent._preprocessing_steps({}, {})
    assert steps == ["Numeric feature coercion; the target column is excluded."]


def test_with_model_details_attaches_card() -> None:
    execution = {
        "task_type": "classification",
        "algorithm": "logistic_regression",
        "target_column": "churn",
        "input_schema": [{"name": "age", "dtype": "int64", "required": True, "example": 34}],
        "training_history": {"kind": "epochs", "series": []},
        "n_features": 1,
        "n_samples": 100,
        "dropped_non_numeric_columns": ["city"],
    }

    summary = _builder()._with_model_details(
        {"best_model": "logistic_regression"},
        json.dumps(execution),
        {"quality_improvements": ["Filled missing values"]},
    )

    details = summary["model_details"]
    assert details["task_type"] == "classification"
    assert details["algorithm"] == "logistic_regression"
    assert details["target"] == "churn"
    assert details["input_schema"] == execution["input_schema"]
    assert details["training_history"] == execution["training_history"]
    assert details["n_features"] == 1
    assert details["n_samples"] == 100
    assert "Filled missing values" in details["preprocessing"]
    assert any("city" in step for step in details["preprocessing"])


def test_with_model_details_survives_malformed_execution() -> None:
    summary = _builder()._with_model_details(
        {"best_model": "linear", "task_type": "regression"},
        "not-json",
        {},
    )

    details = summary["model_details"]
    assert details["algorithm"] == "linear"
    assert details["task_type"] == "regression"
    assert details["target"] == "target"
    assert details["input_schema"] == []
    assert details["training_history"] is None
    assert details["preprocessing"] == [
        "Numeric feature coercion; the target column is excluded."
    ]


def test_with_model_details_passes_through_non_dict_summary() -> None:
    assert _builder()._with_model_details("nope", "{}", {}) == "nope"


# ——————————————————————————————————————————————————————————————
# User-facing hyperparameter catalog


def test_catalog_exposes_typed_hyperparameters_per_model() -> None:
    catalog = {entry["model_key"]: entry for entry in build_model_catalog(_MODEL_REGISTRY)}

    rf = catalog["random_forest_classifier"]
    params = {spec["name"]: spec for spec in rf["hyperparameters"]}
    assert params["n_estimators"]["type"] == "int"
    assert params["n_estimators"]["default"] == 50
    assert params["max_depth"]["nullable"] is True
    assert "n_jobs" not in params  # internal defaults stay hidden

    bilstm = catalog["bilstm"]
    assert bilstm["supports_epochs"] is True
    assert {spec["name"] for spec in bilstm["hyperparameters"] if spec["fit_param"]} == {
        "epochs", "batch_size"
    }


def test_normalize_hyperparameters_coerces_and_validates() -> None:
    values = normalize_hyperparameters(
        "gradient_boosting_classifier",
        {"n_estimators": "120", "learning_rate": "0.02", "max_depth": 5},
    )

    assert values == {"n_estimators": 120, "learning_rate": 0.02, "max_depth": 5}


def test_normalize_hyperparameters_rejects_unknown_and_out_of_range() -> None:
    with pytest.raises(ValueError, match="Unsupported hyperparameter"):
        normalize_hyperparameters("ridge", {"alphaa": 1.0})

    with pytest.raises(ValueError, match="at most"):
        normalize_hyperparameters("logistic_regression", {"C": 999999.0})

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_hyperparameters("random_forest_classifier", {"max_depth": ""})


def test_run_selected_uses_a_single_step_plan_and_fit_params() -> None:
    frame = pd.DataFrame({"a": [index % 5 for index in range(40)], "target": [index % 2 for index in range(40)]})
    builder = _builder()

    fitted, summary = builder.run_selected(
        frame,
        "random_forest_classifier",
        hyperparameters={"n_estimators": 10},
        preprocessing_summary={},
    )

    assert list(fitted) == ["random_forest_classifier"]
    assert summary["best_model"] == "random_forest_classifier"
    assert summary["models_trained"] == ["random_forest_classifier"]
    assert summary["hyperparameters_by_model"]["random_forest_classifier"]["n_estimators"] == 10
    assert any(
        entry["model_key"] == "random_forest_classifier"
        for entry in summary["model_comparison"]
    )


def test_run_selected_rejects_wrong_task_model() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0], "target": [1.5, 2.5]})

    with pytest.raises(ValueError, match="classification"):
        _builder().run_selected(frame, "logistic_regression")


def test_training_history_uses_keras_validation_history() -> None:
    history = _builder()._training_history(
        estimator=None,
        X=pd.DataFrame(),
        y=pd.Series(dtype=float),
        task_type="regression",
        step_result={"validation": {"history": {"loss": [0.8, 0.4]}}},
    )

    assert history is not None
    assert history["kind"] == "epochs"
    assert history["series"][0]["points"] == [{"x": 1, "y": 0.8}, {"x": 2, "y": 0.4}]


def test_training_history_builds_learning_curve() -> None:
    rows = 60
    frame = pd.DataFrame(
        {
            "a": [index % 7 for index in range(rows)],
            "b": [index % 5 for index in range(rows)],
        }
    )
    labels = pd.Series([index % 2 for index in range(rows)])

    history = _builder()._training_history(
        estimator=LogisticRegression(max_iter=200),
        X=frame,
        y=labels,
        task_type="classification",
        step_result=None,
    )

    assert history is not None
    assert history["kind"] == "learning_curve"
    assert history["y_label"] == "Error (1 - accuracy)"
    assert [series["name"] for series in history["series"]] == ["Training", "Validation"]
    assert len(history["series"][0]["points"]) == 4
    points = history["series"][0]["points"]
    assert [point["x"] for point in points] == sorted(point["x"] for point in points)
    assert all(0.0 <= point["y"] <= 1.0 for point in points)


def test_training_history_skips_tiny_datasets() -> None:
    history = _builder()._training_history(
        estimator=LogisticRegression(),
        X=pd.DataFrame({"a": [1, 2, 3]}),
        y=pd.Series([0, 1, 0]),
        task_type="classification",
        step_result=None,
    )
    assert history is None


# ——————————————————————————————————————————————————————————————
# ETL training agent


def _numeric_frame(rows: int = 30) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": [float(index) for index in range(rows)],
            "y": [float((index * 3) % 11) for index in range(rows)],
        }
    )


def test_etl_clustering_details_include_elbow_curve() -> None:
    frame = _numeric_frame()
    spec = MLSpec(task="clustering", n_clusters=3)

    result = ETLModelTrainingAgent().run(frame, spec)

    details = result["details"]
    assert details["task_type"] == "clustering"
    assert details["algorithm"] == "KMeans"
    assert details["target"] is None
    assert [column["name"] for column in details["input_schema"]] == ["x", "y"]
    assert details["n_features"] == 2
    assert details["n_samples"] == len(frame)
    assert any("Requested clusters: 3" in step for step in details["preprocessing"])
    assert details["training_history"]["kind"] == "elbow"
    assert len(details["training_history"]["series"][0]["points"]) >= 2
    assert result["metrics"]["n_clusters"] == 3


# ——————————————————————————————————————————————————————————————
# Retrain (refit) model cards


def test_refit_details_drops_stale_supervised_curve() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
    model_row = {
        "algorithm": "random_forest",
        "details": {
            "input_schema": [{"name": "a", "dtype": "float64", "required": True}],
            "preprocessing": ["Numeric feature coercion"],
            "training_history": {"kind": "learning_curve", "series": []},
            "n_samples": 1,
        },
        "metrics": {},
    }

    details = MLTrainer._refit_details(
        model_row,
        "classification",
        "target",
        ["a", "b"],
        frame,
    )

    assert details["refit"] is True
    assert details["task_type"] == "classification"
    assert details["algorithm"] == "random_forest"
    assert details["target"] == "target"
    assert details["n_features"] == 2
    assert details["n_samples"] == 3
    # The previous version's held-out curve must not be presented as this one's.
    assert details["training_history"] is None
    assert details["input_schema"] == model_row["details"]["input_schema"]
    assert details["preprocessing"] == ["Numeric feature coercion"]


def test_refit_details_rebuilds_clustering_curve() -> None:
    frame = _numeric_frame(24)
    model_row = {
        "algorithm": "KMeans",
        "details": {"input_schema": [], "preprocessing": []},
        "metrics": {"n_clusters": 4},
    }

    details = MLTrainer._refit_details(
        model_row,
        "clustering",
        None,
        ["x", "y"],
        frame,
    )

    assert details["task_type"] == "clustering"
    assert details["target"] is None
    assert details["training_history"]["kind"] == "elbow"
    assert len(details["training_history"]["series"][0]["points"]) >= 2


def test_refit_details_accepts_json_string_details() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0]})
    model_row = {
        "algorithm": "linear",
        "details": json.dumps({"preprocessing": ["kept"], "training_history": {"kind": "x"}}),
        "metrics": {},
    }

    details = MLTrainer._refit_details(model_row, "regression", "y", ["a"], frame)

    assert details["preprocessing"] == ["kept"]
    assert details["training_history"] is None
    assert details["n_samples"] == 2
