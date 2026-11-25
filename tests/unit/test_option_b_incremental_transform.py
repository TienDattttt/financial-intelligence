"""Unit and opt-in empirical tests for Stream B2 Native Polars Incremental Transform & Audits.

Verified Sources:
    Budiu, M., Chajed, T., McSherry, F., Ryzhyk, L., & Tannen, V. (2023).
    "DBSP: Automatic Incremental View Maintenance for Rich Query Languages."
    PVLDB Vol. 16, No. 7 (VLDB 2023 Best Research Paper Award).
    docs/v2_specs/07_STREAM_B2_SQLMESH_AND_DAGSTER_PLATFORM.md (§3.1 & §4.1).
    src/finintel/quality/gate.py, src/finintel/analytics/tables.py,
    src/finintel/analytics/dupont.py, src/finintel/ml/tables.py.
"""

from __future__ import annotations

import math
import os
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError, CriticalDataQualityError
from finintel.storage.schemas import (
    ANALYTICS_SCHEMAS,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARK_KEY,
    FINANCIAL_FACTS_SCHEMA,
    FINANCIAL_RATIOS_SCHEMA,
    FLAG_KEY,
    ISSUERS_SCHEMA,
    ML_SCORES_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIO_KEY,
    canonicalize_financial_facts,
)
from finintel.transform import (
    DUPONT_TOLERANCE,
    TRANSFORM_STATE_FILENAME,
    MLTransformConfig,
    load_transform_state,
    run_blocking_publish_audits,
    run_incremental_analytics_transform,
    write_transform_state,
)

EMPIRICAL_SNAPSHOT_ENV = "FININTEL_EMPIRICAL_SNAPSHOT_DIR"


def _assert_frames_close(
    actual: pl.DataFrame,
    expected: pl.DataFrame,
    *,
    sort_keys: tuple[str, ...],
    abs_tol: float = 1e-12,
) -> None:
    """Assert two Polars tables match identically on keys/strings and within abs_tol on floats."""
    assert actual.columns == expected.columns
    assert actual.schema == expected.schema
    assert actual.height == expected.height

    left = actual.sort(list(sort_keys), nulls_last=True)
    right = expected.sort(list(sort_keys), nulls_last=True)

    for col in left.columns:
        dtype = left.schema[col]
        if dtype.is_float():
            lhs = left[col]
            rhs = right[col]
            assert (lhs.is_null() == rhs.is_null()).all(), f"Null mismatch in {col}"
            both_valid = lhs.is_not_null() & rhs.is_not_null()
            if both_valid.any():
                max_diff = (lhs.filter(both_valid) - rhs.filter(both_valid)).abs().max()
                assert isinstance(max_diff, (int, float))
                assert math.isfinite(float(max_diff))
                assert float(max_diff) <= abs_tol, (
                    f"Column {col} exceeded tolerance {abs_tol}: max_diff={max_diff}"
                )
        else:
            assert left[col].equals(right[col], null_equal=True), (
                f"Mismatch in non-float column {col}"
            )


def _make_corporate_issuers(
    count: int = 10,
    *,
    extra_ids: tuple[str, ...] = (),
) -> pl.DataFrame:
    issuer_ids = [f"CORP_{i:02d}" for i in range(1, count + 1)] + list(extra_ids)
    rows: list[dict[str, object]] = []
    for idx, iid in enumerate(issuer_ids):
        icb2 = "2000" if idx % 2 == 0 else "3000"
        icb3 = "2010" if idx % 2 == 0 else "3010"
        rows.append(
            {
                "issuer_id": iid,
                "ticker": iid.replace("CORP_", "C"),
                "exchange": "HOSE",
                "company_name_vn": f"Cong ty {iid}",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": icb2,
                "icb_name_level2": f"Nganh {icb2}",
                "icb_code_level3": icb3,
                "icb_name_level3": f"Phan nganh {icb3}",
                "industry_snapshot_date": date(2020, 1, 1),
                "delisted_date": None,
            }
        )
    return pl.DataFrame(rows, schema=ISSUERS_SCHEMA)


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    starts = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
    ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    sm, sd = starts[quarter]
    em, ed = ends[quarter]
    return date(year, sm, sd), date(year, em, ed)


def _build_period_rows(
    issuer_id: str,
    *,
    year: int,
    quarter: int | None,
    scale: float,
    statement_scope: str = "CONSOLIDATED",
    negative_equity: bool = False,
    negative_income: bool = False,
    bs_mismatch: bool = False,
    derived_q4: bool = False,
    revision_no: int = 1,
    revenue_boost: float = 1.0,
    filing_suffix: str = "",
) -> list[dict[str, object]]:
    period_type = "ANNUAL" if quarter is None else "QUARTER"
    fiscal_start = date(year, 1, 1)
    if quarter is None:
        period_start = date(year, 1, 1)
        period_end = date(year, 12, 31)
        avail = date(year + 1, 3, 25)
    else:
        period_start, period_end = _quarter_dates(year, quarter)
        avail = period_end + timedelta(days=30)

    is_annual = quarter is None
    flow_factor = 4.0 if is_annual else 1.0

    assets = 1_000_000.0 * scale
    if negative_equity:
        equity = -50_000.0 * scale
        liabilities = assets - equity
    elif bs_mismatch:
        equity = 400_000.0 * scale
        liabilities = 450_000.0 * scale  # 150k mismatch (15%)
    else:
        equity = 600_000.0 * scale
        liabilities = 400_000.0 * scale

    minority_interest = 30_000.0 * scale if not negative_equity else 0.0
    current_assets = 520_000.0 * scale
    noncurrent_assets = assets - current_assets
    current_liabilities = 250_000.0 * scale
    cash = 110_000.0 * scale
    st_debt = 120_000.0 * scale
    lt_debt = 80_000.0 * scale
    inventory = 90_000.0 * scale
    receivables_st = 115_000.0 * scale
    payables_st = 75_000.0 * scale
    tangible_assets = 320_000.0 * scale
    retained_earnings = 180_000.0 * scale

    revenue = 250_000.0 * scale * flow_factor * revenue_boost
    cogs = 150_000.0 * scale * flow_factor
    gross_profit = revenue - cogs
    selling = 18_000.0 * scale * flow_factor
    admin = 22_000.0 * scale * flow_factor
    operating_profit = gross_profit - selling - admin
    interest = 6_000.0 * scale * flow_factor
    if negative_income:
        ebt = -25_000.0 * scale * flow_factor
        current_tax = 0.0
        deferred_tax = 0.0
        net_income = -25_000.0 * scale * flow_factor
        net_income_parent = -24_000.0 * scale * flow_factor
    else:
        ebt = operating_profit - interest
        current_tax = 0.20 * ebt
        deferred_tax = 0.0
        net_income = ebt - current_tax
        net_income_parent = 0.94 * net_income

    cfo = 42_000.0 * scale * flow_factor
    capex = -12_000.0 * scale * flow_factor
    depreciation = 10_000.0 * scale * flow_factor
    cf_ending_cash = cash

    prefix = f"{issuer_id}-{statement_scope}-{year}-{'FY' if is_annual else f'Q{quarter}'}-r{revision_no}{filing_suffix}"
    metrics: list[tuple[str, float, str]] = [
        ("BS_TOTAL_ASSETS", assets, f"{prefix}-bs"),
        ("BS_TOTAL_LIABILITIES", liabilities, f"{prefix}-bs"),
        ("BS_TOTAL_EQUITY", equity, f"{prefix}-bs"),
        ("BS_MINORITY_INTEREST", minority_interest, f"{prefix}-bs"),
        ("BS_CURRENT_ASSETS", current_assets, f"{prefix}-bs"),
        ("BS_NONCURRENT_ASSETS", noncurrent_assets, f"{prefix}-bs"),
        ("BS_CURRENT_LIABILITIES", current_liabilities, f"{prefix}-bs"),
        ("BS_CASH_AND_EQUIVALENTS", cash, f"{prefix}-bs"),
        ("BS_SHORT_TERM_BORROWINGS", st_debt, f"{prefix}-bs"),
        ("BS_LONG_TERM_BORROWINGS", lt_debt, f"{prefix}-bs"),
        ("BS_INVENTORIES", inventory, f"{prefix}-bs"),
        ("BS_TRADE_RECEIVABLES_ST", receivables_st, f"{prefix}-bs"),
        ("BS_TRADE_PAYABLES_ST", payables_st, f"{prefix}-bs"),
        ("BS_TANGIBLE_FIXED_ASSETS", tangible_assets, f"{prefix}-bs"),
        ("BS_RETAINED_EARNINGS", retained_earnings, f"{prefix}-bs"),
        ("BS_ORDINARY_SHARES_CAPITAL", 200_000.0 * scale, f"{prefix}-bs"),
        ("BS_TREASURY_SHARES", 0.0, f"{prefix}-bs"),
        ("IS_NET_REVENUE", revenue, f"{prefix}-is"),
        ("IS_COST_OF_GOODS_SOLD", cogs, f"{prefix}-is"),
        ("IS_GROSS_PROFIT", gross_profit, f"{prefix}-is"),
        ("IS_SELLING_EXPENSES", selling, f"{prefix}-is"),
        ("IS_ADMINISTRATIVE_EXPENSES", admin, f"{prefix}-is"),
        ("IS_OPERATING_PROFIT", operating_profit, f"{prefix}-is"),
        ("IS_PROFIT_BEFORE_TAX", ebt, f"{prefix}-is"),
        ("IS_INTEREST_EXPENSE", interest, f"{prefix}-is"),
        ("IS_CURRENT_INCOME_TAX", current_tax, f"{prefix}-is"),
        ("IS_DEFERRED_INCOME_TAX", deferred_tax, f"{prefix}-is"),
        ("IS_NET_INCOME", net_income, f"{prefix}-is"),
        ("IS_NET_INCOME_PARENT", net_income_parent, f"{prefix}-is"),
        ("CF_OPERATING_CASH_FLOW", cfo, f"{prefix}-cf"),
        ("CF_CAPITAL_EXPENDITURE", capex, f"{prefix}-cf"),
        ("CF_DEPRECIATION_AMORTIZATION", depreciation, f"{prefix}-cf"),
        ("CF_ENDING_CASH", cf_ending_cash, f"{prefix}-cf"),
        ("CF_EQUITY_ISSUANCE_PROCEEDS", 0.0, f"{prefix}-cf"),
    ]

    derivation = (
        "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX"
        if derived_q4 and quarter == 4
        else "REPORTED_DIRECTLY"
    )
    rows: list[dict[str, object]] = []
    for metric_id, val, fid in metrics:
        is_bs = metric_id.startswith("BS_")
        rows.append(
            {
                "issuer_id": issuer_id,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "fiscal_year_start_date": fiscal_start,
                "period_end_date": period_end,
                "metric_id": metric_id,
                "taxonomy_version": "VAS_TT200_2014",
                "revision_no": revision_no,
                "filing_id": fid,
                "fiscal_year": year,
                "fiscal_quarter": quarter,
                "period_start_date": period_end if is_bs else period_start,
                "published_at": avail,
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": avail,
                "revision_reason": "ORIGINAL" if revision_no == 1 else "AUDITED_UPDATE",
                "derivation_method": "REPORTED_DIRECTLY" if is_bs else derivation,
                "audit_status": "AUDITED" if is_annual else "UNAUDITED",
                "audit_opinion": "UNMODIFIED" if is_annual else "NOT_APPLICABLE",
                "value": val,
                "ttm_value": None,
                "computation_note": None,
            }
        )
    return rows


def _make_universe_facts(
    issuer_ids: list[str],
    *,
    negative_equity_issuers: set[str] | None = None,
    negative_income_issuers: set[str] | None = None,
    bs_mismatch_issuers: set[str] | None = None,
    derived_q4_issuers: set[str] | None = None,
) -> pl.DataFrame:
    neg_eq = negative_equity_issuers or set()
    neg_inc = negative_income_issuers or set()
    mismatch = bs_mismatch_issuers or set()
    derived_q4 = derived_q4_issuers or set()
    rows: list[dict[str, object]] = []
    for idx, iid in enumerate(issuer_ids, start=1):
        base_scale = 1.0 + 0.08 * idx
        for year in (2023, 2024):
            for quarter in (1, 2, 3, 4):
                is_latest_q = year == 2024 and quarter == 4
                rows.extend(
                    _build_period_rows(
                        iid,
                        year=year,
                        quarter=quarter,
                        scale=base_scale
                        * (1.0 + 0.02 * (year - 2023) + 0.005 * quarter),
                        negative_equity=is_latest_q and iid in neg_eq,
                        negative_income=is_latest_q and iid in neg_inc,
                        bs_mismatch=is_latest_q and iid in mismatch,
                        derived_q4=year == 2024 and iid in derived_q4,
                    )
                )
            rows.extend(
                _build_period_rows(
                    iid,
                    year=year,
                    quarter=None,
                    scale=base_scale * (1.0 + 0.02 * (year - 2023)),
                    negative_equity=year == 2024 and iid in neg_eq,
                    negative_income=year == 2024 and iid in neg_inc,
                    bs_mismatch=year == 2024 and iid in mismatch,
                )
            )
    return canonicalize_financial_facts(pl.DataFrame(rows))


def test_incremental_equivalence_add_modify_delete_red_flag_and_annual_no_duplicates(
    tmp_path: Path,
) -> None:
    """Spec 07 §4.1: Incremental add/modify/delete/clear-red-flag matches full rebuild <= 1e-12."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / "metadata" / TRANSFORM_STATE_FILENAME

    issuers_v1 = _make_corporate_issuers(10)
    issuer_ids_v1 = issuers_v1["issuer_id"].to_list()
    # CORP_03 starts with RF_06_NEGATIVE_EQUITY in 2024 Q4 and FY2024.
    facts_v1 = _make_universe_facts(
        issuer_ids_v1,
        negative_equity_issuers={"CORP_03"},
    )

    bundle_v1 = run_incremental_analytics_transform(
        facts_v1,
        issuers_v1,
        as_of=as_of,
        run_id="run-v1",
        state_path=state_path,
    )
    assert bundle_v1.diff.full_rebuild is True
    assert bundle_v1.diff.benchmarks_full_rebuild is True
    assert bundle_v1.ml_scores.is_empty()
    assert bundle_v1.ml_scores.schema == ML_SCORES_SCHEMA
    assert bundle_v1.evaluation_report is None
    assert bundle_v1.ml_run_manifest is None
    assert (
        bundle_v1.accounting_red_flags.filter(
            (pl.col("issuer_id") == "CORP_03")
            & (pl.col("flag_code") == "RF_06_NEGATIVE_EQUITY")
        ).height
        >= 1
    )

    updated_qs_v1 = run_blocking_publish_audits(bundle_v1, run_id="run-v1")
    assert updated_qs_v1.height > 0
    write_transform_state(
        state_path,
        bundle_v1.next_transform_state,
        snapshot_id="snap_v1",
    )
    loaded_state = load_transform_state(state_path)
    assert loaded_state is not None
    assert loaded_state["snapshot_id"] == "snap_v1"

    # Build v2:
    # (a) Add CORP_11 (present in issuers from start so issuers_metadata stays unchanged)
    # Wait: keep issuers_v2 identical to issuers_v1 for pure fact-level incremental test!
    issuers_all = _make_corporate_issuers(10, extra_ids=("CORP_11",))
    bundle_base = run_incremental_analytics_transform(
        facts_v1,
        issuers_all,
        as_of=as_of,
        run_id="run-base",
        state_path=state_path,
    )
    run_blocking_publish_audits(bundle_base, run_id="run-base")
    write_transform_state(
        state_path,
        bundle_base.next_transform_state,
        snapshot_id="snap_base",
    )

    # Mutate facts:
    # 1. Delete CORP_10 entirely
    # 2. Clear CORP_03 negative equity (so RF_06_NEGATIVE_EQUITY disappears -> 0 ghost flags)
    # 3. Modify CORP_02 FY2024 (ANNUAL with fiscal_quarter=None) & 2024 Q4 via revision 2
    # 4. Add CORP_11
    kept_v2 = facts_v1.filter(~pl.col("issuer_id").is_in(["CORP_10", "CORP_03"]))
    corp03_fixed = _make_universe_facts(["CORP_03"])
    corp02_revision = canonicalize_financial_facts(
        pl.DataFrame(
            [
                *_build_period_rows(
                    "CORP_02",
                    year=2024,
                    quarter=4,
                    scale=1.16 * 1.04,
                    revision_no=2,
                    revenue_boost=1.12,
                ),
                *_build_period_rows(
                    "CORP_02",
                    year=2024,
                    quarter=None,
                    scale=1.16 * 1.02,
                    revision_no=2,
                    revenue_boost=1.12,
                ),
            ]
        )
    )
    corp11_new = _make_universe_facts(["CORP_11"])
    facts_v2 = pl.concat([kept_v2, corp03_fixed, corp02_revision, corp11_new])

    bundle_inc = run_incremental_analytics_transform(
        facts_v2,
        issuers_all,
        as_of=as_of,
        run_id="run-v2-inc",
        previous_ratios=bundle_base.financial_ratios,
        previous_benchmarks=bundle_base.industry_benchmarks,
        previous_red_flags=bundle_base.accounting_red_flags,
        previous_snapshot_id="snap_base",
        state_path=state_path,
    )
    assert bundle_inc.diff.full_rebuild is False
    assert bundle_inc.diff.benchmarks_full_rebuild is False
    assert bundle_inc.diff.added_issuers == ("CORP_11",)
    assert bundle_inc.diff.modified_issuers == ("CORP_02", "CORP_03")
    assert bundle_inc.diff.deleted_issuers == ("CORP_10",)
    assert bundle_inc.diff.dirty_issuers == ("CORP_02", "CORP_03", "CORP_10", "CORP_11")
    assert len(bundle_inc.diff.dirty_slices) > 0

    # Zero ghost red flags for CORP_03 and zero residual rows for deleted CORP_10
    assert (
        bundle_inc.accounting_red_flags.filter(
            (pl.col("issuer_id") == "CORP_03")
            & (pl.col("flag_code") == "RF_06_NEGATIVE_EQUITY")
        ).height
        == 0
    )
    assert (
        bundle_inc.financial_ratios.filter(pl.col("issuer_id") == "CORP_10").height == 0
    )
    assert (
        bundle_inc.accounting_red_flags.filter(pl.col("issuer_id") == "CORP_10").height
        == 0
    )
    assert (
        bundle_inc.industry_benchmarks.filter(pl.col("issuer_id") == "CORP_10").height
        == 0
    )

    # Zero duplicate ANNUAL rows (where fiscal_quarter IS NULL)
    annual_rows = bundle_inc.financial_ratios.filter(pl.col("period_type") == "ANNUAL")
    assert annual_rows.height > 0
    assert annual_rows["fiscal_quarter"].null_count() == annual_rows.height
    assert not annual_rows.select(
        pl.struct(
            [
                "issuer_id",
                "statement_scope",
                "period_type",
                "fiscal_year",
                "period_end_date",
            ]
        )
        .is_duplicated()
        .any()
    ).item()

    # Compare against fresh full rebuild from scratch
    bundle_full = run_incremental_analytics_transform(
        facts_v2,
        issuers_all,
        as_of=as_of,
        run_id="run-v2-full",
    )
    assert bundle_full.diff.full_rebuild is True

    _assert_frames_close(
        bundle_inc.financial_ratios,
        bundle_full.financial_ratios,
        sort_keys=RATIO_KEY,
        abs_tol=1e-12,
    )
    _assert_frames_close(
        bundle_inc.industry_benchmarks,
        bundle_full.industry_benchmarks,
        sort_keys=BENCHMARK_KEY,
        abs_tol=1e-12,
    )
    _assert_frames_close(
        bundle_inc.accounting_red_flags,
        bundle_full.accounting_red_flags,
        sort_keys=FLAG_KEY,
        abs_tol=1e-12,
    )

    run_blocking_publish_audits(bundle_inc, run_id="run-v2-inc")


def test_noop_incremental_and_snapshot_or_config_mismatch_triggers_full_rebuild(
    tmp_path: Path,
) -> None:
    """Verify no-op reuse, snapshot mismatch invalidation, and as_of config invalidation."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / TRANSFORM_STATE_FILENAME
    issuers = _make_corporate_issuers(6)
    facts = _make_universe_facts(issuers["issuer_id"].to_list())

    b1 = run_incremental_analytics_transform(
        facts, issuers, as_of=as_of, run_id="r1", state_path=state_path
    )
    write_transform_state(state_path, b1.next_transform_state, snapshot_id="snap_001")

    # No-op run
    b_noop = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="r2",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_001",
        state_path=state_path,
    )
    assert b_noop.diff.full_rebuild is False
    assert b_noop.diff.benchmarks_full_rebuild is False
    assert b_noop.diff.dirty_issuers == ()
    assert b_noop.diff.dirty_slices == ()
    _assert_frames_close(
        b_noop.financial_ratios, b1.financial_ratios, sort_keys=RATIO_KEY
    )

    # Snapshot mismatch forces full rebuild
    b_snap_mismatch = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="r3",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_999",
        state_path=state_path,
    )
    assert b_snap_mismatch.diff.full_rebuild is True

    # Advancing as_of with no newly visible facts does NOT force full rebuild (ARCH-06).
    b_next_day = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=date(2025, 7, 1),
        run_id="r4",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_001",
        state_path=state_path,
    )
    assert b_next_day.diff.transform_config_changed is False
    assert b_next_day.diff.full_rebuild is False
    assert b_next_day.diff.dirty_issuers == ()

    # Algorithm/quality config change forces full rebuild.
    from finintel.quality.rules import QualityGateConfig

    b_cfg_change = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=date(2025, 7, 1),
        run_id="r5",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_001",
        state_path=state_path,
        quality_config=QualityGateConfig(max_critical_issuer_ratio=0.05),
    )
    assert b_cfg_change.diff.transform_config_changed is True
    assert b_cfg_change.diff.full_rebuild is True


def test_icb_change_triggers_auto_full_rebuild_of_industry_benchmarks(
    tmp_path: Path,
) -> None:
    """Spec 07 §4.1: Mutating icb_code_level2 auto-rebuilds all industry_benchmarks to parity."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / TRANSFORM_STATE_FILENAME
    issuers_v1 = _make_corporate_issuers(10)
    facts = _make_universe_facts(issuers_v1["issuer_id"].to_list())

    b1 = run_incremental_analytics_transform(
        facts, issuers_v1, as_of=as_of, run_id="icb-1", state_path=state_path
    )
    write_transform_state(state_path, b1.next_transform_state, snapshot_id="snap_icb1")

    # Change CORP_01's icb_code_level2 from "2000" -> "3000" (and icb_code_level3 -> "3010")
    issuers_v2 = issuers_v1.with_columns(
        pl.when(pl.col("issuer_id") == "CORP_01")
        .then(pl.lit("3000"))
        .otherwise(pl.col("icb_code_level2"))
        .alias("icb_code_level2"),
        pl.when(pl.col("issuer_id") == "CORP_01")
        .then(pl.lit("3010"))
        .otherwise(pl.col("icb_code_level3"))
        .alias("icb_code_level3"),
    )

    b_inc = run_incremental_analytics_transform(
        facts,
        issuers_v2,
        as_of=as_of,
        run_id="icb-2",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_icb1",
        state_path=state_path,
    )
    assert b_inc.diff.issuers_metadata_changed is True
    assert b_inc.diff.benchmarks_full_rebuild is True
    assert b_inc.diff.full_rebuild is False

    b_full = run_incremental_analytics_transform(
        facts, issuers_v2, as_of=as_of, run_id="icb-full"
    )
    _assert_frames_close(
        b_inc.industry_benchmarks,
        b_full.industry_benchmarks,
        sort_keys=BENCHMARK_KEY,
        abs_tol=1e-12,
    )
    # Confirm benchmarks actually changed relative to b1 (CORP_01 moved to peer group 3000)
    assert not b_inc.industry_benchmarks.equals(b1.industry_benchmarks)

    # Also verify accounting_regime change (CORPORATE -> BANK) recomputes affected issuer cleanly
    issuers_regime = issuers_v1.with_columns(
        pl.when(pl.col("issuer_id") == "CORP_02")
        .then(pl.lit("BANK"))
        .otherwise(pl.col("accounting_regime"))
        .alias("accounting_regime")
    )
    b_reg_inc = run_incremental_analytics_transform(
        facts,
        issuers_regime,
        as_of=as_of,
        run_id="regime-inc",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_icb1",
        state_path=state_path,
    )
    assert "CORP_02" in b_reg_inc.diff.modified_issuers
    assert b_reg_inc.diff.benchmarks_full_rebuild is True
    b_reg_full = run_incremental_analytics_transform(
        facts, issuers_regime, as_of=as_of, run_id="regime-full"
    )
    _assert_frames_close(
        b_reg_inc.financial_ratios, b_reg_full.financial_ratios, sort_keys=RATIO_KEY
    )
    _assert_frames_close(
        b_reg_inc.industry_benchmarks,
        b_reg_full.industry_benchmarks,
        sort_keys=BENCHMARK_KEY,
    )
    _assert_frames_close(
        b_reg_inc.accounting_red_flags,
        b_reg_full.accounting_red_flags,
        sort_keys=FLAG_KEY,
    )

    # Pure ticker rename (unchanged ICB/regime/facts -> issuers_metadata_changed is False)
    # must still update ticker in financial_ratios (part of RATIO_KEY) to match full rebuild.
    issuers_renamed = issuers_v1.with_columns(
        pl.when(pl.col("issuer_id") == "CORP_03")
        .then(pl.lit("NEW03"))
        .otherwise(pl.col("ticker"))
        .alias("ticker")
    )
    b_ren_inc = run_incremental_analytics_transform(
        facts,
        issuers_renamed,
        as_of=as_of,
        run_id="rename-inc",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_icb1",
        state_path=state_path,
    )
    assert b_ren_inc.diff.issuers_metadata_changed is False
    assert b_ren_inc.diff.full_rebuild is False
    assert b_ren_inc.diff.modified_issuers == ("CORP_03",)
    assert b_ren_inc.financial_ratios.filter(pl.col("issuer_id") == "CORP_03")[
        "ticker"
    ].unique().to_list() == ["NEW03"]
    b_ren_full = run_incremental_analytics_transform(
        facts, issuers_renamed, as_of=as_of, run_id="rename-full"
    )
    _assert_frames_close(
        b_ren_inc.financial_ratios, b_ren_full.financial_ratios, sort_keys=RATIO_KEY
    )
    _assert_frames_close(
        b_ren_inc.industry_benchmarks,
        b_ren_full.industry_benchmarks,
        sort_keys=BENCHMARK_KEY,
    )
    _assert_frames_close(
        b_ren_inc.accounting_red_flags,
        b_ren_full.accounting_red_flags,
        sort_keys=FLAG_KEY,
    )


def test_incremental_partial_quarantine_empty_facts_and_corrupt_state_recovery(
    tmp_path: Path,
) -> None:
    """Verify <=10% QG1 quarantine during incremental update, empty facts, and corrupt state fallback."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / TRANSFORM_STATE_FILENAME
    issuers = _make_corporate_issuers(10)
    issuer_ids = issuers["issuer_id"].to_list()
    facts_v1 = _make_universe_facts(issuer_ids)

    b1 = run_incremental_analytics_transform(
        facts_v1, issuers, as_of=as_of, run_id="pq-1", state_path=state_path
    )
    write_transform_state(state_path, b1.next_transform_state, snapshot_id="snap_pq1")

    # Inject 1/10 (10% <= 10%) issuer with BS_IDENTITY_MISMATCH in 2024 Q4 + FY2024
    facts_v2 = _make_universe_facts(issuer_ids, bs_mismatch_issuers={"CORP_05"})
    b2 = run_incremental_analytics_transform(
        facts_v2,
        issuers,
        as_of=as_of,
        run_id="pq-2",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_pq1",
        state_path=state_path,
    )
    assert b2.critical_issuers == 1
    assert b2.critical_issuer_ratio == pytest.approx(0.10)
    assert b2.diff.full_rebuild is False
    assert b2.diff.modified_issuers == ("CORP_05",)
    assert b2.audit_quarantine.height >= 1
    run_blocking_publish_audits(b2, run_id="pq-2")

    b2_full = run_incremental_analytics_transform(
        facts_v2, issuers, as_of=as_of, run_id="pq-2-full"
    )
    _assert_frames_close(
        b2.financial_ratios, b2_full.financial_ratios, sort_keys=RATIO_KEY
    )
    _assert_frames_close(
        b2.industry_benchmarks, b2_full.industry_benchmarks, sort_keys=BENCHMARK_KEY
    )

    # Corrupt _transform_state.json safely triggers full rebuild
    state_path.write_text("{not valid json", encoding="utf-8")
    assert load_transform_state(state_path) is None
    b_recover = run_incremental_analytics_transform(
        facts_v1,
        issuers,
        as_of=as_of,
        run_id="recover",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        state_path=state_path,
    )
    assert b_recover.diff.full_rebuild is True

    # Empty facts returns valid empty tables and passes blocking audits
    empty_bundle = run_incremental_analytics_transform(
        pl.DataFrame(schema=FINANCIAL_FACTS_SCHEMA),
        issuers,
        as_of=as_of,
        run_id="empty-run",
    )
    assert empty_bundle.financial_ratios.is_empty()
    assert empty_bundle.industry_benchmarks.is_empty()
    assert empty_bundle.accounting_red_flags.is_empty()
    assert empty_bundle.ml_scores.is_empty()
    run_blocking_publish_audits(empty_bundle, run_id="empty-run")


def test_blocking_qg1_circuit_breaker_over_10_pct_bs_identity_mismatch(
    tmp_path: Path,
) -> None:
    """Spec 07 §4.1: >10% critical BS_IDENTITY_MISMATCH trips QG1 before publish or state write."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / TRANSFORM_STATE_FILENAME
    issuers = _make_corporate_issuers(10)
    # 2/10 = 20% > 10%
    facts = _make_universe_facts(
        issuers["issuer_id"].to_list(),
        bs_mismatch_issuers={"CORP_01", "CORP_02"},
    )

    with pytest.raises(CriticalDataQualityError, match="2/10 issuers"):
        run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id="qg1-fail",
            state_path=state_path,
            persist_state=True,
        )

    assert not state_path.exists()


def test_blocking_qg2_rejects_orphan_filing_ids_in_ratios_red_flags_and_ml() -> None:
    """Spec 07 §4.1: Any orphan filing_id in ratios, source_filing_ids, red_flags, or ml_scores blocks."""
    as_of = date(2025, 6, 1)
    issuers = _make_corporate_issuers(4)
    facts = _make_universe_facts(
        issuers["issuer_id"].to_list(),
        negative_equity_issuers={"CORP_01"},
    )
    bundle = run_incremental_analytics_transform(
        facts, issuers, as_of=as_of, run_id="qg2-base"
    )
    # Baseline must pass
    run_blocking_publish_audits(bundle, run_id="qg2-base")

    # 1. Orphan in financial_ratios.filing_id
    bad_ratios = bundle.financial_ratios.with_columns(
        pl.when(pl.arange(0, pl.len()) == 0)
        .then(pl.lit("ORPHAN-FILING-RATIO-001"))
        .otherwise(pl.col("filing_id"))
        .alias("filing_id")
    )
    with pytest.raises(CriticalDataQualityError, match="orphan"):
        run_blocking_publish_audits(
            replace(bundle, financial_ratios=bad_ratios), run_id="qg2-orphan-ratio"
        )

    # 2. Orphan inside financial_ratios.source_filing_ids
    bad_source_ids = bundle.financial_ratios.with_columns(
        pl.when(pl.arange(0, pl.len()) == 0)
        .then(pl.lit(["ORPHAN-SOURCE-FILING-002"], dtype=pl.List(pl.String)))
        .otherwise(pl.col("source_filing_ids"))
        .alias("source_filing_ids")
    )
    with pytest.raises(CriticalDataQualityError, match="orphan"):
        run_blocking_publish_audits(
            replace(bundle, financial_ratios=bad_source_ids),
            run_id="qg2-orphan-source-ids",
        )

    # 3. Orphan in accounting_red_flags.filing_id
    assert bundle.accounting_red_flags.height > 0
    bad_rf = bundle.accounting_red_flags.with_columns(
        pl.when(pl.arange(0, pl.len()) == 0)
        .then(pl.lit("ORPHAN-FILING-RF-003"))
        .otherwise(pl.col("filing_id"))
        .alias("filing_id")
    )
    with pytest.raises(CriticalDataQualityError, match="orphan"):
        run_blocking_publish_audits(
            replace(bundle, accounting_red_flags=bad_rf), run_id="qg2-orphan-rf"
        )

    # 4. Orphan in ml_scores_and_explanations.filing_id
    sample_row = bundle.financial_ratios.row(0, named=True)
    bad_ml = pl.DataFrame(
        [
            {
                "issuer_id": sample_row["issuer_id"],
                "statement_scope": sample_row["statement_scope"],
                "period_type": sample_row["period_type"],
                "period_end_date": sample_row["period_end_date"],
                "filing_id": "ORPHAN-FILING-ML-004",
                "model_role": "SCREENING_INDICATOR",
                "altman_z_double_prime": 3.5,
                "altman_em_score": 6.75,
                "altman_zone": "SAFE_ZONE",
                "piotroski_f_score": 7,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -2.5,
                "beneish_flag": "VALID",
                "pca_pc1": None,
                "pca_pc2": None,
                "pca_pc3": None,
                "cluster_id": None,
                "cluster_archetype_name": None,
                "anomaly_score_raw": None,
                "anomaly_percentile_rank": None,
                "statistical_outlier_flag": None,
                "anomaly_flag": None,
                "distress_risk_score": None,
                "calibrated_distress_prob": None,
                "model_version": None,
                "shap_top_drivers_json": None,
            }
        ],
        schema=ML_SCORES_SCHEMA,
    )
    with pytest.raises(CriticalDataQualityError, match="orphan"):
        run_blocking_publish_audits(
            replace(bundle, ml_scores=bad_ml), run_id="qg2-orphan-ml"
        )


def test_blocking_audits_enforce_dupont_5_and_6_factor_at_1e_9_and_pk_uniqueness() -> (
    None
):
    """Spec 07 §4.1: Enforce DuPont 5-factor & 6-factor <= 1e-9 and PK uniqueness."""
    assert DUPONT_TOLERANCE == 1e-9
    as_of = date(2025, 6, 1)
    issuers = _make_corporate_issuers(4)
    facts = _make_universe_facts(issuers["issuer_id"].to_list())
    bundle = run_incremental_analytics_transform(
        facts, issuers, as_of=as_of, run_id="dupont-base"
    )
    valid_rows = bundle.financial_ratios.filter(
        pl.col("dupont_valid_flag").fill_null(False)
    )
    assert valid_rows.height > 0
    assert valid_rows["parent_profit_share"].null_count() == 0
    assert valid_rows["equity_multiplier_parent"].null_count() == 0
    run_blocking_publish_audits(bundle, run_id="dupont-base")

    # Perturbation within tolerance (5e-10 <= 1e-9) passes
    within_tol = bundle.financial_ratios.with_columns(
        pl.when(pl.col("dupont_valid_flag").fill_null(False))
        .then(pl.col("roe_total") + 5e-10)
        .otherwise(pl.col("roe_total"))
        .alias("roe_total")
    )
    run_blocking_publish_audits(
        replace(bundle, financial_ratios=within_tol), run_id="dupont-within-tol"
    )

    # 5-factor violation > 1e-9 (2e-9 << 1e-6) MUST raise CriticalDataQualityError
    bad_5f = bundle.financial_ratios.with_columns(
        pl.when(pl.col("dupont_valid_flag").fill_null(False))
        .then(pl.col("roe_total") + 2e-9)
        .otherwise(pl.col("roe_total"))
        .alias("roe_total")
    )
    with pytest.raises(CriticalDataQualityError, match="DuPont"):
        run_blocking_publish_audits(
            replace(bundle, financial_ratios=bad_5f), run_id="dupont-5f-fail"
        )

    # 6-factor violation > 1e-9 while 5-factor holds MUST raise CriticalDataQualityError
    bad_6f = bundle.financial_ratios.with_columns(
        pl.when(pl.col("dupont_valid_flag").fill_null(False))
        .then(pl.col("roe_parent") + 2e-9)
        .otherwise(pl.col("roe_parent"))
        .alias("roe_parent")
    )
    with pytest.raises(CriticalDataQualityError, match="DuPont"):
        run_blocking_publish_audits(
            replace(bundle, financial_ratios=bad_6f), run_id="dupont-6f-fail"
        )

    # Duplicate PK in financial_ratios (specifically ANNUAL row where fiscal_quarter is NULL)
    annual_dup = pl.concat(
        [
            bundle.financial_ratios,
            bundle.financial_ratios.filter(pl.col("period_type") == "ANNUAL").head(1),
        ]
    )
    with pytest.raises(CriticalDataQualityError):
        run_blocking_publish_audits(
            replace(bundle, financial_ratios=annual_dup), run_id="pk-dup-ratios"
        )

    # Duplicate PK in quality_summary
    qs_dup = pl.concat([bundle.quality_summary, bundle.quality_summary.head(1)])
    with pytest.raises(CriticalDataQualityError):
        run_blocking_publish_audits(
            replace(bundle, quality_summary=qs_dup), run_id="pk-dup-qs"
        )

    # Duplicate PK in audit_quarantine
    bad_aq_row = pl.DataFrame(
        [
            {
                "issuer_id": "CORP_01",
                "filing_id": "f1",
                "statement_scope": "CONSOLIDATED",
                "period_type": "ANNUAL",
                "period_end_date": date(2024, 12, 31),
                "taxonomy_version": "VAS_TT200_2014",
                "rule_code": "BS_IDENTITY_MISMATCH",
                "severity": "CRITICAL",
                "details_json": "{}",
                "quarantined_at": "2025-06-01T00:00:00+00:00",
            }
        ],
        schema=ANALYTICS_SCHEMAS[AUDIT_QUARANTINE_TABLE],
    )
    with pytest.raises(CriticalDataQualityError):
        run_blocking_publish_audits(
            replace(bundle, audit_quarantine=pl.concat([bad_aq_row, bad_aq_row])),
            run_id="pk-dup-aq",
        )


def test_blocking_audits_propagate_derived_q4_and_negative_roe_to_quality_summary() -> (
    None
):
    """Spec 07 §4.1: DERIVED_Q4_INPUT & NEGATIVE_ROE propagate into updated quality_summary."""
    as_of = date(2025, 6, 1)
    issuers = _make_corporate_issuers(4)
    facts = _make_universe_facts(
        issuers["issuer_id"].to_list(),
        derived_q4_issuers={"CORP_01"},
        negative_income_issuers={"CORP_02"},
    )
    bundle = run_incremental_analytics_transform(
        facts, issuers, as_of=as_of, run_id="prop-run"
    )
    updated_qs = run_blocking_publish_audits(bundle, run_id="prop-run")
    assert updated_qs.schema == ANALYTICS_SCHEMAS[QUALITY_SUMMARY_TABLE]

    corp01_q4 = updated_qs.filter(
        (pl.col("issuer_id") == "CORP_01")
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    ).row(0, named=True)
    assert corp01_q4["quality_status"] == "WARNING"
    assert "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX" in corp01_q4["warning_codes"]

    corp02_fy = updated_qs.filter(
        (pl.col("issuer_id") == "CORP_02")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    ).row(0, named=True)
    assert corp02_fy["quality_status"] == "PASS"
    assert "NEGATIVE_ROE" in corp02_fy["info_codes"]


def test_ml_disabled_vs_enabled_guardrails_and_pit_artifacts() -> None:
    """Spec 07 §4.1: Verify ML disabled schema, G1/G2 guardrails, and full PIT ML run."""
    as_of = date(2025, 3, 1)
    issuers = _make_corporate_issuers(12)
    issuer_ids = issuers["issuer_id"].to_list()

    # Build a 2018-2024 exact-filing quarterly panel with controlled distress onset in train/val/test
    distress_schedule = {
        "CORP_01": 2021,
        "CORP_02": 2021,
        "CORP_03": 2021,
        "CORP_04": 2022,
        "CORP_05": 2022,
        "CORP_06": 2024,
        "CORP_07": 2024,
    }
    rows: list[dict[str, object]] = []
    for idx, iid in enumerate(issuer_ids, start=1):
        d_year = distress_schedule.get(iid)
        for year in range(2018, 2025):
            for quarter in (1, 2, 3, 4):
                rows.extend(
                    _build_period_rows(
                        iid,
                        year=year,
                        quarter=quarter,
                        scale=1.0 + 0.03 * idx + 0.01 * quarter,
                        negative_equity=(d_year == year and quarter == 3),
                    )
                )
    facts = canonicalize_financial_facts(pl.DataFrame(rows))

    # 1. ML disabled -> empty ML_SCORES_SCHEMA + None reports
    bundle_off = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="ml-off",
        ml_config=MLTransformConfig(enabled=False),
    )
    assert bundle_off.ml_scores.is_empty()
    assert bundle_off.ml_scores.schema == ML_SCORES_SCHEMA
    assert bundle_off.evaluation_report is None
    assert bundle_off.ml_run_manifest is None

    # 2. Guardrail G2: approved_target=False rejected
    with pytest.raises(ContractViolationError, match="approved_target"):
        run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id="ml-g2-fail",
            ml_config=MLTransformConfig(
                enabled=True,
                approved_target=False,
                require_exact_filing=True,
                validation_start=date(2021, 12, 1),
                test_start=date(2023, 3, 1),
            ),
        )

    # 3. Guardrail G1: require_exact_filing=False rejected
    with pytest.raises(ContractViolationError, match="require_exact_filing"):
        run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id="ml-g1-fail",
            ml_config=MLTransformConfig(
                enabled=True,
                approved_target=True,
                require_exact_filing=False,
                validation_start=date(2021, 12, 1),
                test_start=date(2023, 3, 1),
            ),
        )

    # 4. Missing test_start split boundary rejected
    with pytest.raises(ContractViolationError, match="test_start"):
        run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id="ml-dates-fail",
            ml_config=MLTransformConfig(
                enabled=True,
                approved_target=True,
                require_exact_filing=True,
                validation_start=None,
                test_start=None,
            ),
        )

    # 4b. TWO_WAY_PURGED_OOF with validation_start=None succeeds cleanly (P1-1)
    bundle_two_way = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="ml-two-way",
        ml_config=MLTransformConfig(
            enabled=True,
            approved_target=True,
            require_exact_filing=True,
            validation_start=None,
            test_start=date(2023, 3, 1),
        ),
    )
    assert bundle_two_way.ml_scores.height > 0
    assert bundle_two_way.ml_scores.schema == ML_SCORES_SCHEMA
    assert bundle_two_way.ml_run_manifest is not None
    assert bundle_two_way.ml_run_manifest["split_mode"] == "TWO_WAY_PURGED_OOF"

    # 5. Valid enabled ML run produces validated scores, evaluation report & provenance manifest
    bundle_on = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="ml-on",
        ml_config=MLTransformConfig(
            enabled=True,
            approved_target=True,
            require_exact_filing=True,
            validation_start=date(2021, 12, 1),
            test_start=date(2023, 3, 1),
        ),
    )
    assert bundle_on.ml_scores.height > 0
    assert bundle_on.ml_scores.schema == ML_SCORES_SCHEMA
    assert bundle_on.evaluation_report is not None
    assert "test" in bundle_on.evaluation_report
    assert bundle_on.ml_run_manifest is not None
    assert bundle_on.ml_run_manifest["require_exact_filing"] is True
    assert bundle_on.ml_run_manifest["approved_target"] is True
    assert bundle_on.ml_run_manifest["facts_sha256"] == bundle_on.diff.facts_sha256
    assert (
        bundle_on.ml_run_manifest["issuers_sha256"]
        == bundle_on.diff.issuers_metadata_sha256
    )

    # Blocking QG2 audit passes cleanly with ML scores attached
    run_blocking_publish_audits(bundle_on, run_id="ml-on")


def _empirical_snapshot_dir() -> Path | None:
    raw = os.environ.get(EMPIRICAL_SNAPSHOT_ENV, "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_dir():
        return None
    return candidate


@pytest.mark.skipif(
    _empirical_snapshot_dir() is None,
    reason=f"Set {EMPIRICAL_SNAPSHOT_ENV} to a read-only empirical snapshot directory to run.",
)
class TestEmpiricalSnapshotIncrementalTransform:
    """Opt-in read-only verification against a real multi-issuer empirical snapshot."""

    def test_empirical_full_incremental_icb_rebuild_and_blocking_audits(
        self, tmp_path: Path
    ) -> None:
        snap_dir = _empirical_snapshot_dir()
        assert snap_dir is not None

        # Strictly read-only reads from empirical snapshot; outputs only in tmp_path.
        facts = pl.read_parquet(snap_dir / "financial_facts.parquet")
        issuers = pl.read_parquet(snap_dir / "issuers.parquet")
        as_of = date(2026, 11, 19)
        state_path = tmp_path / TRANSFORM_STATE_FILENAME

        # 1. Full transform + blocking audits on full real universe
        full_bundle = run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id="emp-full",
            state_path=state_path,
        )
        assert full_bundle.total_issuers == issuers["issuer_id"].n_unique()
        assert full_bundle.critical_issuer_ratio <= 0.10
        assert full_bundle.financial_ratios.schema == FINANCIAL_RATIOS_SCHEMA
        assert len(full_bundle.financial_ratios.columns) == len(FINANCIAL_RATIOS_SCHEMA)
        assert (
            full_bundle.accounting_red_flags.filter(
                pl.col("flag_code") == "RF_08_NON_CORE_EARNINGS_DEPENDENCE"
            ).height
            > 0
        )

        updated_qs = run_blocking_publish_audits(full_bundle, run_id="emp-full")
        assert updated_qs.height > 0
        write_transform_state(
            state_path,
            full_bundle.next_transform_state,
            snapshot_id="emp_snap_001",
        )

        # 2. Mutate 2 clean issuers + delete 1 clean issuer -> verify incremental == full <= 1e-12
        clean_issuer_ids = sorted(
            full_bundle.clean_facts["issuer_id"].unique().to_list()
        )
        assert len(clean_issuer_ids) >= 5
        mod_a, mod_b, del_c = (
            clean_issuer_ids[0],
            clean_issuer_ids[1],
            clean_issuer_ids[2],
        )

        mutated_facts = facts.filter(pl.col("issuer_id") != del_c).with_columns(
            pl.when(
                pl.col("issuer_id").is_in([mod_a, mod_b])
                & (pl.col("metric_id") == "IS_NET_REVENUE")
                & pl.col("value").is_not_null()
            )
            .then(pl.col("value") * 1.03)
            .otherwise(pl.col("value"))
            .alias("value")
        )

        inc_bundle = run_incremental_analytics_transform(
            mutated_facts,
            issuers,
            as_of=as_of,
            run_id="emp-inc",
            previous_ratios=full_bundle.financial_ratios,
            previous_benchmarks=full_bundle.industry_benchmarks,
            previous_red_flags=full_bundle.accounting_red_flags,
            previous_snapshot_id="emp_snap_001",
            state_path=state_path,
        )
        assert inc_bundle.diff.full_rebuild is False
        assert inc_bundle.diff.benchmarks_full_rebuild is False
        assert inc_bundle.diff.added_issuers == ()
        assert inc_bundle.diff.modified_issuers == tuple(sorted([mod_a, mod_b]))
        assert inc_bundle.diff.deleted_issuers == (del_c,)
        assert inc_bundle.diff.dirty_issuers == tuple(sorted([mod_a, mod_b, del_c]))
        assert (
            inc_bundle.financial_ratios.filter(pl.col("issuer_id") == del_c).height == 0
        )

        ref_bundle = run_incremental_analytics_transform(
            mutated_facts,
            issuers,
            as_of=as_of,
            run_id="emp-ref",
        )
        _assert_frames_close(
            inc_bundle.financial_ratios,
            ref_bundle.financial_ratios,
            sort_keys=RATIO_KEY,
            abs_tol=1e-12,
        )
        _assert_frames_close(
            inc_bundle.industry_benchmarks,
            ref_bundle.industry_benchmarks,
            sort_keys=BENCHMARK_KEY,
            abs_tol=1e-12,
        )
        _assert_frames_close(
            inc_bundle.accounting_red_flags,
            ref_bundle.accounting_red_flags,
            sort_keys=FLAG_KEY,
            abs_tol=1e-12,
        )
        run_blocking_publish_audits(inc_bundle, run_id="emp-inc")

        # 3. Mutate 1 issuer's icb_code_level2 -> verify auto-full-rebuild of industry_benchmarks
        icb2_values = sorted(issuers["icb_code_level2"].drop_nulls().unique().to_list())
        assert len(icb2_values) >= 2
        target_issuer = clean_issuer_ids[3]
        current_icb2 = issuers.filter(pl.col("issuer_id") == target_issuer)[
            "icb_code_level2"
        ].item()
        new_icb2 = next(code for code in icb2_values if code != current_icb2)
        mutated_issuers = issuers.with_columns(
            pl.when(pl.col("issuer_id") == target_issuer)
            .then(pl.lit(new_icb2))
            .otherwise(pl.col("icb_code_level2"))
            .alias("icb_code_level2")
        )

        icb_inc = run_incremental_analytics_transform(
            facts,
            mutated_issuers,
            as_of=as_of,
            run_id="emp-icb-inc",
            previous_ratios=full_bundle.financial_ratios,
            previous_benchmarks=full_bundle.industry_benchmarks,
            previous_red_flags=full_bundle.accounting_red_flags,
            previous_snapshot_id="emp_snap_001",
            state_path=state_path,
        )
        assert icb_inc.diff.issuers_metadata_changed is True
        assert icb_inc.diff.benchmarks_full_rebuild is True
        assert icb_inc.diff.full_rebuild is False

        icb_ref = run_incremental_analytics_transform(
            facts,
            mutated_issuers,
            as_of=as_of,
            run_id="emp-icb-ref",
        )
        _assert_frames_close(
            icb_inc.industry_benchmarks,
            icb_ref.industry_benchmarks,
            sort_keys=BENCHMARK_KEY,
            abs_tol=1e-12,
        )
        run_blocking_publish_audits(icb_inc, run_id="emp-icb-inc")


def test_incremental_market_prices_change_marks_only_affected_treasury_issuer_dirty(
    tmp_path: Path,
) -> None:
    """Changing market_prices for one treasury-share issuer triggers partial incremental recompute."""
    as_of = date(2025, 6, 1)
    state_path = tmp_path / "metadata" / TRANSFORM_STATE_FILENAME
    issuers = _make_corporate_issuers(2)
    issuer_ids = issuers["issuer_id"].to_list()
    facts = _make_universe_facts(issuer_ids).with_columns(
        pl.when(pl.col("metric_id") == "BS_TREASURY_SHARES")
        .then(pl.lit(-10_000.0))
        .when(pl.col("metric_id") == "BS_ORDINARY_SHARES_CAPITAL")
        .then(pl.lit(10_000_000_000.0))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    facts = canonicalize_financial_facts(facts)

    trade_dates = [
        date(2024, 1, 30),
        date(2024, 5, 2),
        date(2024, 7, 30),
        date(2024, 10, 30),
        date(2025, 1, 30),
        date(2025, 3, 25),
    ]

    def _prices(c1_shares: float, c2_shares: float) -> pl.DataFrame:
        rows: list[dict[str, object]] = []
        for d in trade_dates:
            for iid, ticker, sh in (
                ("CORP_01", "C01", c1_shares),
                ("CORP_02", "C02", c2_shares),
            ):
                rows.append(
                    {
                        "issuer_id": iid,
                        "ticker": ticker,
                        "trade_date": d,
                        "close_raw": 20_000.0,
                        "close_adj": 20_000.0,
                        "volume": 100_000.0,
                        "shares_outstanding_market": sh,
                        "source_provider": "vndirect_finfo",
                    }
                )
        return pl.DataFrame(rows)

    prices_v1 = _prices(950_000.0, 960_000.0)
    b1 = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="mkt-v1",
        state_path=state_path,
        market_prices=prices_v1,
    )
    write_transform_state(state_path, b1.next_transform_state, snapshot_id="snap_m1")

    c1_r1 = b1.financial_ratios.filter(
        (pl.col("issuer_id") == "CORP_01")
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    ).row(0, named=True)
    assert c1_r1["shares_source"] == "SHARES_OUTSTANDING_MARKET"
    assert c1_r1["estimated_shares_outstanding"] == pytest.approx(950_000.0)
    assert c1_r1["eps_estimate_basis"] == "SHARES_OUTSTANDING_MARKET"
    assert c1_r1["eps_ttm_estimated"] is not None

    # Update market_prices ONLY for CORP_01 (950k -> 900k); facts & CORP_02 unchanged
    prices_v2 = _prices(900_000.0, 960_000.0)
    b2 = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="mkt-v2",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_m1",
        state_path=state_path,
        market_prices=prices_v2,
    )
    assert b2.diff.full_rebuild is False
    assert b2.diff.dirty_issuers == ("CORP_01",)
    assert b2.diff.modified_issuers == ("CORP_01",)

    b2_full = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id="mkt-v2-full",
        market_prices=prices_v2,
    )
    _assert_frames_close(
        b2.financial_ratios,
        b2_full.financial_ratios,
        sort_keys=RATIO_KEY,
        abs_tol=1e-12,
    )
    c1_r2 = b2.financial_ratios.filter(
        (pl.col("issuer_id") == "CORP_01")
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    ).row(0, named=True)
    assert c1_r2["estimated_shares_outstanding"] == pytest.approx(900_000.0)
    assert c1_r2["eps_ttm_estimated"] > c1_r1["eps_ttm_estimated"]
