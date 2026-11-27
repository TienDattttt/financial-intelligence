"""Data Ops QA Queue service and append-only revision store (V3-23).

Verified Sources:
    spec/v3/SPEC-v3-analyst-depth-and-ops-qa.md (§4, §5.2, §6, §8)
    config/migrations/app_v3_004_ops_qa.sql
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import AuthenticatedUser
from finintel.workspace.audit import record_audit_event

OpsTicketStatus = Literal["OPEN", "IN_REVIEW", "RESOLVED"]
OpsDecision = Literal["APPROVE_REINGEST", "KEEP_QUARANTINED"]
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_HTTP_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


class OpsQaError(Exception):
    """Domain error mapped cleanly to HTTP status codes in the Ops router."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class OpsRevisionRecord:
    submission_id: UUID
    ticket_id: UUID
    issuer_id: str
    filing_id: str
    revision_no: int
    source_url: str | None
    raw_file_sha256: str | None
    reason: str
    payload_summary_json: dict[str, object]
    reingest_status: str
    dagster_run_id: str | None
    submitted_by_user_id: UUID | None
    submitted_at: datetime


@dataclass(frozen=True)
class OpsTicketRecord:
    ticket_id: UUID
    issuer_id: str
    ticker: str
    filing_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    taxonomy_version: str
    rule_code: str
    severity: str
    details_json: str
    status: OpsTicketStatus
    decision: OpsDecision | None
    assigned_to_user_id: UUID | None
    root_cause_note: str
    latest_revision_no: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: UUID | None
    revisions: tuple[OpsRevisionRecord, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IssuerLifecycleEventRecord:
    event_id: UUID
    issuer_id: str
    ticker: str
    exchange: str
    event_type: str
    effective_date: date
    fy_end_month: int
    previous_exchange: str | None
    source_url: str
    notes: str
    status: str
    recorded_by_user_id: UUID | None
    created_at: datetime


class OpsQaStoreProtocol(Protocol):
    def upsert_quarantine_ticket(
        self, row: Mapping[str, object]
    ) -> OpsTicketRecord: ...
    def list_tickets(
        self, *, status: OpsTicketStatus | None = None
    ) -> list[OpsTicketRecord]: ...
    def get_ticket(self, ticket_id: UUID) -> OpsTicketRecord | None: ...
    def assign_ticket(
        self, *, ticket_id: UUID, assigned_to_user_id: UUID | None, root_cause_note: str
    ) -> OpsTicketRecord | None: ...
    def append_revision(
        self,
        *,
        ticket_id: UUID,
        source_url: str | None,
        raw_file_sha256: str | None,
        reason: str,
        payload_summary_json: dict[str, object],
        reingest_status: str,
        dagster_run_id: str | None,
        submitted_by_user_id: UUID | None,
    ) -> OpsTicketRecord | None: ...
    def resolve_ticket(
        self,
        *,
        ticket_id: UUID,
        decision: OpsDecision,
        note: str,
        resolved_by_user_id: UUID,
    ) -> OpsTicketRecord | None: ...
    def list_lifecycle_events(
        self, *, ticker: str | None = None
    ) -> list[IssuerLifecycleEventRecord]: ...
    def insert_lifecycle_event(
        self,
        *,
        issuer_id: str,
        ticker: str,
        exchange: str,
        event_type: str,
        effective_date: date,
        fy_end_month: int,
        previous_exchange: str | None,
        source_url: str,
        notes: str,
        status: str,
        recorded_by_user_id: UUID | None,
    ) -> IssuerLifecycleEventRecord: ...


def _ticket_key(row: Mapping[str, object]) -> tuple[str, str, str, str, date, str, str]:
    raw_end = row["period_end_date"]
    pend = (
        raw_end if isinstance(raw_end, date) else date.fromisoformat(str(raw_end)[:10])
    )
    return (
        str(row["issuer_id"]),
        str(row["filing_id"]),
        str(row["statement_scope"]),
        str(row["period_type"]),
        pend,
        str(row["taxonomy_version"]),
        str(row["rule_code"]),
    )


class InMemoryOpsQaStore:
    """Thread-safe in-memory store with strictly append-only ``ops_revision_submissions``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets: dict[UUID, OpsTicketRecord] = {}
        self._by_key: dict[tuple[str, str, str, str, date, str, str], UUID] = {}
        self._revisions: dict[UUID, list[OpsRevisionRecord]] = {}
        self._lifecycle: list[IssuerLifecycleEventRecord] = []

    def _hydrate(self, ticket: OpsTicketRecord) -> OpsTicketRecord:
        revs = tuple(
            sorted(
                self._revisions.get(ticket.ticket_id, []), key=lambda r: r.revision_no
            )
        )
        return OpsTicketRecord(**{**ticket.__dict__, "revisions": revs})

    def upsert_quarantine_ticket(self, row: Mapping[str, object]) -> OpsTicketRecord:
        key = _ticket_key(row)
        now = datetime.now(UTC)
        with self._lock:
            existing_id = self._by_key.get(key)
            if existing_id is not None:
                return self._hydrate(self._tickets[existing_id])
            ticket_id = uuid4()
            ticket = OpsTicketRecord(
                ticket_id=ticket_id,
                issuer_id=key[0],
                ticker=str(row.get("ticker") or key[0]),
                filing_id=key[1],
                statement_scope=key[2],
                period_type=key[3],
                period_end_date=key[4],
                taxonomy_version=key[5],
                rule_code=key[6],
                severity=str(row.get("severity") or "CRITICAL"),
                details_json=str(row.get("details_json") or "{}"),
                status="OPEN",
                decision=None,
                assigned_to_user_id=None,
                root_cause_note="",
                latest_revision_no=1,
                created_at=now,
                updated_at=now,
                resolved_at=None,
                resolved_by_user_id=None,
            )
            rev1 = OpsRevisionRecord(
                submission_id=uuid4(),
                ticket_id=ticket_id,
                issuer_id=key[0],
                filing_id=key[1],
                revision_no=1,
                source_url=None,
                raw_file_sha256=None,
                reason="ORIGINAL_QUARANTINED_FILING",
                payload_summary_json={},
                reingest_status="QUARANTINED",
                dagster_run_id=None,
                submitted_by_user_id=None,
                submitted_at=now,
            )
            self._tickets[ticket_id] = ticket
            self._by_key[key] = ticket_id
            self._revisions[ticket_id] = [rev1]
            return self._hydrate(ticket)

    def list_tickets(
        self, *, status: OpsTicketStatus | None = None
    ) -> list[OpsTicketRecord]:
        with self._lock:
            items = [
                self._hydrate(t)
                for t in self._tickets.values()
                if status is None or t.status == status
            ]
            items.sort(key=lambda t: t.updated_at, reverse=True)
            return items

    def get_ticket(self, ticket_id: UUID) -> OpsTicketRecord | None:
        with self._lock:
            t = self._tickets.get(ticket_id)
            return self._hydrate(t) if t is not None else None

    def assign_ticket(
        self, *, ticket_id: UUID, assigned_to_user_id: UUID | None, root_cause_note: str
    ) -> OpsTicketRecord | None:
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                return None
            next_status: OpsTicketStatus = (
                "IN_REVIEW" if t.status == "OPEN" else t.status
            )
            updated = OpsTicketRecord(
                **{
                    **t.__dict__,
                    "status": next_status,
                    "assigned_to_user_id": assigned_to_user_id,
                    "root_cause_note": root_cause_note,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._tickets[ticket_id] = updated
            return self._hydrate(updated)

    def append_revision(
        self,
        *,
        ticket_id: UUID,
        source_url: str | None,
        raw_file_sha256: str | None,
        reason: str,
        payload_summary_json: dict[str, object],
        reingest_status: str,
        dagster_run_id: str | None,
        submitted_by_user_id: UUID | None,
    ) -> OpsTicketRecord | None:
        now = datetime.now(UTC)
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                return None
            next_rev_no = t.latest_revision_no + 1
            rev = OpsRevisionRecord(
                submission_id=uuid4(),
                ticket_id=ticket_id,
                issuer_id=t.issuer_id,
                filing_id=t.filing_id,
                revision_no=next_rev_no,
                source_url=source_url,
                raw_file_sha256=raw_file_sha256,
                reason=reason,
                payload_summary_json=dict(payload_summary_json),
                reingest_status=reingest_status,
                dagster_run_id=dagster_run_id,
                submitted_by_user_id=submitted_by_user_id,
                submitted_at=now,
            )
            self._revisions.setdefault(ticket_id, []).append(rev)
            next_status: OpsTicketStatus = (
                "IN_REVIEW" if t.status == "OPEN" else t.status
            )
            updated = OpsTicketRecord(
                **{
                    **t.__dict__,
                    "status": next_status,
                    "latest_revision_no": next_rev_no,
                    "updated_at": now,
                }
            )
            self._tickets[ticket_id] = updated
            return self._hydrate(updated)

    def resolve_ticket(
        self,
        *,
        ticket_id: UUID,
        decision: OpsDecision,
        note: str,
        resolved_by_user_id: UUID,
    ) -> OpsTicketRecord | None:
        now = datetime.now(UTC)
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                return None
            updated = OpsTicketRecord(
                **{
                    **t.__dict__,
                    "status": "RESOLVED",
                    "decision": decision,
                    "root_cause_note": note if note else t.root_cause_note,
                    "resolved_at": now,
                    "resolved_by_user_id": resolved_by_user_id,
                    "updated_at": now,
                }
            )
            self._tickets[ticket_id] = updated
            return self._hydrate(updated)

    def list_lifecycle_events(
        self, *, ticker: str | None = None
    ) -> list[IssuerLifecycleEventRecord]:
        with self._lock:
            rows = [
                e
                for e in reversed(self._lifecycle)
                if ticker is None or e.ticker == ticker
            ]
            return rows

    def insert_lifecycle_event(
        self,
        *,
        issuer_id: str,
        ticker: str,
        exchange: str,
        event_type: str,
        effective_date: date,
        fy_end_month: int,
        previous_exchange: str | None,
        source_url: str,
        notes: str,
        status: str,
        recorded_by_user_id: UUID | None,
    ) -> IssuerLifecycleEventRecord:
        rec = IssuerLifecycleEventRecord(
            event_id=uuid4(),
            issuer_id=issuer_id,
            ticker=ticker,
            exchange=exchange,
            event_type=event_type,
            effective_date=effective_date,
            fy_end_month=fy_end_month,
            previous_exchange=previous_exchange,
            source_url=source_url,
            notes=notes,
            status=status,
            recorded_by_user_id=recorded_by_user_id,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._lifecycle.append(rec)
            return rec


class OpsQaService:
    """INTERNAL_OPS QA queue workflow with immutable append-only revisions and 100% audit logging."""

    def __init__(
        self,
        qa_store: OpsQaStoreProtocol,
        *,
        audit_store: AppStoreProtocol | None = None,
        reingest_trigger: Callable[
            [OpsTicketRecord, int, str | None, str | None], str | None
        ]
        | None = None,
    ) -> None:
        self._qa = qa_store
        self._audit = audit_store
        self._reingest_trigger = reingest_trigger

    def _audit_ops(
        self,
        *,
        actor: AuthenticatedUser,
        action: str,
        resource_type: str,
        resource_id: str,
        client_ip: str,
        metadata: dict[str, object],
    ) -> None:
        if self._audit is not None:
            record_audit_event(
                self._audit,
                actor_user_id=actor.user_id,
                actor_role=actor.role.value,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome="SUCCESS",
                client_ip=client_ip,
                metadata=metadata,
            )

    def list_queue(
        self,
        *,
        snapshot_quarantine_rows: Sequence[Mapping[str, object]] = (),
        status: OpsTicketStatus | None = None,
    ) -> list[OpsTicketRecord]:
        for row in snapshot_quarantine_rows:
            self._qa.upsert_quarantine_ticket(row)
        return self._qa.list_tickets(status=status)

    def assign_ticket(
        self,
        *,
        ticket_id: UUID,
        actor: AuthenticatedUser,
        assigned_to_user_id: UUID | None,
        root_cause_note: str,
        client_ip: str = "",
    ) -> OpsTicketRecord:
        updated = self._qa.assign_ticket(
            ticket_id=ticket_id,
            assigned_to_user_id=assigned_to_user_id or actor.user_id,
            root_cause_note=root_cause_note.strip(),
        )
        if updated is None:
            raise OpsQaError(404, "Không tìm thấy phiếu kiểm tra chất lượng dữ liệu.")
        self._audit_ops(
            actor=actor,
            action="OPS_QA_TICKET_ASSIGNED",
            resource_type="ops_quarantine_ticket",
            resource_id=str(ticket_id),
            client_ip=client_ip,
            metadata={"ticker": updated.ticker, "status": updated.status},
        )
        return updated

    def submit_revision(
        self,
        *,
        ticket_id: UUID,
        actor: AuthenticatedUser,
        source_url: str | None,
        raw_file_sha256: str | None,
        reason: str,
        payload_summary: dict[str, object] | None = None,
        client_ip: str = "",
    ) -> OpsTicketRecord:
        clean_reason = reason.strip()
        if len(clean_reason) < 5:
            raise OpsQaError(422, "Vui lòng nhập lý do đính chính tối thiểu 5 ký tự.")
        clean_url = source_url.strip() if source_url and source_url.strip() else None
        clean_sha = (
            raw_file_sha256.strip().lower()
            if raw_file_sha256 and raw_file_sha256.strip()
            else None
        )
        if clean_url is None and clean_sha is None:
            raise OpsQaError(
                422,
                "Bắt buộc đính kèm source_url hoặc raw_file_sha256 để truy vết bản ghi đính chính.",
            )
        if clean_url is not None and not _HTTP_URL_RE.fullmatch(clean_url):
            raise OpsQaError(
                422, "source_url phải là địa chỉ http:// hoặc https:// hợp lệ."
            )
        if clean_sha is not None and not _SHA256_RE.fullmatch(clean_sha):
            raise OpsQaError(
                422, "raw_file_sha256 phải là chuỗi SHA-256 gồm 64 ký tự hex."
            )

        existing = self._qa.get_ticket(ticket_id)
        if existing is None:
            raise OpsQaError(404, "Không tìm thấy phiếu kiểm tra chất lượng dữ liệu.")

        dagster_run_id: str | None = None
        reingest_status = "PENDING_REINGEST"
        if self._reingest_trigger is not None:
            dagster_run_id = self._reingest_trigger(
                existing, existing.latest_revision_no + 1, clean_url, clean_sha
            )
            if dagster_run_id:
                reingest_status = "TRIGGERED_DAGSTER"

        updated = self._qa.append_revision(
            ticket_id=ticket_id,
            source_url=clean_url,
            raw_file_sha256=clean_sha,
            reason=clean_reason,
            payload_summary_json=payload_summary or {},
            reingest_status=reingest_status,
            dagster_run_id=dagster_run_id,
            submitted_by_user_id=actor.user_id,
        )
        assert updated is not None
        self._audit_ops(
            actor=actor,
            action="OPS_QA_REVISION_SUBMITTED",
            resource_type="ops_quarantine_ticket",
            resource_id=str(ticket_id),
            client_ip=client_ip,
            metadata={
                "ticker": updated.ticker,
                "revision_no": updated.latest_revision_no,
                "has_source_url": clean_url is not None,
                "has_raw_sha256": clean_sha is not None,
            },
        )
        return updated

    def decide_ticket(
        self,
        *,
        ticket_id: UUID,
        actor: AuthenticatedUser,
        decision: OpsDecision,
        note: str = "",
        client_ip: str = "",
    ) -> OpsTicketRecord:
        existing = self._qa.get_ticket(ticket_id)
        if existing is None:
            raise OpsQaError(404, "Không tìm thấy phiếu kiểm tra chất lượng dữ liệu.")
        if decision == "APPROVE_REINGEST" and existing.latest_revision_no < 2:
            raise OpsQaError(
                422,
                "Phải nạp bản ghi đính chính (revision_no >= 2) kèm nguồn trước khi duyệt nạp lại.",
            )
        updated = self._qa.resolve_ticket(
            ticket_id=ticket_id,
            decision=decision,
            note=note.strip(),
            resolved_by_user_id=actor.user_id,
        )
        assert updated is not None
        self._audit_ops(
            actor=actor,
            action="OPS_QA_DECISION",
            resource_type="ops_quarantine_ticket",
            resource_id=str(ticket_id),
            client_ip=client_ip,
            metadata={
                "ticker": updated.ticker,
                "decision": decision,
                "revision_no": updated.latest_revision_no,
            },
        )
        return updated
