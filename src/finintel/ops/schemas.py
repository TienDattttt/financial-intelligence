"""Pydantic v2 DTOs for V3 analyst depth and INTERNAL_OPS QA/lifecycle endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SuggestedPeerItem(BaseModel):
    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    exchange: str | None = None
    icb_code_level2: str | None = None
    icb_name_level2: str | None = None
    icb_code_level3: str | None = None
    icb_name_level3: str | None = None
    peer_basis: str
    size_metric: str
    size_ratio: float | None = None
    market_cap_vnd: float | None = None
    bs_total_assets: float | None = None
    risk_tier: str | None = None
    calibrated_distress_prob: float | None = None


class SuggestedPeersResponse(BaseModel):
    snapshot_id: str | None = None
    ticker: str
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    peer_basis: str
    peers: list[SuggestedPeerItem]


class AnomalyDriverItem(BaseModel):
    metric_name: str
    display_name_vn: str
    company_value: float
    peer_median: float | None = None
    sector_z: float
    abs_sector_z: float
    direction_vs_sector: Literal["ABOVE_MEDIAN", "BELOW_MEDIAN", "AT_MEDIAN"]
    peer_group_level: str | None = None
    peer_count: int = 0


class AnomalyDriversResponse(BaseModel):
    snapshot_id: str | None = None
    ticker: str
    statement_scope: str
    period_type: str
    period_end_date: date | None = None
    anomaly_percentile_rank: float | None = None
    statistical_outlier_flag: bool = False
    is_multivariate_outlier: bool = False
    drivers: list[AnomalyDriverItem]


class OpsRevisionItem(BaseModel):
    submission_id: UUID
    ticket_id: UUID
    revision_no: int
    source_url: str | None = None
    raw_file_sha256: str | None = None
    reason: str
    reingest_status: str
    dagster_run_id: str | None = None
    submitted_by_user_id: UUID | None = None
    submitted_at: datetime


class OpsQuarantineTicketItem(BaseModel):
    ticket_id: UUID
    issuer_id: str
    ticker: str
    filing_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    taxonomy_version: str
    rule_code: str
    severity: str
    details_json: str
    status: Literal["OPEN", "IN_REVIEW", "RESOLVED"]
    decision: Literal["APPROVE_REINGEST", "KEEP_QUARANTINED"] | None = None
    assigned_to_user_id: UUID | None = None
    root_cause_note: str = ""
    latest_revision_no: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    revisions: list[OpsRevisionItem] = Field(default_factory=list)


class OpsQaQueueResponse(BaseModel):
    snapshot_id: str | None = None
    total: int
    items: list[OpsQuarantineTicketItem]


class OpsAssignTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to_user_id: UUID | None = None
    root_cause_note: str = Field("", max_length=1000)


class OpsSubmitRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str | None = Field(default=None, max_length=1000)
    raw_file_sha256: str | None = Field(default=None, max_length=64)
    reason: str = Field(..., min_length=5, max_length=1000)
    payload_summary: dict[str, object] = Field(default_factory=dict)


class OpsDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE_REINGEST", "KEEP_QUARANTINED"]
    note: str = Field("", max_length=1000)


class OpsTicketMutationResponse(BaseModel):
    status: str
    ticket: OpsQuarantineTicketItem


class IssuerLifecycleEventItem(BaseModel):
    event_id: UUID
    issuer_id: str
    ticker: str
    exchange: Literal["HOSE", "HNX", "UPCOM", "DELISTED"]
    event_type: Literal[
        "IPO_LISTING",
        "EXCHANGE_TRANSFER",
        "DELISTING",
        "FY_END_CHANGE",
        "METADATA_SYNC",
    ]
    effective_date: date
    fy_end_month: int
    previous_exchange: str | None = None
    source_url: str = ""
    notes: str = ""
    status: str
    created_at: datetime


class IssuerLifecycleItem(BaseModel):
    issuer_id: str
    ticker: str
    company_name_vn: str | None = None
    exchange: str
    accounting_regime: str
    icb_name_level2: str | None = None
    icb_name_level3: str | None = None
    delisted_date: date | None = None
    fy_end_month: int = 12
    recent_events: list[IssuerLifecycleEventItem] = Field(default_factory=list)


class OpsIssuerLifecycleResponse(BaseModel):
    snapshot_id: str | None = None
    total: int
    items: list[IssuerLifecycleItem]


class OpsIssuerSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(..., min_length=1, max_length=10)
    issuer_id: str | None = Field(default=None, max_length=64)
    exchange: Literal["HOSE", "HNX", "UPCOM", "DELISTED"]
    event_type: Literal[
        "IPO_LISTING",
        "EXCHANGE_TRANSFER",
        "DELISTING",
        "FY_END_CHANGE",
        "METADATA_SYNC",
    ]
    effective_date: date
    fy_end_month: int = Field(12, ge=1, le=12)
    previous_exchange: str | None = Field(default=None, max_length=16)
    source_url: str = Field("", max_length=1000)
    notes: str = Field("", max_length=1000)


class OpsIssuerSyncResponse(BaseModel):
    status: str
    event: IssuerLifecycleEventItem
