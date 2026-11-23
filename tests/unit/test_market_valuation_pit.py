"""TDD tests for PIT market valuation tolerance, date_basis, and OOS model_markers (P0-2, P2-6)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from finintel.analytics.market_valuation import (
    build_price_risk_overlay,
    compute_pit_valuation_series,
)


def _prices_frame(
    rows: list[tuple[date, float, float, float | None]],
    *,
    issuer_id: str = "HBC",
    ticker: str = "HBC",
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


def test_p0_2_model_markers_exclude_in_sample_train_rows() -> None:
    """Only OUT_OF_SAMPLE* rows may emit early-warning model_markers (zero IN_SAMPLE_TRAIN leakage)."""
    start = date(2019, 1, 2)
    sessions = [
        start + timedelta(days=i)
        for i in range(1900)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    prices = _prices_frame(
        [(d, 10_000.0, 10_000.0, 10_000_000.0) for d in sessions],
        issuer_id="HBC",
        ticker="HBC",
    )

    ml_scores = pl.DataFrame(
        {
            "issuer_id": ["HBC", "HBC", "HBC", "HBC"],
            "statement_scope": ["CONSOLIDATED"] * 4,
            "period_type": ["QUARTER"] * 4,
            "period_end_date": [
                date(2019, 3, 31),
                date(2020, 12, 31),
                date(2022, 12, 31),
                date(2025, 3, 31),
            ],
            "available_at": [
                date(2019, 4, 29),
                date(2021, 1, 29),
                date(2023, 1, 30),
                date(2025, 4, 29),
            ],
            "model_role": [
                "IN_SAMPLE_TRAIN",
                "IN_SAMPLE_TRAIN",
                "OUT_OF_SAMPLE_WALK_FORWARD",
                "OUT_OF_SAMPLE_LIVE",
            ],
            "risk_tier": ["R5", "R4", "R4", "R5"],
            "calibrated_distress_prob": [0.40, 0.20, 0.14, 0.32],
            "risk_market_percentile": [97.0, 93.0, 91.0, 98.0],
            "relative_risk_lift": [16.0, 8.0, 5.6, 12.8],
        }
    )

    overlay = build_price_risk_overlay(prices, ticker="HBC", ml_scores=ml_scores)
    assert len(overlay["model_markers"]) == 2
    r4, r5 = overlay["model_markers"]
    assert r4["period_end_date"] == date(2022, 12, 31)
    assert r4["marker_kind"] == "FIRST_R4_UPGRADE"
    assert r5["period_end_date"] == date(2025, 3, 31)
    assert r5["marker_kind"] == "FIRST_R5_UPGRADE"

    in_sample_only = ml_scores.filter(pl.col("model_role") == "IN_SAMPLE_TRAIN")
    overlay_empty = build_price_risk_overlay(
        prices, ticker="HBC", ml_scores=in_sample_only
    )
    assert overlay_empty["model_markers"] == []


def test_p2_6_valuation_join_asof_180d_tolerance_stops_stale_carryforward() -> None:
    """Fundamentals are not carried forward past 180 days when an issuer stops filing."""
    prices = _prices_frame(
        [
            (date(2024, 4, 29), 20_000.0, 20_000.0, 10_000_000.0),  # 0d -> VALID
            (date(2024, 5, 2), 20_000.0, 20_000.0, 10_000_000.0),  # +3d -> VALID
            (date(2024, 10, 25), 18_000.0, 18_000.0, 10_000_000.0),  # +179d -> VALID
            (
                date(2024, 10, 28),
                15_000.0,
                15_000.0,
                10_000_000.0,
            ),  # +182d -> STALE (>180d)
        ],
        issuer_id="FPT",
        ticker="FPT",
    )
    periods = pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2024, 3, 31)],
            "published_at": [date(2024, 4, 29)],
            "estimated_shares_outstanding": [10_000_000.0],
            "IS_NET_INCOME_PARENT_TTM": [100_000_000_000.0],
            "parent_equity": [100_000_000_000.0],
        }
    )

    val = compute_pit_valuation_series(prices, periods, ticker="FPT")
    rows = {r["trade_date"]: r for r in val.to_dicts()}

    assert rows[date(2024, 5, 2)]["pe_flag"] == "VALID"
    assert rows[date(2024, 5, 2)]["pe_ttm"] == pytest.approx(2.0)
    assert rows[date(2024, 10, 25)]["pe_flag"] == "VALID"
    assert rows[date(2024, 10, 25)]["pe_ttm"] == pytest.approx(1.8)

    stale = rows[date(2024, 10, 28)]
    assert stale["period_end_date"] is None
    assert stale["pe_ttm"] is None
    assert stale["pb_ratio"] is None
    assert stale["pe_flag"] == "MISSING_EARNINGS"
    assert stale["pb_flag"] == "MISSING_EQUITY"


def test_p2_6_model_markers_per_row_date_basis_pit_vs_fallback() -> None:
    """Each model marker carries its own row-level date_basis (PIT_DISCLOSURE vs PERIOD_END_FALLBACK)."""
    start = date(2023, 1, 2)
    sessions = [
        start + timedelta(days=i)
        for i in range(400)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    prices = _prices_frame(
        [(d, 10_000.0, 10_000.0, 10_000_000.0) for d in sessions],
        issuer_id="HBC",
        ticker="HBC",
    )
    ml_scores = pl.DataFrame(
        {
            "issuer_id": ["HBC", "HBC"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "period_end_date": [date(2023, 3, 31), date(2023, 6, 30)],
            "model_role": ["OUT_OF_SAMPLE_WALK_FORWARD", "OUT_OF_SAMPLE_WALK_FORWARD"],
            "risk_tier": ["R4", "R5"],
            "calibrated_distress_prob": [0.15, 0.35],
            "risk_market_percentile": [92.0, 98.0],
            "relative_risk_lift": [6.0, 14.0],
        }
    )
    # financial_periods only has 2023-03-31 -> 2023-06-30 must get PERIOD_END_FALLBACK!
    financial_periods = pl.DataFrame(
        {
            "issuer_id": ["HBC"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2023, 3, 31)],
            "published_at": [date(2023, 4, 28)],
        }
    )

    overlay = build_price_risk_overlay(
        prices,
        ticker="HBC",
        ml_scores=ml_scores,
        financial_periods=financial_periods,
    )
    assert len(overlay["model_markers"]) == 2
    r4, r5 = overlay["model_markers"]
    assert r4["disclosure_date"] == date(2023, 4, 28)
    assert r4["date_basis"] == "PIT_DISCLOSURE"
    assert r5["disclosure_date"] == date(2023, 6, 30)
    assert r5["date_basis"] == "PERIOD_END_FALLBACK"
