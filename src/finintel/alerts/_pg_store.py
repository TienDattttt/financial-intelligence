"""PostgreSQL ``finintel_app`` store for V3 alerts with strict lazy ``psycopg`` imports."""

from __future__ import annotations

import importlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from finintel.alerts._store import (
    DEFAULT_SUBSCRIPTION_EVENT_TYPES,
    AlertChannel,
    AlertSubscriptionRecord,
    AlertSubscriptionUpsert,
    DeliveryRecord,
    DeliveryStatus,
    MatchedSubscriber,
    UserNotificationRecord,
    default_subscription_for_user,
)
from finintel.alerts.detector import AlertEvent, AlertEventType, AlertSeverity
from finintel.domain.compliance import ConsentPurpose


class PostgresAlertStore:
    """PostgreSQL implementation of ``AlertStoreProtocol`` with lazy ``psycopg`` imports."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("FININTEL_APP_PG_DSN must be a non-empty PostgreSQL DSN.")
        self._dsn = dsn.strip()

    def _connect(self) -> Any:
        psycopg_mod = importlib.import_module("psycopg")
        return psycopg_mod.connect(self._dsn)

    @staticmethod
    def _row_to_subscription(row: tuple[Any, ...]) -> AlertSubscriptionRecord:
        raw_types = row[3] if isinstance(row[3], list) else []
        parsed_types: list[AlertEventType] = []
        for item in raw_types:
            try:
                parsed_types.append(AlertEventType(str(item)))
            except ValueError:
                continue
        return AlertSubscriptionRecord(
            subscription_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            watchlist_id=UUID(str(row[2])) if row[2] is not None else None,
            event_types=tuple(parsed_types) or DEFAULT_SUBSCRIPTION_EVENT_TYPES,
            in_app_enabled=bool(row[4]),
            email_digest_enabled=bool(row[5]),
            email_frequency="INSTANT" if str(row[6]) == "INSTANT" else "DAILY_DIGEST",
            telegram_enabled=bool(row[7]),
            telegram_chat_id=str(row[8]) if row[8] is not None else None,
            webhook_enabled=bool(row[9]),
            webhook_url=str(row[10]) if row[10] is not None else None,
            webhook_secret_hmac=str(row[11]) if row[11] is not None else None,
            merton_dtd_threshold=float(row[12]),
            is_active=bool(row[13]),
            created_at=row[14],
            updated_at=row[15],
        )

    @staticmethod
    def _row_to_notification(row: tuple[Any, ...]) -> UserNotificationRecord:
        sev = str(row[6])
        typed_sev: AlertSeverity = (
            "CRITICAL"
            if sev == "CRITICAL"
            else (
                "HIGH" if sev == "HIGH" else ("MEDIUM" if sev == "MEDIUM" else "INFO")
            )
        )
        return UserNotificationRecord(
            notification_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            event_id=UUID(str(row[2])) if row[2] is not None else None,
            dedup_key=str(row[3]),
            ticker=str(row[4]),
            event_type=AlertEventType(str(row[5])),
            severity=typed_sev,
            title_vi=str(row[7]),
            company_url=str(row[8]),
            evidence=dict(row[9]) if isinstance(row[9], dict) else {},
            is_read=bool(row[10]),
            read_at=row[11],
            created_at=row[12],
        )

    def save_market_event(self, event: AlertEvent) -> tuple[UUID, bool]:
        eid = uuid4()
        payload = json.dumps(event.evidence, ensure_ascii=False, default=str)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO market_alert_events (
                        event_id, dedup_key, snapshot_id, ticker, event_type,
                        severity, title_vi, evidence_json, detected_at
                    )
                    VALUES (
                        %(event_id)s, %(dedup_key)s, %(snapshot_id)s, %(ticker)s, %(event_type)s,
                        %(severity)s, %(title_vi)s, %(evidence_json)s::jsonb, %(detected_at)s
                    )
                    ON CONFLICT (dedup_key) DO NOTHING
                    RETURNING event_id
                    """,
                    {
                        "event_id": eid,
                        "dedup_key": event.dedup_key,
                        "snapshot_id": event.snapshot_id,
                        "ticker": event.ticker,
                        "event_type": event.event_type.value,
                        "severity": event.severity,
                        "title_vi": event.title_vi,
                        "evidence_json": payload,
                        "detected_at": event.detected_at,
                    },
                )
                row = cur.fetchone()
                if row is not None:
                    conn.commit()
                    return UUID(str(row[0])), True
                cur.execute(
                    "SELECT event_id FROM market_alert_events WHERE dedup_key = %(dedup_key)s",
                    {"dedup_key": event.dedup_key},
                )
                existing = cur.fetchone()
            conn.commit()
        assert existing is not None
        return UUID(str(existing[0])), False

    def get_subscription(self, user_id: UUID) -> AlertSubscriptionRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT subscription_id, user_id, watchlist_id, event_types,
                           in_app_enabled, email_digest_enabled, email_frequency,
                           telegram_enabled, telegram_chat_id, webhook_enabled,
                           webhook_url, webhook_secret_hmac, merton_dtd_threshold,
                           is_active, created_at, updated_at
                    FROM alert_subscriptions
                    WHERE user_id = %(user_id)s
                    """,
                    {"user_id": user_id},
                )
                row = cur.fetchone()
        return self._row_to_subscription(row) if row is not None else None

    def has_alert_consent(self, user_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT granted, withdrawn_at
                    FROM user_consents
                    WHERE user_id = %(user_id)s AND purpose = %(purpose)s
                    ORDER BY recorded_at DESC
                    LIMIT 1
                    """,
                    {
                        "user_id": user_id,
                        "purpose": ConsentPurpose.ALERT_NOTIFICATIONS.value,
                    },
                )
                row = cur.fetchone()
        return bool(row is not None and row[0] and row[1] is None)

    def upsert_subscription(
        self, payload: AlertSubscriptionUpsert
    ) -> AlertSubscriptionRecord:
        sid = uuid4()
        types_json = json.dumps([e.value for e in dict.fromkeys(payload.event_types)])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alert_subscriptions (
                        subscription_id, user_id, watchlist_id, event_types,
                        in_app_enabled, email_digest_enabled, email_frequency,
                        telegram_enabled, telegram_chat_id, webhook_enabled,
                        webhook_url, webhook_secret_hmac, merton_dtd_threshold, is_active
                    )
                    VALUES (
                        %(subscription_id)s, %(user_id)s, %(watchlist_id)s, %(event_types)s::jsonb,
                        %(in_app_enabled)s, %(email_digest_enabled)s, %(email_frequency)s,
                        %(telegram_enabled)s, %(telegram_chat_id)s, %(webhook_enabled)s,
                        %(webhook_url)s, %(webhook_secret_hmac)s, %(merton_dtd_threshold)s, %(is_active)s
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        watchlist_id = EXCLUDED.watchlist_id,
                        event_types = EXCLUDED.event_types,
                        in_app_enabled = EXCLUDED.in_app_enabled,
                        email_digest_enabled = EXCLUDED.email_digest_enabled,
                        email_frequency = EXCLUDED.email_frequency,
                        telegram_enabled = EXCLUDED.telegram_enabled,
                        telegram_chat_id = EXCLUDED.telegram_chat_id,
                        webhook_enabled = EXCLUDED.webhook_enabled,
                        webhook_url = EXCLUDED.webhook_url,
                        webhook_secret_hmac = CASE
                            WHEN %(preserve_secret)s THEN alert_subscriptions.webhook_secret_hmac
                            ELSE EXCLUDED.webhook_secret_hmac
                        END,
                        merton_dtd_threshold = EXCLUDED.merton_dtd_threshold,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                    RETURNING subscription_id, user_id, watchlist_id, event_types,
                              in_app_enabled, email_digest_enabled, email_frequency,
                              telegram_enabled, telegram_chat_id, webhook_enabled,
                              webhook_url, webhook_secret_hmac, merton_dtd_threshold,
                              is_active, created_at, updated_at
                    """,
                    {
                        "subscription_id": sid,
                        "user_id": payload.user_id,
                        "watchlist_id": payload.watchlist_id,
                        "event_types": types_json,
                        "in_app_enabled": payload.in_app_enabled,
                        "email_digest_enabled": payload.email_digest_enabled,
                        "email_frequency": payload.email_frequency,
                        "telegram_enabled": payload.telegram_enabled,
                        "telegram_chat_id": payload.telegram_chat_id,
                        "webhook_enabled": payload.webhook_enabled,
                        "webhook_url": payload.webhook_url,
                        "webhook_secret_hmac": payload.webhook_secret_hmac,
                        "preserve_secret": payload.preserve_existing_secret,
                        "merton_dtd_threshold": float(payload.merton_dtd_threshold),
                        "is_active": payload.is_active,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return self._row_to_subscription(row)

    def list_subscribers_for_tickers(
        self, tickers: Sequence[str]
    ) -> list[MatchedSubscriber]:
        wanted = [t.strip().upper() for t in tickers if t.strip()]
        if not wanted:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT u.user_id, u.email
                    FROM users u
                    JOIN watchlist_items wi ON wi.user_id = u.user_id
                    WHERE u.is_active = TRUE
                      AND u.deleted_at IS NULL
                      AND wi.ticker = ANY(%(tickers)s)
                    """,
                    {"tickers": wanted},
                )
                user_rows = cur.fetchall()

        matched: list[MatchedSubscriber] = []
        for uid_raw, email_raw in user_rows:
            uid = UUID(str(uid_raw))
            sub = self.get_subscription(uid) or default_subscription_for_user(uid)
            if not sub.is_active:
                continue
            with self._connect() as conn:
                with conn.cursor() as cur:
                    if sub.watchlist_id is not None:
                        cur.execute(
                            """
                            SELECT ticker FROM watchlist_items
                            WHERE user_id = %(user_id)s AND watchlist_id = %(watchlist_id)s
                            """,
                            {"user_id": uid, "watchlist_id": sub.watchlist_id},
                        )
                    else:
                        cur.execute(
                            "SELECT ticker FROM watchlist_items WHERE user_id = %(user_id)s",
                            {"user_id": uid},
                        )
                    watched = frozenset(str(r[0]).upper() for r in cur.fetchall())
            if not (watched & set(wanted)):
                continue
            matched.append(
                MatchedSubscriber(
                    user_id=uid,
                    email=str(email_raw),
                    has_alert_consent=self.has_alert_consent(uid),
                    subscription=sub,
                    watched_tickers=watched,
                )
            )
        return matched

    def create_notification_if_new(
        self,
        *,
        user_id: UUID,
        event_id: UUID | None,
        dedup_key: str,
        ticker: str,
        event_type: AlertEventType,
        severity: AlertSeverity,
        title_vi: str,
        company_url: str,
        evidence: dict[str, object],
        created_at: datetime,
    ) -> tuple[UserNotificationRecord | None, bool]:
        nid = uuid4()
        payload = json.dumps(evidence, ensure_ascii=False, default=str)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_notifications (
                        notification_id, user_id, event_id, dedup_key, ticker,
                        event_type, severity, title_vi, company_url, evidence_json, created_at
                    )
                    VALUES (
                        %(notification_id)s, %(user_id)s, %(event_id)s, %(dedup_key)s, %(ticker)s,
                        %(event_type)s, %(severity)s, %(title_vi)s, %(company_url)s,
                        %(evidence_json)s::jsonb, %(created_at)s
                    )
                    ON CONFLICT (user_id, dedup_key) DO NOTHING
                    RETURNING notification_id, user_id, event_id, dedup_key, ticker,
                              event_type, severity, title_vi, company_url, evidence_json,
                              is_read, read_at, created_at
                    """,
                    {
                        "notification_id": nid,
                        "user_id": user_id,
                        "event_id": event_id,
                        "dedup_key": dedup_key,
                        "ticker": ticker,
                        "event_type": event_type.value,
                        "severity": severity,
                        "title_vi": title_vi,
                        "company_url": company_url,
                        "evidence_json": payload,
                        "created_at": created_at,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            return None, False
        return self._row_to_notification(row), True

    def list_notifications(
        self,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserNotificationRecord], int, int]:
        where_unread = " AND is_read = FALSE" if unread_only else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM user_notifications WHERE user_id = %(user_id)s AND is_read = FALSE",
                    {"user_id": user_id},
                )
                unread_row = cur.fetchone()
                unread_count = int(unread_row[0]) if unread_row else 0
                cur.execute(
                    f"SELECT COUNT(*) FROM user_notifications WHERE user_id = %(user_id)s{where_unread}",
                    {"user_id": user_id},
                )
                total_row = cur.fetchone()
                total = int(total_row[0]) if total_row else 0
                cur.execute(
                    f"""
                    SELECT notification_id, user_id, event_id, dedup_key, ticker,
                           event_type, severity, title_vi, company_url, evidence_json,
                           is_read, read_at, created_at
                    FROM user_notifications
                    WHERE user_id = %(user_id)s{where_unread}
                    ORDER BY created_at DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    {"user_id": user_id, "limit": limit, "offset": offset},
                )
                rows = cur.fetchall()
        return [self._row_to_notification(r) for r in rows], total, unread_count

    def mark_notification_read(
        self, user_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> UserNotificationRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_notifications
                    SET is_read = TRUE, read_at = COALESCE(read_at, %(read_at)s)
                    WHERE notification_id = %(notification_id)s AND user_id = %(user_id)s
                    RETURNING notification_id, user_id, event_id, dedup_key, ticker,
                              event_type, severity, title_vi, company_url, evidence_json,
                              is_read, read_at, created_at
                    """,
                    {
                        "notification_id": notification_id,
                        "user_id": user_id,
                        "read_at": read_at,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_notification(row) if row is not None else None

    def mark_all_notifications_read(self, user_id: UUID, *, read_at: datetime) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_notifications
                    SET is_read = TRUE, read_at = %(read_at)s
                    WHERE user_id = %(user_id)s AND is_read = FALSE
                    """,
                    {"user_id": user_id, "read_at": read_at},
                )
                count = int(cur.rowcount or 0)
            conn.commit()
        return count

    def claim_delivery(
        self,
        *,
        user_id: UUID,
        event_id: UUID | None,
        channel: AlertChannel,
        dedup_key: str,
    ) -> tuple[UUID, bool]:
        did = uuid4()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO webhook_deliveries (
                        delivery_id, user_id, event_id, channel, dedup_key, status
                    )
                    VALUES (
                        %(delivery_id)s, %(user_id)s, %(event_id)s, %(channel)s, %(dedup_key)s, 'PENDING'
                    )
                    ON CONFLICT (user_id, channel, dedup_key) DO NOTHING
                    RETURNING delivery_id
                    """,
                    {
                        "delivery_id": did,
                        "user_id": user_id,
                        "event_id": event_id,
                        "channel": channel,
                        "dedup_key": dedup_key,
                    },
                )
                row = cur.fetchone()
                if row is not None:
                    conn.commit()
                    return UUID(str(row[0])), True
                cur.execute(
                    """
                    SELECT delivery_id FROM webhook_deliveries
                    WHERE user_id = %(user_id)s AND channel = %(channel)s AND dedup_key = %(dedup_key)s
                    """,
                    {"user_id": user_id, "channel": channel, "dedup_key": dedup_key},
                )
                existing = cur.fetchone()
            conn.commit()
        assert existing is not None
        return UUID(str(existing[0])), False

    def update_delivery_status(
        self,
        delivery_id: UUID,
        *,
        status: DeliveryStatus,
        attempts: int,
        http_status: int | None = None,
        error_code: str | None = None,
        last_error: str | None = None,
        next_retry_at: datetime | None = None,
        delivered_at: datetime | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE webhook_deliveries
                    SET status = %(status)s,
                        attempts = %(attempts)s,
                        http_status = %(http_status)s,
                        error_code = %(error_code)s,
                        last_error = %(last_error)s,
                        next_retry_at = %(next_retry_at)s,
                        delivered_at = %(delivered_at)s,
                        updated_at = NOW()
                    WHERE delivery_id = %(delivery_id)s
                    """,
                    {
                        "delivery_id": delivery_id,
                        "status": status,
                        "attempts": attempts,
                        "http_status": http_status,
                        "error_code": error_code,
                        "last_error": last_error,
                        "next_retry_at": next_retry_at,
                        "delivered_at": delivered_at,
                    },
                )
            conn.commit()

    def count_sent_emails_on_date(self, target_date: date) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT CASE
                        WHEN dedup_key LIKE 'digest:%%' THEN user_id::text || '|' || dedup_key
                        ELSE delivery_id::text
                    END)
                    FROM webhook_deliveries
                    WHERE channel = 'EMAIL_DIGEST'
                      AND status = 'SENT'
                      AND delivered_at IS NOT NULL
                      AND (delivered_at AT TIME ZONE 'UTC')::date = %(target_date)s
                    """,
                    {"target_date": target_date},
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_deliveries(
        self, user_id: UUID, *, limit: int = 50
    ) -> list[DeliveryRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT delivery_id, user_id, event_id, channel, dedup_key, status,
                           attempts, http_status, error_code, last_error, next_retry_at,
                           delivered_at, created_at, updated_at
                    FROM webhook_deliveries
                    WHERE user_id = %(user_id)s
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"user_id": user_id, "limit": limit},
                )
                rows = cur.fetchall()
        return [
            DeliveryRecord(
                delivery_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])),
                event_id=UUID(str(r[2])) if r[2] is not None else None,
                channel=r[3],
                dedup_key=r[4],
                status=r[5],
                attempts=int(r[6]),
                http_status=r[7],
                error_code=r[8],
                last_error=r[9],
                next_retry_at=r[10],
                delivered_at=r[11],
                created_at=r[12],
                updated_at=r[13],
            )
            for r in rows
        ]


__all__ = ["PostgresAlertStore"]
