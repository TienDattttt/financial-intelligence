"""Pydantic HTTP schemas for ``/api/v3/alerts/*`` endpoints (V3-18)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from finintel.alerts.detector import AlertEventType, AlertSeverity


class AlertNotificationItem(BaseModel):
    notification_id: UUID
    ticker: str
    event_type: AlertEventType
    severity: AlertSeverity
    title_vi: str
    company_url: str
    evidence: dict[str, object]
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationsListResponse(BaseModel):
    items: list[AlertNotificationItem]
    total: int
    unread_count: int
    limit: int
    offset: int


class NotificationReadResponse(BaseModel):
    notification_id: UUID
    is_read: bool
    read_at: datetime | None = None


class MarkAllReadResponse(BaseModel):
    status: Literal["OK"]
    updated_count: int


class AlertSubscriptionResponse(BaseModel):
    subscription_id: UUID
    watchlist_id: UUID | None = None
    event_types: list[AlertEventType]
    in_app_enabled: bool
    email_digest_enabled: bool
    email_frequency: Literal["INSTANT", "DAILY_DIGEST"]
    telegram_enabled: bool
    telegram_chat_id: str | None = None
    webhook_enabled: bool
    webhook_url: str | None = None
    webhook_secret_configured: bool
    merton_dtd_threshold: float
    is_active: bool
    has_alert_consent: bool
    updated_at: datetime


class AlertSubscriptionUpdateRequest(BaseModel):
    watchlist_id: UUID | None = None
    event_types: list[AlertEventType] = Field(
        default_factory=lambda: list(AlertEventType)
    )
    in_app_enabled: bool = True
    email_digest_enabled: bool = False
    email_frequency: Literal["INSTANT", "DAILY_DIGEST"] = "DAILY_DIGEST"
    telegram_enabled: bool = False
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    webhook_enabled: bool = False
    webhook_url: str | None = Field(default=None, max_length=500)
    webhook_secret_hmac: str | None = Field(default=None, max_length=256)
    merton_dtd_threshold: float = Field(default=1.5, ge=0.0, le=10.0)
    is_active: bool = True


class WebhookTestRequest(BaseModel):
    webhook_url: str | None = Field(default=None, max_length=500)
    webhook_secret_hmac: str | None = Field(default=None, max_length=256)


class WebhookTestResponse(BaseModel):
    delivered: bool
    http_status: int | None = None
    signature_header_prefix: str | None = None
    detail: str


__all__ = [
    "AlertNotificationItem",
    "AlertSubscriptionResponse",
    "AlertSubscriptionUpdateRequest",
    "MarkAllReadResponse",
    "NotificationReadResponse",
    "NotificationsListResponse",
    "WebhookTestRequest",
    "WebhookTestResponse",
]
