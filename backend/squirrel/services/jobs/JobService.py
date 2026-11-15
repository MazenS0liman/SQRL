#!/usr/bin/python
"""
Job Service
===========

PostgreSQL-backed registry for durable background jobs and their event log.

The service is deliberately transport-agnostic: it knows how to create, read,
and update job rows, but it does not enqueue Celery tasks. Callers create the
row first, then enqueue ``squirrel.tasks.jobs.run_job`` with the returned id.
This ordering guarantees a job is visible in the API even if the broker is
temporarily unavailable.

All read methods accept an optional ``owner_user_id``; when supplied, rows
belonging to other users are indistinguishable from missing rows.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
import uuid
from typing import Any, Dict, List, Optional

# Logging
from loguru import logger

# Constants
from squirrel.constants.jobs import JobStatus

# Storage
from squirrel.services.jobs.schema import JOB_DDL_STATEMENTS
from squirrel.services.storage.database.PostgresService import PostgresService


class JobNotFoundError(Exception):
    """Raised when a job id does not exist for the requesting owner."""


class JobService:
    """Create and track durable background jobs."""

    _JOBS_TABLE = "jobs"
    _EVENTS_TABLE = "job_events"

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._db = db
        self._tables_ready = False

    # ------------------------------------------------------------------
    # Bootstrap

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            self._ensure_tables(self._db)
            self._tables_ready = True
        return self._db

    def _ensure_tables(self, db: PostgresService) -> None:
        for statement in JOB_DDL_STATEMENTS:
            db.execute(statement, params={}, fetch=False)

    # ------------------------------------------------------------------
    # Writes

    def create_job(
        self,
        job_type: str,
        owner_user_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        status: str = JobStatus.QUEUED,
    ) -> Dict[str, Any]:
        """
        Insert a queued job and return the stored row.

        :param job_type: One of :class:`~squirrel.constants.jobs.JobType`.
        :param owner_user_id: User that owns the job, if any.
        :param payload: JSON-serialisable job input.
        :param status: Initial status; defaults to ``queued``.
        :return: The inserted job row.
        """
        if status not in JobStatus.ALL:
            raise ValueError(f"Unknown job status: {status!r}")

        job_id = str(uuid.uuid4())
        db = self._get_db()
        result = db.query(
            """
            INSERT INTO jobs (id, owner_user_id, type, status, payload)
            VALUES (%(id)s, %(owner_user_id)s, %(type)s, %(status)s, %(payload)s::jsonb)
            RETURNING *
            """,
            params={
                "id": job_id,
                "owner_user_id": owner_user_id,
                "type": job_type,
                "status": status,
                "payload": json.dumps(payload or {}),
            },
            fetch=True,
        )
        row = (result or {}).get("rows", [{}])[0]
        logger.info(
            "Created job {} (type={}, owner={}, status={})",
            job_id, job_type, owner_user_id, status,
        )
        return row

    def set_status(
        self,
        job_id: str,
        status: str,
        *,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update a job's lifecycle state and timestamps.

        ``started_at`` is set the first time a job enters ``running``;
        ``finished_at`` is set when it reaches a terminal state.
        """
        if status not in JobStatus.ALL:
            raise ValueError(f"Unknown job status: {status!r}")

        progress_value = None if progress is None else max(0, min(100, int(progress)))
        db = self._get_db()
        updated = db.query(
            """
            UPDATE jobs
            SET status      = %(status)s,
                progress    = COALESCE(%(progress)s, progress),
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
                "id": job_id,
                "status": status,
                "progress": progress_value,
                "result": json.dumps(result) if result is not None else None,
                "error": error,
            },
            fetch=True,
        )
        rows = (updated or {}).get("rows") or []
        if not rows:
            raise JobNotFoundError(job_id)
        return rows[0]

    def mark_running(self, job_id: str, progress: Optional[int] = None) -> Dict[str, Any]:
        """Mark a job as running."""
        return self.set_status(job_id, JobStatus.RUNNING, progress=progress)

    def mark_succeeded(
        self,
        job_id: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mark a job as succeeded with an optional result payload."""
        return self.set_status(job_id, JobStatus.SUCCEEDED, progress=100, result=result)

    def mark_failed(self, job_id: str, error: str) -> Dict[str, Any]:
        """Mark a job as failed and record the error message."""
        return self.set_status(job_id, JobStatus.FAILED, error=error)

    def append_event(
        self,
        job_id: str,
        message: str,
        *,
        level: str = "info",
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a human-readable event to a job's log."""
        db = self._get_db()
        result = db.query(
            """
            INSERT INTO job_events (job_id, level, message, data)
            VALUES (%(job_id)s, %(level)s, %(message)s, %(data)s::jsonb)
            RETURNING *
            """,
            params={
                "job_id": job_id,
                "level": level,
                "message": message,
                "data": json.dumps(data) if data is not None else None,
            },
            fetch=True,
        )
        return (result or {}).get("rows", [{}])[0]

    # ------------------------------------------------------------------
    # Reads

    def get_job(
        self,
        job_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return one job, scoped to *owner_user_id* when provided."""
        db = self._get_db()
        params: Dict[str, Any] = {"id": job_id}
        owner_clause = ""
        if owner_user_id is not None:
            owner_clause = "AND owner_user_id = %(owner_user_id)s"
            params["owner_user_id"] = owner_user_id

        result = db.query(
            f"SELECT * FROM jobs WHERE id = %(id)s {owner_clause} LIMIT 1",
            params=params,
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise JobNotFoundError(job_id)
        return rows[0]

    def list_jobs(
        self,
        owner_user_id: Optional[str] = None,
        *,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List jobs, newest first, optionally filtered by owner/status/type."""
        if status is not None and status not in JobStatus.ALL:
            raise ValueError(f"Unknown job status: {status!r}")

        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))

        clauses: List[str] = []
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if owner_user_id is not None:
            clauses.append("owner_user_id = %(owner_user_id)s")
            params["owner_user_id"] = owner_user_id
        if status is not None:
            clauses.append("status = %(status)s")
            params["status"] = status
        if job_type is not None:
            clauses.append("type = %(job_type)s")
            params["job_type"] = job_type

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        result = self._get_db().query(
            f"""
            SELECT * FROM jobs
            {where}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params=params,
            fetch=True,
        )
        return (result or {}).get("rows") or []

    def list_events(
        self,
        job_id: str,
        owner_user_id: Optional[str] = None,
        *,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List a job's events in chronological order."""
        # Ownership is enforced by resolving the job first; this also gives a
        # clean JobNotFoundError instead of silently returning no events.
        self.get_job(job_id, owner_user_id=owner_user_id)

        limit = max(1, min(1000, int(limit)))
        result = self._get_db().query(
            """
            SELECT * FROM job_events
            WHERE job_id = %(job_id)s
            ORDER BY id ASC
            LIMIT %(limit)s
            """,
            params={"job_id": job_id, "limit": limit},
            fetch=True,
        )
        return (result or {}).get("rows") or []
