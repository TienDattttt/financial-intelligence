"""FastAPI router for ``/api/v3/hitl/*`` expert triage endpoints (V3-29)."""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from finintel.auth._store import AppStoreProtocol, InMemoryAppStore
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_role,
    verify_csrf_request,
)
from finintel.hitl._store import HitlStoreProtocol, InMemoryHitlStore, PostgresHitlStore
from finintel.hitl.service import HitlService

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI


def _lookup_icb_level2_readonly(request: Any, ticker: str) -> str | None:
    """Best-effort read-only lookup of ICB Level 2 from active snapshot (never writes)."""
    try:
        repo = getattr(request.app.state, "repository", None)
        if repo is None:
            return None
        with repo.snapshot_session() as pinned:
            cols = pinned.get_table_columns("issuers.parquet")
            if "icb_code_level2" not in cols:
                return None
            frame = pinned.read_table(
                "issuers.parquet",
                columns=["icb_code_level2"],
                filters={"ticker": ticker},
            )
            if frame.is_empty():
                return None
            val = frame.row(0, named=True).get("icb_code_level2")
            return str(val).strip() if val is not None and str(val).strip() else None
    except Exception:
        return None


def create_hitl_router(
    *,
    get_hitl_store: Callable[[], HitlStoreProtocol | None],
    get_app_store: Callable[[], AppStoreProtocol | None] | None = None,
) -> "APIRouter":
    """Create ``/api/v3/hitl/*`` router with strict RBAC on summary."""
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    from finintel.hitl.schemas import (
        HitlFeedbackItem,
        HitlFeedbackRequest,
        HitlSummaryResponse,
    )

    router = APIRouter(prefix="/api/v3/hitl", tags=["v3-hitl"])
    require_any_analyst = require_role(
        UserRole.USER, UserRole.RESEARCHER, UserRole.INTERNAL_OPS
    )
    require_ops = require_role(UserRole.INTERNAL_OPS)

    def _service() -> HitlService:
        store = get_hitl_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ phản hồi chuyên gia HITL chưa được cấu hình cơ sở dữ liệu.",
            )
        audit = get_app_store() if get_app_store is not None else None
        return HitlService(store, audit_store=audit)

    @router.post("/feedback", response_model=HitlFeedbackItem, status_code=201)
    def submit_hitl_feedback(
        request: Request,
        body: HitlFeedbackRequest,
        actor: AuthenticatedUser = Depends(require_any_analyst),
        _: None = Depends(verify_csrf_request),
    ) -> HitlFeedbackItem:
        svc = _service()
        resolved_icb = (
            None
            if body.icb_code_level2
            else _lookup_icb_level2_readonly(request, body.ticker.strip().upper())
        )
        return svc.submit_feedback(
            actor=actor,
            payload=body,
            resolved_icb_level2=resolved_icb,
            client_ip=extract_client_ip(request),
        )

    @router.get("/summary", response_model=HitlSummaryResponse)
    def get_hitl_summary(
        signal_code: str | None = Query(default=None, max_length=64),
        icb_code_level2: str | None = Query(default=None, max_length=32),
        _actor: AuthenticatedUser = Depends(require_ops),
    ) -> HitlSummaryResponse:
        svc = _service()
        return svc.summarize(
            signal_code=signal_code,
            icb_code_level2=icb_code_level2,
        )

    return router


def mount_hitl_router(
    api: "FastAPI",
    *,
    hitl_store: HitlStoreProtocol | None = None,
) -> None:
    """Mount ``/api/v3/hitl/*`` lazily sharing ``finintel_app`` store state."""
    lock = threading.Lock()
    if hitl_store is not None:
        api.state._hitl_store = hitl_store

    def _resolve_app_store() -> AppStoreProtocol | None:
        resolver = getattr(api.state, "_resolve_app_store", None)
        if callable(resolver):
            return cast(AppStoreProtocol | None, resolver())
        return cast(AppStoreProtocol | None, getattr(api.state, "_app_store", None))

    def _resolve_hitl_store() -> HitlStoreProtocol | None:
        existing = getattr(api.state, "_hitl_store", None)
        if existing is not None:
            return cast(HitlStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_hitl_store", None)
            if existing is not None:
                return cast(HitlStoreProtocol, existing)
            app_store = _resolve_app_store()
            if isinstance(app_store, InMemoryAppStore):
                mem_hitl = InMemoryHitlStore()
                api.state._hitl_store = mem_hitl
                return mem_hitl
            from finintel.auth._pg_store import resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            pg_hitl = PostgresHitlStore(dsn=dsn)
            api.state._hitl_store = pg_hitl
            return pg_hitl

    api.state._resolve_hitl_store = _resolve_hitl_store
    api.include_router(
        create_hitl_router(
            get_hitl_store=_resolve_hitl_store,
            get_app_store=_resolve_app_store,
        )
    )


__all__ = ["create_hitl_router", "mount_hitl_router"]
