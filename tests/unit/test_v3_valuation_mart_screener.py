"""Unit and integration tests for FinIntel V3.0 Module v3-valuation-mart-and-screener (V3-04..V3-06).

Verified Sources:
- spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.1, §5.2, §7)
- Circular 96/2020/TT-BTC (PIT disclosure timing & forward alignment)
- Polars join_asof(..., strategy="forward") zero look-ahead contract
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import RatioStatusFlag, calculate_ratios
from finintel.analytics.valuation_mart import build_pit_valuation_mart
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.storage.schemas import PIT_VALUATION_MART_SCHEMA


def _make_issuers(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _make_facts(
    periods: dict[tuple[int, int | None], dict[str, float | None]],
    *,
    issuer_id: str = "FPT",
    scope: str = "CONSOLIDATED",
    available_overrides: dict[tuple[int, int | None], date] | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for (year, quarter), values in periods.items():
        fiscal_start = date(year, 1, 1)
        start = date(year, 1 + 3 * ((quarter or 1) - 1), 1)
        ends = [
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        ]
        end = ends[(quarter or 4) - 1]
        avail = (
            available_overrides[(year, quarter)]
            if available_overrides and (year, quarter) in available_overrides
            else end + timedelta(days=90 if quarter is None else 45)
        )
        filing = f"{issuer_id}-{year}-{quarter}"
        for metric, value in values.items():
            rows.append(
                {
                    "issuer_id": issuer_id,
                    "statement_scope": scope,
                    "period_type": "ANNUAL" if quarter is None else "QUARTER",
                    "fiscal_year": year,
                    "fiscal_quarter": quarter,
                    "fiscal_year_start_date": fiscal_start,
                    "period_start_date": start,
                    "period_end_date": end,
                    "period_days": (end - start).days + 1,
                    "effective_available_at": avail,
                    "published_at_basis": "STATUTORY_LAG",
                    "revision_no": 1,
                    "filing_id": filing,
                    "source_filing_ids": [filing],
                    "source_provider": "fhsc",
                    "taxonomy_version": (
                        "VAS_TT200_2014" if year < 2026 else "VAS_TT99_2025"
                    ),
                    "revision_reason": "ORIGINAL",
                    "audit_status": "AUDITED" if quarter is None else "UNAUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "derivation_method": "REPORTED_DIRECTLY",
                    "metric_id": metric,
                    "value": value,
                    "value_type": "STOCK" if metric.startswith("BS_") else "FLOW",
                    "comparable_across_taxonomy": True,
                }
            )
    return pl.DataFrame(rows, schema=FACT_SCHEMA)


def test_ocf_to_total_liabilities_valid_and_denominator_guards() -> None:
    """ocf_to_total_liabilities returns exact ratio when liabilities > 0 and NULL + flag otherwise (zero fill_null(0))."""
    issuers = _make_issuers(
        [
            {"issuer_id": "AAA", "ticker": "AAA", "accounting_regime": "CORPORATE"},
            {"issuer_id": "ZERO", "ticker": "ZERO", "accounting_regime": "CORPORATE"},
            {"issuer_id": "MISS", "ticker": "MISS", "accounting_regime": "CORPORATE"},
        ]
    )
    facts_aaa = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 1_000.0,
                "BS_TOTAL_LIABILITIES": 400.0,
                "CF_OPERATING_CASH_FLOW": 100.0,
            }
        },
        issuer_id="AAA",
    )
    facts_zero = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 500.0,
                "BS_TOTAL_LIABILITIES": 0.0,
                "CF_OPERATING_CASH_FLOW": 50.0,
            }
        },
        issuer_id="ZERO",
    )
    facts_miss = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 500.0,
                "CF_OPERATING_CASH_FLOW": 50.0,
            }
        },
        issuer_id="MISS",
    )
    all_facts = pl.concat([facts_aaa, facts_zero, facts_miss])
    periods = prepare_periods(all_facts, issuers, as_of=date(2026, 6, 1))
    ratios = calculate_ratios(periods)

    by_issuer = {row["issuer_id"]: row for row in ratios.to_dicts()}
    assert by_issuer["AAA"]["ocf_to_total_liabilities"] == pytest.approx(0.25)
    assert (
        by_issuer["AAA"]["ocf_to_total_liabilities_flag"] == RatioStatusFlag.VALID.value
    )

    assert by_issuer["ZERO"]["ocf_to_total_liabilities"] is None
    assert (
        by_issuer["ZERO"]["ocf_to_total_liabilities_flag"]
        == RatioStatusFlag.ZERO_OR_NEGATIVE_DENOMINATOR.value
    )

    assert by_issuer["MISS"]["ocf_to_total_liabilities"] is None
    assert (
        by_issuer["MISS"]["ocf_to_total_liabilities_flag"]
        == RatioStatusFlag.MISSING_INPUT.value
    )


def test_pit_valuation_mart_uses_close_raw_on_split_and_zero_weekend_lookahead() -> (
    None
):
    """pit_valuation_mart uses close_raw (never close_adj) on 2:1 split and aligns weekend filings forward to Monday."""
    issuers = _make_issuers(
        [
            {"issuer_id": "SPL", "ticker": "SPL", "accounting_regime": "CORPORATE"},
            {"issuer_id": "LOSS", "ticker": "LOSS", "accounting_regime": "CORPORATE"},
        ]
    )
    # 2026-04-18 is Saturday -> must align forward to Monday 2026-04-20, NEVER Friday 2026-04-17!
    sat_avail = date(2026, 4, 18)
    facts_spl = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 500_000_000_000.0,
                "BS_TOTAL_LIABILITIES": 300_000_000_000.0,
                "BS_TOTAL_EQUITY": 200_000_000_000.0,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000_000_000.0,  # 10M shares @ 10,000 par
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_INCOME_PARENT": 40_000_000_000.0,  # EPS = 4,000 VND/sh
                "CF_OPERATING_CASH_FLOW": 50_000_000_000.0,
            }
        },
        issuer_id="SPL",
        available_overrides={(2025, None): sat_avail},
    )
    # LOSS company has negative EPS and negative equity -> NEGATIVE_DENOMINATOR
    facts_loss = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 100_000_000_000.0,
                "BS_TOTAL_LIABILITIES": 150_000_000_000.0,
                "BS_TOTAL_EQUITY": -50_000_000_000.0,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": 50_000_000_000.0,  # 5M shares
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_INCOME_PARENT": -10_000_000_000.0,
                "CF_OPERATING_CASH_FLOW": -5_000_000_000.0,
            }
        },
        issuer_id="LOSS",
        available_overrides={(2025, None): sat_avail},
    )
    all_facts = pl.concat([facts_spl, facts_loss])
    periods = prepare_periods(all_facts, issuers, as_of=date(2026, 6, 1))
    ratios = calculate_ratios(periods)

    # 2:1 split fixture: close_adj is halved (20,000) vs close_raw (40,000) on Monday 2026-04-20.
    # Friday 2026-04-17 has different price (99,000) to prove zero backward look-ahead.
    prices = pl.DataFrame(
        {
            "issuer_id": ["SPL", "SPL", "LOSS", "LOSS"],
            "ticker": ["SPL", "SPL", "LOSS", "LOSS"],
            "trade_date": [
                date(2026, 4, 17),
                date(2026, 4, 20),
                date(2026, 4, 17),
                date(2026, 4, 20),
            ],
            "close_raw": [99_000.0, 40_000.0, 15_000.0, 10_000.0],
            "close_adj": [49_500.0, 20_000.0, 15_000.0, 10_000.0],
            "volume": [100_000.0, 120_000.0, 50_000.0, 60_000.0],
            "shares_outstanding_market": [
                10_000_000.0,
                10_000_000.0,
                5_000_000.0,
                5_000_000.0,
            ],
            "source_provider": ["test", "test", "test", "test"],
        }
    )

    mart = build_pit_valuation_mart(
        ratios,
        prices,
        facts=all_facts,
        as_of=date(2026, 6, 1),
    )
    assert mart.schema == PIT_VALUATION_MART_SCHEMA
    assert mart.height == 2

    spl_row = mart.filter(pl.col("issuer_id") == "SPL").row(0, named=True)
    # Must match Monday 2026-04-20 (NOT Friday 2026-04-17) and use close_raw (40,000, NOT close_adj 20,000)
    assert spl_row["effective_trade_date"] == date(2026, 4, 20)
    assert spl_row["close_raw_vnd"] == pytest.approx(40_000.0)
    assert spl_row["pit_shares_outstanding"] == pytest.approx(10_000_000.0)
    assert spl_row["market_cap_vnd"] == pytest.approx(400_000_000_000.0)
    assert spl_row["market_cap_flag"] == "OK"
    assert spl_row["pe_ttm"] == pytest.approx(10.0)
    assert spl_row["pe_flag"] == "OK"
    assert spl_row["pb_ratio"] == pytest.approx(2.0)
    assert spl_row["pb_flag"] == "OK"

    loss_row = mart.filter(pl.col("issuer_id") == "LOSS").row(0, named=True)
    assert loss_row["effective_trade_date"] == date(2026, 4, 20)
    assert loss_row["market_cap_vnd"] == pytest.approx(50_000_000_000.0)
    assert loss_row["market_cap_flag"] == "OK"
    assert loss_row["pe_ttm"] is None
    assert loss_row["pe_flag"] == "NEGATIVE_DENOMINATOR"
    assert loss_row["pb_ratio"] is None
    assert loss_row["pb_flag"] == "NEGATIVE_DENOMINATOR"


def test_pit_valuation_mart_empty_prices_returns_empty_schema() -> None:
    empty_mart = build_pit_valuation_mart(
        pl.DataFrame(),
        None,
        as_of=date(2026, 6, 1),
    )
    assert empty_mart.is_empty()
    assert empty_mart.schema == PIT_VALUATION_MART_SCHEMA
