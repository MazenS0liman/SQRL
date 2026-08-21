"""
Workspace Schemas
=================

Pydantic models for the workspace CRUD, upload, and build endpoints.

All response models share a common ``WorkspaceRecord`` that mirrors the
``workspaces`` database row so that callers can track artifacts across
multiple pipeline runs. Every workspace is keyed on ``workspace_id`` (the
same value used throughout ``WorkspaceService``) — there is no separate
integer id in play.

"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
from datetime import datetime
from typing import Any, Dict, List, Optional

# Third-Party Libraries
from pydantic import BaseModel, Field

# Schemas
from squirrel.schemas.api import LLMTokens
from squirrel.schemas.file import UploadedSourceOut

# Constants
from squirrel.constants.workspace import DataType

# ——————————————————————————————————————————————————————————————
# Datasource Model

class DataSource(BaseModel):
    """
    A single input source attached to a workspace.

    Generic across source kinds ("upload" | "connector") and, within
    uploads, across file types — a CSV source populates ``columns`` /
    ``row_count``; other file types (once implemented) may leave those
    unset and rely on ``connector_id`` being absent to distinguish
    themselves from connector sources.
    """

    source_id: str
    kind: str
    name: str
    file_type: Optional[str] = None
    file_url: Optional[str] = None
    columns: Optional[List[str]] = None
    all_columns: Optional[List[str]] = None   # NEW — full column set, for uploads
    row_count: Optional[int] = None
    connector_id: Optional[str] = None
    table: Optional[str] = None
    query: Optional[str] = None

class SourceColumnsRequest(BaseModel):
    columns: Optional[List[str]] = None  # None/full list => use every column

# ——————————————————————————————————————————————————————————————
# Workspace & Workspace Run Record Model


class WorkspaceRecord(BaseModel):
    """
    A single row from the ``workspaces`` table, keyed on ``workspace_id``.
    """

    workspaceId:   str                 = Field(..., alias="workspace_id")
    name:          str
    status:        str
    dataType:      str                 = Field(default=DataType.STRUCTURED, alias="data_type")
    targetColumn:  Optional[str]       = Field(default=None, alias="target_column")
    inputSources:  List[DataSource]    = Field(default=[], alias="input_sources")
    error:         Optional[str]       = None
    created_at:    Any
    updated_at:    Any

    class Config:
        populate_by_name = True


class WorkspaceRunRecord(BaseModel):
    """
    A single row from the ``workspace_runs`` table.
    """

    id:               Optional[int]       = None
    workspaceId:      str                 = Field(..., alias="workspace_id")
    agentType:        str                 = Field(..., alias="agent_type")
    inputFileUrls:    List[str]           = Field(default_factory=list, alias="input_file_urls")
    outputFileUrls:   List[str]           = Field(default_factory=list, alias="output_file_urls")
    agentSummary:     Dict[str, Any]      = Field(default_factory=dict, alias="agent_summary")
    promptTokens:     int                 = Field(0, alias="prompt_tokens")
    completionTokens: int                 = Field(0, alias="completion_tokens")
    totalTokens:      int                 = Field(0, alias="total_tokens")
    responseTime:     int                 = Field(0, alias="response_time_ms")
    createdAt:        Optional[datetime]  = Field(None, alias="created_at")

    class Config:
        populate_by_name = True


# ——————————————————————————————————————————————————————————————
# Workspace mutation requests


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Display name for the workspace.")
    data_type: str = Field(
        default=DataType.STRUCTURED,
        description="Kind of data this workspace holds: structured, image, text, audio, or other.",
    )


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    data_type: Optional[str] = None


class ConnectorSourceRequest(BaseModel):
    connector_id: str
    name: str
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "SQL query selecting exactly the data this source should provide "
            "— built from a table picked via GET /connectors/{connector_id}/tables, "
            "or typed freehand and checked via POST /connectors/{connector_id}/preview."
        ),
    )

# ——————————————————————————————————————————————————————————————
# Upload endpoint


class UploadResponse(BaseModel):
    """
    Response payload for ``POST /workspace/{workspace_id}/upload``.

    Generic across every file type accepted by the upload endpoint — see
    ``UploadedSourceOut`` for why its fields are mostly optional.
    """

    workspace_id: str
    uploaded:     List[UploadedSourceOut]
    sources:      List[DataSource]          = Field(default_factory=list)


# ——————————————————————————————————————————————————————————————
# Build endpoint


class BuildRequest(BaseModel):
    target_column: str = Field(..., min_length=1, description="Column to predict, chosen from the union of source columns.")
    query: Optional[str] = Field(
        default=None,
        description=(
            "Optional free-text instruction describing how the workspace's "
            "input sources relate to one another (e.g. 'join on customer_id') "
            "and/or what the resulting model should optimize for. Passed "
            "through to the preprocessing and model-building agents as "
            "additional grounding — most useful when sources don't share an "
            "identical or obviously-disjoint schema and the agent has to "
            "infer a relationship between them."
        ),
    )


class ModelMetric(BaseModel):
    model_key: str
    metric_name: str
    mean: float
    std: Optional[float] = None


class ModelFileOut(BaseModel):
    model_key: str
    file_url: str


class BuildResponse(BaseModel):
    workspace_id: str
    status: str
    preprocessing_summary: Dict[str, Any]
    model_summary: Dict[str, Any]
    model_comparison: List[ModelMetric]
    best_model: Optional[str] = None
    output_file_urls: List[str] = []
    model_files: List[ModelFileOut] = []


class ModelsResponse(BaseModel):
    workspace_id: str
    status: str
    target_column: Optional[str] = None
    preprocessing_summary: Optional[Dict[str, Any]] = None
    model_summary: Optional[Dict[str, Any]] = None
    model_comparison: List[ModelMetric] = []
    best_model: Optional[str] = None
    output_file_urls: List[str] = []
    model_files: List[ModelFileOut] = []


# ——————————————————————————————————————————————————————————————
# Chat-driven preprocessing / model-build endpoints (legacy conversational
# flow, kept alongside the workspace-pipeline endpoints above)


class PreprocessRequest(BaseModel):
    """
    Request payload for ``POST /preprocess``.

    Callers must supply a ``conversationId`` so the workspace row can be
    linked to the chat history, and a ``workspaceId`` for workspace-level
    queries. The ``fileUrls`` list must contain at least one ``s3://`` URL
    pointing to the raw CSV that the preprocessing agent should clean.
    """

    workspaceId:    str                      = Field(..., description="Owning workspace identifier.")
    conversationId: str                      = Field(..., description="Links this run to a chat conversation.")
    fileUrls:       List[str]                = Field(..., min_length=1, description="s3:// URLs of raw input files.")
    edaSummary:     Optional[Dict[str, Any]] = Field(None, description="Structured EDA output from the inspector agent.")
    objective:      Optional[str]            = Field(None, description="Free-text preprocessing goal.")
    query:          Optional[str]            = Field(None, description="Optional natural-language instruction.")


class PreprocessResponse(BaseModel):
    """
    Response payload for ``POST /preprocess``.

    On success ``workspaceId`` and ``outputFileUrls`` are populated so the
    caller can immediately chain into the model-building endpoint.
    """

    reply:          str                      = ""
    workspaceId:    Optional[str]            = None
    outputFileUrls: List[str]               = Field(default_factory=list)
    summary:        Optional[Dict[str, Any]] = None
    llmTokens:      Optional[LLMTokens]      = None
    responseTime:   Optional[int]            = None
    metadata:       Optional[Dict[str, Any]] = None


class ModelBuildRequest(BaseModel):
    """
    Request payload for ``POST /model-build``.

    ``fileUrls`` should point to the processed CSV(s) produced by the
    preprocessing pipeline (i.e. ``outputFileUrls`` from a
    :class:`PreprocessResponse`).  ``targetColumn`` is the name of the label
    column in that CSV.

    """

    workspaceId:          str                      = Field(..., description="Owning workspace identifier.")
    conversationId:       str                      = Field(..., description="Links this run to a chat conversation.")
    fileUrls:             List[str]                = Field(..., min_length=1, description="s3:// URLs of processed input files.")
    targetColumn:         str                      = Field(..., description="Name of the target / label column.")
    preprocessingSummary: Optional[Dict[str, Any]] = Field(None, description="Structured output from the preprocessing agent.")
    objective:            Optional[str]            = Field(None, description="Free-text modelling goal.")
    query:                Optional[str]            = Field(None, description="Optional natural-language instruction.")


class ModelBuildResponse(BaseModel):
    """
    Response payload for ``POST /model-build``.

    ``outputFileUrls`` contains ``s3://`` URLs for every ``*.joblib`` model
    file written to MinIO so callers can download or reference them later.
    """

    reply:          str                      = ""
    workspaceId:    Optional[str]            = None
    outputFileUrls: List[str]               = Field(default_factory=list)
    summary:        Optional[Dict[str, Any]] = None
    llmTokens:      Optional[LLMTokens]      = None
    responseTime:   Optional[int]            = None
    metadata:       Optional[Dict[str, Any]] = None


# ——————————————————————————————————————————————————————————————
# Workspace retrieval endpoint


class WorkspaceListResponse(BaseModel):
    """Paginated list of workspace records."""

    total:      int                  = 0
    workspaces: List[WorkspaceRecord] = Field(default_factory=list)