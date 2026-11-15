"""Tests for the generic Celery job dispatcher."""
from __future__ import annotations

import pytest

from squirrel.constants.jobs import JobType, NotificationType
from squirrel.services.jobs.JobService import JobNotFoundError
from squirrel.tasks import jobs as jobs_task


class FakeJobService:
    """JobService double that records lifecycle calls."""

    def __init__(self, job: dict | None = None, missing: bool = False) -> None:
        self.job = job or {}
        self.missing = missing
        self.calls: list[tuple] = []

    def get_job(self, job_id: str, owner_user_id: str | None = None) -> dict:
        self.calls.append(("get_job", job_id, owner_user_id))
        if self.missing:
            raise JobNotFoundError(job_id)
        return dict(self.job)

    def mark_running(self, job_id: str, progress: int | None = None) -> None:
        self.calls.append(("mark_running", job_id, progress))

    def mark_succeeded(self, job_id: str, result: dict | None = None) -> None:
        self.calls.append(("mark_succeeded", job_id, result))

    def mark_failed(self, job_id: str, error: str) -> None:
        self.calls.append(("mark_failed", job_id, error))

    def append_event(
        self,
        job_id: str,
        message: str,
        *,
        level: str = "info",
        data: dict | None = None,
    ) -> None:
        self.calls.append(("append_event", job_id, message, level))


class FakeNotificationService:
    """NotificationService double that records emitted notifications."""

    def __init__(self, error: Exception | None = None) -> None:
        self.emitted: list[dict] = []
        self.error = error

    def emit(
        self,
        user_id: str,
        title: str,
        *,
        notification_type: str,
        body: str = "",
        link: str | None = None,
    ) -> dict:
        if self.error is not None:
            raise self.error
        self.emitted.append(
            {
                "user_id": user_id,
                "title": title,
                "notification_type": notification_type,
                "body": body,
                "link": link,
            }
        )
        return {"id": "notification-1"}


@pytest.fixture(autouse=True)
def clear_handlers() -> None:
    """Keep the global handler registry isolated between tests."""
    jobs_task._JOB_HANDLERS.clear()
    yield
    jobs_task._JOB_HANDLERS.clear()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    job: dict | None = None,
    missing: bool = False,
    notification_error: Exception | None = None,
) -> tuple[FakeJobService, FakeNotificationService]:
    service = FakeJobService(job=job, missing=missing)
    notifications = FakeNotificationService(error=notification_error)
    monkeypatch.setattr(jobs_task, "JobService", lambda: service)
    monkeypatch.setattr(jobs_task, "NotificationService", lambda: notifications)
    return service, notifications


def test_run_job_reports_missing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    service, notifications = _install(monkeypatch, missing=True)

    result = jobs_task.run_job("missing")

    assert result == {"job_id": "missing", "status": "missing"}
    assert notifications.emitted == []
    assert [call[0] for call in service.calls] == ["get_job"]


def test_run_job_fails_when_no_handler_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, notifications = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.STREAM, "owner_user_id": "user-1"},
    )

    result = jobs_task.run_job("job-1")

    assert result["status"] == "failed"
    assert "No handler registered" in result["error"]
    assert ("mark_failed", "job-1", result["error"]) in service.calls
    assert any(
        call[0] == "append_event" and call[3] == "error" for call in service.calls
    )
    assert notifications.emitted[0]["notification_type"] == NotificationType.JOB_FAILED
    assert notifications.emitted[0]["link"] == "/jobs/job-1"


def test_run_job_executes_handler_and_marks_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, notifications = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.GENERIC, "owner_user_id": "user-1"},
    )
    jobs_task.register_job_handler(JobType.GENERIC)(lambda job: {"rows": 5})

    result = jobs_task.run_job("job-1")

    assert result == {
        "job_id": "job-1",
        "status": "succeeded",
        "result": {"rows": 5},
    }
    assert ("mark_running", "job-1", None) in service.calls
    assert ("mark_succeeded", "job-1", {"rows": 5}) in service.calls
    assert ("append_event", "job-1", "Job started.", "info") in service.calls
    assert notifications.emitted[0]["notification_type"] == (
        NotificationType.JOB_SUCCEEDED
    )
    assert notifications.emitted[0]["user_id"] == "user-1"


def test_run_job_treats_none_result_as_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.GENERIC, "owner_user_id": "user-1"},
    )
    jobs_task.register_job_handler(JobType.GENERIC)(lambda job: None)

    result = jobs_task.run_job("job-1")

    assert result["result"] == {}
    assert ("mark_succeeded", "job-1", {}) in service.calls


def test_run_job_records_handler_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    service, notifications = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.GENERIC, "owner_user_id": "user-1"},
    )

    def boom(job: dict) -> dict:
        raise RuntimeError("bad input data")

    jobs_task.register_job_handler(JobType.GENERIC)(boom)

    result = jobs_task.run_job("job-1")

    assert result == {
        "job_id": "job-1",
        "status": "failed",
        "error": "bad input data",
    }
    assert ("mark_failed", "job-1", "bad input data") in service.calls
    assert any(
        call[0] == "append_event" and call[2] == "Job failed: bad input data"
        for call in service.calls
    )
    assert notifications.emitted[0]["notification_type"] == NotificationType.JOB_FAILED


def test_run_job_skips_notification_without_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, notifications = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.GENERIC, "owner_user_id": None},
    )
    jobs_task.register_job_handler(JobType.GENERIC)(lambda job: {})

    jobs_task.run_job("job-1")

    assert notifications.emitted == []


def test_run_job_succeeds_even_when_notification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _install(
        monkeypatch,
        job={"id": "job-1", "type": JobType.GENERIC, "owner_user_id": "user-1"},
        notification_error=RuntimeError("notification store down"),
    )
    jobs_task.register_job_handler(JobType.GENERIC)(lambda job: {"ok": True})

    result = jobs_task.run_job("job-1")

    assert result["status"] == "succeeded"
    assert ("mark_succeeded", "job-1", {"ok": True}) in service.calls
