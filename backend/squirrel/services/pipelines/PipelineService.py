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

import calendar
import io
import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from loguru import logger

# Config
from squirrel.core.config import settings

# Constants
from squirrel.constants.etl import (
    DASHBOARD_TASK_ID,
    ML_TASK_ID,
    MLMode,
    ScheduleMode,
    TaskState,
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
from squirrel.schemas.etl import (
    MLModelRead,
    MLSpec,
    MaterializeSpec,
    PipelineDashboardRead,
    PipelineDefinition,
    PipelineRead,
    PipelineRunRead,
    ScheduleSpec,
    SourceSpec,
)

# Services
from squirrel.services.data_connector.DataConnectorService import DataConnectorService
from squirrel.services.jobs.JobService import JobService
from squirrel.services.pipelines.compiler import (
    CompiledPipeline,
    PipelineCompiler,
    PipelineValidationError,
    quote_identifier,
)
from squirrel.services.pipelines.MLTrainer import MLTrainer
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
    # Scheduling

    def update_schedule(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str],
        schedule: ScheduleSpec,
    ) -> Dict[str, Any]:
        """Replace a pipeline's automatic schedule and compute its next run."""
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        enabled = bool(schedule.enabled) and schedule.mode != ScheduleMode.MANUAL
        if enabled and pipeline.get("status") != PipelineStatus.APPROVED:
            raise PipelineValidationError(
                "Approve the pipeline before enabling an automatic schedule."
            )

        next_run = self.compute_next_run(schedule, enabled=enabled)
        result = self._get_db().query(
            """
            UPDATE pipelines
            SET schedule_mode           = %(mode)s,
                schedule_enabled        = %(enabled)s,
                schedule_time           = %(time)s,
                schedule_day            = %(day)s,
                schedule_month          = %(month)s,
                schedule_interval_seconds = %(interval)s,
                next_run_at             = %(next_run)s,
                updated_at              = NOW()
            WHERE id = %(id)s
            RETURNING *
            """,
            params={
                "id": pipeline_id,
                "mode": schedule.mode,
                "enabled": enabled,
                "time": schedule.time,
                "day": schedule.day,
                "month": schedule.month,
                "interval": schedule.interval_seconds,
                "next_run": next_run,
            },
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineNotFoundError(pipeline_id)
        logger.info(
            "Updated schedule for pipeline {} (mode={}, enabled={}, next_run={})",
            pipeline_id, schedule.mode, enabled, next_run,
        )
        return rows[0]

    @staticmethod
    def compute_next_run(
        schedule: ScheduleSpec,
        *,
        enabled: Optional[bool] = None,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Return the next UTC run time for *schedule*, or ``None`` if manual."""
        is_enabled = schedule.enabled if enabled is None else enabled
        if not is_enabled or schedule.mode == ScheduleMode.MANUAL:
            return None

        now = now or datetime.now(timezone.utc)
        if schedule.mode == ScheduleMode.CONTINUOUS:
            return now + timedelta(seconds=int(schedule.interval_seconds))

        try:
            hour, minute = (int(part) for part in schedule.time.split(":"))
        except (AttributeError, ValueError):
            hour, minute = 2, 0

        if schedule.mode == ScheduleMode.DAILY:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate

        if schedule.mode == ScheduleMode.MONTHLY:
            year, month = now.year, now.month
            for _ in range(24):
                day = min(int(schedule.day), calendar.monthrange(year, month)[1])
                candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                if candidate > now:
                    return candidate
                month += 1
                if month > 12:
                    month, year = 1, year + 1
            return None

        # Yearly
        month = max(1, min(12, int(schedule.month)))
        for year in range(now.year, now.year + 5):
            day = min(int(schedule.day), calendar.monthrange(year, month)[1])
            candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            if candidate > now:
                return candidate
        return None

    def dispatch_due_pipelines(self, limit: int = 50) -> Dict[str, int]:
        """
        Queue every approved pipeline whose ``next_run_at`` has passed.

        Called by the Celery beat task. Unapproved pipelines and pipelines that
        already have a queued/running run are skipped, and their schedule is
        advanced so they are not retried every dispatcher tick.
        """
        now = datetime.now(timezone.utc)
        limit = max(1, min(200, int(limit)))
        result = self._get_db().query(
            """
            SELECT * FROM pipelines
            WHERE schedule_enabled
              AND schedule_mode <> %(manual)s
              AND next_run_at IS NOT NULL
              AND next_run_at <= %(now)s
            ORDER BY next_run_at ASC
            LIMIT %(limit)s
            """,
            params={"manual": ScheduleMode.MANUAL, "now": now, "limit": limit},
            fetch=True,
        )

        dispatched = skipped = failed = 0
        for pipeline in (result or {}).get("rows") or []:
            pipeline_id = pipeline.get("id")
            try:
                schedule = ScheduleSpec(
                    mode=pipeline.get("schedule_mode") or ScheduleMode.MANUAL,
                    enabled=True,
                    time=pipeline.get("schedule_time") or "02:00",
                    day=int(pipeline.get("schedule_day") or 1),
                    month=int(pipeline.get("schedule_month") or 1),
                    interval_seconds=int(pipeline.get("schedule_interval_seconds") or 300),
                )
                next_run = self.compute_next_run(schedule, enabled=True, now=now)

                if pipeline.get("status") != PipelineStatus.APPROVED:
                    skipped += 1
                    self._set_next_run(pipeline_id, next_run)
                    continue
                if self._has_active_run(pipeline_id):
                    skipped += 1
                    self._set_next_run(pipeline_id, next_run)
                    continue

                self.create_run(
                    pipeline_id,
                    pipeline.get("owner_user_id"),
                    dry_run=False,
                    enqueue=True,
                )
                self._set_next_run(pipeline_id, next_run, ran=True)
                dispatched += 1
            except Exception:  # noqa: BLE001 - one bad pipeline must not stop the rest
                failed += 1
                logger.exception("Failed to dispatch scheduled pipeline {}", pipeline_id)

        if dispatched or skipped or failed:
            logger.info(
                "Scheduled pipeline dispatch: dispatched={}, skipped={}, failed={}",
                dispatched, skipped, failed,
            )
        return {"dispatched": dispatched, "skipped": skipped, "failed": failed}

    def _has_active_run(self, pipeline_id: str) -> bool:
        result = self._get_db().query(
            """
            SELECT 1 AS active FROM pipeline_runs
            WHERE pipeline_id = %(pipeline_id)s
              AND status IN (%(queued)s, %(running)s)
            LIMIT 1
            """,
            params={
                "pipeline_id": pipeline_id,
                "queued": PipelineRunStatus.QUEUED,
                "running": PipelineRunStatus.RUNNING,
            },
            fetch=True,
        )
        return bool((result or {}).get("rows"))

    def _set_next_run(
        self,
        pipeline_id: str,
        next_run: Optional[datetime],
        *,
        ran: bool = False,
    ) -> None:
        self._get_db().query(
            """
            UPDATE pipelines
            SET next_run_at = %(next_run)s,
                last_scheduled_at = CASE WHEN %(ran)s THEN NOW() ELSE last_scheduled_at END,
                updated_at = NOW()
            WHERE id = %(id)s
            """,
            params={"id": pipeline_id, "next_run": next_run, "ran": ran},
            fetch=False,
        )

    # ------------------------------------------------------------------
    # Model registry

    def list_models(
        self,
        owner_user_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Registered ETL models, newest first."""
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        if owner_user_id is not None:
            clauses.append("m.owner_user_id = %(owner_user_id)s")
            params["owner_user_id"] = owner_user_id
        if pipeline_id:
            clauses.append("m.pipeline_id = %(pipeline_id)s")
            params["pipeline_id"] = pipeline_id
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        result = self._get_db().query(
            f"""
            SELECT m.*, p.name AS pipeline_name
            FROM etl_models m
            LEFT JOIN pipelines p ON p.id = m.pipeline_id
            {where}
            ORDER BY m.created_at DESC
            """,
            params=params,
            fetch=True,
        )
        return (result or {}).get("rows") or []

    def get_model(
        self,
        model_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"id": model_id}
        owner_clause = ""
        if owner_user_id is not None:
            owner_clause = "AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id
        result = self._get_db().query(
            f"SELECT * FROM etl_models WHERE id = %(id)s {owner_clause} LIMIT 1",
            params=params,
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise PipelineValidationError(f"Model '{model_id}' not found.")
        return rows[0]

    def _resolve_ml_model(
        self,
        spec: MLSpec,
        owner_user_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if spec.mode not in (MLMode.RETRAIN, MLMode.INFERENCE):
            return None
        if not spec.model_id:
            raise ValueError(f"ML mode '{spec.mode}' requires a registered model.")
        return self.get_model(spec.model_id, owner_user_id)

    def _register_model(
        self,
        pipeline: Dict[str, Any],
        spec: MLSpec,
        outcome: Dict[str, Any],
        run_id: str,
        owner_user_id: Optional[str],
    ) -> str:
        model_id = str(uuid.uuid4())
        name = f"{pipeline.get('name') or 'ETL'} · {spec.task}"
        if spec.target:
            name = f"{name} ({spec.target})"
        result = self._get_db().query(
            """
            INSERT INTO etl_models
                (id, pipeline_id, owner_user_id, name, task, algorithm,
                 target, features, metrics, details, model_key, file_url, run_id)
            VALUES
                (%(id)s, %(pipeline_id)s, %(owner_user_id)s, %(name)s,
                 %(task)s, %(algorithm)s, %(target)s, %(features)s::jsonb,
                 %(metrics)s::jsonb, %(details)s::jsonb, %(model_key)s,
                 %(file_url)s, %(run_id)s)
            RETURNING id
            """,
            params={
                "id": model_id,
                "pipeline_id": pipeline.get("id"),
                "owner_user_id": owner_user_id,
                "name": name[:200],
                "task": outcome.get("task") or spec.task,
                "algorithm": outcome.get("algorithm"),
                "target": outcome.get("target"),
                "features": json.dumps(outcome.get("features") or []),
                "metrics": json.dumps(outcome.get("metrics") or {}, default=str),
                "details": json.dumps(outcome.get("details") or {}, default=str),
                "model_key": outcome.get("model_key"),
                "file_url": outcome.get("file_url"),
                "run_id": run_id,
            },
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        registered_id = rows[0].get("id") if rows else model_id
        logger.info("Registered model {} for pipeline {}", registered_id, pipeline.get("id"))
        return registered_id

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

        Progress is written to ``result['task_states']`` for the DAG view.
        """
        run = self.get_run(run_id)
        pipeline = self.get_pipeline(run["pipeline_id"])
        definition = self._parse_definition(pipeline.get("definition"))
        owner_user_id = run.get("owner_user_id")
        dry_run = bool(run.get("dry_run"))
        outputs = definition.outputs

        tasks = self._init_task_states(definition)
        self._set_run_status(run_id, PipelineRunStatus.RUNNING, result={"task_states": tasks})

        staging: List[str] = []
        try:
            source_schemas = self._resolve_source_schemas(definition, owner_user_id)
            compiled = self._compiler.compile(definition, source_schemas)

            if dry_run:
                for step in definition.steps:
                    self._task(run_id, tasks, step.id, TaskState.SUCCESS, flush=False)
                for pseudo in (DASHBOARD_TASK_ID, ML_TASK_ID):
                    if pseudo in tasks:
                        self._task(
                            run_id, tasks, pseudo, TaskState.SKIPPED,
                            flush=False, detail="Skipped in a dry run.",
                        )
                result = self._dry_run_result(compiled)
                result["task_states"] = tasks
                self._set_run_status(run_id, PipelineRunStatus.SUCCEEDED, result=result)
                return result

            source_tables: Dict[str, str] = {}
            for step in compiled.sources:
                self._task(run_id, tasks, step.id, TaskState.RUNNING)
                table, is_staging = self._materialize_source(
                    step, run_id, owner_user_id
                )
                source_tables[step.id] = table
                if is_staging:
                    staging.append(table)
                self._task(run_id, tasks, step.id, TaskState.SUCCESS)

            sql_ids = [s.id for s in compiled.transforms] + [compiled.sink_step.id]
            for step_id in sql_ids:
                self._task(run_id, tasks, step_id, TaskState.RUNNING, flush=False)
            self._flush_tasks(run_id, tasks)
            sql, params = compiled.render(source_tables)
            sink_result = self._execute_sink(compiled, sql, params)
            for step_id in sql_ids:
                self._task(run_id, tasks, step_id, TaskState.SUCCESS, flush=False)
            self._flush_tasks(run_id, tasks)

            result: Dict[str, Any] = {
                "sink": sink_result,
                "columns": compiled.sink_columns,
                "steps": [
                    {"id": s.id, "type": s.type, "columns": s.columns}
                    for s in [*compiled.sources, *compiled.transforms]
                ],
                "row_count": sink_result.get("row_count"),
                "task_states": tasks,
            }

            if outputs and outputs.dashboard:
                if compiled.sink.kind == SinkKind.POSTGRES:
                    self._task(run_id, tasks, DASHBOARD_TASK_ID, TaskState.RUNNING)
                    try:
                        result["materialization"] = self._materialize_dashboard(
                            definition.materialize or MaterializeSpec(),
                            sink_result,
                            owner_user_id,
                            pipeline=pipeline,
                        )
                        self._task(run_id, tasks, DASHBOARD_TASK_ID, TaskState.SUCCESS)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Dashboard materialisation failed for run {}", run_id)
                        result["materialization_error"] = str(exc)
                        self._task(run_id, tasks, DASHBOARD_TASK_ID, TaskState.FAILED, detail=str(exc))
                else:
                    self._task(
                        run_id, tasks, DASHBOARD_TASK_ID, TaskState.SKIPPED,
                        detail="Dashboards read the result from Postgres, so the sink must be a Postgres table.",
                    )
            elif definition.materialize and compiled.sink.kind == SinkKind.POSTGRES:
                try:
                    result["materialization"] = self._materialize_dashboard(
                        definition.materialize,
                        sink_result,
                        owner_user_id,
                        pipeline=pipeline,
                    )
                except Exception as exc:  # noqa: BLE001 - sink already succeeded
                    logger.exception("Dashboard materialisation failed for run {}", run_id)
                    result["materialization_error"] = str(exc)

            if outputs and outputs.ml:
                self._task(run_id, tasks, ML_TASK_ID, TaskState.RUNNING)
                try:
                    frame = self._read_table(sink_result["table"])
                    ml_spec = outputs.ml
                    model_row = self._resolve_ml_model(ml_spec, owner_user_id)
                    outcome = MLTrainer(self._get_minio()).execute(
                        frame,
                        ml_spec,
                        run_id=run_id,
                        pipeline_id=pipeline["id"],
                        model_row=model_row,
                    )
                    if ml_spec.mode in (MLMode.TRAIN, MLMode.RETRAIN):
                        outcome["model_id"] = self._register_model(
                            pipeline, ml_spec, outcome, run_id, owner_user_id
                        )
                    result["ml"] = outcome
                    self._task(run_id, tasks, ML_TASK_ID, TaskState.SUCCESS)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Model output failed for run {}", run_id)
                    result["ml_error"] = str(exc)
                    self._task(run_id, tasks, ML_TASK_ID, TaskState.FAILED, detail=str(exc))

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
            for step_id, info in tasks.items():
                if info.get("state") == TaskState.RUNNING:
                    self._task(run_id, tasks, step_id, TaskState.FAILED, flush=False, detail=str(exc))
                elif info.get("state") == TaskState.QUEUED:
                    self._task(run_id, tasks, step_id, TaskState.UPSTREAM_FAILED, flush=False)
            self._set_run_status(
                run_id, PipelineRunStatus.FAILED, error=str(exc),
                result={"task_states": tasks},
            )
            raise
        finally:
            self._drop_staging(staging)

    # ------------------------------------------------------------------
    # Source resolution / materialisation

    def list_source_tables(self) -> List[Dict[str, Any]]:
        """
        App-database tables that are valid ETL sources, with their columns.

        Mirrors :meth:`_validate_source_table` so the source picker never offers
        a table the compiler would reject.
        """
        result = self._get_db().query(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            ORDER BY table_name, ordinal_position
            """,
            fetch=True,
        )

        allowed = set(settings.etl_allowed_source_tables)
        prefixes = tuple(settings.etl_source_table_prefixes)
        grouped: Dict[str, List[Dict[str, str]]] = {}

        for row in (result or {}).get("rows", []):
            table_name = str(row.get("table_name") or "")
            if not table_name:
                continue
            if (
                table_name in RESERVED_SINK_TABLES
                or table_name.startswith(RESERVED_SOURCE_PREFIXES)
            ):
                continue
            if allowed:
                if table_name not in allowed:
                    continue
            elif not table_name.startswith(prefixes):
                continue
            grouped.setdefault(table_name, []).append(
                {
                    "name": str(row.get("column_name") or ""),
                    "data_type": str(row.get("data_type") or "text"),
                }
            )

        return [
            {"table_name": table_name, "columns": columns}
            for table_name, columns in grouped.items()
        ]

    def resolve_source_columns(
        self,
        source: SourceSpec,
        owner_user_id: Optional[str]
    ) -> List[str]:
        """Columns exposed by one source spec (ownership + allow-list checked)."""
        if source.kind == SourceKind.CONNECTOR:
            connector = self._get_connector()
            assert source.connector_id and source.table
            query = connector.build_table_query(
                source.connector_id, source.table,
                columns=source.columns or None, owner_user_id=owner_user_id,
            )
            frame = connector.preview_dataframe(
                source.connector_id, query, limit=1, owner_user_id=owner_user_id
            )
            return list(frame.columns)

        if source.kind == SourceKind.FILE:
            assert source.file_url
            stored = self._get_minio().retrieve_file(source.file_url)
            if stored is None or stored.fileByte is None:
                raise PipelineValidationError(f"Could not read file source '{source.file_url}'.")
            frame = pd.read_csv(io.BytesIO(stored.fileByte), nrows=0)
            return self._normalize_columns(list(frame.columns))

        if source.kind == SourceKind.TABLE:
            assert source.table_name
            self._validate_source_table(source.table_name)
            result = self._get_db().query(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %(table_name)s
                ORDER BY ordinal_position
                """,
                params={"table_name": source.table_name},
                fetch=True,
            )
            columns = [r["column_name"] for r in (result or {}).get("rows", [])]
            if not columns:
                raise PipelineValidationError(
                    f"Source table '{source.table_name}' does not exist or has no columns."
                )
            return columns

        raise PipelineValidationError(f"Unsupported source kind '{source.kind}'.")

    def _resolve_source_schemas(self, definition, owner_user_id):
        return {
            step.id: self.resolve_source_columns(step.source, owner_user_id)
            for step in definition.steps
            if step.type == StepType.SOURCE and step.source is not None
        }

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

    @staticmethod
    def _init_task_states(definition: PipelineDefinition) -> Dict[str, Dict[str, Any]]:
        tasks = {step.id: {"state": TaskState.QUEUED} for step in definition.steps}
        outputs = definition.outputs
        if outputs and outputs.dashboard:
            tasks[DASHBOARD_TASK_ID] = {"state": TaskState.QUEUED}
        if outputs and outputs.ml:
            tasks[ML_TASK_ID] = {"state": TaskState.QUEUED}
        return tasks

    def _task(
        self,
        run_id: str,
        tasks: Dict[str, Dict[str, Any]],
        task_id: str,
        state: str,
        *,
        flush: bool = True,
        detail: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        info = tasks.setdefault(task_id, {})
        info["state"] = state
        if state == TaskState.RUNNING:
            info["started_at"] = now
        elif state in (TaskState.SUCCESS, TaskState.FAILED):
            info["finished_at"] = now
        if detail:
            info["detail"] = detail
        if flush:
            self._flush_tasks(run_id, tasks)

    def _flush_tasks(self, run_id: str, tasks: Dict[str, Dict[str, Any]]) -> None:
        try:
            self._get_db().execute(
                """
                UPDATE pipeline_runs
                SET result = COALESCE(result, '{}'::jsonb)
                             || jsonb_build_object('task_states', %(tasks)s::jsonb),
                    updated_at = NOW()
                WHERE id = %(id)s
                """,
                params={"id": run_id, "tasks": json.dumps(tasks, default=str)},
                fetch=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record task states for run {}", run_id)

    def _read_table(self, table: str) -> pd.DataFrame:
        limit = int(getattr(settings, "ETL_ML_MAX_ROWS", 200_000)) + 1
        result = self._get_db().query(
            f"SELECT * FROM {quote_identifier(table)} LIMIT {limit}", fetch=True
        )
        return pd.DataFrame((result or {}).get("rows") or [])

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
        notebook_id: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        *,
        regenerate: bool = False,
    ) -> Dict[str, Any]:
        """
        Build (or rebuild) the dashboard for the latest successful Postgres run.

        When ``notebook_id`` is omitted the pipeline's own dashboard notebook is
        created/reused, so the caller never has to pick one.
        """
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        definition = self._parse_definition(pipeline.get("definition"))
        successful = self._latest_successful_postgres_run(pipeline_id, owner_user_id)
        if successful is None:
            raise PipelineValidationError(
                "This pipeline has no successful Postgres run to materialise."
            )
        sink_result = successful["result"]["sink"]
        target_notebook = notebook_id or pipeline.get("dashboard_notebook_id")
        cell_id = pipeline.get("dashboard_cell_id")

        if regenerate and target_notebook and cell_id:
            notebook = self._get_notebook()
            try:
                cell = notebook.update_cell(
                    target_notebook, cell_id, owner_user_id=owner_user_id
                )
                materialized = self._cell_summary(
                    cell, target_notebook, sink_result.get("table")
                )
                self._store_dashboard_refs(
                    pipeline_id, target_notebook, getattr(cell, "id", cell_id)
                )
            except Exception:  # noqa: BLE001 - fall back to a fresh cell
                logger.exception("Dashboard regeneration failed; rebuilding from scratch.")
                materialized = self._materialize_dashboard(
                    definition.materialize or MaterializeSpec(),
                    sink_result,
                    owner_user_id,
                    notebook_id=target_notebook,
                    pipeline=pipeline,
                )
        else:
            materialized = self._materialize_dashboard(
                definition.materialize or MaterializeSpec(),
                sink_result,
                owner_user_id,
                notebook_id=target_notebook,
                pipeline=pipeline,
            )

        tasks = (successful.get("result") or {}).get("task_states")
        if isinstance(tasks, dict) and DASHBOARD_TASK_ID in tasks:
            self._task(successful["id"], tasks, DASHBOARD_TASK_ID, TaskState.SUCCESS)
        return materialized

    def get_dashboard(
        self,
        pipeline_id: str,
        owner_user_id: Optional[str] = None,
    ) -> PipelineDashboardRead:
        """Return the stored dashboard for the pipeline's latest successful run."""
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        return self._dashboard_payload(pipeline, owner_user_id=owner_user_id)

    def save_dashboard_layout(
        self,
        pipeline_id: str,
        layout: Dict[str, Any],
        owner_user_id: Optional[str] = None,
    ) -> PipelineDashboardRead:
        """Persist dashboard customisations made in the pipeline modal."""
        pipeline = self.get_pipeline(pipeline_id, owner_user_id=owner_user_id)
        notebook_id = pipeline.get("dashboard_notebook_id")
        cell_id = pipeline.get("dashboard_cell_id")
        if not notebook_id or not cell_id:
            raise PipelineValidationError("This pipeline has no dashboard yet.")

        from squirrel.schemas.notebook import DashboardLayout

        self._get_notebook().save_dashboard_layout(
            notebook_id,
            cell_id,
            DashboardLayout(**(layout or {})),
            owner_user_id=owner_user_id,
        )
        return self._dashboard_payload(pipeline, owner_user_id=owner_user_id)

    def _latest_successful_postgres_run(
        self, pipeline_id: str, owner_user_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        runs = self.list_runs(pipeline_id, owner_user_id=owner_user_id, limit=50)
        return next(
            (
                run for run in runs
                if run.get("status") == PipelineRunStatus.SUCCEEDED
                and not run.get("dry_run")
                and (run.get("result") or {}).get("sink", {}).get("kind") == SinkKind.POSTGRES
            ),
            None,
        )

    def _ensure_dashboard_notebook(
        self, pipeline: Dict[str, Any], owner_user_id: Optional[str]
    ) -> str:
        """Reuse the pipeline's dashboard notebook, creating one if needed."""
        notebook = self._get_notebook()
        current = pipeline.get("dashboard_notebook_id")
        if current:
            try:
                notebook.get_notebook(current, owner_user_id=owner_user_id)
                return current
            except Exception:  # noqa: BLE001 - deleted notebook: provision a new one
                logger.warning("Dashboard notebook {} is gone; recreating it.", current)

        created = notebook.create_notebook(
            name=f"{pipeline.get('name') or 'ETL'} — dashboard"[:200],
            description="Auto-created dashboard for an ETL pipeline output.",
            owner_user_id=owner_user_id,
        )
        notebook_id = created.id
        self._get_db().query(
            "UPDATE pipelines SET dashboard_notebook_id = %(notebook_id)s, "
            "updated_at = NOW() WHERE id = %(id)s",
            params={"notebook_id": notebook_id, "id": pipeline.get("id")},
            fetch=False,
        )
        return notebook_id

    def _store_dashboard_refs(
        self,
        pipeline_id: Optional[str],
        notebook_id: str,
        cell_id: Optional[str],
    ) -> None:
        if not pipeline_id:
            return
        try:
            self._get_db().query(
                """
                UPDATE pipelines
                SET dashboard_notebook_id = %(notebook_id)s,
                    dashboard_cell_id = %(cell_id)s,
                    updated_at = NOW()
                WHERE id = %(id)s
                """,
                params={
                    "id": pipeline_id,
                    "notebook_id": notebook_id,
                    "cell_id": cell_id,
                },
                fetch=False,
            )
        except Exception:  # noqa: BLE001 - the dashboard itself already exists
            logger.exception("Failed to record dashboard refs for {}", pipeline_id)

    def _dashboard_payload(
        self,
        pipeline: Dict[str, Any],
        *,
        owner_user_id: Optional[str] = None,
    ) -> PipelineDashboardRead:
        notebook_id = pipeline.get("dashboard_notebook_id")
        if not notebook_id:
            raise PipelineValidationError(
                "No dashboard has been built for this pipeline yet."
            )

        notebook = self._get_notebook()
        cells = notebook.list_cells(notebook_id, owner_user_id=owner_user_id)
        dashboards = [cell for cell in cells if getattr(cell, "type", None) == "dashboard"]
        wanted = pipeline.get("dashboard_cell_id")
        cell = next(
            (item for item in dashboards if getattr(item, "id", None) == wanted),
            None,
        )
        if cell is None and dashboards:
            cell = dashboards[-1]
        if cell is None:
            raise PipelineValidationError(
                "This pipeline's dashboard notebook has no dashboard cell yet."
            )

        latest = self._latest_successful_postgres_run(
            pipeline["id"], owner_user_id
        )
        table = (((latest or {}).get("result") or {}).get("sink") or {}).get("table")
        layout = getattr(cell, "dashboard_layout", None)
        if layout is not None and hasattr(layout, "model_dump"):
            layout = layout.model_dump()

        return PipelineDashboardRead(
            pipeline_id=pipeline["id"],
            notebook_id=notebook_id,
            cell_id=getattr(cell, "id", None),
            table=table,
            charts=self._serialize_charts(cell),
            layout=layout,
            reply=getattr(cell, "reply", None),
            run_id=(latest or {}).get("id"),
            updated_at=getattr(cell, "created_at", None),
        )

    @staticmethod
    def _serialize_charts(cell: Any) -> List[Dict[str, Any]]:
        charts = []
        for chart in getattr(cell, "charts", None) or []:
            charts.append(chart.model_dump() if hasattr(chart, "model_dump") else chart)
        return charts

    def _cell_summary(
        self,
        cell: Any,
        notebook_id: str,
        table: Optional[str],
    ) -> Dict[str, Any]:
        layout = getattr(cell, "dashboard_layout", None) if cell is not None else None
        if layout is not None and hasattr(layout, "model_dump"):
            layout = layout.model_dump()
        return {
            "notebook_id": notebook_id,
            "table": table,
            "dashboard_cell_id": getattr(cell, "id", None),
            "charts": self._serialize_charts(cell) if cell is not None else [],
            "layout": layout,
            "reply": getattr(cell, "reply", None) if cell is not None else None,
        }

    def _materialize_dashboard(
        self,
        spec: Any,
        sink_result: Dict[str, Any],
        owner_user_id: Optional[str],
        *,
        notebook_id: Optional[str] = None,
        pipeline: Optional[Dict[str, Any]] = None,
        pipeline_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        notebook_id = notebook_id or getattr(spec, "notebook_id", None)
        if not notebook_id and pipeline is not None:
            notebook_id = self._ensure_dashboard_notebook(pipeline, owner_user_id)
        if not notebook_id:
            raise PipelineValidationError("No notebook could be provisioned for the dashboard.")

        table = sink_result.get("table")
        if not table:
            raise PipelineValidationError("The run did not produce a Postgres table to materialise.")

        pipeline_name = pipeline_name or (pipeline or {}).get("name") or "ETL"
        notebook = self._get_notebook()
        notebook_state = notebook.add_managed_table_data_source(
            notebook_id,
            table,
            label=f"{pipeline_name} — {table}",
            owner_user_id=owner_user_id,
        )
        source_ids = [
            source.id
            for source in getattr(notebook_state, "data_sources", [])
            if getattr(source, "table_name", None) == table
        ]
        cell = None
        if getattr(spec, "create_dashboard_cell", True):
            if hasattr(notebook, "run_cell"):
                cell = notebook.run_cell(
                    notebook_id=notebook_id,
                    cell_type="dashboard",
                    query=f"Generate a dashboard for ETL output table `{table}`.",
                    data_source_ids=source_ids or None,
                    owner_user_id=owner_user_id,
                )
            else:
                # Compatibility for lightweight notebook doubles and older
                # service adapters; the real NotebookService uses the agent.
                cell = notebook.create_table_dashboard_cell(
                    notebook_id,
                    table,
                    title=f"{pipeline_name} · {table}",
                    owner_user_id=owner_user_id,
                )

        self._store_dashboard_refs(
            (pipeline or {}).get("id"), notebook_id, getattr(cell, "id", None)
        )
        return self._cell_summary(cell, notebook_id, table)

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

    @staticmethod
    def to_model_read(row: Dict[str, Any]) -> MLModelRead:
        payload = dict(row)
        for key in ("features", "metrics", "details"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    payload[key] = json.loads(value)
                except json.JSONDecodeError:
                    payload[key] = None if key in ("metrics", "details") else []
        payload.setdefault("features", [])
        return MLModelRead(**payload)
