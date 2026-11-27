"""Pydantic API contracts for HITL red-flag & outlier triage (V3-29)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

HitlVerdict = Literal["CONFIRMED_RISK", "BENIGN_DOMAIN_EXCEPTION"]
HitlReasonCategory = Literal[
    "CONSTRUCTION_PROGRESS_BILLING",
    "HOLDING_ASSOCIATE_DIVIDENDS",
    "SEASONAL_WORKING_CAPITAL",
    "TRANSIENT_CAPEX_EXPANSION",
    "ACCOUNTING_RECLASSIFICATION",
    "CONFIRMED_FUNDAMENTAL_RISK",
    "OTHER",
]


class HitlFeedbackRequest(BaseModel):
    """Expert triage payload for a triggered red flag or multivariate outlier."""

    ticker: str = Field(
        ..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]{1,20}$"
    )
    icb_code_level2: str | None = Field(default=None, max_length=32)
    period_end_date: date
    signal_code: str = Field(
        ..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]{2,64}$"
    )
    verdict: HitlVerdict
    reason_category: HitlReasonCategory
    note: str = Field(..., min_length=3, max_length=2000)
    citation_page: int | None = Field(default=None, ge=1, le=5000)


class HitlFeedbackItem(BaseModel):
    """Recorded expert feedback item (SR 11-7 safe: never mutates ML ground truth)."""

    feedback_id: UUID
    user_id: UUID | None = None
    ticker: str
    icb_code_level2: str
    period_end_date: date
    signal_code: str
    verdict: HitlVerdict
    reason_category: HitlReasonCategory
    note: str
    citation_page: int | None = None
    created_at: datetime
    ml_training_labels_untouched: Literal[True] = True


class HitlSummaryCell(BaseModel):
    """False-positive aggregation for one ``signal_code x icb_code_level2`` pair."""

    signal_code: str
    icb_code_level2: str
    total_count: int
    confirmed_risk_count: int
    benign_exception_count: int
    false_positive_rate: float
    top_reason_categories: list[str]


class HitlSummaryResponse(BaseModel):
    """INTERNAL_OPS aggregated HITL triage report."""

    total_feedback: int
    overall_false_positive_rate: float | None = None
    items: list[HitlSummaryCell]
    mrm_governance_note: str
