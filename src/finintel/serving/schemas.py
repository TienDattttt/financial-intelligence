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

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from finintel.serving._schema_extensions import (
    BootstrapCI95Item as BootstrapCI95Item,
)
from finintel.serving._schema_extensions import (
    FeatureDriftItem as FeatureDriftItem,
)
from finintel.serving._schema_extensions import (
    HistoricalCaseStudyItem as HistoricalCaseStudyItem,
)
from finintel.serving._schema_extensions import (
    ModelCardResponse as ModelCardResponse,
)
from finintel.serving.routers.screener_v3 import (
    RatioNameV3,
    ScreenerSortFieldV3,
    ScreenerV3ItemFields,
)
from finintel.serving._schema_extensions import (
    ModelMetricsItem as ModelMetricsItem,
)
from finintel.serving._schema_extensions import (
    RiskTierCutoffsItem as RiskTierCutoffsItem,
)
from finintel.serving._schema_extensions import (
    RiskTierReliabilityReportItem as RiskTierReliabilityReportItem,
)
from finintel.serving._schema_extensions import (
    RiskTierReliabilityRowItem as RiskTierReliabilityRowItem,
)
from finintel.serving._schema_extensions import (
    ShapDriverItem as ShapDriverItem,
)
from finintel.serving._schema_extensions import (
    ShapExplanationItem as ShapExplanationItem,
)
from finintel.serving._schema_extensions import (
    parse_shap_top_drivers as parse_shap_top_drivers,
)
from finintel.serving._schema_extensions import (
    TaxonomyDriftPSIItem as TaxonomyDriftPSIItem,
)
from finintel.serving._schema_extensions import (
    TaxonomyScoreDriftItem as TaxonomyScoreDriftItem,
)
from finintel.serving._schema_extensions import (
    TT99MrmGateItem as TT99MrmGateItem,
)
from finintel.serving._schema_extensions import (
    WalkForwardModelSummaryItem as WalkForwardModelSummaryItem,
)
from finintel.serving._schema_extensions import (
    WalkForwardReportItem as WalkForwardReportItem,
)
from finintel.serving._schema_extensions import (
    WalkForwardYearItem as WalkForwardYearItem,
)
from finintel.serving._schema_extensions import normalize_reference_icrs_map
from finintel.serving.dictionary import (
    RatioCategory,
    RatioDirection,
    RatioFormatType,
    StatementType,
)
from finintel.serving.freshness import DataFreshnessItem
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

ScreenerSortField = ScreenerSortFieldV3
SortDirection = Literal["asc", "desc"]
_TICKER_ITEM_RE = re.compile(r"^[A-Z0-9]{1,10}$")

RatioName = RatioNameV3


class HealthResponse(BaseModel):
    """Route 1 response: service readiness and active snapshot binding."""

    status: Literal["ok", "degraded"]
    active_snapshot_id: str | None
    storage_backend: str
    rag_available: bool = False


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
    piotroski_missing_criteria: list[str] = Field(default_factory=list)
    piotroski_na_reason: str | None = None
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


class ScreenerItem(ScreenerV3ItemFields):
    """Single issuer row matching ``DashboardQueryService.screen_market()`` (with V3 ratios & valuation)."""

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
    include_all_newly_distressed: bool | None = None
    tickers: list[str] | None = Field(default=None, max_length=200)
    reference_icrs: dict[str, float | None] | None = None

    @field_validator("scenario_name")
    @classmethod
    def _non_blank_scenario_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("scenario_name must not be blank.")
        return stripped

    @field_validator("tickers")
    @classmethod
    def _normalize_tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("tickers must contain ticker strings.")
            tok = item.strip().upper()
            if not tok or not _TICKER_ITEM_RE.fullmatch(tok):
                raise ValueError(f"Invalid ticker format in tickers list: {item!r}")
            if tok not in seen:
                seen.add(tok)
                normalized.append(tok)
        return normalized

    @field_validator("reference_icrs", mode="before")
    @classmethod
    def _normalize_reference_icrs(cls, value: object) -> dict[str, float | None] | None:
        return normalize_reference_icrs_map(value)


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
    scenario_a_icr: float | None = None
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
    total_newly_distressed: int | None = None
    issuer_icrs: dict[str, float | None] = Field(default_factory=dict)
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
    freshness: DataFreshnessItem | None = None


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
    period_end_date: date | None = None
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


DrawdownHorizonKey = Literal["T+30d", "T+90d", "T+180d", "T+365d"]


class DrawdownWindowMetricsItem(BaseModel):
    """Peak/trough prices and percentage drawdowns over a post-warning window (UI-09b)."""

    start_trade_date: date
    end_trade_date: date
    target_end_date: date
    session_count: int
    base_close_adj: float
    peak_close_adj: float
    peak_date: date
    trough_close_adj: float
    trough_date: date
    end_close_adj: float
    return_pct: float
    drawdown_from_marker_pct: float
    max_peak_to_trough_drawdown_pct: float


class MarkerDrawdownBundleItem(BaseModel):
    """Post-warning drawdown metrics across standard horizons and cutoff bounds."""

    horizons: dict[DrawdownHorizonKey, DrawdownWindowMetricsItem | None]
    to_first_regulatory_event: DrawdownWindowMetricsItem | None = None
    to_current: DrawdownWindowMetricsItem | None = None


class ModelRiskMarkerItem(BaseModel):
    """PIT marker for first model upgrade to R4 or R5."""

    marker_type: Literal["MODEL_RISK_TIER"]
    marker_kind: Literal["FIRST_R4_UPGRADE", "FIRST_R5_UPGRADE"]
    risk_tier: Literal["R4", "R5"]
    is_first_r4_or_r5: bool
    disclosure_date: date
    date_basis: Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"]
    aligned_trade_date: date | None = None
    period_end_date: date
    close_adj_at_marker: float | None = None
    close_raw_at_marker: float | None = None
    calibrated_distress_prob: float | None = None
    risk_market_percentile: float | None = None
    relative_risk_lift: float | None = None
    drawdowns: MarkerDrawdownBundleItem | None = None


class RegulatoryEventMarkerItem(BaseModel):
    """PIT marker for a real regulatory distress or audit sanction event."""

    marker_type: Literal["REGULATORY_EVENT"]
    event_type: str
    event_stage: str
    event_date: date
    effective_from: date
    resolution_date: date | None = None
    aligned_trade_date: date | None = None
    close_adj_at_marker: float | None = None
    close_raw_at_marker: float | None = None
    raw_headline: str
    source_provider: str
    source_url: str
    drawdowns: MarkerDrawdownBundleItem | None = None


class PriceOverlayPointItem(BaseModel):
    """Single daily point on the continuous Price & Risk Event Overlay series."""

    trade_date: date
    close_adj: float
    close_raw: float
    volume: float
    shares_outstanding_market: float | None = None
    running_drawdown_pct: float
    drawdown_from_first_warning_pct: float | None = None
    market_cap_vnd: float | None = None
    pe_ttm: float | None = None
    pb_ratio: float | None = None
    pe_flag: str | None = None
    pb_flag: str | None = None
    shares_source: str | None = None
    merton_dtd: float | None = None
    equity_volatility_1y: float | None = None
    naive_edf: float | None = None
    dtd_flag: str | None = None


class WarningSummaryItem(BaseModel):
    """Summary anchored at the earliest warning marker (model R4/R5 or regulatory)."""

    warning_source: Literal["MODEL_RISK_TIER", "REGULATORY_EVENT"]
    warning_label: str
    warning_date: date
    aligned_trade_date: date | None = None
    base_close_adj: float | None = None
    latest_trade_date: date | None = None
    latest_close_adj: float | None = None
    horizons: dict[DrawdownHorizonKey, DrawdownWindowMetricsItem | None]
    to_first_regulatory_event: DrawdownWindowMetricsItem | None = None
    to_current: DrawdownWindowMetricsItem | None = None


class CompanyMarketOverlayResponse(BaseModel):
    """V2 Company route response: PIT valuation & Price/Risk Event Overlay (UI-09b)."""

    snapshot_id: str | None = None
    issuer_id: str
    ticker: str
    as_of: date | None = None
    latest_valuation: PriceOverlayPointItem | None = None
    price_series: list[PriceOverlayPointItem]
    model_markers: list[ModelRiskMarkerItem]
    regulatory_markers: list[RegulatoryEventMarkerItem]
    earliest_warning_summary: WarningSummaryItem | None = None
