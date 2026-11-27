"""B2B webhook registration, SSRF validation, and HMAC-SHA256 test routes (V3-25)."""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from finintel.alerts.dispatcher import (
    WebhookSSRFError,
    send_signed_webhook,
    validate_webhook_url,
)
from finintel.api_gateway.keys import ApiGatewayStoreProtocol, B2BWebhookRecord
from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_role,
    verify_csrf_request,
)
from finintel.workspace.audit import record_audit_event

if TYPE_CHECKING:
    from fastapi import APIRouter

    from finintel.api_gateway._http_models import B2BWebhookItem


def _to_webhook_item(rec: B2BWebhookRecord) -> "B2BWebhookItem":
    from finintel.api_gateway._http_models import B2BWebhookItem

    return B2BWebhookItem(
        endpoint_id=rec.endpoint_id,
        label=rec.label,
        url=rec.url,
        event_types=list(rec.event_types),
        secret_configured=bool(rec.secret_hmac),
        is_active=rec.is_active,
        last_status=rec.last_status,
        last_error=rec.last_error,
        last_delivered_at=rec.last_delivered_at,
        created_at=rec.created_at,
    )


def attach_b2b_webhook_routes(
    router: "APIRouter",
    *,
    require_store: Callable[[], ApiGatewayStoreProtocol],
    get_app_store: Callable[[], AppStoreProtocol | None] | None,
) -> None:
    """Register ``/api/v3/b2b/webhooks*`` routes onto ``router``."""
    from fastapi import Depends, HTTPException, Request

    from finintel.api_gateway._http_models import (
        B2BWebhookCreateRequest,
        B2BWebhookCreateResponse,
        B2BWebhookDeleteResponse,
        B2BWebhookListResponse,
        B2BWebhookTestResponse,
    )

    require_researcher = require_role(UserRole.RESEARCHER, UserRole.INTERNAL_OPS)

    def _audit(
        request: Request,
        user: AuthenticatedUser,
        action: str,
        resource_id: str,
        metadata: dict[str, object],
    ) -> None:
        app_store = get_app_store() if get_app_store is not None else None
        if app_store is not None:
            record_audit_event(
                app_store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action=action,
                resource_type="b2b_webhook",
                resource_id=resource_id,
                client_ip=extract_client_ip(request),
                metadata=metadata,
            )

    @router.post(
        "/webhooks",
        response_model=B2BWebhookCreateResponse,
        status_code=201,
    )
    def create_b2b_webhook(
        request: Request,
        body: B2BWebhookCreateRequest,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> B2BWebhookCreateResponse:
        store = require_store()
        resolver = getattr(request.app.state, "_alerts_dns_resolver", None)
        try:
            clean_url, _resolved_ips = validate_webhook_url(body.url, resolver=resolver)
        except WebhookSSRFError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        secret = (
            body.secret_hmac.strip()
            if body.secret_hmac and body.secret_hmac.strip()
            else f"whsec_{secrets.token_urlsafe(24)}"
        )
        rec = store.create_webhook(
            user_id=user.user_id,
            label=body.label,
            url=clean_url,
            secret_hmac=secret,
            event_types=tuple(body.event_types),
        )
        _audit(
            request,
            user,
            "B2B_WEBHOOK_CREATED",
            str(rec.endpoint_id),
            {"label": rec.label},
        )
        return B2BWebhookCreateResponse(
            webhook=_to_webhook_item(rec),
            secret_hmac=secret,
        )

    @router.get("/webhooks", response_model=B2BWebhookListResponse)
    def list_b2b_webhooks(
        user: AuthenticatedUser = Depends(require_researcher),
    ) -> B2BWebhookListResponse:
        store = require_store()
        items = [_to_webhook_item(w) for w in store.list_webhooks(user.user_id)]
        return B2BWebhookListResponse(items=items)

    @router.delete(
        "/webhooks/{endpoint_id}",
        response_model=B2BWebhookDeleteResponse,
    )
    def delete_b2b_webhook(
        request: Request,
        endpoint_id: UUID,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> B2BWebhookDeleteResponse:
        store = require_store()
        if not store.delete_webhook(user.user_id, endpoint_id):
            raise HTTPException(
                status_code=404, detail="Không tìm thấy Webhook endpoint."
            )
        _audit(request, user, "B2B_WEBHOOK_DELETED", str(endpoint_id), {})
        return B2BWebhookDeleteResponse(status="DELETED", endpoint_id=endpoint_id)

    @router.post(
        "/webhooks/{endpoint_id}/test",
        response_model=B2BWebhookTestResponse,
    )
    def test_b2b_webhook(
        request: Request,
        endpoint_id: UUID,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> B2BWebhookTestResponse:
        store = require_store()
        rec = store.get_webhook(user.user_id, endpoint_id)
        if rec is None:
            raise HTTPException(
                status_code=404, detail="Không tìm thấy Webhook endpoint."
            )
        resolver = getattr(request.app.state, "_alerts_dns_resolver", None)
        http_client = getattr(request.app.state, "_alerts_http_client", None)
        payload: dict[str, Any] = {
            "event": "finintel.b2b.webhook_test",
            "endpoint_id": str(rec.endpoint_id),
            "sent_at": datetime.now(UTC).isoformat(),
        }
        try:
            res = send_signed_webhook(
                url=rec.url,
                payload=payload,
                secret=rec.secret_hmac,
                client=http_client,
                resolver=resolver,
            )
        except WebhookSSRFError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        store.update_webhook_delivery(
            rec.endpoint_id,
            http_status=res.http_status,
            error=res.error_detail,
            delivered_at=datetime.now(UTC) if res.delivered else None,
        )
        sig_prefix = res.signature_header[:18] if res.signature_header else None
        return B2BWebhookTestResponse(
            delivered=res.delivered,
            http_status=res.http_status,
            signature_header_prefix=sig_prefix,
            detail=(
                "Gửi thử Webhook thành công."
                if res.delivered
                else (res.error_detail or "Không thể gửi Webhook tới điểm cuối.")
            ),
        )


__all__ = ["attach_b2b_webhook_routes"]
