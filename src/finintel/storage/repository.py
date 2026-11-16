"""Stateless DuckDB repository for immutable Parquet datasets."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import cast

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError


class ParquetRepository:
    """Query Parquet files through a fresh in-memory DuckDB connection.

    A repository never opens a persistent DuckDB database.  Each call gets an
    independent in-memory connection, so readers do not contend with a
    publisher replacing a completed snapshot directory.
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
        if selected.is_dir():
            selected = self._discover_facts_file(selected)
        self.path = selected.resolve()

    @classmethod
    def _discover_facts_file(cls, root: Path) -> Path:
        direct = [root / "financial_facts.parquet", root / "latest_facts.parquet"]
        for candidate in direct:
            if candidate.is_file():
                return candidate

        snapshot_root = root / "published" / "snapshots"
        if root.name == "snapshots":
            snapshot_root = root
        elif (
            not snapshot_root.is_dir()
            and (root / "data" / "published" / "snapshots").is_dir()
        ):
            snapshot_root = root / "data" / "published" / "snapshots"
        if snapshot_root.is_dir():
            snapshot_dirs = sorted(
                (
                    path
                    for path in snapshot_root.iterdir()
                    if path.is_dir() and path.name.startswith("snap_")
                ),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
            active_id = cls._active_snapshot_id(snapshot_root)
            if active_id is not None:
                snapshot_dirs.sort(
                    key=lambda path: (
                        path.name != active_id,
                        -path.stat().st_mtime_ns,
                        path.name,
                    ),
                )
            for snapshot in snapshot_dirs:
                for filename in ("financial_facts.parquet", "latest_facts.parquet"):
                    candidate = snapshot / filename
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
    def _active_snapshot_id(snapshot_root: Path) -> str | None:
        data_root = snapshot_root.parent.parent
        state_path = data_root / "metadata" / "state.sqlite"
        if not state_path.is_file():
            return None
        try:
            with closing(sqlite3.connect(state_path)) as connection:
                row = connection.execute(
                    "SELECT snapshot_id FROM active_snapshot WHERE singleton = 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        return str(row[0]) if row is not None else None

    def get_facts_as_of(
        self, as_of_date: date, require_exact_filing_date: bool = False
    ) -> pl.DataFrame:
        """Return the latest fact known at ``as_of_date`` for each business key.

        Exact mode uses only an exact filing publication date.  MVP mode uses
        ``effective_available_at`` so statutory availability delays remain
        respected even where the source did not expose an exact filing date.
        """

        self._require_file()
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
                    query, [str(self.path), as_of_date]
                ).fetch_arrow_table()
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to query financial facts at {self.path}."
            ) from exc
        return cast(pl.DataFrame, pl.from_arrow(result))

    def read_table(self, table_name: str) -> pl.DataFrame:
        """Read one table from a published snapshot by its Parquet filename."""

        if not table_name or Path(table_name).name != table_name:
            raise ValueError("table_name must be a filename without a path component.")
        path = self.path.parent / table_name
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

    def _require_file(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)


Repository = ParquetRepository
DuckDBRepository = ParquetRepository
DuckDBQueryRepository = ParquetRepository
