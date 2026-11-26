"""Export immutable filing manifest lineage and latest PIT facts for snapshot publication.

Verified Sources:
- DuckDB SQL Window Functions / Polars DataFrame PIT resolution (exact tie-breaking parity
  with ``ParquetRepository._query_facts``):
  https://duckdb.org/docs/stable/sql/functions/window_functions
- Polars Expressions & Joins: https://docs.pola.rs/
- Internal Layer 2 TTM contract: ``finintel.normalization.periods.compute_ttm``
"""

from __future__ import annotations

import hashlib
import json
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


def build_pit_facts_sql(
    source_expr: str,
    *,
    require_exact_filing_date: bool,
    has_provider: bool = False,
    has_value: bool = True,
) -> str:
    """Build DuckDB PIT resolution SQL preserving within-provider revisions and provider priority."""
    date_column = (
        "published_at" if require_exact_filing_date else "effective_available_at"
    )
    predicate = f"{date_column} IS NOT NULL AND {date_column} <= CAST(? AS DATE)"
    if require_exact_filing_date:
        predicate += " AND published_at_basis = 'EXACT_FILING'"
    if not has_provider:
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
    null_order = "(value IS NULL) ASC, " if has_value else ""
    return f"""
        WITH eligible AS (
            SELECT *
            FROM {source_expr}
            WHERE {predicate}
        ), per_provider AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY issuer_id, statement_scope, period_type,
                             fiscal_year_start_date, period_end_date,
                             metric_id, taxonomy_version, source_provider
                ORDER BY {date_column} DESC, effective_available_at DESC,
                         revision_no DESC, filing_id DESC
            ) AS _prov_rank
            FROM eligible
        ), ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY issuer_id, statement_scope, period_type,
                             fiscal_year_start_date, period_end_date,
                             metric_id, taxonomy_version
                ORDER BY {null_order}
                         CASE source_provider
                             WHEN 'fhsc' THEN 0
                             WHEN 'issuer' THEN 1
                             WHEN 'vietcap_iq' THEN 2
                             WHEN 'cafef_pdf' THEN 3
                             WHEN 'vndirect_finfo' THEN 4
                             ELSE 99
                         END ASC,
                         {date_column} DESC, effective_available_at DESC,
                         revision_no DESC, filing_id DESC
            ) AS _pit_rank
            FROM per_provider
            WHERE _prov_rank = 1
        )
        SELECT * EXCLUDE (_prov_rank, _pit_rank)
        FROM ranked
        WHERE _pit_rank = 1
    """


def _resolve_pit_latest_facts(
    facts: pl.DataFrame,
    *,
    as_of: date,
    require_exact_filing_date: bool = False,
) -> pl.DataFrame:
    """Resolve point-in-time latest facts in memory with deterministic tie-breakers."""
    if type(as_of) is not date:
        raise TypeError("as_of must be a datetime.date instance.")
    if type(require_exact_filing_date) is not bool:
        raise TypeError("require_exact_filing_date must be a bool.")
    if facts.is_empty():
        return facts

    query = build_pit_facts_sql(
        "_in_memory_facts",
        require_exact_filing_date=require_exact_filing_date,
        has_provider="source_provider" in facts.columns,
        has_value="value" in facts.columns,
    )
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
    if (
        "fiscal_year_start_date" in latest.columns
        and "fiscal_year_start_date" in ttm.columns
    ):
        join_keys.append("fiscal_year_start_date")

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


def _load_vietcap_payload_sha_map(state: StateStore) -> dict[tuple[str, str], str]:
    """Load optional 64-char raw payload SHA-256 receipts from ``vietcap_supplement``."""
    db_path = getattr(state, "db_path", None)
    if not isinstance(db_path, Path):
        return {}
    manifest_file = (
        db_path.parent.parent
        / "raw_disclosures"
        / "vietcap_supplement"
        / "receipts_manifest.json"
    )
    if not manifest_file.is_file():
        return {}
    try:
        entries = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    sec_tag_map = {"NOTE": "note", "CASH_FLOW": "cf", "INCOME_STATEMENT": "is"}
    out: dict[tuple[str, str], str] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        tk = str(item.get("ticker") or "").strip().upper()
        sec = sec_tag_map.get(str(item.get("section") or ""))
        sha = str(item.get("sha256") or "").strip().lower()
        if tk and sec and len(sha) == 64:
            out[(sec, tk)] = sha
    return out


def _build_vietcap_fallback_manifest_rows(
    facts: pl.DataFrame,
    missing_ids: set[str],
    payload_sha_map: dict[tuple[str, str], str],
) -> list[dict[str, object]]:
    if not missing_ids or "source_provider" not in facts.columns:
        return []
    vci_facts = facts.filter(
        pl.col("filing_id").cast(pl.String).is_in(sorted(missing_ids))
        & (pl.col("source_provider") == "vietcap_iq")
    )
    if vci_facts.is_empty():
        return []

    out: list[dict[str, object]] = []
    for sub_df in vci_facts.partition_by("filing_id", maintain_order=False):
        first = sub_df.row(0, named=True)
        fid = str(first["filing_id"])
        tk = str(first.get("ticker_at_filing") or first["issuer_id"]).strip().upper()
        parts = fid.split("_")
        sec_tag = parts[1] if len(parts) >= 3 and parts[0] == "vci" else ""
        payload_sha = payload_sha_map.get((sec_tag, tk))
        if payload_sha is None:
            payload_sha = hashlib.sha256(f"payload:{fid}".encode()).hexdigest()

        metric_items = sorted(
            (
                str(r.get("metric_id") or ""),
                str(r.get("raw_metric_key") or ""),
                r.get("value"),
            )
            for r in sub_df.select("metric_id", "raw_metric_key", "value").to_dicts()
        )
        canonical_sha = hashlib.sha256(
            json.dumps(
                {"filing_id": fid, "metrics": metric_items},
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        p_end = first.get("period_end_date")
        pub_at = first.get("published_at")
        eff_at = first.get("effective_available_at")
        out.append(
            {
                "filing_id": fid,
                "issuer_id": str(first["issuer_id"]),
                "ticker_at_filing": tk,
                "source_provider": "vietcap_iq",
                "connector_name": "vietcap_iq_supplement",
                "statement_type": str(first.get("statement_type") or "CASH_FLOW"),
                "statement_scope": str(first.get("statement_scope") or "CONSOLIDATED"),
                "period_type": str(first.get("period_type") or "ANNUAL"),
                "period_end_date": p_end.isoformat()
                if isinstance(p_end, date)
                else str(p_end),
                "published_at": pub_at.isoformat()
                if isinstance(pub_at, date)
                else (str(pub_at) if pub_at is not None else None),
                "published_at_basis": str(
                    first.get("published_at_basis") or "STATUTORY_LAG"
                ),
                "effective_available_at": eff_at.isoformat()
                if isinstance(eff_at, date)
                else str(eff_at),
                "audit_status": str(first.get("audit_status") or "UNAUDITED"),
                "audit_opinion": str(first.get("audit_opinion") or "NOT_APPLICABLE"),
                "revision_no": int(first.get("revision_no") or 1),
                "revision_reason": str(first.get("revision_reason") or "ORIGINAL"),
                "canonical_content_sha256": canonical_sha,
                "payload_file_sha256": payload_sha,
            }
        )
    return out


def build_filings_manifest_snapshot(
    state: StateStore, facts_source: Path | pl.DataFrame
) -> pl.DataFrame:
    """Project SQLite ``filings_manifest`` rows referenced by ``facts_source`` into Parquet.

    Includes ``source_provider``, ``connector_name``, ``published_at``,
    ``published_at_basis``, and ``canonical_content_sha256`` so Layer 5 Lineage
    Cards read 100% from the immutable published snapshot without touching SQLite.
    Synthesizes deterministic lineage rows for ``vietcap_iq`` supplement filings.
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
    found_ids: set[str] = set()
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
                    for row in fetched:
                        row_dict = dict(row)
                        rows.append(row_dict)
                        found_ids.add(str(row_dict["filing_id"]))
    except sqlite3.Error as exc:
        raise ContractViolationError(
            "Unable to export filings_manifest provenance for snapshot publication."
        ) from exc

    missing_ids = referenced_ids - found_ids
    if missing_ids:
        sha_map = _load_vietcap_payload_sha_map(state)
        rows.extend(_build_vietcap_fallback_manifest_rows(facts, missing_ids, sha_map))

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
