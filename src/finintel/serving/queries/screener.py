"""Headless read-only DuckDB query submodule (multi-criteria market screener, industry comparison, and quality audit pages) for Layer 5 serving.

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

from datetime import date
from typing import Any, Literal

import duckdb
import polars as pl

from finintel.serving.queries._cache import (
    _COHORT_PEER_RANKING_CACHE,
    _INDUSTRY_COMPARISON_CACHE,
    _MAX_QUERY_CACHE_SIZE,
    _QUERY_CACHE_LOCK,
    _SCREENER_CACHE,
    SCREENER_SCHEMA,
    IndustryComparisonData,
    QualityTablesPage,
    _available_table_columns,
    _can_use_query_result_cache,
    _evict_stale_l1b_caches_locked,
    _pad_schema_columns,
    _QueryServiceBase,
    _resolve_issuer_row,
    _select_existing_columns,
    _sql_read_page,
    _validate_pagination,
    _wrap_cached_dicts_df,
)
from finintel.serving.queries._guards import (
    apply_d8_ratio_guards,
    guard_benchmark_frame_d8,
    guard_ratio_by_denominator,
    normalize_company_display_name_expr,
)
from finintel.serving.queries._helpers import (
    empty_industry_comparison,
    opt_finite_float,
    read_ml_scores_slice,
    read_red_flag_counts_for_period,
    resolve_benchmark_cohort_fallback,
    resolve_latest_ratio_period,
    validate_screener_filters,
)
from finintel.serving.queries._screener_filters import (
    ML_SCREENER_DESIRED,
    ML_SCREENER_JOIN_SCHEMA,
    ScreenerExtraFilters,
    apply_extended_screener_filters,
    extract_extended_filters,
    read_pit_valuation_slice,
    validate_extended_filters,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    PIT_VALUATION_MART_TABLE,
    QUALITY_SUMMARY_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
)


class ScreenerQueryMixin(_QueryServiceBase):
    """Market screener, cohort peer comparison, and quality/quarantine table queries."""

    @staticmethod
    def _resolve_screener_period_date(
        pinned: ParquetRepository,
        *,
        period_end_date: date | None,
        statement_scope: str,
        period_type: str,
        snap_key: tuple[object, ...] | None,
        connection: duckdb.DuckDBPyConnection | None,
    ) -> date | None:
        if period_end_date is not None:
            return period_end_date
        resolved_date, _ = resolve_latest_ratio_period(
            pinned,
            statement_scope=statement_scope,
            period_type=period_type,
            snap_key=snap_key,
            connection=connection,
        )
        return resolved_date

    @staticmethod
    def _build_screener_frame(
        pinned: ParquetRepository,
        *,
        avail_ratio_cols: set[str],
        resolved_date: date,
        period_type: str,
        statement_scope: str,
        icb_code_level2: str | None,
        exchange: str | None,
        min_f_score: int | None,
        altman_zone: str | None,
        effective_risk_tiers: tuple[str, ...] | None,
        beneish_flag: str | None,
        max_distress_prob: float | None,
        min_distress_prob: float | None,
        max_red_flags: int | None,
        min_red_flags: int | None,
        clean_q: str | None,
        extra_filters: ScreenerExtraFilters,
        sort_by: str,
        sort_dir: Literal["asc", "desc"],
        connection: duckdb.DuckDBPyConnection | None,
    ) -> pl.DataFrame:
        issuer_cols = [
            "issuer_id",
            "ticker",
            "company_name_vn",
            "exchange",
            "accounting_regime",
            "icb_code_level2",
            "icb_name_level2",
            "icb_code_level3",
            "icb_name_level3",
        ]
        issuer_filter_map: dict[str, object] = {}
        if icb_code_level2 is not None:
            issuer_filter_map["icb_code_level2"] = icb_code_level2
        if exchange is not None:
            issuer_filter_map["exchange"] = exchange
        issuers = pinned.read_table(
            "issuers.parquet",
            columns=issuer_cols,
            filters=issuer_filter_map or None,
            connection=connection,
        )
        if issuers.is_empty():
            return pl.DataFrame(schema=SCREENER_SCHEMA)
        issuers = issuers.with_columns(normalize_company_display_name_expr())

        ratio_desired = (
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "taxonomy_version",
            "net_revenue_ttm",
            "borrowing_cost_ttm",
            "roe_total",
            "roe_parent",
            "roa",
            "eps_ttm_estimated",
            "ebit_margin",
            "liabilities_to_assets",
            "debt_to_equity",
            "interest_coverage",
            "cfo_ttm",
            "fcf_ttm",
            "ocf_to_total_liabilities",
            "revenue_growth_yoy",
            "accruals_to_assets",
            "core_operating_margin",
            "non_core_distortion_to_assets",
            "core_to_ebt_ratio",
        )
        ratio_cols = _select_existing_columns(ratio_desired, avail_ratio_cols)
        ratio_filters: dict[str, object] = {
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": resolved_date,
        }
        if issuer_filter_map:
            ratio_filters["issuer_id"] = issuers["issuer_id"].unique().to_list()
        ratios = pinned.read_table(
            RATIOS_TABLE,
            columns=ratio_cols,
            filters=ratio_filters,
            connection=connection,
        )
        if ratios.is_empty():
            return pl.DataFrame(schema=SCREENER_SCHEMA)
        ratios = apply_d8_ratio_guards(ratios)

        for forensic_col in (
            "core_operating_margin",
            "non_core_distortion_to_assets",
            "core_to_ebt_ratio",
        ):
            if forensic_col not in ratios.columns:
                ratios = ratios.with_columns(
                    pl.lit(None, dtype=pl.Float64).alias(forensic_col)
                )

        rf_counts = read_red_flag_counts_for_period(
            pinned,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            include_critical=True,
            connection=connection,
        )

        ml_slice = read_ml_scores_slice(
            pinned,
            desired_cols=ML_SCREENER_DESIRED,
            target_schema=ML_SCREENER_JOIN_SCHEMA,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            connection=connection,
        )

        avail_mart_cols = _available_table_columns(
            pinned, PIT_VALUATION_MART_TABLE, connection=connection
        )
        mart_slice = read_pit_valuation_slice(
            pinned,
            avail_mart_cols=avail_mart_cols,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            issuer_ids=issuers["issuer_id"].unique().to_list()
            if issuer_filter_map
            else None,
            connection=connection,
        )

        joined = (
            ratios.join(issuers, on="issuer_id", how="inner")
            .join(rf_counts, on="issuer_id", how="left")
            .join(ml_slice, on="issuer_id", how="left")
            .join(mart_slice, on="issuer_id", how="left")
            .with_columns(
                # Triggered alert counts from LEFT JOIN (0 alerts triggered, not a financial metric).
                pl.coalesce(pl.col("red_flag_count"), pl.lit(0, dtype=pl.Int64)).alias(
                    "red_flag_count"
                ),
                pl.coalesce(
                    pl.col("critical_red_flag_count"), pl.lit(0, dtype=pl.Int64)
                ).alias("critical_red_flag_count"),
            )
        )

        if min_f_score is not None:
            joined = joined.filter(
                pl.col("piotroski_f_score").is_not_null()
                & (pl.col("piotroski_f_score") >= min_f_score)
            )
        if altman_zone is not None:
            joined = joined.filter(pl.col("altman_zone") == altman_zone)
        if effective_risk_tiers is not None:
            if len(effective_risk_tiers) == 1:
                joined = joined.filter(pl.col("risk_tier") == effective_risk_tiers[0])
            else:
                joined = joined.filter(
                    pl.col("risk_tier").is_in(list(effective_risk_tiers))
                )
        if beneish_flag is not None:
            joined = joined.filter(pl.col("beneish_flag") == beneish_flag)
        if max_distress_prob is not None:
            joined = joined.filter(
                pl.col("calibrated_distress_prob").is_not_null()
                & (pl.col("calibrated_distress_prob") <= float(max_distress_prob))
            )
        if min_distress_prob is not None:
            joined = joined.filter(
                pl.col("calibrated_distress_prob").is_not_null()
                & (pl.col("calibrated_distress_prob") >= float(min_distress_prob))
            )
        if max_red_flags is not None:
            joined = joined.filter(pl.col("red_flag_count") <= max_red_flags)
        if min_red_flags is not None:
            joined = joined.filter(pl.col("red_flag_count") >= min_red_flags)
        if clean_q is not None:
            joined = joined.filter(
                pl.col("ticker").str.to_lowercase().str.contains(clean_q, literal=True)
                | pl.col("company_name_vn")
                .fill_null("")
                .str.to_lowercase()
                .str.contains(clean_q, literal=True)
            )
        joined = apply_extended_screener_filters(joined, extra_filters)

        desc_flag = sort_dir == "desc"
        if sort_by == "calibrated_distress_prob" and desc_flag:
            sort_cols = ["calibrated_distress_prob", "red_flag_count", "ticker"]
            sort_desc = [True, True, False]
        elif sort_by == "ticker":
            sort_cols = ["ticker"]
            sort_desc = [desc_flag]
        else:
            sort_cols = [sort_by, "ticker"]
            sort_desc = [desc_flag, False]

        return _pad_schema_columns(joined, SCREENER_SCHEMA).sort(
            sort_cols,
            descending=sort_desc,
            nulls_last=True,
        )

    def screen_market(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        exchange: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        risk_tier: str | None = None,
        risk_tiers: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        min_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        min_red_flags: int | None = None,
        q: str | None = None,
        sort_by: str = "calibrated_distress_prob",
        sort_dir: Literal["asc", "desc"] = "desc",
        limit: int = 50,
        offset: int = 0,
        extra_filters: ScreenerExtraFilters | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
        **extra_kwargs: object,
    ) -> tuple[pl.DataFrame, int]:
        """Historical cross-sectional market screener over the latest published snapshot.

        Uses ``LEFT JOIN`` onto ``ml_scores_and_explanations`` and ``accounting_red_flags``
        so screening degrades cleanly when a snapshot has no ML or red-flag tables.
        """
        _validate_pagination(limit, offset, max_limit=200)
        resolved_ext = extract_extended_filters(extra_filters, extra_kwargs)
        validate_extended_filters(resolved_ext)
        clean_q, effective_risk_tiers = validate_screener_filters(
            period_type=period_type,
            statement_scope=statement_scope,
            exchange=exchange,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            risk_tier=risk_tier,
            risk_tiers=risk_tiers,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            min_distress_prob=min_distress_prob,
            max_red_flags=max_red_flags,
            min_red_flags=min_red_flags,
            q=q,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

            snap_key = pinned._snapshot_cache_key()
            resolved_date = self._resolve_screener_period_date(
                pinned,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                period_type=period_type,
                snap_key=snap_key,
                connection=connection,
            )
            if resolved_date is None:
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

            screener_cache_key = (
                (
                    *snap_key,
                    period_type,
                    resolved_date,
                    statement_scope,
                    icb_code_level2,
                    exchange,
                    min_f_score,
                    altman_zone,
                    effective_risk_tiers,
                    beneish_flag,
                    float(max_distress_prob) if max_distress_prob is not None else None,
                    float(min_distress_prob) if min_distress_prob is not None else None,
                    max_red_flags,
                    min_red_flags,
                    clean_q,
                    sort_by,
                    sort_dir,
                    limit,
                    offset,
                    *resolved_ext.cache_tuple(),
                )
                if snap_key is not None and _can_use_query_result_cache(pinned)
                else None
            )
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_screener = _SCREENER_CACHE.get(screener_cache_key)
                    if cached_screener is not None:
                        _SCREENER_CACHE.move_to_end(screener_cache_key)
                        return cached_screener[0], cached_screener[1]

            projected = self._build_screener_frame(
                pinned,
                avail_ratio_cols=avail_ratio_cols,
                resolved_date=resolved_date,
                period_type=period_type,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                exchange=exchange,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                effective_risk_tiers=effective_risk_tiers,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                clean_q=clean_q,
                extra_filters=resolved_ext,
                sort_by=sort_by,
                sort_dir=sort_dir,
                connection=connection,
            )
            total_count = projected.height
            sliced = _wrap_cached_dicts_df(projected.slice(offset, limit))
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _SCREENER_CACHE[screener_cache_key] = (sliced, total_count)
                    while len(_SCREENER_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _SCREENER_CACHE.popitem(last=False)
            return sliced, total_count

    def export_screener(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        exchange: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        risk_tier: str | None = None,
        risk_tiers: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        min_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        min_red_flags: int | None = None,
        q: str | None = None,
        sort_by: str = "calibrated_distress_prob",
        sort_dir: Literal["asc", "desc"] = "desc",
        extra_filters: ScreenerExtraFilters | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
        **extra_kwargs: object,
    ) -> pl.DataFrame:
        """Return 100% of filtered & sorted screener rows without pagination cap (UI-05a)."""
        resolved_ext = extract_extended_filters(extra_filters, extra_kwargs)
        validate_extended_filters(resolved_ext)
        clean_q, effective_risk_tiers = validate_screener_filters(
            period_type=period_type,
            statement_scope=statement_scope,
            exchange=exchange,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            risk_tier=risk_tier,
            risk_tiers=risk_tiers,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            min_distress_prob=min_distress_prob,
            max_red_flags=max_red_flags,
            min_red_flags=min_red_flags,
            q=q,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return pl.DataFrame(schema=SCREENER_SCHEMA)

            snap_key = pinned._snapshot_cache_key()
            resolved_date = self._resolve_screener_period_date(
                pinned,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                period_type=period_type,
                snap_key=snap_key,
                connection=connection,
            )
            if resolved_date is None:
                return pl.DataFrame(schema=SCREENER_SCHEMA)

            return self._build_screener_frame(
                pinned,
                avail_ratio_cols=avail_ratio_cols,
                resolved_date=resolved_date,
                period_type=period_type,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                exchange=exchange,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                effective_risk_tiers=effective_risk_tiers,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                clean_q=clean_q,
                extra_filters=resolved_ext,
                sort_by=sort_by,
                sort_dir=sort_dir,
                connection=connection,
            )

    def get_industry_comparison(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metric_name: str = "roe_total",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> IndustryComparisonData:
        """Fetch cross-sectional peer comparison with predicate & projection pushdown."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            comp_cache_key = (
                (
                    *snap_key,
                    clean_ticker,
                    statement_scope,
                    period_type,
                    period_end_date,
                    metric_name,
                )
                if snap_key is not None
                else None
            )
            if comp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_comp = _INDUSTRY_COMPARISON_CACHE.get(comp_cache_key)
                    if cached_comp is not None:
                        _INDUSTRY_COMPARISON_CACHE.move_to_end(comp_cache_key)
                        return cached_comp

            with self._parquet_cursor(pinned, connection) as q_conn:
                issuer_row = _resolve_issuer_row(
                    pinned,
                    clean_ticker,
                    columns=(
                        "issuer_id",
                        "ticker",
                        "industry_snapshot_date",
                        "accounting_regime",
                        "icb_code_level2",
                        "icb_code_level3",
                    ),
                    connection=q_conn,
                )
                issuer_id = str(issuer_row["issuer_id"])
                snapshot_date = issuer_row.get("industry_snapshot_date")
                resolved_snapshot_date = (
                    snapshot_date if isinstance(snapshot_date, date) else None
                )

                avail_bench_cols = _available_table_columns(
                    pinned, BENCHMARKS_TABLE, connection=q_conn
                )
                if not avail_bench_cols:
                    return empty_industry_comparison(
                        ticker=clean_ticker,
                        issuer_id=issuer_id,
                        statement_scope=statement_scope,
                        period_type=period_type,
                        period_end_date=period_end_date or date.today(),
                        metric_name=metric_name,
                        industry_snapshot_date=resolved_snapshot_date,
                    )

                target_cols = _select_existing_columns(
                    (
                        "issuer_id",
                        "accounting_regime",
                        "statement_scope",
                        "period_type",
                        "period_end_date",
                        "metric_or_ratio_name",
                        "icb_code_used",
                        "peer_group_level",
                        "peer_count",
                        "value",
                        "peer_median",
                        "peer_p25",
                        "peer_p75",
                        "peer_iqr",
                        "percentile_rank",
                        "robust_z_score",
                    ),
                    avail_bench_cols,
                )
                target_filters: dict[str, object] = {
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "metric_or_ratio_name": metric_name,
                }
                if period_end_date is not None:
                    target_filters["period_end_date"] = period_end_date

                target_rows = pinned.read_table(
                    BENCHMARKS_TABLE,
                    columns=target_cols,
                    filters=target_filters,
                    connection=q_conn,
                )
                if period_end_date is None and not target_rows.is_empty():
                    target_rows = target_rows.sort("period_end_date").tail(1)

                if target_rows.is_empty():
                    resolved_end_opt: date | None = period_end_date
                    if resolved_end_opt is None:
                        avail_ratio = _available_table_columns(
                            pinned, RATIOS_TABLE, connection=q_conn
                        )
                        if avail_ratio and "period_end_date" in avail_ratio:
                            r_dates = pinned.read_table(
                                RATIOS_TABLE,
                                columns=["period_end_date"],
                                filters={
                                    "issuer_id": issuer_id,
                                    "statement_scope": statement_scope,
                                    "period_type": period_type,
                                },
                                connection=q_conn,
                            )
                            if not r_dates.is_empty():
                                raw_d = r_dates.get_column("period_end_date").max()
                                if isinstance(raw_d, date):
                                    resolved_end_opt = raw_d
                        if resolved_end_opt is None:
                            resolved_end_opt, _ = resolve_latest_ratio_period(
                                pinned,
                                statement_scope=statement_scope,
                                period_type=period_type,
                                snap_key=snap_key,
                                connection=q_conn,
                            )
                    acc_regime = str(issuer_row.get("accounting_regime") or "CORPORATE")
                    raw_l3 = issuer_row.get("icb_code_level3")
                    raw_l2 = issuer_row.get("icb_code_level2")
                    fb = (
                        resolve_benchmark_cohort_fallback(
                            pinned,
                            avail_bench_cols=avail_bench_cols,
                            accounting_regime=acc_regime,
                            icb_code_level3=(
                                str(raw_l3) if raw_l3 is not None else None
                            ),
                            icb_code_level2=(
                                str(raw_l2) if raw_l2 is not None else None
                            ),
                            statement_scope=statement_scope,
                            period_type=period_type,
                            period_end_date=resolved_end_opt,
                            metric_names=[metric_name],
                            connection=q_conn,
                        )
                        if isinstance(resolved_end_opt, date)
                        else {}
                    )
                    if metric_name not in fb:
                        return empty_industry_comparison(
                            ticker=clean_ticker,
                            issuer_id=issuer_id,
                            statement_scope=statement_scope,
                            period_type=period_type,
                            period_end_date=resolved_end_opt or date.today(),
                            metric_name=metric_name,
                            industry_snapshot_date=resolved_snapshot_date,
                        )
                    selected: dict[str, Any] = {
                        **fb[metric_name],
                        "accounting_regime": acc_regime,
                        "period_end_date": resolved_end_opt,
                        "value": None,
                        "percentile_rank": None,
                        "robust_z_score": None,
                    }
                else:
                    selected = target_rows.row(0, named=True)
                raw_resolved_end = selected["period_end_date"]
                resolved_end = (
                    raw_resolved_end
                    if isinstance(raw_resolved_end, date)
                    else (period_end_date or date.today())
                )
                peer_level = str(selected["peer_group_level"])
                icb_used = selected.get("icb_code_used")
                accounting_regime = str(selected["accounting_regime"])
                raw_peer_count = selected.get("peer_count")
                peer_count = (
                    int(raw_peer_count)
                    if isinstance(raw_peer_count, int)
                    and not isinstance(raw_peer_count, bool)
                    else 0
                )
                company_val = opt_finite_float(selected.get("value"))
                if company_val is not None and metric_name in (
                    "ebit_margin",
                    "interest_coverage",
                ):
                    denom_col = (
                        "net_revenue_ttm"
                        if metric_name == "ebit_margin"
                        else "borrowing_cost_ttm"
                    )
                    avail_r_cols = _available_table_columns(
                        pinned, RATIOS_TABLE, connection=q_conn
                    )
                    if avail_r_cols and denom_col in avail_r_cols:
                        denom_df = pinned.read_table(
                            RATIOS_TABLE,
                            columns=[denom_col],
                            filters={
                                "issuer_id": issuer_id,
                                "statement_scope": statement_scope,
                                "period_type": period_type,
                                "period_end_date": resolved_end,
                            },
                            connection=q_conn,
                        )
                        if not denom_df.is_empty():
                            denom_val = opt_finite_float(
                                denom_df.get_column(denom_col)[0]
                            )
                            company_val, _ = guard_ratio_by_denominator(
                                metric_name,
                                company_val,
                                net_revenue_ttm=(
                                    denom_val if metric_name == "ebit_margin" else None
                                ),
                                borrowing_cost_ttm=(
                                    denom_val
                                    if metric_name == "interest_coverage"
                                    else None
                                ),
                            )

                cohort_cache_key = (
                    (
                        *snap_key,
                        accounting_regime,
                        statement_scope,
                        period_type,
                        resolved_end,
                        metric_name,
                        peer_level,
                        icb_used,
                    )
                    if snap_key is not None
                    else None
                )
                peer_ranking: pl.DataFrame | None = None
                if cohort_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        peer_ranking = _COHORT_PEER_RANKING_CACHE.get(cohort_cache_key)
                        if peer_ranking is not None:
                            _COHORT_PEER_RANKING_CACHE.move_to_end(cohort_cache_key)

                if peer_ranking is None:
                    cohort_cols = _select_existing_columns(
                        (
                            "issuer_id",
                            "value",
                            "percentile_rank",
                            "robust_z_score",
                            "peer_group_level",
                            "icb_code_used",
                        ),
                        avail_bench_cols,
                    )
                    cohort_filters: dict[str, object] = {
                        "accounting_regime": accounting_regime,
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_end,
                        "metric_or_ratio_name": metric_name,
                        "peer_group_level": peer_level,
                        "icb_code_used": icb_used,
                    }
                    cohort = pinned.read_table(
                        BENCHMARKS_TABLE,
                        columns=cohort_cols,
                        filters=cohort_filters,
                        connection=q_conn,
                    )
                    cohort_issuer_ids = cohort["issuer_id"].unique().to_list()
                    if not cohort.is_empty():
                        cohort = guard_benchmark_frame_d8(
                            cohort,
                            pinned=pinned,
                            metric_name=metric_name,
                            statement_scope=statement_scope,
                            period_type=period_type,
                            join_on="issuer_id",
                            issuer_ids=cohort_issuer_ids,
                            period_end_date=resolved_end,
                            connection=q_conn,
                        )
                    peer_issuers = pinned.read_table(
                        "issuers.parquet",
                        columns=[
                            "issuer_id",
                            "ticker",
                            "company_name_vn",
                            "icb_name_level2",
                            "icb_name_level3",
                        ],
                        filters={"issuer_id": cohort_issuer_ids},
                        connection=q_conn,
                    )
                    if not peer_issuers.is_empty():
                        peer_issuers = peer_issuers.with_columns(
                            normalize_company_display_name_expr()
                        )

                    peer_ranking = _wrap_cached_dicts_df(
                        cohort.join(peer_issuers, on="issuer_id", how="left")
                        .select(
                            "ticker",
                            "company_name_vn",
                            "value",
                            "percentile_rank",
                            "robust_z_score",
                            "peer_group_level",
                            "icb_code_used",
                            "icb_name_level3",
                            "icb_name_level2",
                        )
                        .sort("value", descending=True, nulls_last=True)
                    )
                    if cohort_cache_key is not None:
                        with _QUERY_CACHE_LOCK:
                            _evict_stale_l1b_caches_locked(snap_key)
                            _COHORT_PEER_RANKING_CACHE[cohort_cache_key] = peer_ranking
                            while (
                                len(_COHORT_PEER_RANKING_CACHE) > _MAX_QUERY_CACHE_SIZE
                            ):
                                _COHORT_PEER_RANKING_CACHE.popitem(last=False)

                comp_result = IndustryComparisonData(
                    ticker=clean_ticker,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    period_end_date=resolved_end,
                    metric_name=metric_name,
                    peer_group_level=peer_level,
                    icb_code_used=str(icb_used) if icb_used is not None else None,
                    peer_count=peer_count,
                    company_value=company_val,
                    peer_median=opt_finite_float(selected.get("peer_median")),
                    peer_p25=opt_finite_float(selected.get("peer_p25")),
                    peer_p75=opt_finite_float(selected.get("peer_p75")),
                    peer_iqr=opt_finite_float(selected.get("peer_iqr")),
                    percentile_rank=opt_finite_float(selected.get("percentile_rank")),
                    robust_z_score=opt_finite_float(selected.get("robust_z_score")),
                    industry_snapshot_date=resolved_snapshot_date,
                    peer_ranking=peer_ranking,
                )
                if comp_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        _INDUSTRY_COMPARISON_CACHE[comp_cache_key] = comp_result
                        while len(_INDUSTRY_COMPARISON_CACHE) > _MAX_QUERY_CACHE_SIZE:
                            _INDUSTRY_COMPARISON_CACHE.popitem(last=False)
                return comp_result

    def get_quality_summary_page(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Return a paginated slice of ``quality_summary.parquet`` and its total row count."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_read_page(
                pinned,
                QUALITY_SUMMARY_TABLE,
                schema=QUALITY_SUMMARY_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "statement_scope" ASC, "period_type" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )

    def get_audit_quarantine_page(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Return a paginated slice of ``audit_quarantine.parquet`` and its total row count."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_read_page(
                pinned,
                AUDIT_QUARANTINE_TABLE,
                schema=AUDIT_QUARANTINE_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "filing_id" ASC, "rule_code" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )

    def get_quality_tables(
        self,
        limit: int = 200,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> QualityTablesPage:
        """Return paginated quality summary and audit quarantine tables with total counts."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            summary, total_summary = _sql_read_page(
                pinned,
                QUALITY_SUMMARY_TABLE,
                schema=QUALITY_SUMMARY_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "statement_scope" ASC, "period_type" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )
            quarantine, total_quarantine = _sql_read_page(
                pinned,
                AUDIT_QUARANTINE_TABLE,
                schema=AUDIT_QUARANTINE_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "filing_id" ASC, "rule_code" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )
            return QualityTablesPage(
                quality_summary=summary,
                audit_quarantine=quarantine,
                total_summary=total_summary,
                total_quarantine=total_quarantine,
            )
