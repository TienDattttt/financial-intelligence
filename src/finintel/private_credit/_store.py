"""Isolated PostgreSQL & In-Memory store for private-issuer dossiers (V3-28, V3-29)."""

from __future__ import annotations

import importlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from finintel.private_credit._qg1 import (
    ParsedFactRow,
    StatementScopeStr,
    TaxonomyVersionStr,
)

DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class PrivateCreditDossierRecord:
    dossier_id: UUID
    user_id: UUID
    company_name: str
    tax_code: str | None
    icb_code_level2: str | None
    statement_scope: StatementScopeStr
    taxonomy_version: TaxonomyVersionStr
    unit_multiplier: int
    source_filename: str
    source_sha256: str
    qg1_status: str
    qg1_report_json: dict[str, object]
    periods: tuple[date, ...]
    facts: tuple[ParsedFactRow, ...]
    latest_score_json: dict[str, object] | None
    retention_expires_at: datetime
    created_at: datetime
    updated_at: datetime


class PrivateCreditStoreProtocol(Protocol):
    def create_dossier(
        self,
        *,
        user_id: UUID,
        company_name: str,
        tax_code: str | None,
        icb_code_level2: str | None,
        statement_scope: StatementScopeStr,
        taxonomy_version: TaxonomyVersionStr,
        unit_multiplier: int,
        source_filename: str,
        source_sha256: str,
        qg1_status: str,
        qg1_report_json: dict[str, object],
        periods: tuple[date, ...],
        facts: tuple[ParsedFactRow, ...],
    ) -> PrivateCreditDossierRecord: ...

    def get_dossier(
        self, *, dossier_id: UUID, user_id: UUID
    ) -> PrivateCreditDossierRecord | None: ...

    def list_dossiers(self, *, user_id: UUID) -> list[PrivateCreditDossierRecord]: ...

    def save_score_result(
        self, *, dossier_id: UUID, user_id: UUID, score_json: dict[str, object]
    ) -> PrivateCreditDossierRecord | None: ...

    def delete_dossier(self, *, dossier_id: UUID, user_id: UUID) -> bool: ...


class InMemoryPrivateCreditStore:
    """Thread-safe offline store enforcing strict user_id isolation (zero IDOR)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dossiers: dict[UUID, PrivateCreditDossierRecord] = {}

    def create_dossier(
        self,
        *,
        user_id: UUID,
        company_name: str,
        tax_code: str | None,
        icb_code_level2: str | None,
        statement_scope: StatementScopeStr,
        taxonomy_version: TaxonomyVersionStr,
        unit_multiplier: int,
        source_filename: str,
        source_sha256: str,
        qg1_status: str,
        qg1_report_json: dict[str, object],
        periods: tuple[date, ...],
        facts: tuple[ParsedFactRow, ...],
    ) -> PrivateCreditDossierRecord:
        now = datetime.now(UTC)
        rec = PrivateCreditDossierRecord(
            dossier_id=uuid4(),
            user_id=user_id,
            company_name=company_name,
            tax_code=tax_code,
            icb_code_level2=icb_code_level2,
            statement_scope=statement_scope,
            taxonomy_version=taxonomy_version,
            unit_multiplier=unit_multiplier,
            source_filename=source_filename,
            source_sha256=source_sha256,
            qg1_status=qg1_status,
            qg1_report_json=qg1_report_json,
            periods=periods,
            facts=facts,
            latest_score_json=None,
            retention_expires_at=now + timedelta(days=DEFAULT_RETENTION_DAYS),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._dossiers[rec.dossier_id] = rec
        return rec

    def get_dossier(
        self, *, dossier_id: UUID, user_id: UUID
    ) -> PrivateCreditDossierRecord | None:
        with self._lock:
            rec = self._dossiers.get(dossier_id)
            if rec is None or rec.user_id != user_id:
                return None
            return rec

    def list_dossiers(self, *, user_id: UUID) -> list[PrivateCreditDossierRecord]:
        with self._lock:
            items = [r for r in self._dossiers.values() if r.user_id == user_id]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items

    def save_score_result(
        self, *, dossier_id: UUID, user_id: UUID, score_json: dict[str, object]
    ) -> PrivateCreditDossierRecord | None:
        with self._lock:
            rec = self._dossiers.get(dossier_id)
            if rec is None or rec.user_id != user_id:
                return None
            updated = PrivateCreditDossierRecord(
                dossier_id=rec.dossier_id,
                user_id=rec.user_id,
                company_name=rec.company_name,
                tax_code=rec.tax_code,
                icb_code_level2=rec.icb_code_level2,
                statement_scope=rec.statement_scope,
                taxonomy_version=rec.taxonomy_version,
                unit_multiplier=rec.unit_multiplier,
                source_filename=rec.source_filename,
                source_sha256=rec.source_sha256,
                qg1_status=rec.qg1_status,
                qg1_report_json=rec.qg1_report_json,
                periods=rec.periods,
                facts=rec.facts,
                latest_score_json=score_json,
                retention_expires_at=rec.retention_expires_at,
                created_at=rec.created_at,
                updated_at=datetime.now(UTC),
            )
            self._dossiers[dossier_id] = updated
            return updated

    def delete_dossier(self, *, dossier_id: UUID, user_id: UUID) -> bool:
        with self._lock:
            rec = self._dossiers.get(dossier_id)
            if rec is None or rec.user_id != user_id:
                return False
            del self._dossiers[dossier_id]
            return True


class PostgresPrivateCreditStore:
    """PostgreSQL ``finintel_app`` persistence with strict lazy ``psycopg`` imports."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must not be empty.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    @staticmethod
    def _row_to_record(
        row: tuple[Any, ...], facts: tuple[ParsedFactRow, ...]
    ) -> PrivateCreditDossierRecord:
        raw_periods = row[12] if isinstance(row[12], list) else []
        periods = tuple(date.fromisoformat(str(p)) for p in raw_periods)
        qg1_json = dict(row[11]) if isinstance(row[11], dict) else {}
        score_json = dict(row[13]) if isinstance(row[13], dict) else None
        return PrivateCreditDossierRecord(
            dossier_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            company_name=str(row[2]),
            tax_code=str(row[3]) if row[3] is not None else None,
            icb_code_level2=str(row[4]) if row[4] is not None else None,
            statement_scope=row[5],
            taxonomy_version=row[6],
            unit_multiplier=int(row[7]),
            source_filename=str(row[8]),
            source_sha256=str(row[9]),
            qg1_status=str(row[10]),
            qg1_report_json=qg1_json,
            periods=periods,
            facts=facts,
            latest_score_json=score_json,
            retention_expires_at=row[14],
            created_at=row[15],
            updated_at=row[16],
        )

    def create_dossier(
        self,
        *,
        user_id: UUID,
        company_name: str,
        tax_code: str | None,
        icb_code_level2: str | None,
        statement_scope: StatementScopeStr,
        taxonomy_version: TaxonomyVersionStr,
        unit_multiplier: int,
        source_filename: str,
        source_sha256: str,
        qg1_status: str,
        qg1_report_json: dict[str, object],
        periods: tuple[date, ...],
        facts: tuple[ParsedFactRow, ...],
    ) -> PrivateCreditDossierRecord:
        did = uuid4()
        now = datetime.now(UTC)
        expires = now + timedelta(days=DEFAULT_RETENTION_DAYS)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO private_credit_dossiers (
                        dossier_id, user_id, company_name, tax_code, icb_code_level2,
                        statement_scope, taxonomy_version, unit_multiplier, source_filename,
                        source_sha256, qg1_status, qg1_report_json, periods_json,
                        retention_expires_at, created_at, updated_at
                    ) VALUES (
                        %(dossier_id)s, %(user_id)s, %(company_name)s, %(tax_code)s, %(icb_code_level2)s,
                        %(statement_scope)s, %(taxonomy_version)s, %(unit_multiplier)s, %(source_filename)s,
                        %(source_sha256)s, %(qg1_status)s, %(qg1_report_json)s::jsonb, %(periods_json)s::jsonb,
                        %(retention_expires_at)s, %(created_at)s, %(updated_at)s
                    )
                    """,
                    {
                        "dossier_id": did,
                        "user_id": user_id,
                        "company_name": company_name,
                        "tax_code": tax_code,
                        "icb_code_level2": icb_code_level2,
                        "statement_scope": statement_scope,
                        "taxonomy_version": taxonomy_version,
                        "unit_multiplier": unit_multiplier,
                        "source_filename": source_filename,
                        "source_sha256": source_sha256,
                        "qg1_status": qg1_status,
                        "qg1_report_json": json.dumps(qg1_report_json),
                        "periods_json": json.dumps([p.isoformat() for p in periods]),
                        "retention_expires_at": expires,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                for f in facts:
                    cur.execute(
                        """
                        INSERT INTO private_credit_facts (
                            fact_id, dossier_id, user_id, statement_type, vas_code,
                            metric_id, line_label_vn, period_end_date, fiscal_year, value
                        ) VALUES (
                            %(fact_id)s, %(dossier_id)s, %(user_id)s, %(statement_type)s, %(vas_code)s,
                            %(metric_id)s, %(line_label_vn)s, %(period_end_date)s, %(fiscal_year)s, %(value)s
                        )
                        """,
                        {
                            "fact_id": uuid4(),
                            "dossier_id": did,
                            "user_id": user_id,
                            "statement_type": f.statement_type,
                            "vas_code": f.vas_code,
                            "metric_id": f.metric_id,
                            "line_label_vn": f.line_label_vn,
                            "period_end_date": f.period_end_date,
                            "fiscal_year": f.fiscal_year,
                            "value": f.value,
                        },
                    )
            conn.commit()
        return PrivateCreditDossierRecord(
            dossier_id=did,
            user_id=user_id,
            company_name=company_name,
            tax_code=tax_code,
            icb_code_level2=icb_code_level2,
            statement_scope=statement_scope,
            taxonomy_version=taxonomy_version,
            unit_multiplier=unit_multiplier,
            source_filename=source_filename,
            source_sha256=source_sha256,
            qg1_status=qg1_status,
            qg1_report_json=qg1_report_json,
            periods=periods,
            facts=facts,
            latest_score_json=None,
            retention_expires_at=expires,
            created_at=now,
            updated_at=now,
        )

    def get_dossier(
        self, *, dossier_id: UUID, user_id: UUID
    ) -> PrivateCreditDossierRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dossier_id, user_id, company_name, tax_code, icb_code_level2,
                           statement_scope, taxonomy_version, unit_multiplier, source_filename,
                           source_sha256, qg1_status, qg1_report_json, periods_json,
                           latest_score_json, retention_expires_at, created_at, updated_at
                    FROM private_credit_dossiers
                    WHERE dossier_id = %(dossier_id)s AND user_id = %(user_id)s
                    """,
                    {"dossier_id": dossier_id, "user_id": user_id},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    """
                    SELECT statement_type, vas_code, metric_id, line_label_vn,
                           period_end_date, fiscal_year, value
                    FROM private_credit_facts
                    WHERE dossier_id = %(dossier_id)s AND user_id = %(user_id)s
                    ORDER BY statement_type, vas_code, period_end_date
                    """,
                    {"dossier_id": dossier_id, "user_id": user_id},
                )
                fact_rows = [
                    ParsedFactRow(
                        statement_type=r[0]
                        if r[0] in ("BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW")
                        else "BALANCE_SHEET",
                        vas_code=str(r[1]),
                        metric_id=str(r[2]),
                        line_label_vn=str(r[3]),
                        period_end_date=r[4],
                        fiscal_year=int(r[5]),
                        value=float(r[6]) if r[6] is not None else None,
                    )
                    for r in cur.fetchall()
                ]
        return self._row_to_record(row, tuple(fact_rows))

    def list_dossiers(self, *, user_id: UUID) -> list[PrivateCreditDossierRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dossier_id, user_id, company_name, tax_code, icb_code_level2,
                           statement_scope, taxonomy_version, unit_multiplier, source_filename,
                           source_sha256, qg1_status, qg1_report_json, periods_json,
                           latest_score_json, retention_expires_at, created_at, updated_at
                    FROM private_credit_dossiers
                    WHERE user_id = %(user_id)s
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    {"user_id": user_id},
                )
                rows = cur.fetchall()
        return [self._row_to_record(r, ()) for r in rows]

    def save_score_result(
        self, *, dossier_id: UUID, user_id: UUID, score_json: dict[str, object]
    ) -> PrivateCreditDossierRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE private_credit_dossiers
                    SET latest_score_json = %(score_json)s::jsonb, updated_at = NOW()
                    WHERE dossier_id = %(dossier_id)s AND user_id = %(user_id)s
                    """,
                    {
                        "dossier_id": dossier_id,
                        "user_id": user_id,
                        "score_json": json.dumps(score_json),
                    },
                )
            conn.commit()
        return self.get_dossier(dossier_id=dossier_id, user_id=user_id)

    def delete_dossier(self, *, dossier_id: UUID, user_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM private_credit_dossiers
                    WHERE dossier_id = %(dossier_id)s AND user_id = %(user_id)s
                    """,
                    {"dossier_id": dossier_id, "user_id": user_id},
                )
                deleted = bool(cur.rowcount and cur.rowcount > 0)
            conn.commit()
        return deleted
