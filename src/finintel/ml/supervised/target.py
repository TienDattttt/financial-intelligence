"""Build forward distress labels from point-in-time corporate ratio periods.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.3 and 7.2.
    Shumway, T. (2001). "Forecasting Bankruptcy More Accurately: A Simple Hazard Model."
        The Journal of Business, Vol. 74, No. 1, pp. 101-124.
    Falkenstein, E., Boral, A., & Carty, L. V. (2000). "RiskCalc For Private Companies:
        Moody's Default Model." Moody's Investors Service Global Credit Research.
    Decree 155/2020/ND-CP (Article 120 - Mandatory Delisting) and VNX Decision 22/QD-HDTV
        (18/04/2025 - Supervision, Control, Trading Restriction and Suspension Rules).
    Labels use the four following fiscal quarters and each source period's
    PIT availability timestamp; incomplete horizons stay unlabeled except when a
    verified regulatory distress event occurs within (a_t, a_t + 365d] in Mode B.
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
_ALLOWED_MODES = frozenset({"FUNDAMENTAL_DISTRESS", "REGULATORY_DISTRESS"})
_REGULATORY_REQUIRED = ("issuer_id", "event_date", "event_type")
_ALLOWED_REGULATORY_EVENT_TYPES = frozenset(
    {
        "CONTROL",
        "TRADING_RESTRICTION",
        "SUSPENSION",
        "FORCED_DELISTING",
        "ADVERSE_OR_DISCLAIMER_AUDIT",
    }
)


def _prepare_regulatory_events(
    regulatory_events: pl.DataFrame | None,
    *,
    as_of: date | None,
) -> pl.DataFrame:
    """Validate the offline regulatory_events contract and filter strictly by as_of."""
    if regulatory_events is None:
        raise ContractViolationError(
            "REGULATORY_DISTRESS mode requires a regulatory_events DataFrame."
        )
    missing = set(_REGULATORY_REQUIRED) - set(regulatory_events.columns)
    if missing:
        raise ContractViolationError(
            f"regulatory_events is missing required columns: {sorted(missing)}"
        )
    expected_dtypes = {
        "issuer_id": (pl.String, pl.Null),
        "event_date": (pl.Date, pl.Null),
        "event_type": (pl.String, pl.Null),
    }
    for col_name, allowed_dtypes in expected_dtypes.items():
        if regulatory_events.schema.get(col_name) not in allowed_dtypes:
            raise ContractViolationError(
                f"regulatory_events column {col_name!r} has invalid dtype "
                f"{regulatory_events.schema.get(col_name)!r}."
            )
    selected = regulatory_events.select(
        pl.col("issuer_id").cast(pl.String),
        pl.col("event_date").cast(pl.Date),
        pl.col("event_type").cast(pl.String),
    )
    if not selected.is_empty():
        has_invalid_nulls = selected.select(
            (
                pl.col("issuer_id").is_null()
                | (pl.col("issuer_id").str.strip_chars() == "")
                | pl.col("event_date").is_null()
                | pl.col("event_type").is_null()
            ).any()
        ).item()
        if has_invalid_nulls:
            raise ContractViolationError(
                "regulatory_events contains null or empty issuer_id, event_date, or event_type."
            )
        invalid_types = (
            set(selected.get_column("event_type").unique().to_list())
            - _ALLOWED_REGULATORY_EVENT_TYPES
        )
        if invalid_types:
            raise ContractViolationError(
                f"regulatory_events contains unsupported event_type values: {sorted(invalid_types)}"
            )
    if as_of is not None:
        selected = selected.filter(pl.col("event_date") <= as_of)
    return selected


def build_distress_targets(
    periods: pl.DataFrame,
    *,
    mode: str = "FUNDAMENTAL_DISTRESS",
    as_of: date | None = None,
    regulatory_events: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Return one four-quarter-ahead label per eligible quarter.

    Fundamental distress is the union of:
    1. Nonpositive total or parent equity in {t+1..t+4}.
    2. At least two quarters in {t+1..t+4} with liquidity distress:
       (interest_coverage < 1.0 & CF_OPERATING_CASH_FLOW < 0 & current_ratio < 1.0)
       or (when total_debt is present: interest_coverage < 0.0 & current_ratio < 0.80 & total_debt > 0).
    3. Severe loss in any quarter of {t+1..t+4}:
       roa < -0.10 combined with either CF_OPERATING_CASH_FLOW < 0 or
       (interest_coverage < 1.0 & current_ratio < 1.0).

    In REGULATORY_DISTRESS mode (offline library contract), fundamental distress is
    combined with verified exchange/audit events within (a_t, a_t + 365d], without
    requiring 4 post-event financial quarters on positive regulatory events and
    enforcing a right-censoring lock (a_t + 365d <= as_of) on negative labels.
    """
    if mode not in _ALLOWED_MODES:
        raise ContractViolationError(
            f"Unsupported distress target mode {mode!r}; expected one of {sorted(_ALLOWED_MODES)}."
        )
    reg_df = (
        _prepare_regulatory_events(regulatory_events, as_of=as_of)
        if mode == "REGULATORY_DISTRESS"
        else None
    )
    missing = set(_REQUIRED) - set(periods.columns)
    if missing:
        raise ContractViolationError(
            f"Distress target input is missing required columns: {sorted(missing)}"
        )
    exact_source_col = (
        "_period_exact_filing"
        if "_period_exact_filing" in periods.columns
        else "_exact_filing_basis"
        if "_exact_filing_basis" in periods.columns
        else None
    )
    has_exact_basis = exact_source_col is not None
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
        (pl.col("interest_coverage_flag") == "NO_INTEREST_EXPENSE").fill_null(False)
        if "interest_coverage_flag" in base.columns
        else pl.lit(False)
    )
    has_total_debt = "total_debt" in base.columns

    equity_bad = (
        (pl.col("BS_TOTAL_EQUITY") <= 0) | (pl.col("parent_equity") <= 0)
    ).fill_null(False)
    equity_known = (
        pl.col("BS_TOTAL_EQUITY").is_not_null() & pl.col("parent_equity").is_not_null()
    )

    branch_a = (
        (pl.col("interest_coverage") < 1.0)
        & (pl.col("CF_OPERATING_CASH_FLOW") < 0)
        & (pl.col("current_ratio") < 1.0)
    )
    branch_b = (
        (pl.col("interest_coverage") < 0.0)
        & (pl.col("current_ratio") < 0.80)
        & (pl.col("total_debt") > 0)
        if has_total_debt
        else pl.lit(False)
    )
    liquidity_bad = (branch_a | branch_b).fill_null(False)
    branch_a_known = (
        no_interest
        | (pl.col("interest_coverage") >= 1.0).fill_null(False)
        | (pl.col("CF_OPERATING_CASH_FLOW") >= 0).fill_null(False)
        | (pl.col("current_ratio") >= 1.0).fill_null(False)
        | (
            pl.col("interest_coverage").is_not_null()
            & pl.col("CF_OPERATING_CASH_FLOW").is_not_null()
            & pl.col("current_ratio").is_not_null()
        )
    )
    branch_b_known = (
        pl.lit(True)
        if not has_total_debt
        else (
            no_interest
            | (pl.col("interest_coverage") >= 0.0).fill_null(False)
            | (pl.col("current_ratio") >= 0.80).fill_null(False)
            | (pl.col("total_debt") <= 0).fill_null(False)
            | (
                pl.col("interest_coverage").is_not_null()
                & pl.col("current_ratio").is_not_null()
                & pl.col("total_debt").is_not_null()
            )
        )
    )
    liquidity_known = branch_a_known & branch_b_known

    severe_loss = (
        (pl.col("roa") < -0.10)
        & (
            (pl.col("CF_OPERATING_CASH_FLOW") < 0)
            | ((pl.col("interest_coverage") < 1.0) & (pl.col("current_ratio") < 1.0))
        )
    ).fill_null(False)
    loss_known = pl.col("roa").is_not_null() & (
        (pl.col("roa") >= -0.10).fill_null(False)
        | (
            pl.col("CF_OPERATING_CASH_FLOW").is_not_null()
            & (pl.col("interest_coverage").is_not_null() | no_interest)
            & pl.col("current_ratio").is_not_null()
        )
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
        past_liq = f"_past_liquidity_{lag}"
        past_eq = f"_past_equity_{lag}"
        past_loss = f"_past_loss_{lag}"
        past = base.select(
            "issuer_id",
            "statement_scope",
            (pl.col("_quarter_index") + lag).alias("_quarter_index"),
            pl.col("_liquidity_bad").alias(past_liq),
            pl.col("_equity_bad").alias(past_eq),
            pl.col("_severe_loss").alias(past_loss),
        )
        result = result.join(
            past,
            on=["issuer_id", "statement_scope", "_quarter_index"],
            how="left",
            validate="1:1",
        )
        past_cols.extend([past_liq, past_eq, past_loss])

    trailing_liquidity_4q = pl.sum_horizontal(
        pl.col("_liquidity_bad").cast(pl.Int8),
        *(
            pl.col(f"_past_liquidity_{lag}").fill_null(False).cast(pl.Int8)
            for lag in range(1, 4)
        ),
    )
    any_eq_or_loss_4q = pl.any_horizontal(
        pl.col("_equity_bad") | pl.col("_severe_loss"),
        *(
            pl.col(f"_past_equity_{lag}").fill_null(False)
            | pl.col(f"_past_loss_{lag}").fill_null(False)
            for lag in range(1, 4)
        ),
    )
    distressed_now = any_eq_or_loss_4q | (trailing_liquidity_4q >= 2)

    reg_temp_cols: list[str] = []
    if reg_df is not None:
        reg_agg = (
            result.select(
                "issuer_id",
                "statement_scope",
                "_quarter_index",
                "_period_available_at",
            )
            .join(reg_df, on="issuer_id", how="inner")
            .with_columns(
                (
                    (pl.col("event_date") > pl.col("_period_available_at"))
                    & (
                        pl.col("event_date")
                        <= (pl.col("_period_available_at") + pl.duration(days=365))
                    )
                ).alias("_is_future_reg"),
                (
                    (
                        pl.col("event_date")
                        >= (pl.col("_period_available_at") - pl.duration(days=365))
                    )
                    & (pl.col("event_date") <= pl.col("_period_available_at"))
                ).alias("_is_prior_reg"),
            )
            .group_by(["issuer_id", "statement_scope", "_quarter_index"])
            .agg(
                pl.col("event_date")
                .filter(pl.col("_is_future_reg"))
                .min()
                .alias("_earliest_future_event_date"),
                pl.col("_is_prior_reg")
                .any()
                .fill_null(False)
                .alias("_had_prior_reg_event"),
            )
        )
        result = result.join(
            reg_agg,
            on=["issuer_id", "statement_scope", "_quarter_index"],
            how="left",
            validate="1:1",
        )
        reg_temp_cols.extend(["_earliest_future_event_date", "_had_prior_reg_event"])
        distressed_now = distressed_now | pl.col("_had_prior_reg_event").fill_null(
            False
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
        if exact_source_col is not None:
            exact_name = f"_future_exact_{offset}"
            select_exprs.append(pl.col(exact_source_col).alias(exact_name))
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
    event_3 = pl.any_horizontal(
        *(pl.col(f"_loss_{offset}").fill_null(False) for offset in range(1, 5))
    )
    event = (
        pl.any_horizontal(
            *(pl.col(f"_equity_{offset}").fill_null(False) for offset in range(1, 5))
        )
        | (count_liquidity >= 2)
        | event_3
    )
    mode_a_positive = quarters_present & event
    mode_a_negative = (
        quarters_present
        & ~event
        & pl.all_horizontal(
            *(
                pl.col(f"_eq_liq_known_{offset}").fill_null(False)
                for offset in range(1, 5)
            ),
            *(
                pl.col(f"_loss_known_{offset}").fill_null(False)
                for offset in range(1, 5)
            ),
        )
    )
    mode_a_avail = pl.max_horizontal(*(pl.col(name) for name in future_available))

    if mode == "REGULATORY_DISTRESS":
        reg_positive = pl.col("_earliest_future_event_date").is_not_null()
        positive_verified = mode_a_positive | reg_positive
        horizon_end = pl.col("_period_available_at") + pl.duration(days=365)
        horizon_complete = (horizon_end <= as_of) if as_of is not None else pl.lit(True)
        negative_verified = mode_a_negative & ~reg_positive & horizon_complete
        labeled = positive_verified | negative_verified
        pos_avail = (
            pl.when(mode_a_positive & reg_positive)
            .then(
                pl.min_horizontal(mode_a_avail, pl.col("_earliest_future_event_date"))
            )
            .when(reg_positive)
            .then(pl.col("_earliest_future_event_date"))
            .otherwise(mode_a_avail)
        )
        neg_avail = pl.max_horizontal(mode_a_avail, horizon_end)
        label_available = (
            pl.when(positive_verified)
            .then(pos_avail)
            .when(negative_verified)
            .then(neg_avail)
            .otherwise(pl.lit(None, dtype=pl.Date))
        )
    else:
        positive_verified = mode_a_positive
        negative_verified = mode_a_negative
        labeled = positive_verified | negative_verified
        label_available = (
            pl.when(labeled).then(mode_a_avail).otherwise(pl.lit(None, dtype=pl.Date))
        )

    output_exprs = [
        pl.when(positive_verified)
        .then(pl.lit(1, dtype=pl.Int8))
        .when(negative_verified)
        .then(pl.lit(0, dtype=pl.Int8))
        .otherwise(pl.lit(None, dtype=pl.Int8))
        .alias("distress_target"),
        label_available.alias("label_available_at"),
    ]
    if has_exact_basis:
        all_future_exact = pl.all_horizontal(
            *(
                pl.col(f"_future_exact_{offset}").fill_null(False)
                for offset in range(1, 5)
            )
        )
        exact_expr = (
            (all_future_exact | pl.col("_earliest_future_event_date").is_not_null())
            if mode == "REGULATORY_DISTRESS"
            else all_future_exact
        )
        output_exprs.append(
            pl.when(labeled)
            .then(exact_expr)
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
            *reg_temp_cols,
            *future_temp_cols,
        )
        .sort(["issuer_id", "statement_scope", "period_end_date"])
    )
