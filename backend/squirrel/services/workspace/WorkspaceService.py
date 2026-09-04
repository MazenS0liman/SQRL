#!/usr/bin/python
"""
Workspace Service
=================

Owns everything related to a **workspace**: its metadata (name, lifecycle
status, data type, input sources) *and* the PostgreSQL writes
for every pipeline run (preprocessing / model-building) executed against it.

A workspace is keyed by ``workspace_id`` — that's its primary key, created
once up front when the user names a new workspace, and referenced by every
run recorded against it afterwards. Every public method on this service
that takes a workspace reference names the parameter ``workspace_id`` for
consistency with the route layer and the database schema below.

Two tables back this service:

``workspaces``
    One row per workspace: its name, lifecycle status, data type
    ("structured" | "image" | "text" | "audio"), the list of
    input sources (uploaded files and/or connected data-connector
    references), the owning user id, and the target column chosen for
    modelling.

``workspace_runs``
    One row per pipeline run (``preprocessing`` or ``model_building``)
    executed for a workspace. Captures S3 URLs for every input file
    consumed, S3 URLs for every output artifact (CSVs, joblib models)
    stored in MinIO, the agent's structured JSON summary, and LLM
    token / timing metadata, plus the owning user id.

Lifecycle
---------

.. code-block::

    created -> uploaded -> preprocessing -> modeling -> completed
                                                      -> failed (any stage)

Schema
------

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id        TEXT            PRIMARY KEY,
        owner_user_id       TEXT,
        name                TEXT            NOT NULL,
        status              TEXT            NOT NULL DEFAULT 'created',
        data_type           TEXT            NOT NULL DEFAULT 'structured',
        target_column       TEXT,
        input_sources       JSONB           NOT NULL DEFAULT '[]',
        error               TEXT,
        created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS workspace_runs (
        id                  BIGSERIAL       PRIMARY KEY,
        workspace_id        TEXT            NOT NULL REFERENCES workspaces(workspace_id),
        owner_user_id       TEXT,
        agent_type          TEXT            NOT NULL,   -- 'preprocessing' | 'model_building'
        input_file_urls     TEXT[]          DEFAULT '{}',
        output_file_urls    TEXT[]          DEFAULT '{}',
        agent_summary       JSONB           DEFAULT '{}',
        prompt_tokens       INT             DEFAULT 0,
        completion_tokens   INT             DEFAULT 0,
        total_tokens        INT             DEFAULT 0,
        response_time_ms    INT             DEFAULT 0,
        created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    );

Input sources
-------------

Every workspace holds its data exclusively as a list of input sources.
Each entry in ``input_sources`` is a small JSON object. The shape is the
same regardless of file type — only ``columns``/``row_count`` are
populated when the underlying source is tabular::

    {
        "source_id":   "src_ab12cd34",
        "kind":        "upload" | "connector",
        "name":        "churn_customers.csv",
        "file_type":   "csv",                                                           # kind == "upload"
        "file_url":    "s3://bucket/ws_.../uploads/src_ab12cd34_churn_customers.csv",   # kind == "upload"
        "columns":     ["customer_id", "age", ...],                                     # tabular uploads only
        "row_count":   1204,                                                            # tabular uploads only
        "connector_id": "conn_9f8e...",                                                 # kind == "connector"
    }

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import io
import json
import re
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

# Third-Party Libraries
import joblib
import pandas as pd

# Services
from squirrel.services.storage.database.PostgresService import PostgresService
from squirrel.services.storage.blob.MinIOService import MinIOService
from squirrel.services.data_connector.DataConnectorService import DataConnectorService
from squirrel.services.file.FileService import FileService

# Agents
from squirrel.modules.agents.preprocessor.TabularDataProcessorAgent import TabularDataProcessorAgent

# Constants
from squirrel.constants.workspace import DataType, SourceKind, WorkspaceStatus

# Exceptions
from squirrel.schemas.error import WorkspaceNotFoundError, SourceNotFoundError

# Logging
from loguru import logger


# ——————————————————————————————————————————————————————————————
# Exceptions specific to this service


class DuplicateColumnError(Exception):
    """
    Raised when two or more input sources share a non-target column name in
    a way that can't be resolved into an unambiguous merge (see
    ``TabularDataProcessorAgent.merge_sources``).
    """

    def __init__(self, columns: List[str]):
        self.columns = columns
        super().__init__(
            f"Columns {columns} appear in more than one input source and "
            "can't be merged unambiguously."
        )


# ——————————————————————————————————————————————————————————————
# WorkspaceService


class WorkspaceService:
    """
    Coordinate workspace bookkeeping and artifact persistence for the
    preprocessing and model-building pipelines.

    **Responsibilities:**

    1. Create / list / fetch / rename / delete workspaces and track their
       lifecycle status and data type.
    2. Manage input sources per workspace: uploaded files of any supported
       type (stored in MinIO), or references to an existing data connector.
    3. Upload processed DataFrames and fitted models to MinIO.
    4. Insert a workspace-run row in PostgreSQL recording the S3 URLs,
       agent summary, and LLM metadata for each pipeline run.
    5. Expose retrieval helpers so downstream routes can fetch workspace
       and run history.
    6. Persist the fitted preprocessing pipeline (plan + execution report,
       including every strategy's data-dependent fitted parameters) as its
       own MinIO artifact, and replay it — together with a saved model —
       against new data via :meth:`predict`.

    :param db: An open :class:`PostgresService` instance.
    :type db: Optional[PostgresService]
    :param minio: An open :class:`MinIOService` instance.
    :type minio: Optional[MinIOService]
    """

    _WORKSPACES_TABLE = "workspaces"
    _RUNS_TABLE        = "workspace_runs"

    # MinIO key prefixes — keeps the bucket organised by pipeline type.
    _PREFIX_UPLOADS       = "uploads"
    _PREFIX_PREPROCESSED  = "preprocessed"
    _PREFIX_MODELS        = "models"
    _PREFIX_PIPELINES     = "pipelines"

    def __init__(
        self,
        db:    Optional[PostgresService] = None,
        minio: Optional[MinIOService]    = None,
        connector: Optional[DataConnectorService] = None
    ) -> None:
        self._db    = db    or PostgresService()
        self._minio = minio or MinIOService()
        self._file_service = FileService()
        self._connector = connector or DataConnectorService()
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Table bootstrap

    def _ensure_tables(self) -> None:
        """
        Create the ``workspaces`` and ``workspace_runs`` tables when they
        don't yet exist.
        """
        try:
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._WORKSPACES_TABLE} (
                    workspace_id        TEXT            PRIMARY KEY,
                    owner_user_id       TEXT,
                    name                TEXT            NOT NULL,
                    status              TEXT            NOT NULL DEFAULT 'created',
                    data_type           TEXT            NOT NULL DEFAULT 'structured',
                    target_column       TEXT,
                    input_sources       JSONB           NOT NULL DEFAULT '[]'::jsonb,
                    error               TEXT,
                    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
                );
                """,
                params={},
                fetch=False,
            )

            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._RUNS_TABLE} (
                    id                  BIGSERIAL       PRIMARY KEY,
                    workspace_id        TEXT            NOT NULL REFERENCES {self._WORKSPACES_TABLE}(workspace_id),
                    owner_user_id       TEXT,
                    agent_type          TEXT            NOT NULL,
                    input_file_urls     TEXT[]          DEFAULT '{{}}',
                    output_file_urls    TEXT[]          DEFAULT '{{}}',
                    agent_summary       JSONB           DEFAULT '{{}}',
                    prompt_tokens       INT             DEFAULT 0,
                    completion_tokens   INT             DEFAULT 0,
                    total_tokens        INT             DEFAULT 0,
                    response_time_ms    INT             DEFAULT 0,
                    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
                );
                """,
                params={},
                fetch=False,
            )
            self._db.execute(
                f"ALTER TABLE {self._WORKSPACES_TABLE} ADD COLUMN IF NOT EXISTS owner_user_id TEXT",
                params={},
                fetch=False,
            )
            self._db.execute(
                f"ALTER TABLE {self._RUNS_TABLE} ADD COLUMN IF NOT EXISTS owner_user_id TEXT",
                params={},
                fetch=False,
            )
            logger.info("workspaces / workspace_runs tables ready.")
        except Exception:
            logger.exception("Failed to ensure workspace tables.")

    @staticmethod
    def _workspace_filters(workspace_id: str, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
        filters: Dict[str, Any] = {"workspace_id": workspace_id}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        return filters

    # ------------------------------------------------------------------
    # Workspace CRUD

    def create_workspace(
        self,
        name: str,
        data_type: str = DataType.STRUCTURED,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new, empty workspace.

        :param name: User-supplied display name (e.g. "Churn model v1").
        :type name: str

        :param data_type: One of :class:`DataType`. Defaults to
            ``"structured"`` (tabular CSV pipeline).
        :type data_type: str

        :return: The newly created workspace row, keyed by ``workspace_id``.
        :rtype: dict
        """
        if data_type not in DataType.ALL:
            raise ValueError(f"Unknown data_type '{data_type}'.")

        workspace_id = f"ws_{uuid.uuid4().hex}"

        result = self._db.insert(
            {
                "sql": f"""
                    INSERT INTO {self._WORKSPACES_TABLE} (
                            workspace_id, owner_user_id, name, status, data_type, input_sources, created_at, updated_at
                    ) VALUES (
                            %(workspace_id)s, %(owner_user_id)s, %(name)s, %(status)s, %(data_type)s, %(input_sources)s::jsonb, %(now)s, %(now)s
                    )
                    RETURNING workspace_id, name, status, data_type, target_column,
                              input_sources, error, created_at, updated_at
                """,
                "params": {
                    "workspace_id": workspace_id,
                        "owner_user_id": owner_user_id,
                    "name": name,
                    "status": WorkspaceStatus.CREATED,
                    "data_type": data_type,
                    "input_sources": json.dumps([]),
                    "now": datetime.now(timezone.utc),
                },
            }
        )

        if not result or not isinstance(result, list) or not result[0].get("workspace_id"):
            raise RuntimeError("Failed to create workspace row.")

        logger.info("Created workspace '{}' (workspace_id={}, data_type={})", name, workspace_id, data_type)
        return result[0]

    def list_workspaces(
        self,
        limit: int = 200,
        owner_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return all workspaces, newest first.
        """
        filters: Dict[str, Any] = {}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        result = self._db.retrieve(self._WORKSPACES_TABLE, filters=filters)
        rows = result.get("rows", []) if result else []
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    def get_workspace(
        self,
        workspace_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch a single workspace row by ``workspace_id``, or raise ``WorkspaceNotFoundError``.
        """
        result = self._db.retrieve(self._WORKSPACES_TABLE, filters=self._workspace_filters(workspace_id, owner_user_id))
        rows = result.get("rows", []) if result else []
        if not rows:
            raise WorkspaceNotFoundError(f"No workspace with workspace_id={workspace_id}")
        return rows[0]

    def rename_workspace(
        self,
        workspace_id: str,
        name: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Rename a workspace and return the updated row.
        """
        name = name.strip()
        if not name:
            raise ValueError("Workspace name cannot be empty.")
        self._update_workspace(workspace_id, {"name": name}, owner_user_id=owner_user_id)
        return self.get_workspace(workspace_id, owner_user_id=owner_user_id)

    def delete_workspace(
        self,
        workspace_id: str,
        purge_artifacts: bool = True,
        owner_user_id: Optional[str] = None,
    ) -> None:
        """
        Permanently delete a workspace: its run history, then the workspace
        row itself.

        :param workspace_id: Workspace to delete.
        :type workspace_id: str

        :param purge_artifacts: If ``True``, best-effort delete every
            uploaded/output MinIO object referenced by the workspace's runs
            and input sources. Failures to purge an individual object are
            logged, not raised — a stray blob shouldn't block the delete.
        :type purge_artifacts: bool

        :raises WorkspaceNotFoundError: if the workspace doesn't exist.
        """
        workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)

        if purge_artifacts:
            self._purge_workspace_artifacts(workspace_id, workspace)

        try:
            run_delete = f"DELETE FROM {self._RUNS_TABLE} WHERE workspace_id = %(workspace_id)s"
            workspace_delete = f"DELETE FROM {self._WORKSPACES_TABLE} WHERE workspace_id = %(workspace_id)s"
            if owner_user_id:
                run_delete += " AND owner_user_id = %(owner_user_id)s"
                workspace_delete += " AND owner_user_id = %(owner_user_id)s"
            self._db.execute(
                run_delete,
                params={"workspace_id": workspace_id, "owner_user_id": owner_user_id},
                fetch=False,
            )
            self._db.execute(
                workspace_delete,
                params={"workspace_id": workspace_id, "owner_user_id": owner_user_id},
                fetch=False,
            )
            logger.info("Deleted workspace workspace_id={}", workspace_id)
        except Exception:
            logger.exception("Failed to delete workspace workspace_id={}", workspace_id)
            raise

    def _purge_workspace_artifacts(
        self,
        workspace_id: str,
        workspace: Dict[str, Any]
    ) -> None:
        urls: List[str] = []
        for src in workspace.get("input_sources") or []:
            if src.get("kind") == SourceKind.UPLOAD and src.get("file_url"):
                urls.append(src["file_url"])

        for run in self.get_records_by_workspace_id(workspace_id, None, limit=1000):
            urls.extend(run.get("output_file_urls") or [])

        for url in set(urls):
            try:
                self._minio.delete_file(url)
            except Exception:
                logger.warning("Could not purge artifact {} for workspace_id={}", url, workspace_id)

    def set_status(
        self,
        workspace_id: str,
        status: str,
        target_column: Optional[str] = None,
        error: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> None:
        """
        Update workspace status (and, optionally, the chosen target column
        and/or a recorded error message).
        """
        fields: Dict[str, Any] = {"status": status}
        if target_column is not None:
            fields["target_column"] = target_column
        if error is not None:
            fields["error"] = error
        self._update_workspace(workspace_id, fields, owner_user_id=owner_user_id)

    def _update_workspace(
        self,
        workspace_id: str,
        fields: Dict[str, Any],
        owner_user_id: Optional[str] = None,
    ) -> None:
        if not fields:
            return
        fields = dict(fields)
        jsonb_cols = {"input_sources"}
        set_parts = []
        params: Dict[str, Any] = {"workspace_id": workspace_id, "updated_at": datetime.now(timezone.utc)}
        for k, v in fields.items():
            if k in jsonb_cols:
                set_parts.append(f"{k} = %({k})s::jsonb")
                params[k] = json.dumps(v)
            else:
                set_parts.append(f"{k} = %({k})s")
                params[k] = v
        set_parts.append("updated_at = %(updated_at)s")
        set_clause = ", ".join(set_parts)
        try:
            where_clause = "workspace_id = %(workspace_id)s"
            if owner_user_id:
                where_clause += " AND owner_user_id = %(owner_user_id)s"
            self._db.execute(
                f"UPDATE {self._WORKSPACES_TABLE} SET {set_clause} WHERE {where_clause}",
                params={**params, **({"owner_user_id": owner_user_id} if owner_user_id else {})},
                fetch=False,
            )
        except Exception:
            logger.exception("Failed to update workspace workspace_id={}", workspace_id)
            raise

    # ------------------------------------------------------------------
    # Input sources — generic across file types

    def add_file_source(
        self,
        workspace_id: str,
        file_path: str,
        file_name: str,
        file_type: str,
        columns: Optional[List[str]] = None,
        row_count: Optional[int] = None,
        owner_user_id: Optional[str] = None
        ) -> Dict[str, Any]:
            """
            ...(docstring unchanged)...
            """
            workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
            source_id = f"src_{uuid.uuid4().hex[:12]}"
            object_name = f"{workspace_id}/{self._PREFIX_UPLOADS}/{source_id}_{file_name}"

            result = self._minio.upload_file(file_path=file_path, object_name=object_name)
            if result is None:
                raise RuntimeError("Failed to upload file to storage.")

            # Record the upload so it shows up on the Files page too, scoped to
            # the same owner and tagged with this workspace/source so the Files
            # page can deep-link back ("Open workspace →").
            self._file_service.record_upload(
                result,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id
            )

            source: Dict[str, Any] = {
                "source_id": source_id,
                "kind": SourceKind.UPLOAD,
                "name": file_name,
                "file_type": file_type,
                "file_url": str(result.fileUrl),
            }
            if columns is not None:
                source["columns"] = columns
                source["all_columns"] = columns
            if row_count is not None:
                source["row_count"] = row_count

            sources = list(workspace.get("input_sources") or [])
            sources.append(source)

            update_fields: Dict[str, Any] = {"input_sources": sources}
            if workspace.get("status") == WorkspaceStatus.CREATED:
                update_fields["status"] = WorkspaceStatus.UPLOADED

            self._update_workspace(workspace_id, update_fields, owner_user_id=owner_user_id)
            return source

    def preview_upload_source(
            self,
            workspace_id: str,
            source_id: str,
            limit: int = 10,
            owner_user_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Re-download and re-parse an already-uploaded CSV source, returning
            its full column list and a small preview. Lets the column editor
            re-open a source (e.g. after a page reload) without the frontend
            needing to keep the original upload-time parse result around.
            """
            workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
            source = next(
                (s for s in workspace.get("input_sources") or [] if s.get("source_id") == source_id),
                None,
            )
            if source is None:
                raise SourceNotFoundError(f"No source '{source_id}' on workspace {workspace_id}")
            if source.get("kind") != SourceKind.UPLOAD:
                raise ValueError("Column preview is only supported for uploaded file sources.")
            if source.get("file_type") != "csv":
                raise NotImplementedError(
                    f"No column-preview support yet for file_type='{source.get('file_type')}'."
                )

            df = self.load_csv(source["file_url"])
            all_columns = source.get("all_columns") or list(df.columns)
            preview_df = df.head(limit).where(pd.notnull(df.head(limit)), None)
            return {
                "table": source.get("name"),
                "columns": all_columns,
                "preview": preview_df.to_dict(orient="records"),
            }

    def update_source_columns(
            self,
            workspace_id: str,
            source_id: str,
            columns: Optional[List[str]],
            owner_user_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Narrow which columns of an already-attached source feed into
            preprocessing, without removing and re-attaching it.

            :param columns: subset of the source's full column list to keep.
                ``None`` (or the full list) resets to "use every column".
            """
            workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
            sources = list(workspace.get("input_sources") or [])
            idx = next((i for i, s in enumerate(sources) if s.get("source_id") == source_id), None)
            if idx is None:
                raise SourceNotFoundError(f"No source '{source_id}' on workspace {workspace_id}")

            source = dict(sources[idx])
            all_columns = source.get("all_columns") or source.get("columns")
            if not all_columns:
                raise ValueError("This source has no known column list to validate against.")

            if columns:
                unknown = [c for c in columns if c not in all_columns]
                if unknown:
                    raise ValueError(f"Unknown column(s) for this source: {unknown}")
                source["columns"] = list(columns) if len(columns) < len(all_columns) else list(all_columns)
            else:
                source["columns"] = list(all_columns)

            sources[idx] = source
            self._update_workspace(workspace_id, {"input_sources": sources}, owner_user_id=owner_user_id)
            return source

    def add_connector_source(
        self,
        workspace_id: str,
        connector_id: str,
        table_name: str,
        name: Optional[str] = None,
        columns: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Attach a single table (optionally restricted to a subset of its
        columns) from a connector as a workspace input source.

        :param columns: Column names to keep. ``None`` means "all columns",
            same as before.
        """
        self._connector.get_connection(connector_id)  # fail fast if missing
        query = self._connector.build_table_query(connector_id, table_name, columns)  # validates table + columns

        display_name = (name or table_name).strip()
        if not display_name:
            raise ValueError("Source name cannot be empty.")

        workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
        source_id = f"src_{uuid.uuid4().hex[:12]}"

        resolved_columns: Optional[List[str]] = columns
        if resolved_columns is None:
            try:
                preview_df = self._connector.preview_table(connector_id, table_name, limit=5)
                resolved_columns = list(preview_df.columns)
            except Exception:
                logger.warning(
                    "Could not derive preview columns for table '{}' on connector_id={}",
                    table_name, connector_id,
                )

        source: Dict[str, Any] = {
            "source_id": source_id,
            "kind": SourceKind.CONNECTOR,
            "name": display_name,
            "connector_id": connector_id,
            "table": table_name,
            "query": query,
        }
        if resolved_columns is not None:
            source["columns"] = resolved_columns

        sources = list(workspace.get("input_sources") or [])
        sources.append(source)

        update_fields: Dict[str, Any] = {"input_sources": sources}
        if workspace.get("status") == WorkspaceStatus.CREATED:
            update_fields["status"] = WorkspaceStatus.UPLOADED

        self._update_workspace(workspace_id, update_fields, owner_user_id=owner_user_id)
        return source


    def add_connector_sources(
        self,
        workspace_id: str,
        connector_id: str,
        tables: List[Dict[str, Any]],
        owner_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Attach multiple tables from the same connector in one call.

        :param tables: list of ``{"table": str, "columns": Optional[List[str]]}``.
            ``columns`` omitted or ``None`` means "all columns" for that table.
        :raises ValueError: if the list is empty, references an unknown table,
            or a table is already attached from this connector.
        """
        if not tables:
            raise ValueError("Select at least one table to attach.")

        table_names = [t["table"] for t in tables]
        known_tables = set(self._connector.list_tables(connector_id))
        unknown = [t for t in table_names if t not in known_tables]
        if unknown:
            raise ValueError(f"Unknown table(s) for this connector: {unknown}")

        workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
        already_attached = {
            s.get("table") for s in (workspace.get("input_sources") or [])
            if s.get("kind") == SourceKind.CONNECTOR and s.get("connector_id") == connector_id
        }
        duplicates = [t for t in table_names if t in already_attached]
        if duplicates:
            raise ValueError(f"Already attached: {duplicates}")

        created: List[Dict[str, Any]] = []
        for entry in tables:
            created.append(
                self.add_connector_source(
                    workspace_id, connector_id, entry["table"], columns=entry.get("columns"), owner_user_id=owner_user_id
                )
            )
        return created

    def remove_source(
        self,
        workspace_id: str,
        source_id: str,
        owner_user_id: Optional[str] = None,
    ) -> None:
        """
        Remove a single input source (upload or connector) from a workspace.
        """
        workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)
        sources = list(workspace.get("input_sources") or [])
        remaining = [s for s in sources if s.get("source_id") != source_id]
        if len(remaining) == len(sources):
            raise SourceNotFoundError(f"No source '{source_id}' on workspace {workspace_id}")

        removed = next(s for s in sources if s.get("source_id") == source_id)
        if removed.get("kind") == SourceKind.UPLOAD and removed.get("file_url"):
            try:
                self._minio.delete_file(removed["file_url"])
            except Exception:
                logger.warning("Could not delete removed source blob {}", removed["file_url"])

        update_fields: Dict[str, Any] = {"input_sources": remaining}
        if not remaining:
            update_fields["status"] = WorkspaceStatus.CREATED
        self._update_workspace(workspace_id, update_fields, owner_user_id=owner_user_id)

    # ------------------------------------------------------------------
    # Retrieve data

    def load_csv(
        self,
        file_url: str
    ) -> pd.DataFrame:
        """
        Download a single uploaded CSV source from MinIO and load it into a
        DataFrame. Used internally by :meth:`preview_upload_source` (and
        previously mirrored, less correctly, by inline logic in
        :meth:`load_source_dataframes`).

        .. note::
            Reads directly from ``retrieve_file``'s in-memory ``fileByte``
            rather than treating ``fileUrl`` as a local filesystem path.
            ``retrieve_file`` returns the *original* ``s3://...`` URL in
            ``fileUrl`` (its local temp download is already deleted before it
            returns) — passing that straight to ``pd.read_csv`` previously hit
            fsspec's own (uncredentialed, non-MinIO-aware) S3 handling and
            surfaced as ``FileNotFoundError: The specified bucket does not
            exist``, since it was never actually reading the file this method
            had just downloaded.
        """
        source_file = self._minio.retrieve_file(file_url)
        if source_file is None:
            raise RuntimeError(f"Could not retrieve source file: {file_url}")

        return pd.read_csv(io.BytesIO(source_file.fileByte))

    def fetch_data(
        self, 
        source: Dict[str, Any]
    ) -> pd.DataFrame:
        """
        Pull a DataFrame from a connected data source, scoped to the ``query``
        captured on the source at attach time (see :meth:`add_connector_source`)
        — never the connector's full underlying table/dataset. Delegates to
        :meth:`DataConnectorService.fetch_dataframe`, which still raises
        ``NotImplementedError`` for connector types with no reader wired up
        (Snowflake, BigQuery, Google Sheets today).
        """
        query = source.get("query")
        if not query:
            raise RuntimeError(
                f"Connector source '{source.get('source_id')}' has no query "
                "recorded — remove and re-attach it via add_connector_source."
            )
        return self._connector.fetch_dataframe(source["connector_id"], query)

    def _upload_loaders(self) -> Dict[str, Any]:
        return {
            "csv": self.load_csv,
        }

    def load_source_dataframes(
        self,
        sources: List[Dict[str, Any]]
    ) -> Dict[str, pd.DataFrame]:
        """
        Resolve every input source on a workspace into an in-memory
        DataFrame, keyed by ``source_id``.

        - ``kind == "upload"``: dispatched by ``file_type`` to the matching
          loader in :meth:`_upload_loaders` (only ``"csv"`` today).
        - ``kind == "connector"``: delegated to :meth:`fetch_data`. This is
          the extension point for wiring up the Data Connectors page's live
          sources (Postgres, BigQuery, warehouses, etc.) — today it raises
          ``NotImplementedError`` with a clear message so the route can
          surface a 501 rather than a silent failure.

        :raises RuntimeError: if any upload source can't be downloaded.
        :raises NotImplementedError: if a connector source, or an upload
            source whose ``file_type`` has no registered loader (e.g. a
            future "zip"/"txt" source), has no data-fetching implementation.
        """
        frames: Dict[str, pd.DataFrame] = {}
        loaders = self._upload_loaders()

        for source in sources:
            source_id = source["source_id"]
            if source.get("kind") == SourceKind.CONNECTOR:
                frames[source_id] = self.fetch_data(source)
                continue

            file_type = source.get("file_type", "csv")
            loader = loaders.get(file_type)
            if loader is None:
                raise NotImplementedError(
                    f"Source '{source_id}' has file_type='{file_type}', which has "
                    f"no registered loader yet. Supported today: {', '.join(loaders)}."
                )

            file_url = source.get("file_url")
            logger.info("Loading source '{}' (file_type='{}') from {}", source, file_type, file_url)
            if not file_url:
                raise RuntimeError(f"Source '{source_id}' has no file_url to load.")
            file = self._minio.retrieve_file(file_url)
            df = pd.read_csv(io.BytesIO(file.fileByte))

            # If the user narrowed this upload's columns via the column
            # editor, only keep the selected subset from here on.
            selected = source.get("columns")
            all_cols = source.get("all_columns")
            if selected and all_cols and len(selected) < len(all_cols):
                keep = [c for c in selected if c in df.columns]
                if keep:
                    df = df[keep]

            frames[source_id] = df

        return frames

    # ------------------------------------------------------------------
    # Save agent's output

    def save_run(
            self,
            workspace_id:       str,
            agent_type:         str,
            input_file_urls:    List[str],
            data:               Any,
            summary:            Dict,
            prompt_tokens:      int = 0,
            completion_tokens:  int = 0,
            total_tokens:       int = 0,
            response_time_ms:   int = 0,
            owner_user_id:      Optional[str] = None,
            plan:               Optional[Any] = None,
            execution_report:   Optional[Any] = None,
        ) -> Dict:
            """
            Persist one pipeline run's output artifacts + summary.

            :param plan: For ``agent_type == "preprocessing"`` only — the
                JSON plan string/dict returned by
                ``TabularDataProcessorAgent.plan()`` (or the third element
                of ``run()``/``run_multi()``'s return tuple). Combined with
                *execution_report* and persisted as a "fitted pipeline"
                artifact so :meth:`predict` can later replay this exact
                preprocessing against new data. Ignored for other
                ``agent_type`` values.
            :param execution_report: For ``agent_type == "preprocessing"``
                only — the JSON execution report string/dict returned by
                ``TabularDataProcessorAgent.execute()`` (or the fourth
                element of ``run()``/``run_multi()``'s return tuple). This
                is what actually carries the fitted parameters (means,
                bounds, encoding maps, fitted power-transform λ, one-hot
                dummy-column sets, ...) that :meth:`predict` needs. When
                omitted, no pipeline artifact is saved and this workspace's
                data won't be predictable on until a run supplies one.
            """
            output_urls: List[str] = []

            if agent_type == "preprocessing" and isinstance(data, pd.DataFrame):
                file_url = self._upload_dataframe(
                    df=data,
                    workspace_id=workspace_id,
                    label="processed",
                    owner_user_id=owner_user_id,
                )
                if file_url:
                    output_urls.append(file_url)
                else:
                    logger.warning(
                        "Could not upload processed CSV for workspace_id={}",
                        workspace_id
                    )

                # Persist the fitted pipeline (plan + execution report) so
                # predict() can replay this exact preprocessing on new rows
                # instead of re-fitting scalers/encoders from them.
                if execution_report is not None:
                    pipeline_url = self.save_pipeline_artifact(
                        workspace_id=workspace_id,
                        plan=plan or {},
                        execution_report=execution_report,
                        owner_user_id=owner_user_id,
                    )
                    if pipeline_url:
                        output_urls.append(pipeline_url)
                        summary = dict(summary or {})
                        summary["pipeline_artifact_url"] = pipeline_url
                    else:
                        logger.warning(
                            "Could not persist fitted pipeline artifact for workspace_id={}",
                            workspace_id
                        )

            elif agent_type == "model_building" and isinstance(data, Dict):
                for model_key, estimator in data.items():
                    model_url = self._upload_model(
                        estimator=estimator,
                        model_key=model_key,
                        workspace_id=workspace_id,
                        owner_user_id=owner_user_id,
                    )
                    if model_url:
                        output_urls.append(model_url)
                    else:
                        logger.warning(
                            "Could not upload model '{}' for workspace_id={}",
                            model_key,
                            workspace_id
                        )

            else:
                pass

            input_file_urls = [str(u) for u in (input_file_urls or [])]
            output_urls = [str(u) for u in output_urls]

            row_id = self._insert_run(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                agent_type=agent_type,
                input_file_urls=input_file_urls,
                output_file_urls=output_urls,
                agent_summary=summary,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=response_time_ms
            )

            return {
                "record_id": row_id,
                "output_file_urls": output_urls
            }

    # ------------------------------------------------------------------
    # Fitted-pipeline persistence & replay
    #
    # TabularDataProcessorAgent.run_multi() (and run()) already return the
    # (plan, execution_report) pair needed to replay training-time
    # preprocessing against new data via apply_fitted_pipeline() — but
    # until now nothing downstream of the /build route actually persisted
    # them. save_run() stores this pair as a JSON artifact in MinIO
    # whenever a preprocessing run supplies execution_report; the methods
    # below read it back and drive apply_fitted_pipeline() + the fitted
    # model for a single predict() call.

    def save_pipeline_artifact(
        self,
        workspace_id: str,
        plan: Any,
        execution_report: Any,
        owner_user_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Persist the preprocessing plan + execution report as one JSON
        artifact in MinIO.

        The execution report's per-step ``per_column`` blocks are exactly
        the fitted state (means, bounds, encoding maps, fitted
        power-transform λ, one-hot dummy-column sets, group aggregation
        stats, ...) that :meth:`predict` needs to replay this pipeline on
        new rows instead of re-fitting from them.

        :param plan: JSON plan string or dict.
        :param execution_report: JSON execution report string or dict.
        :return: The artifact's ``s3://`` URL, or ``None`` on failure.
        """
        try:
            plan_obj = plan if isinstance(plan, Dict) else json.loads(plan)
        except (TypeError, json.JSONDecodeError):
            plan_obj = {}

        try:
            execution_obj = (
                execution_report if isinstance(execution_report, Dict)
                else json.loads(execution_report)
            )
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "execution_report for workspace_id={} was not valid JSON; "
                "saving pipeline artifact with an empty execution_report.",
                workspace_id,
            )
            execution_obj = {}

        payload = json.dumps(
            {"plan": plan_obj, "execution_report": execution_obj},
            default=str,
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        object_name = f"{workspace_id}/{self._PREFIX_PIPELINES}/pipeline_{ts}.json"

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name

        try:
            result = self._minio.upload_file(file_path=tmp_path, object_name=object_name)
            if result is None:
                return None

            # Same reasoning as _upload_dataframe/_upload_model: this is an
            # artifact this workspace produced, so it should show up on the
            # Files page like everything else.
            self._file_service.record_upload(
                result,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return str(result.fileUrl)
        except Exception:
            logger.exception("Pipeline artifact upload to MinIO failed.")
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def load_pipeline_artifact(self, file_url: str) -> Dict[str, Any]:
        """
        Download and parse a pipeline artifact written by
        :meth:`save_pipeline_artifact`.

        :return: ``{"plan": {...}, "execution_report": {...}}``.
        :raises RuntimeError: if the artifact can't be downloaded.
        :raises ValueError: if the artifact isn't valid JSON.
        """
        raw = self.download_output_file(file_url)
        if raw is None:
            raise RuntimeError(f"Could not retrieve pipeline artifact: {file_url}")
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Pipeline artifact at {file_url} is not valid JSON: {exc}") from exc

    def get_latest_pipeline_artifact_url(
        self,
        workspace_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Return the ``pipeline_artifact_url`` recorded in the ``agent_summary``
        of the most recent ``preprocessing`` run for this workspace, or
        ``None`` if the workspace has never been preprocessed with pipeline
        persistence enabled (e.g. runs saved before this feature existed).
        """
        runs = self.get_records_by_workspace_id(
            workspace_id, agent_type="preprocessing", limit=1, owner_user_id=owner_user_id
        )
        if not runs:
            return None
        return (runs[0].get("agent_summary") or {}).get("pipeline_artifact_url")

    @staticmethod
    def _model_key_from_url(file_url: str) -> str:
        """
        Recover ``model_key`` from a stored model artifact URL named
        ``{model_key}_{timestamp}.joblib`` by :meth:`_upload_model`.
        Mirrors the identical helper in the workspace route.
        """
        filename = file_url.rsplit("/", 1)[-1]
        stem = filename[: -len(".joblib")] if filename.endswith(".joblib") else filename
        match = re.match(r"^(.*)_\d{8}T\d{6}$", stem)
        return match.group(1) if match else stem

    def get_latest_model_url(
        self,
        workspace_id: str,
        model_key: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> str:
        """
        Resolve a fitted model's MinIO URL from the most recent
        ``model_building`` run on this workspace.

        :param model_key: Which fitted model to use. ``None`` falls back to
            that run's recorded ``best_model``, then to the first output URL
            if neither is available.
        :raises RuntimeError: if there's no completed model-building run, or
            *model_key* doesn't match any of its fitted models.
        """
        runs = self.get_records_by_workspace_id(
            workspace_id, agent_type="model_building", limit=1, owner_user_id=owner_user_id
        )
        if not runs:
            raise RuntimeError(f"Workspace {workspace_id} has no completed model-building run.")

        run = runs[0]
        urls: List[str] = list(run.get("output_file_urls") or [])
        if not urls:
            raise RuntimeError(f"Model-building run for workspace {workspace_id} has no artifacts.")

        resolved_key = model_key or (run.get("agent_summary") or {}).get("best_model")
        if resolved_key:
            for url in urls:
                if self._model_key_from_url(url) == resolved_key:
                    return url
            raise RuntimeError(
                f"Model '{resolved_key}' not found among this workspace's fitted models."
            )
        return urls[0]

    def _load_model(self, file_url: str) -> Any:
        """Download and unpickle a joblib model artifact from MinIO."""
        raw = self.download_output_file(file_url)
        if raw is None:
            raise RuntimeError(f"Could not retrieve model artifact: {file_url}")
        return joblib.load(io.BytesIO(raw))

    def predict(
        self,
        workspace_id: str,
        new_data: pd.DataFrame,
        model_key: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Inference-time counterpart to ``/build``: replay this workspace's
        saved preprocessing pipeline on *new_data* — reusing training-time
        fitted parameters via
        ``TabularDataProcessorAgent.apply_fitted_pipeline`` rather than
        re-fitting scalers/encoders from the new rows — then run the chosen
        fitted model on the transformed result.

        :param new_data: Raw new rows, shaped like the original input
            source(s) *before* preprocessing (i.e. exactly what you'd have
            uploaded/attached). The target column, if present, is dropped
            before prediction.
        :param model_key: Which fitted model to use. ``None`` uses the
            workspace's recorded ``best_model``.
        :return: ``{"model_key", "predictions", ["probabilities", "classes"]}``.
        :raises RuntimeError: no saved pipeline, no fitted model, or
            *model_key* can't be resolved.
        """
        workspace = self.get_workspace(workspace_id, owner_user_id=owner_user_id)

        pipeline_url = self.get_latest_pipeline_artifact_url(workspace_id, owner_user_id=owner_user_id)
        if not pipeline_url:
            raise RuntimeError(
                f"Workspace {workspace_id} has no saved preprocessing pipeline. "
                "Run /build at least once before predicting."
            )
        pipeline_artifact = self.load_pipeline_artifact(pipeline_url)
        execution_report = pipeline_artifact.get("execution_report") or {}

        trained_dtypes = (execution_report.get("dataset_before") or {}).get("dtypes") or {}
        new_data = new_data.copy()
        for col, dtype_str in trained_dtypes.items():
            if col not in new_data.columns:
                continue
            dtype_str = str(dtype_str)
            try:
                if "datetime" in dtype_str:
                    new_data[col] = pd.to_datetime(new_data[col], errors="coerce")
                elif "int" in dtype_str or "float" in dtype_str:
                    new_data[col] = pd.to_numeric(new_data[col], errors="coerce")
            except Exception:
                logger.warning(
                    "Could not coerce column '%s' to trained dtype '%s' for workspace_id=%s",
                    col, dtype_str, workspace_id,
                )

        transformed = TabularDataProcessorAgent().apply_fitted_pipeline(new_data, execution_report)

        target_column = workspace.get("target_column")
        if target_column and target_column in transformed.columns:
            transformed = transformed.drop(columns=[target_column])

        non_numeric_cols = transformed.select_dtypes(exclude=["number", "bool"]).columns.tolist()
        if non_numeric_cols:
            logger.warning(
                "Dropping non-numeric columns before prediction for workspace_id=%s "
                "(preprocessing replay didn't remove these): %s",
                workspace_id, non_numeric_cols,
            )
            transformed = transformed.drop(columns=non_numeric_cols)

        model_url = self.get_latest_model_url(workspace_id, model_key=model_key, owner_user_id=owner_user_id)
        estimator = self._load_model(model_url)
        predictions = estimator.predict(transformed)
        result: Dict[str, Any] = {
            "model_key": model_key or self._model_key_from_url(model_url),
            "predictions": predictions.tolist() if hasattr(predictions, "tolist") else list(predictions),
        }

        if hasattr(estimator, "predict_proba"):
            try:
                proba = estimator.predict_proba(transformed)
                result["probabilities"] = proba.tolist()
                if hasattr(estimator, "classes_"):
                    result["classes"] = [str(c) for c in estimator.classes_]
            except Exception:
                logger.warning(
                    "predict_proba failed for workspace_id={} model_key={}",
                    workspace_id, result["model_key"],
                )

        return result

    # ------------------------------------------------------------------
    # Retrieval

    def get_record(
        self,
        record_id: int,
        owner_user_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Fetch a single workspace-run row by primary key.
        """
        filters: Dict[str, Any] = {"id": record_id}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        result = self._db.retrieve(self._RUNS_TABLE, filters=filters)
        rows = result.get("rows", []) if result else []
        return rows[0] if rows else None

    def get_records_by_workspace_id(
        self,
        workspace_id: str,
        agent_type: Optional[str] = None,
        limit: int = 100,
        owner_user_id: Optional[str] = None,
    ) -> List[dict]:
        """
        Return workspace-run rows for a workspace, newest first.
        """
        filters: dict = {"workspace_id": workspace_id}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        if agent_type:
            filters["agent_type"] = agent_type

        result = self._db.retrieve(self._RUNS_TABLE, filters=filters)
        rows = result.get("rows", []) if result else []
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    def download_output_file(
        self,
        s3_url: str
    ) -> Optional[bytes]:
        """
        Download an output artifact from MinIO and return its raw bytes.
        """
        file_obj = self._minio.retrieve_file(s3_url)
        if file_obj is None:
            return None
        
        try:
            return file_obj.fileByte
        except Exception:
            logger.exception("Failed to read downloaded artifact: {}", file_obj.fileUrl)
            return None

    # ------------------------------------------------------------------
    # Private helpers — MinIO uploads

    def _upload_dataframe(
        self,
        df: pd.DataFrame,
        workspace_id: str,
        label: str = "processed",
        owner_user_id: Optional[str] = None,
    ) -> Optional[str]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        object_name = f"{workspace_id}/{self._PREFIX_PREPROCESSED}/{label}_{ts}.csv"

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as tmp:
            df.to_csv(tmp, index=False)
            tmp_path = tmp.name

        try:
            result = self._minio.upload_file(file_path=tmp_path, object_name=object_name)
            if result is None:
                return None

            # Agent-generated output is still "a file this user's workspace
            # produced" — record it the same way any other upload is, so
            # Files-page visibility and ownership stay consistent across
            # every artifact type instead of only the ones the user chose.
            self._file_service.record_upload(
                result,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return str(result.fileUrl)
        except Exception:
            logger.exception("DataFrame upload to MinIO failed.")
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)


    def _upload_model(
        self,
        estimator: object,
        model_key: str,
        workspace_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Optional[str]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        object_name = f"{workspace_id}/{self._PREFIX_MODELS}/{model_key}_{ts}.joblib"

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            joblib.dump(estimator, tmp_path)
            result = self._minio.upload_file(file_path=tmp_path, object_name=object_name)
            if result is None:
                return None

            self._file_service.record_upload(
                result,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return str(result.fileUrl)
        except Exception:
            logger.exception("Model '{}' upload to MinIO failed.", model_key)
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Private helpers — PostgreSQL writes

    def _insert_run(
        self,
        workspace_id:    str,
        owner_user_id:   Optional[str],
        agent_type:      str,
        input_file_urls: List[str],
        output_file_urls: List[str],
        agent_summary:   dict,
        prompt_tokens:   int,
        completion_tokens: int,
        total_tokens:    int,
        response_time_ms: int
    ) -> Optional[int]:
        try:
            result = self._db.insert(
                {
                    "sql": f"""
                        INSERT INTO {self._RUNS_TABLE} (
                            workspace_id,
                            owner_user_id,
                            agent_type,
                            input_file_urls,
                            output_file_urls,
                            agent_summary,
                            prompt_tokens,
                            completion_tokens,
                            total_tokens,
                            response_time_ms,
                            created_at
                        ) VALUES (
                            %(workspace_id)s,
                            %(owner_user_id)s,
                            %(agent_type)s,
                            %(input_file_urls)s,
                            %(output_file_urls)s,
                            %(agent_summary)s::jsonb,
                            %(prompt_tokens)s,
                            %(completion_tokens)s,
                            %(total_tokens)s,
                            %(response_time_ms)s,
                            %(created_at)s
                        )
                        RETURNING id
                    """,
                    "params": {
                        "workspace_id":      workspace_id,
                        "owner_user_id":     owner_user_id,
                        "agent_type":        agent_type,
                        "input_file_urls":   [str(u) for u in (input_file_urls or [])],
                        "output_file_urls":  [str(u) for u in (output_file_urls or [])],
                        "agent_summary":     json.dumps(agent_summary),
                        "prompt_tokens":     prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens":      total_tokens,
                        "response_time_ms":  response_time_ms,
                        "created_at":        datetime.now(timezone.utc),
                    },
                }
            )

            if result and isinstance(result, list) and result[0].get("id"):
                run_id = result[0]["id"]
                logger.info(
                    "Persisted workspace_run id={} agent_type={} workspace_id={}",
                    run_id, agent_type, workspace_id,
                )
                return run_id

            logger.error("Insert returned no id for workspace_id={}", workspace_id)
            return None

        except Exception:
            logger.exception("Failed to insert workspace_run row for workspace_id={}", workspace_id)
            return None