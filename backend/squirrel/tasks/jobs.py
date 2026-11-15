#!/usr/bin/python
"""
Job Tasks
=========

Generic Celery entry point for durable jobs plus a small handler registry.

Feature modules (ETL, retraining, streaming) register handlers at import time::

    from squirrel.tasks.jobs import register_job_handler
    from squirrel.constants.jobs import JobType

    @register_job_handler(JobType.ETL_PIPELINE)
    def run_etl(job: dict) -> dict:
        ...

Keeping the dispatcher generic means every feature gets the same lifecycle,
event log, error handling, and completion notification without re-implementing
them.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import Any, Callable, Dict, Optional

# Logging
from loguru import logger

# Celery
from squirrel.core.celery_app import celery_app

# Constants
from squirrel.constants.jobs import NotificationType

# Services
from squirrel.services.jobs.JobService import JobService, JobNotFoundError
from squirrel.services.notifications.NotificationService import NotificationService


JobHandler = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]

_JOB_HANDLERS: Dict[str, JobHandler] = {}


def register_job_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator registering a handler for one job type."""
    def decorator(handler: JobHandler) -> JobHandler:
        _JOB_HANDLERS[job_type] = handler
        return handler
    return decorator


def get_job_handler(job_type: str) -> Optional[JobHandler]:
    """Return the registered handler for *job_type*, if any."""
    return _JOB_HANDLERS.get(job_type)


def _notify_outcome(
    job: Dict[str, Any],
    *,
    succeeded: bool,
    error: Optional[str] = None,
) -> None:
    """Emit an in-app notification for a finished job, if it has an owner."""
    owner_user_id = job.get("owner_user_id")
    if not owner_user_id:
        return
    try:
        if succeeded:
            NotificationService().emit(
                owner_user_id,
                f"{job.get('type', 'Job')} completed",
                notification_type=NotificationType.JOB_SUCCEEDED,
                body=f"Job {job['id']} finished successfully.",
                link=f"/jobs/{job['id']}",
            )
        else:
            NotificationService().emit(
                owner_user_id,
                f"{job.get('type', 'Job')} failed",
                notification_type=NotificationType.JOB_FAILED,
                body=f"Job {job['id']} failed: {error or 'unknown error'}",
                link=f"/jobs/{job['id']}",
            )
    except Exception:  # noqa: BLE001 - notification failure must not mask the job result
        logger.exception("Failed to emit completion notification for job {}", job.get("id"))


@celery_app.task(
    name="squirrel.tasks.jobs.run_job",
    bind=True,
    max_retries=0,
)
def run_job(self, job_id: str) -> Dict[str, Any]:
    """
    Execute one job row through its registered handler.

    The job row must already exist (created by the API before enqueueing).
    Lifecycle transitions and events are persisted so the API can show
    progress even after a worker restart.
    """
    service = JobService()
    try:
        job = service.get_job(job_id)
    except JobNotFoundError:
        logger.error("run_job: job {} does not exist", job_id)
        return {"job_id": job_id, "status": "missing"}

    handler = get_job_handler(job.get("type", ""))
    if handler is None:
        message = f"No handler registered for job type '{job.get('type')}'."
        service.mark_failed(job_id, message)
        service.append_event(job_id, message, level="error")
        _notify_outcome(job, succeeded=False, error=message)
        return {"job_id": job_id, "status": "failed", "error": message}

    service.mark_running(job_id)
    service.append_event(job_id, "Job started.")
    logger.info("run_job: starting job {} (type={})", job_id, job.get("type"))

    try:
        result = handler(job) or {}
    except Exception as exc:  # noqa: BLE001 - persist the failure for the API
        logger.exception("run_job: job {} failed", job_id)
        service.mark_failed(job_id, str(exc))
        service.append_event(job_id, f"Job failed: {exc}", level="error")
        _notify_outcome(job, succeeded=False, error=str(exc))
        return {"job_id": job_id, "status": "failed", "error": str(exc)}

    service.mark_succeeded(job_id, result=result)
    service.append_event(job_id, "Job succeeded.")
    _notify_outcome(job, succeeded=True)
    logger.info("run_job: job {} succeeded", job_id)
    return {"job_id": job_id, "status": "succeeded", "result": result}
