"""
Data Connector Schemas
=======================

Pydantic models for the ``/connector`` endpoints backing the Data
Connectors page (create/update/test/delete a ``DataConnection``, and list
the ``ConnectorTypeDef`` catalog the "Add a source" tiles are built from).

"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

# ——————————————————————————————————————————————————————————————
# Connector type catalog (read-only, describes the "Add a source" tiles)

class ConnectorField(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    placeholder: Optional[str] = None

class ConnectorType(BaseModel):
    id: str
    label: str
    description: str
    fields: List[ConnectorField]

class TableSelection(BaseModel):
    table: str
    columns: Optional[List[str]] = None

class TablePreview(BaseModel):
    table: str
    columns: List[str] = Field(default_factory=list)
    preview: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None  # set if this one table's preview failed

# ——————————————————————————————————————————————————————————————
# Requests

class ConnectorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., description="One of the ids returned by GET /connectors/types.")
    config: Dict[str, str] = Field(
        default_factory=dict,
        description="Field values keyed by the connector type's field keys (e.g. host, port, password).",
    )

class ConnectorUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    config: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Partial or full field values to update. A blank value for a "
            "secret field (e.g. password) means 'keep the existing value' — "
            "it is never overwritten with an empty string."
        ),
    )

class ConnectorPreviewRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=20, ge=1, le=200)

class ConnectorSourcesRequest(BaseModel):
    connector_id: str
    tables: List[TableSelection]

# ——————————————————————————————————————————————————————————————
# Responses

class ConnectorPreviewResponse(BaseModel):
    columns: List[str]
    row_count: int
    preview: List[Dict[str, Any]]

class ConnectorTablesPreviewResponse(BaseModel):
    tables: List[TablePreview]

class DataConnectionOut(BaseModel):
    """
    A single connection, safe to return from the API: secret field values
    are always redacted (returned as ``""``) regardless of what's stored.
    """
    connector_id:   str
    name:           str
    type:           str
    status:         str
    error:          Optional[str] = None
    last_tested_at: Optional[datetime] = None
    config:         Dict[str, str] = Field(default_factory=dict)
    created_at:     datetime
    updated_at:     datetime

# ——————————————————————————————————————————————————————————————
# Exceptions

class ConnectorNotFoundError(Exception):
    """Raised when a ``connector_id`` doesn't exist."""

class ConnectorValidationError(Exception):
    """Raised when a create/update request's ``config`` fails validation."""

class ConnectorInUseError(Exception):
    """Raised when a connector can't be deleted because it's still referenced
    by at least one notebook data source or workspace input source."""

