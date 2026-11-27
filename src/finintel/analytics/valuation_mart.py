"""Vectorized Point-in-Time valuation mart builder for DuckLake (V3-04).

Verified Sources:
    spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.1)
    src/finintel/ports/market_prices.py (align_events_to_market_prices_pit, strategy="forward")
    https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join_asof.html
    Circular 96/2020/TT-BTC (Vietnam listed corporate disclosure timing & PIT cutoffs)
"""

from __future__ import annotations

from datetime import date

import polars as pl

from finintel.analytics.intermediates import SHARES, TREASURY_SHARES
from finintel.ports.market_prices import (
    align_events_to_market_prices_pit,
    validate_market_prices,
)
from finintel.storage.schemas import (
    PIT_VALUATION_MART_KEY,
    PIT_VALUATION_MART_SCHEMA,
)

_DISCLOSURE_DATE_CANDIDATES: tuple[str, ...] = (
    "effective_available_at",
    "available_at",
    "_period_available_at",
    "_amounts_available_at",
    "published_at",
)


def _empty_pit_valuation_mart() -> pl.DataFrame:
    return pl.DataFrame(schema=PIT_VALUATION_MART_SCHEMA)


def _attach_disclosure_available_at(
    ratios: pl.DataFrame,
    *,
    filings_manifest: pl.DataFrame | None,
    facts: pl.DataFrame | None,
) -> pl.DataFrame:
    """Attach ``_disclosure_available_at`` (Date) without period_end_date look-ahead."""
    direct_cols = [
        pl.col(col).cast(pl.Date)
        for col in _DISCLOSURE_DATE_CANDIDATES
        if col in ratios.columns
    ]
    if direct_cols:
        return ratios.with_columns(
            pl.coalesce(*direct_cols).alias("_disclosure_available_at")
        )

    if (
        "filing_id" in ratios.columns
        and filings_manifest is not None
        and not filings_manifest.is_empty()
        and "filing_id" in filings_manifest.columns
    ):
        m_dates = [
            pl.col(c).cast(pl.Date)
            for c in ("effective_available_at", "published_at")
            if c in filings_manifest.columns
        ]
        if m_dates:
            manifest_lookup = (
                filings_manifest.select(
                    pl.col("filing_id").cast(pl.String).alias("filing_id"),
                    pl.coalesce(*m_dates).alias("_disclosure_available_at"),
                )
                .filter(pl.col("_disclosure_available_at").is_not_null())
                .unique(subset=["filing_id"], keep="last")
            )
            return ratios.join(manifest_lookup, on="filing_id", how="left")

    if (
        "filing_id" in ratios.columns
        and facts is not None
        and not facts.is_empty()
        and "filing_id" in facts.columns
    ):
        f_dates = [
            pl.col(c).cast(pl.Date)
            for c in ("effective_available_at", "published_at")
            if c in facts.columns
        ]
        if f_dates:
            facts_lookup = (
                facts.select(
                    pl.col("filing_id").cast(pl.String).alias("filing_id"),
                    pl.coalesce(*f_dates).alias("_f_avail"),
                )
                .filter(pl.col("_f_avail").is_not_null())
                .group_by("filing_id")
                .agg(pl.col("_f_avail").max().alias("_disclosure_available_at"))
            )
            return ratios.join(facts_lookup, on="filing_id", how="left")

    return ratios.with_columns(
        pl.lit(None, dtype=pl.Date).alias("_disclosure_available_at")
    )


def _resolve_bctc_shares_expr(frame: pl.DataFrame) -> tuple[pl.Expr, pl.Expr]:
    if "estimated_shares_outstanding" in frame.columns:
        raw_est = pl.col("estimated_shares_outstanding").cast(pl.Float64)
        est = (
            pl.when(raw_est.is_finite() & (raw_est > 0.0))
            .then(raw_est)
            .otherwise(pl.lit(None, dtype=pl.Float64))
        )
        src = (
            pl.col("shares_source").cast(pl.String)
            if "shares_source" in frame.columns
            else pl.lit("ESTIMATED_SHARES_OUTSTANDING")
        )
        return est, src
    if SHARES in frame.columns:
        raw_par = pl.col(SHARES).cast(pl.Float64)
        treasury = (
            pl.col(TREASURY_SHARES).cast(pl.Float64)
            if TREASURY_SHARES in frame.columns
            else pl.lit(None, dtype=pl.Float64)
        )
        valid = (
            raw_par.is_finite()
            & (raw_par > 0.0)
            & (treasury.is_null() | (treasury == 0.0))
        )
        return (
            pl.when(valid)
            .then(raw_par / 10_000.0)
            .otherwise(pl.lit(None, dtype=pl.Float64)),
            pl.lit("BS_ORDINARY_SHARES"),
        )
    return pl.lit(None, dtype=pl.Float64), pl.lit(None, dtype=pl.String)


def build_pit_valuation_mart(
    ratios: pl.DataFrame,
    market_prices: pl.DataFrame | None,
    *,
    filings_manifest: pl.DataFrame | None = None,
    facts: pl.DataFrame | None = None,
    as_of: date | None = None,
) -> pl.DataFrame:
    """Build cross-market ``pit_valuation_mart`` using unadjusted ``close_raw`` and forward ``join_asof``.

    Guarantees:
    - Uses ``close_raw`` strictly (never ``close_adj``) for Market Cap, P/E, and P/B.
    - Uses ``align_events_to_market_prices_pit(..., strategy="forward")`` so weekend or
      holiday financial disclosures align to the next live trading session (0% look-ahead).
    - Returns ``NULL`` + explicit flag (``NEGATIVE_DENOMINATOR`` / ``MISSING_PRICE`` /
      ``MISSING_SHARES`` / ``MISSING_INPUT``) without blind zero-filling.
    """
    if ratios.is_empty() or market_prices is None or market_prices.is_empty():
        return _empty_pit_valuation_mart()

    validated_prices = validate_market_prices(market_prices)
    if as_of is not None:
        validated_prices = validated_prices.filter(pl.col("trade_date") <= as_of)
    if validated_prices.is_empty():
        return _empty_pit_valuation_mart()

    sort_key = [*PIT_VALUATION_MART_KEY]
    if "filing_id" in ratios.columns:
        sort_key.append("filing_id")
    base_ratios = (
        ratios.sort(sort_key)
        .unique(subset=list(PIT_VALUATION_MART_KEY), keep="last")
        .with_columns(
            pl.col("issuer_id").cast(pl.String),
            pl.col("ticker").cast(pl.String),
            pl.col("statement_scope").cast(pl.String),
            pl.col("period_type").cast(pl.String),
            pl.col("period_end_date").cast(pl.Date),
        )
    )

    staged = _attach_disclosure_available_at(
        base_ratios,
        filings_manifest=filings_manifest,
        facts=facts,
    )
    if as_of is not None:
        staged = staged.with_columns(
            pl.when(pl.col("_disclosure_available_at") <= as_of)
            .then(pl.col("_disclosure_available_at"))
            .otherwise(pl.lit(None, dtype=pl.Date))
            .alias("_disclosure_available_at")
        )

    alignable = staged.filter(pl.col("_disclosure_available_at").is_not_null()).select(
        *PIT_VALUATION_MART_KEY,
        "_disclosure_available_at",
    )
    if alignable.is_empty():
        aligned_joined = staged.with_columns(
            pl.lit(None, dtype=pl.Date).alias("effective_trade_date"),
            pl.lit(None, dtype=pl.Float64).alias("close_raw_vnd"),
            pl.lit(None, dtype=pl.Float64).alias("_market_shares"),
        )
    else:
        aligned = align_events_to_market_prices_pit(
            alignable,
            validated_prices,
            event_date_col="_disclosure_available_at",
        ).select(
            *PIT_VALUATION_MART_KEY,
            pl.col("trade_date").cast(pl.Date).alias("effective_trade_date"),
            pl.col("close_raw").cast(pl.Float64).alias("close_raw_vnd"),
            pl.col("shares_outstanding_market")
            .cast(pl.Float64)
            .alias("_market_shares"),
        )
        aligned_joined = staged.join(
            aligned,
            on=list(PIT_VALUATION_MART_KEY),
            how="left",
            nulls_equal=True,
            validate="1:1",
        )

    c = pl.col
    bctc_shares_raw, bctc_source_raw = _resolve_bctc_shares_expr(aligned_joined)
    market_shares_expr = (
        pl.when(c("_market_shares").is_finite() & (c("_market_shares") > 0.0))
        .then(c("_market_shares"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    bctc_not_market = bctc_source_raw.fill_null("") != "SHARES_OUTSTANDING_MARKET"
    primary_bctc = pl.when(bctc_not_market).then(bctc_shares_raw).otherwise(None)
    pit_shares = pl.coalesce(primary_bctc, market_shares_expr, bctc_shares_raw)

    close_valid = (
        c("close_raw_vnd").is_not_null()
        & c("close_raw_vnd").is_finite()
        & (c("close_raw_vnd") > 0.0)
    )
    shares_valid = (
        pit_shares.is_not_null() & pit_shares.is_finite() & (pit_shares > 0.0)
    )
    mcap_raw = c("close_raw_vnd") * pit_shares

    mcap_flag = (
        pl.when(~close_valid)
        .then(pl.lit("MISSING_PRICE"))
        .when(~shares_valid)
        .then(pl.lit("MISSING_SHARES"))
        .when(mcap_raw.is_finite())
        .then(pl.lit("OK"))
        .otherwise(pl.lit("MISSING_PRICE"))
    )

    if "eps_ttm_estimated" in aligned_joined.columns:
        eps_expr = c("eps_ttm_estimated").cast(pl.Float64)
    elif "IS_NET_INCOME_PARENT" in aligned_joined.columns:
        eps_expr = c("IS_NET_INCOME_PARENT").cast(pl.Float64) / pit_shares
    else:
        eps_expr = pl.lit(None, dtype=pl.Float64)

    eq_expr = (
        c("parent_equity").cast(pl.Float64)
        if "parent_equity" in aligned_joined.columns
        else pl.lit(None, dtype=pl.Float64)
    )

    pe_flag = (
        pl.when(~close_valid)
        .then(pl.lit("MISSING_PRICE"))
        .when(eps_expr.is_null() | ~eps_expr.is_finite())
        .then(
            pl.when(~shares_valid)
            .then(pl.lit("MISSING_SHARES"))
            .otherwise(pl.lit("MISSING_INPUT"))
        )
        .when(eps_expr <= 0.0)
        .then(pl.lit("NEGATIVE_DENOMINATOR"))
        .otherwise(pl.lit("OK"))
    )

    pb_flag = (
        pl.when(~close_valid)
        .then(pl.lit("MISSING_PRICE"))
        .when(~shares_valid)
        .then(pl.lit("MISSING_SHARES"))
        .when(eq_expr.is_null() | ~eq_expr.is_finite())
        .then(pl.lit("MISSING_INPUT"))
        .when(eq_expr <= 0.0)
        .then(pl.lit("NEGATIVE_DENOMINATOR"))
        .otherwise(pl.lit("OK"))
    )

    return (
        aligned_joined.with_columns(
            pit_shares.cast(pl.Float64).alias("pit_shares_outstanding"),
            pl.when(mcap_flag == "OK")
            .then(mcap_raw)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .cast(pl.Float64)
            .alias("market_cap_vnd"),
            pl.when(pe_flag == "OK")
            .then(c("close_raw_vnd") / eps_expr)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .cast(pl.Float64)
            .alias("pe_ttm"),
            pl.when(pb_flag == "OK")
            .then(mcap_raw / eq_expr)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .cast(pl.Float64)
            .alias("pb_ratio"),
            pe_flag.cast(pl.String).alias("pe_flag"),
            pb_flag.cast(pl.String).alias("pb_flag"),
            mcap_flag.cast(pl.String).alias("market_cap_flag"),
        )
        .select(PIT_VALUATION_MART_SCHEMA.names())
        .sort(list(PIT_VALUATION_MART_KEY))
    )
