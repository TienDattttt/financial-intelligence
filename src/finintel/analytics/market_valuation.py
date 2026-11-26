"""Point-in-time market valuation and Price & Risk Event Overlay engine (UI-09b / B-06).

Verified Sources:
    docs/refactor/03_FRONTEND_UX_AND_DIFFERENTIATION_PLAN.md (ADR-UI-03, UI-09, B-06).
    docs/refactor/market_prices_audit_375.md (Port 3 EOD dual raw/adj price audit).
    src/finintel/ports/market_prices.py (align_events_to_market_prices_pit, strategy="forward").
    https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join_asof.html
    Circular 96/2020/TT-BTC (Vietnam listed corporate disclosure timing & PIT cutoffs).

Pure Polars analytics layer computing:
1. Point-in-time ``market_cap_vnd``, ``pe_ttm``, and ``pb_ratio`` strictly from
   unadjusted ``close_raw`` and PIT shares outstanding (``estimated_shares_outstanding``
   with ``B-06`` null-treasury guard, falling back to ``shares_outstanding_market``
   with explicit ``shares_source`` provenance). Financial disclosures are forward-aligned
   to trading sessions via ``align_events_to_market_prices_pit(..., strategy="forward")``
   so weekend/holiday disclosures match the following trading day with 0% look-ahead.
2. Structured Price & Risk Event Overlay payloads from split-adjusted ``close_adj``,
   combining PIT model upgrade markers (``R4`` / ``R5``), real regulatory event markers
   (``regulatory_events``), and post-warning drawdown analytics across ``T+30/90/180/365d``
   as well as up to regulatory event dates and current dates.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, TypedDict, cast

import polars as pl

from finintel.analytics.intermediates import SHARES, TREASURY_SHARES
from finintel.analytics.merton_dtd import compute_merton_dtd_series
from finintel.exceptions import ContractViolationError
from finintel.ports.market_prices import (
    align_events_to_market_prices_pit,
    validate_market_prices,
)
from finintel.ports.regulatory import validate_regulatory_events

ValuationFlag = Literal[
    "VALID",
    "NEGATIVE_EARNINGS",
    "NEGATIVE_EQUITY",
    "MISSING_EARNINGS",
    "MISSING_EQUITY",
    "MISSING_SHARES",
]

DrawdownHorizonKey = Literal["T+30d", "T+90d", "T+180d", "T+365d"]
_HORIZON_DAYS: tuple[tuple[DrawdownHorizonKey, int], ...] = (
    ("T+30d", 30),
    ("T+90d", 90),
    ("T+180d", 180),
    ("T+365d", 365),
)
_HIGH_RISK_TIERS: frozenset[str] = frozenset({"R4", "R5"})
_TIER_RANK: dict[str, int] = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}


class DrawdownWindowMetrics(TypedDict):
    """Peak/trough prices and percentage drawdowns over a post-warning window."""

    start_trade_date: date
    end_trade_date: date
    target_end_date: date
    session_count: int
    base_close_adj: float
    peak_close_adj: float
    peak_date: date
    trough_close_adj: float
    trough_date: date
    end_close_adj: float
    return_pct: float
    drawdown_from_marker_pct: float
    max_peak_to_trough_drawdown_pct: float


class MarkerDrawdownBundle(TypedDict):
    """Post-warning drawdown metrics across standard horizons and cutoff bounds."""

    horizons: dict[DrawdownHorizonKey, DrawdownWindowMetrics | None]
    to_first_regulatory_event: DrawdownWindowMetrics | None
    to_current: DrawdownWindowMetrics | None


class ModelRiskMarker(TypedDict):
    """PIT marker for first model upgrade to R4 or R5."""

    marker_type: Literal["MODEL_RISK_TIER"]
    marker_kind: Literal["FIRST_R4_UPGRADE", "FIRST_R5_UPGRADE"]
    risk_tier: Literal["R4", "R5"]
    is_first_r4_or_r5: bool
    disclosure_date: date
    date_basis: Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"]
    aligned_trade_date: date | None
    period_end_date: date
    close_adj_at_marker: float | None
    close_raw_at_marker: float | None
    calibrated_distress_prob: float | None
    risk_market_percentile: float | None
    relative_risk_lift: float | None
    drawdowns: MarkerDrawdownBundle | None


class RegulatoryEventMarker(TypedDict):
    """PIT marker for a real regulatory distress or audit sanction event."""

    marker_type: Literal["REGULATORY_EVENT"]
    event_type: str
    event_stage: str
    event_date: date
    effective_from: date
    resolution_date: date | None
    aligned_trade_date: date | None
    close_adj_at_marker: float | None
    close_raw_at_marker: float | None
    raw_headline: str
    source_provider: str
    source_url: str
    drawdowns: MarkerDrawdownBundle | None


class PriceOverlayPoint(TypedDict):
    """Single daily point on the continuous Price & Risk Event Overlay series."""

    trade_date: date
    close_adj: float
    close_raw: float
    volume: float
    shares_outstanding_market: float | None
    running_drawdown_pct: float
    drawdown_from_first_warning_pct: float | None
    market_cap_vnd: float | None
    pe_ttm: float | None
    pb_ratio: float | None
    pe_flag: str | None
    pb_flag: str | None
    shares_source: str | None
    merton_dtd: float | None
    equity_volatility_1y: float | None
    naive_edf: float | None
    dtd_flag: str | None


class WarningSummary(TypedDict):
    """Summary anchored at the earliest warning marker (model R4/R5 or regulatory)."""

    warning_source: Literal["MODEL_RISK_TIER", "REGULATORY_EVENT"]
    warning_label: str
    warning_date: date
    aligned_trade_date: date | None
    base_close_adj: float | None
    latest_trade_date: date | None
    latest_close_adj: float | None
    horizons: dict[DrawdownHorizonKey, DrawdownWindowMetrics | None]
    to_first_regulatory_event: DrawdownWindowMetrics | None
    to_current: DrawdownWindowMetrics | None


class PriceRiskOverlayPayload(TypedDict):
    """Structured payload returned by ``build_price_risk_overlay``."""

    issuer_id: str
    ticker: str
    as_of: date | None
    price_series: list[PriceOverlayPoint]
    model_markers: list[ModelRiskMarker]
    regulatory_markers: list[RegulatoryEventMarker]
    earliest_warning_summary: WarningSummary | None


def _resolve_disclosure_date_expr(
    frame: pl.DataFrame,
    *,
    available_date_col: str | None,
) -> tuple[pl.Expr, Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"]]:
    """Resolve the PIT disclosure date expression without look-ahead."""
    if available_date_col is not None:
        if available_date_col not in frame.columns:
            raise ContractViolationError(
                f"Financial periods table is missing requested date column {available_date_col!r}."
            )
        if frame.schema[available_date_col] != pl.Date:
            raise ContractViolationError(
                f"Disclosure date column {available_date_col!r} must have Polars Date dtype."
            )
        basis: Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"] = (
            "PERIOD_END_FALLBACK"
            if available_date_col == "period_end_date"
            else "PIT_DISCLOSURE"
        )
        return pl.col(available_date_col), basis

    for candidate in (
        "available_at",
        "effective_available_at",
        "_period_available_at",
        "_amounts_available_at",
    ):
        if candidate in frame.columns and "published_at" not in frame.columns:
            if frame.schema[candidate] != pl.Date:
                raise ContractViolationError(
                    f"Disclosure date column {candidate!r} must have Polars Date dtype."
                )
            return pl.col(candidate), "PIT_DISCLOSURE"

    if "available_at" in frame.columns:
        if frame.schema["available_at"] != pl.Date:
            raise ContractViolationError(
                "Disclosure date column 'available_at' must have Polars Date dtype."
            )
        return pl.col("available_at"), "PIT_DISCLOSURE"

    if "published_at" in frame.columns:
        if frame.schema["published_at"] not in (pl.Date, pl.Null):
            raise ContractViolationError(
                "Disclosure date column 'published_at' must have Polars Date dtype."
            )
        fallbacks = [
            pl.col(name)
            for name in (
                "effective_available_at",
                "_period_available_at",
                "_amounts_available_at",
            )
            if name in frame.columns and frame.schema[name] == pl.Date
        ]
        if fallbacks:
            return pl.coalesce("published_at", *fallbacks), "PIT_DISCLOSURE"
        return pl.col("published_at").cast(pl.Date), "PIT_DISCLOSURE"

    raise ContractViolationError(
        "Financial periods table must contain a PIT disclosure Date column "
        "('available_at', 'published_at', 'effective_available_at', or '_period_available_at')."
    )


def _resolve_optional_float_expr(
    frame: pl.DataFrame,
    candidates: tuple[str, ...],
) -> pl.Expr:
    for col_name in candidates:
        if col_name in frame.columns:
            return pl.col(col_name).cast(pl.Float64)
    return pl.lit(None, dtype=pl.Float64)


def _resolve_b06_estimated_shares_exprs(
    frame: pl.DataFrame,
) -> tuple[pl.Expr, pl.Expr]:
    """Return ``(_est_shares, _est_shares_source)`` honoring B-06 null-treasury rules."""
    has_est = "estimated_shares_outstanding" in frame.columns
    has_raw_par = SHARES in frame.columns
    has_treasury = TREASURY_SHARES in frame.columns
    has_source = "shares_source" in frame.columns

    if has_est:
        est_shares = (
            pl.when(
                pl.col("estimated_shares_outstanding").is_finite()
                & (pl.col("estimated_shares_outstanding") > 0.0)
            )
            .then(pl.col("estimated_shares_outstanding").cast(pl.Float64))
            .otherwise(pl.lit(None, dtype=pl.Float64))
        )
        if has_source:
            source_expr = (
                pl.when(est_shares.is_not_null())
                .then(
                    pl.coalesce(
                        pl.col("shares_source").cast(pl.String),
                        pl.lit("ESTIMATED_SHARES_OUTSTANDING"),
                    )
                )
                .otherwise(pl.lit(None, dtype=pl.String))
            )
        else:
            source_expr = (
                pl.when(est_shares.is_not_null())
                .then(pl.lit("ESTIMATED_SHARES_OUTSTANDING"))
                .otherwise(pl.lit(None, dtype=pl.String))
            )
        return est_shares, source_expr

    if has_raw_par:
        treasury = (
            pl.col(TREASURY_SHARES).cast(pl.Float64)
            if has_treasury
            else pl.lit(None, dtype=pl.Float64)
        )
        par_valid = (pl.col(SHARES).cast(pl.Float64) > 0.0) & (
            treasury.fill_null(0.0) == 0.0
        )
        est_shares = (
            pl.when(par_valid)
            .then(pl.col(SHARES).cast(pl.Float64) / 10000.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
        )
        source_expr = (
            pl.when(par_valid & treasury.is_not_null())
            .then(pl.lit("BS_ORDINARY_SHARES_EXPLICIT_ZERO_TREASURY"))
            .when(par_valid & treasury.is_null())
            .then(pl.lit("BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO"))
            .otherwise(pl.lit(None, dtype=pl.String))
        )
        return est_shares, source_expr

    return pl.lit(None, dtype=pl.Float64), pl.lit(None, dtype=pl.String)


def _empty_valuation_series() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "issuer_id": pl.String,
            "ticker": pl.String,
            "trade_date": pl.Date,
            "close_raw": pl.Float64,
            "close_adj": pl.Float64,
            "volume": pl.Float64,
            "source_provider": pl.String,
            "disclosure_available_at": pl.Date,
            "effective_trade_date": pl.Date,
            "period_end_date": pl.Date,
            "fiscal_year": pl.Int32,
            "fiscal_quarter": pl.Int32,
            "estimated_shares_outstanding": pl.Float64,
            "shares_outstanding_market": pl.Float64,
            "pit_shares_outstanding": pl.Float64,
            "shares_source": pl.String,
            "is_net_income_parent_ttm": pl.Float64,
            "parent_equity": pl.Float64,
            "market_cap_vnd": pl.Float64,
            "market_cap_flag": pl.String,
            "pe_ttm": pl.Float64,
            "pe_flag": pl.String,
            "pb_ratio": pl.Float64,
            "pb_flag": pl.String,
        }
    )


def compute_pit_valuation_series(
    market_prices: pl.DataFrame,
    financial_periods: pl.DataFrame,
    *,
    ticker: str | None = None,
    issuer_id: str | None = None,
    statement_scope: str = "CONSOLIDATED",
    period_type: str | None = "QUARTER",
    available_date_col: str | None = None,
) -> pl.DataFrame:
    """Compute PIT daily ``market_cap_vnd``, ``pe_ttm``, and ``pb_ratio`` in pure Polars.

    Uses ``align_events_to_market_prices_pit(..., strategy="forward")`` from
    ``finintel.ports.market_prices`` to map each financial disclosure date onto its
    first live trading session (0% look-ahead onto prior Friday sessions for weekend
    disclosures), then carries active disclosures forward across subsequent sessions.

    All historical Market Cap, P/E, and P/B valuations are computed strictly from
    unadjusted ``close_raw`` (never ``close_adj``) and PIT shares outstanding
    (``estimated_shares_outstanding`` falling back to ``shares_outstanding_market``
    with explicit ``shares_source`` provenance). Non-positive earnings, equity, or
    missing shares yield ``None`` with explicit reason flags (``NEGATIVE_EARNINGS``,
    ``NEGATIVE_EQUITY``, ``MISSING_SHARES``).
    """
    prices = validate_market_prices(market_prices)
    if ticker is not None:
        clean_ticker = ticker.strip().upper()
        prices = prices.filter(pl.col("ticker") == clean_ticker)
    if issuer_id is not None:
        clean_issuer = issuer_id.strip()
        prices = prices.filter(pl.col("issuer_id") == clean_issuer)

    if prices.is_empty():
        return _empty_valuation_series()

    if "issuer_id" not in financial_periods.columns:
        raise ContractViolationError(
            "Financial periods table must contain 'issuer_id' for PIT valuation."
        )

    periods = financial_periods
    if issuer_id is not None:
        periods = periods.filter(pl.col("issuer_id") == issuer_id.strip())
    elif ticker is not None:
        valid_issuers = prices["issuer_id"].unique().to_list()
        periods = periods.filter(pl.col("issuer_id").is_in(valid_issuers))

    if "statement_scope" in periods.columns:
        periods = periods.filter(pl.col("statement_scope") == statement_scope)
    if period_type is not None and "period_type" in periods.columns:
        periods = periods.filter(pl.col("period_type") == period_type)

    disclosure_expr, _ = _resolve_disclosure_date_expr(
        periods, available_date_col=available_date_col
    )
    est_shares_expr, est_source_expr = _resolve_b06_estimated_shares_exprs(periods)
    ni_expr = _resolve_optional_float_expr(
        periods,
        ("IS_NET_INCOME_PARENT_TTM", "IS_NET_INCOME_PARENT", "net_income_parent_ttm"),
    )
    equity_expr = _resolve_optional_float_expr(periods, ("parent_equity",))
    period_end_expr = (
        pl.col("period_end_date").cast(pl.Date)
        if "period_end_date" in periods.columns
        else pl.lit(None, dtype=pl.Date)
    )
    fiscal_year_expr = (
        pl.col("fiscal_year").cast(pl.Int32)
        if "fiscal_year" in periods.columns
        else pl.lit(None, dtype=pl.Int32)
    )
    fiscal_quarter_expr = (
        pl.col("fiscal_quarter").cast(pl.Int32)
        if "fiscal_quarter" in periods.columns
        else pl.lit(None, dtype=pl.Int32)
    )

    prepared_filings = (
        periods.select(
            pl.col("issuer_id").cast(pl.String).alias("issuer_id"),
            disclosure_expr.alias("_disclosure_available_at"),
            period_end_expr.alias("period_end_date"),
            fiscal_year_expr.alias("fiscal_year"),
            fiscal_quarter_expr.alias("fiscal_quarter"),
            est_shares_expr.alias("_bctc_estimated_shares"),
            est_source_expr.alias("_bctc_shares_source"),
            ni_expr.alias("is_net_income_parent_ttm"),
            equity_expr.alias("parent_equity"),
        )
        .filter(pl.col("_disclosure_available_at").is_not_null())
        .sort(["issuer_id", "_disclosure_available_at", "period_end_date"])
    )

    prices_sorted = prices.sort(["issuer_id", "trade_date"])

    if prepared_filings.is_empty():
        joined = prices_sorted.with_columns(
            pl.lit(None, dtype=pl.Date).alias("disclosure_available_at"),
            pl.lit(None, dtype=pl.Date).alias("effective_trade_date"),
            pl.lit(None, dtype=pl.Date).alias("period_end_date"),
            pl.lit(None, dtype=pl.Int32).alias("fiscal_year"),
            pl.lit(None, dtype=pl.Int32).alias("fiscal_quarter"),
            pl.lit(None, dtype=pl.Float64).alias("_bctc_estimated_shares"),
            pl.lit(None, dtype=pl.String).alias("_bctc_shares_source"),
            pl.lit(None, dtype=pl.Float64).alias("is_net_income_parent_ttm"),
            pl.lit(None, dtype=pl.Float64).alias("parent_equity"),
        )
    else:
        # Step 1: Forward-align each disclosure date onto its first live trading session
        # via Port 3 helper (join_asof strategy="forward") so weekend disclosures land on
        # the following Monday and never leak back to Friday.
        forward_aligned = (
            align_events_to_market_prices_pit(
                prepared_filings,
                prices_sorted,
                event_date_col="_disclosure_available_at",
            )
            .filter(pl.col("trade_date").is_not_null())
            .rename(
                {
                    "_disclosure_available_at": "disclosure_available_at",
                    "trade_date": "effective_trade_date",
                }
            )
            .sort(
                [
                    "issuer_id",
                    "effective_trade_date",
                    "disclosure_available_at",
                    "period_end_date",
                ]
            )
            .unique(subset=["issuer_id", "effective_trade_date"], keep="last")
            .select(
                "issuer_id",
                "effective_trade_date",
                "disclosure_available_at",
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "_bctc_estimated_shares",
                "_bctc_shares_source",
                "is_net_income_parent_ttm",
                "parent_equity",
            )
        )

        if forward_aligned.is_empty():
            joined = prices_sorted.with_columns(
                pl.lit(None, dtype=pl.Date).alias("disclosure_available_at"),
                pl.lit(None, dtype=pl.Date).alias("effective_trade_date"),
                pl.lit(None, dtype=pl.Date).alias("period_end_date"),
                pl.lit(None, dtype=pl.Int32).alias("fiscal_year"),
                pl.lit(None, dtype=pl.Int32).alias("fiscal_quarter"),
                pl.lit(None, dtype=pl.Float64).alias("_bctc_estimated_shares"),
                pl.lit(None, dtype=pl.String).alias("_bctc_shares_source"),
                pl.lit(None, dtype=pl.Float64).alias("is_net_income_parent_ttm"),
                pl.lit(None, dtype=pl.Float64).alias("parent_equity"),
            )
        else:
            # Step 2: Carry each forward-aligned disclosure across daily trading bars
            # until the next disclosure's effective_trade_date, bounded at 180d (P2-6).
            joined = prices_sorted.join_asof(
                forward_aligned.sort(["issuer_id", "effective_trade_date"]),
                left_on="trade_date",
                right_on="effective_trade_date",
                by="issuer_id",
                strategy="backward",
                tolerance="180d",
                check_sortedness=False,
            )

    c = pl.col
    bctc_shares = (
        pl.when(
            c("_bctc_estimated_shares").is_finite()
            & (c("_bctc_estimated_shares") > 0.0)
        )
        .then(c("_bctc_estimated_shares"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    market_shares = (
        pl.when(
            c("shares_outstanding_market").is_finite()
            & (c("shares_outstanding_market") > 0.0)
        )
        .then(c("shares_outstanding_market"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    # Use market shares fallback ONLY when BCTC estimated shares are missing,
    # unless bctc shares are unavailable because shares_source is SHARES_OUTSTANDING_MARKET.
    bctc_not_market = (
        c("_bctc_shares_source").fill_null("") != "SHARES_OUTSTANDING_MARKET"
    )
    primary_bctc_shares = pl.when(bctc_not_market).then(bctc_shares).otherwise(None)
    pit_shares = pl.coalesce(primary_bctc_shares, market_shares, bctc_shares)

    shares_source = (
        pl.when(primary_bctc_shares.is_not_null())
        .then(c("_bctc_shares_source"))
        .when(market_shares.is_not_null())
        .then(pl.lit("SHARES_OUTSTANDING_MARKET"))
        .when(bctc_shares.is_not_null())
        .then(c("_bctc_shares_source"))
        .otherwise(pl.lit("MISSING_SHARES"))
    )

    staged = joined.with_columns(
        bctc_shares.alias("estimated_shares_outstanding"),
        pit_shares.alias("pit_shares_outstanding"),
        shares_source.alias("shares_source"),
    )

    # Strictly compute market cap, P/E, and P/B from unadjusted close_raw (B-06).
    market_cap_raw = c("close_raw") * c("pit_shares_outstanding")
    has_shares = c("pit_shares_outstanding").is_not_null() & (
        c("pit_shares_outstanding") > 0.0
    )
    ni = c("is_net_income_parent_ttm")
    pe_flag = (
        pl.when(~has_shares)
        .then(pl.lit("MISSING_SHARES"))
        .when(ni.is_null() | ~ni.is_finite())
        .then(pl.lit("MISSING_EARNINGS"))
        .when(ni <= 0.0)
        .then(pl.lit("NEGATIVE_EARNINGS"))
        .otherwise(pl.lit("VALID"))
    )
    eq = c("parent_equity")
    pb_flag = (
        pl.when(~has_shares)
        .then(pl.lit("MISSING_SHARES"))
        .when(eq.is_null() | ~eq.is_finite())
        .then(pl.lit("MISSING_EQUITY"))
        .when(eq <= 0.0)
        .then(pl.lit("NEGATIVE_EQUITY"))
        .otherwise(pl.lit("VALID"))
    )
    mcap_flag = (
        pl.when(has_shares & market_cap_raw.is_finite())
        .then(pl.lit("VALID"))
        .otherwise(pl.lit("MISSING_SHARES"))
    )

    return (
        staged.with_columns(
            pl.when(mcap_flag == "VALID")
            .then(market_cap_raw)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("market_cap_vnd"),
            mcap_flag.alias("market_cap_flag"),
            pl.when(pe_flag == "VALID")
            .then(market_cap_raw / ni)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("pe_ttm"),
            pe_flag.alias("pe_flag"),
            pl.when(pb_flag == "VALID")
            .then(market_cap_raw / eq)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("pb_ratio"),
            pb_flag.alias("pb_flag"),
        )
        .select(
            "issuer_id",
            "ticker",
            "trade_date",
            "close_raw",
            "close_adj",
            "volume",
            "source_provider",
            "disclosure_available_at",
            "effective_trade_date",
            "period_end_date",
            "fiscal_year",
            "fiscal_quarter",
            "estimated_shares_outstanding",
            "shares_outstanding_market",
            "pit_shares_outstanding",
            "shares_source",
            "is_net_income_parent_ttm",
            "parent_equity",
            "market_cap_vnd",
            "market_cap_flag",
            "pe_ttm",
            "pe_flag",
            "pb_ratio",
            "pb_flag",
        )
        .sort(["issuer_id", "trade_date"])
    )


def _compute_window_drawdown(
    prices: pl.DataFrame,
    *,
    start_trade_date: date,
    end_bound_date: date,
    target_end_date: date,
) -> DrawdownWindowMetrics | None:
    """Compute peak/trough/return/drawdown metrics over ``[start_trade_date, end_bound_date]`` using ``close_adj``."""
    window = (
        prices.filter(
            (pl.col("trade_date") >= start_trade_date)
            & (pl.col("trade_date") <= end_bound_date)
        )
        .sort("trade_date")
        .with_columns(
            ((pl.col("close_adj") / pl.col("close_adj").cum_max() - 1.0) * 100.0).alias(
                "_p2t_dd"
            )
        )
    )
    if window.is_empty():
        return None
    base_row = window.row(0, named=True)
    end_row = window.row(-1, named=True)
    base_price = float(base_row["close_adj"])
    end_price = float(end_row["close_adj"])
    peak_row = window.sort(["close_adj", "trade_date"], descending=[True, False]).row(
        0, named=True
    )
    trough_row = window.sort(
        ["close_adj", "trade_date"], descending=[False, False]
    ).row(0, named=True)
    peak_price = float(peak_row["close_adj"])
    trough_price = float(trough_row["close_adj"])
    max_p2t_raw = window["_p2t_dd"].min()
    max_p2t = float(cast(float, max_p2t_raw)) if max_p2t_raw is not None else 0.0
    return {
        "start_trade_date": cast(date, base_row["trade_date"]),
        "end_trade_date": cast(date, end_row["trade_date"]),
        "target_end_date": target_end_date,
        "session_count": window.height,
        "base_close_adj": base_price,
        "peak_close_adj": peak_price,
        "peak_date": cast(date, peak_row["trade_date"]),
        "trough_close_adj": trough_price,
        "trough_date": cast(date, trough_row["trade_date"]),
        "end_close_adj": end_price,
        "return_pct": (end_price / base_price - 1.0) * 100.0,
        "drawdown_from_marker_pct": (trough_price / base_price - 1.0) * 100.0,
        "max_peak_to_trough_drawdown_pct": max_p2t,
    }


def _compute_marker_drawdown_bundle(
    prices: pl.DataFrame,
    *,
    aligned_trade_date: date | None,
    first_reg_trade_date: date | None,
) -> MarkerDrawdownBundle | None:
    if aligned_trade_date is None or prices.is_empty():
        return None
    latest_date = cast(date, prices["trade_date"].max())
    if aligned_trade_date > latest_date:
        return None

    horizons: dict[DrawdownHorizonKey, DrawdownWindowMetrics | None] = {}
    for label, days in _HORIZON_DAYS:
        target_end = aligned_trade_date + timedelta(days=days)
        horizons[label] = _compute_window_drawdown(
            prices,
            start_trade_date=aligned_trade_date,
            end_bound_date=min(target_end, latest_date),
            target_end_date=target_end,
        )

    to_first_reg: DrawdownWindowMetrics | None = None
    if first_reg_trade_date is not None and first_reg_trade_date >= aligned_trade_date:
        to_first_reg = _compute_window_drawdown(
            prices,
            start_trade_date=aligned_trade_date,
            end_bound_date=first_reg_trade_date,
            target_end_date=first_reg_trade_date,
        )

    to_current = _compute_window_drawdown(
        prices,
        start_trade_date=aligned_trade_date,
        end_bound_date=latest_date,
        target_end_date=latest_date,
    )
    return {
        "horizons": horizons,
        "to_first_regulatory_event": to_first_reg,
        "to_current": to_current,
    }


def _extract_first_r4_r5_markers(
    ml_scores: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    target_issuer_id: str,
    statement_scope: str,
    financial_periods: pl.DataFrame | None,
    as_of: date | None,
) -> list[dict[str, object]]:
    """Extract first PIT model upgrades to R4 and R5 aligned onto ``prices``."""
    if ml_scores.is_empty() or "risk_tier" not in ml_scores.columns:
        return []
    if (
        "issuer_id" not in ml_scores.columns
        or "period_end_date" not in ml_scores.columns
    ):
        raise ContractViolationError(
            "ml_scores must contain 'issuer_id' and 'period_end_date' for overlay extraction."
        )

    scoped = ml_scores.filter(pl.col("issuer_id") == target_issuer_id)
    if "statement_scope" in scoped.columns:
        scoped = scoped.filter(pl.col("statement_scope") == statement_scope)
    if "period_type" in scoped.columns:
        quarterly = scoped.filter(pl.col("period_type") == "QUARTER")
        if not quarterly.is_empty():
            scoped = quarterly
    if "model_role" in scoped.columns:
        scoped = scoped.filter(
            pl.col("model_role").cast(pl.String).str.starts_with("OUT_OF_SAMPLE")
        )
    if scoped.is_empty():
        return []

    date_basis: Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"]
    if any(
        col in scoped.columns
        for col in (
            "available_at",
            "published_at",
            "effective_available_at",
            "_period_available_at",
        )
    ):
        disc_expr, date_basis = _resolve_disclosure_date_expr(
            scoped, available_date_col=None
        )
        with_dates = scoped.with_columns(
            disc_expr.alias("_disclosure_date"),
            pl.lit(date_basis).alias("_date_basis"),
        )
    elif financial_periods is not None and not financial_periods.is_empty():
        fp = financial_periods.filter(pl.col("issuer_id") == target_issuer_id)
        if "statement_scope" in fp.columns:
            fp = fp.filter(pl.col("statement_scope") == statement_scope)
        fp_disc_expr, date_basis = _resolve_disclosure_date_expr(
            fp, available_date_col=None
        )
        fp_dates = (
            fp.select(
                pl.col("issuer_id").cast(pl.String),
                pl.col("period_end_date").cast(pl.Date),
                fp_disc_expr.alias("_disclosure_date"),
            )
            .filter(pl.col("_disclosure_date").is_not_null())
            .sort(["issuer_id", "period_end_date", "_disclosure_date"])
            .unique(subset=["issuer_id", "period_end_date"], keep="last")
        )
        with_dates = scoped.with_columns(pl.col("period_end_date").cast(pl.Date)).join(
            fp_dates, on=["issuer_id", "period_end_date"], how="left"
        )
        with_dates = with_dates.with_columns(
            pl.when(pl.col("_disclosure_date").is_not_null())
            .then(pl.lit(date_basis))
            .otherwise(pl.lit("PERIOD_END_FALLBACK"))
            .alias("_date_basis"),
            pl.coalesce("_disclosure_date", "period_end_date").alias(
                "_disclosure_date"
            ),
        )
    else:
        with_dates = scoped.with_columns(
            pl.col("period_end_date").cast(pl.Date).alias("_disclosure_date"),
            pl.lit("PERIOD_END_FALLBACK").alias("_date_basis"),
        )

    if as_of is not None:
        with_dates = with_dates.filter(pl.col("_disclosure_date") <= as_of)

    with_dates = with_dates.filter(
        pl.col("_disclosure_date").is_not_null() & pl.col("risk_tier").is_not_null()
    ).sort(["_disclosure_date", "period_end_date"])

    if with_dates.is_empty():
        return []

    selected_rows: list[dict[str, object]] = []
    seen_r4 = False
    seen_r5 = False
    seen_any_high = False
    max_rank_seen = 0

    for row in with_dates.iter_rows(named=True):
        tier = str(row["risk_tier"])
        rank = _TIER_RANK.get(tier, 0)
        if rank == 0:
            continue
        if tier == "R4" and not seen_r4 and max_rank_seen < 4:
            seen_r4 = True
            is_first = not seen_any_high
            seen_any_high = True
            selected_rows.append(
                {
                    "issuer_id": target_issuer_id,
                    "marker_kind": "FIRST_R4_UPGRADE",
                    "risk_tier": "R4",
                    "is_first_r4_or_r5": is_first,
                    "disclosure_date": row["_disclosure_date"],
                    "date_basis": row["_date_basis"],
                    "period_end_date": row["period_end_date"],
                    "calibrated_distress_prob": row.get("calibrated_distress_prob"),
                    "risk_market_percentile": row.get("risk_market_percentile"),
                    "relative_risk_lift": row.get("relative_risk_lift"),
                }
            )
        elif tier == "R5" and not seen_r5 and max_rank_seen < 5:
            seen_r5 = True
            is_first = not seen_any_high
            seen_any_high = True
            selected_rows.append(
                {
                    "issuer_id": target_issuer_id,
                    "marker_kind": "FIRST_R5_UPGRADE",
                    "risk_tier": "R5",
                    "is_first_r4_or_r5": is_first,
                    "disclosure_date": row["_disclosure_date"],
                    "date_basis": row["_date_basis"],
                    "period_end_date": row["period_end_date"],
                    "calibrated_distress_prob": row.get("calibrated_distress_prob"),
                    "risk_market_percentile": row.get("risk_market_percentile"),
                    "relative_risk_lift": row.get("relative_risk_lift"),
                }
            )
        if rank > max_rank_seen:
            max_rank_seen = rank

    if not selected_rows:
        return []

    events_df = pl.DataFrame(
        selected_rows,
        schema_overrides={
            "issuer_id": pl.String,
            "disclosure_date": pl.Date,
            "period_end_date": pl.Date,
            "calibrated_distress_prob": pl.Float64,
            "risk_market_percentile": pl.Float64,
            "relative_risk_lift": pl.Float64,
        },
    )
    aligned = align_events_to_market_prices_pit(
        events_df,
        prices,
        event_date_col="disclosure_date",
    ).sort(["disclosure_date", "period_end_date"])
    return aligned.to_dicts()


def build_price_risk_overlay(
    market_prices: pl.DataFrame,
    *,
    ticker: str | None = None,
    issuer_id: str | None = None,
    ml_scores: pl.DataFrame | None = None,
    regulatory_events: pl.DataFrame | None = None,
    financial_periods: pl.DataFrame | None = None,
    dtd_financial_data: pl.DataFrame | None = None,
    valuation_series: pl.DataFrame | None = None,
    statement_scope: str = "CONSOLIDATED",
    as_of: date | None = None,
) -> PriceRiskOverlayPayload:
    """Build structured Price & Risk Event Overlay payload for a corporate issuer.

    Produces:
    - Continuous split-adjusted price series from ``close_adj`` (and ``close_raw``),
      plus optional PIT valuation columns if ``valuation_series`` or
      ``financial_periods`` is provided.
    - PIT markers for first model upgrades to ``R4`` / ``R5`` (forward-aligned).
    - PIT markers for real regulatory warning / control / restriction / delisting
      events (forward-aligned via ``align_events_to_market_prices_pit``).
    - Post-warning ``% drawdown`` analytics at ``T+30/90/180/365d``, up to the first
      regulatory event date, and up to the current/latest date.
    """
    prices = validate_market_prices(market_prices)
    if as_of is not None:
        prices = prices.filter(pl.col("trade_date") <= as_of)
    if ticker is not None:
        clean_ticker = ticker.strip().upper()
        prices = prices.filter(pl.col("ticker") == clean_ticker)
    if issuer_id is not None:
        clean_issuer = issuer_id.strip()
        prices = prices.filter(pl.col("issuer_id") == clean_issuer)

    unique_issuers = prices["issuer_id"].unique().to_list()
    if len(unique_issuers) == 0:
        raise ContractViolationError(
            "No market price rows found for the requested ticker/issuer_id."
        )
    if len(unique_issuers) > 1:
        raise ContractViolationError(
            "build_price_risk_overlay requires a single issuer; pass ticker or issuer_id."
        )

    target_issuer_id = str(unique_issuers[0])
    target_ticker = str(prices["ticker"].drop_nulls().item(0))
    prices_sorted = prices.sort("trade_date")

    # 1. Regulatory event markers (PIT forward-aligned onto trading sessions).
    raw_reg_rows: list[dict[str, object]] = []
    if regulatory_events is not None and not regulatory_events.is_empty():
        validated_reg = validate_regulatory_events(regulatory_events).filter(
            pl.col("issuer_id") == target_issuer_id
        )
        if as_of is not None:
            validated_reg = validated_reg.filter(pl.col("event_date") <= as_of)
        if not validated_reg.is_empty():
            aligned_reg = align_events_to_market_prices_pit(
                validated_reg,
                prices_sorted,
                event_date_col="event_date",
            ).sort(["event_date", "effective_from", "event_type"])
            raw_reg_rows = aligned_reg.to_dicts()

    reg_trade_dates = [
        cast(date, r["trade_date"])
        for r in raw_reg_rows
        if r.get("trade_date") is not None
    ]
    first_reg_trade_date: date | None = (
        min(reg_trade_dates) if reg_trade_dates else None
    )

    regulatory_markers: list[RegulatoryEventMarker] = []
    for row in raw_reg_rows:
        aligned_td = cast(date | None, row.get("trade_date"))
        bundle = _compute_marker_drawdown_bundle(
            prices_sorted,
            aligned_trade_date=aligned_td,
            first_reg_trade_date=None,
        )
        regulatory_markers.append(
            {
                "marker_type": "REGULATORY_EVENT",
                "event_type": str(row["event_type"]),
                "event_stage": str(row["event_stage"]),
                "event_date": cast(date, row["event_date"]),
                "effective_from": cast(date, row["effective_from"]),
                "resolution_date": cast(date | None, row.get("resolution_date")),
                "aligned_trade_date": aligned_td,
                "close_adj_at_marker": (
                    float(cast(float, row["close_adj"]))
                    if row.get("close_adj") is not None
                    else None
                ),
                "close_raw_at_marker": (
                    float(cast(float, row["close_raw"]))
                    if row.get("close_raw") is not None
                    else None
                ),
                "raw_headline": str(row["raw_headline"]),
                "source_provider": str(row["source_provider"]),
                "source_url": str(row["source_url"]),
                "drawdowns": bundle,
            }
        )

    # 2. Model R4/R5 first-upgrade markers (PIT forward-aligned).
    raw_model_rows: list[dict[str, object]] = []
    if ml_scores is not None and not ml_scores.is_empty():
        raw_model_rows = _extract_first_r4_r5_markers(
            ml_scores,
            prices_sorted,
            target_issuer_id=target_issuer_id,
            statement_scope=statement_scope,
            financial_periods=financial_periods,
            as_of=as_of,
        )

    model_markers: list[ModelRiskMarker] = []
    for row in raw_model_rows:
        aligned_td = cast(date | None, row.get("trade_date"))
        bundle = _compute_marker_drawdown_bundle(
            prices_sorted,
            aligned_trade_date=aligned_td,
            first_reg_trade_date=first_reg_trade_date,
        )
        model_markers.append(
            {
                "marker_type": "MODEL_RISK_TIER",
                "marker_kind": cast(
                    Literal["FIRST_R4_UPGRADE", "FIRST_R5_UPGRADE"],
                    row["marker_kind"],
                ),
                "risk_tier": cast(Literal["R4", "R5"], row["risk_tier"]),
                "is_first_r4_or_r5": bool(row["is_first_r4_or_r5"]),
                "disclosure_date": cast(date, row["disclosure_date"]),
                "date_basis": cast(
                    Literal["PIT_DISCLOSURE", "PERIOD_END_FALLBACK"],
                    row["date_basis"],
                ),
                "aligned_trade_date": aligned_td,
                "period_end_date": cast(date, row["period_end_date"]),
                "close_adj_at_marker": (
                    float(cast(float, row["close_adj"]))
                    if row.get("close_adj") is not None
                    else None
                ),
                "close_raw_at_marker": (
                    float(cast(float, row["close_raw"]))
                    if row.get("close_raw") is not None
                    else None
                ),
                "calibrated_distress_prob": (
                    float(cast(float, row["calibrated_distress_prob"]))
                    if row.get("calibrated_distress_prob") is not None
                    else None
                ),
                "risk_market_percentile": (
                    float(cast(float, row["risk_market_percentile"]))
                    if row.get("risk_market_percentile") is not None
                    else None
                ),
                "relative_risk_lift": (
                    float(cast(float, row["relative_risk_lift"]))
                    if row.get("relative_risk_lift") is not None
                    else None
                ),
                "drawdowns": bundle,
            }
        )

    # 3. Determine earliest warning marker to anchor drawdown_from_first_warning_pct.
    warning_candidates: list[
        tuple[
            date,
            Literal["MODEL_RISK_TIER", "REGULATORY_EVENT"],
            str,
            date | None,
            float | None,
            MarkerDrawdownBundle | None,
        ]
    ] = []
    for mm in model_markers:
        warning_candidates.append(
            (
                mm["aligned_trade_date"] or mm["disclosure_date"],
                "MODEL_RISK_TIER",
                f"Model {mm['risk_tier']}",
                mm["aligned_trade_date"],
                mm["close_adj_at_marker"],
                mm["drawdowns"],
            )
        )
    for rm in regulatory_markers:
        warning_candidates.append(
            (
                rm["aligned_trade_date"] or rm["event_date"],
                "REGULATORY_EVENT",
                rm["event_type"],
                rm["aligned_trade_date"],
                rm["close_adj_at_marker"],
                rm["drawdowns"],
            )
        )

    earliest_warning_summary: WarningSummary | None = None
    first_warn_trade_date: date | None = None
    first_warn_base_price: float | None = None
    if warning_candidates:
        warning_candidates.sort(
            key=lambda item: (item[0], 0 if item[1] == "MODEL_RISK_TIER" else 1)
        )
        w_date, w_source, w_label, w_aligned_td, w_base_price, w_bundle = (
            warning_candidates[0]
        )
        first_warn_trade_date = w_aligned_td
        first_warn_base_price = w_base_price
        latest_row = prices_sorted.row(-1, named=True)
        earliest_warning_summary = {
            "warning_source": w_source,
            "warning_label": w_label,
            "warning_date": w_date,
            "aligned_trade_date": w_aligned_td,
            "base_close_adj": w_base_price,
            "latest_trade_date": cast(date, latest_row["trade_date"]),
            "latest_close_adj": float(latest_row["close_adj"]),
            "horizons": (
                w_bundle["horizons"]
                if w_bundle is not None
                else {key: None for key, _ in _HORIZON_DAYS}
            ),
            "to_first_regulatory_event": (
                w_bundle["to_first_regulatory_event"] if w_bundle is not None else None
            ),
            "to_current": w_bundle["to_current"] if w_bundle is not None else None,
        }

    # 4. Build continuous close_adj price series (optionally enriched with PIT valuations).
    series_df = prices_sorted.with_columns(
        ((pl.col("close_adj") / pl.col("close_adj").cum_max() - 1.0) * 100.0).alias(
            "running_drawdown_pct"
        ),
        (
            pl.when(pl.col("trade_date") >= first_warn_trade_date)
            .then((pl.col("close_adj") / first_warn_base_price - 1.0) * 100.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            if first_warn_trade_date is not None and first_warn_base_price is not None
            else pl.lit(None, dtype=pl.Float64)
        ).alias("drawdown_from_first_warning_pct"),
    )

    val_frame: pl.DataFrame | None = valuation_series
    if val_frame is None and financial_periods is not None:
        val_frame = compute_pit_valuation_series(
            prices_sorted,
            financial_periods,
            issuer_id=target_issuer_id,
            statement_scope=statement_scope,
        )

    if val_frame is not None and not val_frame.is_empty():
        val_scoped = (
            val_frame.filter(pl.col("issuer_id") == target_issuer_id)
            .select(
                "trade_date",
                "market_cap_vnd",
                "pe_ttm",
                "pb_ratio",
                "pe_flag",
                "pb_flag",
                "shares_source",
            )
            .unique(subset=["trade_date"], keep="last")
        )
        series_df = series_df.join(val_scoped, on="trade_date", how="left")
    else:
        series_df = series_df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("market_cap_vnd"),
            pl.lit(None, dtype=pl.Float64).alias("pe_ttm"),
            pl.lit(None, dtype=pl.Float64).alias("pb_ratio"),
            pl.lit(None, dtype=pl.String).alias("pe_flag"),
            pl.lit(None, dtype=pl.String).alias("pb_flag"),
            pl.lit(None, dtype=pl.String).alias("shares_source"),
        )

    dtd_src = (
        dtd_financial_data if dtd_financial_data is not None else financial_periods
    )
    dtd_cols = (
        "trade_date",
        "merton_dtd",
        "equity_volatility_1y",
        "naive_edf",
        "dtd_flag",
    )
    dtd_df = compute_merton_dtd_series(
        prices_sorted,
        dtd_src,
        issuer_id=target_issuer_id,
        statement_scope=statement_scope,
        as_of=as_of,
    ).select(*dtd_cols)
    series_df = series_df.join(dtd_df, on="trade_date", how="left")
    price_series = cast(
        list[PriceOverlayPoint],
        series_df.select(*PriceOverlayPoint.__annotations__)
        .sort("trade_date")
        .to_dicts(),
    )

    return {
        "issuer_id": target_issuer_id,
        "ticker": target_ticker,
        "as_of": as_of,
        "price_series": price_series,
        "model_markers": model_markers,
        "regulatory_markers": regulatory_markers,
        "earliest_warning_summary": earliest_warning_summary,
    }


__all__ = [
    "DrawdownHorizonKey",
    "DrawdownWindowMetrics",
    "MarkerDrawdownBundle",
    "ModelRiskMarker",
    "PriceOverlayPoint",
    "PriceRiskOverlayPayload",
    "RegulatoryEventMarker",
    "ValuationFlag",
    "WarningSummary",
    "build_price_risk_overlay",
    "compute_pit_valuation_series",
]
