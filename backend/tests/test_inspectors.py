"""Tests for the tabular inspection strategies and the strategy dispatcher."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from squirrel.modules.inspectors.tabular import (
    CorrelationInspectionStrategy,
    DataInspectStrategy,
    DataTypeInspectionStrategy,
    DetectOutliersInspectionStrategy,
    MissingValuesInspectionStrategy,
    SummaryStatisticsInspectionStrategy,
    TabularDataInpsector,
)


@pytest.fixture()
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 5, 6, 7, 8],
            "age": [25, 32, 41, 29, np.nan, 38, 47, 51],
            "income": [40_000, 52_000, 61_000, 45_000, 48_000, 900_000, 55_000, 58_000],
            "city": ["London", "Paris", "London", None, "Paris", "Berlin", "London", "Paris"],
            "signup_date": pd.to_datetime(
                [
                    "2023-01-01",
                    "2023-02-01",
                    "2023-03-01",
                    "2023-04-01",
                    "2023-05-01",
                    "2023-06-01",
                    "2023-07-01",
                    "2023-08-01",
                ]
            ),
            "status": ["active", "pending", "active", "active", "cancelled", "pending", "active", "active"],
            "churn": [0, 1, 0, 0, 1, 0, 1, 0],
        }
    )


# ——————————————————————————————————————————————————————————————
# Strategy catalog / schema plumbing


def test_strategy_catalog_entry_is_json_friendly() -> None:
    entry = DataTypeInspectionStrategy.to_catalog_entry()

    assert entry["name"] == "DataTypeInspectionStrategy"
    assert entry["description"]
    assert entry["json_schema"]["type"] == "object"
    assert isinstance(entry["arguments_schema"], list)
    assert entry["example"]["inspection"] == "DataTypeInspectionStrategy"


def test_strategy_json_schema_renders_every_argument_type() -> None:
    class Everything(DataInspectStrategy):
        argument_specs = []

        def inspect(self, data, **kwargs):  # pragma: no cover - not executed
            return {}

    from squirrel.schemas.argument_spec import ArgumentSpec

    Everything.argument_specs = [
        ArgumentSpec("a", "str", True, None, "", "", {}),
        ArgumentSpec("b", "integer", False, 1, "", "", {}),
        ArgumentSpec("c", "float", False, 1.5, "", "", {}),
        ArgumentSpec("d", "boolean", False, False, "", "", {}),
        ArgumentSpec("e", "object", False, {}, "", "", {}),
        ArgumentSpec("f", "list", False, [], "", "", {}),
        ArgumentSpec("g", "list[str]", False, [], "", "", {}),
        ArgumentSpec("h", "unknown-type", False, None, "", "", {}, condition="only when a == 1"),
    ]

    schema = Everything.to_json_schema()

    assert schema["required"] == ["a"]
    assert schema["properties"]["b"]["type"] == "integer"
    assert schema["properties"]["c"]["type"] == "number"
    assert schema["properties"]["d"]["type"] == "boolean"
    assert schema["properties"]["e"]["type"] == "object"
    assert schema["properties"]["f"]["type"] == "array"
    assert schema["properties"]["g"]["type"] == "array"
    assert schema["properties"]["g"]["items"]["type"] == "string"
    assert schema["properties"]["h"]["type"] == "string"
    assert "Required: a" in Everything._arguments_description()
    assert "h" in Everything._arguments_description()


# ——————————————————————————————————————————————————————————————
# Data types


def test_data_type_strategy_profiles_every_column(frame: pd.DataFrame) -> None:
    result = DataTypeInspectionStrategy().inspect(frame)

    assert set(result["columns"]) == set(frame.columns)
    assert result["columns"]["signup_date"]["semantic_type"] == "datetime"
    assert result["columns"]["city"]["semantic_type"] in {"categorical", "text"}
    assert result["summary"]["total_columns"] == len(frame.columns)
    assert isinstance(result["summary"]["columns_requiring_attention"], list)


def test_data_type_strategy_flags_missing_and_high_cardinality() -> None:
    data = pd.DataFrame(
        {
            # 12 distinct values with >50% missing so the missingness rule wins
            # over the low-cardinality rule.
            "mostly_missing": list(range(12)) + [None] * 14,
            "unique_ids": [f"id-{i}" for i in range(26)],
            "constant": ["same"] * 26,
        }
    )

    result = DataTypeInspectionStrategy().inspect(data)

    assert result["columns"]["mostly_missing"]["recommended_action"] == "high_missingness_review_before_use"
    assert result["columns"]["unique_ids"]["recommended_action"] == "likely_identifier_consider_dropping"
    assert result["columns"]["constant"]["recommended_action"] == "constant_column_consider_dropping"


def test_data_type_strategy_semantic_detection_helpers() -> None:
    strategy = DataTypeInspectionStrategy()
    numeric = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    datetime_strings = pd.Series(["2023-01-01", "2023-02-01"])
    booleans = pd.Series([True, False, True])

    assert strategy._semantic_type(numeric, "amount") == "numeric"
    assert strategy._semantic_type(datetime_strings, "signup") == "datetime_string"
    assert strategy._semantic_type(booleans, "is_active") == "boolean"

    # ``_recommend`` is the single place that maps a profile to an action.
    assert strategy._recommend(numeric, "numeric", 3, 0, 10) == "consider_casting_to_categorical"
    assert strategy._recommend(numeric, "numeric", 11, 0, 20) == "none"
    assert strategy._recommend(numeric, "datetime_string", 9, 0, 10) == "cast_to_datetime"
    assert strategy._recommend(numeric, "categorical", 10, 0, 10) == "likely_identifier_consider_dropping"
    assert strategy._recommend(numeric, "categorical", 1, 0, 10) == "constant_column_consider_dropping"
    assert strategy._recommend(numeric, "numeric", 11, 8, 12) == "high_missingness_review_before_use"


# ——————————————————————————————————————————————————————————————
# Summary statistics


def test_summary_statistics_reports_numeric_and_categorical(frame: pd.DataFrame) -> None:
    result = SummaryStatisticsInspectionStrategy().inspect(frame)

    assert "age" in result["numeric"]
    assert result["numeric"]["age"]["count"] == 7
    assert result["categorical"]["city"]["n_unique"] == 3
    assert "age" in result["summary"]["numeric_columns"]
    assert "city" in result["summary"]["categorical_columns"]


def test_summary_statistics_marks_all_missing_column() -> None:
    result = SummaryStatisticsInspectionStrategy().inspect(
        pd.DataFrame({"empty": [np.nan, np.nan, np.nan]})
    )

    assert result["numeric"]["empty"] == {"error": "all_missing"}


def test_summary_statistics_flags_skew_and_cardinality() -> None:
    data = pd.DataFrame(
        {
            "skewed": [1] * 49 + [1000],
            "many_values": [f"v{i}" for i in range(50)],
        }
    )

    result = SummaryStatisticsInspectionStrategy().inspect(data)

    assert "skewed" in result["summary"]["columns_with_skew"]
    assert "many_values" in result["summary"]["columns_with_high_cardinality"]


# ——————————————————————————————————————————————————————————————
# Outliers


def test_detect_outliers_iqr_and_zscore(frame: pd.DataFrame) -> None:
    iqr = DetectOutliersInspectionStrategy().inspect(frame, method="iqr")

    assert iqr["method"] == "iqr"
    assert "income" in iqr["columns"]
    assert iqr["columns"]["income"]["n_outliers"] >= 1
    assert "income" in iqr["summary"]["columns_with_outliers"]

    # A single extreme value is visible to the z-score method on a wide frame.
    spiky = pd.DataFrame({"value": [1] * 14 + [1000]})
    zscore = DetectOutliersInspectionStrategy().inspect(spiky, method="zscore")

    assert zscore["method"] == "zscore"
    assert zscore["columns"]["value"]["n_outliers"] >= 1
    assert "value" in zscore["summary"]["medium_severity_columns"]


def test_detect_outliers_unknown_method_falls_back_to_iqr(frame: pd.DataFrame) -> None:
    # The strategy is permissive: an unknown method label is echoed back but
    # the bounds are still computed with the IQR default.
    result = DetectOutliersInspectionStrategy().inspect(frame, method="magic")

    assert result["method"] == "magic"
    assert result["columns"]["income"]["n_outliers"] >= 1


def test_detect_outliers_all_missing_column() -> None:
    result = DetectOutliersInspectionStrategy().inspect(
        pd.DataFrame({"empty": [np.nan, np.nan]}), method="iqr"
    )

    assert result["columns"]["empty"] == {"error": "all_missing"}


# ——————————————————————————————————————————————————————————————
# Correlation


def test_correlation_numeric_pair(frame: pd.DataFrame) -> None:
    result = CorrelationInspectionStrategy().inspect(
        frame, column_1="age", column_2="income", threshold=0.3
    )

    assert result["method"] in {"pearson", "spearman"}
    assert result["columns"] == ["age", "income"]
    assert isinstance(result["is_associated"], bool)
    assert result["confidence"] in {"low", "medium", "high"}
    assert result["recommended_action"]


def test_correlation_categorical_pair(frame: pd.DataFrame) -> None:
    result = CorrelationInspectionStrategy().inspect(
        frame, column_1="city", column_2="status"
    )

    assert result["method"] in {"cramers_v", "chi_square"}
    assert result["is_associated"] in {True, False}


def test_correlation_mixed_pair(frame: pd.DataFrame) -> None:
    result = CorrelationInspectionStrategy().inspect(
        frame, column_1="city", column_2="income"
    )

    assert result["method"] in {"point_biserial", "anova", "eta_squared", "kruskal"}


def test_correlation_ordinal_uses_spearman(frame: pd.DataFrame) -> None:
    result = CorrelationInspectionStrategy().inspect(
        frame,
        column_1="age",
        column_2="churn",
        ordinal_columns=["churn"],
    )

    assert result["column_types"]["churn"] == "ordinal"
    assert result["method"] == "spearman"


def test_correlation_validation_errors(frame: pd.DataFrame) -> None:
    strategy = CorrelationInspectionStrategy()

    with pytest.raises(ValueError, match="Two column names"):
        strategy.inspect(frame)
    with pytest.raises(ValueError, match="Column not found"):
        strategy.inspect(frame, column_1="missing", column_2="age")
    with pytest.raises(ValueError, match="Column not found"):
        strategy.inspect(frame, column_1="age", column_2="missing")


# ——————————————————————————————————————————————————————————————
# Missing values


def test_missing_values_reports_ratio_and_indices(frame: pd.DataFrame) -> None:
    result = MissingValuesInspectionStrategy().inspect(
        frame, column_1="age", column_2="city", threshold=0.1
    )

    assert result["column"] == "age"
    assert result["missing_count"] == 1
    assert result["total_count"] == len(frame)
    assert result["has_missing_values"] is True
    assert result["missing_indices"] == [4]
    assert result["missingness_type"] in {"dependent", "random_like"}


def test_missing_values_without_missing_data(frame: pd.DataFrame) -> None:
    result = MissingValuesInspectionStrategy().inspect(frame, column="id")

    assert result["missing_count"] == 0
    assert result["missingness_type"] == "none"


def test_missing_values_without_related_column(frame: pd.DataFrame) -> None:
    result = MissingValuesInspectionStrategy().inspect(frame, target_column="city")

    assert result["missingness_type"] == "unassessed"
    assert result["dependency"] == "no_related_column_provided"
    assert "observed_value_counts" in result


def test_missing_values_validation_errors(frame: pd.DataFrame) -> None:
    strategy = MissingValuesInspectionStrategy()

    with pytest.raises(ValueError, match="target column"):
        strategy.inspect(frame)
    with pytest.raises(ValueError, match="Column not found"):
        strategy.inspect(frame, column_1="missing")
    with pytest.raises(ValueError, match="Column not found"):
        strategy.inspect(frame, column_1="age", column_2="missing")


# ——————————————————————————————————————————————————————————————
# Dispatcher


@pytest.mark.parametrize(
    ("name", "strategy_type"),
    [
        ("data_type", DataTypeInspectionStrategy),
        ("summary_statistics", SummaryStatisticsInspectionStrategy),
        ("correlation", CorrelationInspectionStrategy),
        ("missing_values", MissingValuesInspectionStrategy),
        ("detect_outliers", DetectOutliersInspectionStrategy),
    ],
)
def test_inspector_resolves_string_strategies(name: str, strategy_type: type) -> None:
    inspector = TabularDataInpsector(name)

    assert isinstance(inspector.strategy, strategy_type)


def test_inspector_accepts_strategy_instance_and_executes(frame: pd.DataFrame) -> None:
    inspector = TabularDataInpsector(DataTypeInspectionStrategy())

    result = inspector.execute_strategy(frame)

    assert "columns" in result


def test_inspector_set_strategy_swaps_implementation() -> None:
    inspector = TabularDataInpsector("data_type")
    inspector.set_strategy("summary_statistics")

    assert isinstance(inspector.strategy, SummaryStatisticsInspectionStrategy)


def test_inspector_rejects_unknown_and_wrong_types() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        TabularDataInpsector("does_not_exist")

    with pytest.raises(ValueError, match="Strategy must be"):
        TabularDataInpsector(123)  # type: ignore[arg-type]

    inspector = TabularDataInpsector("data_type")
    with pytest.raises(ValueError, match="Unknown strategy"):
        inspector.set_strategy("does_not_exist")
    with pytest.raises(ValueError, match="Strategy must be"):
        inspector.set_strategy(object())  # type: ignore[arg-type]
