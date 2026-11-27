"""Lazy-imported Pydantic HTTP response schemas for ``/api/v3/quota/*``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from finintel.auth.dependencies import UserRole
from finintel.quota.policies import QuotaMeter


def build_quota_schemas() -> tuple[type[Any], type[Any], type[Any]]:
    """Build Pydantic schemas lazily so core CI without ``pydantic`` stays green."""
    from pydantic import BaseModel

    class QuotaMeterStatus(BaseModel):
        meter: QuotaMeter
        used: int
        limit: int | None = None
        remaining: int | None = None
        unlimited: bool = False
        reset_at: datetime

    class MyQuotaResponse(BaseModel):
        authenticated: bool
        role: UserRole | Literal["ANONYMOUS"]
        quota_date: date
        timezone: Literal["Asia/Ho_Chi_Minh"]
        reset_at: datetime
        cross_border_rag_consent: bool
        circuit_breaker_open: bool
        circuit_breaker_reopen_at: datetime | None = None
        meters: list[QuotaMeterStatus]

    class SystemBudgetResponse(BaseModel):
        budget_date: date
        timezone: Literal["Asia/Ho_Chi_Minh"]
        reset_at: datetime
        gemini_requests_used: int
        gemini_requests_cap: int
        gemini_utilization_ratio: float
        circuit_breaker_ratio: float
        circuit_breaker_open: bool
        vision_pages_used: int
        vision_pages_cap: int
        vision_pages_remaining: int

    return QuotaMeterStatus, MyQuotaResponse, SystemBudgetResponse


__all__ = ["build_quota_schemas"]
