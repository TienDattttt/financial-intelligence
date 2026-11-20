"""Local and global TreeSHAP explanations for the fitted distress screen.

Verified Sources:
    https://shap.readthedocs.io/en/stable/generated/shap.TreeExplainer.html
    https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html
    Lundberg, S. M. et al. (2020). "From local explanations to global understanding
        with explainable AI for trees." Nature Machine Intelligence, Vol. 2, pp. 56-67.
    Raw-margin SHAP values are additive to XGBoost's log-odds output within 1e-6.
    Missingness indicator contributions are folded into their corresponding original
    financial feature so exact additivity holds over original feature names without
    leaking synthetic *_missing columns into downstream UI payloads.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import shap  # type: ignore[import-untyped]
from numpy.typing import NDArray

from finintel.exceptions import ContractViolationError
from finintel.ml.supervised.train import TrainingResult
from finintel.storage.schemas import ML_SCORES_KEY

_FEATURE_DISPLAY_NAME_VN: dict[str, str] = {
    "accruals_to_assets": "Dồn tích trên tổng tài sản",
    "altman_z_double_prime": "Điểm Altman Z''",
    "asset_turnover": "Vòng quay tổng tài sản",
    "cash_conversion_cycle": "Chu kỳ chuyển đổi tiền mặt (CCC)",
    "cash_ratio": "Tỷ số thanh khoản tiền mặt",
    "current_ratio": "Tỷ số thanh khoản hiện hành",
    "debt_to_equity": "Nợ vay trên vốn chủ sở hữu (D/E)",
    "ebit_margin": "Biên lợi nhuận EBIT",
    "fcf_to_revenue": "Dòng tiền tự do trên doanh thu",
    "financial_debt_to_assets": "Nợ vay trên tổng tài sản",
    "interest_coverage": "Khả năng chi trả lãi vay",
    "liabilities_to_assets": "Nợ phải trả trên tổng tài sản",
    "net_debt_to_ebitda": "Nợ vay ròng trên EBITDA",
    "ocf_to_current_liabilities": "Dòng tiền HĐKD trên nợ ngắn hạn",
    "piotroski_f_score": "Điểm Piotroski F-Score",
    "quick_ratio": "Tỷ số thanh khoản nhanh",
    "revenue_growth_yoy": "Tăng trưởng doanh thu thuần YoY",
    "roa": "Tỷ suất sinh lời trên tài sản (ROA)",
    "roe_parent": "Tỷ suất sinh lời trên VCSH công ty mẹ (ROE)",
    "roic": "Tỷ suất sinh lời trên vốn đầu tư (ROIC)",
    "sector_z_asset_turnover": "Vòng quay tài sản chuẩn hóa ngành (Z-Score)",
    "sector_z_cash_conversion_cycle": "Chu kỳ tiền mặt chuẩn hóa ngành (Z-Score)",
    "sector_z_debt_to_equity": "Đòn bẩy D/E chuẩn hóa ngành (Z-Score)",
    "sector_z_ebit_margin": "Biên EBIT chuẩn hóa ngành (Z-Score)",
}

_DRIVER_LIST_DTYPE = pl.List(
    pl.Struct(
        {
            "feature": pl.String,
            "display_name_vn": pl.String,
            "value": pl.Float64,
            "peer_median": pl.Float64,
            "shap_delta": pl.Float64,
        }
    )
)
_PEER_JOIN_CANDIDATES = (
    "issuer_id",
    "accounting_regime",
    "statement_scope",
    "period_type",
    "period_end_date",
)


def compute_shap_contributions(
    result: TrainingResult, frame: pl.DataFrame
) -> tuple[float, tuple[str, ...], NDArray[np.float64], NDArray[np.float64]]:
    """Return base_value, original_features, aggregated SHAP matrix and raw margins."""
    transformed = result.preprocessor.transform(frame)
    matrix = transformed.select(result.features).to_numpy()
    explainer = shap.TreeExplainer(result.classifier, model_output="raw")
    values = np.asarray(explainer.shap_values(matrix), dtype=np.float64)
    if values.ndim == 3:
        values = values[:, :, -1]
    base = np.asarray(explainer.expected_value, dtype=np.float64).reshape(-1)
    base_value = float(base[-1])
    margin = np.asarray(
        result.classifier.predict(matrix, output_margin=True), dtype=np.float64
    )
    if values.shape != matrix.shape:
        raise ContractViolationError(
            "TreeSHAP output does not match the model feature matrix."
        )

    feature_to_index = {name: idx for idx, name in enumerate(result.features)}
    original_features = tuple(
        name for name in result.features if not name.endswith("_missing")
    )
    aggregated = np.zeros((frame.height, len(original_features)), dtype=np.float64)
    for out_idx, name in enumerate(original_features):
        contrib = values[:, feature_to_index[name]]
        missing_name = f"{name}_missing"
        if missing_name in feature_to_index:
            contrib = contrib + values[:, feature_to_index[missing_name]]
        aggregated[:, out_idx] = contrib

    if not np.allclose(
        base_value + aggregated.sum(axis=1), margin, rtol=1e-6, atol=1e-6
    ):
        raise ContractViolationError(
            "TreeSHAP additivity check failed against XGBoost raw margins."
        )
    return base_value, original_features, aggregated, margin


def _resolve_peer_medians_long(
    frame_with_id: pl.DataFrame,
    original_features: tuple[str, ...],
    peer_medians: pl.DataFrame | None,
) -> pl.DataFrame:
    empty = pl.DataFrame(
        schema={"_row_id": pl.UInt32, "feature": pl.String, "peer_median": pl.Float64}
    )
    if peer_medians is None or peer_medians.is_empty():
        return empty
    if {"metric_or_ratio_name", "peer_median"} - set(peer_medians.columns):
        raise ContractViolationError(
            "peer_medians requires metric_or_ratio_name and peer_median columns."
        )
    filtered = peer_medians.filter(
        pl.col("metric_or_ratio_name").is_in(original_features)
    ).with_columns(
        pl.when(pl.col("peer_median").cast(pl.Float64).is_finite())
        .then(pl.col("peer_median").cast(pl.Float64))
        .otherwise(None)
        .alias("peer_median")
    )
    if filtered.is_empty():
        return empty

    join_keys = [
        key
        for key in _PEER_JOIN_CANDIDATES
        if key in frame_with_id.columns and key in filtered.columns
    ]
    if join_keys:
        dedup_keys = [*join_keys, "metric_or_ratio_name"]
        if filtered.select(pl.struct(dedup_keys).is_duplicated().any()).item():
            raise ContractViolationError(
                "peer_medians contains duplicate benchmark rows for the same entity/period/metric."
            )
        return (
            frame_with_id.select("_row_id", *join_keys)
            .join(
                filtered.select(*join_keys, "metric_or_ratio_name", "peer_median"),
                on=join_keys,
                how="inner",
            )
            .rename({"metric_or_ratio_name": "feature"})
            .select("_row_id", "feature", "peer_median")
        )

    if filtered["metric_or_ratio_name"].is_duplicated().any():
        raise ContractViolationError(
            "Multi-row peer_medians requires matching entity/period join keys on frame."
        )
    return (
        frame_with_id.select("_row_id")
        .join(
            filtered.select(
                pl.col("metric_or_ratio_name").alias("feature"), "peer_median"
            ),
            how="cross",
        )
        .select("_row_id", "feature", "peer_median")
    )


def explain_distress(
    result: TrainingResult,
    frame: pl.DataFrame,
    *,
    peer_medians: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Return vectorized JSON contributions whose base + top drivers + other_features_delta reproduces raw margin."""
    present_keys = [name for name in ML_SCORES_KEY if name in frame.columns]
    if frame.is_empty():
        empty_json = pl.DataFrame(schema={"shap_top_drivers_json": pl.String})
        return (
            frame.select(present_keys).hstack(empty_json)
            if present_keys
            else empty_json
        )

    base_value, original_features, orig_shap, margin = compute_shap_contributions(
        result, frame
    )
    indexed = frame.with_row_index("_row_id")

    shap_long = (
        pl.DataFrame(orig_shap, schema=list(original_features), orient="row")
        .with_row_index("_row_id")
        .unpivot(
            index="_row_id",
            on=list(original_features),
            variable_name="feature",
            value_name="shap_delta",
        )
    )
    values_long = indexed.select(
        "_row_id",
        *(
            pl.when(pl.col(name).cast(pl.Float64).is_finite())
            .then(pl.col(name).cast(pl.Float64))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias(name)
            if name in indexed.columns
            else pl.lit(None, dtype=pl.Float64).alias(name)
            for name in original_features
        ),
    ).unpivot(
        index="_row_id",
        on=list(original_features),
        variable_name="feature",
        value_name="value",
    )
    medians_long = _resolve_peer_medians_long(indexed, original_features, peer_medians)

    drivers_long = (
        shap_long.join(values_long, on=["_row_id", "feature"], how="left")
        .join(medians_long, on=["_row_id", "feature"], how="left")
        .select(
            "_row_id",
            "feature",
            pl.col("feature")
            .replace_strict(_FEATURE_DISPLAY_NAME_VN, default=pl.col("feature"))
            .cast(pl.String)
            .alias("display_name_vn"),
            pl.col("value").cast(pl.Float64),
            pl.when(pl.col("feature").str.starts_with("sector_z_"))
            .then(
                pl.coalesce(
                    pl.col("peer_median").cast(pl.Float64),
                    pl.lit(0.0, dtype=pl.Float64),
                )
            )
            .otherwise(pl.col("peer_median").cast(pl.Float64))
            .alias("peer_median"),
            pl.col("shap_delta").cast(pl.Float64),
        )
    )
    increasing = (
        drivers_long.filter(pl.col("shap_delta") > 0.0)
        .sort(["_row_id", "shap_delta", "feature"], descending=[False, True, False])
        .group_by("_row_id", maintain_order=True)
        .agg(
            pl.struct(
                "feature",
                "display_name_vn",
                "value",
                "peer_median",
                "shap_delta",
            )
            .head(5)
            .alias("top_risk_increasing"),
            pl.col("shap_delta").head(5).sum().alias("_inc_sum"),
        )
    )
    decreasing = (
        drivers_long.filter(pl.col("shap_delta") < 0.0)
        .sort(["_row_id", "shap_delta", "feature"], descending=[False, False, False])
        .group_by("_row_id", maintain_order=True)
        .agg(
            pl.struct(
                "feature",
                "display_name_vn",
                "value",
                "peer_median",
                "shap_delta",
            )
            .head(5)
            .alias("top_risk_decreasing"),
            pl.col("shap_delta").head(5).sum().alias("_dec_sum"),
        )
    )

    output = (
        pl.DataFrame(
            {
                "_row_id": np.arange(frame.height, dtype=np.uint32),
                "base_value": np.full(frame.height, base_value, dtype=np.float64),
                "prediction_score": margin,
            }
        )
        .join(increasing, on="_row_id", how="left")
        .join(decreasing, on="_row_id", how="left")
        .with_columns(
            pl.col("top_risk_increasing").fill_null(
                pl.lit([], dtype=_DRIVER_LIST_DTYPE)
            ),
            pl.col("top_risk_decreasing").fill_null(
                pl.lit([], dtype=_DRIVER_LIST_DTYPE)
            ),
            (
                pl.col("prediction_score")
                - pl.col("base_value")
                - pl.col("_inc_sum").fill_null(0.0)
                - pl.col("_dec_sum").fill_null(0.0)
            )
            .cast(pl.Float64)
            .alias("other_features_delta"),
        )
        .sort("_row_id")
        .select(
            pl.struct(
                "base_value",
                "prediction_score",
                "other_features_delta",
                "top_risk_increasing",
                "top_risk_decreasing",
            )
            .struct.json_encode()
            .alias("shap_top_drivers_json")
        )
    )
    return frame.select(present_keys).hstack(output) if present_keys else output


def explain_global_importance(
    result: TrainingResult, frame: pl.DataFrame
) -> dict[str, float]:
    """Summarize global mean absolute TreeSHAP contributions by original feature."""
    if frame.is_empty():
        return {}
    _, original_features, orig_shap, _ = compute_shap_contributions(result, frame)
    return {
        name: float(np.abs(orig_shap[:, index]).mean())
        for index, name in enumerate(original_features)
    }
