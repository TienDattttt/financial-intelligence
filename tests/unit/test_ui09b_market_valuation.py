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
