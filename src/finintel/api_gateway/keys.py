"""API Key generation, constant-time SHA-256 verification, and store contract (V3-25)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

DEFAULT_RATE_LIMIT_PER_MIN = 30
DEFAULT_DAILY_LIMIT = 1000
VALID_API_SCOPES: frozenset[str] = frozenset(
    {
        "read:derived_risk",
        "write:webhooks",
        "read:rag",
        "read:raw_statements",
    }
)


def hash_api_key(plaintext_key: str) -> str:
    """Compute non-reversible SHA-256 hex digest of an API key."""
    return hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()


def is_well_formed_api_key(plaintext_key: str) -> bool:
    """Validate ``fi_res_<prefix>_<token>`` or ``fi_live_<prefix>_<token>`` structure."""
    if not isinstance(plaintext_key, str):
        return False
    stripped = plaintext_key.strip()
    if len(stripped) < 24 or any(ch.isspace() for ch in stripped):
        return False
    parts = stripped.split("_", 3)
    if len(parts) != 4:
        return False
    brand, mode, prefix_id, secret_part = parts
    return (
        brand == "fi"
        and mode in {"res", "live"}
        and len(prefix_id) == 8
        and len(secret_part) >= 16
    )


def generate_api_key(
    *, prefix_mode: Literal["res", "live"] = "res"
) -> tuple[str, str, str]:
    """Generate ``(plaintext_key, key_prefix, sha256_hash)``. Plaintext is returned once."""
    if prefix_mode not in {"res", "live"}:
        raise ValueError(f"Unsupported key prefix mode: {prefix_mode!r}")
    prefix_id = secrets.token_hex(4)
    secret_part = secrets.token_urlsafe(32)
    key_prefix = f"fi_{prefix_mode}_{prefix_id}"
    plaintext = f"{key_prefix}_{secret_part}"
    return plaintext, key_prefix, hash_api_key(plaintext)


def verify_api_key_hash(plaintext_key: str, stored_sha256_hex: str) -> bool:
    """Verify plaintext key against stored SHA-256 hex digest in constant time."""
    if not plaintext_key or not stored_sha256_hex:
        return False
    candidate_digest = hash_api_key(plaintext_key)
    return hmac.compare_digest(candidate_digest, stored_sha256_hex)


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """Normalize and validate requested scopes against allowlisted values."""
    if not scopes:
        return ("read:derived_risk",)
    cleaned: list[str] = []
    for raw in scopes:
        item = str(raw).strip()
        if item not in VALID_API_SCOPES:
            raise ValueError(f"Unsupported API scope: {item!r}")
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    """Stored API key metadata (never contains plaintext secret)."""

    key_id: UUID
    user_id: UUID
    name: str
    key_prefix: str
    key_hash: str
    scopes: tuple[str, ...]
    rate_limit_per_min: int
    daily_limit: int
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def is_usable(self, now: datetime | None = None) -> bool:
        """Return True iff key is active, unrevoked, and not expired."""
        if not self.is_active or self.revoked_at is not None:
            return False
        if self.expires_at is not None:
            ref = now or datetime.now(UTC)
            if self.expires_at <= ref:
                return False
        return True


@dataclass(frozen=True, slots=True)
class ApiUsageRecord:
    """Daily aggregated API key usage record."""

    usage_date: date
    key_id: UUID
    key_prefix: str
    user_id: UUID
    endpoint: str
    status_class: str
    request_count: int
    cache_hits: int
    last_request_at: datetime


@dataclass(frozen=True, slots=True)
class B2BWebhookRecord:
    """Registered B2B outgoing webhook endpoint."""

    endpoint_id: UUID
    user_id: UUID
    label: str
    url: str
    secret_hmac: str
    event_types: tuple[str, ...]
    is_active: bool
    last_status: int | None
    last_error: str | None
    last_delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApiGatewayStoreProtocol(Protocol):
    """Persistence contract for API keys, daily usage, and B2B webhooks."""

    def create_api_key(
        self,
        *,
        user_id: UUID,
        name: str,
        key_prefix: str,
        key_hash: str,
        scopes: tuple[str, ...],
        rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        expires_at: datetime | None = None,
    ) -> ApiKeyRecord: ...

    def list_api_keys(self, user_id: UUID) -> list[ApiKeyRecord]: ...

    def get_api_key(self, user_id: UUID, key_id: UUID) -> ApiKeyRecord | None: ...

    def revoke_api_key(
        self, user_id: UUID, key_id: UUID, *, revoked_at: datetime
    ) -> ApiKeyRecord | None: ...

    def find_key_by_hash(self, key_hash: str) -> ApiKeyRecord | None: ...

    def touch_key_last_used(self, key_id: UUID, *, used_at: datetime) -> None: ...

    def record_usage(
        self,
        *,
        usage_date: date,
        key_id: UUID,
        user_id: UUID,
        endpoint: str,
        status_code: int,
        cache_hit: bool = False,
        now: datetime | None = None,
    ) -> None: ...

    def list_usage(
        self, user_id: UUID, *, from_date: date, to_date: date
    ) -> list[ApiUsageRecord]: ...

    def create_webhook(
        self,
        *,
        user_id: UUID,
        label: str,
        url: str,
        secret_hmac: str,
        event_types: tuple[str, ...],
    ) -> B2BWebhookRecord: ...

    def list_webhooks(self, user_id: UUID) -> list[B2BWebhookRecord]: ...

    def list_active_webhooks_for_event(
        self, event_type: str
    ) -> list[B2BWebhookRecord]: ...

    def get_webhook(
        self, user_id: UUID, endpoint_id: UUID
    ) -> B2BWebhookRecord | None: ...

    def delete_webhook(self, user_id: UUID, endpoint_id: UUID) -> bool: ...

    def update_webhook_delivery(
        self,
        endpoint_id: UUID,
        *,
        http_status: int | None,
        error: str | None,
        delivered_at: datetime | None,
    ) -> None: ...


def rotate_api_key_record(
    store: ApiGatewayStoreProtocol,
    *,
    user_id: UUID,
    key_id: UUID,
    now: datetime | None = None,
) -> tuple[ApiKeyRecord, str, ApiKeyRecord] | None:
    """Revoke ``key_id`` and issue a replacement key preserving name/scopes/limits."""
    existing = store.get_api_key(user_id, key_id)
    if existing is None:
        return None
    ts = now or datetime.now(UTC)
    prefix_mode: Literal["res", "live"] = (
        "live" if existing.key_prefix.startswith("fi_live_") else "res"
    )
    plaintext, new_prefix, new_hash = generate_api_key(prefix_mode=prefix_mode)
    revoked = store.revoke_api_key(user_id, key_id, revoked_at=ts)
    if revoked is None:
        return None
    remaining_exp: datetime | None = None
    if existing.expires_at is not None and existing.expires_at > ts:
        remaining_exp = ts + timedelta(
            seconds=max(60, int((existing.expires_at - ts).total_seconds()))
        )
    created = store.create_api_key(
        user_id=user_id,
        name=existing.name,
        key_prefix=new_prefix,
        key_hash=new_hash,
        scopes=existing.scopes,
        rate_limit_per_min=existing.rate_limit_per_min,
        daily_limit=existing.daily_limit,
        expires_at=remaining_exp,
    )
    return created, plaintext, revoked


__all__ = [
    "DEFAULT_DAILY_LIMIT",
    "DEFAULT_RATE_LIMIT_PER_MIN",
    "VALID_API_SCOPES",
    "ApiGatewayStoreProtocol",
    "ApiKeyRecord",
    "ApiUsageRecord",
    "B2BWebhookRecord",
    "generate_api_key",
    "hash_api_key",
    "is_well_formed_api_key",
    "normalize_scopes",
    "rotate_api_key_record",
    "verify_api_key_hash",
]
