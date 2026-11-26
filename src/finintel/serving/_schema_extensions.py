"""Extension Pydantic v2 DTOs (Model Card & SHAP Driver schemas) for Layer 5 serving.

Verified Sources:
- Pydantic v2 Documentation (``pydantic==2.14.4``):
  https://docs.pydantic.dev/latest/
- Lundberg, S. M. et al. (2020) TreeSHAP additive payload contract
  (``src/finintel/ml/supervised/explain.py:254-321``).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from finintel.serving.queries import RiskTier


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
    case_studies: list[HistoricalCaseStudyItem]
    limitations: list[str]
