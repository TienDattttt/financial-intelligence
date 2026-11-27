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


def _publish_v3_serving_snapshot(tmp_path: Path) -> tuple[Path, str]:
    from finintel.analytics.tables import build_analytics_tables
    from finintel.pipeline.state_store import StateStore
    from finintel.storage.schemas import (
        AUDIT_QUARANTINE_SCHEMA,
        AUDIT_QUARANTINE_TABLE,
        BENCHMARKS_TABLE,
        FILINGS_MANIFEST_TABLE,
        ML_SCORES_SCHEMA,
        ML_SCORES_TABLE,
        PIT_VALUATION_MART_TABLE,
        QUALITY_SUMMARY_SCHEMA,
        QUALITY_SUMMARY_TABLE,
        RATIOS_TABLE,
        RED_FLAGS_TABLE,
    )
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    data_root = tmp_path / "serving_data"
    issuers = _make_issuers(
        [
            {
                "issuer_id": "VN_FPT",
                "ticker": "FPT",
                "exchange": "HOSE",
                "company_name_vn": "CTCP FPT",
                "icb_code": "9530",
                "icb_code_level2": "9500",
                "icb_name_level2": "Technology",
                "icb_code_level3": "9530",
                "icb_name_level3": "Software",
                "accounting_regime": "CORPORATE",
                "is_listed": True,
                "industry_snapshot_date": date(2026, 6, 1),
                "delisted_date": None,
            },
            {
                "issuer_id": "VN_HPG",
                "ticker": "HPG",
                "exchange": "HOSE",
                "company_name_vn": "CTCP Tap doan Hoa Phat",
                "icb_code": "1750",
                "icb_code_level2": "1700",
                "icb_name_level2": "Basic Resources",
                "icb_code_level3": "1750",
                "icb_name_level3": "Industrial Metals",
                "accounting_regime": "CORPORATE",
                "is_listed": True,
                "industry_snapshot_date": date(2026, 6, 1),
                "delisted_date": None,
            },
            {
                "issuer_id": "VN_DGC",
                "ticker": "DGC",
                "exchange": "HNX",
                "company_name_vn": "CTCP Duc Giang",
                "icb_code": "1350",
                "icb_code_level2": "1300",
                "icb_name_level2": "Chemicals",
                "icb_code_level3": "1350",
                "icb_name_level3": "Specialty Chemicals",
                "accounting_regime": "CORPORATE",
                "is_listed": True,
                "industry_snapshot_date": date(2026, 6, 1),
                "delisted_date": None,
            },
        ]
    )

    def _base_metrics(
        *,
        rev_24: float,
        rev_25: float,
        ni_25: float,
        cfo_25: float,
        liab_25: float,
        eq_25: float,
        debt_25: float,
        shares_cap: float,
    ) -> dict[tuple[int, int | None], dict[str, float | None]]:
        return {
            (2024, None): {
                "BS_TOTAL_ASSETS": eq_25 + liab_25,
                "BS_CURRENT_ASSETS": 400_000_000_000.0,
                "BS_CURRENT_LIABILITIES": 200_000_000_000.0,
                "BS_TOTAL_LIABILITIES": liab_25,
                "BS_TOTAL_EQUITY": eq_25,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_SHORT_TERM_BORROWINGS": debt_25,
                "BS_LONG_TERM_BORROWINGS": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": shares_cap,
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_REVENUE": rev_24,
                "IS_GROSS_PROFIT": rev_24 * 0.3,
                "IS_OPERATING_PROFIT": rev_24 * 0.2,
                "IS_PROFIT_BEFORE_TAX": rev_24 * 0.18,
                "IS_INTEREST_EXPENSE": 5_000_000_000.0,
                "IS_NET_INCOME": ni_25,
                "IS_NET_INCOME_PARENT": ni_25,
                "CF_OPERATING_CASH_FLOW": cfo_25,
                "CF_CAPITAL_EXPENDITURE": -20_000_000_000.0,
            },
            (2025, None): {
                "BS_TOTAL_ASSETS": eq_25 + liab_25,
                "BS_CURRENT_ASSETS": 450_000_000_000.0,
                "BS_CURRENT_LIABILITIES": 220_000_000_000.0,
                "BS_TOTAL_LIABILITIES": liab_25,
                "BS_TOTAL_EQUITY": eq_25,
                "BS_MINORITY_INTEREST": 0.0,
                "BS_SHORT_TERM_BORROWINGS": debt_25,
                "BS_LONG_TERM_BORROWINGS": 0.0,
                "BS_ORDINARY_SHARES_CAPITAL": shares_cap,
                "BS_TREASURY_SHARES": 0.0,
                "IS_NET_REVENUE": rev_25,
                "IS_GROSS_PROFIT": rev_25 * 0.35,
                "IS_OPERATING_PROFIT": rev_25 * 0.25,
                "IS_PROFIT_BEFORE_TAX": rev_25 * 0.22,
                "IS_INTEREST_EXPENSE": 5_000_000_000.0,
                "IS_NET_INCOME": ni_25,
                "IS_NET_INCOME_PARENT": ni_25,
                "CF_OPERATING_CASH_FLOW": cfo_25,
                "CF_CAPITAL_EXPENDITURE": -25_000_000_000.0,
            },
        }

    # FPT: ROE=12%, ROA=6%, EPS=6,000, liab/assets=0.5, D/E=0.4, CFO=90B, OCF/Liab=0.15, rev_growth=+25%, PE=15, PB=1.8, MCap=900B
    # HPG: ROE=30%, ROA=20%, EPS=15,000, liab/assets=0.333, D/E=0.1, CFO=350B, OCF/Liab=0.70, rev_growth=+50%, PE=10, PB=3.0, MCap=3,000B
    # DGC: ROE=-10%, ROA=-5%, EPS=-2,000, liab/assets=0.5, D/E=0.8, CFO=-20B, OCF/Liab=-0.10, rev_growth=-10%, PE=None, PB=0.8, MCap=160B
    facts = pl.concat(
        [
            _make_facts(
                _base_metrics(
                    rev_24=800_000_000_000.0,
                    rev_25=1_000_000_000_000.0,
                    ni_25=60_000_000_000.0,
                    cfo_25=90_000_000_000.0,
                    liab_25=600_000_000_000.0,
                    eq_25=500_000_000_000.0,
                    debt_25=200_000_000_000.0,
                    shares_cap=100_000_000_000.0,
                ),
                issuer_id="VN_FPT",
                available_overrides={
                    (2024, None): date(2025, 3, 20),
                    (2025, None): date(2026, 3, 20),
                },
            ),
            _make_facts(
                _base_metrics(
                    rev_24=1_000_000_000_000.0,
                    rev_25=1_500_000_000_000.0,
                    ni_25=300_000_000_000.0,
                    cfo_25=350_000_000_000.0,
                    liab_25=500_000_000_000.0,
                    eq_25=1_000_000_000_000.0,
                    debt_25=100_000_000_000.0,
                    shares_cap=200_000_000_000.0,
                ),
                issuer_id="VN_HPG",
                available_overrides={
                    (2024, None): date(2025, 3, 20),
                    (2025, None): date(2026, 3, 20),
                },
            ),
            _make_facts(
                _base_metrics(
                    rev_24=500_000_000_000.0,
                    rev_25=450_000_000_000.0,
                    ni_25=-20_000_000_000.0,
                    cfo_25=-20_000_000_000.0,
                    liab_25=200_000_000_000.0,
                    eq_25=200_000_000_000.0,
                    debt_25=160_000_000_000.0,
                    shares_cap=100_000_000_000.0,
                ),
                issuer_id="VN_DGC",
                available_overrides={
                    (2024, None): date(2025, 3, 20),
                    (2025, None): date(2026, 3, 20),
                },
            ),
        ]
    )
    prices = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_HPG", "VN_DGC"],
            "ticker": ["FPT", "HPG", "DGC"],
            "trade_date": [date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 20)],
            "close_raw": [90_000.0, 150_000.0, 16_000.0],
            "close_adj": [70_000.0, 120_000.0, 16_000.0],
            "volume": [1_000_000.0, 2_000_000.0, 500_000.0],
            "shares_outstanding_market": [10_000_000.0, 20_000_000.0, 10_000_000.0],
            "source_provider": ["test", "test", "test"],
        }
    )

    analytics = build_analytics_tables(facts, issuers, as_of=date(2026, 6, 1))
    ratios_df = analytics[RATIOS_TABLE]
    benchmarks_df = analytics[BENCHMARKS_TABLE]
    red_flags_df = analytics[RED_FLAGS_TABLE]
    mart_df = build_pit_valuation_mart(
        ratios_df, prices, facts=facts, as_of=date(2026, 6, 1)
    )

    ml_rows: list[dict[str, object]] = []
    for row in ratios_df.filter(
        pl.col("period_end_date") == date(2025, 12, 31)
    ).iter_rows(named=True):
        iid = str(row["issuer_id"])
        tkr = str(row["ticker"])
        ml_rows.append(
            {
                "issuer_id": iid,
                "ticker": tkr,
                "statement_scope": "CONSOLIDATED",
                "period_type": "ANNUAL",
                "period_end_date": date(2025, 12, 31),
                "filing_id": f"{iid}-2025-None",
                "altman_z_double_prime": 5.0 if iid != "VN_DGC" else 1.0,
                "altman_em_score": 5.0 if iid != "VN_DGC" else 1.0,
                "altman_zone": "SAFE_ZONE" if iid != "VN_DGC" else "DISTRESS_ZONE",
                "piotroski_f_score": 8
                if iid == "VN_HPG"
                else (6 if iid == "VN_FPT" else 3),
                "beneish_m8_score": -2.5,
                "beneish_flag": "LOW_RISK",
                "pca_pc1": 0.1,
                "pca_pc2": 0.2,
                "pca_pc3": 0.0,
                "cluster_id": 0 if iid == "VN_FPT" else 1,
                "cluster_archetype_name": "Balanced" if iid == "VN_FPT" else "Growth",
                "anomaly_score_raw": 0.1 if iid == "VN_FPT" else 0.9,
                "anomaly_percentile_rank": 0.2 if iid == "VN_FPT" else 0.95,
                "statistical_outlier_flag": iid != "VN_FPT",
                "anomaly_flag": (
                    None
                    if iid == "VN_FPT"
                    else (
                        "ATYPICAL_POSITIVE" if iid == "VN_HPG" else "ATYPICAL_NEGATIVE"
                    )
                ),
                "distress_risk_score": 0.05 if iid != "VN_DGC" else 0.45,
                "calibrated_distress_prob": 0.02
                if iid == "VN_HPG"
                else (0.04 if iid == "VN_FPT" else 0.40),
                "risk_tier": "R1" if iid != "VN_DGC" else "R4",
                "risk_market_percentile": 0.1 if iid != "VN_DGC" else 0.9,
                "market_base_rate": 0.05,
                "relative_risk_lift": 0.8 if iid != "VN_DGC" else 8.0,
                "calibrated_distress_prob_ci_lower": 0.01,
                "calibrated_distress_prob_ci_upper": 0.08,
                "top_risk_drivers_json": "[]",
                "top_protective_drivers_json": "[]",
                "explainability_method": "SHAP_TREE",
                "explainability_available": True,
                "model_role": "PRIMARY",
                "model_version": "v3_test",
                "is_out_of_sample": True,
                "feature_set_version": "v3",
                "score_basis": "CALIBRATED",
            }
        )
    ml_df = pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA)

    manifest_rows = [
        {
            "issuer_id": iid,
            "filing_id": f"{iid}-2025-None",
            "ticker_at_filing": iid.removeprefix("VN_"),
            "source_provider": "fhsc",
            "connector_name": "test",
            "statement_type": "CONSOLIDATED_PACK",
            "statement_scope": "CONSOLIDATED",
            "period_type": "ANNUAL",
            "period_end_date": date(2025, 12, 31),
            "published_at": date(2026, 3, 20),
            "published_at_basis": "EXACT_FILING",
            "effective_available_at": date(2026, 3, 20),
            "audit_status": "AUDITED",
            "audit_opinion": "UNMODIFIED",
            "revision_no": 1,
            "revision_reason": "ORIGINAL",
            "canonical_content_sha256": "a" * 64,
            "payload_file_sha256": "b" * 64,
        }
        for iid in ("VN_FPT", "VN_HPG", "VN_DGC")
    ]
    manifest_df = pl.DataFrame(manifest_rows)

    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    snap_id = "snap_v3_screener_001"
    run_id = "run_v3_screener_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(
        snap_id,
        {
            "issuers.parquet": issuers,
            "financial_facts.parquet": facts,
            "latest_facts.parquet": facts,
            RATIOS_TABLE: ratios_df,
            BENCHMARKS_TABLE: benchmarks_df,
            RED_FLAGS_TABLE: red_flags_df,
            ML_SCORES_TABLE: ml_df,
            PIT_VALUATION_MART_TABLE: mart_df,
            QUALITY_SUMMARY_TABLE: pl.DataFrame(schema=QUALITY_SUMMARY_SCHEMA),
            AUDIT_QUARANTINE_TABLE: pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA),
            FILINGS_MANIFEST_TABLE: manifest_df,
        },
        run_id=run_id,
    )
    state.finish_run(run_id, "SUCCEEDED", snap_id)
    return data_root, snap_id


def test_v3_dictionary_and_lineage_include_ocf_to_total_liabilities(
    tmp_path: Path,
) -> None:
    """/api/v2/meta/dictionary and /api/v1/companies/{ticker}/lineage expose ocf_to_total_liabilities."""
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app

    data_root, _ = _publish_v3_serving_snapshot(tmp_path)
    with TestClient(create_app(data_root=data_root)) as client:
        r_dict = client.get("/api/v2/meta/dictionary")
        assert r_dict.status_code == 200
        ratios_meta = r_dict.json()["ratios"]
        assert len(ratios_meta) == 39
        ocf_entry = next(
            (r for r in ratios_meta if r["ratio_name"] == "ocf_to_total_liabilities"),
            None,
        )
        assert ocf_entry is not None
        assert ocf_entry["direction"] == "HIGHER_IS_BETTER"

        r_lin = client.get(
            "/api/v1/companies/FPT/lineage",
            params={
                "ratio_name": "ocf_to_total_liabilities",
                "statement_scope": "CONSOLIDATED",
                "period_type": "ANNUAL",
                "period_end_date": "2025-12-31",
            },
        )
        assert r_lin.status_code == 200
        lin_json = r_lin.json()
        assert lin_json["ratio_name"] == "ocf_to_total_liabilities"
        assert lin_json["ratio_value"] == pytest.approx(
            90_000_000_000.0 / 600_000_000_000.0
        )


def test_screener_v3_filters_projections_export_and_cache_keys(
    tmp_path: Path,
) -> None:
    """GET /api/v1/screener and /export support all 14 V3 filter/column groups and unique Valkey cache keys."""
    from fastapi.testclient import TestClient

    from finintel.serving._api_helpers import build_screener_cache_params
    from finintel.serving.api import create_app
    from finintel.serving.cache import build_snapshot_cache_key

    data_root, snap_id = _publish_v3_serving_snapshot(tmp_path)
    with TestClient(create_app(data_root=data_root)) as client:
        base = client.get("/api/v1/screener", params={"period_type": "ANNUAL"})
        assert base.status_code == 200
        items = {row["ticker"]: row for row in base.json()["items"]}
        assert set(items) == {"FPT", "HPG", "DGC"}
        assert items["FPT"]["pe_ttm"] == pytest.approx(15.0)
        assert items["FPT"]["pb_ratio"] == pytest.approx(1.8)
        assert items["FPT"]["market_cap_vnd"] == pytest.approx(900_000_000_000.0)
        assert items["FPT"]["ocf_to_total_liabilities"] == pytest.approx(0.15)
        assert items["FPT"]["eps_ttm_estimated"] == pytest.approx(6_000.0)
        assert items["FPT"]["cfo_ttm"] == pytest.approx(90_000_000_000.0)
        assert items["FPT"]["roe_parent"] == pytest.approx(0.12)
        assert items["FPT"]["liabilities_to_assets"] == pytest.approx(600.0 / 1100.0)
        assert items["FPT"]["revenue_growth_yoy"] == pytest.approx(0.25)
        assert items["DGC"]["pe_ttm"] is None
        assert items["DGC"]["pe_flag"] == "NEGATIVE_DENOMINATOR"

        filter_expectations: list[
            tuple[dict[str, float | int | bool | str], set[str]]
        ] = [
            ({"min_roe_parent": 0.25}, {"HPG"}),
            ({"min_roa": 0.15}, {"HPG"}),
            ({"min_eps_ttm_estimated": 10_000.0}, {"HPG"}),
            ({"max_liabilities_to_assets": 0.40}, {"HPG"}),
            ({"max_debt_to_equity": 0.20}, {"HPG"}),
            ({"min_cfo_ttm": 200_000_000_000.0}, {"HPG"}),
            ({"min_ocf_to_total_liabilities": 0.50}, {"HPG"}),
            ({"min_revenue_growth_yoy": 0.40}, {"HPG"}),
            ({"min_pe_ttm": 12.0, "max_pe_ttm": 20.0}, {"FPT"}),
            ({"min_pb_ratio": 2.5}, {"HPG"}),
            ({"min_market_cap_bn_vnd": 2_000.0}, {"HPG"}),
            ({"exchange": "HNX"}, {"DGC"}),
            ({"cluster_id": 0}, {"FPT"}),
            ({"anomaly_flag": True}, {"HPG", "DGC"}),
            ({"anomaly_flag": False}, {"FPT"}),
        ]
        for f_params, expected_tickers in filter_expectations:
            resp = client.get(
                "/api/v1/screener",
                params={"period_type": "ANNUAL", **f_params},
            )
            assert resp.status_code == 200, f"Failed for {f_params}: {resp.text}"
            got = {row["ticker"] for row in resp.json()["items"]}
            assert got == expected_tickers, f"Mismatch for {f_params}: {got}"

        sorted_resp = client.get(
            "/api/v1/screener",
            params={
                "period_type": "ANNUAL",
                "sort_by": "market_cap_vnd",
                "sort_dir": "desc",
            },
        )
        assert sorted_resp.status_code == 200
        assert [row["ticker"] for row in sorted_resp.json()["items"]] == [
            "HPG",
            "FPT",
            "DGC",
        ]

        csv_resp = client.get(
            "/api/v1/screener/export",
            params={"period_type": "ANNUAL", "min_ocf_to_total_liabilities": 0.10},
        )
        assert csv_resp.status_code == 200
        csv_text = csv_resp.content.decode("utf-8-sig")
        header_line = csv_text.splitlines()[0]
        for col in (
            "pe_ttm",
            "pb_ratio",
            "market_cap_vnd",
            "ocf_to_total_liabilities",
            "eps_ttm_estimated",
            "cfo_ttm",
        ):
            assert col in header_line
        assert "HPG" in csv_text and "FPT" in csv_text and "DGC" not in csv_text

    # Valkey L2 cache key differentiation across all extended filter keys
    base_params = build_screener_cache_params(
        period_type="ANNUAL",
        period_end_date=None,
        statement_scope="CONSOLIDATED",
        icb_code_level2=None,
        exchange=None,
        min_f_score=None,
        altman_zone=None,
        effective_tiers=None,
        beneish_flag=None,
        max_distress_prob=None,
        min_distress_prob=None,
        max_red_flags=None,
        min_red_flags=None,
        q=None,
        sort_by="calibrated_distress_prob",
        sort_dir="desc",
        limit=50,
        offset=0,
    )
    base_key = build_snapshot_cache_key(snap_id, "v1/screener", base_params)
    seen_keys = {base_key}
    for extra in (
        {"cluster_id": 1},
        {"anomaly_flag": True},
        {"min_roe_parent": 0.1},
        {"min_pe_ttm": 5.0},
        {"max_pb_ratio": 3.0},
        {"min_market_cap_bn_vnd": 500.0},
        {"min_ocf_to_total_liabilities": 0.2},
        {"min_cfo_ttm": 100.0},
    ):
        alt_params = build_screener_cache_params(
            period_type="ANNUAL",
            period_end_date=None,
            statement_scope="CONSOLIDATED",
            icb_code_level2=None,
            exchange=None,
            min_f_score=None,
            altman_zone=None,
            effective_tiers=None,
            beneish_flag=None,
            max_distress_prob=None,
            min_distress_prob=None,
            max_red_flags=None,
            min_red_flags=None,
            q=None,
            sort_by="calibrated_distress_prob",
            sort_dir="desc",
            limit=50,
            offset=0,
            extra_params=extra,
        )
        alt_key = build_snapshot_cache_key(snap_id, "v1/screener", alt_params)
        assert alt_key not in seen_keys
        seen_keys.add(alt_key)


def test_pit_valuation_mart_empty_prices_returns_empty_schema() -> None:
    empty_mart = build_pit_valuation_mart(
        pl.DataFrame(),
        None,
        as_of=date(2026, 6, 1),
    )
    assert empty_mart.is_empty()
    assert empty_mart.schema == PIT_VALUATION_MART_SCHEMA
