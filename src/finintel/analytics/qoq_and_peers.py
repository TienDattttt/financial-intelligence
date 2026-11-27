"""Taxonomy-safe discrete QoQ growth, ICB peer auto-suggest, and anomaly Z-drivers (V3-22).

Verified Sources:
    spec/v3/SPEC-v3-analyst-depth-and-ops-qa.md (§1, §4, §5.1, §6)
    Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (Q4/2025 -> Q1/2026 boundary)
    Polars DataFrame expressions & window functions: https://docs.pola.rs/api/python/stable/
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import polars as pl

DISCRETE_QUARTER_MIN_DAYS = 80
DISCRETE_QUARTER_MAX_DAYS = 100
MAX_CONSECUTIVE_QUARTER_GAP_DAYS = 100
MIN_CONSECUTIVE_QUARTER_GAP_DAYS = 60
MIN_ICB_L3_COHORT_ISSUERS = 5
MULTIVARIATE_OUTLIER_PERCENTILE = 95.0


class QoqStatusFlag(StrEnum):
    VALID = "VALID"
    MIXED_TAXONOMY = "MIXED_TAXONOMY"
    NON_POSITIVE_BASE_PERIOD = "NON_POSITIVE_BASE_PERIOD"
    MISSING_INPUT = "MISSING_INPUT"
    MISSING_PRIOR_QUARTER_BASE = "MISSING_PRIOR_QUARTER_BASE"
    NOT_DISCRETE_QUARTER = "NOT_DISCRETE_QUARTER"
    NUMERIC_OVERFLOW = "NUMERIC_OVERFLOW"


@dataclass(frozen=True)
class SuggestedPeer:
    issuer_id: str
    ticker: str
    company_name_vn: str | None
    exchange: str | None
    icb_code_level2: str | None
    icb_name_level2: str | None
    icb_code_level3: str | None
    icb_name_level3: str | None
    peer_basis: str
    size_metric: str
    size_ratio: float | None
    market_cap_vnd: float | None
    bs_total_assets: float | None
    risk_tier: str | None
    calibrated_distress_prob: float | None


@dataclass(frozen=True)
class AnomalyDriver:
    metric_name: str
    company_value: float
    peer_median: float | None
    sector_z: float
    abs_sector_z: float
    direction_vs_sector: str
    peer_group_level: str | None
    peer_count: int


def _opt_float(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def _opt_str(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def compute_discrete_qoq_growth(
    frame: pl.DataFrame,
    *,
    metric_columns: tuple[tuple[str, str], ...] = (
        ("IS_NET_REVENUE", "revenue_growth_qoq"),
        ("IS_NET_INCOME_PARENT", "net_income_parent_growth_qoq"),
    ),
) -> pl.DataFrame:
    """Compute strictly consecutive discrete-quarter QoQ growth without crossing TT200->TT99."""
    if frame.is_empty():
        return frame.with_columns(
            [
                expr
                for _, out_col in metric_columns
                for expr in (
                    pl.lit(None, dtype=pl.Float64).alias(out_col),
                    pl.lit(None, dtype=pl.String).alias(f"{out_col}_flag"),
                )
            ]
        )

    partition_cols = [c for c in ("issuer_id", "statement_scope") if c in frame.columns]
    sorted_frame = frame.sort([*partition_cols, "period_end_date"])

    def _over(expr: pl.Expr) -> pl.Expr:
        return expr.over(partition_cols) if partition_cols else expr

    if "period_days" in sorted_frame.columns:
        curr_days = pl.col("period_days").cast(pl.Int64, strict=False)
    elif {"period_start_date", "period_end_date"}.issubset(sorted_frame.columns):
        curr_days = (
            pl.col("period_end_date").cast(pl.Date)
            - pl.col("period_start_date").cast(pl.Date)
        ).dt.total_days() + 1
    else:
        curr_days = pl.lit(90, dtype=pl.Int64)

    is_q = (
        pl.col("period_type").cast(pl.String) == "QUARTER"
        if "period_type" in sorted_frame.columns
        else pl.lit(True)
    )
    is_discrete_curr = is_q & curr_days.is_between(
        DISCRETE_QUARTER_MIN_DAYS, DISCRETE_QUARTER_MAX_DAYS
    )
    is_discrete_prev = _over(is_discrete_curr.shift(1)).fill_null(False)

    end_date = pl.col("period_end_date").cast(pl.Date)
    prev_end_date = _over(end_date.shift(1))
    consecutive_gap = (
        (end_date - prev_end_date)
        .dt.total_days()
        .is_between(MIN_CONSECUTIVE_QUARTER_GAP_DAYS, MAX_CONSECUTIVE_QUARTER_GAP_DAYS)
    )
    if {"fiscal_year", "fiscal_quarter"}.issubset(sorted_frame.columns):
        q_idx = pl.col("fiscal_year").cast(pl.Int64, strict=False) * 4 + pl.col(
            "fiscal_quarter"
        ).cast(pl.Int64, strict=False)
        slot_ok = ((q_idx - _over(q_idx.shift(1))) == 1).fill_null(True)
    else:
        slot_ok = pl.lit(True)

    has_consecutive_prior = (
        prev_end_date.is_not_null()
        & is_discrete_prev
        & consecutive_gap.fill_null(False)
        & slot_ok
    )

    if "taxonomy_version" in sorted_frame.columns:
        tax = pl.col("taxonomy_version").cast(pl.String)
        prev_tax = _over(tax.shift(1))
        mixed_tax = (
            tax.is_not_null()
            & prev_tax.is_not_null()
            & (tax != "")
            & (prev_tax != "")
            & (tax != prev_tax)
        ).fill_null(False)
    else:
        mixed_tax = pl.lit(False)

    out_exprs: list[pl.Expr] = []
    for src_col, out_col in metric_columns:
        curr_val = (
            pl.col(src_col).cast(pl.Float64, strict=False)
            if src_col in sorted_frame.columns
            else pl.lit(None, dtype=pl.Float64)
        )
        prev_val = _over(curr_val.shift(1))
        qoq = (curr_val - prev_val) / prev_val
        flag = (
            pl.when(~is_discrete_curr.fill_null(False))
            .then(pl.lit(QoqStatusFlag.NOT_DISCRETE_QUARTER.value))
            .when(~has_consecutive_prior)
            .then(pl.lit(QoqStatusFlag.MISSING_PRIOR_QUARTER_BASE.value))
            .when(mixed_tax)
            .then(pl.lit(QoqStatusFlag.MIXED_TAXONOMY.value))
            .when(
                curr_val.is_null()
                | prev_val.is_null()
                | ~curr_val.is_finite()
                | ~prev_val.is_finite()
            )
            .then(pl.lit(QoqStatusFlag.MISSING_INPUT.value))
            .when(prev_val <= 0.0)
            .then(pl.lit(QoqStatusFlag.NON_POSITIVE_BASE_PERIOD.value))
            .when(~qoq.is_finite())
            .then(pl.lit(QoqStatusFlag.NUMERIC_OVERFLOW.value))
            .otherwise(pl.lit(QoqStatusFlag.VALID.value))
        )
        out_exprs.extend(
            [
                pl.when(flag == QoqStatusFlag.VALID.value)
                .then(qoq)
                .otherwise(pl.lit(None, dtype=pl.Float64))
                .cast(pl.Float64)
                .alias(out_col),
                flag.cast(pl.String).alias(f"{out_col}_flag"),
            ]
        )
    return sorted_frame.with_columns(out_exprs)


def compute_statement_row_qoq_map(
    *,
    periods: Sequence[Mapping[str, object]],
    values: Mapping[str, float | None],
    period_type: str,
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Compute per-period discrete QoQ maps for a single statement row."""
    if not periods or period_type != "QUARTER":
        return {}, {}
    rows: list[dict[str, object]] = []
    for p in periods:
        raw_end = p.get("period_end_date")
        pend = (
            raw_end
            if isinstance(raw_end, date)
            else date.fromisoformat(str(raw_end)[:10])
        )
        iso = pend.isoformat()
        rows.append(
            {
                "period_end_date": pend,
                "period_iso": iso,
                "period_type": period_type,
                "fiscal_year": p.get("fiscal_year"),
                "fiscal_quarter": p.get("fiscal_quarter"),
                "taxonomy_version": p.get("taxonomy_version"),
                "metric_val": _opt_float(values.get(iso)),
            }
        )
    frame = pl.DataFrame(
        rows,
        schema_overrides={
            "period_end_date": pl.Date,
            "fiscal_year": pl.Int64,
            "fiscal_quarter": pl.Int64,
            "taxonomy_version": pl.String,
            "metric_val": pl.Float64,
        },
    )
    computed = compute_discrete_qoq_growth(
        frame, metric_columns=(("metric_val", "qoq"),)
    )
    isos = computed.get_column("period_iso").to_list()
    return (
        dict(zip(isos, computed.get_column("qoq").to_list(), strict=True)),
        dict(zip(isos, computed.get_column("qoq_flag").to_list(), strict=True)),
    )


def _select_peer_cohort(
    active: pl.DataFrame, target_id: str, target_l3: str | None, target_l2: str | None
) -> tuple[str, pl.DataFrame]:
    if target_l3 is not None and "icb_code_level3" in active.columns:
        l3 = active.filter(
            pl.col("icb_code_level3").cast(pl.String).str.strip_chars() == target_l3
        )
        if l3.height >= MIN_ICB_L3_COHORT_ISSUERS:
            return "ICB_LEVEL_3", l3
    if target_l2 is not None and "icb_code_level2" in active.columns:
        l2 = active.filter(
            pl.col("icb_code_level2").cast(pl.String).str.strip_chars() == target_l2
        )
        if l2.filter(pl.col("issuer_id") != target_id).height > 0:
            return "ICB_LEVEL_2", l2
    return "REGIME_FALLBACK", active


def suggest_similar_peers(
    target_ticker: str,
    issuers: pl.DataFrame,
    *,
    valuation_mart: pl.DataFrame | None = None,
    ratios: pl.DataFrame | None = None,
    ml_scores: pl.DataFrame | None = None,
    limit: int = 4,
) -> tuple[str, list[SuggestedPeer]]:
    """Suggest up to ``limit`` closest peers in ICB L3 (fallback L2 when < 5 issuers in L3)."""
    clean_ticker = target_ticker.strip().upper()
    if not clean_ticker or issuers.is_empty() or limit < 1:
        return "ICB_LEVEL_3", []

    norm_iss = issuers.with_columns(
        pl.col("ticker")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("ticker")
    )
    target_rows = norm_iss.filter(pl.col("ticker") == clean_ticker)
    if target_rows.is_empty():
        raise ValueError(f"Ticker not found in active snapshot: {clean_ticker}")
    target = target_rows.row(0, named=True)
    target_id = str(target["issuer_id"])
    target_regime = _opt_str(target.get("accounting_regime")) or "CORPORATE"
    active = norm_iss.filter(
        (pl.col("accounting_regime").cast(pl.String) == target_regime)
        & (
            pl.col("delisted_date").is_null()
            if "delisted_date" in norm_iss.columns
            else pl.lit(True)
        )
        & (
            (pl.col("exchange").cast(pl.String) != "DELISTED")
            if "exchange" in norm_iss.columns
            else pl.lit(True)
        )
    )
    peer_basis, enriched = _select_peer_cohort(
        active,
        target_id,
        _opt_str(target.get("icb_code_level3")),
        _opt_str(target.get("icb_code_level2")),
    )

    if (
        valuation_mart is not None
        and not valuation_mart.is_empty()
        and {"issuer_id", "market_cap_vnd"}.issubset(valuation_mart.columns)
    ):
        enriched = enriched.join(
            valuation_mart.select(
                pl.col("issuer_id").cast(pl.String),
                pl.col("market_cap_vnd").cast(pl.Float64, strict=False),
            ).unique(subset=["issuer_id"], keep="last"),
            on="issuer_id",
            how="left",
        )
    else:
        enriched = enriched.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("market_cap_vnd")
        )

    if (
        ratios is not None
        and not ratios.is_empty()
        and {"issuer_id", "BS_TOTAL_ASSETS"}.issubset(ratios.columns)
    ):
        enriched = enriched.join(
            ratios.select(
                pl.col("issuer_id").cast(pl.String),
                pl.col("BS_TOTAL_ASSETS")
                .cast(pl.Float64, strict=False)
                .alias("bs_total_assets"),
            ).unique(subset=["issuer_id"], keep="last"),
            on="issuer_id",
            how="left",
        )
    else:
        enriched = enriched.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("bs_total_assets")
        )

    if (
        ml_scores is not None
        and not ml_scores.is_empty()
        and "issuer_id" in ml_scores.columns
    ):
        ml_cols = [
            c
            for c in ("issuer_id", "risk_tier", "calibrated_distress_prob")
            if c in ml_scores.columns
        ]
        enriched = enriched.join(
            ml_scores.select(ml_cols).unique(subset=["issuer_id"], keep="last"),
            on="issuer_id",
            how="left",
        )
    for col_name, dtype in (
        ("risk_tier", pl.String),
        ("calibrated_distress_prob", pl.Float64),
    ):
        if col_name not in enriched.columns:
            enriched = enriched.with_columns(pl.lit(None, dtype=dtype).alias(col_name))

    target_enriched = enriched.filter(pl.col("issuer_id") == target_id)
    target_row = (
        target_enriched.row(0, named=True) if not target_enriched.is_empty() else {}
    )
    target_mcap = _opt_float(target_row.get("market_cap_vnd"))
    target_assets = _opt_float(target_row.get("bs_total_assets"))
    candidates = enriched.filter(pl.col("issuer_id") != target_id)
    if candidates.is_empty():
        return peer_basis, []

    mcap_col, assets_col = pl.col("market_cap_vnd"), pl.col("bs_total_assets")
    has_target_mcap = target_mcap is not None and target_mcap > 0.0
    has_target_assets = target_assets is not None and target_assets > 0.0
    use_mcap = (
        mcap_col.is_finite() & (mcap_col > 0.0) if has_target_mcap else pl.lit(False)
    )
    use_assets = (
        assets_col.is_finite() & (assets_col > 0.0)
        if has_target_assets
        else pl.lit(False)
    )
    mcap_dist = (
        (mcap_col.log() - math.log(target_mcap)).abs()
        if target_mcap and target_mcap > 0.0
        else pl.lit(None, dtype=pl.Float64)
    )
    assets_dist = (
        (assets_col.log() - math.log(target_assets)).abs()
        if target_assets and target_assets > 0.0
        else pl.lit(None, dtype=pl.Float64)
    )

    ranked = (
        candidates.with_columns(
            pl.when(use_mcap)
            .then(pl.lit("market_cap_vnd"))
            .when(use_assets)
            .then(pl.lit("BS_TOTAL_ASSETS"))
            .otherwise(pl.lit("UNAVAILABLE"))
            .alias("size_metric"),
            pl.when(use_mcap)
            .then(pl.lit(0))
            .when(use_assets)
            .then(pl.lit(1))
            .otherwise(pl.lit(2))
            .alias("_metric_priority"),
            pl.when(use_mcap)
            .then(mcap_dist)
            .when(use_assets)
            .then(assets_dist)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("_size_dist"),
            pl.when(use_mcap)
            .then(mcap_col / (target_mcap or 1.0))
            .when(use_assets)
            .then(assets_col / (target_assets or 1.0))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("size_ratio"),
        )
        .sort(["_metric_priority", "_size_dist", "ticker"], nulls_last=True)
        .head(limit)
    )
    return peer_basis, [
        SuggestedPeer(
            issuer_id=str(row["issuer_id"]),
            ticker=str(row["ticker"]),
            company_name_vn=_opt_str(row.get("company_name_vn")),
            exchange=_opt_str(row.get("exchange")),
            icb_code_level2=_opt_str(row.get("icb_code_level2")),
            icb_name_level2=_opt_str(row.get("icb_name_level2")),
            icb_code_level3=_opt_str(row.get("icb_code_level3")),
            icb_name_level3=_opt_str(row.get("icb_name_level3")),
            peer_basis=peer_basis,
            size_metric=str(row["size_metric"]),
            size_ratio=_opt_float(row.get("size_ratio")),
            market_cap_vnd=_opt_float(row.get("market_cap_vnd")),
            bs_total_assets=_opt_float(row.get("bs_total_assets")),
            risk_tier=_opt_str(row.get("risk_tier")),
            calibrated_distress_prob=_opt_float(row.get("calibrated_distress_prob")),
        )
        for row in ranked.to_dicts()
    ]


def extract_top_anomaly_drivers(
    benchmarks: pl.DataFrame,
    *,
    anomaly_percentile_rank: float | None,
    statistical_outlier_flag: bool | None = None,
    top_k: int = 3,
) -> tuple[bool, list[AnomalyDriver]]:
    """Return top ``top_k`` |sector_z| drivers for multivariate Top 5% outliers."""
    is_outlier = bool(statistical_outlier_flag) or (
        anomaly_percentile_rank is not None
        and math.isfinite(anomaly_percentile_rank)
        and anomaly_percentile_rank >= MULTIVARIATE_OUTLIER_PERCENTILE
    )
    if not is_outlier or benchmarks.is_empty() or top_k < 1:
        return is_outlier, []
    if not {"metric_or_ratio_name", "value", "robust_z_score"}.issubset(
        benchmarks.columns
    ):
        return True, []

    ranked = (
        benchmarks.with_columns(
            pl.col("value").cast(pl.Float64, strict=False).alias("_val"),
            pl.col("robust_z_score").cast(pl.Float64, strict=False).alias("_z"),
        )
        .filter(pl.col("_val").is_finite() & pl.col("_z").is_finite())
        .with_columns(pl.col("_z").abs().alias("_abs_z"))
        .sort(["_abs_z", "metric_or_ratio_name"], descending=[True, False])
        .head(top_k)
    )
    drivers: list[AnomalyDriver] = []
    for row in ranked.to_dicts():
        z_val = float(row["_z"])
        raw_cnt = row.get("peer_count")
        drivers.append(
            AnomalyDriver(
                metric_name=str(row["metric_or_ratio_name"]),
                company_value=float(row["_val"]),
                peer_median=_opt_float(row.get("peer_median")),
                sector_z=z_val,
                abs_sector_z=abs(z_val),
                direction_vs_sector="ABOVE_MEDIAN"
                if z_val > 0.0
                else ("BELOW_MEDIAN" if z_val < 0.0 else "AT_MEDIAN"),
                peer_group_level=_opt_str(row.get("peer_group_level")),
                peer_count=int(raw_cnt) if isinstance(raw_cnt, int) else 0,
            )
        )
    return True, drivers


__all__ = [
    "AnomalyDriver",
    "QoqStatusFlag",
    "SuggestedPeer",
    "compute_discrete_qoq_growth",
    "compute_statement_row_qoq_map",
    "extract_top_anomaly_drivers",
    "suggest_similar_peers",
]
