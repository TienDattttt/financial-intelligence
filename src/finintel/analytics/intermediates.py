"""Prepare point-in-time accounting amounts from normalized facts.

Verified Sources:
    https://github.com/JerBouma/FinanceToolkit/tree/main/financetoolkit/ratios
    The upstream ratio modules now end in _model.py. Use their separation of
    pure formulas and averaging of balance sheet stocks. Use the local VAS
    formulas for EBT plus interest, cash-adjusted capital, 20% tax on
    nonpositive EBT, and CFO less the magnitude of CAPEX.
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed., McGraw-Hill).
    Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material Accounting
    Misstatements." Contemporary Accounting Research, Vol. 28, Issue 1, pp. 17-82.
    Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
    IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    https://vanban.chinhphu.vn/?docid=178998&lang=vi&pageid=27160
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-99-2025-tt-btc-46529/59658.htm
    docs/layers/LAYER_2_NORMALIZATION.md (L138 SIGNED_EXPENSE convention).
    Gate 0 FHSC evidence and config/metric_definitions.yaml determine the fields.
    TT99 B02-DN line 24 is borrowing costs. Use it for TT99 and TT200 line
    23 for TT200 with separate identifiers and cross-taxonomy guards.
    FPT's audited B02-DN/HN matches the FHSC tax fields: positive current
    tax is an expense and negative deferred tax is a benefit.
    FHSC's vonchusohuu_tong and vonchusohuu agree in all 77 FPT Gate 0
    balance sheets. Layer 2 applies the explicit provider fallback.
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-202-2014-tt-btc-6558.htm
"""

from bisect import bisect_left, bisect_right, insort
import calendar
import math
from datetime import date, timedelta

import polars as pl

from finintel.domain.schemas import _roll_to_next_vn_business_day
from finintel.exceptions import ContractViolationError
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.periods import (
    BRIDGED_TTM_NOTE,
    KNOWN_SCOPES,
    compute_ttm,
    derive_quarters,
)
from finintel.ports.market_prices import (
    align_events_to_market_prices_pit,
    validate_market_prices,
)
from finintel.storage.schemas import canonicalize_financial_facts

ASSETS = "BS_TOTAL_ASSETS"
EQUITY = "BS_TOTAL_EQUITY"
CASH = "BS_CASH_AND_EQUIVALENTS"
CURRENT_ASSETS = "BS_CURRENT_ASSETS"
CURRENT_LIABILITIES = "BS_CURRENT_LIABILITIES"
LIABILITIES = "BS_TOTAL_LIABILITIES"
INVESTMENTS = "BS_SHORT_TERM_INVESTMENTS"
ST_DEBT = "BS_SHORT_TERM_BORROWINGS"
LT_DEBT = "BS_LONG_TERM_BORROWINGS"
BONDS = "BS_CONVERTIBLE_BONDS"
NCI = "BS_MINORITY_INTEREST"
SHARES = "BS_ORDINARY_SHARES_CAPITAL"
TREASURY_SHARES = "BS_TREASURY_SHARES"
TRADE_RECEIVABLES = "BS_TRADE_RECEIVABLES_ST"
RECEIVABLES = "BS_SHORT_TERM_RECEIVABLES"
LT_RECEIVABLES = "BS_LT_RECEIVABLES"
LT_TRADE_RECEIVABLES = "BS_TRADE_RECEIVABLES_LT"
ST_BAD_DEBT_PROVISION = "BS_BAD_DEBT_PROVISION_ST"
LT_BAD_DEBT_PROVISION = "BS_BAD_DEBT_PROVISION_LT"
ST_TRADE_ALLOWANCE = "BS_TRADE_RECEIVABLES_ALLOWANCE_ST"
LT_TRADE_ALLOWANCE = "BS_TRADE_RECEIVABLES_ALLOWANCE_LT"
INVENTORY = "BS_INVENTORIES"
PAYABLES = "BS_TRADE_PAYABLES_ST"
ST_RECEIVABLE_INPUTS = (
    RECEIVABLES,
    TRADE_RECEIVABLES,
    ST_BAD_DEBT_PROVISION,
    ST_TRADE_ALLOWANCE,
)
LT_RECEIVABLE_INPUTS = (
    LT_RECEIVABLES,
    LT_TRADE_RECEIVABLES,
    LT_BAD_DEBT_PROVISION,
    LT_TRADE_ALLOWANCE,
)
RF03_INPUTS = (ASSETS, *ST_RECEIVABLE_INPUTS, *LT_RECEIVABLE_INPUTS)
REVENUE = "IS_NET_REVENUE"
EARNINGS = "IS_NET_INCOME"
PARENT_EARNINGS = "IS_NET_INCOME_PARENT"
EBT = "IS_PROFIT_BEFORE_TAX"
INTEREST = "IS_INTEREST_EXPENSE"
BORROWING_COSTS = "IS_BORROWING_COSTS"
CURRENT_TAX = "IS_CURRENT_INCOME_TAX"
DEFERRED_TAX = "IS_DEFERRED_INCOME_TAX"
COGS = "IS_COST_OF_GOODS_SOLD"
GROSS_PROFIT = "IS_GROSS_PROFIT"
OPERATING_PROFIT = "IS_OPERATING_PROFIT"
EPS = "IS_EPS_BASIC_REPORTED"
CFO = "CF_OPERATING_CASH_FLOW"
CAPEX = "CF_CAPITAL_EXPENDITURE"
DEPRECIATION = "CF_DEPRECIATION_AMORTIZATION"
RETAINED_EARNINGS = "BS_RETAINED_EARNINGS"
TANGIBLE_ASSETS = "BS_TANGIBLE_FIXED_ASSETS"
CIP_LONG_TERM = "BS_CIP_LONG_TERM"
LT_INVESTMENTS = "BS_LT_FINANCIAL_INVESTMENTS"
SELLING_EXPENSES = "IS_SELLING_EXPENSES"
ADMIN_EXPENSES = "IS_ADMINISTRATIVE_EXPENSES"
FINANCIAL_REVENUE = "IS_FINANCIAL_REVENUE"
OTHER_INCOME = "IS_OTHER_INCOME"
OTHER_EXPENSES = "IS_OTHER_EXPENSES"
OTHER_PROFIT = "IS_OTHER_PROFIT"
IP_DISPOSAL = "IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS"
EQUITY_PROCEEDS = "CF_EQUITY_ISSUANCE_PROCEEDS"
NONCASH_ISSUANCE = "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE"
CONTINUING_EARNINGS = "IS_CONTINUING_OPERATIONS_NET_INCOME"
ML_STOCKS = (RETAINED_EARNINGS, LT_INVESTMENTS)
ML_FLOWS = (
    EQUITY_PROCEEDS,
    NONCASH_ISSUANCE,
    CONTINUING_EARNINGS,
)

STOCKS = (
    ASSETS,
    EQUITY,
    CASH,
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    LIABILITIES,
    INVESTMENTS,
    ST_DEBT,
    LT_DEBT,
    BONDS,
    NCI,
    SHARES,
    TREASURY_SHARES,
    TRADE_RECEIVABLES,
    RECEIVABLES,
    LT_RECEIVABLES,
    LT_TRADE_RECEIVABLES,
    ST_BAD_DEBT_PROVISION,
    LT_BAD_DEBT_PROVISION,
    ST_TRADE_ALLOWANCE,
    LT_TRADE_ALLOWANCE,
    INVENTORY,
    PAYABLES,
    TANGIBLE_ASSETS,
    CIP_LONG_TERM,
)
FLOWS = (
    REVENUE,
    EARNINGS,
    PARENT_EARNINGS,
    EBT,
    INTEREST,
    BORROWING_COSTS,
    CURRENT_TAX,
    DEFERRED_TAX,
    COGS,
    GROSS_PROFIT,
    OPERATING_PROFIT,
    CFO,
    CAPEX,
    DEPRECIATION,
    SELLING_EXPENSES,
    ADMIN_EXPENSES,
    FINANCIAL_REVENUE,
    OTHER_INCOME,
    OTHER_EXPENSES,
    OTHER_PROFIT,
    IP_DISPOSAL,
)
FILING_TRACKED_STOCKS = (*RF03_INPUTS, CIP_LONG_TERM, TANGIBLE_ASSETS)
CORE_PROFIT_INPUTS = (GROSS_PROFIT, SELLING_EXPENSES, ADMIN_EXPENSES)
ALIASES = {
    "BS_ST_FINANCIAL_INVESTMENTS": INVESTMENTS,
    "BS_ST_BORROWINGS": ST_DEBT,
    "BS_LT_BORROWINGS": LT_DEBT,
    "BS_ST_RECEIVABLES_TOTAL": RECEIVABLES,
    "BS_INVENTORIES_NET": INVENTORY,
    "IS_NET_INCOME_TOTAL": EARNINGS,
    "IS_CURRENT_TAX_EXPENSE": CURRENT_TAX,
    "IS_DEFERRED_TAX_EXPENSE": DEFERRED_TAX,
    "IS_COGS": COGS,
    "CF_CAPEX_PURCHASE_FIXED_ASSETS": CAPEX,
    "BS_TANGIBLE_FIXED_ASSETS_NET": TANGIBLE_ASSETS,
    "IS_SELLING_EXPENSE": SELLING_EXPENSES,
    "IS_G_AND_A_EXPENSE": ADMIN_EXPENSES,
}
PERIOD = [
    "issuer_id",
    "statement_scope",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "fiscal_year_start_date",
    "period_end_date",
]
SERIES = ["issuer_id", "statement_scope", "period_type"]
MIXED = "MIXED_TAXONOMY"
REVISION_ORDER = ["effective_available_at", "revision_no", "filing_id"]
METRIC = pl.col("metric_id")
CORPORATE = pl.col("accounting_regime") == "CORPORATE"

DEBT_INPUTS = (ST_DEBT, LT_DEBT, BONDS)
CASH_INPUTS = (CASH, INVESTMENTS)
CAPITAL_INPUTS = (*DEBT_INPUTS, EQUITY, CASH)
PARENT_INPUTS = (EQUITY, NCI)
EBIT_INPUTS = (EBT, INTEREST, BORROWING_COSTS)
EBITDA_INPUTS = (*EBIT_INPUTS, DEPRECIATION)
NOPAT_INPUTS = (*EBIT_INPUTS, CURRENT_TAX, DEFERRED_TAX)
FCF_INPUTS = (CFO, CAPEX)
EPS_INPUTS = (PARENT_EARNINGS, EPS, SHARES, TREASURY_SHARES)


def input_has(prefix: str, *names: str) -> pl.Expr:
    """Combine an input's provenance with that of other formula operands."""
    return pl.any_horizontal(
        *(pl.col(f"_{prefix}_{name}").fill_null(False) for name in names)
    )


def average(name: str) -> pl.Expr:
    """Average current and opening STOCK. A missing endpoint yields null."""
    return pl.col(name) / 2.0 + pl.col(f"{name}_prior") / 2.0


def cross_taxonomy(*names: str) -> pl.Expr:
    """Check whether any present year-over-year input lacks comparability."""
    comparable = [
        pl.col(f"_comparable_{name}{suffix}")
        for name in names
        for suffix in ("", "_prior")
    ]
    compatible = pl.all_horizontal(*(expr.fill_null(True) for expr in comparable))
    return (
        (pl.col("taxonomy_version") != pl.col("_prior_taxonomy")) & ~compatible
    ).fill_null(False)


def _visible_facts(
    facts: pl.DataFrame,
    as_of: date,
    stocks: tuple[str, ...],
    *,
    statutory_delay_days: int = 0,
    _precanonicalized: bool = False,
) -> pl.DataFrame:
    if statutory_delay_days < 0:
        raise ContractViolationError("statutory_delay_days must be non-negative.")
    frame = facts if _precanonicalized else canonicalize_financial_facts(facts)
    optional = {
        "source_provider": pl.lit("normalized"),
        "source_filing_ids": pl.concat_list("filing_id"),
        "comparable_across_taxonomy": pl.lit(False),
        "is_filed_late": pl.lit(None, dtype=pl.Boolean),
    }
    stat_avail = (
        pl.col("effective_available_at").dt.offset_by(f"{statutory_delay_days}d")
        if statutory_delay_days > 0
        else pl.col("effective_available_at")
    )
    available = (
        pl.when(pl.col("published_at_basis") == "EXACT_FILING")
        .then(pl.col("published_at"))
        .otherwise(stat_avail)
    )
    return (
        frame.with_columns(
            *(
                value.alias(name)
                for name, value in optional.items()
                if name not in frame.columns
            ),
            METRIC.replace(ALIASES).alias("metric_id"),
            pl.col("fiscal_quarter").cast(pl.Int32),
            (
                (
                    pl.col("period_end_date") - pl.col("period_start_date")
                ).dt.total_days()
                + 1
            )
            .cast(pl.Int32)
            .alias("period_days"),
            available.alias("effective_available_at"),
        )
        .filter(
            (pl.col("effective_available_at") <= as_of)
            & (pl.col("period_end_date") <= as_of)
            & pl.col("statement_scope").is_in(KNOWN_SCOPES)
        )
        .with_columns(
            pl.when(pl.col("period_type") == "ANNUAL")
            .then(None)
            .otherwise(pl.col("fiscal_quarter"))
            .alias("fiscal_quarter"),
            pl.when(METRIC.is_in(stocks))
            .then(pl.lit("STOCK"))
            .when(METRIC == EPS)
            .then(pl.lit("NON_ADDITIVE"))
            .otherwise(pl.lit("FLOW"))
            .alias("value_type"),
            (pl.col("is_filed_late") == 1).alias("is_filed_late"),
            pl.coalesce("source_filing_ids", pl.concat_list("filing_id")).alias(
                "source_filing_ids"
            ),
        )
    )


def _synthesize_missing_selling_expenses(facts: pl.DataFrame) -> pl.DataFrame:
    """Synthesize IS_SELLING_EXPENSES = 0.0 per individual filing before TTM when GP, Admin, and OP (Code 30) exist."""
    if facts.is_empty():
        return facts
    filing_key = [
        "issuer_id",
        "statement_scope",
        "period_type",
        "fiscal_year_start_date",
        "period_start_date",
        "period_end_date",
        "filing_id",
        "source_provider",
        "derivation_method",
    ]
    has_gp = (
        ((METRIC == GROSS_PROFIT) & pl.col("value").is_not_null())
        .any()
        .over(filing_key)
    )
    has_admin = (
        ((METRIC == ADMIN_EXPENSES) & pl.col("value").is_not_null())
        .any()
        .over(filing_key)
    )
    has_selling_row = (METRIC == SELLING_EXPENSES).any().over(filing_key)
    synthesized = (
        facts.filter(
            (METRIC == OPERATING_PROFIT)
            & pl.col("value").is_not_null()
            & has_gp
            & has_admin
            & ~has_selling_row
        )
        .unique(subset=filing_key, keep="last")
        .with_columns(
            pl.lit(SELLING_EXPENSES).alias("metric_id"),
            pl.lit(0.0, dtype=pl.Float64).alias("value"),
        )
    )
    if synthesized.is_empty():
        return facts
    return pl.concat([facts, synthesized], how="vertical_relaxed")


def _period_amounts(
    facts: pl.DataFrame, stocks: tuple[str, ...], flows: tuple[str, ...]
) -> pl.DataFrame:
    facts = _synthesize_missing_selling_expenses(facts)
    reports = facts.filter(
        (pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")
        & pl.col("period_type").is_in(["QUARTER", "ANNUAL"])
    )
    reported = pl.col("derivation_method") == "REPORTED_DIRECTLY"
    # Older Layer 3 files can omit Layer 2's ancillary lineage columns.
    flow = facts.filter(METRIC.is_in(flows)).select(
        pl.col(name) if name in facts.columns else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in FACT_SCHEMA.items()
    )
    quarterly_series = ["issuer_id", "statement_scope", "source_provider", "metric_id"]
    ttm_flow = compute_ttm(flow)
    if "ttm_exact_filing" in ttm_flow.columns:
        ttm_exact_expr = pl.col("ttm_exact_filing").fill_null(False)
    else:
        ttm_flow = ttm_flow.sort([*quarterly_series, "period_end_date"])
        ttm_exact_expr = pl.all_horizontal(
            *(
                (pl.col("published_at_basis") == "EXACT_FILING")
                .shift(lag)
                .over(quarterly_series)
                .fill_null(False)
                for lag in range(4)
            )
        )
    quarterly = ttm_flow.with_columns(ttm_exact_expr.alias("_ttm_exact"))
    anchor_columns = [*PERIOD, *REVISION_ORDER, "taxonomy_version", "derivation_method"]
    derived_q4 = quarterly.filter(
        pl.col("derivation_method") == "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX"
    ).select(anchor_columns)
    anchors = pl.concat(
        [reports.select(anchor_columns), derived_q4], how="vertical_relaxed"
    )
    anchors = (
        anchors.sort([reported, *REVISION_ORDER])
        .group_by(PERIOD)
        .agg(
            pl.col("filing_id").last(),
            pl.col("taxonomy_version").last(),
        )
    )
    warnings = (
        reports.filter(reported)
        .sort(REVISION_ORDER)
        .unique(subset=[*PERIOD, "source_provider", "metric_id"], keep="last")
        .filter(
            pl.col("audit_opinion").is_in(["EXCEPT_FOR", "ADVERSE", "DISCLAIMER"])
            | pl.col("is_filed_late")
        )
        .sort(REVISION_ORDER)
        .group_by(PERIOD)
        .agg(
            pl.col("audit_opinion").last(),
            pl.col("is_filed_late").last(),
            pl.col("filing_id").last().alias("audit_filing_id"),
        )
    )
    data_columns = ["metric_id", "computation_note", "comparable_across_taxonomy"]
    raw = reports.filter(
        METRIC.is_in(stocks)
        | ((pl.col("period_type") == "ANNUAL") & METRIC.is_in((*flows, EPS)))
    )
    # Fiscal-year closing stocks may be published only with the annual
    # report. Use them for matching Q4 rows lacking a quarterly stock fact.
    quarter = (pl.col("period_type") == "QUARTER") & (pl.col("fiscal_quarter") == 4)
    closing_keys = [*PERIOD, "taxonomy_version"]
    closing_stocks = (
        raw.filter((pl.col("period_type") == "ANNUAL") & METRIC.is_in(stocks))
        .with_columns(
            pl.lit("QUARTER").alias("period_type"),
            pl.lit(4, dtype=pl.Int32).alias("fiscal_quarter"),
        )
        .join(anchors.filter(quarter).select(closing_keys), on=closing_keys, how="semi")
        .join(
            raw.filter(quarter).select(*PERIOD, "metric_id"),
            on=[*PERIOD, "metric_id"],
            how="anti",
        )
    )
    raw = pl.concat([raw, closing_stocks]).select(
        *PERIOD,
        *REVISION_ORDER,
        *data_columns,
        "value",
        "source_filing_ids",
        pl.lit(False).alias("_q4"),
        (pl.col("published_at_basis") == "EXACT_FILING").alias("_exact"),
    )
    amounts = (
        pl.concat(
            [
                raw,
                quarterly.select(
                    *PERIOD,
                    pl.coalesce("ttm_available_at", "effective_available_at").alias(
                        "effective_available_at"
                    ),
                    "revision_no",
                    "filing_id",
                    *data_columns,
                    pl.col("ttm_value").alias("value"),
                    pl.col("ttm_source_filing_ids").alias("source_filing_ids"),
                    pl.col("ttm_contains_derived_q4").alias("_q4"),
                    pl.col("_ttm_exact").alias("_exact"),
                ),
            ],
            how="vertical_relaxed",
        )
        .sort(REVISION_ORDER)
        .unique(subset=[*PERIOD, "metric_id"], keep="last")
    )
    wide = amounts.group_by(PERIOD).agg(
        *(
            expression.filter(METRIC == name).last().alias(f"{prefix}{name}")
            for name in (*stocks, *flows, EPS)
            for prefix, expression in (
                ("", pl.col("value")),
                ("_mixed_", pl.col("computation_note") == MIXED),
                ("_derived_", pl.col("_q4")),
                ("_comparable_", pl.col("comparable_across_taxonomy").fill_null(False)),
            )
        ),
        pl.col("effective_available_at").max().alias("_amounts_available_at"),
        (
            pl.col("value").is_not_null().any()
            & pl.col("_exact").filter(pl.col("value").is_not_null()).all()
        )
        .fill_null(False)
        .alias("_period_exact_filing"),
        *(
            pl.col("filing_id").filter(METRIC == name).last().alias(f"_filing_{name}")
            for name in FILING_TRACKED_STOCKS
        ),
        (
            pl.col("computation_note").filter(METRIC == BORROWING_COSTS).last()
            == BRIDGED_TTM_NOTE
        )
        .fill_null(False)
        .alias("_bridged_debt_cost"),
        pl.col("source_filing_ids")
        .explode()
        .drop_nulls()
        .unique()
        .sort()
        .alias("_period_filing_ids"),
    )
    return anchors.join(wide, on=PERIOD, how="left", nulls_equal=True).join(
        warnings, on=PERIOD, how="left", nulls_equal=True
    )


def _aggregates(frame: pl.DataFrame) -> pl.DataFrame:
    """Apply the VAS intermediate formulas below to the named metric IDs.

    Add the magnitude of the regime-specific borrowing cost to EBT.
    For positive EBT, sum both signed tax lines before dividing and clipping.
    For nonpositive EBT, ETR is 20%. Subtract the reported CAPEX magnitude
    from CFO. Treat null treasury shares as zero for par-based share count
    (B-06) with fallback to ``shares_outstanding_market`` and explicit
    ``shares_source`` provenance, while preserving explicit-zero treasury
    semantics for ``_eps_shares`` / ``eps_estimate_basis``.
    Parent equity is consolidated BS_TOTAL_EQUITY less BS_MINORITY_INTEREST,
    or BS_TOTAL_EQUITY directly when the statement is separate.
    Preserve SIGNED_EXPENSE sign on IS_SELLING_EXPENSES and IS_ADMINISTRATIVE_EXPENSES.
    """
    c = pl.col
    debt = pl.when(
        pl.any_horizontal(*(c(name).is_not_null() for name in DEBT_INPUTS))
    ).then(c(ST_DEBT).fill_null(0) + c(LT_DEBT).fill_null(0) + c(BONDS).fill_null(0))
    ebt = c(EBT)
    tax = c(CURRENT_TAX) + c(DEFERRED_TAX)
    annual = c("period_type") == "ANNUAL"
    debt_cost = (
        pl.when(c("taxonomy_version") == "VAS_TT99_2025")
        .then(
            pl.when(input_has("mixed", BORROWING_COSTS))
            .then(None)
            .otherwise(
                pl.coalesce(
                    c(BORROWING_COSTS),
                    pl.when(~input_has("mixed", INTEREST)).then(c(INTEREST)),
                )
            )
        )
        .otherwise(c(INTEREST))
        .abs()
    )
    tax_inputs = (EBT, CURRENT_TAX, DEFERRED_TAX)
    implied_shares = c(PARENT_EARNINGS) / c(EPS)
    par_shares = pl.when(
        (c(SHARES) > 0) & (c(TREASURY_SHARES).fill_null(0.0) == 0.0)
    ).then(c(SHARES) / 10000.0)
    explicit_zero_treasury_shares = pl.when(
        (c(SHARES) > 0) & (c(TREASURY_SHARES) == 0)
    ).then(c(SHARES) / 10000.0)
    market_shares = (
        pl.when(
            c("shares_outstanding_market").is_finite()
            & (c("shares_outstanding_market") > 0)
        )
        .then(c("shares_outstanding_market").cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        if "shares_outstanding_market" in frame.columns
        else pl.lit(None, dtype=pl.Float64)
    )
    result = frame.with_columns(
        pl.when(CORPORATE & ~input_has("mixed", *DEBT_INPUTS))
        .then(debt)
        .alias("total_debt"),
        pl.when(c("statement_scope") == "CONSOLIDATED")
        .then(c(EQUITY) - c(NCI))
        .otherwise(c(EQUITY))
        .alias("parent_equity"),
        pl.when(CORPORATE)
        .then(c(CASH) + c(INVESTMENTS).fill_null(0))
        .alias("total_cash_and_st_investments"),
        pl.when(CORPORATE)
        .then(c(CURRENT_ASSETS) - c(CURRENT_LIABILITIES))
        .alias("working_capital"),
        debt_cost.alias("_borrowing_cost_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", INTEREST, BORROWING_COSTS))
        .then(debt_cost)
        .alias("borrowing_cost_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", REVENUE))
        .then(c(REVENUE))
        .alias("net_revenue_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", GROSS_PROFIT))
        .then(c(GROSS_PROFIT))
        .alias("gross_profit_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", COGS))
        .then(c(COGS).abs())
        .alias("cogs_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", CFO)).then(c(CFO)).alias("cfo_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", *CORE_PROFIT_INPUTS))
        .then(c(GROSS_PROFIT) - c(SELLING_EXPENSES) - c(ADMIN_EXPENSES))
        .alias("core_operating_profit_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", *EBIT_INPUTS))
        .then(ebt + debt_cost)
        .alias("ebit_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", *tax_inputs) & (ebt > 0))
        .then((tax / ebt).clip(0.0, 1.0))
        .when(CORPORATE & ~input_has("mixed", EBT) & (ebt <= 0))
        .then(0.20)
        .alias("effective_tax_rate"),
        pl.when(CORPORATE & ~input_has("mixed", *FCF_INPUTS))
        .then(c(CFO) - c(CAPEX).abs())
        .alias("fcf_ttm"),
        pl.coalesce(par_shares, market_shares).alias("estimated_shares_outstanding"),
        pl.when(par_shares.is_not_null() & c(TREASURY_SHARES).is_not_null())
        .then(pl.lit("BS_ORDINARY_SHARES_EXPLICIT_ZERO_TREASURY"))
        .when(par_shares.is_not_null() & c(TREASURY_SHARES).is_null())
        .then(pl.lit("BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO"))
        .when(market_shares.is_not_null())
        .then(pl.lit("SHARES_OUTSTANDING_MARKET"))
        .otherwise(pl.lit("MISSING_SHARES"))
        .alias("shares_source"),
        pl.when(annual & (implied_shares > 0) & implied_shares.is_finite())
        .then(implied_shares)
        .alias("implied_weighted_shares"),
        pl.when(annual).then(c(EPS)).alias("eps_basic_reported"),
    )
    return result.with_columns(
        pl.coalesce("implied_weighted_shares", explicit_zero_treasury_shares).alias(
            "_eps_shares"
        ),
        pl.when(c("implied_weighted_shares").is_not_null())
        .then(pl.lit("IMPLIED_ANNUAL_WEIGHTED"))
        .when(explicit_zero_treasury_shares.is_not_null())
        .then(pl.lit("NO_TREASURY_AT_PERIOD_END"))
        .alias("eps_estimate_basis"),
        (c("total_debt") - c("total_cash_and_st_investments")).alias("net_debt"),
        (c("total_debt") + c(EQUITY) - c(CASH)).alias("invested_capital"),
        (c("ebit_ttm") + c(DEPRECIATION).abs()).alias("ebitda_ttm"),
        (c("ebit_ttm") * (1 - c("effective_tax_rate"))).alias("nopat_ttm"),
        _intermediate_flag("ebit_ttm", *EBIT_INPUTS).alias("ebit_flag"),
        _intermediate_flag("effective_tax_rate", *tax_inputs).alias(
            "effective_tax_rate_flag"
        ),
        _intermediate_flag("fcf_ttm", *FCF_INPUTS).alias("fcf_flag"),
        _intermediate_flag("core_operating_profit_ttm", *CORE_PROFIT_INPUTS).alias(
            "core_operating_profit_flag"
        ),
    ).with_columns(
        pl.when(~CORPORATE)
        .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
        .when(input_has("mixed", *EBITDA_INPUTS))
        .then(pl.lit(MIXED))
        .when(c("ebit_ttm").is_null())
        .then(pl.lit("MISSING_INPUT"))
        .when(c(DEPRECIATION).is_null())
        .then(pl.lit("MISSING_DEPRECIATION_DIRECT_CF"))
        .otherwise(_intermediate_flag("ebitda_ttm", *EBITDA_INPUTS))
        .alias("ebitda_flag"),
        _intermediate_flag("nopat_ttm", *NOPAT_INPUTS).alias("nopat_flag"),
    )


def _intermediate_flag(value: str, *inputs: str) -> pl.Expr:
    amount = pl.col(value)
    return (
        pl.when(~CORPORATE)
        .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
        .when(amount.is_null() & input_has("mixed", *inputs))
        .then(pl.lit(MIXED))
        .when(amount.is_null())
        .then(pl.lit("MISSING_INPUT"))
        .when(~amount.is_finite())
        .then(pl.lit("NUMERIC_OVERFLOW"))
        .when(input_has("derived", *inputs))
        .then(pl.lit("DERIVED_Q4_INPUT"))
        .otherwise(pl.lit("VALID"))
    )


def combine_filing_ids(*columns: pl.Expr) -> pl.Expr:
    """Combine selected facts' filing lineage and omit missing lists and IDs."""
    empty = pl.lit([], dtype=pl.List(pl.String))
    return (
        pl.concat_list(*(column.fill_null(empty) for column in columns))
        .list.drop_nulls()
        .list.unique()
        .list.sort()
    )


def with_prior_year(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    """Match the same fiscal slot from the previous year in the same scope."""
    keys = [*SERIES, "fiscal_year", "fiscal_quarter"]
    end = pl.col("period_end_date")
    prior = frame.select(
        *SERIES,
        (pl.col("fiscal_year") + 1).alias("fiscal_year"),
        "fiscal_quarter",
        end.alias("_join_prior_end"),
        *columns,
    ).rename({name: f"{name}_prior" for name in columns})
    joined = frame.join(prior, on=keys, how="left", nulls_equal=True, validate="m:1")
    matches = (end - pl.col("_join_prior_end")).dt.total_days().is_between(350, 380)
    return joined.with_columns(
        pl.when(matches).then(pl.col(name)).alias(name)
        for name in ["_join_prior_end", *(f"{name}_prior" for name in columns)]
    ).rename({"_join_prior_end": "_prior_end"})


def _shift_quarter_end(period_end: date, quarters_back: int) -> date:
    """Shift a fiscal quarter-end date back by ``quarters_back`` quarters, aligned to month-end."""
    if quarters_back == 0:
        return period_end
    total_months = (period_end.year * 12 + (period_end.month - 1)) - 3 * quarters_back
    target_year, zero_month = divmod(total_months, 12)
    target_month = zero_month + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, last_day)


def _quarter_statutory_deadline(
    q_end: date,
    fiscal_quarter: int,
    *,
    is_reviewed: bool = False,
) -> date:
    """Circular 96/2020/TT-BTC quarterly outer ceiling rolled to Vietnam business days."""
    days = 60 if fiscal_quarter == 2 else (45 if is_reviewed else 30)
    try:
        return _roll_to_next_vn_business_day(q_end + timedelta(days=days))
    except OverflowError:
        return q_end + timedelta(days=days)


def _compute_overdue_or_late_count_4q(
    visible_facts: pl.DataFrame,
    frame: pl.DataFrame,
    *,
    statutory_delay_days: int = 0,
) -> pl.DataFrame:
    """Count trailing-4Q overdue or late filings at each row's PIT ``a_t`` with 0% lookahead (H-06).

    For each corporate quarter ``t`` available at ``a_t = _period_available_at``, inspects
    fiscal slots ``{q_idx - 3, q_idx - 2, q_idx - 1, q_idx}`` at or after the issuer's first
    observed quarter ``<= a_t``. Whenever ``statutory_deadline(q_s) < a_t``, adds 1 if no
    ``REPORTED_DIRECTLY`` quarterly filing became available on time (``<= statutory_deadline``).
    Captures both late filings and missing/evaded quarters with 0% future leakage.
    """
    if frame.is_empty():
        return frame.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("overdue_or_late_count_4q")
        )
    slots_map: dict[
        tuple[str, str],
        dict[int, tuple[date, date | None, date | None, date | None, date]],
    ] = {}
    q_facts = visible_facts.filter(
        (pl.col("period_type") == "QUARTER") & pl.col("fiscal_quarter").is_between(1, 4)
    )
    if not q_facts.is_empty():
        q_agg = (
            q_facts.with_columns(
                (
                    pl.col("fiscal_year").cast(pl.Int32) * 4
                    + pl.col("fiscal_quarter").cast(pl.Int32)
                ).alias("_q_idx")
            )
            .group_by(["issuer_id", "statement_scope", "_q_idx"])
            .agg(
                pl.col("effective_available_at").min().alias("_any_avail"),
                pl.col("effective_available_at")
                .filter(
                    (pl.col("derivation_method") == "REPORTED_DIRECTLY")
                    & (pl.col("published_at_basis") == "EXACT_FILING")
                    & ~(pl.col("is_filed_late").fill_null(False))
                )
                .min()
                .alias("_exact_ontime_avail"),
                pl.col("effective_available_at")
                .filter(
                    (pl.col("derivation_method") == "REPORTED_DIRECTLY")
                    & (pl.col("published_at_basis") == "STATUTORY_LAG")
                    & ~(pl.col("is_filed_late").fill_null(False))
                )
                .min()
                .alias("_stat_avail"),
                pl.col("effective_available_at")
                .filter(
                    (pl.col("derivation_method") == "REPORTED_DIRECTLY")
                    & (pl.col("audit_status") == "REVIEWED")
                )
                .min()
                .alias("_reviewed_avail"),
                pl.col("period_end_date").first().alias("_slot_end"),
            )
        )
        for row in q_agg.iter_rows(named=True):
            key = (str(row["issuer_id"]), str(row["statement_scope"]))
            slots_map.setdefault(key, {})[int(row["_q_idx"])] = (
                row["_any_avail"],
                row["_exact_ontime_avail"],
                row["_stat_avail"],
                row["_reviewed_avail"],
                row["_slot_end"],
            )

    counts: list[float | None] = []
    for row in frame.select(
        "issuer_id",
        "statement_scope",
        "accounting_regime",
        "period_type",
        "fiscal_year",
        "fiscal_quarter",
        "period_end_date",
        "_period_available_at",
    ).iter_rows(named=True):
        a_t = row["_period_available_at"]
        fq = row["fiscal_quarter"]
        if (
            row["accounting_regime"] != "CORPORATE"
            or row["period_type"] != "QUARTER"
            or fq is None
            or a_t is None
        ):
            counts.append(None)
            continue
        q_idx = int(row["fiscal_year"]) * 4 + int(fq)
        t_end: date = row["period_end_date"]
        slots = slots_map.get((str(row["issuer_id"]), str(row["statement_scope"])), {})
        first_qi = min(
            (qi for qi, vals in slots.items() if vals[0] <= a_t),
            default=q_idx,
        )
        overdue = 0
        for lag in range(4):
            q_s = q_idx - lag
            if q_s < first_qi:
                continue
            q_num_s = ((q_s - 1) % 4) + 1
            slot = slots.get(q_s)
            q_end_s = slot[4] if slot is not None else _shift_quarter_end(t_end, lag)
            is_rev_s = slot is not None and slot[3] is not None and slot[3] <= a_t
            deadline_s = _quarter_statutory_deadline(
                q_end_s, q_num_s, is_reviewed=is_rev_s
            )
            if deadline_s >= a_t:
                continue
            exact_ontime = slot[1] if slot is not None else None
            stat_ontime = slot[2] if slot is not None else None
            arrived_on_time = (
                exact_ontime is not None and exact_ontime <= deadline_s
            ) or (stat_ontime is not None and stat_ontime <= deadline_s)
            if not arrived_on_time:
                overdue += 1
        counts.append(float(overdue))

    return frame.with_columns(
        pl.Series("overdue_or_late_count_4q", counts, dtype=pl.Float64)
    )


def _compute_consecutive_loss_quarters(
    visible_facts: pl.DataFrame,
    frame: pl.DataFrame,
) -> pl.DataFrame:
    """Count consecutive discrete-quarter net losses up to ``t`` without future lookahead."""
    if frame.is_empty():
        return frame.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("consecutive_loss_quarters")
        )
    ni_facts = visible_facts.filter(METRIC == EARNINGS).select(
        pl.col(name)
        if name in visible_facts.columns
        else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in FACT_SCHEMA.items()
    )
    ni_map: dict[tuple[str, str, int], tuple[float, date]] = {}
    if not ni_facts.is_empty():
        discrete_ni = (
            derive_quarters(ni_facts)
            .filter(
                (pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")
                & pl.col("value").is_not_null()
            )
            .sort(REVISION_ORDER)
            .unique(subset=PERIOD, keep="last")
        )
        for row in discrete_ni.select(
            "issuer_id",
            "statement_scope",
            "fiscal_year",
            "fiscal_quarter",
            "value",
            "effective_available_at",
        ).iter_rows(named=True):
            val = float(row["value"])
            if not math.isfinite(val):
                continue
            q_idx = int(row["fiscal_year"]) * 4 + int(row["fiscal_quarter"])
            ni_map[(str(row["issuer_id"]), str(row["statement_scope"]), q_idx)] = (
                val,
                row["effective_available_at"],
            )

    streaks: list[float | None] = []
    for row in frame.select(
        "issuer_id",
        "statement_scope",
        "accounting_regime",
        "period_type",
        "fiscal_year",
        "fiscal_quarter",
        "_period_available_at",
    ).iter_rows(named=True):
        a_t = row["_period_available_at"]
        fq = row["fiscal_quarter"]
        if (
            row["accounting_regime"] != "CORPORATE"
            or row["period_type"] != "QUARTER"
            or fq is None
            or a_t is None
        ):
            streaks.append(None)
            continue
        iss = str(row["issuer_id"])
        scope = str(row["statement_scope"])
        q_idx = int(row["fiscal_year"]) * 4 + int(fq)
        cur = ni_map.get((iss, scope, q_idx))
        if cur is None or cur[1] > a_t:
            streaks.append(None)
            continue
        if cur[0] >= 0.0:
            streaks.append(0.0)
            continue
        streak = 1
        prev_q = q_idx - 1
        while True:
            prev = ni_map.get((iss, scope, prev_q))
            if prev is None or prev[1] > a_t or prev[0] >= 0.0:
                break
            streak += 1
            prev_q -= 1
        streaks.append(float(streak))

    return frame.with_columns(
        pl.Series("consecutive_loss_quarters", streaks, dtype=pl.Float64)
    )


def _compute_pit_cross_sectional_asset_size_pct(
    frame: pl.DataFrame,
    valid_asset: pl.Expr,
    asset_group: list[str],
) -> pl.Series:
    """Compute cross-sectional asset size percentile strictly over peers with ``_period_available_at <= row._period_available_at`` (P2-10)."""
    if frame.is_empty():
        return pl.Series("cross_sectional_asset_size_pct", [], dtype=pl.Float64)

    staged = frame.with_row_index("_row_idx").select(
        "_row_idx",
        *asset_group,
        "_period_available_at",
        valid_asset.alias("_valid_asset"),
    )
    out: list[float | None] = [None] * frame.height
    grouped = staged.filter(
        pl.col("_valid_asset").is_not_null()
        & pl.col("_period_available_at").is_not_null()
    ).partition_by(asset_group, maintain_order=False)
    for sub in grouped:
        sub_sorted = sub.sort(["_period_available_at", "_valid_asset", "_row_idx"])
        dates_col = sub_sorted["_period_available_at"].to_list()
        vals_col = sub_sorted["_valid_asset"].to_list()
        idxs_col = sub_sorted["_row_idx"].to_list()
        sorted_peers: list[float] = []
        n = len(dates_col)
        pos = 0
        while pos < n:
            cur_date = dates_col[pos]
            end = pos
            while end < n and dates_col[end] == cur_date:
                insort(sorted_peers, float(vals_col[end]))
                end += 1
            count = len(sorted_peers)
            for k in range(pos, end):
                r_idx = int(idxs_col[k])
                if count == 1:
                    out[r_idx] = 50.0
                else:
                    v = float(vals_col[k])
                    lo = bisect_left(sorted_peers, v)
                    hi = bisect_right(sorted_peers, v)
                    avg_rank = 1.0 + 0.5 * (lo + hi - 1)
                    out[r_idx] = 100.0 * (avg_rank - 1.0) / (count - 1.0)
            pos = end
    return pl.Series("cross_sectional_asset_size_pct", out, dtype=pl.Float64)


def _attach_ml_candidate_features(
    frame: pl.DataFrame,
    visible_facts: pl.DataFrame,
    *,
    statutory_delay_days: int = 0,
) -> pl.DataFrame:
    """Attach leak-free ML-05 candidate features onto corporate periods."""
    with_counts = _compute_consecutive_loss_quarters(
        visible_facts,
        _compute_overdue_or_late_count_4q(
            visible_facts, frame, statutory_delay_days=statutory_delay_days
        ),
    )

    c = pl.col
    asset_group = [
        "accounting_regime",
        "statement_scope",
        "period_type",
        "period_end_date",
        "fiscal_year",
        "fiscal_quarter",
    ]
    valid_asset = (
        pl.when(
            CORPORATE
            & ~input_has("mixed", ASSETS)
            & c(ASSETS).is_finite()
            & (c(ASSETS) > 0)
        )
        .then(c(ASSETS).cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    asset_pct_series = _compute_pit_cross_sectional_asset_size_pct(
        with_counts, valid_asset, asset_group
    )

    avg_assets = average(ASSETS)
    ml_roa = (
        pl.when(
            CORPORATE
            & ~input_has("mixed", EARNINGS, ASSETS, f"{ASSETS}_prior")
            & ~cross_taxonomy(ASSETS)
            & c("_prior_end").is_not_null()
            & c(EARNINGS).is_finite()
            & avg_assets.is_finite()
            & (avg_assets > 0)
        )
        .then((c(EARNINGS) / avg_assets).cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("_ml_roa")
    )
    ml_lta = (
        pl.when(
            CORPORATE
            & ~input_has("mixed", LIABILITIES, ASSETS)
            & c(LIABILITIES).is_finite()
            & c(ASSETS).is_finite()
            & (c(ASSETS) > 0)
        )
        .then((c(LIABILITIES) / c(ASSETS)).cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("_ml_lta")
    )
    ml_cr = (
        pl.when(
            CORPORATE
            & ~input_has("mixed", CURRENT_ASSETS, CURRENT_LIABILITIES)
            & c(CURRENT_ASSETS).is_finite()
            & c(CURRENT_LIABILITIES).is_finite()
            & (c(CURRENT_LIABILITIES) > 0)
        )
        .then((c(CURRENT_ASSETS) / c(CURRENT_LIABILITIES)).cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("_ml_cr")
    )
    staged = with_counts.with_columns(asset_pct_series, ml_roa, ml_lta, ml_cr)

    prior_keys = [*SERIES, "fiscal_year", "fiscal_quarter"]
    prior_ratios = staged.select(
        *SERIES,
        (c("fiscal_year") + 1).alias("fiscal_year"),
        "fiscal_quarter",
        c("period_end_date").alias("_ml_prior_end"),
        c("_ml_roa").alias("_ml_roa_prior"),
        c("_ml_lta").alias("_ml_lta_prior"),
        c("_ml_cr").alias("_ml_cr_prior"),
    )
    joined = staged.join(
        prior_ratios, on=prior_keys, how="left", nulls_equal=True, validate="m:1"
    )
    prior_match = (
        (c("period_end_date") - c("_ml_prior_end"))
        .dt.total_days()
        .is_between(350, 380)
        .fill_null(False)
    )
    return joined.with_columns(
        pl.when(
            prior_match
            & ~cross_taxonomy(EARNINGS, ASSETS)
            & ~input_has(
                "mixed", EARNINGS, ASSETS, f"{EARNINGS}_prior", f"{ASSETS}_prior"
            )
        )
        .then(c("_ml_roa") - c("_ml_roa_prior"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .cast(pl.Float64)
        .alias("delta_roa_4q"),
        pl.when(
            prior_match
            & ~cross_taxonomy(LIABILITIES, ASSETS)
            & ~input_has(
                "mixed", LIABILITIES, ASSETS, f"{LIABILITIES}_prior", f"{ASSETS}_prior"
            )
        )
        .then(c("_ml_lta") - c("_ml_lta_prior"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .cast(pl.Float64)
        .alias("delta_liabilities_to_assets_4q"),
        pl.when(
            prior_match
            & ~cross_taxonomy(CURRENT_ASSETS, CURRENT_LIABILITIES)
            & ~input_has(
                "mixed",
                CURRENT_ASSETS,
                CURRENT_LIABILITIES,
                f"{CURRENT_ASSETS}_prior",
                f"{CURRENT_LIABILITIES}_prior",
            )
        )
        .then(c("_ml_cr") - c("_ml_cr_prior"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .cast(pl.Float64)
        .alias("delta_current_ratio_4q"),
    ).drop(
        "_ml_roa",
        "_ml_lta",
        "_ml_cr",
        "_ml_prior_end",
        "_ml_roa_prior",
        "_ml_lta_prior",
        "_ml_cr_prior",
    )


def _attach_market_shares_pit(
    periods: pl.DataFrame,
    market_prices: pl.DataFrame,
    *,
    as_of: date,
) -> pl.DataFrame:
    """Attach PIT-aligned ``shares_outstanding_market`` from Port 3 EOD prices."""
    validated_prices = validate_market_prices(market_prices).filter(
        pl.col("trade_date") <= as_of
    )
    if periods.is_empty() or validated_prices.is_empty():
        return periods.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("shares_outstanding_market")
        )
    alignable = periods.filter(pl.col("_amounts_available_at").is_not_null()).select(
        *PERIOD, "_amounts_available_at"
    )
    if alignable.is_empty():
        return periods.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("shares_outstanding_market")
        )
    aligned = align_events_to_market_prices_pit(
        alignable,
        validated_prices,
        event_date_col="_amounts_available_at",
    ).select(*PERIOD, "shares_outstanding_market")
    return periods.join(
        aligned, on=PERIOD, how="left", nulls_equal=True, validate="1:1"
    )


def prepare_periods(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    include_ml_inputs: bool = False,
    market_prices: pl.DataFrame | None = None,
    statutory_delay_days: int = 0,
    _precanonicalized: bool = False,
) -> pl.DataFrame:
    """Build annual and TTM inputs from reports available by the PIT cut-off.

    Annual FLOW is reported directly. Quarterly FLOW sums four consecutive
    discrete quarters through Layer 2. Match opening stocks by fiscal slot.
    Unknown scopes cannot establish that two reports are comparable.
    """
    required = ("issuer_id", "ticker", "accounting_regime")
    if set(required) - set(issuers.columns):
        raise ContractViolationError(
            "Analytics requires issuer ID, ticker and accounting regime."
        )
    classification_visible = (
        pl.col("industry_snapshot_date").cast(pl.Date) <= as_of
        if "industry_snapshot_date" in issuers.columns
        else pl.lit(True)
    )
    identity = issuers.select(
        *(pl.col(name).cast(pl.String) for name in required),
        *(
            pl.when(classification_visible)
            .then(pl.col(name).cast(pl.String))
            .alias(name)
            if name in issuers.columns
            else pl.lit(None, dtype=pl.String).alias(name)
            for name in ("icb_code_level2", "icb_code_level3")
        ),
    )
    if (
        identity["issuer_id"].n_unique() != identity.height
        or identity.select(
            pl.any_horizontal(
                *(
                    pl.col(name).is_null() | (pl.col(name).str.strip_chars() == "")
                    for name in required
                ),
                ~pl.col("accounting_regime").is_in(
                    ["CORPORATE", "BANK", "SECURITIES", "INSURANCE"]
                ),
            ).any()
        ).item()
    ):
        raise ContractViolationError(
            "Analytics requires unique issuer IDs and valid issuer metadata."
        )
    stocks = (*STOCKS, *ML_STOCKS) if include_ml_inputs else STOCKS
    flows = (*FLOWS, *ML_FLOWS) if include_ml_inputs else FLOWS
    amounts = (*stocks, *flows, EPS)
    visible_facts = _visible_facts(
        facts,
        as_of,
        stocks,
        statutory_delay_days=statutory_delay_days,
        _precanonicalized=_precanonicalized,
    )
    periods = (
        _period_amounts(
            visible_facts,
            stocks,
            flows,
        )
        .join(identity, on="issuer_id", how="left")
        .with_columns(pl.col("_amounts_available_at").alias("_period_available_at"))
    )
    if periods["accounting_regime"].null_count():
        raise ContractViolationError(
            "Analytics facts contain an issuer absent from issuers.parquet."
        )
    keys = [*SERIES, "fiscal_year", "fiscal_quarter"]
    if periods.select(pl.struct(keys).is_duplicated().any()).item():
        raise ContractViolationError(
            "Multiple period ends occupy the same fiscal slot."
        )
    if market_prices is not None:
        periods = _attach_market_shares_pit(periods, market_prices, as_of=as_of)
    result = with_prior_year(
        _aggregates(periods),
        "taxonomy_version",
        *amounts,
        "_period_filing_ids",
        "_period_available_at",
        "_period_exact_filing",
        "parent_equity",
        "invested_capital",
        "ebit_ttm",
        *(f"_filing_{name}" for name in (ASSETS, CIP_LONG_TERM, TANGIBLE_ASSETS)),
        *(
            f"_{prefix}_{name}"
            for prefix in ("comparable", "mixed", "derived")
            for name in amounts
        ),
    )
    assembled = result.with_columns(
        pl.when(pl.col("_period_available_at").is_not_null())
        .then(pl.max_horizontal("_period_available_at", "_period_available_at_prior"))
        .otherwise(None)
        .alias("_period_available_at"),
        (
            pl.col("_period_exact_filing").fill_null(False)
            & pl.when(pl.col("_prior_end").is_not_null())
            .then(
                (
                    pl.col("_period_available_at_prior")
                    <= pl.col("_amounts_available_at")
                ).fill_null(False)
            )
            .otherwise(pl.lit(True))
        ).alias("_exact_filing_basis"),
        combine_filing_ids(
            pl.col("_period_filing_ids"), pl.col("_period_filing_ids_prior")
        ).alias("source_filing_ids"),
    ).rename({"taxonomy_version_prior": "_prior_taxonomy"})
    if include_ml_inputs:
        assembled = _attach_ml_candidate_features(
            assembled,
            visible_facts,
            statutory_delay_days=statutory_delay_days,
        )
    return assembled.sort(PERIOD)
