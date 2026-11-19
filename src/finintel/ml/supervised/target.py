"""Build forward distress labels from point-in-time corporate ratio periods.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.3 and 7.2.
    Labels use the four following fiscal quarters and each source period's
    PIT availability timestamp; incomplete horizons stay unlabeled.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from finintel.exceptions import ContractViolationError

_REQUIRED = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "accounting_regime",
    "BS_TOTAL_EQUITY",
    "parent_equity",
    "interest_coverage",
    "CF_OPERATING_CASH_FLOW",
    "current_ratio",
    "roa",
    "_period_available_at",
)


def build_distress_targets(
    periods: pl.DataFrame,
    *,
    mode: str = "FUNDAMENTAL_DISTRESS",
    as_of: date | None = None,
) -> pl.DataFrame:
    """Return one four-quarter-ahead label per eligible quarter.

    Fundamental distress is the union of nonpositive total/parent equity in
    the forward window, at least two quarters with interest coverage < 1,
    negative TTM CFO and current ratio < 1, or quarter +4 ROA < -10% with
    negative TTM CFO. Debt-free firms with explicit NO_INTEREST_EXPENSE have a
    known non-distressed interest-coverage state rather than missing data.
    Missing or nonconsecutive horizons produce a null label. Availability uses
    the latest PIT timestamp across all four future quarters.
    """
    if mode != "FUNDAMENTAL_DISTRESS":
        raise ContractViolationError(
            "Only FUNDAMENTAL_DISTRESS is supported without regulatory events."
        )
    missing = set(_REQUIRED) - set(periods.columns)
    if missing:
        raise ContractViolationError(
            f"Distress target input is missing required columns: {sorted(missing)}"
        )
    has_exact_basis = "_exact_filing_basis" in periods.columns
    base = periods.filter(
        (pl.col("accounting_regime") == "CORPORATE")
        & (pl.col("period_type") == "QUARTER")
        & pl.col("fiscal_quarter").is_between(1, 4)
        & pl.col("_period_available_at").is_not_null()
    )
    if as_of is not None:
        # A point-in-time label build may only use observations already
        # available at the requested cut-off, including delayed filings.
        base = base.filter(
            (pl.col("period_end_date") <= as_of)
            & (pl.col("_period_available_at") <= as_of)
        )
    if base.is_empty():
        empty_cols = [
            pl.lit(None, dtype=pl.Int8).alias("distress_target"),
            pl.lit(None, dtype=pl.Date).alias("label_available_at"),
            pl.lit(None, dtype=pl.Boolean).alias("already_distressed_at_t"),
        ]
        if has_exact_basis:
            empty_cols.append(
                pl.lit(None, dtype=pl.Boolean).alias("_label_exact_filing_basis")
            )
        return periods.head(0).with_columns(*empty_cols)

    duplicate_keys = base.select(
        pl.struct("issuer_id", "statement_scope", "fiscal_year", "fiscal_quarter")
        .is_duplicated()
        .any()
    ).item()
    if duplicate_keys:
        raise ContractViolationError(
            "Target construction requires unique issuer/scope/fiscal-quarter rows."
        )

    no_interest = (
        pl.col("interest_coverage_flag") == "NO_INTEREST_EXPENSE"
        if "interest_coverage_flag" in base.columns
        else pl.lit(False)
    )
    equity_bad = (
        (pl.col("BS_TOTAL_EQUITY") <= 0) | (pl.col("parent_equity") <= 0)
    ).fill_null(False)
    equity_known = (
        pl.col("BS_TOTAL_EQUITY").is_not_null() & pl.col("parent_equity").is_not_null()
    )
    liquidity_bad = (
        (pl.col("interest_coverage") < 1.0)
        & (pl.col("CF_OPERATING_CASH_FLOW") < 0)
        & (pl.col("current_ratio") < 1.0)
    ).fill_null(False)
    liquidity_known = (
        (pl.col("interest_coverage").is_not_null() | no_interest)
        & pl.col("CF_OPERATING_CASH_FLOW").is_not_null()
        & pl.col("current_ratio").is_not_null()
    )
    severe_loss = (
        (pl.col("roa") < -0.10) & (pl.col("CF_OPERATING_CASH_FLOW") < 0)
    ).fill_null(False)
    loss_known = (
        pl.col("roa").is_not_null() & pl.col("CF_OPERATING_CASH_FLOW").is_not_null()
    )

    base = base.with_columns(
        (
            pl.col("fiscal_year").cast(pl.Int32) * 4
            + pl.col("fiscal_quarter").cast(pl.Int32)
        ).alias("_quarter_index"),
        equity_bad.alias("_equity_bad"),
        equity_known.alias("_equity_known"),
        liquidity_bad.alias("_liquidity_bad"),
        liquidity_known.alias("_liquidity_known"),
        severe_loss.alias("_severe_loss"),
        loss_known.alias("_loss_known"),
    ).sort(["issuer_id", "statement_scope", "_quarter_index"])

    result = base
    past_cols: list[str] = []
    for lag in range(1, 4):
        past_name = f"_past_liquidity_{lag}"
        past = base.select(
            "issuer_id",
            "statement_scope",
            (pl.col("_quarter_index") + lag).alias("_quarter_index"),
            pl.col("_liquidity_bad").alias(past_name),
        )
        result = result.join(
            past,
            on=["issuer_id", "statement_scope", "_quarter_index"],
            how="left",
            validate="1:1",
        )
        past_cols.append(past_name)

    trailing_liquidity = pl.sum_horizontal(
        pl.col("_liquidity_bad").cast(pl.Int8),
        *(pl.col(name).fill_null(False).cast(pl.Int8) for name in past_cols),
    )
    distressed_now = (
        pl.col("_equity_bad") | pl.col("_severe_loss") | (trailing_liquidity >= 2)
    )
    current_known = (
        pl.col("_equity_known") & pl.col("_liquidity_known") & pl.col("_loss_known")
    )
    result = result.with_columns(
        pl.when(distressed_now)
        .then(pl.lit(True))
        .when(current_known)
        .then(pl.lit(False))
        .otherwise(pl.lit(None, dtype=pl.Boolean))
        .alias("already_distressed_at_t")
    )

    future_temp_cols: list[str] = []
    future_available: list[str] = []
    for offset in range(1, 5):
        date_name = f"_future_end_{offset}"
        available_name = f"_future_available_{offset}"
        select_exprs: list[pl.Expr] = [
            pl.col("issuer_id"),
            pl.col("statement_scope"),
            (pl.col("_quarter_index") - offset).alias("_quarter_index"),
            pl.col("_equity_bad").alias(f"_equity_{offset}"),
            pl.col("_liquidity_bad").alias(f"_liquidity_{offset}"),
            pl.col("_severe_loss").alias(f"_loss_{offset}"),
            (pl.col("_equity_known") & pl.col("_liquidity_known")).alias(
                f"_eq_liq_known_{offset}"
            ),
            pl.col("_loss_known").alias(f"_loss_known_{offset}"),
            pl.col("period_end_date").alias(date_name),
            pl.col("_period_available_at").alias(available_name),
        ]
        temp_names = [
            f"_equity_{offset}",
            f"_liquidity_{offset}",
            f"_loss_{offset}",
            f"_eq_liq_known_{offset}",
            f"_loss_known_{offset}",
            date_name,
            available_name,
        ]
        if has_exact_basis:
            exact_name = f"_future_exact_{offset}"
            select_exprs.append(pl.col("_exact_filing_basis").alias(exact_name))
            temp_names.append(exact_name)
        future = base.select(*select_exprs)
        result = result.join(
            future,
            on=["issuer_id", "statement_scope", "_quarter_index"],
            how="left",
            validate="1:1",
        )
        future_temp_cols.extend(temp_names)
        future_available.append(available_name)

    quarters_present = pl.all_horizontal(
        *(pl.col(f"_future_end_{offset}").is_not_null() for offset in range(1, 5))
    )
    count_liquidity = pl.sum_horizontal(
        *(
            pl.col(f"_liquidity_{offset}").fill_null(False).cast(pl.Int8)
            for offset in range(1, 5)
        )
    )
    event = (
        pl.any_horizontal(
            *(pl.col(f"_equity_{offset}").fill_null(False) for offset in range(1, 5))
        )
        | (count_liquidity >= 2)
        | pl.col("_loss_4").fill_null(False)
    )
    positive_verified = quarters_present & event
    negative_verified = (
        quarters_present
        & ~event
        & pl.all_horizontal(
            *(
                pl.col(f"_eq_liq_known_{offset}").fill_null(False)
                for offset in range(1, 5)
            ),
            pl.col("_loss_known_4").fill_null(False),
        )
    )
    labeled = positive_verified | negative_verified
    label_available = pl.max_horizontal(*(pl.col(name) for name in future_available))

    output_exprs = [
        pl.when(positive_verified)
        .then(pl.lit(1, dtype=pl.Int8))
        .when(negative_verified)
        .then(pl.lit(0, dtype=pl.Int8))
        .otherwise(pl.lit(None, dtype=pl.Int8))
        .alias("distress_target"),
        pl.when(labeled)
        .then(label_available)
        .otherwise(None)
        .alias("label_available_at"),
    ]
    if has_exact_basis:
        output_exprs.append(
            pl.when(labeled)
            .then(
                pl.all_horizontal(
                    *(
                        pl.col(f"_future_exact_{offset}").fill_null(False)
                        for offset in range(1, 5)
                    )
                )
            )
            .otherwise(None)
            .alias("_label_exact_filing_basis")
        )

    return (
        result.with_columns(*output_exprs)
        .drop(
            "_quarter_index",
            "_equity_bad",
            "_equity_known",
            "_liquidity_bad",
            "_liquidity_known",
            "_severe_loss",
            "_loss_known",
            *past_cols,
            *future_temp_cols,
        )
        .sort(["issuer_id", "statement_scope", "period_end_date"])
    )
