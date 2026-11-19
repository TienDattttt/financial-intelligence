"""Deterministic corporate financial stress testing following IMF WP/15/216.

Verified Sources:
    Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
    IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (docs/layers/LAYER_2_NORMALIZATION.md:138).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from finintel.exceptions import ContractViolationError

REQUIRED_STRESS_COLUMNS: tuple[str, ...] = (
    "accounting_regime",
    "statement_scope",
    "period_type",
    "period_end_date",
    "net_revenue_ttm",
    "gross_profit_ttm",
    "cogs_ttm",
    "cfo_ttm",
    "borrowing_cost_ttm",
    "total_debt",
    "ebit_ttm",
    "gross_margin",
    "effective_tax_rate",
    "interest_coverage",
    "interest_coverage_flag",
)

_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("interest_rate_shock_bps", -500.0, 1500.0),
    ("revenue_shock_pct", -0.80, 0.50),
    ("gross_margin_shock_pp", -0.50, 0.20),
    ("dso_shock_days", -90.0, 180.0),
    ("dio_shock_days", -90.0, 180.0),
)


@dataclass(frozen=True, slots=True)
class StressScenario:
    """Parameterized shock scenario for deterministic corporate balance-sheet stress testing."""

    name: str
    interest_rate_shock_bps: float = 0.0
    revenue_shock_pct: float = 0.0
    gross_margin_shock_pp: float = 0.0
    dso_shock_days: float = 0.0
    dio_shock_days: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ContractViolationError(
                "StressScenario requires a non-empty scenario name."
            )
        for field_name, low, high in _BOUNDS:
            raw = getattr(self, field_name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ContractViolationError(
                    f"StressScenario parameter {field_name} must be a finite number."
                )
            value = float(raw)
            if not math.isfinite(value) or not (low <= value <= high):
                raise ContractViolationError(
                    f"StressScenario parameter {field_name}={value!r} outside allowed bounds [{low}, {high}]."
                )


def simulate_financial_stress(
    ratios_with_inputs: pl.DataFrame,
    scenario: StressScenario,
) -> pl.DataFrame:
    """Vectorized simulation of stressed EBIT, borrowing cost, ICR, CFO, and IMF Debt-at-Risk."""
    missing = [
        col for col in REQUIRED_STRESS_COLUMNS if col not in ratios_with_inputs.columns
    ]
    if missing:
        raise ContractViolationError(
            "Stress simulation frame is missing required columns: "
            + ", ".join(missing)
            + "."
        )

    c = pl.col
    corporate = (c("accounting_regime") == "CORPORATE").fill_null(False)
    eligible = (
        corporate
        & c("net_revenue_ttm").is_not_null()
        & c("net_revenue_ttm").is_finite()
        & (c("net_revenue_ttm") > 0.0)
    ).fill_null(False)

    rev_prime = pl.when(eligible).then(
        c("net_revenue_ttm") * (1.0 + float(scenario.revenue_shock_pct))
    )
    gm_prime = pl.when(eligible & c("gross_margin").is_not_null()).then(
        c("gross_margin") + float(scenario.gross_margin_shock_pp)
    )
    gp_prime = pl.when(eligible & gm_prime.is_not_null()).then(rev_prime * gm_prime)
    delta_gp = gp_prime - c("gross_profit_ttm")
    cogs_prime = pl.when(eligible & gp_prime.is_not_null()).then(
        pl.max_horizontal(pl.lit(0.0), rev_prime - gp_prime)
    )

    ebit_prime = pl.when(
        eligible & c("ebit_ttm").is_not_null() & delta_gp.is_not_null()
    ).then(c("ebit_ttm") + delta_gp)
    int_prime = pl.when(
        eligible & c("borrowing_cost_ttm").is_not_null() & c("total_debt").is_not_null()
    ).then(
        pl.max_horizontal(
            pl.lit(0.0),
            c("borrowing_cost_ttm")
            + c("total_debt") * (float(scenario.interest_rate_shock_bps) / 10000.0),
        )
    )
    delta_int = int_prime - c("borrowing_cost_ttm")

    icr_quotient = ebit_prime / int_prime
    stressed_icr_flag = (
        pl.when(~corporate)
        .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
        .when(~eligible | ebit_prime.is_null() | int_prime.is_null())
        .then(pl.lit("MISSING_INPUT"))
        .when(~ebit_prime.is_finite() | ~int_prime.is_finite())
        .then(pl.lit("NUMERIC_OVERFLOW"))
        .when(int_prime == 0.0)
        .then(pl.lit("NO_INTEREST_EXPENSE"))
        .when(~icr_quotient.is_finite())
        .then(pl.lit("NUMERIC_OVERFLOW"))
        .otherwise(pl.lit("VALID"))
    )
    stressed_icr = (
        pl.when(stressed_icr_flag == "VALID").then(icr_quotient).cast(pl.Float64)
    )

    ebt_0 = c("ebit_ttm") - c("borrowing_cost_ttm")
    ebt_prime = ebit_prime - int_prime
    delta_tax = pl.when(
        ebt_prime.is_not_null()
        & ebt_0.is_not_null()
        & c("effective_tax_rate").is_not_null()
    ).then(
        (
            pl.max_horizontal(pl.lit(0.0), ebt_prime)
            - pl.max_horizontal(pl.lit(0.0), ebt_0)
        )
        * c("effective_tax_rate")
    )
    delta_wc_cash = -(rev_prime / 365.0) * float(scenario.dso_shock_days) - (
        cogs_prime / 365.0
    ) * float(scenario.dio_shock_days)
    cfo_prime = pl.when(
        eligible
        & c("cfo_ttm").is_not_null()
        & delta_gp.is_not_null()
        & delta_int.is_not_null()
        & delta_tax.is_not_null()
        & delta_wc_cash.is_not_null()
    ).then(c("cfo_ttm") + (delta_gp - delta_int) - delta_tax + delta_wc_cash)

    baseline_dar = (
        pl.when(~eligible | c("total_debt").is_null() | c("ebit_ttm").is_null())
        .then(pl.lit(None, dtype=pl.Boolean))
        .when(
            (c("interest_coverage_flag") == "NO_INTEREST_EXPENSE")
            | (c("total_debt") <= 0.0)
        )
        .then(pl.lit(False))
        .when(
            c("interest_coverage").is_not_null()
            & (c("interest_coverage") < 1.0)
            & (c("total_debt") > 0.0)
        )
        .then(pl.lit(True))
        .when(c("interest_coverage").is_not_null() & (c("interest_coverage") >= 1.0))
        .then(pl.lit(False))
        .otherwise(pl.lit(None, dtype=pl.Boolean))
    )
    stressed_dar = (
        pl.when(~eligible | c("total_debt").is_null() | ebit_prime.is_null())
        .then(pl.lit(None, dtype=pl.Boolean))
        .when((stressed_icr_flag == "NO_INTEREST_EXPENSE") | (c("total_debt") <= 0.0))
        .then(pl.lit(False))
        .when(
            stressed_icr.is_not_null() & (stressed_icr < 1.0) & (c("total_debt") > 0.0)
        )
        .then(pl.lit(True))
        .when(stressed_icr.is_not_null() & (stressed_icr >= 1.0))
        .then(pl.lit(False))
        .otherwise(pl.lit(None, dtype=pl.Boolean))
    )

    return ratios_with_inputs.with_columns(
        pl.lit(scenario.name).alias("scenario_name"),
        rev_prime.cast(pl.Float64).alias("stressed_net_revenue_ttm"),
        gm_prime.cast(pl.Float64).alias("stressed_gross_margin"),
        gp_prime.cast(pl.Float64).alias("stressed_gross_profit_ttm"),
        cogs_prime.cast(pl.Float64).alias("stressed_cogs_ttm"),
        ebit_prime.cast(pl.Float64).alias("stressed_ebit_ttm"),
        int_prime.cast(pl.Float64).alias("stressed_borrowing_cost_ttm"),
        stressed_icr.alias("stressed_interest_coverage"),
        stressed_icr_flag.alias("stressed_interest_coverage_flag"),
        cfo_prime.cast(pl.Float64).alias("stressed_cfo_ttm"),
        baseline_dar.alias("baseline_debt_at_risk"),
        stressed_dar.alias("stressed_debt_at_risk"),
    ).with_columns(
        pl.when(
            c("baseline_debt_at_risk").is_not_null()
            & c("stressed_debt_at_risk").is_not_null()
        )
        .then((~c("baseline_debt_at_risk")) & c("stressed_debt_at_risk"))
        .otherwise(pl.lit(None, dtype=pl.Boolean))
        .alias("newly_distressed_under_shock")
    )


def summarize_stress_by_sector(stressed_df: pl.DataFrame) -> pl.DataFrame:
    """Aggregate IMF Debt-at-Risk exposure by ICB Level 2 sector for a single period slice."""
    required = (
        "issuer_id",
        "statement_scope",
        "period_type",
        "period_end_date",
        "total_debt",
        "baseline_debt_at_risk",
        "stressed_debt_at_risk",
        "newly_distressed_under_shock",
    )
    missing = [col for col in required if col not in stressed_df.columns]
    if missing:
        raise ContractViolationError(
            "Stressed DataFrame is missing required summary columns: "
            + ", ".join(missing)
            + "."
        )
    slice_keys = ["statement_scope", "period_type", "period_end_date"]
    if stressed_df.select(slice_keys).unique().height > 1:
        raise ContractViolationError(
            "summarize_stress_by_sector requires a single period slice "
            "(statement_scope, period_type, period_end_date)."
        )

    c = pl.col
    staged = stressed_df.with_columns(
        (
            pl.when(
                c("icb_code_level2").is_not_null()
                & (c("icb_code_level2").cast(pl.String).str.strip_chars() != "")
            )
            .then(c("icb_code_level2").cast(pl.String))
            .otherwise(pl.lit("UNCLASSIFIED"))
            if "icb_code_level2" in stressed_df.columns
            else pl.lit("UNCLASSIFIED")
        ).alias("icb_code_level2"),
        (
            pl.when(
                c("icb_name_level2").is_not_null()
                & (c("icb_name_level2").cast(pl.String).str.strip_chars() != "")
            )
            .then(c("icb_name_level2").cast(pl.String))
            .otherwise(pl.lit("UNCLASSIFIED"))
            if "icb_name_level2" in stressed_df.columns
            else pl.lit("UNCLASSIFIED")
        ).alias("icb_name_level2"),
    )

    evaluable = (
        c("baseline_debt_at_risk").is_not_null()
        & c("stressed_debt_at_risk").is_not_null()
        & c("total_debt").is_not_null()
    )
    evaluable_debt = pl.when(evaluable).then(c("total_debt")).otherwise(0.0)
    return (
        staged.group_by(["icb_code_level2", "icb_name_level2"])
        .agg(
            c("issuer_id").n_unique().cast(pl.UInt32).alias("issuer_count"),
            evaluable_debt.sum().cast(pl.Float64).alias("total_debt_sum"),
            pl.when(evaluable & c("baseline_debt_at_risk"))
            .then(c("total_debt"))
            .otherwise(0.0)
            .sum()
            .alias("_baseline_dar_debt"),
            pl.when(evaluable & c("stressed_debt_at_risk"))
            .then(c("total_debt"))
            .otherwise(0.0)
            .sum()
            .alias("_stressed_dar_debt"),
            c("newly_distressed_under_shock")
            .drop_nulls()
            .sum()
            .cast(pl.UInt32)
            .alias("newly_distressed_count"),
        )
        .with_columns(
            pl.when(c("total_debt_sum") > 0.0)
            .then(c("_baseline_dar_debt") / c("total_debt_sum"))
            .otherwise(0.0)
            .cast(pl.Float64)
            .alias("baseline_debt_at_risk_pct"),
            pl.when(c("total_debt_sum") > 0.0)
            .then(c("_stressed_dar_debt") / c("total_debt_sum"))
            .otherwise(0.0)
            .cast(pl.Float64)
            .alias("stressed_debt_at_risk_pct"),
        )
        .select(
            "icb_code_level2",
            "icb_name_level2",
            "issuer_count",
            "total_debt_sum",
            "baseline_debt_at_risk_pct",
            "stressed_debt_at_risk_pct",
            "newly_distressed_count",
        )
        .sort(["icb_code_level2", "icb_name_level2"])
    )
