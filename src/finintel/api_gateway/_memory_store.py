"""In-memory B2B API gateway store for offline mode and unit tests (V3-25)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from finintel.api_gateway.keys import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_RATE_LIMIT_PER_MIN,
    ApiKeyRecord,
    ApiUsageRecord,
    B2BWebhookRecord,
)


class InMemoryApiGatewayStore:
    """Thread-safe offline store for API keys, usage, and B2B webhooks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[UUID, ApiKeyRecord] = {}
        self._hash_index: dict[str, UUID] = {}
        self._usage: dict[tuple[date, UUID, str, str], ApiUsageRecord] = {}
        self._webhooks: dict[UUID, B2BWebhookRecord] = {}

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
    ) -> ApiKeyRecord:
        now = datetime.now(UTC)
        rec = ApiKeyRecord(
            key_id=uuid4(),
            user_id=user_id,
            name=name.strip(),
            key_prefix=key_prefix,
            key_hash=key_hash,
            scopes=scopes,
            rate_limit_per_min=rate_limit_per_min,
            daily_limit=daily_limit,
            is_active=True,
            last_used_at=None,
            expires_at=expires_at,
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._keys[rec.key_id] = rec
            self._hash_index[key_hash] = rec.key_id
        return rec

    def list_api_keys(self, user_id: UUID) -> list[ApiKeyRecord]:
        with self._lock:
            items = [k for k in self._keys.values() if k.user_id == user_id]
        return sorted(items, key=lambda k: k.created_at, reverse=True)

    def get_api_key(self, user_id: UUID, key_id: UUID) -> ApiKeyRecord | None:
        with self._lock:
            rec = self._keys.get(key_id)
            return rec if rec is not None and rec.user_id == user_id else None

    def revoke_api_key(
        self, user_id: UUID, key_id: UUID, *, revoked_at: datetime
    ) -> ApiKeyRecord | None:
        with self._lock:
            rec = self._keys.get(key_id)
            if rec is None or rec.user_id != user_id:
                return None
            updated = ApiKeyRecord(
                key_id=rec.key_id,
                user_id=rec.user_id,
                name=rec.name,
                key_prefix=rec.key_prefix,
                key_hash=rec.key_hash,
                scopes=rec.scopes,
                rate_limit_per_min=rec.rate_limit_per_min,
                daily_limit=rec.daily_limit,
                is_active=False,
                last_used_at=rec.last_used_at,
                expires_at=rec.expires_at,
                revoked_at=rec.revoked_at or revoked_at,
                created_at=rec.created_at,
                updated_at=revoked_at,
            )
            self._keys[key_id] = updated
            return updated

    def find_key_by_hash(self, key_hash: str) -> ApiKeyRecord | None:
        with self._lock:
            kid = self._hash_index.get(key_hash)
            return self._keys.get(kid) if kid is not None else None

    def touch_key_last_used(self, key_id: UUID, *, used_at: datetime) -> None:
        with self._lock:
            rec = self._keys.get(key_id)
            if rec is None:
                return
            self._keys[key_id] = ApiKeyRecord(
                key_id=rec.key_id,
                user_id=rec.user_id,
                name=rec.name,
                key_prefix=rec.key_prefix,
                key_hash=rec.key_hash,
                scopes=rec.scopes,
                rate_limit_per_min=rec.rate_limit_per_min,
                daily_limit=rec.daily_limit,
                is_active=rec.is_active,
                last_used_at=used_at,
                expires_at=rec.expires_at,
                revoked_at=rec.revoked_at,
                created_at=rec.created_at,
                updated_at=rec.updated_at,
            )

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
    ) -> None:
        ts = now or datetime.now(UTC)
        status_class = f"{max(2, min(5, status_code // 100))}xx"
        bucket = (usage_date, key_id, endpoint, status_class)
        with self._lock:
            key_rec = self._keys.get(key_id)
            prefix = key_rec.key_prefix if key_rec is not None else "fi_res"
            existing = self._usage.get(bucket)
            if existing is None:
                self._usage[bucket] = ApiUsageRecord(
                    usage_date=usage_date,
                    key_id=key_id,
                    key_prefix=prefix,
                    user_id=user_id,
                    endpoint=endpoint,
                    status_class=status_class,
                    request_count=1,
                    cache_hits=1 if cache_hit else 0,
                    last_request_at=ts,
                )
            else:
                self._usage[bucket] = ApiUsageRecord(
                    usage_date=existing.usage_date,
                    key_id=existing.key_id,
                    key_prefix=existing.key_prefix,
                    user_id=existing.user_id,
                    endpoint=existing.endpoint,
                    status_class=existing.status_class,
                    request_count=existing.request_count + 1,
                    cache_hits=existing.cache_hits + (1 if cache_hit else 0),
                    last_request_at=ts,
                )

    def list_usage(
        self, user_id: UUID, *, from_date: date, to_date: date
    ) -> list[ApiUsageRecord]:
        with self._lock:
            items = [
                u
                for u in self._usage.values()
                if u.user_id == user_id and from_date <= u.usage_date <= to_date
            ]
        return sorted(items, key=lambda u: (u.usage_date, u.endpoint), reverse=True)

    def create_webhook(
        self,
        *,
        user_id: UUID,
        label: str,
        url: str,
        secret_hmac: str,
        event_types: tuple[str, ...],
    ) -> B2BWebhookRecord:
        now = datetime.now(UTC)
        rec = B2BWebhookRecord(
            endpoint_id=uuid4(),
            user_id=user_id,
            label=label.strip(),
            url=url.strip(),
            secret_hmac=secret_hmac,
            event_types=event_types,
            is_active=True,
            last_status=None,
            last_error=None,
            last_delivered_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._webhooks[rec.endpoint_id] = rec
        return rec

    def list_webhooks(self, user_id: UUID) -> list[B2BWebhookRecord]:
        with self._lock:
            items = [w for w in self._webhooks.values() if w.user_id == user_id]
        return sorted(items, key=lambda w: w.created_at, reverse=True)

    def get_webhook(self, user_id: UUID, endpoint_id: UUID) -> B2BWebhookRecord | None:
        with self._lock:
            rec = self._webhooks.get(endpoint_id)
            return rec if rec is not None and rec.user_id == user_id else None

    def delete_webhook(self, user_id: UUID, endpoint_id: UUID) -> bool:
        with self._lock:
            rec = self._webhooks.get(endpoint_id)
            if rec is None or rec.user_id != user_id:
                return False
            del self._webhooks[endpoint_id]
            return True

    def update_webhook_delivery(
        self,
        endpoint_id: UUID,
        *,
        http_status: int | None,
        error: str | None,
        delivered_at: datetime | None,
    ) -> None:
        with self._lock:
            rec = self._webhooks.get(endpoint_id)
            if rec is None:
                return
            self._webhooks[endpoint_id] = B2BWebhookRecord(
                endpoint_id=rec.endpoint_id,
                user_id=rec.user_id,
                label=rec.label,
                url=rec.url,
                secret_hmac=rec.secret_hmac,
                event_types=rec.event_types,
                is_active=rec.is_active,
                last_status=http_status,
                last_error=error,
                last_delivered_at=delivered_at,
                created_at=rec.created_at,
                updated_at=datetime.now(UTC),
            )


__all__ = ["InMemoryApiGatewayStore"]
