"""Replay immutable Layer 1 filing revisions through the canonical mapper."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import FilingMetadata
from finintel.ingestion.file_adapter import FileAdapter
from finintel.ingestion.hasher import canonical_content_sha256, payload_file_sha256
from finintel.normalization.mapper import FACT_SCHEMA, UNMAPPED_SCHEMA, TaxonomyMapper
from finintel.normalization.restatement import map_restatement
from finintel.pipeline.state_store import StateStore


def _fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolationError("Expected a mapping in normalization inputs.")
    return dict(value)


def _text(fields: Mapping[str, object], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"Normalization requires {key}.")
    return value


def _integer(fields: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = fields.get(key, default)
    if type(value) is not int:
        raise ContractViolationError(f"Normalization requires integer {key}.")
    return value


def _decode(content: str | bytes) -> object:
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        raise ContractViolationError("Malformed JSON in the raw archive.") from None


def _fhsc_record(
    content: bytes,
    locator: str,
    metadata: FilingMetadata,
) -> dict[str, object]:
    match = re.fullmatch(r"/data/(0|[1-9][0-9]{0,5})", locator)
    body = _fields(_decode(content))
    records = body.get("data")
    if (
        match is None
        or body.get("error_code") != "0"
        or not isinstance(records, list)
        or int(match[1]) >= len(records)
    ):
        raise ContractViolationError("Invalid FHSC raw record locator or payload.")
    record = _fields(records[int(match[1])])
    year, quarter = record.pop("year", None), record.pop("quarter", None)
    if (
        type(year) is not int
        or year != metadata.fiscal_year
        or (quarter is not None and type(quarter) is not int)
        or quarter != metadata.fiscal_quarter
    ):
        raise ContractViolationError("FHSC raw period differs from its manifest entry.")
    return record


def _file_metrics(
    content: bytes,
    path: Path,
    metadata: FilingMetadata,
    source: str,
    attrs: Mapping[str, object],
    value_column: str,
) -> dict[str, object]:
    sheet = _text(attrs, "sheet") if attrs.get("sheet") is not None else None
    return FileAdapter(
        path,
        metadata,
        source_provider=source,
        metric_column=_text(attrs, "metric_column"),
        value_column=value_column,
        sheet=sheet,
        header_row=_integer(attrs, "header_row"),
    ).read_metrics(content)


def _archived_filing(
    row: Mapping[str, object],
    content: bytes,
    path: Path,
    settings: Mapping[str, object],
    mapper: TaxonomyMapper,
) -> list[tuple[pl.DataFrame, pl.DataFrame]]:
    metadata = FilingMetadata.from_mapping(row)
    filing_id, source = _text(row, "filing_id"), _text(row, "source_provider")
    comparison = settings.get("comparative")
    unit_multiplier = _integer(
        settings, "unit_multiplier", 1 if source == "fhsc" else None
    )
    cash_flow_method = (
        _text(settings, "cash_flow_method") if "cash_flow_method" in settings else None
    )
    attrs: dict[str, object] = {}
    if source == "fhsc":
        if unit_multiplier != 1 or comparison is not None:
            raise ContractViolationError(
                "FHSC archive records use VND and a single verified period."
            )
        metrics = _fhsc_record(content, _text(row, "raw_record_locator"), metadata)
    elif row.get("connector_name") == "file_adapter_v1":
        attrs = _fields(_decode(_text(row, "provider_attrs_json")))
        metrics = _file_metrics(
            content, path, metadata, source, attrs, _text(attrs, "value_column")
        )
    else:
        raise ContractViolationError("Unsupported raw filing connector.")
    if canonical_content_sha256(
        metrics, metadata.statement_scope, metadata.period_end_date
    ) != row.get("canonical_content_sha256"):
        raise ContractViolationError(
            "Raw metrics differ from the filing manifest hash."
        )
    revision_no = _integer(row, "revision_no")
    result = [
        mapper.map_metrics(
            metrics,
            metadata,
            filing_id=filing_id,
            source_provider=source,
            revision_no=revision_no,
            unit_multiplier=unit_multiplier,
            cash_flow_method=cash_flow_method,
        )
    ]
    if comparison is not None:
        fields = _fields(comparison)
        value_column = _text(fields, "value_column")
        if value_column == attrs.get("value_column"):
            raise ContractViolationError(
                "Comparative amounts require a separate source column."
            )
        comparative_period = FilingMetadata.from_mapping(
            {
                "issuer_id": metadata.issuer_id,
                "ticker_at_filing": metadata.ticker_at_filing,
                "statement_type": metadata.statement_type,
                "statement_scope": metadata.statement_scope,
                **{
                    key: fields.get(key)
                    for key in (
                        "fiscal_year",
                        "fiscal_quarter",
                        "fiscal_year_start_date",
                        "period_type",
                        "period_start_date",
                        "period_end_date",
                    )
                },
            }
        )
        metrics = _file_metrics(content, path, metadata, source, attrs, value_column)
        result.append(
            map_restatement(
                mapper,
                metrics,
                metadata,
                comparative_period,
                filing_id=filing_id,
                source_provider=source,
                revision_no=revision_no,
                unit_multiplier=unit_multiplier,
                cash_flow_method=cash_flow_method,
            )
        )
    return result


def normalize_archive(
    state: StateStore,
    raw_root: Path,
    mapper: TaxonomyMapper,
    *,
    source: str,
    tickers: tuple[str, ...] = (),
    filing_settings: Mapping[str, object] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return all mapped filing revisions and unmapped metrics for the selected issuers."""
    if source not in {"fhsc", "file"}:
        raise ContractViolationError("Unsupported normalization source.")
    conditions = [
        "source_provider = 'fhsc'"
        if source == "fhsc"
        else "connector_name = 'file_adapter_v1'"
    ]
    if tickers:
        placeholders = ", ".join("?" for _ in tickers)
        conditions.append(f"ticker_at_filing IN ({placeholders})")
    try:
        with state.connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is None
            ):
                raise ContractViolationError(
                    "No filing manifest is available for normalization."
                )
            rows = conn.execute(
                f"SELECT * FROM filings_manifest WHERE {' AND '.join(conditions)} "
                "ORDER BY issuer_id, statement_type, period_end_date, revision_no, filing_id",
                tickers,
            ).fetchall()
    except sqlite3.Error:
        raise ContractViolationError(
            "Unable to read the raw filing manifest."
        ) from None
    root = raw_root.resolve()

    @lru_cache(maxsize=16)
    def read_payload(relative: str, digest: str) -> tuple[Path, bytes]:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ContractViolationError("Raw filing path is outside its archive.")
        try:
            content = path.read_bytes()
        except OSError:
            raise ContractViolationError(
                "Raw filing is missing or unreadable."
            ) from None
        if payload_file_sha256(content) != digest:
            raise ContractViolationError(
                "Raw filing bytes differ from the manifest hash."
            )
        return path, content

    facts, unmapped = (
        [pl.DataFrame(schema=FACT_SCHEMA)],
        [pl.DataFrame(schema=UNMAPPED_SCHEMA)],
    )
    configured = filing_settings or {}
    for entry in rows:
        row = dict(entry)
        path, content = read_payload(
            _text(row, "raw_archive_path"), _text(row, "payload_file_sha256")
        )
        settings = _fields(configured.get(_text(row, "filing_id"), {}))
        for mapped, unknown in _archived_filing(row, content, path, settings, mapper):
            facts.append(mapped)
            unmapped.append(unknown)
    return pl.concat(facts), pl.concat(unmapped)
