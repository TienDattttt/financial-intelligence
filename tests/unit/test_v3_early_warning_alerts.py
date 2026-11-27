"""Unit and integration tests for V3-16..V3-18: Early Warning Alerts."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

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
