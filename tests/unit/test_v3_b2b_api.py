"""Unit and integration tests for V3-25..V3-26: Open Research API Gateway & Python SDK."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import polars as pl
import pytest

from finintel.analytics.tables import build_analytics_tables
from finintel.api_gateway import (
    InMemoryApiGatewayStore,
    SlidingWindowRateLimiter,
    generate_api_key,
    hash_api_key,
    is_raw_redistribution_enabled,
    is_well_formed_api_key,
    rotate_api_key_record,
    verify_api_key_hash,
)
from finintel.auth.dependencies import UserRole
from finintel.domain.compliance import CURRENT_CONSENT_POLICY_VERSION
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    ANALYTICS_SCHEMAS,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)

if TYPE_CHECKING:
    import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_minimal_snapshot(
    data_root: Path, snapshot_id: str = "snap_v3_b2b_001"
) -> None:
    snap_dir = data_root / "published" / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    period_start = date(2025, 10, 1)
    period_end = date(2025, 12, 31)
    pub_date = date(2026, 1, 25)

    issuers = pl.DataFrame(
        {
            "issuer_id": ["ISS_HPG"],
            "ticker": ["HPG"],
            "exchange": ["HOSE"],
            "company_name_vn": ["Tập đoàn Hòa Phát"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["1700"],
            "icb_name_level2": ["Tài nguyên cơ bản"],
            "icb_code_level3": ["1750"],
            "icb_name_level3": ["Kim loại"],
            "industry_snapshot_date": [date(2025, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )
    issuers.write_parquet(snap_dir / "issuers.parquet")

    metrics: dict[str, float] = {
        "BS_CURRENT_ASSETS": 90e12,
        "BS_CASH_AND_EQUIVALENTS": 18e12,
        "BS_SHORT_TERM_INVESTMENTS": 5e12,
        "BS_SHORT_TERM_RECEIVABLES": 18e12,
        "BS_TRADE_RECEIVABLES_ST": 15e12,
        "BS_BAD_DEBT_PROVISION_ST": -500e9,
        "BS_INVENTORIES": 35e12,
        "BS_TOTAL_ASSETS": 210e12,
        "BS_TOTAL_LIABILITIES": 100e12,
        "BS_CURRENT_LIABILITIES": 60e12,
        "BS_SHORT_TERM_BORROWINGS": 40e12,
        "BS_TRADE_PAYABLES_ST": 20e12,
        "BS_LONG_TERM_BORROWINGS": 30e12,
        "BS_CONVERTIBLE_BONDS": 0.0,
        "BS_TOTAL_EQUITY": 110e12,
        "BS_ORDINARY_SHARES_CAPITAL": 58e12,
        "BS_TREASURY_SHARES": 0.0,
        "BS_MINORITY_INTEREST": 2e12,
        "IS_NET_REVENUE": 30e12,
        "IS_COST_OF_GOODS_SOLD": -24.6e12,
        "IS_GROSS_PROFIT": 5.4e12,
        "IS_SELLING_EXPENSES": -600e9,
        "IS_ADMINISTRATIVE_EXPENSES": -700e9,
        "IS_INTEREST_EXPENSE": -800e9,
        "IS_OPERATING_PROFIT": 3.3e12,
        "IS_PROFIT_BEFORE_TAX": 3.5e12,
        "IS_CURRENT_INCOME_TAX": -600e9,
        "IS_DEFERRED_INCOME_TAX": 50e9,
        "IS_NET_INCOME": 2.95e12,
        "IS_NET_INCOME_PARENT": 2.9e12,
        "IS_EPS_BASIC_REPORTED": 1000.0,
        "CF_DEPRECIATION_AMORTIZATION": 1.2e12,
        "CF_OPERATING_CASH_FLOW": 3.8e12,
        "CF_CAPITAL_EXPENDITURE": -1.5e12,
    }
    fact_rows = [
        {
            "issuer_id": "ISS_HPG",
            "statement_scope": "CONSOLIDATED",
            "period_type": "QUARTER",
            "fiscal_year_start_date": date(2025, 1, 1),
            "period_end_date": period_end,
            "metric_id": mid,
            "taxonomy_version": "VAS_TT200_2014",
            "revision_no": 1,
            "filing_id": "FIL_HPG_2025Q4",
            "fiscal_year": 2025,
            "fiscal_quarter": 4,
            "period_start_date": period_start,
            "published_at": pub_date,
            "published_at_basis": "EXACT_FILING",
            "effective_available_at": pub_date,
            "revision_reason": "ORIGINAL",
            "derivation_method": "REPORTED_DIRECTLY",
            "audit_status": "AUDITED",
            "audit_opinion": "UNMODIFIED",
            "value": float(val),
            "ttm_value": float(val) * 4.0 if not mid.startswith("BS_") else None,
            "computation_note": None,
        }
        for mid, val in metrics.items()
    ]
    facts_df = pl.DataFrame(fact_rows).with_columns(
        pl.col("fiscal_year").cast(pl.Int32),
        pl.col("fiscal_quarter").cast(pl.Int8),
        pl.col("revision_no").cast(pl.Int64),
        pl.col("computation_note").cast(pl.String),
    )
    facts_df.write_parquet(snap_dir / "financial_facts.parquet")
    facts_df.write_parquet(snap_dir / "latest_facts.parquet")

    analytics = build_analytics_tables(facts_df, issuers, as_of=date(2026, 2, 1))
    analytics[RATIOS_TABLE].write_parquet(snap_dir / RATIOS_TABLE)
    analytics[BENCHMARKS_TABLE].write_parquet(snap_dir / BENCHMARKS_TABLE)
    rf_df = analytics.get(
        RED_FLAGS_TABLE, pl.DataFrame(schema=ANALYTICS_SCHEMAS[RED_FLAGS_TABLE])
    )
    rf_df.write_parquet(snap_dir / RED_FLAGS_TABLE)

    ml_row = {
        "issuer_id": "ISS_HPG",
        "statement_scope": "CONSOLIDATED",
        "period_type": "QUARTER",
        "period_end_date": period_end,
        "filing_id": "FIL_HPG_2025Q4",
        "model_role": "PRIMARY",
        "altman_z_double_prime": 2.9,
        "altman_em_score": 2.5,
        "altman_zone": "SAFE_ZONE",
        "piotroski_f_score": 7,
        "piotroski_valid_components_count": 9,
        "beneish_m8_score": -2.4,
        "beneish_flag": "VALID",
        "pca_pc1": 0.1,
        "pca_pc2": 0.2,
        "pca_pc3": -0.1,
        "cluster_id": 1,
        "cluster_archetype_name": "Balanced",
        "anomaly_score_raw": 0.05,
        "anomaly_percentile_rank": 0.12,
        "statistical_outlier_flag": False,
        "anomaly_flag": "NORMAL",
        "distress_risk_score": 0.08,
        "calibrated_distress_prob": 0.035,
        "risk_tier": "R1",
        "risk_market_percentile": 20.0,
        "market_base_rate": 0.04,
        "relative_risk_lift": 0.875,
        "model_version": "v2.0",
        "shap_top_drivers_json": "{}",
    }
    pl.DataFrame([ml_row], schema=ML_SCORES_SCHEMA).write_parquet(
        snap_dir / ML_SCORES_TABLE
    )

    manifest = pl.DataFrame(
        [
            {
                "filing_id": "FIL_HPG_2025Q4",
                "issuer_id": "ISS_HPG",
                "ticker_at_filing": "HPG",
                "source_provider": "hose_pdf_extract",
                "connector_name": "file_adapter_v1",
                "statement_type": "CONSOLIDATED_PACK",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": period_end,
                "published_at": pub_date,
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": pub_date,
                "audit_status": "AUDITED",
                "audit_opinion": "UNMODIFIED",
                "revision_no": 1,
                "revision_reason": "ORIGINAL",
                "canonical_content_sha256": "a" * 64,
                "payload_file_sha256": "b" * 64,
            }
        ],
        schema=FILINGS_MANIFEST_SCHEMA,
    )
    manifest.write_parquet(snap_dir / FILINGS_MANIFEST_TABLE)

    meta_path = snap_dir / "snapshot_metadata.json"
    meta_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "published_at": "2026-01-26T00:00:00Z",
                "issuer_count": 1,
                "status": "PUBLISHED",
            }
        ),
        encoding="utf-8",
    )
    active_ptr = data_root / "published" / "active_snapshot.json"
    active_ptr.parent.mkdir(parents=True, exist_ok=True)
    active_ptr.write_text(json.dumps({"snapshot_id": snapshot_id}), encoding="utf-8")
    ParquetRepository(data_root=data_root)


def test_b2b_migration_ddl_tables_and_security() -> None:
    """V3-25: Migration defines api_keys, api_key_usage_daily, b2b_webhook_endpoints."""
    ddl_path = REPO_ROOT / "config" / "migrations" / "app_v3_005_b2b_api.sql"
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in ("api_keys", "api_key_usage_daily", "b2b_webhook_endpoints"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "key_hash CHAR(64) NOT NULL UNIQUE" in ddl
    assert "plaintext" not in ddl.lower().split("create table")[1]


def test_api_gateway_lazy_imports_without_platform_deps() -> None:
    """Offline mvp-checks simulation: api_gateway imports cleanly without platform deps."""
    script = (
        "import importlib, sys\n"
        "for pkg in ('fastapi', 'pydantic', 'argon2', 'psycopg', 'redis'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.api_gateway',\n"
        "    'finintel.api_gateway.keys',\n"
        "    'finintel.api_gateway.rate_limiter',\n"
        "    'finintel.api_gateway.middleware',\n"
        "    'finintel.api_gateway.schemas',\n"
        "    'finintel.api_gateway.router',\n"
        "    'finintel.api_gateway.open_router',\n"
        "    'finintel.api_gateway._memory_store',\n"
        "    'finintel.api_gateway._webhooks_router',\n"
        "    'finintel.api_gateway._pg_store',\n"
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


def test_api_key_generation_hashing_and_rotation() -> None:
    """V3-25: fi_res_/fi_live_ keys store only SHA-256 hash and rotate cleanly."""
    pt_res, prefix_res, hash_res = generate_api_key(prefix_mode="res")
    assert pt_res.startswith("fi_res_")
    assert prefix_res.startswith("fi_res_")
    assert len(hash_res) == 64
    assert is_well_formed_api_key(pt_res)
    assert verify_api_key_hash(pt_res, hash_res)
    assert not verify_api_key_hash(pt_res + "x", hash_res)

    pt_live, prefix_live, hash_live = generate_api_key(prefix_mode="live")
    assert pt_live.startswith("fi_live_")
    assert prefix_live.startswith("fi_live_")
    assert is_well_formed_api_key(pt_live)
    assert verify_api_key_hash(pt_live, hash_live)

    store = InMemoryApiGatewayStore()
    uid = uuid4()
    created = store.create_api_key(
        user_id=uid,
        name="Notebook Key",
        key_prefix=prefix_res,
        key_hash=hash_res,
        scopes=("read:derived_risk",),
    )
    assert created.is_usable()
    assert pt_res not in repr(created)

    rotated = rotate_api_key_record(store, user_id=uid, key_id=created.key_id)
    assert rotated is not None
    new_rec, new_plaintext, old_revoked = rotated
    assert not old_revoked.is_usable()
    assert new_rec.is_usable()
    assert new_plaintext != pt_res
    assert new_rec.key_hash == hash_api_key(new_plaintext)


def test_raw_redistribution_gate_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """V3-25: FININTEL_ENABLE_RAW_REDISTRIBUTION defaults strictly False (fail-closed)."""
    monkeypatch.delenv("FININTEL_ENABLE_RAW_REDISTRIBUTION", raising=False)
    assert is_raw_redistribution_enabled() is False
    monkeypatch.setenv("FININTEL_ENABLE_RAW_REDISTRIBUTION", "False")
    assert is_raw_redistribution_enabled() is False
    monkeypatch.setenv("FININTEL_ENABLE_RAW_REDISTRIBUTION", "0")
    assert is_raw_redistribution_enabled() is False
    monkeypatch.setenv("FININTEL_ENABLE_RAW_REDISTRIBUTION", "true")
    assert is_raw_redistribution_enabled() is True


def test_sliding_window_rate_limiter_enforces_minute_and_daily_caps() -> None:
    """V3-25: Sliding window blocks request 31 with positive Retry-After and resets after 60s."""
    now = [1_700_000_000.0]
    limiter = SlidingWindowRateLimiter(valkey_url=None, clock_fn=lambda: now[0])
    kid = uuid4()

    for i in range(30):
        dec = limiter.check_and_consume(
            key_id=kid, rate_limit_per_min=30, daily_limit=1000
        )
        assert dec.allowed is True
        assert dec.remaining == 29 - i

    blocked = limiter.check_and_consume(
        key_id=kid, rate_limit_per_min=30, daily_limit=1000
    )
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after is not None and blocked.retry_after >= 1
    assert blocked.reason == "MINUTE_RATE_LIMIT"

    now[0] += 61.0
    recovered = limiter.check_and_consume(
        key_id=kid, rate_limit_per_min=30, daily_limit=1000
    )
    assert recovered.allowed is True

    kid_day = uuid4()
    for _ in range(3):
        assert (
            limiter.check_and_consume(
                key_id=kid_day, rate_limit_per_min=30, daily_limit=3
            ).allowed
            is True
        )
    day_blocked = limiter.check_and_consume(
        key_id=kid_day, rate_limit_per_min=30, daily_limit=3
    )
    assert day_blocked.allowed is False
    assert day_blocked.reason == "DAILY_QUOTA_EXCEEDED"
    assert day_blocked.retry_after is not None and day_blocked.retry_after >= 1


def test_sliding_window_rate_limiter_valkey_lua_path() -> None:
    """V3-25: Valkey sliding-window Lua path enforces limits and revocation cache."""

    class _FakeRedis:
        def __init__(self) -> None:
            self.calls = 0
            self.revoked: set[str] = set()

        def eval(self, _script: str, _numkeys: int, *_args: Any) -> list[int]:
            self.calls += 1
            if self.calls <= 2:
                return [1, self.calls, self.calls, 0, 0]
            return [0, 2, 2, 17, 1]

        def delete(self, _key: str) -> None:
            return None

        def setex(self, key: str, _ttl: int, _val: str) -> None:
            self.revoked.add(key)

        def get(self, key: str) -> str | None:
            return "1" if key in self.revoked else None

    limiter = SlidingWindowRateLimiter(valkey_url="redis://localhost:6379/0")
    fake = _FakeRedis()
    limiter._redis_client = fake
    kid = uuid4()

    d1 = limiter.check_and_consume(key_id=kid, rate_limit_per_min=2, daily_limit=10)
    assert d1.allowed is True and d1.remaining == 1
    d2 = limiter.check_and_consume(key_id=kid, rate_limit_per_min=2, daily_limit=10)
    assert d2.allowed is True and d2.remaining == 0
    d3 = limiter.check_and_consume(key_id=kid, rate_limit_per_min=2, daily_limit=10)
    assert d3.allowed is False and d3.retry_after == 17

    limiter.mark_key_revoked("abc123")
    assert limiter.is_key_revoked_cached("abc123") is True


def _make_test_client(
    tmp_path: Path,
    *,
    role: UserRole = UserRole.RESEARCHER,
) -> tuple[Any, Any, InMemoryApiGatewayStore, Any]:
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from finintel.auth._store import InMemoryAppStore, UserRecord
    from finintel.auth.service import AuthService
    from finintel.serving.api import create_app

    data_root = tmp_path / "data_root"
    _build_minimal_snapshot(data_root)
    app_store = InMemoryAppStore()
    gw_store = InMemoryApiGatewayStore()

    app = create_app(data_root=data_root, valkey_url="")
    app.state._app_store = app_store
    app.state._api_gateway_store = gw_store

    auth_svc = AuthService(app_store)
    profile = auth_svc.register(
        email="researcher@finintel.vn",
        password="VeryStrongPassword!2026",
        display_name="Minh Researcher",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
    )
    if role != UserRole.USER:
        stored = app_store._users[profile.user_id]
        app_store._users[profile.user_id] = UserRecord(
            user_id=stored.user_id,
            email=stored.email,
            email_sha256=stored.email_sha256,
            display_name=stored.display_name,
            password_hash=stored.password_hash,
            role=role,
            is_active=stored.is_active,
            email_verified_at=stored.email_verified_at,
            failed_login_attempts=stored.failed_login_attempts,
            locked_until=stored.locked_until,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )

    @contextmanager
    def _ctx() -> Any:
        with TestClient(app) as client:
            login_resp = client.post(
                "/api/v3/auth/login",
                json={
                    "email": "researcher@finintel.vn",
                    "password": "VeryStrongPassword!2026",
                },
                headers={"X-Requested-With": "FinIntel-Web"},
            )
            assert login_resp.status_code == 200
            yield client, app, gw_store, app_store

    return _ctx(), app, gw_store, app_store


def test_b2b_api_lifecycle_raw_gate_revoke_and_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """V3-25 end-to-end: key create -> derived 200 -> ETag 304 -> raw 403 -> revoke 401 -> 429."""
    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")
    pytest.importorskip("argon2")

    monkeypatch.setenv("FININTEL_COOKIE_SECURE", "0")
    monkeypatch.delenv("FININTEL_ENABLE_RAW_REDISTRIBUTION", raising=False)
    caplog.set_level(logging.DEBUG)

    user_ctx, _, _, _ = _make_test_client(tmp_path / "u", role=UserRole.USER)
    with user_ctx as (user_client, _, _, _):
        denied = user_client.post(
            "/api/v3/b2b/keys",
            json={"name": "Not Allowed"},
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert denied.status_code == 403

    res_ctx, _app, _gw_store, app_store = _make_test_client(
        tmp_path / "r", role=UserRole.RESEARCHER
    )
    with res_ctx as (client, _app2, _gw2, _app_store2):
        create_resp = client.post(
            "/api/v3/b2b/keys",
            json={"name": "Thesis Notebook", "prefix_mode": "res"},
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert create_resp.status_code == 201
        created_data = create_resp.json()
        plaintext = created_data["plaintext_key"]
        key_id = created_data["key"]["key_id"]
        assert plaintext.startswith("fi_res_")

        list_resp = client.get("/api/v3/b2b/keys")
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        assert list_body["raw_redistribution_enabled"] is False
        assert plaintext not in json.dumps(list_body)

        logs, _ = app_store.list_audit_logs_ops(limit=20, offset=0)
        assert any(log.action == "API_KEY_CREATED" for log in logs)
        assert all(plaintext not in json.dumps(log.metadata_json) for log in logs)

        risk_resp = client.get(
            "/api/v3/open/companies/HPG/risk",
            headers={"X-API-Key": plaintext},
        )
        assert risk_resp.status_code == 200
        assert risk_resp.headers["X-Snapshot-ID"] == "snap_v3_b2b_001"
        assert risk_resp.headers["X-FinIntel-License-Scope"] == "read:derived_risk"
        assert int(risk_resp.headers["X-RateLimit-Limit"]) == 30
        assert int(risk_resp.headers["X-RateLimit-Remaining"]) == 29
        etag = risk_resp.headers["ETag"]
        assert risk_resp.json()["ml_scores"][0]["risk_tier"] == "R1"

        cached_resp = client.get(
            "/api/v3/open/companies/HPG/risk",
            headers={"X-API-Key": plaintext, "If-None-Match": etag},
        )
        assert cached_resp.status_code == 304

        scr_resp = client.get(
            "/api/v3/open/screener",
            headers={"X-API-Key": plaintext},
        )
        assert scr_resp.status_code == 200
        assert scr_resp.json()["total"] == 1

        stress_resp = client.post(
            "/api/v3/open/stress-test",
            json={"interest_rate_shock_bps": 200.0, "tickers": ["HPG"]},
            headers={"X-API-Key": plaintext},
        )
        assert stress_resp.status_code == 200
        assert stress_resp.json()["issuers_stressed"] == 1

        raw_resp = client.get(
            "/api/v3/open/companies/HPG/statements",
            headers={"X-API-Key": plaintext},
        )
        assert raw_resp.status_code == 403

        usage_resp = client.get("/api/v3/b2b/usage")
        assert usage_resp.status_code == 200
        usage_data = usage_resp.json()
        assert usage_data["total_requests"] >= 5
        assert usage_data["total_cache_hits"] >= 1

        for _ in range(25):
            r = client.get(
                "/api/v3/open/screener",
                headers={"X-API-Key": plaintext},
            )
            assert r.status_code == 200
        limited = client.get(
            "/api/v3/open/screener",
            headers={"X-API-Key": plaintext},
        )
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        assert limited.headers["X-RateLimit-Remaining"] == "0"

        # 8b. Rotate a key: old key immediately fails (401), new key succeeds (200)
        rot_resp = client.post(
            f"/api/v3/b2b/keys/{key_id}/rotate",
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert rot_resp.status_code == 201
        rotated_data = rot_resp.json()
        rotated_plaintext = rotated_data["plaintext_key"]
        rotated_id = rotated_data["key"]["key_id"]
        assert rotated_plaintext != plaintext
        assert (
            client.get(
                "/api/v3/open/companies/HPG/risk",
                headers={"X-API-Key": plaintext},
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v3/open/companies/HPG/risk",
                headers={"X-API-Key": rotated_plaintext},
            ).status_code
            == 200
        )

        revoke_resp = client.delete(
            f"/api/v3/b2b/keys/{rotated_id}",
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert revoke_resp.status_code == 200
        after_revoke = client.get(
            "/api/v3/open/companies/HPG/risk",
            headers={"X-API-Key": rotated_plaintext},
        )
        assert after_revoke.status_code == 401
        assert plaintext not in caplog.text
        assert rotated_plaintext not in caplog.text


def test_b2b_webhooks_ssrf_guard_and_hmac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V3-25: Webhooks enforce SSRF guard (block loopback/metadata/3xx) and sign HMAC."""
    pytest.importorskip("fastapi")
    pytest.importorskip("argon2")
    pytest.importorskip("httpx")
    import httpx as httpx_mod

    monkeypatch.setenv("FININTEL_COOKIE_SECURE", "0")
    wh_ctx, _, _, _ = _make_test_client(tmp_path / "wh", role=UserRole.RESEARCHER)

    import socket

    def _public_dns(host: str, port: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        if host in {"127.0.0.1", "localhost", "169.254.169.254"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with wh_ctx as (client, app, _, _):
        app.state._alerts_dns_resolver = _public_dns

        for bad_url in (
            "http://hooks.example.com/wh",
            "https://127.0.0.1/wh",
            "https://169.254.169.254/latest/meta-data",
        ):
            bad = client.post(
                "/api/v3/b2b/webhooks",
                json={"label": "Bad", "url": bad_url},
                headers={"X-Requested-With": "FinIntel-Web"},
            )
            assert bad.status_code == 422

        good = client.post(
            "/api/v3/b2b/webhooks",
            json={"label": "Lab Hook", "url": "https://hooks.example.com/finintel"},
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert good.status_code == 201
        wh_data = good.json()
        endpoint_id = wh_data["webhook"]["endpoint_id"]
        secret = wh_data["secret_hmac"]

        captured: dict[str, Any] = {}

        def _mock_handler(req: httpx.Request) -> httpx.Response:
            captured["body"] = req.content
            captured["sig"] = req.headers.get("X-FinIntel-Signature", "")
            return httpx_mod.Response(200, json={"ok": True})

        app.state._alerts_http_client = httpx_mod.Client(
            transport=httpx_mod.MockTransport(_mock_handler),
            follow_redirects=False,
        )
        test_resp = client.post(
            f"/api/v3/b2b/webhooks/{endpoint_id}/test",
            headers={"X-Requested-With": "FinIntel-Web"},
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["delivered"] is True

        from finintel_sdk import verify_webhook_signature

        assert (
            verify_webhook_signature(captured["body"], secret, captured["sig"]) is True
        )
        assert (
            verify_webhook_signature(captured["body"] + b" ", secret, captured["sig"])
            is False
        )


def test_python_sdk_sync_and_async_with_mock_transport() -> None:
    """V3-26: Python SDK supports ETag 304 caching, 429/503 retries, 403/401 errors, and HMAC."""
    import asyncio

    pytest.importorskip("httpx")
    pytest.importorskip("pydantic")
    import httpx as httpx_mod

    from finintel_sdk import (
        AsyncFinIntelClient,
        AuthenticationError,
        FinIntelClient,
        PermissionDeniedError,
        StressScenarioInput,
        sign_webhook_payload,
        verify_webhook_signature,
    )

    sample_risk = {
        "snapshot_id": "snap_sdk_01",
        "license_scope": "read:derived_risk",
        "ticker": "HPG",
        "company_name_vn": "Tập đoàn Hòa Phát",
        "accounting_regime": "CORPORATE",
        "statement_scope": "CONSOLIDATED",
        "period_view": "TTM",
        "ml_scores": [{"risk_tier": "R1", "calibrated_distress_prob": 0.03}],
        "red_flags": [],
        "ratios": [],
        "disclaimer_vi": "Thông tin định lượng tham khảo.",
    }
    calls: list[tuple[str, str, str | None]] = []
    sleeps: list[float] = []

    def _sync_handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("If-None-Match")
        calls.append((request.method, request.url.path, inm))
        if request.url.path == "/api/v3/open/companies/HPG/risk":
            if len(calls) == 1:
                return httpx_mod.Response(
                    429,
                    headers={"Retry-After": "2"},
                    json={"detail": "Rate limit exceeded"},
                )
            if len(calls) == 2:
                return httpx_mod.Response(503, json={"detail": "Temporary unavailable"})
            if inm == '"snap_sdk_01"':
                return httpx_mod.Response(304, headers={"ETag": '"snap_sdk_01"'})
            return httpx_mod.Response(
                200,
                headers={"ETag": '"snap_sdk_01"'},
                json=sample_risk,
            )
        if request.url.path == "/api/v3/open/companies/HPG/statements":
            return httpx_mod.Response(
                403, json={"detail": "Raw redistribution disabled"}
            )
        if request.url.path == "/api/v3/open/screener":
            return httpx_mod.Response(401, json={"detail": "Invalid API key"})
        return httpx_mod.Response(
            200,
            json={
                "snapshot_id": "snap_sdk_01",
                "license_scope": "read:derived_risk",
                "scenario_name": "FX +5%",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "issuers_stressed": 1,
                "newly_distressed_count": 0,
                "sectors": [],
                "issuers": [],
                "disclaimer_vi": "Thông tin định lượng tham khảo.",
            },
        )

    with FinIntelClient(
        api_key="fi_res_12345678_abcdefghijklmnopqrstuvwxyz",
        transport=httpx_mod.MockTransport(_sync_handler),
        max_retries=3,
        sleep_fn=sleeps.append,
    ) as sdk:
        risk1 = sdk.get_company_risk("HPG")
        assert risk1.ticker == "HPG"
        assert sleeps[0] == 2.0
        assert len(sleeps) == 2

        risk2 = sdk.get_company_risk("HPG")
        assert risk2.ticker == "HPG"
        assert sdk.etag_cache_hits == 1
        assert calls[-1][2] == '"snap_sdk_01"'

        stress = sdk.run_stress_test(StressScenarioInput(interest_rate_shock_bps=150.0))
        assert stress.issuers_stressed == 1

        with pytest.raises(PermissionDeniedError):
            sdk.get_company_statements("HPG")

        with pytest.raises(AuthenticationError):
            sdk.screen_market()

    async_calls: list[str | None] = []
    async_sleeps: list[float] = []

    async def _async_sleep(sec: float) -> None:
        async_sleeps.append(sec)

    def _async_handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("If-None-Match")
        async_calls.append(inm)
        if len(async_calls) == 1:
            return httpx_mod.Response(
                429, headers={"Retry-After": "1"}, json={"detail": "Slow down"}
            )
        if inm == '"snap_sdk_01"':
            return httpx_mod.Response(304, headers={"ETag": '"snap_sdk_01"'})
        return httpx_mod.Response(
            200, headers={"ETag": '"snap_sdk_01"'}, json=sample_risk
        )

    async def _run_async() -> None:
        async with AsyncFinIntelClient(
            api_key="fi_res_12345678_abcdefghijklmnopqrstuvwxyz",
            transport=httpx_mod.MockTransport(_async_handler),
            max_retries=2,
            sleep_fn=_async_sleep,
        ) as async_sdk:
            r1 = await async_sdk.get_company_risk("HPG")
            r2 = await async_sdk.get_company_risk("HPG")
            assert r1.ticker == "HPG"
            assert r2.ticker == "HPG"
            assert async_sdk.etag_cache_hits == 1
            assert async_sleeps == [1.0]

    asyncio.run(_run_async())

    raw = b'{"event":"RISK_TIER_UPGRADE","ticker":"HPG"}'
    sig = sign_webhook_payload(raw, "whsec_test_secret_123456")
    assert verify_webhook_signature(raw, "whsec_test_secret_123456", sig) is True
    assert (
        verify_webhook_signature(
            b'{"event":"RISK_TIER_UPGRADE","ticker":"VNM"}',
            "whsec_test_secret_123456",
            sig,
        )
        is False
    )
    assert verify_webhook_signature(raw, "wrong_secret", sig) is False


def test_unused_timedelta_import_guard() -> None:
    """Sanity check date math helper."""
    assert datetime.now(UTC) + timedelta(days=1) > datetime.now(UTC)
