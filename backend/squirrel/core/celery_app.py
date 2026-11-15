#!/usr/bin/python
"""
Celery Application
==================

Shared Celery instance for the API, worker, and beat processes.

Start the worker with::

    celery -A squirrel.core.celery_app.celery_app worker --loglevel=INFO

and the scheduler with::

    celery -A squirrel.core.celery_app.celery_app beat --loglevel=INFO

Redis is used as both broker and result backend by default; set
``CELERY_BROKER_URL`` / ``CELERY_RESULT_BACKEND`` to override either.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Third-party
from celery import Celery

# Settings
from squirrel.core.config import settings


celery_app = Celery(
    "squirrel",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
    imports=(
        "squirrel.tasks.jobs",
        "squirrel.tasks.notifications",
        "squirrel.tasks.pipelines",
    ),
)

# Periodic tasks. Feature-specific schedules (ETL, retraining, streaming) will
# register their own entries in later phases; the notification outbox drain is
# the first one because every other feature emits notifications.
celery_app.conf.beat_schedule = {
    "drain-notification-emails": {
        "task": "squirrel.tasks.notifications.send_pending_notification_emails",
        "schedule": 60.0,
    },
}
