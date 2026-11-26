"""Unit and real-data tests for UI-09b / B-06 PIT valuation and risk overlay engine."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.market_valuation import (
    build_price_risk_overlay,
    compute_pit_valuation_series,
)
from finintel.exceptions import ContractViolationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_REPO_DATA_DIR = Path("d:/KiCuoiCung/DATN/financial-intelligence/data")
_MARKET_PRICES_PATH = (
    _REPO_ROOT / "data" / "market_prices" / "market_prices.parquet"
    if (_REPO_ROOT / "data" / "market_prices" / "market_prices.parquet").exists()
    else _MAIN_REPO_DATA_DIR / "market_prices" / "market_prices.parquet"
)
_REGULATORY_EVENTS_PATH = (
    _REPO_ROOT / "data" / "regulatory_ledger" / "regulatory_events.parquet"
    if (
        _REPO_ROOT / "data" / "regulatory_ledger" / "regulatory_events.parquet"
    ).exists()
    else _MAIN_REPO_DATA_DIR / "regulatory_ledger" / "regulatory_events.parquet"
)
_HAS_REAL_PORT_DATA = _MARKET_PRICES_PATH.exists() and _REGULATORY_EVENTS_PATH.exists()


def _sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _issuer(issuer_id: str = "FPT", ticker: str = "FPT") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": [issuer_id],
            "ticker": [ticker],
            "exchange": ["HOSE"],
            "company_name_vn": [f"{ticker} Corp"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["9000"],
            "icb_name_level2": ["Technology"],
            "icb_code_level3": ["9537"],
            "icb_name_level3": ["Software"],
            "industry_snapshot_date": [date(2020, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )


def _facts_rows(
    entries: list[tuple[int, int | None, date, dict[str, float | None]]],
    *,
    issuer_id: str = "FPT",
    ticker: str = "FPT",
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for year, quarter, avail_date, metrics in entries:
        if quarter is None:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            period_type = "ANNUAL"
        else:
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
            period_type = "QUARTER"
        for metric_id, val in metrics.items():
            rows.append(
                {
                    "issuer_id": issuer_id,
                    "statement_scope": "CONSOLIDATED",
                    "period_type": period_type,
                    "fiscal_year_start_date": date(year, 1, 1),
                    "period_end_date": end,
                    "metric_id": metric_id,
                    "taxonomy_version": "VAS_TT200_2014",
                    "revision_no": 1,
                    "filing_id": f"{ticker}-{year}-{quarter}",
                    "fiscal_year": year,
                    "fiscal_quarter": quarter,
                    "period_start_date": start,
                    "published_at": avail_date,
                    "published_at_basis": "EXACT_FILING",
                    "effective_available_at": avail_date,
                    "revision_reason": "ORIGINAL",
                    "derivation_method": "REPORTED_DIRECTLY",
                    "audit_status": "AUDITED" if quarter is None else "UNAUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "value": float(val) if val is not None else None,
                    "source_provider": "fhsc",
                    "ticker_at_filing": ticker,
                }
            )
    return pl.DataFrame(
        rows,
        schema_overrides={
            "fiscal_quarter": pl.Int8,
            "revision_no": pl.Int64,
            "fiscal_year": pl.Int32,
            "value": pl.Float64,
        },
    )


def _prices_frame(
    rows: list[tuple[date, float, float, float | None]],
    *,
    issuer_id: str = "FPT",
    ticker: str = "FPT",
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": [issuer_id] * len(rows),
            "ticker": [ticker] * len(rows),
            "trade_date": [r[0] for r in rows],
            "close_raw": [r[1] for r in rows],
            "close_adj": [r[2] for r in rows],
            "volume": [100_000.0] * len(rows),
            "shares_outstanding_market": [r[3] for r in rows],
            "source_provider": ["vndirect_finfo"] * len(rows),
        },
        schema_overrides={"shares_outstanding_market": pl.Float64},
    )


def test_b06_null_treasury_shares_guard_and_market_shares_fallback() -> None:
    facts = _facts_rows(
        [
            # 2024 annual: BS_TREASURY_SHARES omitted (null) -> assumed 0 per B-06
            (
                2024,
                None,
                date(2025, 3, 10),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_000_000_000_000.0,
                    "BS_TOTAL_EQUITY": 3_000_000_000_000.0,
                    "IS_NET_INCOME_PARENT": 500_000_000_000.0,
                },
            ),
            # 2025 annual: BS_TREASURY_SHARES explicitly 0
            (
                2025,
                None,
                date(2026, 3, 10),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_200_000_000_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                    "BS_TOTAL_EQUITY": 3_600_000_000_000.0,
                    "IS_NET_INCOME_PARENT": 600_000_000_000.0,
                },
            ),
        ]
    )
    periods_no_mkt = prepare_periods(facts, _issuer(), as_of=date(2026, 4, 1))
    assert periods_no_mkt["estimated_shares_outstanding"].to_list() == [
        100_000_000.0,
        120_000_000.0,
    ]
    assert periods_no_mkt["shares_source"].to_list() == [
        "BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO",
        "BS_ORDINARY_SHARES_EXPLICIT_ZERO_TREASURY",
    ]

    # When treasury shares are non-zero, par shares cannot be assumed; fallback to market shares
    nonzero_treasury = _facts_rows(
        [
            (
                2025,
                None,
                date(2026, 3, 10),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_200_000_000_000.0,
                    "BS_TREASURY_SHARES": -50_000_000_000.0,
                    "BS_TOTAL_EQUITY": 3_600_000_000_000.0,
                },
            )
        ]
    )
    periods_without_mkt = prepare_periods(
        nonzero_treasury, _issuer(), as_of=date(2026, 4, 1)
    )
    assert periods_without_mkt["estimated_shares_outstanding"].to_list() == [None]
    assert periods_without_mkt["shares_source"].to_list() == ["MISSING_SHARES"]

    prices = _prices_frame([(date(2026, 3, 10), 40_000.0, 40_000.0, 118_500_000.0)])
    periods_with_mkt = prepare_periods(
        nonzero_treasury, _issuer(), as_of=date(2026, 4, 1), market_prices=prices
    )
    assert periods_with_mkt["estimated_shares_outstanding"].to_list() == [118_500_000.0]
    assert periods_with_mkt["shares_source"].to_list() == ["SHARES_OUTSTANDING_MARKET"]


def test_split_adjustment_uses_close_raw_for_valuation_and_close_adj_for_overlay() -> (
    None
):
    # Pre-split session: close_raw=60,000 VND vs close_adj=15,000 VND (4x split factor)
    # Post-split session: close_raw=15,000 VND vs close_adj=15,000 VND (no economic drop)
    prices = _prices_frame(
        [
            (date(2024, 5, 2), 60_000.0, 15_000.0, 100_000_000.0),
            (date(2024, 5, 3), 15_000.0, 15_000.0, 400_000_000.0),
        ]
    )
    periods = pl.DataFrame(
        {
            "issuer_id": ["FPT", "FPT"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "period_end_date": [date(2024, 3, 31), date(2024, 3, 31)],
            "available_at": [date(2024, 5, 2), date(2024, 5, 3)],
            "estimated_shares_outstanding": [100_000_000.0, 400_000_000.0],
            "IS_NET_INCOME_PARENT_TTM": [500_000_000_000.0, 500_000_000_000.0],
            "parent_equity": [2_000_000_000_000.0, 2_000_000_000_000.0],
        }
    )

    val = compute_pit_valuation_series(prices, periods, ticker="FPT")
    assert val.height == 2

    pre_split = val.row(0, named=True)
    post_split = val.row(1, named=True)
    # Must be 60,000 * 100M = 6T VND (NOT close_adj 15,000 * 100M = 1.5T VND)
    assert pre_split["market_cap_vnd"] == pytest.approx(6_000_000_000_000.0)
    assert pre_split["pe_ttm"] == pytest.approx(12.0)
    assert pre_split["pb_ratio"] == pytest.approx(3.0)
    assert post_split["market_cap_vnd"] == pytest.approx(6_000_000_000_000.0)
    assert post_split["pe_ttm"] == pytest.approx(12.0)
    assert post_split["pb_ratio"] == pytest.approx(3.0)

    # Overlay price series must use close_adj so a split produces 0% artificial drawdown
    overlay = build_price_risk_overlay(prices, ticker="FPT", valuation_series=val)
    assert [p["close_adj"] for p in overlay["price_series"]] == [15_000.0, 15_000.0]
    assert [p["running_drawdown_pct"] for p in overlay["price_series"]] == [0.0, 0.0]


def test_weekend_disclosure_forward_aligns_to_monday_without_friday_lookahead() -> None:
    # Trading days: Fri 2024-04-26, Mon 2024-04-29, Fri 2024-07-26, Mon 2024-07-29
    prices = _prices_frame(
        [
            (date(2024, 4, 26), 20_000.0, 20_000.0, 10_000_000.0),
            (date(2024, 4, 29), 21_000.0, 21_000.0, 10_000_000.0),
            (date(2024, 7, 26), 22_000.0, 22_000.0, 10_000_000.0),
            (date(2024, 7, 29), 24_000.0, 24_000.0, 10_000_000.0),
        ]
    )
    # Q1 published Saturday 2024-04-27; Q2 published Sunday 2024-07-28
    periods = pl.DataFrame(
        {
            "issuer_id": ["FPT", "FPT"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "period_end_date": [date(2024, 3, 31), date(2024, 6, 30)],
            "published_at": [date(2024, 4, 27), date(2024, 7, 28)],
            "estimated_shares_outstanding": [10_000_000.0, 10_000_000.0],
            "IS_NET_INCOME_PARENT_TTM": [100_000_000_000.0, 200_000_000_000.0],
            "parent_equity": [100_000_000_000.0, 120_000_000_000.0],
        }
    )

    val = compute_pit_valuation_series(prices, periods, ticker="FPT")
    rows = {r["trade_date"]: r for r in val.to_dicts()}

    # Friday 2024-04-26 MUST NOT see Saturday 2024-04-27 Q1 disclosure
    fri1 = rows[date(2024, 4, 26)]
    assert fri1["period_end_date"] is None
    assert fri1["pe_ttm"] is None
    assert fri1["pe_flag"] == "MISSING_EARNINGS"
    assert fri1["shares_source"] == "SHARES_OUTSTANDING_MARKET"

    # Monday 2024-04-29 receives Saturday 2024-04-27 Q1 disclosure
    mon1 = rows[date(2024, 4, 29)]
    assert mon1["disclosure_available_at"] == date(2024, 4, 27)
    assert mon1["effective_trade_date"] == date(2024, 4, 29)
    assert mon1["period_end_date"] == date(2024, 3, 31)
    assert mon1["pe_ttm"] == pytest.approx(2.1)

    # Friday 2024-07-26 still uses Q1 (0% look-ahead to Sunday 2024-07-28 Q2)
    fri2 = rows[date(2024, 7, 26)]
    assert fri2["period_end_date"] == date(2024, 3, 31)
    assert fri2["pe_ttm"] == pytest.approx(2.2)

    # Monday 2024-07-29 transitions to Q2
    mon2 = rows[date(2024, 7, 29)]
    assert mon2["disclosure_available_at"] == date(2024, 7, 28)
    assert mon2["effective_trade_date"] == date(2024, 7, 29)
    assert mon2["period_end_date"] == date(2024, 6, 30)
    assert mon2["pe_ttm"] == pytest.approx(1.2)


def test_negative_earnings_equity_and_missing_shares_flags() -> None:
    prices = _prices_frame(
        [
            (date(2025, 1, 2), 10_000.0, 10_000.0, 1_000_000.0),
            (date(2025, 4, 2), 10_000.0, 10_000.0, 1_000_000.0),
            (date(2025, 7, 2), 10_000.0, 10_000.0, None),
        ],
        issuer_id="HBC",
        ticker="HBC",
    )
    periods = pl.DataFrame(
        {
            "issuer_id": ["HBC", "HBC", "HBC"],
            "statement_scope": ["CONSOLIDATED"] * 3,
            "period_type": ["QUARTER"] * 3,
            "period_end_date": [
                date(2024, 9, 30),
                date(2024, 12, 31),
                date(2025, 3, 31),
            ],
            "available_at": [date(2025, 1, 2), date(2025, 4, 2), date(2025, 7, 2)],
            "estimated_shares_outstanding": [None, 1_000_000.0, None],
            "IS_NET_INCOME_PARENT_TTM": [
                -5_000_000_000.0,
                2_000_000_000.0,
                1_000_000_000.0,
            ],
            "parent_equity": [5_000_000_000.0, -3_000_000_000.0, 5_000_000_000.0],
        },
        schema_overrides={"estimated_shares_outstanding": pl.Float64},
    )

    val = compute_pit_valuation_series(prices, periods, ticker="HBC")
    rows = val.to_dicts()

    # Row 0: negative earnings, positive equity, market shares fallback
    assert rows[0]["shares_source"] == "SHARES_OUTSTANDING_MARKET"
    assert rows[0]["market_cap_vnd"] == pytest.approx(10_000_000_000.0)
    assert rows[0]["pe_ttm"] is None
    assert rows[0]["pe_flag"] == "NEGATIVE_EARNINGS"
    assert rows[0]["pb_ratio"] == pytest.approx(2.0)
    assert rows[0]["pb_flag"] == "VALID"

    # Row 1: positive earnings, negative equity
    assert rows[1]["shares_source"] == "ESTIMATED_SHARES_OUTSTANDING"
    assert rows[1]["pe_ttm"] == pytest.approx(5.0)
    assert rows[1]["pe_flag"] == "VALID"
    assert rows[1]["pb_ratio"] is None
    assert rows[1]["pb_flag"] == "NEGATIVE_EQUITY"

    # Row 2: both BCTC shares and market shares missing -> MISSING_SHARES
    assert rows[2]["shares_source"] == "MISSING_SHARES"
    assert rows[2]["market_cap_vnd"] is None
    assert rows[2]["market_cap_flag"] == "MISSING_SHARES"
    assert rows[2]["pe_ttm"] is None
    assert rows[2]["pe_flag"] == "MISSING_SHARES"
    assert rows[2]["pb_ratio"] is None
    assert rows[2]["pb_flag"] == "MISSING_SHARES"


def test_build_price_risk_overlay_markers_and_drawdowns() -> None:
    # Build daily sessions covering > 365 days around R4, R5, and CONTROL events
    start = date(2023, 1, 2)
    session_dates = [
        start + timedelta(days=i)
        for i in range(600)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    # R4 disclosure Sat 2023-04-01 -> Mon 2023-04-03 (base price 20,000)
    # R5 disclosure Mon 2023-07-03 (price 16,000)
    # Regulatory CONTROL Sat 2023-09-02 -> Mon 2023-09-04 (price 12,000)
    # Price drops steadily after 2023-04-03 down to trough 5,000
    price_rows: list[tuple[date, float, float, float | None]] = []
    for d in session_dates:
        if d < date(2023, 4, 3):
            px = 20_000.0
        elif d < date(2023, 5, 15):
            px = 18_000.0
        elif d < date(2023, 7, 3):
            px = 16_000.0
        elif d < date(2023, 9, 4):
            px = 12_000.0
        elif d < date(2024, 1, 1):
            px = 8_000.0
        else:
            px = 5_000.0
        price_rows.append((d, px, px, 50_000_000.0))

    prices = _prices_frame(price_rows, issuer_id="HBC", ticker="HBC")
    # Set exact base price at 2023-04-03 to 20,000
    prices = prices.with_columns(
        pl.when(pl.col("trade_date") == date(2023, 4, 3))
        .then(20_000.0)
        .otherwise(pl.col("close_adj"))
        .alias("close_adj")
    ).with_columns(pl.col("close_adj").alias("close_raw"))

    ml_scores = pl.DataFrame(
        {
            "issuer_id": ["HBC"] * 5,
            "statement_scope": ["CONSOLIDATED"] * 5,
            "period_type": ["QUARTER"] * 5,
            "period_end_date": [
                date(2022, 9, 30),
                date(2022, 12, 31),
                date(2023, 3, 31),
                date(2023, 6, 30),
                date(2023, 9, 30),
            ],
            "available_at": [
                date(2023, 1, 15),  # R2
                date(2023, 4, 1),  # Sat -> Mon 2023-04-03 (FIRST_R4_UPGRADE)
                date(2023, 5, 2),  # R4 repeat (must NOT emit duplicate)
                date(2023, 7, 3),  # Mon 2023-07-03 (FIRST_R5_UPGRADE)
                date(2023, 10, 30),  # R4 downgrade (must NOT emit upgrade)
            ],
            "risk_tier": ["R2", "R4", "R4", "R5", "R4"],
            "calibrated_distress_prob": [0.02, 0.14, 0.16, 0.35, 0.18],
            "risk_market_percentile": [45.0, 91.0, 93.0, 98.0, 92.0],
            "relative_risk_lift": [0.8, 5.6, 6.4, 14.0, 7.2],
        }
    )

    reg_events = pl.DataFrame(
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
            "raw_headline": ["HBC put under control"],
            "payload_sha256": [_sha256("hbc-control")],
        },
        schema_overrides={
            "resolution_date": pl.Date,
            "exchange_delisted_from": pl.String,
        },
    )

    overlay = build_price_risk_overlay(
        prices,
        ticker="HBC",
        ml_scores=ml_scores,
        regulatory_events=reg_events,
    )

    assert overlay["issuer_id"] == "HBC"
    assert overlay["ticker"] == "HBC"

    # Model markers: only 2 (first R4 upgrade + first R5 upgrade)
    assert len(overlay["model_markers"]) == 2
    r4, r5 = overlay["model_markers"]
    assert r4["marker_kind"] == "FIRST_R4_UPGRADE"
    assert r4["is_first_r4_or_r5"] is True
    assert r4["disclosure_date"] == date(2023, 4, 1)
    assert r4["aligned_trade_date"] == date(2023, 4, 3)
    assert r4["close_adj_at_marker"] == pytest.approx(20_000.0)
    assert r4["drawdowns"] is not None
    assert r4["drawdowns"]["horizons"]["T+30d"] is not None
    assert r4["drawdowns"]["horizons"]["T+90d"] is not None
    assert r4["drawdowns"]["horizons"]["T+180d"] is not None
    assert r4["drawdowns"]["horizons"]["T+365d"] is not None
    # Pre-event drawdown from R4 (20,000 on 2023-04-03) up to CONTROL (8,000 on 2023-09-04)
    to_reg = r4["drawdowns"]["to_first_regulatory_event"]
    assert to_reg is not None
    assert to_reg["end_trade_date"] == date(2023, 9, 4)
    assert to_reg["drawdown_from_marker_pct"] == pytest.approx(-60.0)
    # Full drawdown up to current date (5,000 / 20,000 - 1 = -75%)
    to_curr = r4["drawdowns"]["to_current"]
    assert to_curr is not None
    assert to_curr["drawdown_from_marker_pct"] == pytest.approx(-75.0)

    assert r5["marker_kind"] == "FIRST_R5_UPGRADE"
    assert r5["is_first_r4_or_r5"] is False
    assert r5["aligned_trade_date"] == date(2023, 7, 3)

    # Regulatory marker aligned from Saturday 2023-09-02 -> Monday 2023-09-04
    assert len(overlay["regulatory_markers"]) == 1
    reg = overlay["regulatory_markers"][0]
    assert reg["event_type"] == "CONTROL"
    assert reg["aligned_trade_date"] == date(2023, 9, 4)
    assert reg["close_adj_at_marker"] == pytest.approx(8_000.0)
    assert reg["drawdowns"] is not None
    assert reg["drawdowns"]["to_current"] is not None
    assert reg["drawdowns"]["to_current"]["drawdown_from_marker_pct"] == pytest.approx(
        -37.5
    )

    # Earliest warning summary anchors on Model R4
    summary = overlay["earliest_warning_summary"]
    assert summary is not None
    assert summary["warning_source"] == "MODEL_RISK_TIER"
    assert summary["warning_label"] == "Model R4"
    assert summary["aligned_trade_date"] == date(2023, 4, 3)
    before_warn = [
        p for p in overlay["price_series"] if p["trade_date"] < date(2023, 4, 3)
    ]
    after_warn = [
        p for p in overlay["price_series"] if p["trade_date"] >= date(2023, 4, 3)
    ]
    assert all(p["drawdown_from_first_warning_pct"] is None for p in before_warn)
    assert after_warn[0]["drawdown_from_first_warning_pct"] == pytest.approx(0.0)
    assert after_warn[-1]["drawdown_from_first_warning_pct"] == pytest.approx(-75.0)

    # Multiple issuers without filter raises ContractViolationError
    with pytest.raises(ContractViolationError):
        build_price_risk_overlay(
            pl.concat(
                [prices, _prices_frame([(date(2023, 4, 3), 10_000.0, 10_000.0, 1.0)])]
            )
        )


@pytest.mark.skipif(
    not _HAS_REAL_PORT_DATA,
    reason="Real Port 1/Port 3 parquet datasets not available on disk.",
)
def test_real_data_split_tickers_and_case_studies() -> None:
    market_prices = pl.read_parquet(_MARKET_PRICES_PATH)
    reg_events = pl.read_parquet(_REGULATORY_EVENTS_PATH)

    # 1. Verify split-adjustment divergence on FPT, HPG, MWG using close_raw vs close_adj
    for ticker in ("FPT", "HPG", "MWG"):
        sub = market_prices.filter(pl.col("ticker") == ticker).sort("trade_date")
        assert not sub.is_empty()
        earliest = sub.row(0, named=True)
        # Historical split factor > 2x on 2018 sessions
        assert earliest["close_raw"] / earliest["close_adj"] > 2.0

        # Compute PIT valuation with synthetic statement at earliest trade date
        synthetic_periods = pl.DataFrame(
            {
                "issuer_id": [earliest["issuer_id"]],
                "statement_scope": ["CONSOLIDATED"],
                "period_type": ["QUARTER"],
                "period_end_date": [date(2017, 12, 31)],
                "available_at": [earliest["trade_date"]],
                "estimated_shares_outstanding": [earliest["shares_outstanding_market"]],
                "IS_NET_INCOME_PARENT_TTM": [2_000_000_000_000.0],
                "parent_equity": [10_000_000_000_000.0],
            }
        )
        val = compute_pit_valuation_series(
            sub.head(5), synthetic_periods, ticker=ticker
        )
        row0 = val.row(0, named=True)
        assert row0["market_cap_vnd"] == pytest.approx(
            earliest["close_raw"] * earliest["shares_outstanding_market"]
        )
        assert (
            row0["market_cap_vnd"]
            > earliest["close_adj"] * earliest["shares_outstanding_market"] * 2.0
        )

    # 2. Verify real case-study drawdown overlays for HBC, POM, HNG, DLG, SMC, QBS
    expected_min_drawdowns = {
        "HBC": -50.0,
        "POM": -50.0,
        "HNG": -50.0,
        "DLG": -40.0,
        "SMC": -40.0,
        "QBS": -70.0,
    }
    for ticker, max_allowed_dd in expected_min_drawdowns.items():
        overlay = build_price_risk_overlay(
            market_prices,
            ticker=ticker,
            regulatory_events=reg_events,
        )
        assert len(overlay["regulatory_markers"]) >= 1
        summary = overlay["earliest_warning_summary"]
        assert summary is not None
        assert summary["to_current"] is not None
        assert summary["to_current"]["drawdown_from_marker_pct"] < max_allowed_dd


def test_market_shares_pit_14d_tolerance_and_strict_treasury_guard() -> None:
    """Block pre-2018 market-share lookahead via 14d tolerance and guard unadjusted MARKETCAP."""
    from finintel.analytics.ratios import calculate_ratios

    facts = _facts_rows(
        [
            # Pre-2018 annual: market_prices starting 2018-01-02 MUST NOT leak backward
            (
                2014,
                None,
                date(2015, 4, 30),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_200_000_000_000.0,
                    "BS_TREASURY_SHARES": -50_000_000_000.0,
                    "BS_TOTAL_EQUITY": 3_000_000_000_000.0,
                    "IS_NET_INCOME_PARENT": 500_000_000_000.0,
                },
            ),
            # 2024 annual: unadjusted market shares == charter_shares (120M) with treasury < 0 -> REJECT
            (
                2024,
                None,
                date(2025, 3, 10),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_200_000_000_000.0,
                    "BS_TREASURY_SHARES": -50_000_000_000.0,
                    "BS_TOTAL_EQUITY": 3_200_000_000_000.0,
                    "IS_NET_INCOME_PARENT": 600_000_000_000.0,
                },
            ),
            # 2025 annual: market shares (118.5M) strictly < charter_shares (120M) -> ACCEPT for EPS
            (
                2025,
                None,
                date(2026, 3, 10),
                {
                    "BS_ORDINARY_SHARES_CAPITAL": 1_200_000_000_000.0,
                    "BS_TREASURY_SHARES": -50_000_000_000.0,
                    "BS_TOTAL_EQUITY": 3_600_000_000_000.0,
                    "IS_NET_INCOME_PARENT": 592_500_000_000.0,
                },
            ),
        ]
    )
    prices = _prices_frame(
        [
            (date(2018, 1, 2), 30_000.0, 15_000.0, 110_000_000.0),
            (date(2025, 3, 10), 40_000.0, 40_000.0, 120_000_000.0),
            (date(2026, 3, 10), 42_000.0, 42_000.0, 118_500_000.0),
        ]
    )
    periods = prepare_periods(
        facts, _issuer(), as_of=date(2026, 4, 1), market_prices=prices
    )
    ratios = calculate_ratios(periods).sort("fiscal_year")
    rows = ratios.to_dicts()

    # 2014: 2018 market shares MUST NOT leak back via unbounded forward join
    assert rows[0]["estimated_shares_outstanding"] is None
    assert rows[0]["shares_source"] == "MISSING_SHARES"
    assert rows[0]["eps_ttm_estimated"] is None

    # 2024: shares_outstanding_market == charter_shares with treasury < 0 -> rejected
    assert rows[1]["estimated_shares_outstanding"] is None
    assert rows[1]["shares_source"] == "MISSING_SHARES"
    assert rows[1]["eps_ttm_estimated"] is None
    assert rows[1]["eps_estimate_basis"] is None

    # 2025: 118.5M < 120M -> accepted for both estimated_shares_outstanding & eps_ttm_estimated
    assert rows[2]["estimated_shares_outstanding"] == pytest.approx(118_500_000.0)
    assert rows[2]["shares_source"] == "SHARES_OUTSTANDING_MARKET"
    assert rows[2]["eps_ttm_estimated"] == pytest.approx(5_000.0)
    assert rows[2]["eps_estimate_basis"] == "SHARES_OUTSTANDING_MARKET"


def test_task08_merton_dtd_convertible_bonds_boundary_flags_and_2step_weekend_pit(
    tmp_path: Path,
) -> None:
    """TASK-08: Merton DTD includes BS_CONVERTIBLE_BONDS, enforces 2-step weekend PIT, halt >14d filter, and all 4 edge flags."""
    import math
    import statistics

    from finintel.analytics.merton_dtd import compute_merton_dtd_series
    from finintel.pipeline.state_store import StateStore
    from finintel.serving.queries import DashboardQueryService
    from finintel.storage.repository import ParquetRepository
    from finintel.storage.schemas import (
        BENCHMARKS_TABLE,
        FILINGS_MANIFEST_SCHEMA,
        FILINGS_MANIFEST_TABLE,
        ML_SCORES_SCHEMA,
        ML_SCORES_TABLE,
        RATIOS_TABLE,
        RED_FLAGS_TABLE,
    )
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    # Build trading calendar across Jan 2025 .. Nov 2025 with a 30-day halt in Feb 2025 (> 14d gap)
    sessions: list[tuple[date, float, float, float | None]] = []
    cur = date(2024, 10, 1)
    idx = 0
    while cur <= date(2025, 11, 10):
        if cur.weekday() < 5 and not (date(2025, 1, 15) <= cur <= date(2025, 2, 20)):
            # Smooth deterministic oscillation; jump by -40% across the >14d halt so halt filter is tested!
            regime_mult = 0.60 if cur > date(2025, 2, 20) else 1.0
            adj = (
                20_000.0
                * regime_mult
                * math.exp(0.01 * math.sin(idx * 0.7) + 0.0005 * idx)
            )
            # 2:1 split on 2025-05-05: pre-split close_raw = 2 * adj with 50M shares; post-split close_raw = adj with 100M shares
            if cur < date(2025, 5, 5):
                raw = adj * 2.0
                shares = 50_000_000.0
            else:
                raw = adj
                shares = 100_000_000.0
            sessions.append((cur, raw, adj, shares))
            idx += 1
        cur += timedelta(days=1)

    prices = _prices_frame(sessions, issuer_id="HBC", ticker="HBC")

    # Disclose Q1 2025 on SATURDAY 2025-04-26 -> Friday 2025-04-25 must NOT see it (MISSING_BCTC_DEBT)!
    # Monday 2025-04-28 activates ST=100B, LT=200B, Convertible Bonds=400B -> F = 100B + 0.5*(200B+400B) = 400B!
    facts_debt = _facts_rows(
        [
            (
                2025,
                1,
                date(2025, 4, 26),  # Saturday!
                {
                    "BS_TOTAL_ASSETS": 2_000_000_000_000.0,
                    "BS_TOTAL_LIABILITIES": 900_000_000_000.0,
                    "BS_TOTAL_EQUITY": 1_100_000_000_000.0,
                    "BS_SHORT_TERM_BORROWINGS": 100_000_000_000.0,
                    "BS_LONG_TERM_BORROWINGS": 200_000_000_000.0,
                    "BS_CONVERTIBLE_BONDS": 400_000_000_000.0,
                    "BS_ORDINARY_SHARES_CAPITAL": 500_000_000_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                    "IS_NET_INCOME_PARENT": 40_000_000_000.0,
                },
            )
        ],
        issuer_id="HBC",
        ticker="HBC",
    )

    dtd_df = compute_merton_dtd_series(prices, facts_debt, ticker="HBC")
    by_date = {r["trade_date"]: r for r in dtd_df.to_dicts()}

    # 1. Early session (< 60 valid return pairs) -> INSUFFICIENT_TRADING_SESSIONS
    first_dt = sessions[10][0]
    assert by_date[first_dt]["dtd_flag"] == "INSUFFICIENT_TRADING_SESSIONS"
    assert by_date[first_dt]["merton_dtd"] is None

    # 2. Friday 2025-04-25 (>= 60 sessions, BEFORE Saturday 2025-04-26 disclosure) -> MISSING_BCTC_DEBT (0% weekend look-ahead!)
    fri_row = by_date[date(2025, 4, 25)]
    assert fri_row["dtd_flag"] == "MISSING_BCTC_DEBT"
    assert fri_row["merton_dtd"] is None

    # 3. Monday 2025-04-28 (first session >= Sat 2025-04-26) -> VALID with exact Bharath & Shumway (2008) math + Convertible Bonds!
    mon_dt = date(2025, 4, 28)
    mon_row = by_date[mon_dt]
    assert mon_row["dtd_flag"] == "VALID"
    assert mon_row["merton_dtd"] is not None
    assert mon_row["naive_edf"] is not None

    # Manually compute exact Bharath-Shumway DTD on Monday 2025-04-28 to verify BS_CONVERTIBLE_BONDS & >14d halt exclusion:
    win = [s for s in sessions if (mon_dt - timedelta(days=380)) <= s[0] <= mon_dt][
        -252:
    ]
    valid_rets = [
        math.log(win[i][2] / win[i - 1][2])
        for i in range(1, len(win))
        if (win[i][0] - win[i - 1][0]).days <= 14
    ]
    expected_sigma_e = max(0.05, math.sqrt(252.0) * statistics.stdev(valid_rets))
    s0 = next(s for s in win if (mon_dt - s[0]).days >= 90)
    expected_mu = max(
        -1.0,
        min(1.0, math.log(win[-1][2] / s0[2]) * (365.25 / (win[-1][0] - s0[0]).days)),
    )
    e_val = win[-1][1] * 50_000_000.0  # close_raw * pit_shares
    f_val = 100e9 + 0.5 * (200e9 + 400e9)  # 400B VND including BS_CONVERTIBLE_BONDS!
    expected_sigma_v = (e_val / (e_val + f_val)) * expected_sigma_e + (
        f_val / (e_val + f_val)
    ) * (0.05 + 0.25 * expected_sigma_e)
    expected_dtd = (
        math.log((e_val + f_val) / f_val) + (expected_mu - 0.5 * expected_sigma_v**2)
    ) / expected_sigma_v
    expected_edf = 0.5 * math.erfc(expected_dtd / math.sqrt(2.0))

    assert mon_row["equity_volatility_1y"] == pytest.approx(expected_sigma_e, rel=1e-9)
    assert mon_row["merton_dtd"] == pytest.approx(expected_dtd, rel=1e-9)
    assert mon_row["naive_edf"] == pytest.approx(expected_edf, rel=1e-9)

    # 4. > 180 days after effective_trade_date 2025-04-28 (2025-11-05 is +191 days) -> STALE_BCTC_DEBT!
    stale_row = by_date[date(2025, 11, 5)]
    assert stale_row["dtd_flag"] == "STALE_BCTC_DEBT"
    assert stale_row["merton_dtd"] is None

    # 5. Debt-free corporate (Assets > 0, Liabilities > 0, ST=LT=Bonds=0) -> ZERO_FINANCIAL_DEBT!
    debt_free_facts = _facts_rows(
        [
            (
                2025,
                1,
                date(2025, 4, 28),
                {
                    "BS_TOTAL_ASSETS": 1_000_000_000_000.0,
                    "BS_TOTAL_LIABILITIES": 200_000_000_000.0,
                    "BS_TOTAL_EQUITY": 800_000_000_000.0,
                    "BS_SHORT_TERM_BORROWINGS": 0.0,
                    "BS_LONG_TERM_BORROWINGS": 0.0,
                    "BS_CONVERTIBLE_BONDS": 0.0,
                    "BS_ORDINARY_SHARES_CAPITAL": 500_000_000_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                },
            )
        ],
        issuer_id="HBC",
        ticker="HBC",
    )
    zero_df = compute_merton_dtd_series(prices, debt_free_facts, ticker="HBC")
    zero_mon = {r["trade_date"]: r for r in zero_df.to_dicts()}[mon_dt]
    assert zero_mon["dtd_flag"] == "ZERO_FINANCIAL_DEBT"
    assert zero_mon["merton_dtd"] is None
    assert zero_mon["equity_volatility_1y"] == pytest.approx(expected_sigma_e, rel=1e-9)

    # 5b. Missing shares (no market shares and no BCTC shares) -> MISSING_SHARES!
    no_sh_prices = prices.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("shares_outstanding_market")
    )
    no_sh_facts = facts_debt.filter(
        ~pl.col("metric_id").is_in(["BS_ORDINARY_SHARES_CAPITAL", "BS_TREASURY_SHARES"])
    )
    no_sh_df = compute_merton_dtd_series(no_sh_prices, no_sh_facts, ticker="HBC")
    no_sh_mon = {r["trade_date"]: r for r in no_sh_df.to_dicts()}[mon_dt]
    assert no_sh_mon["dtd_flag"] == "MISSING_SHARES"
    assert no_sh_mon["merton_dtd"] is None

    # 6. Verify build_price_risk_overlay & DashboardQueryService.get_company_market_overlay expose Merton DTD fields!
    iss = _issuer("HBC", "HBC")
    overlay = build_price_risk_overlay(
        prices.filter(pl.col("trade_date") <= date(2025, 5, 10)),
        ticker="HBC",
        financial_periods=prepare_periods(
            facts_debt, iss, as_of=date(2025, 5, 10), market_prices=prices
        ),
        as_of=date(2025, 5, 10),
    )
    mon_pt = next(p for p in overlay["price_series"] if p["trade_date"] == mon_dt)
    assert mon_pt["dtd_flag"] == "VALID"
    assert mon_pt["merton_dtd"] == pytest.approx(expected_dtd, rel=1e-9)

    data_root = tmp_path / "data"
    (data_root / "market_prices").mkdir(parents=True, exist_ok=True)
    prices.filter(pl.col("trade_date") <= date(2025, 5, 10)).write_parquet(
        data_root / "market_prices" / "market_prices.parquet"
    )
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("r_dtd", "MANUAL", {}, None)
    from finintel.analytics.tables import build_analytics_tables

    tables = build_analytics_tables(
        facts_debt, iss, as_of=date(2025, 5, 10), market_prices=prices
    )
    pub = SnapshotPublisher(data_root / "published" / "snapshots", state_store=state)
    pub.publish(
        "snap_dtd_01",
        {
            "issuers.parquet": iss,
            "financial_facts.parquet": facts_debt,
            RATIOS_TABLE: tables[RATIOS_TABLE],
            BENCHMARKS_TABLE: tables[BENCHMARKS_TABLE],
            RED_FLAGS_TABLE: tables[RED_FLAGS_TABLE],
            ML_SCORES_TABLE: pl.DataFrame(schema=ML_SCORES_SCHEMA),
            FILINGS_MANIFEST_TABLE: pl.DataFrame(schema=FILINGS_MANIFEST_SCHEMA),
        },
        run_id="r_dtd",
    )
    state.finish_run("r_dtd", "SUCCEEDED", "snap_dtd_01")
    svc = DashboardQueryService(ParquetRepository(data_root=data_root))
    srv_overlay = svc.get_company_market_overlay("HBC", as_of=date(2025, 5, 10))
    assert srv_overlay["latest_valuation"] is not None
    assert srv_overlay["latest_valuation"]["dtd_flag"] == "VALID"
    assert srv_overlay["latest_valuation"]["merton_dtd"] is not None

    # 7. P1-2, P2-1, P3-1: Post-split share-count check (2025-05-06), multi-revision Q2 (Jul vs Aug)
    #    without retroactive erasure, non-regressing period_end_date, >14d zero-volume halt,
    #    and no post-as_of debt restatement leak in get_company_market_overlay!
    post_split_pt = next(
        p for p in overlay["price_series"] if p["trade_date"] == date(2025, 5, 6)
    )
    assert post_split_pt["dtd_flag"] == "VALID"
    assert post_split_pt["market_cap_vnd"] == pytest.approx(
        by_date[date(2025, 5, 6)]["market_cap_vnd"], rel=1e-9
    )

    # Zero-volume >14d halt after 2025-05-06 must mark INSUFFICIENT_TRADING_SESSIONS (P2-1)
    halted_prices = prices.with_columns(
        pl.when(pl.col("trade_date") >= date(2025, 5, 1))
        .then(pl.lit(0.0))
        .otherwise(pl.col("volume"))
        .alias("volume")
    )
    halted_dtd = compute_merton_dtd_series(halted_prices, facts_debt, ticker="HBC")
    halted_may20 = {r["trade_date"]: r for r in halted_dtd.to_dicts()}[
        date(2025, 5, 20)
    ]
    assert halted_may20["dtd_flag"] == "INSUFFICIENT_TRADING_SESSIONS"

    # Multi-revision Q1 (Apr 28 low debt 400B -> May 20 restated high debt 900B) + late 2024-Q3 filing on May 2
    q1_restated = facts_debt.filter(
        pl.col("period_end_date") == date(2025, 3, 31)
    ).with_columns(
        pl.lit(date(2025, 5, 20)).alias("published_at"),
        pl.lit(date(2025, 5, 20)).alias("effective_available_at"),
        pl.lit(2, dtype=facts_debt.schema["revision_no"]).alias("revision_no"),
        pl.lit("CORRECTION").alias("revision_reason"),
        pl.lit("HBC-2025-Q1-rev2").alias("filing_id"),
        pl.when(pl.col("metric_id") == "BS_SHORT_TERM_BORROWINGS")
        .then(pl.lit(600_000_000_000.0))
        .otherwise(pl.col("value"))
        .alias("value"),
    )
    late_old_period = _facts_rows(
        [
            (
                2024,
                3,
                date(2025, 5, 2),
                {
                    "BS_TOTAL_ASSETS": 2_000_000_000_000.0,
                    "BS_TOTAL_LIABILITIES": 1_500_000_000_000.0,
                    "BS_TOTAL_EQUITY": 500_000_000_000.0,
                    "BS_SHORT_TERM_BORROWINGS": 1_200_000_000_000.0,
                    "BS_LONG_TERM_BORROWINGS": 0.0,
                    "BS_CONVERTIBLE_BONDS": 0.0,
                    "BS_ORDINARY_SHARES_CAPITAL": 500_000_000_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                },
            )
        ],
        issuer_id="HBC",
        ticker="HBC",
    )
    multi_rev_facts = pl.concat(
        [facts_debt, q1_restated, late_old_period], how="vertical"
    )
    multi_dtd = {
        r["trade_date"]: r
        for r in compute_merton_dtd_series(
            prices, multi_rev_facts, ticker="HBC"
        ).to_dicts()
    }
    # May 6 must preserve original Apr 28 Q1 debt (400B), NOT May 20 restatement (900B) and NOT regressed 2024-Q3 (1200B)!
    assert multi_dtd[date(2025, 5, 6)]["default_point_vnd"] == pytest.approx(400e9)
    assert multi_dtd[date(2025, 5, 21)]["default_point_vnd"] == pytest.approx(900e9)

    # Serving overlay with as_of=2025-05-10 on snapshot containing May 20 restatement must NOT leak 900B debt!
    state.start_run("r_dtd_2", "MANUAL", {}, None)
    pub.publish(
        "snap_dtd_02",
        {
            "issuers.parquet": iss,
            "financial_facts.parquet": multi_rev_facts,
            RATIOS_TABLE: tables[RATIOS_TABLE],
            BENCHMARKS_TABLE: tables[BENCHMARKS_TABLE],
            RED_FLAGS_TABLE: tables[RED_FLAGS_TABLE],
            ML_SCORES_TABLE: pl.DataFrame(schema=ML_SCORES_SCHEMA),
            FILINGS_MANIFEST_TABLE: pl.DataFrame(schema=FILINGS_MANIFEST_SCHEMA),
        },
        run_id="r_dtd_2",
    )
    state.finish_run("r_dtd_2", "SUCCEEDED", "snap_dtd_02")
    prices.filter(pl.col("trade_date") <= date(2025, 5, 25)).write_parquet(
        data_root / "market_prices" / "market_prices.parquet"
    )
    srv_cut = DashboardQueryService(
        ParquetRepository(data_root=data_root)
    ).get_company_market_overlay("HBC", as_of=date(2025, 5, 10))
    srv_mon_cut = next(p for p in srv_cut["price_series"] if p["trade_date"] == mon_dt)
    assert srv_mon_cut["merton_dtd"] == pytest.approx(expected_dtd, rel=1e-9)


def test_task08_merton_dtd_walk_forward_ablation_pit_monotonicity_and_shap_vn() -> None:
    """TASK-08: Walk-forward ML ablation aligns _period_available_at forward (14d), enforces monotonicity, and labels SHAP in VN."""
    import math

    import numpy as np

    from finintel.analytics.merton_dtd import attach_merton_dtd_to_periods
    from finintel.ml.supervised.explain import (
        explain_distress,
        get_feature_display_names,
    )
    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        MERTON_ABLATION_FEATURES,
        _constraints,
        score_distress_model,
        train_distress_model,
    )

    # 1. Verify constraints & Vietnamese display names
    ablation_feats = (*BASE_FEATURES, "merton_dtd", "equity_volatility_1y", "naive_edf")
    cmap = dict(zip(ablation_feats, _constraints(ablation_feats), strict=True))
    assert cmap["merton_dtd"] == -1
    assert cmap["equity_volatility_1y"] == 1
    assert cmap["naive_edf"] == 1
    assert "merton_dtd" in MERTON_ABLATION_FEATURES
    vn_names = get_feature_display_names("PIT_REGIME_SYMMETRIC")
    assert "Merton" in vn_names["merton_dtd"]
    assert "Biến động" in vn_names["equity_volatility_1y"]
    assert "EDF" in vn_names["naive_edf"]

    # 2. Verify Saturday _period_available_at aligns strictly FORWARD onto Monday (never Friday!)
    sessions: list[tuple[date, float, float, float | None]] = []
    cur = date(2024, 11, 1)
    while cur <= date(2025, 5, 5):
        if cur.weekday() < 5:
            # Crash -50% on Monday 2025-04-28 so Friday vs Monday DTD differ sharply!
            px = 10_000.0 if cur >= date(2025, 4, 28) else 20_000.0
            sessions.append((cur, px, px, 100_000_000.0))
        cur += timedelta(days=1)
    prices = _prices_frame(sessions, issuer_id="FPT", ticker="FPT")
    periods_sat = pl.DataFrame(
        {
            "issuer_id": ["FPT", "FPT"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "fiscal_year": [2024, 2025],
            "fiscal_quarter": [4, 1],
            "period_end_date": [date(2024, 12, 31), date(2025, 3, 31)],
            "_period_available_at": [
                date(2025, 1, 24),
                date(2025, 4, 26),
            ],  # Q1 disclosed Sat 2025-04-26!
            "BS_TOTAL_ASSETS": [2_000e9, 2_000e9],
            "BS_TOTAL_LIABILITIES": [800e9, 800e9],
            "BS_SHORT_TERM_BORROWINGS": [200e9, 200e9],
            "BS_LONG_TERM_BORROWINGS": [200e9, 200e9],
            "BS_CONVERTIBLE_BONDS": [0.0, 0.0],
            "estimated_shares_outstanding": [100_000_000.0, 100_000_000.0],
        }
    )
    # If as_of = Sunday 2025-04-27 (before Monday 2025-04-28 session exists!), Q1 must NOT look back to Friday 2025-04-25!
    attached_sun = attach_merton_dtd_to_periods(
        periods_sat, prices, as_of=date(2025, 4, 27)
    )
    q1_sun = attached_sun.filter(pl.col("fiscal_year") == 2025).row(0, named=True)
    assert q1_sun["merton_dtd"] is None

    # Once as_of = Monday 2025-04-28, Q1 aligns forward to Monday's post-crash DTD AND advances _period_available_at to Monday (P1-3)!
    from finintel.exceptions import ContractViolationError

    attached_mon = attach_merton_dtd_to_periods(
        periods_sat, prices, as_of=date(2025, 4, 28)
    )
    q1_mon = attached_mon.filter(pl.col("fiscal_year") == 2025).row(0, named=True)
    assert q1_mon["merton_dtd"] is not None
    assert q1_mon["dtd_flag"] == "VALID"
    assert q1_mon["_period_available_at"] == date(2025, 4, 28)

    with pytest.raises(ContractViolationError):
        attach_merton_dtd_to_periods(
            periods_sat.with_columns(pl.lit(0).alias("distress_target")),
            prices,
            as_of=date(2025, 4, 28),
        )

    # 3. Walk-Forward Ablation fit + monotone decreasing risk in merton_dtd + TreeSHAP additivity
    def _fold(seed: int, n_rows: int, yr: int) -> pl.DataFrame:
        rng = np.random.default_rng(seed)
        dtd_vals = rng.uniform(-1.5, 6.0, size=n_rows)
        vol_vals = rng.uniform(0.15, 0.85, size=n_rows)
        edf_vals = 0.5 * np.vectorize(math.erfc)(dtd_vals / math.sqrt(2.0))
        roa_vals = rng.uniform(-0.12, 0.18, size=n_rows)
        logits = (
            -0.9 * dtd_vals
            + 1.5 * vol_vals
            - 4.0 * roa_vals
            + rng.normal(0.0, 0.4, size=n_rows)
            - 0.3
        )
        y = (logits > 0.0).astype(int).tolist()
        cols: dict[str, list[float]] = {}
        for idx_f, fname in enumerate(BASE_FEATURES):
            cols[fname] = (
                roa_vals.tolist()
                if fname == "roa"
                else rng.normal(0.2 + 0.01 * idx_f, 0.2, size=n_rows).tolist()
            )
        cols["merton_dtd"] = dtd_vals.tolist()
        cols["equity_volatility_1y"] = vol_vals.tolist()
        cols["naive_edf"] = edf_vals.tolist()
        return pl.DataFrame(
            {
                **cols,
                "issuer_id": [f"ABL_{i:03d}" for i in range(n_rows)],
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "period_type": ["QUARTER"] * n_rows,
                "fiscal_year": [yr] * n_rows,
                "fiscal_quarter": [(i % 4) + 1 for i in range(n_rows)],
                "period_end_date": [date(yr, 12, 31)] * n_rows,
                "filing_id": [f"F_{seed}_{i:03d}" for i in range(n_rows)],
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(yr + 1, 1, 25)] * n_rows,
                "distress_target": y,
            }
        )

    tr_df = _fold(11, 200, 2021)
    va_df = _fold(22, 100, 2023)
    trained = train_distress_model(tr_df, va_df, features=ablation_feats)
    probe_row = _fold(33, 1, 2024)
    dtd_grid = [-1.5, -0.5, 0.5, 1.5, 3.0, 5.0]
    probes = pl.concat(
        [probe_row.with_columns(pl.lit(v).alias("merton_dtd")) for v in dtd_grid]
    )
    scores = score_distress_model(trained, probes)["distress_risk_score"].to_numpy()
    assert np.all(np.diff(scores) <= 1e-12)
    assert scores[-1] < scores[0]

    explained = explain_distress(trained, va_df.head(5))
    assert explained.height == 5
