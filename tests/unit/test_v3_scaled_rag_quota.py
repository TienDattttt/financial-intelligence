"""Unit and concurrency tests for V3-19..V3-21: Fair-Use Quota, Circuit Breaker, and Scaled PDF RAG."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from finintel.auth.dependencies import AuthenticatedUser, UserRole
from finintel.domain.compliance import ConsentPurpose

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_user(
    role: UserRole = UserRole.USER,
    *,
    consents: frozenset[ConsentPurpose] = frozenset(
        {ConsentPurpose.ACCOUNT_CORE, ConsentPurpose.CROSS_BORDER_LLM_RAG}
    ),
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        email="analyst@finintel.vn",
        role=role,
        consents=consents,
        display_name="Minh Analyst",
    )


def test_quota_migration_ddl_and_zero_payment_tables() -> None:
    """V3-19: Migration defines fair-use quota & ledger tables with zero payment/billing artifacts."""
    ddl_path = REPO_ROOT / "config" / "migrations" / "app_v3_003_fair_use_quota.sql"
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in ("usage_quota_daily", "global_llm_budget_daily", "rag_usage_ledger"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    lowered = ddl.lower()
    for forbidden in ("stripe", "vnpay", "payos", "subscription_price", "checkout"):
        assert forbidden not in lowered


def test_quota_lazy_imports_without_platform_or_rag_deps() -> None:
    """Core quota modules import cleanly when psycopg/redis/fastapi/pymupdf are absent."""
    script = (
        "import importlib, sys\n"
        "for pkg in ('psycopg', 'redis', 'fastapi', 'pydantic', 'pymupdf', 'fitz', 'google', 'google.genai', 'pgvector', 'sentence_transformers'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.quota',\n"
        "    'finintel.quota.policies',\n"
        "    'finintel.quota.guard',\n"
        "    'finintel.quota.router',\n"
        "    'finintel.quota.schemas',\n"
        "):\n"
        "    importlib.import_module(mod_name)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_role_policies_and_hanoi_midnight_reset() -> None:
    """V3-19: USER (15 RAG/10 xlsx), RESEARCHER (50 RAG/30 xlsx), INTERNAL_OPS configurable, reset at 00:00 +07:00."""
    from finintel.quota.policies import (
        VIETNAM_TZ,
        current_quota_window,
        get_role_quota_policy,
    )

    user_pol = get_role_quota_policy(UserRole.USER)
    assert user_pol.rag_queries_per_day == 15
    assert user_pol.xlsx_exports_per_day == 10
    assert user_pol.unlimited is False

    res_pol = get_role_quota_policy(UserRole.RESEARCHER)
    assert res_pol.rag_queries_per_day == 50
    assert res_pol.xlsx_exports_per_day == 30
    assert res_pol.unlimited is False

    ops_pol = get_role_quota_policy(UserRole.INTERNAL_OPS)
    assert ops_pol.rag_queries_per_day >= 500
    assert ops_pol.unlimited is True

    # 2026-11-27 17:30 UTC is 2026-11-28 00:30 Asia/Ho_Chi_Minh -> resets at 2026-11-29 00:00+07:00
    now_utc = datetime(2026, 11, 27, 17, 30, tzinfo=UTC)
    quota_date, reset_at = current_quota_window(now_utc)
    assert quota_date.isoformat() == "2026-11-28"
    assert reset_at.tzinfo == VIETNAM_TZ
    assert reset_at.isoformat() == "2026-11-29T00:00:00+07:00"


def test_atomic_concurrent_quota_enforcement_20_threads_limit_15() -> None:
    """V3-19: 20 concurrent requests with limit 15 -> exactly 15 succeed, 5 raise HTTP 429 with reset_at."""
    from finintel.quota.guard import (
        InMemoryQuotaStore,
        QuotaExceededError,
        enforce_and_consume_quota,
    )

    store = InMemoryQuotaStore()
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 8, 0, tzinfo=UTC)

    def _attempt(_idx: int) -> tuple[bool, int | None, str | None]:
        try:
            receipt = enforce_and_consume_quota(
                user=user,
                meter="rag_query",
                store=store,
                now=now,
            )
            return True, receipt.remaining, None
        except QuotaExceededError as exc:
            return False, exc.status_code, exc.reset_at.isoformat()

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(_attempt, range(20)))

    successes = [o for o in outcomes if o[0] is True]
    failures = [o for o in outcomes if o[0] is False]
    assert len(successes) == 15
    assert len(failures) == 5
    for _, code, reset_iso in failures:
        assert code == 429
        assert reset_iso is not None and reset_iso.endswith("T00:00:00+07:00")

    status = store.get_user_status(user=user, now=now)
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 15
    assert rag_status.remaining == 0


def test_refund_on_cache_hit_and_5xx_never_deducts_quota() -> None:
    """V3-19: Refunding reserved quota (cache HIT or 5xx error) preserves exact remaining count."""
    from finintel.quota.guard import (
        InMemoryQuotaStore,
        enforce_and_consume_quota,
        refund_quota,
    )

    store = InMemoryQuotaStore()
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)

    receipt = enforce_and_consume_quota(
        user=user, meter="rag_query", store=store, now=now
    )
    assert receipt.used == 1
    assert receipt.remaining == 14

    refund_quota(receipt, store=store)
    status = store.get_user_status(user=user, now=now)
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 0
    assert rag_status.remaining == 15


def test_global_gemini_circuit_breaker_trips_at_85_percent() -> None:
    """V3-19: Global Gemini Circuit Breaker trips at 85% daily free-tier cap returning 503 without deducting user quota."""
    from finintel.quota.guard import (
        CircuitBreakerOpenError,
        InMemoryQuotaStore,
        enforce_and_consume_quota,
    )
    from finintel.quota.policies import QuotaBudgetConfig

    cfg = QuotaBudgetConfig(
        global_daily_gemini_cap=100,
        circuit_breaker_ratio=0.85,
        daily_vision_page_cap=150,
    )
    store = InMemoryQuotaStore(budget_config=cfg)
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)

    # 84 calls (< 85%) -> allowed
    store.record_global_gemini_call(units=84, now=now)
    receipt = enforce_and_consume_quota(
        user=user, meter="rag_query", store=store, now=now
    )
    assert receipt.used == 1

    # Reach 85/100 (85%) -> circuit breaker opens -> 503, user quota untouched!
    store.record_global_gemini_call(units=1, now=now)
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        enforce_and_consume_quota(user=user, meter="rag_query", store=store, now=now)
    assert exc_info.value.status_code == 503
    assert exc_info.value.reopen_at.isoformat().endswith("T00:00:00+07:00")
    assert int(exc_info.value.headers["Retry-After"]) > 0

    status = store.get_user_status(user=user, now=now)
    assert status.circuit_breaker_open is True
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 1  # Not deducted on 503!

    # xlsx_export is NOT a Gemini call, so it still works during Gemini circuit breaker!
    xlsx_receipt = enforce_and_consume_quota(
        user=user, meter="xlsx_export", store=store, now=now
    )
    assert xlsx_receipt.used == 1


def test_quota_fastapi_endpoints_and_ops_rbac() -> None:
    """V3-19: GET /api/v3/quota/me and INTERNAL_OPS-only GET /api/v3/ops/quota/system-budget."""
    pytest.importorskip("fastapi")
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request, Response
    from fastapi.testclient import TestClient

    from finintel.quota.guard import InMemoryQuotaStore, QuotaGuard
    from finintel.quota.router import create_quota_router

    store = InMemoryQuotaStore()
    guard = QuotaGuard(store)
    active_user: list[AuthenticatedUser | None] = [None]

    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if active_user[0] is not None:
            request.state._cached_auth_user = active_user[0]
            request.state._cached_auth_checked = True
        return await call_next(request)

    app.include_router(create_quota_router(get_quota_guard=lambda: guard))

    with TestClient(app) as client:
        # Anonymous guest sees unauthenticated quota view (200) but cannot hit ops endpoint (401)
        r_anon = client.get("/api/v3/quota/me")
        assert r_anon.status_code == 200
        assert r_anon.json()["authenticated"] is False

        r_ops_anon = client.get("/api/v3/ops/quota/system-budget")
        assert r_ops_anon.status_code == 401

        # Regular USER sees 15/10 limits, forbidden (403) on ops endpoint
        regular = _make_user(UserRole.USER)
        active_user[0] = regular
        guard.enforce_and_consume(user=regular, meter="rag_query", units=2)
        r_user = client.get("/api/v3/quota/me")
        assert r_user.status_code == 200
        body = r_user.json()
        assert body["authenticated"] is True
        assert body["role"] == "USER"
        assert body["cross_border_rag_consent"] is True
        assert body["timezone"] == "Asia/Ho_Chi_Minh"
        rag_m = next(m for m in body["meters"] if m["meter"] == "rag_query")
        assert rag_m["used"] == 2
        assert rag_m["limit"] == 15
        assert rag_m["remaining"] == 13

        assert client.get("/api/v3/ops/quota/system-budget").status_code == 403

        # INTERNAL_OPS accesses system budget (200)
        active_user[0] = _make_user(UserRole.INTERNAL_OPS)
        r_ops = client.get("/api/v3/ops/quota/system-budget")
        assert r_ops.status_code == 200
        ops_body = r_ops.json()
        assert ops_body["vision_pages_cap"] == 150
        assert ops_body["gemini_requests_cap"] == 1500
        assert ops_body["circuit_breaker_ratio"] == 0.85
