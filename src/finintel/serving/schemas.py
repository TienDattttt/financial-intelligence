"""Strict Pydantic v2 request and response DTO schemas for the Layer 5 FastAPI API.

Verified Sources:
- Pydantic v2 Documentation (``pydantic==2.14.4``, ``Field(ge=..., le=..., allow_inf_nan=False)``):
  https://docs.pydantic.dev/latest/
- FastAPI Official Documentation (``fastapi==0.165.0``):
  https://fastapi.tiangolo.com/
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216 (matching exact decimal bounds in
  ``src/finintel/analytics/stress_test.py:38-44``).
- Lundberg, S. M. et al. (2020) TreeSHAP additive payload contract
  (``src/finintel/ml/supervised/explain.py:254-321``) with legacy-snapshot fallback
  verified against ``snap_universe_exact_pit_g1_001``.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from finintel.serving.dictionary import (
    RatioCategory,
    RatioDirection,
    RatioFormatType,
    StatementType,
)
from finintel.serving.queries import (
    AltmanZone,
    BeneishFlag,
    DenominatorMode,
    PeerGroupLevel,
    PeriodType,
    PeriodView,
    RiskTier,
    StatementScope,
)

ScreenerSortField = Literal[
    "ticker",
    "calibrated_distress_prob",
    "altman_z_double_prime",
    "piotroski_f_score",
    "beneish_m8_score",
    "red_flag_count",
    "roe_total",
    "debt_to_equity",
]
SortDirection = Literal["asc", "desc"]

RatioName = Literal[
    "gross_margin",
    "ebit_margin",
    "ebitda_margin",
    "operating_margin",
    "net_profit_margin",
    "roa",
    "roe_total",
    "roe_parent",
    "roic",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "ocf_to_current_liabilities",
    "liabilities_to_assets",
    "financial_debt_to_assets",
    "debt_to_equity",
    "net_debt_to_equity",
    "net_debt_to_ebitda",
    "interest_coverage",
    "asset_turnover",
    "inventory_turnover",
    "receivables_turnover",
    "payables_turnover",
    "cash_conversion_cycle",
    "ocf_to_net_income",
    "fcf_to_revenue",
    "accruals_to_assets",
    "eps_ttm_estimated",
    "equity_to_assets",
    "dio",
    "dso",
    "dpo",
    "revenue_growth_yoy",
    "ebit_growth_yoy",
    "net_income_parent_growth_yoy",
    "core_operating_margin",
    "non_core_distortion_to_assets",
    "core_to_ebt_ratio",
]


def _finite_float_or_none(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


class HealthResponse(BaseModel):
    """Route 1 response: service readiness and active snapshot binding."""

    status: Literal["ok", "degraded"]
    active_snapshot_id: str | None
    storage_backend: str


class LivenessResponse(BaseModel):
    """Event-loop-only Kubernetes/Docker liveness probe response."""

    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    """Deep storage & DuckDB readiness probe response (HTTP 200 ready / HTTP 503 unready)."""

    status: Literal["ready", "unready"]
    active_snapshot_id: str | None = None
    storage_backend: str = "parquet"
    duckdb_ok: bool = False
    snapshot_readable: bool = False
    cache_enabled: bool = False
    detail: str | None = None


class AvailablePeriodsResponse(BaseModel):
    """Distinct available reporting period dates for a ``(statement_scope, period_type)`` slice."""

    snapshot_id: str | None
    statement_scope: StatementScope
    period_type: PeriodType
    periods: list[date]


class SnapshotOverviewResponse(BaseModel):
    """Route 2 response: active snapshot summary counts."""

    snapshot_id: str | None
    published_at: str | None
    storage_backend: str
    total_issuers: int
    total_ratio_periods: int
    pass_periods: int
    warning_periods: int
    quarantined_rows: int


class IssuerItem(BaseModel):
    """Single issuer record from ``issuers.parquet``."""

    issuer_id: str
    ticker: str
    exchange: str | None = None
    company_name_vn: str | None = None
    accounting_regime: str
    icb_code_level2: str | None = None
    icb_name_level2: str | None = None
    icb_code_level3: str | None = None
    icb_name_level3: str | None = None
    industry_snapshot_date: date | None = None
    delisted_date: date | None = None


class IssuersPageResponse(BaseModel):
    """Route 3 response: paginated issuers list."""

    snapshot_id: str | None
    total: int
    limit: int
    offset: int
    items: list[IssuerItem]


class ShapDriverItem(BaseModel):
    """Single TreeSHAP driver entry (risk-increasing or risk-decreasing)."""

    feature: str
    display_name_vn: str
    value: float | None = None
    peer_median: float | None = None
    shap_delta: float


class ShapExplanationItem(BaseModel):
    """Parsed TreeSHAP local explanation with additive ``other_features_delta``."""

    base_value: float
    prediction_score: float
    other_features_delta: float
    top_risk_increasing: list[ShapDriverItem]
    top_risk_decreasing: list[ShapDriverItem]


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


class MLScoreItem(BaseModel):
    """Single period Layer 4B classical + unsupervised + supervised ML score record."""

    issuer_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    filing_id: str
    model_role: str | None = None
    altman_z_double_prime: float | None = None
    altman_em_score: float | None = None
    altman_zone: str | None = None
    piotroski_f_score: int | None = None
    piotroski_valid_components_count: int | None = None
    beneish_m8_score: float | None = None
    beneish_flag: str | None = None
    pca_pc1: float | None = None
    pca_pc2: float | None = None
    pca_pc3: float | None = None
    cluster_id: int | None = None
    cluster_archetype_name: str | None = None
    anomaly_score_raw: float | None = None
    anomaly_percentile_rank: float | None = None
    statistical_outlier_flag: bool | None = None
    anomaly_flag: str | None = None
    distress_risk_score: float | None = None
    calibrated_distress_prob: float | None = None
    risk_tier: RiskTier | str | None = None
    risk_market_percentile: float | None = None
    market_base_rate: float | None = None
    relative_risk_lift: float | None = None
    model_version: str | None = None
    shap_top_drivers_json: str | None = None
    shap_top_drivers: ShapExplanationItem | None = None


class GrowthPoint(BaseModel):
    """Pre-computed revenue, net income, and CFO time-series point."""

    period_end_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    IS_NET_REVENUE: float | None = None
    IS_NET_INCOME: float | None = None
    CF_OPERATING_CASH_FLOW: float | None = None


class CapitalStructurePoint(BaseModel):
    """Pre-computed assets, equity, liabilities, and total debt time-series point."""

    period_end_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    BS_TOTAL_ASSETS: float | None = None
    BS_TOTAL_EQUITY: float | None = None
    BS_TOTAL_LIABILITIES: float | None = None
    total_debt: float | None = None


class DuPontPoint(BaseModel):
    """Pre-computed 5-factor DuPont decomposition point."""

    period_end_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    taxonomy_version: str | None = None
    tax_burden: float | None = None
    interest_burden: float | None = None
    ebit_margin: float | None = None
    asset_turnover: float | None = None
    equity_multiplier_total: float | None = None
    roe_total: float | None = None
    dupont_valid_flag: bool | None = None


class CoreEarningsPoint(BaseModel):
    """Pre-computed V2 Core Earnings quality metrics (Rouen, So & Wang 2021)."""

    period_end_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    taxonomy_version: str | None = None
    core_operating_profit_ttm: float | None = None
    core_operating_profit_flag: str | None = None
    core_operating_margin: float | None = None
    core_operating_margin_flag: str | None = None
    non_core_distortion_to_assets: float | None = None
    non_core_distortion_to_assets_flag: str | None = None
    core_to_ebt_ratio: float | None = None
    core_to_ebt_ratio_flag: str | None = None


class RedFlagItem(BaseModel):
    """Single Layer 4A accounting red flag record (``RF_01..RF_09``)."""

    period_end_date: date
    flag_code: str
    severity: str
    flag_title_vn: str | None = None
    explanation_text_vn: str | None = None
    evidence_json: str | None = None
    filing_id: str | None = None


class CompanyProfileResponse(BaseModel):
    """Route 4 response: full financial profile, core earnings, red flags, and ML scores."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    company_name_vn: str | None = None
    accounting_regime: str
    statement_scope: StatementScope
    period_view: PeriodView
    latest_taxonomy_version: str | None = None
    latest_quality_status: str | None = None
    latest_warning_codes: list[str]
    growth_series: list[GrowthPoint]
    capital_structure_series: list[CapitalStructurePoint]
    dupont_series: list[DuPontPoint]
    core_earnings_series: list[CoreEarningsPoint]
    red_flags: list[RedFlagItem]
    ratios_table: list[dict[str, Any]]
    ml_scores: list[MLScoreItem]


class PeerRankingItem(BaseModel):
    """Single peer issuer row in an ICB cohort ranking."""

    ticker: str | None = None
    company_name_vn: str | None = None
    value: float | None = None
    percentile_rank: float | None = None
    robust_z_score: float | None = None
    peer_group_level: str | None = None
    icb_code_used: str | None = None
    icb_name_level3: str | None = None
    icb_name_level2: str | None = None


class PeerBandHistoryPoint(BaseModel):
    """Single period on the historical peer percentile band trajectory."""

    period_end_date: date
    value: float | None = None
    peer_p25: float | None = None
    peer_median: float | None = None
    peer_p75: float | None = None
    percentile_rank: float | None = None
    peer_group_level: PeerGroupLevel | str | None = None
    icb_code_used: str | None = None
    peer_count: int | None = None


class CompanyBenchmarksResponse(BaseModel):
    """Route 5 response: cross-sectional ICB peer benchmark + optional history trajectory."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    statement_scope: StatementScope
    period_type: PeriodType
    period_end_date: date
    metric_name: RatioName
    peer_group_level: str | None = None
    icb_code_used: str | None = None
    peer_count: int
    company_value: float | None = None
    peer_median: float | None = None
    peer_p25: float | None = None
    peer_p75: float | None = None
    peer_iqr: float | None = None
    percentile_rank: float | None = None
    robust_z_score: float | None = None
    industry_snapshot_date: date | None = None
    peer_ranking: list[PeerRankingItem]
    history: list[PeerBandHistoryPoint]


class LineageComponentItem(BaseModel):
    """Single numerator or denominator component provenance entry."""

    role: Literal["NUMERATOR", "DENOMINATOR"]
    metric_id: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None = None
    period_end_date: date
    value: float | None = None
    filing_id: str
    taxonomy_version: str
    derivation_method: str
    source_provider: str | None = None
    connector_name: str | None = None
    published_at: date | None = None
    published_at_basis: str | None = None
    effective_available_at: date | None = None
    canonical_content_sha256: str | None = None


class RatioLineageResponse(BaseModel):
    """Route 6 response: full audit lineage card for any of the 38 published ratios."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    statement_scope: StatementScope
    period_type: PeriodType
    fiscal_year: int
    fiscal_quarter: int | None = None
    period_end_date: date
    ratio_name: RatioName
    display_name_vn: str
    formula: str
    ratio_value: float | None = None
    status_flag: str | None = None
    taxonomy_version: str
    denominator_mode: DenominatorMode
    anchor_filing_id: str
    source_filing_ids: list[str]
    numerator_components: list[LineageComponentItem]
    denominator_components: list[LineageComponentItem]
    orphan_filing_ids: list[str]


class ScreenerItem(BaseModel):
    """Single issuer row matching ``DashboardQueryService.screen_market()`` (41 columns)."""

    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    exchange: str | None = None
    accounting_regime: str
    icb_code_level2: str | None = None
    icb_name_level2: str | None = None
    icb_code_level3: str | None = None
    icb_name_level3: str | None = None
    statement_scope: str
    period_type: str
    period_end_date: date
    taxonomy_version: str | None = None
    roe_total: float | None = None
    roa: float | None = None
    ebit_margin: float | None = None
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    accruals_to_assets: float | None = None
    core_operating_margin: float | None = None
    non_core_distortion_to_assets: float | None = None
    core_to_ebt_ratio: float | None = None
    red_flag_count: int
    critical_red_flag_count: int
    altman_z_double_prime: float | None = None
    altman_em_score: float | None = None
    altman_zone: AltmanZone | str | None = None
    piotroski_f_score: int | None = None
    beneish_m8_score: float | None = None
    beneish_flag: BeneishFlag | str | None = None
    cluster_id: int | None = None
    cluster_archetype_name: str | None = None
    anomaly_flag: str | None = None
    distress_risk_score: float | None = None
    calibrated_distress_prob: float | None = None
    risk_tier: RiskTier | str | None = None
    risk_market_percentile: float | None = None
    market_base_rate: float | None = None
    relative_risk_lift: float | None = None
    model_role: str | None = None
    model_version: str | None = None


class ScreenerResponse(BaseModel):
    """Route 7 response: paginated historical cross-sectional screener results."""

    snapshot_id: str | None
    statement_scope: StatementScope
    period_type: PeriodType
    period_end_date: date | None = None
    total: int
    limit: int
    offset: int
    items: list[ScreenerItem]


class StressScenarioRequest(BaseModel):
    """Route 8 request body: IMF WP/15/216 corporate shock scenario in decimal units."""

    model_config = ConfigDict(extra="forbid")

    scenario_name: str = Field("Custom Stress", min_length=1, max_length=120)
    statement_scope: StatementScope = "CONSOLIDATED"
    period_type: PeriodType = "QUARTER"
    period_end_date: date | None = None
    revenue_shock_pct: float = Field(0.0, ge=-0.80, le=0.50, allow_inf_nan=False)
    gross_margin_shock_pp: float = Field(0.0, ge=-0.50, le=0.20, allow_inf_nan=False)
    interest_rate_shock_bps: float = Field(
        0.0, ge=-500.0, le=1500.0, allow_inf_nan=False
    )
    dso_shock_days: float = Field(0.0, ge=-90.0, le=180.0, allow_inf_nan=False)
    dio_shock_days: float = Field(0.0, ge=-90.0, le=180.0, allow_inf_nan=False)
    top_n: int = Field(50, ge=1, le=200)
    include_all_newly_distressed: bool = False

    @field_validator("scenario_name")
    @classmethod
    def _non_blank_scenario_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("scenario_name must not be blank.")
        return stripped


class StressSectorSummaryItem(BaseModel):
    """Single ICB Level 2 sector row from ``summarize_stress_by_sector()``."""

    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int
    total_debt_sum: float
    baseline_debt_at_risk_pct: float
    stressed_debt_at_risk_pct: float
    newly_distressed_count: int


class StressIssuerItem(BaseModel):
    """Single corporate issuer result from ``simulate_financial_stress()``."""

    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    icb_code_level2: str | None = None
    icb_name_level2: str | None = None
    total_debt: float | None = None
    interest_coverage: float | None = None
    stressed_interest_coverage: float | None = None
    stressed_interest_coverage_flag: str | None = None
    ebit_ttm: float | None = None
    stressed_ebit_ttm: float | None = None
    borrowing_cost_ttm: float | None = None
    stressed_borrowing_cost_ttm: float | None = None
    cfo_ttm: float | None = None
    stressed_cfo_ttm: float | None = None
    baseline_debt_at_risk: bool | None = None
    stressed_debt_at_risk: bool | None = None
    newly_distressed_under_shock: bool | None = None


class StressTestResponse(BaseModel):
    """Route 8 response: deterministic IMF WP/15/216 market & sector stress simulation."""

    snapshot_id: str | None
    scenario_name: str
    statement_scope: StatementScope
    period_type: PeriodType
    period_end_date: date | None = None
    total_issuers_evaluated: int
    total_debt_sum: float
    baseline_debt_at_risk_pct: float
    stressed_debt_at_risk_pct: float
    newly_distressed_count: int
    sector_summary: list[StressSectorSummaryItem]
    newly_distressed_issuers: list[StressIssuerItem]
    top_stressed_issuers: list[StressIssuerItem]


class QualitySummaryItem(BaseModel):
    """Single row from ``quality_summary.parquet``."""

    issuer_id: str
    statement_scope: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None = None
    period_end_date: date
    taxonomy_version: str
    quality_status: str
    warning_codes: list[str]
    info_codes: list[str]
    filing_ids: list[str]
    checked_at: str


class QualitySummaryPageResponse(BaseModel):
    """Route 9 response: paginated ``quality_summary`` table."""

    snapshot_id: str | None
    total: int
    limit: int
    offset: int
    items: list[QualitySummaryItem]


class AuditQuarantineItem(BaseModel):
    """Single row from ``audit_quarantine.parquet``."""

    issuer_id: str
    filing_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    taxonomy_version: str
    rule_code: str
    severity: str
    details_json: str
    quarantined_at: str


class AuditQuarantinePageResponse(BaseModel):
    """Route 10 response: paginated ``audit_quarantine`` table."""

    snapshot_id: str | None
    total: int
    limit: int
    offset: int
    items: list[AuditQuarantineItem]


class SectorOptionItem(BaseModel):
    """ICB Level 2 sector summary option for global filters."""

    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int


class MetaPeriodsResponse(BaseModel):
    """Available snapshot periods per scope/type and ICB Level 2 sector directory."""

    snapshot_id: str | None
    published_at: str | None
    available_periods: dict[str, dict[str, list[date]]]
    sectors_level2: list[SectorOptionItem]


class RatioDictionaryItem(BaseModel):
    """Dictionary definition for one published financial ratio."""

    ratio_name: RatioName
    display_name_vn: str
    category: RatioCategory
    unit: str
    format_type: RatioFormatType
    direction: RatioDirection
    formula_display: str
    description_vn: str
    is_overview_core: bool


class RedFlagDictionaryItem(BaseModel):
    """Dictionary definition for one forensic red-flag screening rule."""

    flag_code: str
    title_vn: str
    default_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    methodology_ref: str
    description_vn: str


class MetaDictionaryResponse(BaseModel):
    """Static ratio & forensic rule dictionary for tooltips and methodology docs."""

    ratios: list[RatioDictionaryItem]
    red_flags: list[RedFlagDictionaryItem]


class BenchmarkSummaryItem(BaseModel):
    """Single ratio peer benchmark + 8-period sparkline for Traffic Light tables."""

    metric_name: RatioName
    display_name_vn: str
    category: RatioCategory
    unit: str
    format_type: RatioFormatType
    direction: RatioDirection
    company_value: float | None = None
    peer_median: float | None = None
    peer_p25: float | None = None
    peer_p75: float | None = None
    percentile_rank: float | None = None
    robust_z_score: float | None = None
    peer_group_level: str | None = None
    icb_code_used: str | None = None
    peer_count: int = 0
    sparkline: list[float | None] = Field(default_factory=list)


class CompanyBenchmarksSummaryResponse(BaseModel):
    """Batch peer comparison across core or all ratios for one company period."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    statement_scope: StatementScope
    period_type: PeriodType
    period_end_date: date
    items: list[BenchmarkSummaryItem]


class StatementPeriodItem(BaseModel):
    """Column metadata for one reporting period in normalized financial statements."""

    period_end_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    label: str
    taxonomy_version: str | None = None
    audit_status: str | None = None


class StatementRowItem(BaseModel):
    """Single standardized VAS line item across multiple reporting periods."""

    metric_id: str
    display_name_vn: str
    tt200_code: str | None = None
    tt99_code: str | None = None
    unit: str
    is_total: bool
    comparable_across_taxonomy: bool
    sort_order: int
    values: dict[str, float | None]


class CompanyStatementsResponse(BaseModel):
    """Multi-period normalized VAS financial statement matrix for one issuer."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    statement_type: StatementType
    statement_scope: StatementScope
    period_type: PeriodType
    periods: list[StatementPeriodItem]
    rows: list[StatementRowItem]


class CompanyFilingItem(BaseModel):
    """Single disclosure/filing audit and restatement record for an issuer."""

    filing_id: str
    statement_type: str | None = None
    statement_scope: str | None = None
    period_type: str | None = None
    period_end_date: date | None = None
    taxonomy_version: str | None = None
    published_at: date | None = None
    published_at_basis: str | None = None
    effective_available_at: date | None = None
    audit_status: str | None = None
    audit_opinion: str | None = None
    revision_no: int | None = None
    revision_reason: str | None = None
    has_restatement: bool = False
    source_provider: str | None = None


class CompanyFilingsResponse(BaseModel):
    """Issuer disclosure, audit opinion, and revision history."""

    snapshot_id: str | None
    ticker: str
    issuer_id: str
    items: list[CompanyFilingItem]


class SectorRiskItem(BaseModel):
    """Aggregated market risk metrics for one ICB Level 2 sector."""

    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    median_distress_prob: float | None = None
    avg_red_flags: float
    total_debt_sum: float


class MarketChangeItem(BaseModel):
    """Quarter-over-quarter or year-over-year risk transition for one issuer."""

    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    icb_name_level2: str | None = None
    prev_period_end_date: date | None = None
    curr_period_end_date: date
    altman_zone_prev: str | None = None
    altman_zone_curr: str | None = None
    distress_prob_prev: float | None = None
    distress_prob_curr: float | None = None
    distress_prob_delta: float | None = None
    risk_tier_prev: RiskTier | str | None = None
    risk_tier_curr: RiskTier | str | None = None
    risk_market_percentile_curr: float | None = None
    market_base_rate_curr: float | None = None
    relative_risk_lift_curr: float | None = None
    red_flag_count_prev: int
    red_flag_count_curr: int
    new_red_flag_codes: list[str]
    resolved_red_flag_codes: list[str]
    deteriorated: bool


class SectorRiskSummaryResponse(BaseModel):
    """Market-wide sector risk heatmap and top deteriorating issuers."""

    snapshot_id: str | None
    statement_scope: StatementScope
    period_type: PeriodType
    period_end_date: date | None = None
    total_issuers: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    sectors: list[SectorRiskItem]
    top_deteriorating: list[MarketChangeItem]


class MarketChangesResponse(BaseModel):
    """Period-over-period risk changes filtered by watchlist tickers or market-wide."""

    snapshot_id: str | None
    statement_scope: StatementScope
    period_type: PeriodType
    items: list[MarketChangeItem]
