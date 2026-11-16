"""Store immutable payloads and serialize version allocation in SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from uuid import uuid4

from filelock import FileLock

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import (
    AuditOpinion,
    AuditStatus,
    FilingMetadata,
    RawFiling,
    RawFilingBundle,
    RevisionReason,
)
from finintel.ingestion.hasher import canonical_content_sha256, payload_file_sha256
from finintel.pipeline.state_store import StateStore

MANIFEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings_manifest (
    filing_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    ticker_at_filing TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    connector_name TEXT NOT NULL,
    payload_nature TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    statement_scope TEXT NOT NULL,
    period_type TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER,
    fiscal_year_start_date TEXT NOT NULL,
    period_start_date TEXT NOT NULL,
    period_end_date TEXT NOT NULL,
    published_at TEXT,
    published_at_basis TEXT NOT NULL,
    effective_available_at TEXT NOT NULL,
    audit_status TEXT NOT NULL,
    audit_opinion TEXT NOT NULL,
    is_filed_late INTEGER,
    revision_no INTEGER NOT NULL DEFAULT 1,
    revision_reason TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_file_sha256 TEXT NOT NULL,
    canonical_content_sha256 TEXT NOT NULL,
    raw_archive_path TEXT NOT NULL,
    raw_record_locator TEXT,
    provider_attrs_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS filings_manifest_by_revision
ON filings_manifest (
    source_provider, issuer_id, statement_type, statement_scope, period_type,
    period_end_date, revision_no
);
"""

LATEST = """
SELECT filing_id, revision_no, canonical_content_sha256,
       fiscal_quarter, audit_status, audit_opinion, published_at, fetched_at,
       provider_attrs_json
FROM filings_manifest
WHERE source_provider = :source_provider
  AND issuer_id = :issuer_id
  AND statement_type = :statement_type
  AND statement_scope = :statement_scope
  AND period_type = :period_type
  AND period_end_date = :period_end_date
ORDER BY revision_no DESC LIMIT 1
"""

METADATA_UPDATE = """
UPDATE filings_manifest
SET audit_status = :audit_status,
    audit_opinion = :audit_opinion,
    published_at = :published_at,
    published_at_basis = :published_at_basis,
    effective_available_at = :effective_available_at,
    is_filed_late = :is_filed_late,
    provider_attrs_json = :provider_attrs_json
WHERE filing_id = :filing_id
"""

INSERT = """
INSERT INTO filings_manifest (
    filing_id, issuer_id, ticker_at_filing, source_provider, connector_name, payload_nature,
    statement_type, statement_scope, period_type, fiscal_year, fiscal_quarter,
    fiscal_year_start_date, period_start_date, period_end_date, published_at,
    published_at_basis, effective_available_at, audit_status, audit_opinion,
    is_filed_late, revision_no, revision_reason, fetched_at, payload_file_sha256,
    canonical_content_sha256, raw_archive_path, raw_record_locator, provider_attrs_json
)
VALUES (
    :filing_id, :issuer_id, :ticker_at_filing, :source_provider, :connector_name, :payload_nature,
    :statement_type, :statement_scope, :period_type, :fiscal_year, :fiscal_quarter,
    :fiscal_year_start_date, :period_start_date, :period_end_date, :published_at,
    :published_at_basis, :effective_available_at, :audit_status, :audit_opinion,
    :is_filed_late, :revision_no, :revision_reason, :fetched_at, :payload_file_sha256,
    :canonical_content_sha256, :raw_archive_path, :raw_record_locator, :provider_attrs_json
)
"""


@dataclass(frozen=True)
class ArchiveOutcome:
    filing_id: str
    issuer_id: str
    status: Literal["ARCHIVED", "SKIPPED_UNCHANGED"]


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ContractViolationError("An immutable archive file has changed.")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _provided_evidence(metadata: FilingMetadata) -> dict[str, str]:
    evidence: dict[str, str] = {}
    if metadata.audit_status != AuditStatus.UNKNOWN:
        evidence["audit_status"] = metadata.audit_status
    if metadata.audit_opinion != AuditOpinion.UNKNOWN:
        evidence["audit_opinion"] = metadata.audit_opinion
    if metadata.published_at is not None:
        evidence["published_at"] = metadata.published_at.isoformat()
    return evidence


def _refresh_metadata(
    conn: sqlite3.Connection,
    previous: sqlite3.Row,
    filing: RawFiling,
    receipt_sha256: str,
    fetched_at: str,
) -> None:
    # Evidence is field-scoped: each value points to the receipt that supplied it.
    # Replaying an older capture must not roll a newer value back.
    attrs = json.loads(previous["provider_attrs_json"] or "{}")
    evidence = attrs.get("metadata_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    changes: dict[str, str] = {}
    refreshed: dict[str, str] = {}
    incoming = datetime.fromisoformat(fetched_at)
    for name, value in _provided_evidence(filing.metadata).items():
        reference = evidence.get(name)
        prior = reference.get("fetched_at") if isinstance(reference, dict) else None
        if not isinstance(prior, str):
            # Values captured before field-level evidence existed got their
            # timestamp from the revision itself.
            prior = previous["fetched_at"]
        prior_at = datetime.fromisoformat(prior)
        if incoming < prior_at:
            continue
        if value == previous[name]:
            # A newer receipt may confirm an unchanged value; move the field's
            # freshness marker so an older replay still cannot replace it.
            if incoming > prior_at:
                refreshed[name] = value
            continue
        changes[name] = value
    if not changes and not refreshed:
        return

    publication = changes.get("published_at", previous["published_at"])
    merged = replace(
        filing.metadata,
        fiscal_quarter=previous["fiscal_quarter"],
        audit_status=AuditStatus(changes.get("audit_status", previous["audit_status"])),
        audit_opinion=AuditOpinion(
            changes.get("audit_opinion", previous["audit_opinion"])
        ),
        published_at=date.fromisoformat(publication)
        if publication is not None
        else None,
    )
    # Keep the original raw payload and retrieval time for this revision.
    # Each changed field points to the receipt that supplied its current value.
    attrs["metadata_evidence"] = evidence
    for name in [*changes, *refreshed]:
        evidence[name] = {
            "receipt_sha256": receipt_sha256,
            "raw_record_locator": filing.locator,
            "fetched_at": fetched_at,
        }
    conn.execute(
        METADATA_UPDATE,
        {
            "filing_id": previous["filing_id"],
            "audit_status": merged.audit_status,
            "audit_opinion": merged.audit_opinion,
            "published_at": publication,
            "published_at_basis": merged.published_at_basis,
            "effective_available_at": merged.effective_available_at.isoformat(),
            "is_filed_late": merged.is_filed_late,
            "provider_attrs_json": json.dumps(
                attrs, ensure_ascii=False, allow_nan=False
            ),
        },
    )


class RawArchive:
    def __init__(self, root: Path, state: StateStore) -> None:
        self.root = root.resolve()
        self.state = state
        self.state.path.parent.mkdir(parents=True, exist_ok=True)
        with self.state.connection() as conn:
            conn.executescript(MANIFEST_SCHEMA)

    def store(
        self, bundle: RawFilingBundle, *, run_id: str | None = None
    ) -> tuple[ArchiveOutcome, ...]:
        payload = bundle.payload
        if payload.fetched_at.utcoffset() is None:
            raise ContractViolationError("Retrieval time must include a timezone.")
        if payload.extension not in {"json", "csv", "xlsx", "parquet"}:
            raise ContractViolationError("Unsupported raw archive file extension.")
        digest = payload_file_sha256(payload.content)
        relative_path = f"sha256/{digest[:2]}/{digest}.{payload.extension}"
        fetched_at = payload.fetched_at.astimezone(UTC).isoformat()
        provenance = {
            "source_provider": payload.source_provider,
            "connector_name": payload.connector_name,
            "payload_nature": payload.nature,
            "fetched_at": fetched_at,
            "payload_file_sha256": digest,
            "raw_archive_path": relative_path,
            "provider_attrs": dict(payload.provider_attrs),
            "pagination_status": bundle.pagination_status,
            "deferred_locators": bundle.deferred_locators,
            "filing_metadata": [
                {
                    "raw_record_locator": filing.locator,
                    **_provided_evidence(filing.metadata),
                }
                for filing in bundle.filings
            ],
            "run_id": run_id,
        }
        receipt = json.dumps(
            provenance, ensure_ascii=False, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        receipt_sha256 = payload_file_sha256(receipt)
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / ".archive.lock", timeout=5):
            _write_immutable(self.root / relative_path, payload.content)
            _write_immutable(
                self.root / "provenance" / f"{receipt_sha256}.json",
                receipt,
            )
        if not bundle.filings:
            return ()

        attrs = json.dumps(
            dict(payload.provider_attrs), ensure_ascii=False, allow_nan=False
        )
        outcomes: list[ArchiveOutcome] = []
        with self.state.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for filing in bundle.filings:
                meta = filing.metadata
                canonical = canonical_content_sha256(
                    filing.metrics, meta.statement_scope, meta.period_end_date
                )
                values = {
                    key: value.isoformat() if isinstance(value, date) else value
                    for key, value in asdict(meta).items()
                }
                values["source_provider"] = payload.source_provider
                previous = conn.execute(LATEST, values).fetchone()
                if (
                    previous is not None
                    and previous["canonical_content_sha256"] == canonical
                ):
                    _refresh_metadata(
                        conn, previous, filing, receipt_sha256, fetched_at
                    )
                    outcomes.append(
                        ArchiveOutcome(
                            str(previous["filing_id"]),
                            meta.issuer_id,
                            "SKIPPED_UNCHANGED",
                        )
                    )
                    continue

                filing_id = str(uuid4())
                reason = RevisionReason.ORIGINAL
                revision = 1
                if previous is not None:
                    revision = int(previous["revision_no"]) + 1
                    reason = meta.revision_reason or (
                        RevisionReason.AUDITED_UPDATE
                        if meta.audit_status == AuditStatus.AUDITED
                        and previous["audit_status"] != AuditStatus.AUDITED
                        else RevisionReason.CORRECTION
                    )
                    if reason == RevisionReason.ORIGINAL:
                        raise ContractViolationError(
                            "A revision requires a non-original reason."
                        )
                conn.execute(
                    INSERT,
                    {
                        **values,
                        "filing_id": filing_id,
                        "connector_name": payload.connector_name,
                        "payload_nature": payload.nature,
                        "published_at_basis": meta.published_at_basis,
                        "effective_available_at": meta.effective_available_at.isoformat(),
                        "is_filed_late": meta.is_filed_late,
                        "revision_no": revision,
                        "revision_reason": reason,
                        "fetched_at": fetched_at,
                        "payload_file_sha256": digest,
                        "canonical_content_sha256": canonical,
                        "raw_archive_path": relative_path,
                        "raw_record_locator": filing.locator,
                        "provider_attrs_json": attrs,
                    },
                )
                outcomes.append(ArchiveOutcome(filing_id, meta.issuer_id, "ARCHIVED"))
        return tuple(outcomes)
