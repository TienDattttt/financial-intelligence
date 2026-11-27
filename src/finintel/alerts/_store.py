"""Persistence protocol, in-memory store, and lazy-psycopg PostgreSQL store for V3 alerts."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from finintel.alerts.detector import AlertEvent, AlertEventType, AlertSeverity
from finintel.auth._store import InMemoryAppStore
from finintel.domain.compliance import ConsentPurpose

AlertChannel = Literal["IN_APP", "TELEGRAM", "EMAIL_DIGEST", "WEBHOOK"]
DeliveryStatus = Literal[
    "PENDING",
    "SENT",
    "FAILED",
    "DEAD_LETTER",
    "SKIPPED_NO_CONSENT",
    "SKIPPED_QUOTA",
]
EmailFrequency = Literal["INSTANT", "DAILY_DIGEST"]

DEFAULT_SUBSCRIPTION_EVENT_TYPES: tuple[AlertEventType, ...] = (
    AlertEventType.NEW_FILING_PUBLISHED,
    AlertEventType.RISK_TIER_UPGRADE,
    AlertEventType.NEW_RED_FLAG_TRIGGERED,
    AlertEventType.REGULATORY_EVENT_ISSUED,
    AlertEventType.MERTON_DTD_BREACH,
)


@dataclass(frozen=True)
class AlertSubscriptionRecord:
    subscription_id: UUID
    user_id: UUID
    watchlist_id: UUID | None
    event_types: tuple[AlertEventType, ...]
    in_app_enabled: bool
    email_digest_enabled: bool
    email_frequency: EmailFrequency
    telegram_enabled: bool
    telegram_chat_id: str | None
    webhook_enabled: bool
    webhook_url: str | None
    webhook_secret_hmac: str | None
    merton_dtd_threshold: float
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AlertSubscriptionUpsert:
    user_id: UUID
    watchlist_id: UUID | None = None
    event_types: tuple[AlertEventType, ...] = DEFAULT_SUBSCRIPTION_EVENT_TYPES
    in_app_enabled: bool = True
    email_digest_enabled: bool = False
    email_frequency: EmailFrequency = "DAILY_DIGEST"
    telegram_enabled: bool = False
    telegram_chat_id: str | None = None
    webhook_enabled: bool = False
    webhook_url: str | None = None
    webhook_secret_hmac: str | None = None
    preserve_existing_secret: bool = False
    merton_dtd_threshold: float = 1.5
    is_active: bool = True


@dataclass(frozen=True)
class MatchedSubscriber:
    user_id: UUID
    email: str
    has_alert_consent: bool
    subscription: AlertSubscriptionRecord
    watched_tickers: frozenset[str]


@dataclass(frozen=True)
class UserNotificationRecord:
    notification_id: UUID
    user_id: UUID
    event_id: UUID | None
    dedup_key: str
    ticker: str
    event_type: AlertEventType
    severity: AlertSeverity
    title_vi: str
    company_url: str
    evidence: dict[str, object]
    is_read: bool
    read_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: UUID
    user_id: UUID
    event_id: UUID | None
    channel: AlertChannel
    dedup_key: str
    status: DeliveryStatus
    attempts: int
    http_status: int | None
    error_code: str | None
    last_error: str | None
    next_retry_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertStoreProtocol(Protocol):
    """Persistence contract for alert subscriptions, events, inbox, and deliveries."""

    def save_market_event(self, event: AlertEvent) -> tuple[UUID, bool]: ...
    def get_subscription(self, user_id: UUID) -> AlertSubscriptionRecord | None: ...
    def has_alert_consent(self, user_id: UUID) -> bool: ...
    def upsert_subscription(
        self, payload: AlertSubscriptionUpsert
    ) -> AlertSubscriptionRecord: ...
    def list_subscribers_for_tickers(
        self, tickers: Sequence[str]
    ) -> list[MatchedSubscriber]: ...
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
    ) -> tuple[UserNotificationRecord | None, bool]: ...
    def list_notifications(
        self,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserNotificationRecord], int, int]: ...
    def mark_notification_read(
        self, user_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> UserNotificationRecord | None: ...
    def mark_all_notifications_read(
        self, user_id: UUID, *, read_at: datetime
    ) -> int: ...
    def claim_delivery(
        self,
        *,
        user_id: UUID,
        event_id: UUID | None,
        channel: AlertChannel,
        dedup_key: str,
    ) -> tuple[UUID, bool]: ...
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
    ) -> None: ...
    def count_sent_emails_on_date(self, target_date: date) -> int: ...
    def list_deliveries(
        self, user_id: UUID, *, limit: int = 50
    ) -> list[DeliveryRecord]: ...


def default_subscription_for_user(user_id: UUID) -> AlertSubscriptionRecord:
    now = datetime.now(UTC)
    return AlertSubscriptionRecord(
        subscription_id=uuid4(),
        user_id=user_id,
        watchlist_id=None,
        event_types=DEFAULT_SUBSCRIPTION_EVENT_TYPES,
        in_app_enabled=True,
        email_digest_enabled=False,
        email_frequency="DAILY_DIGEST",
        telegram_enabled=False,
        telegram_chat_id=None,
        webhook_enabled=False,
        webhook_url=None,
        webhook_secret_hmac=None,
        merton_dtd_threshold=1.5,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


class InMemoryAlertStore:
    """Thread-safe offline implementation of ``AlertStoreProtocol``."""

    def __init__(self, *, app_store: InMemoryAppStore | None = None) -> None:
        self._lock = threading.RLock()
        self._app_store = app_store
        self._events_by_dedup: dict[str, UUID] = {}
        self._subscriptions: dict[UUID, AlertSubscriptionRecord] = {}
        self._notifications: dict[UUID, UserNotificationRecord] = {}
        self._notification_keys: set[tuple[UUID, str]] = set()
        self._deliveries: dict[UUID, DeliveryRecord] = {}
        self._delivery_keys: set[tuple[UUID, AlertChannel, str]] = set()

    def save_market_event(self, event: AlertEvent) -> tuple[UUID, bool]:
        with self._lock:
            existing = self._events_by_dedup.get(event.dedup_key)
            if existing is not None:
                return existing, False
            eid = uuid4()
            self._events_by_dedup[event.dedup_key] = eid
            return eid, True

    def get_subscription(self, user_id: UUID) -> AlertSubscriptionRecord | None:
        with self._lock:
            return self._subscriptions.get(user_id)

    def has_alert_consent(self, user_id: UUID) -> bool:
        if self._app_store is None:
            return False
        consents = self._app_store.list_user_consents(user_id)
        for c in consents:
            if c.purpose == ConsentPurpose.ALERT_NOTIFICATIONS:
                return c.granted and c.withdrawn_at is None
        return False

    def upsert_subscription(
        self, payload: AlertSubscriptionUpsert
    ) -> AlertSubscriptionRecord:
        with self._lock:
            now = datetime.now(UTC)
            prev = self._subscriptions.get(payload.user_id)
            secret = payload.webhook_secret_hmac
            if payload.preserve_existing_secret and prev is not None:
                secret = prev.webhook_secret_hmac
            record = AlertSubscriptionRecord(
                subscription_id=prev.subscription_id if prev else uuid4(),
                user_id=payload.user_id,
                watchlist_id=payload.watchlist_id,
                event_types=tuple(dict.fromkeys(payload.event_types)),
                in_app_enabled=payload.in_app_enabled,
                email_digest_enabled=payload.email_digest_enabled,
                email_frequency=payload.email_frequency,
                telegram_enabled=payload.telegram_enabled,
                telegram_chat_id=payload.telegram_chat_id,
                webhook_enabled=payload.webhook_enabled,
                webhook_url=payload.webhook_url,
                webhook_secret_hmac=secret,
                merton_dtd_threshold=float(payload.merton_dtd_threshold),
                is_active=payload.is_active,
                created_at=prev.created_at if prev else now,
                updated_at=now,
            )
            self._subscriptions[payload.user_id] = record
            return record

    def list_subscribers_for_tickers(
        self, tickers: Sequence[str]
    ) -> list[MatchedSubscriber]:
        if self._app_store is None:
            return []
        wanted = {t.strip().upper() for t in tickers if t.strip()}
        if not wanted:
            return []
        with self._lock:
            subs_copy = dict(self._subscriptions)
        with self._app_store._lock:
            users = list(self._app_store._users.values())
        matched: list[MatchedSubscriber] = []
        for user in users:
            if not user.is_active:
                continue
            sub = subs_copy.get(user.user_id)
            if sub is None:
                sub = default_subscription_for_user(user.user_id)
            if not sub.is_active:
                continue
            watchlists = self._app_store.list_watchlists(user.user_id)
            watched: set[str] = set()
            for wl in watchlists:
                if sub.watchlist_id is not None and wl.watchlist_id != sub.watchlist_id:
                    continue
                watched.update(t.upper() for t in wl.items)
            if not (watched & wanted):
                continue
            matched.append(
                MatchedSubscriber(
                    user_id=user.user_id,
                    email=user.email,
                    has_alert_consent=self.has_alert_consent(user.user_id),
                    subscription=sub,
                    watched_tickers=frozenset(watched),
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
        with self._lock:
            key = (user_id, dedup_key)
            if key in self._notification_keys:
                return None, False
            rec = UserNotificationRecord(
                notification_id=uuid4(),
                user_id=user_id,
                event_id=event_id,
                dedup_key=dedup_key,
                ticker=ticker,
                event_type=event_type,
                severity=severity,
                title_vi=title_vi,
                company_url=company_url,
                evidence=dict(evidence),
                is_read=False,
                read_at=None,
                created_at=created_at,
            )
            self._notification_keys.add(key)
            self._notifications[rec.notification_id] = rec
            return rec, True

    def list_notifications(
        self,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserNotificationRecord], int, int]:
        with self._lock:
            owned = [n for n in self._notifications.values() if n.user_id == user_id]
            owned.sort(key=lambda n: n.created_at, reverse=True)
            unread_count = sum(1 for n in owned if not n.is_read)
            filtered = [n for n in owned if not n.is_read] if unread_only else owned
            total = len(filtered)
            return filtered[offset : offset + limit], total, unread_count

    def mark_notification_read(
        self, user_id: UUID, notification_id: UUID, *, read_at: datetime
    ) -> UserNotificationRecord | None:
        with self._lock:
            rec = self._notifications.get(notification_id)
            if rec is None or rec.user_id != user_id:
                return None
            if rec.is_read:
                return rec
            updated = UserNotificationRecord(
                notification_id=rec.notification_id,
                user_id=rec.user_id,
                event_id=rec.event_id,
                dedup_key=rec.dedup_key,
                ticker=rec.ticker,
                event_type=rec.event_type,
                severity=rec.severity,
                title_vi=rec.title_vi,
                company_url=rec.company_url,
                evidence=rec.evidence,
                is_read=True,
                read_at=read_at,
                created_at=rec.created_at,
            )
            self._notifications[notification_id] = updated
            return updated

    def mark_all_notifications_read(self, user_id: UUID, *, read_at: datetime) -> int:
        with self._lock:
            updated_count = 0
            for nid, rec in list(self._notifications.items()):
                if rec.user_id == user_id and not rec.is_read:
                    self._notifications[nid] = UserNotificationRecord(
                        notification_id=rec.notification_id,
                        user_id=rec.user_id,
                        event_id=rec.event_id,
                        dedup_key=rec.dedup_key,
                        ticker=rec.ticker,
                        event_type=rec.event_type,
                        severity=rec.severity,
                        title_vi=rec.title_vi,
                        company_url=rec.company_url,
                        evidence=rec.evidence,
                        is_read=True,
                        read_at=read_at,
                        created_at=rec.created_at,
                    )
                    updated_count += 1
            return updated_count

    def claim_delivery(
        self,
        *,
        user_id: UUID,
        event_id: UUID | None,
        channel: AlertChannel,
        dedup_key: str,
    ) -> tuple[UUID, bool]:
        with self._lock:
            key = (user_id, channel, dedup_key)
            if key in self._delivery_keys:
                for rec in self._deliveries.values():
                    if (
                        rec.user_id == user_id
                        and rec.channel == channel
                        and rec.dedup_key == dedup_key
                    ):
                        return rec.delivery_id, False
            did = uuid4()
            now = datetime.now(UTC)
            rec = DeliveryRecord(
                delivery_id=did,
                user_id=user_id,
                event_id=event_id,
                channel=channel,
                dedup_key=dedup_key,
                status="PENDING",
                attempts=0,
                http_status=None,
                error_code=None,
                last_error=None,
                next_retry_at=None,
                delivered_at=None,
                created_at=now,
                updated_at=now,
            )
            self._delivery_keys.add(key)
            self._deliveries[did] = rec
            return did, True

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
        with self._lock:
            prev = self._deliveries.get(delivery_id)
            if prev is None:
                return
            self._deliveries[delivery_id] = DeliveryRecord(
                delivery_id=prev.delivery_id,
                user_id=prev.user_id,
                event_id=prev.event_id,
                channel=prev.channel,
                dedup_key=prev.dedup_key,
                status=status,
                attempts=attempts,
                http_status=http_status,
                error_code=error_code,
                last_error=last_error,
                next_retry_at=next_retry_at,
                delivered_at=delivered_at,
                created_at=prev.created_at,
                updated_at=datetime.now(UTC),
            )

    def count_sent_emails_on_date(self, target_date: date) -> int:
        with self._lock:
            digest_keys: set[tuple[UUID, str]] = set()
            count = 0
            for d in self._deliveries.values():
                if (
                    d.channel == "EMAIL_DIGEST"
                    and d.status == "SENT"
                    and d.delivered_at is not None
                    and d.delivered_at.date() == target_date
                ):
                    if d.dedup_key.startswith("digest:"):
                        key = (d.user_id, d.dedup_key)
                        if key not in digest_keys:
                            digest_keys.add(key)
                            count += 1
                    else:
                        count += 1
            return count

    def list_deliveries(
        self, user_id: UUID, *, limit: int = 50
    ) -> list[DeliveryRecord]:
        with self._lock:
            rows = [d for d in self._deliveries.values() if d.user_id == user_id]
            rows.sort(key=lambda d: d.created_at, reverse=True)
            return rows[:limit]


__all__ = [
    "AlertChannel",
    "AlertStoreProtocol",
    "AlertSubscriptionRecord",
    "AlertSubscriptionUpsert",
    "DEFAULT_SUBSCRIPTION_EVENT_TYPES",
    "DeliveryRecord",
    "DeliveryStatus",
    "EmailFrequency",
    "InMemoryAlertStore",
    "MatchedSubscriber",
    "UserNotificationRecord",
    "default_subscription_for_user",
]
