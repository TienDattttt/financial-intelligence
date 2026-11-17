"""Prepare point-in-time accounting amounts from normalized facts.

Verified Sources:
    https://github.com/JerBouma/FinanceToolkit/tree/main/financetoolkit/ratios
    The upstream ratio modules now end in _model.py. Use their separation of
    pure formulas and averaging of balance sheet stocks. Use the local VAS
    formulas for EBT plus interest, cash-adjusted capital, 20% tax on
    nonpositive EBT, and CFO less the magnitude of CAPEX.
    https://vanban.chinhphu.vn/?docid=178998&lang=vi&pageid=27160
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-99-2025-tt-btc-46529/59658.htm
    Gate 0 FHSC evidence and config/metric_definitions.yaml determine the fields.
    TT99 B02-DN line 24 is borrowing costs. Use it for TT99 and TT200 line
    23 for TT200 with separate identifiers and cross-taxonomy guards.
    FPT's audited B02-DN/HN matches the FHSC tax fields: positive current
    tax is an expense and negative deferred tax is a benefit.
    FHSC's vonchusohuu_tong and vonchusohuu agree in all 77 FPT Gate 0
    balance sheets. Layer 2 applies the explicit provider fallback.
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-202-2014-tt-btc-6558.htm
"""

from datetime import date

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.periods import KNOWN_SCOPES, compute_ttm
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
LT_INVESTMENTS = "BS_LT_FINANCIAL_INVESTMENTS"
SELLING_EXPENSES = "IS_SELLING_EXPENSES"
ADMIN_EXPENSES = "IS_ADMINISTRATIVE_EXPENSES"
EQUITY_PROCEEDS = "CF_EQUITY_ISSUANCE_PROCEEDS"
NONCASH_ISSUANCE = "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE"
CONTINUING_EARNINGS = "IS_CONTINUING_OPERATIONS_NET_INCOME"
ML_STOCKS = (RETAINED_EARNINGS, TANGIBLE_ASSETS, LT_INVESTMENTS)
ML_FLOWS = (
    SELLING_EXPENSES,
    ADMIN_EXPENSES,
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
)
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
    facts: pl.DataFrame, as_of: date, stocks: tuple[str, ...]
) -> pl.DataFrame:
    frame = canonicalize_financial_facts(facts)
    optional = {
        "source_provider": pl.lit("normalized"),
        "source_filing_ids": pl.concat_list("filing_id"),
        "comparable_across_taxonomy": pl.lit(False),
        "is_filed_late": pl.lit(None, dtype=pl.Boolean),
    }
    available = (
        pl.when(pl.col("published_at_basis") == "EXACT_FILING")
        .then(pl.col("published_at"))
        .otherwise(pl.col("effective_available_at"))
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


def _period_amounts(
    facts: pl.DataFrame, stocks: tuple[str, ...], flows: tuple[str, ...]
) -> pl.DataFrame:
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
    quarterly = compute_ttm(flow)
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
            pl.col("effective_available_at").last().alias("_anchor_available_at"),
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
        *(
            pl.col("filing_id").filter(METRIC == name).last().alias(f"_filing_{name}")
            for name in RF03_INPUTS
        ),
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
    from CFO. Require explicit zero treasury stock to estimate shares from par.
    Parent equity is consolidated BS_TOTAL_EQUITY less BS_MINORITY_INTEREST,
    or BS_TOTAL_EQUITY directly when the statement is separate.
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
        .then(c(BORROWING_COSTS))
        .otherwise(c(INTEREST))
        .abs()
    )
    tax_inputs = (EBT, CURRENT_TAX, DEFERRED_TAX)
    implied_shares = c(PARENT_EARNINGS) / c(EPS)
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
        pl.when((c(SHARES) > 0) & (c(TREASURY_SHARES) == 0))
        .then(c(SHARES) / 10000.0)
        .alias("estimated_shares_outstanding"),
        pl.when(annual & (implied_shares > 0) & implied_shares.is_finite())
        .then(implied_shares)
        .alias("implied_weighted_shares"),
        pl.when(annual).then(c(EPS)).alias("eps_basic_reported"),
    )
    return result.with_columns(
        pl.coalesce("implied_weighted_shares", "estimated_shares_outstanding").alias(
            "_eps_shares"
        ),
        pl.when(c("implied_weighted_shares").is_not_null())
        .then(pl.lit("IMPLIED_ANNUAL_WEIGHTED"))
        .when(c("estimated_shares_outstanding").is_not_null())
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
    ).with_columns(
        pl.when(~CORPORATE)
        .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
        .when(input_has("mixed", *EBITDA_INPUTS))
        .then(pl.lit(MIXED))
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


def prepare_periods(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    include_ml_inputs: bool = False,
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
    periods = (
        _period_amounts(_visible_facts(facts, as_of, stocks), stocks, flows)
        .join(identity, on="issuer_id", how="left")
        .with_columns(
            pl.max_horizontal("_anchor_available_at", "_amounts_available_at").alias(
                "_period_available_at"
            )
        )
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
    result = with_prior_year(
        _aggregates(periods),
        "taxonomy_version",
        *amounts,
        "_period_filing_ids",
        "_period_available_at",
        "parent_equity",
        "invested_capital",
        "ebit_ttm",
        *(
            f"_{prefix}_{name}"
            for prefix in ("comparable", "mixed", "derived")
            for name in amounts
        ),
    )
    return (
        result.with_columns(
            combine_filing_ids(
                pl.col("_period_filing_ids"), pl.col("_period_filing_ids_prior")
            ).alias("source_filing_ids")
        )
        .rename({"taxonomy_version_prior": "_prior_taxonomy"})
        .sort(PERIOD)
    )
