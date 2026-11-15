#!/usr/bin/python
"""
ETL Pipeline Tasks
==================

Celery job handler for :attr:`~squirrel.constants.jobs.JobType.ETL_PIPELINE`.

The API creates the job and run rows first, then enqueues the generic
``run_job`` dispatcher. This module only needs to map a job payload back to a
pipeline run and execute it, so the durable lifecycle (status, events,
completion notifications) is shared with every other feature.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from typing import Any, Dict, Optional

from loguru import logger

from squirrel.constants.jobs import JobType
from squirrel.services.pipelines.PipelineService import (
    PipelineService,
    PipelineValidationError,
)
from squirrel.tasks.jobs import register_job_handler


@register_job_handler(JobType.ETL_PIPELINE)
def run_etl_pipeline(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Execute the pipeline run referenced by a job payload."""
    payload = job.get("payload") or {}
    run_id = payload.get("run_id")
    if not run_id:
        raise PipelineValidationError("ETL job payload is missing 'run_id'.")

    logger.info(
        "run_etl_pipeline: job={} run={} pipeline={} dry_run={}",
        job.get("id"),
        run_id,
        payload.get("pipeline_id"),
        payload.get("dry_run"),
    )
    return PipelineService().execute_run(run_id, job_id=job.get("id"))
