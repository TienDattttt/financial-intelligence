"""Stateless DuckDB repository for DuckLake Lakehouse and immutable Parquet snapshots.

Verified Sources:
- DuckLake Official Documentation (time travel ``AT (VERSION => ...)``, READ_ONLY attach):
  https://ducklake.select/docs/stable/
- DuckDB Python API & Concurrency Guide (``:memory:`` + ``ATTACH ... (READ_ONLY)``,
  thread-safe non-owning ``conn.cursor()``):
  https://duckdb.org/docs/stable/clients/python/overview
  https://duckdb.org/docs/stable/connect/concurrency
- filelock ReadWriteLock Documentation (shared concurrent readers):
  https://py-filelock.readthedocs.io/

Runtime API Verification (Python 3.11.9, duckdb==1.4.2, polars==1.35.2, filelock==4.2.10):
- ``SELECT ... FROM alias.tbl AT (VERSION => N) WHERE "col" = ?`` pushes both projection
  and filter predicates into ``DUCKLAKE_SCAN`` (verified via ``EXPLAIN``).
- Caller-provided ``connection`` (e.g. ``conn.cursor()``) is strictly non-owning and is
  never closed by ``snapshot_session()`` or ``read_table()``.
"""

from __future__ import annotations

import gc
import json
import math
import os
import sqlite3
import stat
import sys
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.storage.ducklake_store import (
    DEFAULT_CATALOG_ALIAS,
    DEFAULT_POSTGRES_DSN_ENV,
    SNAPSHOT_AUDIT_TABLE,
    CatalogDriver,
    ensure_ducklake_attached,
    validate_sql_identifier,
)
from finintel.storage.snapshot_publisher import (
    KNOWN_TABLES,
    snapshot_read_lock,
)

_SCALAR_FILTER_TYPES = (str, bytes, bool, int, float, date, datetime)
_COLLECTION_FILTER_TYPES = (list, tuple, set, frozenset)
_RANGE_BOUND_TYPES = (int, float, date, datetime, str)


@dataclass(frozen=True)
class FilterRange:
    """Typed range predicate for ``ParquetRepository.read_table(filters=...)``."""

    ge: object | None = None
    le: object | None = None
    gt: object | None = None
    lt: object | None = None

    def __post_init__(self) -> None:
        bounds = (self.ge, self.le, self.gt, self.lt)
        if all(bound is None for bound in bounds):
            raise ValueError(
                "FilterRange requires at least one bound (ge, le, gt, lt)."
            )
        for bound in bounds:
            if bound is None:
                continue
            if isinstance(bound, bool) or not isinstance(bound, _RANGE_BOUND_TYPES):
                raise ValueError(
                    f"Unsupported FilterRange bound type: {type(bound).__name__}."
                )
            if isinstance(bound, float) and not math.isfinite(bound):
                raise ValueError("FilterRange float bounds must be finite.")


@dataclass(frozen=True)
class _DuckLakeBinding:
    catalog_path: Path
    data_path: Path
    catalog_alias: str
    snapshot_id: str
    ducklake_version: int
    published_at: str | None
    catalog_identity: int = 0


@dataclass(frozen=True)
class _ResolvedDuckLakeTable:
    exists: bool
    columns: tuple[str, ...]
    parquet_files: tuple[str, ...]


@dataclass(frozen=True)
class _CachedTableData:
    columns: tuple[str, ...]
    allowed_columns: set[str]
    frame: pl.DataFrame
    empty_frame: pl.DataFrame
    by_issuer: Mapping[str, pl.DataFrame] | None = None
    by_issuer_scope_period: Mapping[tuple[str, str, str], pl.DataFrame] | None = None
    by_ticker: Mapping[str, pl.DataFrame] | None = None
    by_period: Mapping[object, pl.DataFrame] | None = None
    by_period_metric: Mapping[tuple[object, str], pl.DataFrame] | None = None


_DuckLakeBindingKey = tuple[str, int, str, str, str, str, str]
_DuckLakeTableKey = tuple[str, int, str, str, str, str, str, int, str]
_ParquetFileStatKey = tuple[str, int, int]
_StateSqliteSigKey = tuple[str, int, int, bytes, int, int, bytes]
_TableDataCacheKey = tuple[object, ...]

_MAX_BINDING_CACHE_SIZE = 128
_MAX_TABLE_META_CACHE_SIZE = 512
_MAX_PARQUET_SCHEMA_CACHE_SIZE = 512
_MAX_TABLE_DATA_CACHE_SIZE = 24

_CACHE_LOCK = threading.Lock()
_CATALOG_DISCOVERY_LOCK = threading.Lock()
_TABLE_LOAD_LOCKS_GUARD = threading.Lock()
_TABLE_LOAD_LOCKS: dict[_TableDataCacheKey, threading.Lock] = {}
_VALIDATED_IDENTIFIERS: set[str] = set()

_ACTIVE_SNAPSHOT_POINTER_CACHE: OrderedDict[
    _StateSqliteSigKey, tuple[str, str | None] | None
] = OrderedDict()
_DUCKLAKE_BINDING_CACHE: OrderedDict[_DuckLakeBindingKey, _DuckLakeBinding] = (
    OrderedDict()
)
_DUCKLAKE_TABLE_CACHE: OrderedDict[_DuckLakeTableKey, _ResolvedDuckLakeTable] = (
    OrderedDict()
)
_PARQUET_SCHEMA_CACHE: OrderedDict[_ParquetFileStatKey, tuple[str, ...]] = OrderedDict()
_PARQUET_PUBLISHED_AT_CACHE: OrderedDict[_ParquetFileStatKey, str | None] = (
    OrderedDict()
)
_SNAPSHOT_TABLE_DATA_CACHE: OrderedDict[_TableDataCacheKey, _CachedTableData] = (
    OrderedDict()
)


_O_RDONLY_BINARY = os.O_RDONLY | getattr(os, "O_BINARY", 0)


def _read_file_prefix(path_str: str, size: int) -> bytes:
    fd = os.open(path_str, _O_RDONLY_BINARY)
    try:
        return os.read(fd, size)
    finally:
        os.close(fd)


def _state_sqlite_sig_key(state_path: Path) -> _StateSqliteSigKey | None:
    state_str = str(state_path)
    try:
        st = os.stat(state_str)
        if not stat.S_ISREG(st.st_mode) or st.st_size < 100:
            return None
        hdr = _read_file_prefix(state_str, 100)
    except OSError:
        return None
    if len(hdr) < 100 or not hdr.startswith(b"SQLite format 3\x00"):
        return None
    wal_mtime = 0
    wal_size = 0
    shm_hdr = b""
    try:
        wst = os.stat(f"{state_str}-wal")
        wal_mtime = wst.st_mtime_ns
        wal_size = wst.st_size
    except OSError:
        pass
    if wal_size > 0:
        try:
            shm_hdr = _read_file_prefix(f"{state_str}-shm", 128)
        except OSError:
            return None
    return (
        state_str,
        st.st_mtime_ns,
        st.st_size,
        hdr[24:44],
        wal_mtime,
        wal_size,
        shm_hdr,
    )


def _read_active_snapshot_pointer(state_path: Path) -> tuple[str, str | None] | None:
    sig_key = _state_sqlite_sig_key(state_path)
    if sig_key is not None:
        with _CACHE_LOCK:
            if sig_key in _ACTIVE_SNAPSHOT_POINTER_CACHE:
                return _ACTIVE_SNAPSHOT_POINTER_CACHE[sig_key]

    try:
        with closing(
            sqlite3.connect(f"{state_path.as_uri()}?mode=ro", uri=True, timeout=5)
        ) as sconn:
            row = sconn.execute(
                "SELECT snapshot_id, published_at FROM active_snapshot WHERE singleton = 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise ContractViolationError(
            f"Unable to read active snapshot from {state_path}."
        ) from exc

    if row is None:
        result: tuple[str, str | None] | None = None
    else:
        snapshot_id = str(row[0])
        state_published_at = str(row[1]) if row[1] is not None else None
        if (
            not snapshot_id.startswith("snap_")
            or Path(snapshot_id).name != snapshot_id
            or ":" in snapshot_id
        ):
            raise ContractViolationError(f"Invalid active snapshot ID in {state_path}.")
        result = (snapshot_id, state_published_at)

    if sig_key is not None:
        with _CACHE_LOCK:
            _ACTIVE_SNAPSHOT_POINTER_CACHE[sig_key] = result
            while len(_ACTIVE_SNAPSHOT_POINTER_CACHE) > _MAX_BINDING_CACHE_SIZE:
                _ACTIVE_SNAPSHOT_POINTER_CACHE.popitem(last=False)
    return result


def _catalog_file_identity(path: Path) -> int:
    try:
        st = os.stat(str(path))
    except OSError:
        return 0
    return st.st_ctime_ns if os.name == "nt" else st.st_ino


def _parquet_file_stat_key(path: Path) -> _ParquetFileStatKey | None:
    path_str = str(path)
    try:
        st = os.stat(path_str)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (path_str, st.st_mtime_ns, st.st_size)


def _get_table_load_lock(key: _TableDataCacheKey) -> threading.Lock:
    with _TABLE_LOAD_LOCKS_GUARD:
        lock = _TABLE_LOAD_LOCKS.get(key)
        if lock is None:
            if len(_TABLE_LOAD_LOCKS) >= 256:
                _TABLE_LOAD_LOCKS.clear()
            lock = threading.Lock()
            _TABLE_LOAD_LOCKS[key] = lock
        return lock


def _build_cached_table_data(filename: str, df: pl.DataFrame) -> _CachedTableData:
    cols = tuple(df.columns)
    allowed = set(cols)
    empty = df.clear()
    by_issuer: dict[str, pl.DataFrame] | None = None
    by_issuer_scope_period: dict[tuple[str, str, str], pl.DataFrame] | None = None
    by_ticker: dict[str, pl.DataFrame] | None = None
    by_period: dict[object, pl.DataFrame] | None = None
    by_period_metric: dict[tuple[object, str], pl.DataFrame] | None = None

    if "issuer_id" in allowed:
        by_issuer = {
            str(k[0]): sub
            for k, sub in df.partition_by(
                "issuer_id", as_dict=True, maintain_order=True
            ).items()
        }
    if {"issuer_id", "statement_scope", "period_type"}.issubset(
        allowed
    ) and filename in (
        "financial_ratios.parquet",
        "quality_summary.parquet",
        "accounting_red_flags.parquet",
        "ml_scores_and_explanations.parquet",
    ):
        by_issuer_scope_period = {
            (str(k[0]), str(k[1]), str(k[2])): sub
            for k, sub in df.partition_by(
                ["issuer_id", "statement_scope", "period_type"],
                as_dict=True,
                maintain_order=True,
            ).items()
        }
    if "ticker" in allowed and filename == "issuers.parquet":
        by_ticker = {
            str(k[0]): sub
            for k, sub in df.partition_by(
                "ticker", as_dict=True, maintain_order=True
            ).items()
        }
    if "period_end_date" in allowed and filename in (
        "financial_ratios.parquet",
        "accounting_red_flags.parquet",
        "ml_scores_and_explanations.parquet",
    ):
        by_period = {
            k[0]: sub
            for k, sub in df.partition_by(
                "period_end_date", as_dict=True, maintain_order=True
            ).items()
        }
    if {"period_end_date", "metric_or_ratio_name"}.issubset(
        allowed
    ) and filename == "industry_benchmarks.parquet":
        by_period_metric = {
            (k[0], str(k[1])): sub
            for k, sub in df.partition_by(
                ["period_end_date", "metric_or_ratio_name"],
                as_dict=True,
                maintain_order=True,
            ).items()
        }

    return _CachedTableData(
        columns=cols,
        allowed_columns=allowed,
        frame=df,
        empty_frame=empty,
        by_issuer=by_issuer,
        by_issuer_scope_period=by_issuer_scope_period,
        by_ticker=by_ticker,
        by_period=by_period,
        by_period_metric=by_period_metric,
    )


def _apply_polars_predicates(
    df: pl.DataFrame, filters: Mapping[str, object]
) -> pl.DataFrame:
    if not filters or df.is_empty():
        return df
    exprs: list[pl.Expr] = []
    for col, raw_val in filters.items():
        c = pl.col(col)
        if raw_val is None:
            exprs.append(c.is_null())
        elif isinstance(raw_val, FilterRange):
            if raw_val.ge is not None:
                exprs.append(c >= pl.lit(raw_val.ge))
            if raw_val.le is not None:
                exprs.append(c <= pl.lit(raw_val.le))
            if raw_val.gt is not None:
                exprs.append(c > pl.lit(raw_val.gt))
            if raw_val.lt is not None:
                exprs.append(c < pl.lit(raw_val.lt))
        elif isinstance(raw_val, _SCALAR_FILTER_TYPES):
            exprs.append(c == pl.lit(raw_val))
        elif isinstance(raw_val, _COLLECTION_FILTER_TYPES):
            items = list(raw_val)
            non_null = [item for item in items if item is not None]
            has_null = any(item is None for item in items)
            if non_null and has_null:
                exprs.append(c.is_in(non_null) | c.is_null())
            elif non_null:
                exprs.append(c.is_in(non_null))
            else:
                exprs.append(c.is_null())
    if not exprs:
        return df
    combined = exprs[0]
    for extra_expr in exprs[1:]:
        combined = combined & extra_expr
    return df.filter(combined)


def _filter_contains_bytes(filters: Mapping[str, object] | None) -> bool:
    if not filters:
        return False
    for raw_val in filters.values():
        if isinstance(raw_val, bytes):
            return True
        if isinstance(raw_val, _COLLECTION_FILTER_TYPES) and any(
            isinstance(item, bytes) for item in raw_val
        ):
            return True
    return False


def _query_cached_table_data(
    cached: _CachedTableData,
    *,
    selected_cols: list[str] | None,
    filters: Mapping[str, object] | None,
    force_empty: bool,
) -> pl.DataFrame | None:
    if force_empty:
        return (
            cached.empty_frame.select(selected_cols)
            if selected_cols is not None
            else cached.empty_frame.clone()
        )
    if not filters:
        return (
            cached.frame.select(selected_cols)
            if selected_cols is not None
            else cached.frame.clone()
        )
    if _filter_contains_bytes(filters):
        return None

    rem_filters = dict(filters)
    df = cached.frame

    if (
        cached.by_issuer_scope_period is not None
        and "issuer_id" in rem_filters
        and "statement_scope" in rem_filters
        and "period_type" in rem_filters
        and isinstance(rem_filters["issuer_id"], str)
        and isinstance(rem_filters["statement_scope"], str)
        and isinstance(rem_filters["period_type"], str)
    ):
        key3 = (
            cast(str, rem_filters.pop("issuer_id")),
            cast(str, rem_filters.pop("statement_scope")),
            cast(str, rem_filters.pop("period_type")),
        )
        df = cached.by_issuer_scope_period.get(key3, cached.empty_frame)
    elif (
        cached.by_issuer is not None
        and "issuer_id" in rem_filters
        and isinstance(rem_filters["issuer_id"], str)
    ):
        ival = cast(str, rem_filters.pop("issuer_id"))
        df = cached.by_issuer.get(ival, cached.empty_frame)
    elif (
        cached.by_ticker is not None
        and "ticker" in rem_filters
        and isinstance(rem_filters["ticker"], str)
    ):
        tval = cast(str, rem_filters.pop("ticker"))
        df = cached.by_ticker.get(tval, cached.empty_frame)
    elif (
        cached.by_period_metric is not None
        and "period_end_date" in rem_filters
        and "metric_or_ratio_name" in rem_filters
        and isinstance(rem_filters["period_end_date"], _SCALAR_FILTER_TYPES)
        and isinstance(rem_filters["metric_or_ratio_name"], str)
    ):
        pval = rem_filters.pop("period_end_date")
        mval = cast(str, rem_filters.pop("metric_or_ratio_name"))
        df = cached.by_period_metric.get((pval, mval), cached.empty_frame)
    elif (
        cached.by_period is not None
        and "period_end_date" in rem_filters
        and isinstance(rem_filters["period_end_date"], _SCALAR_FILTER_TYPES)
    ):
        pval = rem_filters.pop("period_end_date")
        df = cached.by_period.get(pval, cached.empty_frame)

    if df.is_empty():
        return (
            df.select(selected_cols)
            if selected_cols is not None and tuple(selected_cols) != cached.columns
            else df.clone()
        )

    try:
        if selected_cols is not None:
            all_same = tuple(selected_cols) == cached.columns
            if not rem_filters:
                return df.clone() if all_same else df.select(selected_cols)
            extra_cols = [c for c in rem_filters if c not in selected_cols]
            if not all_same:
                df = df.select([*selected_cols, *extra_cols])
            df = _apply_polars_predicates(df, rem_filters)
            return df.select(selected_cols) if (not all_same and extra_cols) else df
        if not rem_filters:
            return df.clone()
        return _apply_polars_predicates(df, rem_filters)
    except (pl.exceptions.PolarsError, TypeError):
        return None


def _normalize_table_name(table_name: str) -> tuple[str, str]:
    """Validate and normalize a table name against ``KNOWN_TABLES`` allowlist.

    Accepts both ``"financial_ratios.parquet"`` and ``"financial_ratios"`` and returns
    ``(parquet_filename, catalog_table_name)``.
    """
    if (
        not isinstance(table_name, str)
        or not table_name
        or table_name.strip() != table_name
        or Path(table_name).name != table_name
        or ":" in table_name
        or "/" in table_name
        or "\\" in table_name
    ):
        raise ValueError(
            "table_name must be a known table name or Parquet filename without path components."
        )
    filename = (
        table_name if table_name.endswith(".parquet") else f"{table_name}.parquet"
    )
    if filename not in KNOWN_TABLES:
        raise ValueError(f"Unknown or disallowed storage table: {table_name!r}")
    catalog_name = validate_sql_identifier(
        filename.removesuffix(".parquet"), label="table_name"
    )
    return filename, catalog_name


def _validate_columns(
    columns: Sequence[str] | None, allowed_columns: set[str], table_name: str
) -> list[str] | None:
    if columns is None:
        return None
    if isinstance(columns, (str, bytes)):
        raise ValueError("columns must be a sequence of column names, not str/bytes.")
    col_list = list(columns)
    if not col_list:
        raise ValueError("columns must not be empty.")
    seen: set[str] = set()
    for col in col_list:
        if not isinstance(col, str) or col not in _VALIDATED_IDENTIFIERS:
            validate_sql_identifier(col, label="column")
            _VALIDATED_IDENTIFIERS.add(col)
        if col in seen:
            raise ValueError(f"Duplicate column requested: {col!r}")
        if col not in allowed_columns:
            raise ValueError(f"Unknown column {col!r} for table {table_name!r}.")
        seen.add(col)
    return col_list


def _build_where_clause(
    filters: Mapping[str, object] | None,
    allowed_columns: set[str],
    table_name: str,
) -> tuple[str, list[object], bool]:
    """Build parameterized SQL WHERE clause safely, returning ``(sql, params, force_empty)``."""
    if not filters:
        return "", [], False

    clauses: list[str] = []
    params: list[object] = []
    force_empty = False

    for col, raw_val in filters.items():
        if not isinstance(col, str) or col not in _VALIDATED_IDENTIFIERS:
            validate_sql_identifier(col, label="filter column")
            _VALIDATED_IDENTIFIERS.add(col)
        if col not in allowed_columns:
            raise ValueError(f"Unknown filter column {col!r} for table {table_name!r}.")
        qcol = f'"{col}"'

        if raw_val is None:
            clauses.append(f"{qcol} IS NULL")
            continue

        if isinstance(raw_val, FilterRange):
            if raw_val.ge is not None:
                clauses.append(f"{qcol} >= ?")
                params.append(raw_val.ge)
            if raw_val.le is not None:
                clauses.append(f"{qcol} <= ?")
                params.append(raw_val.le)
            if raw_val.gt is not None:
                clauses.append(f"{qcol} > ?")
                params.append(raw_val.gt)
            if raw_val.lt is not None:
                clauses.append(f"{qcol} < ?")
                params.append(raw_val.lt)
            continue

        # Explicitly separate str/bytes (and other scalars) from collections so
        # strings NEVER expand into `IN ('F', 'P', 'T')`.
        if isinstance(raw_val, _SCALAR_FILTER_TYPES):
            if isinstance(raw_val, float) and not math.isfinite(raw_val):
                raise ValueError(f"Filter value for {col!r} must be finite.")
            clauses.append(f"{qcol} = ?")
            params.append(raw_val)
            continue

        if isinstance(raw_val, _COLLECTION_FILTER_TYPES):
            items = list(raw_val)
            if not items:
                force_empty = True
                continue
            has_null = False
            non_null: list[object] = []
            for item in items:
                if item is None:
                    has_null = True
                elif isinstance(item, _SCALAR_FILTER_TYPES):
                    if isinstance(item, float) and not math.isfinite(item):
                        raise ValueError(f"Filter value for {col!r} must be finite.")
                    non_null.append(item)
                else:
                    raise ValueError(
                        f"Unsupported element type {type(item).__name__} in filter {col!r}."
                    )
            if isinstance(raw_val, (set, frozenset)):
                non_null = sorted(non_null, key=repr)
            if non_null and has_null:
                placeholders = ", ".join("?" for _ in non_null)
                clauses.append(f"({qcol} IN ({placeholders}) OR {qcol} IS NULL)")
                params.extend(non_null)
            elif non_null:
                placeholders = ", ".join("?" for _ in non_null)
                clauses.append(f"{qcol} IN ({placeholders})")
                params.extend(non_null)
            else:
                clauses.append(f"{qcol} IS NULL")
            continue

        raise ValueError(
            f"Unsupported filter value type {type(raw_val).__name__} for column {col!r}."
        )

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params, force_empty


class ParquetRepository:
    """Stateless DuckDB query repository supporting DuckLake and legacy Parquet snapshots.

    Auto-discovers DuckLake when ``metadata/ducklake_catalog.sqlite`` holds the active
    snapshot from ``state.sqlite``, and falls back cleanly to immutable Parquet snapshots
    protected by ``filelock.ReadWriteLock``.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        financial_facts_path: Path | None = None,
        data_root: Path | None = None,
        catalog_path: Path | None = None,
        lakehouse_data_path: Path | None = None,
        catalog_alias: str = DEFAULT_CATALOG_ALIAS,
        catalog_driver: CatalogDriver = "sqlite",
        postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
        backend: Literal["auto", "parquet", "ducklake"] = "auto",
        _lock_held: bool = False,
        _pinned_binding: _DuckLakeBinding | None = None,
        _session_connection: duckdb.DuckDBPyConnection | None = None,
        _already_resolved: bool = False,
    ) -> None:
        selected = financial_facts_path or path or data_root
        if selected is None:
            raise ValueError("Provide a Parquet path or data_root.")
        self._source = selected if _already_resolved else selected.resolve()
        self._is_directory = (
            True
            if _already_resolved
            else (
                self._source.is_dir()
                or (financial_facts_path is None and path is None)
                or self._source.name == "snapshots"
            )
        )
        self._catalog_path = (
            (catalog_path if _already_resolved else catalog_path.resolve())
            if catalog_path is not None
            else None
        )
        self._lakehouse_data_path = (
            (
                lakehouse_data_path
                if _already_resolved
                else lakehouse_data_path.resolve()
            )
            if lakehouse_data_path is not None
            else None
        )
        self._catalog_alias = (
            catalog_alias
            if _already_resolved
            else validate_sql_identifier(catalog_alias, label="catalog_alias")
        )
        self._catalog_driver = catalog_driver
        self._postgres_dsn_env = postgres_dsn_env
        self._backend = backend
        self._lock_held = _lock_held
        self._pinned_binding = _pinned_binding
        self._session_connection = _session_connection
        self._cached_located_catalog: tuple[Path, Path, Path] | None = None

    def _find_ducklake_catalog(self) -> tuple[Path, Path, Path] | None:
        """Return ``(catalog_path, state_path, lake_data_path)`` if DuckLake is configured or present."""
        cached_loc = self._cached_located_catalog
        if cached_loc is not None:
            cat_c, state_c, _ = cached_loc
            if (self._catalog_driver == "postgres" and state_c.exists()) or (
                self._catalog_driver != "postgres" and cat_c.is_file()
            ):
                return cached_loc
            self._cached_located_catalog = None

        if self._catalog_driver == "postgres":
            state_candidates = (
                (self._catalog_path.parent / "state.sqlite",)
                if self._catalog_path is not None
                else (
                    self._source / "metadata" / "state.sqlite",
                    self._source / "data" / "metadata" / "state.sqlite",
                )
            )
            for state_path in state_candidates:
                if state_path.exists():
                    meta_dir = state_path.parent
                    data_root = meta_dir.parent
                    cat_path = self._catalog_path or (
                        meta_dir / "ducklake_catalog.sqlite"
                    )
                    lake_data = self._lakehouse_data_path or (
                        data_root / "lakehouse" / "data"
                    )
                    resolved_loc = (
                        cat_path.resolve(),
                        state_path.resolve(),
                        lake_data.resolve(),
                    )
                    self._cached_located_catalog = resolved_loc
                    return resolved_loc
            return None

        if self._catalog_path is not None:
            if not self._catalog_path.is_file():
                return None
            meta_dir = self._catalog_path.parent
            data_root = meta_dir.parent
            lake_data = self._lakehouse_data_path or (data_root / "lakehouse" / "data")
            resolved_loc = (self._catalog_path, meta_dir / "state.sqlite", lake_data)
            self._cached_located_catalog = resolved_loc
            return resolved_loc

        candidates = (
            self._source / "metadata" / "ducklake_catalog.sqlite",
            self._source / "data" / "metadata" / "ducklake_catalog.sqlite",
        )
        for cat in candidates:
            if cat.is_file():
                meta_dir = cat.parent
                data_root = meta_dir.parent
                lake_data = self._lakehouse_data_path or (
                    data_root / "lakehouse" / "data"
                )
                resolved_loc = (
                    cat.resolve(),
                    (meta_dir / "state.sqlite").resolve(),
                    lake_data.resolve(),
                )
                self._cached_located_catalog = resolved_loc
                return resolved_loc
        return None

    def _binding_cache_key(
        self,
        *,
        cat_path: Path,
        lake_data: Path,
        snapshot_id: str,
        cat_identity: int | None = None,
    ) -> _DuckLakeBindingKey:
        cat_id = (
            cat_identity
            if cat_identity is not None
            else (
                _catalog_file_identity(cat_path)
                if self._catalog_driver == "sqlite"
                else 0
            )
        )
        return (
            str(cat_path),
            cat_id,
            str(lake_data),
            self._catalog_alias,
            self._catalog_driver,
            self._postgres_dsn_env,
            snapshot_id,
        )

    def _table_meta_cache_key(
        self, binding: _DuckLakeBinding, filename: str
    ) -> _DuckLakeTableKey:
        base = self._binding_cache_key(
            cat_path=binding.catalog_path,
            lake_data=binding.data_path,
            snapshot_id=binding.snapshot_id,
            cat_identity=binding.catalog_identity,
        )
        return (*base, int(binding.ducklake_version), filename)

    def _snapshot_cache_key(self) -> tuple[object, ...] | None:
        binding = self._discover_ducklake_binding()
        if binding is not None:
            return (
                "ducklake",
                *self._binding_cache_key(
                    cat_path=binding.catalog_path,
                    lake_data=binding.data_path,
                    snapshot_id=binding.snapshot_id,
                    cat_identity=binding.catalog_identity,
                ),
                int(binding.ducklake_version),
            )
        source = self._source
        if self._is_directory and source.name.startswith("snap_"):
            return ("parquet_dir", str(source), source.name)
        snap_id = self.snapshot_id
        if snap_id is not None:
            return ("parquet_snap", str(source), snap_id)
        return None

    def _discover_ducklake_binding(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> _DuckLakeBinding | None:
        """Resolve active DuckLake snapshot binding, or return ``None`` for Parquet mode."""
        if self._pinned_binding is not None:
            return self._pinned_binding
        if (
            not self._is_directory
            or self._source.name.startswith("snap_")
            or self._backend == "parquet"
        ):
            return None

        located = self._find_ducklake_catalog()
        if located is None:
            return None
        cat_path, state_path, lake_data = located
        if not state_path.exists():
            return None

        pointer = _read_active_snapshot_pointer(state_path)
        if pointer is None:
            return None
        snapshot_id, state_published_at = pointer

        cat_id = (
            _catalog_file_identity(cat_path) if self._catalog_driver == "sqlite" else 0
        )
        cache_key = self._binding_cache_key(
            cat_path=cat_path,
            lake_data=lake_data,
            snapshot_id=snapshot_id,
            cat_identity=cat_id,
        )
        with _CACHE_LOCK:
            cached_binding = _DUCKLAKE_BINDING_CACHE.get(cache_key)
            if cached_binding is not None:
                return cached_binding

        with _CATALOG_DISCOVERY_LOCK:
            with _CACHE_LOCK:
                cached_binding = _DUCKLAKE_BINDING_CACHE.get(cache_key)
                if cached_binding is not None:
                    _DUCKLAKE_BINDING_CACHE.move_to_end(cache_key)
                    return cached_binding

            active_conn = (
                connection if connection is not None else self._session_connection
            )
            owns_conn = active_conn is None
            con = duckdb.connect(":memory:") if active_conn is None else active_conn
            try:
                ensure_ducklake_attached(
                    con,
                    catalog_path=cat_path,
                    data_path=lake_data,
                    catalog_alias=self._catalog_alias,
                    catalog_driver=self._catalog_driver,
                    postgres_dsn_env=self._postgres_dsn_env,
                    read_only=True,
                )
                audit_row = con.execute(
                    f"""
                    SELECT ducklake_version, published_at
                    FROM "{self._catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}"
                    WHERE snapshot_id = ?
                    ORDER BY ducklake_version DESC
                    LIMIT 1
                    """,
                    [snapshot_id],
                ).fetchone()
            except duckdb.CatalogException:
                audit_row = None
            except duckdb.Error as exc:
                raise ContractViolationError(
                    f"Unable to query DuckLake snapshot audit metadata at {cat_path}."
                ) from exc
            finally:
                if owns_conn:
                    con.close()

            if audit_row is None:
                if self._backend == "ducklake":
                    raise ContractViolationError(
                        f"Active snapshot {snapshot_id!r} is missing from DuckLake audit table."
                    )
                return None

            binding = _DuckLakeBinding(
                catalog_path=cat_path,
                data_path=lake_data,
                catalog_alias=self._catalog_alias,
                snapshot_id=snapshot_id,
                ducklake_version=int(audit_row[0]),
                published_at=str(audit_row[1])
                if audit_row[1] is not None
                else state_published_at,
                catalog_identity=cat_id,
            )
            with _CACHE_LOCK:
                _DUCKLAKE_BINDING_CACHE[cache_key] = binding
                while len(_DUCKLAKE_BINDING_CACHE) > _MAX_BINDING_CACHE_SIZE:
                    _DUCKLAKE_BINDING_CACHE.popitem(last=False)
            return binding

    @property
    def path(self) -> Path:
        """Return the facts path selected at the time of access."""
        binding = self._discover_ducklake_binding()
        if binding is not None:
            return (binding.data_path / "financial_facts.parquet").resolve()
        with self._read_path() as path:
            return path.resolve()

    @property
    def snapshot_id(self) -> str | None:
        """Return the pinned or currently active snapshot ID, if available."""
        if self._pinned_binding is not None:
            return self._pinned_binding.snapshot_id
        binding = self._discover_ducklake_binding()
        if binding is not None:
            return binding.snapshot_id

        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            return snapshot.name
        if not self._is_directory:
            return None
        snapshots = self._find_snapshot_root(source)
        if snapshots is None:
            return None
        lock_ctx = nullcontext() if self._lock_held else snapshot_read_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            return candidates[0].name if candidates else None

    def get_snapshot_metadata(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> dict[str, object]:
        """Return active snapshot metadata without direct ``_snapshot_meta.json`` file reads."""
        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            return {
                "snapshot_id": binding.snapshot_id,
                "published_at": binding.published_at,
                "storage_backend": "ducklake",
                "ducklake_version": binding.ducklake_version,
            }

        source = self._source
        snapshot_dir: Path | None = None
        if self._is_directory and source.name.startswith("snap_"):
            lock_ctx = (
                nullcontext() if self._lock_held else snapshot_read_lock(source.parent)
            )
            with lock_ctx:
                snapshot_dir = source if source.is_dir() else None
                published_at = (
                    self._read_parquet_snapshot_published_at(snapshot_dir)
                    if snapshot_dir is not None
                    else None
                )
        elif not self._is_directory and source.parent.name.startswith("snap_"):
            lock_ctx = (
                nullcontext()
                if self._lock_held
                else snapshot_read_lock(source.parent.parent)
            )
            with lock_ctx:
                snapshot_dir = source.parent if source.parent.is_dir() else None
                published_at = (
                    self._read_parquet_snapshot_published_at(snapshot_dir)
                    if snapshot_dir is not None
                    else None
                )
        elif self._is_directory:
            snapshots = self._find_snapshot_root(source)
            if snapshots is None:
                published_at = None
            else:
                lock_ctx = (
                    nullcontext() if self._lock_held else snapshot_read_lock(snapshots)
                )
                with lock_ctx:
                    candidates = self._snapshot_candidates(snapshots)
                    snapshot_dir = candidates[0] if candidates else None
                    published_at = (
                        self._read_parquet_snapshot_published_at(snapshot_dir)
                        if snapshot_dir is not None
                        else None
                    )
        else:
            published_at = None

        return {
            "snapshot_id": snapshot_dir.name if snapshot_dir is not None else None,
            "published_at": published_at,
            "storage_backend": "parquet",
            "ducklake_version": None,
        }

    @staticmethod
    def _read_parquet_snapshot_published_at(snapshot_dir: Path) -> str | None:
        meta_path = snapshot_dir / "_snapshot_meta.json"
        stat_key = _parquet_file_stat_key(meta_path)
        if stat_key is None:
            return None
        with _CACHE_LOCK:
            if stat_key in _PARQUET_PUBLISHED_AT_CACHE:
                _PARQUET_PUBLISHED_AT_CACHE.move_to_end(stat_key)
                return _PARQUET_PUBLISHED_AT_CACHE[stat_key]
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        resolved: str | None = None
        if isinstance(meta, dict):
            published_at = meta.get("published_at")
            if isinstance(published_at, str):
                resolved = published_at
        with _CACHE_LOCK:
            _PARQUET_PUBLISHED_AT_CACHE[stat_key] = resolved
            while len(_PARQUET_PUBLISHED_AT_CACHE) > _MAX_PARQUET_SCHEMA_CACHE_SIZE:
                _PARQUET_PUBLISHED_AT_CACHE.popitem(last=False)
        return resolved

    def pin_active_snapshot(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> ParquetRepository:
        """Return a repository bound to the active snapshot version/directory at call time."""
        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            return ParquetRepository(
                self._source,
                catalog_path=binding.catalog_path,
                lakehouse_data_path=binding.data_path,
                catalog_alias=binding.catalog_alias,
                catalog_driver=self._catalog_driver,
                postgres_dsn_env=self._postgres_dsn_env,
                backend="ducklake",
                _lock_held=self._lock_held,
                _pinned_binding=binding,
                _session_connection=connection or self._session_connection,
                _already_resolved=True,
            )

        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            if not snapshot.is_dir():
                raise FileNotFoundError(snapshot)
            return ParquetRepository(
                snapshot,
                backend="parquet",
                _lock_held=self._lock_held,
                _session_connection=connection or self._session_connection,
            )
        snapshots = self._find_snapshot_root(source) if self._is_directory else None
        if snapshots is None:
            raise FileNotFoundError(source / "published" / "snapshots")
        lock_ctx = nullcontext() if self._lock_held else snapshot_read_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            if not candidates:
                raise FileNotFoundError(snapshots)
            return ParquetRepository(
                candidates[0],
                backend="parquet",
                _lock_held=self._lock_held,
                _session_connection=connection or self._session_connection,
            )

    @contextmanager
    def snapshot_session(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> Iterator[ParquetRepository]:
        """Pin one consistent snapshot version across multiple table reads.

        ``connection`` is non-owning (e.g. a per-request ``conn.cursor()``); this
        method never closes a caller-supplied connection.
        """
        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            active_conn = (
                connection if connection is not None else self._session_connection
            )
            if (
                self._pinned_binding is not None
                and self._lock_held
                and active_conn is self._session_connection
            ):
                yield self
                return
            yield ParquetRepository(
                self._source,
                catalog_path=binding.catalog_path,
                lakehouse_data_path=binding.data_path,
                catalog_alias=binding.catalog_alias,
                catalog_driver=self._catalog_driver,
                postgres_dsn_env=self._postgres_dsn_env,
                backend="ducklake",
                _lock_held=True,
                _pinned_binding=binding,
                _session_connection=active_conn,
                _already_resolved=True,
            )
            return

        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            lock_ctx = (
                nullcontext()
                if self._lock_held
                else snapshot_read_lock(snapshot.parent)
            )
            with lock_ctx:
                if not snapshot.is_dir():
                    raise FileNotFoundError(snapshot)
                yield ParquetRepository(
                    snapshot,
                    backend="parquet",
                    _lock_held=True,
                    _session_connection=connection or self._session_connection,
                )
            return
        snapshots = self._find_snapshot_root(source) if self._is_directory else None
        if snapshots is None:
            raise FileNotFoundError(source / "published" / "snapshots")
        lock_ctx = nullcontext() if self._lock_held else snapshot_read_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            if not candidates:
                raise FileNotFoundError(snapshots)
            yield ParquetRepository(
                candidates[0],
                backend="parquet",
                _lock_held=True,
                _session_connection=connection or self._session_connection,
            )

    @contextmanager
    def _read_path(self, table_name: str | None = None) -> Iterator[Path]:
        source = self._source
        snapshot = source if self._is_directory else source.parent
        snapshots = None
        lock_root: Path | None
        if snapshot.name.startswith("snap_"):
            lock_root = snapshot.parent
        else:
            if self._is_directory:
                snapshots = self._find_snapshot_root(source)
            lock_root = snapshots
        lock_ctx = (
            nullcontext()
            if self._lock_held or lock_root is None
            else snapshot_read_lock(lock_root)
        )
        with lock_ctx:
            if not self._is_directory:
                yield source if table_name is None else source.with_name(table_name)
                return

            if snapshot.name.startswith("snap_"):
                yield (
                    source / "financial_facts.parquet"
                    if table_name is None
                    else source / table_name
                )
                return

            candidates = (
                self._snapshot_candidates(snapshots) if snapshots is not None else []
            )
            if table_name is not None:
                if candidates:
                    yield candidates[0] / table_name
                    return
                if snapshots is not None:
                    yield snapshots / table_name
                    return

            facts_path = self._discover_facts_file(source, candidates)
            yield facts_path if table_name is None else facts_path.with_name(table_name)

    @staticmethod
    def _find_snapshot_root(source: Path) -> Path | None:
        candidates = (
            (source,)
            if source.name == "snapshots"
            else (
                source / "published" / "snapshots",
                source / "data" / "published" / "snapshots",
            )
        )
        for root in candidates:
            if not root.is_dir():
                # SQLite can still point to an active snapshot after the root is lost.
                try:
                    ParquetRepository._snapshot_state_path(root).stat()
                except FileNotFoundError:
                    continue
            return root.resolve()
        return None

    @classmethod
    def _discover_facts_file(cls, root: Path, snapshots: list[Path]) -> Path:
        direct = root / "financial_facts.parquet"
        if direct.is_file():
            return direct

        for snapshot in snapshots:
            candidate = snapshot / "financial_facts.parquet"
            if candidate.is_file():
                return candidate

        curated = root / "curated" / "financial_facts.parquet"
        if curated.is_file():
            return curated
        batches = sorted(root.glob("curated/financial_facts/*/financial_facts.parquet"))
        if len(batches) == 1:
            return batches[0]
        if len(batches) > 1:
            resolved = cls._resolve_curated_batch(root, batches)
            if resolved is not None:
                return resolved
            raise ContractViolationError(
                "Multiple curated batches found; provide the active snapshot or an explicit file."
            )
        return root / "financial_facts.parquet"

    @classmethod
    def _resolve_curated_batch(cls, root: Path, batches: list[Path]) -> Path | None:
        state_candidates = (
            root / "metadata" / "state.sqlite",
            root / "data" / "metadata" / "state.sqlite",
        )
        state_path = next((p for p in state_candidates if p.is_file()), None)
        if state_path is None:
            return None
        by_run = {batch.parent.name: batch for batch in batches}
        try:
            with closing(
                sqlite3.connect(f"{state_path.as_uri()}?mode=ro", uri=True, timeout=5)
            ) as connection:
                rows = connection.execute(
                    """
                    SELECT r.run_id,
                           json_extract(r.cli_args_json, '$.period') IS NULL
                           AND COALESCE(json_array_length(r.cli_args_json, '$.tickers'), 0) = 0
                    FROM pipeline_runs AS r
                    JOIN pipeline_steps AS s ON s.run_id = r.run_id
                    WHERE s.stage_name = 'NORMALIZE' AND s.status = 'SUCCEEDED'
                    ORDER BY s.step_id DESC
                    """
                ).fetchall()
        except sqlite3.Error:
            return None
        fallback: Path | None = None
        for run_id_raw, legacy_unfiltered in rows:
            run_id = str(run_id_raw)
            candidate = by_run.get(run_id)
            if candidate is None or not candidate.is_file():
                continue
            if fallback is None:
                fallback = candidate
            meta_path = candidate.parent / "_normalize_meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(meta, dict) and meta.get("full_history") is True:
                    return candidate
            elif bool(legacy_unfiltered):
                return candidate
        return fallback

    @staticmethod
    def _snapshot_state_path(snapshot_root: Path) -> Path:
        return snapshot_root.parent.parent / "metadata" / "state.sqlite"

    @classmethod
    def _snapshot_candidates(cls, snapshot_root: Path) -> list[Path]:
        state_path = cls._snapshot_state_path(snapshot_root)
        try:
            state_path.stat()
        except FileNotFoundError:
            if not snapshot_root.is_dir():
                return []
            return sorted(
                (
                    path
                    for path in snapshot_root.iterdir()
                    if path.is_dir() and path.name.startswith("snap_")
                ),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )

        pointer = _read_active_snapshot_pointer(state_path)
        if pointer is None:
            return []
        snapshot_id, _ = pointer
        active = snapshot_root / snapshot_id
        if not active.is_dir():
            raise ContractViolationError(
                f"Active snapshot directory is missing: {active}."
            )
        return [active]

    def get_facts_as_of(
        self,
        as_of_date: date,
        require_exact_filing_date: bool = False,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Return the latest fact known at ``as_of_date`` for each business key.

        Exact mode uses only an exact filing publication date.  MVP mode uses
        ``effective_available_at`` so statutory availability delays remain
        respected even where the source did not expose an exact filing date.
        """
        if type(as_of_date) is not date:
            raise TypeError("as_of_date must be a datetime.date instance.")
        if type(require_exact_filing_date) is not bool:
            raise TypeError("require_exact_filing_date must be a bool.")

        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            return self._query_facts_ducklake(
                binding,
                as_of_date,
                require_exact_filing_date,
                connection=connection,
            )

        with self._read_path() as path:
            if not path.is_file():
                raise FileNotFoundError(path)
            active_conn = (
                connection if connection is not None else self._session_connection
            )
            if active_conn is not None:
                query = self._pit_facts_sql(
                    "read_parquet(?)", require_exact_filing_date
                )
                try:
                    result = active_conn.execute(
                        query, [str(path), as_of_date]
                    ).fetch_arrow_table()
                except (duckdb.Error, OSError) as exc:
                    raise ContractViolationError(
                        f"Unable to query financial facts at {path}."
                    ) from exc
                return cast(pl.DataFrame, pl.from_arrow(result))
            return self._query_facts(path, as_of_date, require_exact_filing_date)

    @staticmethod
    def _pit_facts_sql(source_expr: str, require_exact_filing_date: bool) -> str:
        date_column = (
            "published_at" if require_exact_filing_date else "effective_available_at"
        )
        predicate = f"{date_column} IS NOT NULL AND {date_column} <= CAST(? AS DATE)"
        if require_exact_filing_date:
            predicate += " AND published_at_basis = 'EXACT_FILING'"
        return f"""
            WITH eligible AS (
                SELECT *
                FROM {source_expr}
                WHERE {predicate}
            ), ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY issuer_id, statement_scope, period_type,
                                 fiscal_year_start_date, period_end_date,
                                 metric_id, taxonomy_version
                    ORDER BY {date_column} DESC, effective_available_at DESC,
                             revision_no DESC, filing_id DESC
                ) AS _pit_rank
                FROM eligible
            )
            SELECT * EXCLUDE (_pit_rank)
            FROM ranked
            WHERE _pit_rank = 1
        """

    def _query_facts(
        self, path: Path, as_of_date: date, require_exact_filing_date: bool
    ) -> pl.DataFrame:
        query = self._pit_facts_sql("read_parquet(?)", require_exact_filing_date)
        try:
            with duckdb.connect(":memory:") as connection:
                result = connection.execute(
                    query, [str(path), as_of_date]
                ).fetch_arrow_table()
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to query financial facts at {path}."
            ) from exc
        return cast(pl.DataFrame, pl.from_arrow(result))

    def _resolve_ducklake_table(
        self,
        con: duckdb.DuckDBPyConnection | None,
        binding: _DuckLakeBinding,
        filename: str,
        catalog_table: str,
    ) -> _ResolvedDuckLakeTable:
        table_key = self._table_meta_cache_key(binding, filename)
        with _CACHE_LOCK:
            cached = _DUCKLAKE_TABLE_CACHE.get(table_key)
        if cached is not None:
            if not cached.parquet_files or all(
                _parquet_file_stat_key(Path(p)) is not None
                for p in cached.parquet_files
            ):
                return cached
            with _CACHE_LOCK:
                _DUCKLAKE_TABLE_CACHE.pop(table_key, None)

        with _CATALOG_DISCOVERY_LOCK:
            with _CACHE_LOCK:
                cached = _DUCKLAKE_TABLE_CACHE.get(table_key)
                if cached is not None:
                    if not cached.parquet_files or all(
                        _parquet_file_stat_key(Path(p)) is not None
                        for p in cached.parquet_files
                    ):
                        _DUCKLAKE_TABLE_CACHE.move_to_end(table_key)
                        return cached
                    _DUCKLAKE_TABLE_CACHE.pop(table_key, None)

            active_conn = con if con is not None else self._session_connection
            owns_conn = active_conn is None
            work_con = (
                duckdb.connect(":memory:") if active_conn is None else active_conn
            )
            try:
                ensure_ducklake_attached(
                    work_con,
                    catalog_path=binding.catalog_path,
                    data_path=binding.data_path,
                    catalog_alias=binding.catalog_alias,
                    catalog_driver=self._catalog_driver,
                    postgres_dsn_env=self._postgres_dsn_env,
                    read_only=True,
                )
                ver = int(binding.ducklake_version)
                try:
                    file_rows = work_con.execute(
                        "SELECT data_file, delete_file, data_file_encryption_key "
                        "FROM ducklake_list_files(?, ?, snapshot_version => ?)",
                        [binding.catalog_alias, catalog_table, ver],
                    ).fetchall()
                except duckdb.CatalogException as exc:
                    if "does not exist" in str(exc).lower():
                        missing = _ResolvedDuckLakeTable(
                            exists=False, columns=(), parquet_files=()
                        )
                        with _CACHE_LOCK:
                            _DUCKLAKE_TABLE_CACHE[table_key] = missing
                            while (
                                len(_DUCKLAKE_TABLE_CACHE) > _MAX_TABLE_META_CACHE_SIZE
                            ):
                                _DUCKLAKE_TABLE_CACHE.popitem(last=False)
                        return missing
                    raise

                direct_ok = bool(file_rows) and all(
                    row[0] is not None
                    and row[1] is None
                    and row[2] is None
                    and _parquet_file_stat_key(Path(str(row[0]))) is not None
                    for row in file_rows
                )
                parquet_files = (
                    tuple(str(row[0]) for row in file_rows) if direct_ok else ()
                )

                if parquet_files:
                    param: object = (
                        parquet_files[0]
                        if len(parquet_files) == 1
                        else list(parquet_files)
                    )
                    schema_cursor = work_con.execute(
                        "SELECT * FROM read_parquet(?) WHERE 1 = 0", [param]
                    )
                else:
                    source_sql = (
                        f'"{binding.catalog_alias}"."{catalog_table}" '
                        f"AT (VERSION => {ver})"
                    )
                    schema_cursor = work_con.execute(
                        f"SELECT * FROM {source_sql} WHERE 1 = 0"
                    )
                description = schema_cursor.description
                if not description:
                    raise ContractViolationError(
                        f"Unable to inspect schema for table {filename!r}."
                    )
                cols = tuple(str(desc[0]) for desc in description)
                resolved = _ResolvedDuckLakeTable(
                    exists=True,
                    columns=cols,
                    parquet_files=parquet_files,
                )
                with _CACHE_LOCK:
                    _DUCKLAKE_TABLE_CACHE[table_key] = resolved
                    while len(_DUCKLAKE_TABLE_CACHE) > _MAX_TABLE_META_CACHE_SIZE:
                        _DUCKLAKE_TABLE_CACHE.popitem(last=False)
                return resolved
            finally:
                if owns_conn:
                    work_con.close()

    def _get_or_load_cached_table_data(
        self,
        cache_key: _TableDataCacheKey,
        filename: str,
        parquet_files: tuple[str, ...],
    ) -> _CachedTableData | None:
        if not parquet_files:
            return None
        with _CACHE_LOCK:
            cached = _SNAPSHOT_TABLE_DATA_CACHE.get(cache_key)
            if cached is not None:
                return cached

        load_lock = _get_table_load_lock(cache_key)
        with load_lock:
            with _CACHE_LOCK:
                cached = _SNAPSHOT_TABLE_DATA_CACHE.get(cache_key)
                if cached is not None:
                    _SNAPSHOT_TABLE_DATA_CACHE.move_to_end(cache_key)
                    return cached

            try:
                if len(parquet_files) == 1:
                    df = pl.read_parquet(parquet_files[0])
                else:
                    df = pl.concat([pl.read_parquet(p) for p in parquet_files])
            except (OSError, pl.exceptions.PolarsError):
                return None

            built = _build_cached_table_data(filename, df)
            with _CACHE_LOCK:
                _SNAPSHOT_TABLE_DATA_CACHE[cache_key] = built
                while len(_SNAPSHOT_TABLE_DATA_CACHE) > _MAX_TABLE_DATA_CACHE_SIZE:
                    _SNAPSHOT_TABLE_DATA_CACHE.popitem(last=False)
            if "pytest" not in sys.modules and filename in (
                "financial_facts.parquet",
                "industry_benchmarks.parquet",
                "financial_ratios.parquet",
            ):
                gc.freeze()
            return built

    def _inspect_parquet_columns(
        self,
        path: Path,
        stat_key: _ParquetFileStatKey,
        table_name: str,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, ...]:
        with _CACHE_LOCK:
            cached = _PARQUET_SCHEMA_CACHE.get(stat_key)
            if cached is not None:
                _PARQUET_SCHEMA_CACHE.move_to_end(stat_key)
                return cached

        active_conn = connection if connection is not None else self._session_connection
        owns_conn = active_conn is None
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            schema_cursor = con.execute(
                "SELECT * FROM read_parquet(?) WHERE 1 = 0", [str(path)]
            )
            description = schema_cursor.description
            if not description:
                raise ContractViolationError(
                    f"Unable to inspect schema for table {table_name!r}."
                )
            cols = tuple(str(desc[0]) for desc in description)
        finally:
            if owns_conn:
                con.close()

        with _CACHE_LOCK:
            _PARQUET_SCHEMA_CACHE[stat_key] = cols
            while len(_PARQUET_SCHEMA_CACHE) > _MAX_PARQUET_SCHEMA_CACHE_SIZE:
                _PARQUET_SCHEMA_CACHE.popitem(last=False)
        return cols

    def get_table_columns(
        self,
        table_name: str,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, ...] | None:
        """Return cached column names for ``table_name`` in the active snapshot, or ``None`` if absent."""
        filename, catalog_table = _normalize_table_name(table_name)
        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            try:
                resolved = self._resolve_ducklake_table(
                    connection, binding, filename, catalog_table
                )
            except duckdb.CatalogException as exc:
                if "does not exist" in str(exc).lower():
                    return None
                raise ContractViolationError(
                    f"Unable to inspect schema for table {filename!r}."
                ) from exc
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to inspect schema for table {filename!r}."
                ) from exc
            return resolved.columns if resolved.exists else None

        with self._read_path(filename) as path:
            stat_key = _parquet_file_stat_key(path)
            if stat_key is None:
                return None
            try:
                return self._inspect_parquet_columns(
                    path, stat_key, filename, connection=connection
                )
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to inspect schema for table {filename!r}."
                ) from exc

    def _query_facts_ducklake(
        self,
        binding: _DuckLakeBinding,
        as_of_date: date,
        require_exact_filing_date: bool,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        active_conn = connection if connection is not None else self._session_connection
        owns_conn = active_conn is None
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            resolved = self._resolve_ducklake_table(
                con, binding, "financial_facts.parquet", "financial_facts"
            )
            if not resolved.exists:
                raise FileNotFoundError("financial_facts.parquet")
            if resolved.parquet_files:
                param: object = (
                    resolved.parquet_files[0]
                    if len(resolved.parquet_files) == 1
                    else list(resolved.parquet_files)
                )
                query = self._pit_facts_sql(
                    "read_parquet(?)", require_exact_filing_date
                )
                arrow = con.execute(query, [param, as_of_date]).fetch_arrow_table()
            else:
                ensure_ducklake_attached(
                    con,
                    catalog_path=binding.catalog_path,
                    data_path=binding.data_path,
                    catalog_alias=binding.catalog_alias,
                    catalog_driver=self._catalog_driver,
                    postgres_dsn_env=self._postgres_dsn_env,
                    read_only=True,
                )
                source_expr = (
                    f'"{binding.catalog_alias}"."financial_facts" '
                    f"AT (VERSION => {int(binding.ducklake_version)})"
                )
                query = self._pit_facts_sql(source_expr, require_exact_filing_date)
                arrow = con.execute(query, [as_of_date]).fetch_arrow_table()
        except FileNotFoundError:
            raise
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                raise FileNotFoundError("financial_facts.parquet") from exc
            raise ContractViolationError(
                f"Unable to query DuckLake financial facts at {binding.catalog_path}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to query DuckLake financial facts at {binding.catalog_path}."
            ) from exc
        finally:
            if owns_conn:
                con.close()
        return cast(pl.DataFrame, pl.from_arrow(arrow))

    def _execute_table_query(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        source_sql: str,
        source_params: list[object],
        table_name: str,
        columns: Sequence[str] | None,
        filters: Mapping[str, object] | None,
        known_columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        if columns is None and not filters:
            arrow = con.execute(
                f"SELECT * FROM {source_sql}", source_params
            ).fetch_arrow_table()
            return cast(pl.DataFrame, pl.from_arrow(arrow))

        if known_columns is not None:
            allowed_columns = set(known_columns)
        else:
            schema_cursor = con.execute(
                f"SELECT * FROM {source_sql} WHERE 1 = 0", source_params
            )
            description = schema_cursor.description
            if not description:
                raise ContractViolationError(
                    f"Unable to inspect schema for table {table_name!r}."
                )
            allowed_columns = {str(desc[0]) for desc in description}

        selected_cols = _validate_columns(columns, allowed_columns, table_name)
        proj_sql = (
            ", ".join(f'"{col}"' for col in selected_cols)
            if selected_cols is not None
            else "*"
        )
        where_sql, filter_params, force_empty = _build_where_clause(
            filters, allowed_columns, table_name
        )
        if force_empty:
            arrow = con.execute(
                f"SELECT {proj_sql} FROM {source_sql} WHERE 1 = 0",
                source_params,
            ).fetch_arrow_table()
            return cast(pl.DataFrame, pl.from_arrow(arrow))

        arrow = con.execute(
            f"SELECT {proj_sql} FROM {source_sql}{where_sql}",
            [*source_params, *filter_params],
        ).fetch_arrow_table()
        return cast(pl.DataFrame, pl.from_arrow(arrow))

    def read_table(
        self,
        table_name: str,
        *,
        columns: Sequence[str] | None = None,
        filters: Mapping[str, object] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read a table from the active snapshot with SQLi-safe projection and filter pushdown."""
        filename, catalog_table = _normalize_table_name(table_name)

        # Fail fast on obviously malformed column/filter identifiers before I/O.
        if columns is not None:
            if isinstance(columns, (str, bytes)):
                raise ValueError(
                    "columns must be a sequence of column names, not str/bytes."
                )
            col_list = list(columns)
            if not col_list:
                raise ValueError("columns must not be empty.")
            seen: set[str] = set()
            for col in col_list:
                if not isinstance(col, str) or col not in _VALIDATED_IDENTIFIERS:
                    validate_sql_identifier(col, label="column")
                    _VALIDATED_IDENTIFIERS.add(col)
                if col in seen:
                    raise ValueError(f"Duplicate column requested: {col!r}")
                seen.add(col)
        if filters is not None:
            for col in filters:
                if not isinstance(col, str) or col not in _VALIDATED_IDENTIFIERS:
                    validate_sql_identifier(col, label="filter column")
                    _VALIDATED_IDENTIFIERS.add(col)

        binding = self._discover_ducklake_binding(connection=connection)
        if binding is not None:
            try:
                resolved = self._resolve_ducklake_table(
                    connection, binding, filename, catalog_table
                )
            except duckdb.CatalogException as exc:
                if "does not exist" in str(exc).lower():
                    raise FileNotFoundError(filename) from exc
                raise ContractViolationError(
                    f"Unable to query DuckLake table {filename!r}."
                ) from exc
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to query DuckLake table {filename!r}."
                ) from exc

            if not resolved.exists:
                raise FileNotFoundError(filename)

            allowed_set = set(resolved.columns)
            selected_cols = _validate_columns(columns, allowed_set, filename)
            _, _, force_empty = _build_where_clause(filters, allowed_set, filename)

            if resolved.parquet_files:
                data_key: _TableDataCacheKey = (
                    "ducklake_data",
                    *self._table_meta_cache_key(binding, filename),
                    resolved.parquet_files,
                )
                cached_data = self._get_or_load_cached_table_data(
                    data_key, filename, resolved.parquet_files
                )
                if cached_data is not None:
                    fast_res = _query_cached_table_data(
                        cached_data,
                        selected_cols=selected_cols,
                        filters=filters,
                        force_empty=force_empty,
                    )
                    if fast_res is not None:
                        return fast_res

            active_conn = (
                connection if connection is not None else self._session_connection
            )
            owns_conn = active_conn is None
            con = duckdb.connect(":memory:") if active_conn is None else active_conn
            try:
                if resolved.parquet_files:
                    param: object = (
                        resolved.parquet_files[0]
                        if len(resolved.parquet_files) == 1
                        else list(resolved.parquet_files)
                    )
                    return self._execute_table_query(
                        con,
                        source_sql="read_parquet(?)",
                        source_params=[param],
                        table_name=filename,
                        columns=columns,
                        filters=filters,
                        known_columns=resolved.columns,
                    )
                ensure_ducklake_attached(
                    con,
                    catalog_path=binding.catalog_path,
                    data_path=binding.data_path,
                    catalog_alias=binding.catalog_alias,
                    catalog_driver=self._catalog_driver,
                    postgres_dsn_env=self._postgres_dsn_env,
                    read_only=True,
                )
                source_sql = (
                    f'"{binding.catalog_alias}"."{catalog_table}" '
                    f"AT (VERSION => {int(binding.ducklake_version)})"
                )
                return self._execute_table_query(
                    con,
                    source_sql=source_sql,
                    source_params=[],
                    table_name=filename,
                    columns=columns,
                    filters=filters,
                    known_columns=resolved.columns,
                )
            except duckdb.CatalogException as exc:
                if "does not exist" in str(exc).lower():
                    raise FileNotFoundError(filename) from exc
                raise ContractViolationError(
                    f"Unable to query DuckLake table {filename!r}."
                ) from exc
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to query DuckLake table {filename!r}."
                ) from exc
            finally:
                if owns_conn:
                    con.close()

        with self._read_path(filename) as path:
            stat_key = _parquet_file_stat_key(path)
            if stat_key is None:
                raise FileNotFoundError(path)
            try:
                known_cols = self._inspect_parquet_columns(
                    path, stat_key, filename, connection=connection
                )
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to query snapshot table at {path}."
                ) from exc

            allowed_set = set(known_cols)
            selected_cols = _validate_columns(columns, allowed_set, filename)
            _, _, force_empty = _build_where_clause(filters, allowed_set, filename)

            pq_data_key: _TableDataCacheKey = ("parquet_data", *stat_key)
            cached_data = self._get_or_load_cached_table_data(
                pq_data_key, filename, (str(path),)
            )
            if cached_data is not None:
                fast_res = _query_cached_table_data(
                    cached_data,
                    selected_cols=selected_cols,
                    filters=filters,
                    force_empty=force_empty,
                )
                if fast_res is not None:
                    return fast_res

            active_conn = (
                connection if connection is not None else self._session_connection
            )
            owns_conn = active_conn is None
            con = duckdb.connect(":memory:") if active_conn is None else active_conn
            try:
                return self._execute_table_query(
                    con,
                    source_sql="read_parquet(?)",
                    source_params=[str(path)],
                    table_name=filename,
                    columns=columns,
                    filters=filters,
                    known_columns=known_cols,
                )
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to query snapshot table at {path}."
                ) from exc
            finally:
                if owns_conn:
                    con.close()

    def get_latest_facts(
        self,
        *,
        columns: Sequence[str] | None = None,
        filters: Mapping[str, object] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read the latest facts table from the selected snapshot."""
        return self.read_table(
            "latest_facts.parquet",
            columns=columns,
            filters=filters,
            connection=connection,
        )


def clear_repository_caches() -> None:
    """Clear all thread-safe repository metadata, schema, and snapshot table caches."""
    with _CACHE_LOCK:
        _ACTIVE_SNAPSHOT_POINTER_CACHE.clear()
        _DUCKLAKE_BINDING_CACHE.clear()
        _DUCKLAKE_TABLE_CACHE.clear()
        _PARQUET_SCHEMA_CACHE.clear()
        _PARQUET_PUBLISHED_AT_CACHE.clear()
        _SNAPSHOT_TABLE_DATA_CACHE.clear()


Repository = ParquetRepository
DuckDBRepository = ParquetRepository
DuckDBQueryRepository = ParquetRepository
