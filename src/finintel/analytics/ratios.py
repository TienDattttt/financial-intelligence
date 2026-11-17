"""Calculate the Layer 4 ratio catalog with VAS boundary flags.

Verified Sources:
    https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/ratios/profitability_model.py
    https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/ratios/liquidity_model.py
    https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/ratios/solvency_model.py
    https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/ratios/efficiency_model.py
    Reuse independent formula expressions. VAS rules require both equity
    endpoints to be positive for ROE and disallow a nonpositive opening
    equity when computing point-in-time leverage. Zero interest produces
    a dedicated flag. TT200/TT99 comparability comes from normalized facts.
    https://vanban.chinhphu.vn/?docid=178998&lang=vi&pageid=27160
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-99-2025-tt-btc-46529/59658.htm
"""

from enum import StrEnum

import polars as pl

from finintel.analytics.intermediates import (
    ASSETS,
    CAPITAL_INPUTS,
    CASH_INPUTS,
    CFO,
    COGS,
    CORPORATE,
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    DEBT_INPUTS,
    EARNINGS,
    EBITDA_INPUTS,
    EBIT_INPUTS,
    EPS_INPUTS,
    EQUITY,
    FCF_INPUTS,
    GROSS_PROFIT,
    INVENTORY,
    LIABILITIES,
    NOPAT_INPUTS,
    OPERATING_PROFIT,
    PARENT_EARNINGS,
    PARENT_INPUTS,
    PAYABLES,
    REVENUE,
    TRADE_RECEIVABLES,
    average,
    cross_taxonomy,
    input_has,
)


class RatioStatusFlag(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    MISSING_PRIOR_YEAR_BASE = "MISSING_PRIOR_YEAR_BASE"
    MIXED_TAXONOMY = "MIXED_TAXONOMY"
    NEGATIVE_OR_ZERO_EQUITY = "NEGATIVE_OR_ZERO_EQUITY"
    NON_POSITIVE_INVESTED_CAPITAL = "NON_POSITIVE_INVESTED_CAPITAL"
    NON_POSITIVE_EBITDA = "NON_POSITIVE_EBITDA"
    NO_INTEREST_EXPENSE = "NO_INTEREST_EXPENSE"
    ZERO_OR_NEGATIVE_DENOMINATOR = "ZERO_OR_NEGATIVE_DENOMINATOR"
    DERIVED_Q4_INPUT = "DERIVED_Q4_INPUT"
    VALID = "VALID"
    NOT_APPLICABLE_FOR_REGIME = "NOT_APPLICABLE_FOR_REGIME"
    NON_POSITIVE_BASE_PERIOD = "NON_POSITIVE_BASE_PERIOD"
    MISSING_DEPRECIATION_DIRECT_CF = "MISSING_DEPRECIATION_DIRECT_CF"
    NUMERIC_OVERFLOW = "NUMERIC_OVERFLOW"


Flag = RatioStatusFlag
CORE_RATIOS = (
    "gross_margin",
    "ebit_margin",
    "ebitda_margin",
    "operating_margin",
    "net_profit_margin",
    "roa",
    "roe_total",
    "roe_parent",
    "roic",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "ocf_to_current_liabilities",
    "liabilities_to_assets",
    "financial_debt_to_assets",
    "debt_to_equity",
    "net_debt_to_equity",
    "net_debt_to_ebitda",
    "interest_coverage",
    "asset_turnover",
    "inventory_turnover",
    "receivables_turnover",
    "payables_turnover",
    "cash_conversion_cycle",
    "ocf_to_net_income",
    "fcf_to_revenue",
    "accruals_to_assets",
    "eps_ttm_estimated",
)
EXTRA_RATIOS = (
    "equity_to_assets",
    "dio",
    "dso",
    "dpo",
    "revenue_growth_yoy",
    "ebit_growth_yoy",
    "net_income_parent_growth_yoy",
)
USABLE_FLAGS = [Flag.VALID.value, Flag.DERIVED_Q4_INPUT.value]


def _has_bad_endpoint(name: str) -> pl.Expr:
    return (pl.col(name) <= 0) | (pl.col(f"{name}_prior") <= 0)


def _ratio(
    name: str,
    numerator: pl.Expr,
    denominator: pl.Expr,
    *inputs: str,
    prior: tuple[str, ...] = (),
    current_input: pl.Expr | None = None,
    universal: bool = False,
    boundary: tuple[pl.Expr, Flag] | None = None,
    missing_depreciation: bool = False,
) -> list[pl.Expr]:
    """Return N/D and its status. Never divide by a nonpositive denominator.

    Inputs identify the canonical amounts used. A missing opening fiscal
    period has its own status. Known incompatible amounts can be null due to
    the taxonomy guard and retain that reason.
    """
    c = pl.col
    operands = (*inputs, *(f"{metric}_prior" for metric in prior))
    mixed = input_has("mixed", *operands)
    missing_prior = pl.lit(False)
    if prior:
        mixed = mixed | cross_taxonomy(*prior)
        current = numerator if current_input is None else current_input
        missing_prior = c("_prior_end").is_null() & current.is_not_null()
    special, special_flag = boundary or (
        pl.lit(False),
        Flag.ZERO_OR_NEGATIVE_DENOMINATOR,
    )
    quotient = numerator / denominator
    status = (
        pl.when(pl.lit(not universal) & ~CORPORATE)
        .then(pl.lit(Flag.NOT_APPLICABLE_FOR_REGIME.value))
        .when(mixed)
        .then(pl.lit(Flag.MIXED_TAXONOMY.value))
        .when(
            pl.lit(special_flag == Flag.NEGATIVE_OR_ZERO_EQUITY)
            & special
            & numerator.is_not_null()
        )
        .then(pl.lit(Flag.NEGATIVE_OR_ZERO_EQUITY.value))
        .when(
            pl.lit(missing_depreciation)
            & (c("ebitda_flag") == Flag.MISSING_DEPRECIATION_DIRECT_CF.value)
        )
        .then(pl.lit(Flag.MISSING_DEPRECIATION_DIRECT_CF.value))
        .when(missing_prior)
        .then(pl.lit(Flag.MISSING_PRIOR_YEAR_BASE.value))
        .when(numerator.is_null() | denominator.is_null())
        .then(pl.lit(Flag.MISSING_INPUT.value))
        .when(special)
        .then(pl.lit(special_flag.value))
        .when(denominator <= 0)
        .then(pl.lit(Flag.ZERO_OR_NEGATIVE_DENOMINATOR.value))
        .when(~quotient.is_finite())
        .then(pl.lit(Flag.NUMERIC_OVERFLOW.value))
        .when(input_has("derived", *operands))
        .then(pl.lit(Flag.DERIVED_Q4_INPUT.value))
        .otherwise(pl.lit(Flag.VALID.value))
    )
    return [
        pl.when(status.is_in(USABLE_FLAGS)).then(quotient).cast(pl.Float64).alias(name),
        status.alias(f"{name}_flag"),
    ]


def _growth(
    name: str, amount: str, *inputs: str, universal: bool = False
) -> list[pl.Expr]:
    """Compute (current − prior)/prior from a known positive same-slot base."""
    current, prior = pl.col(amount), pl.col(f"{amount}_prior")
    return _ratio(
        name,
        current - prior,
        prior,
        *inputs,
        prior=inputs,
        current_input=current,
        universal=universal,
        boundary=(prior <= 0, Flag.NON_POSITIVE_BASE_PERIOD),
    )


def _days_and_cycle(frame: pl.DataFrame) -> pl.DataFrame:
    """Convert turnovers to 365/turnover and compute DIO + DSO − DPO.

    Missing or nonpositive turnover produces null. Preserve offending input
    status when the cycle cannot be computed.
    """
    c = pl.col
    days: list[pl.Expr] = []
    for name, turnover in (
        ("dio", "inventory_turnover"),
        ("dso", "receivables_turnover"),
        ("dpo", "payables_turnover"),
    ):
        value = c(turnover)
        quotient = 365.0 / value
        status = (
            pl.when(value <= 0)
            .then(pl.lit(Flag.ZERO_OR_NEGATIVE_DENOMINATOR.value))
            .when(~quotient.is_finite())
            .then(pl.lit(Flag.NUMERIC_OVERFLOW.value))
            .otherwise(c(f"{turnover}_flag"))
        )
        days.extend(
            [
                pl.when(status.is_in(USABLE_FLAGS)).then(quotient).alias(name),
                status.alias(f"{name}_flag"),
            ]
        )
    flags = [c(f"{name}_flag") for name in ("dio", "dso", "dpo")]
    errors = [pl.when(~flag.is_in(USABLE_FLAGS)).then(flag) for flag in flags]
    cycle = c("dio") + c("dso") - c("dpo")
    return frame.with_columns(days).with_columns(
        pl.when(cycle.is_finite()).then(cycle).alias("cash_conversion_cycle"),
        pl.coalesce(
            *errors,
            pl.when(~cycle.is_finite()).then(pl.lit(Flag.NUMERIC_OVERFLOW.value)),
            pl.when(
                pl.any_horizontal(
                    *(flag == Flag.DERIVED_Q4_INPUT.value for flag in flags)
                )
            ).then(pl.lit(Flag.DERIVED_Q4_INPUT.value)),
            pl.lit(Flag.VALID.value),
        ).alias("cash_conversion_cycle_flag"),
    )


def calculate_ratios(periods: pl.DataFrame) -> pl.DataFrame:
    """Add the 28 value/flag pairs, day counts and YoY growth to accounting periods.

    Canonical metric IDs and formulas are paired below. Missing inputs and
    nonpositive denominators are handled consistently by the ratio helper.
    """
    c = pl.col
    equity_guard = (_has_bad_endpoint(EQUITY), Flag.NEGATIVE_OR_ZERO_EQUITY)
    net_debt_inputs = (*DEBT_INPUTS, *CASH_INPUTS)
    revenue, earnings, cfo = c(REVENUE), c(EARNINGS), c(CFO)
    ebit, ebitda = c("ebit_ttm"), c("ebitda_ttm")
    debt, net_debt = c("total_debt"), c("net_debt")
    interest, cogs = c("_borrowing_cost_ttm"), c(COGS).abs()
    return _days_and_cycle(
        periods.with_columns(
            *_ratio("gross_margin", c(GROSS_PROFIT), revenue, GROSS_PROFIT, REVENUE),
            *_ratio("ebit_margin", ebit, revenue, *EBIT_INPUTS, REVENUE),
            *_ratio(
                "ebitda_margin",
                ebitda,
                revenue,
                *EBITDA_INPUTS,
                REVENUE,
                missing_depreciation=True,
            ),
            *_ratio(
                "operating_margin",
                c(OPERATING_PROFIT),
                revenue,
                OPERATING_PROFIT,
                REVENUE,
            ),
            *_ratio("net_profit_margin", earnings, revenue, EARNINGS, REVENUE),
            *_ratio(
                "roa",
                earnings,
                average(ASSETS),
                EARNINGS,
                ASSETS,
                prior=(ASSETS,),
                universal=True,
            ),
            *_ratio(
                "roe_total",
                earnings,
                average(EQUITY),
                EARNINGS,
                EQUITY,
                prior=(EQUITY,),
                boundary=equity_guard,
                universal=True,
            ),
            *_ratio(
                "roe_parent",
                c(PARENT_EARNINGS),
                average("parent_equity"),
                PARENT_EARNINGS,
                *PARENT_INPUTS,
                prior=PARENT_INPUTS,
                universal=True,
                boundary=(
                    _has_bad_endpoint("parent_equity"),
                    Flag.NEGATIVE_OR_ZERO_EQUITY,
                ),
            ),
            *_ratio(
                "roic",
                c("nopat_ttm"),
                average("invested_capital"),
                *NOPAT_INPUTS,
                *CAPITAL_INPUTS,
                prior=CAPITAL_INPUTS,
                boundary=(
                    _has_bad_endpoint("invested_capital"),
                    Flag.NON_POSITIVE_INVESTED_CAPITAL,
                ),
            ),
            *_ratio(
                "current_ratio",
                c(CURRENT_ASSETS),
                c(CURRENT_LIABILITIES),
                CURRENT_ASSETS,
                CURRENT_LIABILITIES,
            ),
            *_ratio(
                "quick_ratio",
                c("total_cash_and_st_investments") + c(TRADE_RECEIVABLES),
                c(CURRENT_LIABILITIES),
                *CASH_INPUTS,
                TRADE_RECEIVABLES,
                CURRENT_LIABILITIES,
            ),
            *_ratio(
                "cash_ratio",
                c("total_cash_and_st_investments"),
                c(CURRENT_LIABILITIES),
                *CASH_INPUTS,
                CURRENT_LIABILITIES,
            ),
            *_ratio(
                "ocf_to_current_liabilities",
                cfo,
                average(CURRENT_LIABILITIES),
                CFO,
                CURRENT_LIABILITIES,
                prior=(CURRENT_LIABILITIES,),
            ),
            *_ratio(
                "liabilities_to_assets",
                c(LIABILITIES),
                c(ASSETS),
                LIABILITIES,
                ASSETS,
                universal=True,
            ),
            *_ratio("financial_debt_to_assets", debt, c(ASSETS), *DEBT_INPUTS, ASSETS),
            *_ratio(
                "debt_to_equity",
                debt,
                c(EQUITY),
                *DEBT_INPUTS,
                EQUITY,
                boundary=equity_guard,
            ),
            *_ratio(
                "net_debt_to_equity",
                net_debt,
                c(EQUITY),
                *net_debt_inputs,
                EQUITY,
                boundary=equity_guard,
            ),
            *_ratio(
                "net_debt_to_ebitda",
                net_debt,
                ebitda,
                *net_debt_inputs,
                *EBITDA_INPUTS,
                boundary=(ebitda <= 0, Flag.NON_POSITIVE_EBITDA),
                missing_depreciation=True,
            ),
            *_ratio(
                "interest_coverage",
                ebit,
                interest,
                *EBIT_INPUTS,
                boundary=(interest == 0, Flag.NO_INTEREST_EXPENSE),
            ),
            *_ratio(
                "asset_turnover",
                revenue,
                average(ASSETS),
                REVENUE,
                ASSETS,
                prior=(ASSETS,),
            ),
            *_ratio(
                "inventory_turnover",
                cogs,
                average(INVENTORY),
                COGS,
                INVENTORY,
                prior=(INVENTORY,),
            ),
            *_ratio(
                "receivables_turnover",
                revenue,
                average(TRADE_RECEIVABLES),
                REVENUE,
                TRADE_RECEIVABLES,
                prior=(TRADE_RECEIVABLES,),
            ),
            *_ratio(
                "payables_turnover",
                cogs,
                average(PAYABLES),
                COGS,
                PAYABLES,
                prior=(PAYABLES,),
            ),
            *_ratio("ocf_to_net_income", cfo, earnings, CFO, EARNINGS),
            *_ratio("fcf_to_revenue", c("fcf_ttm"), revenue, *FCF_INPUTS, REVENUE),
            *_ratio(
                "accruals_to_assets",
                earnings - cfo,
                average(ASSETS),
                EARNINGS,
                CFO,
                ASSETS,
                prior=(ASSETS,),
            ),
            *_ratio(
                "eps_ttm_estimated",
                c(PARENT_EARNINGS),
                c("_eps_shares"),
                *EPS_INPUTS,
                universal=True,
            ),
            *_ratio(
                "equity_to_assets", c(EQUITY), c(ASSETS), EQUITY, ASSETS, universal=True
            ),
            *_growth("revenue_growth_yoy", REVENUE, REVENUE),
            *_growth("ebit_growth_yoy", "ebit_ttm", *EBIT_INPUTS),
            *_growth(
                "net_income_parent_growth_yoy",
                PARENT_EARNINGS,
                PARENT_EARNINGS,
                universal=True,
            ),
        )
    )
