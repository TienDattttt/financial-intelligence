"""Atomic Fair-Use Quota Guard, Global Gemini Circuit Breaker, and PII anonymizer (V3-19)."""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from finintel.auth.dependencies import AuthenticatedUser, UserRole
from finintel.domain.compliance import ConsentPurpose
from finintel.quota.policies import (
    ALL_QUOTA_METERS,
    VIETNAM_TIMEZONE_NAME,
    QuotaBudgetConfig,
    QuotaMeter,
    current_quota_window,
    get_role_quota_policy,
    seconds_until_reset,
)

_EMAIL_PII_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
)
_VN_PHONE_PII_RE = re.compile(
    r"(?<!\d)(?:\+84|84|0)(?:3|5|7|8|9)(?:[\s.]?\d){8}(?!\d)",
)
_VN_ID_DOC_RE = re.compile(
    r"\b(?:CCCD|CMND|hộ\s*chiếu|passport|MST\s*cá\s*nhân)\s*[:#]?\s*\d{9,12}\b",
    re.IGNORECASE,
)


def anonymize_rag_prompt_pii(
    text: str,
    *,
    user: AuthenticatedUser | None = None,
) -> str:
    """Strip personal identifiers (email, phone, national ID, user name) before LLM prompt."""
    cleaned = _EMAIL_PII_RE.sub("[EMAIL_REDACTED]", text)
    cleaned = _VN_PHONE_PII_RE.sub("[PHONE_REDACTED]", cleaned)
    cleaned = _VN_ID_DOC_RE.sub("[ID_REDACTED]", cleaned)
    if user is not None:
        if user.email:
            cleaned = re.sub(
                re.escape(user.email),
                "[USER_REDACTED]",
                cleaned,
                flags=re.IGNORECASE,
            )
        name = user.display_name.strip()
        if len(name) >= 4:
            cleaned = re.sub(
                re.escape(name),
                "[USER_REDACTED]",
                cleaned,
                flags=re.IGNORECASE,
            )
    return cleaned


def hash_actor_subject(subject: str) -> str:
    """Return a non-reversible SHA-256 hex digest for ledger attribution."""
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


class QuotaExceededError(Exception):
    """Raised when a user exceeds their daily fair-use quota (HTTP 429)."""

    def __init__(
        self,
        *,
        meter: QuotaMeter,
        limit: int,
        used: int,
        reset_at: datetime,
        detail: str | None = None,
    ) -> None:
        reset_label = reset_at.strftime("%H:%M %d/%m/%Y (UTC+7)")
        msg = detail or (
            f"Bạn đã sử dụng hết {used}/{limit} lượt miễn phí hôm nay cho tác vụ này. "
            f"Hạn mức sẽ được làm mới lúc {reset_label}."
        )
        super().__init__(msg)
        self.status_code = 429
        self.meter = meter
        self.limit = limit
        self.used = used
        self.reset_at = reset_at
        self.detail = msg
        self.headers: dict[str, str] = {
            "Retry-After": str(seconds_until_reset(reset_at)),
            "X-Quota-Reset": reset_at.isoformat(),
            "X-Quota-Limit": str(limit),
            "X-Quota-Remaining": "0",
        }


class CircuitBreakerOpenError(Exception):
    """Raised when global free-tier Gemini daily budget reaches threshold (HTTP 503)."""

    def __init__(
        self,
        *,
        used: int,
        cap: int,
        reopen_at: datetime,
        detail: str | None = None,
    ) -> None:
        reopen_label = reopen_at.strftime("%H:%M %d/%m/%Y (UTC+7)")
        msg = detail or (
            "Hệ thống tra cứu tài liệu đang tạm dừng nhận truy vấn mới để bảo vệ "
            f"ngân sách miễn phí trong ngày. Vui lòng thử lại sau {reopen_label}."
        )
        super().__init__(msg)
        self.status_code = 503
        self.used = used
        self.cap = cap
        self.reopen_at = reopen_at
        self.detail = msg
        self.headers: dict[str, str] = {
            "Retry-After": str(seconds_until_reset(reopen_at)),
            "X-Circuit-Reopen-At": reopen_at.isoformat(),
            "Cache-Control": "private, no-store",
        }


@dataclass(frozen=True, slots=True)
class QuotaReceipt:
    """Receipt returned after atomic quota reservation/consumption."""

    subject_key: str
    meter: QuotaMeter
    quota_date: date
    units: int
    used: int
    limit: int | None
    remaining: int | None
    unlimited: bool
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class MeterQuotaSnapshot:
    """Current daily usage snapshot for a single meter."""

    meter: QuotaMeter
    used: int
    limit: int | None
    remaining: int | None
    unlimited: bool
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class UserQuotaSnapshot:
    """Full daily quota overview for an authenticated or anonymous user."""

    authenticated: bool
    role: UserRole | str
    quota_date: date
    timezone: str
    reset_at: datetime
    cross_border_rag_consent: bool
    circuit_breaker_open: bool
    circuit_breaker_reopen_at: datetime | None
    meters: tuple[MeterQuotaSnapshot, ...]


@dataclass(frozen=True, slots=True)
class SystemBudgetSnapshot:
    """Global Gemini & Vision OCR free-tier daily budget state."""

    budget_date: date
    timezone: str
    reset_at: datetime
    gemini_requests_used: int
    gemini_requests_cap: int
    gemini_utilization_ratio: float
    circuit_breaker_ratio: float
    circuit_breaker_open: bool
    vision_pages_used: int
    vision_pages_cap: int
    vision_pages_remaining: int


@dataclass(frozen=True, slots=True)
class RagUsageLedgerEntry:
    """Audit/cost-tracking entry recorded in ``rag_usage_ledger`` ($0 free tier)."""

    usage_date: date
    actor_subject_hash: str
    operation: str
    ticker: str = ""
    filing_id: str = ""
    pdf_sha256: str = ""
    digital_pages: int = 0
    vision_pages: int = 0
    llm_calls: int = 0
    cache_hit: bool = False
    status: str = "COMPLETED"
    estimated_cost_usd: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class QuotaStoreProtocol(Protocol):
    """Storage backend protocol for atomic quota counters, circuit breaker, and ledger."""

    budget_config: QuotaBudgetConfig

    def check_and_consume_subject(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        limit: int | None,
        unlimited: bool,
        units: int,
        now: datetime | None = None,
    ) -> QuotaReceipt: ...

    def refund_subject(self, receipt: QuotaReceipt) -> None: ...

    def get_subject_used(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        now: datetime | None = None,
    ) -> int: ...

    def get_system_budget(
        self, *, now: datetime | None = None
    ) -> SystemBudgetSnapshot: ...

    def record_global_gemini_call(
        self, *, units: int = 1, now: datetime | None = None
    ) -> SystemBudgetSnapshot: ...

    def consume_vision_pages(
        self, *, requested: int, now: datetime | None = None
    ) -> tuple[int, SystemBudgetSnapshot]: ...

    def record_rag_usage(self, entry: RagUsageLedgerEntry) -> None: ...

    def list_rag_usage(self, *, limit: int = 200) -> list[RagUsageLedgerEntry]: ...


class InMemoryQuotaStore:
    """Thread-safe atomic in-memory quota store (used offline & in unit tests)."""

    def __init__(self, *, budget_config: QuotaBudgetConfig | None = None) -> None:
        self.budget_config = budget_config or QuotaBudgetConfig.from_env()
        self._lock = threading.Lock()
        self._usage: dict[tuple[date, str, QuotaMeter], int] = {}
        self._gemini_used: dict[date, int] = {}
        self._vision_used: dict[date, int] = {}
        self._ledger: list[RagUsageLedgerEntry] = []

    def check_and_consume_subject(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        limit: int | None,
        unlimited: bool,
        units: int,
        now: datetime | None = None,
    ) -> QuotaReceipt:
        if units < 1:
            raise ValueError("units must be >= 1")
        qdate, reset_at = current_quota_window(now)
        with self._lock:
            key = (qdate, subject_key, meter)
            used = self._usage.get(key, 0)
            if not unlimited and limit is not None and (used + units) > limit:
                raise QuotaExceededError(
                    meter=meter,
                    limit=limit,
                    used=used,
                    reset_at=reset_at,
                )
            new_used = used + units
            self._usage[key] = new_used
        remaining = None if (unlimited or limit is None) else max(0, limit - new_used)
        return QuotaReceipt(
            subject_key=subject_key,
            meter=meter,
            quota_date=qdate,
            units=units,
            used=new_used,
            limit=limit,
            remaining=remaining,
            unlimited=unlimited,
            reset_at=reset_at,
        )

    def refund_subject(self, receipt: QuotaReceipt) -> None:
        if receipt.units <= 0:
            return
        with self._lock:
            key = (receipt.quota_date, receipt.subject_key, receipt.meter)
            current = self._usage.get(key, 0)
            self._usage[key] = max(0, current - receipt.units)

    def get_subject_used(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        now: datetime | None = None,
    ) -> int:
        qdate, _ = current_quota_window(now)
        with self._lock:
            return self._usage.get((qdate, subject_key, meter), 0)

    def _build_budget_values(
        self, bdate: date, reset_at: datetime, *, g_used: int, v_used: int
    ) -> SystemBudgetSnapshot:
        g_cap = self.budget_config.global_daily_gemini_cap
        v_cap = self.budget_config.daily_vision_page_cap
        trip_at = self.budget_config.circuit_breaker_trip_count()
        ratio = round(g_used / max(1, g_cap), 4)
        return SystemBudgetSnapshot(
            budget_date=bdate,
            timezone=VIETNAM_TIMEZONE_NAME,
            reset_at=reset_at,
            gemini_requests_used=g_used,
            gemini_requests_cap=g_cap,
            gemini_utilization_ratio=ratio,
            circuit_breaker_ratio=self.budget_config.circuit_breaker_ratio,
            circuit_breaker_open=g_used >= trip_at,
            vision_pages_used=v_used,
            vision_pages_cap=v_cap,
            vision_pages_remaining=max(0, v_cap - v_used),
        )

    def _build_budget_locked(
        self, bdate: date, reset_at: datetime
    ) -> SystemBudgetSnapshot:
        return self._build_budget_values(
            bdate,
            reset_at,
            g_used=self._gemini_used.get(bdate, 0),
            v_used=self._vision_used.get(bdate, 0),
        )

    def get_system_budget(self, *, now: datetime | None = None) -> SystemBudgetSnapshot:
        bdate, reset_at = current_quota_window(now)
        with self._lock:
            return self._build_budget_locked(bdate, reset_at)

    def record_global_gemini_call(
        self, *, units: int = 1, now: datetime | None = None
    ) -> SystemBudgetSnapshot:
        bdate, reset_at = current_quota_window(now)
        with self._lock:
            if units > 0:
                self._gemini_used[bdate] = self._gemini_used.get(bdate, 0) + units
            return self._build_budget_locked(bdate, reset_at)

    def consume_vision_pages(
        self, *, requested: int, now: datetime | None = None
    ) -> tuple[int, SystemBudgetSnapshot]:
        if requested <= 0:
            return 0, self.get_system_budget(now=now)
        bdate, reset_at = current_quota_window(now)
        with self._lock:
            used = self._vision_used.get(bdate, 0)
            cap = self.budget_config.daily_vision_page_cap
            available = max(0, cap - used)
            granted = min(requested, available)
            if granted > 0:
                self._vision_used[bdate] = used + granted
            return granted, self._build_budget_locked(bdate, reset_at)

    def record_rag_usage(self, entry: RagUsageLedgerEntry) -> None:
        with self._lock:
            self._ledger.append(entry)

    def list_rag_usage(self, *, limit: int = 200) -> list[RagUsageLedgerEntry]:
        with self._lock:
            return list(reversed(self._ledger[-max(1, limit) :]))

    def get_user_status(
        self,
        *,
        user: AuthenticatedUser | None,
        now: datetime | None = None,
    ) -> UserQuotaSnapshot:
        return build_user_quota_snapshot(store=self, user=user, now=now)


_VALKEY_CONSUME_LUA = """
local key = KEYS[1]
local units = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key) or '0')
if limit >= 0 and (current + units) > limit then
    return {0, current}
end
local updated = redis.call('INCRBY', key, units)
if ttl > 0 then
    redis.call('EXPIRE', key, ttl)
end
return {1, updated}
"""

_VALKEY_CAP_GRANT_LUA = """
local key = KEYS[1]
local requested = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key) or '0')
local available = cap - current
if available < 0 then
    available = 0
end
local granted = requested
if granted > available then
    granted = available
end
local updated = current
if granted > 0 then
    updated = redis.call('INCRBY', key, granted)
    if ttl > 0 then
        redis.call('EXPIRE', key, ttl)
    end
end
return {granted, updated}
"""


class ValkeyBackedQuotaStore(InMemoryQuotaStore):
    """Atomic Valkey/Redis + optional Postgres-ledger quota store with lazy imports."""

    def __init__(
        self,
        *,
        valkey_url: str | None = None,
        pg_dsn: str | None = None,
        budget_config: QuotaBudgetConfig | None = None,
    ) -> None:
        super().__init__(budget_config=budget_config)
        self._valkey_url = (valkey_url or "").strip() or None
        self._pg_dsn = (pg_dsn or "").strip() or None
        self._redis_client: Any | None = None

    def _get_redis(self) -> Any | None:
        if not self._valkey_url:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            redis_mod = importlib.import_module("redis")  # Lazy import (M-03)
            self._redis_client = redis_mod.Redis.from_url(
                self._valkey_url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=True,
            )
            return self._redis_client
        except Exception:
            return None

    def check_and_consume_subject(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        limit: int | None,
        unlimited: bool,
        units: int,
        now: datetime | None = None,
    ) -> QuotaReceipt:
        client = self._get_redis()
        if client is not None:
            qdate, reset_at = current_quota_window(now)
            ttl = seconds_until_reset(reset_at, now) + 300
            rkey = f"finintel:quota:{qdate.isoformat()}:{subject_key}:{meter}"
            eff_limit = -1 if (unlimited or limit is None) else int(limit)
            try:
                res = client.eval(_VALKEY_CONSUME_LUA, 1, rkey, units, eff_limit, ttl)
                allowed = int(res[0]) == 1
                val = int(res[1])
                if not allowed:
                    assert limit is not None
                    raise QuotaExceededError(
                        meter=meter,
                        limit=limit,
                        used=val,
                        reset_at=reset_at,
                    )
                remaining = (
                    None if (unlimited or limit is None) else max(0, limit - val)
                )
                return QuotaReceipt(
                    subject_key=subject_key,
                    meter=meter,
                    quota_date=qdate,
                    units=units,
                    used=val,
                    limit=limit,
                    remaining=remaining,
                    unlimited=unlimited,
                    reset_at=reset_at,
                )
            except QuotaExceededError:
                raise
            except Exception:
                pass
        return super().check_and_consume_subject(
            subject_key=subject_key,
            meter=meter,
            limit=limit,
            unlimited=unlimited,
            units=units,
            now=now,
        )

    def refund_subject(self, receipt: QuotaReceipt) -> None:
        client = self._get_redis()
        if client is not None and receipt.units > 0:
            rkey = (
                f"finintel:quota:{receipt.quota_date.isoformat()}:"
                f"{receipt.subject_key}:{receipt.meter}"
            )
            try:
                new_val = int(client.decrby(rkey, receipt.units))
                if new_val < 0:
                    client.set(rkey, 0)
                return
            except Exception:
                pass
        super().refund_subject(receipt)

    def get_subject_used(
        self,
        *,
        subject_key: str,
        meter: QuotaMeter,
        now: datetime | None = None,
    ) -> int:
        client = self._get_redis()
        if client is not None:
            qdate, _ = current_quota_window(now)
            rkey = f"finintel:quota:{qdate.isoformat()}:{subject_key}:{meter}"
            try:
                raw = client.get(rkey)
                if raw is not None:
                    return max(0, int(raw))
            except Exception:
                pass
        return super().get_subject_used(subject_key=subject_key, meter=meter, now=now)

    @staticmethod
    def _global_budget_keys(bdate: date) -> tuple[str, str]:
        prefix = f"finintel:quota:{bdate.isoformat()}:global"
        return f"{prefix}:gemini_requests", f"{prefix}:vision_pages"

    def get_system_budget(self, *, now: datetime | None = None) -> SystemBudgetSnapshot:
        client = self._get_redis()
        if client is not None:
            bdate, reset_at = current_quota_window(now)
            gemini_key, vision_key = self._global_budget_keys(bdate)
            try:
                g_raw = client.get(gemini_key)
                v_raw = client.get(vision_key)
                if g_raw is not None or v_raw is not None:
                    fallback = super().get_system_budget(now=now)
                    g_used = (
                        max(0, int(g_raw))
                        if g_raw is not None
                        else fallback.gemini_requests_used
                    )
                    v_used = (
                        max(0, int(v_raw))
                        if v_raw is not None
                        else fallback.vision_pages_used
                    )
                    return self._build_budget_values(
                        bdate, reset_at, g_used=g_used, v_used=v_used
                    )
            except Exception:
                pass
        return super().get_system_budget(now=now)

    def record_global_gemini_call(
        self, *, units: int = 1, now: datetime | None = None
    ) -> SystemBudgetSnapshot:
        client = self._get_redis()
        if client is not None:
            bdate, reset_at = current_quota_window(now)
            gemini_key, vision_key = self._global_budget_keys(bdate)
            ttl = seconds_until_reset(reset_at, now) + 300
            try:
                if units > 0:
                    res = client.eval(
                        _VALKEY_CONSUME_LUA, 1, gemini_key, units, -1, ttl
                    )
                    g_used = max(0, int(res[1]))
                else:
                    g_used = max(0, int(client.get(gemini_key) or 0))
                v_raw = client.get(vision_key)
                v_used = max(0, int(v_raw)) if v_raw is not None else 0
                return self._build_budget_values(
                    bdate, reset_at, g_used=g_used, v_used=v_used
                )
            except Exception:
                pass
        return super().record_global_gemini_call(units=units, now=now)

    def consume_vision_pages(
        self, *, requested: int, now: datetime | None = None
    ) -> tuple[int, SystemBudgetSnapshot]:
        if requested <= 0:
            return 0, self.get_system_budget(now=now)
        client = self._get_redis()
        if client is not None:
            bdate, reset_at = current_quota_window(now)
            gemini_key, vision_key = self._global_budget_keys(bdate)
            ttl = seconds_until_reset(reset_at, now) + 300
            cap = int(self.budget_config.daily_vision_page_cap)
            try:
                res = client.eval(
                    _VALKEY_CAP_GRANT_LUA, 1, vision_key, requested, cap, ttl
                )
                granted = max(0, int(res[0]))
                v_used = max(0, int(res[1]))
                g_raw = client.get(gemini_key)
                g_used = max(0, int(g_raw)) if g_raw is not None else 0
                return granted, self._build_budget_values(
                    bdate, reset_at, g_used=g_used, v_used=v_used
                )
            except Exception:
                pass
        return super().consume_vision_pages(requested=requested, now=now)

    def record_rag_usage(self, entry: RagUsageLedgerEntry) -> None:
        super().record_rag_usage(entry)
        if not self._pg_dsn:
            return
        try:
            psycopg_mod = importlib.import_module("psycopg")  # Lazy import (M-03)
            with psycopg_mod.connect(self._pg_dsn, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO rag_usage_ledger (
                            usage_date, actor_subject_hash, operation, ticker,
                            filing_id, pdf_sha256, digital_pages, vision_pages,
                            llm_calls, cache_hit, status, estimated_cost_usd
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            entry.usage_date,
                            entry.actor_subject_hash,
                            entry.operation,
                            entry.ticker,
                            entry.filing_id,
                            entry.pdf_sha256,
                            entry.digital_pages,
                            entry.vision_pages,
                            entry.llm_calls,
                            entry.cache_hit,
                            entry.status,
                            entry.estimated_cost_usd,
                        ),
                    )
                conn.commit()
        except Exception:
            pass


def build_user_quota_snapshot(
    *,
    store: QuotaStoreProtocol,
    user: AuthenticatedUser | None,
    now: datetime | None = None,
) -> UserQuotaSnapshot:
    """Build current daily quota status for ``user`` (or anonymous visitor)."""
    qdate, reset_at = current_quota_window(now)
    budget = store.get_system_budget(now=now)
    reopen_at = reset_at if budget.circuit_breaker_open else None

    if user is None:
        return UserQuotaSnapshot(
            authenticated=False,
            role="ANONYMOUS",
            quota_date=qdate,
            timezone=VIETNAM_TIMEZONE_NAME,
            reset_at=reset_at,
            cross_border_rag_consent=False,
            circuit_breaker_open=budget.circuit_breaker_open,
            circuit_breaker_reopen_at=reopen_at,
            meters=(),
        )

    policy = get_role_quota_policy(user.role)
    subject_key = f"user:{user.user_id}"
    meters: list[MeterQuotaSnapshot] = []
    for meter in ALL_QUOTA_METERS:
        used = store.get_subject_used(subject_key=subject_key, meter=meter, now=now)
        limit = policy.limit_for(meter)
        remaining = (
            None if (policy.unlimited or limit is None) else max(0, limit - used)
        )
        meters.append(
            MeterQuotaSnapshot(
                meter=meter,
                used=used,
                limit=limit,
                remaining=remaining,
                unlimited=policy.unlimited,
                reset_at=reset_at,
            )
        )
    return UserQuotaSnapshot(
        authenticated=True,
        role=user.role,
        quota_date=qdate,
        timezone=VIETNAM_TIMEZONE_NAME,
        reset_at=reset_at,
        cross_border_rag_consent=ConsentPurpose.CROSS_BORDER_LLM_RAG in user.consents,
        circuit_breaker_open=budget.circuit_breaker_open,
        circuit_breaker_reopen_at=reopen_at,
        meters=tuple(meters),
    )


_DEFAULT_STORE_LOCK = threading.Lock()
_DEFAULT_QUOTA_STORE: QuotaStoreProtocol | None = None


def get_default_quota_store() -> QuotaStoreProtocol:
    """Return process-wide default ``QuotaStoreProtocol`` instance."""
    global _DEFAULT_QUOTA_STORE
    if _DEFAULT_QUOTA_STORE is not None:
        return _DEFAULT_QUOTA_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_QUOTA_STORE is None:
            valkey_url = (
                os.getenv("FININTEL_VALKEY_URL")
                or os.getenv("FININTEL_REDIS_URL")
                or ""
            ).strip() or None
            pg_dsn = os.getenv("FININTEL_APP_PG_DSN", "").strip() or None
            if valkey_url or pg_dsn:
                _DEFAULT_QUOTA_STORE = ValkeyBackedQuotaStore(
                    valkey_url=valkey_url,
                    pg_dsn=pg_dsn,
                )
            else:
                _DEFAULT_QUOTA_STORE = InMemoryQuotaStore()
        return _DEFAULT_QUOTA_STORE


class QuotaGuard:
    """High-level coordinator enforcing fair-use limits and the global Gemini breaker."""

    def __init__(self, store: QuotaStoreProtocol | None = None) -> None:
        self.store = store or get_default_quota_store()

    def ensure_global_circuit_available(
        self, *, now: datetime | None = None
    ) -> SystemBudgetSnapshot:
        """Raise ``CircuitBreakerOpenError`` (503) when Gemini free-tier daily cap hits 85%."""
        budget = self.store.get_system_budget(now=now)
        if budget.circuit_breaker_open:
            raise CircuitBreakerOpenError(
                used=budget.gemini_requests_used,
                cap=budget.gemini_requests_cap,
                reopen_at=budget.reset_at,
            )
        return budget

    def enforce_and_consume(
        self,
        *,
        user: AuthenticatedUser,
        meter: QuotaMeter,
        units: int = 1,
        now: datetime | None = None,
    ) -> QuotaReceipt:
        """Atomically verify global circuit breaker (for LLM meters) and consume user quota."""
        if meter == "rag_query":
            self.ensure_global_circuit_available(now=now)
        policy = get_role_quota_policy(user.role)
        limit = policy.limit_for(meter)
        return self.store.check_and_consume_subject(
            subject_key=f"user:{user.user_id}",
            meter=meter,
            limit=limit,
            unlimited=policy.unlimited,
            units=units,
            now=now,
        )

    def enforce_guest_rag(
        self,
        *,
        client_ip: str,
        units: int = 1,
        now: datetime | None = None,
    ) -> QuotaReceipt:
        """Enforce global circuit breaker and IP-hashed daily cap for unauthenticated guests."""
        self.ensure_global_circuit_available(now=now)
        ip_hash = hash_actor_subject(client_ip or "anonymous")[:24]
        guest_limit = self.store.budget_config.guest_rag_daily_limit
        return self.store.check_and_consume_subject(
            subject_key=f"guest:{ip_hash}",
            meter="rag_query",
            limit=guest_limit,
            unlimited=False,
            units=units,
            now=now,
        )

    def refund(self, receipt: QuotaReceipt | None) -> None:
        """Refund reserved units (must be called on cache HIT or 5xx error)."""
        if receipt is not None:
            self.store.refund_subject(receipt)

    def record_global_gemini_call(
        self, *, units: int = 1, now: datetime | None = None
    ) -> SystemBudgetSnapshot:
        return self.store.record_global_gemini_call(units=units, now=now)

    def record_rag_usage(self, entry: RagUsageLedgerEntry) -> None:
        self.store.record_rag_usage(entry)

    def get_user_status(
        self,
        *,
        user: AuthenticatedUser | None,
        now: datetime | None = None,
    ) -> UserQuotaSnapshot:
        return build_user_quota_snapshot(store=self.store, user=user, now=now)

    def get_system_budget(self, *, now: datetime | None = None) -> SystemBudgetSnapshot:
        return self.store.get_system_budget(now=now)


def enforce_and_consume_quota(
    *,
    user: AuthenticatedUser,
    meter: QuotaMeter,
    units: int = 1,
    store: QuotaStoreProtocol | None = None,
    now: datetime | None = None,
) -> QuotaReceipt:
    """SPEC §5.1 helper: atomically check daily fair-use limit & global free-tier circuit breaker."""
    guard = QuotaGuard(store=store)
    return guard.enforce_and_consume(user=user, meter=meter, units=units, now=now)


def refund_quota(
    receipt: QuotaReceipt | None,
    *,
    store: QuotaStoreProtocol | None = None,
) -> None:
    """Refund a previously reserved ``QuotaReceipt`` (e.g. on cache HIT or 5xx error)."""
    if receipt is None:
        return
    guard = QuotaGuard(store=store)
    guard.refund(receipt)


__all__ = [
    "CircuitBreakerOpenError",
    "InMemoryQuotaStore",
    "MeterQuotaSnapshot",
    "QuotaExceededError",
    "QuotaGuard",
    "QuotaReceipt",
    "QuotaStoreProtocol",
    "RagUsageLedgerEntry",
    "SystemBudgetSnapshot",
    "UserQuotaSnapshot",
    "ValkeyBackedQuotaStore",
    "anonymize_rag_prompt_pii",
    "build_user_quota_snapshot",
    "enforce_and_consume_quota",
    "get_default_quota_store",
    "hash_actor_subject",
    "refund_quota",
]
