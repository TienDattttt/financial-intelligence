"""Headless read-only DuckDB query submodule (snapshot overview, issuers, filings, sector risk, and market changes) for Layer 5 serving.

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

from collections.abc import Sequence
from datetime import date
from typing import cast

import duckdb
import polars as pl

from finintel.serving.queries._cache import (
    _ALTMAN_SEVERITY_RANK,
    _MAX_QUERY_CACHE_SIZE,
    _QUERY_CACHE_LOCK,
    _RISK_TIER_SEVERITY_RANK,
    _SNAPSHOT_OVERVIEW_CACHE,
    PERIOD_TYPES,
    STATEMENT_SCOPES,
    SectorRiskDict,
    SectorRiskSummaryDict,
    SnapshotOverview,
    _available_table_columns,
    _evict_stale_l1b_caches_locked,
    _pad_schema_columns,
    _QueryServiceBase,
    _resolve_issuer_row,
    _select_existing_columns,
    _sql_count_quality_statuses,
    _sql_count_rows,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    FILINGS_MANIFEST_TABLE,
    ISSUERS_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)


class OverviewQueryMixin(_QueryServiceBase):
    """Snapshot overview, issuer directory, filings, and market-change queries."""

    def get_snapshot_overview(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> SnapshotOverview:
        """Return active snapshot metadata and aggregate row counts via DuckDB SQL."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            meta = pinned.get_snapshot_metadata(connection=connection)
            raw_snap_id = meta.get("snapshot_id")
            raw_published_at = meta.get("published_at")
            raw_backend = meta.get("storage_backend")
            snap_id = str(raw_snap_id) if isinstance(raw_snap_id, str) else None
            published_at = (
                str(raw_published_at) if isinstance(raw_published_at, str) else None
            )
            storage_backend = (
                str(raw_backend) if isinstance(raw_backend, str) else "parquet"
            )
            snap_key = pinned._snapshot_cache_key()
            if snap_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_overview = _SNAPSHOT_OVERVIEW_CACHE.get(snap_key)
                    if cached_overview is not None:
                        _SNAPSHOT_OVERVIEW_CACHE.move_to_end(snap_key)
                        return cached_overview

            total_issuers = _sql_count_rows(
                pinned, "issuers.parquet", connection=connection
            )
            total_ratio_periods = _sql_count_rows(
                pinned, RATIOS_TABLE, connection=connection
            )
            pass_periods, warning_periods = _sql_count_quality_statuses(
                pinned, connection=connection
            )
            quarantined_rows = _sql_count_rows(
                pinned, AUDIT_QUARANTINE_TABLE, connection=connection
            )
            overview = SnapshotOverview(
                snapshot_id=snap_id,
                published_at=published_at,
                total_issuers=total_issuers,
                total_ratio_periods=total_ratio_periods,
                pass_periods=pass_periods,
                warning_periods=warning_periods,
                quarantined_rows=quarantined_rows,
                storage_backend=storage_backend,
            )
            if snap_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _SNAPSHOT_OVERVIEW_CACHE[snap_key] = overview
                    while len(_SNAPSHOT_OVERVIEW_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _SNAPSHOT_OVERVIEW_CACHE.popitem(last=False)
            return overview

    def count_issuers(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> int:
        """Return total issuer count in the active snapshot via DuckDB SQL."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_count_rows(pinned, "issuers.parquet", connection=connection)

    def list_issuers(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """List issuers sorted by ticker, with optional pagination."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer.")

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail = _available_table_columns(
                pinned, "issuers.parquet", connection=connection
            )
            cols = _select_existing_columns(list(ISSUERS_SCHEMA.keys()), avail)
            if not cols:
                return pl.DataFrame(schema=ISSUERS_SCHEMA)
            issuers = pinned.read_table(
                "issuers.parquet", columns=cols, connection=connection
            ).sort("ticker")
            if offset > 0 or limit is not None:
                return issuers.slice(offset, limit)
            return issuers

    def get_company_filings(
        self,
        ticker: str,
        *,
        statement_scope: str | None = None,
        limit: int = 100,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, list[dict[str, object]]]:
        """Return audit status, audit opinion, and revision history for an issuer."""
        if statement_scope is not None and statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if isinstance(limit, bool) or not (1 <= limit <= 200):
            raise ValueError("limit must be an integer between 1 and 200.")

        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            source_table: str | None = FILINGS_MANIFEST_TABLE
            avail_cols = _available_table_columns(
                pinned, FILINGS_MANIFEST_TABLE, connection=connection
            )
            if not avail_cols:
                source_table = "financial_facts.parquet"
                avail_cols = _available_table_columns(
                    pinned, source_table, connection=connection
                )
            if not avail_cols or not source_table:
                return issuer_id, []

            desired = (
                "filing_id",
                "statement_type",
                "statement_scope",
                "period_type",
                "period_end_date",
                "taxonomy_version",
                "published_at",
                "published_at_basis",
                "effective_available_at",
                "audit_status",
                "audit_opinion",
                "revision_no",
                "revision_reason",
                "source_provider",
            )
            cols = _select_existing_columns(desired, avail_cols)
            filters: dict[str, object] = {"issuer_id": issuer_id}
            if statement_scope is not None and "statement_scope" in avail_cols:
                filters["statement_scope"] = statement_scope

            df = pinned.read_table(
                source_table,
                columns=cols,
                filters=filters,
                connection=connection,
            )
            if df.is_empty():
                return issuer_id, []

            unique_subset = [
                col for col in ("filing_id", "statement_type") if col in df.columns
            ]
            if unique_subset:
                df = df.unique(subset=unique_subset, keep="last")
            sort_cols = [
                col
                for col in ("period_end_date", "revision_no", "filing_id")
                if col in df.columns
            ]
            sort_desc = [col in ("period_end_date", "revision_no") for col in sort_cols]
            if sort_cols:
                df = df.sort(sort_cols, descending=sort_desc, nulls_last=True)

            head_df = df.head(limit)
            rev_expr = (
                pl.col("revision_no").cast(pl.Int64, strict=False)
                if "revision_no" in head_df.columns
                else pl.lit(None, dtype=pl.Int64)
            )
            filing_expr = (
                pl.coalesce(pl.col("filing_id").cast(pl.String), pl.lit(""))
                if "filing_id" in head_df.columns
                else pl.lit("")
            )
            out_df = head_df.select(
                filing_expr.alias("filing_id"),
                (
                    pl.col("statement_type")
                    if "statement_type" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("statement_type"),
                (
                    pl.col("statement_scope")
                    if "statement_scope" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("statement_scope"),
                (
                    pl.col("period_type")
                    if "period_type" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("period_type"),
                (
                    pl.col("period_end_date")
                    if "period_end_date" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("period_end_date"),
                (
                    pl.col("taxonomy_version")
                    if "taxonomy_version" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("taxonomy_version"),
                (
                    pl.col("published_at")
                    if "published_at" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("published_at"),
                (
                    pl.col("published_at_basis")
                    if "published_at_basis" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("published_at_basis"),
                (
                    pl.col("effective_available_at")
                    if "effective_available_at" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("effective_available_at"),
                (
                    pl.col("audit_status")
                    if "audit_status" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("audit_status"),
                (
                    pl.col("audit_opinion")
                    if "audit_opinion" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("audit_opinion"),
                rev_expr.alias("revision_no"),
                (
                    pl.col("revision_reason")
                    if "revision_reason" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("revision_reason"),
                (rev_expr.is_not_null() & (rev_expr > 1)).alias("has_restatement"),
                (
                    pl.col("source_provider")
                    if "source_provider" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("source_provider"),
            )
            items = cast(list[dict[str, object]], out_df.to_dicts())
        return issuer_id, items

    @staticmethod
    def _compute_market_changes_from_pinned(
        pinned: ParquetRepository,
        *,
        statement_scope: str,
        period_type: str,
        target_date: date | None = None,
        tickers: Sequence[str] | None = None,
        limit: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[dict[str, object]]:
        """Compute period-over-period Altman/distress/red-flag changes between T and T-1."""
        issuer_cols = ["issuer_id", "ticker", "company_name_vn", "icb_name_level2"]
        issuer_filters: dict[str, object] | None = (
            {"ticker": list(tickers)} if tickers else None
        )
        issuers = pinned.read_table(
            "issuers.parquet",
            columns=issuer_cols,
            filters=issuer_filters,
            connection=connection,
        )
        if issuers.is_empty():
            return []
        issuer_ids = issuers["issuer_id"].unique().to_list()

        avail_ratio_cols = _available_table_columns(
            pinned, RATIOS_TABLE, connection=connection
        )
        if not avail_ratio_cols:
            return []
        ratio_filters: dict[str, object] = {
            "statement_scope": statement_scope,
            "period_type": period_type,
        }
        if tickers:
            ratio_filters["issuer_id"] = issuer_ids
        ratio_dates_df = pinned.read_table(
            RATIOS_TABLE,
            columns=["issuer_id", "period_end_date"],
            filters=ratio_filters,
            connection=connection,
        ).filter(pl.col("period_end_date").is_not_null())
        if target_date is not None:
            ratio_dates_df = ratio_dates_df.filter(
                pl.col("period_end_date") <= target_date
            )
        if ratio_dates_df.is_empty():
            return []

        pairs_df = (
            ratio_dates_df.unique()
            .sort(["issuer_id", "period_end_date"], descending=[False, True])
            .group_by("issuer_id", maintain_order=True)
            .agg(
                pl.col("period_end_date").first().alias("curr_period_end_date"),
                pl.when(pl.len() > 1)
                .then(pl.col("period_end_date").get(1))
                .otherwise(None)
                .cast(pl.Date)
                .alias("prev_period_end_date"),
            )
        )
        if target_date is not None:
            pairs_df = pairs_df.filter(pl.col("curr_period_end_date") == target_date)
        if pairs_df.is_empty():
            return []

        date_list = (
            pl.concat(
                [
                    pairs_df.select(pl.col("curr_period_end_date").alias("d")),
                    pairs_df.select(
                        pl.col("prev_period_end_date").alias("d")
                    ).drop_nulls(),
                ]
            )
            .unique()
            .sort("d")
            .get_column("d")
            .to_list()
        )

        ml_pair_schema = pl.Schema(
            {
                "issuer_id": pl.String,
                "period_end_date": pl.Date,
                "altman_zone": pl.String,
                "calibrated_distress_prob": pl.Float64,
                "risk_tier": pl.String,
                "risk_market_percentile": pl.Float64,
                "market_base_rate": pl.Float64,
                "relative_risk_lift": pl.Float64,
            }
        )
        avail_ml_cols = _available_table_columns(
            pinned, ML_SCORES_TABLE, connection=connection
        )
        ml_dedup = pl.DataFrame(schema=ml_pair_schema)
        if avail_ml_cols:
            ml_cols = _select_existing_columns(
                (
                    "issuer_id",
                    "period_end_date",
                    "altman_zone",
                    "calibrated_distress_prob",
                    "risk_tier",
                    "risk_market_percentile",
                    "market_base_rate",
                    "relative_risk_lift",
                    "filing_id",
                ),
                avail_ml_cols,
            )
            ml_filters: dict[str, object] = {
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": date_list,
            }
            if tickers:
                ml_filters["issuer_id"] = issuer_ids
            ml_df = pinned.read_table(
                ML_SCORES_TABLE,
                columns=ml_cols,
                filters=ml_filters,
                connection=connection,
            ).filter(pl.col("period_end_date").is_not_null())
            if not ml_df.is_empty():
                sort_ml = [
                    c
                    for c in ("issuer_id", "period_end_date", "filing_id")
                    if c in ml_df.columns
                ]
                ml_dedup = _pad_schema_columns(
                    ml_df.sort(sort_ml).unique(
                        subset=["issuer_id", "period_end_date"], keep="last"
                    ),
                    ml_pair_schema,
                )
                float_ml_cols = [
                    "calibrated_distress_prob",
                    "risk_market_percentile",
                    "market_base_rate",
                    "relative_risk_lift",
                ]
                ml_dedup = ml_dedup.with_columns(
                    pl.when(pl.col(c).is_finite())
                    .then(pl.col(c))
                    .otherwise(None)
                    .alias(c)
                    for c in float_ml_cols
                )

        rf_agg = pl.DataFrame(
            schema={
                "issuer_id": pl.String,
                "period_end_date": pl.Date,
                "flags": pl.List(pl.String),
            }
        )
        avail_rf_cols = _available_table_columns(
            pinned, RED_FLAGS_TABLE, connection=connection
        )
        if avail_rf_cols:
            rf_filters: dict[str, object] = {
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": date_list,
            }
            if tickers:
                rf_filters["issuer_id"] = issuer_ids
            rf_df = pinned.read_table(
                RED_FLAGS_TABLE,
                columns=["issuer_id", "period_end_date", "flag_code"],
                filters=rf_filters,
                connection=connection,
            ).filter(pl.col("period_end_date").is_not_null())
            if not rf_df.is_empty():
                rf_agg = rf_df.group_by(["issuer_id", "period_end_date"]).agg(
                    pl.col("flag_code").cast(pl.String).unique().sort().alias("flags")
                )

        curr_ml_df = ml_dedup.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("curr_period_end_date"),
            pl.col("altman_zone").alias("altman_zone_curr"),
            pl.col("calibrated_distress_prob").alias("distress_prob_curr"),
            pl.col("risk_tier").alias("risk_tier_curr"),
            pl.col("risk_market_percentile").alias("risk_market_percentile_curr"),
            pl.col("market_base_rate").alias("market_base_rate_curr"),
            pl.col("relative_risk_lift").alias("relative_risk_lift_curr"),
        )
        prev_ml_df = ml_dedup.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("prev_period_end_date"),
            pl.col("altman_zone").alias("altman_zone_prev"),
            pl.col("calibrated_distress_prob").alias("distress_prob_prev"),
            pl.col("risk_tier").alias("risk_tier_prev"),
        )
        curr_rf_df = rf_agg.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("curr_period_end_date"),
            pl.col("flags").alias("_curr_flags"),
        )
        prev_rf_df = rf_agg.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("prev_period_end_date"),
            pl.col("flags").alias("_prev_flags"),
        )

        empty_flags = pl.lit([], dtype=pl.List(pl.String))
        joined = (
            pairs_df.join(issuers, on="issuer_id", how="left")
            .join(curr_ml_df, on=["issuer_id", "curr_period_end_date"], how="left")
            .join(prev_ml_df, on=["issuer_id", "prev_period_end_date"], how="left")
            .join(curr_rf_df, on=["issuer_id", "curr_period_end_date"], how="left")
            .join(prev_rf_df, on=["issuer_id", "prev_period_end_date"], how="left")
            .with_columns(
                pl.coalesce(
                    pl.when(pl.col("ticker").is_not_null() & (pl.col("ticker") != ""))
                    .then(pl.col("ticker"))
                    .otherwise(None),
                    pl.col("issuer_id"),
                ).alias("ticker"),
                pl.coalesce(pl.col("_curr_flags"), empty_flags).alias("_curr_flags"),
                pl.coalesce(pl.col("_prev_flags"), empty_flags).alias("_prev_flags"),
            )
            .with_columns(
                (
                    pl.when(
                        pl.col("distress_prob_curr").is_not_null()
                        & pl.col("distress_prob_prev").is_not_null()
                    )
                    .then(pl.col("distress_prob_curr") - pl.col("distress_prob_prev"))
                    .otherwise(None)
                    .cast(pl.Float64)
                ).alias("distress_prob_delta"),
                pl.col("_curr_flags")
                .list.len()
                .cast(pl.Int64)
                .alias("red_flag_count_curr"),
                pl.when(pl.col("prev_period_end_date").is_not_null())
                .then(pl.col("_prev_flags").list.len().cast(pl.Int64))
                .otherwise(pl.lit(0, dtype=pl.Int64))
                .alias("red_flag_count_prev"),
                pl.col("_curr_flags")
                .list.set_difference(pl.col("_prev_flags"))
                .list.sort()
                .alias("new_red_flag_codes"),
                pl.when(pl.col("prev_period_end_date").is_not_null())
                .then(
                    pl.col("_prev_flags")
                    .list.set_difference(pl.col("_curr_flags"))
                    .list.sort()
                )
                .otherwise(empty_flags)
                .alias("resolved_red_flag_codes"),
            )
            .with_columns(
                pl.col("new_red_flag_codes")
                .list.len()
                .cast(pl.Int64)
                .alias("_new_flag_count"),
                (
                    (
                        pl.col("altman_zone_curr").is_not_null()
                        & pl.col("altman_zone_prev").is_not_null()
                        & (
                            pl.col("altman_zone_curr").replace_strict(
                                _ALTMAN_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                            > pl.col("altman_zone_prev").replace_strict(
                                _ALTMAN_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                        )
                    )
                    | (
                        pl.col("risk_tier_curr").is_not_null()
                        & pl.col("risk_tier_prev").is_not_null()
                        & (
                            pl.col("risk_tier_curr").replace_strict(
                                _RISK_TIER_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                            > pl.col("risk_tier_prev").replace_strict(
                                _RISK_TIER_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                        )
                    )
                    | (
                        pl.col("distress_prob_delta").is_not_null()
                        & (pl.col("distress_prob_delta") >= 0.003)
                    )
                    | (pl.col("new_red_flag_codes").list.len() > 0)
                ).alias("deteriorated"),
            )
            .sort(
                ["deteriorated", "_new_flag_count", "distress_prob_delta", "ticker"],
                descending=[True, True, True, False],
                nulls_last=True,
            )
            .head(limit)
            .select(
                "issuer_id",
                "ticker",
                "company_name_vn",
                "icb_name_level2",
                "prev_period_end_date",
                "curr_period_end_date",
                "altman_zone_prev",
                "altman_zone_curr",
                "distress_prob_prev",
                "distress_prob_curr",
                "distress_prob_delta",
                "risk_tier_prev",
                "risk_tier_curr",
                "risk_market_percentile_curr",
                "market_base_rate_curr",
                "relative_risk_lift_curr",
                "red_flag_count_prev",
                "red_flag_count_curr",
                "new_red_flag_codes",
                "resolved_red_flag_codes",
                "deteriorated",
            )
        )
        return cast(list[dict[str, object]], joined.to_dicts())

    def get_sector_risk_summary(
        self,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> SectorRiskSummaryDict:
        """Aggregate market-wide and ICB Level 2 risk indicators for the overview heatmap."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
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

            resolved_date = period_end_date
            if resolved_date is None:
                dates_df = pinned.read_table(
                    RATIOS_TABLE,
                    columns=["period_end_date"],
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                    },
                    connection=connection,
                )
                raw_max = (
                    dates_df.get_column("period_end_date").max()
                    if not dates_df.is_empty()
                    else None
                )
                if not isinstance(raw_max, date):
                    return {
                        "period_end_date": None,
                        "total_issuers": 0,
                        "safe_zone_count": 0,
                        "grey_zone_count": 0,
                        "distress_zone_count": 0,
                        "beneish_flagged_count": 0,
                        "high_red_flag_count": 0,
                        "sectors": [],
                        "top_deteriorating": [],
                    }
                resolved_date = raw_max

            ratio_cols = _select_existing_columns(
                ("issuer_id", "total_debt"), avail_ratio_cols
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if ratios.is_empty():
                return {
                    "period_end_date": resolved_date,
                    "total_issuers": 0,
                    "safe_zone_count": 0,
                    "grey_zone_count": 0,
                    "distress_zone_count": 0,
                    "beneish_flagged_count": 0,
                    "high_red_flag_count": 0,
                    "sectors": [],
                    "top_deteriorating": [],
                }
            if "total_debt" not in ratios.columns:
                ratios = ratios.with_columns(
                    pl.lit(0.0, dtype=pl.Float64).alias("total_debt")
                )

            issuers = pinned.read_table(
                "issuers.parquet",
                columns=["issuer_id", "icb_code_level2", "icb_name_level2"],
                connection=connection,
            )

            avail_rf = _available_table_columns(
                pinned, RED_FLAGS_TABLE, connection=connection
            )
            if avail_rf:
                rf_slice = pinned.read_table(
                    RED_FLAGS_TABLE,
                    columns=["issuer_id"],
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_date,
                    },
                    connection=connection,
                )
                rf_counts = (
                    rf_slice.group_by("issuer_id").agg(
                        pl.len().cast(pl.Int64).alias("red_flag_count")
                    )
                    if not rf_slice.is_empty()
                    else pl.DataFrame(
                        schema={"issuer_id": pl.String, "red_flag_count": pl.Int64}
                    )
                )
            else:
                rf_counts = pl.DataFrame(
                    schema={"issuer_id": pl.String, "red_flag_count": pl.Int64}
                )

            ml_join_schema = pl.Schema(
                {
                    "issuer_id": pl.String,
                    "altman_zone": pl.String,
                    "beneish_flag": pl.String,
                    "calibrated_distress_prob": pl.Float64,
                }
            )
            avail_ml = _available_table_columns(
                pinned, ML_SCORES_TABLE, connection=connection
            )
            if avail_ml:
                ml_cols = _select_existing_columns(
                    (
                        "issuer_id",
                        "filing_id",
                        "altman_zone",
                        "beneish_flag",
                        "calibrated_distress_prob",
                    ),
                    avail_ml,
                )
                ml_raw = pinned.read_table(
                    ML_SCORES_TABLE,
                    columns=ml_cols,
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_date,
                    },
                    connection=connection,
                )
                if not ml_raw.is_empty():
                    sort_ml = (
                        ["issuer_id", "filing_id"]
                        if "filing_id" in ml_raw.columns
                        else ["issuer_id"]
                    )
                    ml_slice = _pad_schema_columns(
                        ml_raw.sort(sort_ml).unique(subset=["issuer_id"], keep="last"),
                        ml_join_schema,
                    )
                else:
                    ml_slice = pl.DataFrame(schema=ml_join_schema)
            else:
                ml_slice = pl.DataFrame(schema=ml_join_schema)

            joined = (
                ratios.join(issuers, on="issuer_id", how="inner")
                .join(rf_counts, on="issuer_id", how="left")
                .join(ml_slice, on="issuer_id", how="left")
                .with_columns(
                    pl.coalesce(
                        pl.col("red_flag_count"), pl.lit(0, dtype=pl.Int64)
                    ).alias("red_flag_count"),
                    pl.coalesce(pl.col("icb_code_level2"), pl.lit("OTHER")).alias(
                        "icb_code_level2"
                    ),
                    pl.coalesce(pl.col("icb_name_level2"), pl.lit("Ngành khác")).alias(
                        "icb_name_level2"
                    ),
                    pl.coalesce(
                        pl.col("total_debt"), pl.lit(0.0, dtype=pl.Float64)
                    ).alias("total_debt"),
                )
            )

            sector_df = (
                joined.group_by(["icb_code_level2", "icb_name_level2"])
                .agg(
                    pl.len().cast(pl.Int64).alias("issuer_count"),
                    (pl.col("altman_zone") == "SAFE_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("safe_zone_count"),
                    (pl.col("altman_zone") == "GREY_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("grey_zone_count"),
                    (pl.col("altman_zone") == "DISTRESS_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("distress_zone_count"),
                    (pl.col("beneish_flag") == "HIGH_MANIPULATION_RISK_SCREEN")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("beneish_flagged_count"),
                    (pl.col("red_flag_count") >= 2)
                    .sum()
                    .cast(pl.Int64)
                    .alias("high_red_flag_count"),
                    pl.col("calibrated_distress_prob")
                    .median()
                    .alias("median_distress_prob"),
                    pl.col("red_flag_count").mean().alias("avg_red_flags"),
                    pl.col("total_debt").sum().alias("total_debt_sum"),
                )
                .with_columns(
                    pl.col("icb_code_level2").cast(pl.String),
                    pl.col("icb_name_level2").cast(pl.String),
                    pl.when(
                        pl.col("median_distress_prob")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("median_distress_prob").cast(pl.Float64, strict=False))
                    .otherwise(None)
                    .alias("median_distress_prob"),
                    pl.when(
                        pl.col("avg_red_flags")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("avg_red_flags").cast(pl.Float64, strict=False))
                    .otherwise(pl.lit(0.0, dtype=pl.Float64))
                    .alias("avg_red_flags"),
                    pl.when(
                        pl.col("total_debt_sum")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("total_debt_sum").cast(pl.Float64, strict=False))
                    .otherwise(pl.lit(0.0, dtype=pl.Float64))
                    .alias("total_debt_sum"),
                )
                .sort(["distress_zone_count", "issuer_count"], descending=[True, True])
            )

            sectors_out = cast(list[SectorRiskDict], sector_df.to_dicts())

            deteriorating = [
                c
                for c in self._compute_market_changes_from_pinned(
                    pinned,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    target_date=resolved_date,
                    limit=30,
                    connection=connection,
                )
                if c["deteriorated"]
            ][:10]

            return {
                "period_end_date": resolved_date,
                "total_issuers": joined.height,
                "safe_zone_count": sum(s["safe_zone_count"] for s in sectors_out),
                "grey_zone_count": sum(s["grey_zone_count"] for s in sectors_out),
                "distress_zone_count": sum(
                    s["distress_zone_count"] for s in sectors_out
                ),
                "beneish_flagged_count": sum(
                    s["beneish_flagged_count"] for s in sectors_out
                ),
                "high_red_flag_count": sum(
                    s["high_red_flag_count"] for s in sectors_out
                ),
                "sectors": sectors_out,
                "top_deteriorating": deteriorating,
            }

    def get_market_changes(
        self,
        *,
        tickers: Sequence[str] | None = None,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        limit: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[dict[str, object]]:
        """Return latest period-over-period risk changes for requested tickers or market-wide."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        if isinstance(limit, bool) or not (1 <= limit <= 100):
            raise ValueError("limit must be an integer between 1 and 100.")
        clean_tickers = (
            tuple(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
            if tickers
            else None
        )
        with self.repository.snapshot_session(connection=connection) as pinned:
            return self._compute_market_changes_from_pinned(
                pinned,
                statement_scope=statement_scope,
                period_type=period_type,
                tickers=clean_tickers,
                limit=limit,
                connection=connection,
            )
