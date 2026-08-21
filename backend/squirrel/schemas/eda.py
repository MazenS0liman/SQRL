#!/usr/bin/python
"""
EDA Models Module
=================

Pydantic data-transfer objects for the Tabular Data Exploratory Agent pipeline.

Models
------

- AxisSpec     — describes one axis (x or y) of a chart.
- Hypothesis   — a single analytical hypothesis to be tested.
- SqlSpec      — SQL query with chart axis metadata.
- ChartResult  — fully resolved chart: hypothesis + data + observation.

"""

# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import Final, Optional, Dict

# Third-Party Libraries
import pandas as pd
from pydantic import BaseModel, Field, ConfigDict, Field
from typing import Optional, Dict

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------

_AXIS_SPEC: Dict = {
    "type": "object",
    "description": "Metadata for a single chart axis.",
    "properties": {
        "column": {
            "type": "string",
            "description": "Exact column alias used in the SELECT clause.",
        },
        "label": {
            "type": "string",
            "description": "Human-readable axis label shown in the chart.",
        },
        "type": {
            "type": "string",
            "enum": ["temporal", "quantitative", "ordinal", "nominal"],
            "description": "Vega-Lite scale type for the axis.",
        },
    },
    "required": ["column", "label", "type"],
}

_KPI: Dict = {
    "type": "object",
    "properties": {
        "name":             {"type": "string"},
        "description":      {"type": "string"},
        "tables_involved":  {"type": "array", "items": {"type": "string"}},
        "sql_hint":         {"type": "string"},
    },
    "required": ["name", "description", "tables_involved", "sql_hint"],
}

_METRIC: Dict = {
    "type": "object",
    "properties": {
        "name":           {"type": "string", "description": "Display name for the metric."},
        "sql_expression": {"type": "string", "description": "Aggregation SQL, e.g. SUM(o.total_amount)."},
        "tables":         {"type": "array", "items": {"type": "string"}},
        "description":    {"type": "string"},
    },
    "required": ["name", "sql_expression", "tables", "description"],
}

_DIMENSION: Dict = {
    "type": "object",
    "properties": {
        "name":        {"type": "string", "description": "Display name for the dimension."},
        "column":      {"type": "string", "description": "Exact column name from the schema."},
        "type":        {
            "type": "string",
            "enum": ["temporal", "categorical", "geographic", "boolean"],
        },
        "description": {"type": "string"},
    },
    "required": ["name", "column", "type", "description"],
}

_KEY_FINDING: Dict = {
    "type": "object",
    "properties": {
        "finding":          {"type": "string", "description": "Specific, quantified insight."},
        "source_analyses":  {"type": "array", "items": {"type": "string"}},
        "business_impact":  {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["finding", "source_analyses", "business_impact"],
}

# ---------------------------------------------------------------------------
# Stage 2 — Domain context
# ---------------------------------------------------------------------------

DOMAIN_CONTEXT_SCHEMA: Final[Dict] = {
    "type": "object",
    "description": (
        "Business-domain inference produced from schema statistics. "
        "Returned by Stage 2 (generate_domain_context)."
    ),
    "properties": {
        "domain": {
            "type": "string",
            "description": "Industry / business domain, e.g. 'e-commerce', 'SaaS', 'healthcare'.",
        },
        "domain_confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "main_entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Core business objects, e.g. ['Customer', 'Order', 'Product'].",
        },
        "fact_tables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tables containing transactional / event data.",
        },
        "dimension_tables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tables containing descriptive / reference data.",
        },
        "kpis": {
            "type": "array",
            "items": _KPI,
            "description": "Candidate KPIs inferred from the schema.",
        },
        "exploration_goals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "High-value analytical questions for this domain.",
        },
        "data_quality_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concerns such as sparse columns, likely PII, or potential duplicates.",
        },
    },
    "required": [
        "domain",
        "domain_confidence",
        "main_entities",
        "fact_tables",
        "dimension_tables",
        "kpis",
        "exploration_goals",
        "data_quality_flags",
    ],
}

# ---------------------------------------------------------------------------
# Stage 3 — Entities and metrics
# ---------------------------------------------------------------------------

ENTITIES_AND_METRICS_SCHEMA: Final[Dict] = {
    "type": "object",
    "description": (
        "Concrete measurable metrics and grouping dimensions resolved from "
        "the domain context. Returned by Stage 3 (extract_entities_and_metrics)."
    ),
    "properties": {
        "metrics": {
            "type": "array",
            "items": _METRIC,
            "minItems": 1,
            "description": "Numeric columns or SQL expressions that can be aggregated.",
        },
        "dimensions": {
            "type": "array",
            "items": _DIMENSION,
            "minItems": 1,
            "description": "Categorical / temporal columns useful for slicing metrics.",
        },
    },
    "required": ["metrics", "dimensions"],
}

# ---------------------------------------------------------------------------
# Stage 4 — Hypotheses
# ---------------------------------------------------------------------------

_HYPOTHESIS_ITEM: Dict = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Unique slug, e.g. 'h_01'.",
            "pattern": r"^h_\d+$",
        },
        "question": {
            "type": "string",
            "description": "Precise analytical question the chart will answer.",
        },
        "title": {
            "type": "string",
            "description": "Chart title — eight words or fewer.",
            "maxLength": 80,
        },
        "description": {
            "type": "string",
            "description": "What the chart shows and why it is interesting.",
        },
        "why_it_matters": {
            "type": "string",
            "description": "Business implication if the hypothesis is confirmed.",
        },
        "plot_type": {
            "type": "string",
            "enum": ["line", "bar", "scatter", "boxplot", "heatmap"],
        },
        "analytical_type": {
            "type": "string",
            "enum": [
                "segmentation",
                "concentration",
                "seasonality",
                "outlier_detection",
                "anomaly_detection",
                "cohort_behavior",
                "change_point_detection",
                "growth_drivers",
                "retention",
                "funnel_analysis",
                "behavioral_clusters",
                "pareto_effect",
                "variance_decomposition",
                "correlation",
            ],
        },
    },
    "required": [
        "id",
        "question",
        "title",
        "description",
        "why_it_matters",
        "plot_type",
        "analytical_type",
    ],
}

HYPOTHESES_SCHEMA: Final[Dict] = {
    "type": "array",
    "description": (
        "Ordered list of analytical hypotheses generated by Stage 4 "
        "(generate_hypotheses). Each item maps 1-to-1 with a ChartResult."
    ),
    "items": _HYPOTHESIS_ITEM,
    "minItems": 1,
}

# ---------------------------------------------------------------------------
# Stage 5 — SQL spec
# ---------------------------------------------------------------------------

SQL_SPEC_SCHEMA: Final[Dict] = {
    "type": "object",
    "description": (
        "PostgreSQL query and axis metadata generated by Stage 5 (generate_sql). "
        "x / y columns must be aliases present in the SELECT clause."
    ),
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "Valid PostgreSQL SELECT statement. "
                "No DDL, no DML, LIMIT 1000 maximum."
            ),
        },
        "x": _AXIS_SPEC,
        "y": _AXIS_SPEC,
        "group_by": {
            "type": ["string", "null"],
            "description": (
                "Column alias used for colour / series grouping, "
                "or null when no grouping is needed."
            ),
        },
    },
    "required": ["sql", "x", "y", "group_by"],
}

# ---------------------------------------------------------------------------
# Stage 8 — Executive summary
# ---------------------------------------------------------------------------

SUMMARY_SCHEMA: Final[Dict] = {
    "type": "object",
    "description": (
        "Board-level executive summary synthesised from all succeeded chart "
        "observations. Returned by Stage 8 (generate_summary)."
    ),
    "properties": {
        "dataset_description": {
            "type": "string",
            "description": (
                "2-3 sentences describing what this dataset contains "
                "and its analytical richness."
            ),
        },
        "key_findings": {
            "type": "array",
            "items": _KEY_FINDING,
            "minItems": 1,
            "description": "Most important, quantified insights from the analyses.",
        },
        "recommended_next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": (
                "Concrete, prioritised actions such as deep-dives, "
                "data collection, or experiments."
            ),
        },
    },
    "required": ["dataset_description", "key_findings", "recommended_next_steps"],
}

# ---------------------------------------------------------------------------
# Convenience export — index by ExplorePromptType value string
# ---------------------------------------------------------------------------

SCHEMA_BY_STAGE: Final[Dict] = {
    "generate_domain_context":       DOMAIN_CONTEXT_SCHEMA,
    "extract_entities_and_metrics":  ENTITIES_AND_METRICS_SCHEMA,
    "generate_hypotheses":           HYPOTHESES_SCHEMA,
    "generate_sql":                  SQL_SPEC_SCHEMA,
    "generate_summary":              SUMMARY_SCHEMA,
}

# ——————————————————————————————————————————————————————————————
# Data-transfer objects

class AxisSpec(BaseModel):
    """
    Describes one axis (x or y) of a chart.
    """

    column: str
    label: str
    type: str  # "temporal" | "quantitative" | "ordinal" | "nominal"


class Hypothesis(BaseModel):
    """
    A single analytical hypothesis to be tested.
    """

    id: str
    question: str
    title: str
    description: str
    why_it_matters: str
    plot_type: str        # "line" | "bar" | "scatter" | "boxplot" | "heatmap"
    analytical_type: str  # "segmentation" | "cohort" | "anomaly" | …


class SqlSpec(BaseModel):
    """
    SQL query with chart axis metadata.
    """

    sql: str
    x: AxisSpec
    y: AxisSpec
    group_by: Optional[str] = None


class ChartResult(BaseModel):
    """
    Fully resolved chart — hypothesis + data + observation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    hypothesis: Hypothesis
    sql_spec: SqlSpec
    data: pd.DataFrame = Field(default_factory=pd.DataFrame)
    observation: str
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.data.empty

    def to_dict(self) -> dict:
        return {
            "id":              self.hypothesis.id,
            "question":        self.hypothesis.question,
            "title":           self.hypothesis.title,
            "description":     self.hypothesis.description,
            "plot_type":       self.hypothesis.plot_type,
            "analytical_type": self.hypothesis.analytical_type,
            "sql":             self.sql_spec.sql,
            "x": {
                "column": self.sql_spec.x.column,
                "label":  self.sql_spec.x.label,
                "type":   self.sql_spec.x.type,
            },
            "y": {
                "column": self.sql_spec.y.column,
                "label":  self.sql_spec.y.label,
                "type":   self.sql_spec.y.type,
            },
            "group_by":    self.sql_spec.group_by,
            "observation": self.observation,
            "data":        self.data.to_dict("records"),
            "error":       self.error,
        }

