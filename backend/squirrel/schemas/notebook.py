#!/usr/bin/python
"""
Notebook Schemas
================

Overview
--------

Pydantic models backing the Notebook feature: a notebook is a workspace-like
container that contains one or more data sources (an uploaded CSV loaded into
Postgres, or a table on an existing :class:`DataConnectorService` connector)
and a running feed of "cells" — a full exploratory-data-analysis run, a
dashboard, an answer to a specific natural-language question, or a markdown
note — all produced (where applicable) by :class:`TabularDataExploratoryAgent`.

There is no separate data-transformation/lineage model here on purpose: a
manipulation-flavored question (e.g. "dedupe this" or "filter out
outliers") is answered like any other question — either with a chart, or,
when :class:`TabularDataExploratoryAgent.classify_question_intent` decides a
table fits better, with a saved SQL VIEW. The underlying data source's table
is never rewritten in place, so there's nothing to model as lineage.

Models
------

- :class:`NotebookDataSource`   — the one data source a notebook owns.
- :class:`Notebook`             — persisted row / list-view shape.
- :class:`NotebookChart`        — one rendered hypothesis/question result.
- :class:`NotebookCell`         — one turn in the notebook's cell feed.
- :class:`CreateNotebookRequest`
- :class:`RenameNotebookRequest`
- :class:`AddCsvDataSourceRequest`      (multipart — see route)
- :class:`AddConnectorDataSourceRequest`
- :class:`RunCellRequest`

"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

# Third-party libraries
from pydantic import BaseModel, Field

# ——————————————————————————————————————————————————————————————
# Data source

class NotebookDataSource(BaseModel):
    """
    One data source attached to a notebook.
    """
    id: str = Field(..., description="Stable id for this attached source")
    kind: Literal["upload", "connector"] = Field(..., description="Where the data lives")
    table_name: str = Field(..., description="Table name queried for this source")
    connector_id: Optional[str] = Field(default=None, description="Set when kind='connector'")
    connector_type: Optional[str] = Field(
        default=None,
        description="e.g. 'postgres', 'bigquery' - set when kind='connector'"
    )
    source_file_url: Optional[str] = Field(default=None, description="MinIO URL when kind='upload'")
    original_filename: Optional[str] = Field(default=None, description="Original filename when kind='upload'")
    row_count: Optional[int] = Field(default=None)
    column_count: Optional[int] = Field(default=None)
    label: Optional[str] = Field(
        default=None,
        description="Short display name for @-mentions, e.g. 'orders.csv' or 'prod-pg/customers'"
    )


class DataSourcePreview(BaseModel):
    """
    Read-only peek at the table a notebook is bound to.
    """
    table_name: str = Field(..., description="Table being previewed")
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: Optional[int] = Field(default=None, description="Approximate total row count")

# ——————————————————————————————————————————————————————————————
# Cells / charts

class NotebookChart(BaseModel):
    """
    One rendered result — either one EDA hypothesis, one answered question,
    or one saved-view preview (``plot_type="table"``).
    """
    id: str = Field(..., description="Stable id for this chart within its cell")
    title: str = Field(..., description="Short title")
    question: str = Field(..., description="The question/hypothesis this chart answers")
    plot_type: str = Field(..., description="line | bar | scatter | boxplot | heatmap | pie | radar | table")
    sql: str = Field(default="", description="The SQL used to produce the data")
    x: Dict[str, str] = Field(default_factory=dict, description="Axis spec: column, label, type")
    y: Dict[str, str] = Field(default_factory=dict, description="Axis spec: column, label, type")
    group_by: Optional[str] = Field(default=None)
    data: List[Dict[str, Any]] = Field(default_factory=list, description="Row-capped query result")
    observation: str = Field(default="", description="Plain-text insight for this chart")
    error: Optional[str] = Field(default=None, description="Set if this chart failed to produce")
    total_row_count: Optional[int] = Field(
        default=None, description="Rows the underlying query actually returned, before capping."
    )
    truncated: bool = Field(
        default=False, description="True when `data` was capped below total_row_count."
    )

class DashboardLayout(BaseModel):
    order: List[str] = Field(default_factory=list, description="Chart ids in display order")
    hidden_ids: List[str] = Field(default_factory=list, description="Chart ids hidden from the dashboard")
    hints: Dict[str, str] = Field(default_factory=dict, description="Per-chart user notes")
    overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Per-chart field overrides")
    custom_charts: List[Dict[str, Any]] = Field(default_factory=list, description="User-added charts")
    text_blocks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="User-added standalone text/heading blocks (id, size, content, width, card)",
    )

class DashboardVersion(BaseModel):
    """Snapshot of a dashboard cell before regeneration."""
    id: str = Field(..., description="Unique version id")
    cell_id: str = Field(..., description="Dashboard cell this version belongs to")
    notebook_id: str = Field(..., description="Owning notebook id")
    version_number: int = Field(..., description="Monotonic version counter per cell")
    reply: str = Field(default="")
    charts: List[NotebookChart] = Field(default_factory=list)
    dashboard_layout: Optional[DashboardLayout] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotebookCell(BaseModel):
    """
    One turn in a notebook's feed: a user request plus the agent's result.
    """
    id: str = Field(..., description="Unique cell id")
    notebook_id: str = Field(..., description="Owning notebook id")
    type: Literal["eda", "question", "dashboard", "markdown"]
    query: str = Field(default="")
    data_source_ids: List[str] = Field(
        default_factory=list,
        description="Which sources this cell was actually scoped to"        
    )
    status: Literal["running", "complete", "error"] = Field(default="complete")
    reply: str = Field(default="")
    charts: List[NotebookChart] = Field(default_factory=list)
    dashboard_layout: Optional[DashboardLayout] = Field(
        default=None,
        description="Persisted dashboard customizations (order, edits, custom charts)",
    )
    error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    response_time_ms: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    order_index: int = Field(default=0, description="Cell's position in the notebook")

# ——————————————————————————————————————————————————————————————
# Notebook

class Notebook(BaseModel):
    """
    Persisted notebook row / detail-view shape.
    """
    id: str = Field(..., description="Unique notebook id")
    name: str = Field(..., description="Display name")
    description: Optional[str] = Field(default=None)
    status: Literal["empty", "ready", "error"] = Field(
        default="empty", description="empty = no data source yet, ready = queryable"
    )
    data_sources: List[NotebookDataSource] = Field(default_factory=list)
    cell_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ——————————————————————————————————————————————————————————————
# Requests

class CreateNotebookRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)


class RenameNotebookRequest(BaseModel):
    """
    Body for ``PATCH /notebooks/{notebook_id}``. Both fields are optional
    and independently nullable-by-omission — the frontend's rename modal
    sends whichever of name/description actually changed, so the service
    layer only touches the fields that were explicitly provided rather
    than clobbering the other one back to empty.
    """
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)


class AddConnectorDataSourceRequest(BaseModel):
    """
    Bind a notebook to a table on an existing connector (no upload).
    """
    connector_id: str = Field(..., description="Existing DataConnectorService connection id")
    table_name: str = Field(..., description="Table on that connector to bind to")


class RunCellRequest(BaseModel):
    """
    Run a cell against some subset of the notebook's attached data sources.

    A manipulation-flavored ``question`` (e.g. "dedupe by customer_id") is
    not a distinct cell type — it's answered by the same "question" pipeline,
    which decides internally whether a chart or a saved view fits better.
    """
    type: Literal["eda", "question", "dashboard", "markdown"] = Field(...)
    query: Optional[str] = Field(default=None, max_length=20000)
    data_source_ids: Optional[List[str]] = Field(default=None)


class UpdateCellRequest(BaseModel):
    query: Optional[str] = Field(default=None, max_length=20000)
    data_source_ids: Optional[List[str]] = Field(default=None)


class AddExistingFileDataSourceRequest(BaseModel):
    file_url: str
    file_name: Optional[str] = None


class RenameDataSourceRequest(BaseModel):
    label: str


class SaveDashboardLayoutRequest(BaseModel):
    """Body for ``PATCH .../dashboard/layout``."""
    order: List[str] = Field(default_factory=list)
    hidden_ids: List[str] = Field(default_factory=list)
    hints: Dict[str, str] = Field(default_factory=dict)
    overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    custom_charts: List[Dict[str, Any]] = Field(default_factory=list)
    text_blocks: List[Dict[str, Any]] = Field(default_factory=list)