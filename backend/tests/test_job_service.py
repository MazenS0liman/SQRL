"""Tests for :class:`squirrel.services.jobs.JobService`."""
from __future__ import annotations

import json

import pytest

from squirrel.constants.jobs import JobStatus, JobType
from squirrel.services.jobs.JobService import JobNotFoundError, JobService
from tests.fakes import FakeDB


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_create_job_persists_payload_and_returns_row(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    row = service.create_job(
        JobType.ETL_PIPELINE,
        owner_user_id="user-1",
        payload={"pipeline_id": "p-1"},
    )

    assert row == {"id": "job-1"}
    query = fake_db.last_query
    assert "INSERT INTO jobs" in query.sql
    assert query.params["owner_user_id"] == "user-1"
    assert query.params["type"] == JobType.ETL_PIPELINE
    assert query.params["status"] == JobStatus.QUEUED
    assert json.loads(query.params["payload"]) == {"pipeline_id": "p-1"}


def test_create_job_rejects_unknown_status(fake_db: FakeDB) -> None:
    service = JobService(db=fake_db)

    with pytest.raises(ValueError):
        service.create_job(JobType.GENERIC, status="not-a-status")

    assert fake_db.queries == []
    assert fake_db.executes == []


def test_job_tables_are_bootstrapped_once(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]}, {"rows": [{"id": "job-2"}]})
    service = JobService(db=fake_db)

    service.create_job(JobType.GENERIC)
    service.create_job(JobType.GENERIC)

    assert len(fake_db.executes) == 2
    assert all("CREATE TABLE IF NOT EXISTS" in call.sql for call in fake_db.executes)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("requested", "expected"),
    [(150, 100), (-5, 0), (42, 42)],
)
def test_set_status_clamps_progress(
    fake_db: FakeDB,
    requested: int,
    expected: int,
) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    service.set_status("job-1", JobStatus.RUNNING, progress=requested)

    query = fake_db.last_query
    assert query.params["progress"] == expected
    assert query.params["status"] == JobStatus.RUNNING
    assert "started_at" in query.sql
    assert "finished_at" in query.sql


def test_mark_running_uses_running_status(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    service.mark_running("job-1")

    assert fake_db.last_query.params["status"] == JobStatus.RUNNING


def test_mark_succeeded_sets_progress_and_result(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    service.mark_succeeded("job-1", result={"rows_written": 10})

    query = fake_db.last_query
    assert query.params["status"] == JobStatus.SUCCEEDED
    assert query.params["progress"] == 100
    assert json.loads(query.params["result"]) == {"rows_written": 10}
    assert "finished_at" in query.sql


def test_mark_failed_records_error(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    service.mark_failed("job-1", "connector timed out")

    query = fake_db.last_query
    assert query.params["status"] == JobStatus.FAILED
    assert query.params["error"] == "connector timed out"


def test_set_status_missing_job_raises(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})
    service = JobService(db=fake_db)

    with pytest.raises(JobNotFoundError):
        service.mark_running("missing")


def test_append_event_persists_level_and_data(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": 1}]})
    service = JobService(db=fake_db)

    service.append_event(
        "job-1",
        "Cleaned 120 rows",
        level="warning",
        data={"step": "clean", "dropped": 3},
    )

    query = fake_db.last_query
    assert "INSERT INTO job_events" in query.sql
    assert query.params["level"] == "warning"
    assert json.loads(query.params["data"]) == {"step": "clean", "dropped": 3}


# ---------------------------------------------------------------------------
# Reads and owner scoping
# ---------------------------------------------------------------------------

def test_get_job_scopes_to_owner(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1", "owner_user_id": "user-1"}]})
    service = JobService(db=fake_db)

    row = service.get_job("job-1", owner_user_id="user-1")

    assert row["owner_user_id"] == "user-1"
    query = fake_db.last_query
    assert "owner_user_id = %(owner_user_id)s" in query.sql
    assert query.params["owner_user_id"] == "user-1"


def test_get_job_without_owner_has_no_owner_filter(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    service.get_job("job-1")

    query = fake_db.last_query
    assert "owner_user_id" not in query.sql
    assert "owner_user_id" not in query.params


def test_get_job_missing_raises(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})
    service = JobService(db=fake_db)

    with pytest.raises(JobNotFoundError):
        service.get_job("missing", owner_user_id="user-1")


def test_list_jobs_applies_filters_and_clamps_pagination(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "job-1"}]})
    service = JobService(db=fake_db)

    rows = service.list_jobs(
        "user-1",
        status=JobStatus.RUNNING,
        job_type=JobType.MODEL_RETRAIN,
        limit=9999,
        offset=-10,
    )

    assert rows == [{"id": "job-1"}]
    query = fake_db.last_query
    assert query.params["limit"] == 200
    assert query.params["offset"] == 0
    assert "owner_user_id = %(owner_user_id)s" in query.sql
    assert "status = %(status)s" in query.sql
    assert "type = %(job_type)s" in query.sql
    assert query.params["status"] == JobStatus.RUNNING
    assert query.params["job_type"] == JobType.MODEL_RETRAIN


def test_list_jobs_without_filters_has_no_where_clause(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})
    service = JobService(db=fake_db)

    service.list_jobs()

    assert "WHERE" not in fake_db.last_query.sql


def test_list_jobs_rejects_unknown_status(fake_db: FakeDB) -> None:
    service = JobService(db=fake_db)

    with pytest.raises(ValueError):
        service.list_jobs(status="not-a-status")

    assert fake_db.queries == []


def test_list_events_checks_ownership_first(fake_db: FakeDB) -> None:
    fake_db.queue(
        {"rows": [{"id": "job-1", "owner_user_id": "user-1"}]},
        {"rows": [{"id": 1, "job_id": "job-1", "message": "Job started."}]},
    )
    service = JobService(db=fake_db)

    events = service.list_events("job-1", owner_user_id="user-1")

    assert events == [{"id": 1, "job_id": "job-1", "message": "Job started."}]
    assert "owner_user_id = %(owner_user_id)s" in fake_db.queries[0].sql
    assert "FROM job_events" in fake_db.queries[1].sql
    assert fake_db.queries[1].params["job_id"] == "job-1"


def test_list_events_missing_job_raises(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})
    service = JobService(db=fake_db)

    with pytest.raises(JobNotFoundError):
        service.list_events("missing")

    # The event query must never run when the ownership check fails.
    assert len(fake_db.queries) == 1
