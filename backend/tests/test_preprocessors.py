"""Tests for the tabular cleaning and transformation strategies.

Each strategy is exercised with the argument dictionary it advertises via
``_example()``, so a change to a strategy's contract (or to its example) is
caught here. The orchestrator helpers (``TabularDataCleaner`` /
``TabularDataTransformer``) are covered for registry resolution, pipeline
execution, and fitted-state replay.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from squirrel.modules.preprocessors.cleaning.tabular import (
    CastDtypesCleanStrategy,
    ClipOutliersCleanStrategy,
    DataCleanStrategy,
    DropColumnsCleanStrategy,
    DropDuplicatesCleanStrategy,
    FilterRowsCleanStrategy,
    ImputeMissingValuesCleanStrategy,
    RenameColumnsCleanStrategy,
    ScaleNumericCleanStrategy,
    TabularDataCleaner,
)
from squirrel.modules.preprocessors.transformers.tabular import (
    AggregationFeaturesTransformStrategy,
    BinaryEncodeTransformStrategy,
    BinNumericTransformStrategy,
    DataTransformStrategy,
    DatetimePartsTransformStrategy,
    FrequencyEncodeTransformStrategy,
    InteractionTermsTransformStrategy,
    LogTransformTransformStrategy,
    MinMaxScaleTransformStrategy,
    OneHotEncodeTransformStrategy,
    OrdinalEncodeTransformStrategy,
    PolynomialFeaturesTransformStrategy,
    PowerTransformTransformStrategy,
    RatioFeaturesTransformStrategy,
    RobustScaleTransformStrategy,
    StandardScaleTransformStrategy,
    TabularDataTransformer,
    TargetEncodeTransformStrategy,
)


@pytest.fixture()
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": list(range(1, 9)),
            "record_hash": [f"hash-{i}" for i in range(1, 9)],
            "Customer ID": [f"cust-{i}" for i in range(1, 9)],
            "age": [25, 32, 41, 29, 35, 38, 47, 51],
            "income": [40_000.0, 52_000.0, 61_000.0, 45_000.0, 48_000.0, 900_000.0, 55_000.0, 58_000.0],
            "city": ["London", "Paris", "London", None, "Paris", "Berlin", "London", "Paris"],
            "signup_date": [
                "2023-01-01",
                "2023-02-01",
                "2023-03-01",
                "2023-04-01",
                "2023-05-01",
                "2023-06-01",
                "2023-07-01",
                "2023-08-01",
            ],
            "status": ["active", "pending", "active", "active", "cancelled", "pending", "active", "active"],
            "gender": ["M", "F", "F", "M", "F", "M", "F", "M"],
            "education": ["bachelor", "master", "high_school", "phd", "bachelor", "master", "phd", "high_school"],
            "churn": [0, 1, 0, 0, 1, 0, 1, 0],
            "food_spend": [200.0, 300.0, 250.0, 400.0, 180.0, 220.0, 310.0, 275.0],
            "transport_spend": [50.0, 60.0, 55.0, 80.0, 40.0, 45.0, 70.0, 65.0],
            "housing_spend": [800.0, 900.0, 850.0, 1000.0, 750.0, 820.0, 950.0, 880.0],
            "total_spend": [1050.0, 1260.0, 1155.0, 1480.0, 970.0, 1085.0, 1330.0, 1220.0],
            "product_category": ["books", "games", "books", "music", "games", "books", "music", "games"],
            "country": ["UK", "FR", "UK", "DE", "FR", "DE", "UK", "FR"],
            "pixel_value": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
        }
    )


CLEANING_STRATEGIES = [
    DropDuplicatesCleanStrategy,
    DropColumnsCleanStrategy,
    ImputeMissingValuesCleanStrategy,
    CastDtypesCleanStrategy,
    RenameColumnsCleanStrategy,
    ClipOutliersCleanStrategy,
    FilterRowsCleanStrategy,
    ScaleNumericCleanStrategy,
]

TRANSFORM_STRATEGIES = [
    BinNumericTransformStrategy,
    DatetimePartsTransformStrategy,
    InteractionTermsTransformStrategy,
    PolynomialFeaturesTransformStrategy,
    AggregationFeaturesTransformStrategy,
    RatioFeaturesTransformStrategy,
    OneHotEncodeTransformStrategy,
    OrdinalEncodeTransformStrategy,
    TargetEncodeTransformStrategy,
    FrequencyEncodeTransformStrategy,
    BinaryEncodeTransformStrategy,
    StandardScaleTransformStrategy,
    MinMaxScaleTransformStrategy,
    RobustScaleTransformStrategy,
    LogTransformTransformStrategy,
    PowerTransformTransformStrategy,
]


# ——————————————————————————————————————————————————————————————
# Cleaning strategies


@pytest.mark.parametrize("strategy_type", CLEANING_STRATEGIES)
def test_cleaning_strategy_runs_with_its_example(strategy_type, frame: pd.DataFrame) -> None:
    strategy = strategy_type()
    example = strategy._example()

    cleaned, report = strategy.clean(frame.copy(), **example["arguments"])

    assert isinstance(cleaned, pd.DataFrame)
    assert isinstance(report, dict)
    assert "rows_affected" in report
    assert "columns_affected" in report


def test_drop_duplicates_reports_removed_rows() -> None:
    data = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})

    cleaned, report = DropDuplicatesCleanStrategy().clean(data)

    assert len(cleaned) == 2
    assert report["rows_affected"] == 1
    assert report["subset_used"] == "all"

    cleaned_keep_last, report_last = DropDuplicatesCleanStrategy().clean(data, keep="last")
    assert len(cleaned_keep_last) == 2
    assert report_last["rows_affected"] == 1


def test_drop_columns_rejects_missing_columns(frame: pd.DataFrame) -> None:
    cleaned, report = DropColumnsCleanStrategy().clean(frame, columns=["id"])

    assert "id" not in cleaned.columns
    assert report["columns_affected"] == ["id"]

    with pytest.raises(KeyError, match="not_a_column"):
        DropColumnsCleanStrategy().clean(frame, columns=["not_a_column"])


def test_impute_missing_values_fills_with_methods(frame: pd.DataFrame) -> None:
    data = frame.copy()
    data.loc[0, "age"] = np.nan
    data.loc[1, "city"] = None

    cleaned, report = ImputeMissingValuesCleanStrategy().clean(
        data, columns=["age", "city"], method="median", column_methods={"city": "mode"}
    )

    assert cleaned["age"].isna().sum() == 0
    assert cleaned["city"].isna().sum() == 0
    # One missing age, plus the fixture's existing city gap and the new one.
    assert report["total_cells_imputed"] == 3


def test_impute_missing_values_supports_constant_and_ffill(frame: pd.DataFrame) -> None:
    data = frame.copy()
    data.loc[0, "city"] = None
    data.loc[1, "city"] = None

    constant, constant_report = ImputeMissingValuesCleanStrategy().clean(
        data, columns=["city"], method="constant", fill_value="unknown"
    )
    # Two new gaps plus the fixture's existing one.
    assert (constant["city"] == "unknown").sum() == 3
    assert constant_report["total_cells_imputed"] == 3

    forward_data = frame.copy()
    forward_data.loc[3, "city"] = None
    forward, _ = ImputeMissingValuesCleanStrategy().clean(
        forward_data, columns=["city"], method="ffill"
    )
    assert forward.loc[3, "city"] == forward.loc[2, "city"]


def test_cast_dtypes_converts_columns(frame: pd.DataFrame) -> None:
    cleaned, report = CastDtypesCleanStrategy().clean(
        frame,
        column_dtypes={"signup_date": "datetime64[ns]", "age": "int64"},
        errors="coerce",
    )

    assert str(cleaned["signup_date"].dtype).startswith("datetime64")
    # pandas' nullable Int64 keeps the column integer-typed without requiring
    # the caller to special-case missing values.
    assert str(cleaned["age"].dtype) in {"int64", "Int64"}
    assert set(report["columns_affected"]) == {"signup_date", "age"}


def test_rename_columns_normalises_names(frame: pd.DataFrame) -> None:
    cleaned, report = RenameColumnsCleanStrategy().clean(
        frame, rename_map={"Customer ID": "customer_id"}, normalise=True
    )

    assert "customer_id" in cleaned.columns
    assert "Customer ID" not in cleaned.columns
    assert report["normalised"] is True


def test_clip_outliers_caps_income(frame: pd.DataFrame) -> None:
    cleaned, report = ClipOutliersCleanStrategy().clean(frame, columns=["income"], method="iqr")

    assert cleaned["income"].max() < frame["income"].max()
    assert report["total_cells_clipped"] >= 1


def test_filter_rows_applies_conditions(frame: pd.DataFrame) -> None:
    cleaned, report = FilterRowsCleanStrategy().clean(
        frame,
        conditions=[
            {"column": "age", "operator": ">=", "value": 18},
            {"column": "status", "operator": "in", "value": ["active", "pending"]},
        ],
    )

    assert set(cleaned["status"].unique()) <= {"active", "pending"}
    assert report["rows_affected"] == len(frame) - len(cleaned)


def test_scale_numeric_replays_fitted_state() -> None:
    train = pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0]})
    strategy = ScaleNumericCleanStrategy()

    scaled_train, report = strategy.clean(train, columns=["value"], method="standard")
    fitted = {"value": report["per_column"]["value"]}

    apply = pd.DataFrame({"value": [35.0]})
    scaled_apply, apply_report = strategy.clean(
        apply, fitted_state=fitted, columns=["value"], method="standard"
    )

    # Replaying the training mean/std keeps a single new row informative
    # instead of collapsing it to 0.
    assert scaled_apply["value"].iloc[0] != 0.0
    assert apply_report["per_column"]["value"]["mean"] == fitted["value"]["mean"]
    assert apply_report["per_column"]["value"]["std"] == fitted["value"]["std"]


def test_scale_numeric_supports_minmax_and_robust() -> None:
    data = pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0, 100.0]})
    strategy = ScaleNumericCleanStrategy()

    minmax, _ = strategy.clean(data, columns=["value"], method="minmax")
    assert minmax["value"].min() == 0.0
    assert minmax["value"].max() == 1.0

    robust, robust_report = strategy.clean(data, columns=["value"], method="robust")
    assert "median" in robust_report["per_column"]["value"]
    assert robust["value"].notna().all()


def test_scale_numeric_reports_invalid_columns() -> None:
    data = pd.DataFrame({"text": ["a", "b"]})

    _, report = ScaleNumericCleanStrategy().clean(data, columns=["text", "missing"])

    assert report["per_column"]["text"]["error"] == "non_numeric_column"
    assert report["per_column"]["missing"]["error"] == "column_not_found"


def test_cleaning_requires_columns_for_column_strategies(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        DropColumnsCleanStrategy().clean(frame, columns=[])
    with pytest.raises(ValueError):
        ScaleNumericCleanStrategy().clean(frame, columns=[])
    with pytest.raises(ValueError):
        ImputeMissingValuesCleanStrategy().clean(frame, columns=[])


# ——————————————————————————————————————————————————————————————
# Transform strategies


@pytest.mark.parametrize("strategy_type", TRANSFORM_STRATEGIES)
def test_transform_strategy_runs_with_its_example(strategy_type, frame: pd.DataFrame) -> None:
    strategy = strategy_type()
    example = strategy._example()

    transformed, report = strategy.transform(frame.copy(), **example["arguments"])

    assert isinstance(transformed, pd.DataFrame)
    assert isinstance(report, dict)
    assert "new_columns" in report
    assert "dropped_columns" in report
    assert report["rows_affected"] == 0


def test_transform_strategy_catalog_entries_are_complete() -> None:
    for strategy_type in TRANSFORM_STRATEGIES:
        entry = strategy_type.to_catalog_entry()
        assert entry["name"] == strategy_type.__name__
        assert entry["description"]
        assert entry["json_schema"]["type"] == "object"
        assert entry["example"]["arguments"] is not None


def test_bin_numeric_creates_binned_column(frame: pd.DataFrame) -> None:
    transformed, report = BinNumericTransformStrategy().transform(
        frame, columns=["age"], n_bins=3, strategy="quantile"
    )

    assert "age_bin" in transformed.columns
    assert report["new_columns"] == ["age_bin"]


def test_datetime_parts_extracts_requested_parts(frame: pd.DataFrame) -> None:
    transformed, report = DatetimePartsTransformStrategy().transform(
        frame, columns=["signup_date"], parts=["year", "month", "is_weekend"]
    )

    assert {"signup_date_year", "signup_date_month", "signup_date_is_weekend"} <= set(transformed.columns)
    assert len(report["new_columns"]) == 3


def test_one_hot_encode_drops_first_level(frame: pd.DataFrame) -> None:
    transformed, report = OneHotEncodeTransformStrategy().transform(
        frame, columns=["gender"], drop_first=True
    )

    assert "gender_M" in transformed.columns or "gender_F" in transformed.columns
    assert report["new_columns"]


def test_ordinal_encode_uses_declared_order(frame: pd.DataFrame) -> None:
    transformed, report = OrdinalEncodeTransformStrategy().transform(
        frame,
        columns=["education"],
        orderings={"education": ["high_school", "bachelor", "master", "phd"]},
    )

    assert transformed["education_ord"].max() == 3
    assert report["new_columns"] == ["education_ord"]


def test_target_encode_uses_target_mean(frame: pd.DataFrame) -> None:
    transformed, report = TargetEncodeTransformStrategy().transform(
        frame, columns=["city"], target_column="churn", smoothing=10.0
    )

    assert transformed["city_target_enc"].dtype.kind == "f"
    assert report["target_column"] == "churn"
    assert "global_mean" in report


def test_frequency_encode_counts_categories(frame: pd.DataFrame) -> None:
    transformed, report = FrequencyEncodeTransformStrategy().transform(
        frame, columns=["city"], normalise=False
    )

    # London appears three times, Paris three times, Berlin once.
    assert transformed["city_freq"].max() == 3
    assert report["new_columns"] == ["city_freq"]


def test_binary_encode_creates_digit_columns(frame: pd.DataFrame) -> None:
    transformed, report = BinaryEncodeTransformStrategy().transform(frame, columns=["country"])

    assert any(col.startswith("country_bin") for col in transformed.columns)
    assert report["new_columns"]


def test_standard_and_minmax_scaling(frame: pd.DataFrame) -> None:
    standard, _ = StandardScaleTransformStrategy().transform(frame, columns=["age", "income"])
    assert standard["age_scaled"].mean() == pytest.approx(0.0, abs=1e-9)

    minmax, minmax_report = MinMaxScaleTransformStrategy().transform(
        frame, columns=["pixel_value"], feature_min=-1.0, feature_max=1.0
    )
    assert minmax["pixel_value_minmax"].min() == pytest.approx(-1.0)
    assert minmax["pixel_value_minmax"].max() == pytest.approx(1.0)
    assert minmax_report["feature_range"] == [-1.0, 1.0]


def test_robust_scale_uses_quantile_range(frame: pd.DataFrame) -> None:
    transformed, report = RobustScaleTransformStrategy().transform(
        frame, columns=["income"], quantile_range=[10.0, 90.0]
    )

    assert transformed["income_robust"].notna().all()
    assert report["new_columns"] == ["income_robust"]


def test_log_transform_supports_bases(frame: pd.DataFrame) -> None:
    for base in ("natural", "log2", "log10"):
        transformed, _ = LogTransformTransformStrategy().transform(
            frame, columns=["income"], base=base, shift=1.0
        )
        assert transformed["income"].notna().all()

    with pytest.raises(ValueError, match="Unknown base"):
        LogTransformTransformStrategy().transform(frame, columns=["income"], base="log5")


def test_power_transform_yeo_johnson(frame: pd.DataFrame) -> None:
    transformed, report = PowerTransformTransformStrategy().transform(
        frame, columns=["income"], method="yeo-johnson"
    )

    assert transformed["income_power"].notna().all()
    assert report["new_columns"] == ["income_power"]


def test_transform_requires_columns() -> None:
    data = pd.DataFrame({"a": [1, 2]})
    for strategy_type in TRANSFORM_STRATEGIES:
        if strategy_type in {InteractionTermsTransformStrategy, AggregationFeaturesTransformStrategy, RatioFeaturesTransformStrategy}:
            continue
        with pytest.raises(ValueError):
            strategy_type().transform(data)


def test_interaction_terms_requires_pairs(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="pairs"):
        InteractionTermsTransformStrategy().transform(frame, pairs=[])


def test_aggregation_features_requires_group_and_columns(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="group_column"):
        AggregationFeaturesTransformStrategy().transform(frame, agg_columns=["income"])
    with pytest.raises(ValueError, match="agg_columns"):
        AggregationFeaturesTransformStrategy().transform(frame, group_column="city")
    with pytest.raises(ValueError, match="Group column not found"):
        AggregationFeaturesTransformStrategy().transform(
            frame, group_column="nope", agg_columns=["income"]
        )


def test_ratio_features_requires_numerators_and_denominator(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="numerator_columns"):
        RatioFeaturesTransformStrategy().transform(frame, denominator_column="total_spend")
    with pytest.raises(ValueError, match="Denominator column not found"):
        RatioFeaturesTransformStrategy().transform(
            frame, numerator_columns=["food_spend"], denominator_column="nope"
        )


def test_polynomial_features_validates_degree(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="degree"):
        PolynomialFeaturesTransformStrategy().transform(frame, columns=["age"], degree=0)


# ——————————————————————————————————————————————————————————————
# Orchestrators


def test_cleaner_resolves_strings_and_instances(frame: pd.DataFrame) -> None:
    cleaner = TabularDataCleaner("drop_duplicates")
    assert isinstance(cleaner.strategy, DropDuplicatesCleanStrategy)

    cleaner.set_strategy(ScaleNumericCleanStrategy())
    assert isinstance(cleaner.strategy, ScaleNumericCleanStrategy)

    with pytest.raises(ValueError, match="Unknown strategy"):
        TabularDataCleaner("nope")
    with pytest.raises(ValueError, match="strategy must be"):
        TabularDataCleaner(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown strategy"):
        cleaner.set_strategy("nope")


def test_cleaner_run_pipeline_threads_reports(frame: pd.DataFrame) -> None:
    cleaner = TabularDataCleaner("drop_duplicates")
    steps = [
        {"cleaning": "rename_columns", "name": "rename", "arguments": {"rename_map": {"Customer ID": "customer_id"}}},
        {"cleaning": "filter_rows", "name": "filter", "arguments": {"conditions": [{"column": "age", "operator": ">=", "value": 30}]}},
    ]

    cleaned, reports = cleaner.run_pipeline(frame, steps)

    assert "customer_id" in cleaned.columns
    assert cleaned["age"].min() >= 30
    assert [r["name"] for r in reports] == ["rename", "filter"]
    assert [r["step"] for r in reports] == [1, 2]


def test_cleaner_run_pipeline_replays_fitted_states() -> None:
    train = pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0]})
    cleaner = TabularDataCleaner("scale_numeric")
    steps = [{"cleaning": "scale_numeric", "arguments": {"columns": ["value"], "method": "standard"}}]

    _, reports = cleaner.run_pipeline(train, steps)
    fitted = [reports[0]["per_column"]]

    applied, _ = cleaner.run_pipeline(pd.DataFrame({"value": [35.0]}), steps, fitted_states=fitted)

    assert applied["value"].iloc[0] != 0.0


def test_transformer_resolves_strings_and_instances(frame: pd.DataFrame) -> None:
    transformer = TabularDataTransformer("bin_numeric")
    assert isinstance(transformer.strategy, BinNumericTransformStrategy)

    transformer.set_strategy(StandardScaleTransformStrategy())
    assert isinstance(transformer.strategy, StandardScaleTransformStrategy)

    with pytest.raises(ValueError, match="Unknown strategy"):
        TabularDataTransformer("nope")
    with pytest.raises(ValueError, match="strategy must be"):
        TabularDataTransformer(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown strategy"):
        transformer.set_strategy("nope")


def test_transformer_run_pipeline_threads_reports(frame: pd.DataFrame) -> None:
    transformer = TabularDataTransformer("bin_numeric")
    steps = [
        {"transform": "bin_numeric", "name": "bin", "arguments": {"columns": ["age"], "n_bins": 3}},
        {"transform": "frequency_encode", "name": "freq", "arguments": {"columns": ["city"]}},
    ]

    transformed, reports = transformer.run_pipeline(frame, steps)

    assert "age_bin" in transformed.columns
    assert transformed["city_freq"].max() <= len(frame)
    assert [r["name"] for r in reports] == ["bin", "freq"]


def test_transformer_run_pipeline_replays_fitted_states() -> None:
    train = pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0]})
    transformer = TabularDataTransformer("standard_scale")
    steps = [{"transform": "standard_scale", "arguments": {"columns": ["value"]}}]

    _, reports = transformer.run_pipeline(train, steps)
    fitted = [reports[0]["per_column"]]

    applied, _ = transformer.run_pipeline(
        pd.DataFrame({"value": [25.0]}), steps, fitted_states=fitted
    )

    assert applied["value"].iloc[0] != 0.0
