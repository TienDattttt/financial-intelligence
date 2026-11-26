"""Headless read-only DuckDB query submodule (IMF WP/15/216 stress test inputs, metadata, and EOD market overlay) for Layer 5 serving.

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

import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.market_valuation import (
    build_price_risk_overlay,
    compute_pit_valuation_series,
)
from finintel.analytics.stress_test import REQUIRED_STRESS_COLUMNS
from finintel.exceptions import ContractViolationError
from finintel.serving.queries._cache import (
    _LATEST_RATIO_PERIOD_CACHE,
    _MAX_QUERY_CACHE_SIZE,
    _QUERY_CACHE_LOCK,
    _STRESS_INPUTS_CACHE,
    DEFAULT_DATA_ROOT,
    PERIOD_TYPES,
    STATEMENT_SCOPES,
    SectorOptionDict,
    _available_table_columns,
    _evict_stale_l1b_caches_locked,
    _QueryServiceBase,
    _resolve_issuer_row,
    _select_existing_columns,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    FILINGS_MANIFEST_TABLE,
    FINANCIAL_RATIOS_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
)


class StressAndOverlayQueryMixin(_QueryServiceBase):
    """Stress-test snapshot inputs, metadata options, and PIT market price overlay."""

    def get_stress_test_snapshot_inputs(
        self,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        *,
        tickers: Sequence[str] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read exact required stress-test inputs and ICB Level 2 sector metadata for one period slice."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        target_tickers: tuple[str, ...] | None = (
            tuple(
                dict.fromkeys(
                    t.strip().upper()
                    for t in tickers
                    if isinstance(t, str) and t.strip()
                )
            )
            if tickers is not None
            else None
        )

        def _filter_tickers(frame: pl.DataFrame) -> pl.DataFrame:
            if target_tickers is None:
                return frame
            if not target_tickers or frame.is_empty():
                return frame.clear()
            return frame.filter(pl.col("ticker").is_in(list(target_tickers)))

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if avail_ratio_cols is None:
                raise ContractViolationError(
                    "Active snapshot is missing financial_ratios table."
                )
            missing_stress_cols = [
                col for col in REQUIRED_STRESS_COLUMNS if col not in avail_ratio_cols
            ]
            if missing_stress_cols:
                raise ContractViolationError(
                    "Active snapshot is missing required stress-test intermediate columns: "
                    + ", ".join(missing_stress_cols)
                    + "."
                )

            snap_key = pinned._snapshot_cache_key()
            resolved_date = period_end_date
            if resolved_date is None:
                period_cache_key = (
                    (*snap_key, statement_scope, period_type)
                    if snap_key is not None
                    else None
                )
                if period_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        resolved_date = _LATEST_RATIO_PERIOD_CACHE.get(period_cache_key)
                        if resolved_date is not None:
                            _LATEST_RATIO_PERIOD_CACHE.move_to_end(period_cache_key)
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
                    if dates_df.is_empty():
                        return pl.DataFrame(
                            schema={
                                "issuer_id": pl.String,
                                "ticker": pl.String,
                                **{
                                    col: FINANCIAL_RATIOS_SCHEMA[col]
                                    for col in REQUIRED_STRESS_COLUMNS
                                },
                                "company_name_vn": pl.String,
                                "icb_code_level2": pl.String,
                                "icb_name_level2": pl.String,
                            }
                        )
                    raw_max = dates_df.get_column("period_end_date").max()
                    if not isinstance(raw_max, date):
                        raise ContractViolationError(
                            "Unable to resolve latest period_end_date for stress-test inputs."
                        )
                    resolved_date = raw_max
                    if period_cache_key is not None:
                        with _QUERY_CACHE_LOCK:
                            _evict_stale_l1b_caches_locked(snap_key)
                            _LATEST_RATIO_PERIOD_CACHE[period_cache_key] = resolved_date
                            while (
                                len(_LATEST_RATIO_PERIOD_CACHE) > _MAX_QUERY_CACHE_SIZE
                            ):
                                _LATEST_RATIO_PERIOD_CACHE.popitem(last=False)

            stress_cache_key = (
                (*snap_key, statement_scope, period_type, resolved_date)
                if snap_key is not None
                else None
            )
            if stress_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_inputs = _STRESS_INPUTS_CACHE.get(stress_cache_key)
                    if cached_inputs is not None:
                        _STRESS_INPUTS_CACHE.move_to_end(stress_cache_key)
                        return _filter_tickers(cached_inputs.clone())

            stress_ratio_cols = list(
                dict.fromkeys(("issuer_id", "ticker", *REQUIRED_STRESS_COLUMNS))
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=stress_ratio_cols,
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if ratios.is_empty():
                return _filter_tickers(
                    ratios.with_columns(
                        pl.lit(None, dtype=pl.String).alias("company_name_vn"),
                        pl.lit(None, dtype=pl.String).alias("icb_code_level2"),
                        pl.lit(None, dtype=pl.String).alias("icb_name_level2"),
                    )
                )

            issuers = pinned.read_table(
                "issuers.parquet",
                columns=[
                    "issuer_id",
                    "company_name_vn",
                    "icb_code_level2",
                    "icb_name_level2",
                ],
                filters={"issuer_id": ratios["issuer_id"].unique().to_list()},
                connection=connection,
            )
            result = ratios.join(issuers, on="issuer_id", how="left").sort("ticker")
            if stress_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _STRESS_INPUTS_CACHE[stress_cache_key] = result
                    while len(_STRESS_INPUTS_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _STRESS_INPUTS_CACHE.popitem(last=False)
            return _filter_tickers(result.clone())

    def get_meta_periods_and_sectors(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> tuple[str | None, dict[str, dict[str, list[date]]], list[SectorOptionDict]]:
        """Return available reporting dates per scope/type and ICB Level 2 sector counts."""
        available_periods: dict[str, dict[str, list[date]]] = {
            scope: {ptype: [] for ptype in PERIOD_TYPES} for scope in STATEMENT_SCOPES
        }
        with self.repository.snapshot_session(connection=connection) as pinned:
            meta = pinned.get_snapshot_metadata(connection=connection)
            raw_pub = meta.get("published_at")
            published_at = str(raw_pub) if isinstance(raw_pub, str) else None

            avail_ratio = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if avail_ratio and {
                "statement_scope",
                "period_type",
                "period_end_date",
            }.issubset(avail_ratio):
                periods_df = (
                    pinned.read_table(
                        RATIOS_TABLE,
                        columns=["statement_scope", "period_type", "period_end_date"],
                        connection=connection,
                    )
                    .drop_nulls(["statement_scope", "period_type", "period_end_date"])
                    .filter(
                        pl.col("statement_scope").is_in(STATEMENT_SCOPES)
                        & pl.col("period_type").is_in(PERIOD_TYPES)
                    )
                    .unique()
                    .sort("period_end_date", descending=True)
                )
                if not periods_df.is_empty():
                    for key_tuple, sub in periods_df.partition_by(
                        ["statement_scope", "period_type"],
                        as_dict=True,
                        maintain_order=True,
                    ).items():
                        scope = str(key_tuple[0])
                        ptype = str(key_tuple[1])
                        if (
                            scope in available_periods
                            and ptype in available_periods[scope]
                        ):
                            available_periods[scope][ptype] = [
                                pdate
                                for pdate in sub.get_column("period_end_date").to_list()
                                if isinstance(pdate, date)
                            ]

            avail_iss = _available_table_columns(
                pinned, "issuers.parquet", connection=connection
            )
            sectors: list[SectorOptionDict] = []
            if avail_iss and {"icb_code_level2", "icb_name_level2"}.issubset(avail_iss):
                iss_df = pinned.read_table(
                    "issuers.parquet",
                    columns=["icb_code_level2", "icb_name_level2"],
                    connection=connection,
                ).filter(pl.col("icb_code_level2").is_not_null())
                if not iss_df.is_empty():
                    grouped = (
                        iss_df.group_by("icb_code_level2")
                        .agg(
                            pl.col("icb_name_level2")
                            .drop_nulls()
                            .first()
                            .alias("icb_name_level2"),
                            pl.len().cast(pl.Int64).alias("issuer_count"),
                        )
                        .with_columns(
                            pl.col("icb_code_level2").cast(pl.String),
                            pl.when(
                                pl.col("icb_name_level2").is_not_null()
                                & (pl.col("icb_name_level2").cast(pl.String) != "")
                            )
                            .then(pl.col("icb_name_level2").cast(pl.String))
                            .otherwise(pl.col("icb_code_level2").cast(pl.String))
                            .alias("icb_name_level2"),
                        )
                        .sort(["icb_name_level2", "icb_code_level2"])
                    )
                    sectors = [
                        {
                            "icb_code_level2": str(code),
                            "icb_name_level2": str(name),
                            "issuer_count": int(cnt),
                        }
                        for code, name, cnt in zip(
                            grouped.get_column("icb_code_level2").to_list(),
                            grouped.get_column("icb_name_level2").to_list(),
                            grouped.get_column("issuer_count").to_list(),
                            strict=True,
                        )
                    ]
        return published_at, available_periods, sectors

    @staticmethod
    def _build_port_candidate_roots(
        repo_source: Path,
        pinned_source: Path,
        *,
        port_data_root: Path | None = None,
    ) -> list[Path]:
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

    @staticmethod
    def _load_port_parquet_for_issuer(
        *,
        rel_parquet_path: Path,
        issuer_id: str,
        candidate_roots: Sequence[Path],
    ) -> pl.DataFrame | None:
        for root in candidate_roots:
            parquet_path = root / rel_parquet_path
            if parquet_path.is_file():
                return (
                    pl.scan_parquet(parquet_path)
                    .filter(pl.col("issuer_id") == issuer_id)
                    .collect()
                )
        return None

    @staticmethod
    def _build_overlay_financial_periods(
        pinned: ParquetRepository,
        *,
        issuer_id: str,
        ticker: str,
        accounting_regime: str,
        statement_scope: str,
        prices: pl.DataFrame,
        as_of: date | None,
        connection: duckdb.DuckDBPyConnection | None,
    ) -> pl.DataFrame | None:
        avail_ratio_cols = _available_table_columns(
            pinned, RATIOS_TABLE, connection=connection
        )
        ratios = pl.DataFrame()
        if avail_ratio_cols:
            ratio_cols = _select_existing_columns(
                (
                    "issuer_id",
                    "statement_scope",
                    "period_type",
                    "period_end_date",
                    "fiscal_year",
                    "fiscal_quarter",
                    "filing_id",
                    "parent_equity",
                    "estimated_shares_outstanding",
                    "shares_source",
                ),
                avail_ratio_cols,
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": "QUARTER",
                },
                connection=connection,
            )
            if not ratios.is_empty():
                sort_ratio = [
                    col
                    for col in ("period_end_date", "filing_id")
                    if col in ratios.columns
                ]
                ratios = ratios.sort(sort_ratio).unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )

        facts_table = "latest_facts.parquet"
        avail_fact_cols = _available_table_columns(
            pinned, facts_table, connection=connection
        )
        if not avail_fact_cols:
            facts_table = "financial_facts.parquet"
            avail_fact_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )

        facts = pl.DataFrame()
        if avail_fact_cols:
            fact_desired = (
                "issuer_id statement_scope period_type fiscal_year_start_date "
                "period_start_date period_end_date fiscal_year fiscal_quarter metric_id "
                "taxonomy_version revision_no revision_reason filing_id published_at "
                "published_at_basis effective_available_at derivation_method audit_status "
                "audit_opinion value ttm_value comparable_across_taxonomy is_filed_late "
                "source_provider source_filing_ids"
            ).split()
            fact_cols = _select_existing_columns(fact_desired, avail_fact_cols)
            facts = pinned.read_table(
                facts_table,
                columns=fact_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": "QUARTER",
                },
                connection=connection,
            )

        ni_ttm_df = pl.DataFrame()
        bs_debt_df = pl.DataFrame()
        period_dates_df = pl.DataFrame()
        pp_df: pl.DataFrame | None = None

        if not facts.is_empty() and "revision_reason" in facts.columns:
            facts = facts.filter(pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")

        if not facts.is_empty():
            f_cols_set = set(facts.columns)
            d_cands = [
                pl.col(c).cast(pl.Date)
                for c in ("published_at", "effective_available_at")
                if c in f_cols_set
            ]
            pit_avail = (
                pl.when(pl.col("published_at_basis") == "EXACT_FILING")
                .then(pl.col("published_at").cast(pl.Date))
                .otherwise(pl.col("effective_available_at").cast(pl.Date))
                if {"published_at_basis", "published_at", "effective_available_at"}
                <= f_cols_set
                else (pl.coalesce(*d_cands) if d_cands else pl.lit(None, dtype=pl.Date))
            )
            prov_rk = (
                pl.col("source_provider").replace_strict(
                    {"fhsc": 5, "issuer": 4, "vietcap_iq": 3, "cafef_pdf": 2}, default=1
                )
                if "source_provider" in f_cols_set
                else pl.lit(1)
            )
            val_ok = (
                pl.col("value").is_not_null()
                & pl.col("value").cast(pl.Float64).is_finite()
                if "value" in f_cols_set
                else pl.lit(True)
            )
            facts = facts.with_columns(
                pit_avail.alias("_pit_avail"),
                val_ok.alias("_val_ok"),
                prov_rk.alias("_prov_rk"),
            )
            sort_fact = [
                c
                for c in (
                    "period_end_date",
                    "_pit_avail",
                    "_val_ok",
                    "_prov_rk",
                    "revision_no",
                    "filing_id",
                )
                if c in facts.columns
            ]
            sorted_facts = facts.sort(sort_fact) if sort_fact else facts
            pit_facts = (
                sorted_facts.filter(
                    pl.col("_pit_avail").is_not_null() & (pl.col("_pit_avail") <= as_of)
                )
                if as_of is not None and d_cands
                else sorted_facts
            )
            bs_debt_metrics = (
                "BS_TOTAL_ASSETS BS_TOTAL_LIABILITIES BS_SHORT_TERM_BORROWINGS "
                "BS_LONG_TERM_BORROWINGS BS_CONVERTIBLE_BONDS"
            ).split()
            bs_sub = pit_facts.filter(pl.col("metric_id").is_in(bs_debt_metrics))
            if not bs_sub.is_empty() and "value" in bs_sub.columns:
                bs_dedup = bs_sub.unique(
                    subset=["issuer_id", "period_end_date", "metric_id"], keep="last"
                )
                bs_debt_df = bs_dedup.group_by(["issuer_id", "period_end_date"]).agg(
                    *(
                        pl.col("value")
                        .cast(pl.Float64)
                        .filter(pl.col("metric_id") == m)
                        .last()
                        .alias(m)
                        for m in bs_debt_metrics
                    )
                )

            date_cols = [
                col
                for col in (
                    "issuer_id",
                    "period_end_date",
                    "published_at",
                    "effective_available_at",
                )
                if col in sorted_facts.columns
            ]
            if (
                "published_at" in date_cols or "effective_available_at" in date_cols
            ) and "period_end_date" in date_cols:
                period_dates_df = sorted_facts.select(date_cols).unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )

            ni_rows = pit_facts.filter(pl.col("metric_id") == "IS_NET_INCOME_PARENT")
            if not ni_rows.is_empty():
                ni_dedup = ni_rows.unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )
                has_direct_ttm = (
                    "ttm_value" in ni_dedup.columns
                    and ni_dedup["ttm_value"].drop_nulls().len() > 0
                )
                if has_direct_ttm:
                    ni_select = [
                        pl.col("issuer_id").cast(pl.String),
                        pl.col("period_end_date").cast(pl.Date),
                        pl.col("ttm_value")
                        .cast(pl.Float64)
                        .alias("IS_NET_INCOME_PARENT_TTM"),
                    ]
                    if "published_at" in ni_dedup.columns:
                        ni_select.append(
                            pl.col("published_at")
                            .cast(pl.Date)
                            .alias("_ni_published_at")
                        )
                    if "effective_available_at" in ni_dedup.columns:
                        ni_select.append(
                            pl.col("effective_available_at")
                            .cast(pl.Date)
                            .alias("_ni_effective_available_at")
                        )
                    ni_ttm_df = ni_dedup.select(ni_select)

            if ni_ttm_df.is_empty():
                required_pp_cols = {
                    "issuer_id",
                    "statement_scope",
                    "period_type",
                    "fiscal_year_start_date",
                    "period_start_date",
                    "period_end_date",
                    "fiscal_year",
                    "fiscal_quarter",
                    "metric_id",
                    "taxonomy_version",
                    "revision_no",
                    "revision_reason",
                    "filing_id",
                    "published_at",
                    "published_at_basis",
                    "effective_available_at",
                    "derivation_method",
                    "audit_status",
                    "audit_opinion",
                    "value",
                }
                if required_pp_cols <= set(facts.columns):
                    issuer_frame = pl.DataFrame(
                        {
                            "issuer_id": [issuer_id],
                            "ticker": [ticker],
                            "accounting_regime": [accounting_regime],
                        }
                    )
                    pp_raw = prepare_periods(
                        facts,
                        issuer_frame,
                        as_of=as_of or date(2099, 12, 31),
                        market_prices=prices,
                    ).filter(
                        (pl.col("statement_scope") == statement_scope)
                        & (pl.col("period_type") == "QUARTER")
                    )
                    if not pp_raw.is_empty():
                        pp_select = [
                            pl.col("issuer_id").cast(pl.String),
                            pl.col("period_end_date").cast(pl.Date),
                        ]
                        if "IS_NET_INCOME_PARENT" in pp_raw.columns:
                            pp_select.append(
                                pl.col("IS_NET_INCOME_PARENT")
                                .cast(pl.Float64)
                                .alias("IS_NET_INCOME_PARENT_TTM")
                            )
                        if "parent_equity" in pp_raw.columns:
                            pp_select.append(
                                pl.col("parent_equity")
                                .cast(pl.Float64)
                                .alias("_pp_parent_equity")
                            )
                        if "estimated_shares_outstanding" in pp_raw.columns:
                            pp_select.append(
                                pl.col("estimated_shares_outstanding")
                                .cast(pl.Float64)
                                .alias("_pp_estimated_shares")
                            )
                        if "shares_source" in pp_raw.columns:
                            pp_select.append(
                                pl.col("shares_source")
                                .cast(pl.String)
                                .alias("_pp_shares_source")
                            )
                        if "_period_available_at" in pp_raw.columns:
                            pp_select.append(
                                pl.col("_period_available_at").cast(pl.Date)
                            )
                        pp_df = pp_raw.select(pp_select).unique(
                            subset=["issuer_id", "period_end_date"], keep="last"
                        )

        if not ratios.is_empty():
            combined = ratios
        elif pp_df is not None and not pp_df.is_empty():
            combined = pp_df.with_columns(
                pl.lit(statement_scope).alias("statement_scope"),
                pl.lit("QUARTER").alias("period_type"),
            )
            if "_pp_parent_equity" in combined.columns:
                combined = combined.rename({"_pp_parent_equity": "parent_equity"})
            if "_pp_estimated_shares" in combined.columns:
                combined = combined.rename(
                    {"_pp_estimated_shares": "estimated_shares_outstanding"}
                )
            if "_pp_shares_source" in combined.columns:
                combined = combined.rename({"_pp_shares_source": "shares_source"})
        elif not period_dates_df.is_empty():
            combined = period_dates_df.with_columns(
                pl.lit(statement_scope).alias("statement_scope"),
                pl.lit("QUARTER").alias("period_type"),
            )
        else:
            return None

        if not period_dates_df.is_empty() and "published_at" not in combined.columns:
            combined = combined.join(
                period_dates_df, on=["issuer_id", "period_end_date"], how="left"
            )

        if not ni_ttm_df.is_empty():
            combined = combined.join(
                ni_ttm_df, on=["issuer_id", "period_end_date"], how="left"
            )
            if "_ni_published_at" in combined.columns:
                pub_expr = (
                    pl.coalesce("_ni_published_at", "published_at")
                    if "published_at" in combined.columns
                    else pl.col("_ni_published_at")
                )
                combined = combined.with_columns(pub_expr.alias("published_at")).drop(
                    "_ni_published_at"
                )
            if "_ni_effective_available_at" in combined.columns:
                eff_expr = (
                    pl.coalesce("_ni_effective_available_at", "effective_available_at")
                    if "effective_available_at" in combined.columns
                    else pl.col("_ni_effective_available_at")
                )
                combined = combined.with_columns(
                    eff_expr.alias("effective_available_at")
                ).drop("_ni_effective_available_at")
        elif pp_df is not None and not pp_df.is_empty() and not ratios.is_empty():
            combined = combined.join(
                pp_df, on=["issuer_id", "period_end_date"], how="left"
            )
            if "_pp_parent_equity" in combined.columns:
                eq_expr = (
                    pl.coalesce("parent_equity", "_pp_parent_equity")
                    if "parent_equity" in combined.columns
                    else pl.col("_pp_parent_equity")
                )
                combined = combined.with_columns(eq_expr.alias("parent_equity")).drop(
                    "_pp_parent_equity"
                )
            if "_pp_estimated_shares" in combined.columns:
                sh_expr = (
                    pl.coalesce("_pp_estimated_shares", "estimated_shares_outstanding")
                    if "estimated_shares_outstanding" in combined.columns
                    else pl.col("_pp_estimated_shares")
                )
                combined = combined.with_columns(
                    sh_expr.alias("estimated_shares_outstanding")
                ).drop("_pp_estimated_shares")
            if "_pp_shares_source" in combined.columns:
                ss_expr = (
                    pl.coalesce("shares_source", "_pp_shares_source")
                    if "shares_source" in combined.columns
                    else pl.col("_pp_shares_source")
                )
                combined = combined.with_columns(ss_expr.alias("shares_source")).drop(
                    "_pp_shares_source"
                )

        if (
            "published_at" not in combined.columns
            and "effective_available_at" not in combined.columns
            and "filing_id" in combined.columns
        ):
            avail_manifest_cols = _available_table_columns(
                pinned, FILINGS_MANIFEST_TABLE, connection=connection
            )
            if avail_manifest_cols:
                m_cols = _select_existing_columns(
                    ("filing_id", "published_at", "effective_available_at"),
                    avail_manifest_cols,
                )
                if "filing_id" in m_cols and (
                    "published_at" in m_cols or "effective_available_at" in m_cols
                ):
                    manifest_df = pinned.read_table(
                        FILINGS_MANIFEST_TABLE,
                        columns=m_cols,
                        filters={"issuer_id": issuer_id},
                        connection=connection,
                    ).unique(subset=["filing_id"], keep="last")
                    if not manifest_df.is_empty():
                        combined = combined.join(
                            manifest_df, on="filing_id", how="left"
                        )

        date_casts = [
            pl.col(col).cast(pl.Date).alias(col)
            for col in (
                "published_at",
                "effective_available_at",
                "_period_available_at",
                "period_end_date",
            )
            if col in combined.columns
        ]
        if date_casts:
            combined = combined.with_columns(date_casts)

        if not bs_debt_df.is_empty():
            missing_bs = [
                col for col in bs_debt_df.columns if col not in combined.columns
            ]
            if missing_bs:
                combined = combined.join(
                    bs_debt_df.select("issuer_id", "period_end_date", *missing_bs),
                    on=["issuer_id", "period_end_date"],
                    how="left",
                )

        has_any_disclosure_col = any(
            col in combined.columns
            for col in (
                "available_at",
                "published_at",
                "effective_available_at",
                "_period_available_at",
                "_amounts_available_at",
            )
        )
        return combined if has_any_disclosure_col else None

    def get_company_market_overlay(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        as_of: date | None = None,
        port_data_root: Path | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> dict[str, Any]:
        """Return PIT valuation series and Price & Risk Event Overlay for one company (UI-09b)."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            raise ValueError("Ticker must not be empty.")

        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker", "accounting_regime"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])
            resolved_ticker = str(issuer_row["ticker"])
            accounting_regime = str(issuer_row["accounting_regime"])

            candidate_roots = self._build_port_candidate_roots(
                self.repository._source,
                pinned._source,
                port_data_root=port_data_root,
            )

            prices = self._load_port_parquet_for_issuer(
                rel_parquet_path=Path("market_prices") / "market_prices.parquet",
                issuer_id=issuer_id,
                candidate_roots=candidate_roots,
            )
            if prices is None:
                prices = pl.DataFrame()
            if not prices.is_empty() and as_of is not None:
                prices = prices.filter(pl.col("trade_date") <= as_of)

            if prices.is_empty():
                return {
                    "issuer_id": issuer_id,
                    "ticker": resolved_ticker,
                    "as_of": as_of,
                    "latest_valuation": None,
                    "price_series": [],
                    "model_markers": [],
                    "regulatory_markers": [],
                    "earliest_warning_summary": None,
                }

            reg_events = self._load_port_parquet_for_issuer(
                rel_parquet_path=Path("regulatory_ledger")
                / "regulatory_events.parquet",
                issuer_id=issuer_id,
                candidate_roots=candidate_roots,
            )
            if reg_events is not None and reg_events.is_empty():
                reg_events = None

            ml_scores: pl.DataFrame | None = None
            avail_ml_cols = _available_table_columns(
                pinned, ML_SCORES_TABLE, connection=connection
            )
            if avail_ml_cols:
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
                if {"issuer_id", "period_end_date", "risk_tier"} <= set(ml_cols):
                    ml_raw = pinned.read_table(
                        ML_SCORES_TABLE,
                        columns=ml_cols,
                        filters={
                            "issuer_id": issuer_id,
                            "statement_scope": statement_scope,
                        },
                        connection=connection,
                    )
                    if not ml_raw.is_empty():
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
                        ml_scores = ml_raw.sort(sort_ml).unique(
                            subset=dedup_ml, keep="last"
                        )

            dtd_facts: pl.DataFrame | None = None
            dtd_want = (
                "issuer_id statement_scope period_type period_end_date metric_id value "
                "published_at published_at_basis effective_available_at revision_no "
                "revision_reason filing_id source_provider"
            ).split()
            for ft_name in ("financial_facts.parquet", "latest_facts.parquet"):
                ft_avail = _available_table_columns(
                    pinned, ft_name, connection=connection
                )
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
                    if not cand_ft.filter(
                        pl.col("metric_id") == "BS_TOTAL_ASSETS"
                    ).is_empty():
                        dtd_facts = cand_ft
                        break

            financial_periods = self._build_overlay_financial_periods(
                pinned,
                issuer_id=issuer_id,
                ticker=resolved_ticker,
                accounting_regime=accounting_regime,
                statement_scope=statement_scope,
                prices=prices,
                as_of=as_of,
                connection=connection,
            )

            valuation_series = (
                compute_pit_valuation_series(
                    prices,
                    financial_periods,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                )
                if financial_periods is not None and not financial_periods.is_empty()
                else None
            )

            payload = build_price_risk_overlay(
                prices,
                issuer_id=issuer_id,
                ml_scores=ml_scores,
                regulatory_events=reg_events,
                financial_periods=financial_periods,
                dtd_financial_data=dtd_facts,
                valuation_series=valuation_series,
                statement_scope=statement_scope,
                as_of=as_of,
            )
            price_series = payload["price_series"]
            latest_valuation = price_series[-1] if price_series else None
            return {
                **payload,
                "latest_valuation": latest_valuation,
            }
