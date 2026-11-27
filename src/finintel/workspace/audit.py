"""Append-only security & workspace audit logger (V3-11 / Law 91/2025 safe)."""

from __future__ import annotations

from uuid import UUID

from finintel.auth._store import AppStoreProtocol, AuditLogRecord
from finintel.auth.passwords import sha256_hex

# Keys stripped defensively so PII / secrets never enter append-only audit logs.
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_hash",
        "token",
        "reset_token",
        "session_token",
        "cookie",
        "email",
        "display_name",
    }
)


def sanitize_audit_metadata(
    raw: dict[str, object] | None,
) -> dict[str, object]:
    """Strip sensitive keys and coerce values to JSON-safe primitives."""
    if not raw:
        return {}
    cleaned: dict[str, object] = {}
    for key, val in raw.items():
        norm_key = str(key).strip().lower()
        if norm_key in _FORBIDDEN_METADATA_KEYS:
            continue
        if isinstance(val, (str, int, float, bool)) or val is None:
            cleaned[str(key)] = val
        elif isinstance(val, list):
            cleaned[str(key)] = [
                item
                if isinstance(item, (str, int, float, bool)) or item is None
                else str(item)
                for item in val[:50]
            ]
        else:
            cleaned[str(key)] = str(val)
    return cleaned


def record_audit_event(
    store: AppStoreProtocol,
    *,
    actor_user_id: UUID | None,
    actor_role: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    outcome: str = "SUCCESS",
    client_ip: str = "",
    subject_identifier: str = "",
    metadata: dict[str, object] | None = None,
) -> AuditLogRecord:
    """Append an immutable event to ``user_audit_logs``."""
    if outcome not in {"SUCCESS", "FAILURE", "DENIED"}:
        raise ValueError(f"Invalid audit outcome: {outcome!r}")
    if actor_user_id is not None:
        subject_hash = sha256_hex(str(actor_user_id))
    elif subject_identifier:
        subject_hash = sha256_hex(subject_identifier)
    else:
        subject_hash = sha256_hex("anonymous")

    ip_hash = sha256_hex(client_ip) if client_ip else ""
    return store.append_audit_log(
        actor_user_id=actor_user_id,
        actor_subject_hash=subject_hash,
        actor_role=actor_role,
        action=action.strip().upper(),
        resource_type=resource_type.strip(),
        resource_id=resource_id.strip(),
        outcome=outcome,
        ip_hash=ip_hash,
        metadata_json=sanitize_audit_metadata(metadata),
    )


def list_my_audit_logs(
    store: AppStoreProtocol,
    *,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLogRecord], int]:
    """Return paginated audit log events owned strictly by ``user_id``."""
    return store.list_audit_logs_for_user(
        user_id=user_id,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )


def list_ops_audit_logs(
    store: AppStoreProtocol,
    *,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    actor_user_id: UUID | None = None,
) -> tuple[list[AuditLogRecord], int]:
    """Return system-wide audit log events for ``INTERNAL_OPS`` review."""
    clean_action = action.strip().upper() if action and action.strip() else None
    return store.list_audit_logs_ops(
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        action=clean_action,
        actor_user_id=actor_user_id,
    )


__all__ = [
    "list_my_audit_logs",
    "list_ops_audit_logs",
    "record_audit_event",
    "sanitize_audit_metadata",
]
