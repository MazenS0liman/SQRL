"""Small, bounded model trainer for pipeline outputs."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import joblib
import pandas as pd

from squirrel.core.config import settings
from squirrel.modules.agents.etl.ETLModelTrainingAgent import (
    ETLModelTrainingAgent,
)
from squirrel.schemas.etl import MLSpec


class MLTrainer:
    """Delegate model building to the shared Workspace model-building agent."""

    def __init__(self, minio: Any) -> None:
        self._minio = minio

    def train(
        self,
        frame: pd.DataFrame,
        spec: MLSpec,
        *,
        run_id: str,
        pipeline_id: str,
    ) -> Dict[str, Any]:
        if frame.empty:
            raise ValueError("The sink table is empty; there is no data to train on.")
        if len(frame) > int(getattr(settings, "ETL_ML_MAX_ROWS", 200_000)):
            raise ValueError("The sink table exceeds the configured ML row limit.")

        trained = ETLModelTrainingAgent().run(frame, spec)
        model = trained["model"]
        features = trained["features"]
        metrics = trained["metrics"]

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as temporary:
            model_path = Path(temporary.name)
        try:
            joblib.dump(model, model_path)
            object_key = f"etl/models/{pipeline_id}/{run_id}.joblib"
            uploaded = self._minio.upload_file(str(model_path), object_key)
        finally:
            model_path.unlink(missing_ok=True)
        if uploaded is None:
            raise RuntimeError(f"Failed to upload trained model to '{object_key}'.")

        return {
            "task": spec.task,
            "algorithm": spec.algorithm,
            "features": features,
            "metrics": metrics,
            "model_key": object_key,
            "file_url": uploaded.fileUrl,
        }

