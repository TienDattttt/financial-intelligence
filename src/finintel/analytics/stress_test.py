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
MIN_INTEREST_DENOMINATOR_VND = 100_000_000.0


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
    rev_shock = float(scenario.revenue_shock_pct)
    gm_shock = float(scenario.gross_margin_shock_pp)
    ir_shock = float(scenario.interest_rate_shock_bps)
    dso_shock = float(scenario.dso_shock_days)
    dio_shock = float(scenario.dio_shock_days)
    base_cols = list(ratios_with_inputs.columns)

    # Stage intermediate expressions sequentially so Polars builds a flat DAG
    # instead of duplicating nested sub-expressions across 12 output columns.
    staged = (
        ratios_with_inputs.with_columns(
            (c("accounting_regime") == "CORPORATE").fill_null(False).alias("_corp"),
        )
        .with_columns(
            (
                c("_corp")
                & c("net_revenue_ttm").is_not_null()
                & c("net_revenue_ttm").is_finite()
                & (c("net_revenue_ttm") > 0.0)
            )
            .fill_null(False)
            .alias("_elig"),
        )
        .with_columns(
            pl.when(c("_elig"))
            .then(c("net_revenue_ttm") * (1.0 + rev_shock))
            .cast(pl.Float64)
            .alias("stressed_net_revenue_ttm"),
            pl.when(c("_elig") & c("gross_margin").is_not_null())
            .then(c("gross_margin") + gm_shock)
            .cast(pl.Float64)
            .alias("stressed_gross_margin"),
            pl.when(
                c("_elig")
                & c("borrowing_cost_ttm").is_not_null()
                & c("total_debt").is_not_null()
            )
            .then(
                pl.max_horizontal(
                    pl.lit(0.0),
                    c("borrowing_cost_ttm") + c("total_debt") * (ir_shock / 10000.0),
                )
            )
            .cast(pl.Float64)
            .alias("stressed_borrowing_cost_ttm"),
        )
        .with_columns(
            pl.when(c("_elig") & c("stressed_gross_margin").is_not_null())
            .then(c("stressed_net_revenue_ttm") * c("stressed_gross_margin"))
            .cast(pl.Float64)
            .alias("stressed_gross_profit_ttm"),
            (c("stressed_borrowing_cost_ttm") - c("borrowing_cost_ttm")).alias(
                "_delta_int"
            ),
        )
        .with_columns(
            (c("stressed_gross_profit_ttm") - c("gross_profit_ttm")).alias("_delta_gp"),
            pl.when(c("_elig") & c("stressed_gross_profit_ttm").is_not_null())
            .then(
                pl.max_horizontal(
                    pl.lit(0.0),
                    c("stressed_net_revenue_ttm") - c("stressed_gross_profit_ttm"),
                )
            )
            .cast(pl.Float64)
            .alias("stressed_cogs_ttm"),
        )
        .with_columns(
            pl.when(
                c("_elig") & c("ebit_ttm").is_not_null() & c("_delta_gp").is_not_null()
            )
            .then(c("ebit_ttm") + c("_delta_gp"))
            .cast(pl.Float64)
            .alias("stressed_ebit_ttm"),
        )
        .with_columns(
            (c("stressed_ebit_ttm") / c("stressed_borrowing_cost_ttm")).alias("_icr_q"),
            (c("ebit_ttm") - c("borrowing_cost_ttm")).alias("_ebt_0"),
            (c("stressed_ebit_ttm") - c("stressed_borrowing_cost_ttm")).alias("_ebt_p"),
            (
                -(c("stressed_net_revenue_ttm") / 365.0) * dso_shock
                - (c("stressed_cogs_ttm") / 365.0) * dio_shock
            ).alias("_delta_wc"),
        )
        .with_columns(
            pl.when(~c("_corp"))
            .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
            .when(
                ~c("_elig")
                | c("stressed_ebit_ttm").is_null()
                | c("stressed_borrowing_cost_ttm").is_null()
            )
            .then(pl.lit("MISSING_INPUT"))
            .when(
                ~c("stressed_ebit_ttm").is_finite()
                | ~c("stressed_borrowing_cost_ttm").is_finite()
            )
            .then(pl.lit("NUMERIC_OVERFLOW"))
            .when(c("stressed_borrowing_cost_ttm") == 0.0)
            .then(pl.lit("NO_INTEREST_EXPENSE"))
            .when(c("stressed_borrowing_cost_ttm") < MIN_INTEREST_DENOMINATOR_VND)
            .then(pl.lit("TINY_DENOMINATOR"))
            .when(~c("_icr_q").is_finite())
            .then(pl.lit("NUMERIC_OVERFLOW"))
            .otherwise(pl.lit("VALID"))
            .alias("stressed_interest_coverage_flag"),
            pl.when(
                c("_ebt_p").is_not_null()
                & c("_ebt_0").is_not_null()
                & c("effective_tax_rate").is_not_null()
            )
            .then(
                (
                    pl.max_horizontal(pl.lit(0.0), c("_ebt_p"))
                    - pl.max_horizontal(pl.lit(0.0), c("_ebt_0"))
                )
                * c("effective_tax_rate")
            )
            .alias("_delta_tax"),
        )
        .with_columns(
            pl.when(c("stressed_interest_coverage_flag") == "VALID")
            .then(c("_icr_q"))
            .cast(pl.Float64)
            .alias("stressed_interest_coverage"),
            pl.when(
                c("_elig")
                & c("cfo_ttm").is_not_null()
                & c("_delta_gp").is_not_null()
                & c("_delta_int").is_not_null()
                & c("_delta_tax").is_not_null()
                & c("_delta_wc").is_not_null()
            )
            .then(
                c("cfo_ttm")
                + (c("_delta_gp") - c("_delta_int"))
                - c("_delta_tax")
                + c("_delta_wc")
            )
            .cast(pl.Float64)
            .alias("stressed_cfo_ttm"),
            pl.when(~c("_elig") | c("total_debt").is_null() | c("ebit_ttm").is_null())
            .then(pl.lit(None, dtype=pl.Boolean))
            .when(
                (c("interest_coverage_flag") == "NO_INTEREST_EXPENSE")
                | (c("total_debt") <= 0.0)
            )
            .then(pl.lit(False))
            .when(
                (c("interest_coverage_flag") == "TINY_DENOMINATOR")
                & c("borrowing_cost_ttm").is_not_null()
                & (c("borrowing_cost_ttm") > 0.0)
            )
            .then((c("ebit_ttm") < c("borrowing_cost_ttm")) & (c("total_debt") > 0.0))
            .when(
                c("interest_coverage").is_not_null()
                & (c("interest_coverage") < 1.0)
                & (c("total_debt") > 0.0)
            )
            .then(pl.lit(True))
            .when(
                c("interest_coverage").is_not_null() & (c("interest_coverage") >= 1.0)
            )
            .then(pl.lit(False))
            .otherwise(pl.lit(None, dtype=pl.Boolean))
            .alias("baseline_debt_at_risk"),
        )
        .with_columns(
            pl.lit(scenario.name).alias("scenario_name"),
            pl.when(
                ~c("_elig")
                | c("total_debt").is_null()
                | c("stressed_ebit_ttm").is_null()
            )
            .then(pl.lit(None, dtype=pl.Boolean))
            .when(
                (c("stressed_interest_coverage_flag") == "NO_INTEREST_EXPENSE")
                | (c("total_debt") <= 0.0)
            )
            .then(pl.lit(False))
            .when(
                (c("stressed_interest_coverage_flag") == "TINY_DENOMINATOR")
                & c("stressed_borrowing_cost_ttm").is_not_null()
                & (c("stressed_borrowing_cost_ttm") > 0.0)
            )
            .then(
                (c("stressed_ebit_ttm") < c("stressed_borrowing_cost_ttm"))
                & (c("total_debt") > 0.0)
            )
            .when(
                c("stressed_interest_coverage").is_not_null()
                & (c("stressed_interest_coverage") < 1.0)
                & (c("total_debt") > 0.0)
            )
            .then(pl.lit(True))
            .when(
                c("stressed_interest_coverage").is_not_null()
                & (c("stressed_interest_coverage") >= 1.0)
            )
            .then(pl.lit(False))
            .otherwise(pl.lit(None, dtype=pl.Boolean))
            .alias("stressed_debt_at_risk"),
        )
    )

    return staged.select(
        *base_cols,
        "scenario_name",
        "stressed_net_revenue_ttm",
        "stressed_gross_margin",
        "stressed_gross_profit_ttm",
        "stressed_cogs_ttm",
        "stressed_ebit_ttm",
        "stressed_borrowing_cost_ttm",
        "stressed_interest_coverage",
        "stressed_interest_coverage_flag",
        "stressed_cfo_ttm",
        "baseline_debt_at_risk",
        "stressed_debt_at_risk",
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
