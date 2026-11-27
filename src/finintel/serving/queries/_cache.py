"""Headless read-only DuckDB query submodule (L1B snapshot-aware caches, types, schemas, and DuckDB pushdown helpers) for Layer 5 serving.

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
import threading
import weakref
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.serving.observability import SPAN_DUCKDB_QUERY, trace_span
from finintel.serving.queries._screener_filters import (
    SCREENER_SCHEMA as SCREENER_SCHEMA,
)
from finintel.serving.queries._screener_filters import (
    SCREENER_SORT_FIELDS as SCREENER_SORT_FIELDS,
)
from finintel.storage.ducklake_store import ensure_ducklake_attached
from finintel.storage.repository import ParquetRepository, _normalize_table_name
from finintel.storage.schemas import ML_SCORES_SCHEMA, QUALITY_SUMMARY_TABLE

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[4] / "data"
DenominatorMode = Literal[
    "AVERAGE_STOCK", "POINT_IN_TIME_STOCK", "FLOW", "PRIOR_YEAR_FLOW", "COMPOSITE"
]
PeriodView = Literal["TTM", "Quarterly", "Annual"]
StatementScope = Literal["CONSOLIDATED", "SEPARATE"]
PeriodType = Literal["QUARTER", "ANNUAL"]
Exchange = Literal["HOSE", "HNX", "UPCOM"]
AltmanZone = Literal["SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE"]
RiskTier = Literal["R1", "R2", "R3", "R4", "R5"]
BeneishFlag = Literal[
    "VALID",
    "HIGH_MANIPULATION_RISK_SCREEN",
    "MIXED_TAXONOMY",
    "MISSING_PRIOR_YEAR_BASE",
    "MISSING_DEPRECIATION_DIRECT_CF",
    "MISSING_OR_INVALID_INPUT",
]
PeerGroupLevel = Literal["ICB_LEVEL_3", "ICB_LEVEL_2", "REGIME_FALLBACK"]

STATEMENT_SCOPES: tuple[str, ...] = ("CONSOLIDATED", "SEPARATE")
PERIOD_TYPES: tuple[str, ...] = ("QUARTER", "ANNUAL")
EXCHANGES: tuple[str, ...] = ("HOSE", "HNX", "UPCOM")
PERIOD_VIEWS: tuple[str, ...] = ("TTM", "Quarterly", "Annual")
ALTMAN_ZONES: tuple[str, ...] = ("SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE")
RISK_TIERS_ORDERED: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5")
BENEISH_FLAGS: tuple[str, ...] = (
    "VALID",
    "HIGH_MANIPULATION_RISK_SCREEN",
    "MIXED_TAXONOMY",
    "MISSING_PRIOR_YEAR_BASE",
    "MISSING_DEPRECIATION_DIRECT_CF",
    "MISSING_OR_INVALID_INPUT",
)
PEER_GROUP_LEVELS: tuple[str, ...] = (
    "ICB_LEVEL_3",
    "ICB_LEVEL_2",
    "REGIME_FALLBACK",
)
STATEMENT_TYPES: tuple[str, ...] = (
    "BALANCE_SHEET",
    "INCOME_STATEMENT",
    "CASH_FLOW",
)


class SectorOptionDict(TypedDict):
    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int


class SectorRiskDict(TypedDict):
    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    median_distress_prob: float | None
    avg_red_flags: float
    total_debt_sum: float


class SectorRiskSummaryDict(TypedDict):
    period_end_date: date | None
    total_issuers: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    sectors: list[SectorRiskDict]
    top_deteriorating: list[dict[str, object]]


_ALTMAN_SEVERITY_RANK: dict[str, int] = {
    "SAFE_ZONE": 0,
    "GREY_ZONE": 1,
    "DISTRESS_ZONE": 2,
}
_RISK_TIER_SEVERITY_RANK: dict[str, int] = {
    "R1": 0,
    "R2": 1,
    "R3": 2,
    "R4": 3,
    "R5": 4,
}

PEER_BAND_HISTORY_SCHEMA = pl.Schema(
    {
        "period_end_date": pl.Date,
        "value": pl.Float64,
        "peer_p25": pl.Float64,
        "peer_median": pl.Float64,
        "peer_p75": pl.Float64,
        "percentile_rank": pl.Float64,
        "peer_group_level": pl.String,
        "icb_code_used": pl.String,
        "peer_count": pl.UInt32,
    }
)

_EMPTY_RED_FLAGS_SCHEMA = pl.Schema(
    {
        "period_end_date": pl.Date,
        "flag_code": pl.String,
        "severity": pl.String,
        "flag_title_vn": pl.String,
        "explanation_text_vn": pl.String,
        "evidence_json": pl.String,
        "filing_id": pl.String,
    }
)

_EMPTY_RATIO_PERIODS_SCHEMA = pl.Schema(
    {
        "statement_scope": pl.String,
        "period_type": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int32,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "filing_id": pl.String,
    }
)

_V2_PROFILE_OPTIONAL_RATIO_COLS: dict[str, pl.DataType] = {
    "core_operating_profit_ttm": pl.Float64(),
    "core_operating_profit_flag": pl.String(),
    "core_operating_margin": pl.Float64(),
    "core_operating_margin_flag": pl.String(),
    "non_core_distortion_to_assets": pl.Float64(),
    "non_core_distortion_to_assets_flag": pl.String(),
    "core_to_ebt_ratio": pl.Float64(),
    "core_to_ebt_ratio_flag": pl.String(),
}


@dataclass(frozen=True)
class RatioLineageSpec:
    """Declarative mathematical specification and metric lineage for a financial ratio."""

    ratio_name: str
    display_name_vn: str
    formula: str
    numerator_metrics: tuple[str, ...]
    denominator_metrics: tuple[str, ...]
    denominator_mode: DenominatorMode


@dataclass(frozen=True)
class SnapshotOverview:
    """High-level summary counts and provenance metadata for the active snapshot."""

    snapshot_id: str | None
    published_at: str | None
    total_issuers: int
    total_ratio_periods: int
    pass_periods: int
    warning_periods: int
    quarantined_rows: int
    storage_backend: str = "parquet"


@dataclass(frozen=True)
class QualityTablesPage:
    """Paginated Layer 6 quality summary and quarantine slices with exact totals."""

    quality_summary: pl.DataFrame
    audit_quarantine: pl.DataFrame
    total_summary: int
    total_quarantine: int


@dataclass(frozen=True)
class CompanyProfileData:
    """Pre-computed financial health profile, DuPont decomposition, red flags, and ML scores."""

    ticker: str
    issuer_id: str
    company_name_vn: str | None
    accounting_regime: str
    statement_scope: str
    period_view: PeriodView
    latest_taxonomy_version: str | None
    latest_quality_status: str | None
    latest_warning_codes: tuple[str, ...]
    growth_series: pl.DataFrame
    capital_structure_series: pl.DataFrame
    dupont_series: pl.DataFrame
    red_flags: pl.DataFrame
    ratios_table: pl.DataFrame
    ml_scores: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=ML_SCORES_SCHEMA)
    )


@dataclass(frozen=True)
class IndustryComparisonData:
    """Single-period cross-sectional ICB peer benchmark and peer cohort ranking."""

    ticker: str
    issuer_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    metric_name: str
    peer_group_level: str | None
    icb_code_used: str | None
    peer_count: int
    company_value: float | None
    peer_median: float | None
    peer_p25: float | None
    peer_p75: float | None
    peer_iqr: float | None
    percentile_rank: float | None
    robust_z_score: float | None
    industry_snapshot_date: date | None
    peer_ranking: pl.DataFrame


@dataclass(frozen=True)
class LineageComponent:
    """Provenance record for a single numerator or denominator input filing fact."""

    role: Literal["NUMERATOR", "DENOMINATOR"]
    metric_id: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end_date: date
    value: float | None
    filing_id: str
    taxonomy_version: str
    derivation_method: str
    source_provider: str | None
    connector_name: str | None
    published_at: date | None
    published_at_basis: str | None
    effective_available_at: date | None
    canonical_content_sha256: str | None


@dataclass(frozen=True)
class RatioLineageCard:
    """Full audit lineage tracing one published ratio to its underlying source filings."""

    ticker: str
    issuer_id: str
    statement_scope: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end_date: date
    ratio_name: str
    display_name_vn: str
    formula: str
    ratio_value: float | None
    status_flag: str | None
    taxonomy_version: str
    denominator_mode: DenominatorMode
    anchor_filing_id: str
    source_filing_ids: tuple[str, ...]
    numerator_components: tuple[LineageComponent, ...]
    denominator_components: tuple[LineageComponent, ...]
    orphan_filing_ids: tuple[str, ...]


def resolve_data_root(explicit: Path | None = None) -> Path:
    """Resolve the active data root from an explicit path, env var, or default."""
    if explicit is not None:
        return explicit.resolve()
    env_root = os.getenv("FININTEL_DATA_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return DEFAULT_DATA_ROOT


def percent_to_decimal(pct: float) -> float:
    """Convert a UI percentage / percentage-point control value to a decimal ratio."""
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise ValueError("pct must be a finite numeric value.")
    value = float(pct)
    if not math.isfinite(value):
        raise ValueError("pct must be finite.")
    return value / 100.0


def pagination_offset(page_num: int, page_size: int) -> int:
    """Compute a 0-based offset from a 1-based page number and page size."""
    if isinstance(page_num, bool) or not isinstance(page_num, int) or page_num < 1:
        raise ValueError("page_num must be an integer >= 1.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be an integer >= 1.")
    return (page_num - 1) * page_size


def pagination_total_pages(total: int, page_size: int) -> int:
    """Return total number of 1-based pages (at least 1) for a paginated table."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be an integer >= 1.")
    return max(1, (total + page_size - 1) // page_size)


def pagination_window(offset: int, limit: int, total: int) -> tuple[int, int, int]:
    """Return ``(current_page, start_row, end_row)`` for 1-based pagination display."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be an integer >= 1.")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer.")
    current_page = (offset // limit) + 1
    if total == 0 or offset >= total:
        return current_page, 0, 0
    return current_page, offset + 1, min(offset + limit, total)


_QUERY_CACHE_LOCK = threading.Lock()
_MAX_QUERY_CACHE_SIZE = 256
_DEFAULT_READ_TABLE = ParquetRepository.read_table
_SNAPSHOT_OVERVIEW_CACHE: OrderedDict[tuple[object, ...], SnapshotOverview] = (
    OrderedDict()
)
_LATEST_RATIO_PERIOD_CACHE: OrderedDict[tuple[object, ...], date] = OrderedDict()
_STRESS_INPUTS_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_COMPANY_PROFILE_CACHE: OrderedDict[tuple[object, ...], CompanyProfileData] = (
    OrderedDict()
)
_INDUSTRY_COMPARISON_CACHE: OrderedDict[tuple[object, ...], IndustryComparisonData] = (
    OrderedDict()
)
_PEER_BAND_HISTORY_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_RATIO_PERIODS_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_RATIO_LINEAGE_CACHE: OrderedDict[tuple[object, ...], RatioLineageCard] = OrderedDict()
_SCREENER_CACHE: OrderedDict[tuple[object, ...], tuple[pl.DataFrame, int]] = (
    OrderedDict()
)
_COHORT_PEER_RANKING_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = (
    OrderedDict()
)
_ALL_L1B_CACHES: tuple[OrderedDict[tuple[object, ...], Any], ...] = (
    _SNAPSHOT_OVERVIEW_CACHE,
    _LATEST_RATIO_PERIOD_CACHE,
    _STRESS_INPUTS_CACHE,
    _COMPANY_PROFILE_CACHE,
    _INDUSTRY_COMPARISON_CACHE,
    _PEER_BAND_HISTORY_CACHE,
    _RATIO_PERIODS_CACHE,
    _RATIO_LINEAGE_CACHE,
    _SCREENER_CACHE,
    _COHORT_PEER_RANKING_CACHE,
)
_ACTIVE_L1B_SNAPSHOT_BY_ROOT: dict[tuple[object, ...], object] = {}


def _l1b_root_and_snapshot(
    cache_key: tuple[object, ...],
) -> tuple[tuple[object, ...], object] | None:
    """Extract ``(root_identity, snapshot_token)`` from an L1B cache key."""
    if not cache_key:
        return None
    kind = cache_key[0]
    if kind == "ducklake" and len(cache_key) >= 9:
        # ("ducklake", cat_path, cat_id, lake_data, alias, driver, dsn_env, snap_id, ver, ...)
        root = (
            "ducklake",
            cache_key[1],
            cache_key[3],
            cache_key[4],
            cache_key[5],
            cache_key[6],
        )
        return (root, (cache_key[2], cache_key[7], cache_key[8]))
    if kind == "parquet_dir" and len(cache_key) >= 3 and isinstance(cache_key[1], str):
        return (("parquet_dir", str(Path(cache_key[1]).parent)), cache_key[2])
    if kind == "parquet_snap" and len(cache_key) >= 3:
        return (("parquet_snap", cache_key[1]), cache_key[2])
    return None


def _evict_stale_l1b_caches_locked(snap_key: tuple[object, ...] | None) -> None:
    """Evict prior-snapshot L1B entries for the same data root (caller holds ``_QUERY_CACHE_LOCK``)."""
    if snap_key is None:
        return
    parsed = _l1b_root_and_snapshot(snap_key)
    if parsed is None:
        return
    target_root, target_snap = parsed
    if _ACTIVE_L1B_SNAPSHOT_BY_ROOT.get(target_root) == target_snap:
        return
    _ACTIVE_L1B_SNAPSHOT_BY_ROOT[target_root] = target_snap
    for cache in _ALL_L1B_CACHES:
        stale_keys = [
            existing
            for existing in cache
            if (ep := _l1b_root_and_snapshot(existing)) is not None
            and ep[0] == target_root
            and ep[1] != target_snap
        ]
        for stale_key in stale_keys:
            cache.pop(stale_key, None)


def clear_query_caches() -> None:
    """Clear all L1B query result caches and tracked active snapshot tokens."""
    with _QUERY_CACHE_LOCK:
        for cache in _ALL_L1B_CACHES:
            cache.clear()
        _ACTIVE_L1B_SNAPSHOT_BY_ROOT.clear()


def _can_use_query_result_cache(repo: ParquetRepository) -> bool:
    return (
        type(repo).read_table is ParquetRepository.read_table
        and ParquetRepository.read_table is _DEFAULT_READ_TABLE
    )


@contextmanager
def _pinned_table_source(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> Iterator[tuple[duckdb.DuckDBPyConnection, str, list[object]] | None]:
    """Yield ``(conn, source_sql, source_params)`` for a pinned snapshot table or ``None`` if absent.

    Caller-provided and session-provided connections are strictly non-owning and are
    never closed here.
    """
    filename, catalog_table = _normalize_table_name(table_name)
    binding = pinned._pinned_binding
    active_conn = connection if connection is not None else pinned._session_connection
    owns_conn = active_conn is None

    if binding is not None:
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            try:
                resolved = pinned._resolve_ducklake_table(
                    con, binding, filename, catalog_table
                )
            except duckdb.CatalogException as exc:
                if "does not exist" in str(exc).lower():
                    yield None
                    return
                raise
            if not resolved.exists:
                yield None
                return
            if resolved.parquet_files:
                param: object = (
                    resolved.parquet_files[0]
                    if len(resolved.parquet_files) == 1
                    else list(resolved.parquet_files)
                )
                yield (con, "read_parquet(?)", [param])
                return
            ensure_ducklake_attached(
                con,
                catalog_path=binding.catalog_path,
                data_path=binding.data_path,
                catalog_alias=binding.catalog_alias,
                catalog_driver=pinned._catalog_driver,
                postgres_dsn_env=pinned._postgres_dsn_env,
                read_only=True,
            )
            source_sql = (
                f'"{binding.catalog_alias}"."{catalog_table}" '
                f"AT (VERSION => {int(binding.ducklake_version)})"
            )
            yield (con, source_sql, [])
        finally:
            if owns_conn:
                con.close()
        return

    with pinned._read_path(filename) as path:
        if not path.is_file():
            yield None
            return
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            yield (con, "read_parquet(?)", [str(path)])
        finally:
            if owns_conn:
                con.close()


def _available_table_columns(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> set[str] | None:
    """Inspect column names for ``table_name`` with zero row scans, or ``None`` if absent."""
    cols = pinned.get_table_columns(table_name, connection=connection)
    return set(cols) if cols is not None else None


def _sql_count_rows(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Count rows in a pinned snapshot table via DuckDB SQL ``COUNT(*)``."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "count_rows",
                "db.table": table_name,
            },
        ),
        _pinned_table_source(pinned, table_name, connection=connection) as source,
    ):
        if source is None:
            return 0
        con, source_sql, source_params = source
        try:
            row = con.execute(
                f"SELECT COUNT(*) FROM {source_sql}", source_params
            ).fetchone()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return 0
            raise ContractViolationError(
                f"Unable to count rows for table {table_name!r}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to count rows for table {table_name!r}."
            ) from exc
        return int(row[0]) if row is not None and row[0] is not None else 0


def _sql_count_quality_statuses(
    pinned: ParquetRepository,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> tuple[int, int]:
    """Count PASS and WARNING periods via DuckDB SQL ``COUNT(*) FILTER (...)``."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "count_quality_statuses",
                "db.table": QUALITY_SUMMARY_TABLE,
            },
        ),
        _pinned_table_source(
            pinned, QUALITY_SUMMARY_TABLE, connection=connection
        ) as source,
    ):
        if source is None:
            return (0, 0)
        con, source_sql, source_params = source
        try:
            row = con.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE "quality_status" = 'PASS'),
                    COUNT(*) FILTER (WHERE "quality_status" = 'WARNING')
                FROM {source_sql}
                """,
                source_params,
            ).fetchone()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return (0, 0)
            raise ContractViolationError(
                "Unable to aggregate quality status counts."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                "Unable to aggregate quality status counts."
            ) from exc
        if row is None:
            return (0, 0)
        return (int(row[0] or 0), int(row[1] or 0))


def _sql_read_page(
    pinned: ParquetRepository,
    table_name: str,
    *,
    schema: pl.Schema,
    order_by_sql: str,
    limit: int,
    offset: int,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> tuple[pl.DataFrame, int]:
    """Read a paginated ``LIMIT/OFFSET`` slice and exact total row count via DuckDB SQL."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "read_page",
                "db.table": table_name,
                "db.limit": limit,
                "db.offset": offset,
            },
        ),
        _pinned_table_source(pinned, table_name, connection=connection) as source,
    ):
        if source is None:
            return pl.DataFrame(schema=schema), 0
        con, source_sql, source_params = source
        try:
            count_row = con.execute(
                f"SELECT COUNT(*) FROM {source_sql}", source_params
            ).fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            if total == 0:
                return pl.DataFrame(schema=schema), 0
            arrow = con.execute(
                f"SELECT * FROM {source_sql} ORDER BY {order_by_sql} LIMIT ? OFFSET ?",
                [*source_params, limit, offset],
            ).fetch_arrow_table()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return pl.DataFrame(schema=schema), 0
            raise ContractViolationError(
                f"Unable to read paginated table {table_name!r}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to read paginated table {table_name!r}."
            ) from exc
        return cast(pl.DataFrame, pl.from_arrow(arrow)), total


def _validate_pagination(limit: int, offset: int, *, max_limit: int = 200) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= max_limit)
    ):
        raise ValueError(f"limit must be an integer between 1 and {max_limit}.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")


def _select_existing_columns(
    desired: Sequence[str], available: set[str] | None
) -> list[str]:
    if available is None:
        return []
    seen: set[str] = set()
    selected: list[str] = []
    for col in desired:
        if col in available and col not in seen:
            selected.append(col)
            seen.add(col)
    return selected


def _resolve_issuer_row(
    pinned: ParquetRepository,
    ticker: str,
    *,
    columns: Sequence[str],
    connection: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, object]:
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise ValueError("Ticker must not be empty.")
    issuer_rows = pinned.read_table(
        "issuers.parquet",
        columns=list(columns),
        filters={"ticker": clean_ticker},
        connection=connection,
    )
    if issuer_rows.is_empty():
        raise ValueError(f"Ticker not found in active snapshot: {clean_ticker}")
    return issuer_rows.row(0, named=True)


def _pad_schema_columns(frame: pl.DataFrame, schema: pl.Schema) -> pl.DataFrame:
    """Ensure every column in ``schema`` exists (typed NULL if absent) and return in schema order."""
    frame_schema = frame.schema
    if tuple(frame_schema.keys()) == tuple(schema.keys()) and all(
        frame_schema[name] == dtype for name, dtype in schema.items()
    ):
        return frame
    missing_exprs = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
        if name not in frame_schema
    ]
    if missing_exprs:
        frame = frame.with_columns(missing_exprs)
        frame_schema = frame.schema
    return frame.select(
        *(
            pl.col(name)
            if frame_schema.get(name) == dtype
            else pl.col(name).cast(dtype, strict=False).alias(name)
            for name, dtype in schema.items()
        )
    )


class _CachedDictsDataFrame(pl.DataFrame):
    """Polars DataFrame subclass that precomputes finite JSON-safe row dicts for zero-copy serving."""

    _cached_finite_dicts: list[dict[str, object]] | None = None
    _cached_schema_obj: pl.Schema | None = None
    _cached_col_list: list[str] | None = None
    _cached_row_count: int | None = None
    _cached_select_map: dict[tuple[str, ...], _CachedDictsDataFrame] | None = None

    @property
    def schema(self) -> pl.Schema:
        if self._cached_schema_obj is not None:
            return self._cached_schema_obj
        return super().schema

    def is_empty(self) -> bool:
        if self._cached_row_count is not None:
            return self._cached_row_count == 0
        return super().is_empty()

    def with_columns(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if (
            self._cached_finite_dicts is not None
            and not named_exprs
            and len(exprs) == 1
            and hasattr(exprs[0], "__iter__")
            and not isinstance(exprs[0], (list, tuple, dict, str, bytes))
        ):
            return self
        return super().with_columns(*exprs, **named_exprs)  # type: ignore[arg-type]

    def tail(self, n: int = 5) -> pl.DataFrame:
        if self._cached_row_count is not None and n >= self._cached_row_count:
            return self
        return _wrap_cached_dicts_df(super().tail(n))

    def head(self, n: int = 5) -> pl.DataFrame:
        if self._cached_row_count is not None and n >= self._cached_row_count:
            return self
        return _wrap_cached_dicts_df(super().head(n))

    def select(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if (
            self._cached_finite_dicts is not None
            and not named_exprs
            and exprs
            and all(isinstance(e, str) for e in exprs)
        ):
            col_key = tuple(str(e) for e in exprs)
            select_map = self._cached_select_map
            if select_map is not None:
                cached_sub = select_map.get(col_key)
                if cached_sub is not None:
                    return cached_sub
            sub = _wrap_cached_dicts_df(super().select(*col_key))
            with _QUERY_CACHE_LOCK:
                if self._cached_select_map is None:
                    self._cached_select_map = {}
                self._cached_select_map[col_key] = sub
            return sub
        return super().select(*exprs, **named_exprs)  # type: ignore[arg-type]

    def to_dicts(self) -> list[dict[str, object]]:
        if self._cached_finite_dicts is not None:
            return [dict(row) for row in self._cached_finite_dicts]
        return super().to_dicts()


def _wrap_cached_dicts_df(df: pl.DataFrame) -> _CachedDictsDataFrame:
    if isinstance(df, _CachedDictsDataFrame) and df._cached_finite_dicts is not None:
        return df
    df_schema = df.schema
    df_cols = list(df_schema.keys())
    df_height = df.height
    if df_height == 0:
        wrapped = _CachedDictsDataFrame(df)
        wrapped._cached_finite_dicts = []
        wrapped._cached_schema_obj = df_schema
        wrapped._cached_col_list = df_cols
        wrapped._cached_row_count = 0
        return wrapped
    rows: list[dict[str, object]] = df.to_dicts()
    float_cols = [name for name, dtype in df_schema.items() if dtype.is_float()]
    if float_cols:
        for row in rows:
            for col in float_cols:
                val = row[col]
                if isinstance(val, float) and not math.isfinite(val):
                    row[col] = None
    wrapped = _CachedDictsDataFrame(df)
    wrapped._cached_finite_dicts = rows
    wrapped._cached_schema_obj = df_schema
    wrapped._cached_col_list = df_cols
    wrapped._cached_row_count = df_height
    return wrapped


_PARQUET_CONN_INIT_LOCK = threading.Lock()


def _close_duckdb_conn(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.close()
    except duckdb.Error:
        pass


class _QueryServiceBase:
    """Shared typed repository holder inherited by all domain query mixins."""

    repository: ParquetRepository
    _parquet_conn: duckdb.DuckDBPyConnection | None = None
    _parquet_conn_cond: threading.Condition | None = None
    _parquet_conn_finalizer: Any = None
    _parquet_active_cursors: dict[int, int] | None = None

    def _get_parquet_conn_cond(self) -> tuple[threading.Condition, dict[int, int]]:
        cond, active = self._parquet_conn_cond, self._parquet_active_cursors
        if cond is None or active is None:
            with _PARQUET_CONN_INIT_LOCK:
                cond, active = self._parquet_conn_cond, self._parquet_active_cursors
                if cond is None or active is None:
                    active = {}
                    cond = threading.Condition()
                    self._parquet_active_cursors = active
                    self._parquet_conn_cond = cond
        return cond, active

    def close(self) -> None:
        """Close any lazily initialized Parquet-only ``:memory:`` DuckDB connection."""
        cond, active = self._parquet_conn_cond, self._parquet_active_cursors
        if cond is None or active is None:
            return
        with cond:
            conn, finalizer = self._parquet_conn, self._parquet_conn_finalizer
            self._parquet_conn = self._parquet_conn_finalizer = None
            if finalizer is not None:
                finalizer.detach()
            if conn is None:
                return
            while active.get(id(conn), 0) > 0:
                cond.wait()
        _close_duckdb_conn(conn)

    @contextmanager
    def _parquet_cursor(
        self,
        pinned: ParquetRepository,
        connection: duckdb.DuckDBPyConnection | None,
    ) -> Iterator[duckdb.DuckDBPyConnection | None]:
        """Yield a thread-safe ``.cursor()`` for pure Parquet mode when ``connection is None``."""
        if (
            connection is not None
            or pinned._session_connection is not None
            or pinned._pinned_binding is not None
        ):
            yield connection
            return
        cond, active = self._get_parquet_conn_cond()
        with cond:
            conn = self._parquet_conn
            if conn is None:
                conn = duckdb.connect(":memory:")
                self._parquet_conn = conn
                self._parquet_conn_finalizer = weakref.finalize(
                    self, _close_duckdb_conn, conn
                )
            cur = conn.cursor()
            conn_id = id(conn)
            active[conn_id] = active.get(conn_id, 0) + 1
        try:
            yield cur
        finally:
            try:
                cur.close()
            finally:
                with cond:
                    rem = active.get(conn_id, 0) - 1
                    if rem > 0:
                        active[conn_id] = rem
                    else:
                        active.pop(conn_id, None)
                        cond.notify_all()
