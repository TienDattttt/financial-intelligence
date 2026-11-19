"""Explain consolidated ROE with a five-factor and a six-factor DuPont chain.

Verified Sources:
    https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/models/dupont_model.py
    https://www.jeroenbouma.com/projects/financetoolkit/docs/models#extended-dupont-analysis
    The extended model telescopes net income / average equity. Layer 4 uses
    average assets and equity consistently and adds parent profit share and
    parent equity to reconcile the parent-shareholder return.
"""

import polars as pl

from finintel.analytics.intermediates import (
    ASSETS,
    CORPORATE,
    EARNINGS,
    EBT,
    EBIT_INPUTS,
    EQUITY,
    PARENT_EARNINGS,
    average,
    input_has,
)


def add_dupont(ratios: pl.DataFrame) -> pl.DataFrame:
    """Add DuPont factors using IS_NET_INCOME, IS_PROFIT_BEFORE_TAX, EBIT,
    IS_NET_REVENUE, average BS_TOTAL_ASSETS and both equity definitions.

    Total ROE = (NI/EBT)(EBT/EBIT)(EBIT/sales)(sales/avg assets)(avg assets/avg equity).
    Parent ROE substitutes avg parent equity and multiplies by parent NI/NI.
    Missing ratios, nonpositive EBT or EBIT, or invalid equity stop the
    corresponding chain. Zero total net income prevents the parent chain.
    """
    c = pl.col
    ebt, ebit = c(EBT), c("ebit_ttm")
    earnings, parent_earnings = c(EARNINGS), c(PARENT_EARNINGS)
    average_assets = average(ASSETS)
    tax_burden, interest_burden = earnings / ebt, ebt / ebit
    total_multiplier = average_assets / average(EQUITY)
    parent_multiplier = average_assets / average("parent_equity")
    parent_share = parent_earnings / earnings
    valid_total = (
        CORPORATE
        & c("statement_scope").is_in(["CONSOLIDATED", "SEPARATE"])
        & (ebt > 0)
        & (ebit > 0)
        & ~input_has("mixed", *EBIT_INPUTS)
        & pl.all_horizontal(
            c("roe_total").is_not_null(),
            c("ebit_margin").is_not_null(),
            c("asset_turnover").is_not_null(),
            tax_burden.is_finite(),
            interest_burden.is_finite(),
            total_multiplier.is_finite(),
        )
    ).fill_null(False)
    valid_parent = (
        valid_total
        & c("roe_parent").is_not_null()
        & (earnings != 0)
        & parent_share.is_finite()
        & parent_multiplier.is_finite()
    ).fill_null(False)
    return ratios.with_columns(
        pl.when(valid_total).then(tax_burden).alias("tax_burden"),
        pl.when(valid_total).then(interest_burden).alias("interest_burden"),
        pl.when(valid_total).then(total_multiplier).alias("equity_multiplier_total"),
        pl.when(valid_parent).then(parent_share).alias("parent_profit_share"),
        pl.when(valid_parent).then(parent_multiplier).alias("equity_multiplier_parent"),
        valid_parent.alias("dupont_valid_flag"),
    )
