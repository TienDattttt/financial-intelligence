"""FinIntel V3.0 Early Warning Alerts module (``V3-16..V3-18``)."""

from __future__ import annotations

from finintel.alerts._pg_store import PostgresAlertStore
from finintel.alerts._store import (
    AlertChannel,
    AlertStoreProtocol,
    AlertSubscriptionRecord,
    AlertSubscriptionUpsert,
    DeliveryRecord,
    DeliveryStatus,
    EmailFrequency,
    InMemoryAlertStore,
    UserNotificationRecord,
)
from finintel.alerts.detector import (
    DEFAULT_MERTON_DTD_THRESHOLD,
    AlertEvent,
    AlertEventType,
    AlertSeverity,
    compute_alert_dedup_key,
    detect_snapshot_alerts,
)
from finintel.alerts.dispatcher import (
    AlertDispatcher,
    DispatchSummary,
    WebhookDeliveryResult,
    WebhookSSRFError,
    send_signed_webhook,
    sign_webhook_payload,
    validate_webhook_url,
    verify_webhook_signature,
)

__all__ = [
    "DEFAULT_MERTON_DTD_THRESHOLD",
    "AlertChannel",
    "AlertDispatcher",
    "AlertEvent",
    "AlertEventType",
    "AlertSeverity",
    "AlertStoreProtocol",
    "AlertSubscriptionRecord",
    "AlertSubscriptionUpsert",
    "DeliveryRecord",
    "DeliveryStatus",
    "DispatchSummary",
    "EmailFrequency",
    "InMemoryAlertStore",
    "PostgresAlertStore",
    "UserNotificationRecord",
    "WebhookDeliveryResult",
    "WebhookSSRFError",
    "compute_alert_dedup_key",
    "detect_snapshot_alerts",
    "send_signed_webhook",
    "sign_webhook_payload",
    "validate_webhook_url",
    "verify_webhook_signature",
]
