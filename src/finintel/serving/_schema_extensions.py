"""Extension Pydantic v2 DTOs (Model Card & SHAP Driver schemas) for Layer 5 serving.

Verified Sources:
- Pydantic v2 Documentation (``pydantic==2.14.4``):
  https://docs.pydantic.dev/latest/
- Lundberg, S. M. et al. (2020) TreeSHAP additive payload contract
  (``src/finintel/ml/supervised/explain.py:254-321``).
"""

from __future__ import annotations

import json
import math
import re
from datetime import date

from pydantic import BaseModel, Field

from finintel.serving.queries import RiskTier

_EXT_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}$")


def normalize_reference_icrs_map(
    value: object,
) -> dict[str, float | None] | None:
    """Validate and uppercase-normalize ``StressScenarioRequest.reference_icrs``."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            "reference_icrs must be a mapping of ticker strings to float or null."
        )
    normalized: dict[str, float | None] = {}
    for raw_key, raw_val in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("reference_icrs keys must be ticker strings.")
        tok = raw_key.strip().upper()
        if not tok or not _EXT_TICKER_RE.fullmatch(tok):
            raise ValueError(f"Invalid ticker format in reference_icrs: {raw_key!r}")
        if raw_val is None:
            normalized[tok] = None
        elif isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
            raise ValueError(
                f"Invalid ICR value in reference_icrs for {tok!r}: {raw_val!r}"
            )
        else:
            fval = float(raw_val)
            normalized[tok] = fval if math.isfinite(fval) else None
    return normalized


class ShapDriverItem(BaseModel):
    """Single TreeSHAP driver entry (risk-increasing or risk-decreasing)."""

    feature: str
    display_name_vn: str
    value: float | None = None
    peer_median: float | None = None
    shap_delta: float


class RiskTierCutoffsItem(BaseModel):
    """Frozen Through-the-Cycle PD boundaries separating R1..R5."""

    R2_min_pd: float
    R3_min_pd: float
    R4_min_pd: float
    R5_min_pd: float


class RiskTierReliabilityRowItem(BaseModel):
    """Empirical event rate and predicted PD for a single risk tier (R1..R5)."""

    risk_tier: RiskTier | str
    count: int
    share: float
    positive_count: int
    empirical_event_rate: float | None = None
    mean_predicted_pd: float | None = None


class RiskTierReliabilityReportItem(BaseModel):
    """Calibration reliability and monotonicity summary across R1..R5."""

    evaluation_source: str | None = None
    cutoffs: RiskTierCutoffsItem
    tiers: list[RiskTierReliabilityRowItem]
    is_monotone_non_decreasing: bool


class ModelMetricsItem(BaseModel):
    """Standardized evaluation metrics on common OOS samples."""

    roc_auc: float | None = None
    pr_auc: float | None = None
    brier_score: float | None = None
    brier_skill_score: float | None = None
    f2_score: float | None = None
    recall_at_top_5_percent: float | None = None
    recall_at_top_10_percent: float | None = None


class BootstrapCI95Item(BaseModel):
    """Issuer-clustered 95% bootstrap confidence intervals."""

    roc_auc: list[float] | None = None
    pr_auc: list[float] | None = None
    brier_score: list[float] | None = None
    brier_skill_score: list[float] | None = None
    recall_at_top_5_percent: list[float] | None = None
    recall_at_top_10_percent: list[float] | None = None


class WalkForwardYearItem(BaseModel):
    """Single expanding Walk-Forward test year comparison (All OOS & True Unseen Issuers)."""

    year: int
    train_rows: int
    train_positive_rows: int
    train_unique_positive_issuers: int
    train_prevalence: float | None = None
    test_rows: int
    test_positive_rows: int
    test_unique_positive_issuers: int
    unseen_rows: int
    unseen_positive_rows: int
    unseen_unique_positive_issuers: int
    models: dict[str, ModelMetricsItem]
    true_unseen_models: dict[str, ModelMetricsItem]


class WalkForwardModelSummaryItem(BaseModel):
    """Aggregated yearly mean/std + pooled cluster-bootstrap CI95 for one model."""

    model: str
    yearly_roc_auc_mean: float | None = None
    yearly_roc_auc_std: float | None = None
    yearly_pr_auc_mean: float | None = None
    yearly_pr_auc_std: float | None = None
    yearly_brier_mean: float | None = None
    yearly_brier_std: float | None = None
    yearly_bss_mean: float | None = None
    yearly_bss_std: float | None = None
    yearly_recall_top5_mean: float | None = None
    yearly_recall_top10_mean: float | None = None
    pooled_point: ModelMetricsItem
    pooled_ci95: BootstrapCI95Item
    n_bootstrap_valid: int
    n_clusters: int


class WalkForwardReportItem(BaseModel):
    """Complete 2-Way Purged Expanding Walk-Forward report (2021-2025)."""

    test_years: list[int]
    skipped_years: list[int] = Field(default_factory=list)
    pooled_reference_train_prevalence: float | None = None
    per_year: list[WalkForwardYearItem]
    summary_all_oos: list[WalkForwardModelSummaryItem]
    summary_true_unseen: list[WalkForwardModelSummaryItem]
    pooled_oos_risk_tier_reliability: RiskTierReliabilityReportItem | None = None


class TaxonomyScoreDriftItem(BaseModel):
    """Score & R4/R5 share drift across TT200 -> TT99 transition."""

    score_psi: float | None = None
    ref_mean_pd: float | None = None
    tgt_mean_pd: float | None = None
    ref_r4_r5_share: float | None = None
    tgt_r4_r5_share: float | None = None
    r4_r5_share_delta: float | None = None


class FeatureDriftItem(BaseModel):
    """Single feature PSI and missing-rate delta across TT200 -> TT99."""

    feature: str
    psi: float | None = None
    ref_missing_rate: float | None = None
    tgt_missing_rate: float | None = None
    missing_rate_delta: float | None = None


class TaxonomyDriftPSIItem(BaseModel):
    """Cross-taxonomy stability audit between VAS_TT200_2014 and VAS_TT99_2025 (M-05)."""

    reference_taxonomy: str
    target_taxonomy: str
    reference_rows: int
    target_rows: int
    mean_feature_psi: float | None = None
    max_feature_psi: float | None = None
    features_psi_gt_0_25: list[str]
    mean_ref_missing_rate: float | None = None
    mean_tgt_missing_rate: float | None = None
    score_drift: TaxonomyScoreDriftItem | None = None
    feature_drift: list[FeatureDriftItem]


class HistoricalCaseStudyItem(BaseModel):
    """Real Vietnamese corporate distress case study from the 23-feature model (ML-05/ML-07a)."""

    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    exchange: str | None = None
    first_warning_or_control_date: date | None = None
    first_warning_event_type: str | None = None
    forced_delisting_or_suspension_date: date | None = None
    exchange_delisted_from: str | None = None
    first_r4_r5_period_end_date: date | None = None
    first_r4_r5_fiscal_year: int | None = None
    first_r4_r5_fiscal_quarter: int | None = None
    first_r4_r5_available_at: date | None = None
    first_r4_r5_risk_tier: RiskTier | str | None = None
    first_r4_r5_pd: float | None = None
    first_r4_r5_market_percentile: float | None = None
    first_r4_r5_lift: float | None = None
    early_warning_lead_quarters: int | None = None
    early_warning_lead_days: int | None = None
    peak_risk_tier: RiskTier | str | None = None
    peak_pd: float | None = None
    peak_period_end_date: date | None = None
    top_3_shap_drivers: list[ShapDriverItem] = Field(default_factory=list)


class TT99MrmGateItem(BaseModel):
    """TT200/2014 -> TT99/2025 Model Risk Management drift gate status (V3-02)."""

    verdict: str
    shadow_mode_active: bool
    auto_promote_allowed: bool
    tt99_sample_count: int
    min_shadow_samples: int
    min_ga_samples: int
    psi_breach_threshold: float
    missing_jump_threshold: float
    max_feature_psi: float | None = None
    max_missing_jump: float | None = None
    breached_psi_features: list[str] = Field(default_factory=list)
    breached_missing_features: list[str] = Field(default_factory=list)
    non_finite_features: list[str] = Field(default_factory=list)
    summary_vi: str


class ModelCardResponse(BaseModel):
    """V2 Meta route response: complete DuckLake-persisted Model Card (ML-08 / H-04)."""

    snapshot_id: str | None
    schema_version: int = 1
    run_id: str
    model_version: str
    model_role: str
    target_mode: str
    statement_scope: str
    feature_set: str
    feature_count: int
    selected_features: list[str]
    peer_standardization_mode: str
    trained_as_of: date
    generated_at: str
    artifact_sha256: str
    platt_slope: float
    platt_intercept: float
    train_prevalence: float
    risk_tier_cutoffs: RiskTierCutoffsItem
    f2_thresholds: dict[str, float]
    walk_forward: WalkForwardReportItem
    risk_tier_reliability: RiskTierReliabilityReportItem
    taxonomy_drift_psi: TaxonomyDriftPSIItem
    tt99_mrm_gate: TT99MrmGateItem | None = None
    case_studies: list[HistoricalCaseStudyItem]
    limitations: list[str]


class ShapExplanationItem(BaseModel):
    """Parsed TreeSHAP local explanation with additive ``other_features_delta``."""

    base_value: float
    prediction_score: float
    other_features_delta: float
    top_risk_increasing: list[ShapDriverItem]
    top_risk_decreasing: list[ShapDriverItem]


def _finite_float_or_none(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def parse_shap_top_drivers(raw_json: str | None) -> ShapExplanationItem | None:
    """Parse ``shap_top_drivers_json`` with graceful fallback for legacy snapshots."""
    if not raw_json or not isinstance(raw_json, str):
        return None
    try:
        parsed = json.loads(raw_json)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None

    base_value = _finite_float_or_none(parsed.get("base_value"))
    prediction_score = _finite_float_or_none(parsed.get("prediction_score"))
    if base_value is None or prediction_score is None:
        return None

    def _parse_drivers(raw_items: object) -> list[ShapDriverItem]:
        if not isinstance(raw_items, list):
            return []
        drivers: list[ShapDriverItem] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            feature = entry.get("feature")
            shap_delta = _finite_float_or_none(entry.get("shap_delta"))
            if not isinstance(feature, str) or not feature or shap_delta is None:
                continue
            raw_vn = entry.get("display_name_vn")
            display_name_vn = (
                str(raw_vn) if isinstance(raw_vn, str) and raw_vn.strip() else feature
            )
            drivers.append(
                ShapDriverItem(
                    feature=feature,
                    display_name_vn=display_name_vn,
                    value=_finite_float_or_none(entry.get("value")),
                    peer_median=_finite_float_or_none(entry.get("peer_median")),
                    shap_delta=shap_delta,
                )
            )
        return drivers

    inc = _parse_drivers(parsed.get("top_risk_increasing"))
    dec = _parse_drivers(parsed.get("top_risk_decreasing"))
    other = _finite_float_or_none(parsed.get("other_features_delta"))
    if other is None:
        other = (
            prediction_score
            - base_value
            - sum(item.shap_delta for item in inc)
            - sum(item.shap_delta for item in dec)
        )
    return ShapExplanationItem(
        base_value=base_value,
        prediction_score=prediction_score,
        other_features_delta=other,
        top_risk_increasing=inc,
        top_risk_decreasing=dec,
    )
