"""Store immutable payloads and serialize version allocation in SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Sequence
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
from finintel.ingestion.hasher import (
    MANIFEST_SIGNATURE_FIELDS,
    canonical_content_sha256,
    compute_manifest_sig_sha256,
    payload_file_sha256,
)
from finintel.pipeline.state_store import StateStore

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIG_FIELDS_SQL = ", ".join(MANIFEST_SIGNATURE_FIELDS)
_INVALIDATE_SIG_DIFF_SQL = "\n      OR ".join(
    f"NEW.{col} IS NOT OLD.{col}" for col in MANIFEST_SIGNATURE_FIELDS
)
_PROVIDER_TICKER_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS filings_manifest_by_provider_ticker "
    "ON filings_manifest (source_provider, ticker_at_filing)"
)
_PROVIDER_ISSUER_SIG_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS filings_manifest_by_provider_issuer_sig "
    "ON filings_manifest (source_provider, issuer_id, filing_id, manifest_sig_sha256)"
)
_INVALIDATE_SIG_TRIGGER_SQL = f"""
CREATE TRIGGER IF NOT EXISTS filings_manifest_invalidate_sig_on_update
AFTER UPDATE OF {_SIG_FIELDS_SQL}
ON filings_manifest
WHEN NEW.manifest_sig_sha256 IS NOT NULL
  AND NEW.manifest_sig_sha256 IS OLD.manifest_sig_sha256
  AND (
      {_INVALIDATE_SIG_DIFF_SQL}
  )
BEGIN
    UPDATE filings_manifest
    SET manifest_sig_sha256 = NULL
    WHERE filing_id = NEW.filing_id;
END
""".strip()

MANIFEST_SCHEMA = f"""
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
    provider_attrs_json TEXT,
    manifest_sig_sha256 TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS filings_manifest_by_revision
ON filings_manifest (
    source_provider, issuer_id, statement_type, statement_scope, period_type,
    period_end_date, revision_no
);
{_PROVIDER_TICKER_INDEX_SQL};
{_PROVIDER_ISSUER_SIG_INDEX_SQL};
{_INVALIDATE_SIG_TRIGGER_SQL};
"""

LATEST = f"""
SELECT filing_id, fetched_at, {_SIG_FIELDS_SQL}
FROM filings_manifest
WHERE source_provider = :source_provider
  AND issuer_id = :issuer_id
  AND statement_type = :statement_type
  AND statement_scope = :statement_scope
  AND period_type = :period_type
  AND period_end_date = :period_end_date
ORDER BY revision_no DESC LIMIT 1
"""

MATCHING_CANONICAL = f"""
SELECT filing_id, fetched_at, {_SIG_FIELDS_SQL}
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
    provider_attrs_json = :provider_attrs_json,
    manifest_sig_sha256 = :manifest_sig_sha256
WHERE filing_id = :filing_id
"""

INSERT = """
INSERT INTO filings_manifest (
    filing_id, issuer_id, ticker_at_filing, source_provider, connector_name, payload_nature,
    statement_type, statement_scope, period_type, fiscal_year, fiscal_quarter,
    fiscal_year_start_date, period_start_date, period_end_date, published_at,
    published_at_basis, effective_available_at, audit_status, audit_opinion,
    is_filed_late, revision_no, revision_reason, fetched_at, payload_file_sha256,
    canonical_content_sha256, raw_archive_path, raw_record_locator, provider_attrs_json,
    manifest_sig_sha256
)
VALUES (
    :filing_id, :issuer_id, :ticker_at_filing, :source_provider, :connector_name, :payload_nature,
    :statement_type, :statement_scope, :period_type, :fiscal_year, :fiscal_quarter,
    :fiscal_year_start_date, :period_start_date, :period_end_date, :published_at,
    :published_at_basis, :effective_available_at, :audit_status, :audit_opinion,
    :is_filed_late, :revision_no, :revision_reason, :fetched_at, :payload_file_sha256,
    :canonical_content_sha256, :raw_archive_path, :raw_record_locator, :provider_attrs_json,
    :manifest_sig_sha256
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


def _needs_metadata_refresh(
    previous: sqlite3.Row,
    filing: RawFiling,
    fetched_at: str,
) -> bool:
    attrs = json.loads(previous["provider_attrs_json"] or "{}")
    evidence = attrs.get("metadata_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    incoming = datetime.fromisoformat(fetched_at)
    for name, value in _provided_evidence(filing.metadata).items():
        reference = evidence.get(name)
        prior = reference.get("fetched_at") if isinstance(reference, dict) else None
        if not isinstance(prior, str):
            prior = previous["fetched_at"]
        prior_at = datetime.fromisoformat(prior)
        if incoming < prior_at:
            continue
        if value == previous[name]:
            if incoming > prior_at:
                return True
            continue
        return True
    return False


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
    canonical_val = upgraded_canonical or previous["canonical_content_sha256"]
    attrs_json = json.dumps(attrs, ensure_ascii=False, allow_nan=False)
    sig_row: dict[str, object] = {
        field: previous[field] for field in MANIFEST_SIGNATURE_FIELDS
    }
    sig_row.update(
        {
            "statement_scope": str(target_scope),
            "canonical_content_sha256": canonical_val,
            "audit_status": str(merged.audit_status),
            "audit_opinion": str(merged.audit_opinion),
            "published_at": publication,
            "published_at_basis": str(merged.published_at_basis),
            "effective_available_at": effective_date.isoformat(),
            "is_filed_late": merged.is_filed_late,
            "provider_attrs_json": attrs_json,
        }
    )
    conn.execute(
        METADATA_UPDATE,
        {
            "filing_id": previous["filing_id"],
            "statement_scope": str(target_scope),
            "canonical_content_sha256": canonical_val,
            "audit_status": merged.audit_status,
            "audit_opinion": merged.audit_opinion,
            "published_at": publication,
            "published_at_basis": merged.published_at_basis,
            "effective_available_at": effective_date.isoformat(),
            "is_filed_late": merged.is_filed_late,
            "provider_attrs_json": attrs_json,
            "manifest_sig_sha256": compute_manifest_sig_sha256(sig_row),
        },
    )


def _backfill_manifest_signatures(conn: sqlite3.Connection) -> None:
    sha256 = hashlib.sha256
    old_factory = conn.row_factory
    conn.row_factory = None
    try:
        while True:
            rows = conn.execute(
                f"SELECT filing_id, CAST(json_array({_SIG_FIELDS_SQL}) AS BLOB) "
                "FROM filings_manifest WHERE manifest_sig_sha256 IS NULL LIMIT 5000"
            ).fetchall()
            if not rows:
                break
            updates = [
                (
                    sha256(
                        blob if isinstance(blob, bytes) else str(blob).encode("utf-8")
                    ).hexdigest(),
                    filing_id,
                )
                for filing_id, blob in rows
            ]
            conn.executemany(
                "UPDATE filings_manifest SET manifest_sig_sha256 = ? WHERE filing_id = ?",
                updates,
            )
    finally:
        conn.row_factory = old_factory


class RawArchive:
    def __init__(
        self, root: Path, state: StateStore, *, read_only: bool = False
    ) -> None:
        if root.is_symlink():
            raise ContractViolationError("Raw archive root cannot be a symbolic link.")
        self.root = root.resolve()
        self.state = state
        self.read_only = read_only or ("mode=ro" in str(state.path))
        if self.read_only:
            return
        self.state.path.parent.mkdir(parents=True, exist_ok=True)
        with self.state.connection() as conn:
            query_only = conn.execute("PRAGMA query_only").fetchone()
            if query_only is not None and int(query_only[0]):
                return
            has_table = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is not None
            )
            if not has_table:
                conn.executescript(MANIFEST_SCHEMA)
                return
            col_names = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(filings_manifest)"
                ).fetchall()
            }
            master_names = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
                ).fetchall()
            }
            needs_col = "manifest_sig_sha256" not in col_names
            needs_ticker_idx = "filings_manifest_by_provider_ticker" not in master_names
            needs_sig_idx = (
                "filings_manifest_by_provider_issuer_sig" not in master_names
            )
            needs_trigger = (
                "filings_manifest_invalidate_sig_on_update" not in master_names
            )
            needs_backfill = (
                needs_col
                or conn.execute(
                    "SELECT 1 FROM filings_manifest WHERE manifest_sig_sha256 IS NULL LIMIT 1"
                ).fetchone()
                is not None
            )
            if not (
                needs_col
                or needs_ticker_idx
                or needs_sig_idx
                or needs_trigger
                or needs_backfill
            ):
                return
            conn.execute("BEGIN IMMEDIATE")
            if needs_col:
                conn.execute(
                    "ALTER TABLE filings_manifest ADD COLUMN manifest_sig_sha256 TEXT"
                )
            if needs_ticker_idx:
                conn.execute(_PROVIDER_TICKER_INDEX_SQL)
            if needs_sig_idx:
                conn.execute(_PROVIDER_ISSUER_SIG_INDEX_SQL)
            if needs_trigger:
                conn.execute(_INVALIDATE_SIG_TRIGGER_SQL)
            if needs_backfill:
                _backfill_manifest_signatures(conn)

    def store(
        self,
        bundle: RawFilingBundle,
        *,
        run_id: str | None = None,
        precomputed_canonical_hashes: Sequence[str | None] | None = None,
    ) -> tuple[ArchiveOutcome, ...]:
        if self.read_only:
            raise ContractViolationError("Cannot write to a read-only RawArchive.")
        payload = bundle.payload
        if payload.fetched_at.utcoffset() is None:
            raise ContractViolationError("Retrieval time must include a timezone.")
        if payload.extension not in {"json", "csv", "xlsx", "parquet"}:
            raise ContractViolationError("Unsupported raw archive file extension.")
        # Pre-validate all filing metrics and compute canonical hashes before
        # touching the filesystem or acquiring SQLite write locks.
        if precomputed_canonical_hashes is None:
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
        else:
            if len(precomputed_canonical_hashes) != len(bundle.filings):
                raise ContractViolationError(
                    "precomputed_canonical_hashes length must match bundle filings."
                )
            hashed_filings = []
            for filing, pre_hash in zip(
                bundle.filings, precomputed_canonical_hashes, strict=True
            ):
                if pre_hash is None:
                    canonical_hash = canonical_content_sha256(
                        filing.metrics,
                        filing.metadata.statement_scope,
                        filing.metadata.period_end_date,
                    )
                elif isinstance(pre_hash, str) and _SHA256_RE.fullmatch(pre_hash):
                    canonical_hash = pre_hash
                else:
                    raise ContractViolationError(
                        "Invalid precomputed canonical SHA-256."
                    )
                hashed_filings.append((filing, canonical_hash))
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

        payload_path = self.root / relative_path
        receipt_path = self.root / "provenance" / f"{receipt_sha256}.json"
        if (
            not payload_path.is_symlink()
            and not payload_path.parent.is_symlink()
            and not receipt_path.is_symlink()
            and not receipt_path.parent.is_symlink()
            and payload_path.is_file()
            and receipt_path.is_file()
            and payload_path.parent.resolve().is_relative_to(self.root)
            and receipt_path.parent.resolve().is_relative_to(self.root)
        ):
            if (
                payload_path.read_bytes() != payload.content
                or receipt_path.read_bytes() != receipt
            ):
                raise ContractViolationError("An immutable archive file has changed.")
            if not hashed_filings:
                return ()
            fast_outcomes: list[ArchiveOutcome] = []
            fast_ok = True
            with self.state.connection() as conn:
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
                        or previous["canonical_content_sha256"] != canonical
                        or _needs_metadata_refresh(previous, filing, fetched_at)
                    ):
                        fast_ok = False
                        break
                    fast_outcomes.append(
                        ArchiveOutcome(
                            str(previous["filing_id"]),
                            meta.issuer_id,
                            "SKIPPED_UNCHANGED",
                        )
                    )
            if fast_ok:
                return tuple(fast_outcomes)

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(self.root / ".archive.lock", timeout=5):
                if not hashed_filings:
                    _write_immutable(self.root, payload_path, payload.content)
                    _write_immutable(self.root, receipt_path, receipt)
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
                        insert_params: dict[str, object] = {
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
                        }
                        insert_params["manifest_sig_sha256"] = (
                            compute_manifest_sig_sha256(insert_params)
                        )
                        conn.execute(INSERT, insert_params)
                        outcomes.append(
                            ArchiveOutcome(filing_id, meta.issuer_id, "ARCHIVED")
                        )
                    _write_immutable(self.root, payload_path, payload.content)
                    _write_immutable(self.root, receipt_path, receipt)
                return tuple(outcomes)
        except Timeout:
            raise TransientSourceError(
                "Timed out waiting for the raw archive lock."
            ) from None
