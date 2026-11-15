"""Tests for Celery queue configuration.

The worker is started with ``-Q default,notifications``. If the app publishes
to Celery's implicit ``celery`` queue instead, jobs sit in Redis forever while
the worker idles — the exact bug this module guards against.
"""
from __future__ import annotations

from squirrel.core.celery_app import (
    DEFAULT_QUEUE,
    NOTIFICATIONS_QUEUE,
    celery_app,
)


def test_default_queue_matches_worker_queue() -> None:
    assert celery_app.conf.task_default_queue == DEFAULT_QUEUE == "default"


def test_declared_queues_have_distinct_exchanges() -> None:
    queues = {queue.name: queue for queue in celery_app.conf.task_queues}

    assert set(queues) == {DEFAULT_QUEUE, NOTIFICATIONS_QUEUE}
    for name, queue in queues.items():
        assert queue.exchange.name == name
        assert queue.routing_key == name


def test_jobs_route_to_default_queue() -> None:
    route = celery_app.amqp.router.route({}, "squirrel.tasks.jobs.run_job")

    assert route["queue"].name == DEFAULT_QUEUE


def test_notifications_route_to_notifications_queue() -> None:
    route = celery_app.amqp.router.route(
        {},
        "squirrel.tasks.notifications.send_pending_notification_emails",
    )

    assert route["queue"].name == NOTIFICATIONS_QUEUE
