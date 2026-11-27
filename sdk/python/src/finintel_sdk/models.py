"""Pydantic v2 response and request models for ``finintel-sdk`` (V3-26)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanyRiskReport(BaseModel):
    """Derived quantitative risk report for a single corporate issuer."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: str | None = None
    license_scope: str = "read:derived_risk"
    ticker: str
    company_name_vn: str
    accounting_regime: str
    statement_scope: str
    period_view: str
    latest_taxonomy_version: str | None = None
    latest_quality_status: str | None = None
    ml_scores: list[dict[str, Any]] = Field(default_factory=list)
    red_flags: list[dict[str, Any]] = Field(default_factory=list)
    ratios: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer_vi: str = ""


class ScreenerPage(BaseModel):
    """Paginated screener response containing derived risk indicators."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: str | None = None
    license_scope: str = "read:derived_risk"
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    total: int
    limit: int
    offset: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer_vi: str = ""


class StressScenarioInput(BaseModel):
    """Input scenario for IMF WP/15/216 balance-sheet stress test."""

    model_config = ConfigDict(extra="forbid")

    scenario_name: str = "Kịch bản sốc lãi suất +200bps"
    statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED"
    period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER"
    period_end_date: date | None = None
    tickers: list[str] | None = None
    interest_rate_shock_bps: float = Field(default=0.0, ge=-500.0, le=2000.0)
    revenue_shock_pct: float = Field(default=0.0, ge=-0.8, le=0.5)
    gross_margin_shock_pp: float = Field(default=0.0, ge=-0.5, le=0.5)
    dso_shock_days: int = Field(default=0, ge=-90, le=180)
    dio_shock_days: int = Field(default=0, ge=-90, le=180)


class StressTestResult(BaseModel):
    """Aggregated and per-issuer stress-test output."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: str | None = None
    license_scope: str = "read:derived_risk"
    scenario_name: str
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    issuers_stressed: int
    newly_distressed_count: int
    sectors: list[dict[str, Any]] = Field(default_factory=list)
    issuers: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer_vi: str = ""


class RawStatementsResponse(BaseModel):
    """Raw normalized VAS financial statements (requires redistribution license)."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: str | None = None
    license_scope: str = "read:raw_statements"
    ticker: str
    issuer_id: str
    statement_type: str
    statement_scope: str
    period_type: str
    periods: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "CompanyRiskReport",
    "RawStatementsResponse",
    "ScreenerPage",
    "StressScenarioInput",
    "StressTestResult",
]
