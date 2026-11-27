"""FastAPI management router for ``/api/v3/b2b/*`` and lazy mounting (V3-25)."""

import os
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from finintel.api_gateway._memory_store import InMemoryApiGatewayStore
from finintel.api_gateway._webhooks_router import attach_b2b_webhook_routes
from finintel.api_gateway.keys import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_RATE_LIMIT_PER_MIN,
    ApiGatewayStoreProtocol,
    ApiKeyRecord,
    generate_api_key,
    normalize_scopes,
    rotate_api_key_record,
)
from finintel.api_gateway.middleware import is_raw_redistribution_enabled
from finintel.api_gateway.open_router import create_open_research_router
from finintel.api_gateway.rate_limiter import SlidingWindowRateLimiter
from finintel.auth._store import AppStoreProtocol, InMemoryAppStore
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_role,
    verify_csrf_request,
)
from finintel.workspace.audit import record_audit_event

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from finintel.api_gateway._http_models import ApiKeyMetadataItem


def _to_key_item(rec: ApiKeyRecord) -> "ApiKeyMetadataItem":
    from finintel.api_gateway._http_models import ApiKeyMetadataItem

    return ApiKeyMetadataItem(
        key_id=rec.key_id,
        name=rec.name,
        key_prefix=rec.key_prefix,
        scopes=list(rec.scopes),
        rate_limit_per_min=rec.rate_limit_per_min,
        daily_limit=rec.daily_limit,
        is_active=rec.is_active,
        last_used_at=rec.last_used_at,
        expires_at=rec.expires_at,
        revoked_at=rec.revoked_at,
        created_at=rec.created_at,
    )


def create_b2b_management_router(
    *,
    get_store: Callable[[], ApiGatewayStoreProtocol | None],
    get_limiter: Callable[[], SlidingWindowRateLimiter],
    get_app_store: Callable[[], AppStoreProtocol | None] | None = None,
) -> "APIRouter":
    """Build ``/api/v3/b2b/*`` management router restricted to RESEARCHER & INTERNAL_OPS."""
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    from finintel.api_gateway._http_models import (
        ApiKeyCreateRequest,
        ApiKeyCreateResponse,
        ApiKeyListResponse,
        ApiKeyRevokeResponse,
        ApiUsageDailyPoint,
        ApiUsageSummaryResponse,
    )

    router = APIRouter(prefix="/api/v3/b2b", tags=["v3-b2b-api"])
    require_researcher = require_role(UserRole.RESEARCHER, UserRole.INTERNAL_OPS)

    def _require_store() -> ApiGatewayStoreProtocol:
        store = get_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ API Key hiện chưa được cấu hình cơ sở dữ liệu.",
            )
        return store

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
                resource_type="api_key",
                resource_id=resource_id,
                client_ip=extract_client_ip(request),
                metadata=metadata,
            )

    @router.post("/keys", response_model=ApiKeyCreateResponse, status_code=201)
    def create_api_key(
        request: Request,
        body: ApiKeyCreateRequest,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> ApiKeyCreateResponse:
        store = _require_store()
        try:
            scopes = normalize_scopes(body.scopes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        plaintext, prefix, key_hash = generate_api_key(prefix_mode=body.prefix_mode)
        expires_at = (
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days is not None
            else None
        )
        rec = store.create_api_key(
            user_id=user.user_id,
            name=body.name,
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
            rate_limit_per_min=DEFAULT_RATE_LIMIT_PER_MIN,
            daily_limit=DEFAULT_DAILY_LIMIT,
            expires_at=expires_at,
        )
        _audit(
            request,
            user,
            "API_KEY_CREATED",
            str(rec.key_id),
            {"key_prefix": rec.key_prefix, "scopes": list(rec.scopes)},
        )
        return ApiKeyCreateResponse(
            key=_to_key_item(rec),
            plaintext_key=plaintext,
        )

    @router.get("/keys", response_model=ApiKeyListResponse)
    def list_api_keys(
        user: AuthenticatedUser = Depends(require_researcher),
    ) -> ApiKeyListResponse:
        store = _require_store()
        items = [_to_key_item(k) for k in store.list_api_keys(user.user_id)]
        return ApiKeyListResponse(
            items=items,
            raw_redistribution_enabled=is_raw_redistribution_enabled(),
            default_rate_limit_per_min=DEFAULT_RATE_LIMIT_PER_MIN,
            default_daily_limit=DEFAULT_DAILY_LIMIT,
        )

    @router.post(
        "/keys/{key_id}/rotate",
        response_model=ApiKeyCreateResponse,
        status_code=201,
    )
    def rotate_api_key(
        request: Request,
        key_id: UUID,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> ApiKeyCreateResponse:
        store = _require_store()
        result = rotate_api_key_record(store, user_id=user.user_id, key_id=key_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy API Key.")
        created, plaintext, revoked = result
        get_limiter().mark_key_revoked(revoked.key_hash)
        _audit(
            request,
            user,
            "API_KEY_ROTATED",
            str(created.key_id),
            {"revoked_key_id": str(revoked.key_id), "new_prefix": created.key_prefix},
        )
        return ApiKeyCreateResponse(
            key=_to_key_item(created),
            plaintext_key=plaintext,
        )

    @router.delete("/keys/{key_id}", response_model=ApiKeyRevokeResponse)
    def revoke_api_key(
        request: Request,
        key_id: UUID,
        user: AuthenticatedUser = Depends(require_researcher),
        _csrf: None = Depends(verify_csrf_request),
    ) -> ApiKeyRevokeResponse:
        store = _require_store()
        now = datetime.now(UTC)
        revoked = store.revoke_api_key(user.user_id, key_id, revoked_at=now)
        if revoked is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy API Key.")
        get_limiter().mark_key_revoked(revoked.key_hash)
        _audit(
            request,
            user,
            "API_KEY_REVOKED",
            str(revoked.key_id),
            {"key_prefix": revoked.key_prefix},
        )
        return ApiKeyRevokeResponse(
            status="REVOKED",
            key_id=revoked.key_id,
            revoked_at=revoked.revoked_at or now,
        )

    @router.get("/usage", response_model=ApiUsageSummaryResponse)
    def get_api_usage(
        from_date: date | None = Query(default=None, alias="from"),
        to_date: date | None = Query(default=None, alias="to"),
        user: AuthenticatedUser = Depends(require_researcher),
    ) -> ApiUsageSummaryResponse:
        store = _require_store()
        today = datetime.now(UTC).date()
        end_d = to_date or today
        start_d = from_date or (end_d - timedelta(days=13))
        if start_d > end_d:
            raise HTTPException(
                status_code=422,
                detail="Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc.",
            )
        rows = store.list_usage(user.user_id, from_date=start_d, to_date=end_d)
        return ApiUsageSummaryResponse(
            from_date=start_d,
            to_date=end_d,
            total_requests=sum(r.request_count for r in rows),
            total_cache_hits=sum(r.cache_hits for r in rows),
            daily=[
                ApiUsageDailyPoint(
                    usage_date=r.usage_date,
                    key_id=r.key_id,
                    key_prefix=r.key_prefix,
                    endpoint=r.endpoint,
                    status_class=r.status_class,
                    request_count=r.request_count,
                    cache_hits=r.cache_hits,
                )
                for r in rows
            ],
        )

    attach_b2b_webhook_routes(
        router,
        require_store=_require_store,
        get_app_store=get_app_store,
    )
    return router


def mount_api_gateway_router(
    api: "FastAPI",
    *,
    valkey_url: str | None = None,
    gateway_store: ApiGatewayStoreProtocol | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> None:
    """Mount ``/api/v3/b2b/*`` and ``/api/v3/open/*`` lazily onto ``api``."""
    lock = threading.Lock()
    if gateway_store is not None:
        api.state._api_gateway_store = gateway_store

    resolved_valkey = (
        valkey_url
        if valkey_url is not None
        else (os.getenv("FININTEL_VALKEY_URL") or os.getenv("FININTEL_REDIS_URL") or "")
    ).strip() or None
    limiter = rate_limiter or SlidingWindowRateLimiter(valkey_url=resolved_valkey)
    api.state._api_gateway_limiter = limiter

    def _resolve_app_store() -> AppStoreProtocol | None:
        resolver = getattr(api.state, "_resolve_app_store", None)
        if callable(resolver):
            return cast(AppStoreProtocol | None, resolver())
        return cast(AppStoreProtocol | None, getattr(api.state, "_app_store", None))

    def _resolve_gateway_store() -> ApiGatewayStoreProtocol | None:
        existing = getattr(api.state, "_api_gateway_store", None)
        if existing is not None:
            return cast(ApiGatewayStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_api_gateway_store", None)
            if existing is not None:
                return cast(ApiGatewayStoreProtocol, existing)
            app_store = _resolve_app_store()
            if isinstance(app_store, InMemoryAppStore):
                mem_store = InMemoryApiGatewayStore()
                api.state._api_gateway_store = mem_store
                return mem_store
            from finintel.auth._pg_store import resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            from finintel.api_gateway._pg_store import PostgresApiGatewayStore

            pg_store = PostgresApiGatewayStore(dsn=dsn)
            api.state._api_gateway_store = pg_store
            return pg_store

    api.state._resolve_api_gateway_store = _resolve_gateway_store
    api.include_router(
        create_b2b_management_router(
            get_store=_resolve_gateway_store,
            get_limiter=lambda: cast(
                SlidingWindowRateLimiter, api.state._api_gateway_limiter
            ),
            get_app_store=_resolve_app_store,
        )
    )
    api.include_router(
        create_open_research_router(
            get_store=_resolve_gateway_store,
            get_limiter=lambda: cast(
                SlidingWindowRateLimiter, api.state._api_gateway_limiter
            ),
        )
    )


__all__ = ["create_b2b_management_router", "mount_api_gateway_router"]
