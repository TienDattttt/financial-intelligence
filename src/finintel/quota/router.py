"""FastAPI router for ``/api/v3/quota/me`` and ``/api/v3/ops/quota/system-budget`` (V3-19/V3-21)."""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    get_optional_user,
    require_role,
)
from finintel.quota.guard import (
    QuotaGuard,
    ValkeyBackedQuotaStore,
    get_default_quota_store,
)
from finintel.quota.schemas import build_quota_schemas

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI


def resolve_quota_guard_from_app(app: Any) -> QuotaGuard:
    """Resolve ``QuotaGuard`` attached to FastAPI ``app.state`` or fall back safely."""
    state = getattr(app, "state", None)
    existing = getattr(state, "_quota_guard", None) if state is not None else None
    if isinstance(existing, QuotaGuard):
        return existing
    guard = QuotaGuard(get_default_quota_store())
    if state is not None:
        state._quota_guard = guard
    return guard


def create_quota_router(
    *,
    get_quota_guard: Callable[[], QuotaGuard],
) -> "APIRouter":
    """Build standalone FastAPI router for Fair-Use Quota status & Ops system budget."""
    from fastapi import APIRouter, Depends, Request

    QuotaMeterStatus, MyQuotaResponse, SystemBudgetResponse = build_quota_schemas()
    router = APIRouter(tags=["v3-quota"])

    @router.get("/api/v3/quota/me", response_model=MyQuotaResponse)
    def get_my_quota(request: Request) -> Any:
        user = get_optional_user(request)
        guard = get_quota_guard()
        snap = guard.get_user_status(user=user)
        return MyQuotaResponse(
            authenticated=snap.authenticated,
            role=snap.role,
            quota_date=snap.quota_date,
            timezone="Asia/Ho_Chi_Minh",
            reset_at=snap.reset_at,
            cross_border_rag_consent=snap.cross_border_rag_consent,
            circuit_breaker_open=snap.circuit_breaker_open,
            circuit_breaker_reopen_at=snap.circuit_breaker_reopen_at,
            meters=[
                QuotaMeterStatus(
                    meter=m.meter,
                    used=m.used,
                    limit=m.limit,
                    remaining=m.remaining,
                    unlimited=m.unlimited,
                    reset_at=m.reset_at,
                )
                for m in snap.meters
            ],
        )

    @router.get(
        "/api/v3/ops/quota/system-budget",
        response_model=SystemBudgetResponse,
    )
    def get_system_budget(
        _ops_user: AuthenticatedUser = Depends(require_role(UserRole.INTERNAL_OPS)),
    ) -> Any:
        guard = get_quota_guard()
        budget = guard.get_system_budget()
        return SystemBudgetResponse(
            budget_date=budget.budget_date,
            timezone="Asia/Ho_Chi_Minh",
            reset_at=budget.reset_at,
            gemini_requests_used=budget.gemini_requests_used,
            gemini_requests_cap=budget.gemini_requests_cap,
            gemini_utilization_ratio=budget.gemini_utilization_ratio,
            circuit_breaker_ratio=budget.circuit_breaker_ratio,
            circuit_breaker_open=budget.circuit_breaker_open,
            vision_pages_used=budget.vision_pages_used,
            vision_pages_cap=budget.vision_pages_cap,
            vision_pages_remaining=budget.vision_pages_remaining,
        )

    return router


def mount_quota_router(
    api: "FastAPI",
    *,
    valkey_url: str | None = None,
    quota_guard: QuotaGuard | None = None,
) -> None:
    """Attach ``QuotaGuard`` to ``app.state`` and mount ``/api/v3/quota/*`` routes lazily."""
    if quota_guard is None:
        resolved_valkey = (
            valkey_url
            if valkey_url is not None
            else (
                os.getenv("FININTEL_VALKEY_URL")
                or os.getenv("FININTEL_REDIS_URL")
                or ""
            )
        ).strip() or None
        resolved_pg = os.getenv("FININTEL_APP_PG_DSN", "").strip() or None
        if resolved_valkey or resolved_pg:
            guard = QuotaGuard(
                ValkeyBackedQuotaStore(valkey_url=resolved_valkey, pg_dsn=resolved_pg)
            )
        else:
            from finintel.quota.guard import InMemoryQuotaStore

            guard = QuotaGuard(InMemoryQuotaStore())
    else:
        guard = quota_guard

    api.state._quota_guard = guard
    api.include_router(
        create_quota_router(get_quota_guard=lambda: resolve_quota_guard_from_app(api))
    )


__all__ = [
    "create_quota_router",
    "mount_quota_router",
    "resolve_quota_guard_from_app",
]
