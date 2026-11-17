"""Assemble the three Layer 4A Parquet tables for snapshot publication.

Verified Sources:
    The FinanceToolkit, Qlib and financial-disclosure sources in the
    companion modules govern calculation. Layer 4 section 6 governs output.
"""

from datetime import date

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.dupont import add_dupont
from finintel.analytics.intermediates import RATIO_KEY, prepare_periods
from finintel.analytics.ratios import CORE_RATIOS, EXTRA_RATIOS, calculate_ratios
from finintel.analytics.red_flags import detect_red_flags

RATIOS_TABLE = "financial_ratios.parquet"
BENCHMARKS_TABLE = "industry_benchmarks.parquet"
RED_FLAGS_TABLE = "accounting_red_flags.parquet"
INTERMEDIATES = (
    "total_debt",
    "total_cash_and_st_investments",
    "net_debt",
    "parent_equity",
    "working_capital",
    "invested_capital",
    "ebit_ttm",
    "ebitda_ttm",
    "effective_tax_rate",
    "nopat_ttm",
    "fcf_ttm",
    "estimated_shares_outstanding",
    "implied_weighted_shares",
    "eps_basic_reported",
)
DUPONT_FIELDS = (
    "tax_burden",
    "interest_burden",
    "equity_multiplier_total",
    "parent_profit_share",
    "equity_multiplier_parent",
    "dupont_valid_flag",
)


def build_analytics_tables(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
) -> dict[str, pl.DataFrame]:
    """Generate snapshot-ready ratios, benchmarks and red flags at a PIT cutoff."""
    periods = prepare_periods(facts, issuers, as_of=as_of)
    ratios = add_dupont(calculate_ratios(periods))
    financial_ratios = ratios.select(
        *RATIO_KEY,
        "taxonomy_version",
        *(
            pl.when(pl.col(name).is_finite()).then(pl.col(name)).alias(name)
            for name in INTERMEDIATES
        ),
        "ebitda_flag",
        *(
            column
            for name in (*CORE_RATIOS, *EXTRA_RATIOS)
            for column in (name, f"{name}_flag")
        ),
        *DUPONT_FIELDS,
    ).sort(RATIO_KEY)
    return {
        RATIOS_TABLE: financial_ratios,
        BENCHMARKS_TABLE: benchmark_ratios(ratios),
        RED_FLAGS_TABLE: detect_red_flags(ratios),
    }
