"""Headless read-only DuckDB query submodule (public facade, ratio lineage catalog, and DashboardQueryService) for Layer 5 serving.

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

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from finintel.analytics.stress_test import (
    StressScenario as StressScenario,
)
from finintel.analytics.stress_test import (
    simulate_financial_stress as simulate_financial_stress,
)
from finintel.analytics.stress_test import (
    summarize_stress_by_sector as summarize_stress_by_sector,
)
from finintel.exceptions import ContractViolationError
from finintel.serving.dictionary import load_statement_metric_catalog
from finintel.analytics.qoq_and_peers import compute_discrete_qoq_growth
from finintel.serving.queries._cache import (
    _ACTIVE_L1B_SNAPSHOT_BY_ROOT,
    _ALL_L1B_CACHES,
    _COHORT_PEER_RANKING_CACHE,
    _COMPANY_PROFILE_CACHE,
    _DEFAULT_READ_TABLE,
    _EMPTY_RATIO_PERIODS_SCHEMA,
    _EMPTY_RED_FLAGS_SCHEMA,
    _INDUSTRY_COMPARISON_CACHE,
    _LATEST_RATIO_PERIOD_CACHE,
    _MAX_QUERY_CACHE_SIZE,
    _PEER_BAND_HISTORY_CACHE,
    _QUERY_CACHE_LOCK,
    _RATIO_LINEAGE_CACHE,
    _RATIO_PERIODS_CACHE,
    _SCREENER_CACHE,
    _SNAPSHOT_OVERVIEW_CACHE,
    _STRESS_INPUTS_CACHE,
    _V2_PROFILE_OPTIONAL_RATIO_COLS,
    ALTMAN_ZONES,
    BENEISH_FLAGS,
    DEFAULT_DATA_ROOT,
    EXCHANGES,
    PEER_BAND_HISTORY_SCHEMA,
    PEER_GROUP_LEVELS,
    PERIOD_TYPES,
    PERIOD_VIEWS,
    RISK_TIERS_ORDERED,
    SCREENER_SCHEMA,
    SCREENER_SORT_FIELDS,
    STATEMENT_SCOPES,
    STATEMENT_TYPES,
    AltmanZone,
    BeneishFlag,
    CompanyProfileData,
    DenominatorMode,
    Exchange,
    IndustryComparisonData,
    LineageComponent,
    PeerGroupLevel,
    PeriodType,
    PeriodView,
    QualityTablesPage,
    RatioLineageCard,
    RatioLineageSpec,
    RiskTier,
    SectorOptionDict,
    SectorRiskDict,
    SectorRiskSummaryDict,
    SnapshotOverview,
    StatementScope,
    _available_table_columns,
    _can_use_query_result_cache,
    _evict_stale_l1b_caches_locked,
    _l1b_root_and_snapshot,
    _pad_schema_columns,
    _resolve_issuer_row,
    _select_existing_columns,
    _wrap_cached_dicts_df,
    clear_query_caches,
    pagination_offset,
    pagination_total_pages,
    pagination_window,
    percent_to_decimal,
    resolve_data_root,
)
from finintel.serving.queries._guards import (
    apply_d8_ratio_guards,
    guard_benchmark_frame_d8,
    normalize_company_display_name,
)
from finintel.serving.queries._helpers import (
    build_statement_periods_and_rows,
    filter_broad_coverage_annual_dates,
)
from finintel.serving.queries._piotroski import (
    PIOTROSKI_EXTRA_FACT_METRICS,
    enrich_ml_scores_piotroski_diagnostics,
)
from finintel.serving.queries.company import (
    ALL_RATIOS,
    RATIO_LINEAGE_CATALOG,
    CompanyQueryMixin,
)
from finintel.serving.queries.overview import OverviewQueryMixin
from finintel.serving.queries.screener import ScreenerQueryMixin
from finintel.serving.queries.stress_and_overlay import StressAndOverlayQueryMixin
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    FINANCIAL_RATIOS_SCHEMA,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    MODEL_CARD_META_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)


class DashboardQueryService(
    OverviewQueryMixin,
    CompanyQueryMixin,
    ScreenerQueryMixin,
    StressAndOverlayQueryMixin,
):
    """Stateless, snapshot-consistent query service over Layer 2/4/6 Parquet outputs."""

    def __init__(self, repository: ParquetRepository) -> None:
        self.repository = repository

    @classmethod
    def from_data_root(cls, data_root: Path | None = None) -> DashboardQueryService:
        return cls(ParquetRepository(data_root=resolve_data_root(data_root)))

    def get_company_ml_scores(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read Layer 4B classical + ML scores for one issuer, or empty schema if ML absent."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])
            return self._read_ml_scores_for_issuer(
                pinned,
                issuer_id=issuer_id,
                statement_scope=statement_scope,
                period_type=period_type,
                connection=connection,
            )

    @staticmethod
    def _read_ml_scores_for_issuer(
        pinned: ParquetRepository,
        *,
        issuer_id: str,
        statement_scope: str,
        period_type: str,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        avail = _available_table_columns(pinned, ML_SCORES_TABLE, connection=connection)
        if not avail:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        cols = _select_existing_columns(list(ML_SCORES_SCHEMA.keys()), avail)
        if not cols:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        try:
            frame = pinned.read_table(
                ML_SCORES_TABLE,
                columns=cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            )
        except FileNotFoundError:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        if frame.is_empty():
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        return _pad_schema_columns(frame, ML_SCORES_SCHEMA).sort(
            ["period_end_date", "filing_id"]
        )

    def get_company_profile(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_view: PeriodView = "TTM",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> CompanyProfileData:
        """Fetch full company health profile with predicate & projection pushdown."""
        clean_ticker = ticker.strip().upper()
        target_period_type = "ANNUAL" if period_view == "Annual" else "QUARTER"

        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            profile_cache_key = (
                (*snap_key, clean_ticker, statement_scope, period_view)
                if snap_key is not None
                else None
            )
            if profile_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_profile = _COMPANY_PROFILE_CACHE.get(profile_cache_key)
                    if cached_profile is not None:
                        _COMPANY_PROFILE_CACHE.move_to_end(profile_cache_key)
                        return cached_profile

            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=(
                    "issuer_id",
                    "ticker",
                    "company_name_vn",
                    "accounting_regime",
                ),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            ratio_cols = _select_existing_columns(
                list(FINANCIAL_RATIOS_SCHEMA.keys()), avail_ratio_cols
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": target_period_type,
                },
                connection=connection,
            ).sort("period_end_date")
            # Gracefully expose V2 core/forensic columns as typed NULL on legacy snapshots.
            missing_v2_exprs = [
                pl.lit(None, dtype=dtype).alias(col)
                for col, dtype in _V2_PROFILE_OPTIONAL_RATIO_COLS.items()
                if col not in ratios.columns
            ]
            if missing_v2_exprs:
                ratios = ratios.with_columns(missing_v2_exprs)
            ratios = apply_d8_ratio_guards(ratios)

            growth_metrics = (
                "IS_NET_REVENUE",
                "IS_NET_INCOME",
                "CF_OPERATING_CASH_FLOW",
            )
            bs_metrics = ("BS_TOTAL_ASSETS", "BS_TOTAL_EQUITY", "BS_TOTAL_LIABILITIES")
            needed_metrics = [
                *growth_metrics,
                *bs_metrics,
                *PIOTROSKI_EXTRA_FACT_METRICS,
            ]

            facts_table = "latest_facts.parquet"
            avail_fact_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )
            if avail_fact_cols is None:
                facts_table = "financial_facts.parquet"
                avail_fact_cols = _available_table_columns(
                    pinned, facts_table, connection=connection
                )
            desired_fact_cols = (
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "metric_id",
                "value",
                "ttm_value",
                "comparable_across_taxonomy",
                "computation_note",
                "effective_available_at",
                "revision_no",
                "filing_id",
            )
            fact_cols = _select_existing_columns(desired_fact_cols, avail_fact_cols)
            sort_fact_cols = [
                col
                for col in (
                    "period_end_date",
                    "taxonomy_version",
                    "effective_available_at",
                    "revision_no",
                    "filing_id",
                )
                if col in fact_cols
            ]
            company_facts = pinned.read_table(
                facts_table,
                columns=fact_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": target_period_type,
                    "metric_id": needed_metrics,
                },
                connection=connection,
            ).sort(sort_fact_cols)

            latest_quality_status: str | None = None
            latest_warnings: tuple[str, ...] = ()
            avail_q_cols = _available_table_columns(
                pinned, QUALITY_SUMMARY_TABLE, connection=connection
            )
            if avail_q_cols:
                q_cols = _select_existing_columns(
                    ("period_end_date", "quality_status", "warning_codes"),
                    avail_q_cols,
                )
                q_filtered = pinned.read_table(
                    QUALITY_SUMMARY_TABLE,
                    columns=q_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": target_period_type,
                    },
                    connection=connection,
                ).sort("period_end_date")
                if not q_filtered.is_empty():
                    q_last = q_filtered.row(-1, named=True)
                    latest_quality_status = str(q_last["quality_status"])
                    raw_warnings = q_last.get("warning_codes") or []
                    latest_warnings = tuple(str(code) for code in raw_warnings)

            latest_taxonomy: str | None = (
                str(ratios["taxonomy_version"][-1]) if not ratios.is_empty() else None
            )

            flow_value_col = (
                "ttm_value"
                if period_view == "TTM" and "ttm_value" in company_facts.columns
                else "value"
            )
            growth_series = (
                company_facts.filter(pl.col("metric_id").is_in(growth_metrics))
                .group_by(["period_end_date", "fiscal_year", "fiscal_quarter"])
                .agg(
                    (
                        pl.col("taxonomy_version").last().alias("taxonomy_version")
                        if "taxonomy_version" in company_facts.columns
                        else pl.lit(None, dtype=pl.String).alias("taxonomy_version")
                    ),
                    *(
                        pl.col(flow_value_col)
                        .filter(pl.col("metric_id") == metric)
                        .last()
                        .alias(metric)
                        for metric in growth_metrics
                    ),
                )
                .sort("period_end_date")
            )
            if period_view == "Quarterly" and not growth_series.is_empty():
                growth_series = compute_discrete_qoq_growth(
                    growth_series.with_columns(pl.lit("QUARTER").alias("period_type")),
                    metric_columns=(
                        ("IS_NET_REVENUE", "revenue_growth_qoq"),
                        ("IS_NET_INCOME", "net_income_growth_qoq"),
                    ),
                ).drop("period_type")

            bs_series = (
                company_facts.filter(pl.col("metric_id").is_in(bs_metrics))
                .group_by(["period_end_date", "fiscal_year", "fiscal_quarter"])
                .agg(
                    *(
                        pl.col("value")
                        .filter(pl.col("metric_id") == metric)
                        .last()
                        .alias(metric)
                        for metric in bs_metrics
                    )
                )
                .sort("period_end_date")
            )
            debt_series = ratios.select("period_end_date", "total_debt")
            capital_structure_series = bs_series.join(
                debt_series, on="period_end_date", how="left"
            ).sort("period_end_date")

            dupont_series = ratios.select(
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "tax_burden",
                "interest_burden",
                "ebit_margin",
                "asset_turnover",
                "equity_multiplier_total",
                "roe_total",
                "dupont_valid_flag",
            ).sort("period_end_date")

            avail_rf_cols = _available_table_columns(
                pinned, RED_FLAGS_TABLE, connection=connection
            )
            if avail_rf_cols:
                rf_cols = _select_existing_columns(
                    list(_EMPTY_RED_FLAGS_SCHEMA.keys()), avail_rf_cols
                )
                red_flags_raw = pinned.read_table(
                    RED_FLAGS_TABLE,
                    columns=rf_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": target_period_type,
                    },
                    connection=connection,
                )
                red_flags = (
                    red_flags_raw.sort(
                        ["period_end_date", "severity", "flag_code"],
                        descending=[True, False, False],
                    )
                    if not red_flags_raw.is_empty()
                    else pl.DataFrame(schema=_EMPTY_RED_FLAGS_SCHEMA)
                )
            else:
                red_flags = pl.DataFrame(schema=_EMPTY_RED_FLAGS_SCHEMA)

            ml_scores = enrich_ml_scores_piotroski_diagnostics(
                self._read_ml_scores_for_issuer(
                    pinned,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                    period_type=target_period_type,
                    connection=connection,
                ),
                ratios,
                company_facts,
                period_type=target_period_type,
            )

            raw_company_name = issuer_row.get("company_name_vn")
            profile_result = CompanyProfileData(
                ticker=clean_ticker,
                issuer_id=issuer_id,
                company_name_vn=normalize_company_display_name(
                    str(raw_company_name) if raw_company_name is not None else None,
                    ticker=clean_ticker,
                ),
                accounting_regime=str(issuer_row["accounting_regime"]),
                statement_scope=statement_scope,
                period_view=period_view,
                latest_taxonomy_version=latest_taxonomy,
                latest_quality_status=latest_quality_status,
                latest_warning_codes=latest_warnings,
                growth_series=_wrap_cached_dicts_df(growth_series),
                capital_structure_series=_wrap_cached_dicts_df(
                    capital_structure_series
                ),
                dupont_series=_wrap_cached_dicts_df(dupont_series),
                red_flags=_wrap_cached_dicts_df(red_flags),
                ratios_table=_wrap_cached_dicts_df(ratios),
                ml_scores=_wrap_cached_dicts_df(ml_scores),
            )
            if profile_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _COMPANY_PROFILE_CACHE[profile_cache_key] = profile_result
                    while len(_COMPANY_PROFILE_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _COMPANY_PROFILE_CACHE.popitem(last=False)
            return profile_result

    def get_peer_band_history(
        self,
        ticker: str,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        metric_name: str = "roe_total",
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Return historical peer band trajectory ([P25, Median, P75] + peer_group_level)."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            hist_cache_key = (
                (*snap_key, clean_ticker, statement_scope, period_type, metric_name)
                if snap_key is not None
                else None
            )
            if hist_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_hist = _PEER_BAND_HISTORY_CACHE.get(hist_cache_key)
                    if cached_hist is not None:
                        _PEER_BAND_HISTORY_CACHE.move_to_end(hist_cache_key)
                        return cached_hist

            with self._parquet_cursor(pinned, connection) as q_conn:
                issuer_row = _resolve_issuer_row(
                    pinned,
                    ticker,
                    columns=("issuer_id", "ticker"),
                    connection=q_conn,
                )
                issuer_id = str(issuer_row["issuer_id"])

                avail = _available_table_columns(
                    pinned, BENCHMARKS_TABLE, connection=q_conn
                )
                if not avail:
                    return pl.DataFrame(schema=PEER_BAND_HISTORY_SCHEMA)
                cols = _select_existing_columns(
                    list(PEER_BAND_HISTORY_SCHEMA.keys()), avail
                )
                frame = pinned.read_table(
                    BENCHMARKS_TABLE,
                    columns=cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "metric_or_ratio_name": metric_name,
                    },
                    connection=q_conn,
                )
                if frame.is_empty():
                    return pl.DataFrame(schema=PEER_BAND_HISTORY_SCHEMA)
                frame = guard_benchmark_frame_d8(
                    frame,
                    pinned=pinned,
                    metric_name=metric_name,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    join_on="period_end_date",
                    issuer_id=issuer_id,
                    connection=q_conn,
                )
                hist_res = _wrap_cached_dicts_df(
                    _pad_schema_columns(frame, PEER_BAND_HISTORY_SCHEMA).sort(
                        "period_end_date"
                    )
                )
                if hist_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        _PEER_BAND_HISTORY_CACHE[hist_cache_key] = hist_res
                        while len(_PEER_BAND_HISTORY_CACHE) > _MAX_QUERY_CACHE_SIZE:
                            _PEER_BAND_HISTORY_CACHE.popitem(last=False)
                return hist_res

    def list_ratio_periods(
        self,
        ticker: str,
        *,
        statement_scope: str | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """List available ratio periods for one ticker using ``issuer_id`` pushdown."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            rp_cache_key = (
                (*snap_key, clean_ticker, statement_scope)
                if snap_key is not None
                else None
            )
            if rp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_rp = _RATIO_PERIODS_CACHE.get(rp_cache_key)
                    if cached_rp is not None:
                        _RATIO_PERIODS_CACHE.move_to_end(rp_cache_key)
                        return cached_rp.clone()

            issuer_rows = pinned.read_table(
                "issuers.parquet",
                columns=["issuer_id", "ticker"],
                filters={"ticker": clean_ticker},
                connection=connection,
            )
            if issuer_rows.is_empty():
                return pl.DataFrame(schema=_EMPTY_RATIO_PERIODS_SCHEMA)
            issuer_id = str(issuer_rows.row(0, named=True)["issuer_id"])

            filters: dict[str, object] = {"issuer_id": issuer_id}
            if statement_scope is not None:
                filters["statement_scope"] = statement_scope
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=list(_EMPTY_RATIO_PERIODS_SCHEMA.keys()),
                filters=filters,
                connection=connection,
            )
            rp_result = ratios.sort(
                ["period_end_date", "period_type"], descending=[True, False]
            )
            if rp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _RATIO_PERIODS_CACHE[rp_cache_key] = rp_result
                    while len(_RATIO_PERIODS_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _RATIO_PERIODS_CACHE.popitem(last=False)
            return rp_result.clone()

    def list_available_periods(
        self,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[date]:
        """List all distinct ``period_end_date`` values available in ``financial_ratios``."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            avail = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail or "period_end_date" not in avail:
                return []
            use_annual_coverage = period_type == "ANNUAL" and "issuer_id" in avail
            dates_df = pinned.read_table(
                RATIOS_TABLE,
                columns=["issuer_id", "period_end_date"]
                if use_annual_coverage
                else ["period_end_date"],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            )
            if dates_df.is_empty():
                return []
            if use_annual_coverage:
                counts_df = (
                    dates_df.filter(pl.col("period_end_date").is_not_null())
                    .group_by("period_end_date")
                    .agg(pl.col("issuer_id").n_unique().cast(pl.Int64).alias("n"))
                )
                pairs = [
                    (d, int(n))
                    for d, n in zip(
                        counts_df.get_column("period_end_date").to_list(),
                        counts_df.get_column("n").to_list(),
                        strict=True,
                    )
                    if isinstance(d, date)
                ]
                return filter_broad_coverage_annual_dates(pairs)
            unique_dates = (
                dates_df.get_column("period_end_date")
                .drop_nulls()
                .unique()
                .sort(descending=True)
                .to_list()
            )
            return [d for d in unique_dates if isinstance(d, date)]

    def get_company_statements(
        self,
        ticker: str,
        *,
        statement_type: str = "INCOME_STATEMENT",
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        periods_limit: int = 8,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        """Return multi-period normalized VAS statement lines for one issuer."""
        if statement_type not in STATEMENT_TYPES:
            raise ValueError(f"Unsupported statement_type: {statement_type!r}")
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        if isinstance(periods_limit, bool) or not (1 <= periods_limit <= 20):
            raise ValueError("periods_limit must be an integer between 1 and 20.")

        clean_ticker = ticker.strip().upper()
        catalog = load_statement_metric_catalog()
        type_metric_ids = [
            mid
            for mid, mmeta in catalog.items()
            if mmeta.statement_type == statement_type
        ]

        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            facts_table = "financial_facts.parquet"
            avail_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )
            if not avail_cols:
                facts_table = "latest_facts.parquet"
                avail_cols = _available_table_columns(
                    pinned, facts_table, connection=connection
                )
            if not avail_cols:
                return issuer_id, [], []

            desired_cols = (
                "metric_id",
                "statement_type",
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "audit_status",
                "revision_no",
                "filing_id",
                "value",
                "unit",
                "comparable_across_taxonomy",
            )
            cols = _select_existing_columns(desired_cols, avail_cols)
            filters: dict[str, object] = {
                "issuer_id": issuer_id,
                "statement_scope": statement_scope,
                "period_type": period_type,
            }
            if "statement_type" in avail_cols:
                filters["statement_type"] = statement_type
            elif type_metric_ids:
                filters["metric_id"] = type_metric_ids

            facts = pinned.read_table(
                facts_table,
                columns=cols,
                filters=filters,
                connection=connection,
            )
            if facts.is_empty():
                return issuer_id, [], []

            sort_keys = [
                col
                for col in ("period_end_date", "metric_id", "revision_no", "filing_id")
                if col in facts.columns
            ]
            if sort_keys:
                facts = facts.sort(sort_keys)
            facts = facts.unique(subset=["metric_id", "period_end_date"], keep="last")

            recent_dates = (
                facts.select("period_end_date")
                .drop_nulls()
                .unique()
                .sort("period_end_date", descending=True)
                .head(periods_limit)
                .get_column("period_end_date")
                .to_list()
            )
            valid_facts = facts.filter(
                pl.col("period_end_date").is_in(recent_dates)
                & pl.col("period_end_date").is_not_null()
            )
            if valid_facts.is_empty():
                return issuer_id, [], []

            periods_out, rows_out = build_statement_periods_and_rows(
                valid_facts, period_type=period_type, catalog=catalog
            )
        return issuer_id, periods_out, rows_out

    def get_model_card(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> dict[str, Any]:
        """Read the persisted Model Card exclusively from active snapshot ``model_card_meta`` (H-04)."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_cols = _available_table_columns(
                pinned, MODEL_CARD_META_TABLE, connection=connection
            )
            if not avail_cols or "model_card_json" not in avail_cols:
                raise ValueError(
                    "Model card metadata was not found in active snapshot."
                )
            card_df = pinned.read_table(
                MODEL_CARD_META_TABLE,
                columns=["model_card_id", "model_card_json"],
                connection=connection,
            )
            if card_df.is_empty():
                raise ValueError(
                    "Model card metadata was not found in active snapshot."
                )
            row = card_df.sort("model_card_id").row(0, named=True)
            raw_json = row.get("model_card_json")
            if not isinstance(raw_json, str) or not raw_json.strip():
                raise ContractViolationError(
                    "Model card metadata table has invalid or empty JSON payload."
                )
            try:
                parsed = json.loads(raw_json)
            except ValueError as exc:
                raise ContractViolationError(
                    "Model card metadata table contains malformed JSON payload."
                ) from exc
            if not isinstance(parsed, dict):
                raise ContractViolationError(
                    "Model card metadata payload must be a JSON object."
                )
            case_studies = parsed.get("case_studies")
            if isinstance(case_studies, list):
                for cs in case_studies:
                    if isinstance(cs, dict) and cs.get("company_name_vn") is not None:
                        raw_cs_ticker = cs.get("ticker")
                        cs["company_name_vn"] = normalize_company_display_name(
                            str(cs["company_name_vn"]),
                            ticker=str(raw_cs_ticker)
                            if raw_cs_ticker is not None
                            else None,
                        )
            if parsed.get("tt99_mrm_gate") is None and isinstance(
                parsed.get("taxonomy_drift_psi"), dict
            ):
                from finintel.ml.mrm_tt99 import evaluate_tt99_gate_from_psi_report

                parsed["tt99_mrm_gate"] = evaluate_tt99_gate_from_psi_report(
                    parsed["taxonomy_drift_psi"]
                )
            return parsed


__all__ = [
    "ALL_RATIOS",
    "ALTMAN_ZONES",
    "AltmanZone",
    "BENEISH_FLAGS",
    "BeneishFlag",
    "CompanyProfileData",
    "CompanyQueryMixin",
    "DEFAULT_DATA_ROOT",
    "DashboardQueryService",
    "DenominatorMode",
    "EXCHANGES",
    "Exchange",
    "IndustryComparisonData",
    "LineageComponent",
    "OverviewQueryMixin",
    "PEER_BAND_HISTORY_SCHEMA",
    "PEER_GROUP_LEVELS",
    "PERIOD_TYPES",
    "PERIOD_VIEWS",
    "PeerGroupLevel",
    "PeriodType",
    "PeriodView",
    "QualityTablesPage",
    "RATIO_LINEAGE_CATALOG",
    "RISK_TIERS_ORDERED",
    "RatioLineageCard",
    "RatioLineageSpec",
    "RiskTier",
    "SCREENER_SCHEMA",
    "SCREENER_SORT_FIELDS",
    "STATEMENT_SCOPES",
    "STATEMENT_TYPES",
    "ScreenerQueryMixin",
    "SectorOptionDict",
    "SectorRiskDict",
    "SectorRiskSummaryDict",
    "SnapshotOverview",
    "StatementScope",
    "StressAndOverlayQueryMixin",
    "StressScenario",
    "_ACTIVE_L1B_SNAPSHOT_BY_ROOT",
    "_ALL_L1B_CACHES",
    "_COHORT_PEER_RANKING_CACHE",
    "_COMPANY_PROFILE_CACHE",
    "_DEFAULT_READ_TABLE",
    "_INDUSTRY_COMPARISON_CACHE",
    "_LATEST_RATIO_PERIOD_CACHE",
    "_PEER_BAND_HISTORY_CACHE",
    "_QUERY_CACHE_LOCK",
    "_RATIO_LINEAGE_CACHE",
    "_RATIO_PERIODS_CACHE",
    "_SCREENER_CACHE",
    "_SNAPSHOT_OVERVIEW_CACHE",
    "_STRESS_INPUTS_CACHE",
    "_l1b_root_and_snapshot",
    "clear_query_caches",
    "pagination_offset",
    "pagination_total_pages",
    "pagination_window",
    "percent_to_decimal",
    "resolve_data_root",
    "simulate_financial_stress",
    "summarize_stress_by_sector",
]
