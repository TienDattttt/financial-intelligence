"""Unit and integration tests for V3-16..V3-18: Early Warning Alerts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_ND88_RE = re.compile(
    r"xếp\s+hạng\s+tín\s+nhiệm"
    r"|(?:xếp\s+)?bậc\s+tín\s+nhiệm"
    r"|phân\s+tầng\s+tín\s+nhiệm"
    r"|\btín\s+nhiệm\b"
    r"|khuyến\s+nghị\s+(?:mua|bán)(?:\s*/\s*(?:mua|bán))?",
    re.IGNORECASE,
)


def _make_issuers(tickers: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": [f"ISS_{t}" for t in tickers],
            "ticker": tickers,
            "exchange": ["HOSE" for _ in tickers],
            "company_name_vn": [f"Công ty {t}" for t in tickers],
            "accounting_regime": ["CORPORATE" for _ in tickers],
            "icb_code_level2": ["1700" for _ in tickers],
            "icb_name_level2": ["Tài nguyên" for _ in tickers],
            "icb_code_level3": ["1750" for _ in tickers],
            "icb_name_level3": ["Kim loại" for _ in tickers],
            "industry_snapshot_date": [date(2025, 1, 1) for _ in tickers],
            "delisted_date": [None for _ in tickers],
        },
        schema_overrides={"delisted_date": pl.Date},
    )


def _make_ml_row(
    ticker: str,
    *,
    period_end: date,
    risk_tier: str,
    filing_id: str,
) -> dict[str, object]:
    return {
        "issuer_id": f"ISS_{ticker}",
        "statement_scope": "CONSOLIDATED",
        "period_type": "QUARTER",
        "period_end_date": period_end,
        "filing_id": filing_id,
        "model_role": "PRIMARY",
        "altman_z_double_prime": 1.2,
        "altman_em_score": 1.0,
        "altman_zone": "GREY_ZONE",
        "piotroski_f_score": 4,
        "piotroski_valid_components_count": 8,
        "beneish_m8_score": -2.1,
        "beneish_flag": "NORMAL",
        "pca_pc1": 0.0,
        "pca_pc2": 0.0,
        "pca_pc3": 0.0,
        "cluster_id": 1,
        "cluster_archetype_name": "Balanced",
        "anomaly_score_raw": 0.1,
        "anomaly_percentile_rank": 0.2,
        "statistical_outlier_flag": False,
        "anomaly_flag": "NORMAL",
        "distress_risk_score": 0.3,
        "calibrated_distress_prob": 0.12,
        "risk_tier": risk_tier,
        "risk_market_percentile": 70.0,
        "market_base_rate": 0.04,
        "relative_risk_lift": 3.0,
        "model_version": "v2.0",
        "shap_top_drivers_json": "{}",
    }


def _make_filing_row(
    ticker: str,
    *,
    period_end: date,
    filing_id: str,
    audit_status: str = "AUDITED",
    audit_opinion: str = "UNMODIFIED",
    revision_no: int = 1,
) -> dict[str, object]:
    return {
        "filing_id": filing_id,
        "issuer_id": f"ISS_{ticker}",
        "ticker_at_filing": ticker,
        "source_provider": "fhsc",
        "connector_name": "fhsc_oapi",
        "statement_type": "FULL",
        "statement_scope": "CONSOLIDATED",
        "period_type": "QUARTER",
        "period_end_date": period_end,
        "published_at": date(2026, 4, 20),
        "published_at_basis": "EXACT_FILING",
        "effective_available_at": date(2026, 4, 20),
        "audit_status": audit_status,
        "audit_opinion": audit_opinion,
        "revision_no": revision_no,
        "revision_reason": "ORIGINAL" if revision_no == 1 else "AUDITED_UPDATE",
        "canonical_content_sha256": "a" * 64,
        "payload_file_sha256": "b" * 64,
    }


def _make_red_flag_row(
    ticker: str,
    *,
    period_end: date,
    flag_code: str,
    severity: str,
    title_vn: str,
    filing_id: str,
) -> dict[str, object]:
    return {
        "issuer_id": f"ISS_{ticker}",
        "statement_scope": "CONSOLIDATED",
        "period_type": "QUARTER",
        "period_end_date": period_end,
        "flag_code": flag_code,
        "severity": severity,
        "flag_title_vn": title_vn,
        "explanation_text_vn": "Chi tiết kiểm tra.",
        "evidence_json": "{}",
        "filing_id": filing_id,
    }


def _write_snapshot_dir(
    root: Path,
    *,
    issuers: pl.DataFrame,
    filings: pl.DataFrame,
    ml_scores: pl.DataFrame,
    red_flags: pl.DataFrame,
    regulatory_events: pl.DataFrame | None = None,
    merton_dtd: pl.DataFrame | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    issuers.write_parquet(root / "issuers.parquet")
    filings.write_parquet(root / "filings_manifest.parquet")
    ml_scores.write_parquet(root / "ml_scores_and_explanations.parquet")
    red_flags.write_parquet(root / "accounting_red_flags.parquet")
    if regulatory_events is not None:
        regulatory_events.write_parquet(root / "regulatory_events.parquet")
    if merton_dtd is not None:
        merton_dtd.write_parquet(root / "merton_dtd.parquet")
    return root


def test_alerts_migration_ddl_contracts() -> None:
    """V3-16: app_v3_002_alerts.sql defines alert tables and dedup_key uniqueness."""
    ddl_path = REPO_ROOT / "config" / "migrations" / "app_v3_002_alerts.sql"
    assert ddl_path.is_file()
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in (
        "alert_subscriptions",
        "market_alert_events",
        "user_notifications",
        "webhook_deliveries",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "dedup_key" in ddl
    assert "UNIQUE (user_id, dedup_key)" in ddl
    assert "UNIQUE (user_id, channel, dedup_key)" in ddl


def test_alerts_lazy_imports_without_platform_packages() -> None:
    """Core alerts modules import cleanly when psycopg/redis/fastapi/pydantic are absent."""
    script = (
        "import importlib, sys\n"
        "for pkg in ('psycopg', 'redis', 'fastapi', 'pydantic', 'dagster'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.alerts',\n"
        "    'finintel.alerts.detector',\n"
        "    'finintel.alerts._store',\n"
        "    'finintel.alerts._pg_store',\n"
        "    'finintel.alerts.dispatcher',\n"
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


def test_detector_emits_all_five_event_types_and_zero_for_unchanged(
    tmp_path: Path,
) -> None:
    """V3-16: Detect all 5 alert types across snapshots with 0 events for unchanged tickers."""
    from finintel.alerts.detector import AlertEventType, detect_snapshot_alerts

    issuers = _make_issuers(["AAA", "BBB", "CCC", "DDD", "EEE", "UNCH"])
    p1 = date(2025, 9, 30)
    p2 = date(2025, 12, 31)

    prev_filings = pl.DataFrame(
        [
            _make_filing_row("AAA", period_end=p1, filing_id="FIL_AAA_Q3"),
            _make_filing_row("BBB", period_end=p1, filing_id="FIL_BBB_Q3"),
            _make_filing_row("UNCH", period_end=p1, filing_id="FIL_UNCH_Q3"),
        ]
    )
    curr_filings = pl.DataFrame(
        [
            _make_filing_row("AAA", period_end=p1, filing_id="FIL_AAA_Q3"),
            _make_filing_row(
                "AAA",
                period_end=p2,
                filing_id="FIL_AAA_Q4",
                audit_status="AUDITED",
                revision_no=2,
            ),
            _make_filing_row("BBB", period_end=p1, filing_id="FIL_BBB_Q3"),
            _make_filing_row("UNCH", period_end=p1, filing_id="FIL_UNCH_Q3"),
        ]
    )

    prev_ml = pl.DataFrame(
        [
            _make_ml_row("BBB", period_end=p1, risk_tier="R2", filing_id="FIL_BBB_Q3"),
            _make_ml_row(
                "UNCH", period_end=p1, risk_tier="R2", filing_id="FIL_UNCH_Q3"
            ),
        ]
    )
    curr_ml = pl.DataFrame(
        [
            _make_ml_row("BBB", period_end=p1, risk_tier="R4", filing_id="FIL_BBB_Q3"),
            _make_ml_row(
                "UNCH", period_end=p1, risk_tier="R2", filing_id="FIL_UNCH_Q3"
            ),
        ]
    )

    prev_rf = pl.DataFrame(
        [
            _make_red_flag_row(
                "UNCH",
                period_end=p1,
                flag_code="RF_01_NEGATIVE_OCF_PROFIT",
                severity="MEDIUM",
                title_vn="Dòng tiền HĐKD âm trong khi lợi nhuận dương",
                filing_id="FIL_UNCH_Q3",
            ),
        ]
    )
    curr_rf = pl.DataFrame(
        [
            _make_red_flag_row(
                "UNCH",
                period_end=p1,
                flag_code="RF_01_NEGATIVE_OCF_PROFIT",
                severity="MEDIUM",
                title_vn="Dòng tiền HĐKD âm trong khi lợi nhuận dương",
                filing_id="FIL_UNCH_Q3",
            ),
            _make_red_flag_row(
                "CCC",
                period_end=p2,
                flag_code="RF_10_POST_AUDIT_PROFIT_DOWNGRADE",
                severity="HIGH",
                title_vn="Lợi nhuận sau kiểm toán giảm mạnh so với báo cáo tự lập",
                filing_id="FIL_CCC_Q4",
            ),
        ]
    )

    prev_reg = pl.DataFrame(
        schema={
            "issuer_id": pl.String,
            "event_date": pl.Date,
            "effective_from": pl.Date,
            "resolution_date": pl.Date,
            "event_type": pl.String,
            "event_stage": pl.String,
            "exchange_delisted_from": pl.String,
            "source_provider": pl.String,
            "source_url": pl.String,
            "raw_headline": pl.String,
            "payload_sha256": pl.String,
        }
    )
    curr_reg = pl.DataFrame(
        [
            {
                "issuer_id": "ISS_DDD",
                "event_date": date(2026, 4, 21),
                "effective_from": date(2026, 4, 22),
                "resolution_date": None,
                "event_type": "CONTROL",
                "event_stage": "FIRST_ENTRY",
                "exchange_delisted_from": None,
                "source_provider": "hsx",
                "source_url": "https://www.hsx.vn/notice/123",
                "raw_headline": "Đưa cổ phiếu DDD vào diện kiểm soát",
                "payload_sha256": "c" * 64,
            }
        ],
        schema=prev_reg.schema,
    )

    prev_dtd = pl.DataFrame(
        {
            "issuer_id": ["ISS_EEE", "ISS_UNCH"],
            "trade_date": [date(2026, 4, 18), date(2026, 4, 18)],
            "merton_dtd": [1.85, 2.40],
            "dtd_flag": ["VALID", "VALID"],
        }
    )
    curr_dtd = pl.DataFrame(
        {
            "issuer_id": ["ISS_EEE", "ISS_UNCH"],
            "trade_date": [date(2026, 4, 21), date(2026, 4, 21)],
            "merton_dtd": [1.12, 2.38],
            "dtd_flag": ["VALID", "VALID"],
        }
    )

    prev_dir = _write_snapshot_dir(
        tmp_path / "snap_1",
        issuers=issuers,
        filings=prev_filings,
        ml_scores=prev_ml,
        red_flags=prev_rf,
        regulatory_events=prev_reg,
        merton_dtd=prev_dtd,
    )
    curr_dir = _write_snapshot_dir(
        tmp_path / "snap_2",
        issuers=issuers,
        filings=curr_filings,
        ml_scores=curr_ml,
        red_flags=curr_rf,
        regulatory_events=curr_reg,
        merton_dtd=curr_dtd,
    )

    fixed_now = datetime(2026, 4, 22, 7, 0, tzinfo=UTC)
    events = detect_snapshot_alerts(
        prev_dir,
        curr_dir,
        snapshot_id="snap_2",
        detected_at=fixed_now,
    )

    assert all(ev.ticker != "UNCH" for ev in events)
    by_pair = {(ev.ticker, ev.event_type): ev for ev in events}
    assert ("AAA", AlertEventType.NEW_FILING_PUBLISHED) in by_pair
    assert ("BBB", AlertEventType.RISK_TIER_UPGRADE) in by_pair
    assert ("CCC", AlertEventType.NEW_RED_FLAG_TRIGGERED) in by_pair
    assert ("DDD", AlertEventType.REGULATORY_EVENT_ISSUED) in by_pair
    assert ("EEE", AlertEventType.MERTON_DTD_BREACH) in by_pair
    assert len(events) == 5

    # RF_10 and R4 upgrade carry HIGH/CRITICAL severity; compliance wording strictly enforced.
    assert (
        by_pair[("CCC", AlertEventType.NEW_RED_FLAG_TRIGGERED)].severity == "CRITICAL"
    )
    assert by_pair[("BBB", AlertEventType.RISK_TIER_UPGRADE)].severity == "HIGH"
    for ev in events:
        assert not _FORBIDDEN_ND88_RE.search(ev.title_vi), ev.title_vi
        assert len(ev.dedup_key) == 64
        assert ev.evidence["company_url"] == f"/companies/{ev.ticker}"

    # Repeated detection over the same pair produces identical dedup_keys (idempotent).
    events_repeat = detect_snapshot_alerts(
        prev_dir,
        curr_dir,
        snapshot_id="snap_2",
        detected_at=fixed_now,
    )
    assert [e.dedup_key for e in events_repeat] == [e.dedup_key for e in events]

    # Identical snapshot vs itself emits zero events.
    assert (
        detect_snapshot_alerts(
            curr_dir,
            curr_dir,
            snapshot_id="snap_2",
            detected_at=fixed_now,
        )
        == []
    )
    # Initial snapshot (prev_snapshot=None) emits zero events.
    assert (
        detect_snapshot_alerts(
            None,
            curr_dir,
            snapshot_id="snap_2",
            detected_at=fixed_now,
        )
        == []
    )
    # Already-breached DTD in prev does not re-trigger on unchanged breach.
    breached_prev = pl.DataFrame(
        {
            "issuer_id": ["ISS_EEE"],
            "trade_date": [date(2026, 4, 18)],
            "merton_dtd": [1.20],
            "dtd_flag": ["VALID"],
        }
    )
    breached_curr = pl.DataFrame(
        {
            "issuer_id": ["ISS_EEE"],
            "trade_date": [date(2026, 4, 21)],
            "merton_dtd": [1.10],
            "dtd_flag": ["VALID"],
        }
    )
    assert (
        detect_snapshot_alerts(
            {"issuers.parquet": issuers},
            {"issuers.parquet": issuers},
            snapshot_id="snap_3",
            prev_merton_dtd=breached_prev,
            curr_merton_dtd=breached_curr,
            detected_at=fixed_now,
        )
        == []
    )


def test_dispatcher_ssrf_blocks_private_loopback_metadata_and_redirects() -> None:
    """V3-17: Outgoing Webhook SSRF guard rejects private IPs, metadata IPs, and 3xx redirects."""
    from finintel.alerts.dispatcher import (
        WebhookSSRFError,
        send_signed_webhook,
        validate_webhook_url,
    )

    def _public_resolver(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def _private_dns_resolver(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    for bad_url in (
        "http://hooks.example.com/alert",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/hook",
        "https://localhost/hook",
        "https://hooks.example.com:8443/hook",
        "https://user:pass@hooks.example.com/hook",
    ):
        with pytest.raises(WebhookSSRFError):
            validate_webhook_url(bad_url, resolver=_public_resolver)

    with pytest.raises(WebhookSSRFError):
        validate_webhook_url(
            "https://rebind.example.com/hook", resolver=_private_dns_resolver
        )

    redirect_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )
    )
    with httpx.Client(transport=redirect_transport, follow_redirects=False) as client:
        with pytest.raises(WebhookSSRFError):
            send_signed_webhook(
                url="https://hooks.example.com/alert",
                payload={"event": "test"},
                secret="my-hmac-secret",
                client=client,
                resolver=_public_resolver,
            )


def test_dispatcher_hmac_signature_matches_raw_payload_bytes() -> None:
    """V3-17: X-FinIntel-Signature sha256=... matches HMAC-SHA256 over raw HTTP bytes."""
    from finintel.alerts.dispatcher import (
        send_signed_webhook,
        verify_webhook_signature,
    )

    captured_body: list[bytes] = []
    captured_sig: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_body.append(request.content)
        captured_sig.append(request.headers.get("X-FinIntel-Signature", ""))
        return httpx.Response(200, json={"ok": True})

    def _public_resolver(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    secret = "whsec_finintel_test_secret_123"
    with httpx.Client(
        transport=httpx.MockTransport(_handler), follow_redirects=False
    ) as client:
        res = send_signed_webhook(
            url="https://hooks.example.com/finintel",
            payload={"ticker": "HPG", "severity": "HIGH", "title_vi": "Cảnh báo"},
            secret=secret,
            client=client,
            resolver=_public_resolver,
        )

    assert res.delivered is True
    assert res.http_status == 200
    assert len(captured_body) == 1
    expected_hex = hmac.new(
        secret.encode("utf-8"), captured_body[0], hashlib.sha256
    ).hexdigest()
    assert captured_sig[0] == f"sha256={expected_hex}"
    assert verify_webhook_signature(captured_body[0], secret, captured_sig[0]) is True
    assert (
        verify_webhook_signature(captured_body[0] + b" ", secret, captured_sig[0])
        is False
    )


def test_dispatcher_consent_idempotency_retry_dead_letter_and_daily_digest(
    tmp_path: Path,
) -> None:
    """V3-17: Enforce PDPL consent, idempotent fan-out, Telegram, Daily Digest cap, and dead-letter."""
    _ = tmp_path
    from finintel.alerts._store import AlertSubscriptionUpsert, InMemoryAlertStore
    from finintel.alerts.detector import AlertEvent, AlertEventType
    from finintel.alerts.dispatcher import AlertDispatcher
    from finintel.auth._store import InMemoryAppStore
    from finintel.auth.rate_limit import AuthRateLimiter
    from finintel.auth.service import AuthService
    from finintel.domain.compliance import CURRENT_CONSENT_POLICY_VERSION
    from finintel.workspace.service import WorkspaceService

    app_store = InMemoryAppStore()
    auth_svc = AuthService(app_store, rate_limiter=AuthRateLimiter())
    ws_svc = WorkspaceService(app_store)
    alert_store = InMemoryAlertStore(app_store=app_store)

    # User 1: has ALERT_NOTIFICATIONS consent + HPG in watchlist + In-App, Telegram, Email Digest, Webhook
    u1 = auth_svc.register(
        email="u1@finintel.local",
        password="StrongPassword!123",
        display_name="Analyst 1",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
        accept_alert_notifications=True,
    )
    login_u1 = auth_svc.login(email="u1@finintel.local", password="StrongPassword!123")
    auth_u1 = auth_svc.authenticate_session_token(login_u1.session_token)
    assert auth_u1 is not None
    wl_u1 = ws_svc.create_watchlist(user=auth_u1, name="Danh mục chính")
    ws_svc.add_ticker(user=auth_u1, watchlist_id=wl_u1.watchlist_id, ticker="HPG")
    alert_store.upsert_subscription(
        AlertSubscriptionUpsert(
            user_id=u1.user_id,
            watchlist_id=None,
            event_types=(
                AlertEventType.RISK_TIER_UPGRADE,
                AlertEventType.NEW_RED_FLAG_TRIGGERED,
            ),
            in_app_enabled=True,
            email_digest_enabled=True,
            email_frequency="DAILY_DIGEST",
            telegram_enabled=True,
            telegram_chat_id="99887766",
            webhook_enabled=True,
            webhook_url="https://hooks.example.com/u1",
            webhook_secret_hmac="sec_u1",
        )
    )

    # User 2: watches HPG, but lacks ConsentPurpose.ALERT_NOTIFICATIONS
    u2 = auth_svc.register(
        email="u2@finintel.local",
        password="StrongPassword!123",
        display_name="Analyst 2",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
        accept_alert_notifications=False,
    )
    login_u2 = auth_svc.login(email="u2@finintel.local", password="StrongPassword!123")
    auth_u2 = auth_svc.authenticate_session_token(login_u2.session_token)
    assert auth_u2 is not None
    wl_u2 = ws_svc.create_watchlist(user=auth_u2, name="Danh mục chính")
    ws_svc.add_ticker(user=auth_u2, watchlist_id=wl_u2.watchlist_id, ticker="HPG")
    alert_store.upsert_subscription(
        AlertSubscriptionUpsert(
            user_id=u2.user_id,
            watchlist_id=None,
            event_types=(AlertEventType.RISK_TIER_UPGRADE,),
            in_app_enabled=True,
            email_digest_enabled=True,
            telegram_enabled=True,
            telegram_chat_id="112233",
        )
    )

    ev1 = AlertEvent(
        dedup_key="1" * 64,
        snapshot_id="snap_2",
        ticker="HPG",
        event_type=AlertEventType.RISK_TIER_UPGRADE,
        severity="HIGH",
        title_vi="HPG: Nhóm cảnh báo rủi ro định lượng chuyển từ R2 lên R4",
        evidence={
            "previous_tier": "R2",
            "current_tier": "R4",
            "company_url": "/companies/HPG",
        },
        detected_at=datetime(2026, 4, 22, 8, 0, tzinfo=UTC),
    )
    ev2 = AlertEvent(
        dedup_key="2" * 64,
        snapshot_id="snap_2",
        ticker="HPG",
        event_type=AlertEventType.NEW_RED_FLAG_TRIGGERED,
        severity="CRITICAL",
        title_vi="HPG: Phát sinh dấu hiệu cảnh báo kế toán mới",
        evidence={
            "flag_code": "RF_10_POST_AUDIT_PROFIT_DOWNGRADE",
            "company_url": "/companies/HPG",
        },
        detected_at=datetime(2026, 4, 22, 8, 0, tzinfo=UTC),
    )

    telegram_calls: list[tuple[str, str]] = []
    email_calls: list[tuple[str, str, str]] = []
    webhook_attempts = 0
    sleep_delays: list[float] = []

    def _public_resolver(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def _webhook_handler(request: httpx.Request) -> httpx.Response:
        nonlocal webhook_attempts
        webhook_attempts += 1
        # Fail ev1 all 3 attempts (dead-letter); succeed ev2 on 2nd attempt!
        body = json.loads(request.content.decode("utf-8"))
        if body["dedup_key"] == ev1.dedup_key:
            return httpx.Response(503, text="upstream down")
        if webhook_attempts == 4:
            return httpx.Response(502, text="transient")
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        transport=httpx.MockTransport(_webhook_handler), follow_redirects=False
    ) as client:
        dispatcher = AlertDispatcher(
            alert_store=alert_store,
            http_client=client,
            dns_resolver=_public_resolver,
            telegram_sender=lambda chat_id, text: telegram_calls.append(
                (chat_id, text)
            ),
            email_sender=lambda to_email, subject, body: email_calls.append(
                (to_email, subject, body)
            ),
            telegram_bot_token="tg-token",
            resend_api_key="re-key",
            sleep_fn=sleep_delays.append,
            max_daily_emails=80,
        )
        summary_1 = dispatcher.dispatch_events([ev1, ev2])
        # Second dispatch with exact same events must emit 0 duplicates across all channels.
        summary_2 = dispatcher.dispatch_events([ev1, ev2])

    assert summary_1.in_app_created == 2
    assert summary_1.telegram_sent == 2
    assert summary_1.email_digests_sent == 1  # Aggregated 2 events into 1 daily digest!
    assert summary_1.webhooks_sent == 1
    assert summary_1.webhooks_dead_letter == 1
    assert summary_1.skipped_no_consent >= 1

    assert summary_2.in_app_created == 0
    assert summary_2.telegram_sent == 0
    assert summary_2.email_digests_sent == 0
    assert summary_2.webhooks_sent == 0

    # U2 has zero notifications because ALERT_NOTIFICATIONS consent was not granted.
    u1_notifs, u1_total, u1_unread = alert_store.list_notifications(u1.user_id)
    u2_notifs, u2_total, _ = alert_store.list_notifications(u2.user_id)
    assert u1_total == 2 and u1_unread == 2 and len(u1_notifs) == 2
    assert u2_total == 0 and len(u2_notifs) == 0

    # Verify Daily Digest aggregated both alerts + legal disclaimer.
    assert len(email_calls) == 1
    assert email_calls[0][0] == "u1@finintel.local"
    assert "/companies/HPG" in email_calls[0][2]
    assert "Nghị định 88/2014/NĐ-CP" in email_calls[0][2]

    # Verify Telegram received both alerts.
    assert len(telegram_calls) == 2
    assert all(chat == "99887766" for chat, _ in telegram_calls)

    # Verify retry backoff occurred and dead-letter status was recorded.
    assert len(sleep_delays) >= 2
    deliveries = alert_store.list_deliveries(u1.user_id)
    dl_statuses = {d.dedup_key: d.status for d in deliveries if d.channel == "WEBHOOK"}
    assert dl_statuses[ev1.dedup_key] == "DEAD_LETTER"
    assert dl_statuses[ev2.dedup_key] == "SENT"

    # Verify daily email quota (< 80/day) protection stops outbound email once cap is reached.
    ev3 = AlertEvent(
        dedup_key="3" * 64,
        snapshot_id="snap_3",
        ticker="HPG",
        event_type=AlertEventType.RISK_TIER_UPGRADE,
        severity="HIGH",
        title_vi="HPG: Nhóm cảnh báo rủi ro định lượng chuyển từ R4 lên R5",
        evidence={"company_url": "/companies/HPG"},
        detected_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        follow_redirects=False,
    ) as client_ok:
        capped_dispatcher = AlertDispatcher(
            alert_store=alert_store,
            http_client=client_ok,
            dns_resolver=_public_resolver,
            telegram_sender=lambda chat_id, text: telegram_calls.append(
                (chat_id, text)
            ),
            email_sender=lambda to_email, subject, body: email_calls.append(
                (to_email, subject, body)
            ),
            telegram_bot_token="tg-token",
            resend_api_key="re-key",
            sleep_fn=sleep_delays.append,
            max_daily_emails=1,  # Already sent 1 digest today!
        )
        capped_summary = capped_dispatcher.dispatch_events([ev3])
    assert capped_summary.email_digests_sent == 0
    assert capped_summary.email_skipped_quota == 1
    assert len(email_calls) == 1


def test_alerts_router_endpoints_idor_ssrf_and_mark_read(tmp_path: Path) -> None:
    """V3-18: Alerts HTTP router enforces auth, CSRF, zero IDOR, SSRF check, and mark-read."""
    from fastapi.testclient import TestClient

    from finintel.alerts._store import InMemoryAlertStore
    from finintel.alerts.detector import AlertEvent, AlertEventType
    from finintel.alerts.dispatcher import AlertDispatcher, verify_webhook_signature
    from finintel.auth._store import InMemoryAppStore
    from finintel.auth.rate_limit import AuthRateLimiter
    from finintel.auth.service import AuthService
    from finintel.domain.compliance import CURRENT_CONSENT_POLICY_VERSION
    from finintel.pipeline.state_store import StateStore
    from finintel.serving.api import create_app
    from finintel.storage.snapshot_publisher import SnapshotPublisher
    from finintel.workspace.service import WorkspaceService

    data_root = tmp_path / "data_alerts_api"
    issuers = _make_issuers(["HPG"])
    snapshots_root = data_root / "published" / "snapshots"
    st = StateStore(data_root / "metadata" / "state.sqlite")
    st.initialize()
    st.start_run("run-alerts", "CLI_MANUAL", {}, None)
    SnapshotPublisher(snapshots_root, state_store=st).publish(
        "snap_alerts_api", {"issuers.parquet": issuers}, run_id="run-alerts"
    )

    app_store = InMemoryAppStore()
    alert_store = InMemoryAlertStore(app_store=app_store)
    captured_webhooks: list[tuple[bytes, str]] = []

    def _public_resolver(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def _wh_handler(request: httpx.Request) -> httpx.Response:
        captured_webhooks.append(
            (request.content, request.headers.get("X-FinIntel-Signature", ""))
        )
        return httpx.Response(200, json={"received": True})

    wh_client = httpx.Client(
        transport=httpx.MockTransport(_wh_handler), follow_redirects=False
    )

    app = create_app(data_root=data_root)
    app.state._app_store = app_store
    app.state._alert_store = alert_store
    app.state._alerts_http_client = wh_client
    app.state._alerts_dns_resolver = _public_resolver

    auth_svc = AuthService(app_store, rate_limiter=AuthRateLimiter())
    ws_svc = WorkspaceService(app_store)

    u1 = auth_svc.register(
        email="alice@finintel.local",
        password="StrongPassword!123",
        display_name="Alice",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
        accept_alert_notifications=True,
    )
    u2 = auth_svc.register(
        email="bob@finintel.local",
        password="StrongPassword!123",
        display_name="Bob",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
        accept_alert_notifications=True,
    )
    tok1 = auth_svc.login(
        email="alice@finintel.local", password="StrongPassword!123"
    ).session_token
    tok2 = auth_svc.login(
        email="bob@finintel.local", password="StrongPassword!123"
    ).session_token
    auth_u1 = auth_svc.authenticate_session_token(tok1)
    assert auth_u1 is not None
    wl1 = ws_svc.create_watchlist(user=auth_u1, name="Chính")
    ws_svc.add_ticker(user=auth_u1, watchlist_id=wl1.watchlist_id, ticker="HPG")

    dispatcher = AlertDispatcher(alert_store=alert_store)
    dispatcher.dispatch_events(
        [
            AlertEvent(
                dedup_key="a" * 64,
                snapshot_id="snap_alerts_api",
                ticker="HPG",
                event_type=AlertEventType.RISK_TIER_UPGRADE,
                severity="HIGH",
                title_vi="HPG: Nhóm cảnh báo rủi ro định lượng chuyển từ R2 lên R4",
                evidence={"company_url": "/companies/HPG"},
                detected_at=datetime(2026, 4, 22, 8, 0, tzinfo=UTC),
            )
        ]
    )

    csrf = {"X-Requested-With": "FinIntel-Web"}
    with TestClient(app) as client:
        # Unauthenticated rejected cleanly
        assert client.get("/api/v3/alerts/notifications").status_code == 401

        # Alice sees 1 unread notification
        client.cookies.set("finintel_sid", tok1)
        res_list = client.get("/api/v3/alerts/notifications?unread_only=true")
        assert res_list.status_code == 200
        body = res_list.json()
        assert body["unread_count"] == 1
        assert body["total"] == 1
        notif_id = body["items"][0]["notification_id"]

        # Bob cannot read or mark Alice's notification (zero IDOR -> 404)
        client.cookies.set("finintel_sid", tok2)
        bob_list = client.get("/api/v3/alerts/notifications")
        assert bob_list.status_code == 200
        assert bob_list.json()["total"] == 0
        bob_mark = client.post(
            f"/api/v3/alerts/notifications/{notif_id}/read", headers=csrf
        )
        assert bob_mark.status_code == 404

        # Alice updates subscription: SSRF URL rejected with 422
        client.cookies.set("finintel_sid", tok1)
        bad_sub = client.put(
            "/api/v3/alerts/subscriptions",
            headers=csrf,
            json={
                "webhook_enabled": True,
                "webhook_url": "http://127.0.0.1:8080/hook",
                "webhook_secret_hmac": "sec123456",
            },
        )
        assert bad_sub.status_code == 422

        # Valid HTTPS subscription saved without leaking secret back
        good_sub = client.put(
            "/api/v3/alerts/subscriptions",
            headers=csrf,
            json={
                "in_app_enabled": True,
                "telegram_enabled": True,
                "telegram_chat_id": "554433",
                "email_digest_enabled": True,
                "email_frequency": "DAILY_DIGEST",
                "webhook_enabled": True,
                "webhook_url": "https://hooks.example.com/alice",
                "webhook_secret_hmac": "alice_secret_key_99",
                "merton_dtd_threshold": 1.4,
            },
        )
        assert good_sub.status_code == 200
        sub_json = good_sub.json()
        assert sub_json["webhook_secret_configured"] is True
        assert "alice_secret_key_99" not in json.dumps(sub_json)
        assert sub_json["has_alert_consent"] is True

        # Webhook test sends signed payload; SSRF blocked on test too
        bad_test = client.post(
            "/api/v3/alerts/webhooks/test",
            headers=csrf,
            json={"webhook_url": "https://169.254.169.254/latest"},
        )
        assert bad_test.status_code == 422

        ok_test = client.post(
            "/api/v3/alerts/webhooks/test",
            headers=csrf,
            json={},
        )
        assert ok_test.status_code == 200
        assert ok_test.json()["delivered"] is True
        assert len(captured_webhooks) == 1
        raw_sent, sig_sent = captured_webhooks[0]
        assert verify_webhook_signature(raw_sent, "alice_secret_key_99", sig_sent)

        # Mark notification as read & verify unread_count drops to 0
        mark_one = client.post(
            f"/api/v3/alerts/notifications/{notif_id}/read", headers=csrf
        )
        assert mark_one.status_code == 200
        assert mark_one.json()["is_read"] is True
        assert (
            client.get("/api/v3/alerts/notifications?unread_only=true").json()[
                "unread_count"
            ]
            == 0
        )

        # Mark-all read works cleanly
        mark_all = client.post("/api/v3/alerts/notifications/read-all", headers=csrf)
        assert mark_all.status_code == 200
        assert mark_all.json()["status"] == "OK"

    wh_client.close()
    _ = u1, u2
