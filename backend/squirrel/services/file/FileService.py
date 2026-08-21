#!/usr/bin/python
"""
File Service
============

Shared service for recording and managing uploads in the ``file``
table so they show up on the Files page regardless of which flow (standalone
file upload, workspace input source, notebook data source) produced them.

All ``file`` reads/writes should go through this class rather than
routes issuing raw SQL directly — that keeps ownership checks, the table
schema, and the propagation-to-notebook-sources logic in one place instead
of duplicated (and potentially drifting) across callers.

Reused by squirrel.api.routes.files, WorkspaceService, and NotebookService,
all of which already own their own PostgresService/MinIOService instances.
Blob storage (MinIO) itself is intentionally NOT handled here — this class
owns metadata only, callers own the actual object storage calls.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import Any, Optional

# Services
from squirrel.services.storage.database.PostgresService import PostgresService

# Schema
from squirrel.schemas.file import File, FileRecordNotFound, FileInUseError

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# File Service class

class FileService:

    TABLE = "file"

    def __init__(self):
        """
        Initialize the FileService instance.
        """
        self.db_service = PostgresService()

        # Create the file table if it doesn't exist
        self.ensure_table()

    def ensure_table(self) -> None:
        """
        Ensure the ``file`` table exists (with a unique constraint on
        file_url, required by the ON CONFLICT clause in record_upload),
        and that it has the columns every read path in this service and in
        squirrel.api.routes.files relies on.
        """
        try:
            self.db_service.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id SERIAL PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    file_url TEXT NOT NULL UNIQUE,
                    file_type TEXT,
                    size BIGINT,
                    uploaded_at TIMESTAMPTZ,
                    workspace_ids TEXT[] NOT NULL DEFAULT '{{}}',
                    notebook_ids TEXT[] NOT NULL DEFAULT '{{}}',
                    owner_user_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """,
                params={},
                fetch=False,
            )
            # Backfill columns for tables created before these existed.
            for column, coltype in (
                ("workspace_ids", "TEXT[] NOT NULL DEFAULT '{}'"),
                ("notebook_ids", "TEXT[] NOT NULL DEFAULT '{}'"),
                ("owner_user_id", "TEXT"),
            ):
                self.db_service.execute(
                    f"ALTER TABLE {self.TABLE} ADD COLUMN IF NOT EXISTS {column} {coltype}",
                    params={},
                    fetch=False,
                )
        except Exception:
            logger.exception("Failed to create/verify {} table.", self.TABLE)
        finally:
            self.db_service.disconnect()

    # ——————————————————————————————————————————————————————————
    # Writes

    def record_upload(
        self,
        file: File,
        owner_user_id: Optional[str],
        workspace_id: Optional[str] = None,
        notebook_id: Optional[str] = None,
    ) -> None:
        """
        Persist a record of an uploaded file. On conflict (same file_url),
        only overwrite workspace_id/notebook_id/source_id when a new value
        was actually provided, so re-uploading doesn't blow away existing
        associations.
        """
        try:
            self.db_service.insert({
                "sql": (
                    f"INSERT INTO {self.TABLE} "
                    "(file_name, file_url, file_type, size, uploaded_at, "
                    " workspace_ids, notebook_ids, owner_user_id) "
                    "VALUES (%(file_name)s, %(file_url)s, %(file_type)s, %(size)s, "
                    "%(uploaded_at)s, %(workspace_ids)s, %(notebook_ids)s, %(owner_user_id)s) "
                    "ON CONFLICT (file_url) DO UPDATE SET "
                    f"workspace_ids = ARRAY(SELECT DISTINCT unnest("
                    f"{self.TABLE}.workspace_ids || EXCLUDED.workspace_ids)), "
                    f"notebook_ids = ARRAY(SELECT DISTINCT unnest("
                    f"{self.TABLE}.notebook_ids || EXCLUDED.notebook_ids)), "
                    "updated_at = NOW()"
                ),
                "params": {
                    "file_name": file.fileName,
                    "file_url": str(file.fileUrl),
                    "file_type": getattr(file, "fileType", None),
                    "size": file.size,
                    "uploaded_at": file.uploadedAt,
                    "workspace_ids": [workspace_id] if workspace_id else [],
                    "notebook_ids": [notebook_id] if notebook_id else [],
                    "owner_user_id": owner_user_id,
                },
            })
        except Exception:
            logger.exception("Failed to record upload metadata for {}", file.fileName)
        finally:
            self.db_service.disconnect()

    def rename_file(self, file_url: str, owner_user_id: str, new_name: str) -> File:
        """
        Rename a file record by ``file_url``, propagating the new name into
        any notebook data source whose ``source_file_url`` matches so the
        two tables stay in sync. Ownership is verified first.

        Raises:
            FileRecordNotFound: if no row is owned by ``owner_user_id``.
        """
        try:
            if not self.is_owned_by(file_url, owner_user_id):
                raise FileRecordNotFound(file_url)

            self.db_service.query(
                f"UPDATE {self.TABLE} SET file_name = %(file_name)s, updated_at = NOW() "
                "WHERE file_url = %(file_url)s AND owner_user_id = %(owner_user_id)s",
                params={"file_name": new_name, "file_url": file_url, "owner_user_id": owner_user_id},
            )

            # Propagate rename into any notebook data source bound to this file URL
            self.db_service.query(
                "UPDATE sqrl_notebook_data_sources SET label = %(label)s WHERE source_file_url = %(file_url)s",
                params={"label": new_name, "file_url": file_url},
            )

            row = self._get_row(file_url, owner_user_id)
            if row is None:
                raise FileRecordNotFound(file_url)
            return self.from_dict(row)
        finally:
            self.db_service.disconnect()

    def delete_file(
        self, file_url: str, owner_user_id: str) -> None:
        """
        Delete a file's DB record, but only if it's owned by the caller and
        not attached to any workspace or notebook.
        """
        try:
            row = self._get_row(file_url, owner_user_id)
            if row is None:
                raise FileRecordNotFound(file_url)

            if row.get("workspace_ids") or row.get("notebook_ids"):
                raise FileInUseError(file_url)

            self.db_service.query(
                f"DELETE FROM {self.TABLE} WHERE file_url = %(file_url)s AND owner_user_id = %(owner_user_id)s",
                params={"file_url": file_url, "owner_user_id": owner_user_id},
            )
        finally:
            self.db_service.disconnect()

    # ——————————————————————————————————————————————————————————
    # Reads

    def is_owned_by(self, file_url: str, owner_user_id: str) -> bool:
        """Return True if ``file_url`` has a row owned by ``owner_user_id``."""
        return self._get_row(file_url, owner_user_id) is not None

    def list_files(self, owner_user_id: str) -> list[File]:
        """Return every file record owned by ``owner_user_id``."""
        try:
            result = self.db_service.retrieve(self.TABLE, filters={"owner_user_id": owner_user_id})
        finally:
            self.db_service.disconnect()

        rows = (result or {}).get("rows", [])
        return [self.from_dict(row) for row in rows]

    def get_owned_file(self, file_url: str, owner_user_id: str) -> Optional[File]:
        """Return the record for ``file_url`` if owned by ``owner_user_id``, else None."""
        row = self._get_row(file_url, owner_user_id)
        return self.from_dict(row) if row else None

    # ——————————————————————————————————————————————————————————
    # Internal helpers

    def _get_row(self, file_url: str, owner_user_id: str) -> Optional[dict[str, Any]]:
        try:
            result = self.db_service.retrieve(
                self.TABLE,
                filters={"file_url": file_url, "owner_user_id": owner_user_id},
            )
        finally:
            self.db_service.disconnect()
        rows = (result or {}).get("rows", [])
        return rows[0] if rows else None

    @staticmethod
    def from_dict(row: dict[str, Any]) -> File:
        return File(
            fileUrl=row["file_url"],
            fileName=row["file_name"],
            fileType=row.get("file_type"),
            size=row.get("size"),
            uploadedAt=row.get("uploaded_at"),
            workspaceIds=row.get("workspace_ids"),
            notebookIds=row.get("notebook_ids"),
            ownerUserId=row.get("owner_user_id")
        )