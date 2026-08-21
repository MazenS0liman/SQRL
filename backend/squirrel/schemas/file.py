"""
File schema module
==================

Provides Pydantic models used to describe files passed into or produced by
the API (URLs, metadata, processing status).

These models are intentionally **file-type agnostic**: a workspace input
source may be a CSV today and a plain-text document or a zipped dataset
tomorrow, so the shapes below use optional fields rather than baking in
CSV-specific columns/row_count/preview assumptions. Anything type-specific
(e.g. CSV column names) is optional and simply left unset for file types
that don't have it.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
from enum import Enum
from datetime import datetime
from typing import Any, Dict, List, Optional

# Pydantic
from pydantic import BaseModel, Field, AnyUrl, model_validator

# --- File Type Model ---
class FileType(str, Enum):
    """
    File Type Enumeration
    ---------------------

    **Description:**

        Enumeration of supported file types that can be generated, uploaded,
        or handled by the system.

    .. note::
        Adding a new file type here does not by itself make it usable as an
        input source — a parser for it must also be registered wherever
        uploads are processed (see the ``_SOURCE_PARSERS`` registry in the
        workspace route). This enum is just the set of types the schema
        layer knows how to *describe*.
    """
    #: Comma-separated values format (.csv)
    CSV = "csv"
    #: Microsoft Excel format (.xlsx)
    XLSX = "xlsx"
    #: Portable Document Format (.pdf)
    PDF = "pdf"
    #: Plain text format (.txt)
    TXT = "txt"
    #: Zip archive, e.g. a bundle of files (.zip)
    ZIP = "zip"


# ——————————————————————————————————————————————————————————————
# File Models
class File(BaseModel):
    """
    Represents a single file reference including optional metadata.
    """
    fileUrl:      str                         = Field(..., description="Public or internal URL referencing the file")
    fileName:     Optional[str]               = Field(None, description="Friendly file name or object key")
    fileType:     Optional[FileType]          = Field(None, description="File type/extension (csv, xlsx, pdf, txt, zip, etc)")
    fileByte:     Optional[bytes]             = Field(None, description="Optional file content as bytes (for uploads)")
    size:         Optional[int]               = Field(None, description="File size in bytes, when known")
    workspaceIds: Optional[List[str]]         = Field(None, description="Optional list of workspace IDs the file is associated with")
    notebookIds:  Optional[List[str]]         = Field(None, description="Optional list of notebook IDs the file is associated with")
    ownerUserId:  Optional[str]               = Field(None, description="Optional user ID of the file owner")
    checksum:     Optional[str]               = Field(None, description="Optional checksum for integrity checks")
    uploadedAt:   Optional[datetime]          = Field(None, description="Optional upload timestamp")
    metadata:     Optional[Dict[str, Any]]    = Field(None, description="Free-form metadata about the file")

    @model_validator(mode="before")
    @classmethod
    def _normalize_empty_strings(cls, values: Any) -> Any:
        # Convert empty-string fields to None for optional URL/name fields
        if isinstance(values, dict):
            for k, v in list(values.items()):
                if isinstance(v, str) and v == "":
                    values[k] = None
        return values


class UploadedSourceOut(BaseModel):
    """
    Generic response describing a single uploaded input source, regardless
    of its underlying file type.

    Only the fields a given file type actually produces are populated —
    e.g. a CSV upload fills ``columns``/``row_count``/``preview``, while a
    plain-text or zipped upload (once wired up) may only populate
    ``metadata``. This is the one schema the upload endpoint returns for
    every file type so the frontend doesn't need type-specific response
    handling.
    """
    source_id:  str
    file_name:  str
    file_type:  str                         = Field(..., description="Lowercase extension the file was uploaded as, e.g. 'csv'.")
    row_count:  Optional[int]                = Field(None, description="Row count, for tabular file types.")
    columns:    Optional[List[str]]          = Field(None, description="Column names, for tabular file types.")
    preview:    Optional[List[Dict[str, Any]]] = Field(None, description="Small sample of parsed rows, for tabular file types.")
    metadata:   Optional[Dict[str, Any]]     = Field(None, description="Free-form metadata for non-tabular file types.")

# ——————————————————————————————————————————————————————————————
# File API Request Model
class FileRequest(BaseModel):
    """Represents a request to process a list of files, including the file references."""
    file: List[File] = Field(..., description="The file reference to be processed")

class RenameFileRequest(BaseModel):
    fileName: str

# ——————————————————————————————————————————————————————————————
# File API Response Model
class FileResponse(BaseModel):
    """Represents the response after processing a file, including status and any output references."""
    success: bool = Field(..., description="Indicates whether the file was processed successfully")
    outputFiles: Optional[Dict[str, File]] = Field(None, description="Optional dictionary of output files generated from processing, keyed by identifier")

class FilesResponse(BaseModel):
    success: bool = Field(..., description="Indicates whether the file was processed successfully")
    files: list[File] = []

# ——————————————————————————————————————————————————————————————
# File Processing Issue Model
class FileProcessingIssue(BaseModel):
    """
    File Processing Issue Model
    --------------------------

    **Description:**

        Represents a file that failed to process, including the file URL, name,
        type of issue encountered, and error message.

    """
    #: URL of the file that couldn't be processed
    fileUrl: AnyUrl = Field(..., description="URL of the file that couldn't be processed")
    #: Name of the file that couldn't be processed
    fileName: str = Field(..., description="Name of the file that couldn't be processed")
    #: Issue encountered during file processing
    issue: str = Field(..., description="Type of issue encountered during file processing (e.g., 'unreadable', 'unsupported format')")
    #: Detailed error message
    message: Optional[str] = Field(None, description="Detailed error message about the processing issue")
    
# —————————————————————————————————————————————————————————————
# Exceptions

class FileRecordNotFound(Exception):
    """Raised when a file_url has no row owned by the given user."""


class FileInUseError(Exception):
    """Raised when trying to delete a file that's attached to a workspace/notebook."""
