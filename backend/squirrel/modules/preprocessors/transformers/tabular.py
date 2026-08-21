#!/usr/bin/python
"""
Tabular Data Transformer Module

A module for performing feature engineering, encoding, and normalisation on tabular
datasets. This module provides a base class `DataTransformStrategy` that mirrors the
`DataCleanStrategy` / `DataInspectStrategy` architecture: every concrete strategy
declares ``argument_specs``, exposes a catalog entry and JSON schema, and returns a
``(transformed_df, report_dict)`` tuple so it can be composed into pipelines via
``TabularDataTransformer.run_pipeline()``.

Strategy groups
---------------
Feature Engineering  : BinNumericTransformStrategy, DatetimePartsTransformStrategy,
                       InteractionTermsTransformStrategy, PolynomialFeaturesTransformStrategy,
                       AggregationFeaturesTransformStrategy, RatioFeaturesTransformStrategy
Encoding             : OneHotEncodeTransformStrategy, OrdinalEncodeTransformStrategy,
                       TargetEncodeTransformStrategy, FrequencyEncodeTransformStrategy,
                       BinaryEncodeTransformStrategy
Normalisation        : StandardScaleTransformStrategy, MinMaxScaleTransformStrategy,
                       RobustScaleTransformStrategy, LogTransformTransformStrategy,
                       PowerTransformTransformStrategy
"""
# ——————————————————————————————————————————————————————————————
# Imports
from abc import ABC, abstractmethod
from typing import Any, Union
import pandas as pd
import numpy as np

from squirrel.schemas.argument_spec import ArgumentSpec


# ——— Base Class ———————————————————————————————————————————————————————————————
class DataTransformStrategy(ABC):
    """
    Abstract base class for tabular data transformation strategies.
    Subclasses declare ``argument_specs`` to expose their accepted arguments
    to the catalog, prompt generator, and runtime validator.
    """

    argument_specs: list[ArgumentSpec] = []

    @abstractmethod
    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        Apply the transformation to *data*.

        :param data: DataFrame to transform.
        :return: (transformed_df, report_dict).  The report always contains at
            least ``new_columns``, ``dropped_columns``, and ``rows_affected``.
        """

    # ── Catalog / schema helpers (identical pattern to DataCleanStrategy) ────

    @classmethod
    def to_catalog_entry(cls) -> dict:
        first_doc_line = (cls.__doc__ or "").strip().splitlines()[0]
        return {
            "name": cls.__name__,
            "description": first_doc_line,
            "json_schema": cls.to_json_schema(),
            "arguments_schema": [
                {
                    "name": s.name, "type": s.type, "required": s.required,
                    "default": s.default, "possible_values": s.possible_values,
                    "value_descriptions": s.value_descriptions,
                    "description": s.description,
                    **({"condition": s.condition} if s.condition else {}),
                }
                for s in cls.argument_specs
            ],
            "arguments_description": cls._arguments_description(),
            "example": cls._example(),
        }

    @classmethod
    def to_json_schema(cls) -> dict:
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        for spec in cls.argument_specs:
            properties[spec.name] = cls._spec_to_json_schema(spec)
            if spec.required:
                required.append(spec.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _spec_to_json_schema(spec: ArgumentSpec) -> dict[str, Any]:
        t = spec.type.strip().lower()
        if t in {"str", "string"}:
            s: dict[str, Any] = {"type": "string"}
        elif t in {"int", "integer"}:
            s = {"type": "integer"}
        elif t in {"float", "number", "double"}:
            s = {"type": "number"}
        elif t in {"bool", "boolean"}:
            s = {"type": "boolean"}
        elif t in {"list", "array"}:
            s = {"type": "array"}
        elif t in {"dict", "object"}:
            s = {"type": "object"}
        elif t.startswith("list[") and t.endswith("]"):
            item_type = t[5:-1].strip()
            s = {
                "type": "array",
                "items": DataTransformStrategy._spec_to_json_schema(
                    ArgumentSpec(
                        name=spec.name, type=item_type, required=spec.required,
                        default=spec.default, description=spec.description,
                        possible_values=spec.possible_values,
                        value_descriptions=spec.value_descriptions,
                        condition=spec.condition,
                    )
                ),
            }
        else:
            s = {"type": "string"}
        if spec.description:
            s["description"] = spec.description
        if spec.default is not None:
            s["default"] = spec.default
        return s

    @classmethod
    def _arguments_description(cls) -> str:
        if not cls.argument_specs:
            return "No arguments required."
        req = [s.name for s in cls.argument_specs if s.required]
        opt = [s.name for s in cls.argument_specs if not s.required]
        parts = []
        if req:
            parts.append(f"Required: {', '.join(req)}.")
        if opt:
            parts.append(f"Optional: {', '.join(opt)}.")
        return " ".join(parts)

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 1,
            "name": cls.__name__,
            "transform": cls.__name__,
            "objective": "(override _example() to provide a concrete objective)",
            "actions": [],
            "arguments": {
                s.name: s.default for s in cls.argument_specs if s.required
            },
            "expected_output": [],
        }


# ══════════════════════════════════════════════════════════════════════════════
# ── FEATURE ENGINEERING ───────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class BinNumericTransformStrategy(DataTransformStrategy):
    """
    Discretises one or more numeric columns into labelled bins using either
    equal-width or quantile-based (equal-frequency) binning, producing a new
    categorical column per source column.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to bin.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is binned independently."},
        ),
        ArgumentSpec(
            name="n_bins",
            type="int",
            required=False,
            default=5,
            description="Number of bins.",
            possible_values="any positive integer ≥ 2",
            value_descriptions={
                "3": "Low / medium / high.",
                "5": "Standard default.",
                "10": "Decile binning.",
            },
        ),
        ArgumentSpec(
            name="strategy",
            type="str",
            required=False,
            default="quantile",
            description="Binning strategy.",
            possible_values="quantile | uniform",
            value_descriptions={
                "quantile": "Equal-frequency bins (robust to skew).",
                "uniform": "Equal-width bins.",
            },
        ),
        ArgumentSpec(
            name="labels",
            type="list[str]",
            required=False,
            default=[],
            description=(
                "Custom bin labels. Must have exactly n_bins elements. "
                "Leave empty to auto-generate ordinal integer labels."
            ),
            possible_values="list of strings of length n_bins, or []",
            value_descriptions={
                "[]": "Auto-labels: 0, 1, 2, …",
                "['low','med','high']": "Example for n_bins=3.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_bin",
            description="Suffix appended to the source column name to create the new column.",
            possible_values="any string",
            value_descriptions={"_bin": "Default — e.g. 'age' → 'age_bin'."},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=False,
            description="If true, remove the source column after binning.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 1,
            "name": "Bin age into quantile deciles",
            "transform": cls.__name__,
            "objective": "Convert continuous age into an ordinal categorical feature.",
            "actions": [
                "Apply 5-quantile binning to 'age'.",
                "Store result in 'age_bin'.",
            ],
            "arguments": {"columns": ["age"], "n_bins": 5, "strategy": "quantile"},
            "expected_output": [
                "New column 'age_bin' with integer bin labels 0–4",
                "Per-column bin edges",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        n_bins: int = int(kwargs.get("n_bins", 5))
        strategy: str = kwargs.get("strategy", "quantile")
        labels_arg: list[str] = kwargs.get("labels") or []
        suffix: str = kwargs.get("suffix", "_bin")
        drop_original: bool = bool(kwargs.get("drop_original", False))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        labels: Any = labels_arg if labels_arg else False
        result = data.copy()
        per_column: dict = {}
        new_columns: list[str] = []
        dropped_columns: list[str] = []

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            new_col = col + suffix
            series = result[col]

            try:
                if strategy == "quantile":
                    binned, bins = pd.qcut(
                        series, q=n_bins, labels=labels, retbins=True, duplicates="drop"
                    )
                else:
                    binned, bins = pd.cut(
                        series, bins=n_bins, labels=labels, retbins=True
                    )
                result[new_col] = binned
                new_columns.append(new_col)
                per_column[col] = {
                    "new_column": new_col,
                    "bin_edges": [round(float(b), 6) for b in bins],
                    "n_bins_actual": len(bins) - 1,
                }
                if drop_original:
                    result.drop(columns=[col], inplace=True)
                    dropped_columns.append(col)
            except Exception as exc:
                per_column[col] = {"error": str(exc)}

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class DatetimePartsTransformStrategy(DataTransformStrategy):
    """
    Extracts calendar and time components from one or more datetime columns,
    creating a configurable set of new integer columns (year, month, day,
    day_of_week, hour, minute, second, quarter, week_of_year, is_weekend).
    """

    _PART_MAP = {
        "year":        lambda s: s.dt.year,
        "month":       lambda s: s.dt.month,
        "day":         lambda s: s.dt.day,
        "day_of_week": lambda s: s.dt.dayofweek,
        "hour":        lambda s: s.dt.hour,
        "minute":      lambda s: s.dt.minute,
        "second":      lambda s: s.dt.second,
        "quarter":     lambda s: s.dt.quarter,
        "week_of_year":lambda s: s.dt.isocalendar().week.astype(int),
        "is_weekend":  lambda s: (s.dt.dayofweek >= 5).astype(int),
    }

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Datetime columns to decompose.",
            possible_values="list of datetime64 column names",
            value_descriptions={"[<col>, ...]": "Each column is decomposed independently."},
        ),
        ArgumentSpec(
            name="parts",
            type="list[str]",
            required=False,
            default=["year", "month", "day", "day_of_week", "hour"],
            description="Calendar/time parts to extract.",
            possible_values=(
                "year | month | day | day_of_week | hour | minute | second | "
                "quarter | week_of_year | is_weekend"
            ),
            value_descriptions={
                "year": "4-digit year integer.",
                "month": "Month integer 1–12.",
                "day": "Day of month 1–31.",
                "day_of_week": "0=Monday … 6=Sunday.",
                "hour": "Hour 0–23.",
                "is_weekend": "1 if Saturday or Sunday, else 0.",
            },
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=False,
            description="If true, remove the source datetime column after extraction.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 2,
            "name": "Decompose signup_date",
            "transform": cls.__name__,
            "objective": "Extract year, month, day_of_week, and is_weekend from signup_date.",
            "actions": ["Extract 4 parts from 'signup_date'."],
            "arguments": {
                "columns": ["signup_date"],
                "parts": ["year", "month", "day_of_week", "is_weekend"],
            },
            "expected_output": [
                "4 new integer columns: signup_date_year, signup_date_month, …",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        parts: list[str] = kwargs.get("parts") or ["year", "month", "day", "day_of_week", "hour"]
        drop_original: bool = bool(kwargs.get("drop_original", False))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            series = pd.to_datetime(result[col], errors="coerce")
            extracted: list[str] = []

            for part in parts:
                if part not in self._PART_MAP:
                    continue
                new_col = f"{col}_{part}"
                result[new_col] = self._PART_MAP[part](series)
                new_columns.append(new_col)
                extracted.append(new_col)

            per_column[col] = {"extracted_columns": extracted}

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class InteractionTermsTransformStrategy(DataTransformStrategy):
    """
    Creates pairwise interaction features between specified numeric column pairs
    using multiplication, division, addition, or subtraction, storing results
    as new columns with auto-generated names.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="pairs",
            type="list",
            required=True,
            default=None,
            description=(
                "List of interaction dicts. Each dict has: "
                "'column_1' (str), 'column_2' (str), and optionally 'operator' (str)."
            ),
            possible_values=(
                "list of dicts with keys: column_1, column_2, operator. "
                "Operators: multiply | divide | add | subtract."
            ),
            value_descriptions={
                "{'column_1': 'a', 'column_2': 'b', 'operator': 'multiply'}": "Creates column 'a_x_b'.",
                "{'column_1': 'a', 'column_2': 'b', 'operator': 'divide'}": "Creates column 'a_div_b'.",
            },
        ),
        ArgumentSpec(
            name="handle_division_by_zero",
            type="str",
            required=False,
            default="nan",
            description="What to do when dividing by zero.",
            possible_values="nan | zero | inf",
            value_descriptions={
                "nan": "Replace result with NaN.",
                "zero": "Replace result with 0.",
                "inf": "Leave as ±inf (default NumPy behaviour).",
            },
            condition="only relevant when operator is 'divide'",
        ),
    ]

    _OP_SUFFIX = {
        "multiply": "_x_",
        "divide": "_div_",
        "add": "_plus_",
        "subtract": "_minus_",
    }

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 3,
            "name": "Create income-per-age interaction",
            "transform": cls.__name__,
            "objective": "Capture income relative to age as a new feature.",
            "actions": ["Divide 'income' by 'age', store as 'income_div_age'."],
            "arguments": {
                "pairs": [{"column_1": "income", "column_2": "age", "operator": "divide"}]
            },
            "expected_output": ["New column 'income_div_age'"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        pairs: list[dict] = kwargs.get("pairs") or []
        zero_handling: str = kwargs.get("handle_division_by_zero", "nan")

        if not pairs:
            raise ValueError("'pairs' must be a non-empty list of dicts.")

        result = data.copy()
        new_columns: list[str] = []
        per_pair: list[dict] = []

        for pair in pairs:
            c1: str = pair.get("column_1", "")
            c2: str = pair.get("column_2", "")
            op: str = pair.get("operator", "multiply")

            for col in (c1, c2):
                if col not in result.columns:
                    per_pair.append({"pair": (c1, c2), "error": f"column_not_found: {col}"})
                    continue

            sep = self._OP_SUFFIX.get(op, "_op_")
            new_col = f"{c1}{sep}{c2}"

            s1 = pd.to_numeric(result[c1], errors="coerce")
            s2 = pd.to_numeric(result[c2], errors="coerce")

            if op == "multiply":
                result[new_col] = s1 * s2
            elif op == "divide":
                with np.errstate(divide="ignore", invalid="ignore"):
                    divided = s1 / s2
                if zero_handling == "nan":
                    divided = divided.replace([np.inf, -np.inf], np.nan)
                elif zero_handling == "zero":
                    divided = divided.replace([np.inf, -np.inf], 0.0)
                result[new_col] = divided
            elif op == "add":
                result[new_col] = s1 + s2
            elif op == "subtract":
                result[new_col] = s1 - s2
            else:
                per_pair.append({"pair": (c1, c2), "error": f"unknown_operator: {op}"})
                continue

            new_columns.append(new_col)
            per_pair.append({"pair": (c1, c2), "operator": op, "new_column": new_col})

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "per_pair": per_pair,
        }


# ——————————————————————————————————————————————————————————————
class PolynomialFeaturesTransformStrategy(DataTransformStrategy):
    """
    Generates polynomial and optional interaction features up to a specified
    degree for a list of numeric columns, without relying on scikit-learn.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to expand.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column gets squared, cubed, etc."},
        ),
        ArgumentSpec(
            name="degree",
            type="int",
            required=False,
            default=2,
            description="Maximum polynomial degree.",
            possible_values="integer ≥ 2",
            value_descriptions={
                "2": "Adds squared terms: col², col1×col2.",
                "3": "Adds squared and cubic terms.",
            },
        ),
        ArgumentSpec(
            name="include_interactions",
            type="bool",
            required=False,
            default=True,
            description="If true, also generate pairwise column cross-products.",
            possible_values="true | false",
            value_descriptions={
                "true": "e.g. col1² and col1×col2.",
                "false": "Only per-column powers: col1², col1³, …",
            },
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=False,
            description="If true, remove the source columns after expansion.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 4,
            "name": "Polynomial degree-2 expansion of age and income",
            "transform": cls.__name__,
            "objective": "Capture non-linear relationships for a linear model.",
            "actions": [
                "Generate age², income², and age×income.",
            ],
            "arguments": {
                "columns": ["age", "income"],
                "degree": 2,
                "include_interactions": True,
            },
            "expected_output": [
                "New columns: age_pow2, income_pow2, age_x_income",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        from itertools import combinations

        columns: list[str] = kwargs.get("columns") or []
        degree: int = int(kwargs.get("degree", 2))
        include_interactions: bool = bool(kwargs.get("include_interactions", True))
        drop_original: bool = bool(kwargs.get("drop_original", False))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")
        if degree < 2:
            raise ValueError("'degree' must be ≥ 2.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []

        valid_cols = [c for c in columns if c in result.columns]
        missing_cols = [c for c in columns if c not in result.columns]

        # Per-column powers
        for col in valid_cols:
            s = pd.to_numeric(result[col], errors="coerce")
            for d in range(2, degree + 1):
                new_col = f"{col}_pow{d}"
                result[new_col] = s ** d
                new_columns.append(new_col)

        # Pairwise interactions (degree 2 cross-products only)
        interaction_columns: list[str] = []
        if include_interactions and len(valid_cols) > 1:
            for c1, c2 in combinations(valid_cols, 2):
                new_col = f"{c1}_x_{c2}"
                result[new_col] = (
                    pd.to_numeric(result[c1], errors="coerce")
                    * pd.to_numeric(result[c2], errors="coerce")
                )
                new_columns.append(new_col)
                interaction_columns.append(new_col)

        if drop_original:
            result.drop(columns=valid_cols, inplace=True)
            dropped_columns = valid_cols

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "degree": degree,
            "interaction_columns": interaction_columns,
            "columns_not_found": missing_cols,
        }


# ——————————————————————————————————————————————————————————————
class AggregationFeaturesTransformStrategy(DataTransformStrategy):
    """
    Computes group-level aggregations (mean, std, min, max, count, median)
    for a numeric target column grouped by a categorical key column, then
    joins the results back as new columns on the original DataFrame.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="group_column",
            type="str",
            required=True,
            default=None,
            description="Categorical column to group by.",
            possible_values="any column name",
            value_descriptions={"<col>": "e.g. 'city', 'product_category'."},
        ),
        ArgumentSpec(
            name="agg_columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to aggregate.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is aggregated separately."},
        ),
        ArgumentSpec(
            name="agg_funcs",
            type="list[str]",
            required=False,
            default=["mean", "std"],
            description="Aggregation functions to compute.",
            possible_values="mean | std | min | max | count | median | sum",
            value_descriptions={
                "mean": "Group mean.",
                "std": "Group standard deviation.",
                "count": "Non-null count per group.",
            },
        ),
        ArgumentSpec(
            name="prefix",
            type="str",
            required=False,
            default="grp_",
            description="Prefix prepended to generated column names.",
            possible_values="any string",
            value_descriptions={"grp_": "Default — e.g. 'grp_city_income_mean'."},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 5,
            "name": "Group income statistics by city",
            "transform": cls.__name__,
            "objective": "Add mean and std of income per city as new features.",
            "actions": ["Compute mean and std of 'income' grouped by 'city'."],
            "arguments": {
                "group_column": "city",
                "agg_columns": ["income"],
                "agg_funcs": ["mean", "std"],
            },
            "expected_output": [
                "New columns: grp_city_income_mean, grp_city_income_std",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        group_column: str = kwargs.get("group_column") or ""
        agg_columns: list[str] = kwargs.get("agg_columns") or []
        agg_funcs: list[str] = kwargs.get("agg_funcs") or ["mean", "std"]
        prefix: str = kwargs.get("prefix", "grp_")

        if not group_column:
            raise ValueError("'group_column' is required.")
        if not agg_columns:
            raise ValueError("'agg_columns' must be a non-empty list.")
        if group_column not in data.columns:
            raise ValueError(f"Group column not found: {group_column}")

        result = data.copy()
        new_columns: list[str] = []

        for agg_col in agg_columns:
            if agg_col not in result.columns:
                continue
            grouped = result.groupby(group_column)[agg_col]
            for func in agg_funcs:
                new_col = f"{prefix}{group_column}_{agg_col}_{func}"
                result[new_col] = result[group_column].map(
                    getattr(grouped, func)()
                )
                new_columns.append(new_col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "group_column": group_column,
            "agg_columns": agg_columns,
            "agg_funcs": agg_funcs,
        }


# ——————————————————————————————————————————————————————————————
class RatioFeaturesTransformStrategy(DataTransformStrategy):
    """
    Computes ratio features between a list of numerator columns and a single
    denominator column (or the row-wise sum of all numerator columns), useful
    for compositional data such as spend breakdown or part-to-whole shares.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="numerator_columns",
            type="list[str]",
            required=True,
            default=None,
            description="Columns to use as numerators.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is divided by the denominator."},
        ),
        ArgumentSpec(
            name="denominator_column",
            type="str",
            required=False,
            default=None,
            description=(
                "Column to use as the denominator. If omitted, the row-wise sum of "
                "all numerator_columns is used (part-of-whole ratio)."
            ),
            possible_values="any numeric column name, or omit for row sum",
            value_descriptions={
                "<col>": "e.g. 'total_spend' when numerators are per-category spends.",
                "omitted": "Denominator = sum of all numerator columns per row.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_ratio",
            description="Suffix appended to each numerator column name.",
            possible_values="any string",
            value_descriptions={"_ratio": "Default — e.g. 'food_spend' → 'food_spend_ratio'."},
        ),
        ArgumentSpec(
            name="handle_division_by_zero",
            type="str",
            required=False,
            default="nan",
            description="Behaviour when denominator is zero.",
            possible_values="nan | zero",
            value_descriptions={
                "nan": "Result becomes NaN.",
                "zero": "Result becomes 0.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 6,
            "name": "Compute per-category spend share",
            "transform": cls.__name__,
            "objective": "Express food, transport, and housing spends as fractions of total spend.",
            "actions": ["Divide each spend column by total_spend."],
            "arguments": {
                "numerator_columns": ["food_spend", "transport_spend", "housing_spend"],
                "denominator_column": "total_spend",
            },
            "expected_output": [
                "New columns: food_spend_ratio, transport_spend_ratio, housing_spend_ratio",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        numerator_columns: list[str] = kwargs.get("numerator_columns") or []
        denominator_column: str | None = kwargs.get("denominator_column")
        suffix: str = kwargs.get("suffix", "_ratio")
        zero_handling: str = kwargs.get("handle_division_by_zero", "nan")

        if not numerator_columns:
            raise ValueError("'numerator_columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []

        if denominator_column:
            if denominator_column not in result.columns:
                raise ValueError(f"Denominator column not found: {denominator_column}")
            denom = pd.to_numeric(result[denominator_column], errors="coerce")
        else:
            valid = [c for c in numerator_columns if c in result.columns]
            denom = result[valid].apply(pd.to_numeric, errors="coerce").sum(axis=1)

        with np.errstate(divide="ignore", invalid="ignore"):
            denom_safe = denom.replace(0, np.nan)

        for col in numerator_columns:
            if col not in result.columns:
                continue
            new_col = col + suffix
            ratio = pd.to_numeric(result[col], errors="coerce") / denom_safe
            if zero_handling == "zero":
                ratio = ratio.fillna(0.0)
            result[new_col] = ratio
            new_columns.append(new_col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "denominator_used": denominator_column or "row_sum_of_numerators",
        }


# ══════════════════════════════════════════════════════════════════════════════
# ── ENCODING ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class OneHotEncodeTransformStrategy(DataTransformStrategy):
    """
    Applies one-hot encoding to one or more categorical columns, optionally
    dropping the first dummy to avoid multicollinearity, and removes the
    source columns by default.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Categorical columns to one-hot encode.",
            possible_values="list of object or category column names",
            value_descriptions={"[<col>, ...]": "Each column is encoded independently."},
        ),
        ArgumentSpec(
            name="drop_first",
            type="bool",
            required=False,
            default=True,
            description="Drop the first dummy column per feature to avoid multicollinearity.",
            possible_values="true | false",
            value_descriptions={
                "true": "k-1 dummies for k categories.",
                "false": "k dummies for k categories.",
            },
        ),
        ArgumentSpec(
            name="prefix_sep",
            type="str",
            required=False,
            default="_",
            description="Separator between column name and category value in dummy column names.",
            possible_values="any string",
            value_descriptions={"_": "Default — e.g. 'city_London'."},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=True,
            description="If true (default), remove the source categorical column.",
            possible_values="true | false",
            value_descriptions={},
        ),
        ArgumentSpec(
            name="max_cardinality",
            type="int",
            required=False,
            default=50,
            description=(
                "Skip encoding any column whose cardinality exceeds this value and "
                "log a warning in the report instead."
            ),
            possible_values="any positive integer",
            value_descriptions={
                "50": "Default guard against high-cardinality explosion.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 7,
            "name": "One-hot encode gender and city",
            "transform": cls.__name__,
            "objective": "Convert nominal categoricals into binary dummy features.",
            "actions": [
                "Encode 'gender' (2 categories → 1 dummy).",
                "Encode 'city' (5 categories → 4 dummies).",
            ],
            "arguments": {"columns": ["gender", "city"], "drop_first": True},
            "expected_output": [
                "5 new binary columns",
                "Source columns dropped",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        drop_first: bool = bool(kwargs.get("drop_first", True))
        prefix_sep: str = kwargs.get("prefix_sep", "_")
        drop_original: bool = bool(kwargs.get("drop_original", True))
        max_cardinality: int = int(kwargs.get("max_cardinality", 50))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        skipped: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            n_unique = result[col].nunique(dropna=True)
            if n_unique > max_cardinality:
                skipped.append(col)
                per_column[col] = {
                    "skipped": True,
                    "reason": f"cardinality {n_unique} exceeds max_cardinality {max_cardinality}",
                }
                continue

            dummies = pd.get_dummies(
                result[col], prefix=col, prefix_sep=prefix_sep, drop_first=drop_first
            ).astype(int)
            result = pd.concat([result, dummies], axis=1)
            new_columns.extend(dummies.columns.tolist())
            per_column[col] = {
                "n_categories": n_unique,
                "dummy_columns": dummies.columns.tolist(),
            }

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "skipped_columns": skipped,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class OrdinalEncodeTransformStrategy(DataTransformStrategy):
    """
    Encodes one or more ordinal categorical columns as integers according to
    a caller-supplied ordering or, if none is given, lexicographic order,
    with optional handling for unseen categories.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Ordinal categorical columns to encode.",
            possible_values="list of column names",
            value_descriptions={"[<col>, ...]": "Each column is encoded independently."},
        ),
        ArgumentSpec(
            name="orderings",
            type="dict",
            required=False,
            default={},
            description=(
                "Per-column explicit category orderings, e.g. "
                "{'size': ['small', 'medium', 'large']}. "
                "Columns not listed here are encoded in lexicographic order."
            ),
            possible_values="dict mapping column names to ordered lists of category values",
            value_descriptions={
                "{}": "All columns use lexicographic ordering.",
                "{'col': ['a', 'b', 'c']}": "'a'→0, 'b'→1, 'c'→2.",
            },
        ),
        ArgumentSpec(
            name="unknown_value",
            type="int",
            required=False,
            default=-1,
            description="Integer assigned to categories not present in the ordering.",
            possible_values="any integer; -1 is a common sentinel",
            value_descriptions={
                "-1": "Mark unknown categories as -1.",
                "-999": "Use as a strong sentinel if -1 is a valid ordinal.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_ord",
            description="Suffix appended to the source column name.",
            possible_values="any string",
            value_descriptions={"_ord": "Default — e.g. 'size' → 'size_ord'."},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=True,
            description="If true (default), remove the source column after encoding.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 8,
            "name": "Ordinal encode education level",
            "transform": cls.__name__,
            "objective": "Encode education as an ordered integer feature.",
            "actions": [
                "Map 'high_school'→0, 'bachelor'→1, 'master'→2, 'phd'→3.",
            ],
            "arguments": {
                "columns": ["education"],
                "orderings": {"education": ["high_school", "bachelor", "master", "phd"]},
            },
            "expected_output": [
                "New column 'education_ord' with integers 0–3",
                "Source column dropped",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        orderings: dict = kwargs.get("orderings") or {}
        unknown_value: int = int(kwargs.get("unknown_value", -1))
        suffix: str = kwargs.get("suffix", "_ord")
        drop_original: bool = bool(kwargs.get("drop_original", True))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            ordering = orderings.get(col) or sorted(
                result[col].dropna().astype(str).unique().tolist()
            )
            mapping = {v: i for i, v in enumerate(ordering)}
            new_col = col + suffix
            result[new_col] = result[col].map(mapping).fillna(unknown_value).astype(int)
            new_columns.append(new_col)
            per_column[col] = {"mapping": mapping, "new_column": new_col, "ordering": ordering}

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class TargetEncodeTransformStrategy(DataTransformStrategy):
    """
    Replaces each category value with the mean of the target column within that
    category (target / mean encoding), with optional smoothing to shrink small
    groups towards the global mean and reduce overfitting.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Categorical columns to target-encode.",
            possible_values="list of column names",
            value_descriptions={"[<col>, ...]": "Each column is encoded independently."},
        ),
        ArgumentSpec(
            name="target_column",
            type="str",
            required=True,
            default=None,
            description="Numeric target column whose mean is used as the encoding.",
            possible_values="any numeric column name",
            value_descriptions={"<col>": "e.g. 'conversion', 'price', 'churn'."},
        ),
        ArgumentSpec(
            name="smoothing",
            type="float",
            required=False,
            default=10.0,
            description=(
                "Smoothing factor (k). Higher values shrink small-group estimates "
                "more strongly toward the global mean. 0 = no smoothing."
            ),
            possible_values="float ≥ 0 — typical range 1–50",
            value_descriptions={
                "0": "No smoothing — pure group mean.",
                "10": "Moderate smoothing (default).",
                "50": "Aggressive shrinkage for very small groups.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_target_enc",
            description="Suffix appended to the source column name.",
            possible_values="any string",
            value_descriptions={"_target_enc": "Default."},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=True,
            description="If true (default), remove the source column after encoding.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 9,
            "name": "Target encode city using churn rate",
            "transform": cls.__name__,
            "objective": "Replace city with smoothed per-city churn rate.",
            "actions": [
                "Compute per-city mean of 'churn'.",
                "Apply smoothing (k=10) to shrink small-group estimates.",
            ],
            "arguments": {
                "columns": ["city"],
                "target_column": "churn",
                "smoothing": 10.0,
            },
            "expected_output": [
                "New column 'city_target_enc' with smoothed means",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        target_column: str = kwargs.get("target_column") or ""
        smoothing: float = float(kwargs.get("smoothing", 10.0))
        suffix: str = kwargs.get("suffix", "_target_enc")
        drop_original: bool = bool(kwargs.get("drop_original", True))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")
        if not target_column:
            raise ValueError("'target_column' is required.")
        if target_column not in data.columns:
            raise ValueError(f"Target column not found: {target_column}")

        result = data.copy()
        global_mean = float(pd.to_numeric(result[target_column], errors="coerce").mean())
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            target_series = pd.to_numeric(result[target_column], errors="coerce")
            stats = result.groupby(col)[target_column].agg(
                group_mean=lambda x: pd.to_numeric(x, errors="coerce").mean(),
                group_count="count",
            )
            # Smoothed encoding: λ * group_mean + (1 - λ) * global_mean
            # λ = n / (n + k)
            lam = stats["group_count"] / (stats["group_count"] + smoothing)
            stats["smoothed"] = lam * stats["group_mean"] + (1 - lam) * global_mean

            new_col = col + suffix
            result[new_col] = result[col].map(stats["smoothed"]).fillna(global_mean)
            new_columns.append(new_col)
            per_column[col] = {
                "new_column": new_col,
                "global_mean": global_mean,
                "group_stats": stats[["group_mean", "group_count", "smoothed"]].to_dict(),
            }

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "target_column": target_column,
            "global_mean": global_mean,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class FrequencyEncodeTransformStrategy(DataTransformStrategy):
    """
    Replaces each category value with its relative frequency (proportion)
    in the dataset — a lightweight, cardinality-safe alternative to one-hot
    encoding for high-cardinality categoricals.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Categorical columns to frequency-encode.",
            possible_values="list of column names",
            value_descriptions={"[<col>, ...]": "Each column is encoded independently."},
        ),
        ArgumentSpec(
            name="normalise",
            type="bool",
            required=False,
            default=True,
            description="If true (default), encode as relative frequency; if false, encode as raw count.",
            possible_values="true | false",
            value_descriptions={
                "true": "Values in (0, 1] — proportions.",
                "false": "Values are raw occurrence counts.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_freq",
            description="Suffix appended to the source column name.",
            possible_values="any string",
            value_descriptions={"_freq": "Default."},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=True,
            description="If true (default), remove the source column after encoding.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 10,
            "name": "Frequency encode product_category",
            "transform": cls.__name__,
            "objective": "Replace product_category with its proportion in the dataset.",
            "actions": ["Map each category to its relative frequency."],
            "arguments": {"columns": ["product_category"], "normalise": True},
            "expected_output": ["New column 'product_category_freq' with float proportions"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        normalise: bool = bool(kwargs.get("normalise", True))
        suffix: str = kwargs.get("suffix", "_freq")
        drop_original: bool = bool(kwargs.get("drop_original", True))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            freq_map = result[col].value_counts(normalize=normalise).to_dict()
            new_col = col + suffix
            result[new_col] = result[col].map(freq_map)
            new_columns.append(new_col)
            per_column[col] = {"new_column": new_col, "frequency_map": freq_map}

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class BinaryEncodeTransformStrategy(DataTransformStrategy):
    """
    Encodes each categorical column into binary digits: ordinal integer codes
    are converted to their binary representation, producing ceil(log2(k)) new
    bit columns per source column — more compact than one-hot for high-cardinality
    features while preserving more information than ordinal encoding.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Categorical columns to binary-encode.",
            possible_values="list of column names",
            value_descriptions={"[<col>, ...]": "Each column is encoded independently."},
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_bin",
            description="Suffix prepended to the bit index to form new column names.",
            possible_values="any string",
            value_descriptions={"_bin": "Default — e.g. 'city_bin0', 'city_bin1', …"},
        ),
        ArgumentSpec(
            name="drop_original",
            type="bool",
            required=False,
            default=True,
            description="If true (default), remove the source column after encoding.",
            possible_values="true | false",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 11,
            "name": "Binary encode country (200 categories)",
            "transform": cls.__name__,
            "objective": "Encode high-cardinality country with 8 bit columns instead of 200 dummies.",
            "actions": ["Map country to integer codes, expand to 8 binary bit columns."],
            "arguments": {"columns": ["country"]},
            "expected_output": ["8 new integer (0/1) columns: country_bin0 … country_bin7"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        suffix: str = kwargs.get("suffix", "_bin")
        drop_original: bool = bool(kwargs.get("drop_original", True))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        dropped_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            categories = result[col].astype("category").cat
            codes = categories.codes  # -1 for NaN
            n_bits = max(int(np.ceil(np.log2(len(categories.categories) + 1))), 1)

            bit_cols: list[str] = []
            for bit in range(n_bits):
                new_col = f"{col}{suffix}{bit}"
                result[new_col] = ((codes >> bit) & 1).astype(int)
                new_columns.append(new_col)
                bit_cols.append(new_col)

            per_column[col] = {
                "n_categories": len(categories.categories),
                "n_bits": n_bits,
                "bit_columns": bit_cols,
            }

            if drop_original:
                result.drop(columns=[col], inplace=True)
                dropped_columns.append(col)

        return result, {
            "new_columns": new_columns,
            "dropped_columns": dropped_columns,
            "rows_affected": 0,
            "per_column": per_column,
        }


# ══════════════════════════════════════════════════════════════════════════════
# ── NORMALISATION ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class StandardScaleTransformStrategy(DataTransformStrategy):
    """
    Applies Z-score standardisation to numeric columns: (x - mean) / std,
    producing zero-mean unit-variance features. Fit statistics are included
    in the report for later inverse-transform or inference-time re-use.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to standardise.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is scaled independently."},
        ),
        ArgumentSpec(
            name="with_std",
            type="bool",
            required=False,
            default=True,
            description="If false, centre only (subtract mean, do not divide by std).",
            possible_values="true | false",
            value_descriptions={
                "true": "Full Z-score: (x - μ) / σ.",
                "false": "Mean-centre only: x - μ.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_scaled",
            description="Suffix appended to each column name. Empty string to scale in place.",
            possible_values="any string, or '' to overwrite the source column",
            value_descriptions={
                "_scaled": "Default — preserves the original column alongside.",
                "''": "In-place scaling.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 12,
            "name": "Z-score scale age and income",
            "transform": cls.__name__,
            "objective": "Standardise numeric features for a distance-sensitive model.",
            "actions": ["Apply (x - mean) / std to 'age' and 'income'."],
            "arguments": {"columns": ["age", "income"], "suffix": "_scaled"},
            "expected_output": [
                "New columns 'age_scaled', 'income_scaled'",
                "Per-column mean and std in report for inference-time re-use",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        with_std: bool = bool(kwargs.get("with_std", True))
        suffix: str = kwargs.get("suffix", "_scaled")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(result[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            s = result[col].astype(float)
            mean = float(s.mean())
            std = float(s.std())
            scaled = (s - mean) / std if (with_std and std != 0) else s - mean
            new_col = col + suffix if suffix else col
            result[new_col] = scaled
            if suffix:
                new_columns.append(new_col)
            per_column[col] = {"mean": mean, "std": std, "new_column": new_col}

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class MinMaxScaleTransformStrategy(DataTransformStrategy):
    """
    Scales numeric columns to a [feature_min, feature_max] range (default [0, 1])
    using min-max normalisation: (x - min) / (max - min) * (max_val - min_val) + min_val.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to scale.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is scaled independently."},
        ),
        ArgumentSpec(
            name="feature_min",
            type="float",
            required=False,
            default=0.0,
            description="Desired minimum of the scaled range.",
            possible_values="any float less than feature_max",
            value_descriptions={"0.0": "Default lower bound.", "-1.0": "Use with feature_max=1.0 for [-1, 1]."},
        ),
        ArgumentSpec(
            name="feature_max",
            type="float",
            required=False,
            default=1.0,
            description="Desired maximum of the scaled range.",
            possible_values="any float greater than feature_min",
            value_descriptions={"1.0": "Default upper bound."},
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_minmax",
            description="Suffix appended to each column name. Empty string to scale in place.",
            possible_values="any string",
            value_descriptions={"_minmax": "Default."},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 13,
            "name": "Min-max scale pixel values to [-1, 1]",
            "transform": cls.__name__,
            "objective": "Normalise features to the range required by a neural network.",
            "actions": ["Scale 'pixel_value' to [-1, 1]."],
            "arguments": {
                "columns": ["pixel_value"],
                "feature_min": -1.0,
                "feature_max": 1.0,
            },
            "expected_output": ["New column 'pixel_value_minmax' in range [-1, 1]"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        feature_min: float = float(kwargs.get("feature_min", 0.0))
        feature_max: float = float(kwargs.get("feature_max", 1.0))
        suffix: str = kwargs.get("suffix", "_minmax")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")
        if feature_min >= feature_max:
            raise ValueError("'feature_min' must be less than 'feature_max'.")

        result = data.copy()
        new_columns: list[str] = []
        per_column: dict = {}
        scale_range = feature_max - feature_min

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(result[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            s = result[col].astype(float)
            mn, mx = float(s.min()), float(s.max())
            data_range = mx - mn
            scaled = (s - mn) / data_range * scale_range + feature_min if data_range else s * 0 + feature_min
            new_col = col + suffix if suffix else col
            result[new_col] = scaled
            if suffix:
                new_columns.append(new_col)
            per_column[col] = {"data_min": mn, "data_max": mx, "new_column": new_col}

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "feature_range": [feature_min, feature_max],
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class RobustScaleTransformStrategy(DataTransformStrategy):
    """
    Scales numeric columns using statistics robust to outliers: centres on the
    median and scales by the interquartile range (IQR), making the transformation
    insensitive to extreme values — (x - median) / IQR.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to robust-scale.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is scaled independently."},
        ),
        ArgumentSpec(
            name="quantile_range",
            type="list[float]",
            required=False,
            default=[25.0, 75.0],
            description="Percentile range used to compute IQR.",
            possible_values="list of two floats [lower_q, upper_q] in (0, 100)",
            value_descriptions={
                "[25.0, 75.0]": "Standard IQR (default).",
                "[10.0, 90.0]": "Wider range, less aggressive scaling.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_robust",
            description="Suffix appended to each column name.",
            possible_values="any string",
            value_descriptions={"_robust": "Default."},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 14,
            "name": "Robust scale income (outlier-heavy column)",
            "transform": cls.__name__,
            "objective": "Scale income without being distorted by extreme earners.",
            "actions": ["Centre on median, scale by IQR."],
            "arguments": {"columns": ["income"], "quantile_range": [25.0, 75.0]},
            "expected_output": ["New column 'income_robust'"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        quantile_range: list[float] = kwargs.get("quantile_range") or [25.0, 75.0]
        suffix: str = kwargs.get("suffix", "_robust")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        q_lo, q_hi = quantile_range[0] / 100.0, quantile_range[1] / 100.0
        result = data.copy()
        new_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(result[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            s = result[col].astype(float)
            median = float(s.median())
            q1, q3 = float(s.quantile(q_lo)), float(s.quantile(q_hi))
            iqr = q3 - q1
            scaled = (s - median) / iqr if iqr else s - median
            new_col = col + suffix if suffix else col
            result[new_col] = scaled
            if suffix:
                new_columns.append(new_col)
            per_column[col] = {"median": median, "q1": q1, "q3": q3, "iqr": iqr, "new_column": new_col}

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class LogTransformTransformStrategy(DataTransformStrategy):
    """
    Applies a logarithmic transformation to numeric columns to reduce right
    skewness, with configurable base (natural, log2, log10) and an optional
    constant shift to handle zero or negative values.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to log-transform.",
            possible_values="list of numeric column names with non-negative values",
            value_descriptions={"[<col>, ...]": "Each column is transformed independently."},
        ),
        ArgumentSpec(
            name="base",
            type="str",
            required=False,
            default="natural",
            description="Logarithm base.",
            possible_values="natural | log2 | log10",
            value_descriptions={
                "natural": "ln(x + shift). Common default.",
                "log2": "log₂(x + shift).",
                "log10": "log₁₀(x + shift). Interpretable as order-of-magnitude.",
            },
        ),
        ArgumentSpec(
            name="shift",
            type="float",
            required=False,
            default=1.0,
            description=(
                "Constant added to each value before taking the log: log(x + shift). "
                "Use shift=1.0 (default) for log1p-style transformation when zeros are present."
            ),
            possible_values="any float ≥ 0",
            value_descriptions={
                "0.0": "No shift — valid only when all values are strictly positive.",
                "1.0": "log1p-style — safe when values are ≥ 0.",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_log",
            description="Suffix appended to each column name.",
            possible_values="any string",
            value_descriptions={"_log": "Default."},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 15,
            "name": "Log-transform income",
            "transform": cls.__name__,
            "objective": "Reduce right skew in income before linear regression.",
            "actions": ["Apply ln(income + 1)."],
            "arguments": {"columns": ["income"], "base": "natural", "shift": 1.0},
            "expected_output": ["New column 'income_log' with reduced skewness"],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        columns: list[str] = kwargs.get("columns") or []
        base: str = kwargs.get("base", "natural")
        shift: float = float(kwargs.get("shift", 1.0))
        suffix: str = kwargs.get("suffix", "_log")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        log_fn = {"natural": np.log, "log2": np.log2, "log10": np.log10}.get(base)
        if log_fn is None:
            raise ValueError(f"Unknown base: '{base}'. Use 'natural', 'log2', or 'log10'.")

        result = data.copy()
        new_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(result[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            s = result[col].astype(float) + shift
            skew_before = float(result[col].skew())
            transformed = log_fn(s.clip(lower=1e-12))
            new_col = col + suffix if suffix else col
            result[new_col] = transformed
            skew_after = float(transformed.skew())
            if suffix:
                new_columns.append(new_col)
            per_column[col] = {
                "base": base,
                "shift": shift,
                "skewness_before": round(skew_before, 4),
                "skewness_after": round(skew_after, 4),
                "new_column": new_col,
            }

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
class PowerTransformTransformStrategy(DataTransformStrategy):
    """
    Applies a Box-Cox (λ) or Yeo-Johnson power transformation to numeric columns
    to stabilise variance and approximate normality. Yeo-Johnson supports zero
    and negative values; Box-Cox requires strictly positive values.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to power-transform.",
            possible_values="list of numeric column names",
            value_descriptions={
                "[<col>, ...]": (
                    "Box-Cox requires all values > 0. "
                    "Yeo-Johnson handles zeros and negatives."
                )
            },
        ),
        ArgumentSpec(
            name="method",
            type="str",
            required=False,
            default="yeo-johnson",
            description="Power transform method.",
            possible_values="yeo-johnson | box-cox",
            value_descriptions={
                "yeo-johnson": "Works on any real-valued data. Default.",
                "box-cox": "Requires strictly positive values; generally sharper on positive data.",
            },
        ),
        ArgumentSpec(
            name="lambda_value",
            type="float",
            required=False,
            default=None,
            description=(
                "Fixed λ exponent. If None (default), λ is estimated to maximise normality "
                "by minimising skewness. Common values: 0 = log, 0.5 = square root, 1 = identity."
            ),
            possible_values="any float, or omit for automatic estimation",
            value_descriptions={
                "0": "Equivalent to log transform (Box-Cox).",
                "0.5": "Square-root transform.",
                "1": "Identity (no change).",
                "None": "Auto-estimate λ (default).",
            },
        ),
        ArgumentSpec(
            name="suffix",
            type="str",
            required=False,
            default="_power",
            description="Suffix appended to each column name.",
            possible_values="any string",
            value_descriptions={"_power": "Default."},
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 16,
            "name": "Yeo-Johnson transform income",
            "transform": cls.__name__,
            "objective": "Stabilise variance and reduce skew in income for a GLM.",
            "actions": ["Estimate optimal λ and apply Yeo-Johnson transform to 'income'."],
            "arguments": {"columns": ["income"], "method": "yeo-johnson"},
            "expected_output": [
                "New column 'income_power'",
                "Estimated λ and skewness before/after in report",
            ],
        }

    def transform(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        from scipy.stats import yeojohnson, boxcox

        columns: list[str] = kwargs.get("columns") or []
        method: str = kwargs.get("method", "yeo-johnson")
        lambda_value = kwargs.get("lambda_value")
        suffix: str = kwargs.get("suffix", "_power")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        result = data.copy()
        new_columns: list[str] = []
        per_column: dict = {}

        for col in columns:
            if col not in result.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(result[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            s = result[col].astype(float)
            skew_before = float(s.skew())
            non_null = s.dropna().values

            try:
                if method == "yeo-johnson":
                    if lambda_value is not None:
                        transformed_vals, lam = yeojohnson(non_null, lmbda=float(lambda_value))
                    else:
                        transformed_vals, lam = yeojohnson(non_null)
                else:  # box-cox
                    if (non_null <= 0).any():
                        per_column[col] = {"error": "box-cox requires strictly positive values"}
                        continue
                    if lambda_value is not None:
                        transformed_vals = boxcox(non_null, lmbda=float(lambda_value))
                        lam = float(lambda_value)
                    else:
                        transformed_vals, lam = boxcox(non_null)

                transformed_series = s.copy()
                transformed_series[s.notna()] = transformed_vals
                new_col = col + suffix if suffix else col
                result[new_col] = transformed_series
                skew_after = float(transformed_series.dropna().skew())
                if suffix:
                    new_columns.append(new_col)
                per_column[col] = {
                    "method": method,
                    "lambda": round(float(lam), 6),
                    "skewness_before": round(skew_before, 4),
                    "skewness_after": round(skew_after, 4),
                    "new_column": new_col,
                }
            except Exception as exc:
                per_column[col] = {"error": str(exc)}

        return result, {
            "new_columns": new_columns,
            "dropped_columns": [],
            "rows_affected": 0,
            "per_column": per_column,
        }


# ══════════════════════════════════════════════════════════════════════════════
# ── Orchestrator ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TabularDataTransformer:
    """
    Applies a sequence of ``DataTransformStrategy`` instances to a DataFrame,
    accumulating reports from each step. API mirrors ``TabularDataCleaner``.
    """

    _REGISTRY: dict[str, type[DataTransformStrategy]] = {
        # Feature engineering
        "bin_numeric":            BinNumericTransformStrategy,
        "datetime_parts":         DatetimePartsTransformStrategy,
        "interaction_terms":      InteractionTermsTransformStrategy,
        "polynomial_features":    PolynomialFeaturesTransformStrategy,
        "aggregation_features":   AggregationFeaturesTransformStrategy,
        "ratio_features":         RatioFeaturesTransformStrategy,
        # Encoding
        "one_hot_encode":         OneHotEncodeTransformStrategy,
        "ordinal_encode":         OrdinalEncodeTransformStrategy,
        "target_encode":          TargetEncodeTransformStrategy,
        "frequency_encode":       FrequencyEncodeTransformStrategy,
        "binary_encode":          BinaryEncodeTransformStrategy,
        # Normalisation
        "standard_scale":         StandardScaleTransformStrategy,
        "minmax_scale":           MinMaxScaleTransformStrategy,
        "robust_scale":           RobustScaleTransformStrategy,
        "log_transform":          LogTransformTransformStrategy,
        "power_transform":        PowerTransformTransformStrategy,
    }

    def __init__(self, strategy: Union[DataTransformStrategy, str]) -> None:
        self.strategy = self._resolve(strategy)

    @classmethod
    def _resolve(cls, strategy: Union[DataTransformStrategy, str]) -> DataTransformStrategy:
        if isinstance(strategy, str):
            if strategy not in cls._REGISTRY:
                raise ValueError(
                    f"Unknown strategy: '{strategy}'. "
                    f"Available: {list(cls._REGISTRY.keys())}"
                )
            return cls._REGISTRY[strategy]()
        if isinstance(strategy, DataTransformStrategy):
            return strategy
        raise ValueError(
            "strategy must be a string identifier or a DataTransformStrategy instance."
        )

    def set_strategy(self, strategy: Union[DataTransformStrategy, str]) -> None:
        """Replace the current strategy."""
        self.strategy = self._resolve(strategy)

    def execute_strategy(
        self, data: pd.DataFrame, **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        Run the current strategy and return ``(transformed_df, report)``.

        :param data: DataFrame to transform.
        :param kwargs: Forwarded to the strategy's ``transform`` method.
        :return: Tuple of transformed DataFrame and report dict.
        """
        return self.strategy.transform(data, **kwargs)

    def run_pipeline(
        self,
        data: pd.DataFrame,
        steps: list[dict],
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Execute a sequence of transformation steps, threading the DataFrame
        through each one and accumulating step-level reports.

        Each step dict must have:
        - ``transform`` (str or DataTransformStrategy): strategy identifier.
        - ``arguments`` (dict): kwargs forwarded to the strategy.
        - ``name`` (str, optional): human-readable step label.

        :param data: Input DataFrame.
        :param steps: Ordered list of step dicts.
        :return: (final_transformed_df, list_of_reports)
        """
        current = data.copy()
        reports: list[dict] = []

        for i, step in enumerate(steps):
            strategy_id = step.get("transform")
            arguments: dict = step.get("arguments") or {}
            label: str = step.get("name", f"step_{i + 1}")

            self.set_strategy(strategy_id)
            current, report = self.strategy.transform(current, **arguments)

            reports.append({
                "step": i + 1,
                "name": label,
                "transform": (
                    strategy_id if isinstance(strategy_id, str)
                    else type(strategy_id).__name__
                ),
                **report,
            })

        return current, reports