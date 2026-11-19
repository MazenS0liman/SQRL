#!/usr/bin/python
"""
Job Constants
=============

Shared vocabulary for the durable background-job system.

Every asynchronous feature (ETL pipelines, scheduled retraining, Spark
streaming, notification delivery) records its work in the ``jobs`` table using
these values, so the API and UI can treat all long-running work uniformly.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


# ——————————————————————————————————————————————————————————————
# Statuses

class JobStatus:
    """Lifecycle states for a job row."""

    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    CANCELLED = "cancelled"

    ALL = {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED}
    TERMINAL = {SUCCEEDED, FAILED, CANCELLED}


class JobType:
    """Known job categories."""

    GENERIC       = "generic"
    ETL_PIPELINE  = "etl_pipeline"
    MODEL_RETRAIN = "model_retrain"
    STREAM        = "stream"
    NOTIFICATION  = "notification"

    ALL = {GENERIC, ETL_PIPELINE, MODEL_RETRAIN, STREAM, NOTIFICATION}


class NotificationType:
    """Known notification categories."""

    JOB_SUCCEEDED    = "job_succeeded"
    JOB_FAILED       = "job_failed"
    PIPELINE_RUN     = "pipeline_run"
    WORKSPACE_BUILD  = "workspace_build"
    NOTEBOOK_RUN     = "notebook_run"
    RETRAIN          = "retrain"
    STREAM           = "stream"
    SYSTEM           = "system"

    ALL = {
        JOB_SUCCEEDED,
        JOB_FAILED,
        PIPELINE_RUN,
        WORKSPACE_BUILD,
        NOTEBOOK_RUN,
        RETRAIN,
        STREAM,
        SYSTEM,
    }


class NotificationChannel:
    """Delivery channels for a notification."""

    IN_APP = "in_app"
    EMAIL  = "email"

    ALL = {IN_APP, EMAIL}
