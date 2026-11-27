"""PostgreSQL finintel_app store for INTERNAL_OPS QA queue & issuer lifecycle (V3-23).

Verified Sources:
    spec/v3/SPEC-v3-analyst-depth-and-ops-qa.md (§4, §5.2, §8)
    config/migrations/app_v3_004_ops_qa.sql
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from finintel.ops.qa_service import (
    IssuerLifecycleEventRecord,
    OpsDecision,
    OpsRevisionRecord,
    OpsTicketRecord,
    OpsTicketStatus,
    _ticket_key,
)


class PostgresOpsQaStore:
    """PostgreSQL ``finintel_app`` implementation with strict lazy ``psycopg`` imports."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must not be empty.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    def _load_revisions(
        self, cur: Any, ticket_ids: list[UUID]
    ) -> dict[UUID, list[OpsRevisionRecord]]:
        if not ticket_ids:
            return {}
        cur.execute(
            """
            SELECT submission_id, ticket_id, issuer_id, filing_id, revision_no,
                   source_url, raw_file_sha256, reason, payload_summary_json,
                   reingest_status, dagster_run_id, submitted_by_user_id, submitted_at
            FROM ops_revision_submissions
            WHERE ticket_id = ANY(%(ticket_ids)s)
            ORDER BY revision_no ASC
            """,
            {"ticket_ids": ticket_ids},
        )
        out: dict[UUID, list[OpsRevisionRecord]] = {}
        for r in cur.fetchall():
            tid = UUID(str(r[1]))
            out.setdefault(tid, []).append(
                OpsRevisionRecord(
                    submission_id=UUID(str(r[0])),
                    ticket_id=tid,
                    issuer_id=str(r[2]),
                    filing_id=str(r[3]),
                    revision_no=int(r[4]),
                    source_url=str(r[5]) if r[5] is not None else None,
                    raw_file_sha256=str(r[6]).strip() if r[6] is not None else None,
                    reason=str(r[7]),
                    payload_summary_json=dict(r[8]) if isinstance(r[8], dict) else {},
                    reingest_status=str(r[9]),
                    dagster_run_id=str(r[10]) if r[10] is not None else None,
                    submitted_by_user_id=UUID(str(r[11]))
                    if r[11] is not None
                    else None,
                    submitted_at=r[12],
                )
            )
        return out

    @staticmethod
    def _row_to_ticket(
        row: tuple[Any, ...], revs: list[OpsRevisionRecord]
    ) -> OpsTicketRecord:
        details = row[10] if isinstance(row[10], str) else json.dumps(row[10] or {})
        return OpsTicketRecord(
            ticket_id=UUID(str(row[0])),
            issuer_id=str(row[1]),
            ticker=str(row[2]),
            filing_id=str(row[3]),
            statement_scope=str(row[4]),
            period_type=str(row[5]),
            period_end_date=row[6],
            taxonomy_version=str(row[7]),
            rule_code=str(row[8]),
            severity=str(row[9]),
            details_json=details,
            status=row[11],
            decision=row[12],
            assigned_to_user_id=UUID(str(row[13])) if row[13] is not None else None,
            root_cause_note=str(row[14] or ""),
            latest_revision_no=int(row[15]),
            created_at=row[16],
            updated_at=row[17],
            resolved_at=row[18],
            resolved_by_user_id=UUID(str(row[19])) if row[19] is not None else None,
            revisions=tuple(revs),
        )

    def upsert_quarantine_ticket(self, row: Mapping[str, object]) -> OpsTicketRecord:
        key = _ticket_key(row)
        tid = uuid4()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ops_quarantine_tickets (
                        ticket_id, issuer_id, ticker, filing_id, statement_scope,
                        period_type, period_end_date, taxonomy_version, rule_code,
                        severity, details_json
                    ) VALUES (
                        %(ticket_id)s, %(issuer_id)s, %(ticker)s, %(filing_id)s, %(statement_scope)s,
                        %(period_type)s, %(period_end_date)s, %(taxonomy_version)s, %(rule_code)s,
                        %(severity)s, %(details_json)s::jsonb
                    )
                    ON CONFLICT (
                        issuer_id, filing_id, statement_scope, period_type,
                        period_end_date, taxonomy_version, rule_code
                    ) DO UPDATE SET ticker = EXCLUDED.ticker
                    RETURNING ticket_id, issuer_id, ticker, filing_id, statement_scope,
                              period_type, period_end_date, taxonomy_version, rule_code,
                              severity, details_json, status, decision, assigned_to_user_id,
                              root_cause_note, latest_revision_no, created_at, updated_at,
                              resolved_at, resolved_by_user_id
                    """,
                    {
                        "ticket_id": tid,
                        "issuer_id": key[0],
                        "ticker": str(row.get("ticker") or key[0]),
                        "filing_id": key[1],
                        "statement_scope": key[2],
                        "period_type": key[3],
                        "period_end_date": key[4],
                        "taxonomy_version": key[5],
                        "rule_code": key[6],
                        "severity": str(row.get("severity") or "CRITICAL"),
                        "details_json": str(row.get("details_json") or "{}"),
                    },
                )
                t_row = cur.fetchone()
                assert t_row is not None
                actual_tid = UUID(str(t_row[0]))
                cur.execute(
                    """
                    INSERT INTO ops_revision_submissions (
                        submission_id, ticket_id, issuer_id, filing_id, revision_no,
                        reason, reingest_status
                    ) VALUES (
                        %(sub_id)s, %(ticket_id)s, %(issuer_id)s, %(filing_id)s, 1,
                        'ORIGINAL_QUARANTINED_FILING', 'QUARANTINED'
                    )
                    ON CONFLICT (ticket_id, revision_no) DO NOTHING
                    """,
                    {
                        "sub_id": uuid4(),
                        "ticket_id": actual_tid,
                        "issuer_id": key[0],
                        "filing_id": key[1],
                    },
                )
                revs = self._load_revisions(cur, [actual_tid])
            conn.commit()
        return self._row_to_ticket(t_row, revs.get(actual_tid, []))

    def list_tickets(
        self, *, status: OpsTicketStatus | None = None
    ) -> list[OpsTicketRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status is None:
                    cur.execute(
                        """
                        SELECT ticket_id, issuer_id, ticker, filing_id, statement_scope,
                               period_type, period_end_date, taxonomy_version, rule_code,
                               severity, details_json, status, decision, assigned_to_user_id,
                               root_cause_note, latest_revision_no, created_at, updated_at,
                               resolved_at, resolved_by_user_id
                        FROM ops_quarantine_tickets ORDER BY updated_at DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticket_id, issuer_id, ticker, filing_id, statement_scope,
                               period_type, period_end_date, taxonomy_version, rule_code,
                               severity, details_json, status, decision, assigned_to_user_id,
                               root_cause_note, latest_revision_no, created_at, updated_at,
                               resolved_at, resolved_by_user_id
                        FROM ops_quarantine_tickets WHERE status = %(status)s ORDER BY updated_at DESC
                        """,
                        {"status": status},
                    )
                rows = cur.fetchall()
                tids = [UUID(str(r[0])) for r in rows]
                revs = self._load_revisions(cur, tids)
        return [self._row_to_ticket(r, revs.get(UUID(str(r[0])), [])) for r in rows]

    def get_ticket(self, ticket_id: UUID) -> OpsTicketRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticket_id, issuer_id, ticker, filing_id, statement_scope,
                           period_type, period_end_date, taxonomy_version, rule_code,
                           severity, details_json, status, decision, assigned_to_user_id,
                           root_cause_note, latest_revision_no, created_at, updated_at,
                           resolved_at, resolved_by_user_id
                    FROM ops_quarantine_tickets WHERE ticket_id = %(ticket_id)s
                    """,
                    {"ticket_id": ticket_id},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                revs = self._load_revisions(cur, [ticket_id])
        return self._row_to_ticket(row, revs.get(ticket_id, []))

    def assign_ticket(
        self, *, ticket_id: UUID, assigned_to_user_id: UUID | None, root_cause_note: str
    ) -> OpsTicketRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ops_quarantine_tickets
                    SET assigned_to_user_id = %(assigned)s,
                        root_cause_note = %(note)s,
                        status = CASE WHEN status = 'OPEN' THEN 'IN_REVIEW' ELSE status END,
                        updated_at = NOW()
                    WHERE ticket_id = %(ticket_id)s
                    RETURNING ticket_id, issuer_id, ticker, filing_id, statement_scope,
                              period_type, period_end_date, taxonomy_version, rule_code,
                              severity, details_json, status, decision, assigned_to_user_id,
                              root_cause_note, latest_revision_no, created_at, updated_at,
                              resolved_at, resolved_by_user_id
                    """,
                    {
                        "ticket_id": ticket_id,
                        "assigned": assigned_to_user_id,
                        "note": root_cause_note,
                    },
                )
                row = cur.fetchone()
                revs = self._load_revisions(cur, [ticket_id]) if row is not None else {}
            conn.commit()
        return (
            self._row_to_ticket(row, revs.get(ticket_id, []))
            if row is not None
            else None
        )

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
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ops_quarantine_tickets
                    SET latest_revision_no = latest_revision_no + 1,
                        status = CASE WHEN status = 'OPEN' THEN 'IN_REVIEW' ELSE status END,
                        updated_at = NOW()
                    WHERE ticket_id = %(ticket_id)s
                    RETURNING ticket_id, issuer_id, ticker, filing_id, statement_scope,
                              period_type, period_end_date, taxonomy_version, rule_code,
                              severity, details_json, status, decision, assigned_to_user_id,
                              root_cause_note, latest_revision_no, created_at, updated_at,
                              resolved_at, resolved_by_user_id
                    """,
                    {"ticket_id": ticket_id},
                )
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return None
                next_rev = int(row[15])
                cur.execute(
                    """
                    INSERT INTO ops_revision_submissions (
                        submission_id, ticket_id, issuer_id, filing_id, revision_no,
                        source_url, raw_file_sha256, reason, payload_summary_json,
                        reingest_status, dagster_run_id, submitted_by_user_id
                    ) VALUES (
                        %(sub_id)s, %(ticket_id)s, %(issuer_id)s, %(filing_id)s, %(rev_no)s,
                        %(source_url)s, %(sha)s, %(reason)s, %(summary)s::jsonb,
                        %(reingest)s, %(run_id)s, %(user_id)s
                    )
                    """,
                    {
                        "sub_id": uuid4(),
                        "ticket_id": ticket_id,
                        "issuer_id": str(row[1]),
                        "filing_id": str(row[3]),
                        "rev_no": next_rev,
                        "source_url": source_url,
                        "sha": raw_file_sha256,
                        "reason": reason,
                        "summary": json.dumps(payload_summary_json),
                        "reingest": reingest_status,
                        "run_id": dagster_run_id,
                        "user_id": submitted_by_user_id,
                    },
                )
                revs = self._load_revisions(cur, [ticket_id])
            conn.commit()
        return self._row_to_ticket(row, revs.get(ticket_id, []))

    def resolve_ticket(
        self,
        *,
        ticket_id: UUID,
        decision: OpsDecision,
        note: str,
        resolved_by_user_id: UUID,
    ) -> OpsTicketRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ops_quarantine_tickets
                    SET status = 'RESOLVED',
                        decision = %(decision)s,
                        root_cause_note = CASE WHEN %(note)s <> '' THEN %(note)s ELSE root_cause_note END,
                        resolved_at = NOW(),
                        resolved_by_user_id = %(user_id)s,
                        updated_at = NOW()
                    WHERE ticket_id = %(ticket_id)s
                    RETURNING ticket_id, issuer_id, ticker, filing_id, statement_scope,
                              period_type, period_end_date, taxonomy_version, rule_code,
                              severity, details_json, status, decision, assigned_to_user_id,
                              root_cause_note, latest_revision_no, created_at, updated_at,
                              resolved_at, resolved_by_user_id
                    """,
                    {
                        "ticket_id": ticket_id,
                        "decision": decision,
                        "note": note,
                        "user_id": resolved_by_user_id,
                    },
                )
                row = cur.fetchone()
                revs = self._load_revisions(cur, [ticket_id]) if row is not None else {}
            conn.commit()
        return (
            self._row_to_ticket(row, revs.get(ticket_id, []))
            if row is not None
            else None
        )

    def list_lifecycle_events(
        self, *, ticker: str | None = None
    ) -> list[IssuerLifecycleEventRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ticker is None:
                    cur.execute(
                        """
                        SELECT event_id, issuer_id, ticker, exchange, event_type,
                               effective_date, fy_end_month, previous_exchange,
                               source_url, notes, status, recorded_by_user_id, created_at
                        FROM issuer_lifecycle_events ORDER BY effective_date DESC, created_at DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT event_id, issuer_id, ticker, exchange, event_type,
                               effective_date, fy_end_month, previous_exchange,
                               source_url, notes, status, recorded_by_user_id, created_at
                        FROM issuer_lifecycle_events WHERE ticker = %(ticker)s
                        ORDER BY effective_date DESC, created_at DESC
                        """,
                        {"ticker": ticker},
                    )
                rows = cur.fetchall()
        return [
            IssuerLifecycleEventRecord(
                event_id=UUID(str(r[0])),
                issuer_id=str(r[1]),
                ticker=str(r[2]),
                exchange=str(r[3]),
                event_type=str(r[4]),
                effective_date=r[5],
                fy_end_month=int(r[6]),
                previous_exchange=str(r[7]) if r[7] is not None else None,
                source_url=str(r[8] or ""),
                notes=str(r[9] or ""),
                status=str(r[10]),
                recorded_by_user_id=UUID(str(r[11])) if r[11] is not None else None,
                created_at=r[12],
            )
            for r in rows
        ]

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
        eid = uuid4()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO issuer_lifecycle_events (
                        event_id, issuer_id, ticker, exchange, event_type,
                        effective_date, fy_end_month, previous_exchange,
                        source_url, notes, status, recorded_by_user_id
                    ) VALUES (
                        %(event_id)s, %(issuer_id)s, %(ticker)s, %(exchange)s, %(event_type)s,
                        %(effective_date)s, %(fy_end_month)s, %(previous_exchange)s,
                        %(source_url)s, %(notes)s, %(status)s, %(recorded_by_user_id)s
                    )
                    RETURNING created_at
                    """,
                    {
                        "event_id": eid,
                        "issuer_id": issuer_id,
                        "ticker": ticker,
                        "exchange": exchange,
                        "event_type": event_type,
                        "effective_date": effective_date,
                        "fy_end_month": fy_end_month,
                        "previous_exchange": previous_exchange,
                        "source_url": source_url,
                        "notes": notes,
                        "status": status,
                        "recorded_by_user_id": recorded_by_user_id,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return IssuerLifecycleEventRecord(
            event_id=eid,
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
            created_at=row[0],
        )


__all__ = ["PostgresOpsQaStore"]
