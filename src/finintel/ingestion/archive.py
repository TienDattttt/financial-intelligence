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
from uuid import NAMESPACE_URL, uuid5

from filelock import FileLock, Timeout

from finintel.domain.schemas import (
    AuditOpinion,
    AuditStatus,
    FilingMetadata,
    RawFiling,
    RawFilingBundle,
    RevisionReason,
    StatementScope,
)
from finintel.exceptions import ContractViolationError, TransientSourceError
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

MATCHING_CANONICAL = """
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
  AND canonical_content_sha256 = :canonical_content_sha256
ORDER BY revision_no DESC LIMIT 1
"""

METADATA_UPDATE = """
UPDATE filings_manifest
SET statement_scope = :statement_scope,
    canonical_content_sha256 = :canonical_content_sha256,
    audit_status = :audit_status,
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


def deterministic_filing_id(
    source_provider: str,
    issuer_id: str,
    statement_type: str,
    statement_scope: str,
    period_type: str,
    period_end_date: str,
    revision_no: int,
) -> str:
    """Derive a deterministic UUIDv5 for a specific filing revision."""
    key = (
        f"finintel:{source_provider}:{issuer_id}:{statement_type}:"
        f"{statement_scope}:{period_type}:{period_end_date}:r{revision_no}"
    )
    return str(uuid5(NAMESPACE_URL, key))


def _write_immutable(root: Path, path: Path, content: bytes) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ContractViolationError("Archive paths cannot use symbolic links.")
    resolved_parent = path.parent.resolve()
    if not resolved_parent.is_relative_to(root):
        raise ContractViolationError("Archive path is outside the raw archive root.")
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
    *,
    upgraded_scope: StatementScope | None = None,
    upgraded_canonical: str | None = None,
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
    if not changes and not refreshed and upgraded_scope is None:
        return

    publication = changes.get("published_at", previous["published_at"])
    target_scope = upgraded_scope or filing.metadata.statement_scope
    merged = replace(
        filing.metadata,
        statement_scope=target_scope,
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
    if upgraded_scope is not None:
        evidence["statement_scope"] = {
            "receipt_sha256": receipt_sha256,
            "raw_record_locator": filing.locator,
            "fetched_at": fetched_at,
        }
    effective_date = merged.effective_available_at
    if int(previous["revision_no"]) > 1 and merged.published_at is None:
        revision_fetched_date = (
            datetime.fromisoformat(str(previous["fetched_at"])).astimezone(UTC).date()
        )
        effective_date = max(effective_date, revision_fetched_date)
    conn.execute(
        METADATA_UPDATE,
        {
            "filing_id": previous["filing_id"],
            "statement_scope": str(target_scope),
            "canonical_content_sha256": upgraded_canonical
            or previous["canonical_content_sha256"],
            "audit_status": merged.audit_status,
            "audit_opinion": merged.audit_opinion,
            "published_at": publication,
            "published_at_basis": merged.published_at_basis,
            "effective_available_at": effective_date.isoformat(),
            "is_filed_late": merged.is_filed_late,
            "provider_attrs_json": json.dumps(
                attrs, ensure_ascii=False, allow_nan=False
            ),
        },
    )


class RawArchive:
    def __init__(self, root: Path, state: StateStore) -> None:
        if root.is_symlink():
            raise ContractViolationError("Raw archive root cannot be a symbolic link.")
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
        # Pre-validate all filing metrics and compute canonical hashes before
        # touching the filesystem or acquiring SQLite write locks.
        hashed_filings = [
            (
                filing,
                canonical_content_sha256(
                    filing.metrics,
                    filing.metadata.statement_scope,
                    filing.metadata.period_end_date,
                ),
            )
            for filing in bundle.filings
        ]
        digest = payload_file_sha256(payload.content)
        relative_path = f"sha256/{digest[:2]}/{digest}.{payload.extension}"
        fetched_utc = payload.fetched_at.astimezone(UTC)
        fetched_at = fetched_utc.isoformat()
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
        try:
            with FileLock(self.root / ".archive.lock", timeout=5):
                if not hashed_filings:
                    _write_immutable(
                        self.root, self.root / relative_path, payload.content
                    )
                    _write_immutable(
                        self.root,
                        self.root / "provenance" / f"{receipt_sha256}.json",
                        receipt,
                    )
                    return ()

                outcomes: list[ArchiveOutcome] = []
                with self.state.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for filing, canonical in hashed_filings:
                        meta = filing.metadata
                        values = {
                            key: value.isoformat() if isinstance(value, date) else value
                            for key, value in asdict(meta).items()
                        }
                        values["source_provider"] = payload.source_provider
                        previous = conn.execute(LATEST, values).fetchone()
                        if (
                            previous is None
                            and meta.statement_scope != StatementScope.UNKNOWN
                        ):
                            unknown_prev = conn.execute(
                                LATEST,
                                {**values, "statement_scope": StatementScope.UNKNOWN},
                            ).fetchone()
                            if unknown_prev is not None:
                                unknown_canonical = canonical_content_sha256(
                                    filing.metrics,
                                    StatementScope.UNKNOWN,
                                    meta.period_end_date,
                                )
                                if (
                                    unknown_prev["canonical_content_sha256"]
                                    == unknown_canonical
                                ):
                                    _refresh_metadata(
                                        conn,
                                        unknown_prev,
                                        filing,
                                        receipt_sha256,
                                        fetched_at,
                                        upgraded_scope=meta.statement_scope,
                                        upgraded_canonical=canonical,
                                    )
                                    outcomes.append(
                                        ArchiveOutcome(
                                            str(unknown_prev["filing_id"]),
                                            meta.issuer_id,
                                            "SKIPPED_UNCHANGED",
                                        )
                                    )
                                    continue

                        if previous is not None and (
                            previous["canonical_content_sha256"] == canonical
                            or fetched_utc
                            < datetime.fromisoformat(
                                str(previous["fetched_at"])
                            ).astimezone(UTC)
                        ):
                            target_row = previous
                            if previous["canonical_content_sha256"] != canonical:
                                matched = conn.execute(
                                    MATCHING_CANONICAL,
                                    {**values, "canonical_content_sha256": canonical},
                                ).fetchone()
                                if matched is not None:
                                    target_row = matched
                            _refresh_metadata(
                                conn, target_row, filing, receipt_sha256, fetched_at
                            )
                            outcomes.append(
                                ArchiveOutcome(
                                    str(target_row["filing_id"]),
                                    meta.issuer_id,
                                    "SKIPPED_UNCHANGED",
                                )
                            )
                            continue

                        reason = meta.revision_reason or RevisionReason.ORIGINAL
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
                        filing_id = deterministic_filing_id(
                            payload.source_provider,
                            meta.issuer_id,
                            str(meta.statement_type),
                            str(meta.statement_scope),
                            str(meta.period_type),
                            meta.period_end_date.isoformat(),
                            revision,
                        )
                        effective_date = meta.effective_available_at
                        if revision > 1 and meta.published_at is None:
                            effective_date = max(effective_date, fetched_utc.date())
                        filing_attrs = dict(payload.provider_attrs)
                        initial_evidence = _provided_evidence(meta)
                        if initial_evidence:
                            filing_attrs["metadata_evidence"] = {
                                name: {
                                    "receipt_sha256": receipt_sha256,
                                    "raw_record_locator": filing.locator,
                                    "fetched_at": fetched_at,
                                }
                                for name in initial_evidence
                            }
                        attrs = json.dumps(
                            filing_attrs, ensure_ascii=False, allow_nan=False
                        )
                        conn.execute(
                            INSERT,
                            {
                                **values,
                                "filing_id": filing_id,
                                "connector_name": payload.connector_name,
                                "payload_nature": payload.nature,
                                "published_at_basis": meta.published_at_basis,
                                "effective_available_at": effective_date.isoformat(),
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
                        outcomes.append(
                            ArchiveOutcome(filing_id, meta.issuer_id, "ARCHIVED")
                        )
                    _write_immutable(
                        self.root, self.root / relative_path, payload.content
                    )
                    _write_immutable(
                        self.root,
                        self.root / "provenance" / f"{receipt_sha256}.json",
                        receipt,
                    )
                return tuple(outcomes)
        except Timeout:
            raise TransientSourceError(
                "Timed out waiting for the raw archive lock."
            ) from None
