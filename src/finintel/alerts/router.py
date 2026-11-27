"""FastAPI router for ``/api/v3/alerts/*`` endpoints and lazy mounting (V3-18)."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from finintel.alerts._store import (
    AlertStoreProtocol,
    AlertSubscriptionRecord,
    AlertSubscriptionUpsert,
    InMemoryAlertStore,
    UserNotificationRecord,
    default_subscription_for_user,
)
from finintel.alerts.dispatcher import (
    WebhookSSRFError,
    send_signed_webhook,
    validate_webhook_url,
)
from finintel.auth._store import AppStoreProtocol, InMemoryAppStore
from finintel.auth.dependencies import (
    AuthenticatedUser,
    extract_client_ip,
    require_authenticated_mutation,
    require_authenticated_user,
)
from finintel.domain.compliance import ConsentPurpose
from finintel.workspace.audit import record_audit_event

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from finintel.alerts.schemas import AlertNotificationItem, AlertSubscriptionResponse


def _to_notif_item(rec: UserNotificationRecord) -> "AlertNotificationItem":
    from finintel.alerts.schemas import AlertNotificationItem

    return AlertNotificationItem(
        notification_id=rec.notification_id,
        ticker=rec.ticker,
        event_type=rec.event_type,
        severity=rec.severity,
        title_vi=rec.title_vi,
        company_url=rec.company_url,
        evidence=rec.evidence,
        is_read=rec.is_read,
        read_at=rec.read_at,
        created_at=rec.created_at,
    )


def _to_sub_response(
    sub: AlertSubscriptionRecord, *, has_alert_consent: bool
) -> "AlertSubscriptionResponse":
    from finintel.alerts.schemas import AlertSubscriptionResponse

    return AlertSubscriptionResponse(
        subscription_id=sub.subscription_id,
        watchlist_id=sub.watchlist_id,
        event_types=list(sub.event_types),
        in_app_enabled=sub.in_app_enabled,
        email_digest_enabled=sub.email_digest_enabled,
        email_frequency=sub.email_frequency,
        telegram_enabled=sub.telegram_enabled,
        telegram_chat_id=sub.telegram_chat_id,
        webhook_enabled=sub.webhook_enabled,
        webhook_url=sub.webhook_url,
        webhook_secret_configured=bool(sub.webhook_secret_hmac),
        merton_dtd_threshold=sub.merton_dtd_threshold,
        is_active=sub.is_active,
        has_alert_consent=has_alert_consent,
        updated_at=sub.updated_at,
    )


def create_alerts_router(
    *,
    get_alert_store: Callable[[], AlertStoreProtocol | None],
    get_app_store: Callable[[], AppStoreProtocol | None] | None = None,
) -> "APIRouter":
    """Build standalone FastAPI router for ``/api/v3/alerts/*``."""
    from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

    from finintel.alerts.schemas import (
        AlertSubscriptionResponse,
        AlertSubscriptionUpdateRequest,
        MarkAllReadResponse,
        NotificationReadResponse,
        NotificationsListResponse,
        WebhookTestRequest,
        WebhookTestResponse,
    )

    router = APIRouter(prefix="/api/v3/alerts", tags=["v3-alerts"])

    def _require_store() -> AlertStoreProtocol:
        store = get_alert_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ cảnh báo hiện chưa được cấu hình trên máy chủ.",
            )
        return store

    @router.get("/notifications", response_model=NotificationsListResponse)
    def list_notifications(
        unread_only: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> NotificationsListResponse:
        store = _require_store()
        items, total, unread_count = store.list_notifications(
            user.user_id, unread_only=unread_only, limit=limit, offset=offset
        )
        return NotificationsListResponse(
            items=[_to_notif_item(n) for n in items],
            total=total,
            unread_count=unread_count,
            limit=limit,
            offset=offset,
        )

    @router.post(
        "/notifications/{notification_id}/read",
        response_model=NotificationReadResponse,
    )
    def mark_notification_read(
        notification_id: UUID,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> NotificationReadResponse:
        store = _require_store()
        updated = store.mark_notification_read(
            user.user_id, notification_id, read_at=datetime.now(UTC)
        )
        if updated is None:
            raise HTTPException(
                status_code=404, detail="Không tìm thấy thông báo cảnh báo."
            )
        return NotificationReadResponse(
            notification_id=updated.notification_id,
            is_read=updated.is_read,
            read_at=updated.read_at,
        )

    @router.post("/notifications/read-all", response_model=MarkAllReadResponse)
    def mark_all_notifications_read(
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> MarkAllReadResponse:
        store = _require_store()
        count = store.mark_all_notifications_read(
            user.user_id, read_at=datetime.now(UTC)
        )
        return MarkAllReadResponse(status="OK", updated_count=count)

    @router.get("/subscriptions", response_model=AlertSubscriptionResponse)
    def get_subscription(
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> AlertSubscriptionResponse:
        store = _require_store()
        sub = store.get_subscription(user.user_id) or default_subscription_for_user(
            user.user_id
        )
        consent_ok = ConsentPurpose.ALERT_NOTIFICATIONS in user.consents
        return _to_sub_response(sub, has_alert_consent=consent_ok)

    @router.put("/subscriptions", response_model=AlertSubscriptionResponse)
    def update_subscription(
        request: Request,
        body: AlertSubscriptionUpdateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AlertSubscriptionResponse:
        store = _require_store()
        clean_url = body.webhook_url.strip() if body.webhook_url else None
        if body.webhook_enabled and not clean_url:
            raise HTTPException(
                status_code=422,
                detail="Vui lòng nhập URL HTTPS hợp lệ khi bật Webhook.",
            )
        resolver = getattr(request.app.state, "_alerts_dns_resolver", None)
        if clean_url:
            try:
                clean_url, _ = validate_webhook_url(clean_url, resolver=resolver)
            except WebhookSSRFError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        clean_chat = body.telegram_chat_id.strip() if body.telegram_chat_id else None
        if body.telegram_enabled and not clean_chat:
            raise HTTPException(
                status_code=422,
                detail="Vui lòng nhập Telegram Chat ID khi bật kênh Telegram.",
            )
        secret = body.webhook_secret_hmac.strip() if body.webhook_secret_hmac else None
        updated = store.upsert_subscription(
            AlertSubscriptionUpsert(
                user_id=user.user_id,
                watchlist_id=body.watchlist_id,
                event_types=tuple(body.event_types) if body.event_types else (),
                in_app_enabled=body.in_app_enabled,
                email_digest_enabled=body.email_digest_enabled,
                email_frequency=body.email_frequency,
                telegram_enabled=body.telegram_enabled,
                telegram_chat_id=clean_chat,
                webhook_enabled=body.webhook_enabled,
                webhook_url=clean_url,
                webhook_secret_hmac=secret,
                preserve_existing_secret=(secret is None),
                merton_dtd_threshold=body.merton_dtd_threshold,
                is_active=body.is_active,
            )
        )
        app_store = get_app_store() if get_app_store is not None else None
        if app_store is not None:
            record_audit_event(
                app_store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="ALERT_SUBSCRIPTION_UPDATED",
                resource_type="alert_subscription",
                resource_id=str(updated.subscription_id),
                client_ip=extract_client_ip(request),
                metadata={
                    "in_app": updated.in_app_enabled,
                    "telegram": updated.telegram_enabled,
                    "email": updated.email_digest_enabled,
                    "webhook": updated.webhook_enabled,
                },
            )
        consent_ok = ConsentPurpose.ALERT_NOTIFICATIONS in user.consents
        return _to_sub_response(updated, has_alert_consent=consent_ok)

    @router.post("/webhooks/test", response_model=WebhookTestResponse)
    def test_webhook(
        request: Request,
        body: WebhookTestRequest = Body(default_factory=WebhookTestRequest),
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WebhookTestResponse:
        store = _require_store()
        sub = store.get_subscription(user.user_id)
        target_url = (body.webhook_url.strip() if body.webhook_url else None) or (
            sub.webhook_url if sub else None
        )
        if not target_url:
            raise HTTPException(
                status_code=422, detail="Chưa có Webhook URL để kiểm tra kết nối."
            )
        secret = (
            (body.webhook_secret_hmac.strip() if body.webhook_secret_hmac else None)
            or (sub.webhook_secret_hmac if sub else None)
            or "finintel-webhook-test-secret"
        )
        resolver = getattr(request.app.state, "_alerts_dns_resolver", None)
        http_client = getattr(request.app.state, "_alerts_http_client", None)
        payload: dict[str, Any] = {
            "event": "finintel.alerts.webhook_test",
            "ticker": "FININTEL",
            "severity": "INFO",
            "title_vi": "Kiểm tra kết nối Webhook cảnh báo sớm FinIntel V3.0",
            "sent_at": datetime.now(UTC).isoformat(),
        }
        try:
            res = send_signed_webhook(
                url=target_url,
                payload=payload,
                secret=secret,
                client=http_client,
                resolver=resolver,
            )
        except WebhookSSRFError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        sig_prefix = res.signature_header[:18] if res.signature_header else None
        return WebhookTestResponse(
            delivered=res.delivered,
            http_status=res.http_status,
            signature_header_prefix=sig_prefix,
            detail=(
                "Gửi thử Webhook thành công."
                if res.delivered
                else (res.error_detail or "Không thể gửi Webhook tới điểm cuối.")
            ),
        )

    return router


def mount_alerts_router(
    api: "FastAPI",
    *,
    alert_store: AlertStoreProtocol | None = None,
) -> None:
    """Mount ``/api/v3/alerts/*`` lazily sharing ``finintel_app`` store state."""
    lock = threading.Lock()
    if alert_store is not None:
        api.state._alert_store = alert_store

    def _resolve_app_store() -> AppStoreProtocol | None:
        resolver = getattr(api.state, "_resolve_app_store", None)
        if callable(resolver):
            return cast(AppStoreProtocol | None, resolver())
        return cast(AppStoreProtocol | None, getattr(api.state, "_app_store", None))

    def _resolve_alert_store() -> AlertStoreProtocol | None:
        existing = getattr(api.state, "_alert_store", None)
        if existing is not None:
            return cast(AlertStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_alert_store", None)
            if existing is not None:
                return cast(AlertStoreProtocol, existing)
            app_store = _resolve_app_store()
            if isinstance(app_store, InMemoryAppStore):
                mem_alerts = InMemoryAlertStore(app_store=app_store)
                api.state._alert_store = mem_alerts
                return mem_alerts
            from finintel.auth._pg_store import resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            from finintel.alerts._pg_store import PostgresAlertStore

            pg_alerts = PostgresAlertStore(dsn=dsn)
            api.state._alert_store = pg_alerts
            return pg_alerts

    api.state._resolve_alert_store = _resolve_alert_store
    api.include_router(
        create_alerts_router(
            get_alert_store=_resolve_alert_store,
            get_app_store=_resolve_app_store,
        )
    )


__all__ = ["create_alerts_router", "mount_alerts_router"]
