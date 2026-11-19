"""Agent boundary for training models from ETL sink data."""
from __future__ import annotations

from typing import Any, Dict, List

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
                "details": {
                    "task_type": "clustering",
                    "algorithm": "KMeans",
                    "target": None,
                    "input_schema": self._input_schema(frame, features),
                    "preprocessing": [
                        "Selected features coerced to numeric values.",
                        "Rows with null or non-numeric features rejected.",
                        f"Requested clusters: {spec.n_clusters}",
                    ],
                    "training_history": self._clustering_history(
                        values, spec.n_clusters
                    ),
                    "n_features": len(features),
                    "n_samples": int(len(values)),
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

        details = dict(summary.get("model_details") or {})
        details.setdefault("task_type", spec.task)
        details.setdefault("algorithm", model_key)
        details.setdefault("target", spec.target)
        details.setdefault("input_schema", self._input_schema(frame, features))
        details.setdefault(
            "preprocessing",
            [
                "Selected features coerced to numeric values.",
                "Rows with null or non-numeric features rejected.",
                f"Target column: {spec.target}",
            ],
        )
        details.setdefault("n_features", len(features))
        details.setdefault("n_samples", int(len(frame)))

        return {
            "model": model,
            "features": features,
            "metrics": {
                "agent": "TabularDataModelBuilderAgent",
                "summary": summary,
            },
            "details": details,
        }

    @staticmethod
    def _input_schema(frame: pd.DataFrame, features: List[str]) -> List[Dict[str, Any]]:
        """Describe the feature columns the trained model expects at inference."""
        schema: List[Dict[str, Any]] = []
        for column in features:
            series = frame[column]
            example: Any = None
            non_null = series.dropna()
            if not non_null.empty:
                value = non_null.iloc[0]
                example = value.item() if hasattr(value, "item") else value
                if not isinstance(example, (int, float, str, bool)):
                    example = str(example)[:80]
            schema.append(
                {
                    "name": str(column),
                    "dtype": str(series.dtype),
                    "required": True,
                    "example": example,
                }
            )
        return schema

    @staticmethod
    def _clustering_history(values: pd.DataFrame, requested: int) -> Dict[str, Any] | None:
        """Elbow curve (inertia vs k) for the KMeans model card."""
        rows = len(values)
        if rows < 4:
            return None
        upper = min(8, rows - 1, max(requested + 3, 5))
        if upper < 2:
            return None
        points: List[Dict[str, Any]] = []
        for k in range(2, upper + 1):
            try:
                candidate = KMeans(n_clusters=k, random_state=42, n_init=10)
                candidate.fit(values)
                points.append({"x": k, "y": round(float(candidate.inertia_), 6)})
            except Exception:
                continue
        if not points:
            return None
        return {
            "kind": "elbow",
            "x_label": "Clusters (k)",
            "y_label": "Inertia",
            "series": [{"name": "Inertia", "points": points}],
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
