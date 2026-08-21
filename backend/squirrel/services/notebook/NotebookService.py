#!/usr/bin/python
"""
Notebook Service
================

Overview
--------

Owns the Notebook feature end-to-end:

1. **CRUD** — create / list / get / delete notebooks, persisted in
   lightweight admin tables inside the main application Postgres database,
   created lazily on first use so no separate migration is required.
2. **Data source binding** — a notebook owns one or more data sources, either
   an uploaded CSV (freshly uploaded, or reused from an existing Files-page
   upload) or a table on an existing connector managed by
   :class:`DataConnectorService`.
3. **Cell execution** — every cell (a full EDA sweep, a dashboard, a specific
   question, or a markdown note) is answered by
   :class:`TabularDataExploratoryAgent`.

Note on data manipulation
--------------------------
This service does NOT mutate the underlying tables on the user's behalf.
A user can still ask a manipulation-flavored question (e.g. "show me orders
with outliers removed" or "give me a deduplicated list of customers") — that
flows through the normal question pipeline, and when
``TabularDataExploratoryAgent.classify_question_intent`` decides the request
wants a table rather than a chart, it's answered by creating a named,
non-destructive SQL VIEW (see ``_run_question`` / ``process_view_request``)
that the user can reference in later questions. There is no separate
in-place "transform" step that rewrites a data source's live table.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import re
import io
import os
import uuid
import json
import base64
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Services
from squirrel.services.storage.database.PostgresService import PostgresService
from squirrel.services.storage.blob.MinIOService import MinIOService
from squirrel.services.data_connector.DataConnectorService import (
    DataConnectorService
)
from squirrel.services.file.FileService import FileService

# Agents
from squirrel.modules.agents import TabularDataExploratoryAgent
from squirrel.schemas.eda import Hypothesis

# Schema
from squirrel.schemas.notebook import (
    Notebook,
    NotebookDataSource,
    NotebookCell,
    NotebookChart,
    DashboardLayout,
    DashboardVersion,
    DataSourcePreview,
)

# Errors
from squirrel.schemas.error import (
    CellNotFoundError,
    NotebookNotFoundError,
    NotebookNotReadyError,
    DataSourceNotFoundError
)

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Notebook Service Class

class NotebookService:
    """
    :param connection_string: Main app DB connection string. Defaults to
        ``$DATABASE_URL``.
    :param minio: Optional pre-constructed :class:`MinIOService`.
    """

    _DASHBOARD_TILE_COUNT = 8

    def __init__(
        self,
        connection_string: Optional[str] = None,
        minio: Optional[MinIOService] = None,
    ) -> None:
        self.connection_string = connection_string or os.environ["DATABASE_URL"]
        self.postgres = PostgresService(connection_string=self.connection_string)
        self.minio = minio or MinIOService()
        self.file_service = FileService()
        
        # Ensure the notebook tables exist on first use. Idempotent.
        self._ensure_tables()

    # ──────────────────────────────────────────────────────────────────────
    # Bootstrap
    # ──────────────────────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        """
        Create the admin tables on first use. Idempotent.
        """
        self.postgres.query(
            """
            CREATE TABLE IF NOT EXISTS sqrl_notebooks (
                id            TEXT PRIMARY KEY,
                owner_user_id TEXT,
                name          TEXT NOT NULL,
                description   TEXT,
                status        TEXT NOT NULL DEFAULT 'empty',
                data_source   JSONB,
                schema_cache  JSONB,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        self.postgres.query(
            """
            CREATE TABLE IF NOT EXISTS sqrl_notebook_cells (
                id                 TEXT PRIMARY KEY,
                notebook_id        TEXT NOT NULL
                                REFERENCES sqrl_notebooks(id)
                                ON DELETE CASCADE,
                type               TEXT NOT NULL,
                query              TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL DEFAULT 'complete',
                reply              TEXT NOT NULL DEFAULT '',
                charts             JSONB NOT NULL DEFAULT '[]'::jsonb,
                dashboard_layout  JSONB,
                error              TEXT,
                data_source_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                response_time_ms   INTEGER NOT NULL DEFAULT 0,
                prompt_tokens      INTEGER NOT NULL DEFAULT 0,
                completion_tokens  INTEGER NOT NULL DEFAULT 0,
                order_index        INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.postgres.query(
            """
            CREATE TABLE IF NOT EXISTS sqrl_notebook_data_sources (
                id                  TEXT PRIMARY KEY,
                notebook_id         TEXT NOT NULL REFERENCES sqrl_notebooks(id) ON DELETE CASCADE,   
                kind                TEXT NOT NULL,
                table_name          TEXT NOT NULL,
                connector_id        TEXT,
                connector_type      TEXT,
                source_file_url     TEXT,
                original_filename   TEXT,
                row_count           INTEGER,
                column_count        INTEGER,
                label               TEXT,
                raw_schema          JSONB,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        self.postgres.query(
            """
            CREATE TABLE IF NOT EXISTS sqrl_notebook_dashboard_versions (
                id              TEXT PRIMARY KEY,
                cell_id         TEXT NOT NULL REFERENCES sqrl_notebook_cells(id) ON DELETE CASCADE,
                notebook_id     TEXT NOT NULL REFERENCES sqrl_notebooks(id) ON DELETE CASCADE,
                version_number  INTEGER NOT NULL,
                reply           TEXT NOT NULL DEFAULT '',
                charts          JSONB,
                dashboard_layout JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        self.postgres.query(
            """
            ALTER TABLE sqrl_notebook_cells
            ADD COLUMN IF NOT EXISTS dashboard_layout JSONB
            """
        )

        self.postgres.query(
            """
            ALTER TABLE sqrl_notebook_cells
            ADD COLUMN IF NOT EXISTS order_index INTEGER NOT NULL DEFAULT 0
            """
        )

        # Update existing cells to have sequential order_index based on created_at
        self.postgres.query(
            """
            WITH ordered_cells AS (
                SELECT id,
                       ROW_NUMBER() OVER (PARTITION BY notebook_id ORDER BY created_at) - 1 as new_order
                FROM sqrl_notebook_cells
            )
            UPDATE sqrl_notebook_cells
            SET order_index = ordered_cells.new_order
            FROM ordered_cells
            WHERE sqrl_notebook_cells.id = ordered_cells.id
            """
        )
        self.postgres.query(
            """
            ALTER TABLE sqrl_notebook_cells
            ADD COLUMN IF NOT EXISTS data_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )

    # ──────────────────────────────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────────────────────────────

    def create_notebook(
        self, 
        name: str, 
        description: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        notebook_id = str(uuid.uuid4())
        self.postgres.query(
            "INSERT INTO sqrl_notebooks (id, owner_user_id, name, description, status) "
            "VALUES (%(id)s, %(owner_user_id)s, %(name)s, %(description)s, 'empty')",
            params={
                "id": notebook_id,
                "owner_user_id": owner_user_id,
                "name": name,
                "description": description,
            },
        )
        return self.get_notebook(notebook_id, owner_user_id=owner_user_id)

    def list_notebooks(
        self, 
        owner_user_id: Optional[str] = None,
    ) -> List[Notebook]:
        where_clause = ""
        params: Dict[str, Any] = {}
        if owner_user_id:
            where_clause = "WHERE n.owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self.postgres.query(
            "SELECT n.*, "
            "(SELECT COUNT(*) FROM sqrl_notebook_cells c WHERE c.notebook_id = n.id) AS cell_count "
            f"FROM sqrl_notebooks n {where_clause} ORDER BY n.updated_at DESC",
            params=params,
        )
        rows = (result or {}).get("rows", [])
        notebooks = []
        for row in rows:
            data_sources = self._get_data_sources(row["id"])
            notebooks.append(self._row_to_notebook(row, data_sources))
        return notebooks

    def get_notebook(
        self, 
        notebook_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        where_clause = "n.id = %(notebook_id)s"
        params: Dict[str, Any] = {"notebook_id": notebook_id}
        if owner_user_id:
            where_clause += " AND n.owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self.postgres.query(
            "SELECT n.*, "
            "(SELECT COUNT(*) FROM sqrl_notebook_cells c WHERE c.notebook_id = n.id) AS cell_count "
            f"FROM sqrl_notebooks n WHERE {where_clause}",
            params=params,
        )
        rows = (result or {}).get("rows", [])
        if not rows:
            raise NotebookNotFoundError(f"Notebook '{notebook_id}' not found.")
        data_sources = self._get_data_sources(notebook_id)
        return self._row_to_notebook(rows[0], data_sources)

    def delete_notebook(
        self, 
        notebook_id: str,
        owner_user_id: Optional[str] = None,
    ) -> None:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing
        self.postgres.query(
            "DELETE FROM sqrl_notebooks WHERE id = %(notebook_id)s"
            + (" AND owner_user_id = %(owner_user_id)s" if owner_user_id else ""),
            params={"notebook_id": notebook_id, "owner_user_id": owner_user_id},
        )

    def list_cells(
        self,
        notebook_id: str,
        owner_user_id: Optional[str] = None,
    ) -> List[NotebookCell]:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing
        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_cells WHERE notebook_id = %(notebook_id)s ORDER BY order_index ASC, created_at ASC",
            params={"notebook_id": notebook_id},
        )
        rows = (result or {}).get("rows", [])
        return [self._row_to_cell(row) for row in rows]

    # ──────────────────────────────────────────────────────────────────────
    # Data source binding
    # ──────────────────────────────────────────────────────────────────────

    def _get_data_sources(self, notebook_id: str) -> List[NotebookDataSource]:
        """Load every data source attached to a notebook, in attach order."""
        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_data_sources WHERE notebook_id = %(notebook_id)s "
            "ORDER BY created_at ASC",
            params={"notebook_id": notebook_id},
        )
        rows = (result or {}).get("rows", [])
        sources = []
        for row in rows:
            raw_schema = row.get("raw_schema")
            sources.append(
                NotebookDataSource(
                    id=row["id"],
                    kind=row["kind"],
                    table_name=row["table_name"],
                    connector_id=row.get("connector_id"),
                    connector_type=row.get("connector_type"),
                    source_file_url=row.get("source_file_url"),
                    original_filename=row.get("original_filename"),
                    row_count=row.get("row_count"),
                    column_count=row.get("column_count"),
                    label=row.get("label"),
                )
            )
        return sources

    def _get_data_source(self, notebook_id: str, source_id: str, owner_user_id: Optional[str] = None) -> NotebookDataSource:
        """Fetch one data source, or raise ``DataSourceNotFoundError``."""
        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_data_sources "
            "WHERE id = %(source_id)s AND notebook_id = %(notebook_id)s"
            + (" AND EXISTS (SELECT 1 FROM sqrl_notebooks n WHERE n.id = %(notebook_id)s AND n.owner_user_id = %(owner_user_id)s)" if owner_user_id else ""),
            params={"source_id": source_id, "notebook_id": notebook_id, "owner_user_id": owner_user_id},
        )
        rows = (result or {}).get("rows", [])
        if not rows:
            raise DataSourceNotFoundError(
                f"Data source '{source_id}' not found in notebook '{notebook_id}'."
            )
        row = rows[0]
        return NotebookDataSource(
            id=row["id"],
            kind=row["kind"],
            table_name=row["table_name"],
            connector_id=row.get("connector_id"),
            connector_type=row.get("connector_type"),
            source_file_url=row.get("source_file_url"),
            original_filename=row.get("original_filename"),
            row_count=row.get("row_count"),
            column_count=row.get("column_count"),
            label=row.get("label"),
        )

    def _unique_label(
        self,
        notebook_id: str,
        candidate: str,
        exclude_source_id: Optional[str] = None,
    ) -> str:
        """
        Disambiguate *candidate* against every label already attached to this
        notebook, so `@mention` resolution in chat never has to guess between
        two same-named sources — "data.csv" colliding with an existing
        "data.csv" becomes "data.csv (2)", then "data.csv (3)", etc.

        ``exclude_source_id`` lets a rename check against every *other*
        source without tripping over its own current label.
        """
        candidate = (candidate or "").strip() or "source"
        result = self.postgres.query(
            "SELECT id, label FROM sqrl_notebook_data_sources WHERE notebook_id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        existing = {
            row["label"]
            for row in (result or {}).get("rows", [])
            if row.get("label") and row["id"] != exclude_source_id
        }
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate} ({n})" in existing:
            n += 1
        return f"{candidate} ({n})"

    def _drop_table_cascade(self, table_name: str) -> None:
        """
        Drop *table_name* (with CASCADE) ahead of a fresh load.

        Why this exists: ``add_csv_data_source`` / ``add_existing_file_data_source``
        derive ``table_name`` deterministically from ``notebook_id`` + the
        filename stem, so re-adding a file with the same name to the same
        notebook reproduces the same table name. That load then goes through
        ``PostgresService.load(..., if_exists="replace")``, which under the
        hood is ``pandas.DataFrame.to_sql`` — and pandas implements
        ``if_exists="replace"`` as a bare ``DROP TABLE`` with no ``CASCADE``.

        Meanwhile, a "list rows" / "deduplicate" style question against this
        same table can have created a non-destructive SQL VIEW on top of it
        (see ``_run_question`` / ``process_view_request`` in the module
        docstring). If such a view exists, the bare ``DROP TABLE`` from
        ``to_sql`` fails with ``psycopg.errors.DependentObjectsStillExist``
        and the whole reload 500s.

        Pre-emptively dropping with CASCADE here fixes the reload, at the
        cost of also dropping any view built on top of this exact table.
        That mirrors the trade-off ``if_exists="replace"`` already makes for
        the table's own rows/schema — a "replace" was always going to
        invalidate anything built on the old data, this just makes sure the
        DB-level DROP actually succeeds instead of raising.

        Best-effort: if the drop itself fails for some other reason (e.g. a
        permissions issue), we don't block the caller here — the subsequent
        ``postgres.load`` call will surface a real error if the table still
        can't be replaced.
        """
        try:
            self.postgres.query(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
        except Exception:
            logger.exception(
                "Failed to pre-drop table {} with CASCADE — the subsequent "
                "load may still fail if a dependent view exists.", table_name,
            )

    def _add_data_source(
        self,
        notebook_id: str,
        data_source: NotebookDataSource,
        raw_schema: Dict[str, Any],
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        """Insert the new source row, then recompute the notebook's schema caches."""
        data_source.label = self._unique_label(notebook_id, data_source.label or data_source.table_name)

        self.postgres.query(
            "INSERT INTO sqrl_notebook_data_sources "
            "(id, notebook_id, kind, table_name, connector_id, connector_type, source_file_url, "
            " original_filename, row_count, column_count, label, raw_schema) "
            "VALUES (%(id)s, %(notebook_id)s, %(kind)s, %(table_name)s, %(connector_id)s, %(connector_type)s, "
            " %(source_file_url)s, %(original_filename)s, %(row_count)s, %(column_count)s, %(label)s, %(raw_schema)s)",
            params={
                "id": data_source.id,
                "notebook_id": notebook_id,
                "kind": data_source.kind,
                "table_name": data_source.table_name,
                "connector_id": data_source.connector_id,
                "connector_type": data_source.connector_type,
                "source_file_url": data_source.source_file_url,
                "original_filename": data_source.original_filename,
                "row_count": data_source.row_count,
                "column_count": data_source.column_count,
                "label": data_source.label,
                "raw_schema": json.dumps(raw_schema, default=str),
            },
        )
        self.postgres.query(
            "UPDATE sqrl_notebooks SET status = 'ready', updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        return self.get_notebook(notebook_id, owner_user_id=owner_user_id)

    def add_csv_data_source(
        self,
        notebook_id: str,
        local_path: str,
        original_filename: str,
        source_file_url: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)

        # Derive the stem from the *original* filename, not `local_path` — the
        # route saves uploads to "/tmp/{notebook_id}_{filename}", so using
        # local_path's stem here baked the (already 36-char) notebook_id into
        # the name a second time and blew past Postgres's 63-char identifier
        # limit on longer filenames.
        filename_stem = Path(original_filename).stem
        table_name = self._safe_table_name(f"nb_{notebook_id[:8]}_{filename_stem}")
        # Pre-drop (CASCADE) so a re-upload of the same filename doesn't 500
        # when a SQL view has been built on top of the table being replaced.
        # See `_drop_table_cascade` docstring.
        self._drop_table_cascade(table_name)
        table_schema = self.postgres.load(
            data_path=local_path, table_name=table_name, if_exists="replace"
        )

        data_source = NotebookDataSource(
            id=str(uuid.uuid4()),
            kind="upload",
            table_name=table_name,
            source_file_url=source_file_url,
            original_filename=original_filename,
            row_count=table_schema.get("row_count"),
            column_count=len(table_schema.get("columns", [])),
            label=original_filename,
        )

        # Persist the raw file to MinIO + uploaded_files (unless the caller
        # already supplied a source_file_url) so it shows up on the Files page
        # like any other upload, and can later be re-attached to a *different*
        # notebook via add_existing_file_data_source, the same way Files-page
        # uploads already can. `data_source.id` is known up front, so
        # record_upload can bind the source_id (and notebook_id, so the Files
        # page can resolve a preview without needing a workspace_id at all) in
        # the same insert instead of a separate post-hoc UPDATE. Best-effort:
        # a failure here shouldn't block the notebook upload itself — it just
        # won't show on the Files page.
        if source_file_url is None and owner_user_id is not None:
            try:
                object_name = f"{owner_user_id}/{uuid.uuid4().hex[:8]}_{original_filename}"
                uploaded = self.minio.upload_file(file_path=local_path, object_name=object_name)
                if uploaded is not None:
                    uploaded.fileName = original_filename
                    uploaded.fileType = Path(original_filename).suffix.lstrip(".").lower() or None
                    data_source.source_file_url = str(uploaded.fileUrl)
                    self.file_service.record_upload(
                        uploaded, 
                        owner_user_id=owner_user_id,
                        notebook_id=notebook_id,
                        workspace_id=None
                    )
            except Exception:
                logger.exception(
                    "Failed to persist notebook upload '{}' to MinIO/uploaded_files — "
                    "continuing with the notebook binding only; it won't appear on "
                    "the Files page.", original_filename,
                )
                data_source.source_file_url = None

        return self._add_data_source(notebook_id, data_source, table_schema, owner_user_id=owner_user_id)

    def add_existing_file_data_source(
        self,
        notebook_id: str,
        file_url: str,
        file_name: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        """
        Bind a notebook to a CSV that was already uploaded via the Files
        page, instead of asking the user to upload a fresh copy.

        Ownership is verified against ``uploaded_files.owner_user_id``
        *before* anything is downloaded from MinIO, so a user can't bind
        another user's file just by knowing/guessing its URL — mirrors the
        same check the ``/files/retrieve`` endpoint does.
        """
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing

        if owner_user_id and not self._file_owned_by(file_url, owner_user_id):
            raise PermissionError(f"File '{file_url}' does not belong to this user.")

        resolved_name = file_name or Path(file_url).name
        retrieved = self.minio.retrieve_file(file_url)
        if retrieved is None:
            raise FileNotFoundError(f"File '{file_url}' could not be retrieved from storage.")

        if retrieved.fileByte is None:
            raise FileNotFoundError(f"File '{file_url}' has no content to load.")

        file_bytes = (
            retrieved.fileByte
            if isinstance(retrieved.fileByte, (bytes, bytearray))
            else retrieved.fileByte.encode("utf-8")
        )

        suffix = Path(resolved_name).suffix or ".csv"
        with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            retrieved_path = Path(tmp.name)

        try:
            filename_stem = Path(resolved_name).stem
            table_name = self._safe_table_name(f"nb_{notebook_id[:8]}_{filename_stem}")
            # Pre-drop (CASCADE) so re-binding the same file doesn't 500 when
            # a SQL view has been built on top of the table being replaced.
            # See `_drop_table_cascade` docstring.
            self._drop_table_cascade(table_name)
            table_schema = self.postgres.load(
                data_path=str(retrieved_path), table_name=table_name, if_exists="replace"
            )

            data_source = NotebookDataSource(
                id=str(uuid.uuid4()),
                kind="upload",
                table_name=table_name,
                source_file_url=file_url,
                original_filename=resolved_name,
                row_count=table_schema.get("row_count"),
                column_count=len(table_schema.get("columns", [])),
                label=resolved_name,
            )
            notebook = self._add_data_source(notebook_id, data_source, table_schema, owner_user_id=owner_user_id)

            # Tag the uploaded_files row with the new binding so future
            # renames on the Files page can also update this source's label.
            newest_source = notebook.data_sources[-1] if notebook.data_sources else None
            if newest_source:
                self._bind_upload_to_source(
                    file_url, 
                    notebook_id
            )

            return notebook
        finally:
            if retrieved_path.exists():
                retrieved_path.unlink()

    def _file_owned_by(self, file_url: str, owner_user_id: str) -> bool:
        db_service = PostgresService()
        try:
            result = db_service.retrieve(
                "file", filters={"file_url": file_url, "owner_user_id": owner_user_id}
            )
        finally:
            db_service.disconnect()
        return bool((result or {}).get("rows"))

    def _bind_upload_to_source(self, file_url: str, notebook_id: str) -> None:
        """Best-effort tagging — should never fail the bind itself."""
        try:
            self.postgres.query(
                """
                UPDATE file
                SET notebook_ids = ARRAY(
                    SELECT DISTINCT unnest(notebook_ids || %(notebook_id)s::text[])
                )
                WHERE file_url = %(file_url)s
                """,
                params={"notebook_id": [notebook_id], "file_url": file_url},
            )
        except Exception:
            logger.exception(
                "Failed to tag upload %s with notebook %s", file_url, notebook_id,
            )

    def add_connector_data_source(
        self, notebook_id: str, connector_id: str, table_name: str, owner_user_id: Optional[str] = None
    ) -> Notebook:
        """Bind a notebook to an additional table living on an existing connector."""
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)

        connector_svc = DataConnectorService()
        connector_svc.list_tables(connector_id)  # raises ConnectorNotFoundError if missing

        connector_row = connector_svc.get_connection(connector_id)
        connector_name = connector_row.get("name") or connector_id
        connector_type = connector_row.get("type")

        source_postgres = self._connector_postgres_service(connector_svc, connector_id)
        table_schema = source_postgres._generate_schema_metadata(table_name=table_name)

        preview_df = connector_svc.preview_table(connector_id, table_name, limit=1)

        row_count = None
        count_result = source_postgres.query(f'SELECT COUNT(*) AS cnt FROM "{table_name}"')
        count_rows = (count_result or {}).get("rows", [])
        if count_rows:
            row_count = count_rows[0].get("cnt")

        data_source = NotebookDataSource(
            id=str(uuid.uuid4()),
            kind="connector",
            table_name=table_name,
            connector_id=connector_id,
            connector_type=connector_type,
            row_count=row_count,
            column_count=len(preview_df.columns) if preview_df is not None else None,
            label=f"{connector_name}/{table_name}",  # de-duplicated inside _add_data_source
        )

        return self._add_data_source(notebook_id, data_source, table_schema, owner_user_id=owner_user_id)

    def _unbind_upload_from_source(self, file_url: str, notebook_id: str) -> None:
        """
        Best-effort cleanup counterpart to :meth:`_bind_upload_to_source`.
        Removes notebook_id from notebook_ids so the Files page reverts to
        treating it as a plain, unattached upload once this notebook no
        longer references it.
        """
        if not file_url:
            return
        try:
            self.postgres.query(
                "UPDATE file SET notebook_ids = array_remove(notebook_ids, %(notebook_id)s) "
                "WHERE file_url = %(file_url)s",
                params={"notebook_id": notebook_id, "file_url": file_url},
            )
        except Exception:
            logger.exception("Failed to unbind upload %s from notebook %s", file_url, notebook_id)

    def remove_data_source(
        self,
        notebook_id: str,
        source_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing
        source = self._get_data_source(notebook_id, source_id, owner_user_id=owner_user_id)

        self.postgres.query(
            "DELETE FROM sqrl_notebook_data_sources "
            "WHERE id = %(source_id)s AND notebook_id = %(notebook_id)s",
            params={"source_id": source_id, "notebook_id": notebook_id},
        )

        # Un-tag any uploaded_files row that pointed at this now-deleted source,
        # so the Files page stops carrying a dangling source_id/notebook_id.
        if source.source_file_url:
            self._unbind_upload_from_source(source.source_file_url, notebook_id)
        
        # Any memoized schema-cache bundle keyed on a source-id set that
        # included this source is now stale — drop just those entries rather
        # than the whole cache so unrelated subsets keep their memoized work.
        self._invalidate_schema_cache_entries(notebook_id, source_id)

        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )

        return self.get_notebook(notebook_id, owner_user_id=owner_user_id)


    def rename_data_source(
        self,
        notebook_id: str,
        source_id: str,
        label: str,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        """
        Rename a data source's display label within this notebook.

        For upload-backed sources this also updates ``uploaded_files.file_name``
        for the matching ``source_file_url``, so the Files page and this
        notebook never disagree about what the same underlying file is called.
        Connector-backed sources only rename the in-notebook label — there's
        no corresponding Files-page row to sync.
        """
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing
        source = self._get_data_source(notebook_id, source_id, owner_user_id=owner_user_id)  # 404 if missing

        clean_label = (label or "").strip() or source.table_name
        unique_label = self._unique_label(notebook_id, clean_label, exclude_source_id=source_id)

        self.postgres.query(
            "UPDATE sqrl_notebook_data_sources SET label = %(label)s "
            "WHERE id = %(source_id)s AND notebook_id = %(notebook_id)s",
            params={"label": unique_label, "source_id": source_id, "notebook_id": notebook_id},
        )

        if source.kind == "upload" and source.source_file_url:
            self.postgres.query(
                "UPDATE file SET file_name = %(file_name)s WHERE file_url = %(file_url)s",
                params={"file_name": unique_label, "file_url": source.source_file_url},
            )

        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        return self.get_notebook(notebook_id, owner_user_id=owner_user_id)

    def _invalidate_schema_cache_entries(self, notebook_id: str, removed_source_id: str) -> None:
        result = self.postgres.query(
            "SELECT schema_cache FROM sqrl_notebooks WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        rows = (result or {}).get("rows", [])
        raw_cache = rows[0].get("schema_cache") if rows else None
        all_caches: Dict[str, Any] = (
            json.loads(raw_cache) if isinstance(raw_cache, str) else (raw_cache or {})
        )
        if not all_caches:
            return

        remaining = {
            key: bundle
            for key, bundle in all_caches.items()
            if removed_source_id not in key.split(",")
        }
        if len(remaining) == len(all_caches):
            return

        self.postgres.query(
            "UPDATE sqrl_notebooks SET schema_cache = %(schema_cache)s WHERE id = %(notebook_id)s",
            params={
                "notebook_id": notebook_id,
                "schema_cache": json.dumps(remaining, default=str),
            },
        )

    def _resolve_selected_sources(
        self,
        notebook: Notebook,
        data_source_ids: Optional[List[str]]
    ) -> List[NotebookDataSource]:
        """
        Resolve which attached sources a cell should run against.
        """
        if not data_source_ids:
            return notebook.data_sources
        
        by_id = {s.id: s for s in notebook.data_sources}
        missing = [sid for sid in data_source_ids if sid not in by_id]
        if missing:
            raise ValueError(
                f"These data sources aren't attached to this notebook: {missing}."
                "Remove the stale @mention and try again."
            )
        
        return [by_id[sid] for sid in data_source_ids]        

    @staticmethod
    def _cache_key(source_ids: List[str]) -> str:
        return ",".join(sorted(source_ids))
    
    def _get_or_build_schema_cache(
        self,
        notebook_id: str,
        sources: List[NotebookDataSource],
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return the {schema_details, domain_context, entities_and_metrics} bundle
        for exactly this subset of sources, computing and memoizing it on first
        use. Memoized under a cache key derived from the sorted source ids, so
        repeated questions against the same subset (the common case) reuse the
        LLM-derived domain/entity inference instead of re-running stages 2-3 every
        cell - only the deterministic stage-1 schema stats are always fresh
        (they're read straight from each source's stored `raw_schema`).

        .. note::
            This cache does NOT cover live column-type repair — see
            :meth:`_repair_column_types`, which runs unconditionally on every
            cell (both ``run_cell`` and ``update_cell``), *before* this method
            is called, specifically so a column's type can keep getting fixed
            across cells even once this schema/domain/entity bundle is
            memoized and no longer being recomputed.
        """
        key = self._cache_key([s.id for s in sources])
        
        result = self.postgres.query(
            "SELECT schema_cache FROM sqrl_notebooks WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
    
        rows = (result or {}).get("rows", [])
        raw_cache = rows[0].get("schema_cache") if rows else None
        all_caches: Dict[str, Any] = (
            json.loads(raw_cache) if isinstance(raw_cache, str) else (raw_cache or {})
        )
        
        if key in all_caches:
            return all_caches[key]
        
        source_rows = self.postgres.query(
            "SELECT table_name, raw_schema FROM sqrl_notebook_data_sources "
            "WHERE id = ANY(%(ids)s)",
            params={"ids": [s.id for s in sources]}
        )
        
        schema_metadata: Dict[str, Any] = {}
        for row in (source_rows or {}).get("rows", []):
            raw = row.get("raw_schema")
            schema_metadata[row["table_name"]] = json.loads(raw) if isinstance(raw, str) else raw

        postgres_service = self._resolve_postgres_service_for_sources(sources)
        agent = TabularDataExploratoryAgent(postgres_service=postgres_service, user_id=owner_user_id)

        schema_details = agent.generate_schema_details(schema_metadata)
        domain_context = agent.generate_domain_context(schema_details) or {}
        entities_and_metrics = agent.extract_entities_and_metrics(schema_details, domain_context) or {}

        logger.info(
            "Notebook {}: resolved schema_details for sources {} -> {}",
            notebook_id, [s.table_name for s in sources], schema_details,
        )

        cache_bundle = {
            "schema_details": schema_details,
            "domain_context": domain_context,
            "entities_and_metrics": entities_and_metrics,
        }

        all_caches[key] = cache_bundle
        self.postgres.query(
            "UPDATE sqrl_notebooks SET schema_cache = %(schema_cache)s, updated_at = now() "
            "WHERE id = %(notebook_id)s",
            params={
                "notebook_id": notebook_id,
                "schema_cache": json.dumps(all_caches, default=str),
            },
        )

        return cache_bundle

    # ──────────────────────────────────────────────────────────────────────
    # Live column-type repair (runs on every cell)
    # ──────────────────────────────────────────────────────────────────────

    def _repair_column_types(
        self,
        agent: TabularDataExploratoryAgent,
        sources: List[NotebookDataSource],
    ) -> None:
        """
        Re-check and, if needed, repair column types directly in the live
        database for every source about to be queried by this cell.

        Why this runs here (not just inside ``generate_schema_details``):
        ``_get_or_build_schema_cache`` only calls ``generate_schema_details``
        (which internally reconciles + type-repairs via
        ``TabularDataExploratoryAgent.reconcile_schema_with_database`` /
        ``fix_column_types``) on the *first* cell run against a given source
        set — every later cell reuses the memoized
        ``schema_details``/``domain_context``/``entities_and_metrics`` bundle
        and never touches the database's schema again. That's fine for
        column *names* (they don't change once a table is loaded), but it
        means a column that was still TEXT-typed at cache-build time stays
        TEXT-typed for the rest of the notebook's life otherwise.

        ``fix_column_types`` is cheap to call unconditionally: it queries
        ``information_schema`` once per table and skips every column that
        isn't currently TEXT/VARCHAR (including ones it already fixed on a
        prior call). So this runs on every question/EDA cell, independent of
        the schema cache, instead of only once at cache-build time.

        :param agent: Agent instance already bound to a
            :class:`PostgresService` connected to *sources*' database.
        :param sources: The data sources this cell will query against.
        """
        table_names = {s.table_name for s in sources}
        schema_metadata = {table: {"columns": []} for table in table_names}

        try:
            reconciled = agent.reconcile_schema_with_database(schema_metadata)
            agent.fix_column_types(reconciled)
        except Exception:
            # Never fail a cell run just because the type-repair pass itself
            # hit an issue — the pipeline still works against whatever types
            # are currently live, same as before this stage existed.
            logger.exception(
                "Column type repair failed for sources {} — continuing with "
                "current column types.", sorted(table_names),
            )

    # ──────────────────────────────────────────────────────────────────────
    # Non-repetition helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_question(text: str) -> str:
        """Whitespace-collapsed, case-folded form used for exact-duplicate
        detection between question cells. Deliberately simple (no stemming
        or semantic matching) — this only needs to catch the "user re-ran
        the same question" case, not paraphrases; paraphrase-level overlap
        for EDA sweeps is handled separately by feeding prior questions to
        the hypothesis-generation prompt (see :meth:`_previous_eda_questions`)."""
        return " ".join((text or "").strip().lower().split())

    def _previous_eda_questions(
        self,
        notebook_id: str,
        source_ids: List[str],
    ) -> List[str]:
        """
        Every chart question already produced for this exact source-id set,
        across all prior cells (both ``eda`` and ``question`` types) — used
        to tell a fresh EDA sweep what not to repeat.

        Scoped to an exact source-set match: a cell run against sources
        {A, B} doesn't count as "already analyzed" for a fresh EDA run
        against {A} alone, since the available columns (and therefore what's
        analytically reachable) differ between the two.

        :param notebook_id: The notebook to inspect.
        :param source_ids: The exact set of source ids the new EDA run will
            use — only cells run against this same set contribute questions.

        :return: List of question strings (may be empty for a notebook's
            first EDA run against a given source set).
        :rtype: List[str]
        """
        key = set(source_ids)
        questions: List[str] = []
        for cell in self.list_cells(notebook_id):
            if set(cell.data_source_ids or []) != key:
                continue
            questions.extend(chart.question for chart in cell.charts if chart.question)
        return questions

    def _find_duplicate_question_cell(
        self,
        notebook_id: str,
        normalized_query: str,
        source_ids: List[str],
    ) -> Optional[NotebookCell]:
        """
        Find a prior, successfully-completed ``question`` cell run against
        the same source-id set whose (normalized) query text matches
        *normalized_query* exactly.

        Only ``status == "complete"`` cells are eligible — a previously
        errored cell for the same question is not treated as a duplicate,
        since re-running it is exactly what the user would want (e.g. after
        a transient DB hiccup, or after this service's own type-repair /
        column-reconciliation fixes have since improved the schema).

        :param notebook_id: The notebook to search.
        :param normalized_query: Output of :meth:`_normalize_question` for
            the incoming question.
        :param source_ids: The exact set of source ids the incoming cell
            will run against.

        :return: The matching prior cell, or ``None`` if there isn't one.
        :rtype: Optional[NotebookCell]
        """
        key = set(source_ids)
        for cell in self.list_cells(notebook_id):
            if cell.type != "question" or cell.status != "complete":
                continue
            if set(cell.data_source_ids or []) != key:
                continue
            if self._normalize_question(cell.query) == normalized_query:
                return cell
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Cell execution
    # ──────────────────────────────────────────────────────────────────────

    def run_cell(
        self,
        notebook_id: str,
        cell_type: str,
        query: Optional[str] = None,
        data_source_ids: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> NotebookCell:
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)

        if cell_type == "question" and not (query or "").strip():
            raise ValueError("A question is required for type='question' cells.")
        if cell_type not in ("eda", "dashboard", "question", "markdown"):
            raise ValueError(f"Unsupported cell type: {cell_type!r}.")

        if cell_type == "markdown":
            content = (query or "").strip()
            if not content:
                raise ValueError("Markdown content is required for type'markdown' cells.")
            cell = NotebookCell(
                id=str(uuid.uuid4()),
                notebook_id=notebook_id,
                type="markdown",
                query=content,
                data_source_ids=[],
                status="complete",
                reply="",
                charts=[]
            )
            self._persist_cell(cell)
            self.postgres.query(
                "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
                params={"notebook_id": notebook_id}
            )
            return cell

        if notebook.status != "ready" or not notebook.data_sources:
            raise NotebookNotReadyError(
                "This notebook has no data source yet — add a CSV or connect a "
                "table before asking questions."
            )
        if cell_type == "question" and not (query or "").strip():
            raise ValueError("A question is required for type='question' cells")

        logger.info(f"Cell type: {cell_type}")

        sources = self._resolve_selected_sources(notebook, data_source_ids)
        resolved_ids = [s.id for s in sources]

        # ── Exact-duplicate short-circuit ───────────────────────────────
        if cell_type == "question":
            normalized = self._normalize_question(query)
            duplicate = self._find_duplicate_question_cell(notebook_id, normalized, resolved_ids)
            if duplicate:
                logger.info(
                    "Notebook {}: question duplicates cell {} — reusing its "
                    "result instead of re-running the pipeline.",
                    notebook_id, duplicate.id,
                )
                return duplicate

        # ── Stage: live column-type repair ──────────────────────────────
        # Runs every cell, ahead of the (possibly stale-on-types) schema
        # cache, so a mistyped column gets fixed before SQL generation ever
        # has to guess a cast for it — not just once, the first time this
        # source set was ever cached. See _repair_column_types docstring.
        postgres_service = self._resolve_postgres_service_for_sources(sources)
        agent = TabularDataExploratoryAgent(postgres_service=postgres_service, user_id=owner_user_id)
        self._repair_column_types(agent, sources)

        cache = self._get_or_build_schema_cache(notebook_id, sources, owner_user_id=owner_user_id)
        logger.info(f"Cache: {cache}")

        start_ms = int(datetime.utcnow().timestamp() * 1000)
        cell_id = str(uuid.uuid4())

        try:
            if cell_type == "eda":
                reply, charts = self._run_eda(agent, cache, notebook_id, resolved_ids)
            elif cell_type == "dashboard":
                reply, charts = self._run_eda(
                    agent, cache, notebook_id, resolved_ids,
                    max_hypotheses_override=self._DASHBOARD_TILE_COUNT
                )
            else:
                reply, charts = self._run_question(agent, cache, query.strip(), notebook_id, sources)

            cell = NotebookCell(
                id=cell_id,
                notebook_id=notebook_id,
                type=cell_type,
                query=query or "",
                data_source_ids=resolved_ids,
                status="complete",
                reply=reply,
                charts=charts,
                response_time_ms=int(datetime.utcnow().timestamp() * 1000) - start_ms,
                prompt_tokens=getattr(agent.model, "last_input_tokens", 0) if agent.model else 0,
                completion_tokens=getattr(agent.model, "last_output_tokens", 0) if agent.model else 0,
            )
        except (ValueError, NotebookNotReadyError):
            raise
        except Exception as exc:
            logger.exception("Notebook cell failed  notebook={}  type={}", notebook_id, cell_type)
            cell = NotebookCell(
                id=cell_id,
                notebook_id=notebook_id,
                type=cell_type,
                query=query or "",
                data_source_ids=resolved_ids,
                status="error",
                reply="",
                charts=[],
                error=str(exc),
                response_time_ms=int(datetime.utcnow().timestamp() * 1000) - start_ms,
            )

        self._persist_cell(cell)

        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )        
        
        return cell

    def _run_eda(
        self,
        agent: TabularDataExploratoryAgent,
        cache: Dict[str, Any],
        notebook_id: str,
        source_ids: List[str],
        max_hypotheses_override: Optional[int] = None
    ):
        schema_details = cache["schema_details"]
        domain_context = cache["domain_context"]
        entities_and_metrics = cache["entities_and_metrics"]

        previous_questions = self._previous_eda_questions(notebook_id, source_ids)
        if previous_questions:
            logger.info(
                "Notebook {}: excluding {} previously-analyzed question(s) "
                "from this EDA sweep.",
                notebook_id, len(previous_questions),
            )
        
        original_max_hypotheses = agent.max_hypotheses
        if max_hypotheses_override:
            agent.max_hypotheses = max_hypotheses_override
        try:
            hypotheses = agent.generate_hypotheses(
                schema_details, domain_context, entities_and_metrics,
                previous_questions=previous_questions,
            )
            chart_results = [
                agent.process_hypothesis(h, schema_details, domain_context, entities_and_metrics)
                for h in hypotheses
            ]
        finally:
            agent.max_hypotheses = original_max_hypotheses

        succeeded = [r for r in chart_results if r.succeeded]
        summary = agent.generate_summary(succeeded)

        reply = summary.get("dataset_description", "Analysis complete.")
        for finding in summary.get("key_findings", []) or []:
            text = finding.get("finding") if isinstance(finding, dict) else str(finding)
            if text:
                reply = "\n\n".join([reply, text])

        charts = [self._chart_result_to_model(r) for r in chart_results]
        return reply, charts

    def _run_question(self, agent, cache, question: str, notebook_id: str, sources: List[NotebookDataSource]):
        """
        Answer a natural-language question — either with a chart (the default)
        or, when the question reads more like "show me the rows" / "give me a
        deduplicated list" / "filter down to ..." than "what pattern exists",
        with a saved, non-destructive SQL VIEW the user can reference in later
        questions. See :meth:`TabularDataExploratoryAgent.classify_question_intent`
        and :meth:`TabularDataExploratoryAgent.process_view_request`.

        There is no separate "transform" cell type — a manipulation-flavored
        request never rewrites a data source's live table; it only ever
        produces a new view on top of it.
        """
        schema_details = cache["schema_details"]
        domain_context = cache["domain_context"]
        entities_and_metrics = cache["entities_and_metrics"]

        intent = agent.classify_question_intent(question)
        logger.info("Question intent for {!r}: {}", question[:80], intent)

        if not intent.get("needs_chart", True):
            view_name = self._safe_table_name(f"nb_view_{uuid.uuid4().hex[:8]}")
            result = agent.process_view_request(question, schema_details, view_name)
            if result["error"]:
                reply = (
                    f"I wasn't able to build that view. It failed with: {result['error']}. "
                    f"Try rephrasing — for example, mention specific column names."
                )
            else:
                reply = f"{result['observation']}\n\nSaved as view `{view_name}` — you can reference it in later questions."
            return reply, [self._view_result_to_chart_model(question, view_name, result)]

        plot_type = intent.get("chart_type") or self._infer_plot_type_from_keywords(question)
        title = question.split("?")[0].strip()[:80] or question[:80]

        hypothesis = Hypothesis(
            id="q1",
            question=question,
            title=title,
            description=f"Direct analysis: {question}",
            why_it_matters="Directly requested by the user.",
            plot_type=plot_type,
            analytical_type="ad_hoc",
        )
        result = agent.process_hypothesis(hypothesis, schema_details, domain_context, entities_and_metrics)
        if result.succeeded:
            reply = result.observation
        else:
            reply = (
                f"I wasn't able to produce a chart for that question. "
                f"The query failed with: {result.error or 'unknown error'}. "
                f"Try rephrasing — for example, mention specific column names you'd like to use."
            )
        return reply, [self._chart_result_to_model(result)]

    def _table_row_count(
        self, 
        postgres_service: PostgresService, 
        table: str
    ) -> Optional[int]:
        try:
            result = postgres_service.query(f'SELECT COUNT(*) AS cnt FROM "{table}"')
            rows = (result or {}).get("rows", [])
            return rows[0].get("cnt") if rows else None
        except Exception:
            return None

    @staticmethod
    def _infer_plot_type_from_keywords(question: str) -> str:
        """Fallback plot-type heuristic — used only when the LLM classifier didn't
        return a chart_type (e.g. it errored and defaulted to needs_chart=True)."""
        q_lower = question.lower()
        if any(w in q_lower for w in ("over time", "trend", "monthly", "weekly", "daily", "by year", "by month", "by date")):
            return "line"
        if any(w in q_lower for w in ("distribution", "spread", "boxplot", "quartile", "percentile")):
            return "boxplot"
        if any(w in q_lower for w in ("correlation", "relationship between", "scatter")):
            return "scatter"
        if any(w in q_lower for w in ("heatmap", "matrix", "cross")):
            return "heatmap"
        return "bar"

    @staticmethod
    def _view_result_to_chart_model(question: str, view_name: str, result: Dict) -> NotebookChart:
        df = result.get("data")
        records: List[Dict] = []
        if df is not None and not df.empty:
            records = json.loads(df.head(500).to_json(orient="records", date_format="iso"))

        return NotebookChart(
            id=f"view_{view_name}",
            title=f"View: {view_name}",
            question=question,
            plot_type="table",
            sql=result.get("sql", ""),
            x={},
            y={},
            group_by=None,
            data=records,
            observation=result.get("observation", ""),
            error=result.get("error"),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Conversion helpers
    # ──────────────────────────────────────────────────────────────────────

    def _resolve_postgres_service_for_sources(
        self,
        sources: List[NotebookDataSource],
    ) -> PostgresService:
        """
        Resolve a single query-capable PostgresService for a set of sources.
        All sources in one cell must live in the same place — either they're
        all uploads (main app DB), or they're all tables on the *same*
        connector. Mixing uploads with connector tables, or tables from two
        different connectors, isn't supported in a single query/cell.
        """
        if not sources:
            raise NotebookNotReadyError("No data sources to resolve a database connection for.")

        kinds = {s.kind for s in sources}
        if len(kinds) > 1:
            raise ValueError(
                "Can't run a single cell across an uploaded CSV and a connector "
                "table together — split them into separate @mentions/cells."
            )

        kind = next(iter(kinds))
        if kind == "upload":
            return self.postgres

        connector_ids = {s.connector_id for s in sources}
        if len(connector_ids) > 1:
            raise ValueError(
                "Can't run a single cell across tables from two different "
                "connectors — split them into separate @mentions/cells."
            )

        connector_svc = DataConnectorService()
        return self._connector_postgres_service(connector_svc, next(iter(connector_ids)))

    @staticmethod
    def _connector_postgres_service(
        connector_svc: DataConnectorService, 
        connector_id: str
    ) -> PostgresService:
        """
        Resolve a query-capable :class:`PostgresService` for a connector.

        See module docstring — implement ``get_postgres_service`` on
        :class:`DataConnectorService` if it doesn't already exist.
        """
        if hasattr(connector_svc, "get_postgres_service"):
            return connector_svc.get_postgres_service(connector_id)
        raise NotImplementedError(
            "DataConnectorService.get_postgres_service(connector_id) is required to "
            "run notebook cells against a connector-backed data source."
        )

    @staticmethod
    def _safe_table_name(
        name: str
    ) -> str:
        """
        Postgres identifiers cap out at 63 bytes. Sanitize to
        ``[a-z0-9_]`` and, if the cleaned name is too long, truncate and
        append a short random suffix so two names that only differ after
        character 54 or so don't collide once cut down.
        """
        cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in name).lower()
        cleaned = cleaned.strip("_") or f"nb_{uuid.uuid4().hex[:8]}"

        max_len = 63
        if len(cleaned) <= max_len:
            return cleaned

        suffix = uuid.uuid4().hex[:8]
        keep = max_len - len(suffix) - 1  # -1 for the joining "_"
        return f"{cleaned[:keep]}_{suffix}"

    def _persist_cell(
        self, 
        cell: NotebookCell
    ) -> None:
        layout_json = (
            json.dumps(cell.dashboard_layout.model_dump(), default=str)
            if cell.dashboard_layout
            else None
        )
        self.postgres.query(
            "INSERT INTO sqrl_notebook_cells "
            "(id, notebook_id, type, query, status, reply, charts, error, data_source_ids, "
            " response_time_ms, prompt_tokens, completion_tokens, dashboard_layout) "
            "VALUES (%(id)s, %(notebook_id)s, %(type)s, %(query)s, %(status)s, %(reply)s, "
            " %(charts)s, %(error)s, %(data_source_ids)s, %(response_time_ms)s, %(prompt_tokens)s, "
            " %(completion_tokens)s, %(dashboard_layout)s)",
            params={
                "id": cell.id,
                "notebook_id": cell.notebook_id,
                "type": cell.type,
                "query": cell.query,
                "status": cell.status,
                "reply": cell.reply,
                "charts": json.dumps([c.model_dump() for c in cell.charts]),
                "error": cell.error,
                "data_source_ids": json.dumps(getattr(cell, "data_source_ids", []) or []),
                "response_time_ms": cell.response_time_ms,
                "prompt_tokens": cell.prompt_tokens,
                "completion_tokens": cell.completion_tokens,
                "dashboard_layout": layout_json,
            },
        )

    @staticmethod
    def _row_to_notebook(row: Dict[str, Any], data_sources: List[NotebookDataSource]) -> Notebook:
        return Notebook(
            id=row["id"],
            name=row["name"],
            description=row.get("description"),
            status=row.get("status") or "empty",
            data_sources=data_sources,
            cell_count=int(row.get("cell_count") or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _parse_dashboard_layout(
        raw: Any
    ) -> Optional[DashboardLayout]:
        if not raw:
            return None
        if isinstance(raw, str):
            raw = json.loads(raw) if raw else None
        if not raw:
            return None
        return DashboardLayout(**raw)

    @staticmethod
    def _row_to_cell(
        row: Dict[str, Any]
    ) -> NotebookCell:
        charts_raw = row.get("charts")
        if isinstance(charts_raw, str):
            charts_raw = json.loads(charts_raw) if charts_raw else []
        ids_raw = row.get("data_source_ids")
        if isinstance(ids_raw, str):
            ids_raw = json.loads(ids_raw) if ids_raw else []
        layout = NotebookService._parse_dashboard_layout(row.get("dashboard_layout"))
        return NotebookCell(
            id=row["id"],
            notebook_id=row["notebook_id"],
            type=row["type"],
            query=row.get("query") or "",
            data_source_ids=ids_raw or [],
            status=row.get("status") or "complete",
            reply=row.get("reply") or "",
            charts=[NotebookChart(**c) for c in (charts_raw or [])],
            dashboard_layout=layout,
            error=row.get("error"),
            created_at=row["created_at"],
            response_time_ms=int(row.get("response_time_ms") or 0),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            order_index=int(row.get("order_index") or 0),
        )
    
    def get_cell(
        self, 
        notebook_id: str, 
        cell_id: str
    ) -> NotebookCell:
        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_cells WHERE notebook_id = %(notebook_id)s AND id = %(cell_id)s",
            params={"notebook_id": notebook_id, "cell_id": cell_id},
        )
        rows = (result or {}).get("rows", [])
        if not rows:
            raise CellNotFoundError(f"Cell '{cell_id}' not found in notebook '{notebook_id}'.")
        return self._row_to_cell(rows[0])

    def delete_cell(
        self,
        notebook_id: str,
        cell_id: str,
        owner_user_id: Optional[str] = None,
    ) -> None:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        self.get_cell(notebook_id, cell_id)  # 404 if missing

        self.postgres.query(
            "DELETE FROM sqrl_notebook_cells WHERE notebook_id = %(notebook_id)s AND id = %(cell_id)s",
            params={"notebook_id": notebook_id, "cell_id": cell_id},
        )
        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
    
    # ──────────────────────────────────────────────────────────────────────
    # Dashboard layout & version history
    # ──────────────────────────────────────────────────────────────────────

    def save_dashboard_layout(
        self,
        notebook_id: str,
        cell_id: str,
        layout: DashboardLayout,
        owner_user_id: Optional[str] = None,
    ) -> NotebookCell:
        """Persist user customizations for a dashboard cell."""
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        cell = self.get_cell(notebook_id, cell_id)
        if cell.type != "dashboard":
            raise ValueError("Dashboard layout can only be saved for dashboard cells.")

        self.postgres.query(
            "UPDATE sqrl_notebook_cells SET dashboard_layout = %(dashboard_layout)s "
            "WHERE id = %(cell_id)s AND notebook_id = %(notebook_id)s",
            params={
                "cell_id": cell_id,
                "notebook_id": notebook_id,
                "dashboard_layout": json.dumps(layout.model_dump(), default=str),
            },
        )
        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        return self.get_cell(notebook_id, cell_id)

    def list_dashboard_versions(
        self,
        notebook_id: str,
        cell_id: str,
        owner_user_id: Optional[str] = None,
    ) -> List[DashboardVersion]:
        """Return all archived dashboard snapshots for a cell, newest first."""
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        cell = self.get_cell(notebook_id, cell_id)
        if cell.type != "dashboard":
            raise ValueError("Version history is only available for dashboard cells.")

        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_dashboard_versions "
            "WHERE notebook_id = %(notebook_id)s AND cell_id = %(cell_id)s "
            "ORDER BY version_number DESC",
            params={"notebook_id": notebook_id, "cell_id": cell_id},
        )
        rows = (result or {}).get("rows", [])
        return [self._row_to_dashboard_version(row) for row in rows]

    def get_dashboard_version(
        self,
        notebook_id: str,
        cell_id: str,
        version_id: str,
        owner_user_id: Optional[str] = None,
    ) -> DashboardVersion:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        cell = self.get_cell(notebook_id, cell_id)
        if cell.type != "dashboard":
            raise ValueError("Version history is only available for dashboard cells.")

        result = self.postgres.query(
            "SELECT * FROM sqrl_notebook_dashboard_versions "
            "WHERE notebook_id = %(notebook_id)s AND cell_id = %(cell_id)s AND id = %(version_id)s",
            params={"notebook_id": notebook_id, "cell_id": cell_id, "version_id": version_id},
        )
        rows = (result or {}).get("rows", [])
        if not rows:
            raise CellNotFoundError(
                f"Dashboard version '{version_id}' not found for cell '{cell_id}'."
            )
        return self._row_to_dashboard_version(rows[0])

    def _next_dashboard_version_number(self, cell_id: str) -> int:
        result = self.postgres.query(
            "SELECT COALESCE(MAX(version_number), 0) AS max_ver "
            "FROM sqrl_notebook_dashboard_versions WHERE cell_id = %(cell_id)s",
            params={"cell_id": cell_id},
        )
        rows = (result or {}).get("rows", [])
        return int(rows[0].get("max_ver") or 0) + 1 if rows else 1

    def _archive_dashboard_version(self, cell: NotebookCell) -> None:
        """Snapshot the current dashboard state before regeneration."""
        if cell.type != "dashboard":
            return
        if not cell.charts and not cell.reply and not cell.dashboard_layout:
            return

        version_id = str(uuid.uuid4())
        version_number = self._next_dashboard_version_number(cell.id)
        layout_json = (
            json.dumps(cell.dashboard_layout.model_dump(), default=str)
            if cell.dashboard_layout
            else None
        )
        self.postgres.query(
            "INSERT INTO sqrl_notebook_dashboard_versions "
            "(id, cell_id, notebook_id, version_number, reply, charts, dashboard_layout) "
            "VALUES (%(id)s, %(cell_id)s, %(notebook_id)s, %(version_number)s, %(reply)s, "
            " %(charts)s, %(dashboard_layout)s)",
            params={
                "id": version_id,
                "cell_id": cell.id,
                "notebook_id": cell.notebook_id,
                "version_number": version_number,
                "reply": cell.reply or "",
                "charts": json.dumps([c.model_dump() for c in cell.charts], default=str),
                "dashboard_layout": layout_json,
            },
        )

    @staticmethod
    def _row_to_dashboard_version(row: Dict[str, Any]) -> DashboardVersion:
        charts_raw = row.get("charts")
        if isinstance(charts_raw, str):
            charts_raw = json.loads(charts_raw) if charts_raw else []
        layout = NotebookService._parse_dashboard_layout(row.get("dashboard_layout"))
        return DashboardVersion(
            id=row["id"],
            cell_id=row["cell_id"],
            notebook_id=row["notebook_id"],
            version_number=int(row.get("version_number") or 0),
            reply=row.get("reply") or "",
            charts=[NotebookChart(**c) for c in (charts_raw or [])],
            dashboard_layout=layout,
            created_at=row["created_at"],
        )

    def update_cell(
        self, 
        notebook_id: str, 
        cell_id: str, 
        query: Optional[str] = None,
        datasource_ids: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> NotebookCell:
        """
        Re-run an existing cell in place — same id/position, fresh output.
        """
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        if notebook.status != "ready" or not notebook.data_sources:
            raise NotebookNotReadyError(
                "This notebook has no data source yet — add a CSV or connect a "
                "table before regenerating cells."
            )
        existing = self.get_cell(notebook_id, cell_id)  # 404 if missing
        cell_type = existing.type
        if cell_type == "markdown":
            content = (query if query is not None else existing.query).strip()
            if not content:
                raise ValueError("Markdown content is required for type='markdown' cells.")
            cell = NotebookCell(
                id=cell_id,
                notebook_id=notebook_id,
                type="markdown",
                query=content,
                data_source_ids=[],
                status="complete",
                reply="",
                charts=[]
            )
            self._update_cell_row(cell)
            self.postgres.query(
                "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
                params={"notebook_id": notebook_id},
            )
            return cell
        
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        if notebook.status != "ready" or not notebook.data_sources:
            raise NotebookNotReadyError(
                "This notebook has no data source yet — add a CSV or connect a "
                "table before regenerating cells."
            )

        effective_query = (query if query is not None else existing.query).strip()
        if cell_type == "question" and not effective_query:
            raise ValueError("A question is required for type='question' cells.")

        # Archive the current dashboard snapshot before overwriting on regenerate.
        if cell_type == "dashboard" and existing.status == "complete" and existing.charts:
            try:
                self._archive_dashboard_version(existing)
            except Exception:
                logger.exception(
                    "Failed to archive dashboard version for cell {} — proceeding "
                    "with regeneration anyway.", cell_id,
                )

        sources = self._resolve_selected_sources(
            notebook,
            datasource_ids if datasource_ids is not None else getattr(existing, "data_source_ids", None),
        )
        resolved_ids = [s.id for s in sources]

        postgres_service = self._resolve_postgres_service_for_sources(sources)
        agent = TabularDataExploratoryAgent(postgres_service=postgres_service, user_id=owner_user_id)
        self._repair_column_types(agent, sources)

        cache = self._get_or_build_schema_cache(notebook_id, sources, owner_user_id=owner_user_id)
        start_ms = int(datetime.utcnow().timestamp() * 1000)

        try:
            if cell_type in ("eda", "dashboard"):
                previous_questions = [
                    q for q in self._previous_eda_questions(notebook_id, resolved_ids)
                    if q not in {c.question for c in existing.charts}
                ]
                if cell_type == "dashboard":
                    original_max_hypotheses = agent.max_hypotheses
                    agent.max_hypotheses = self._DASHBOARD_TILE_COUNT
                try:
                    hypotheses = agent.generate_hypotheses(
                        cache["schema_details"], cache["domain_context"], cache["entities_and_metrics"],
                        previous_questions=previous_questions,
                    )
                    chart_results = [
                        agent.process_hypothesis(
                            h, cache["schema_details"], cache["domain_context"], cache["entities_and_metrics"]
                        )
                        for h in hypotheses
                    ]
                finally:
                    if cell_type == "dashboard":
                        agent.max_hypotheses = original_max_hypotheses

                succeeded = [r for r in chart_results if r.succeeded]
                summary = agent.generate_summary(succeeded)
                reply = summary.get("dataset_description", "Analysis complete.")
                for finding in summary.get("key_findings", []) or []:
                    text = finding.get("finding") if isinstance(finding, dict) else str(finding)
                    if text:
                        reply = "\n\n".join([reply, text])
                charts = [self._chart_result_to_model(r) for r in chart_results]
            else:
                reply, charts = self._run_question(agent, cache, effective_query, notebook_id, sources)

            cell = NotebookCell(
                id=cell_id,
                notebook_id=notebook_id,
                type=cell_type,
                query=effective_query if cell_type == "question" else existing.query,
                data_source_ids=resolved_ids,
                status="complete",
                reply=reply,
                charts=charts,
                dashboard_layout=None if cell_type == "dashboard" else existing.dashboard_layout,
                response_time_ms=int(datetime.utcnow().timestamp() * 1000) - start_ms,
                prompt_tokens=getattr(agent.model, "last_input_tokens", 0) if agent.model else 0,
                completion_tokens=getattr(agent.model, "last_output_tokens", 0) if agent.model else 0,
            )
        except Exception as exc:
            logger.exception("Notebook cell regeneration failed  notebook={}  cell={}", notebook_id, cell_id)
            cell = NotebookCell(
                id=cell_id,
                notebook_id=notebook_id,
                type=cell_type,
                query=effective_query if cell_type == "question" else existing.query,
                data_source_ids=resolved_ids,
                status="error",
                reply="",
                charts=[],
                dashboard_layout=existing.dashboard_layout,
                error=str(exc),
                response_time_ms=int(datetime.utcnow().timestamp() * 1000) - start_ms,
            )

        self._update_cell_row(cell)

        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        return cell

    def _update_cell_row(
        self, 
        cell: NotebookCell
    ) -> None:
        layout_json = (
            json.dumps(cell.dashboard_layout.model_dump(), default=str)
            if cell.dashboard_layout
            else None
        )
        self.postgres.query(
            "UPDATE sqrl_notebook_cells SET query = %(query)s, status = %(status)s, reply = %(reply)s, "
            "charts = %(charts)s, error = %(error)s, response_time_ms = %(response_time_ms)s, "
            "prompt_tokens = %(prompt_tokens)s, completion_tokens = %(completion_tokens)s, "
            "dashboard_layout = %(dashboard_layout)s, data_source_ids = %(data_source_ids)s "
            "WHERE id = %(cell_id)s",
            params={
                "cell_id": cell.id,
                "query": cell.query,
                "status": cell.status,
                "reply": cell.reply,
                "charts": json.dumps([c.model_dump() for c in cell.charts]),
                "error": cell.error,
                "response_time_ms": cell.response_time_ms,
                "prompt_tokens": cell.prompt_tokens,
                "completion_tokens": cell.completion_tokens,
                "dashboard_layout": layout_json,
                "data_source_ids": json.dumps(getattr(cell, "data_source_ids", []) or []),
            },
        )

    def preview_data_source(
        self,
        notebook_id: str,
        data_source_id: Optional[str] = None,
        limit: int = 50,
        owner_user_id: Optional[str] = None,
    ) -> DataSourcePreview:
        """
        Read-only preview of one of the notebook's bound tables. Defaults to
        the first attached source if none is specified.
        """
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)
        if notebook.status != "ready" or not notebook.data_sources:
            raise NotebookNotReadyError(
                "This notebook has no data source yet — add a CSV or connect a "
                "table before previewing data."
            )

        sources = self._resolve_selected_sources(
            notebook=notebook, 
            data_source_ids=[data_source_id] if data_source_id else None
        )
        source = sources[0]

        postgres_service = self._resolve_postgres_service_for_sources([source])
        table_name = source.table_name

        result = postgres_service.query(
            f'SELECT * FROM "{table_name}" LIMIT %(limit)s',
            params={"limit": limit},
        )
        rows = (result or {}).get("rows", [])
        columns = list(rows[0].keys()) if rows else []

        return DataSourcePreview(
            table_name=table_name,
            columns=columns,
            rows=rows,
            row_count=source.row_count,
        )
        
        
    def rename_notebook(
        self,
        notebook_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Notebook:
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing

        if name is None and description is None:
            return self.get_notebook(notebook_id)

        sets: List[str] = []
        params: Dict[str, Any] = {"notebook_id": notebook_id}

        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if description is not None:
            sets.append("description = %(description)s")
            params["description"] = description
        sets.append("updated_at = now()")

        sql = f"UPDATE sqrl_notebooks SET {', '.join(sets)} WHERE id = %(notebook_id)s"
        if owner_user_id:
            sql += " AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id

        self.postgres.query(sql, params=params)

        return self.get_notebook(notebook_id, owner_user_id=owner_user_id)

    
    def _unique_notebook_name(self, candidate: str) -> str:
        """
        Disambiguate a notebook name against every other notebook's name, the
        same way `_unique_label` disambiguates data source labels within a
        notebook — "Churn analysis (copy)" colliding with an existing notebook
        of that exact name becomes "Churn analysis (copy) (2)", etc.
        """
        candidate = (candidate or "").strip() or "Untitled notebook"
        result = self.postgres.query("SELECT name FROM sqrl_notebooks")
        existing = {row["name"] for row in (result or {}).get("rows", []) if row.get("name")}
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate} ({n})" in existing:
            n += 1
        return f"{candidate} ({n})"
    
    
    def reorder_cells(
        self,
        notebook_id: str,
        ordered_cell_ids: List[str],
        owner_user_id: Optional[str] = None,
    ) -> List[NotebookCell]:
        """Update the order_index of cells based on the provided list."""
        self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing

        # Verify that all cell ids belong to the notebook
        cells = self.list_cells(notebook_id, owner_user_id=owner_user_id)
        cell_id_set = {c.id for c in cells}
        missing = [cid for cid in ordered_cell_ids if cid not in cell_id_set]
        if missing:
            raise CellNotFoundError(
                f"The following cell ids are not in notebook {notebook_id}: {missing}"
            )
        # Also ensure we have exactly the same set (no extra ids)
        extra = [cid for cid in cell_id_set if cid not in ordered_cell_ids]
        if extra:
            raise CellNotFoundError(
                f"The ordered list is missing these cell ids from notebook {notebook_id}: {extra}"
            )

        # Update order_index for each cell
        for idx, cell_id in enumerate(ordered_cell_ids):
            self.postgres.query(
                "UPDATE sqrl_notebook_cells SET order_index = %(order_index)s "
                "WHERE id = %(cell_id)s AND notebook_id = %(notebook_id)s",
                params={"order_index": idx, "cell_id": cell_id, "notebook_id": notebook_id},
            )

        self.postgres.query(
            "UPDATE sqrl_notebooks SET updated_at = now() WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )

        # Return the cells in the new order
        return self.list_cells(notebook_id, owner_user_id=owner_user_id)

    def duplicate_notebook(self, notebook_id: str, owner_user_id: Optional[str] = None) -> Notebook:
        """
        Clone a notebook: its metadata, every attached data source (as new
        bindings — the underlying uploaded table / connector table is *shared*,
        not copied), and every cell (with `data_source_ids` remapped onto the
        new bindings so `@mention` resolution still works post-copy).

        Deliberately does NOT copy `schema_cache`: the cache keys are derived
        from sorted source ids, and those ids change on duplication, so a
        copied cache would just be dead weight. The new notebook lazily
        rebuilds it on its first cell run, same as any notebook would.
        """
        original = self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing

        status_result = self.postgres.query(
            "SELECT status FROM sqrl_notebooks WHERE id = %(notebook_id)s",
            params={"notebook_id": notebook_id},
        )
        status_rows = (status_result or {}).get("rows", [])
        status = status_rows[0]["status"] if status_rows else "empty"

        new_notebook_id = str(uuid.uuid4())
        new_name = self._unique_notebook_name(f"{original.name} (copy)")

        self.postgres.query(
            "INSERT INTO sqrl_notebooks (id, owner_user_id, name, description, status) "
            "VALUES (%(id)s, %(owner_user_id)s, %(name)s, %(description)s, %(status)s)",
            params={
                "id": new_notebook_id,
                "owner_user_id": owner_user_id,
                "name": new_name,
                "description": original.description,
                "status": status,
            },
        )

        # Copy data sources, tracking old id -> new id so cells can be remapped.
        source_id_map: Dict[str, str] = {}
        for source in original.data_sources:
            new_source_id = str(uuid.uuid4())
            source_id_map[source.id] = new_source_id
            self.postgres.query(
                "INSERT INTO sqrl_notebook_data_sources "
                "(id, notebook_id, kind, table_name, connector_id, connector_type, "
                " source_file_url, original_filename, row_count, column_count, label, raw_schema) "
                "SELECT %(new_id)s, %(new_notebook_id)s, kind, table_name, connector_id, "
                "       connector_type, source_file_url, original_filename, row_count, "
                "       column_count, label, raw_schema "
                "FROM sqrl_notebook_data_sources WHERE id = %(old_id)s",
                params={
                    "new_id": new_source_id,
                    "new_notebook_id": new_notebook_id,
                    "old_id": source.id,
                },
            )

        # Copy cells, remapping their data_source_ids onto the new bindings.
        for cell in self.list_cells(notebook_id):
            remapped_ids = [source_id_map.get(sid, sid) for sid in (cell.data_source_ids or [])]
            layout_json = (
                json.dumps(cell.dashboard_layout.model_dump(), default=str)
                if cell.dashboard_layout
                else None
            )
            self.postgres.query(
                "INSERT INTO sqrl_notebook_cells "
                "(id, notebook_id, type, query, status, reply, charts, error, data_source_ids, "
                " response_time_ms, prompt_tokens, completion_tokens, dashboard_layout) "
                "VALUES (%(id)s, %(notebook_id)s, %(type)s, %(query)s, %(status)s, %(reply)s, "
                " %(charts)s, %(error)s, %(data_source_ids)s, %(response_time_ms)s, "
                " %(prompt_tokens)s, %(completion_tokens)s, %(dashboard_layout)s)",
                params={
                    "id": str(uuid.uuid4()),
                    "notebook_id": new_notebook_id,
                    "type": cell.type,
                    "query": cell.query,
                    "status": cell.status,
                    "reply": cell.reply,
                    "charts": json.dumps([c.model_dump() for c in cell.charts]),
                    "error": cell.error,
                    "data_source_ids": json.dumps(remapped_ids),
                    "response_time_ms": cell.response_time_ms,
                    "prompt_tokens": cell.prompt_tokens,
                    "completion_tokens": cell.completion_tokens,
                    "dashboard_layout": layout_json,
                },
            )

        return self.get_notebook(new_notebook_id)


    # ──────────────────────────────────────────────────────────────────────
    # Jupyter (.ipynb) export
    # ──────────────────────────────────────────────────────────────────────

    def export_notebook_ipynb(
        self,
        notebook_id: str,
        owner_user_id: Optional[str] = None,
    ) -> bytes:
        """
        Render this notebook as a Jupyter Notebook (nbformat v4) file, encoded
        as UTF-8 JSON bytes ready to stream back over HTTP.

        Each :class:`NotebookCell` becomes:
          * one markdown cell — its type (EDA/Question), the query asked (if
            any), and its reply/error text.
          * for every chart it produced: a markdown cell (title +
            observation) followed by a code cell that rebuilds the chart's
            underlying data as a ``pandas.DataFrame`` and re-plots it with
            matplotlib. The chart's original SQL is included as a comment
            for reference, not executed — this notebook has no live DB
            connection, only the data already captured at analysis time.

        Cells whose chart ``error`` is set are skipped (nothing useful to
        replay), matching how the UI already filters them out via
        ``CellCard``'s ``validCharts``.
        """
        notebook = self.get_notebook(notebook_id, owner_user_id=owner_user_id)  # 404 if missing
        cells = self.list_cells(notebook_id, owner_user_id=owner_user_id)
        nb_cells: List[Dict[str, Any]] = []

        overview = [f"# {notebook.name}\n"]
        if notebook.description:
            overview.append(f"\n{notebook.description}\n")
        if notebook.data_sources:
            overview.append("\n**Data sources:**\n")
            for ds in notebook.data_sources:
                overview.append(f"- `{ds.label or ds.table_name}` ({ds.kind})\n")
        nb_cells.append(self._md_cell("".join(overview)))

        nb_cells.append(self._code_cell(
            "import pandas as pd\n"
            "import matplotlib.pyplot as plt\n"
            "\n"
            "# Exported from a Squirrel notebook. Each chart's original SQL is\n"
            "# included as a comment for reference — the data below is a snapshot\n"
            "# captured at analysis time, not a live query result. Charts are\n"
            "# pre-rendered below; re-run a cell to regenerate its figure.\n"
        ))

        exec_count = 0
        for i, cell in enumerate(cells, start=1):
            if cell.type == "markdown":
                nb_cells.append(self._md_cell(cell.query))
                continue
            kind_label = "EDA sweep" if cell.type == "eda" else "Dashboard" if cell.type == "dashboard" else "Question"
            header = f"## [{i}] {kind_label}\n"
            if cell.query:
                header += f"\n> {cell.query}\n"
            if cell.status == "error":
                header += f"\n**This cell failed:** {cell.error or 'unknown error'}\n"
            elif cell.reply:
                header += f"\n{cell.reply}\n"
            nb_cells.append(self._md_cell(header))

            for chart in cell.charts or []:
                if chart.error:
                    continue
                chart_md = f"### {chart.title}\n"
                if chart.observation:
                    chart_md += f"\n{chart.observation}\n"
                nb_cells.append(self._md_cell(chart_md))

                source = self._chart_to_code(chart)
                png_bytes = self._render_chart_png(chart)
                if png_bytes:
                    exec_count += 1
                    nb_cells.append(self._code_cell_with_image(source, png_bytes, exec_count))
                else:
                    # Rendering failed or the chart has no plottable axes —
                    # still export the reproduction code, just without a
                    # baked-in figure.
                    nb_cells.append(self._code_cell(source))

        notebook_json = {
            "cells": nb_cells,
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }

        return json.dumps(notebook_json, indent=1, default=str).encode("utf-8")

    @staticmethod
    def _md_cell(text: str) -> Dict[str, Any]:
        return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True) or [text]}

    @staticmethod
    def _code_cell(text: str) -> Dict[str, Any]:
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": text.splitlines(keepends=True) or [text],
        }

    @staticmethod
    def _chart_to_code(chart: NotebookChart) -> str:
        """Generate a runnable pandas/matplotlib cell reproducing one chart."""
        lines: List[str] = []
        if chart.sql:
            sql_comment = "\n".join(f"# {line}" for line in chart.sql.splitlines())
            lines.append(f"# SQL used to produce this chart:\n{sql_comment}\n")

        data_json = json.dumps(chart.data, default=str)
        lines.append(f"df = pd.DataFrame({data_json})\n")

        x_col = (chart.x or {}).get("column")
        y_col = (chart.y or {}).get("column")
        plot_type = chart.plot_type or "bar"

        if not x_col or not y_col or not chart.data:
            lines.append("df\n")
            return "".join(lines)

        if plot_type == "line":
            lines.append(f"df.plot(x={x_col!r}, y={y_col!r}, kind='line', figsize=(8, 4), title={chart.title!r})\n")
        elif plot_type == "scatter":
            lines.append(f"df.plot(x={x_col!r}, y={y_col!r}, kind='scatter', figsize=(8, 4), title={chart.title!r})\n")
        elif plot_type == "heatmap":
            lines.append(f"pivot = df.pivot_table(index={y_col!r}, columns={x_col!r}, values=df.columns[-1], aggfunc='mean')\n")
            lines.append("plt.figure(figsize=(8, 5))\n")
            lines.append("plt.imshow(pivot, cmap='Blues', aspect='auto')\n")
            lines.append("plt.colorbar()\n")
            lines.append(f"plt.title({chart.title!r})\n")
            lines.append("plt.xticks(range(len(pivot.columns)), pivot.columns, rotation=45, ha='right')\n")
            lines.append("plt.yticks(range(len(pivot.index)), pivot.index)\n")
        elif plot_type == "boxplot":
            lines.append(f"df.boxplot(column={y_col!r}, by={x_col!r}, figsize=(8, 4))\n")
            lines.append(f"plt.title({chart.title!r})\n")
        else:
            lines.append(f"df.plot(x={x_col!r}, y={y_col!r}, kind='bar', figsize=(8, 4), title={chart.title!r})\n")

        lines.append("plt.tight_layout()\n")
        lines.append("plt.show()\n")
        return "".join(lines)

    @staticmethod
    def _code_cell_with_image(source: str, png_bytes: bytes, execution_count: int) -> Dict[str, Any]:
        """
        Same shape as :meth:`_code_cell`, but with a pre-baked
        ``display_data`` output attached — so the figure renders the moment
        the notebook is opened, without the reader needing to execute the
        cell first. ``execution_count`` is cosmetic (it's just the ``[N]``
        Jupyter shows next to the cell) and doesn't correspond to a real
        kernel session.
        """
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return {
            "cell_type": "code",
            "execution_count": execution_count,
            "metadata": {},
            "outputs": [
                {
                    "output_type": "display_data",
                    "data": {
                        "image/png": b64,
                        "text/plain": ["<Figure: pre-rendered at export time>"],
                    },
                    "metadata": {},
                }
            ],
            "source": source.splitlines(keepends=True) or [source],
        }

    @staticmethod
    def _render_chart_png(chart: NotebookChart) -> Optional[bytes]:
        """
        Render one chart server-side with matplotlib (headless ``Agg``
        backend) and return PNG bytes, or ``None`` if the chart has no
        plottable data/axes, or if rendering itself fails. Never raises —
        a chart that can't be pre-rendered still gets its reproduction
        code exported via :meth:`_chart_to_code`, just without a baked-in
        image.
        """
        x_col = (chart.x or {}).get("column")
        y_col = (chart.y or {}).get("column")
        if not x_col or not y_col or not chart.data:
            return None

        df = pd.DataFrame(chart.data)
        if x_col not in df.columns or y_col not in df.columns:
            return None

        plot_type = chart.plot_type or "bar"
        fig, ax = plt.subplots(figsize=(8, 4.5))
        try:
            if plot_type == "line":
                df.plot(x=x_col, y=y_col, kind="line", ax=ax)
            elif plot_type == "scatter":
                df.plot(x=x_col, y=y_col, kind="scatter", ax=ax)
            elif plot_type == "heatmap":
                value_col = next((c for c in df.columns if c not in (x_col, y_col)), y_col)
                pivot = df.pivot_table(index=y_col, columns=x_col, values=value_col, aggfunc="mean")
                im = ax.imshow(pivot, cmap="Blues", aspect="auto")
                fig.colorbar(im, ax=ax)
                ax.set_xticks(range(len(pivot.columns)))
                ax.set_xticklabels(pivot.columns, rotation=45, ha="right")
                ax.set_yticks(range(len(pivot.index)))
                ax.set_yticklabels(pivot.index)
            elif plot_type == "boxplot":
                df.boxplot(column=y_col, by=x_col, ax=ax)
                fig.suptitle("")  # pandas' boxplot adds its own default suptitle — drop it, ax.set_title below covers it
            else:  # bar (default)
                df.plot(x=x_col, y=y_col, kind="bar", ax=ax)

            ax.set_title(chart.title or "")
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110)
            return buf.getvalue()
        except Exception:
            logger.exception(
                "Failed to pre-render chart {!r} for ipynb export — falling "
                "back to code-only for this cell.", chart.title,
            )
            return None
        finally:
            plt.close(fig)

    @staticmethod
    def _chart_result_to_model(
        result: Any,
        max_rows_per_chart=500
    ) -> NotebookChart:
        hypothesis = result.hypothesis
        sql_spec = result.sql_spec
        df = result.data
        total = 0 if df is None else len(df)
        records = []
        if df is not None and not df.empty:
            records = json.loads(df.head(max_rows_per_chart).to_json(orient="records", date_format="iso"))
        
        return NotebookChart(
            id=hypothesis.id,
            title=hypothesis.title or hypothesis.question,
            question=hypothesis.question,
            plot_type=hypothesis.plot_type or "bar",
            sql=sql_spec.sql if sql_spec else "",
            x={"column": sql_spec.x.column, "label": sql_spec.x.label, "type": sql_spec.x.type} if sql_spec else {},
            y={"column": sql_spec.y.column, "label": sql_spec.y.label, "type": sql_spec.y.type} if sql_spec else {},
            group_by=sql_spec.group_by if sql_spec else None,
            data=records,
            observation=result.observation or "",
            error=result.error,
            total_row_count=total,
            truncated=total > max_rows_per_chart,
        )