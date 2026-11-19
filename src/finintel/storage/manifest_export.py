"""Export immutable filing manifest lineage and latest PIT facts for snapshot publication.

Verified Sources:
- DuckDB SQL Window Functions / Polars DataFrame PIT resolution (exact tie-breaking parity
  with ``ParquetRepository._query_facts``):
  https://duckdb.org/docs/stable/sql/functions/window_functions
- Polars Expressions & Joins: https://docs.pola.rs/
- Internal Layer 2 TTM contract: ``finintel.normalization.periods.compute_ttm``
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.periods import compute_ttm
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import (
    FILINGS_MANIFEST_KEY,
    FILINGS_MANIFEST_SCHEMA,
)


def _load_facts_frame(facts_source: Path | pl.DataFrame) -> pl.DataFrame:
    if isinstance(facts_source, pl.DataFrame):
        return facts_source
    try:
        return pl.read_parquet(facts_source)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ContractViolationError(
            "Unable to read financial_facts for snapshot export."
        ) from exc


def _resolve_pit_latest_facts(
    facts: pl.DataFrame,
    *,
    as_of: date,
    require_exact_filing_date: bool = False,
) -> pl.DataFrame:
    """Resolve point-in-time latest facts in memory with deterministic tie-breakers.

    Matches ``ParquetRepository._query_facts`` window ranking exactly:
    ``PARTITION BY issuer_id, statement_scope, period_type, fiscal_year_start_date,
    period_end_date, metric_id, taxonomy_version`` ordered by
    ``{date_column} DESC, effective_available_at DESC, revision_no DESC, filing_id DESC``.
    """
    if type(as_of) is not date:
        raise TypeError("as_of must be a datetime.date instance.")
    if type(require_exact_filing_date) is not bool:
        raise TypeError("require_exact_filing_date must be a bool.")
    if facts.is_empty():
        return facts

    date_column = (
        "published_at" if require_exact_filing_date else "effective_available_at"
    )
    predicate = f"{date_column} IS NOT NULL AND {date_column} <= CAST(? AS DATE)"
    if require_exact_filing_date:
        predicate += " AND published_at_basis = 'EXACT_FILING'"
    query = f"""
        WITH eligible AS (
            SELECT *
            FROM _in_memory_facts
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
            connection.register("_in_memory_facts", facts)
            arrow = connection.execute(query, [as_of]).fetch_arrow_table()
    except duckdb.Error as exc:
        raise ContractViolationError(
            "Unable to resolve point-in-time latest facts in memory."
        ) from exc
    return cast(pl.DataFrame, pl.from_arrow(arrow))


def build_latest_facts_table(
    facts_source: Path | pl.DataFrame, *, as_of: date
) -> pl.DataFrame:
    """Resolve point-in-time latest facts and attach Layer 2 TTM values for Serving.

    Accepts either an already-loaded ``pl.DataFrame`` (zero extra disk I/O) or a ``Path``.
    Includes ``source_provider`` in TTM join keys so multi-source rows never collide,
    while preserving ``comparable_across_taxonomy``, ``source_filing_ids``, and
    ``is_filed_late`` intact.
    """
    full_facts = _load_facts_frame(facts_source)
    latest = _resolve_pit_latest_facts(full_facts, as_of=as_of)
    if latest.is_empty():
        return latest

    required_for_ttm = {
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
        "filing_id",
        "effective_available_at",
        "published_at_basis",
        "audit_status",
        "derivation_method",
        "value",
    }
    if required_for_ttm - set(full_facts.columns):
        return latest

    enriched_input = full_facts
    if "value_type" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            pl.when(pl.col("metric_id").str.starts_with("BS_"))
            .then(pl.lit("STOCK"))
            .when(pl.col("metric_id") == "IS_EPS_BASIC_REPORTED")
            .then(pl.lit("NON_ADDITIVE"))
            .otherwise(pl.lit("FLOW"))
            .alias("value_type")
        )
    if "period_days" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            (
                (
                    pl.col("period_end_date") - pl.col("period_start_date")
                ).dt.total_days()
                + 1
            )
            .cast(pl.Int32)
            .alias("period_days")
        )
    if "source_provider" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            pl.lit("normalized").alias("source_provider")
        )
    if "source_filing_ids" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            pl.concat_list("filing_id").alias("source_filing_ids")
        )
    if "comparable_across_taxonomy" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            pl.lit(False).alias("comparable_across_taxonomy")
        )
    if "is_filed_late" not in enriched_input.columns:
        enriched_input = enriched_input.with_columns(
            pl.lit(None, dtype=pl.Boolean).alias("is_filed_late")
        )

    projected = enriched_input.select(
        pl.col(name).cast(dtype, strict=False)
        if name in enriched_input.columns
        else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in FACT_SCHEMA.items()
    )
    ttm = compute_ttm(projected, as_of=as_of)
    if ttm.is_empty():
        return latest

    join_keys = [
        "issuer_id",
        "statement_scope",
        "period_type",
        "period_end_date",
        "metric_id",
        "taxonomy_version",
    ]
    if "source_provider" in latest.columns and "source_provider" in ttm.columns:
        join_keys.append("source_provider")

    ttm_slice = ttm.select(
        *join_keys,
        pl.col("ttm_value").alias("_computed_ttm_value"),
        pl.col("computation_note").alias("_computed_note"),
    ).unique(subset=join_keys, keep="last")
    ttm_expr = (
        pl.coalesce("ttm_value", "_computed_ttm_value")
        if "ttm_value" in latest.columns
        else pl.col("_computed_ttm_value")
    ).alias("ttm_value")
    note_expr = (
        pl.coalesce("computation_note", "_computed_note")
        if "computation_note" in latest.columns
        else pl.col("_computed_note")
    ).alias("computation_note")
    return (
        latest.join(ttm_slice, on=join_keys, how="left")
        .with_columns(ttm_expr, note_expr)
        .drop("_computed_ttm_value", "_computed_note")
    )


def build_filings_manifest_snapshot(
    state: StateStore, facts_source: Path | pl.DataFrame
) -> pl.DataFrame:
    """Project SQLite ``filings_manifest`` rows referenced by ``facts_source`` into Parquet.

    Includes ``source_provider``, ``connector_name``, ``published_at``,
    ``published_at_basis``, and ``canonical_content_sha256`` so Layer 5 Lineage
    Cards read 100% from the immutable published snapshot without touching SQLite.
    """
    facts = _load_facts_frame(facts_source)
    if facts.is_empty():
        return pl.DataFrame(schema=FILINGS_MANIFEST_SCHEMA)

    referenced_ids: set[str] = set(
        facts["filing_id"].drop_nulls().cast(pl.String).to_list()
    )
    if "source_filing_ids" in facts.columns:
        referenced_ids.update(
            facts.select(
                pl.col("source_filing_ids").explode().drop_nulls().cast(pl.String)
            )["source_filing_ids"].to_list()
        )
    if not referenced_ids:
        return pl.DataFrame(schema=FILINGS_MANIFEST_SCHEMA)

    rows: list[dict[str, object]] = []
    try:
        with state.connection() as conn:
            table_exists = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is not None
            )
            if table_exists:
                sorted_ids = sorted(referenced_ids)
                # Chunk parameters to stay comfortably below SQLite's variable limit.
                chunk_size = 500
                for start in range(0, len(sorted_ids), chunk_size):
                    chunk = sorted_ids[start : start + chunk_size]
                    placeholders = ", ".join("?" for _ in chunk)
                    fetched = conn.execute(
                        f"""
                        SELECT filing_id, issuer_id, ticker_at_filing, source_provider,
                               connector_name, statement_type, statement_scope, period_type,
                               period_end_date, published_at, published_at_basis,
                               effective_available_at, audit_status, audit_opinion,
                               revision_no, revision_reason, canonical_content_sha256,
                               payload_file_sha256
                        FROM filings_manifest
                        WHERE filing_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                    rows.extend(dict(row) for row in fetched)
    except sqlite3.Error as exc:
        raise ContractViolationError(
            "Unable to export filings_manifest provenance for snapshot publication."
        ) from exc

    if not rows:
        return pl.DataFrame(schema=FILINGS_MANIFEST_SCHEMA)

    date_cols = ("period_end_date", "published_at", "effective_available_at")
    raw_df = pl.DataFrame(rows)
    cast_exprs = [
        (
            pl.col(name).cast(pl.String).str.to_date("%Y-%m-%d", strict=False)
            if name in date_cols
            else pl.col(name).cast(dtype, strict=True)
        ).alias(name)
        for name, dtype in FILINGS_MANIFEST_SCHEMA.items()
    ]
    return (
        raw_df.select(cast_exprs)
        .unique(subset=list(FILINGS_MANIFEST_KEY), keep="last")
        .sort("filing_id")
    )
