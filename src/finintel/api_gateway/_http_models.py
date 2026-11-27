"""Pydantic v2 HTTP request/response models for ``/api/v3/b2b/*`` and ``/api/v3/open/*``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ApiScope = Literal[
    "read:derived_risk",
    "write:webhooks",
    "read:rag",
    "read:raw_statements",
]
KeyPrefixMode = Literal["res", "live"]


def _default_scopes() -> list[ApiScope]:
    return ["read:derived_risk", "write:webhooks"]


def _default_event_types() -> list[str]:
    return [
        "NEW_FILING_PUBLISHED",
        "RISK_TIER_UPGRADE",
        "NEW_RED_FLAG_TRIGGERED",
        "REGULATORY_EVENT_ISSUED",
        "MERTON_DTD_BREACH",
    ]


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    prefix_mode: KeyPrefixMode = "res"
    scopes: list[ApiScope] = Field(default_factory=_default_scopes)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyMetadataItem(BaseModel):
    key_id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_per_min: int
    daily_limit: int
    is_active: bool
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class ApiKeyCreateResponse(BaseModel):
    key: ApiKeyMetadataItem
    plaintext_key: str


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyMetadataItem]
    raw_redistribution_enabled: bool
    default_rate_limit_per_min: int
    default_daily_limit: int


class ApiKeyRevokeResponse(BaseModel):
    status: Literal["REVOKED"]
    key_id: UUID
    revoked_at: datetime


class ApiUsageDailyPoint(BaseModel):
    usage_date: date
    key_id: UUID
    key_prefix: str
    endpoint: str
    status_class: str
    request_count: int
    cache_hits: int


class ApiUsageSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_requests: int
    total_cache_hits: int
    daily: list[ApiUsageDailyPoint]


class B2BWebhookCreateRequest(BaseModel):
    label: str = Field(default="", max_length=80)
    url: str = Field(..., min_length=8, max_length=512)
    secret_hmac: str | None = Field(default=None, min_length=16, max_length=256)
    event_types: list[str] = Field(default_factory=_default_event_types)


class B2BWebhookItem(BaseModel):
    endpoint_id: UUID
    label: str
    url: str
    event_types: list[str]
    secret_configured: bool
    is_active: bool
    last_status: int | None = None
    last_error: str | None = None
    last_delivered_at: datetime | None = None
    created_at: datetime


class B2BWebhookCreateResponse(BaseModel):
    webhook: B2BWebhookItem
    secret_hmac: str


class B2BWebhookListResponse(BaseModel):
    items: list[B2BWebhookItem]


class B2BWebhookDeleteResponse(BaseModel):
    status: Literal["DELETED"]
    endpoint_id: UUID


class B2BWebhookTestResponse(BaseModel):
    delivered: bool
    http_status: int | None = None
    signature_header_prefix: str | None = None
    detail: str


class OpenDerivedRiskResponse(BaseModel):
    snapshot_id: str | None = None
    license_scope: str
    ticker: str
    company_name_vn: str
    accounting_regime: str
    statement_scope: str
    period_view: str
    latest_taxonomy_version: str | None = None
    latest_quality_status: str | None = None
    ml_scores: list[dict[str, Any]]
    red_flags: list[dict[str, Any]]
    ratios: list[dict[str, Any]]
    disclaimer_vi: str


class OpenScreenerResponse(BaseModel):
    snapshot_id: str | None = None
    license_scope: str
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    total: int
    limit: int
    offset: int
    items: list[dict[str, Any]]
    disclaimer_vi: str


class OpenStressTestRequest(BaseModel):
    scenario_name: str = Field(default="Kịch bản sốc lãi suất +200bps", max_length=120)
    statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED"
    period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER"
    period_end_date: date | None = None
    tickers: list[str] | None = Field(default=None, max_length=100)
    interest_rate_shock_bps: float = Field(default=0.0, ge=-500.0, le=2000.0)
    revenue_shock_pct: float = Field(default=0.0, ge=-0.8, le=0.5)
    gross_margin_shock_pp: float = Field(default=0.0, ge=-0.5, le=0.5)
    dso_shock_days: int = Field(default=0, ge=-90, le=180)
    dio_shock_days: int = Field(default=0, ge=-90, le=180)


class OpenStressTestResponse(BaseModel):
    snapshot_id: str | None = None
    license_scope: str
    scenario_name: str
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    issuers_stressed: int
    newly_distressed_count: int
    sectors: list[dict[str, Any]]
    issuers: list[dict[str, Any]]
    disclaimer_vi: str


class OpenRawStatementsResponse(BaseModel):
    snapshot_id: str | None = None
    license_scope: str
    ticker: str
    issuer_id: str
    statement_type: str
    statement_scope: str
    period_type: str
    periods: list[dict[str, Any]]
    rows: list[dict[str, Any]]


__all__ = [
    "ApiKeyCreateRequest",
    "ApiKeyCreateResponse",
    "ApiKeyListResponse",
    "ApiKeyMetadataItem",
    "ApiKeyRevokeResponse",
    "ApiScope",
    "ApiUsageDailyPoint",
    "ApiUsageSummaryResponse",
    "B2BWebhookCreateRequest",
    "B2BWebhookCreateResponse",
    "B2BWebhookDeleteResponse",
    "B2BWebhookItem",
    "B2BWebhookListResponse",
    "B2BWebhookTestResponse",
    "KeyPrefixMode",
    "OpenDerivedRiskResponse",
    "OpenRawStatementsResponse",
    "OpenScreenerResponse",
    "OpenStressTestRequest",
    "OpenStressTestResponse",
]
