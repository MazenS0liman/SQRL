"""Tests for pipeline outputs: dashboards, ML modes, and scheduling."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from squirrel.constants.etl import MLMode, PipelineRunStatus, PipelineStatus
from squirrel.schemas.etl import MLSpec, PipelineDefinition, ScheduleSpec
from squirrel.services.pipelines import MLTrainer as ml_trainer_module
from squirrel.services.pipelines.compiler import PipelineValidationError
from squirrel.services.pipelines.MLTrainer import MLTrainer
from squirrel.services.pipelines.PipelineService import PipelineService
from tests.fakes import FakeDB
from tests.test_pipeline_service import (
    FakeJobService,
    _definition,
    _pipeline_row,
    _run_row,
    _service,
)


# ——————————————————————————————————————————————————————————————
# Scheduling


def _schedule(**overrides) -> ScheduleSpec:
    payload = {
        "mode": "daily",
        "enabled": True,
        "time": "02:00",
        "day": 1,
        "month": 1,
        "interval_seconds": 300,
    }
    payload.update(overrides)
    return ScheduleSpec(**payload)


def test_compute_next_run_daily_rolls_to_tomorrow() -> None:
    now = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)

    assert PipelineService.compute_next_run(_schedule(), now=now) == datetime(
        2026, 11, 18, 2, 0, tzinfo=timezone.utc
    )


def test_compute_next_run_daily_keeps_today_when_still_ahead() -> None:
    now = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)

    assert PipelineService.compute_next_run(_schedule(), now=now) == datetime(
        2026, 11, 17, 2, 0, tzinfo=timezone.utc
    )


def test_compute_next_run_monthly_clamps_to_last_day() -> None:
    now = datetime(2026, 1, 31, 3, 0, tzinfo=timezone.utc)

    next_run = PipelineService.compute_next_run(
        _schedule(mode="monthly", day=31), now=now
    )

    assert next_run == datetime(2026, 2, 28, 2, 0, tzinfo=timezone.utc)


def test_compute_next_run_yearly_uses_month_and_day() -> None:
    now = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)

    next_run = PipelineService.compute_next_run(
        _schedule(mode="yearly", month=12, day=25), now=now
    )

    assert next_run == datetime(2026, 12, 25, 2, 0, tzinfo=timezone.utc)


def test_compute_next_run_continuous_uses_interval() -> None:
    now = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)

    next_run = PipelineService.compute_next_run(
        _schedule(mode="continuous", interval_seconds=90), now=now
    )

    assert next_run == datetime(2026, 11, 17, 3, 1, 30, tzinfo=timezone.utc)


def test_compute_next_run_manual_is_none() -> None:
    assert PipelineService.compute_next_run(_schedule(mode="manual", enabled=False)) is None


def test_update_schedule_requires_approval() -> None:
    pipeline = _pipeline_row(status=PipelineStatus.PENDING_APPROVAL)
    db = FakeDB().queue({"rows": [pipeline]})

    with pytest.raises(PipelineValidationError, match="Approve the pipeline"):
        _service(db).update_schedule("pipeline-1", "user-1", _schedule())


def test_update_schedule_persists_next_run() -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    updated = {**pipeline, "schedule_mode": "daily", "schedule_enabled": True}
    db = FakeDB().queue({"rows": [pipeline]}, {"rows": [updated]})

    row = _service(db).update_schedule(
        "pipeline-1", "user-1", _schedule(mode="daily", time="05:30")
    )

    assert row["schedule_mode"] == "daily"
    params = db.last_query.params
    assert params["mode"] == "daily"
    assert params["enabled"] is True
    assert params["time"] == "05:30"
    assert params["next_run"] is not None


def test_dispatch_due_pipelines_creates_run_and_advances_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    pipeline.update(
        {
            "schedule_mode": "daily",
            "schedule_enabled": True,
            "schedule_time": "02:00",
            "schedule_day": 1,
            "schedule_month": 1,
            "schedule_interval_seconds": 300,
        }
    )
    db = FakeDB().queue({"rows": [pipeline]}, {"rows": []})
    service = _service(db)

    created: list[tuple] = []
    monkeypatch.setattr(
        service,
        "create_run",
        lambda pipeline_id, owner, **kwargs: created.append((pipeline_id, owner, kwargs))
        or {"id": "run-2"},
    )

    summary = service.dispatch_due_pipelines()

    assert summary == {"dispatched": 1, "skipped": 0, "failed": 0}
    assert created == [("pipeline-1", "user-1", {"dry_run": False, "enqueue": True})]
    assert db.last_query.params["ran"] is True
    assert db.last_query.params["next_run"] is not None


def test_dispatch_due_pipelines_skips_unapproved_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline_row(status=PipelineStatus.PENDING_APPROVAL)
    pipeline.update({"schedule_mode": "daily", "schedule_enabled": True})
    db = FakeDB().queue({"rows": [pipeline]})
    service = _service(db)

    created: list[tuple] = []
    monkeypatch.setattr(
        service, "create_run", lambda *a, **k: created.append((a, k)) or {"id": "run-2"}
    )

    summary = service.dispatch_due_pipelines()

    assert summary == {"dispatched": 0, "skipped": 1, "failed": 0}
    assert created == []
    assert db.last_query.params["ran"] is False


def test_dispatch_due_pipelines_skips_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    pipeline.update({"schedule_mode": "daily", "schedule_enabled": True})
    db = FakeDB().queue({"rows": [pipeline]}, {"rows": [{"active": 1}]})
    service = _service(db)

    created: list[tuple] = []
    monkeypatch.setattr(
        service, "create_run", lambda *a, **k: created.append((a, k)) or {"id": "run-2"}
    )

    summary = service.dispatch_due_pipelines()

    assert summary == {"dispatched": 0, "skipped": 1, "failed": 0}
    assert created == []


# ——————————————————————————————————————————————————————————————
# Dashboard


class FakeCell:
    def __init__(
        self,
        *,
        cell_id: str = "cell-1",
        charts: list[dict] | None = None,
        layout: dict | None = None,
        reply: str | None = "Dashboard ready",
    ) -> None:
        self.id = cell_id
        self.type = "dashboard"
        self.charts = charts or [{"id": "chart-1", "title": "Revenue"}]
        self.dashboard_layout = layout
        self.reply = reply
        self.created_at = None


class FakeDashboardNotebook:
    def __init__(self, cells: list[FakeCell] | None = None) -> None:
        self.cells = cells or [FakeCell()]
        self.saved_layouts: list[tuple] = []

    def list_cells(self, notebook_id, *, owner_user_id=None):
        return self.cells

    def save_dashboard_layout(self, notebook_id, cell_id, layout, *, owner_user_id=None):
        self.saved_layouts.append((notebook_id, cell_id, layout, owner_user_id))
        return self.cells[0]


def _dashboard_pipeline() -> dict:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    pipeline["dashboard_notebook_id"] = "notebook-1"
    pipeline["dashboard_cell_id"] = "cell-1"
    return pipeline


def _successful_run() -> dict:
    return _run_row(
        status=PipelineRunStatus.SUCCEEDED,
        result={"sink": {"kind": "postgres", "table": "etl_out"}},
    )


def test_get_dashboard_returns_stored_payload() -> None:
    pipeline = _dashboard_pipeline()
    layout = {"order": ["chart-1"], "hidden_ids": [], "hints": {}}
    notebook = FakeDashboardNotebook([FakeCell(layout=layout)])
    db = FakeDB().queue(
        {"rows": [pipeline]},
        {"rows": [pipeline]},
        {"rows": [_successful_run()]},
    )

    dashboard = _service(db, notebook=notebook).get_dashboard("pipeline-1", "user-1")

    assert dashboard.pipeline_id == "pipeline-1"
    assert dashboard.notebook_id == "notebook-1"
    assert dashboard.cell_id == "cell-1"
    assert dashboard.table == "etl_out"
    assert dashboard.charts == [{"id": "chart-1", "title": "Revenue"}]
    assert dashboard.layout == layout
    assert dashboard.reply == "Dashboard ready"
    assert dashboard.run_id == "run-1"


def test_get_dashboard_without_notebook_raises() -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    db = FakeDB().queue({"rows": [pipeline]})

    with pytest.raises(PipelineValidationError, match="No dashboard has been built"):
        _service(db, notebook=FakeDashboardNotebook()).get_dashboard(
            "pipeline-1", "user-1"
        )


def test_save_dashboard_layout_persists_and_returns_payload() -> None:
    pipeline = _dashboard_pipeline()
    notebook = FakeDashboardNotebook()
    db = FakeDB().queue(
        {"rows": [pipeline]},
        {"rows": [pipeline]},
        {"rows": [_successful_run()]},
    )
    layout = {
        "order": ["chart-1"],
        "hidden_ids": ["chart-2"],
        "hints": {"chart-1": "Revenue is up"},
        "overrides": {},
        "custom_charts": [],
        "text_blocks": [],
    }

    dashboard = _service(db, notebook=notebook).save_dashboard_layout(
        "pipeline-1", layout, "user-1"
    )

    assert dashboard.notebook_id == "notebook-1"
    notebook_id, cell_id, saved, owner = notebook.saved_layouts[0]
    assert (notebook_id, cell_id, owner) == ("notebook-1", "cell-1", "user-1")
    assert saved.order == ["chart-1"]
    assert saved.hidden_ids == ["chart-2"]
    assert saved.hints == {"chart-1": "Revenue is up"}


def test_save_dashboard_layout_requires_existing_dashboard() -> None:
    pipeline = _pipeline_row(status=PipelineStatus.APPROVED)
    db = FakeDB().queue({"rows": [pipeline]})

    with pytest.raises(PipelineValidationError, match="no dashboard yet"):
        _service(db, notebook=FakeDashboardNotebook()).save_dashboard_layout(
            "pipeline-1", {"order": []}, "user-1"
        )


# ——————————————————————————————————————————————————————————————
# Model registry


def test_register_model_returns_returned_id() -> None:
    db = FakeDB().queue({"rows": [{"id": "db-model-id"}]})
    service = _service(db)
    spec = MLSpec(mode="train", task="classification", target="label")
    pipeline = _pipeline_row()

    model_id = service._register_model(
        pipeline,
        spec,
        {
            "task": "classification",
            "algorithm": "linear",
            "target": "label",
            "features": ["a", "b"],
            "metrics": {"accuracy": 0.9},
            "model_key": "etl/models/pipeline-1/run-1.joblib",
            "file_url": "https://minio/etl/models/pipeline-1/run-1.joblib",
        },
        "run-1",
        "user-1",
    )

    assert model_id == "db-model-id"
    params = db.last_query.params
    assert params["pipeline_id"] == "pipeline-1"
    assert params["target"] == "label"
    assert json.loads(params["features"]) == ["a", "b"]
    assert json.loads(params["metrics"]) == {"accuracy": 0.9}


def test_list_models_filters_by_owner_and_pipeline() -> None:
    row = {"id": "model-1", "pipeline_id": "pipeline-1"}
    db = FakeDB().queue({"rows": [row]})

    rows = _service(db).list_models("user-1", pipeline_id="pipeline-1")

    assert rows == [row]
    assert "owner_user_id = %(owner_user_id)s" in db.last_query.sql
    assert "pipeline_id = %(pipeline_id)s" in db.last_query.sql


def test_resolve_ml_model_requires_a_model_for_retrain() -> None:
    spec = MLSpec.model_construct(mode=MLMode.RETRAIN, task="classification", model_id=None)

    with pytest.raises(ValueError, match="requires a registered model"):
        _service(FakeDB())._resolve_ml_model(spec, "user-1")


# ——————————————————————————————————————————————————————————————
# ML trainer modes


class FakeMinio:
    def __init__(self, blob: bytes | None = None) -> None:
        self.bucket = "squirrel-bucket"
        self.blob = blob
        self.uploads: dict[str, bytes] = {}
        self.retrieved: list[str] = []

    def upload_file(self, path: str, object_key: str) -> SimpleNamespace:
        with open(path, "rb") as handle:
            self.uploads[object_key] = handle.read()
        return SimpleNamespace(fileUrl=f"https://minio/{self.bucket}/{object_key}")

    def retrieve_file(self, path: str) -> SimpleNamespace:
        self.retrieved.append(path)
        return SimpleNamespace(fileByte=self.blob)


def _fitted_model() -> LogisticRegression:
    frame = pd.DataFrame({"a": [0, 1, 0, 1], "b": [0, 1, 1, 0]})
    model = LogisticRegression()
    model.fit(frame, [0, 1, 0, 1])
    return model


def _model_row() -> dict:
    return {
        "id": "model-1",
        "model_key": "etl/models/pipeline-1/run-1.joblib",
        "task": "classification",
        "algorithm": "linear",
        "target": "label",
        "features": ["a", "b"],
    }


def test_ml_trainer_train_uploads_and_reports_features(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        def run(self, frame, spec):
            return {
                "model": _fitted_model(),
                "features": ["a", "b"],
                "metrics": {"accuracy": 1.0},
            }

    monkeypatch.setattr(ml_trainer_module, "ETLModelTrainingAgent", FakeAgent)
    minio = FakeMinio()
    spec = MLSpec(mode="train", task="classification", target="label")
    frame = pd.DataFrame({"a": [0, 1], "b": [0, 1], "label": [0, 1]})

    outcome = MLTrainer(minio).train(
        frame, spec, run_id="run-2", pipeline_id="pipeline-1"
    )

    assert outcome["mode"] == MLMode.TRAIN
    assert outcome["features"] == ["a", "b"]
    assert outcome["metrics"] == {"accuracy": 1.0}
    assert "etl/models/pipeline-1/run-2.joblib" in minio.uploads


def test_ml_trainer_refit_loads_registered_model_and_refits() -> None:
    buffer = io.BytesIO()
    joblib.dump(_fitted_model(), buffer)
    minio = FakeMinio(buffer.getvalue())
    spec = MLSpec(
        mode="retrain", task="classification", target="label", model_id="model-1"
    )
    frame = pd.DataFrame(
        {"a": [1, 0, 1, 0], "b": [1, 0, 0, 1], "label": [1, 0, 1, 0]}
    )

    outcome = MLTrainer(minio).refit(
        frame,
        spec,
        model_row=_model_row(),
        run_id="run-2",
        pipeline_id="pipeline-1",
    )

    assert outcome["mode"] == MLMode.RETRAIN
    assert outcome["features"] == ["a", "b"]
    assert outcome["source_model_id"] == "model-1"
    assert outcome["metrics"]["rows"] == 4
    assert minio.retrieved == ["squirrel-bucket/etl/models/pipeline-1/run-1.joblib"]
    assert "etl/models/pipeline-1/run-2.joblib" in minio.uploads


def test_ml_trainer_inference_scores_rows_without_training() -> None:
    buffer = io.BytesIO()
    joblib.dump(_fitted_model(), buffer)
    minio = FakeMinio(buffer.getvalue())
    spec = MLSpec(mode="inference", task="classification", model_id="model-1")
    frame = pd.DataFrame({"a": [1, 0, 1], "b": [1, 0, 0]})

    outcome = MLTrainer(minio).infer(
        frame,
        spec,
        model_row=_model_row(),
        run_id="run-2",
        pipeline_id="pipeline-1",
    )

    assert outcome["mode"] == MLMode.INFERENCE
    assert outcome["row_count"] == 3
    assert len(outcome["predictions"]) == 3
    assert all("prediction" in row for row in outcome["predictions"])
    assert outcome["prediction_key"] == "etl/predictions/pipeline-1/run-2.csv"
    assert "etl/predictions/pipeline-1/run-2.csv" in minio.uploads


def test_ml_trainer_requires_model_row_for_retrain_and_inference() -> None:
    frame = pd.DataFrame({"a": [1], "b": [1], "label": [1]})
    trainer = MLTrainer(FakeMinio())

    with pytest.raises(ValueError, match="Retraining requires"):
        trainer.execute(
            frame,
            MLSpec(mode="retrain", task="classification", target="label", model_id="m"),
            run_id="run-2",
            pipeline_id="pipeline-1",
        )

    with pytest.raises(ValueError, match="Inference requires"):
        trainer.execute(
            frame,
            MLSpec(mode="inference", task="classification", model_id="m"),
            run_id="run-2",
            pipeline_id="pipeline-1",
        )


def test_ml_trainer_rejects_missing_feature_columns() -> None:
    buffer = io.BytesIO()
    joblib.dump(_fitted_model(), buffer)
    trainer = MLTrainer(FakeMinio(buffer.getvalue()))
    spec = MLSpec(mode="inference", task="classification", model_id="model-1")

    with pytest.raises(ValueError, match="missing from the sink table"):
        trainer.infer(
            pd.DataFrame({"a": [1, 0]}),
            spec,
            model_row=_model_row(),
            run_id="run-2",
            pipeline_id="pipeline-1",
        )


# ——————————————————————————————————————————————————————————————
# Outputs validation


def test_ml_output_requires_model_id_for_retrain() -> None:
    with pytest.raises(ValueError, match="requires a registered model_id"):
        MLSpec(mode="retrain", task="classification", target="label")


def test_ml_inference_output_allows_omitted_target_and_features() -> None:
    spec = MLSpec(mode="inference", task="classification", model_id="model-1")

    assert spec.target is None
    assert spec.features == []


def test_definition_accepts_inference_output() -> None:
    definition = PipelineDefinition.model_validate(
        {
            **_definition().model_dump(),
            "outputs": {
                "dashboard": True,
                "ml": {"mode": "inference", "task": "classification", "model_id": "model-1"},
            },
        }
    )

    assert definition.outputs is not None
    assert definition.outputs.ml is not None
    assert definition.outputs.ml.mode == MLMode.INFERENCE
