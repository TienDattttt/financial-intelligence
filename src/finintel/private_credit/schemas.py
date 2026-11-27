"""Pydantic API contracts for isolated private-issuer VAS parsing and credit scoring (V3-28, V3-29)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

StatementScopeLiteral = Literal["CONSOLIDATED", "SEPARATE"]
TaxonomyLiteral = Literal["VAS_TT200_2014", "VAS_TT99_2025"]
Qg1StatusLiteral = Literal["PASS", "WARNING", "FAIL"]


class Qg1DiscrepancyItem(BaseModel):
    """Single Layer 6 QG1 accounting discrepancy detected in uploaded workbook."""

    period_end_date: date
    rule_code: str
    severity: str
    message_vi: str
    expected_value_vnd: float | None = None
    actual_value_vnd: float | None = None
    abs_residual_vnd: float | None = None
    missing_metrics: list[str] = Field(default_factory=list)


class Qg1ReportResponse(BaseModel):
    """Layer 6 QG1 verification summary for a private issuer upload."""

    status: Qg1StatusLiteral
    discrepancies: list[Qg1DiscrepancyItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    info_signals: list[str] = Field(default_factory=list)
    can_score: bool


class PrivateParsedLineItem(BaseModel):
    """Single normalized VAS statement line across uploaded periods."""

    statement_type: Literal["BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW"]
    vas_code: str
    metric_id: str
    line_label_vn: str
    values_by_period: dict[str, float | None] = Field(default_factory=dict)


class PrivateDossierPreviewResponse(BaseModel):
    """Response returned by ``POST /api/v3/private-credit/parse`` and ``GET /dossiers/{id}``."""

    dossier_id: UUID
    company_name: str
    tax_code: str | None = None
    icb_code_level2: str | None = None
    statement_scope: StatementScopeLiteral
    taxonomy_version: TaxonomyLiteral
    unit_multiplier: int
    source_filename: str
    source_sha256: str
    periods: list[date]
    qg1: Qg1ReportResponse
    lines: list[PrivateParsedLineItem]
    retention_days: int = 30
    retention_expires_at: datetime
    processing_mode: Literal["LOCAL_SERVER_ONLY"] = "LOCAL_SERVER_ONLY"
    created_at: datetime


class PrivateDossierSummaryItem(BaseModel):
    """Compact summary item when listing a user's own private dossiers."""

    dossier_id: UUID
    company_name: str
    tax_code: str | None = None
    taxonomy_version: TaxonomyLiteral
    qg1_status: Qg1StatusLiteral
    periods: list[date]
    has_score: bool
    retention_expires_at: datetime
    created_at: datetime


class PrivateDossierListResponse(BaseModel):
    """List of private credit dossiers owned by the authenticated user."""

    items: list[PrivateDossierSummaryItem]


class PrivateScoreRequest(BaseModel):
    """Optional stress-test shock parameters when scoring a private issuer dossier."""

    scenario_name: str = Field(
        default="Kịch bản bất lợi chuẩn IMF", min_length=1, max_length=120
    )
    interest_rate_shock_bps: float = Field(default=200.0, ge=-500.0, le=1500.0)
    revenue_shock_pct: float = Field(default=-0.15, ge=-0.80, le=0.50)
    gross_margin_shock_pp: float = Field(default=-0.03, ge=-0.50, le=0.20)
    dso_shock_days: float = Field(default=15.0, ge=-90.0, le=180.0)
    dio_shock_days: float = Field(default=15.0, ge=-90.0, le=180.0)


class PrivateClassicalScores(BaseModel):
    """Classical corporate accounting distress & forensic scores."""

    altman_z_double_prime: float | None = None
    altman_em_score: float | None = None
    altman_zone: str | None = None
    piotroski_f_score: int | None = None
    piotroski_valid_components_count: int | None = None
    beneish_m8_score: float | None = None
    beneish_flag: str | None = None


class PrivateDupontBreakdown(BaseModel):
    """5-factor and 6-factor DuPont decomposition for the latest period."""

    tax_burden: float | None = None
    interest_burden: float | None = None
    ebit_margin: float | None = None
    asset_turnover: float | None = None
    equity_multiplier_total: float | None = None
    parent_profit_share: float | None = None
    equity_multiplier_parent: float | None = None
    roe_total: float | None = None
    roe_parent: float | None = None
    dupont_valid_flag: bool = False


class PrivateRedFlagItem(BaseModel):
    """Triggered Layer 4A accounting red flag on the private dossier."""

    period_end_date: date
    flag_code: str
    severity: str
    flag_title_vn: str
    explanation_text_vn: str
    evidence_json: str


class PrivateStressResult(BaseModel):
    """Deterministic IMF WP/15/216 stress test result for the private issuer."""

    scenario_name: str
    baseline_ebit_vnd: float | None = None
    stressed_ebit_vnd: float | None = None
    baseline_interest_coverage: float | None = None
    stressed_interest_coverage: float | None = None
    stressed_interest_coverage_flag: str | None = None
    baseline_cfo_vnd: float | None = None
    stressed_cfo_vnd: float | None = None
    baseline_debt_at_risk: bool | None = None
    stressed_debt_at_risk: bool | None = None


class PrivateMlAccountingOnlyResult(BaseModel):
    """Isolated ML PD estimate under explicit ACCOUNTING_ONLY_MODE."""

    ml_mode: Literal["ACCOUNTING_ONLY_MODE"] = "ACCOUNTING_ONLY_MODE"
    calibrated_distress_prob: float | None = None
    risk_tier: str | None = None
    model_version: str
    accounting_feature_coverage: float
    missing_market_tt96_features: list[str]
    coverage_warning_vi: str


class PrivateScoreResponse(BaseModel):
    """Complete isolated private-issuer quantitative risk report."""

    dossier_id: UUID
    company_name: str
    tax_code: str | None = None
    taxonomy_version: TaxonomyLiteral
    statement_scope: StatementScopeLiteral
    scored_period_end_date: date
    qg1_status: Qg1StatusLiteral
    ratios: dict[str, float | None]
    ratio_flags: dict[str, str | None]
    dupont: PrivateDupontBreakdown
    classical: PrivateClassicalScores
    red_flags: list[PrivateRedFlagItem]
    ml_assessment: PrivateMlAccountingOnlyResult
    stress_test: PrivateStressResult
    ducklake_contaminated: Literal[False] = False
    ml_labels_modified: Literal[False] = False
    elapsed_ms: float


class PrivateDossierDeleteResponse(BaseModel):
    """Deletion confirmation for a private credit dossier."""

    status: Literal["DELETED"] = "DELETED"
    dossier_id: UUID
