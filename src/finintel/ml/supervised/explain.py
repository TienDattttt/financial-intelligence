"""Local and global TreeSHAP explanations for the fitted distress screen.

Verified Sources:
    https://shap.readthedocs.io/en/stable/generated/shap.TreeExplainer.html
    https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html
    Raw-margin SHAP values are additive to XGBoost's log-odds output. JSON
    exposes feature names and directionally ranked local contributions.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

import numpy as np
import polars as pl
import shap  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError
from finintel.ml.supervised.train import TrainingResult


class DriverRecord(TypedDict):
    feature: str
    value: float | None
    peer_median: float | None
    shap_delta: float


def _json_number(value: float | None) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None


def explain_distress(
    result: TrainingResult,
    frame: pl.DataFrame,
    *,
    peer_medians: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Return JSON contributions whose base plus SHAP sum reproduces raw margin."""
    if frame.is_empty():
        return pl.DataFrame(schema={"shap_top_drivers_json": pl.String})
    transformed = result.preprocessor.transform(frame)
    matrix = transformed.select(result.features).to_numpy()
    explainer = shap.TreeExplainer(result.classifier, model_output="raw")
    values = np.asarray(explainer.shap_values(matrix), dtype=float)
    if values.ndim == 3:
        values = values[:, :, -1]
    base = np.asarray(explainer.expected_value, dtype=float).reshape(-1)
    base_value = float(base[-1])
    margin = np.asarray(
        result.classifier.predict(matrix, output_margin=True), dtype=float
    )
    if values.shape != matrix.shape:
        raise ContractViolationError(
            "TreeSHAP output does not match the model feature matrix."
        )
    if not np.allclose(base_value + values.sum(axis=1), margin, rtol=1e-6, atol=1e-6):
        raise ContractViolationError(
            "TreeSHAP additivity check failed against XGBoost raw margins."
        )
    original_features = tuple(
        name for name in result.features if not name.endswith("_missing")
    )
    medians: dict[str, float | None] = {name: None for name in original_features}
    if peer_medians is not None:
        required = peer_medians.filter(
            pl.col("metric_or_ratio_name").is_in(original_features)
        ).select("metric_or_ratio_name", "peer_median")
        medians.update(
            dict(
                zip(
                    required["metric_or_ratio_name"].to_list(),
                    required["peer_median"].to_list(),
                    strict=True,
                )
            )
        )
    records: list[str] = []
    for row_index, contributions in enumerate(values):
        named: list[DriverRecord] = [
            {
                "feature": feature,
                "value": _json_number(
                    float(frame[feature][row_index])
                    if feature in frame.columns
                    and frame[feature][row_index] is not None
                    else None
                    if feature in frame.columns
                    else _json_number(float(transformed[feature][row_index]))
                    if feature in transformed.columns
                    else None
                ),
                "peer_median": _json_number(medians.get(feature)),
                "shap_delta": float(delta),
            }
            for feature, delta in zip(result.features, contributions, strict=True)
        ]
        increasing = sorted(
            (item for item in named if item["shap_delta"] > 0),
            key=lambda item: item["shap_delta"],
            reverse=True,
        )[:5]
        decreasing = sorted(
            (item for item in named if item["shap_delta"] < 0),
            key=lambda item: item["shap_delta"],
        )[:5]
        records.append(
            json.dumps(
                {
                    "base_value": base_value,
                    "prediction_score": float(margin[row_index]),
                    "top_risk_increasing": increasing,
                    "top_risk_decreasing": decreasing,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
        )
    return pl.DataFrame({"shap_top_drivers_json": records})


def explain_global_importance(
    result: TrainingResult, frame: pl.DataFrame
) -> dict[str, float]:
    """Summarize global mean absolute TreeSHAP contributions by original feature."""
    if frame.is_empty():
        return {}
    matrix = result.preprocessor.transform(frame).select(result.features).to_numpy()
    values: Any = shap.TreeExplainer(result.classifier, model_output="raw").shap_values(
        matrix
    )
    array = np.asarray(values, dtype=float)
    if array.ndim == 3:
        array = array[:, :, -1]
    return {
        name: float(abs(array[:, index]).mean())
        for index, name in enumerate(result.features)
    }
