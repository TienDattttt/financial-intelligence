"""PostgreSQL and In-Memory stores for HITL false-positive triage (V3-29)."""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from finintel.hitl.schemas import HitlReasonCategory, HitlVerdict


@dataclass(frozen=True)
class HitlFeedbackRecord:
    feedback_id: UUID
    user_id: UUID | None
    ticker: str
    icb_code_level2: str
    period_end_date: date
    signal_code: str
    verdict: HitlVerdict
    reason_category: HitlReasonCategory
    note: str
    citation_page: int | None
    created_at: datetime


class HitlStoreProtocol(Protocol):
    def insert_feedback(
        self,
        *,
        user_id: UUID,
        ticker: str,
        icb_code_level2: str,
        period_end_date: date,
        signal_code: str,
        verdict: HitlVerdict,
        reason_category: HitlReasonCategory,
        note: str,
        citation_page: int | None,
    ) -> HitlFeedbackRecord: ...

    def list_feedback(
        self,
        *,
        signal_code: str | None = None,
        icb_code_level2: str | None = None,
    ) -> list[HitlFeedbackRecord]: ...


class InMemoryHitlStore:
    """Thread-safe offline HITL triage store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[HitlFeedbackRecord] = []

    def insert_feedback(
        self,
        *,
        user_id: UUID,
        ticker: str,
        icb_code_level2: str,
        period_end_date: date,
        signal_code: str,
        verdict: HitlVerdict,
        reason_category: HitlReasonCategory,
        note: str,
        citation_page: int | None,
    ) -> HitlFeedbackRecord:
        rec = HitlFeedbackRecord(
            feedback_id=uuid4(),
            user_id=user_id,
            ticker=ticker,
            icb_code_level2=icb_code_level2,
            period_end_date=period_end_date,
            signal_code=signal_code,
            verdict=verdict,
            reason_category=reason_category,
            note=note,
            citation_page=citation_page,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._records.append(rec)
        return rec

    def list_feedback(
        self,
        *,
        signal_code: str | None = None,
        icb_code_level2: str | None = None,
    ) -> list[HitlFeedbackRecord]:
        with self._lock:
            items = list(self._records)
        if signal_code:
            items = [r for r in items if r.signal_code == signal_code]
        if icb_code_level2:
            items = [r for r in items if r.icb_code_level2 == icb_code_level2]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items


class PostgresHitlStore:
    """PostgreSQL ``finintel_app`` implementation with strict lazy ``psycopg`` imports."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must not be empty.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    def insert_feedback(
        self,
        *,
        user_id: UUID,
        ticker: str,
        icb_code_level2: str,
        period_end_date: date,
        signal_code: str,
        verdict: HitlVerdict,
        reason_category: HitlReasonCategory,
        note: str,
        citation_page: int | None,
    ) -> HitlFeedbackRecord:
        fid = uuid4()
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hitl_flag_feedback (
                        feedback_id, user_id, ticker, icb_code_level2, period_end_date,
                        signal_code, verdict, reason_category, note, citation_page, created_at
                    ) VALUES (
                        %(feedback_id)s, %(user_id)s, %(ticker)s, %(icb_code_level2)s, %(period_end_date)s,
                        %(signal_code)s, %(verdict)s, %(reason_category)s, %(note)s, %(citation_page)s, %(created_at)s
                    )
                    """,
                    {
                        "feedback_id": fid,
                        "user_id": user_id,
                        "ticker": ticker,
                        "icb_code_level2": icb_code_level2,
                        "period_end_date": period_end_date,
                        "signal_code": signal_code,
                        "verdict": verdict,
                        "reason_category": reason_category,
                        "note": note,
                        "citation_page": citation_page,
                        "created_at": now,
                    },
                )
            conn.commit()
        return HitlFeedbackRecord(
            feedback_id=fid,
            user_id=user_id,
            ticker=ticker,
            icb_code_level2=icb_code_level2,
            period_end_date=period_end_date,
            signal_code=signal_code,
            verdict=verdict,
            reason_category=reason_category,
            note=note,
            citation_page=citation_page,
            created_at=now,
        )

    def list_feedback(
        self,
        *,
        signal_code: str | None = None,
        icb_code_level2: str | None = None,
    ) -> list[HitlFeedbackRecord]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if signal_code:
            clauses.append("signal_code = %(signal_code)s")
            params["signal_code"] = signal_code
        if icb_code_level2:
            clauses.append("icb_code_level2 = %(icb_code_level2)s")
            params["icb_code_level2"] = icb_code_level2
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT feedback_id, user_id, ticker, COALESCE(icb_code_level2, 'UNCLASSIFIED'),
                   period_end_date, signal_code, verdict, reason_category, note,
                   citation_page, created_at
            FROM hitl_flag_feedback
            {where_sql}
            ORDER BY created_at DESC
            LIMIT 5000
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [
            HitlFeedbackRecord(
                feedback_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])) if r[1] is not None else None,
                ticker=str(r[2]),
                icb_code_level2=str(r[3]),
                period_end_date=r[4],
                signal_code=str(r[5]),
                verdict=r[6],
                reason_category=r[7],
                note=str(r[8]),
                citation_page=int(r[9]) if r[9] is not None else None,
                created_at=r[10],
            )
            for r in rows
        ]
