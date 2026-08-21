#!/usr/bin/python
"""
Tabular Data Inspector Module

A module for performing explanatory data analysis (EDA) on tabular datasets. This module provides a base class `TabularDataInspector` that can be extended to implement specific EDA techniques for analyzing and summarizing tabular data. The `inspect` method is an abstract method that must be implemented by any subclass, allowing for customized analysis based on the specific requirements of the dataset being analyzed.

"""
# ——————————————————————————————————————————————————————————————
# Imports
from abc import ABC, abstractmethod
from typing import Any, Union
import pandas as pd

# Schema
from squirrel.schemas.argument_spec import ArgumentSpec

# ——— Data Inspector Base Class —————————————————————————————————————————
class DataInspectStrategy(ABC):
    """
    Abstract base class for tabular data inspection strategies.
    Subclasses declare ``argument_specs`` to expose their accepted arguments
    to the catalog, prompt generator, and runtime validator.
    """

    argument_specs: list[ArgumentSpec] = []   # override in subclasses

    @abstractmethod
    def inspect(self, data: pd.DataFrame, **kwargs) -> Any:
        pass

    @classmethod
    def to_catalog_entry(cls) -> dict:
        """
        Build a catalog entry dict from the class itself.
        Called by ``build_inspection_catalog()`` — no manual catalog needed.
        """
        first_doc_line = (cls.__doc__ or "").strip().splitlines()[0]

        return {
            "name": cls.__name__,
            "description": first_doc_line,
            "json_schema": cls.to_json_schema(),
            "arguments_schema": [
                {
                    "name": spec.name,
                    "type": spec.type,
                    "required": spec.required,
                    "default": spec.default,
                    "possible_values": spec.possible_values,
                    "value_descriptions": spec.value_descriptions,
                    "description": spec.description,
                    **({"condition": spec.condition} if spec.condition else {}),
                }
                for spec in cls.argument_specs
            ],
            "arguments_description": cls._arguments_description(),
            "example": cls._example(),
        }

    @classmethod
    def to_json_schema(cls) -> dict:
        """
        Return a JSON Schema object describing this inspection's arguments.

        The schema is intended for LLM tool prompting and runtime validation.
        """
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []

        for spec in cls.argument_specs:
            properties[spec.name] = cls._argument_spec_to_json_schema(spec)
            if spec.required:
                required.append(spec.name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _argument_spec_to_json_schema(spec: ArgumentSpec) -> dict[str, Any]:
        """Convert one ``ArgumentSpec`` into a JSON Schema property."""
        type_name = spec.type.strip().lower()

        if type_name in {"str", "string"}:
            schema: dict[str, Any] = {"type": "string"}
        elif type_name in {"int", "integer"}:
            schema = {"type": "integer"}
        elif type_name in {"float", "number", "double"}:
            schema = {"type": "number"}
        elif type_name in {"bool", "boolean"}:
            schema = {"type": "boolean"}
        elif type_name in {"dict", "object", "mapping"}:
            schema = {"type": "object"}
        elif type_name in {"list", "array"}:
            schema = {"type": "array"}
        elif type_name.startswith("list[") and type_name.endswith("]"):
            item_type = type_name[5:-1].strip()
            item_schema = DataInspectStrategy._argument_spec_to_json_schema(
                ArgumentSpec(
                    name=spec.name,
                    type=item_type,
                    required=spec.required,
                    default=spec.default,
                    description=spec.description,
                    possible_values=spec.possible_values,
                    value_descriptions=spec.value_descriptions,
                    condition=spec.condition,
                )
            )
            schema = {"type": "array", "items": item_schema}
        else:
            schema = {"type": "string"}

        if spec.description:
            schema["description"] = spec.description
        if spec.default is not None:
            schema["default"] = spec.default

        return schema

    @classmethod
    def _arguments_description(cls) -> str:
        """Override in subclasses to provide usage guidance beyond the schema."""
        if not cls.argument_specs:
            return "No arguments required."
        required = [s.name for s in cls.argument_specs if s.required]
        optional = [s.name for s in cls.argument_specs if not s.required]
        parts = []
        if required:
            parts.append(f"Required: {', '.join(required)}.")
        if optional:
            parts.append(f"Optional: {', '.join(optional)}.")
        return " ".join(parts)

    @classmethod
    def _example(cls) -> dict:
        """Override in subclasses to provide a complete example plan step."""
        return {
            "step": 1,
            "name": cls.__name__,
            "inspection": cls.__name__,
            "objective": "(override _example() to provide a concrete objective)",
            "actions": [],
            "arguments": {
                spec.name: spec.default
                for spec in cls.argument_specs
                if spec.required
            },
            "expected_output": [],
        }

# ——————————————————————————————————————————————————————————————
# Data Type Inspector with Semantic Classification and Recommendations
class DataTypeInspectionStrategy(DataInspectStrategy):
    """
    A concrete implementation of DataInspectStrategy that inspects the data types
    of each column and enriches the result with semantic type classification,
    cardinality, and agent-facing recommendations.
    """

    # Cardinality threshold below which a numeric column is flagged as likely ordinal/categorical.
    _LOW_CARDINALITY_THRESHOLD = 10

    argument_specs: list[ArgumentSpec] = []   # no arguments

    @classmethod
    def _arguments_description(cls) -> str:
        return "No arguments required. Inspects all columns automatically."

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 1,
            "name": "Inspect column types",
            "inspection": cls.__name__,
            "objective": "Identify raw and semantic dtype of each column and flag columns needing casting or dropping.",
            "actions": [
                "Classify each column as numeric, categorical, boolean, datetime, or datetime_string.",
                "Flag low-cardinality numeric columns as likely categorical.",
                "Flag constant columns and likely identifiers for removal.",
            ],
            "arguments": {},
            "expected_output": [
                "Per-column semantic type",
                "List of columns requiring attention",
                "Recommended casting or dropping actions",
            ],
        }

    def inspect(self, data: pd.DataFrame, **kwargs) -> dict:
        """
        Inspect the data types of each column and return a structured, agent-ready summary.

        :param data: DataFrame to inspect.
        :type data: pd.DataFrame
        :return: Per-column type profile plus a dataset-level summary.
        :rtype: dict
        """
        columns: dict = {}
        for col in data.columns:
            series = data[col]
            raw_dtype = str(series.dtype)
            semantic_type = self._semantic_type(series, col)
            n_unique = int(series.nunique(dropna=True))
            n_missing = int(series.isna().sum())
            total = len(series)

            columns[col] = {
                "raw_dtype": raw_dtype,
                "semantic_type": semantic_type,
                "n_unique": n_unique,
                "n_missing": n_missing,
                "missing_ratio": round(n_missing / total, 4) if total else 0.0,
                "recommended_action": self._recommend(series, semantic_type, n_unique, n_missing, total),
            }

        numeric_cols = [c for c, v in columns.items() if v["semantic_type"] == "numeric"]
        categorical_cols = [c for c, v in columns.items() if v["semantic_type"] == "categorical"]
        datetime_cols = [c for c, v in columns.items() if v["semantic_type"] == "datetime"]
        flag_cols = [c for c, v in columns.items() if v["recommended_action"] != "none"]

        return {
            "columns": columns,
            "summary": {
                "total_columns": len(data.columns),
                "numeric_columns": numeric_cols,
                "categorical_columns": categorical_cols,
                "datetime_columns": datetime_cols,
                "columns_requiring_attention": flag_cols,
            },
        }

    def _semantic_type(self, series: pd.Series, name: str) -> str:
        """Classify a column into a semantic type beyond the raw pandas dtype."""
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        if pd.api.types.is_bool_dtype(series):
            return "boolean"
        if pd.api.types.is_numeric_dtype(series):
            return "numeric"
        # Attempt datetime parse on object columns.
        if series.dtype == object:
            sample = series.dropna().head(50)
            try:
                pd.to_datetime(sample, infer_datetime_format=True)
                return "datetime_string"  # parseable but not yet cast
            except (ValueError, TypeError):
                pass
        return "categorical"

    def _recommend(
        self,
        series: pd.Series,
        semantic_type: str,
        n_unique: int,
        n_missing: int,
        total: int,
    ) -> str:
        if semantic_type == "datetime_string":
            return "cast_to_datetime"
        if semantic_type == "numeric" and n_unique <= self._LOW_CARDINALITY_THRESHOLD:
            return "consider_casting_to_categorical"
        if semantic_type == "categorical" and n_unique == total and total > 1:
            return "likely_identifier_consider_dropping"
        if semantic_type == "categorical" and n_unique == 1:
            return "constant_column_consider_dropping"
        if n_missing / total > 0.5 if total else False:
            return "high_missingness_review_before_use"
        return "none"


# ——————————————————————————————————————————————————————————————
# Summary Statistics Inspector with Distribution Shape Indicators
class SummaryStatisticsInspectionStrategy(DataInspectStrategy):
    """
    A concrete implementation of DataInspectStrategy that computes per-column
    summary statistics as structured dicts, separated by column type and enriched
    with distribution shape indicators for agent consumption.
    """

    argument_specs: list[ArgumentSpec] = []

    @classmethod
    def _arguments_description(cls) -> str:
        return "No arguments required. Covers all numeric and categorical columns automatically."

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 2,
            "name": "Compute summary statistics",
            "inspection": cls.__name__,
            "objective": "Understand the distribution, central tendency, and spread of each column.",
            "actions": [
                "Compute mean, std, min, max, quartiles for numeric columns.",
                "Compute skewness and kurtosis and classify distribution shape.",
                "Compute mode, cardinality ratio, and top-5 values for categorical columns.",
            ],
            "arguments": {},
            "expected_output": [
                "Per-column descriptive statistics",
                "Distribution shape labels (e.g. highly_right_skewed_leptokurtic)",
                "High-cardinality categorical columns",
            ],
        }

    def inspect(self, data: pd.DataFrame, **kwargs) -> dict:
        """
        Compute summary statistics for each column, structured for agent use.

        :param data: DataFrame to inspect.
        :type data: pd.DataFrame
        :return: Structured per-column statistics split by column type.
        :rtype: dict
        """
        numeric_stats = self._numeric_summary(data)
        categorical_stats = self._categorical_summary(data)

        return {
            "numeric": numeric_stats,
            "categorical": categorical_stats,
            "summary": {
                "numeric_columns": list(numeric_stats.keys()),
                "categorical_columns": list(categorical_stats.keys()),
                "columns_with_skew": [
                    col for col, v in numeric_stats.items()
                    if abs(v.get("skewness", 0) or 0) > 1.0
                ],
                "columns_with_high_cardinality": [
                    col for col, v in categorical_stats.items()
                    if v.get("cardinality_ratio", 0) > 0.5
                ],
            },
        }

    def _numeric_summary(self, data: pd.DataFrame) -> dict:
        """Per-column descriptive stats plus skewness and kurtosis."""
        result = {}
        for col in data.select_dtypes(include="number").columns:
            series = data[col].dropna()
            if series.empty:
                result[col] = {"error": "all_missing"}
                continue
            result[col] = {
                "count": int(series.count()),
                "mean": float(series.mean()),
                "std": float(series.std()),
                "min": float(series.min()),
                "q25": float(series.quantile(0.25)),
                "median": float(series.median()),
                "q75": float(series.quantile(0.75)),
                "max": float(series.max()),
                "skewness": round(float(series.skew()), 4),
                "kurtosis": round(float(series.kurt()), 4),
                "n_missing": int(data[col].isna().sum()),
                "distribution_shape": self._shape_label(series.skew(), series.kurt()),
            }
        return result

    def _categorical_summary(self, data: pd.DataFrame) -> dict:
        """Per-column frequency profile for categorical and object columns."""
        result = {}
        for col in data.select_dtypes(include=["object", "category"]).columns:
            series = data[col]
            n_total = len(series)
            n_missing = int(series.isna().sum())
            value_counts = series.value_counts(dropna=True)
            n_unique = int(value_counts.shape[0])
            result[col] = {
                "count": n_total - n_missing,
                "n_unique": n_unique,
                "n_missing": n_missing,
                "missing_ratio": round(n_missing / n_total, 4) if n_total else 0.0,
                "cardinality_ratio": round(n_unique / n_total, 4) if n_total else 0.0,
                "top_values": value_counts.head(5).to_dict(),
                "mode": value_counts.index[0] if not value_counts.empty else None,
                "mode_frequency": int(value_counts.iloc[0]) if not value_counts.empty else 0,
                "mode_ratio": round(value_counts.iloc[0] / (n_total - n_missing), 4)
                if not value_counts.empty and (n_total - n_missing) > 0 else 0.0,
            }
        return result

    def _shape_label(self, skew: float, kurt: float) -> str:
        """Heuristic label describing the distribution shape."""
        if abs(skew) < 0.5:
            shape = "approximately_normal"
        elif skew > 1.0:
            shape = "highly_right_skewed"
        elif skew > 0.5:
            shape = "moderately_right_skewed"
        elif skew < -1.0:
            shape = "highly_left_skewed"
        else:
            shape = "moderately_left_skewed"

        if kurt > 3.0:
            shape += "_leptokurtic"
        elif kurt < -1.0:
            shape += "_platykurtic"

        return shape


# ——————————————————————————————————————————————————————————————
# Outlier Detection Inspector with Severity Classification and Recommendations
class DetectOutliersInspectionStrategy(DataInspectStrategy):
    """
    A concrete implementation of DataInspectStrategy that detects outliers using
    IQR (default) or Z-score, with per-column severity classification and
    agent-facing recommended actions.
    """

    _SEVERITY_THRESHOLDS = {
        "high": 10.0,    # >10 % outliers
        "medium": 5.0,   # >5 %
        "low": 0.0,      # anything else
    }

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="method",
            type="str",
            required=False,
            default="iqr",
            description="Outlier detection algorithm to apply across all numeric columns.",
            possible_values="iqr | zscore",
            value_descriptions={
                "iqr": (
                    "Interquartile range fence rule: flags values below Q1 - 1.5×IQR "
                    "or above Q3 + 1.5×IQR. Robust to non-normal distributions. "
                    "Preferred for skewed or heavy-tailed columns."
                ),
                "zscore": (
                    "Flags values whose Z-score exceeds zscore_threshold. "
                    "Assumes approximate normality. Use only when the column is "
                    "roughly normally distributed."
                ),
            },
        ),
        ArgumentSpec(
            name="zscore_threshold",
            type="float",
            required=False,
            default=3.0,
            description="Z-score cutoff. Ignored entirely when method is 'iqr'.",
            possible_values="any positive float — typical range 2.0–3.5",
            value_descriptions={
                "2.0": "Strict — flags ~5% of a normal distribution.",
                "2.5": "Moderate-strict — flags ~1.2% of a normal distribution.",
                "3.0": "Standard default — flags ~0.3% of a normal distribution.",
                "3.5": "Lenient — flags only extreme values (~0.05%).",
            },
            condition="only used when method == 'zscore'",
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Pass `method` to choose the detection algorithm. "
            "IQR is the safer default for real-world data with unknown distributions. "
            "Only pass `zscore_threshold` when method is 'zscore'."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 3,
            "name": "Detect numeric outliers",
            "inspection": cls.__name__,
            "objective": "Identify rows with extreme values that may distort model training.",
            "actions": [
                "Apply 1.5 × IQR fence to all numeric columns.",
                "Count and index outlier rows per column.",
                "Assign severity (low / medium / high) based on outlier ratio.",
            ],
            "arguments": {"method": "iqr"},
            "expected_output": [
                "Outlier count and ratio per numeric column",
                "Severity classification per column",
                "List of high-severity columns requiring treatment",
            ],
        }

    def inspect(self, data: pd.DataFrame, **kwargs) -> dict:
        """
        Detect outliers in all numeric columns.

        :param data: DataFrame to inspect.
        :type data: pd.DataFrame
        :param kwargs: Optional keys:
            - ``method`` (str): ``"iqr"`` (default) or ``"zscore"``.
            - ``zscore_threshold`` (float): cutoff for Z-score method, default ``3.0``.
        :return: Per-column outlier profile plus a dataset-level summary.
        :rtype: dict
        """
        method = kwargs.get("method", "iqr")
        zscore_threshold = float(kwargs.get("zscore_threshold", 3.0))

        columns: dict = {}
        numeric_cols = data.select_dtypes(include="number").columns

        for col in numeric_cols:
            series = data[col].dropna()
            if series.empty:
                columns[col] = {"error": "all_missing"}
                continue

            if method == "zscore":
                col_result = self._zscore_outliers(data, col, series, zscore_threshold)
            else:
                col_result = self._iqr_outliers(data, col, series)

            col_result["severity"] = self._severity(col_result["outlier_ratio"])
            col_result["recommended_action"] = self._recommend(
                col_result["severity"], col_result["outlier_ratio"]
            )
            columns[col] = col_result

        high_severity = [c for c, v in columns.items() if v.get("severity") == "high"]
        medium_severity = [c for c, v in columns.items() if v.get("severity") == "medium"]

        return {
            "method": method,
            "columns": columns,
            "summary": {
                "total_numeric_columns": len(numeric_cols),
                "columns_with_outliers": [
                    c for c, v in columns.items() if v.get("n_outliers", 0) > 0
                ],
                "high_severity_columns": high_severity,
                "medium_severity_columns": medium_severity,
            },
        }

    def _iqr_outliers(self, data: pd.DataFrame, col: str, series: pd.Series) -> dict:
        """Detect outliers using the 1.5 × IQR fence rule."""
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        outlier_mask = (data[col] < lower) | (data[col] > upper)
        outlier_indices = data.index[outlier_mask & data[col].notna()].tolist()
        n_outliers = len(outlier_indices)
        n_total = len(data)

        return {
            "n_outliers": n_outliers,
            "outlier_ratio": round(n_outliers / n_total, 4) if n_total else 0.0,
            "outlier_indices": outlier_indices,
            "lower_bound": round(lower, 6),
            "upper_bound": round(upper, 6),
            "q1": round(q1, 6),
            "q3": round(q3, 6),
            "iqr": round(iqr, 6),
        }

    def _zscore_outliers(
        self, data: pd.DataFrame, col: str, series: pd.Series, threshold: float
    ) -> dict:
        """Detect outliers using the Z-score method."""
        mean = float(series.mean())
        std = float(series.std(ddof=0))

        if std == 0.0:
            return {
                "n_outliers": 0,
                "outlier_ratio": 0.0,
                "outlier_indices": [],
                "mean": mean,
                "std": std,
                "zscore_threshold": threshold,
                "note": "zero_variance_column",
            }

        zscores = (data[col] - mean) / std
        outlier_mask = zscores.abs() > threshold
        outlier_indices = data.index[outlier_mask & data[col].notna()].tolist()
        n_outliers = len(outlier_indices)
        n_total = len(data)

        return {
            "n_outliers": n_outliers,
            "outlier_ratio": round(n_outliers / n_total, 4) if n_total else 0.0,
            "outlier_indices": outlier_indices,
            "mean": round(mean, 6),
            "std": round(std, 6),
            "zscore_threshold": threshold,
        }

    def _severity(self, outlier_ratio: float) -> str:
        pct = outlier_ratio * 100
        if pct > self._SEVERITY_THRESHOLDS["high"]:
            return "high"
        if pct > self._SEVERITY_THRESHOLDS["medium"]:
            return "medium"
        return "low"

    def _recommend(self, severity: str, outlier_ratio: float) -> str:
        if severity == "high":
            return "review_and_treat: high outlier density may distort model"
        if severity == "medium":
            return "investigate: moderate outliers present, consider capping or transformation"
        if outlier_ratio > 0:
            return "monitor: low outlier count, likely acceptable"
        return "none"
    
# —————————————————————————————————————————————————————————————
# Correlation and Missingness Dependency Inspector
class CorrelationInspectionStrategy(DataInspectStrategy):
    """
    A concrete implementation of DataInspectStrategy that measures the association
    between two columns, automatically selecting the appropriate method based on
    their dtypes:

    - numeric  × numeric  → Pearson r
    - ordinal  × ordinal  → Spearman ρ  (pass ordinal_columns=[...] to flag these)
    - numeric  × category → Eta squared  (η²)
    - category × category → Cramér's V
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="column_1",
            type="str",
            required=True,
            default=None,
            description="First column in the association pair.",
            possible_values="any column name present in the dataset",
            value_descriptions={
                "<numeric_column>": "Paired with numeric → Pearson. With ordinal → Spearman. With categorical → Eta squared.",
                "<categorical_column>": "Paired with categorical → Cramér's V. With numeric → Eta squared.",
            },
        ),
        ArgumentSpec(
            name="column_2",
            type="str",
            required=True,
            default=None,
            description="Second column in the association pair. Method is chosen automatically from the pair's dtypes.",
            possible_values="any column name present in the dataset, different from column_1",
            value_descriptions={
                "<numeric_column>": "See column_1 — method is determined by the pair.",
                "<categorical_column>": "See column_1 — method is determined by the pair.",
            },
        ),
        ArgumentSpec(
            name="threshold",
            type="float",
            required=False,
            default=0.3,
            description=(
                "Minimum association_strength to classify the pair as associated. "
                "Applies uniformly across Pearson, Spearman, Cramér's V, and Eta squared "
                "since all return a value in [0, 1]."
            ),
            possible_values="any float in (0.0, 1.0) — typical range 0.3–0.7",
            value_descriptions={
                "0.3": "Standard default — flags moderate and strong associations.",
                "0.5": "Moderate-strict — flags only medium-to-strong associations.",
                "0.7": "Strict collinearity screen — use when dropping features before modelling.",
            },
        ),
        ArgumentSpec(
            name="ordinal_columns",
            type="list[str]",
            required=False,
            default=[],
            description=(
                "Columns that are numeric in dtype but ordinal in meaning "
                "(e.g. survey ratings, education levels coded as integers). "
                "If either column appears here, Spearman ρ is used instead of Pearson r."
            ),
            possible_values="list of column names, or [] to use default dtype detection",
            value_descriptions={
                "[]": "Default — numeric columns treated as continuous, paired using Pearson r.",
                "[<column_name>, ...]": "Named columns treated as ordinal; triggers Spearman ρ.",
            },
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Always provide both column_1 and column_2. "
            "Do not specify the method — it is chosen automatically from dtypes. "
            "Produce one step per column pair. "
            "Use threshold=0.7 when the goal is collinearity screening before modelling."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 5,
            "name": "Correlate age and income",
            "inspection": cls.__name__,
            "objective": "Determine whether age and income are linearly associated.",
            "actions": [
                "Detect both columns are numeric and select Pearson r automatically.",
                "Drop rows with missing values in either column.",
                "Compare association_strength against threshold and set is_associated.",
            ],
            "arguments": {"column_1": "age", "column_2": "income", "threshold": 0.3},
            "expected_output": [
                "Association coefficient and strength (Pearson r)",
                "Direction: positive or negative",
                "is_associated flag",
                "recommended_action (e.g. flag_for_removal if strength >= 0.7)",
            ],
        }

    def inspect(self, data: pd.DataFrame, **kwargs) -> dict:
        """
        Measure the association between two columns and return a structured result
        suitable for downstream agent consumption.

        :param data: DataFrame containing the tabular data to inspect.
        :type data: pd.DataFrame
        :param kwargs: Keyword arguments:
            - ``column_1`` (str): first column name.
            - ``column_2`` (str): second column name.
            - ``threshold`` (float, optional): strength cutoff, default ``0.3``.
            - ``ordinal_columns`` (list[str], optional): columns to treat as ordinal
              (triggers Spearman instead of Pearson for numeric-looking columns).
        :return: Structured association result for agent consumption.
        :rtype: dict
        """
        first_column = kwargs.get("column_1")
        second_column = kwargs.get("column_2")
        threshold = float(kwargs.get("threshold", 0.3))
        ordinal_columns: list = kwargs.get("ordinal_columns") or []

        if not first_column or not second_column:
            raise ValueError(
                "Two column names must be provided using 'column_1' and 'column_2'."
            )
        for col in (first_column, second_column):
            if col not in data.columns:
                raise ValueError(f"Column not found: {col}")

        col1_type = self._resolve_dtype(data[first_column], first_column, ordinal_columns)
        col2_type = self._resolve_dtype(data[second_column], second_column, ordinal_columns)

        method, result_updates = self._compute_association(
            data, first_column, second_column, col1_type, col2_type, threshold
        )

        strength = result_updates["association_strength"]
        is_associated = strength >= threshold

        return {
            # ── Identity ──────────────────────────────────────────────────────
            "columns": [first_column, second_column],
            "column_types": {first_column: col1_type, second_column: col2_type},
            # ── Method ───────────────────────────────────────────────────────
            "method": method,
            "threshold": threshold,
            # ── Computed values ───────────────────────────────────────────────
            **result_updates,
            # ── Agent-facing fields ───────────────────────────────────────────
            "is_associated": is_associated,
            "confidence": self._confidence(result_updates["paired_observations"]),
            "recommended_action": self._recommend(
                is_associated, col1_type, col2_type, method, strength
            ),
        }

    # ── Dtype resolution ──────────────────────────────────────────────────────

    def _resolve_dtype(
        self, series: pd.Series, name: str, ordinal_columns: list
    ) -> str:
        """Return 'ordinal', 'numeric', or 'categorical' for a column."""
        if name in ordinal_columns:
            return "ordinal"
        if pd.api.types.is_numeric_dtype(series):
            return "numeric"
        return "categorical"

    # ── Method dispatch ───────────────────────────────────────────────────────

    def _compute_association(
        self,
        data: pd.DataFrame,
        col1: str,
        col2: str,
        type1: str,
        type2: str,
        threshold: float,
    ) -> tuple[str, dict]:
        """Route to the correct association method and return (method_name, updates)."""
        numeric_types = {"numeric", "ordinal"}

        both_numeric = type1 in numeric_types and type2 in numeric_types
        both_categorical = type1 == "categorical" and type2 == "categorical"
        mixed = not both_numeric and not both_categorical

        if both_numeric:
            use_spearman = type1 == "ordinal" or type2 == "ordinal"
            method = "spearman" if use_spearman else "pearson"
            return method, self._pearson_or_spearman(data, col1, col2, method)

        if both_categorical:
            return "cramers_v", self._cramers_v(data, col1, col2)

        # mixed: one numeric/ordinal, one categorical
        num_col, cat_col = (col1, col2) if type1 in numeric_types else (col2, col1)
        return "eta_squared", self._eta_squared(data, num_col, cat_col)

    # ── Association methods ───────────────────────────────────────────────────

    def _pearson_or_spearman(
        self, data: pd.DataFrame, col1: str, col2: str, method: str
    ) -> dict:
        """Pearson r or Spearman ρ for two numeric/ordinal columns."""
        s1 = pd.to_numeric(data[col1], errors="coerce")
        s2 = pd.to_numeric(data[col2], errors="coerce")
        paired = pd.concat([s1, s2], axis=1).dropna()

        if paired.shape[0] < 2:
            raise ValueError(
                "At least two overlapping numeric values are required."
            )

        correlation = (
            paired.iloc[:, 0].corr(paired.iloc[:, 1])
            if method == "pearson"
            else paired.iloc[:, 0].corr(paired.iloc[:, 1], method="spearman")
        )

        if pd.isna(correlation):
            raise ValueError("Correlation could not be calculated.")

        return {
            "association_coefficient": float(correlation),
            "association_strength": float(abs(correlation)),
            "direction": "positive" if correlation > 0 else "negative",
            "paired_observations": int(paired.shape[0]),
        }

    def _cramers_v(self, data: pd.DataFrame, col1: str, col2: str) -> dict:
        """
        Cramér's V for two categorical columns.
        Ranges from 0 (no association) to 1 (perfect association).
        """
        contingency = pd.crosstab(
            data[col1].astype("string"), data[col2].astype("string")
        )
        n = int(contingency.values.sum())

        if n < 2:
            raise ValueError(
                "At least two observations are required for Cramér's V."
            )

        chi2 = float(
            ((contingency - contingency.values.sum(axis=1, keepdims=True)
              * contingency.values.sum(axis=0, keepdims=True) / n) ** 2
             / (contingency.values.sum(axis=1, keepdims=True)
                * contingency.values.sum(axis=0, keepdims=True) / n)
             ).values.sum()
        )
        k = min(contingency.shape) - 1
        v = float((chi2 / (n * k)) ** 0.5) if k > 0 else 0.0

        return {
            "association_coefficient": v,
            "association_strength": v,
            "direction": "none",          # V is symmetric and unsigned
            "paired_observations": n,
            "chi2": chi2,
            "contingency_shape": list(contingency.shape),
        }

    def _eta_squared(
        self, data: pd.DataFrame, num_col: str, cat_col: str
    ) -> dict:
        """
        Eta squared (η²) for a numeric column grouped by a categorical one.
        Ranges from 0 (no effect) to 1 (all variance explained by the group).
        """
        paired = data[[num_col, cat_col]].dropna()
        paired = paired.copy()
        paired[num_col] = pd.to_numeric(paired[num_col], errors="coerce")
        paired = paired.dropna()

        if paired.shape[0] < 2:
            raise ValueError(
                "At least two complete observations are required for η²."
            )

        grand_mean = paired[num_col].mean()
        ss_total = float(((paired[num_col] - grand_mean) ** 2).sum())
        ss_between = float(
            paired.groupby(cat_col)[num_col]
            .apply(lambda g: len(g) * (g.mean() - grand_mean) ** 2)
            .sum()
        )
        eta_sq = (ss_between / ss_total) if ss_total > 0 else 0.0

        return {
            "association_coefficient": float(eta_sq),
            "association_strength": float(eta_sq),
            "direction": "none",          # η² is unsigned
            "paired_observations": int(paired.shape[0]),
            "ss_between": ss_between,
            "ss_total": ss_total,
            "numeric_column": num_col,
            "categorical_column": cat_col,
        }

    # ── Agent support helpers ─────────────────────────────────────────────────

    def _confidence(self, n: int) -> str:
        """Heuristic confidence tier based on sample size."""
        if n >= 100:
            return "high"
        if n >= 30:
            return "medium"
        return "low"

    def _recommend(
        self,
        is_associated: bool,
        type1: str,
        type2: str,
        method: str,
        strength: float,
    ) -> str:
        """
        Return a plain-language action string an agent can parse or display.
        Keeps logic out of the agent and in the strategy where it belongs.
        """
        if not is_associated:
            return "no_action_required: association below threshold"

        if method in ("pearson", "spearman"):
            if strength >= 0.7:
                return "flag_for_removal: high collinearity risk"
            return "investigate: moderate linear association detected"

        if method == "cramers_v":
            return "investigate: categorical dependency detected"

        if method == "eta_squared":
            return "investigate: numeric distribution differs across categories"

        return "investigate: association detected"

# —————————————————————————————————————————————————————————————
# Missing Values Inspector with Dependency Assessment
class MissingValuesInspectionStrategy(DataInspectStrategy):
    """
    A unified strategy that inspects missing values for any column type and,
    when a related column is provided, estimates whether the missingness
    depends on it (numeric or categorical related column both supported).

    Target column dtype is detected automatically:
    - Categorical target: result includes ``observed_value_counts``.
    - Numeric target: result includes ``target_column_type: "numeric"``.

    Dependency is assessed on the *related* column's dtype:
    - Numeric related: standardized mean gap between missing / observed groups.
    - Categorical related: max per-category missing-rate deviation from overall rate.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="column_1",
            type="str",
            required=True,
            default=None,
            description="Target column to inspect for missing values.",
            possible_values="any column name present in the dataset",
            value_descriptions={
                "<column_name>": (
                    "Works for both numeric and categorical columns. "
                    "Categorical columns additionally return an observed_value_counts "
                    "frequency distribution."
                ),
            },
        ),
        ArgumentSpec(
            name="column_2",
            type="str",
            required=False,
            default=None,
            description=(
                "Optional column to test whether missingness in column_1 depends on it. "
                "Choose a column you suspect explains why values are missing."
            ),
            possible_values="any column name present in the dataset, or omit entirely",
            value_descriptions={
                "<numeric_column>": (
                    "Tests dependency via standardized mean gap: compares the mean of this "
                    "column between rows where column_1 is missing vs present."
                ),
                "<categorical_column>": (
                    "Tests dependency via per-category missing rates: compares the missing "
                    "rate of column_1 within each category against the overall rate."
                ),
                "omitted": (
                    "Missingness is reported but not assessed. "
                    "Result will contain missingness_type: 'unassessed'."
                ),
            },
        ),
        ArgumentSpec(
            name="threshold",
            type="float",
            required=False,
            default=0.1,
            description=(
                "Sensitivity threshold for dependency classification. "
                "For numeric related columns: minimum standardized mean gap. "
                "For categorical related columns: minimum per-category rate deviation."
            ),
            possible_values="any float in (0.0, 1.0) — typical range 0.05–0.3",
            value_descriptions={
                "0.05": "Strict — flags even small deviations as dependent.",
                "0.1": "Standard default — balanced sensitivity for most datasets.",
                "0.2": "Lenient — only flags substantial dependency.",
                "0.3": "Very lenient — flags only strong, obvious dependency.",
            },
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Always provide column_1. Optionally provide column_2 to assess MAR vs MCAR. "
            "Produce one step per column you want to inspect."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 4,
            "name": "Assess income missingness vs age",
            "inspection": cls.__name__,
            "objective": "Determine whether missing income values are random or related to age.",
            "actions": [
                "Count and index missing values in income.",
                "Compare mean age between rows where income is missing vs present.",
                "Classify missingness as dependent or random_like using standardized gap.",
            ],
            "arguments": {"column_1": "income", "column_2": "age", "threshold": 0.1},
            "expected_output": [
                "Missing count and ratio for income",
                "Standardized mean gap between missing and observed age groups",
                "Missingness classification: dependent or random_like",
            ],
        }

    def inspect(self, data: pd.DataFrame, **kwargs) -> dict:
        """
        Inspect missing values for a target column and optionally assess
        whether missingness depends on a related column.

        :param data: DataFrame containing the data to inspect.
        :type data: pd.DataFrame
        :param kwargs: Keyword arguments:
            - ``column_1`` / ``column`` / ``target_column`` (str): target column name.
            - ``column_2`` / ``related_column`` / ``comparison_column`` (str, optional):
              column to test for missingness dependence.
            - ``threshold`` (float, optional): dependence sensitivity, default ``0.1``.
        :return: Summary dict with missing stats and, when a related column is
            supplied, a ``dependency`` and ``missingness_type`` classification.
        :rtype: dict
        """
        target_column = (
            kwargs.get("column_1")
            or kwargs.get("column")
            or kwargs.get("target_column")
        )
        related_column = (
            kwargs.get("column_2")
            or kwargs.get("related_column")
            or kwargs.get("comparison_column")
        )
        threshold = float(kwargs.get("threshold", 0.1))

        if not target_column:
            raise ValueError("A target column must be provided using 'column_1'.")
        if target_column not in data.columns:
            raise ValueError(f"Column not found: {target_column}")

        # ── Base stats ────────────────────────────────────────────────────────

        missing_mask = data[target_column].isna()
        missing_count = int(missing_mask.sum())
        total_count = int(data.shape[0])
        missing_ratio = (missing_count / total_count) if total_count else 0.0
        is_categorical = not pd.api.types.is_numeric_dtype(data[target_column])

        result: dict = {
            "column": target_column,
            "target_column_type": "categorical" if is_categorical else "numeric",
            "missing_count": missing_count,
            "total_count": total_count,
            "missing_ratio": missing_ratio,
            "has_missing_values": missing_count > 0,
            "missing_indices": data.index[missing_mask].tolist(),
        }

        # Categorical targets get a frequency distribution of observed values.
        if is_categorical:
            result["observed_value_counts"] = (
                data.loc[~missing_mask, target_column]
                .astype("string")
                .value_counts()
                .to_dict()
            )

        # ── Early exits ───────────────────────────────────────────────────────

        if missing_count == 0:
            result.update(
                {"missingness_type": "none", "dependency": None,
                 "related_column": related_column}
            )
            return result

        if not related_column:
            result.update(
                {"missingness_type": "unassessed",
                 "dependency": "no_related_column_provided"}
            )
            return result

        if related_column not in data.columns:
            raise ValueError(f"Column not found: {related_column}")

        # ── Dependency assessment ─────────────────────────────────────────────

        related_series = data[related_column]

        if pd.api.types.is_numeric_dtype(related_series):
            updates = self._assess_numeric_related(
                missing_mask, related_series, threshold
            )
        else:
            updates = self._assess_categorical_related(
                missing_mask, related_series, missing_ratio, threshold
            )

        result.update({"related_column": related_column, **updates})
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _assess_numeric_related(
        self,
        missing_mask: pd.Series,
        related: pd.Series,
        threshold: float,
    ) -> dict:
        """
        Compare the mean of *related* between the missing and observed groups.

        A standardized gap >= ``threshold`` indicates the missingness is likely
        dependent on the related column (MAR rather than MCAR).
        """
        observed_mask = ~missing_mask

        missing_vals = pd.to_numeric(related[missing_mask], errors="coerce").dropna()
        observed_vals = pd.to_numeric(related[observed_mask], errors="coerce").dropna()

        if missing_vals.empty or observed_vals.empty:
            return {
                "related_column_type": "numeric",
                "missingness_type": "indeterminate",
                "dependency": "insufficient_data_for_numeric_comparison",
            }

        missing_mean = float(missing_vals.mean())
        observed_mean = float(observed_vals.mean())
        pooled_std = float(related.dropna().std(ddof=0) or 0.0)
        mean_gap = abs(missing_mean - observed_mean)
        standardized_gap = (mean_gap / pooled_std) if pooled_std else 0.0
        dependent = standardized_gap >= threshold

        return {
            "related_column_type": "numeric",
            "missing_group_mean": missing_mean,
            "observed_group_mean": observed_mean,
            "mean_gap": mean_gap,
            "standardized_gap": standardized_gap,
            "dependency": "likely_dependent" if dependent else "no_strong_dependency_detected",
            "missingness_type": "dependent" if dependent else "random_like",
        }

    def _assess_categorical_related(
        self,
        missing_mask: pd.Series,
        related: pd.Series,
        overall_missing_rate: float,
        threshold: float,
    ) -> dict:
        """
        Compare per-category missing rates against the overall missing rate.

        If any category's missing rate deviates by >= ``threshold``, missingness
        is likely dependent on the related column.
        """
        category_counts = pd.crosstab(related.astype("string"), missing_mask)

        if category_counts.empty:
            return {
                "related_column_type": "categorical",
                "missingness_type": "indeterminate",
                "dependency": "insufficient_data_for_categorical_comparison",
            }

        category_missing_rates: dict = (
            category_counts.div(category_counts.sum(axis=1), axis=0)
            .get(True)
            .fillna(0.0)
            .to_dict()
            if True in category_counts.columns
            else {cat: 0.0 for cat in category_counts.index}
        )

        max_rate_gap = max(
            (abs(r - overall_missing_rate) for r in category_missing_rates.values()),
            default=0.0,
        )
        dependent = max_rate_gap >= threshold

        return {
            "related_column_type": "categorical",
            "category_missing_rates": category_missing_rates,
            "overall_missing_rate": overall_missing_rate,
            "max_rate_gap": max_rate_gap,
            "dependency": "likely_dependent" if dependent else "no_strong_dependency_detected",
            "missingness_type": "dependent" if dependent else "random_like",
        }

# ——————————————————————————————————————————————————————————————
# Tabular Data Analyzer
class TabularDataInpsector:
    """
    A class for analyzing tabular data using a specified data inspection strategy. This class allows for flexible analysis of tabular datasets by utilizing different strategies to inspect and summarize the data. 
    The `analyze` method can be implemented to perform specific analyses based on the chosen strategy, providing insights into the structure and characteristics of the dataset.
    """
    def __init__(
        self, 
        strategy: Union[DataInspectStrategy, str]
    ) -> str:
        """
        Initialize the TabularDataAnalyzer with a specific data inspection strategy.
        
        :param strategy: The data inspection strategy to be used, either as an instance of DataInspectStrategy or as a string identifier.
        :type strategy: Union[DataInspectStrategy, str]
        
        :raises ValueError: If the provided strategy is not recognized or is invalid.
        """
        if isinstance(strategy, str):
            if strategy == "data_type":
                self.strategy = DataTypeInspectionStrategy()
            elif strategy == "summary_statistics":
                self.strategy = SummaryStatisticsInspectionStrategy()
            elif strategy == "correlation":
                self.strategy = CorrelationInspectionStrategy()
            elif strategy == "missing_values":
                self.strategy = MissingValuesInspectionStrategy()
            elif strategy == "detect_outliers":
                self.strategy = DetectOutliersInspectionStrategy()
            else:
                raise ValueError(f"Unknown strategy: {strategy}")
        elif isinstance(strategy, DataInspectStrategy):
            self.strategy = strategy
        else:
            raise ValueError("Strategy must be either a string identifier or an instance of DataInspectStrategy.")

    def set_strategy(self, strategy: Union[DataInspectStrategy, str]):
        """
        Set a new data inspection strategy for the analyzer.
        
        :param strategy: The new data inspection strategy to be used, either as an instance of DataInspectStrategy or as a string identifier.
        :type strategy: Union[DataInspectStrategy, str]
        
        :raises ValueError: If the provided strategy is not recognized or is invalid.
        """
        if isinstance(strategy, str):
            if strategy == "data_type":
                self.strategy = DataTypeInspectionStrategy()
            elif strategy == "summary_statistics":
                self.strategy = SummaryStatisticsInspectionStrategy()
            elif strategy == "correlation":
                self.strategy = CorrelationInspectionStrategy()
            elif strategy == "missing_values":
                self.strategy = MissingValuesInspectionStrategy()
            elif strategy == "detect_outliers":
                self.strategy = DetectOutliersInspectionStrategy()
            else:
                raise ValueError(f"Unknown strategy: {strategy}")
        elif isinstance(strategy, DataInspectStrategy):
            self.strategy = strategy
        else:
            raise ValueError("Strategy must be either a string identifier or an instance of DataInspectStrategy.")

    def execute_strategy(self, data: pd.DataFrame, **kwargs) -> Any:
        """
        Execute the current data inspection strategy on the provided DataFrame.
        
        :param data: A pandas DataFrame containing the tabular data to be analyzed.
        :type data: pd.DataFrame
        :param kwargs: Additional keyword arguments forwarded to the strategy.
        :type kwargs: Any
        
        :return: The result of the data inspection based on the current strategy.
        :rtype: Any
        """
        return self.strategy.inspect(data, **kwargs)