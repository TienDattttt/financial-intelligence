"""Atomic immutable snapshot publication and bounded garbage collection."""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

import polars as pl
from filelock import FileLock

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import (
    ANALYTICS_SCHEMAS,
    ISSUERS_SCHEMA,
    OPTIONAL_ANALYTICS_SCHEMAS,
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


def snapshot_lock(root: Path) -> FileLock:
    """Return the lock shared by snapshot readers, publication and GC."""
    return FileLock(root.parent / f".{root.name}.publish.lock")


class SnapshotPublisher:
    """Stage a complete snapshot, atomically activate it, then retain two."""

    def __init__(
        self,
        snapshots_root: Path,
        *,
        state_store: StateStore | None = None,
        keep_snapshots: int = 2,
    ) -> None:
        if keep_snapshots < 2:
            raise ValueError("At least two snapshots are required for rollback safety.")
        self.root = snapshots_root.resolve()
        self.state_store = state_store
        self.keep_snapshots = keep_snapshots
        self._lock = snapshot_lock(self.root)
        self._stage_lock = FileLock(self.root.parent / f".{self.root.name}.stage.lock")

    def publish(
        self,
        snapshot_id: str,
        tables: Mapping[str, TableInput],
        run_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Path:
        """Publish tables under ``snap_<id>`` and return its active directory."""

        self._validate_snapshot_id(snapshot_id)
        if not tables:
            raise ValueError("A snapshot must contain at least one Parquet table.")
        if self.state_store is not None and run_id is None:
            raise ValueError("run_id is required when publishing with a StateStore.")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._stage_lock:
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
                with self._lock:
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
        """Delete abandoned staging directories and all but newest snapshots."""

        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._collect_garbage_unlocked()

    def _collect_garbage_unlocked(self) -> None:
        """Run garbage collection while the caller holds ``self._lock``."""

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
        else:
            ordered_ids = list(
                dict.fromkeys(
                    snapshot_id
                    for snapshot_id in self.state_store.published_snapshot_ids()
                    if snapshot_id in snapshots
                )
            )
        keep = set(ordered_ids[: self.keep_snapshots])
        for snapshot_id, path in snapshots.items():
            if snapshot_id not in keep:
                self._remove_snapshot(path)

    @staticmethod
    def _remove_snapshot(path: Path) -> None:
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
            return
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

    def _write_table(self, staging: Path, filename: str, table: TableInput) -> None:
        if (
            Path(filename).name != filename
            or ":" in filename
            or not filename.endswith(".parquet")
        ):
            raise ValueError(f"Snapshot table must be a Parquet filename: {filename}")
        destination = staging / filename
        if isinstance(table, pl.DataFrame):
            table = self._prepare_table(filename, table)
            if table.is_empty() and filename not in _ALLOW_EMPTY_TABLES:
                raise ValueError(f"Snapshot table is empty: {filename}")
            table.write_parquet(destination)
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
            if filename in {
                "financial_facts.parquet",
                "latest_facts.parquet",
                "issuers.parquet",
                "unmapped_metrics.parquet",
            }:
                table_frame = self._prepare_table(filename, table_frame)
                if table_frame.is_empty() and filename not in _ALLOW_EMPTY_TABLES:
                    raise ValueError(f"Snapshot table is empty: {filename}")
                table_frame.write_parquet(destination)
            else:
                self._validate_table_contract(filename, table_frame)
                if table_frame.is_empty() and filename not in _ALLOW_EMPTY_TABLES:
                    raise ValueError(f"Snapshot table is empty: {filename}")
                shutil.copy2(source, destination)

    @staticmethod
    def _prepare_table(filename: str, table: pl.DataFrame) -> pl.DataFrame:
        if filename in {"financial_facts.parquet", "latest_facts.parquet"}:
            return canonicalize_financial_facts(table)
        if filename == "issuers.parquet":
            return canonicalize_table(table, ISSUERS_SCHEMA)
        if filename == "unmapped_metrics.parquet":
            return canonicalize_table(table, UNMAPPED_METRICS_SCHEMA)
        if filename in ANALYTICS_SCHEMAS:
            validate_analytics_table(filename, table)
        return table

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

    @staticmethod
    def _validate_table_contract(filename: str, table: pl.DataFrame) -> None:
        if filename in {"financial_facts.parquet", "latest_facts.parquet"}:
            canonicalize_financial_facts(table)
        elif filename == "issuers.parquet":
            canonicalize_table(table, ISSUERS_SCHEMA)
        elif filename == "unmapped_metrics.parquet":
            canonicalize_table(table, UNMAPPED_METRICS_SCHEMA)
        elif filename in ANALYTICS_SCHEMAS:
            validate_analytics_table(filename, table)

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
