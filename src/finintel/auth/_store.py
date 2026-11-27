"""Application store protocol, domain records, and offline in-memory implementation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID, uuid4

from finintel.auth.dependencies import UserRole
from finintel.auth.passwords import sha256_hex
from finintel.domain.compliance import ConsentPurpose


class DuplicateResourceError(ValueError):
    """Raised when a unique constraint (email, watchlist name, screen name) collides."""


@dataclass(frozen=True)
class UserRecord:
    user_id: UUID
    email: str
    email_sha256: str
    display_name: str
    password_hash: str
    role: UserRole
    is_active: bool
    email_verified_at: datetime | None
    failed_login_attempts: int
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConsentDbRecord:
    consent_id: UUID
    user_id: UUID
    purpose: ConsentPurpose
    policy_version: str
    granted: bool
    source_channel: str
    recorded_at: datetime
    withdrawn_at: datetime | None


@dataclass(frozen=True)
class SessionRecord:
    session_id: UUID
    user_id: UUID
    token_sha256: str
    user_agent_hash: str | None
    ip_hash: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ResetTokenRecord:
    reset_id: UUID
    user_id: UUID
    token_sha256: str
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class WatchlistRecord:
    watchlist_id: UUID
    user_id: UUID
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalystNoteRecord:
    note_id: UUID
    user_id: UUID
    ticker: str
    period_end_date: date | None
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SavedScreenRecord:
    screen_id: UUID
    user_id: UUID
    name: str
    filters_json: dict[str, object]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AuditLogRecord:
    audit_id: int
    actor_user_id: UUID | None
    actor_subject_hash: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip_hash: str
    metadata_json: dict[str, object]
    created_at: datetime


class AppStoreProtocol(Protocol):
    """Persistence contract implemented by ``PostgresAppStore`` and ``InMemoryAppStore``."""

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
    ) -> UserRecord: ...

    def get_user_by_email(self, email: str) -> UserRecord | None: ...
    def get_user_by_id(self, user_id: UUID) -> UserRecord | None: ...
    def update_user_profile(
        self, *, user_id: UUID, display_name: str
    ) -> UserRecord | None: ...
    def update_user_password(self, *, user_id: UUID, password_hash: str) -> None: ...
    def record_failed_login(
        self, *, user_id: UUID, attempts: int, locked_until: datetime | None
    ) -> None: ...
    def clear_failed_logins(self, *, user_id: UUID) -> None: ...
    def list_user_consents(self, user_id: UUID) -> list[ConsentDbRecord]: ...
    def upsert_consent(self, record: ConsentDbRecord) -> None: ...
    def create_session(self, session: SessionRecord) -> None: ...
    def get_session_by_token_hash(self, token_sha256: str) -> SessionRecord | None: ...
    def touch_session(self, session_id: UUID, seen_at: datetime) -> None: ...
    def list_active_sessions(
        self, user_id: UUID, now: datetime
    ) -> list[SessionRecord]: ...
    def revoke_session(
        self, *, user_id: UUID, session_id: UUID, revoked_at: datetime
    ) -> bool: ...
    def revoke_all_sessions(self, *, user_id: UUID, revoked_at: datetime) -> int: ...
    def create_reset_token(self, token: ResetTokenRecord) -> None: ...
    def get_reset_token_by_hash(self, token_sha256: str) -> ResetTokenRecord | None: ...
    def mark_reset_token_used(self, reset_id: UUID, used_at: datetime) -> None: ...
    def erase_user_and_anonymize_audit(
        self, *, user_id: UUID, erased_at: datetime
    ) -> None: ...

    def list_watchlists(self, user_id: UUID) -> list[WatchlistRecord]: ...
    def create_watchlist(
        self, *, user_id: UUID, name: str, is_default: bool = False
    ) -> WatchlistRecord: ...
    def update_watchlist(
        self, *, user_id: UUID, watchlist_id: UUID, name: str
    ) -> WatchlistRecord | None: ...
    def delete_watchlist(self, *, user_id: UUID, watchlist_id: UUID) -> bool: ...
    def add_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool: ...
    def remove_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool: ...

    def list_notes(
        self, *, user_id: UUID, ticker: str, limit: int, offset: int
    ) -> list[AnalystNoteRecord]: ...
    def count_notes(self, *, user_id: UUID) -> int: ...
    def create_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        period_end_date: date | None,
        title: str,
        content: str,
    ) -> AnalystNoteRecord: ...
    def update_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        note_id: UUID,
        title: str | None,
        content: str | None,
        period_end_date: date | None,
        clear_period_end_date: bool = False,
    ) -> AnalystNoteRecord | None: ...
    def delete_note(self, *, user_id: UUID, ticker: str, note_id: UUID) -> bool: ...

    def list_saved_screens(self, user_id: UUID) -> list[SavedScreenRecord]: ...
    def create_saved_screen(
        self, *, user_id: UUID, name: str, filters_json: dict[str, object]
    ) -> SavedScreenRecord: ...
    def delete_saved_screen(self, *, user_id: UUID, screen_id: UUID) -> bool: ...

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
    ) -> AuditLogRecord: ...
    def list_audit_logs_for_user(
        self, *, user_id: UUID, limit: int, offset: int
    ) -> tuple[list[AuditLogRecord], int]: ...
    def list_audit_logs_ops(
        self,
        *,
        limit: int,
        offset: int,
        action: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> tuple[list[AuditLogRecord], int]: ...


class InMemoryAppStore:
    """Thread-safe stateful offline implementation of ``AppStoreProtocol``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[UUID, UserRecord] = {}
        self._consents: dict[UUID, list[ConsentDbRecord]] = {}
        self._sessions: dict[UUID, SessionRecord] = {}
        self._reset_tokens: dict[UUID, ResetTokenRecord] = {}
        self._watchlists: dict[UUID, WatchlistRecord] = {}
        self._watchlist_items: dict[UUID, list[str]] = {}
        self._notes: dict[UUID, AnalystNoteRecord] = {}
        self._saved_screens: dict[UUID, SavedScreenRecord] = {}
        self._audit_logs: list[AuditLogRecord] = []
        self._next_audit_id = 1

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
        now = datetime.now(UTC)
        with self._lock:
            for existing in self._users.values():
                if existing.email == email or existing.email_sha256 == email_sha256:
                    raise DuplicateResourceError("Email already registered.")
            user = UserRecord(
                user_id=user_id,
                email=email,
                email_sha256=email_sha256,
                display_name=display_name,
                password_hash=password_hash,
                role=role,
                is_active=True,
                email_verified_at=None,
                failed_login_attempts=0,
                locked_until=None,
                created_at=now,
                updated_at=now,
            )
            self._users[user_id] = user
            self._consents[user_id] = list(consents)
            return user

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._lock:
            for user in self._users.values():
                if user.email == email:
                    return user
            return None

    def get_user_by_id(self, user_id: UUID) -> UserRecord | None:
        with self._lock:
            return self._users.get(user_id)

    def update_user_profile(
        self, *, user_id: UUID, display_name: str
    ) -> UserRecord | None:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            updated = UserRecord(
                **{
                    **user.__dict__,
                    "display_name": display_name,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._users[user_id] = updated
            return updated

    def update_user_password(self, *, user_id: UUID, password_hash: str) -> None:
        with self._lock:
            user = self._users.get(user_id)
            if user is not None:
                self._users[user_id] = UserRecord(
                    **{
                        **user.__dict__,
                        "password_hash": password_hash,
                        "failed_login_attempts": 0,
                        "locked_until": None,
                        "updated_at": datetime.now(UTC),
                    }
                )

    def record_failed_login(
        self, *, user_id: UUID, attempts: int, locked_until: datetime | None
    ) -> None:
        with self._lock:
            user = self._users.get(user_id)
            if user is not None:
                self._users[user_id] = UserRecord(
                    **{
                        **user.__dict__,
                        "failed_login_attempts": attempts,
                        "locked_until": locked_until,
                        "updated_at": datetime.now(UTC),
                    }
                )

    def clear_failed_logins(self, *, user_id: UUID) -> None:
        with self._lock:
            user = self._users.get(user_id)
            if user is not None:
                self._users[user_id] = UserRecord(
                    **{
                        **user.__dict__,
                        "failed_login_attempts": 0,
                        "locked_until": None,
                        "updated_at": datetime.now(UTC),
                    }
                )

    def list_user_consents(self, user_id: UUID) -> list[ConsentDbRecord]:
        with self._lock:
            latest: dict[ConsentPurpose, ConsentDbRecord] = {}
            for rec in self._consents.get(user_id, []):
                latest[rec.purpose] = rec
            return list(latest.values())

    def upsert_consent(self, record: ConsentDbRecord) -> None:
        with self._lock:
            self._consents.setdefault(record.user_id, []).append(record)

    def create_session(self, session: SessionRecord) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get_session_by_token_hash(self, token_sha256: str) -> SessionRecord | None:
        with self._lock:
            for sess in self._sessions.values():
                if sess.token_sha256 == token_sha256:
                    return sess
            return None

    def touch_session(self, session_id: UUID, seen_at: datetime) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None:
                self._sessions[session_id] = SessionRecord(
                    **{**sess.__dict__, "last_seen_at": seen_at}
                )

    def list_active_sessions(self, user_id: UUID, now: datetime) -> list[SessionRecord]:
        with self._lock:
            return sorted(
                [
                    s
                    for s in self._sessions.values()
                    if s.user_id == user_id
                    and s.revoked_at is None
                    and s.expires_at > now
                ],
                key=lambda s: s.created_at,
                reverse=True,
            )

    def revoke_session(
        self, *, user_id: UUID, session_id: UUID, revoked_at: datetime
    ) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None or sess.user_id != user_id or sess.revoked_at is not None:
                return False
            self._sessions[session_id] = SessionRecord(
                **{**sess.__dict__, "revoked_at": revoked_at}
            )
            return True

    def revoke_all_sessions(self, *, user_id: UUID, revoked_at: datetime) -> int:
        with self._lock:
            count = 0
            for sid, sess in list(self._sessions.items()):
                if sess.user_id == user_id and sess.revoked_at is None:
                    self._sessions[sid] = SessionRecord(
                        **{**sess.__dict__, "revoked_at": revoked_at}
                    )
                    count += 1
            return count

    def create_reset_token(self, token: ResetTokenRecord) -> None:
        with self._lock:
            self._reset_tokens[token.reset_id] = token

    def get_reset_token_by_hash(self, token_sha256: str) -> ResetTokenRecord | None:
        with self._lock:
            for tok in self._reset_tokens.values():
                if tok.token_sha256 == token_sha256:
                    return tok
            return None

    def mark_reset_token_used(self, reset_id: UUID, used_at: datetime) -> None:
        with self._lock:
            tok = self._reset_tokens.get(reset_id)
            if tok is not None:
                self._reset_tokens[reset_id] = ResetTokenRecord(
                    **{**tok.__dict__, "used_at": used_at}
                )

    def erase_user_and_anonymize_audit(
        self, *, user_id: UUID, erased_at: datetime
    ) -> None:
        _ = erased_at
        erased_hash = sha256_hex(f"erased:{user_id}")
        with self._lock:
            self._users.pop(user_id, None)
            self._consents.pop(user_id, None)
            for sid in [k for k, v in self._sessions.items() if v.user_id == user_id]:
                self._sessions.pop(sid, None)
            for rid in [
                k for k, v in self._reset_tokens.items() if v.user_id == user_id
            ]:
                self._reset_tokens.pop(rid, None)
            for wid in [k for k, v in self._watchlists.items() if v.user_id == user_id]:
                self._watchlists.pop(wid, None)
                self._watchlist_items.pop(wid, None)
            for nid in [k for k, v in self._notes.items() if v.user_id == user_id]:
                self._notes.pop(nid, None)
            for scid in [
                k for k, v in self._saved_screens.items() if v.user_id == user_id
            ]:
                self._saved_screens.pop(scid, None)
            for idx, entry in enumerate(self._audit_logs):
                if entry.actor_user_id == user_id:
                    self._audit_logs[idx] = AuditLogRecord(
                        audit_id=entry.audit_id,
                        actor_user_id=None,
                        actor_subject_hash=erased_hash,
                        actor_role="ERASED",
                        action=entry.action,
                        resource_type=entry.resource_type,
                        resource_id=entry.resource_id,
                        outcome=entry.outcome,
                        ip_hash="",
                        metadata_json={"erased": True},
                        created_at=entry.created_at,
                    )

    def list_watchlists(self, user_id: UUID) -> list[WatchlistRecord]:
        with self._lock:
            rows = [w for w in self._watchlists.values() if w.user_id == user_id]
            rows.sort(key=lambda w: (not w.is_default, w.created_at))
            return [
                WatchlistRecord(
                    watchlist_id=w.watchlist_id,
                    user_id=w.user_id,
                    name=w.name,
                    is_default=w.is_default,
                    created_at=w.created_at,
                    updated_at=w.updated_at,
                    items=tuple(self._watchlist_items.get(w.watchlist_id, [])),
                )
                for w in rows
            ]

    def create_watchlist(
        self, *, user_id: UUID, name: str, is_default: bool = False
    ) -> WatchlistRecord:
        now = datetime.now(UTC)
        with self._lock:
            for existing in self._watchlists.values():
                if (
                    existing.user_id == user_id
                    and existing.name.lower() == name.lower()
                ):
                    raise DuplicateResourceError("Watchlist name already exists.")
            wl = WatchlistRecord(
                watchlist_id=uuid4(),
                user_id=user_id,
                name=name,
                is_default=is_default,
                created_at=now,
                updated_at=now,
                items=(),
            )
            self._watchlists[wl.watchlist_id] = wl
            self._watchlist_items[wl.watchlist_id] = []
            return wl

    def update_watchlist(
        self, *, user_id: UUID, watchlist_id: UUID, name: str
    ) -> WatchlistRecord | None:
        with self._lock:
            wl = self._watchlists.get(watchlist_id)
            if wl is None or wl.user_id != user_id:
                return None
            for existing in self._watchlists.values():
                if (
                    existing.user_id == user_id
                    and existing.watchlist_id != watchlist_id
                    and existing.name.lower() == name.lower()
                ):
                    raise DuplicateResourceError("Watchlist name already exists.")
            updated = WatchlistRecord(
                watchlist_id=wl.watchlist_id,
                user_id=wl.user_id,
                name=name,
                is_default=wl.is_default,
                created_at=wl.created_at,
                updated_at=datetime.now(UTC),
                items=tuple(self._watchlist_items.get(watchlist_id, [])),
            )
            self._watchlists[watchlist_id] = updated
            return updated

    def delete_watchlist(self, *, user_id: UUID, watchlist_id: UUID) -> bool:
        with self._lock:
            wl = self._watchlists.get(watchlist_id)
            if wl is None or wl.user_id != user_id:
                return False
            self._watchlists.pop(watchlist_id, None)
            self._watchlist_items.pop(watchlist_id, None)
            return True

    def add_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool:
        with self._lock:
            wl = self._watchlists.get(watchlist_id)
            if wl is None or wl.user_id != user_id:
                return False
            items = self._watchlist_items.setdefault(watchlist_id, [])
            if ticker in items:
                return False
            items.append(ticker)
            return True

    def remove_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool:
        with self._lock:
            wl = self._watchlists.get(watchlist_id)
            if wl is None or wl.user_id != user_id:
                return False
            items = self._watchlist_items.get(watchlist_id, [])
            if ticker not in items:
                return False
            items.remove(ticker)
            return True

    def list_notes(
        self, *, user_id: UUID, ticker: str, limit: int, offset: int
    ) -> list[AnalystNoteRecord]:
        with self._lock:
            rows = [
                n
                for n in self._notes.values()
                if n.user_id == user_id and n.ticker == ticker
            ]
            rows.sort(key=lambda n: n.updated_at, reverse=True)
            return rows[offset : offset + limit]

    def count_notes(self, *, user_id: UUID) -> int:
        with self._lock:
            return sum(1 for n in self._notes.values() if n.user_id == user_id)

    def create_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        period_end_date: date | None,
        title: str,
        content: str,
    ) -> AnalystNoteRecord:
        now = datetime.now(UTC)
        with self._lock:
            note = AnalystNoteRecord(
                note_id=uuid4(),
                user_id=user_id,
                ticker=ticker,
                period_end_date=period_end_date,
                title=title,
                content=content,
                created_at=now,
                updated_at=now,
            )
            self._notes[note.note_id] = note
            return note

    def update_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        note_id: UUID,
        title: str | None,
        content: str | None,
        period_end_date: date | None,
        clear_period_end_date: bool = False,
    ) -> AnalystNoteRecord | None:
        with self._lock:
            note = self._notes.get(note_id)
            if note is None or note.user_id != user_id or note.ticker != ticker:
                return None
            next_period = (
                None
                if clear_period_end_date
                else (
                    period_end_date
                    if period_end_date is not None
                    else note.period_end_date
                )
            )
            updated = AnalystNoteRecord(
                note_id=note.note_id,
                user_id=note.user_id,
                ticker=note.ticker,
                period_end_date=next_period,
                title=title if title is not None else note.title,
                content=content if content is not None else note.content,
                created_at=note.created_at,
                updated_at=datetime.now(UTC),
            )
            self._notes[note_id] = updated
            return updated

    def delete_note(self, *, user_id: UUID, ticker: str, note_id: UUID) -> bool:
        with self._lock:
            note = self._notes.get(note_id)
            if note is None or note.user_id != user_id or note.ticker != ticker:
                return False
            self._notes.pop(note_id, None)
            return True

    def list_saved_screens(self, user_id: UUID) -> list[SavedScreenRecord]:
        with self._lock:
            rows = [s for s in self._saved_screens.values() if s.user_id == user_id]
            rows.sort(key=lambda s: s.updated_at, reverse=True)
            return rows

    def create_saved_screen(
        self, *, user_id: UUID, name: str, filters_json: dict[str, object]
    ) -> SavedScreenRecord:
        now = datetime.now(UTC)
        with self._lock:
            for existing in self._saved_screens.values():
                if (
                    existing.user_id == user_id
                    and existing.name.lower() == name.lower()
                ):
                    updated = SavedScreenRecord(
                        screen_id=existing.screen_id,
                        user_id=user_id,
                        name=name,
                        filters_json=dict(filters_json),
                        created_at=existing.created_at,
                        updated_at=now,
                    )
                    self._saved_screens[existing.screen_id] = updated
                    return updated
            screen = SavedScreenRecord(
                screen_id=uuid4(),
                user_id=user_id,
                name=name,
                filters_json=dict(filters_json),
                created_at=now,
                updated_at=now,
            )
            self._saved_screens[screen.screen_id] = screen
            return screen

    def delete_saved_screen(self, *, user_id: UUID, screen_id: UUID) -> bool:
        with self._lock:
            screen = self._saved_screens.get(screen_id)
            if screen is None or screen.user_id != user_id:
                return False
            self._saved_screens.pop(screen_id, None)
            return True

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
        with self._lock:
            rec = AuditLogRecord(
                audit_id=self._next_audit_id,
                actor_user_id=actor_user_id,
                actor_subject_hash=actor_subject_hash,
                actor_role=actor_role,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                ip_hash=ip_hash,
                metadata_json=dict(metadata_json),
                created_at=datetime.now(UTC),
            )
            self._next_audit_id += 1
            self._audit_logs.append(rec)
            return rec

    def list_audit_logs_for_user(
        self, *, user_id: UUID, limit: int, offset: int
    ) -> tuple[list[AuditLogRecord], int]:
        with self._lock:
            rows = [r for r in reversed(self._audit_logs) if r.actor_user_id == user_id]
            return rows[offset : offset + limit], len(rows)

    def list_audit_logs_ops(
        self,
        *,
        limit: int,
        offset: int,
        action: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> tuple[list[AuditLogRecord], int]:
        with self._lock:
            rows = list(reversed(self._audit_logs))
            if action:
                rows = [r for r in rows if r.action == action]
            if actor_user_id is not None:
                rows = [r for r in rows if r.actor_user_id == actor_user_id]
            return rows[offset : offset + limit], len(rows)


__all__ = [
    "AnalystNoteRecord",
    "AppStoreProtocol",
    "AuditLogRecord",
    "ConsentDbRecord",
    "DuplicateResourceError",
    "InMemoryAppStore",
    "ResetTokenRecord",
    "SavedScreenRecord",
    "SessionRecord",
    "UserRecord",
    "WatchlistRecord",
]
