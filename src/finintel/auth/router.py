"""FastAPI router for ``/api/v3/auth/*`` and lazy app-router mounting."""

import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    extract_client_ip,
    get_optional_user,
    require_authenticated_mutation,
    require_authenticated_user,
    verify_csrf_request,
)
from finintel.auth.email import ResendEmailAdapter, TransactionalEmailSender
from finintel.auth.rate_limit import AuthRateLimiter
from finintel.auth.service import (
    SESSION_TTL_DAYS,
    AuthError,
    AuthService,
    UserProfileView,
)

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from finintel.auth.schemas import UserProfileResponse


def _cookie_secure_flag() -> bool:
    raw = os.getenv("FININTEL_COOKIE_SECURE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _to_profile_response(profile: UserProfileView) -> "UserProfileResponse":
    from finintel.auth.schemas import ConsentStatusItem, UserProfileResponse

    return UserProfileResponse(
        user_id=profile.user_id,
        email=profile.email,
        display_name=profile.display_name,
        role=profile.role,
        consents=[
            ConsentStatusItem(
                purpose=c.purpose,
                policy_version=c.policy_version,
                granted=c.granted,
                active=c.active,
                recorded_at=c.recorded_at,
                withdrawn_at=c.withdrawn_at,
            )
            for c in profile.consents
        ],
        active_sessions_count=profile.active_sessions_count,
        created_at=profile.created_at,
    )


def create_auth_router(
    *,
    get_auth_service: Callable[[], AuthService | None],
) -> "APIRouter":
    """Build standalone FastAPI router for ``/api/v3/auth/*`` endpoints."""
    from fastapi import (
        APIRouter,
        Body,
        Depends,
        Header,
        HTTPException,
        Request,
        Response,
    )

    from finintel.auth.schemas import (
        AuthActionMessageResponse,
        DataDeletionReceiptResponse,
        DeleteAccountRequest,
        LoginRequest,
        PasswordChangeBody,
        PasswordResetConfirmBody,
        PasswordResetRequestBody,
        RegisterRequest,
        SessionStatusResponse,
        SessionsListResponse,
        SessionSummaryItem,
        UpdateProfileRequest,
        UserProfileResponse,
    )

    router = APIRouter(prefix="/api/v3/auth", tags=["v3-auth"])

    def _require_service() -> AuthService:
        svc = get_auth_service()
        if svc is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ định danh hiện chưa được cấu hình trên máy chủ.",
            )
        return svc

    def _raise_auth_http(exc: AuthError) -> HTTPException:
        return HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers or None,
        )

    @router.post("/register", response_model=UserProfileResponse, status_code=201)
    def register(request: Request, body: RegisterRequest) -> UserProfileResponse:
        svc = _require_service()
        try:
            profile = svc.register(
                email=body.email,
                password=body.password,
                display_name=body.display_name,
                accepted_consent_version=body.accepted_consent_version,
                accept_account_core=body.accept_account_core,
                accept_alert_notifications=body.accept_alert_notifications,
                accept_cross_border_llm_rag=body.accept_cross_border_llm_rag,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return _to_profile_response(profile)

    @router.post("/login", response_model=UserProfileResponse)
    def login(
        request: Request,
        response: Response,
        body: LoginRequest,
    ) -> UserProfileResponse:
        svc = _require_service()
        try:
            result = svc.login(
                email=body.email,
                password=body.password,
                client_ip=extract_client_ip(request),
                user_agent=request.headers.get("user-agent", ""),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=result.session_token,
            max_age=SESSION_TTL_DAYS * 86400,
            httponly=True,
            secure=_cookie_secure_flag(),
            samesite="lax",
            path="/",
        )
        return _to_profile_response(result.profile)

    @router.get("/session", response_model=SessionStatusResponse)
    def session_status(
        request: Request,
        response: Response,
    ) -> SessionStatusResponse:
        svc = get_auth_service()
        if svc is None:
            return SessionStatusResponse(
                authenticated=False,
                auth_configured=False,
                user=None,
            )
        user = get_optional_user(request)
        if user is None:
            if request.cookies.get(SESSION_COOKIE_NAME):
                response.delete_cookie(
                    key=SESSION_COOKIE_NAME,
                    path="/",
                    httponly=True,
                    secure=_cookie_secure_flag(),
                    samesite="lax",
                )
            return SessionStatusResponse(
                authenticated=False,
                auth_configured=True,
                user=None,
            )
        try:
            profile = svc.get_profile(user)
        except AuthError:
            return SessionStatusResponse(
                authenticated=False,
                auth_configured=True,
                user=None,
            )
        return SessionStatusResponse(
            authenticated=True,
            auth_configured=True,
            user=_to_profile_response(profile),
        )

    @router.post("/logout", response_model=AuthActionMessageResponse)
    def logout(
        request: Request,
        response: Response,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        svc.logout(user=user, client_ip=extract_client_ip(request))
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=_cookie_secure_flag(),
            samesite="lax",
        )
        return AuthActionMessageResponse(status="OK", message="Đã đăng xuất.")

    @router.post("/logout-all", response_model=AuthActionMessageResponse)
    def logout_all(
        request: Request,
        response: Response,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        count = svc.logout_all(user=user, client_ip=extract_client_ip(request))
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=_cookie_secure_flag(),
            samesite="lax",
        )
        return AuthActionMessageResponse(
            status="OK",
            message=f"Đã thu hồi {count} phiên đăng nhập.",
        )

    @router.post(
        "/password-reset/request",
        response_model=AuthActionMessageResponse,
        status_code=202,
    )
    def password_reset_request(
        request: Request, body: PasswordResetRequestBody
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        try:
            svc.request_password_reset(
                email=body.email,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return AuthActionMessageResponse(
            status="ACCEPTED",
            message=(
                "Nếu địa chỉ email tồn tại trong hệ thống, hướng dẫn đặt lại "
                "mật khẩu (hiệu lực 15 phút) đã được gửi."
            ),
        )

    @router.post("/password-reset/confirm", response_model=AuthActionMessageResponse)
    def password_reset_confirm(
        request: Request, body: PasswordResetConfirmBody
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        try:
            svc.confirm_password_reset(
                token=body.token,
                new_password=body.new_password,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return AuthActionMessageResponse(
            status="OK",
            message="Mật khẩu đã được đặt lại thành công. Vui lòng đăng nhập lại.",
        )

    @router.post("/password-change", response_model=AuthActionMessageResponse)
    def password_change(
        request: Request,
        body: PasswordChangeBody,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        try:
            svc.change_password(
                user=user,
                current_password=body.current_password,
                new_password=body.new_password,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return AuthActionMessageResponse(
            status="OK",
            message="Đổi mật khẩu thành công.",
        )

    @router.get("/me", response_model=UserProfileResponse)
    def get_me(
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> UserProfileResponse:
        svc = _require_service()
        try:
            profile = svc.get_profile(user)
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return _to_profile_response(profile)

    @router.patch("/me", response_model=UserProfileResponse)
    def patch_me(
        request: Request,
        body: UpdateProfileRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> UserProfileResponse:
        svc = _require_service()
        try:
            profile = svc.update_profile(
                user=user,
                display_name=body.display_name,
                consents=body.consents,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return _to_profile_response(profile)

    @router.get("/sessions", response_model=SessionsListResponse)
    def get_sessions(
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> SessionsListResponse:
        svc = _require_service()
        sessions = svc.list_sessions(user)
        return SessionsListResponse(
            sessions=[
                SessionSummaryItem(
                    session_id=s.session_id,
                    created_at=s.created_at,
                    last_seen_at=s.last_seen_at,
                    expires_at=s.expires_at,
                    is_current=s.is_current,
                )
                for s in sessions
            ]
        )

    @router.delete("/sessions/{session_id}", response_model=AuthActionMessageResponse)
    def revoke_one_session(
        request: Request,
        session_id: UUID,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AuthActionMessageResponse:
        svc = _require_service()
        try:
            svc.revoke_session(
                user=user,
                session_id=session_id,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        return AuthActionMessageResponse(status="OK", message="Đã thu hồi phiên.")

    @router.delete("/me", response_model=DataDeletionReceiptResponse)
    def delete_me(
        request: Request,
        response: Response,
        body: DeleteAccountRequest | None = Body(default=None),
        confirm_password_header: str | None = Header(
            default=None, alias="X-Confirm-Password"
        ),
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> DataDeletionReceiptResponse:
        verify_csrf_request(request)
        svc = _require_service()
        pw = (
            body.current_password
            if body is not None
            else (confirm_password_header or "")
        )
        reason = body.reason if body is not None else None
        try:
            receipt = svc.delete_account(
                user=user,
                current_password=pw,
                reason=reason,
                client_ip=extract_client_ip(request),
            )
        except AuthError as exc:
            raise _raise_auth_http(exc) from None
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=_cookie_secure_flag(),
            samesite="lax",
        )
        return DataDeletionReceiptResponse(
            request_id=receipt.request_id,
            status="COMPLETED",
            sla_days=receipt.sla_days,
            anonymized_audit_logs=receipt.anonymized_audit_logs,
            completed_at=receipt.completed_at,
            legal_basis=receipt.legal_basis,
        )

    return router


def mount_identity_workspace_routers(
    api: "FastAPI",
    *,
    valkey_url: str | None = None,
    app_store: AppStoreProtocol | None = None,
    email_sender: TransactionalEmailSender | None = None,
) -> None:
    """Mount V3 auth and workspace routers lazily without breaking offline read-only boot."""
    lock = threading.Lock()
    api.state._app_store = app_store
    api.state._email_sender = email_sender
    api.state._rate_limiter = AuthRateLimiter(valkey_url=valkey_url)

    def _resolve_store() -> AppStoreProtocol | None:
        existing = getattr(api.state, "_app_store", None)
        if existing is not None:
            return cast(AppStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_app_store", None)
            if existing is not None:
                return cast(AppStoreProtocol, existing)
            from finintel.auth._pg_store import PostgresAppStore, resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            pg_store = PostgresAppStore(dsn=dsn)
            api.state._app_store = pg_store
            return pg_store

    def _resolve_auth_service() -> AuthService | None:
        store = _resolve_store()
        if store is None:
            return None
        cached = getattr(api.state, "_auth_service", None)
        if (
            isinstance(cached, AuthService)
            and getattr(api.state, "_auth_service_store", None) is store
        ):
            return cached
        sender = getattr(api.state, "_email_sender", None) or ResendEmailAdapter()
        svc = AuthService(
            store,
            rate_limiter=api.state._rate_limiter,
            email_sender=sender,
        )
        api.state._auth_service = svc
        api.state._auth_service_store = store
        return svc

    def _resolve_workspace_service() -> Any | None:
        from finintel.workspace.service import WorkspaceService

        store = _resolve_store()
        if store is None:
            return None
        cached = getattr(api.state, "_workspace_service", None)
        if (
            isinstance(cached, WorkspaceService)
            and getattr(api.state, "_workspace_service_store", None) is store
        ):
            return cached
        svc = WorkspaceService(store)
        api.state._workspace_service = svc
        api.state._workspace_service_store = store
        return svc

    api.state._resolve_app_store = _resolve_store
    api.state._resolve_auth_service = _resolve_auth_service
    api.state._resolve_workspace_service = _resolve_workspace_service

    from finintel.workspace.router import create_workspace_router

    api.include_router(create_auth_router(get_auth_service=_resolve_auth_service))
    api.include_router(
        create_workspace_router(
            get_workspace_service=_resolve_workspace_service,
            get_store=_resolve_store,
        )
    )


__all__ = ["create_auth_router", "mount_identity_workspace_routers"]
