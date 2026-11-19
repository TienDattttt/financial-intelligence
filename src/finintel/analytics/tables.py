"""Assemble the three Layer 4A Parquet tables for snapshot publication.

Verified Sources:
    The FinanceToolkit, Qlib and financial-disclosure sources in the
    companion modules govern calculation. Layer 4 section 6 governs output.
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed., McGraw-Hill).
    Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material Accounting
    Misstatements." Contemporary Accounting Research, Vol. 28, Issue 1, pp. 17-82.
    Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
    IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (docs/layers/LAYER_2_NORMALIZATION.md:138).
"""

from datetime import date

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.dupont import add_dupont
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.analytics.red_flags import detect_red_flags
from finintel.storage.schemas import (
    BENCHMARKS_TABLE as BENCHMARKS_TABLE,
    FINANCIAL_RATIOS_SCHEMA,
    RATIO_INTERMEDIATES,
    RATIO_KEY,
    RATIOS_TABLE as RATIOS_TABLE,
    RED_FLAGS_TABLE as RED_FLAGS_TABLE,
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
    financial_ratios = (
        ratios.with_columns(
            pl.when(pl.col(name).is_finite()).then(pl.col(name)).alias(name)
            for name in RATIO_INTERMEDIATES
        )
        .select(FINANCIAL_RATIOS_SCHEMA.names())
        .sort(RATIO_KEY)
    )
    return {
        RATIOS_TABLE: financial_ratios,
        BENCHMARKS_TABLE: benchmark_ratios(ratios),
        RED_FLAGS_TABLE: detect_red_flags(ratios),
    }
