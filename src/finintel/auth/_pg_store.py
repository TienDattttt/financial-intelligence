"""PostgreSQL ``finintel_app`` persistence store with strict lazy ``psycopg`` imports."""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime
from typing import Any
from uuid import UUID

from finintel.auth._store import (
    AuditLogRecord,
    ConsentDbRecord,
    DuplicateResourceError,
    ResetTokenRecord,
    SessionRecord,
    UserRecord,
)
from finintel.auth.dependencies import UserRole
from finintel.auth.passwords import sha256_hex
from finintel.domain.compliance import ConsentPurpose
from finintel.workspace._pg_workspace import PostgresWorkspaceMixin

DEFAULT_PG_CONNECT_TIMEOUT = 3


def resolve_app_pg_dsn(explicit: str | None = None) -> str | None:
    """Resolve ``finintel_app`` PostgreSQL DSN if configured, else ``None``."""
    raw = (
        explicit.strip()
        if explicit is not None
        else os.getenv("FININTEL_APP_PG_DSN", "").strip()
    )
    if not raw:
        return None
    if "connect_timeout=" not in raw and "://" in raw:
        sep = "&" if "?" in raw else "?"
        raw = f"{raw}{sep}connect_timeout={DEFAULT_PG_CONNECT_TIMEOUT}"
    return raw


class PostgresAppStore(PostgresWorkspaceMixin):
    """PostgreSQL implementation of ``AppStoreProtocol`` backed by ``finintel_app``."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must be a non-empty PostgreSQL DSN.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    @staticmethod
    def _row_to_user(row: tuple[Any, ...]) -> UserRecord:
        return UserRecord(
            user_id=UUID(str(row[0])),
            email=str(row[1]),
            email_sha256=str(row[2]),
            display_name=str(row[3]),
            password_hash=str(row[4]),
            role=UserRole(str(row[5])),
            is_active=bool(row[6]),
            email_verified_at=row[7],
            failed_login_attempts=int(row[8]),
            locked_until=row[9],
            created_at=row[10],
            updated_at=row[11],
        )

    @staticmethod
    def _row_to_audit(row: tuple[Any, ...]) -> AuditLogRecord:
        return AuditLogRecord(
            audit_id=int(row[0]),
            actor_user_id=UUID(str(row[1])) if row[1] is not None else None,
            actor_subject_hash=str(row[2]),
            actor_role=str(row[3]),
            action=str(row[4]),
            resource_type=str(row[5]),
            resource_id=str(row[6]),
            outcome=str(row[7]),
            ip_hash=str(row[8]),
            metadata_json=dict(row[9]) if isinstance(row[9], dict) else {},
            created_at=row[10],
        )

    def create_user(
        self,
        *,
        user_id: UUID,
        email: str,
        email_sha256: str,
        display_name: str,
        password_hash: str,
        role: UserRole,
        consents: list[ConsentDbRecord],
    ) -> UserRecord:
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (user_id, email, email_sha256, display_name, password_hash, role)
                        VALUES (%(user_id)s, %(email)s, %(email_sha256)s, %(display_name)s, %(password_hash)s, %(role)s)
                        RETURNING user_id, email, email_sha256, display_name, password_hash, role,
                                  is_active, email_verified_at, failed_login_attempts, locked_until,
                                  created_at, updated_at
                        """,
                        {
                            "user_id": user_id,
                            "email": email,
                            "email_sha256": email_sha256,
                            "display_name": display_name,
                            "password_hash": password_hash,
                            "role": role.value,
                        },
                    )
                    row = cur.fetchone()
                    for c in consents:
                        cur.execute(
                            """
                            INSERT INTO user_consents (
                                consent_id, user_id, purpose, policy_version, granted,
                                source_channel, recorded_at, withdrawn_at
                            )
                            VALUES (
                                %(consent_id)s, %(user_id)s, %(purpose)s, %(policy_version)s,
                                %(granted)s, %(source_channel)s, %(recorded_at)s, %(withdrawn_at)s
                            )
                            """,
                            {
                                "consent_id": c.consent_id,
                                "user_id": c.user_id,
                                "purpose": c.purpose.value,
                                "policy_version": c.policy_version,
                                "granted": c.granted,
                                "source_channel": c.source_channel,
                                "recorded_at": c.recorded_at,
                                "withdrawn_at": c.withdrawn_at,
                            },
                        )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if self._is_unique_violation(exc):
                    raise DuplicateResourceError("Email already registered.") from exc
                raise
        assert row is not None
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, email, email_sha256, display_name, password_hash, role,
                           is_active, email_verified_at, failed_login_attempts, locked_until,
                           created_at, updated_at
                    FROM users
                    WHERE email = %(email)s AND deleted_at IS NULL
                    """,
                    {"email": email},
                )
                row = cur.fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_user_by_id(self, user_id: UUID) -> UserRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, email, email_sha256, display_name, password_hash, role,
                           is_active, email_verified_at, failed_login_attempts, locked_until,
                           created_at, updated_at
                    FROM users
                    WHERE user_id = %(user_id)s AND deleted_at IS NULL
                    """,
                    {"user_id": user_id},
                )
                row = cur.fetchone()
        return self._row_to_user(row) if row is not None else None

    def update_user_profile(
        self, *, user_id: UUID, display_name: str
    ) -> UserRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET display_name = %(display_name)s, updated_at = NOW()
                    WHERE user_id = %(user_id)s AND deleted_at IS NULL
                    RETURNING user_id, email, email_sha256, display_name, password_hash, role,
                              is_active, email_verified_at, failed_login_attempts, locked_until,
                              created_at, updated_at
                    """,
                    {"user_id": user_id, "display_name": display_name},
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_user(row) if row is not None else None

    def update_user_password(self, *, user_id: UUID, password_hash: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %(password_hash)s,
                        failed_login_attempts = 0,
                        locked_until = NULL,
                        updated_at = NOW()
                    WHERE user_id = %(user_id)s
                    """,
                    {"user_id": user_id, "password_hash": password_hash},
                )
            conn.commit()

    def record_failed_login(
        self, *, user_id: UUID, attempts: int, locked_until: datetime | None
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET failed_login_attempts = %(attempts)s,
                        locked_until = %(locked_until)s,
                        updated_at = NOW()
                    WHERE user_id = %(user_id)s
                    """,
                    {
                        "user_id": user_id,
                        "attempts": attempts,
                        "locked_until": locked_until,
                    },
                )
            conn.commit()

    def clear_failed_logins(self, *, user_id: UUID) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET failed_login_attempts = 0, locked_until = NULL, updated_at = NOW()
                    WHERE user_id = %(user_id)s
                    """,
                    {"user_id": user_id},
                )
            conn.commit()

    def list_user_consents(self, user_id: UUID) -> list[ConsentDbRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (purpose)
                           consent_id, user_id, purpose, policy_version, granted,
                           source_channel, recorded_at, withdrawn_at
                    FROM user_consents
                    WHERE user_id = %(user_id)s
                    ORDER BY purpose, recorded_at DESC
                    """,
                    {"user_id": user_id},
                )
                rows = cur.fetchall()
        return [
            ConsentDbRecord(
                consent_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])),
                purpose=ConsentPurpose(str(r[2])),
                policy_version=str(r[3]),
                granted=bool(r[4]),
                source_channel=str(r[5]),
                recorded_at=r[6],
                withdrawn_at=r[7],
            )
            for r in rows
        ]

    def upsert_consent(self, record: ConsentDbRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_consents (
                        consent_id, user_id, purpose, policy_version, granted,
                        source_channel, recorded_at, withdrawn_at
                    )
                    VALUES (
                        %(consent_id)s, %(user_id)s, %(purpose)s, %(policy_version)s,
                        %(granted)s, %(source_channel)s, %(recorded_at)s, %(withdrawn_at)s
                    )
                    """,
                    {
                        "consent_id": record.consent_id,
                        "user_id": record.user_id,
                        "purpose": record.purpose.value,
                        "policy_version": record.policy_version,
                        "granted": record.granted,
                        "source_channel": record.source_channel,
                        "recorded_at": record.recorded_at,
                        "withdrawn_at": record.withdrawn_at,
                    },
                )
            conn.commit()

    def create_session(self, session: SessionRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_sessions (
                        session_id, user_id, token_sha256, user_agent_hash, ip_hash,
                        created_at, last_seen_at, expires_at, revoked_at
                    )
                    VALUES (
                        %(session_id)s, %(user_id)s, %(token_sha256)s, %(user_agent_hash)s,
                        %(ip_hash)s, %(created_at)s, %(last_seen_at)s, %(expires_at)s, %(revoked_at)s
                    )
                    """,
                    {
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                        "token_sha256": session.token_sha256,
                        "user_agent_hash": session.user_agent_hash,
                        "ip_hash": session.ip_hash,
                        "created_at": session.created_at,
                        "last_seen_at": session.last_seen_at,
                        "expires_at": session.expires_at,
                        "revoked_at": session.revoked_at,
                    },
                )
            conn.commit()

    def get_session_by_token_hash(self, token_sha256: str) -> SessionRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, user_id, token_sha256, user_agent_hash, ip_hash,
                           created_at, last_seen_at, expires_at, revoked_at
                    FROM user_sessions
                    WHERE token_sha256 = %(token_sha256)s
                    """,
                    {"token_sha256": token_sha256},
                )
                r = cur.fetchone()
        if r is None:
            return None
        return SessionRecord(
            session_id=UUID(str(r[0])),
            user_id=UUID(str(r[1])),
            token_sha256=str(r[2]),
            user_agent_hash=str(r[3]) if r[3] is not None else None,
            ip_hash=str(r[4]) if r[4] is not None else None,
            created_at=r[5],
            last_seen_at=r[6],
            expires_at=r[7],
            revoked_at=r[8],
        )

    def touch_session(self, session_id: UUID, seen_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE user_sessions SET last_seen_at = %(seen_at)s WHERE session_id = %(session_id)s",
                    {"session_id": session_id, "seen_at": seen_at},
                )
            conn.commit()

    def list_active_sessions(self, user_id: UUID, now: datetime) -> list[SessionRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, user_id, token_sha256, user_agent_hash, ip_hash,
                           created_at, last_seen_at, expires_at, revoked_at
                    FROM user_sessions
                    WHERE user_id = %(user_id)s
                      AND revoked_at IS NULL
                      AND expires_at > %(now)s
                    ORDER BY created_at DESC
                    """,
                    {"user_id": user_id, "now": now},
                )
                rows = cur.fetchall()
        return [
            SessionRecord(
                session_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])),
                token_sha256=str(r[2]),
                user_agent_hash=str(r[3]) if r[3] is not None else None,
                ip_hash=str(r[4]) if r[4] is not None else None,
                created_at=r[5],
                last_seen_at=r[6],
                expires_at=r[7],
                revoked_at=r[8],
            )
            for r in rows
        ]

    def revoke_session(
        self, *, user_id: UUID, session_id: UUID, revoked_at: datetime
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = %(revoked_at)s
                    WHERE session_id = %(session_id)s
                      AND user_id = %(user_id)s
                      AND revoked_at IS NULL
                    """,
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "revoked_at": revoked_at,
                    },
                )
                revoked = int(cur.rowcount or 0) > 0
            conn.commit()
        return revoked

    def revoke_all_sessions(self, *, user_id: UUID, revoked_at: datetime) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = %(revoked_at)s
                    WHERE user_id = %(user_id)s AND revoked_at IS NULL
                    """,
                    {"user_id": user_id, "revoked_at": revoked_at},
                )
                count = int(cur.rowcount or 0)
            conn.commit()
        return count

    def create_reset_token(self, token: ResetTokenRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO password_reset_tokens (reset_id, user_id, token_sha256, expires_at, used_at, created_at)
                    VALUES (%(reset_id)s, %(user_id)s, %(token_sha256)s, %(expires_at)s, %(used_at)s, %(created_at)s)
                    """,
                    {
                        "reset_id": token.reset_id,
                        "user_id": token.user_id,
                        "token_sha256": token.token_sha256,
                        "expires_at": token.expires_at,
                        "used_at": token.used_at,
                        "created_at": token.created_at,
                    },
                )
            conn.commit()

    def get_reset_token_by_hash(self, token_sha256: str) -> ResetTokenRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT reset_id, user_id, token_sha256, expires_at, used_at, created_at
                    FROM password_reset_tokens
                    WHERE token_sha256 = %(token_sha256)s
                    """,
                    {"token_sha256": token_sha256},
                )
                r = cur.fetchone()
        if r is None:
            return None
        return ResetTokenRecord(
            reset_id=UUID(str(r[0])),
            user_id=UUID(str(r[1])),
            token_sha256=str(r[2]),
            expires_at=r[3],
            used_at=r[4],
            created_at=r[5],
        )

    def mark_reset_token_used(self, reset_id: UUID, used_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE password_reset_tokens SET used_at = %(used_at)s WHERE reset_id = %(reset_id)s",
                    {"reset_id": reset_id, "used_at": used_at},
                )
            conn.commit()

    def erase_user_and_anonymize_audit(
        self, *, user_id: UUID, erased_at: datetime
    ) -> None:
        _ = erased_at
        erased_hash = sha256_hex(f"erased:{user_id}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_audit_logs
                    SET actor_user_id = NULL,
                        actor_subject_hash = %(erased_hash)s,
                        actor_role = 'ERASED',
                        ip_hash = '',
                        metadata_json = '{"erased": true}'::jsonb
                    WHERE actor_user_id = %(user_id)s
                    """,
                    {"user_id": user_id, "erased_hash": erased_hash},
                )
                cur.execute(
                    "DELETE FROM users WHERE user_id = %(user_id)s",
                    {"user_id": user_id},
                )
            conn.commit()

    def append_audit_log(
        self,
        *,
        actor_user_id: UUID | None,
        actor_subject_hash: str,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        ip_hash: str,
        metadata_json: dict[str, object],
    ) -> AuditLogRecord:
        payload = json.dumps(metadata_json, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_audit_logs (
                        actor_user_id, actor_subject_hash, actor_role, action,
                        resource_type, resource_id, outcome, ip_hash, metadata_json
                    )
                    VALUES (
                        %(actor_user_id)s, %(actor_subject_hash)s, %(actor_role)s, %(action)s,
                        %(resource_type)s, %(resource_id)s, %(outcome)s, %(ip_hash)s, %(metadata_json)s::jsonb
                    )
                    RETURNING audit_id, actor_user_id, actor_subject_hash, actor_role, action,
                              resource_type, resource_id, outcome, ip_hash, metadata_json, created_at
                    """,
                    {
                        "actor_user_id": actor_user_id,
                        "actor_subject_hash": actor_subject_hash,
                        "actor_role": actor_role,
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "outcome": outcome,
                        "ip_hash": ip_hash,
                        "metadata_json": payload,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return self._row_to_audit(row)

    def list_audit_logs_for_user(
        self, *, user_id: UUID, limit: int, offset: int
    ) -> tuple[list[AuditLogRecord], int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM user_audit_logs WHERE actor_user_id = %(user_id)s",
                    {"user_id": user_id},
                )
                count_row = cur.fetchone()
                total = int(count_row[0]) if count_row else 0
                cur.execute(
                    """
                    SELECT audit_id, actor_user_id, actor_subject_hash, actor_role, action,
                           resource_type, resource_id, outcome, ip_hash, metadata_json, created_at
                    FROM user_audit_logs
                    WHERE actor_user_id = %(user_id)s
                    ORDER BY created_at DESC, audit_id DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    {"user_id": user_id, "limit": limit, "offset": offset},
                )
                rows = cur.fetchall()
        return [self._row_to_audit(r) for r in rows], total

    def list_audit_logs_ops(
        self,
        *,
        limit: int,
        offset: int,
        action: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> tuple[list[AuditLogRecord], int]:
        clauses: list[str] = ["1=1"]
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if action:
            clauses.append("action = %(action)s")
            params["action"] = action
        if actor_user_id is not None:
            clauses.append("actor_user_id = %(actor_user_id)s")
            params["actor_user_id"] = actor_user_id
        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM user_audit_logs WHERE {where_sql}",
                    params,
                )
                count_row = cur.fetchone()
                total = int(count_row[0]) if count_row else 0
                cur.execute(
                    f"""
                    SELECT audit_id, actor_user_id, actor_subject_hash, actor_role, action,
                           resource_type, resource_id, outcome, ip_hash, metadata_json, created_at
                    FROM user_audit_logs
                    WHERE {where_sql}
                    ORDER BY created_at DESC, audit_id DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    params,
                )
                rows = cur.fetchall()
        return [self._row_to_audit(r) for r in rows], total


__all__ = ["PostgresAppStore", "resolve_app_pg_dsn"]
