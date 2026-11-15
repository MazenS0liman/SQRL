"""Tests for the ETL Celery job handler."""
from __future__ import annotations

import pytest

from squirrel.constants.jobs import JobType
from squirrel.services.pipelines.PipelineService import PipelineValidationError
from squirrel.tasks import jobs as jobs_task
from squirrel.tasks import pipelines as pipelines_task


class FakePipelineService:
    """PipelineService double that records execution requests."""

    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"row_count": 4}
        self.calls: list[tuple] = []

    def execute_run(self, run_id: str, *, job_id: str | None = None):
        self.calls.append((run_id, job_id))
        return self.result


def test_run_etl_pipeline_dispatches_to_execute_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakePipelineService()
    monkeypatch.setattr(pipelines_task, "PipelineService", lambda: service)

    result = pipelines_task.run_etl_pipeline(
        {
            "id": "job-1",
            "payload": {
                "run_id": "run-1",
                "pipeline_id": "pipeline-1",
                "dry_run": False,
            },
        }
    )

    assert result == {"row_count": 4}
    assert service.calls == [("run-1", "job-1")]


def test_run_etl_pipeline_requires_a_run_id() -> None:
    with pytest.raises(PipelineValidationError, match="missing 'run_id'"):
        pipelines_task.run_etl_pipeline({"id": "job-1", "payload": {}})


def test_handler_is_registered_for_etl_jobs() -> None:
    # Re-register explicitly: another test module clears the global registry.
    jobs_task.register_job_handler(JobType.ETL_PIPELINE)(
        pipelines_task.run_etl_pipeline
    )

    assert jobs_task._JOB_HANDLERS[JobType.ETL_PIPELINE] is (
        pipelines_task.run_etl_pipeline
    )
