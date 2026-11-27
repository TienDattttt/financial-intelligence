"""Extended V3 multi-criteria screener filters, sort fields, and PIT valuation join helpers.

Verified Sources:
- spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.1, §5.2)
- Polars Expressions & Null-Safe Filtering:
  https://docs.pola.rs/api/python/stable/reference/expressions/index.html
- Circular 96/2020/TT-BTC (PIT valuation alignment via pit_valuation_mart)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, cast

import duckdb
import polars as pl

from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import ML_SCORES_SCHEMA, PIT_VALUATION_MART_TABLE

ML_SCREENER_DESIRED: tuple[str, ...] = (
    "issuer_id",
    "filing_id",
    "altman_z_double_prime",
    "altman_em_score",
    "altman_zone",
    "piotroski_f_score",
    "beneish_m8_score",
    "beneish_flag",
    "cluster_id",
    "cluster_archetype_name",
    "anomaly_flag",
    "distress_risk_score",
    "calibrated_distress_prob",
    "risk_tier",
    "risk_market_percentile",
    "market_base_rate",
    "relative_risk_lift",
    "model_role",
    "model_version",
)
ML_SCREENER_JOIN_SCHEMA = pl.Schema(
    {
        name: ML_SCORES_SCHEMA[name]
        for name in ML_SCREENER_DESIRED
        if name != "filing_id"
    }
)

SCREENER_SORT_FIELDS: tuple[str, ...] = (
    "ticker",
    "calibrated_distress_prob",
    "altman_z_double_prime",
    "piotroski_f_score",
    "beneish_m8_score",
    "red_flag_count",
    "roe_total",
    "roe_parent",
    "roa",
    "eps_ttm_estimated",
    "liabilities_to_assets",
    "debt_to_equity",
    "ebit_margin",
    "interest_coverage",
    "cfo_ttm",
    "ocf_to_total_liabilities",
    "revenue_growth_yoy",
    "pe_ttm",
    "pb_ratio",
    "market_cap_vnd",
)

SCREENER_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "company_name_vn": pl.String,
        "exchange": pl.String,
        "accounting_regime": pl.String,
        "icb_code_level2": pl.String,
        "icb_name_level2": pl.String,
        "icb_code_level3": pl.String,
        "icb_name_level3": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "roe_total": pl.Float64,
        "roe_parent": pl.Float64,
        "roa": pl.Float64,
        "eps_ttm_estimated": pl.Float64,
        "ebit_margin": pl.Float64,
        "liabilities_to_assets": pl.Float64,
        "debt_to_equity": pl.Float64,
        "interest_coverage": pl.Float64,
        "cfo_ttm": pl.Float64,
        "fcf_ttm": pl.Float64,
        "ocf_to_total_liabilities": pl.Float64,
        "revenue_growth_yoy": pl.Float64,
        "accruals_to_assets": pl.Float64,
        "core_operating_margin": pl.Float64,
        "non_core_distortion_to_assets": pl.Float64,
        "core_to_ebt_ratio": pl.Float64,
        "market_cap_vnd": pl.Float64,
        "pe_ttm": pl.Float64,
        "pb_ratio": pl.Float64,
        "pe_flag": pl.String,
        "pb_flag": pl.String,
        "red_flag_count": pl.Int64,
        "critical_red_flag_count": pl.Int64,
        "altman_z_double_prime": pl.Float64,
        "altman_em_score": pl.Float64,
        "altman_zone": pl.String,
        "piotroski_f_score": pl.Int8,
        "beneish_m8_score": pl.Float64,
        "beneish_flag": pl.String,
        "cluster_id": pl.Int32,
        "cluster_archetype_name": pl.String,
        "anomaly_flag": pl.String,
        "distress_risk_score": pl.Float64,
        "calibrated_distress_prob": pl.Float64,
        "risk_tier": pl.String,
        "risk_market_percentile": pl.Float64,
        "market_base_rate": pl.Float64,
        "relative_risk_lift": pl.Float64,
        "model_role": pl.String,
        "model_version": pl.String,
    }
)

PIT_VALUATION_JOIN_COLS: tuple[str, ...] = (
    "issuer_id",
    "market_cap_vnd",
    "pe_ttm",
    "pb_ratio",
    "pe_flag",
    "pb_flag",
)

PIT_VALUATION_EMPTY_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "market_cap_vnd": pl.Float64,
        "pe_ttm": pl.Float64,
        "pb_ratio": pl.Float64,
        "pe_flag": pl.String,
        "pb_flag": pl.String,
    }
)

_RANGE_COLUMN_MAP: tuple[tuple[str, str, str, float], ...] = (
    ("roe_parent", "min_roe_parent", "max_roe_parent", 1.0),
    ("roa", "min_roa", "max_roa", 1.0),
    (
        "eps_ttm_estimated",
        "min_eps_ttm_estimated",
        "max_eps_ttm_estimated",
        1.0,
    ),
    (
        "liabilities_to_assets",
        "min_liabilities_to_assets",
        "max_liabilities_to_assets",
        1.0,
    ),
    ("debt_to_equity", "min_debt_to_equity", "max_debt_to_equity", 1.0),
    ("cfo_ttm", "min_cfo_ttm", "max_cfo_ttm", 1.0),
    (
        "ocf_to_total_liabilities",
        "min_ocf_to_total_liabilities",
        "max_ocf_to_total_liabilities",
        1.0,
    ),
    (
        "revenue_growth_yoy",
        "min_revenue_growth_yoy",
        "max_revenue_growth_yoy",
        1.0,
    ),
    ("pe_ttm", "min_pe_ttm", "max_pe_ttm", 1.0),
    ("pb_ratio", "min_pb_ratio", "max_pb_ratio", 1.0),
    (
        "market_cap_vnd",
        "min_market_cap_bn_vnd",
        "max_market_cap_bn_vnd",
        1_000_000_000.0,
    ),
)


@dataclass(frozen=True, slots=True)
class ScreenerExtraFilters:
    """Typed container for V3 multi-criteria financial, valuation, cluster, and outlier filters."""

    cluster_id: int | None = None
    anomaly_flag: bool | None = None
    min_roe_parent: float | None = None
    max_roe_parent: float | None = None
    min_roa: float | None = None
    max_roa: float | None = None
    min_eps_ttm_estimated: float | None = None
    max_eps_ttm_estimated: float | None = None
    min_liabilities_to_assets: float | None = None
    max_liabilities_to_assets: float | None = None
    min_debt_to_equity: float | None = None
    max_debt_to_equity: float | None = None
    min_cfo_ttm: float | None = None
    max_cfo_ttm: float | None = None
    min_ocf_to_total_liabilities: float | None = None
    max_ocf_to_total_liabilities: float | None = None
    min_revenue_growth_yoy: float | None = None
    max_revenue_growth_yoy: float | None = None
    min_pe_ttm: float | None = None
    max_pe_ttm: float | None = None
    min_pb_ratio: float | None = None
    max_pb_ratio: float | None = None
    min_market_cap_bn_vnd: float | None = None
    max_market_cap_bn_vnd: float | None = None

    def cache_tuple(self) -> tuple[object, ...]:
        return (
            self.cluster_id,
            self.anomaly_flag,
            *(getattr(self, min_attr) for _, min_attr, _, _ in _RANGE_COLUMN_MAP),
            *(getattr(self, max_attr) for _, _, max_attr, _ in _RANGE_COLUMN_MAP),
        )

    def to_cache_dict(self) -> dict[str, object]:
        out: dict[str, object] = {}
        if self.cluster_id is not None:
            out["cluster_id"] = self.cluster_id
        if self.anomaly_flag is not None:
            out["anomaly_flag"] = self.anomaly_flag
        for _, min_attr, max_attr, _ in _RANGE_COLUMN_MAP:
            min_v = getattr(self, min_attr)
            max_v = getattr(self, max_attr)
            if min_v is not None:
                out[min_attr] = float(min_v)
            if max_v is not None:
                out[max_attr] = float(max_v)
        return out


def extract_extended_filters(
    extra_filters: ScreenerExtraFilters | None,
    kwargs: dict[str, object],
) -> ScreenerExtraFilters:
    """Merge explicit ``ScreenerExtraFilters`` with optional keyword args cleanly."""
    base = extra_filters if extra_filters is not None else ScreenerExtraFilters()
    if not kwargs:
        return base
    allowed = set(ScreenerExtraFilters.__dataclass_fields__)
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise TypeError(f"Unexpected screener filter argument(s): {', '.join(unknown)}")
    return replace(base, **cast(dict[str, Any], kwargs))


def validate_extended_filters(ext: ScreenerExtraFilters) -> None:
    """Validate cluster_id, anomaly_flag, and all numeric range bounds."""
    if ext.cluster_id is not None and (
        isinstance(ext.cluster_id, bool)
        or not isinstance(ext.cluster_id, int)
        or ext.cluster_id < 0
    ):
        raise ValueError("cluster_id must be a non-negative integer.")
    if ext.anomaly_flag is not None and not isinstance(ext.anomaly_flag, bool):
        raise ValueError("anomaly_flag must be a boolean.")
    for _, min_attr, max_attr, _ in _RANGE_COLUMN_MAP:
        for attr in (min_attr, max_attr):
            val = getattr(ext, attr)
            if val is not None and (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(float(val))
            ):
                raise ValueError(f"{attr} must be a finite numeric value.")


def read_pit_valuation_slice(
    pinned: ParquetRepository,
    *,
    avail_mart_cols: set[str] | None,
    statement_scope: str,
    period_type: str,
    period_end_date: date,
    issuer_ids: list[str] | None,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame:
    """Read PIT valuation columns for one period slice, degrading gracefully if absent."""
    if not avail_mart_cols:
        return pl.DataFrame(schema=PIT_VALUATION_EMPTY_SCHEMA)
    cols = [c for c in PIT_VALUATION_JOIN_COLS if c in avail_mart_cols]
    if "issuer_id" not in cols or len(cols) <= 1:
        return pl.DataFrame(schema=PIT_VALUATION_EMPTY_SCHEMA)
    flt: dict[str, object] = {
        "statement_scope": statement_scope,
        "period_type": period_type,
        "period_end_date": period_end_date,
    }
    if issuer_ids:
        flt["issuer_id"] = issuer_ids
    mart = pinned.read_table(
        PIT_VALUATION_MART_TABLE,
        columns=cols,
        filters=flt,
        connection=connection,
    )
    if mart.is_empty():
        return pl.DataFrame(schema=PIT_VALUATION_EMPTY_SCHEMA)
    return mart.unique(subset=["issuer_id"], keep="last")


def apply_extended_screener_filters(
    joined: pl.DataFrame, ext: ScreenerExtraFilters
) -> pl.DataFrame:
    """Apply cluster, anomaly, financial, and PIT valuation range filters without zero-filling."""
    if ext.cluster_id is not None:
        joined = joined.filter(
            pl.col("cluster_id").is_not_null()
            & (pl.col("cluster_id") == ext.cluster_id)
        )
    if ext.anomaly_flag is True:
        joined = joined.filter(
            pl.col("anomaly_flag").is_not_null()
            & (pl.col("anomaly_flag").cast(pl.String).str.strip_chars() != "")
        )
    elif ext.anomaly_flag is False:
        joined = joined.filter(
            pl.col("anomaly_flag").is_null()
            | (pl.col("anomaly_flag").cast(pl.String).str.strip_chars() == "")
        )

    for col_name, min_attr, max_attr, scale in _RANGE_COLUMN_MAP:
        min_v = getattr(ext, min_attr)
        max_v = getattr(ext, max_attr)
        if min_v is None and max_v is None:
            continue
        if col_name not in joined.columns:
            return joined.head(0)
        col_expr = pl.col(col_name).cast(pl.Float64)
        cond = col_expr.is_not_null() & col_expr.is_finite()
        if min_v is not None:
            cond = cond & (col_expr >= float(min_v) * scale)
        if max_v is not None:
            cond = cond & (col_expr <= float(max_v) * scale)
        joined = joined.filter(cond)
    return joined
