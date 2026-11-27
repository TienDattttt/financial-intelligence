"""Unit and integration tests for FinIntel V3.0 Module v3-valuation-mart-and-screener (V3-04..V3-06).

Verified Sources:
- spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.1, §5.2, §7)
- Circular 96/2020/TT-BTC (PIT disclosure timing & forward alignment)
- Polars join_asof(..., strategy="forward") zero look-ahead contract
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
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


def test_pit_valuation_mart_catalog_and_b03_extension_port_preservation(
    tmp_path: Path,
) -> None:
    """PIT_VALUATION_MART_TABLE is managed cleanly while regulatory_events and market_prices remain untouched (B-03)."""
    from finintel.pipeline.state_store import StateStore
    from finintel.storage.ducklake_store import (
        EXTENSION_PORT_CATALOG_TABLES,
        MANAGED_MART_CATALOG_TABLES,
        MANAGED_MART_TABLES,
        OPTIONAL_MANAGED_MART_TABLES,
        DuckLakeStore,
        ensure_ducklake_attached,
    )
    from finintel.storage.schemas import PIT_VALUATION_MART_TABLE

    assert len(MANAGED_MART_TABLES) == 10
    assert PIT_VALUATION_MART_TABLE in OPTIONAL_MANAGED_MART_TABLES
    assert "pit_valuation_mart" in MANAGED_MART_CATALOG_TABLES
    assert MANAGED_MART_CATALOG_TABLES.isdisjoint(EXTENSION_PORT_CATALOG_TABLES)

    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )
    issuers = _make_issuers(
        [
            {
                "issuer_id": "VN_FPT",
                "ticker": "FPT",
                "exchange": "HOSE",
                "company_name_vn": "FPT Corp",
                "icb_code": "9530",
                "icb_code_level2": "9500",
                "icb_name_level2": "Technology",
                "icb_code_level3": "9530",
                "icb_name_level3": "Software & Computer Services",
                "accounting_regime": "CORPORATE",
                "is_listed": True,
                "industry_snapshot_date": date(2026, 6, 1),
                "delisted_date": None,
            }
        ]
    )
    facts = _make_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 1_000_000_000_000.0,
                "BS_TOTAL_LIABILITIES": 400_000_000_000.0,
                "BS_TOTAL_EQUITY": 600_000_000_000.0,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000_000_000.0,
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_INCOME_PARENT": 80_000_000_000.0,
                "CF_OPERATING_CASH_FLOW": 100_000_000_000.0,
            }
        },
        issuer_id="VN_FPT",
        available_overrides={(2025, None): date(2026, 4, 20)},
    )
    prices = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT"],
            "ticker": ["FPT"],
            "trade_date": [date(2026, 4, 20)],
            "close_raw": [120_000.0],
            "close_adj": [100_000.0],
            "volume": [1_000_000.0],
            "shares_outstanding_market": [10_000_000.0],
            "source_provider": ["test"],
        }
    )
    periods = prepare_periods(facts, issuers, as_of=date(2026, 6, 1))
    ratios = calculate_ratios(periods)
    mart = build_pit_valuation_mart(ratios, prices, facts=facts, as_of=date(2026, 6, 1))

    state.start_run("run-1", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_001",
        {
            "issuers.parquet": issuers,
            "financial_facts.parquet": facts,
            PIT_VALUATION_MART_TABLE: mart,
        },
        run_id="run-1",
    )
    state.finish_run("run-1", "SUCCEEDED", "snap_001")

    con = store._open_rw_connection()
    try:
        reg_df = pl.DataFrame(
            {
                "issuer_id": ["VN_FPT"],
                "event_date": [date(2026, 1, 15)],
                "effective_from": [date(2026, 1, 16)],
                "resolution_date": [None],
                "event_type": ["WARNING"],
                "event_stage": ["FIRST_ENTRY"],
                "exchange_delisted_from": [None],
                "source_provider": ["hsx"],
                "source_url": ["https://hsx.vn/test"],
                "raw_headline": ["Test event"],
                "payload_sha256": ["a" * 64],
            }
        )
        con.register("_stage_reg", reg_df)
        con.register("_stage_px", prices)
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."regulatory_events" AS SELECT * FROM "_stage_reg";'
        )
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."market_prices" AS SELECT * FROM "_stage_px";'
        )
    finally:
        con.close()

    state.start_run("run-2", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_002",
        {
            "issuers.parquet": issuers,
            "financial_facts.parquet": facts,
        },
        run_id="run-2",
    )
    state.finish_run("run-2", "SUCCEEDED", "snap_002")

    con_ro = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            con_ro,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=store.catalog_alias,
            read_only=True,
        )
        tables = {
            str(row[0])
            for row in con_ro.execute(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE table_catalog = '{store.catalog_alias}' AND table_schema = 'main'"
            ).fetchall()
        }
        assert "regulatory_events" in tables
        assert "market_prices" in tables
        assert "pit_valuation_mart" not in tables
        assert con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."market_prices"'
        ).fetchone() == (1,)
        assert con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."regulatory_events"'
        ).fetchone() == (1,)
    finally:
        con_ro.close()


def test_incremental_transform_populates_pit_valuation_mart() -> None:
    """run_incremental_analytics_transform builds bundle.pit_valuation_mart from ratios + market_prices."""
    from finintel.transform.incremental import run_incremental_analytics_transform

    issuers = _make_issuers(
        [
            {
                "issuer_id": "VN_FPT",
                "ticker": "FPT",
                "exchange": "HOSE",
                "company_name_vn": "FPT Corp",
                "icb_code": "9530",
                "icb_code_level2": "9500",
                "icb_name_level2": "Technology",
                "icb_code_level3": "9530",
                "icb_name_level3": "Software & Computer Services",
                "accounting_regime": "CORPORATE",
                "is_listed": True,
                "industry_snapshot_date": date(2026, 6, 1),
                "delisted_date": None,
            }
        ]
    )
    facts = _make_facts(
        {
            (2024, None): {
                "BS_TOTAL_ASSETS": 900_000_000_000.0,
                "BS_CURRENT_ASSETS": 400_000_000_000.0,
                "BS_CURRENT_LIABILITIES": 200_000_000_000.0,
                "BS_TOTAL_LIABILITIES": 400_000_000_000.0,
                "BS_TOTAL_EQUITY": 500_000_000_000.0,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000_000_000.0,
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_REVENUE": 700_000_000_000.0,
                "IS_NET_INCOME": 70_000_000_000.0,
                "IS_NET_INCOME_PARENT": 70_000_000_000.0,
                "CF_OPERATING_CASH_FLOW": 85_000_000_000.0,
            },
            (2025, None): {
                "BS_TOTAL_ASSETS": 1_000_000_000_000.0,
                "BS_CURRENT_ASSETS": 450_000_000_000.0,
                "BS_CURRENT_LIABILITIES": 220_000_000_000.0,
                "BS_TOTAL_LIABILITIES": 400_000_000_000.0,
                "BS_TOTAL_EQUITY": 600_000_000_000.0,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000_000_000.0,
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_REVENUE": 800_000_000_000.0,
                "IS_NET_INCOME": 80_000_000_000.0,
                "IS_NET_INCOME_PARENT": 80_000_000_000.0,
                "CF_OPERATING_CASH_FLOW": 100_000_000_000.0,
            },
        },
        issuer_id="VN_FPT",
        available_overrides={
            (2024, None): date(2025, 4, 1),
            (2025, None): date(2026, 4, 20),
        },
    )
    prices = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_FPT"],
            "ticker": ["FPT", "FPT"],
            "trade_date": [date(2025, 4, 1), date(2026, 4, 20)],
            "close_raw": [100_000.0, 120_000.0],
            "close_adj": [80_000.0, 100_000.0],
            "volume": [1_000_000.0, 1_200_000.0],
            "shares_outstanding_market": [10_000_000.0, 10_000_000.0],
            "source_provider": ["test", "test"],
        }
    )
    bundle = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=date(2026, 6, 1),
        run_id="v3-05-transform-test",
        market_prices=prices,
        persist_state=False,
    )
    assert bundle.pit_valuation_mart is not None
    assert bundle.pit_valuation_mart.schema == PIT_VALUATION_MART_SCHEMA
    row_2025 = bundle.pit_valuation_mart.filter(
        pl.col("period_end_date") == date(2025, 12, 31)
    ).row(0, named=True)
    assert row_2025["pe_ttm"] == pytest.approx(15.0)
    assert row_2025["pb_ratio"] == pytest.approx(2.0)


def test_pit_valuation_mart_empty_prices_returns_empty_schema() -> None:
    empty_mart = build_pit_valuation_mart(
        pl.DataFrame(),
        None,
        as_of=date(2026, 6, 1),
    )
    assert empty_mart.is_empty()
    assert empty_mart.schema == PIT_VALUATION_MART_SCHEMA
