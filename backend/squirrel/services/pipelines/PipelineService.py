#!/usr/bin/python
"""
Pipeline Service
================

Owns the Phase 1 ETL feature end-to-end:

- CRUD for pipeline definitions (always validated by
  :class:`~squirrel.services.pipelines.compiler.PipelineCompiler`).
- Human approval before the first production run.
- Run history in ``pipeline_runs``, with durable job/event logging delegated
  to :class:`~squirrel.services.jobs.JobService`.
- Deterministic batch execution into a Postgres table or MinIO CSV object.
- Optional materialisation of a Postgres sink as a notebook data source and
  dashboard cell, reusing the existing notebook engine rather than building a
  second dashboard system.

Nothing in a pipeline definition is executed as arbitrary Python. Source rows
are copied into short-lived staging tables; transforms compile to a single
parameterised SQL statement; sinks are restricted by configuration.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from __future__ import annotations

import io
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from loguru import logger

# Config
from squirrel.core.config import settings

# Constants
from squirrel.constants.etl import (
    PipelineRunStatus,
    PipelineStatus,
    RESERVED_SINK_TABLES,
    RESERVED_SOURCE_PREFIXES,
    SinkKind,
    SinkMode,
    SourceKind,
    StepType,
)
from squirrel.constants.jobs import JobType

# Schemas
from squirrel.schemas.etl import PipelineDefinition, PipelineRead, PipelineRunRead

# Services
from squirrel.services.data_connector.DataConnectorService import DataConnectorService
from squirrel.services.jobs.JobService import JobService
from squirrel.services.pipelines.compiler import (
    CompiledPipeline,
    PipelineCompiler,
    PipelineValidationError,
    quote_identifier,
)
from squirrel.services.pipelines.schema import PIPELINE_DDL_STATEMENTS
from squirrel.services.storage.blob.MinIOService import MinIOService
from squirrel.services.storage.database.PostgresService import PostgresService


class PipelineNotFoundError(Exception):
    """Raised when a pipeline id does not exist for the requesting owner."""


class PipelineRunNotFoundError(Exception):
    """Raised when a run id does not exist for the requesting owner."""


class PipelineNotApprovedError(Exception):
    """Raised when a production run is attempted before human approval."""


class PipelineService:
    """Create, approve, run, and inspect ETL pipelines."""

    _PIPELINES_TABLE = "pipelines"
    _RUNS_TABLE = "pipeline_runs"

    def __init__(
        self,
        db: Optional[PostgresService] = None,
        connector: Optional[DataConnectorService] = None,
        minio: Optional[MinIOService] = None,
        jobs: Optional[JobService] = None,
        compiler: Optional[PipelineCompiler] = None,
        notebook: Optional[Any] = None,
    ) -> None:
        self._db = db
        self._connector = connector
        self._minio = minio
        self._jobs = jobs
        self._compiler = compiler or PipelineCompiler(max_steps=settings.ETL_MAX_STEPS)
        self._notebook = notebook
        self._tables_ready = False

    # ------------------------------------------------------------------
    # Bootstrap / dependencies

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            self._ensure_tables(self._db)
            self._tables_ready = True
        return self._db

    def _ensure_tables(self, db: PostgresService) -> None:
        # ``pipeline_runs.job_id`` references ``jobs``, so make sure the Phase 0
        # tables exist when the service bootstraps outside Alembic.
        from squirrel.services.jobs.schema import JOB_DDL_STATEMENTS

        for statement in (*JOB_DDL_STATEMENTS, *PIPELINE_DDL_STATEMENTS):
            # Each DDL constant bundles a CREATE TABLE with its indexes. Split
            # them so the driver never receives multiple commands in one
            # extended-protocol execute.
            for part in statement.split(";"):
                if part.strip():
                    db.execute(part, params={}, fetch=False)

    def _get_jobs(self) -> JobService:
        if self._jobs is None:
            self._jobs = JobService(db=self._get_db())
        return self._jobs

    def _get_connector(self) -> DataConnectorService:
        if self._connector is None:
            self._connector = DataConnectorService()
        return self._connector

    def _get_minio(self) -> MinIOService:
        if self._minio is None:
            self._minio = MinIOService()
        return self._minio

    def _get_notebook(self) -> Any:
        if self._notebook is None:
            from squirrel.services.notebook.NotebookService import NotebookService

            self._notebook = NotebookService()
        return self._notebook

    # ------------------------------------------------------------------
    # Pipeline CRUD

    def create_pipeline(
        self,
        owner_user_id: Optional[str],
        name: str,
        definition: PipelineDefinition,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and store a new pipeline awaiting human approval."""
        self._compiler.validate_structure(definition)
        pipeline_id = str(uuid.uuid4())
        row = self._get_db().query(
            """
            INSERT INTO pipelines
                (id, owner_user_id, name, description, status, definition, version)
            VALUES
                (%(id)s, %(owner_user_id)s, %(name)s, %(description)s,
                 %(status)s, %(definition)s::jsonb, 1)
            RETURNING *
            """,
            params={
                "id": pipeline_id,
                "owner_user_id": owner_user_id,
                "name": name,
                "description": description,
                "status": PipelineStatus.PENDING_APPROVAL,
                "definition": json.dumps(definition.model_dump(), default=str),
            },
            fetch=True,
        )
        stored = (row or {}).get("rows", [{}])[0]
        logger.info(
            "Created pipeline {} (owner={}, status={})",
            pipeline_id, owner_user_id, PipelineStatus.PENDING_APPROVAL,
        )
        return stored

    def list_pipelines(
        self,
        owner_user_id: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        where = ""
        if owner_user_id is not None:
            where = "WHERE owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self._get_db().query(
            f"SELECT * FROM pipelines {where} ORDER BY created_at DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            params=params,
            fetch=True,
        )
        return (result or {}).get("rows") or []

    def get_pipeline(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"id": pipeline_id}
        owner_clause = ""
        if owner_user_id is not None:
            owner_clause = "AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self._get_db().query(
            f"SELECT * FROM pipelines WHERE id = %(id)s {owner_clause} LIMIT 1",
            params=params,
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineNotFoundError(pipeline_id)
        return rows[0]

    def update_pipeline(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str],
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        definition: Optional[PipelineDefinition] = None,
    ) -> Dict[str, Any]:
        """
        Update a pipeline. Any definition change resets approval, because the
        approved artifact is the definition itself — not the pipeline id.
        """
        existing = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        if name is None and description is None and definition is None:
            return existing

        sets: List[str] = []
        params: Dict[str, Any] = {"id": pipeline_id}
        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if description is not None:
            sets.append("description = %(description)s")
            params["description"] = description
        if definition is not None:
            self._compiler.validate_structure(definition)
            sets.extend(
                [
                    "definition = %(definition)s::jsonb",
                    "version = version + 1",
                    "status = %(status)s",
                    "approved_at = NULL",
                    "approved_by = NULL",
                ]
            )
            params["definition"] = json.dumps(definition.model_dump(), default=str)
            params["status"] = PipelineStatus.PENDING_APPROVAL
        sets.append("updated_at = NOW()")

        result = self._get_db().query(
            f"UPDATE pipelines SET {', '.join(sets)} WHERE id = %(id)s "
            "RETURNING *",
            params=params,
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineNotFoundError(pipeline_id)
        return rows[0]

    def delete_pipeline(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str] = None,
    ) -> None:
        self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        params: Dict[str, Any] = {"id": pipeline_id}
        owner_clause = ""
        if owner_user_id is not None:
            owner_clause = " AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        self._get_db().query(
            f"DELETE FROM pipelines WHERE id = %(id)s{owner_clause}",
            params=params,
        )

    def approve_pipeline(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record explicit human approval for the current definition version."""
        self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        result = self._get_db().query(
            """
            UPDATE pipelines
            SET status = %(status)s,
                approved_at = NOW(),
                approved_by = %(approved_by)s,
                updated_at = NOW()
            WHERE id = %(id)s
            RETURNING *
            """,
            params={
                "id": pipeline_id,
                "status": PipelineStatus.APPROVED,
                "approved_by": approved_by or owner_user_id,
            },
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineNotFoundError(pipeline_id)
        logger.info("Approved pipeline {} by {}", pipeline_id, approved_by or owner_user_id)
        return rows[0]

    # ------------------------------------------------------------------
    # Runs

    def create_run(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str],
        *,
        dry_run: bool = False,
        enqueue: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a durable run row + job, then enqueue it.

        A production run requires the pipeline to be approved. Dry runs are
        always allowed and never write to the destination.
        """
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        if not dry_run and pipeline.get("status") != PipelineStatus.APPROVED:
            raise PipelineNotApprovedError(
                "This pipeline has not been approved. Run a dry run, review the "
                "plan, then approve it before the first production run."
            )

        run_id = str(uuid.uuid4())
        job = self._get_jobs().create_job(
            job_type=JobType.ETL_PIPELINE,
            owner_user_id=owner_user_id,
            payload={
                "run_id": run_id,
                "pipeline_id": pipeline_id,
                "dry_run": dry_run,
            },
        )
        job_id = job.get("id")

        result = self._get_db().query(
            """
            INSERT INTO pipeline_runs
                (id, pipeline_id, owner_user_id, job_id, status, dry_run)
            VALUES
                (%(id)s, %(pipeline_id)s, %(owner_user_id)s, %(job_id)s,
                 %(status)s, %(dry_run)s)
            RETURNING *
            """,
            params={
                "id": run_id,
                "pipeline_id": pipeline_id,
                "owner_user_id": owner_user_id,
                "job_id": job_id,
                "status": PipelineRunStatus.QUEUED,
                "dry_run": dry_run,
            },
            fetch=True,
        )
        run = (result or {}).get("rows", [{}])[0]

        if enqueue and job_id:
            try:
                from squirrel.tasks.jobs import run_job

                run_job.delay(job_id)
            except Exception:  # noqa: BLE001 - the run row stays visible and retryable
                logger.exception("Failed to enqueue ETL run {}", run_id)

        return run

    def get_run(
        self,
        run_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"id": run_id}
        owner_clause = ""
        if owner_user_id is not None:
            owner_clause = "AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self._get_db().query(
            f"SELECT * FROM pipeline_runs WHERE id = %(id)s {owner_clause} LIMIT 1",
            params=params,
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineRunNotFoundError(run_id)
        return rows[0]

    def list_runs(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        result = self._get_db().query(
            """
            SELECT * FROM pipeline_runs
            WHERE pipeline_id = %(pipeline_id)s
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params={"pipeline_id": pipeline_id, "limit": limit, "offset": offset},
            fetch=True,
        )
        return (result or {}).get("rows") or []

    # ------------------------------------------------------------------
    # Execution

    def execute_run(
        self,
        run_id: str,
        *,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute one run row. Called by the Celery job handler.

        Dry runs resolve source metadata and compile the plan, but never write
        to a sink. Production runs materialise sources, execute the compiled
        statement, and persist the outcome on the run row.
        """
        run = self.get_run(run_id)
        pipeline = self.get_pipeline(run["pipeline_id"])
        definition = self._parse_definition(pipeline.get("definition"))
        owner_user_id = run.get("owner_user_id")
        dry_run = bool(run.get("dry_run"))

        self._set_run_status(run_id, PipelineRunStatus.RUNNING)

        staging: List[str] = []
        try:
            source_schemas = self._resolve_source_schemas(definition, owner_user_id)
            compiled = self._compiler.compile(definition, source_schemas)

            if dry_run:
                result = self._dry_run_result(compiled)
                self._set_run_status(run_id, PipelineRunStatus.SUCCEEDED, result=result)
                return result

            source_tables: Dict[str, str] = {}
            for step in compiled.sources:
                table, is_staging = self._materialize_source(
                    step, run_id, owner_user_id
                )
                source_tables[step.id] = table
                if is_staging:
                    staging.append(table)

            sql, params = compiled.render(source_tables)
            sink_result = self._execute_sink(compiled, sql, params)

            result: Dict[str, Any] = {
                "sink": sink_result,
                "columns": compiled.sink_columns,
                "steps": [
                    {"id": s.id, "type": s.type, "columns": s.columns}
                    for s in [*compiled.sources, *compiled.transforms]
                ],
                "row_count": sink_result.get("row_count"),
            }

            if definition.materialize and compiled.sink.kind == SinkKind.POSTGRES:
                try:
                    result["materialization"] = self._materialize_dashboard(
                        definition.materialize, sink_result, owner_user_id
                    )
                except Exception as exc:  # noqa: BLE001 - sink already succeeded
                    logger.exception("Dashboard materialisation failed for run {}", run_id)
                    result["materialization_error"] = str(exc)

            self._set_run_status(
                run_id,
                PipelineRunStatus.SUCCEEDED,
                row_count=sink_result.get("row_count"),
                result=result,
            )
            logger.info(
                "ETL run {} succeeded (pipeline={}, rows={})",
                run_id, pipeline.get("id"), sink_result.get("row_count"),
            )
            return result

        except Exception as exc:
            logger.exception("ETL run {} failed", run_id)
            self._set_run_status(run_id, PipelineRunStatus.FAILED, error=str(exc))
            raise
        finally:
            self._drop_staging(staging)

    # ------------------------------------------------------------------
    # Source resolution / materialisation

    def _resolve_source_schemas(
        self,
        definition: PipelineDefinition,
        owner_user_id: Optional[str],
    ) -> Dict[str, List[str]]:
        """Resolve the columns available from every source step."""
        schemas: Dict[str, List[str]] = {}
        for step in definition.steps:
            if step.type != StepType.SOURCE or step.source is None:
                continue
            source = step.source

            if source.kind == SourceKind.CONNECTOR:
                connector = self._get_connector()
                assert source.connector_id and source.table
                query = connector.build_table_query(
                    source.connector_id,
                    source.table,
                    columns=source.columns or None,
                    owner_user_id=owner_user_id,
                )
                frame = connector.preview_dataframe(
                    source.connector_id,
                    query,
                    limit=1,
                    owner_user_id=owner_user_id,
                )
                schemas[step.id] = list(frame.columns)

            elif source.kind == SourceKind.FILE:
                assert source.file_url
                stored = self._get_minio().retrieve_file(source.file_url)
                if stored is None or stored.fileByte is None:
                    raise PipelineValidationError(
                        f"Could not read file source '{source.file_url}'."
                    )
                frame = pd.read_csv(io.BytesIO(stored.fileByte), nrows=0)
                schemas[step.id] = self._normalize_columns(list(frame.columns))

            elif source.kind == SourceKind.TABLE:
                assert source.table_name
                self._validate_source_table(source.table_name)
                result = self._get_db().query(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %(table_name)s
                    ORDER BY ordinal_position
                    """,
                    params={"table_name": source.table_name},
                    fetch=True,
                )
                columns = [
                    row["column_name"]
                    for row in (result or {}).get("rows", [])
                ]
                if not columns:
                    raise PipelineValidationError(
                        f"Source table '{source.table_name}' does not exist or has no columns."
                    )
                schemas[step.id] = columns

        return schemas

    def _materialize_source(
        self,
        step: Any,
        run_id: str,
        owner_user_id: Optional[str],
    ) -> Tuple[str, bool]:
        """
        Return ``(table_name, is_staging)`` for a compiled source step.

        Connector/file sources are copied into a short-lived staging table so
        the rest of the pipeline runs against the app database. App-table
        sources are read in place.
        """
        source = step.source
        assert source is not None

        if source.kind == SourceKind.TABLE:
            assert source.table_name
            self._validate_source_table(source.table_name)
            return source.table_name, False

        if source.kind == SourceKind.CONNECTOR:
            connector = self._get_connector()
            assert source.connector_id and source.table
            query = connector.build_table_query(
                source.connector_id,
                source.table,
                columns=source.columns or None,
                owner_user_id=owner_user_id,
            )
            frame = connector.fetch_dataframe(
                source.connector_id,
                query,
                owner_user_id=owner_user_id,
            )
        elif source.kind == SourceKind.FILE:
            assert source.file_url
            stored = self._get_minio().retrieve_file(source.file_url)
            if stored is None or stored.fileByte is None:
                raise PipelineValidationError(
                    f"Could not read file source '{source.file_url}'."
                )
            frame = pd.read_csv(io.BytesIO(stored.fileByte))
            frame.columns = self._normalize_columns(list(frame.columns))
        else:  # pragma: no cover - schema validation already rejects this
            raise PipelineValidationError(f"Unsupported source kind '{source.kind}'.")

        if len(frame) > settings.ETL_MAX_SOURCE_ROWS:
            raise PipelineValidationError(
                f"Source step '{step.id}' returned {len(frame)} rows, above the "
                f"configured limit of {settings.ETL_MAX_SOURCE_ROWS}."
            )

        staging_table = self._staging_name(run_id, step.id)
        self._write_dataframe(frame, staging_table)
        return staging_table, True

    def _write_dataframe(self, frame: pd.DataFrame, table_name: str) -> None:
        """
        Copy a DataFrame into the app database.

        Unlike :meth:`PostgresService.load`, this preserves connector column
        names verbatim — the compiler already validated them as identifiers, and
        normalising them here would make a case-sensitive connector column
        unreachable from the compiled SQL.
        """
        from sqlalchemy import create_engine

        db = self._get_db()
        connection_string = getattr(db, "connection_string", None) or settings.database_url
        if not connection_string:
            raise RuntimeError("No database connection string is configured for ETL staging.")

        engine = create_engine(
            connection_string.replace("postgresql://", "postgresql+psycopg://")
        )
        try:
            frame.to_sql(
                table_name,
                engine,
                if_exists="replace",
                index=False,
                chunksize=10_000,
                method=PostgresService._copy,
            )
        finally:
            engine.dispose()

    @staticmethod
    def _normalize_columns(columns: Sequence[str]) -> List[str]:
        """Turn CSV headers into the identifiers the DSL can reference."""
        return [
            str(column).strip().lower().replace(" ", "_").replace("-", "_")
            for column in columns
        ]

    def _validate_source_table(self, table_name: str) -> None:
        quote_identifier(table_name)
        if table_name in RESERVED_SINK_TABLES or table_name.startswith(RESERVED_SOURCE_PREFIXES):
            raise PipelineValidationError(
                f"'{table_name}' is a platform-owned table and cannot be an ETL source."
            )

        allowed = settings.etl_allowed_source_tables
        if allowed:
            if table_name not in allowed:
                raise PipelineValidationError(
                    f"Source table '{table_name}' is not in ETL_ALLOWED_SOURCE_TABLES."
                )
            return

        if not any(table_name.startswith(prefix) for prefix in settings.etl_source_table_prefixes):
            raise PipelineValidationError(
                f"Source table '{table_name}' must start with one of "
                f"{settings.etl_source_table_prefixes}. Set ETL_ALLOWED_SOURCE_TABLES "
                "to allow other explicit names."
            )

    @staticmethod
    def _staging_name(run_id: str, step_id: str) -> str:
        # Postgres identifiers cap at 63 bytes; keep the run prefix short.
        suffix = uuid.uuid4().hex[:8]
        return f"etl_stage_{run_id[:8]}_{step_id}"[:46] + f"_{suffix}"

    def _drop_staging(self, tables: Sequence[str]) -> None:
        for table in tables:
            try:
                self._get_db().query(
                    f"DROP TABLE IF EXISTS {quote_identifier(table)} CASCADE"
                )
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.exception("Failed to drop ETL staging table {}", table)

    # ------------------------------------------------------------------
    # Sinks

    def _execute_sink(
        self,
        compiled: CompiledPipeline,
        sql: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        sink = compiled.sink
        if sink.kind == SinkKind.POSTGRES:
            assert sink.table
            target = quote_identifier(sink.table)
            if sink.mode == SinkMode.REPLACE:
                self._get_db().query(f"DROP TABLE IF EXISTS {target} CASCADE")
                self._get_db().query(
                    f"CREATE TABLE {target} AS {sql}",
                    params=params,
                    fetch=False,
                )
            else:
                self._get_db().query(
                    f"CREATE TABLE IF NOT EXISTS {target} AS {sql} WITH NO DATA",
                    params=params,
                    fetch=False,
                )
                self._get_db().query(
                    f"INSERT INTO {target} {sql}",
                    params=params,
                    fetch=False,
                )
            return {
                "kind": SinkKind.POSTGRES,
                "table": sink.table,
                "mode": sink.mode,
                "row_count": self._count_rows(sink.table),
            }

        assert sink.kind == SinkKind.MINIO and sink.object_key
        result = self._get_db().query(sql, params=params, fetch=True)
        rows = (result or {}).get("rows") or []
        frame = pd.DataFrame(rows)

        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".csv", delete=False, mode="w", encoding="utf-8", newline=""
            ) as tmp:
                frame.to_csv(tmp.name, index=False)
                tmp_path = tmp.name
            uploaded = self._get_minio().upload_file(tmp_path, sink.object_key)
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        if uploaded is None:
            raise RuntimeError(f"Failed to upload ETL output to '{sink.object_key}'.")

        return {
            "kind": SinkKind.MINIO,
            "object_key": sink.object_key,
            "file_url": uploaded.fileUrl,
            "row_count": len(rows),
        }

    def _count_rows(self, table: str) -> Optional[int]:
        try:
            result = self._get_db().query(
                f"SELECT COUNT(*) AS cnt FROM {quote_identifier(table)}",
                fetch=True,
            )
            rows = (result or {}).get("rows") or []
            return int(rows[0]["cnt"]) if rows else None
        except Exception:  # noqa: BLE001 - count is diagnostic, not critical
            logger.exception("Failed to count ETL sink rows in {}", table)
            return None

    # ------------------------------------------------------------------
    # Materialisation

    def materialize_pipeline(
        self,
        pipeline_id: str,
        notebook_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Expose the latest successful Postgres sink as a notebook dashboard."""
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        definition = self._parse_definition(pipeline.get("definition"))
        runs = self.list_runs(pipeline_id, owner_user_id=owner_user_id, limit=50)
        successful = next(
            (
                run for run in runs
                if run.get("status") == PipelineRunStatus.SUCCEEDED
                and not run.get("dry_run")
                and (run.get("result") or {}).get("sink", {}).get("kind") == SinkKind.POSTGRES
            ),
            None,
        )
        if successful is None:
            raise PipelineValidationError(
                "This pipeline has no successful Postgres run to materialise."
            )
        sink_result = successful["result"]["sink"]
        spec = definition.materialize
        return self._materialize_dashboard(
            spec,
            sink_result,
            owner_user_id,
            notebook_id=notebook_id,
            pipeline_name=pipeline.get("name") or "ETL pipeline",
        )

    def _materialize_dashboard(
        self,
        spec: Any,
        sink_result: Dict[str, Any],
        owner_user_id: Optional[str],
        *,
        notebook_id: Optional[str] = None,
        pipeline_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        notebook_id = notebook_id or getattr(spec, "notebook_id", None)
        if not notebook_id:
            raise PipelineValidationError("No notebook_id was supplied for materialisation.")

        table = sink_result.get("table")
        if not table:
            raise PipelineValidationError("The run did not produce a Postgres table to materialise.")

        notebook = self._get_notebook()
        notebook.add_managed_table_data_source(
            notebook_id,
            table,
            label=f"{pipeline_name or 'ETL'} — {table}",
            owner_user_id=owner_user_id,
        )
        cell = None
        if getattr(spec, "create_dashboard_cell", True):
            cell = notebook.create_table_dashboard_cell(
                notebook_id,
                table,
                title=f"{pipeline_name or 'ETL'} · {table}",
                owner_user_id=owner_user_id,
            )
        return {
            "notebook_id": notebook_id,
            "table": table,
            "dashboard_cell_id": getattr(cell, "id", None),
        }

    # ------------------------------------------------------------------
    # Row helpers

    def _set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        row_count: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in PipelineRunStatus.ALL:
            raise ValueError(f"Unknown pipeline run status: {status!r}")
        updated = self._get_db().query(
            """
            UPDATE pipeline_runs
            SET status      = %(status)s,
                row_count   = COALESCE(%(row_count)s, row_count),
                result      = COALESCE(%(result)s::jsonb, result),
                error       = %(error)s,
                started_at  = CASE
                                WHEN %(status)s = 'running'
                                THEN COALESCE(started_at, NOW())
                                ELSE started_at
                              END,
                finished_at = CASE
                                WHEN %(status)s IN ('succeeded', 'failed', 'cancelled')
                                THEN NOW()
                                ELSE finished_at
                              END,
                updated_at  = NOW()
            WHERE id = %(id)s
            RETURNING *
            """,
            params={
                "id": run_id,
                "status": status,
                "row_count": row_count,
                "result": json.dumps(result, default=str) if result is not None else None,
                "error": error,
            },
            fetch=True,
        )
        rows = (updated or {}).get("rows") or []
        if not rows:
            raise PipelineRunNotFoundError(run_id)
        return rows[0]

    @staticmethod
    def _parse_definition(raw: Any) -> PipelineDefinition:
        if isinstance(raw, str):
            raw = json.loads(raw)
        return PipelineDefinition(**(raw or {}))

    @staticmethod
    def _dry_run_result(compiled: CompiledPipeline) -> Dict[str, Any]:
        return {
            "dry_run": True,
            "sink": compiled.sink.model_dump(),
            "columns": compiled.sink_columns,
            "steps": [
                {"id": step.id, "type": step.type, "columns": step.columns}
                for step in [*compiled.sources, *compiled.transforms]
            ],
        }

    @staticmethod
    def to_pipeline_read(row: Dict[str, Any]) -> PipelineRead:
        definition = PipelineService._parse_definition(row.get("definition"))
        return PipelineRead(**{**row, "definition": definition})

    @staticmethod
    def to_run_read(row: Dict[str, Any]) -> PipelineRunRead:
        result = row.get("result")
        if isinstance(result, str):
            result = json.loads(result)
        return PipelineRunRead(**{**row, "result": result})
