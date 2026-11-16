"""Derive fiscal quarters and four-quarter totals with explicit filing provenance."""

from datetime import date

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.regime_resolver import TT99_EFFECTIVE_DATE

SERIES = ["issuer_id", "statement_scope", "source_provider", "metric_id"]
QUARTER_KEY = [*SERIES, "fiscal_year_start_date", "fiscal_quarter"]
FISCAL_SERIES = [*SERIES, "fiscal_year_start_date", "taxonomy_version"]
FISCAL_QUARTER = [*FISCAL_SERIES, "fiscal_quarter"]
PERIOD_ORDER = [*SERIES, "period_end_date"]
KNOWN_SCOPES = ["CONSOLIDATED", "SEPARATE"]
PRE_AUDIT = ["UNAUDITED", "REVIEWED"]
REVISION_ORDER = ["effective_available_at", "revision_no", "filing_id"]
Q = pl.col("fiscal_quarter")
START = pl.col("period_start_date")
END = pl.col("period_end_date")
FISCAL_START = pl.col("fiscal_year_start_date")
DAYS = pl.col("period_days")
VALUE = pl.col("value")
AUDIT = pl.col("audit_status")
AVAILABLE = pl.col("effective_available_at")
EXACT = pl.col("published_at_basis") == "EXACT_FILING"


def _days(start: pl.Expr, end: pl.Expr) -> pl.Expr:
    return ((end - start).dt.total_days() + 1).cast(pl.Int32)


def _disclosure(available: pl.Expr, exact: pl.Expr) -> list[pl.Expr]:
    return [
        available.alias("effective_available_at"),
        pl.when(exact).then(available).otherwise(None).alias("published_at"),
        pl.when(exact)
        .then(pl.lit("EXACT_FILING"))
        .otherwise(pl.lit("LATEST_INPUT_AVAILABILITY"))
        .alias("published_at_basis"),
    ]


def _derived(
    start: pl.Expr,
    amount: pl.Expr,
    method: str,
    priority: int,
    available: pl.Expr,
    exact: pl.Expr,
    filings: pl.Expr,
) -> list[pl.Expr]:
    return [
        start.alias("period_start_date"),
        _days(start, END).alias("period_days"),
        amount.alias("value"),
        pl.lit("QUARTER").alias("period_type"),
        pl.lit(method).alias("derivation_method"),
        pl.lit(priority).alias("_priority"),
        filings.list.unique(maintain_order=True).alias("source_filing_ids"),
        *_disclosure(available, exact),
    ]


def _prefer_quarters(quarters: pl.DataFrame) -> pl.DataFrame:
    return (
        quarters.with_columns(VALUE.is_null().alias("_missing"))
        .sort(
            ["_missing", "_priority", *REVISION_ORDER],
            descending=[False, False, True, True, True],
        )
        .unique(subset=FISCAL_QUARTER, keep="first")
        .drop("_missing")
    )


def _ytd_diff(cumulative: pl.DataFrame) -> pl.DataFrame:
    keys = [*FISCAL_SERIES, "fiscal_quarter", "audit_status"]
    previous = cumulative.filter(Q.is_between(1, 3)).select(
        *FISCAL_SERIES,
        (Q + 1).alias("fiscal_quarter"),
        AUDIT,
        END.alias("_previous_end"),
        VALUE.alias("_previous_value"),
        AVAILABLE.alias("_previous_available"),
        EXACT.alias("_previous_exact"),
        pl.col("source_filing_ids").alias("_previous_filings"),
    )
    start = pl.col("_previous_end").dt.offset_by("1d")
    return (
        cumulative.filter(Q.is_between(2, 4) & VALUE.is_not_null())
        .join(previous, on=keys, how="inner", validate="m:1")
        .filter(
            pl.col("_previous_value").is_not_null()
            & _days(start, END).is_between(80, 100)
        )
        .with_columns(
            _derived(
                start,
                VALUE - pl.col("_previous_value"),
                "DERIVED_YTD_DIFF",
                1,
                pl.max_horizontal(AVAILABLE, "_previous_available"),
                EXACT & pl.col("_previous_exact"),
                pl.col("_previous_filings").list.concat("source_filing_ids"),
            )
        )
        .select(*FACT_SCHEMA.names(), "_priority")
    )


def _audited_q4(annual: pl.DataFrame, quarters: pl.DataFrame) -> pl.DataFrame:
    first_nine_months = (
        quarters.filter(
            Q.is_between(1, 3) & VALUE.is_not_null() & AUDIT.is_in(PRE_AUDIT)
        )
        .sort([*FISCAL_SERIES, "fiscal_quarter"])
        .with_columns(
            END.shift(1).over(FISCAL_SERIES).dt.offset_by("1d").alias("_next_start")
        )
        .group_by(FISCAL_SERIES)
        .agg(
            pl.len().alias("_quarter_count"),
            (START == pl.col("_next_start").fill_null(FISCAL_START))
            .all()
            .alias("_contiguous"),
            END.last().alias("_q3_end"),
            VALUE.sum().alias("_9m_value"),
            AVAILABLE.max().alias("_9m_available"),
            EXACT.all().alias("_9m_exact"),
            pl.col("source_filing_ids")
            .explode()
            .unique(maintain_order=True)
            .alias("_9m_filings"),
        )
        .filter((pl.col("_quarter_count") == 3) & pl.col("_contiguous"))
    )
    start = pl.col("_q3_end").dt.offset_by("1d")
    return (
        annual.filter((AUDIT == "AUDITED") & VALUE.is_not_null())
        .join(first_nine_months, on=FISCAL_SERIES, how="inner", validate="m:1")
        .filter(_days(start, END).is_between(80, 100))
        .with_columns(
            *_derived(
                start,
                VALUE - pl.col("_9m_value"),
                "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX",
                2,
                pl.max_horizontal(AVAILABLE, "_9m_available"),
                EXACT & pl.col("_9m_exact"),
                pl.col("_9m_filings").list.concat("source_filing_ids"),
            ),
            pl.lit(4, dtype=pl.Int32).alias("fiscal_quarter"),
        )
        .select(*FACT_SCHEMA.names(), "_priority")
    )


def derive_quarters(facts: pl.DataFrame, *, as_of: date | None = None) -> pl.DataFrame:
    """Return one visible discrete FLOW observation per fiscal quarter and taxonomy.

    For each canonical FLOW metric, prefer a reported 80–100 day observation,
    then YTD(q) − YTD(q−1), then audited FY − (Q1 + Q2 + Q3).
    Preserve null reported amounts. Missing or incompatible operands yield
    no derived observation. No denominator is used.
    """
    visible = facts.filter(AVAILABLE <= as_of) if as_of is not None else facts
    flow = (
        visible.filter(pl.col("value_type") == "FLOW")
        .sort(REVISION_ORDER)
        .unique(
            subset=[
                *FISCAL_QUARTER,
                "period_type",
                "period_start_date",
                "period_end_date",
                "audit_status",
                "derivation_method",
            ],
            keep="last",
        )
    )
    annual = flow.filter(
        (pl.col("period_type") == "ANNUAL")
        & (START == FISCAL_START)
        & DAYS.is_between(350, 380)
        & pl.col("statement_scope").is_in(KNOWN_SCOPES)
    )
    direct = flow.filter(
        (pl.col("period_type") == "QUARTER")
        & (pl.col("derivation_method") == "REPORTED_DIRECTLY")
        & Q.is_between(1, 4)
        & DAYS.is_between(80, 100)
        & (DAYS == _days(START, END))
        & (START - FISCAL_START).dt.total_days().is_between((Q - 1) * 80, (Q - 1) * 100)
    ).with_columns(pl.lit(0).alias("_priority"))
    cumulative = (
        pl.concat(
            [
                flow.filter(
                    pl.col("period_type").is_in(["QUARTER", "SEMI_ANNUAL"])
                ).with_columns(
                    pl.when(pl.col("period_type") == "SEMI_ANNUAL")
                    .then(2)
                    .otherwise(Q)
                    .alias("fiscal_quarter")
                ),
                annual.with_columns(pl.lit(4, dtype=pl.Int32).alias("fiscal_quarter")),
            ]
        )
        .filter(
            (START == FISCAL_START)
            & Q.is_between(1, 4)
            & DAYS.is_between(Q * 80, Q * 100)
            & (DAYS == _days(START, END))
            & (AUDIT != "UNKNOWN")
            & pl.col("statement_scope").is_in(KNOWN_SCOPES)
        )
        .sort(REVISION_ORDER)
        .unique(subset=[*FISCAL_QUARTER, "audit_status"], keep="last")
    )
    reported_or_ytd = _prefer_quarters(pl.concat([direct, _ytd_diff(cumulative)]))
    quarters = (
        _prefer_quarters(
            pl.concat([reported_or_ytd, _audited_q4(annual, reported_or_ytd)])
        )
        .select(FACT_SCHEMA.names())
        .sort([*PERIOD_ORDER, "taxonomy_version"])
    )
    if quarters.select((VALUE.is_not_null() & ~VALUE.is_finite()).any()).item():
        raise ContractViolationError("Quarter derivation exceeded the numeric range.")
    return quarters


def compute_ttm(facts_df: pl.DataFrame, *, as_of: date | None = None) -> pl.DataFrame:
    """Compute TTM(t) = FLOW(t−3) + FLOW(t−2) + FLOW(t−1) + FLOW(t).

    Use four nonnull adjacent discrete quarters in a verified scope and
    350–380 days of coverage. Use a visible TT99 comparative only for
    the quarter it covers. Missing, noncontiguous or incompatible quarters
    yield null. No denominator is used. Availability is the latest input date.
    """
    quarters = derive_quarters(facts_df, as_of=as_of)
    visible = facts_df.filter(AVAILABLE <= as_of) if as_of is not None else facts_df
    unresolved = (
        visible.filter(
            (pl.col("value_type") == "FLOW")
            & (pl.col("period_type") == "QUARTER")
            & Q.is_between(1, 4)
        )
        .join(quarters.select(FISCAL_QUARTER), on=FISCAL_QUARTER, how="anti")
        .sort(REVISION_ORDER)
        .unique(subset=FISCAL_QUARTER, keep="last")
        .with_columns(
            pl.lit(None, dtype=pl.Float64).alias("value"),
            pl.lit(None, dtype=pl.String).alias("derivation_method"),
        )
    )
    quarters = pl.concat([quarters, unresolved])
    is_comparative = (
        (pl.col("revision_reason") == "RESTATED_REGIME_CHANGE")
        & (pl.col("taxonomy_version") == "VAS_TT99_2025")
        & (FISCAL_START < TT99_EFFECTIVE_DATE)
    )
    original = quarters.filter(~is_comparative)
    result = _ttm_windows(original)
    comparative = quarters.filter(is_comparative & VALUE.is_not_null())
    if comparative.is_empty():
        return result
    adjusted = pl.concat(
        [
            original.join(comparative.select(QUARTER_KEY), on=QUARTER_KEY, how="anti"),
            comparative,
        ]
    )
    revised = _ttm_windows(adjusted).filter(
        (FISCAL_START >= TT99_EFFECTIVE_DATE) & pl.col("ttm_value").is_not_null()
    )
    return pl.concat(
        [
            result.join(revised.select(QUARTER_KEY), on=QUARTER_KEY, how="anti"),
            revised,
        ]
    ).sort(PERIOD_ORDER)


def _ttm_windows(quarters: pl.DataFrame) -> pl.DataFrame:
    quarters = quarters.sort(PERIOD_ORDER)
    previous_q = Q.shift(1).over(SERIES)
    previous_year = FISCAL_START.shift(1).over(SERIES)
    contiguous = (START == END.shift(1).over(SERIES).dt.offset_by("1d")) & (
        ((Q == previous_q + 1) & (FISCAL_START == previous_year))
        | ((Q == 1) & (previous_q == 4) & (FISCAL_START > previous_year))
    )
    staged = quarters.with_columns(contiguous.alias("_contiguous"))

    def last_four(expr: pl.Expr) -> list[pl.Expr]:
        return [expr, *(expr.shift(lag).over(SERIES) for lag in range(1, 4))]

    valid = (
        pl.all_horizontal(*(value.is_not_null() for value in last_four(VALUE)))
        & pl.col("statement_scope").is_in(KNOWN_SCOPES)
        & pl.all_horizontal(
            *(pl.col("_contiguous").shift(lag).over(SERIES) for lag in range(3))
        )
        & pl.sum_horizontal(*last_four(DAYS), ignore_nulls=False).is_between(350, 380)
    )
    tax = pl.col("taxonomy_version")
    mixed = ~pl.all_horizontal(*(tax == previous for previous in last_four(tax)[1:]))
    comparable = pl.all_horizontal(*last_four(pl.col("comparable_across_taxonomy")))
    allowed = valid & (~mixed | comparable)
    result = staged.with_columns(
        pl.when(allowed)
        .then(pl.sum_horizontal(*last_four(VALUE), ignore_nulls=False))
        .otherwise(None)
        .alias("ttm_value"),
        pl.when(valid & mixed & ~comparable)
        .then(pl.lit("MIXED_TAXONOMY"))
        .otherwise(None)
        .alias("computation_note"),
        pl.when(allowed)
        .then(pl.max_horizontal(*last_four(AVAILABLE)))
        .otherwise(None)
        .alias("ttm_available_at"),
        pl.when(allowed)
        .then(
            pl.concat_list(*last_four(pl.col("source_filing_ids"))).list.unique(
                maintain_order=True
            )
        )
        .otherwise(None)
        .alias("ttm_source_filing_ids"),
    ).drop("_contiguous")
    if result.select((pl.col("ttm_value").is_infinite()).any()).item():
        raise ContractViolationError("TTM computation exceeded the numeric range.")
    return result
