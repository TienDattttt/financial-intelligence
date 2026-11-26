"""Headless shared pure query helpers for Layer 5 serving submodules.

Verified Sources:
- DuckDB Python API & Multithreading (``duckdb==1.4.2``, non-owning ``conn.cursor()``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
- DuckLake Official Documentation (time travel ``AT (VERSION => ...)``):
  https://ducklake.select/docs/stable/
- Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
  Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

import duckdb
import polars as pl

from finintel.serving.queries._cache import (
    _LATEST_RATIO_PERIOD_CACHE,
    _MAX_QUERY_CACHE_SIZE,
    _QUERY_CACHE_LOCK,
    ALTMAN_ZONES,
    BENEISH_FLAGS,
    DEFAULT_DATA_ROOT,
    PERIOD_TYPES,
    RISK_TIERS_ORDERED,
    SCREENER_SORT_FIELDS,
    STATEMENT_SCOPES,
    DenominatorMode,
    IndustryComparisonData,
    LineageComponent,
    SectorRiskSummaryDict,
    _available_table_columns,
    _evict_stale_l1b_caches_locked,
    _pad_schema_columns,
    _select_existing_columns,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)


def opt_finite_float(raw: object) -> float | None:
    """Return a finite Python ``float`` or ``None`` for null/bool/non-finite inputs."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def resolve_latest_ratio_period(
    pinned: ParquetRepository,
    *,
    statement_scope: str,
    period_type: str,
    snap_key: tuple[object, ...] | None,
    connection: duckdb.DuckDBPyConnection | None,
) -> tuple[date | None, bool]:
    """Resolve the latest ``period_end_date`` in ``financial_ratios`` with L1B cache.

    Returns ``(resolved_date, has_rows)`` so callers preserve exact empty vs
    malformed-date semantics.
    """
    period_cache_key = (
        (*snap_key, statement_scope, period_type) if snap_key is not None else None
    )
    if period_cache_key is not None:
        with _QUERY_CACHE_LOCK:
            _evict_stale_l1b_caches_locked(snap_key)
            cached_date = _LATEST_RATIO_PERIOD_CACHE.get(period_cache_key)
            if cached_date is not None:
                _LATEST_RATIO_PERIOD_CACHE.move_to_end(period_cache_key)
                return cached_date, True

    dates_df = pinned.read_table(
        RATIOS_TABLE,
        columns=["period_end_date"],
        filters={
            "statement_scope": statement_scope,
            "period_type": period_type,
        },
        connection=connection,
    )
    if dates_df.is_empty():
        return None, False

    raw_max = dates_df.get_column("period_end_date").max()
    if not isinstance(raw_max, date):
        return None, True

    resolved_date = raw_max
    if period_cache_key is not None:
        with _QUERY_CACHE_LOCK:
            _evict_stale_l1b_caches_locked(snap_key)
            _LATEST_RATIO_PERIOD_CACHE[period_cache_key] = resolved_date
            while len(_LATEST_RATIO_PERIOD_CACHE) > _MAX_QUERY_CACHE_SIZE:
                _LATEST_RATIO_PERIOD_CACHE.popitem(last=False)
    return resolved_date, True


def read_red_flag_counts_for_period(
    pinned: ParquetRepository,
    *,
    statement_scope: str,
    period_type: str,
    period_end_date: date,
    include_critical: bool,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame:
    """Aggregate red-flag counts (and optional CRITICAL counts) per issuer for one period."""
    empty_schema: dict[str, pl.DataType] = {
        "issuer_id": pl.String(),
        "red_flag_count": pl.Int64(),
    }
    if include_critical:
        empty_schema["critical_red_flag_count"] = pl.Int64()

    avail_rf = _available_table_columns(pinned, RED_FLAGS_TABLE, connection=connection)
    if not avail_rf:
        return pl.DataFrame(schema=empty_schema)

    rf_cols = ["issuer_id", "severity"] if include_critical else ["issuer_id"]
    rf_slice = pinned.read_table(
        RED_FLAGS_TABLE,
        columns=rf_cols,
        filters={
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": period_end_date,
        },
        connection=connection,
    )
    if rf_slice.is_empty():
        return pl.DataFrame(schema=empty_schema)

    aggs: list[pl.Expr] = [pl.len().cast(pl.Int64).alias("red_flag_count")]
    if include_critical:
        aggs.append(
            (pl.col("severity") == "CRITICAL")
            .sum()
            .cast(pl.Int64)
            .alias("critical_red_flag_count")
        )
    return rf_slice.group_by("issuer_id").agg(aggs)


def read_ml_scores_slice(
    pinned: ParquetRepository,
    *,
    desired_cols: tuple[str, ...],
    target_schema: pl.Schema,
    statement_scope: str,
    period_type: str,
    period_end_date: date,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame:
    """Read, deduplicate by ``issuer_id``, and schema-pad one ML scores period slice."""
    avail_ml = _available_table_columns(pinned, ML_SCORES_TABLE, connection=connection)
    if not avail_ml:
        return pl.DataFrame(schema=target_schema)

    ml_cols = _select_existing_columns(desired_cols, avail_ml)
    ml_raw = pinned.read_table(
        ML_SCORES_TABLE,
        columns=ml_cols,
        filters={
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": period_end_date,
        },
        connection=connection,
    )
    if ml_raw.is_empty():
        return pl.DataFrame(schema=target_schema)

    sort_ml = (
        ["issuer_id", "filing_id"] if "filing_id" in ml_raw.columns else ["issuer_id"]
    )
    ml_dedup = ml_raw.sort(sort_ml).unique(subset=["issuer_id"], keep="last")
    return _pad_schema_columns(ml_dedup, target_schema)


def empty_sector_risk_summary(period_end_date: date | None) -> SectorRiskSummaryDict:
    """Return an empty ``SectorRiskSummaryDict`` payload."""
    return {
        "period_end_date": period_end_date,
        "total_issuers": 0,
        "safe_zone_count": 0,
        "grey_zone_count": 0,
        "distress_zone_count": 0,
        "beneish_flagged_count": 0,
        "high_red_flag_count": 0,
        "sectors": [],
        "top_deteriorating": [],
    }


def empty_industry_comparison(
    *,
    ticker: str,
    issuer_id: str,
    statement_scope: str,
    period_type: str,
    period_end_date: date,
    metric_name: str,
    industry_snapshot_date: date | None,
) -> IndustryComparisonData:
    """Return an empty ``IndustryComparisonData`` payload."""
    return IndustryComparisonData(
        ticker=ticker,
        issuer_id=issuer_id,
        statement_scope=statement_scope,
        period_type=period_type,
        period_end_date=period_end_date,
        metric_name=metric_name,
        peer_group_level=None,
        icb_code_used=None,
        peer_count=0,
        company_value=None,
        peer_median=None,
        peer_p25=None,
        peer_p75=None,
        peer_iqr=None,
        percentile_rank=None,
        robust_z_score=None,
        industry_snapshot_date=industry_snapshot_date,
        peer_ranking=pl.DataFrame(),
    )


def validate_screener_filters(
    *,
    period_type: str,
    statement_scope: str,
    min_f_score: int | None,
    altman_zone: str | None,
    risk_tier: str | None,
    beneish_flag: str | None,
    max_distress_prob: float | None,
    min_distress_prob: float | None,
    max_red_flags: int | None,
    min_red_flags: int | None,
    q: str | None,
    sort_by: str,
    sort_dir: Literal["asc", "desc"],
) -> str | None:
    """Validate screener filter inputs and return normalized lowercase search text."""
    if statement_scope not in STATEMENT_SCOPES:
        raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
    if period_type not in PERIOD_TYPES:
        raise ValueError(f"Unsupported period_type: {period_type!r}")
    if min_f_score is not None and (
        isinstance(min_f_score, bool)
        or not isinstance(min_f_score, int)
        or not (0 <= min_f_score <= 9)
    ):
        raise ValueError("min_f_score must be an integer between 0 and 9.")
    if altman_zone is not None and altman_zone not in ALTMAN_ZONES:
        raise ValueError(f"Unsupported altman_zone: {altman_zone!r}")
    if risk_tier is not None and risk_tier not in RISK_TIERS_ORDERED:
        raise ValueError(f"Unsupported risk_tier: {risk_tier!r}")
    if beneish_flag is not None and beneish_flag not in BENEISH_FLAGS:
        raise ValueError(f"Unsupported beneish_flag: {beneish_flag!r}")
    for label, prob in (
        ("max_distress_prob", max_distress_prob),
        ("min_distress_prob", min_distress_prob),
    ):
        if prob is not None and (
            isinstance(prob, bool)
            or not isinstance(prob, (int, float))
            or not math.isfinite(float(prob))
            or not (0.0 <= float(prob) <= 1.0)
        ):
            raise ValueError(f"{label} must be a finite float in [0.0, 1.0].")
    for label, count_bound in (
        ("max_red_flags", max_red_flags),
        ("min_red_flags", min_red_flags),
    ):
        if count_bound is not None and (
            isinstance(count_bound, bool)
            or not isinstance(count_bound, int)
            or count_bound < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer.")
    if sort_by not in SCREENER_SORT_FIELDS:
        raise ValueError(f"Unsupported sort_by: {sort_by!r}")
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"Unsupported sort_dir: {sort_dir!r}")
    return q.strip().lower() if q and q.strip() else None


def select_lineage_window(
    frame: pl.DataFrame,
    role: Literal["NUMERATOR", "DENOMINATOR"],
    *,
    period_type: str,
    period_end_date: date,
    denominator_mode: DenominatorMode,
) -> pl.DataFrame:
    """Filter facts to the exact numerator/denominator window for ratio lineage."""
    if frame.is_empty():
        return frame
    is_stock = pl.col("metric_id").str.starts_with("BS_")
    if role == "NUMERATOR":
        if period_type == "ANNUAL":
            return frame.filter(
                (pl.col("period_end_date") == period_end_date)
                & (is_stock | (pl.col("period_type") == "ANNUAL"))
            )
        stock_rows = frame.filter(
            is_stock & (pl.col("period_end_date") == period_end_date)
        )
        flow_rows = (
            frame.filter(
                ~is_stock
                & (pl.col("period_type") == "QUARTER")
                & (pl.col("period_end_date") <= period_end_date)
            )
            .sort(["metric_id", "period_end_date"])
            .group_by("metric_id", maintain_order=True)
            .tail(4)
            .select(frame.columns)
        )
        return pl.concat([stock_rows, flow_rows])

    if denominator_mode == "POINT_IN_TIME_STOCK":
        return frame.filter(pl.col("period_end_date") == period_end_date)
    if denominator_mode == "AVERAGE_STOCK":
        closing = frame.filter(pl.col("period_end_date") == period_end_date)
        opening = (
            frame.filter(pl.col("period_end_date") < period_end_date)
            .sort(["metric_id", "period_end_date"])
            .group_by("metric_id", maintain_order=True)
            .tail(1)
            .select(frame.columns)
        )
        return pl.concat([opening, closing])
    if denominator_mode == "FLOW":
        if period_type == "ANNUAL":
            return frame.filter(
                (pl.col("period_end_date") == period_end_date)
                & (pl.col("period_type") == "ANNUAL")
            )
        return (
            frame.filter(
                (pl.col("period_type") == "QUARTER")
                & (pl.col("period_end_date") <= period_end_date)
            )
            .sort(["metric_id", "period_end_date"])
            .group_by("metric_id", maintain_order=True)
            .tail(4)
            .select(frame.columns)
        )
    if denominator_mode == "PRIOR_YEAR_FLOW":
        if period_type == "ANNUAL":
            return (
                frame.filter(
                    (pl.col("period_type") == "ANNUAL")
                    & (pl.col("period_end_date") < period_end_date)
                )
                .sort(["metric_id", "period_end_date"])
                .group_by("metric_id", maintain_order=True)
                .tail(1)
                .select(frame.columns)
            )
        return (
            frame.filter(
                (pl.col("period_type") == "QUARTER")
                & (pl.col("period_end_date") < period_end_date)
            )
            .sort(["metric_id", "period_end_date"])
            .group_by("metric_id", maintain_order=True)
            .tail(4)
            .select(frame.columns)
        )
    return frame


def extract_lineage_components(
    scoped_facts: pl.DataFrame,
    *,
    role: Literal["NUMERATOR", "DENOMINATOR"],
    metrics: tuple[str, ...],
    period_type: str,
    period_end_date: date,
    denominator_mode: DenominatorMode,
    manifest_lookup: dict[str, dict[str, object]],
) -> tuple[LineageComponent, ...]:
    """Assemble sorted ``LineageComponent`` tuples from deduplicated facts and manifest."""
    matched = (
        scoped_facts.filter(pl.col("metric_id").is_in(list(metrics)))
        .sort(["metric_id", "period_end_date", "revision_no", "filing_id"])
        .unique(
            subset=["metric_id", "period_end_date", "period_type"],
            keep="last",
        )
    )
    windowed = select_lineage_window(
        matched,
        role,
        period_type=period_type,
        period_end_date=period_end_date,
        denominator_mode=denominator_mode,
    ).sort(["metric_id", "period_end_date"])
    items: list[LineageComponent] = []
    for row in windowed.to_dicts():
        fid = str(row["filing_id"])
        m_info = manifest_lookup.get(fid, {})
        raw_src_provider = m_info.get("source_provider") or row.get("source_provider")
        raw_connector = m_info.get("connector_name")
        raw_sha = m_info.get("canonical_content_sha256")
        pub_at = row.get("published_at")
        if pub_at is None and isinstance(m_info.get("published_at"), date):
            pub_at = m_info["published_at"]
        eff_at = row.get("effective_available_at")
        pub_basis = row.get("published_at_basis") or m_info.get("published_at_basis")
        items.append(
            LineageComponent(
                role=role,
                metric_id=str(row["metric_id"]),
                period_type=str(row["period_type"]),
                fiscal_year=int(row["fiscal_year"]),
                fiscal_quarter=(
                    int(row["fiscal_quarter"])
                    if row.get("fiscal_quarter") is not None
                    else None
                ),
                period_end_date=row["period_end_date"],
                value=(float(row["value"]) if row.get("value") is not None else None),
                filing_id=fid,
                taxonomy_version=str(row["taxonomy_version"]),
                derivation_method=str(row["derivation_method"]),
                source_provider=(
                    str(raw_src_provider) if raw_src_provider is not None else None
                ),
                connector_name=(
                    str(raw_connector) if raw_connector is not None else None
                ),
                published_at=pub_at if isinstance(pub_at, date) else None,
                published_at_basis=(str(pub_basis) if pub_basis is not None else None),
                effective_available_at=(eff_at if isinstance(eff_at, date) else None),
                canonical_content_sha256=(
                    str(raw_sha) if raw_sha is not None else None
                ),
            )
        )
    return tuple(items)


def build_port_candidate_roots(
    repo_source: Path,
    pinned_source: Path,
    *,
    port_data_root: Path | None = None,
) -> list[Path]:
    """Resolve deduplicated filesystem search roots for Extension Port Parquets."""
    raw_candidates: list[Path] = []
    if port_data_root is not None:
        raw_candidates.extend([port_data_root, port_data_root.parent])
    raw_candidates.extend([repo_source, pinned_source, repo_source.parent])
    for src in (repo_source, pinned_source):
        if (
            src.name.startswith("snap_")
            and src.parent.name == "snapshots"
            and src.parent.parent.name == "published"
            and len(src.parents) >= 3
        ):
            snap_root = src.parents[2]
            raw_candidates.extend([snap_root, snap_root.parent])
    env_root = os.getenv("FININTEL_DATA_ROOT")
    if env_root:
        env_path = Path(env_root).resolve()
        raw_candidates.extend([env_path, env_path.parent])
    else:
        default_resolved = DEFAULT_DATA_ROOT.resolve()
        for maybe_src in (port_data_root, repo_source, pinned_source):
            if maybe_src is not None:
                src_resolved = maybe_src.resolve()
                if (
                    src_resolved == default_resolved
                    or default_resolved in src_resolved.parents
                ):
                    raw_candidates.append(default_resolved)
                    break
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw_candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def load_port_parquet_for_issuer(
    *,
    rel_parquet_path: Path,
    issuer_id: str,
    candidate_roots: Sequence[Path],
) -> pl.DataFrame | None:
    """Load an Extension Port Parquet slice filtered to ``issuer_id`` from candidate roots."""
    for root in candidate_roots:
        parquet_path = root / rel_parquet_path
        if parquet_path.is_file():
            return (
                pl.scan_parquet(parquet_path)
                .filter(pl.col("issuer_id") == issuer_id)
                .collect()
            )
    return None


def read_overlay_ml_scores(
    pinned: ParquetRepository,
    *,
    issuer_id: str,
    statement_scope: str,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame | None:
    """Read and deduplicate ``ml_scores`` rows needed by the market overlay."""
    avail_ml_cols = _available_table_columns(
        pinned, ML_SCORES_TABLE, connection=connection
    )
    if not avail_ml_cols:
        return None
    ml_cols = _select_existing_columns(
        (
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "filing_id",
            "risk_tier",
            "calibrated_distress_prob",
            "risk_market_percentile",
            "relative_risk_lift",
            "model_role",
            "available_at",
            "published_at",
            "effective_available_at",
        ),
        avail_ml_cols,
    )
    if not ({"issuer_id", "period_end_date", "risk_tier"} <= set(ml_cols)):
        return None
    ml_raw = pinned.read_table(
        ML_SCORES_TABLE,
        columns=ml_cols,
        filters={
            "issuer_id": issuer_id,
            "statement_scope": statement_scope,
        },
        connection=connection,
    )
    if ml_raw.is_empty():
        return None
    sort_ml = [
        c
        for c in ("period_type", "period_end_date", "filing_id")
        if c in ml_raw.columns
    ]
    dedup_ml = [
        c
        for c in ("issuer_id", "period_type", "period_end_date")
        if c in ml_raw.columns
    ]
    return ml_raw.sort(sort_ml).unique(subset=dedup_ml, keep="last")


def read_overlay_dtd_facts(
    pinned: ParquetRepository,
    *,
    issuer_id: str,
    statement_scope: str,
    connection: duckdb.DuckDBPyConnection | None,
) -> pl.DataFrame | None:
    """Read facts containing ``BS_TOTAL_ASSETS`` for Merton DTD in the market overlay."""
    dtd_want = (
        "issuer_id statement_scope period_type period_end_date metric_id value "
        "published_at published_at_basis effective_available_at revision_no "
        "revision_reason filing_id source_provider"
    ).split()
    for ft_name in ("financial_facts.parquet", "latest_facts.parquet"):
        ft_avail = _available_table_columns(pinned, ft_name, connection=connection)
        if ft_avail and {"metric_id", "value"} <= set(ft_avail):
            cand_ft = pinned.read_table(
                ft_name,
                columns=_select_existing_columns(dtd_want, ft_avail),
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                },
                connection=connection,
            )
            if not cand_ft.filter(pl.col("metric_id") == "BS_TOTAL_ASSETS").is_empty():
                return cand_ft
    return None


def build_statement_periods_and_rows(
    valid_facts: pl.DataFrame,
    *,
    period_type: str,
    catalog: Mapping[str, Any],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Transform deduplicated financial facts into period headers and sorted statement rows."""
    fy_expr = (
        pl.coalesce(
            pl.col("fiscal_year").cast(pl.Int64, strict=False),
            pl.col("period_end_date").dt.year().cast(pl.Int64),
        )
        if "fiscal_year" in valid_facts.columns
        else pl.col("period_end_date").dt.year().cast(pl.Int64)
    )
    fq_expr = (
        pl.col("fiscal_quarter").cast(pl.Int64, strict=False)
        if "fiscal_quarter" in valid_facts.columns
        else pl.lit(None, dtype=pl.Int64)
    )
    derived_fq = ((pl.col("period_end_date").dt.month().cast(pl.Int64) - 1) // 3) + 1
    label_expr = (
        (pl.lit("Năm ") + fy_expr.cast(pl.String))
        if period_type == "ANNUAL"
        else (
            pl.lit("Q")
            + pl.coalesce(
                pl.when(fq_expr != 0).then(fq_expr).otherwise(None),
                derived_fq,
            ).cast(pl.String)
            + pl.lit("/")
            + fy_expr.cast(pl.String)
        )
    )
    periods_df = (
        valid_facts.unique(subset=["period_end_date"], keep="first")
        .select(
            pl.col("period_end_date"),
            fy_expr.alias("fiscal_year"),
            fq_expr.alias("fiscal_quarter"),
            label_expr.alias("label"),
            (
                pl.col("taxonomy_version").cast(pl.String)
                if "taxonomy_version" in valid_facts.columns
                else pl.lit(None, dtype=pl.String)
            ).alias("taxonomy_version"),
            (
                pl.col("audit_status").cast(pl.String)
                if "audit_status" in valid_facts.columns
                else pl.lit(None, dtype=pl.String)
            ).alias("audit_status"),
        )
        .sort("period_end_date", descending=True)
    )
    periods_out = cast(list[dict[str, object]], periods_df.to_dicts())

    val_f64 = pl.col("value").cast(pl.Float64, strict=False)
    clean_facts = valid_facts.select(
        pl.col("metric_id").cast(pl.String).alias("metric_id"),
        pl.col("period_end_date").dt.strftime("%Y-%m-%d").alias("period_iso"),
        pl.when(val_f64.is_finite()).then(val_f64).otherwise(None).alias("value"),
        (
            pl.col("unit").cast(pl.String)
            if "unit" in valid_facts.columns
            else pl.lit(None, dtype=pl.String)
        ).alias("unit"),
        (
            pl.col("comparable_across_taxonomy").cast(pl.Boolean, strict=False)
            if "comparable_across_taxonomy" in valid_facts.columns
            else pl.lit(None, dtype=pl.Boolean)
        ).alias("comparable_across_taxonomy"),
    )

    rows_out: list[dict[str, object]] = []
    for key_tuple, sub in clean_facts.partition_by(
        "metric_id", as_dict=True, maintain_order=True
    ).items():
        mid = str(key_tuple[0])
        mmeta = catalog.get(mid)
        values: dict[str, float | None] = dict(
            zip(
                sub.get_column("period_iso").to_list(),
                sub.get_column("value").to_list(),
                strict=True,
            )
        )
        if mmeta is not None:
            unit_val = mmeta.unit
            comp_val = mmeta.comparable_across_taxonomy
        else:
            non_null_units = sub.get_column("unit").drop_nulls()
            unit_val = (
                str(non_null_units[0]) if not non_null_units.is_empty() else "VND"
            )
            non_null_comp = sub.get_column("comparable_across_taxonomy").drop_nulls()
            comp_val = bool(non_null_comp[0]) if not non_null_comp.is_empty() else True
        rows_out.append(
            {
                "metric_id": mid,
                "display_name_vn": mmeta.display_name_vn if mmeta else mid,
                "tt200_code": mmeta.tt200_code if mmeta else None,
                "tt99_code": mmeta.tt99_code if mmeta else None,
                "unit": unit_val,
                "is_total": mmeta.is_total if mmeta else False,
                "comparable_across_taxonomy": comp_val,
                "sort_order": mmeta.sort_order if mmeta else 99_999,
                "values": values,
            }
        )
    rows_out.sort(key=lambda r: (int(cast(int, r["sort_order"])), str(r["metric_id"])))
    return periods_out, rows_out
