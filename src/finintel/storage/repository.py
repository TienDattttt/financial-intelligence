"""Stateless DuckDB repository for immutable Parquet datasets."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from datetime import date
from pathlib import Path
from typing import cast

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.storage.snapshot_publisher import snapshot_lock


class ParquetRepository:
    """Query Parquet files through a fresh in-memory DuckDB connection.

    Each call opens an independent in-memory connection. Snapshot reads hold
    the publication lock until DuckDB has finished reading the selected file.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        financial_facts_path: Path | None = None,
        data_root: Path | None = None,
        _lock_held: bool = False,
    ) -> None:
        selected = financial_facts_path or path or data_root
        if selected is None:
            raise ValueError("Provide a Parquet path or data_root.")
        self._source = selected.resolve()
        self._is_directory = (
            self._source.is_dir()
            or (financial_facts_path is None and path is None)
            or self._source.name == "snapshots"
        )
        self._lock_held = _lock_held

    @property
    def path(self) -> Path:
        """Return the facts path selected at the time of access."""
        with self._read_path() as path:
            return path.resolve()

    @property
    def snapshot_id(self) -> str | None:
        """Return the pinned or currently active snapshot ID, if available."""
        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            return snapshot.name
        if not self._is_directory:
            return None
        snapshots = self._find_snapshot_root(source)
        if snapshots is None:
            return None
        lock_ctx = nullcontext() if self._lock_held else snapshot_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            return candidates[0].name if candidates else None

    def pin_active_snapshot(self) -> ParquetRepository:
        """Return a repository bound to the active snapshot directory at call time."""
        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            if not snapshot.is_dir():
                raise FileNotFoundError(snapshot)
            return ParquetRepository(snapshot, _lock_held=self._lock_held)
        snapshots = self._find_snapshot_root(source) if self._is_directory else None
        if snapshots is None:
            raise FileNotFoundError(source / "published" / "snapshots")
        lock_ctx = nullcontext() if self._lock_held else snapshot_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            if not candidates:
                raise FileNotFoundError(snapshots)
            return ParquetRepository(candidates[0], _lock_held=self._lock_held)

    @contextmanager
    def snapshot_session(self) -> Iterator[ParquetRepository]:
        """Hold the snapshot read lock across multiple table reads on one snapshot."""
        source = self._source
        snapshot = source if self._is_directory else source.parent
        if snapshot.name.startswith("snap_"):
            lock_ctx = (
                nullcontext() if self._lock_held else snapshot_lock(snapshot.parent)
            )
            with lock_ctx:
                if not snapshot.is_dir():
                    raise FileNotFoundError(snapshot)
                yield ParquetRepository(snapshot, _lock_held=True)
            return
        snapshots = self._find_snapshot_root(source) if self._is_directory else None
        if snapshots is None:
            raise FileNotFoundError(source / "published" / "snapshots")
        lock_ctx = nullcontext() if self._lock_held else snapshot_lock(snapshots)
        with lock_ctx:
            candidates = self._snapshot_candidates(snapshots)
            if not candidates:
                raise FileNotFoundError(snapshots)
            yield ParquetRepository(candidates[0], _lock_held=True)

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
            else snapshot_lock(lock_root)
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

        try:
            with closing(
                sqlite3.connect(f"{state_path.as_uri()}?mode=ro", uri=True, timeout=5)
            ) as connection:
                row = connection.execute(
                    "SELECT snapshot_id FROM active_snapshot WHERE singleton = 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractViolationError(
                f"Unable to read active snapshot from {state_path}."
            ) from exc
        if row is None:
            return []
        snapshot_id = str(row[0])
        if (
            not snapshot_id.startswith("snap_")
            or Path(snapshot_id).name != snapshot_id
            or ":" in snapshot_id
        ):
            raise ContractViolationError(f"Invalid active snapshot ID in {state_path}.")
        active = snapshot_root / snapshot_id
        if not active.is_dir():
            raise ContractViolationError(
                f"Active snapshot directory is missing: {active}."
            )
        return [active]

    def get_facts_as_of(
        self, as_of_date: date, require_exact_filing_date: bool = False
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
        with self._read_path() as path:
            if not path.is_file():
                raise FileNotFoundError(path)
            return self._query_facts(path, as_of_date, require_exact_filing_date)

    def _query_facts(
        self, path: Path, as_of_date: date, require_exact_filing_date: bool
    ) -> pl.DataFrame:
        date_column = (
            "published_at" if require_exact_filing_date else "effective_available_at"
        )
        predicate = f"{date_column} IS NOT NULL AND {date_column} <= CAST(? AS DATE)"
        if require_exact_filing_date:
            predicate += " AND published_at_basis = 'EXACT_FILING'"
        query = f"""
            WITH eligible AS (
                SELECT *
                FROM read_parquet(?)
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

    def read_table(self, table_name: str) -> pl.DataFrame:
        """Read one table from a published snapshot by its Parquet filename."""

        if (
            not table_name
            or Path(table_name).name != table_name
            or ":" in table_name
            or not table_name.endswith(".parquet")
        ):
            raise ValueError(
                "table_name must be a Parquet filename without a path component."
            )
        with self._read_path(table_name) as path:
            if not path.is_file():
                raise FileNotFoundError(path)
            try:
                with duckdb.connect(":memory:") as connection:
                    arrow = connection.execute(
                        "SELECT * FROM read_parquet(?)", [str(path)]
                    ).fetch_arrow_table()
            except (duckdb.Error, OSError) as exc:
                raise ContractViolationError(
                    f"Unable to query snapshot table at {path}."
                ) from exc
            return cast(pl.DataFrame, pl.from_arrow(arrow))

    def get_latest_facts(self) -> pl.DataFrame:
        """Read the latest facts table from the selected snapshot."""

        return self.read_table("latest_facts.parquet")


Repository = ParquetRepository
DuckDBRepository = ParquetRepository
DuckDBQueryRepository = ParquetRepository
