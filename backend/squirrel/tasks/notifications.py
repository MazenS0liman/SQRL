#!/usr/bin/python
"""
Notification Tasks
==================

Celery task that drains the notification email outbox.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Logging
from loguru import logger

# Celery
from squirrel.core.celery_app import celery_app

# Services
from squirrel.services.notifications.NotificationService import NotificationService


@celery_app.task(
    name="squirrel.tasks.notifications.send_pending_notification_emails",
    bind=True,
    max_retries=3,
)
def send_pending_notification_emails(
    self,
    limit: int = 50,
) -> dict:
    """Deliver pending notification emails, retrying transient SMTP failures."""
    try:
        result = NotificationService().send_pending_emails(limit=limit)
        logger.info("Notification email drain finished: {}", result)
        return result
    except Exception as exc:  # noqa: BLE001 - Celery retry boundary
        logger.exception("Notification email drain failed; retrying")
        raise self.retry(exc=exc, countdown=60)
