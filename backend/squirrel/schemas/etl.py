#!/usr/bin/python
"""
ETL Schemas
===========

Pydantic models for the constrained pipeline DSL, API requests, and persisted
pipeline/run rows.

The DSL is intentionally small and declarative. A pipeline is a DAG of
``source`` / ``transform`` / ``sink`` steps; transforms are a closed set of
structured operations (filter, rename, cast, aggregate, ...) rather than raw
SQL or Python. That makes every definition safe to validate before execution
and keeps LLM output out of the code-execution path.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from squirrel.constants.etl import (
    AggregateFn,
    CastType,
    FilterOp,
    JoinHow,
    SinkKind,
    SinkMode,
    SourceKind,
    StepType,
    TransformOp,
)


_STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"


class _StrictModel(BaseModel):
    """Reject unknown keys so an LLM cannot smuggle extra behaviour through."""

    model_config = ConfigDict(extra="forbid")


# ——————————————————————————————————————————————————————————————
# Step payloads

class SourceSpec(_StrictModel):
    """Read rows from a connector table, a stored file, or an app table."""

    kind: Literal["connector", "file", "table"]
    # connector sources
    connector_id: Optional[str] = Field(default=None, max_length=200)
    table: Optional[str] = Field(default=None, max_length=200)
    columns: List[str] = Field(default_factory=list)
    # file sources
    file_url: Optional[str] = Field(default=None, max_length=2000)
    # app-table sources
    table_name: Optional[str] = Field(default=None, max_length=63)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in SourceKind.ALL:
            raise ValueError(f"Unknown source kind: {value!r}")
        return value


class Aggregation(_StrictModel):
    """One aggregate expression inside an ``aggregate`` transform."""

    column: Optional[str] = Field(default=None, max_length=63)
    function: Literal["count", "sum", "avg", "min", "max"]
    alias: str = Field(..., min_length=1, max_length=63)

    @field_validator("function")
    @classmethod
    def _known_function(cls, value: str) -> str:
        if value not in AggregateFn.ALL:
            raise ValueError(f"Unknown aggregate function: {value!r}")
        return value


class JoinKey(_StrictModel):
    """One equality predicate between the left and right join inputs."""

    left: str = Field(..., min_length=1, max_length=63)
    right: str = Field(..., min_length=1, max_length=63)


class TransformSpec(_StrictModel):
    """
    One structured transformation.

    Which fields are required depends on ``op``; the compiler enforces that
    relationship so the DSL stays explicit and self-describing.
    """

    op: Literal[
        "select", "filter", "rename", "cast", "dedupe",
        "limit", "aggregate", "join",
    ]
    # select
    columns: Optional[List[str]] = None
    # filter
    column: Optional[str] = Field(default=None, max_length=63)
    comparison: Optional[
        Literal[
            "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
            "is_null", "not_null", "like", "contains",
        ]
    ] = None
    value: Optional[Any] = None
    values: Optional[List[Any]] = None
    # rename
    mapping: Optional[Dict[str, str]] = None
    # cast
    casts: Optional[Dict[str, str]] = None
    # limit
    count: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    # aggregate
    group_by: Optional[List[str]] = None
    aggregations: Optional[List[Aggregation]] = None
    # join
    right: Optional[str] = Field(default=None, pattern=_STEP_ID_PATTERN)
    on: Optional[List[JoinKey]] = None
    how: Optional[Literal["inner", "left", "right", "full"]] = None

    @field_validator("op")
    @classmethod
    def _known_op(cls, value: str) -> str:
        if value not in TransformOp.ALL:
            raise ValueError(f"Unknown transform op: {value!r}")
        return value

    @field_validator("comparison")
    @classmethod
    def _known_comparison(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in FilterOp.ALL:
            raise ValueError(f"Unknown filter comparison: {value!r}")
        return value

    @field_validator("how")
    @classmethod
    def _known_join_how(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in JoinHow.ALL:
            raise ValueError(f"Unknown join type: {value!r}")
        return value

    @field_validator("casts")
    @classmethod
    def _known_cast_types(
        cls, value: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        if value is None:
            return None
        unknown = [v for v in value.values() if v not in CastType.ALL]
        if unknown:
            raise ValueError(
                f"Unsupported cast type(s): {unknown}. "
                f"Allowed: {sorted(CastType.ALL)}"
            )
        return value


class SinkSpec(_StrictModel):
    """Write the final result to Postgres or MinIO."""

    kind: Literal["postgres", "minio"]
    # postgres
    table: Optional[str] = Field(default=None, max_length=63)
    mode: Literal["replace", "append"] = SinkMode.REPLACE
    # minio
    object_key: Optional[str] = Field(default=None, max_length=1024)
    format: Literal["csv"] = "csv"

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in SinkKind.ALL:
            raise ValueError(f"Unknown sink kind: {value!r}")
        return value

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in SinkMode.ALL:
            raise ValueError(f"Unknown sink mode: {value!r}")
        return value


# ——————————————————————————————————————————————————————————————
# DAG

class PipelineStep(_StrictModel):
    """One node in the pipeline DAG."""

    id: str = Field(..., pattern=_STEP_ID_PATTERN)
    type: Literal["source", "transform", "sink"]
    input: Optional[str] = Field(default=None, pattern=_STEP_ID_PATTERN)
    source: Optional[SourceSpec] = None
    transform: Optional[TransformSpec] = None
    sink: Optional[SinkSpec] = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in StepType.ALL:
            raise ValueError(f"Unknown step type: {value!r}")
        return value


class MaterializeSpec(_StrictModel):
    """Optionally expose a Postgres sink as a notebook dashboard."""

    notebook_id: str = Field(..., min_length=1, max_length=200)
    create_dashboard_cell: bool = True


class PipelineDefinition(_StrictModel):
    """The complete, validated pipeline DAG."""

    steps: List[PipelineStep] = Field(..., min_length=1, max_length=100)
    materialize: Optional[MaterializeSpec] = None


# ——————————————————————————————————————————————————————————————
# Requests

class CreatePipelineRequest(_StrictModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    definition: PipelineDefinition


class UpdatePipelineRequest(_StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    definition: Optional[PipelineDefinition] = None


class ApprovePipelineRequest(_StrictModel):
    comment: Optional[str] = Field(default=None, max_length=2000)


class RunPipelineRequest(_StrictModel):
    """``dry_run=True`` validates and compiles without writing a destination."""

    dry_run: bool = False


class MaterializePipelineRequest(_StrictModel):
    notebook_id: str = Field(..., min_length=1, max_length=200)


class PlanPipelineRequest(_StrictModel):
    """Natural-language request for the ETL planner agent."""

    query: str = Field(..., min_length=1, max_length=20_000)
    connector_id: Optional[str] = Field(default=None, max_length=200)
    table: Optional[str] = Field(default=None, max_length=200)
    file_urls: List[str] = Field(default_factory=list, max_length=20)
    sink_table: Optional[str] = Field(default=None, max_length=63)
    notebook_id: Optional[str] = Field(default=None, max_length=200)
    context: Dict[str, Any] = Field(default_factory=dict)


# ——————————————————————————————————————————————————————————————
# Reads

class PipelineRead(BaseModel):
    """One stored pipeline definition."""

    id: str
    owner_user_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    status: str
    version: int = 1
    definition: PipelineDefinition
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PipelineRunRead(BaseModel):
    """One execution of a pipeline."""

    id: str
    pipeline_id: str
    owner_user_id: Optional[str] = None
    job_id: Optional[str] = None
    status: str
    dry_run: bool = False
    row_count: Optional[int] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PipelinePlanRead(BaseModel):
    """A planner proposal that has not been persisted yet."""

    name: str
    description: Optional[str] = None
    definition: PipelineDefinition
    validation: Dict[str, Any] = Field(default_factory=dict)
