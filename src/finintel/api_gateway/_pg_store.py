"""PostgreSQL ``finintel_app`` persistence for B2B API keys, usage, and webhooks (V3-25)."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from finintel.api_gateway.keys import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_RATE_LIMIT_PER_MIN,
    ApiKeyRecord,
    ApiUsageRecord,
    B2BWebhookRecord,
)

_KEY_COLS = (
    "key_id, user_id, name, key_prefix, key_hash, scopes, rate_limit_per_min, "
    "daily_limit, is_active, last_used_at, expires_at, revoked_at, created_at, updated_at"
)
_WH_COLS = (
    "endpoint_id, user_id, label, url, secret_hmac, event_types, is_active, "
    "last_status, last_error, last_delivered_at, created_at, updated_at"
)


class PostgresApiGatewayStore:
    """PostgreSQL store for ``api_keys``, ``api_key_usage_daily``, ``b2b_webhook_endpoints``."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must be a non-empty PostgreSQL DSN.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    @staticmethod
    def _row_to_key(row: tuple[Any, ...]) -> ApiKeyRecord:
        raw_scopes = row[5] if isinstance(row[5], list) else []
        return ApiKeyRecord(
            key_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            name=str(row[2]),
            key_prefix=str(row[3]),
            key_hash=str(row[4]),
            scopes=tuple(str(s) for s in raw_scopes),
            rate_limit_per_min=int(row[6]),
            daily_limit=int(row[7]),
            is_active=bool(row[8]),
            last_used_at=row[9],
            expires_at=row[10],
            revoked_at=row[11],
            created_at=row[12],
            updated_at=row[13],
        )

    @staticmethod
    def _row_to_webhook(row: tuple[Any, ...]) -> B2BWebhookRecord:
        raw_ev = row[5] if isinstance(row[5], list) else []
        return B2BWebhookRecord(
            endpoint_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            label=str(row[2]),
            url=str(row[3]),
            secret_hmac=str(row[4]),
            event_types=tuple(str(e) for e in raw_ev),
            is_active=bool(row[6]),
            last_status=int(row[7]) if row[7] is not None else None,
            last_error=str(row[8]) if row[8] is not None else None,
            last_delivered_at=row[9],
            created_at=row[10],
            updated_at=row[11],
        )

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
        kid = uuid4()
        sql = (
            f"INSERT INTO api_keys (key_id, user_id, name, key_prefix, key_hash, "
            f"scopes, rate_limit_per_min, daily_limit, expires_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING {_KEY_COLS}"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(kid),
                        str(user_id),
                        name.strip(),
                        key_prefix,
                        key_hash,
                        json.dumps(list(scopes)),
                        rate_limit_per_min,
                        daily_limit,
                        expires_at,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return self._row_to_key(row)

    def list_api_keys(self, user_id: UUID) -> list[ApiKeyRecord]:
        sql = f"SELECT {_KEY_COLS} FROM api_keys WHERE user_id = %s ORDER BY created_at DESC"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(user_id),))
                rows = cur.fetchall()
        return [self._row_to_key(r) for r in rows]

    def get_api_key(self, user_id: UUID, key_id: UUID) -> ApiKeyRecord | None:
        sql = f"SELECT {_KEY_COLS} FROM api_keys WHERE user_id = %s AND key_id = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(user_id), str(key_id)))
                row = cur.fetchone()
        return self._row_to_key(row) if row is not None else None

    def revoke_api_key(
        self, user_id: UUID, key_id: UUID, *, revoked_at: datetime
    ) -> ApiKeyRecord | None:
        sql = (
            f"UPDATE api_keys SET is_active = FALSE, "
            f"revoked_at = COALESCE(revoked_at, %s), updated_at = %s "
            f"WHERE user_id = %s AND key_id = %s RETURNING {_KEY_COLS}"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (revoked_at, revoked_at, str(user_id), str(key_id)))
                row = cur.fetchone()
            conn.commit()
        return self._row_to_key(row) if row is not None else None

    def find_key_by_hash(self, key_hash: str) -> ApiKeyRecord | None:
        sql = f"SELECT {_KEY_COLS} FROM api_keys WHERE key_hash = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (key_hash,))
                row = cur.fetchone()
        return self._row_to_key(row) if row is not None else None

    def touch_key_last_used(self, key_id: UUID, *, used_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE api_keys SET last_used_at = %s WHERE key_id = %s",
                    (used_at, str(key_id)),
                )
            conn.commit()

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
        sql = """
            INSERT INTO api_key_usage_daily (
                usage_date, key_id, user_id, endpoint, status_class,
                request_count, cache_hits, last_request_at
            ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT (usage_date, key_id, endpoint, status_class)
            DO UPDATE SET
                request_count = api_key_usage_daily.request_count + 1,
                cache_hits = api_key_usage_daily.cache_hits + EXCLUDED.cache_hits,
                last_request_at = EXCLUDED.last_request_at
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        usage_date,
                        str(key_id),
                        str(user_id),
                        endpoint,
                        status_class,
                        1 if cache_hit else 0,
                        ts,
                    ),
                )
            conn.commit()

    def list_usage(
        self, user_id: UUID, *, from_date: date, to_date: date
    ) -> list[ApiUsageRecord]:
        sql = """
            SELECT u.usage_date, u.key_id, k.key_prefix, u.user_id,
                   u.endpoint, u.status_class, u.request_count,
                   u.cache_hits, u.last_request_at
            FROM api_key_usage_daily u
            JOIN api_keys k ON k.key_id = u.key_id
            WHERE u.user_id = %s AND u.usage_date >= %s AND u.usage_date <= %s
            ORDER BY u.usage_date DESC, u.endpoint ASC
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(user_id), from_date, to_date))
                rows = cur.fetchall()
        return [
            ApiUsageRecord(
                usage_date=r[0],
                key_id=UUID(str(r[1])),
                key_prefix=str(r[2]),
                user_id=UUID(str(r[3])),
                endpoint=str(r[4]),
                status_class=str(r[5]),
                request_count=int(r[6]),
                cache_hits=int(r[7]),
                last_request_at=r[8],
            )
            for r in rows
        ]

    def create_webhook(
        self,
        *,
        user_id: UUID,
        label: str,
        url: str,
        secret_hmac: str,
        event_types: tuple[str, ...],
    ) -> B2BWebhookRecord:
        eid = uuid4()
        sql = (
            f"INSERT INTO b2b_webhook_endpoints "
            f"(endpoint_id, user_id, label, url, secret_hmac, event_types) "
            f"VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING {_WH_COLS}"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(eid),
                        str(user_id),
                        label.strip(),
                        url.strip(),
                        secret_hmac,
                        json.dumps(list(event_types)),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return self._row_to_webhook(row)

    def list_webhooks(self, user_id: UUID) -> list[B2BWebhookRecord]:
        sql = f"SELECT {_WH_COLS} FROM b2b_webhook_endpoints WHERE user_id = %s ORDER BY created_at DESC"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(user_id),))
                rows = cur.fetchall()
        return [self._row_to_webhook(r) for r in rows]

    def get_webhook(self, user_id: UUID, endpoint_id: UUID) -> B2BWebhookRecord | None:
        sql = f"SELECT {_WH_COLS} FROM b2b_webhook_endpoints WHERE user_id = %s AND endpoint_id = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(user_id), str(endpoint_id)))
                row = cur.fetchone()
        return self._row_to_webhook(row) if row is not None else None

    def delete_webhook(self, user_id: UUID, endpoint_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM b2b_webhook_endpoints WHERE user_id = %s AND endpoint_id = %s",
                    (str(user_id), str(endpoint_id)),
                )
                deleted = int(cur.rowcount or 0) > 0
            conn.commit()
        return deleted

    def update_webhook_delivery(
        self,
        endpoint_id: UUID,
        *,
        http_status: int | None,
        error: str | None,
        delivered_at: datetime | None,
    ) -> None:
        sql = (
            "UPDATE b2b_webhook_endpoints SET last_status = %s, last_error = %s, "
            "last_delivered_at = %s, updated_at = NOW() WHERE endpoint_id = %s"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (http_status, error, delivered_at, str(endpoint_id)))
            conn.commit()


__all__ = ["PostgresApiGatewayStore"]
