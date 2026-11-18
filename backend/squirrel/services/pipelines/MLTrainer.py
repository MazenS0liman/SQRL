"""Small, bounded model trainer for pipeline outputs."""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import pandas as pd

from squirrel.constants.etl import MLMode
from squirrel.core.config import settings
from squirrel.modules.agents.etl.ETLModelTrainingAgent import (
    ETLModelTrainingAgent,
)
from squirrel.schemas.etl import MLSpec


class MLTrainer:
    """Train, refit, or score models from pipeline sink data."""

    def __init__(self, minio: Any) -> None:
        self._minio = minio

    # ------------------------------------------------------------------
    # Public entry point

    def execute(
        self,
        frame: pd.DataFrame,
        spec: MLSpec,
        *,
        run_id: str,
        pipeline_id: str,
        model_row: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the mode selected by ``spec.mode``."""
        if spec.mode == MLMode.INFERENCE:
            if model_row is None:
                raise ValueError("Inference requires a registered model.")
            return self.infer(
                frame, spec, model_row=model_row, run_id=run_id, pipeline_id=pipeline_id
            )
        if spec.mode == MLMode.RETRAIN:
            if model_row is None:
                raise ValueError("Retraining requires a registered model.")
            return self.refit(
                frame, spec, model_row=model_row, run_id=run_id, pipeline_id=pipeline_id
            )
        return self.train(frame, spec, run_id=run_id, pipeline_id=pipeline_id)

    # ------------------------------------------------------------------
    # Modes

    def train(
        self,
        frame: pd.DataFrame,
        spec: MLSpec,
        *,
        run_id: str,
        pipeline_id: str,
    ) -> Dict[str, Any]:
        """Fit a brand-new model on the sink table."""
        self._guard_rows(frame)
        if frame.empty:
            raise ValueError("The sink table is empty; there is no data to train on.")

        trained = ETLModelTrainingAgent().run(frame, spec)
        model = trained["model"]
        features = trained["features"]
        metrics = trained["metrics"]

        object_key = f"etl/models/{pipeline_id}/{run_id}.joblib"
        uploaded = self._save_model(model, object_key)

        return {
            "mode": MLMode.TRAIN,
            "task": spec.task,
            "algorithm": spec.algorithm,
            "target": spec.target,
            "features": features,
            "metrics": metrics,
            "details": trained.get("details"),
            "model_key": object_key,
            "file_url": uploaded.fileUrl,
        }

    def refit(
        self,
        frame: pd.DataFrame,
        spec: MLSpec,
        *,
        model_row: Dict[str, Any],
        run_id: str,
        pipeline_id: str,
    ) -> Dict[str, Any]:
        """Load a registered model and refit it on the new sink rows."""
        self._guard_rows(frame)
        if frame.empty:
            raise ValueError("The sink table is empty; there is no data to retrain on.")

        model = self._load_model(str(model_row["model_key"]))
        features = self._resolve_features(spec, model_row, model, frame)
        target = spec.target or model_row.get("target")
        task = model_row.get("task") or spec.task

        if task == "clustering":
            values = self._numeric_frame(frame, features)
            model.fit(values)
            metrics = {
                "mode": MLMode.RETRAIN,
                "agent": "ETLModelTrainingAgent",
                "inertia": float(getattr(model, "inertia_", 0.0)),
                "n_clusters": int(getattr(model, "n_clusters", spec.n_clusters)),
            }
        else:
            if not target:
                raise ValueError("Retraining a supervised model requires a target column.")
            values, labels = self._supervised_frame(frame, features, str(target))
            model.fit(values, labels)
            metrics = {
                "mode": MLMode.RETRAIN,
                "agent": "ETLModelTrainingAgent",
                "rows": int(len(values)),
                "target": target,
            }

        object_key = f"etl/models/{pipeline_id}/{run_id}.joblib"
        uploaded = self._save_model(model, object_key)

        details = self._refit_details(model_row, task, target, features, frame)
        return {
            "mode": MLMode.RETRAIN,
            "task": task,
            "algorithm": model_row.get("algorithm") or spec.algorithm,
            "target": target,
            "features": features,
            "metrics": metrics,
            "details": details,
            "model_key": object_key,
            "file_url": uploaded.fileUrl,
            "source_model_id": model_row.get("id"),
        }

    def infer(
        self,
        frame: pd.DataFrame,
        spec: MLSpec,
        *,
        model_row: Dict[str, Any],
        run_id: str,
        pipeline_id: str,
    ) -> Dict[str, Any]:
        """Score the new sink rows with a registered model."""
        self._guard_rows(frame)
        if frame.empty:
            raise ValueError("The sink table is empty; there is no data to score.")

        model = self._load_model(str(model_row["model_key"]))
        features = self._resolve_features(spec, model_row, model, frame)
        values = self._numeric_frame(frame, features)
        predictions = model.predict(values)

        preview = frame.copy()
        preview["prediction"] = predictions
        preview_rows = preview.head(20).to_dict(orient="records")

        object_key = f"etl/predictions/{pipeline_id}/{run_id}.csv"
        uploaded = self._save_predictions(preview, object_key)

        return {
            "mode": MLMode.INFERENCE,
            "task": model_row.get("task") or spec.task,
            "algorithm": model_row.get("algorithm") or spec.algorithm,
            "target": model_row.get("target"),
            "features": features,
            "metrics": {
                "mode": MLMode.INFERENCE,
                "rows": int(len(values)),
            },
            "details": model_row.get("details"),
            "model_key": model_row["model_key"],
            "file_url": uploaded.fileUrl,
            "prediction_key": object_key,
            "predictions": preview_rows,
            "row_count": int(len(values)),
            "source_model_id": model_row.get("id"),
        }

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _refit_details(
        model_row: Dict[str, Any],
        task: str,
        target: Any,
        features: List[str],
        frame: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Carry the model card forward, refreshing sample counts and curves."""
        base = model_row.get("details") or {}
        if isinstance(base, str):
            try:
                import json

                base = json.loads(base)
            except Exception:
                base = {}
        details = dict(base) if isinstance(base, dict) else {}
        details.setdefault("input_schema", [])
        details.setdefault("preprocessing", [])
        details["task_type"] = task
        details["algorithm"] = model_row.get("algorithm")
        details["target"] = target
        details["n_features"] = len(features)
        details["n_samples"] = int(len(frame))
        details["refit"] = True
        if task == "clustering":
            from squirrel.modules.agents.etl.ETLModelTrainingAgent import (
                ETLModelTrainingAgent,
            )

            details["training_history"] = ETLModelTrainingAgent._clustering_history(
                ETLModelTrainingAgent._numeric_frame(frame, features),
                int(model_row.get("metrics", {}).get("n_clusters") or 3)
                if isinstance(model_row.get("metrics"), dict)
                else 3,
            )
        else:
            # A retrain has no held-out curve of its own; do not present the
            # previous version's curve as if it belonged to this model.
            details["training_history"] = None
        return details

    @staticmethod
    def _guard_rows(frame: pd.DataFrame) -> None:
        if len(frame) > int(getattr(settings, "ETL_ML_MAX_ROWS", 200_000)):
            raise ValueError("The sink table exceeds the configured ML row limit.")

    @staticmethod
    def _resolve_features(
        spec: MLSpec,
        model_row: Dict[str, Any],
        model: Any,
        frame: pd.DataFrame,
    ) -> List[str]:
        candidates: List[str] = []
        candidates.extend(list(spec.features or []))
        candidates.extend(list(model_row.get("features") or []))
        learned = getattr(model, "feature_names_in_", None)
        if learned is not None:
            candidates.extend([str(name) for name in learned])
        if not candidates:
            target = spec.target or model_row.get("target")
            candidates = [column for column in frame.columns if column != target]
        seen: List[str] = []
        for column in candidates:
            if column and column not in seen:
                seen.append(column)
        if not seen:
            raise ValueError("No model features could be resolved for this run.")
        return seen

    @staticmethod
    def _numeric_frame(frame: pd.DataFrame, features: List[str]) -> pd.DataFrame:
        missing = [column for column in features if column not in frame.columns]
        if missing:
            raise ValueError(f"ML features are missing from the sink table: {missing}")
        values = frame[features].apply(pd.to_numeric, errors="coerce")
        if values.isna().any().any():
            raise ValueError("ML features must contain numeric, non-null values.")
        return values

    @classmethod
    def _supervised_frame(
        cls, frame: pd.DataFrame, features: List[str], target: str
    ) -> tuple[pd.DataFrame, pd.Series]:
        if target not in frame.columns:
            raise ValueError(f"ML target '{target}' is missing from the sink table.")
        values = cls._numeric_frame(frame, features)
        labels = frame[target]
        if labels.isna().any():
            raise ValueError("The ML target column contains null values.")
        return values, labels

    def _load_model(self, model_key: str) -> Any:
        path = model_key
        bucket = getattr(self._minio, "bucket", None)
        if bucket and not path.startswith("s3://") and not path.startswith(f"{bucket}/"):
            path = f"{bucket}/{path}"
        stored = self._minio.retrieve_file(path)
        if stored is None or getattr(stored, "fileByte", None) is None:
            raise ValueError(f"Could not read the registered model '{model_key}'.")
        return joblib.load(io.BytesIO(stored.fileByte))

    def _save_model(self, model: Any, object_key: str) -> Any:
        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as temporary:
            model_path = Path(temporary.name)
        try:
            joblib.dump(model, model_path)
            uploaded = self._minio.upload_file(str(model_path), object_key)
        finally:
            model_path.unlink(missing_ok=True)
        if uploaded is None:
            raise RuntimeError(f"Failed to upload trained model to '{object_key}'.")
        return uploaded

    def _save_predictions(self, frame: pd.DataFrame, object_key: str) -> Any:
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", encoding="utf-8", newline=""
        ) as temporary:
            frame.to_csv(temporary.name, index=False)
            csv_path = Path(temporary.name)
        try:
            uploaded = self._minio.upload_file(str(csv_path), object_key)
        finally:
            csv_path.unlink(missing_ok=True)
        if uploaded is None:
            raise RuntimeError(f"Failed to upload predictions to '{object_key}'.")
        return uploaded
