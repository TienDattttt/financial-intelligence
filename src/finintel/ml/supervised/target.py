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
    negative TTM CFO. Missing or nonconsecutive horizons produce a null label.
    Availability uses the latest PIT timestamp of each future observation.
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
        return periods.head(0).with_columns(
            pl.lit(None, dtype=pl.Int8).alias("distress_target"),
            pl.lit(None, dtype=pl.Date).alias("label_available_at"),
            pl.lit(False).alias("already_distressed_at_t"),
        )
    duplicate_keys = base.select(
        pl.struct("issuer_id", "statement_scope", "fiscal_year", "fiscal_quarter")
        .is_duplicated()
        .any()
    ).item()
    if duplicate_keys:
        raise ContractViolationError(
            "Target construction requires unique issuer/scope/fiscal-quarter rows."
        )
    base = base.with_columns(
        (
            pl.col("fiscal_year").cast(pl.Int32) * 4
            + pl.col("fiscal_quarter").cast(pl.Int32)
        ).alias("_quarter_index")
    ).sort(["issuer_id", "statement_scope", "_quarter_index"])
    equity_bad = (
        (pl.col("BS_TOTAL_EQUITY") <= 0) | (pl.col("parent_equity") <= 0)
    ).fill_null(False)
    liquidity_bad = (
        (pl.col("interest_coverage") < 1.0)
        & (pl.col("CF_OPERATING_CASH_FLOW") < 0)
        & (pl.col("current_ratio") < 1.0)
    ).fill_null(False)
    severe_loss = (
        (pl.col("roa") < -0.10) & (pl.col("CF_OPERATING_CASH_FLOW") < 0)
    ).fill_null(False)
    base = base.with_columns(
        (equity_bad | liquidity_bad | severe_loss).alias("already_distressed_at_t")
    )
    result = base
    future_dates: list[str] = []
    future_available: list[str] = []
    for offset in range(1, 5):
        date_name = f"_future_end_{offset}"
        available_name = f"_future_available_{offset}"
        future = base.select(
            "issuer_id",
            "statement_scope",
            (pl.col("_quarter_index") - offset).alias("_quarter_index"),
            equity_bad.alias(f"_equity_{offset}"),
            liquidity_bad.alias(f"_liquidity_{offset}"),
            severe_loss.alias(f"_loss_{offset}"),
            pl.col("period_end_date").alias(date_name),
            pl.col("_period_available_at").alias(available_name),
        )
        result = result.join(
            future,
            on=["issuer_id", "statement_scope", "_quarter_index"],
            how="left",
            validate="1:1",
        )
        future_dates.append(date_name)
        future_available.append(available_name)
    present = pl.all_horizontal(*(pl.col(name).is_not_null() for name in future_dates))
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
    label_available = pl.max_horizontal(*(pl.col(name) for name in future_available))
    return (
        result.with_columns(
            pl.when(present)
            .then(event.cast(pl.Int8))
            .otherwise(None)
            .alias("distress_target"),
            pl.when(present)
            .then(label_available)
            .otherwise(None)
            .alias("label_available_at"),
        )
        .drop(
            "_quarter_index",
            *(
                f"_{part}_{offset}"
                for offset in range(1, 5)
                for part in ("equity", "liquidity", "loss")
            ),
            *future_dates,
            *future_available,
        )
        .sort(["issuer_id", "statement_scope", "period_end_date"])
    )
