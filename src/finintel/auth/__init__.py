"""FinIntel V3.0 Domestic Identity, Session Auth, and RBAC package."""

from __future__ import annotations

from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    get_optional_user,
    require_authenticated_user,
    require_role,
)

__all__ = [
    "AuthenticatedUser",
    "UserRole",
    "get_optional_user",
    "require_authenticated_user",
    "require_role",
]
