"""Stateless DuckDB repository for immutable Parquet datasets."""

from __future__ import annotations

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

    @property
    def path(self) -> Path:
        """Return the facts path selected at the time of access."""
        with self._read_path() as path:
            return path.resolve()

    @contextmanager
    def _read_path(self) -> Iterator[Path]:
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
        with snapshot_lock(lock_root) if lock_root is not None else nullcontext():
            yield (
                self._discover_facts_file(source, snapshots)
                if self._is_directory
                else source
            )

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
        return next((root.resolve() for root in candidates if root.is_dir()), None)

    @classmethod
    def _discover_facts_file(cls, root: Path, snapshots: Path | None) -> Path:
        direct = [root / "financial_facts.parquet"]
        if root.name.startswith("snap_"):
            direct.append(root / "latest_facts.parquet")
        for candidate in direct:
            if candidate.is_file():
                return candidate

        if snapshots is not None:
            for snapshot in cls._snapshot_candidates(snapshots):
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
            raise ContractViolationError(
                "Multiple curated batches found; provide the active snapshot or an explicit file."
            )
        return root / "financial_facts.parquet"

    @staticmethod
    def _snapshot_candidates(snapshot_root: Path) -> list[Path]:
        data_root = snapshot_root.parent.parent
        state_path = data_root / "metadata" / "state.sqlite"
        try:
            state_path.stat()
        except FileNotFoundError:
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
                sqlite3.connect(f"{state_path.as_uri()}?mode=ro", uri=True)
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
        if not snapshot_id.startswith("snap_") or Path(snapshot_id).name != snapshot_id:
            raise ContractViolationError(f"Invalid active snapshot ID in {state_path}.")
        return [snapshot_root / snapshot_id]

    def get_facts_as_of(
        self, as_of_date: date, require_exact_filing_date: bool = False
    ) -> pl.DataFrame:
        """Return the latest fact known at ``as_of_date`` for each business key.

        Exact mode uses only an exact filing publication date.  MVP mode uses
        ``effective_available_at`` so statutory availability delays remain
        respected even where the source did not expose an exact filing date.
        """

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
                                 period_end_date, metric_id
                    ORDER BY effective_available_at DESC, revision_no DESC
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

        if not table_name or Path(table_name).name != table_name:
            raise ValueError("table_name must be a filename without a path component.")
        with self._read_path() as facts_path:
            path = facts_path.parent / table_name
            if not path.exists():
                raise FileNotFoundError(path)
            with duckdb.connect(":memory:") as connection:
                arrow = connection.execute(
                    "SELECT * FROM read_parquet(?)", [str(path)]
                ).fetch_arrow_table()
            return cast(pl.DataFrame, pl.from_arrow(arrow))

    def get_latest_facts(self) -> pl.DataFrame:
        """Read the latest facts table from the repository's parent directory."""

        return self.read_table("latest_facts.parquet")


Repository = ParquetRepository
DuckDBRepository = ParquetRepository
DuckDBQueryRepository = ParquetRepository
