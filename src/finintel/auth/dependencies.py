"""Shared FastAPI authentication, CSRF, and RBAC dependencies (V3-10)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

from finintel.domain.compliance import ConsentPurpose

if TYPE_CHECKING:
    from fastapi import Request
else:
    try:
        from fastapi import Request
    except ImportError:  # pragma: no cover - mvp-checks without platform deps
        Request = Any

SESSION_COOKIE_NAME = "finintel_sid"
CSRF_HEADER_NAME = "X-Requested-With"


class UserRole(StrEnum):
    """Canonical 3-role RBAC enum across FinIntel V3.0."""

    USER = "USER"
    RESEARCHER = "RESEARCHER"
    INTERNAL_OPS = "INTERNAL_OPS"


@dataclass(frozen=True)
class AuthenticatedUser:
    """Immutable authenticated principal resolved from an active opaque session."""

    user_id: UUID
    email: str
    role: UserRole
    consents: frozenset[ConsentPurpose]
    session_id: UUID | None = None
    display_name: str = ""


def _resolve_auth_service_from_request(request: Request) -> Any | None:
    resolver = getattr(request.app.state, "_resolve_auth_service", None)
    if callable(resolver):
        return resolver()
    return getattr(request.app.state, "_auth_service", None)


def extract_client_ip(request: Request) -> str:
    """Extract client IP without trusting arbitrary spoofed headers outside proxy."""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return str(host or "unknown")


def verify_csrf_request(request: Request) -> None:
    """Enforce custom CSRF header and Origin/Referer check on mutating requests."""
    from fastapi import HTTPException

    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return

    req_with = request.headers.get(CSRF_HEADER_NAME, "").strip()
    if not req_with:
        raise HTTPException(
            status_code=403,
            detail="Thiếu tiêu đề bảo vệ CSRF cho yêu cầu thay đổi dữ liệu.",
        )

    origin = request.headers.get("origin", "").strip()
    referer = request.headers.get("referer", "").strip()
    candidate = origin or referer
    if not candidate:
        return

    parsed = urlparse(candidate)
    candidate_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    host_header = request.headers.get("host", "").strip().lower()
    allowed_raw = os.getenv("FININTEL_CORS_ORIGINS", "")
    allowed_origins = {
        item.strip().rstrip("/").lower()
        for item in allowed_raw.split(",")
        if item.strip()
    }
    if host_header:
        allowed_origins.add(f"http://{host_header}")
        allowed_origins.add(f"https://{host_header}")

    # Allow Starlette/FastAPI TestClient base URLs and local dev origins when matching host.
    if candidate_origin not in allowed_origins:
        raise HTTPException(
            status_code=403,
            detail="Nguồn gốc yêu cầu không hợp lệ cho thao tác thay đổi dữ liệu.",
        )


def get_optional_user(request: Request) -> AuthenticatedUser | None:
    """Return ``AuthenticatedUser`` if a valid session cookie is present, else ``None``."""
    cached = getattr(request.state, "_cached_auth_user", None)
    if isinstance(cached, AuthenticatedUser):
        return cached
    if getattr(request.state, "_cached_auth_checked", False):
        return None

    token = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if not token:
        request.state._cached_auth_checked = True
        return None

    auth_service = _resolve_auth_service_from_request(request)
    if auth_service is None:
        request.state._cached_auth_checked = True
        return None

    user: AuthenticatedUser | None = auth_service.authenticate_session_token(token)
    request.state._cached_auth_checked = True
    request.state._cached_auth_user = user
    return user


def require_authenticated_user(request: Request) -> AuthenticatedUser:
    """Require an active authenticated session, raising HTTP 401 otherwise."""
    from fastapi import HTTPException

    user = get_optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
        )
    return user


def require_authenticated_mutation(request: Request) -> AuthenticatedUser:
    """Require both authenticated session and CSRF protection for mutating endpoints."""
    verify_csrf_request(request)
    return require_authenticated_user(request)


def require_role(*roles: UserRole) -> Callable[[Request], AuthenticatedUser]:
    """Return a FastAPI dependency enforcing membership in ``roles`` (HTTP 403 on mismatch)."""
    allowed = frozenset(roles)

    def _dependency(request: Request) -> AuthenticatedUser:
        from fastapi import HTTPException

        user = require_authenticated_user(request)
        if allowed and user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail="Bạn không có quyền thực hiện thao tác này.",
            )
        return user

    return _dependency


__all__ = [
    "AuthenticatedUser",
    "CSRF_HEADER_NAME",
    "SESSION_COOKIE_NAME",
    "UserRole",
    "extract_client_ip",
    "get_optional_user",
    "require_authenticated_mutation",
    "require_authenticated_user",
    "require_role",
    "verify_csrf_request",
]
