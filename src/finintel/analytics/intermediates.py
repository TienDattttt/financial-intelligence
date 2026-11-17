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
    TT99 borrowing costs retain their separate identifier from TT200 interest.
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
TRADE_RECEIVABLES = "BS_TRADE_RECEIVABLES_ST"
RECEIVABLES = "BS_SHORT_TERM_RECEIVABLES"
LT_RECEIVABLES = "BS_LT_RECEIVABLES"
INVENTORY = "BS_INVENTORIES"
PAYABLES = "BS_TRADE_PAYABLES_ST"
REVENUE = "IS_NET_REVENUE"
EARNINGS = "IS_NET_INCOME"
PARENT_EARNINGS = "IS_NET_INCOME_PARENT"
EBT = "IS_PROFIT_BEFORE_TAX"
INTEREST = "IS_INTEREST_EXPENSE"
CURRENT_TAX = "IS_CURRENT_INCOME_TAX"
DEFERRED_TAX = "IS_DEFERRED_INCOME_TAX"
COGS = "IS_COST_OF_GOODS_SOLD"
GROSS_PROFIT = "IS_GROSS_PROFIT"
OPERATING_PROFIT = "IS_OPERATING_PROFIT"
EPS = "IS_EPS_BASIC_REPORTED"
CFO = "CF_OPERATING_CASH_FLOW"
CAPEX = "CF_CAPITAL_EXPENDITURE"
DEPRECIATION = "CF_DEPRECIATION_AMORTIZATION"

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
    TRADE_RECEIVABLES,
    RECEIVABLES,
    LT_RECEIVABLES,
    INVENTORY,
    PAYABLES,
)
FLOWS = (
    REVENUE,
    EARNINGS,
    PARENT_EARNINGS,
    EBT,
    INTEREST,
    CURRENT_TAX,
    DEFERRED_TAX,
    COGS,
    GROSS_PROFIT,
    OPERATING_PROFIT,
    EPS,
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
RATIO_KEY = [
    "issuer_id",
    "ticker",
    "accounting_regime",
    "statement_scope",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "filing_id",
]
MIXED = "MIXED_TAXONOMY"
REVISION_ORDER = ["effective_available_at", "revision_no", "filing_id"]
METRIC = pl.col("metric_id")
CORPORATE = pl.col("accounting_regime") == "CORPORATE"

DEBT_INPUTS = (ST_DEBT, LT_DEBT, BONDS)
CASH_INPUTS = (CASH, INVESTMENTS)
CAPITAL_INPUTS = (*DEBT_INPUTS, EQUITY, CASH)
PARENT_INPUTS = (EQUITY, NCI)
EBIT_INPUTS = (EBT, INTEREST)
EBITDA_INPUTS = (*EBIT_INPUTS, DEPRECIATION)
NOPAT_INPUTS = (*EBIT_INPUTS, CURRENT_TAX, DEFERRED_TAX)
FCF_INPUTS = (CFO, CAPEX)


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


def _visible_facts(facts: pl.DataFrame, as_of: date) -> pl.DataFrame:
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
            pl.when(METRIC.is_in(STOCKS))
            .then(pl.lit("STOCK"))
            .otherwise(pl.lit("FLOW"))
            .alias("value_type"),
            (pl.col("is_filed_late") == 1).alias("is_filed_late"),
        )
    )


def _period_amounts(facts: pl.DataFrame) -> pl.DataFrame:
    reports = facts.filter(
        (pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")
        & pl.col("period_type").is_in(["QUARTER", "ANNUAL"])
    )
    reported = pl.col("derivation_method") == "REPORTED_DIRECTLY"
    # Older Layer 3 files can omit Layer 2's ancillary lineage columns.
    flow = facts.filter(METRIC.is_in(FLOWS)).select(
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
        METRIC.is_in(STOCKS)
        | ((pl.col("period_type") == "ANNUAL") & METRIC.is_in(FLOWS))
    ).select(
        *PERIOD, *REVISION_ORDER, *data_columns, "value", pl.lit(False).alias("_q4")
    )
    amounts = pl.concat(
        [
            raw,
            quarterly.select(
                *PERIOD,
                *REVISION_ORDER,
                *data_columns,
                pl.col("ttm_value").alias("value"),
                pl.col("ttm_contains_derived_q4").alias("_q4"),
            ),
        ],
        how="vertical_relaxed",
    ).sort(REVISION_ORDER)
    wide = amounts.group_by(PERIOD).agg(
        *(
            expression.filter(METRIC == name).last().alias(f"{prefix}{name}")
            for name in (*STOCKS, *FLOWS)
            for prefix, expression in (
                ("", pl.col("value")),
                ("_mixed_", pl.col("computation_note") == MIXED),
                ("_derived_", pl.col("_q4")),
                ("_comparable_", pl.col("comparable_across_taxonomy")),
            )
        )
    )
    return anchors.join(wide, on=PERIOD, how="left", nulls_equal=True).join(
        warnings, on=PERIOD, how="left", nulls_equal=True
    )


def _aggregates(frame: pl.DataFrame) -> pl.DataFrame:
    """Apply the VAS intermediate formulas below to the named metric IDs.

    Required inputs propagate null. Use the explicitly allowed zero defaults
    for optional inputs. ETR is 20% for nonpositive EBT. Implied weighted
    shares require positive reported annual EPS and parent income.
    """
    c = pl.col
    debt = pl.when(c(ST_DEBT).is_not_null() | c(LT_DEBT).is_not_null()).then(
        c(ST_DEBT).fill_null(0) + c(LT_DEBT).fill_null(0) + c(BONDS).fill_null(0)
    )
    ebt = c(EBT)
    tax = c(CURRENT_TAX).fill_null(0) + c(DEFERRED_TAX).fill_null(0)
    annual = c("period_type") == "ANNUAL"
    result = frame.with_columns(
        pl.when(CORPORATE & ~input_has("mixed", *DEBT_INPUTS))
        .then(debt)
        .alias("total_debt"),
        (c(EQUITY) - c(NCI).fill_null(0)).alias("parent_equity"),
        pl.when(CORPORATE)
        .then(c(CASH) + c(INVESTMENTS).fill_null(0))
        .alias("total_cash_and_st_investments"),
        pl.when(CORPORATE)
        .then(c(CURRENT_ASSETS) - c(CURRENT_LIABILITIES))
        .alias("working_capital"),
        pl.when(CORPORATE & ~input_has("mixed", *EBIT_INPUTS))
        .then(ebt + c(INTEREST).fill_null(0).abs())
        .alias("ebit_ttm"),
        pl.when(CORPORATE & ~input_has("mixed", *NOPAT_INPUTS) & (ebt > 0))
        .then((tax / ebt).clip(0.0, 1.0))
        .when(CORPORATE & ~input_has("mixed", *NOPAT_INPUTS) & (ebt <= 0))
        .then(0.20)
        .alias("effective_tax_rate"),
        pl.when(CORPORATE & ~input_has("mixed", *FCF_INPUTS))
        .then(c(CFO) - c(CAPEX).fill_null(0).abs())
        .alias("fcf_ttm"),
        (c(SHARES) / 10000.0).alias("estimated_shares_outstanding"),
        pl.when(annual & (c(EPS) > 0) & (c(PARENT_EARNINGS) > 0))
        .then(c(PARENT_EARNINGS) / c(EPS))
        .alias("implied_weighted_shares"),
        pl.when(annual).then(c(EPS)).alias("eps_basic_reported"),
    )
    return result.with_columns(
        (c("total_debt") - c("total_cash_and_st_investments")).alias("net_debt"),
        (c("total_debt") + c(EQUITY) - c(CASH)).alias("invested_capital"),
        (c("ebit_ttm") + c(DEPRECIATION).abs()).alias("ebitda_ttm"),
        pl.when(~CORPORATE)
        .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
        .when(input_has("mixed", *EBITDA_INPUTS))
        .then(pl.lit(MIXED))
        .when(c(DEPRECIATION).is_null())
        .then(pl.lit("MISSING_DEPRECIATION_DIRECT_CF"))
        .when(c("ebit_ttm").is_null())
        .then(pl.lit("MISSING_INPUT"))
        .when(input_has("derived", *EBITDA_INPUTS))
        .then(pl.lit("DERIVED_Q4_INPUT"))
        .otherwise(pl.lit("VALID"))
        .alias("ebitda_flag"),
        (c("ebit_ttm") * (1 - c("effective_tax_rate"))).alias("nopat_ttm"),
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
    facts: pl.DataFrame, issuers: pl.DataFrame, *, as_of: date
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
    periods = _period_amounts(_visible_facts(facts, as_of)).join(
        identity, on="issuer_id", how="left"
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
        *STOCKS,
        *FLOWS,
        "parent_equity",
        "invested_capital",
        "ebit_ttm",
        *(
            f"_{prefix}_{name}"
            for prefix in ("comparable", "mixed", "derived")
            for name in (*STOCKS, *FLOWS)
        ),
    )
    return result.rename({"taxonomy_version_prior": "_prior_taxonomy"}).sort(PERIOD)
