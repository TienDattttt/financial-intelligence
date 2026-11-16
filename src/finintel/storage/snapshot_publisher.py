"""Atomic immutable snapshot publication and bounded garbage collection."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

import polars as pl
from filelock import FileLock

from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import canonicalize_financial_facts

TableInput: TypeAlias = pl.DataFrame | Path


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
        self._lock = FileLock(str(self.root / ".publish.lock"))

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
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._remove_staging_directories()
            staging = self.root / f"_tmp_{snapshot_id}"
            destination = self.root / snapshot_id
            if destination.exists():
                raise FileExistsError(destination)
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
                os.replace(staging, destination)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            if self.state_store is not None and run_id is not None:
                try:
                    self.state_store.publish_snapshot(run_id, snapshot_id)
                except BaseException:
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
            self.collect_garbage()
            return destination

    publish_snapshot = publish

    def collect_garbage(self) -> None:
        """Delete abandoned staging directories and all but newest snapshots."""

        if not self.root.exists():
            return
        self._remove_staging_directories()
        snapshots = [
            path
            for path in self.root.iterdir()
            if path.is_dir() and path.name.startswith("snap_")
        ]
        snapshots.sort(
            key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True
        )
        for old in snapshots[self.keep_snapshots :]:
            try:
                shutil.rmtree(old)
            except OSError:
                # A dashboard reader may still hold a Windows file handle. The
                # next successful publication retries this best-effort cleanup.
                continue

    garbage_collect = collect_garbage

    def _write_table(self, staging: Path, filename: str, table: TableInput) -> None:
        if Path(filename).name != filename or not filename.endswith(".parquet"):
            raise ValueError(f"Snapshot table must be a Parquet filename: {filename}")
        destination = staging / filename
        if isinstance(table, pl.DataFrame):
            if filename == "financial_facts.parquet":
                table = canonicalize_financial_facts(table)
            table.write_parquet(destination)
        else:
            source = table.resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, destination)

    def _verify(self, staging: Path, tables: Mapping[str, TableInput]) -> None:
        for filename in tables:
            path = staging / filename
            if not path.is_file():
                raise OSError(f"Snapshot table was not written: {filename}")
            if pl.scan_parquet(path).select(pl.len()).collect().item() <= 0:
                raise ValueError(f"Snapshot table is empty: {filename}")
        if not (staging / "_snapshot_meta.json").is_file():
            raise OSError("Snapshot metadata was not written.")

    def _remove_staging_directories(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if path.is_dir() and path.name.startswith("_tmp_"):
                shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> None:
        if not snapshot_id.startswith("snap_") or Path(snapshot_id).name != snapshot_id:
            raise ValueError(
                "snapshot_id must be a path-safe value beginning with 'snap_'."
            )
