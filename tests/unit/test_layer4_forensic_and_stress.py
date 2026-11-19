"""TDD unit tests for Wave 2A (Stream A3) Layer 4A forensic ratios, red flags, and stress testing.

Verified Sources:
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed., McGraw-Hill),
    Shenanigan #3 (RF_08) & Shenanigan #4 (RF_09).
    Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material Accounting
    Misstatements." Contemporary Accounting Research, Vol. 28, Issue 1, pp. 17-82.
    Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
    IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (Appendix IV - B01-DN & B02-DN),
    SIGNED_EXPENSE convention (docs/layers/LAYER_2_NORMALIZATION.md:138), BS_CIP_LONG_TERM (242 -> 252),
    and TT99 B02-DN Code 21 (IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS).
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import polars as pl
import pytest

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.dupont import add_dupont
from finintel.analytics.intermediates import (
    FLOWS,
    ML_FLOWS,
    ML_STOCKS,
    STOCKS,
    prepare_periods,
)
from finintel.analytics.ratios import FORENSIC_RATIOS, calculate_ratios
from finintel.analytics.red_flags import detect_red_flags
from finintel.analytics.stress_test import (
    StressScenario,
    simulate_financial_stress,
    summarize_stress_by_sector,
)
from finintel.analytics.tables import (
    BENCHMARKS_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    build_analytics_tables,
)
from finintel.exceptions import ContractViolationError
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.storage.schemas import (
    CORE_RATIOS,
    EXTRA_RATIOS,
    FINANCIAL_RATIOS_SCHEMA,
)


def _issuer(
    issuer_id: str = "FPT",
    regime: str = "CORPORATE",
    icb2: str = "9500",
    icb2_name: str = "Công nghệ thông tin",
    icb3: str = "9530",
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": [issuer_id],
            "ticker": [issuer_id],
            "exchange": ["HOSE"],
            "company_name_vn": [f"Company {issuer_id}"],
            "accounting_regime": [regime],
            "icb_code_level2": [icb2],
            "icb_name_level2": [icb2_name],
            "icb_code_level3": [icb3],
            "icb_name_level3": ["Phần mềm"],
            "industry_snapshot_date": [date(2025, 1, 1)],
            "delisted_date": [None],
        }
    )


def _build_facts(
    periods: dict[tuple[int, int | None], dict[str, float | None]],
    *,
    issuer_id: str = "FPT",
    scope: str = "CONSOLIDATED",
    source_provider: str = "fhsc",
    published_at_basis: str = "EXACT_FILING",
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    non_comparable = {
        "IS_INTEREST_EXPENSE",
        "IS_BORROWING_COSTS",
        "IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS",
        "IS_EPS_BASIC_REPORTED",
        "BS_TREASURY_SHARES",
    }
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
        avail = end + timedelta(days=60 if quarter is None else 25)
        filing = f"{issuer_id}-{year}-{quarter}"
        for metric, value in values.items():
            if metric == "IS_EPS_BASIC_REPORTED":
                v_type = "NON_ADDITIVE"
            elif metric.startswith("BS_"):
                v_type = "STOCK"
            else:
                v_type = "FLOW"
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
                    "published_at": avail
                    if published_at_basis == "EXACT_FILING"
                    else None,
                    "effective_available_at": avail,
                    "published_at_basis": published_at_basis,
                    "revision_no": 1,
                    "filing_id": filing,
                    "source_filing_ids": [filing],
                    "source_provider": source_provider,
                    "taxonomy_version": "VAS_TT200_2014"
                    if year < 2026
                    else "VAS_TT99_2025",
                    "revision_reason": "ORIGINAL",
                    "audit_status": "AUDITED" if quarter is None else "UNAUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "derivation_method": "REPORTED_DIRECTLY",
                    "metric_id": metric,
                    "value": value,
                    "value_type": v_type,
                    "comparable_across_taxonomy": metric not in non_comparable,
                    "is_filed_late": False,
                }
            )
    return pl.DataFrame(rows, schema=FACT_SCHEMA)


def test_gap02_blk09_tt99_continuity_and_ttm_exact_filing() -> None:
    """Verify 2025 TT200 -> 2026 TT99 quarterly TTM continuity, _exact_filing_basis, and non-FHSC guard."""

    def _q_metrics(year: int, scale: float) -> dict[str, float | None]:
        debt_key = "IS_INTEREST_EXPENSE" if year < 2026 else "IS_BORROWING_COSTS"
        return {
            "BS_TOTAL_ASSETS": 1000.0 * scale,
            "BS_TOTAL_EQUITY": 600.0 * scale,
            "BS_MINORITY_INTEREST": 50.0 * scale,
            "BS_CASH_AND_EQUIVALENTS": 100.0 * scale,
            "BS_SHORT_TERM_BORROWINGS": 200.0 * scale,
            "BS_LONG_TERM_BORROWINGS": 100.0 * scale,
            "BS_CURRENT_ASSETS": 400.0 * scale,
            "BS_CURRENT_LIABILITIES": 200.0 * scale,
            "IS_NET_REVENUE": 300.0 * scale,
            "IS_COST_OF_GOODS_SOLD": 180.0 * scale,
            "IS_GROSS_PROFIT": 120.0 * scale,
            "IS_SELLING_EXPENSES": 20.0 * scale,
            "IS_ADMINISTRATIVE_EXPENSES": 15.0 * scale,
            "IS_OPERATING_PROFIT": 85.0 * scale,
            "IS_PROFIT_BEFORE_TAX": 75.0 * scale,
            debt_key: 10.0 * scale,
            "IS_CURRENT_INCOME_TAX": 15.0 * scale,
            "IS_DEFERRED_INCOME_TAX": 0.0,
            "IS_NET_INCOME": 60.0 * scale,
            "IS_NET_INCOME_PARENT": 55.0 * scale,
            "CF_OPERATING_CASH_FLOW": 70.0 * scale,
            "CF_CAPITAL_EXPENDITURE": -20.0 * scale,
            "CF_DEPRECIATION_AMORTIZATION": 12.0 * scale,
        }

    # Include 2024 Q2-Q4 + 2025 Q1-Q4 + 2026 Q1-Q2 so 2025 Q1/Q2 also have full 4Q TTM for YoY growth.
    periods_map: dict[tuple[int, int | None], dict[str, float | None]] = {
        (2024, 2): _q_metrics(2024, 0.9),
        (2024, 3): _q_metrics(2024, 0.9),
        (2024, 4): _q_metrics(2024, 0.9),
        (2025, 1): _q_metrics(2025, 1.0),
        (2025, 2): _q_metrics(2025, 1.0),
        (2025, 3): _q_metrics(2025, 1.0),
        (2025, 4): _q_metrics(2025, 1.0),
        (2026, 1): _q_metrics(2026, 1.2),
        (2026, 2): _q_metrics(2026, 1.2),
    }
    fhsc_facts = _build_facts(
        periods_map, source_provider="fhsc", published_at_basis="EXACT_FILING"
    )
    periods_df = prepare_periods(fhsc_facts, _issuer(), as_of=date(2026, 9, 1))
    ratios_df = add_dupont(calculate_ratios(periods_df))

    q_2026 = ratios_df.filter(pl.col("fiscal_year") == 2026).sort("fiscal_quarter")
    assert q_2026.height == 2
    for row in q_2026.to_dicts():
        assert row["_exact_filing_basis"] is True
        assert row["ebit_ttm"] is not None and row["ebit_ttm"] > 0
        assert row["ebit_flag"] == "VALID"
        assert row["ebitda_ttm"] is not None and row["ebitda_ttm"] > 0
        assert row["ebitda_flag"] == "VALID"
        assert row["nopat_ttm"] is not None and row["nopat_ttm"] > 0
        assert row["nopat_flag"] == "VALID"
        assert row["fcf_ttm"] is not None and row["fcf_ttm"] > 0
        assert row["fcf_flag"] == "VALID"
        assert row["roic"] is not None and row["roic_flag"] == "VALID"
        assert (
            row["interest_coverage"] is not None
            and row["interest_coverage_flag"] == "VALID"
        )
        assert (
            row["ebit_growth_yoy"] is not None
            and row["ebit_growth_yoy_flag"] == "VALID"
        )
        assert row["dupont_valid_flag"] is True

    # Non-FHSC source must remain blocked by MIXED_TAXONOMY across TT200 -> TT99.
    non_fhsc_facts = _build_facts(
        periods_map, source_provider="file", published_at_basis="EXACT_FILING"
    )
    non_fhsc_ratios = add_dupont(
        calculate_ratios(
            prepare_periods(non_fhsc_facts, _issuer(), as_of=date(2026, 9, 1))
        )
    )
    for row in non_fhsc_ratios.filter(pl.col("fiscal_year") == 2026).to_dicts():
        assert row["ebit_ttm"] is None
        assert row["ebit_flag"] == "MIXED_TAXONOMY"
        assert row["ebitda_flag"] == "MIXED_TAXONOMY"
        assert row["roic_flag"] == "MIXED_TAXONOMY"
        assert row["interest_coverage_flag"] == "MIXED_TAXONOMY"
        assert row["ebit_growth_yoy_flag"] == "MIXED_TAXONOMY"
        assert row["dupont_valid_flag"] is False


def test_gap04_med03_exact_filing_basis_eliminates_8q_domino_and_rejects_late_prior() -> (
    None
):
    """2017 STATUTORY_LAG + 2018-Q1..2019-Q3 EXACT_FILING -> 2018-Q4..2019-Q3 have _exact_filing_basis == True."""
    q_template = {"BS_TOTAL_ASSETS": 500.0, "IS_NET_REVENUE": 100.0}
    facts_2017 = _build_facts(
        {(2017, q): q_template for q in range(1, 5)},
        published_at_basis="STATUTORY_LAG",
    )
    facts_2018_2019 = _build_facts(
        {
            **{(2018, q): q_template for q in range(1, 5)},
            **{(2019, q): q_template for q in range(1, 4)},
        },
        published_at_basis="EXACT_FILING",
    )
    combined = pl.concat([facts_2017, facts_2018_2019])
    periods = prepare_periods(combined, _issuer(), as_of=date(2020, 1, 1))

    # 2018-Q1..Q3 TTM windows still include 2017 STATUTORY_LAG quarters -> False.
    q_2018_1_3 = periods.filter(
        (pl.col("fiscal_year") == 2018) & (pl.col("fiscal_quarter") <= 3)
    )
    assert q_2018_1_3["_exact_filing_basis"].to_list() == [False, False, False]

    # 2018-Q4..2019-Q3 have 4 EXACT_FILING quarters in current TTM and _period_available_at_prior <= _amounts_available_at.
    q_2018_4_to_2019_3 = periods.filter(
        ((pl.col("fiscal_year") == 2018) & (pl.col("fiscal_quarter") == 4))
        | (pl.col("fiscal_year") == 2019)
    )
    assert q_2018_4_to_2019_3["_exact_filing_basis"].to_list() == [
        True,
        True,
        True,
        True,
    ]

    # If a prior-year period has _period_available_at_prior > _amounts_available_at, _exact_filing_basis must be False.
    late_prior = combined.with_columns(
        pl.when((pl.col("fiscal_year") == 2018) & (pl.col("fiscal_quarter") == 1))
        .then(pl.lit(date(2019, 6, 1)))
        .otherwise(pl.col("effective_available_at"))
        .alias("effective_available_at"),
        pl.when((pl.col("fiscal_year") == 2018) & (pl.col("fiscal_quarter") == 1))
        .then(pl.lit(date(2019, 6, 1)))
        .otherwise(pl.col("published_at"))
        .alias("published_at"),
    )
    periods_late = prepare_periods(late_prior, _issuer(), as_of=date(2020, 1, 1))
    row_2019_q1 = periods_late.filter(
        (pl.col("fiscal_year") == 2019) & (pl.col("fiscal_quarter") == 1)
    ).row(0, named=True)
    assert row_2019_q1["_exact_filing_basis"] is False


def test_hi11_disjoint_ml_inputs_no_duplicate_error_and_38_benchmark_ratios() -> None:
    """Verify STOCKS/ML_STOCKS and FLOWS/ML_FLOWS are disjoint and benchmark_ratios covers 38 ratios."""
    assert set(STOCKS).isdisjoint(ML_STOCKS)
    assert set(FLOWS).isdisjoint(ML_FLOWS)
    assert len(CORE_RATIOS) == 28
    assert len(EXTRA_RATIOS) == 7
    assert len(FORENSIC_RATIOS) == 3

    sample_metrics: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 1000.0,
        "BS_TOTAL_EQUITY": 500.0,
        "BS_MINORITY_INTEREST": 0.0,
        "BS_CURRENT_ASSETS": 400.0,
        "BS_CURRENT_LIABILITIES": 200.0,
        "BS_TOTAL_LIABILITIES": 500.0,
        "BS_CASH_AND_EQUIVALENTS": 100.0,
        "BS_SHORT_TERM_INVESTMENTS": 50.0,
        "BS_SHORT_TERM_BORROWINGS": 100.0,
        "BS_LONG_TERM_BORROWINGS": 100.0,
        "BS_CONVERTIBLE_BONDS": 0.0,
        "BS_TRADE_RECEIVABLES_ST": 100.0,
        "BS_INVENTORIES": 100.0,
        "BS_TRADE_PAYABLES_ST": 80.0,
        "BS_ORDINARY_SHARES_CAPITAL": 100_000.0,
        "BS_TREASURY_SHARES": 0.0,
        "BS_TANGIBLE_FIXED_ASSETS": 300.0,
        "BS_CIP_LONG_TERM": 50.0,
        "BS_RETAINED_EARNINGS": 200.0,
        "BS_LT_FINANCIAL_INVESTMENTS": 50.0,
        "IS_NET_REVENUE": 800.0,
        "IS_COST_OF_GOODS_SOLD": 480.0,
        "IS_GROSS_PROFIT": 320.0,
        "IS_SELLING_EXPENSES": 60.0,
        "IS_ADMINISTRATIVE_EXPENSES": 40.0,
        "IS_OPERATING_PROFIT": 220.0,
        "IS_PROFIT_BEFORE_TAX": 200.0,
        "IS_INTEREST_EXPENSE": 20.0,
        "IS_CURRENT_INCOME_TAX": 40.0,
        "IS_DEFERRED_INCOME_TAX": 0.0,
        "IS_NET_INCOME": 160.0,
        "IS_NET_INCOME_PARENT": 160.0,
        "IS_EPS_BASIC_REPORTED": 16.0,
        "CF_OPERATING_CASH_FLOW": 180.0,
        "CF_CAPITAL_EXPENDITURE": -50.0,
        "CF_DEPRECIATION_AMORTIZATION": 30.0,
    }
    history = _build_facts(
        {
            (2024, None): sample_metrics,
            (2025, None): {
                **sample_metrics,
                "IS_NET_REVENUE": 960.0,
                "IS_GROSS_PROFIT": 384.0,
                "IS_OPERATING_PROFIT": 264.0,
                "IS_PROFIT_BEFORE_TAX": 240.0,
                "IS_NET_INCOME": 192.0,
                "IS_NET_INCOME_PARENT": 192.0,
            },
        }
    )

    p_no_ml = prepare_periods(
        history, _issuer(), as_of=date(2026, 6, 1), include_ml_inputs=False
    )
    p_with_ml = prepare_periods(
        history, _issuer(), as_of=date(2026, 6, 1), include_ml_inputs=True
    )
    assert p_no_ml.height == 2
    assert p_with_ml.height == 2

    ratios = calculate_ratios(p_no_ml)
    benchmarks = benchmark_ratios(ratios)
    bench_2025 = benchmarks.filter(pl.col("period_end_date") == date(2025, 12, 31))
    observed_metrics = set(bench_2025["metric_or_ratio_name"].to_list())
    expected_38 = set((*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS))
    assert len(expected_38) == 38
    assert observed_metrics == expected_38


def test_core_earnings_signed_expense_reversal_and_pre_ttm_missing_selling_rule() -> (
    None
):
    """Preserve SIGNED_EXPENSE sign (no .abs()) and apply missing IS_SELLING_EXPENSES = 0.0 BEFORE compute_ttm."""
    # 1. Reversal of selling expenses: IS_SELLING_EXPENSES = -5, GP = 100, Admin = 20 -> Core OP = 100 - (-5) - 20 = 85.
    reversal_facts = _build_facts(
        {
            (2024, None): {"BS_TOTAL_ASSETS": 1000.0},
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "IS_NET_REVENUE": 200.0,
                "IS_GROSS_PROFIT": 100.0,
                "IS_SELLING_EXPENSES": -5.0,
                "IS_ADMINISTRATIVE_EXPENSES": 20.0,
                "IS_OPERATING_PROFIT": 85.0,
                "IS_PROFIT_BEFORE_TAX": 100.0,
                "IS_INTEREST_EXPENSE": 10.0,
            },
        }
    )
    ratios = calculate_ratios(
        prepare_periods(reversal_facts, _issuer(), as_of=date(2026, 6, 1))
    )
    row = ratios.filter(pl.col("fiscal_year") == 2025).row(0, named=True)
    assert row["core_operating_profit_ttm"] == pytest.approx(85.0)
    assert row["core_operating_profit_flag"] == "VALID"
    assert row["core_operating_margin"] == pytest.approx(85.0 / 200.0)
    assert row["core_to_ebt_ratio"] == pytest.approx((85.0 - 10.0) / 100.0)
    assert row["non_core_distortion_to_assets"] == pytest.approx(
        (100.0 - (85.0 - 10.0)) / 1000.0
    )

    # 2. Missing IS_SELLING_EXPENSES in Q4 only on a filing that has GP, Admin, and Operating Profit (Code 30):
    # Q1..Q3 each have GP=100, Selling=10, Admin=20, OP=70.
    # Q4 omits IS_SELLING_EXPENSES while reporting GP=100, Admin=20, OP=80.
    # Pre-TTM synthesis sets Q4 Selling = 0.0 -> 4Q TTM Selling = 30.0 -> 4Q Core OP = 400 - 30 - 80 = 290.0.
    q_with_selling = {
        "IS_NET_REVENUE": 200.0,
        "IS_GROSS_PROFIT": 100.0,
        "IS_SELLING_EXPENSES": 10.0,
        "IS_ADMINISTRATIVE_EXPENSES": 20.0,
        "IS_OPERATING_PROFIT": 70.0,
    }
    q4_missing_selling = {
        "IS_NET_REVENUE": 200.0,
        "IS_GROSS_PROFIT": 100.0,
        "IS_ADMINISTRATIVE_EXPENSES": 20.0,
        "IS_OPERATING_PROFIT": 80.0,
    }
    q_facts = _build_facts(
        {
            (2025, 1): q_with_selling,
            (2025, 2): q_with_selling,
            (2025, 3): q_with_selling,
            (2025, 4): q4_missing_selling,
        }
    )
    q_ratios = calculate_ratios(
        prepare_periods(q_facts, _issuer(), as_of=date(2026, 6, 1))
    )
    q4_row = q_ratios.filter(pl.col("fiscal_quarter") == 4).row(0, named=True)
    assert q4_row["IS_SELLING_EXPENSES"] == pytest.approx(30.0)
    assert q4_row["core_operating_profit_ttm"] == pytest.approx(290.0)
    assert q4_row["core_operating_profit_flag"] == "VALID"

    # If IS_OPERATING_PROFIT is also absent on Q4, IS_SELLING_EXPENSES must NOT be fabricated as 0.0.
    q4_incomplete = {
        "IS_NET_REVENUE": 200.0,
        "IS_GROSS_PROFIT": 100.0,
        "IS_ADMINISTRATIVE_EXPENSES": 20.0,
    }
    incomplete_facts = _build_facts(
        {
            (2025, 1): q_with_selling,
            (2025, 2): q_with_selling,
            (2025, 3): q_with_selling,
            (2025, 4): q4_incomplete,
        }
    )
    inc_row = (
        calculate_ratios(
            prepare_periods(incomplete_facts, _issuer(), as_of=date(2026, 6, 1))
        )
        .filter(pl.col("fiscal_quarter") == 4)
        .row(0, named=True)
    )
    assert inc_row["core_operating_profit_ttm"] is None
    assert inc_row["core_operating_profit_flag"] == "MISSING_INPUT"


def test_rf08_branch_separation_tt99_code21_and_other_profit_fallback() -> None:
    """RF_08 Branch 1 fires without opening assets; evidence includes ip_disposal_gain_loss_ttm & fallback other_profit."""
    # Branch 1 in 2026 (TT99): no 2025 prior period (opening assets NULL), core_op - debt_cost <= 0, EBT > 0.
    branch1_tt99 = _build_facts(
        {
            (2026, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "IS_NET_REVENUE": 200.0,
                "IS_GROSS_PROFIT": 50.0,
                "IS_SELLING_EXPENSES": 25.0,
                "IS_ADMINISTRATIVE_EXPENSES": 20.0,
                "IS_OPERATING_PROFIT": 60.0,
                "IS_BORROWING_COSTS": 10.0,
                "IS_FINANCIAL_REVENUE": 40.0,
                "IS_OTHER_INCOME": 25.0,
                "IS_OTHER_EXPENSES": 5.0,
                "IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS": 15.0,
                "IS_PROFIT_BEFORE_TAX": 70.0,
            }
        }
    )
    flags = detect_red_flags(
        calculate_ratios(
            prepare_periods(branch1_tt99, _issuer(), as_of=date(2027, 6, 1))
        )
    )
    rf08 = flags.filter(pl.col("flag_code") == "RF_08_NON_CORE_EARNINGS_DEPENDENCE")
    assert rf08.height == 1
    row = rf08.row(0, named=True)
    assert row["severity"] == "HIGH"
    assert "liên doanh" in row["explanation_text_vn"]
    assert "tiền gửi" in row["explanation_text_vn"]
    ev = json.loads(row["evidence_json"])
    assert ev["ebt_ttm"] == pytest.approx(70.0)
    assert ev["core_operating_profit_ttm"] == pytest.approx(5.0)
    assert ev["debt_cost_ttm"] == pytest.approx(10.0)
    assert ev["other_profit_ttm"] == pytest.approx(20.0)
    assert ev["ip_disposal_gain_loss_ttm"] == pytest.approx(15.0)
    assert ev["non_core_distortion_to_assets"] is None

    # Branch 2 in 2025 (TT200): core_op - debt_cost > 0, but core_to_ebt_ratio < 0.50 and distortion > 0.03.
    branch2_tt200 = _build_facts(
        {
            (2024, None): {"BS_TOTAL_ASSETS": 1000.0},
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "IS_NET_REVENUE": 300.0,
                "IS_GROSS_PROFIT": 80.0,
                "IS_SELLING_EXPENSES": 30.0,
                "IS_ADMINISTRATIVE_EXPENSES": 20.0,
                "IS_OPERATING_PROFIT": 90.0,
                "IS_INTEREST_EXPENSE": 10.0,
                "IS_FINANCIAL_REVENUE": 70.0,
                "IS_OTHER_PROFIT": 10.0,
                "IS_PROFIT_BEFORE_TAX": 90.0,
            },
        }
    )
    flags_b2 = detect_red_flags(
        calculate_ratios(
            prepare_periods(branch2_tt200, _issuer(), as_of=date(2026, 6, 1))
        )
    ).filter(pl.col("flag_code") == "RF_08_NON_CORE_EARNINGS_DEPENDENCE")
    assert flags_b2.height == 1
    ev_b2 = json.loads(flags_b2.row(0, named=True)["evidence_json"])
    assert ev_b2["core_to_ebt_ratio"] == pytest.approx(20.0 / 90.0)
    assert ev_b2["non_core_distortion_to_assets"] == pytest.approx(70.0 / 1000.0)
    assert ev_b2["ip_disposal_gain_loss_ttm"] is None


def test_rf09_frozen_cip_uses_bs_cip_long_term_and_handles_zero_opening_tfa() -> None:
    """RF_09 uses BS_CIP_LONG_TERM (242 -> 252), handles TFA_{t-4} == 0 without ZeroDivisionError, and ignores healthy CAPEX."""
    # Case 1: TFA_{t-4} == 0 and TFA_t == 0 across TT200 (2025) -> TT99 (2026) transition.
    frozen_zero_tfa = _build_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "BS_CIP_LONG_TERM": 250.0,
                "BS_TANGIBLE_FIXED_ASSETS": 0.0,
                "IS_NET_REVENUE": 500.0,
            },
            (2026, None): {
                "BS_TOTAL_ASSETS": 1020.0,
                "BS_CIP_LONG_TERM": 260.0,
                "BS_TANGIBLE_FIXED_ASSETS": 0.0,
                "IS_NET_REVENUE": 510.0,
            },
        }
    )
    flags = detect_red_flags(
        calculate_ratios(
            prepare_periods(frozen_zero_tfa, _issuer(), as_of=date(2027, 6, 1))
        )
    ).filter(pl.col("flag_code") == "RF_09_FROZEN_CIP_CAPITALIZATION")
    assert flags.height == 1
    row = flags.row(0, named=True)
    assert row["severity"] == "MEDIUM"
    ev = json.loads(row["evidence_json"])
    assert ev["cip_to_assets_t"] == pytest.approx(260.0 / 1020.0)
    assert ev["cip_to_assets_prior"] == pytest.approx(250.0 / 1000.0)
    assert ev["cip_growth_yoy"] == pytest.approx((260.0 - 250.0) / 250.0)
    assert ev["tangible_fixed_assets_growth_yoy"] is None
    assert ev["revenue_growth_yoy"] == pytest.approx(0.02)

    # Case 2: TFA increases +25% (commissioned fixed assets) -> RF_09 must NOT fire.
    healthy_commissioning = _build_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "BS_CIP_LONG_TERM": 250.0,
                "BS_TANGIBLE_FIXED_ASSETS": 200.0,
                "IS_NET_REVENUE": 500.0,
            },
            (2026, None): {
                "BS_TOTAL_ASSETS": 1020.0,
                "BS_CIP_LONG_TERM": 260.0,
                "BS_TANGIBLE_FIXED_ASSETS": 250.0,
                "IS_NET_REVENUE": 510.0,
            },
        }
    )
    flags_healthy = detect_red_flags(
        calculate_ratios(
            prepare_periods(healthy_commissioning, _issuer(), as_of=date(2027, 6, 1))
        )
    ).filter(pl.col("flag_code") == "RF_09_FROZEN_CIP_CAPITALIZATION")
    assert flags_healthy.is_empty()

    # Case 3: BS_CIP_LONG_TERM is NULL -> no-op.
    no_cip = _build_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000.0,
                "BS_TANGIBLE_FIXED_ASSETS": 200.0,
                "IS_NET_REVENUE": 500.0,
            },
            (2026, None): {
                "BS_TOTAL_ASSETS": 1020.0,
                "BS_TANGIBLE_FIXED_ASSETS": 200.0,
                "IS_NET_REVENUE": 510.0,
            },
        }
    )
    flags_no_cip = detect_red_flags(
        calculate_ratios(prepare_periods(no_cip, _issuer(), as_of=date(2027, 6, 1)))
    ).filter(pl.col("flag_code") == "RF_09_FROZEN_CIP_CAPITALIZATION")
    assert flags_no_cip.is_empty()


def test_stress_test_zero_shock_identity_monotonicity_and_edge_cases() -> None:
    """Verify IMF WP/15/216 stress test zero-shock identity, monotonicity, tax-shield cap, and 3-valued NULL handling."""
    issuers = pl.concat(
        [
            _issuer("A", icb2="9500", icb2_name="Tech"),
            _issuer("B", icb2="9500", icb2_name="Tech"),
            _issuer("LOSS", icb2="2000", icb2_name="Industrials"),
        ]
    )
    facts = pl.concat(
        [
            # Firm A: healthy borrower with positive interest expense.
            _build_facts(
                {
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 1000.0,
                        "BS_TOTAL_EQUITY": 500.0,
                        "BS_SHORT_TERM_BORROWINGS": 300.0,
                        "IS_NET_REVENUE": 1000.0,
                        "IS_COST_OF_GOODS_SOLD": 700.0,
                        "IS_GROSS_PROFIT": 300.0,
                        "IS_PROFIT_BEFORE_TAX": 90.0,
                        "IS_INTEREST_EXPENSE": 10.0,
                        "IS_CURRENT_INCOME_TAX": 18.0,
                        "IS_DEFERRED_INCOME_TAX": 0.0,
                        "CF_OPERATING_CASH_FLOW": 120.0,
                    }
                },
                issuer_id="A",
            ),
            # Firm B: zero baseline interest expense (NO_INTEREST_EXPENSE) but has debt = 400.
            _build_facts(
                {
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 1000.0,
                        "BS_TOTAL_EQUITY": 400.0,
                        "BS_SHORT_TERM_BORROWINGS": 400.0,
                        "IS_NET_REVENUE": 500.0,
                        "IS_COST_OF_GOODS_SOLD": 400.0,
                        "IS_GROSS_PROFIT": 100.0,
                        "IS_PROFIT_BEFORE_TAX": 10.0,
                        "IS_INTEREST_EXPENSE": 0.0,
                        "IS_CURRENT_INCOME_TAX": 2.0,
                        "IS_DEFERRED_INCOME_TAX": 0.0,
                        "CF_OPERATING_CASH_FLOW": 40.0,
                    }
                },
                issuer_id="B",
            ),
            # Firm LOSS: negative baseline EBT (-50), must not receive synthetic 20% tax refund on deepening loss.
            _build_facts(
                {
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 1000.0,
                        "BS_TOTAL_EQUITY": 300.0,
                        "BS_SHORT_TERM_BORROWINGS": 500.0,
                        "IS_NET_REVENUE": 400.0,
                        "IS_COST_OF_GOODS_SOLD": 360.0,
                        "IS_GROSS_PROFIT": 40.0,
                        "IS_PROFIT_BEFORE_TAX": -50.0,
                        "IS_INTEREST_EXPENSE": 30.0,
                        "IS_CURRENT_INCOME_TAX": 0.0,
                        "IS_DEFERRED_INCOME_TAX": 0.0,
                        "CF_OPERATING_CASH_FLOW": -10.0,
                    }
                },
                issuer_id="LOSS",
            ),
        ]
    )
    tables = build_analytics_tables(facts, issuers, as_of=date(2026, 6, 1))
    ratios_df = tables[RATIOS_TABLE].join(
        issuers.select("issuer_id", "icb_code_level2", "icb_name_level2"),
        on="issuer_id",
        how="left",
    )

    # 1. Zero-Shock Identity (within 1e-12 for all Rev > 0 rows).
    zero_df = simulate_financial_stress(ratios_df, StressScenario(name="zero"))
    for row in zero_df.to_dicts():
        assert math.isclose(
            row["stressed_net_revenue_ttm"], row["net_revenue_ttm"], abs_tol=1e-12
        )
        assert math.isclose(row["stressed_ebit_ttm"], row["ebit_ttm"], abs_tol=1e-12)
        assert math.isclose(
            row["stressed_borrowing_cost_ttm"], row["borrowing_cost_ttm"], abs_tol=1e-12
        )
        assert math.isclose(row["stressed_cfo_ttm"], row["cfo_ttm"], abs_tol=1e-12)
        if row["interest_coverage"] is not None:
            assert math.isclose(
                row["stressed_interest_coverage"],
                row["interest_coverage"],
                abs_tol=1e-12,
            )
        else:
            assert row["stressed_interest_coverage"] is None
        assert row["stressed_debt_at_risk"] == row["baseline_debt_at_risk"]
        assert row["newly_distressed_under_shock"] is False

    # 2. Rate cut (-500 bps) clamps Int' >= 0.0.
    rate_cut = simulate_financial_stress(
        ratios_df, StressScenario(name="cut", interest_rate_shock_bps=-500.0)
    )
    for row in rate_cut.to_dicts():
        assert row["stressed_borrowing_cost_ttm"] >= 0.0
    row_a_cut = rate_cut.filter(pl.col("issuer_id") == "A").row(0, named=True)
    # Firm A baseline Int = 10, debt = 300, -500 bps = -15 -> clamped to 0.0 -> NO_INTEREST_EXPENSE.
    assert row_a_cut["stressed_borrowing_cost_ttm"] == 0.0
    assert row_a_cut["stressed_interest_coverage"] is None
    assert row_a_cut["stressed_interest_coverage_flag"] == "NO_INTEREST_EXPENSE"
    assert row_a_cut["stressed_debt_at_risk"] is False

    # 3. Severe shock: Firm B (NO_INTEREST_EXPENSE at baseline) gets +500 bps on debt 400 -> Int' = 20 > EBIT' (10) -> ICR' = 0.5 < 1.0.
    severe = simulate_financial_stress(
        ratios_df,
        StressScenario(
            name="severe",
            interest_rate_shock_bps=500.0,
            revenue_shock_pct=-0.20,
            gross_margin_shock_pp=-0.05,
        ),
    )
    row_b_sev = severe.filter(pl.col("issuer_id") == "B").row(0, named=True)
    assert row_b_sev["baseline_debt_at_risk"] is False
    assert row_b_sev["stressed_debt_at_risk"] is True
    assert row_b_sev["newly_distressed_under_shock"] is True

    # 4. Firm LOSS has EBT_0 = -50 < 0 and EBT' < 0 -> delta_tax MUST be 0.0 (no synthetic 20% tax shield on loss!).
    loss_shock = simulate_financial_stress(
        ratios_df,
        StressScenario(name="loss_rev_drop", revenue_shock_pct=-0.25),
    )
    row_loss = loss_shock.filter(pl.col("issuer_id") == "LOSS").row(0, named=True)
    expected_delta_gp = 400.0 * 0.75 * 0.10 - 40.0  # -10.0
    assert row_loss["stressed_cfo_ttm"] == pytest.approx(
        row_loss["cfo_ttm"] + expected_delta_gp, abs=1e-12
    )

    # 5. Monotonicity check across progressively harsher shocks.
    mild = simulate_financial_stress(
        ratios_df,
        StressScenario(
            name="mild", interest_rate_shock_bps=100.0, revenue_shock_pct=-0.05
        ),
    )
    harsh = simulate_financial_stress(
        ratios_df,
        StressScenario(
            name="harsh", interest_rate_shock_bps=400.0, revenue_shock_pct=-0.20
        ),
    )
    icr_a_zero = zero_df.filter(pl.col("issuer_id") == "A")[
        "stressed_interest_coverage"
    ].item()
    icr_a_mild = mild.filter(pl.col("issuer_id") == "A")[
        "stressed_interest_coverage"
    ].item()
    icr_a_harsh = harsh.filter(pl.col("issuer_id") == "A")[
        "stressed_interest_coverage"
    ].item()
    assert icr_a_zero >= icr_a_mild >= icr_a_harsh
    assert (
        zero_df["stressed_debt_at_risk"].sum()
        <= mild["stressed_debt_at_risk"].sum()
        <= harsh["stressed_debt_at_risk"].sum()
    )

    # 6. Single-slice sector summary & multi-period ContractViolationError guard.
    sector_summary = summarize_stress_by_sector(severe)
    assert sector_summary.height == 2
    tech_row = sector_summary.filter(pl.col("icb_code_level2") == "9500").row(
        0, named=True
    )
    assert tech_row["issuer_count"] == 2
    assert tech_row["total_debt_sum"] == pytest.approx(700.0)
    assert tech_row["newly_distressed_count"] >= 1

    multi_period = pl.concat(
        [
            severe,
            severe.with_columns(pl.lit(date(2024, 12, 31)).alias("period_end_date")),
        ]
    )
    with pytest.raises(ContractViolationError, match="single period"):
        summarize_stress_by_sector(multi_period)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interest_rate_shock_bps": -501.0},
        {"interest_rate_shock_bps": 1501.0},
        {"revenue_shock_pct": -0.85},
        {"revenue_shock_pct": 0.55},
        {"gross_margin_shock_pp": -0.55},
        {"gross_margin_shock_pp": 0.25},
        {"dso_shock_days": -95.0},
        {"dso_shock_days": 185.0},
        {"dio_shock_days": -91.0},
        {"dio_shock_days": 181.0},
        {"interest_rate_shock_bps": float("nan")},
        {"revenue_shock_pct": float("inf")},
    ],
)
def test_stress_scenario_rejects_out_of_bounds_or_non_finite_parameters(
    kwargs: dict[str, float],
) -> None:
    """StressScenario.__post_init__ must enforce finite bounds on all 5 parameters."""
    with pytest.raises(ContractViolationError):
        StressScenario(name="invalid", **kwargs)
