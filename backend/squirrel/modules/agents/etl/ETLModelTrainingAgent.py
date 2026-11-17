"""Agent boundary for training models from ETL sink data."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
from sklearn.cluster import KMeans

from squirrel.modules.agents.builder.TabularDataModelBuilderAgent import (
    TabularDataModelBuilderAgent,
)
from squirrel.schemas.etl import MLSpec


class ETLModelTrainingAgent:
    """Delegate supervised work to the Workspace model builder and own clustering."""

    def run(self, frame: pd.DataFrame, spec: MLSpec) -> Dict[str, Any]:
        if spec.task == "clustering":
            features = list(spec.features) or list(frame.columns)
            values = self._numeric_frame(frame, features)
            model = KMeans(n_clusters=spec.n_clusters, random_state=42, n_init=10)
            model.fit(values)
            return {
                "model": model,
                "features": features,
                "metrics": {
                    "agent": self.__class__.__name__,
                    "inertia": float(model.inertia_),
                    "n_clusters": spec.n_clusters,
                },
            }

        assert spec.target
        features = list(spec.features) or [
            column for column in frame.columns if column != spec.target
        ]
        selected = [*features, spec.target]
        missing = [column for column in selected if column not in frame.columns]
        if missing:
            raise ValueError(f"ML columns are missing from the sink table: {missing}")
        if not features:
            raise ValueError("At least one ML feature is required.")

        fitted_models, summary = TabularDataModelBuilderAgent(
            target_column=spec.target
        ).run(
            data=frame[selected],
            preprocessing_summary={
                "requested_task": spec.task,
                "requested_algorithm": spec.algorithm,
                "test_size": spec.test_size,
                "features": features,
            },
            objective=(
                f"Train a {spec.task} model using features {features}. "
                f"Prefer the {spec.algorithm} algorithm and evaluate on "
                f"{spec.test_size:.0%} held-out data."
            ),
        )
        model_key = summary.get("best_model")
        model = fitted_models.get(model_key) if model_key else None
        if model is None:
            raise RuntimeError(
                f"The model-building agent did not produce a best model: {summary}"
            )
        return {
            "model": model,
            "features": features,
            "metrics": {
                "agent": "TabularDataModelBuilderAgent",
                "summary": summary,
            },
        }

    @staticmethod
    def _numeric_frame(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
        missing = [column for column in features if column not in frame.columns]
        if missing:
            raise ValueError(f"ML features are missing from the sink table: {missing}")
        values = frame[features].apply(pd.to_numeric, errors="coerce")
        if values.isna().any().any():
            raise ValueError("ML features must contain numeric, non-null values.")
        return values
