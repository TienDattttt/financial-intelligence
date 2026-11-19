"""Atomic immutable snapshot publication, physical layout, RWLock, and bounded GC.

Verified Sources:
- DuckDB Parquet Tips (row_group_size & sorted zone maps):
  https://duckdb.org/docs/stable/data/parquet/tips
- filelock ReadWriteLock Documentation (is_singleton=False, read_lock, write_lock, close):
  https://py-filelock.readthedocs.io/
- Moerkotte, G. (1998). Small Materialized Aggregates (VLDB 1998).
- Zeng, X. et al. (2023). An Empirical Evaluation of Columnar Storage Formats (PVLDB 17(2)).
- Michael, M. M. (2004) & Hart, T. E. et al. (2007) — two-phase deferred reclamation.

Runtime API Verification (Python 3.11.9, polars==1.35.2, filelock==4.2.10):
- ``filelock.ReadWriteLock(path, timeout=-1, *, blocking=True, is_singleton=False)``
  supports concurrent cross-thread/cross-process ``read_lock()`` and exclusive
  ``write_lock()``, and releases SQLite handles via ``close()``.
- ``filelock.FileLock`` defaults to ``is_singleton=False``; same-thread nesting with
  ``Runner.run`` is coordinated in ``pipeline_write_lock()`` without modifying ``runner.py``.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

import polars as pl
from filelock import FileLock, ReadWriteLock

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import (
    ANALYTICS_SCHEMAS,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ISSUERS_SCHEMA,
    ML_SCORES_TABLE,
    OPTIONAL_ANALYTICS_SCHEMAS,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    UNMAPPED_METRICS_SCHEMA,
    canonicalize_financial_facts,
    canonicalize_table,
    validate_analytics_table,
)

logger = logging.getLogger("finintel.storage")
TableInput: TypeAlias = pl.DataFrame | Path
_ALLOW_EMPTY_TABLES = frozenset(OPTIONAL_ANALYTICS_SCHEMAS) | {
    "unmapped_metrics.parquet"
}
DEFAULT_PIPELINE_LOCK_TIMEOUT = 30.0
_THREAD_LOCK_STATE = threading.local()


@dataclass(frozen=True)
class TablePhysicalLayout:
    """Physical sort order and Parquet row-group sizing for one Layer 3 table."""

    sort_by: tuple[str, ...]
    row_group_size: int


TABLE_PHYSICAL_LAYOUT: Mapping[str, TablePhysicalLayout] = {
    "financial_facts.parquet": TablePhysicalLayout(
        sort_by=(
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "metric_id",
        ),
        row_group_size=25_000,
    ),
    "latest_facts.parquet": TablePhysicalLayout(
        sort_by=(
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "metric_id",
        ),
        row_group_size=25_000,
    ),
    RATIOS_TABLE: TablePhysicalLayout(
        sort_by=(
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
        ),
        row_group_size=2_000,
    ),
    BENCHMARKS_TABLE: TablePhysicalLayout(
        sort_by=(
            "statement_scope",
            "period_type",
            "metric_or_ratio_name",
            "period_end_date",
            "peer_group_level",
            "icb_code_used",
            "issuer_id",
        ),
        row_group_size=25_000,
    ),
    RED_FLAGS_TABLE: TablePhysicalLayout(
        sort_by=("issuer_id", "period_end_date"),
        row_group_size=2_000,
    ),
    ML_SCORES_TABLE: TablePhysicalLayout(
        sort_by=("issuer_id", "period_end_date"),
        row_group_size=2_000,
    ),
    QUALITY_SUMMARY_TABLE: TablePhysicalLayout(
        sort_by=("issuer_id", "period_end_date"),
        row_group_size=2_000,
    ),
    AUDIT_QUARANTINE_TABLE: TablePhysicalLayout(
        sort_by=("issuer_id", "period_end_date"),
        row_group_size=2_000,
    ),
    FILINGS_MANIFEST_TABLE: TablePhysicalLayout(
        sort_by=("filing_id",),
        row_group_size=25_000,
    ),
    "issuers.parquet": TablePhysicalLayout(
        sort_by=("ticker", "issuer_id"),
        row_group_size=5_000,
    ),
    "unmapped_metrics.parquet": TablePhysicalLayout(
        sort_by=("issuer_id", "period_end_date"),
        row_group_size=25_000,
    ),
}

KNOWN_TABLES: frozenset[str] = frozenset(TABLE_PHYSICAL_LAYOUT)
KNOWN_CATALOG_TABLES: frozenset[str] = frozenset(
    name.removesuffix(".parquet") for name in KNOWN_TABLES
)


def sort_table_for_storage(filename: str, frame: pl.DataFrame) -> pl.DataFrame:
    """Physically sort a Layer 3 table according to ``TABLE_PHYSICAL_LAYOUT``."""
    layout = TABLE_PHYSICAL_LAYOUT.get(filename)
    if layout is None or frame.height <= 1:
        return frame
    sort_cols = [col for col in layout.sort_by if col in frame.columns]
    if not sort_cols:
        return frame
    return frame.sort(sort_cols, nulls_last=True)


def row_group_size_for_table(filename: str) -> int:
    """Return the configured Parquet row group size for a Layer 3 table."""
    layout = TABLE_PHYSICAL_LAYOUT.get(filename)
    return layout.row_group_size if layout is not None else 25_000


def prepare_storage_table(filename: str, table: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and validate a Layer 3 table against its storage contract."""
    if filename in {"financial_facts.parquet", "latest_facts.parquet"}:
        return canonicalize_financial_facts(table)
    if filename == "issuers.parquet":
        return canonicalize_table(table, ISSUERS_SCHEMA)
    if filename == "unmapped_metrics.parquet":
        return canonicalize_table(table, UNMAPPED_METRICS_SCHEMA)
    if filename in ANALYTICS_SCHEMAS:
        validate_analytics_table(filename, table)
    return table


def snapshot_lock_path(root: Path) -> Path:
    """Return the path of the ReadWriteLock file guarding ``root``."""
    return root.parent / f".{root.name}.publish.lock"


def snapshot_lock(root: Path) -> ReadWriteLock:
    """Return a non-singleton ReadWriteLock shared by snapshot readers, publish, and GC."""
    return ReadWriteLock(snapshot_lock_path(root), is_singleton=False)


@contextmanager
def snapshot_read_lock(root: Path, *, timeout: float = -1) -> Iterator[None]:
    """Acquire a shared snapshot read lock and always close its SQLite handle."""
    root.parent.mkdir(parents=True, exist_ok=True)
    rw_lock = snapshot_lock(root)
    try:
        with rw_lock.read_lock(timeout=timeout):
            yield
    finally:
        rw_lock.close()


@contextmanager
def snapshot_write_lock(root: Path, *, timeout: float = -1) -> Iterator[None]:
    """Acquire an exclusive snapshot write lock and always close its SQLite handle."""
    root.parent.mkdir(parents=True, exist_ok=True)
    rw_lock = snapshot_lock(root)
    try:
        with rw_lock.write_lock(timeout=timeout):
            yield
    finally:
        rw_lock.close()


def _held_pipeline_lock_counts() -> dict[Path, int]:
    counts: dict[Path, int] | None = getattr(_THREAD_LOCK_STATE, "counts", None)
    if counts is None:
        counts = {}
        _THREAD_LOCK_STATE.counts = counts
    return counts


def _runner_holds_pipeline_lock_on_current_thread(resolved_lock_path: Path) -> bool:
    """Detect whether ``Runner.run`` already holds the same ``pipeline.lock`` on this thread."""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_code.co_name == "run":
                local_lock = frame.f_locals.get("lock")
                if (
                    isinstance(local_lock, FileLock)
                    and local_lock.is_locked
                    and Path(local_lock.lock_file).resolve() == resolved_lock_path
                ):
                    return True
            frame = frame.f_back
    finally:
        del frame
    return False


@contextmanager
def pipeline_write_lock(
    lock_path: Path, *, timeout: float = DEFAULT_PIPELINE_LOCK_TIMEOUT
) -> Iterator[None]:
    """Serialize all storage writers (publish, maintain, rollback, curated_gc) via ``pipeline.lock``.

    Supports safe same-thread nesting (including when invoked from ``Runner.run``)
    while strictly blocking concurrent writers in other threads or processes.
    """
    resolved = lock_path.resolve()
    counts = _held_pipeline_lock_counts()
    current_depth = counts.get(resolved, 0)
    if current_depth > 0:
        counts[resolved] = current_depth + 1
        try:
            yield
        finally:
            counts[resolved] -= 1
            if counts[resolved] <= 0:
                counts.pop(resolved, None)
        return

    if _runner_holds_pipeline_lock_on_current_thread(resolved):
        counts[resolved] = 1
        try:
            yield
        finally:
            counts.pop(resolved, None)
        return

    resolved.parent.mkdir(parents=True, exist_ok=True)
    file_lock = FileLock(resolved, timeout=timeout, is_singleton=False)
    with file_lock:
        counts[resolved] = 1
        try:
            yield
        finally:
            counts.pop(resolved, None)


class SnapshotPublisher:
    """Stage a complete sorted Parquet snapshot, atomically activate it, then retain N."""

    def __init__(
        self,
        snapshots_root: Path,
        *,
        state_store: StateStore | None = None,
        keep_snapshots: int = 2,
        min_snapshot_age_seconds: int = 0,
    ) -> None:
        if type(keep_snapshots) is not int or keep_snapshots < 2:
            raise ValueError("At least two snapshots are required for rollback safety.")
        if type(min_snapshot_age_seconds) is not int or min_snapshot_age_seconds < 0:
            raise ValueError("min_snapshot_age_seconds must be a non-negative integer.")
        self.root = snapshots_root.resolve()
        self.state_store = state_store
        self.keep_snapshots = keep_snapshots
        self.min_snapshot_age_seconds = min_snapshot_age_seconds
        self._stage_lock = FileLock(self.root.parent / f".{self.root.name}.stage.lock")

    def publish(
        self,
        snapshot_id: str,
        tables: Mapping[str, TableInput],
        run_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Path:
        """Publish sorted Parquet tables under ``snap_<id>`` and return its directory."""

        self._validate_snapshot_id(snapshot_id)
        if not tables:
            raise ValueError("A snapshot must contain at least one Parquet table.")
        if self.state_store is not None and run_id is None:
            raise ValueError("run_id is required when publishing with a StateStore.")
        self.root.mkdir(parents=True, exist_ok=True)
        writer_ctx = (
            pipeline_write_lock(self.state_store.path.with_name("pipeline.lock"))
            if self.state_store is not None
            else nullcontext()
        )
        with writer_ctx, self._stage_lock:
            staging = self.root / f"_tmp_{snapshot_id}"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            try:
                for filename, table in tables.items():
                    self._write_table(staging, filename, table)
                meta = {
                    **(dict(metadata) if metadata is not None else {}),
                    "snapshot_id": snapshot_id,
                    "published_at": datetime.now(UTC).isoformat(),
                    "files": sorted(tables),
                }
                (staging / "_snapshot_meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str),
                    encoding="utf-8",
                )
                self._verify(staging, tables)
                with snapshot_write_lock(self.root):
                    self._remove_staging_directories(preserve=staging)
                    destination = self.root / snapshot_id
                    if destination.exists():
                        if (
                            self.state_store is not None
                            and snapshot_id
                            not in self.state_store.published_snapshot_ids()
                        ):
                            self._remove_snapshot(destination)
                        if destination.exists():
                            raise FileExistsError(destination)
                    os.replace(staging, destination)
                    if self.state_store is not None and run_id is not None:
                        try:
                            self.state_store.publish_snapshot(run_id, snapshot_id)
                        except BaseException:
                            if not self._is_snapshot_committed(snapshot_id):
                                shutil.rmtree(destination, ignore_errors=True)
                            raise
                    self._collect_garbage_unlocked()
                    return destination
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise

    publish_snapshot = publish

    def _is_snapshot_committed(self, snapshot_id: str) -> bool:
        if self.state_store is None:
            return False
        try:
            active = self.state_store.active_snapshot()
            if active is not None and str(active.get("snapshot_id")) == snapshot_id:
                return True
            return snapshot_id in self.state_store.published_snapshot_ids()
        except Exception:
            return True

    def collect_garbage(self) -> None:
        """Delete abandoned staging directories and aged snapshots beyond retention."""

        self.root.mkdir(parents=True, exist_ok=True)
        writer_ctx = (
            pipeline_write_lock(self.state_store.path.with_name("pipeline.lock"))
            if self.state_store is not None
            else nullcontext()
        )
        with writer_ctx, snapshot_write_lock(self.root):
            self._collect_garbage_unlocked()

    def _collect_garbage_unlocked(self) -> None:
        """Run garbage collection while the caller holds the exclusive snapshot lock."""

        self._remove_staging_directories()
        snapshots = {
            path.name: path
            for path in self.root.iterdir()
            if path.is_dir() and path.name.startswith("snap_")
        }
        if self.state_store is None:
            ordered_ids = sorted(
                snapshots,
                key=lambda snapshot_id: (
                    snapshots[snapshot_id].stat().st_mtime_ns,
                    snapshot_id,
                ),
                reverse=True,
            )
            keep = set(ordered_ids[: self.keep_snapshots])
        else:
            ordered_ids = list(
                dict.fromkeys(
                    snapshot_id
                    for snapshot_id in self.state_store.published_snapshot_ids()
                    if snapshot_id in snapshots
                )
            )
            keep = set(ordered_ids[: self.keep_snapshots])
            active = self.state_store.active_snapshot()
            if active is not None and active.get("snapshot_id") is not None:
                keep.add(str(active["snapshot_id"]))

        now = time.time()
        for snapshot_id, path in snapshots.items():
            if snapshot_id in keep:
                continue
            if self.min_snapshot_age_seconds > 0:
                try:
                    age_seconds = now - path.stat().st_mtime
                except OSError:
                    continue
                if age_seconds < self.min_snapshot_age_seconds:
                    continue
            self._remove_snapshot(path)

    @staticmethod
    def _remove_snapshot(path: Path) -> bool:
        """Atomically quarantine then remove a directory, deferring safely on Windows locks."""
        quarantined = path.parent / f"_tmp_gc_{path.name}_{uuid4().hex[:8]}"
        try:
            os.replace(path, quarantined)
        except OSError:
            # On Windows, renaming a directory with an open file handle raises
            # PermissionError before any file is unlinked, keeping the snapshot
            # intact and its mtime unchanged until the next GC cycle.
            logger.warning(
                "Deferred removal of locked snapshot directory",
                extra={
                    "event": "snapshot_gc_deferred",
                    "details": {"snapshot": path.name},
                },
            )
            return False
        try:
            shutil.rmtree(quarantined)
        except OSError:
            logger.warning(
                "Incomplete cleanup of quarantined snapshot directory",
                extra={
                    "event": "snapshot_gc_incomplete",
                    "details": {"snapshot": path.name},
                },
            )
        return True

    def _write_table(self, staging: Path, filename: str, table: TableInput) -> None:
        if (
            Path(filename).name != filename
            or ":" in filename
            or not filename.endswith(".parquet")
        ):
            raise ValueError(f"Snapshot table must be a Parquet filename: {filename}")
        destination = staging / filename
        if isinstance(table, pl.DataFrame):
            table_frame = table
        else:
            source = table.resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            try:
                table_frame = pl.read_parquet(source)
            except (OSError, pl.exceptions.PolarsError) as exc:
                raise ContractViolationError(
                    f"Unable to read Parquet table {filename}."
                ) from exc

        table_frame = self._prepare_table(filename, table_frame)
        if table_frame.is_empty() and filename not in _ALLOW_EMPTY_TABLES:
            raise ValueError(f"Snapshot table is empty: {filename}")

        sorted_frame = sort_table_for_storage(filename, table_frame)
        sorted_frame.write_parquet(
            destination,
            row_group_size=row_group_size_for_table(filename),
        )

    @staticmethod
    def _prepare_table(filename: str, table: pl.DataFrame) -> pl.DataFrame:
        return prepare_storage_table(filename, table)

    def _verify(self, staging: Path, tables: Mapping[str, TableInput]) -> None:
        for filename in tables:
            path = staging / filename
            if not path.is_file():
                raise OSError(f"Snapshot table was not written: {filename}")
            try:
                row_count = int(pl.scan_parquet(path).select(pl.len()).collect().item())
            except (OSError, pl.exceptions.PolarsError) as exc:
                raise ContractViolationError(
                    f"Staged Parquet table is unreadable: {filename}."
                ) from exc
            if row_count <= 0 and filename not in _ALLOW_EMPTY_TABLES:
                raise ValueError(f"Snapshot table is empty: {filename}")
        if not (staging / "_snapshot_meta.json").is_file():
            raise OSError("Snapshot metadata was not written.")

    garbage_collect = collect_garbage

    def _remove_staging_directories(self, *, preserve: Path | None = None) -> None:
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if (
                path.is_dir()
                and path.name.startswith("_tmp_")
                and (preserve is None or path.resolve() != preserve.resolve())
            ):
                shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> None:
        if (
            not snapshot_id.startswith("snap_")
            or Path(snapshot_id).name != snapshot_id
            or ":" in snapshot_id
        ):
            raise ValueError(
                "snapshot_id must be a path-safe value beginning with 'snap_'."
            )
