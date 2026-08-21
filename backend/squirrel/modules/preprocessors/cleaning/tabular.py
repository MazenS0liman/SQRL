#!/usr/bin/python
"""
Tabular Data Cleaner Module
===========================

A module for performing data cleaning operations on tabular datasets. This module
provides a base class `DataCleanStrategy` that can be extended to implement specific
cleaning techniques. Each strategy exposes ``argument_specs`` so it can be catalogued,
prompted, and validated in the same way as the inspection strategies it is designed
to complement.

"""
# ——————————————————————————————————————————————————————————————
# Imports
from abc import ABC, abstractmethod
from typing import Any, Union
import pandas as pd
import numpy as np

# Schema
from squirrel.schemas.argument_spec import ArgumentSpec


# ——— Data Cleaner Base Class ———————————————————————————————————————————————
class DataCleanStrategy(ABC):
    """
    Abstract base class for tabular data cleaning strategies.
    Subclasses declare ``argument_specs`` to expose their accepted arguments
    to the catalog, prompt generator, and runtime validator.
    """

    argument_specs: list[ArgumentSpec] = []  # override in subclasses

    @abstractmethod
    def clean(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        Apply the cleaning operation to *data*.

        :param data: DataFrame to clean.
        :return: A tuple of (cleaned_dataframe, report_dict).  The report dict
            always contains at least ``rows_affected`` and ``columns_affected``.
        """

    @classmethod
    def to_catalog_entry(cls) -> dict:
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
        elif t.startswith("list[") and t.endswith("]"):
            item_type = t[5:-1].strip()
            s = {
                "type": "array",
                "items": DataCleanStrategy._spec_to_json_schema(
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
            "cleaning": cls.__name__,
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
# 1. Drop Duplicate Rows
class DropDuplicatesCleanStrategy(DataCleanStrategy):
    """
    Removes duplicate rows, optionally scoped to a subset of columns,
    and returns a report of how many rows were dropped and which indices were affected.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="subset",
            type="list[str]",
            required=False,
            default=[],
            description=(
                "Column names to consider when identifying duplicates. "
                "An empty list (default) considers all columns."
            ),
            possible_values="list of column names, or [] to use all columns",
            value_descriptions={
                "[]": "Default — all columns must match for a row to be considered duplicate.",
                "[<col>, ...]": "Only these columns are compared.",
            },
        ),
        ArgumentSpec(
            name="keep",
            type="str",
            required=False,
            default="first",
            description="Which occurrence of a duplicate to keep.",
            possible_values="first | last | false",
            value_descriptions={
                "first": "Keep the first occurrence; drop all subsequent duplicates.",
                "last": "Keep the last occurrence; drop all prior duplicates.",
                "false": "Drop ALL duplicate rows (no occurrence is kept).",
            },
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Provide subset to scope deduplication to specific columns. "
            "Use keep='false' to drop all copies of a duplicate group."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 1,
            "name": "Drop duplicate rows",
            "cleaning": cls.__name__,
            "objective": "Remove exact duplicate rows introduced by data pipeline errors.",
            "actions": [
                "Identify duplicate rows across all columns.",
                "Keep the first occurrence of each duplicate group.",
                "Report dropped indices.",
            ],
            "arguments": {"keep": "first"},
            "expected_output": [
                "Cleaned DataFrame with duplicates removed",
                "Count and indices of dropped rows",
            ],
        }

    def clean(
        self, 
        data: pd.DataFrame, 
        **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``subset`` (list[str]): columns to check; default all.
            - ``keep`` (str): ``'first'``, ``'last'``, or ``'false'``; default ``'first'``.
        :return: (cleaned_df, report)
        """
        subset = kwargs.get("subset") or None
        keep_arg = kwargs.get("keep", "first")
        keep: Union[str, bool] = False if keep_arg == "false" else keep_arg

        before = len(data)
        duplicate_mask = data.duplicated(subset=subset, keep=keep)
        dropped_indices = data.index[duplicate_mask].tolist()
        cleaned = data[~duplicate_mask].copy()
        after = len(cleaned)

        return cleaned, {
            "rows_before": before,
            "rows_after": after,
            "rows_affected": before - after,
            "columns_affected": [],
            "dropped_indices": dropped_indices,
            "subset_used": subset if subset else "all",
        }


# ——————————————————————————————————————————————————————————————
# 2. Drop Columns
class DropColumnsCleanStrategy(DataCleanStrategy):
    """
    Drops one or more columns from the DataFrame, with optional safety checks
    that prevent dropping columns with low missing ratios or high variance.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="List of column names to drop.",
            possible_values="list of column names present in the dataset",
            value_descriptions={
                "[<col>, ...]": "Every named column will be removed from the DataFrame.",
            },
        ),
        ArgumentSpec(
            name="errors",
            type="str",
            required=False,
            default="raise",
            description="How to handle missing column names.",
            possible_values="raise | ignore",
            value_descriptions={
                "raise": "Raise a KeyError if any named column does not exist.",
                "ignore": "Silently skip column names that are not found.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 2,
            "name": "Drop identifier and constant columns",
            "cleaning": cls.__name__,
            "objective": "Remove columns flagged as identifiers or constants by the type inspection.",
            "actions": ["Drop columns: ['id', 'record_hash']"],
            "arguments": {"columns": ["id", "record_hash"]},
            "expected_output": ["DataFrame with specified columns removed", "List of actually dropped columns"],
        }

    def clean(
        self, 
        data: pd.DataFrame, 
        **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``columns`` (list[str]): columns to drop (required).
            - ``errors`` (str): ``'raise'`` or ``'ignore'``; default ``'raise'``.
        :return: (cleaned_df, report)
        """
        columns: list[str] = kwargs.get("columns") or []
        errors: str = kwargs.get("errors", "raise")

        if not columns:
            raise ValueError("'columns' must be a non-empty list of column names.")

        existing = [c for c in columns if c in data.columns]
        missing = [c for c in columns if c not in data.columns]

        if missing and errors == "raise":
            raise KeyError(f"Columns not found in DataFrame: {missing}")

        cleaned = data.drop(columns=existing)

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": existing,
            "columns_not_found": missing,
            "columns_before": list(data.columns),
            "columns_after": list(cleaned.columns),
        }


# ——————————————————————————————————————————————————————————————
# 3. Impute Missing Values
class ImputeMissingValuesCleanStrategy(DataCleanStrategy):
    """
    Imputes missing values in one or more columns using a chosen strategy
    (mean, median, mode, constant, or forward/backward fill),
    selected per column or applied uniformly.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Columns to impute.",
            possible_values="list of column names present in the dataset",
            value_descriptions={"[<col>, ...]": "Each named column will be imputed."},
        ),
        ArgumentSpec(
            name="method",
            type="str",
            required=False,
            default="mean",
            description="Imputation method applied to all listed columns unless overridden by column_methods.",
            possible_values="mean | median | mode | constant | ffill | bfill",
            value_descriptions={
                "mean": "Replace with column mean (numeric only).",
                "median": "Replace with column median (numeric only).",
                "mode": "Replace with most frequent value (any dtype).",
                "constant": "Replace with the value supplied in fill_value.",
                "ffill": "Forward-fill from the preceding non-null value.",
                "bfill": "Backward-fill from the next non-null value.",
            },
        ),
        ArgumentSpec(
            name="fill_value",
            type="str",
            required=False,
            default=None,
            description="Value used when method is 'constant'. Cast to the column dtype at runtime.",
            possible_values="any scalar value",
            value_descriptions={"<scalar>": "Will be cast to match the column dtype."},
            condition="only used when method == 'constant'",
        ),
        ArgumentSpec(
            name="column_methods",
            type="dict",
            required=False,
            default={},
            description=(
                "Per-column method overrides, e.g. {'age': 'median', 'city': 'mode'}. "
                "Any column not listed here falls back to ``method``."
            ),
            possible_values="dict mapping column names to method strings",
            value_descriptions={},
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Always provide columns. Use method for a uniform strategy. "
            "Use column_methods to override individual columns. "
            "Provide fill_value only when method or a column override is 'constant'."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 3,
            "name": "Impute missing numeric and categorical values",
            "cleaning": cls.__name__,
            "objective": "Fill missing values in 'age' (median) and 'city' (mode) before modelling.",
            "actions": [
                "Impute 'age' with median.",
                "Impute 'city' with mode.",
                "Report fill values used and count of cells imputed.",
            ],
            "arguments": {
                "columns": ["age", "city"],
                "method": "median",
                "column_methods": {"city": "mode"},
            },
            "expected_output": [
                "DataFrame with no missing values in specified columns",
                "Per-column fill values and imputed cell counts",
            ],
        }

    def clean(
        self, 
        data: pd.DataFrame, 
        **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``columns`` (list[str]): columns to impute (required).
            - ``method`` (str): default imputation method; default ``'mean'``.
            - ``fill_value``: used when method is ``'constant'``.
            - ``column_methods`` (dict): per-column overrides.
        :return: (cleaned_df, report)
        """
        columns: list[str] = kwargs.get("columns") or []
        default_method: str = kwargs.get("method", "mean")
        fill_value = kwargs.get("fill_value")
        column_methods: dict = kwargs.get("column_methods") or {}

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        cleaned = data.copy()
        report_per_column: dict = {}
        total_cells_imputed = 0

        for col in columns:
            if col not in cleaned.columns:
                report_per_column[col] = {"error": "column_not_found"}
                continue

            method = column_methods.get(col, default_method)
            series = cleaned[col]
            missing_before = int(series.isna().sum())

            if missing_before == 0:
                report_per_column[col] = {"cells_imputed": 0, "fill_value": None, "method": method}
                continue

            computed_fill = self._compute_fill(series, method, fill_value)
            cleaned[col] = series.fillna(computed_fill) if method not in ("ffill", "bfill") else (
                series.ffill() if method == "ffill" else series.bfill()
            )
            cells_imputed = int(missing_before - cleaned[col].isna().sum())
            total_cells_imputed += cells_imputed
            report_per_column[col] = {
                "method": method,
                "fill_value": computed_fill if method not in ("ffill", "bfill") else method,
                "cells_imputed": cells_imputed,
            }

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": columns,
            "total_cells_imputed": total_cells_imputed,
            "per_column": report_per_column,
        }

    def _compute_fill(
        self, 
        series: pd.Series, 
        method: str, 
        fill_value: Any
    ) -> Any:
        if method == "mean":
            return series.mean()
        if method == "median":
            return series.median()
        if method == "mode":
            mode = series.mode()
            return mode.iloc[0] if not mode.empty else None
        if method == "constant":
            return fill_value
        return None  # ffill / bfill handled inline


# ——————————————————————————————————————————————————————————————
# 4. Cast Column Dtypes
class CastDtypesCleanStrategy(DataCleanStrategy):
    """
    Casts one or more columns to specified dtypes, with configurable error
    handling and optional coercion (invalid values become NaN instead of raising).
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="column_dtypes",
            type="dict",
            required=True,
            default=None,
            description=(
                "Mapping of column name → target dtype string. "
                "Examples: {'age': 'int64', 'signup_date': 'datetime64[ns]', 'score': 'float32'}."
            ),
            possible_values="dict with column names as keys and pandas dtype strings as values",
            value_descriptions={
                "int64 / int32": "Integer cast; fails on non-numeric strings unless errors='coerce'.",
                "float64 / float32": "Float cast.",
                "str / object": "String cast.",
                "category": "Memory-efficient categorical.",
                "bool": "Boolean cast.",
                "datetime64[ns]": "Datetime cast via pd.to_datetime (errors respected).",
            },
        ),
        ArgumentSpec(
            name="errors",
            type="str",
            required=False,
            default="coerce",
            description="Behaviour when a value cannot be cast.",
            possible_values="raise | coerce | ignore",
            value_descriptions={
                "raise": "Raise on first bad value.",
                "coerce": "Set bad values to NaN / NaT.",
                "ignore": "Leave bad values unchanged (only valid for numeric casts).",
            },
        ),
    ]

    @classmethod
    def _arguments_description(cls) -> str:
        return (
            "Always provide column_dtypes. The default errors='coerce' is recommended "
            "to avoid pipeline failures on dirty data."
        )

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 4,
            "name": "Cast signup_date to datetime and age to int",
            "cleaning": cls.__name__,
            "objective": "Correct dtypes identified by the type inspection step.",
            "actions": [
                "Cast 'signup_date' from object to datetime64[ns] with coerce.",
                "Cast 'age' from object to int64.",
            ],
            "arguments": {
                "column_dtypes": {"signup_date": "datetime64[ns]", "age": "int64"},
                "errors": "coerce",
            },
            "expected_output": [
                "Columns with corrected dtypes",
                "Count of values coerced to NaN per column",
            ],
        }

    def clean(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``column_dtypes`` (dict): col → dtype mapping (required).
            - ``errors`` (str): ``'raise'``, ``'coerce'``, or ``'ignore'``; default ``'coerce'``.
        :return: (cleaned_df, report)
        """
        column_dtypes: dict = kwargs.get("column_dtypes") or {}
        errors: str = kwargs.get("errors", "coerce")

        if not column_dtypes:
            raise ValueError("'column_dtypes' must be a non-empty dict.")

        cleaned = data.copy()
        per_column: dict = {}

        for col, dtype in column_dtypes.items():
            if col not in cleaned.columns:
                per_column[col] = {"error": "column_not_found"}
                continue

            null_before = int(cleaned[col].isna().sum())
            dtype_lower = str(dtype).lower()

            try:
                if "datetime" in dtype_lower:
                    cleaned[col] = pd.to_datetime(cleaned[col], errors=errors)
                elif dtype_lower in ("int", "int32", "int64"):
                    cleaned[col] = pd.to_numeric(cleaned[col], errors=errors).astype(
                        "Int64"  # nullable integer
                    )
                elif dtype_lower in ("float", "float32", "float64"):
                    cleaned[col] = pd.to_numeric(cleaned[col], errors=errors).astype(dtype)
                elif dtype_lower == "bool":
                    cleaned[col] = cleaned[col].astype(bool)
                elif dtype_lower == "category":
                    cleaned[col] = cleaned[col].astype("category")
                else:
                    cleaned[col] = cleaned[col].astype(dtype)

                null_after = int(cleaned[col].isna().sum())
                per_column[col] = {
                    "target_dtype": dtype,
                    "actual_dtype": str(cleaned[col].dtype),
                    "null_before": null_before,
                    "null_after": null_after,
                    "values_coerced": null_after - null_before,
                }
            except Exception as exc:
                per_column[col] = {"error": str(exc)}

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": list(column_dtypes.keys()),
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
# 5. Rename Columns
class RenameColumnsCleanStrategy(DataCleanStrategy):
    """
    Renames columns according to a mapping and optionally normalises all names
    to snake_case (lowercase, spaces and hyphens replaced with underscores).
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="rename_map",
            type="dict",
            required=False,
            default={},
            description="Old-name → new-name mapping, e.g. {'Full Name': 'full_name'}.",
            possible_values="dict with existing column names as keys",
            value_descriptions={
                "{'old': 'new'}": "Rename 'old' to 'new'.",
                "{}": "No explicit renames; combine with normalise=true for automatic normalisation.",
            },
        ),
        ArgumentSpec(
            name="normalise",
            type="bool",
            required=False,
            default=False,
            description=(
                "If true, after applying rename_map, convert all column names to "
                "snake_case: lowercase, strip leading/trailing whitespace, replace "
                "spaces and hyphens with underscores."
            ),
            possible_values="true | false",
            value_descriptions={
                "true": "Normalise all names after renaming.",
                "false": "Only apply explicit rename_map entries.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 5,
            "name": "Rename and normalise column names",
            "cleaning": cls.__name__,
            "objective": "Standardise column names for downstream processing.",
            "actions": [
                "Rename 'Customer ID' to 'customer_id'.",
                "Normalise remaining names to snake_case.",
            ],
            "arguments": {"rename_map": {"Customer ID": "customer_id"}, "normalise": True},
            "expected_output": [
                "DataFrame with standardised column names",
                "Before/after name mapping",
            ],
        }

    def clean(
        self, 
        data: pd.DataFrame, 
        **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``rename_map`` (dict): explicit old → new; default ``{}``.
            - ``normalise`` (bool): auto-snake_case; default ``False``.
        :return: (cleaned_df, report)
        """
        rename_map: dict = kwargs.get("rename_map") or {}
        normalise: bool = bool(kwargs.get("normalise", False))

        cleaned = data.rename(columns=rename_map)

        if normalise:
            cleaned.columns = [
                col.strip().lower().replace(" ", "_").replace("-", "_")
                for col in cleaned.columns
            ]

        original = list(data.columns)
        final = list(cleaned.columns)
        before_after = dict(zip(original, final))
        changed = {k: v for k, v in before_after.items() if k != v}

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": list(changed.keys()),
            "rename_map_applied": rename_map,
            "normalised": normalise,
            "column_name_changes": changed,
        }


# ——————————————————————————————————————————————————————————————
# 6. Clip / Cap Outliers
class ClipOutliersCleanStrategy(DataCleanStrategy):
    """
    Clips extreme values in numeric columns to a specified range, using either
    explicit bounds or IQR-derived fences, replacing values outside the range
    with the boundary value (Winsorisation).
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="columns",
            type="list[str]",
            required=True,
            default=None,
            description="Numeric columns to clip.",
            possible_values="list of numeric column names",
            value_descriptions={"[<col>, ...]": "Each column is clipped independently."},
        ),
        ArgumentSpec(
            name="method",
            type="str",
            required=False,
            default="iqr",
            description="Strategy for computing bounds when lower/upper are not supplied.",
            possible_values="iqr | percentile",
            value_descriptions={
                "iqr": "Bounds = Q1 - 1.5×IQR and Q3 + 1.5×IQR (same as outlier detection).",
                "percentile": "Bounds = lower_percentile and upper_percentile of the data.",
            },
        ),
        ArgumentSpec(
            name="lower",
            type="float",
            required=False,
            default=None,
            description="Explicit lower clip bound. Overrides method-derived lower bound.",
            possible_values="any float, or omit to compute from method",
            value_descriptions={},
        ),
        ArgumentSpec(
            name="upper",
            type="float",
            required=False,
            default=None,
            description="Explicit upper clip bound. Overrides method-derived upper bound.",
            possible_values="any float, or omit to compute from method",
            value_descriptions={},
        ),
        ArgumentSpec(
            name="lower_percentile",
            type="float",
            required=False,
            default=1.0,
            description="Lower percentile used when method='percentile'. Default 1.0 (1st percentile).",
            possible_values="float in (0, 50)",
            value_descriptions={},
            condition="only used when method == 'percentile'",
        ),
        ArgumentSpec(
            name="upper_percentile",
            type="float",
            required=False,
            default=99.0,
            description="Upper percentile used when method='percentile'. Default 99.0 (99th percentile).",
            possible_values="float in (50, 100)",
            value_descriptions={},
            condition="only used when method == 'percentile'",
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 6,
            "name": "Clip outliers in income using IQR",
            "cleaning": cls.__name__,
            "objective": "Winsorise extreme income values flagged as high-severity by outlier detection.",
            "actions": [
                "Compute Q1, Q3, IQR for 'income'.",
                "Clip values below Q1-1.5×IQR and above Q3+1.5×IQR.",
            ],
            "arguments": {"columns": ["income"], "method": "iqr"},
            "expected_output": [
                "DataFrame with clipped values",
                "Per-column bounds and cell-level clip counts",
            ],
        }

    def clean(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``columns`` (list[str]): required.
            - ``method`` (str): ``'iqr'`` or ``'percentile'``; default ``'iqr'``.
            - ``lower``, ``upper`` (float): explicit bounds; override method.
            - ``lower_percentile``, ``upper_percentile`` (float): for percentile method.
        :return: (cleaned_df, report)
        """
        columns: list[str] = kwargs.get("columns") or []
        method: str = kwargs.get("method", "iqr")
        explicit_lower = kwargs.get("lower")
        explicit_upper = kwargs.get("upper")
        lower_pct: float = float(kwargs.get("lower_percentile", 1.0))
        upper_pct: float = float(kwargs.get("upper_percentile", 99.0))

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        cleaned = data.copy()
        per_column: dict = {}
        total_clipped = 0

        for col in columns:
            if col not in cleaned.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(cleaned[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            series = cleaned[col].dropna()
            lower, upper = self._compute_bounds(
                series, method, explicit_lower, explicit_upper, lower_pct, upper_pct
            )

            clipped_mask = (cleaned[col] < lower) | (cleaned[col] > upper)
            n_clipped = int(clipped_mask.sum())
            cleaned[col] = cleaned[col].clip(lower=lower, upper=upper)
            total_clipped += n_clipped

            per_column[col] = {
                "lower_bound": round(lower, 6),
                "upper_bound": round(upper, 6),
                "cells_clipped": n_clipped,
                "method": method,
            }

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": columns,
            "total_cells_clipped": total_clipped,
            "per_column": per_column,
        }

    def _compute_bounds(
        self,
        series: pd.Series,
        method: str,
        explicit_lower: Any,
        explicit_upper: Any,
        lower_pct: float,
        upper_pct: float,
    ) -> tuple[float, float]:
        if method == "iqr":
            q1, q3 = float(series.quantile(0.25)), float(series.quantile(0.75))
            iqr = q3 - q1
            derived_lower = q1 - 1.5 * iqr
            derived_upper = q3 + 1.5 * iqr
        else:  # percentile
            derived_lower = float(np.percentile(series, lower_pct))
            derived_upper = float(np.percentile(series, upper_pct))

        lower = float(explicit_lower) if explicit_lower is not None else derived_lower
        upper = float(explicit_upper) if explicit_upper is not None else derived_upper
        return lower, upper


# ——————————————————————————————————————————————————————————————
# 7. Filter Rows
class FilterRowsCleanStrategy(DataCleanStrategy):
    """
    Removes rows that do not satisfy one or more column-level conditions,
    supporting numeric comparisons and categorical membership checks.
    """

    argument_specs: list[ArgumentSpec] = [
        ArgumentSpec(
            name="conditions",
            type="list",
            required=True,
            default=None,
            description=(
                "List of condition dicts. Each dict has: "
                "'column' (str), 'operator' (str), 'value' (scalar or list). "
                "Rows failing any condition are removed."
            ),
            possible_values=(
                "list of dicts, each with keys: column, operator, value. "
                "Operators: '>', '>=', '<', '<=', '==', '!=', 'in', 'not_in', 'notnull', 'isnull'."
            ),
            value_descriptions={
                "{'column': 'age', 'operator': '>=', 'value': 18}": "Keep rows where age >= 18.",
                "{'column': 'status', 'operator': 'in', 'value': ['active', 'pending']}": "Keep rows with listed status values.",
                "{'column': 'email', 'operator': 'notnull'}": "Keep rows where email is not null.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 7,
            "name": "Filter out underage and inactive users",
            "cleaning": cls.__name__,
            "objective": "Restrict dataset to active users aged 18 or over.",
            "actions": [
                "Keep rows where age >= 18.",
                "Keep rows where status in ['active', 'pending'].",
            ],
            "arguments": {
                "conditions": [
                    {"column": "age", "operator": ">=", "value": 18},
                    {"column": "status", "operator": "in", "value": ["active", "pending"]},
                ]
            },
            "expected_output": [
                "Filtered DataFrame",
                "Count of rows removed per condition",
            ],
        }

    def clean(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``conditions`` (list[dict]): required.
        :return: (cleaned_df, report)
        """
        conditions: list[dict] = kwargs.get("conditions") or []
        if not conditions:
            raise ValueError("'conditions' must be a non-empty list of condition dicts.")

        cleaned = data.copy()
        condition_reports = []

        for cond in conditions:
            col: str = cond["column"]
            op: str = cond["operator"]
            val = cond.get("value")

            if col not in cleaned.columns:
                condition_reports.append({"column": col, "error": "column_not_found"})
                continue

            before = len(cleaned)
            mask = self._build_mask(cleaned[col], op, val)
            cleaned = cleaned[mask]
            after = len(cleaned)
            condition_reports.append({
                "column": col, "operator": op, "value": val,
                "rows_removed": before - after,
            })

        return cleaned, {
            "rows_before": len(data),
            "rows_after": len(cleaned),
            "rows_affected": len(data) - len(cleaned),
            "columns_affected": [],
            "condition_reports": condition_reports,
        }

    def _build_mask(self, series: pd.Series, op: str, val: Any) -> pd.Series:
        if op == ">":
            return series > val
        if op == ">=":
            return series >= val
        if op == "<":
            return series < val
        if op == "<=":
            return series <= val
        if op == "==":
            return series == val
        if op == "!=":
            return series != val
        if op == "in":
            return series.isin(val)
        if op == "not_in":
            return ~series.isin(val)
        if op == "notnull":
            return series.notna()
        if op == "isnull":
            return series.isna()
        raise ValueError(f"Unknown operator: {op}")


# ——————————————————————————————————————————————————————————————
# 8. Standardise / Normalise Numeric Columns
class ScaleNumericCleanStrategy(DataCleanStrategy):
    """
    Scales numeric columns using standard scaling (Z-score), min-max normalisation,
    or robust scaling (IQR-based), fitting on the provided data in place.
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
            name="method",
            type="str",
            required=False,
            default="standard",
            description="Scaling method.",
            possible_values="standard | minmax | robust",
            value_descriptions={
                "standard": "Z-score: (x - mean) / std. Mean ≈ 0, std ≈ 1.",
                "minmax": "Min-max: (x - min) / (max - min). Range [0, 1].",
                "robust": "Robust: (x - median) / IQR. Less sensitive to outliers.",
            },
        ),
    ]

    @classmethod
    def _example(cls) -> dict:
        return {
            "step": 8,
            "name": "Standard-scale age and income",
            "cleaning": cls.__name__,
            "objective": "Normalise numeric features before feeding into a distance-based model.",
            "actions": ["Apply Z-score scaling to 'age' and 'income'."],
            "arguments": {"columns": ["age", "income"], "method": "standard"},
            "expected_output": [
                "DataFrame with scaled numeric columns",
                "Per-column scaling parameters (mean/std or min/max)",
            ],
        }

    def clean(self, data: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, dict]:
        """
        :param data: Input DataFrame.
        :param kwargs:
            - ``columns`` (list[str]): required.
            - ``method`` (str): ``'standard'``, ``'minmax'``, or ``'robust'``; default ``'standard'``.
        :return: (cleaned_df, report)
        """
        columns: list[str] = kwargs.get("columns") or []
        method: str = kwargs.get("method", "standard")

        if not columns:
            raise ValueError("'columns' must be a non-empty list.")

        cleaned = data.copy()
        per_column: dict = {}

        for col in columns:
            if col not in cleaned.columns:
                per_column[col] = {"error": "column_not_found"}
                continue
            if not pd.api.types.is_numeric_dtype(cleaned[col]):
                per_column[col] = {"error": "non_numeric_column"}
                continue

            series = cleaned[col].astype(float)

            if method == "standard":
                mean, std = float(series.mean()), float(series.std())
                cleaned[col] = (series - mean) / std if std else series - mean
                per_column[col] = {"method": method, "mean": mean, "std": std}

            elif method == "minmax":
                mn, mx = float(series.min()), float(series.max())
                cleaned[col] = (series - mn) / (mx - mn) if mx != mn else series - mn
                per_column[col] = {"method": method, "min": mn, "max": mx}

            elif method == "robust":
                median = float(series.median())
                q1, q3 = float(series.quantile(0.25)), float(series.quantile(0.75))
                iqr = q3 - q1
                cleaned[col] = (series - median) / iqr if iqr else series - median
                per_column[col] = {"method": method, "median": median, "iqr": iqr}

            else:
                per_column[col] = {"error": f"unknown_method: {method}"}

        return cleaned, {
            "rows_affected": 0,
            "columns_affected": columns,
            "per_column": per_column,
        }


# ——————————————————————————————————————————————————————————————
# Tabular Data Cleaner (Orchestrator)
class TabularDataCleaner:
    """
    Applies a sequence of ``DataCleanStrategy`` instances to a DataFrame,
    accumulating reports from each step. Mirrors the TabularDataInpsector API.
    """

    def __init__(
        self,
        strategy: Union[DataCleanStrategy, str],
    ) -> None:
        """
        :param strategy: A DataCleanStrategy instance or string identifier.
        :raises ValueError: For unrecognised string identifiers.
        """
        self.strategy = self._resolve(strategy)

    @staticmethod
    def _resolve(strategy: Union[DataCleanStrategy, str]) -> DataCleanStrategy:
        _registry: dict[str, type[DataCleanStrategy]] = {
            "drop_duplicates": DropDuplicatesCleanStrategy,
            "drop_columns": DropColumnsCleanStrategy,
            "impute_missing": ImputeMissingValuesCleanStrategy,
            "cast_dtypes": CastDtypesCleanStrategy,
            "rename_columns": RenameColumnsCleanStrategy,
            "clip_outliers": ClipOutliersCleanStrategy,
            "filter_rows": FilterRowsCleanStrategy,
            "scale_numeric": ScaleNumericCleanStrategy,
        }
        if isinstance(strategy, str):
            if strategy not in _registry:
                raise ValueError(
                    f"Unknown strategy: '{strategy}'. "
                    f"Available: {list(_registry.keys())}"
                )
            return _registry[strategy]()
        if isinstance(strategy, DataCleanStrategy):
            return strategy
        raise ValueError(
            "strategy must be a string identifier or a DataCleanStrategy instance."
        )

    def set_strategy(self, strategy: Union[DataCleanStrategy, str]) -> None:
        """Replace the current strategy."""
        self.strategy = self._resolve(strategy)

    def execute_strategy(
        self, data: pd.DataFrame, **kwargs
    ) -> tuple[pd.DataFrame, dict]:
        """
        Run the current strategy and return ``(cleaned_df, report)``.

        :param data: DataFrame to clean.
        :param kwargs: Forwarded to the strategy's ``clean`` method.
        :return: Tuple of cleaned DataFrame and report dict.
        """
        return self.strategy.clean(data, **kwargs)

    def run_pipeline(
        self,
        data: pd.DataFrame,
        steps: list[dict],
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Execute a sequence of cleaning steps, threading the DataFrame through each.

        Each step dict must have:
        - ``cleaning`` (str or DataCleanStrategy): strategy identifier.
        - ``arguments`` (dict): kwargs forwarded to the strategy.
        - ``name`` (str, optional): human-readable step label.

        :param data: Input DataFrame.
        :param steps: List of step dicts.
        :return: (final_cleaned_df, list_of_reports)
        """
        current = data.copy()
        reports: list[dict] = []

        for i, step in enumerate(steps):
            strategy_id = step.get("cleaning")
            arguments: dict = step.get("arguments") or {}
            label: str = step.get("name", f"step_{i + 1}")

            self.set_strategy(strategy_id)
            current, report = self.strategy.clean(current, **arguments)

            reports.append({
                "step": i + 1,
                "name": label,
                "cleaning": strategy_id if isinstance(strategy_id, str) else type(strategy_id).__name__,
                **report,
            })

        return current, reports