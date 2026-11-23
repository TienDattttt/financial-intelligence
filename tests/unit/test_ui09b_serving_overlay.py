"""Unit and integration tests for UI-09b: GET /api/v2/companies/{ticker}/market-overlay."""

from __future__ import annotations

import hashlib
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from finintel.analytics.tables import build_analytics_tables  # noqa: E402
from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.serving.api import create_app  # noqa: E402
from finintel.serving.schemas import CompanyMarketOverlayResponse  # noqa: E402
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    MARKET_PRICES_SCHEMA,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    REGULATORY_EVENTS_SCHEMA,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_DATA_DIR = Path("d:/KiCuoiCung/DATN/financial-intelligence/data")
_REAL_SNAPSHOT_ROOT = (
    _REPO_ROOT / "data" / "live_fhsc_universe_full"
    if (
        _REPO_ROOT / "data" / "live_fhsc_universe_full" / "published" / "snapshots"
    ).exists()
    else _MAIN_DATA_DIR / "live_fhsc_universe_full"
)
_REAL_MARKET_PRICES = (
    _REAL_SNAPSHOT_ROOT.parent / "market_prices" / "market_prices.parquet"
)
_REAL_REG_EVENTS = (
    _REAL_SNAPSHOT_ROOT.parent / "regulatory_ledger" / "regulatory_events.parquet"
)
_HAS_REAL_UNIVERSE = (
    (_REAL_SNAPSHOT_ROOT / "published" / "snapshots").exists()
    and _REAL_MARKET_PRICES.exists()
    and _REAL_REG_EVENTS.exists()
)


def _sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 1:
        end = date(year, 3, 31)
    elif quarter == 2:
        end = date(year, 6, 30)
    elif quarter == 3:
        end = date(year, 9, 30)
    else:
        end = date(year, 12, 31)
    return start, end


def _build_overlay_fixture_tables() -> tuple[
    dict[str, pl.DataFrame], pl.DataFrame, pl.DataFrame
]:
    issuers = pl.DataFrame(
        {
            "issuer_id": ["FPT", "HBC", "NOPX"],
            "ticker": ["FPT", "HBC", "NOPX"],
            "exchange": ["HOSE", "HOSE", "UPCOM"],
            "company_name_vn": [
                "Công ty Cổ phần FPT",
                "Tập đoàn Xây dựng Hòa Bình",
                "Công ty Không Có Giá",
            ],
            "accounting_regime": ["CORPORATE", "CORPORATE", "CORPORATE"],
            "icb_code_level2": ["9500", "2300", "2700"],
            "icb_name_level2": ["Công nghệ thông tin", "Xây dựng", "Công nghiệp"],
            "icb_code_level3": ["9537", "2357", "2790"],
            "icb_name_level3": ["Phần mềm", "Xây dựng", "Dịch vụ"],
            "industry_snapshot_date": [date(2020, 1, 1)] * 3,
            "delisted_date": [None, None, None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )

    # 8 quarters from 2022Q3 to 2024Q2 so prepare_periods / TTM has full history.
    fpt_quarters: list[tuple[int, int, date, float, float]] = [
        (2022, 3, date(2022, 10, 28), 125_000_000_000.0, 100_000_000.0),
        (2022, 4, date(2023, 1, 27), 125_000_000_000.0, 100_000_000.0),
        (2023, 1, date(2023, 4, 28), 125_000_000_000.0, 100_000_000.0),
        (2023, 2, date(2023, 7, 28), 125_000_000_000.0, 100_000_000.0),
        (2023, 3, date(2023, 10, 27), 125_000_000_000.0, 100_000_000.0),
        (2023, 4, date(2024, 1, 26), 125_000_000_000.0, 100_000_000.0),
        # Q1 2024 disclosed on Sat 2024-04-27 -> forward-aligned to Mon 2024-04-29
        (2024, 1, date(2024, 4, 27), 125_000_000_000.0, 100_000_000.0),
        # Q2 2024 post-4x-split shares 400M, NI_Q=250B -> TTM=625B -> wait keep TTM=500B for clean math
        (2024, 2, date(2024, 7, 26), 125_000_000_000.0, 400_000_000.0),
    ]
    hbc_quarters: list[tuple[int, int, date, float, float]] = [
        (2022, 3, date(2022, 10, 28), 10_000_000_000.0, 50_000_000.0),
        (
            2022,
            4,
            date(2023, 4, 1),
            -80_000_000_000.0,
            50_000_000.0,
        ),  # Sat -> Mon 2023-04-03 (R4)
        (2023, 1, date(2023, 5, 2), -90_000_000_000.0, 50_000_000.0),  # R4 repeat
        (
            2023,
            2,
            date(2023, 7, 3),
            -120_000_000_000.0,
            50_000_000.0,
        ),  # Mon 2023-07-03 (R5)
        (2023, 3, date(2023, 10, 30), -100_000_000_000.0, 50_000_000.0),  # R4 downgrade
        (2023, 4, date(2024, 1, 29), -100_000_000_000.0, 50_000_000.0),
        (2024, 1, date(2024, 4, 29), -100_000_000_000.0, 50_000_000.0),
        (2024, 2, date(2024, 7, 29), -100_000_000_000.0, 50_000_000.0),
    ]

    fact_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []

    def _append_company_facts(
        issuer: str, entries: list[tuple[int, int, date, float, float]], equity: float
    ) -> None:
        for fy, fq, pub, ni_q, shares_cnt in entries:
            pstart, pend = _quarter_dates(fy, fq)
            fid = f"{issuer}_{fy}Q{fq}"
            manifest_rows.append(
                {
                    "filing_id": fid,
                    "issuer_id": issuer,
                    "ticker_at_filing": issuer,
                    "source_provider": "TEST",
                    "connector_name": "fixture",
                    "statement_type": "INCOME_STATEMENT",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "period_end_date": pend,
                    "published_at": pub,
                    "published_at_basis": "EXACT_FILING",
                    "effective_available_at": pub,
                    "audit_status": "UNAUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "revision_no": 1,
                    "revision_reason": "ORIGINAL",
                    "canonical_content_sha256": _sha256(f"c_{fid}"),
                    "payload_file_sha256": _sha256(f"r_{fid}"),
                }
            )
            metrics_map: dict[str, tuple[str, float]] = {
                "BS_TOTAL_ASSETS": ("BALANCE_SHEET", equity * 2.0),
                "BS_CURRENT_ASSETS": ("BALANCE_SHEET", equity * 0.9),
                "BS_CASH_AND_EQUIVALENTS": ("BALANCE_SHEET", equity * 0.2),
                "BS_SHORT_TERM_INVESTMENTS": ("BALANCE_SHEET", equity * 0.05),
                "BS_TRADE_RECEIVABLES_ST": ("BALANCE_SHEET", equity * 0.25),
                "BS_INVENTORIES": ("BALANCE_SHEET", equity * 0.3),
                "BS_CURRENT_LIABILITIES": ("BALANCE_SHEET", equity * 0.6),
                "BS_SHORT_TERM_BORROWINGS": ("BALANCE_SHEET", equity * 0.2),
                "BS_LONG_TERM_BORROWINGS": ("BALANCE_SHEET", equity * 0.2),
                "BS_TOTAL_LIABILITIES": ("BALANCE_SHEET", equity),
                "BS_TOTAL_EQUITY": ("BALANCE_SHEET", equity),
                "BS_MINORITY_INTEREST": ("BALANCE_SHEET", 0.0),
                "BS_TRADE_PAYABLES_ST": ("BALANCE_SHEET", equity * 0.15),
                "BS_ORDINARY_SHARES_CAPITAL": ("BALANCE_SHEET", shares_cnt * 10_000.0),
                "BS_TREASURY_SHARES": ("BALANCE_SHEET", 0.0),
                "IS_NET_REVENUE": ("INCOME_STATEMENT", 1_000_000_000_000.0),
                "IS_COST_OF_GOODS_SOLD": ("INCOME_STATEMENT", -700_000_000_000.0),
                "IS_GROSS_PROFIT": ("INCOME_STATEMENT", 300_000_000_000.0),
                "IS_SELLING_EXPENSES": ("INCOME_STATEMENT", -50_000_000_000.0),
                "IS_ADMINISTRATIVE_EXPENSES": ("INCOME_STATEMENT", -50_000_000_000.0),
                "IS_OPERATING_PROFIT": ("INCOME_STATEMENT", 200_000_000_000.0),
                "IS_PROFIT_BEFORE_TAX": ("INCOME_STATEMENT", ni_q * 1.25),
                "IS_INTEREST_EXPENSE": ("INCOME_STATEMENT", -10_000_000_000.0),
                "IS_CURRENT_INCOME_TAX": (
                    "INCOME_STATEMENT",
                    -ni_q * 0.25 if ni_q > 0 else 0.0,
                ),
                "IS_NET_INCOME": ("INCOME_STATEMENT", ni_q),
                "IS_NET_INCOME_PARENT": ("INCOME_STATEMENT", ni_q),
                "CF_OPERATING_CASH_FLOW": ("CASH_FLOW", ni_q * 1.1),
                "CF_DEPRECIATION_AMORTIZATION": ("CASH_FLOW", 20_000_000_000.0),
                "CF_CAPITAL_EXPENDITURE": ("CASH_FLOW", -30_000_000_000.0),
            }
            for mid, (stype, val) in metrics_map.items():
                fact_rows.append(
                    {
                        "issuer_id": issuer,
                        "ticker": issuer,
                        "ticker_at_filing": issuer,
                        "accounting_regime": "CORPORATE",
                        "statement_scope": "CONSOLIDATED",
                        "statement_type": stype,
                        "period_type": "QUARTER",
                        "fiscal_year": fy,
                        "fiscal_quarter": fq,
                        "fiscal_year_start_date": date(fy, 1, 1),
                        "period_start_date": pstart,
                        "period_end_date": pend,
                        "metric_id": mid,
                        "value": float(val),
                        # Leave ttm_value None to also verify real-data TTM computation path via prepare_periods.
                        "ttm_value": None,
                        "unit": "VND",
                        "value_type": "STOCK" if stype == "BALANCE_SHEET" else "FLOW",
                        "sign_convention": "SIGNED",
                        "comparable_across_taxonomy": True,
                        "taxonomy_version": "VAS_TT200_2014",
                        "derivation_method": "REPORTED_DIRECTLY",
                        "filing_id": fid,
                        "revision_no": 1,
                        "revision_reason": "ORIGINAL",
                        "audit_status": "UNAUDITED",
                        "audit_opinion": "UNMODIFIED",
                        "source_provider": "TEST",
                        "published_at": pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": pub,
                        "is_filed_late": False,
                        "source_filing_ids": [fid],
                    }
                )

    _append_company_facts("FPT", fpt_quarters, 2_000_000_000_000.0)
    _append_company_facts("HBC", hbc_quarters, 1_000_000_000_000.0)
    _append_company_facts("NOPX", fpt_quarters[:2], 500_000_000_000.0)

    facts_df = pl.DataFrame(
        fact_rows,
        schema_overrides={"ttm_value": pl.Float64},
        infer_schema_length=None,
    )
    analytics = build_analytics_tables(facts_df, issuers, as_of=date(2024, 12, 31))
    ratios_df = analytics[RATIOS_TABLE]

    hbc_tiers = {
        date(2022, 9, 30): ("R5", 0.40, 99.0, 16.0),
        date(2022, 12, 31): ("R4", 0.14, 91.0, 5.6),
        date(2023, 3, 31): ("R4", 0.16, 93.0, 6.4),
        date(2023, 6, 30): ("R5", 0.35, 98.0, 14.0),
        date(2023, 9, 30): ("R4", 0.18, 92.0, 7.2),
    }

    ml_rows: list[dict[str, object]] = []
    for rec in ratios_df.to_dicts():
        issuer = str(rec["issuer_id"])
        pend = rec["period_end_date"]
        tier, prob, pct, lift = ("R1", 0.01, 15.0, 0.4)
        if issuer == "HBC" and pend in hbc_tiers:
            tier, prob, pct, lift = hbc_tiers[pend]
        role = (
            "IN_SAMPLE_TRAIN"
            if pend < date(2022, 12, 31)
            else "OUT_OF_SAMPLE_WALK_FORWARD"
        )
        ml_rows.append(
            {
                "issuer_id": issuer,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": pend,
                "filing_id": str(rec["filing_id"]),
                "model_role": role,
                "altman_z_double_prime": 1.2 if issuer == "HBC" else 4.2,
                "altman_em_score": 3.0 if issuer == "HBC" else 6.5,
                "altman_zone": "DISTRESS_ZONE" if issuer == "HBC" else "SAFE_ZONE",
                "piotroski_f_score": 3 if issuer == "HBC" else 8,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -2.4,
                "beneish_flag": "VALID",
                "pca_pc1": 0.0,
                "pca_pc2": 0.0,
                "pca_pc3": 0.0,
                "cluster_id": 1,
                "cluster_archetype_name": "Doanh nghiệp sản xuất",
                "anomaly_score_raw": 0.1,
                "anomaly_percentile_rank": 30.0,
                "statistical_outlier_flag": False,
                "anomaly_flag": "NORMAL",
                "distress_risk_score": -1.5,
                "calibrated_distress_prob": prob,
                "risk_tier": tier,
                "risk_market_percentile": pct,
                "market_base_rate": 0.025,
                "relative_risk_lift": lift,
                "model_version": "v2.test",
                "shap_top_drivers_json": None,
            }
        )
    ml_df = pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA)
    manifest_df = pl.DataFrame(manifest_rows, schema=FILINGS_MANIFEST_SCHEMA)

    # Market prices spanning 2023-01-02 .. 2024-08-30 (~430 weekdays)
    start = date(2023, 1, 2)
    sessions = [
        start + timedelta(days=i)
        for i in range(610)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    price_rows: list[dict[str, object]] = []
    for d in sessions:
        # FPT: 4x split takes effect on 2024-05-03 (close_raw 60k -> 15k; close_adj always 15k)
        if d <= date(2024, 5, 2):
            fpt_raw, fpt_adj, fpt_sh = 60_000.0, 15_000.0, 100_000_000.0
        else:
            fpt_raw, fpt_adj, fpt_sh = 15_000.0, 15_000.0, 400_000_000.0
        price_rows.append(
            {
                "issuer_id": "FPT",
                "ticker": "FPT",
                "trade_date": d,
                "close_raw": fpt_raw,
                "close_adj": fpt_adj,
                "volume": 1_000_000.0,
                "shares_outstanding_market": fpt_sh,
                "source_provider": "vndirect_finfo",
            }
        )

        # HBC: steady drop after R4 warning (Mon 2023-04-03 @ 20,000) -> CONTROL (Mon 2023-09-04 @ 8,000) -> 5,000
        if d < date(2023, 4, 3):
            hbc_px = 20_000.0
        elif d < date(2023, 5, 15):
            hbc_px = 18_000.0
        elif d < date(2023, 7, 3):
            hbc_px = 16_000.0
        elif d < date(2023, 9, 4):
            hbc_px = 12_000.0
        elif d < date(2024, 1, 1):
            hbc_px = 8_000.0
        else:
            hbc_px = 5_000.0
        if d == date(2023, 4, 3):
            hbc_px = 20_000.0
        price_rows.append(
            {
                "issuer_id": "HBC",
                "ticker": "HBC",
                "trade_date": d,
                "close_raw": hbc_px,
                "close_adj": hbc_px,
                "volume": 500_000.0,
                "shares_outstanding_market": 50_000_000.0,
                "source_provider": "vndirect_finfo",
            }
        )

    market_prices_df = pl.DataFrame(price_rows, schema=MARKET_PRICES_SCHEMA)
    reg_events_df = pl.DataFrame(
        {
            "issuer_id": ["HBC"],
            "event_date": [date(2023, 9, 2)],  # Sat -> Mon 2023-09-04
            "effective_from": [date(2023, 9, 4)],
            "resolution_date": [None],
            "event_type": ["CONTROL"],
            "event_stage": ["FIRST_ENTRY"],
            "exchange_delisted_from": [None],
            "source_provider": ["vietcap_iq"],
            "source_url": ["https://example.com/hbc-control"],
            "raw_headline": ["HBC: Quyết định đưa cổ phiếu vào diện kiểm soát"],
            "payload_sha256": [_sha256("hbc-control")],
        },
        schema=REGULATORY_EVENTS_SCHEMA,
    )

    tables: dict[str, pl.DataFrame] = {
        "issuers.parquet": issuers,
        "financial_facts.parquet": facts_df,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: ml_df,
        FILINGS_MANIFEST_TABLE: manifest_df,
    }
    return tables, market_prices_df, reg_events_df


def _publish_fixture(
    tmp_path: Path,
    *,
    include_port_files: bool,
) -> tuple[Path, str, str]:
    data_root = tmp_path / "data"
    tables, market_prices_df, reg_events_df = _build_overlay_fixture_tables()
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_ui09b_001"
    snapshot_id = "snap_ui09b_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, "sha_ui09b")
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(
        snapshot_id,
        tables,
        run_id=run_id,
    )
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)

    if include_port_files:
        (data_root / "market_prices").mkdir(parents=True, exist_ok=True)
        (data_root / "regulatory_ledger").mkdir(parents=True, exist_ok=True)
        market_prices_df.write_parquet(
            data_root / "market_prices" / "market_prices.parquet"
        )
        reg_events_df.write_parquet(
            data_root / "regulatory_ledger" / "regulatory_events.parquet"
        )
    return data_root, snapshot_id, run_id


def test_v2_market_overlay_fpt_split_hbc_markers_valkey_and_etag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root, snapshot_id, run_id = _publish_fixture(tmp_path, include_port_files=True)

    kv_store: dict[str, bytes] = {}

    class _InMemoryRedisClient:
        def ping(self) -> bool:
            return True

        def get(self, key: str) -> bytes | None:
            return kv_store.get(key)

        def setex(self, key: str, _ttl: int, payload: bytes) -> bool:
            kv_store[key] = bytes(payload)
            return True

        def close(self) -> None:
            return None

    try:
        import redis

        monkeypatch.setattr(
            redis.Redis,
            "from_url",
            classmethod(lambda _cls, *_args, **_kwargs: _InMemoryRedisClient()),
        )
    except ImportError:

        class _StubRedis:
            @classmethod
            def from_url(
                cls, *_args: object, **_kwargs: object
            ) -> _InMemoryRedisClient:
                return _InMemoryRedisClient()

        monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_StubRedis))

    app = create_app(data_root=data_root, valkey_url="redis://127.0.0.1:6379/0")
    with TestClient(app) as client:
        # 1. FPT: dual close_raw vs close_adj across 4x split + Valkey MISS/HIT + ETag 304
        r1 = client.get("/api/v2/companies/FPT/market-overlay")
        assert r1.status_code == 200
        assert r1.headers.get("X-Cache") == "MISS"
        assert r1.headers.get("X-Snapshot-ID") == snapshot_id
        assert r1.headers.get("X-Request-ID") is not None
        assert r1.headers.get("Cache-Control") == "private, max-age=0, must-revalidate"
        etag = r1.headers.get("ETag")
        assert etag is not None

        fpt_payload = CompanyMarketOverlayResponse(**r1.json())
        assert fpt_payload.snapshot_id == snapshot_id
        assert fpt_payload.ticker == "FPT"
        assert fpt_payload.latest_valuation is not None
        assert fpt_payload.latest_valuation == fpt_payload.price_series[-1]

        by_date = {pt.trade_date: pt for pt in fpt_payload.price_series}
        pre_split = by_date[date(2024, 5, 2)]
        post_split = by_date[date(2024, 7, 26)]
        assert pre_split.close_raw == pytest.approx(60_000.0)
        assert pre_split.close_adj == pytest.approx(15_000.0)
        assert pre_split.market_cap_vnd == pytest.approx(6_000_000_000_000.0)
        assert pre_split.pe_ttm == pytest.approx(12.0)
        assert pre_split.pb_ratio == pytest.approx(3.0)
        assert pre_split.pe_flag == "VALID"
        assert pre_split.pb_flag == "VALID"
        assert post_split.close_raw == pytest.approx(15_000.0)
        assert post_split.market_cap_vnd == pytest.approx(6_000_000_000_000.0)
        assert post_split.running_drawdown_pct == pytest.approx(0.0)

        # Valkey cache HIT on repeat request
        r2 = client.get("/api/v2/companies/FPT/market-overlay")
        assert r2.status_code == 200
        assert r2.headers.get("X-Cache") == "HIT"
        assert r2.json() == r1.json()

        # ETag 304
        r304 = client.get(
            "/api/v2/companies/FPT/market-overlay",
            headers={"If-None-Match": etag},
        )
        assert r304.status_code == 304

        # 2. HBC: R4/R5 PIT markers + regulatory CONTROL marker + negative earnings flag + drawdowns
        r_hbc = client.get("/api/v2/companies/HBC/market-overlay")
        assert r_hbc.status_code == 200
        hbc = CompanyMarketOverlayResponse(**r_hbc.json())
        assert len(hbc.model_markers) == 2
        r4, r5 = hbc.model_markers
        assert r4.marker_kind == "FIRST_R4_UPGRADE"
        assert r4.date_basis == "PIT_DISCLOSURE"
        assert r4.disclosure_date == date(2023, 4, 1)
        assert r4.aligned_trade_date == date(2023, 4, 3)
        assert r4.drawdowns is not None
        assert r4.drawdowns.horizons["T+90d"] is not None
        assert r4.drawdowns.horizons["T+365d"] is not None
        assert r4.drawdowns.to_first_regulatory_event is not None
        assert (
            r4.drawdowns.to_first_regulatory_event.drawdown_from_marker_pct
            == pytest.approx(-60.0)
        )
        assert r5.marker_kind == "FIRST_R5_UPGRADE"
        assert r5.aligned_trade_date == date(2023, 7, 3)

        assert len(hbc.regulatory_markers) == 1
        reg = hbc.regulatory_markers[0]
        assert reg.event_type == "CONTROL"
        assert reg.aligned_trade_date == date(2023, 9, 4)

        assert hbc.earliest_warning_summary is not None
        assert hbc.earliest_warning_summary.warning_source == "MODEL_RISK_TIER"
        assert hbc.earliest_warning_summary.warning_label == "Model R4"
        assert hbc.latest_valuation is not None
        assert hbc.latest_valuation.pe_ttm is None
        assert hbc.latest_valuation.pe_flag == "NEGATIVE_EARNINGS"
        assert hbc.latest_valuation.pb_flag == "VALID"

        # 3. as_of query filter limits series and markers
        r_asof = client.get(
            "/api/v2/companies/HBC/market-overlay",
            params={"as_of": "2023-06-30"},
        )
        assert r_asof.status_code == 200
        hbc_asof = CompanyMarketOverlayResponse(**r_asof.json())
        assert hbc_asof.as_of == date(2023, 6, 30)
        assert all(p.trade_date <= date(2023, 6, 30) for p in hbc_asof.price_series)
        assert len(hbc_asof.model_markers) == 1
        assert hbc_asof.regulatory_markers == []

        # 4. Missing-price ticker (NOPX) returns graceful empty overlay (200, not 500)
        r_nopx = client.get("/api/v2/companies/NOPX/market-overlay")
        assert r_nopx.status_code == 200
        nopx = CompanyMarketOverlayResponse(**r_nopx.json())
        assert nopx.ticker == "NOPX"
        assert nopx.price_series == []
        assert nopx.latest_valuation is None
        assert nopx.model_markers == []
        assert nopx.regulatory_markers == []
        assert nopx.earliest_warning_summary is None

        # 5. Unknown ticker -> 404
        r_404 = client.get("/api/v2/companies/NOPE/market-overlay")
        assert r_404.status_code == 404


def test_v2_market_overlay_omitted_port_files_do_not_leak_repo_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Port 1/3 files are omitted in a tmp_path root, never leak real data/ files."""
    data_root, snapshot_id, _ = _publish_fixture(tmp_path, include_port_files=False)
    monkeypatch.setenv("FININTEL_DATA_ROOT", str(data_root))

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        resp = client.get("/api/v2/companies/FPT/market-overlay")
        assert resp.status_code == 200
        body = CompanyMarketOverlayResponse(**resp.json())
        assert body.snapshot_id == snapshot_id
        assert body.price_series == []
        assert body.latest_valuation is None
        assert body.model_markers == []
        assert body.regulatory_markers == []
        assert body.earliest_warning_summary is None


@pytest.mark.skipif(
    not _HAS_REAL_UNIVERSE,
    reason="Real universe snapshot and Port 1/3 datasets are not available on disk.",
)
def test_v2_market_overlay_real_fpt_and_hbc() -> None:
    """Verify real-data end-to-end overlay on FPT (split history) and HBC (regulatory distress)."""
    app = create_app(data_root=_REAL_SNAPSHOT_ROOT)
    with TestClient(app) as client:
        r_fpt = client.get("/api/v2/companies/FPT/market-overlay")
        assert r_fpt.status_code == 200
        fpt = CompanyMarketOverlayResponse(**r_fpt.json())
        assert len(fpt.price_series) > 2000
        assert fpt.latest_valuation is not None
        assert fpt.latest_valuation.pe_flag == "VALID"
        assert fpt.latest_valuation.pb_flag == "VALID"
        assert (
            fpt.latest_valuation.pe_ttm is not None and fpt.latest_valuation.pe_ttm > 0
        )
        assert fpt.latest_valuation.market_cap_vnd is not None

        r_hbc = client.get("/api/v2/companies/HBC/market-overlay")
        assert r_hbc.status_code == 200
        hbc = CompanyMarketOverlayResponse(**r_hbc.json())
        assert len(hbc.price_series) > 2000
        assert len(hbc.regulatory_markers) >= 1
        assert hbc.earliest_warning_summary is not None
        assert hbc.earliest_warning_summary.to_current is not None
        assert hbc.earliest_warning_summary.to_current.drawdown_from_marker_pct < -50.0
