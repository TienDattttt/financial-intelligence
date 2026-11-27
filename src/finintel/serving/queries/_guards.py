"""Headless D8 denominator guards and D10 display name normalization helpers for Layer 5 serving.

Verified Sources:
- Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC ratio denominator thresholds
  (``src/finintel/analytics/ratios.py``, ``src/finintel/analytics/stress_test.py``).
- OWASP CSV Injection Prevention Cheat Sheet (preserving leading ``\\t`` and ``\\r``):
  https://cheatsheetseries.owasp.org/cheatsheets/CSV_Injection_Prevention_Cheat_Sheet.html
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import date
from typing import Literal

import duckdb
import polars as pl

from finintel.serving.queries._cache import _available_table_columns
from finintel.serving.queries._helpers import compute_broad_annual_coverage
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import RATIOS_TABLE

MIN_REVENUE_DENOMINATOR_VND = 1_000_000_000.0
MIN_INTEREST_DENOMINATOR_VND = 100_000_000.0
_CO_PHIEU_PREFIX_RE = re.compile(r"^\s*cổ\s+phiếu\s+", re.IGNORECASE)
_GENERIC_TICKER_PREFIX_RE = re.compile(
    r"^(?!CTCP\b|TCT\b|TCTCP\b)[A-Z0-9]{1,10}\s*[-–—]\s*"
)
_MULTI_SPACE_RE = re.compile(r" {2,}")


def apply_d8_ratio_guards(df: pl.DataFrame) -> pl.DataFrame:
    """Mask ``ebit_margin`` (rev < 1B) and ``interest_coverage`` / ``stressed_interest_coverage`` (int < 100M)."""
    if df.is_empty():
        return df
    exprs: list[pl.Expr] = []
    if "net_revenue_ttm" in df.columns and "ebit_margin" in df.columns:
        rev = pl.col("net_revenue_ttm").cast(pl.Float64, strict=False)
        bad_rev = rev.is_not_null() & (
            ~rev.is_finite() | (rev < MIN_REVENUE_DENOMINATOR_VND)
        )
        exprs.append(
            pl.when(bad_rev)
            .then(None)
            .otherwise(pl.col("ebit_margin"))
            .alias("ebit_margin")
        )
        if "ebit_margin_flag" in df.columns:
            exprs.append(
                pl.when(
                    bad_rev
                    & pl.col("ebit_margin_flag").is_in(["VALID", "DERIVED_Q4_INPUT"])
                )
                .then(pl.lit("TINY_DENOMINATOR"))
                .otherwise(pl.col("ebit_margin_flag"))
                .alias("ebit_margin_flag")
            )
    if "borrowing_cost_ttm" in df.columns and "interest_coverage" in df.columns:
        bc = pl.col("borrowing_cost_ttm").cast(pl.Float64, strict=False)
        bad_bc = bc.is_not_null() & (
            ~bc.is_finite() | (bc < MIN_INTEREST_DENOMINATOR_VND)
        )
        exprs.append(
            pl.when(bad_bc)
            .then(None)
            .otherwise(pl.col("interest_coverage"))
            .alias("interest_coverage")
        )
        if "interest_coverage_flag" in df.columns:
            exprs.append(
                pl.when(
                    bc.is_not_null()
                    & (bc > 0.0)
                    & (bc < MIN_INTEREST_DENOMINATOR_VND)
                    & pl.col("interest_coverage_flag").is_in(
                        ["VALID", "DERIVED_Q4_INPUT"]
                    )
                )
                .then(pl.lit("TINY_DENOMINATOR"))
                .otherwise(pl.col("interest_coverage_flag"))
                .alias("interest_coverage_flag")
            )
    if (
        "stressed_borrowing_cost_ttm" in df.columns
        and "stressed_interest_coverage" in df.columns
    ):
        sbc = pl.col("stressed_borrowing_cost_ttm").cast(pl.Float64, strict=False)
        bad_sbc = sbc.is_not_null() & (
            ~sbc.is_finite() | (sbc < MIN_INTEREST_DENOMINATOR_VND)
        )
        exprs.append(
            pl.when(bad_sbc)
            .then(None)
            .otherwise(pl.col("stressed_interest_coverage"))
            .alias("stressed_interest_coverage")
        )
    return df.with_columns(exprs) if exprs else df


def guard_ratio_by_denominator(
    ratio_name: str,
    value: float | None,
    *,
    net_revenue_ttm: float | None = None,
    borrowing_cost_ttm: float | None = None,
    status_flag: str | None = None,
) -> tuple[float | None, str | None]:
    """Scalar D8 denominator guard for ``ebit_margin`` and ``interest_coverage``."""
    if ratio_name == "ebit_margin" and net_revenue_ttm is not None:
        if (
            not math.isfinite(net_revenue_ttm)
            or net_revenue_ttm < MIN_REVENUE_DENOMINATOR_VND
        ):
            new_flag = (
                "TINY_DENOMINATOR"
                if status_flag in ("VALID", "DERIVED_Q4_INPUT")
                and net_revenue_ttm > 0.0
                else status_flag
            )
            return None, new_flag
    if ratio_name == "interest_coverage" and borrowing_cost_ttm is not None:
        if (
            not math.isfinite(borrowing_cost_ttm)
            or borrowing_cost_ttm < MIN_INTEREST_DENOMINATOR_VND
        ):
            new_flag = (
                "TINY_DENOMINATOR"
                if status_flag in ("VALID", "DERIVED_Q4_INPUT")
                and borrowing_cost_ttm > 0.0
                else status_flag
            )
            return None, new_flag
    return value, status_flag


def apply_d8_value_mask(
    df: pl.DataFrame,
    denom_df: pl.DataFrame,
    *,
    metric_name: str,
    join_on: str,
) -> pl.DataFrame:
    """Apply D8 denominator guards to a ``value`` column via a joined denominator frame."""
    if df.is_empty() or "value" not in df.columns:
        return df
    denom_col = (
        "net_revenue_ttm"
        if metric_name == "ebit_margin"
        else "borrowing_cost_ttm"
        if metric_name == "interest_coverage"
        else None
    )
    if (
        denom_col is None
        or denom_df.is_empty()
        or join_on not in df.columns
        or join_on not in denom_df.columns
        or denom_col not in denom_df.columns
    ):
        return df
    clean_denom = (
        denom_df.select(join_on, denom_col)
        .filter(pl.col(join_on).is_not_null())
        .unique(subset=[join_on], keep="last")
    )
    if clean_denom.is_empty():
        return df
    orig_cols = df.columns
    guarded = apply_d8_ratio_guards(
        df.join(clean_denom, on=join_on, how="left").with_columns(
            pl.col("value").alias(metric_name)
        )
    )
    return guarded.with_columns(pl.col(metric_name).alias("value")).select(orig_cols)


def guard_benchmark_frame_d8(
    df: pl.DataFrame,
    *,
    pinned: ParquetRepository,
    metric_name: str,
    statement_scope: str,
    period_type: str,
    join_on: Literal["issuer_id", "period_end_date"],
    issuer_id: str | None = None,
    issuer_ids: Sequence[str] | None = None,
    period_end_date: date | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Mask D8 tiny-denominator ``value`` cells in benchmark peer/history frames."""
    if df.is_empty() or metric_name not in ("ebit_margin", "interest_coverage"):
        return df
    denom_col = (
        "net_revenue_ttm" if metric_name == "ebit_margin" else "borrowing_cost_ttm"
    )
    avail_r_cols = _available_table_columns(pinned, RATIOS_TABLE, connection=connection)
    if not avail_r_cols or denom_col not in avail_r_cols or join_on not in avail_r_cols:
        return df
    filters: dict[str, object] = {
        "statement_scope": statement_scope,
        "period_type": period_type,
    }
    if issuer_id is not None:
        filters["issuer_id"] = issuer_id
    elif issuer_ids is not None:
        if not issuer_ids:
            return df
        filters["issuer_id"] = list(issuer_ids)
    if period_end_date is not None:
        filters["period_end_date"] = period_end_date
    denom_df = pinned.read_table(
        RATIOS_TABLE,
        columns=[join_on, denom_col],
        filters=filters,
        connection=connection,
    )
    return apply_d8_value_mask(df, denom_df, metric_name=metric_name, join_on=join_on)


def build_market_change_period_pairs(
    pinned: ParquetRepository,
    *,
    statement_scope: str,
    period_type: str,
    issuer_ids: Sequence[str],
    filter_by_issuers: bool,
    target_date: date | None,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame:
    """Resolve ``(issuer_id, curr_period_end_date, prev_period_end_date)`` pairs."""
    empty_pairs = pl.DataFrame(
        schema={
            "issuer_id": pl.String,
            "curr_period_end_date": pl.Date,
            "prev_period_end_date": pl.Date,
        }
    )
    ratio_filters: dict[str, object] = {
        "statement_scope": statement_scope,
        "period_type": period_type,
    }
    if filter_by_issuers and period_type != "ANNUAL":
        ratio_filters["issuer_id"] = list(issuer_ids)
    ratio_dates_df = pinned.read_table(
        RATIOS_TABLE,
        columns=["issuer_id", "period_end_date"],
        filters=ratio_filters,
        connection=connection,
    ).filter(pl.col("period_end_date").is_not_null())
    if ratio_dates_df.is_empty():
        return empty_pairs

    enforce_annual_yoy = False
    if period_type == "ANNUAL":
        broad_dates, max_annual_count = compute_broad_annual_coverage(ratio_dates_df)
        if filter_by_issuers:
            ratio_dates_df = ratio_dates_df.filter(
                pl.col("issuer_id").is_in(list(issuer_ids))
            )
        if max_annual_count >= 20:
            enforce_annual_yoy = True
            ratio_dates_df = ratio_dates_df.filter(
                pl.col("period_end_date").is_in(broad_dates)
            )
    if target_date is not None:
        ratio_dates_df = ratio_dates_df.filter(pl.col("period_end_date") <= target_date)
    if ratio_dates_df.is_empty():
        return empty_pairs

    sorted_dates = ratio_dates_df.unique().sort(
        ["issuer_id", "period_end_date"], descending=[False, True]
    )
    prev_expr = (
        pl.col("period_end_date")
        .filter(
            (
                pl.col("period_end_date").first() - pl.col("period_end_date")
            ).dt.total_days()
            >= 330
        )
        .first()
        .cast(pl.Date)
        .alias("prev_period_end_date")
        if enforce_annual_yoy
        else pl.when(pl.len() > 1)
        .then(pl.col("period_end_date").get(1))
        .otherwise(None)
        .cast(pl.Date)
        .alias("prev_period_end_date")
    )
    pairs_df = sorted_dates.group_by("issuer_id", maintain_order=True).agg(
        pl.col("period_end_date").first().alias("curr_period_end_date"),
        prev_expr,
    )
    if target_date is not None:
        pairs_df = pairs_df.filter(pl.col("curr_period_end_date") == target_date)
    return pairs_df


def normalize_company_display_name(
    name: str | None, *, ticker: str | None = None
) -> str | None:
    """Strip redundant ``Cổ phiếu `` and leading ``<TICKER> - `` prefixes (preserving ``\\t``/``\\r``)."""
    if name is None:
        return None
    stripped = name.strip(" ")
    if not stripped:
        return stripped
    cleaned = _CO_PHIEU_PREFIX_RE.sub("", stripped).strip(" ")
    if ticker and ticker.strip():
        clean_ticker = ticker.strip().upper()
        cleaned = re.sub(rf"^{re.escape(clean_ticker)}\s*[-–—]\s*", "", cleaned).strip(
            " "
        )
    else:
        cleaned = _GENERIC_TICKER_PREFIX_RE.sub("", cleaned).strip(" ")
    return _MULTI_SPACE_RE.sub(" ", cleaned)


def normalize_company_display_name_expr(
    col: str = "company_name_vn", *, ticker_col: str | None = "ticker"
) -> pl.Expr:
    """Vectorized Polars expression mirroring ``normalize_company_display_name``."""
    step1 = (
        pl.col(col)
        .cast(pl.String)
        .str.strip_chars(" ")
        .str.replace(r"(?i)^cổ\s+phiếu\s+", "")
        .str.strip_chars(" ")
    )
    prefix = step1.str.extract(r"^([A-Z0-9]{1,10})\s*[-–—]\s*", 1)
    rest = step1.str.replace(r"^[A-Z0-9]{1,10}\s*[-–—]\s*", "")
    is_ticker_prefix = (
        prefix.is_not_null()
        & (
            prefix
            == pl.col(ticker_col).cast(pl.String).str.strip_chars().str.to_uppercase()
        )
        if ticker_col is not None
        else prefix.is_not_null() & ~prefix.is_in(["CTCP", "TCT", "TCTCP"])
    )
    step2 = pl.when(is_ticker_prefix).then(rest).otherwise(step1)
    return (
        pl.when(pl.col(col).is_null())
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(step2.str.strip_chars(" ").str.replace_all(r" {2,}", " "))
        .alias(col)
    )
