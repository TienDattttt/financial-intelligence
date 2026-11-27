"""Non-commercial Fair-Use Quota policies and free-tier budget settings (V3-19)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from finintel.auth.dependencies import UserRole

QuotaMeter = Literal["rag_query", "xlsx_export", "api_call"]
ALL_QUOTA_METERS: tuple[QuotaMeter, ...] = ("rag_query", "xlsx_export", "api_call")

VIETNAM_TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
VIETNAM_TZ = ZoneInfo(VIETNAM_TIMEZONE_NAME)

DEFAULT_GLOBAL_DAILY_GEMINI_CAP = 1500
DEFAULT_CIRCUIT_BREAKER_RATIO = 0.85
DEFAULT_DAILY_VISION_PAGE_CAP = 150
DEFAULT_GUEST_RAG_DAILY_LIMIT = 15


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, parsed)


def _env_float(
    name: str, default: float, *, minimum: float = 0.05, maximum: float = 1.0
) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, parsed))


@dataclass(frozen=True, slots=True)
class RoleQuotaPolicy:
    """Daily non-commercial fair-use limits for a given RBAC role."""

    role: UserRole
    rag_queries_per_day: int
    xlsx_exports_per_day: int
    api_calls_per_day: int
    max_watchlists: int
    max_tickers_per_watchlist: int
    unlimited: bool = False

    def limit_for(self, meter: QuotaMeter) -> int | None:
        """Return daily numeric cap for ``meter``, or ``None`` when unlimited."""
        if self.unlimited:
            return None
        if meter == "rag_query":
            return self.rag_queries_per_day
        if meter == "xlsx_export":
            return self.xlsx_exports_per_day
        return self.api_calls_per_day


@dataclass(frozen=True, slots=True)
class QuotaBudgetConfig:
    """Global free-tier safety caps protecting Gemini AI Studio and Vision OCR budgets."""

    global_daily_gemini_cap: int = DEFAULT_GLOBAL_DAILY_GEMINI_CAP
    circuit_breaker_ratio: float = DEFAULT_CIRCUIT_BREAKER_RATIO
    daily_vision_page_cap: int = DEFAULT_DAILY_VISION_PAGE_CAP
    guest_rag_daily_limit: int = DEFAULT_GUEST_RAG_DAILY_LIMIT

    @classmethod
    def from_env(cls) -> QuotaBudgetConfig:
        """Build budget config from environment variables with safe free-tier defaults."""
        return cls(
            global_daily_gemini_cap=_env_int(
                "FININTEL_GLOBAL_DAILY_GEMINI_CAP",
                DEFAULT_GLOBAL_DAILY_GEMINI_CAP,
                minimum=1,
            ),
            circuit_breaker_ratio=_env_float(
                "FININTEL_GEMINI_CIRCUIT_BREAKER_RATIO",
                DEFAULT_CIRCUIT_BREAKER_RATIO,
                minimum=0.1,
                maximum=1.0,
            ),
            daily_vision_page_cap=_env_int(
                "FININTEL_DAILY_VISION_PAGE_CAP",
                DEFAULT_DAILY_VISION_PAGE_CAP,
                minimum=1,
            ),
            guest_rag_daily_limit=_env_int(
                "FININTEL_GUEST_RAG_DAILY_LIMIT",
                DEFAULT_GUEST_RAG_DAILY_LIMIT,
                minimum=1,
            ),
        )

    def circuit_breaker_trip_count(self) -> int:
        """Return integer request count at which the global circuit breaker opens."""
        # Use exact rounded threshold so e.g. 100 * 0.85 == 85, 1500 * 0.85 == 1275.
        raw = int(round(self.global_daily_gemini_cap * self.circuit_breaker_ratio))
        return max(1, min(self.global_daily_gemini_cap, raw))


DEFAULT_ROLE_POLICIES: dict[UserRole, RoleQuotaPolicy] = {
    UserRole.USER: RoleQuotaPolicy(
        role=UserRole.USER,
        rag_queries_per_day=15,
        xlsx_exports_per_day=10,
        api_calls_per_day=100,
        max_watchlists=5,
        max_tickers_per_watchlist=50,
        unlimited=False,
    ),
    UserRole.RESEARCHER: RoleQuotaPolicy(
        role=UserRole.RESEARCHER,
        rag_queries_per_day=50,
        xlsx_exports_per_day=30,
        api_calls_per_day=1000,
        max_watchlists=20,
        max_tickers_per_watchlist=100,
        unlimited=False,
    ),
    UserRole.INTERNAL_OPS: RoleQuotaPolicy(
        role=UserRole.INTERNAL_OPS,
        rag_queries_per_day=1000,
        xlsx_exports_per_day=500,
        api_calls_per_day=10_000,
        max_watchlists=50,
        max_tickers_per_watchlist=200,
        unlimited=True,
    ),
}


def get_role_quota_policy(role: UserRole) -> RoleQuotaPolicy:
    """Resolve daily fair-use quota policy for ``role`` (with env overrides for INTERNAL_OPS)."""
    if role == UserRole.INTERNAL_OPS:
        unlimited_raw = os.getenv("FININTEL_OPS_QUOTA_UNLIMITED", "1").strip().lower()
        unlimited = unlimited_raw not in {"0", "false", "no", "off"}
        return RoleQuotaPolicy(
            role=UserRole.INTERNAL_OPS,
            rag_queries_per_day=_env_int("FININTEL_OPS_RAG_DAILY_LIMIT", 1000),
            xlsx_exports_per_day=_env_int("FININTEL_OPS_XLSX_DAILY_LIMIT", 500),
            api_calls_per_day=_env_int("FININTEL_OPS_API_DAILY_LIMIT", 10_000),
            max_watchlists=50,
            max_tickers_per_watchlist=200,
            unlimited=unlimited,
        )
    return DEFAULT_ROLE_POLICIES[role]


def current_quota_window(now: datetime | None = None) -> tuple[date, datetime]:
    """Return ``(local_quota_date, next_midnight_reset_at)`` in ``Asia/Ho_Chi_Minh``."""
    base = now if now is not None else datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    local_now = base.astimezone(VIETNAM_TZ)
    quota_date = local_now.date()
    next_midnight = datetime.combine(
        quota_date + timedelta(days=1),
        time.min,
        tzinfo=VIETNAM_TZ,
    )
    return quota_date, next_midnight


def seconds_until_reset(reset_at: datetime, now: datetime | None = None) -> int:
    """Return positive integer seconds remaining until ``reset_at``."""
    base = now if now is not None else datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    delta = int((reset_at - base).total_seconds())
    return max(1, delta)


__all__ = [
    "ALL_QUOTA_METERS",
    "DEFAULT_CIRCUIT_BREAKER_RATIO",
    "DEFAULT_DAILY_VISION_PAGE_CAP",
    "DEFAULT_GLOBAL_DAILY_GEMINI_CAP",
    "DEFAULT_GUEST_RAG_DAILY_LIMIT",
    "DEFAULT_ROLE_POLICIES",
    "QuotaBudgetConfig",
    "QuotaMeter",
    "RoleQuotaPolicy",
    "VIETNAM_TIMEZONE_NAME",
    "VIETNAM_TZ",
    "current_quota_window",
    "get_role_quota_policy",
    "seconds_until_reset",
]
