"""FastAPI router for ``/api/v3/workspace/*``, ``/api/v3/audit/my-activity``, and ``/api/v3/ops/audit-logs``."""

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from finintel.auth._store import AppStoreProtocol, AuditLogRecord, WatchlistRecord
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_authenticated_mutation,
    require_authenticated_user,
    require_role,
)
from finintel.workspace.audit import list_my_audit_logs, list_ops_audit_logs
from finintel.workspace.service import WorkspaceError, WorkspaceService

if TYPE_CHECKING:
    from fastapi import APIRouter

    from finintel.workspace.schemas import AuditLogItemResponse, WatchlistResponseItem


def _to_watchlist_item(wl: WatchlistRecord) -> "WatchlistResponseItem":
    from finintel.workspace.schemas import WatchlistResponseItem

    return WatchlistResponseItem(
        watchlist_id=wl.watchlist_id,
        name=wl.name,
        is_default=wl.is_default,
        items=list(wl.items),
        created_at=wl.created_at,
        updated_at=wl.updated_at,
    )


def _to_audit_item(rec: AuditLogRecord) -> "AuditLogItemResponse":
    from finintel.workspace.schemas import AuditLogItemResponse

    return AuditLogItemResponse(
        audit_id=rec.audit_id,
        actor_user_id=rec.actor_user_id,
        actor_subject_hash=rec.actor_subject_hash,
        actor_role=rec.actor_role,
        action=rec.action,
        resource_type=rec.resource_type,
        resource_id=rec.resource_id,
        outcome=rec.outcome,
        ip_hash=rec.ip_hash,
        metadata_json=rec.metadata_json,
        created_at=rec.created_at,
    )


def create_workspace_router(
    *,
    get_workspace_service: Callable[[], WorkspaceService | None],
    get_store: Callable[[], AppStoreProtocol | None],
) -> "APIRouter":
    """Build standalone FastAPI router for V3 workspace & audit endpoints."""
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    from finintel.workspace.schemas import (
        AnalystNoteCreateRequest,
        AnalystNoteItem,
        AnalystNotesListResponse,
        AnalystNoteUpdateRequest,
        AuditLogsPageResponse,
        ImportLocalWatchlistRequest,
        ImportLocalWatchlistResponse,
        SavedScreenCreateRequest,
        SavedScreenItem,
        SavedScreensListResponse,
        WatchlistCreateRequest,
        WatchlistLimitsInfo,
        WatchlistRenameRequest,
        WatchlistResponseItem,
        WatchlistsListResponse,
        WorkspaceDeleteResponse,
    )

    router = APIRouter(tags=["v3-workspace"])

    def _require_svc() -> WorkspaceService:
        svc = get_workspace_service()
        if svc is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ không gian làm việc hiện chưa được cấu hình.",
            )
        return svc

    def _require_store() -> AppStoreProtocol:
        store = get_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ lưu trữ nhật ký hiện chưa được cấu hình.",
            )
        return store

    @router.get("/api/v3/workspace/watchlists", response_model=WatchlistsListResponse)
    def list_watchlists(
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> WatchlistsListResponse:
        svc = _require_svc()
        items, limits = svc.list_watchlists(user=user)
        return WatchlistsListResponse(
            watchlists=[_to_watchlist_item(w) for w in items],
            limits=WatchlistLimitsInfo(
                max_watchlists=limits.max_watchlists,
                max_items_per_watchlist=limits.max_items_per_watchlist,
                max_notes=limits.max_notes,
                max_saved_screens=limits.max_saved_screens,
            ),
        )

    @router.post(
        "/api/v3/workspace/watchlists",
        response_model=WatchlistResponseItem,
        status_code=201,
    )
    def create_watchlist(
        request: Request,
        body: WatchlistCreateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WatchlistResponseItem:
        svc = _require_svc()
        try:
            created = svc.create_watchlist(
                user=user, name=body.name, client_ip=extract_client_ip(request)
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return _to_watchlist_item(created)

    @router.patch(
        "/api/v3/workspace/watchlists/{watchlist_id}",
        response_model=WatchlistResponseItem,
    )
    def rename_watchlist(
        request: Request,
        watchlist_id: UUID,
        body: WatchlistRenameRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WatchlistResponseItem:
        svc = _require_svc()
        try:
            updated = svc.rename_watchlist(
                user=user,
                watchlist_id=watchlist_id,
                name=body.name,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return _to_watchlist_item(updated)

    @router.delete(
        "/api/v3/workspace/watchlists/{watchlist_id}",
        response_model=WorkspaceDeleteResponse,
    )
    def delete_watchlist(
        request: Request,
        watchlist_id: UUID,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WorkspaceDeleteResponse:
        svc = _require_svc()
        try:
            svc.delete_watchlist(
                user=user,
                watchlist_id=watchlist_id,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return WorkspaceDeleteResponse(status="DELETED", id=str(watchlist_id))

    @router.post(
        "/api/v3/workspace/watchlists/import-local",
        response_model=ImportLocalWatchlistResponse,
    )
    def import_local_watchlist(
        request: Request,
        body: ImportLocalWatchlistRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> ImportLocalWatchlistResponse:
        svc = _require_svc()
        try:
            res = svc.import_local_watchlist(
                user=user,
                raw_tickers=body.tickers,
                watchlist_name=body.watchlist_name,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return ImportLocalWatchlistResponse(
            watchlist=_to_watchlist_item(res.watchlist),
            added_count=res.added_count,
            skipped_duplicates=res.skipped_duplicates,
            truncated_by_limit=res.truncated_by_limit,
        )

    @router.put(
        "/api/v3/workspace/watchlists/{watchlist_id}/items/{ticker}",
        response_model=WatchlistResponseItem,
    )
    def add_watchlist_ticker(
        request: Request,
        watchlist_id: UUID,
        ticker: str,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WatchlistResponseItem:
        svc = _require_svc()
        try:
            wl = svc.add_ticker(
                user=user,
                watchlist_id=watchlist_id,
                ticker=ticker,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return _to_watchlist_item(wl)

    @router.delete(
        "/api/v3/workspace/watchlists/{watchlist_id}/items/{ticker}",
        response_model=WorkspaceDeleteResponse,
    )
    def remove_watchlist_ticker(
        request: Request,
        watchlist_id: UUID,
        ticker: str,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WorkspaceDeleteResponse:
        svc = _require_svc()
        try:
            svc.remove_ticker(
                user=user,
                watchlist_id=watchlist_id,
                ticker=ticker,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return WorkspaceDeleteResponse(status="DELETED", id=ticker.strip().upper())

    @router.get(
        "/api/v3/workspace/notes/{ticker}",
        response_model=AnalystNotesListResponse,
    )
    def list_notes(
        ticker: str,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> AnalystNotesListResponse:
        svc = _require_svc()
        try:
            notes = svc.list_notes(user=user, ticker=ticker, limit=limit, offset=offset)
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return AnalystNotesListResponse(
            ticker=ticker.strip().upper(),
            notes=[
                AnalystNoteItem(
                    note_id=n.note_id,
                    ticker=n.ticker,
                    period_end_date=n.period_end_date,
                    title=n.title,
                    content=n.content,
                    created_at=n.created_at,
                    updated_at=n.updated_at,
                )
                for n in notes
            ],
        )

    @router.post(
        "/api/v3/workspace/notes/{ticker}",
        response_model=AnalystNoteItem,
        status_code=201,
    )
    def create_note(
        request: Request,
        ticker: str,
        body: AnalystNoteCreateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AnalystNoteItem:
        svc = _require_svc()
        try:
            n = svc.create_note(
                user=user,
                ticker=ticker,
                period_end_date=body.period_end_date,
                title=body.title,
                content=body.content,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return AnalystNoteItem(
            note_id=n.note_id,
            ticker=n.ticker,
            period_end_date=n.period_end_date,
            title=n.title,
            content=n.content,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )

    @router.patch(
        "/api/v3/workspace/notes/{ticker}/{note_id}",
        response_model=AnalystNoteItem,
    )
    def update_note(
        request: Request,
        ticker: str,
        note_id: UUID,
        body: AnalystNoteUpdateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> AnalystNoteItem:
        svc = _require_svc()
        try:
            n = svc.update_note(
                user=user,
                ticker=ticker,
                note_id=note_id,
                title=body.title,
                content=body.content,
                period_end_date=body.period_end_date,
                clear_period_end_date=body.clear_period_end_date,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return AnalystNoteItem(
            note_id=n.note_id,
            ticker=n.ticker,
            period_end_date=n.period_end_date,
            title=n.title,
            content=n.content,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )

    @router.delete(
        "/api/v3/workspace/notes/{ticker}/{note_id}",
        response_model=WorkspaceDeleteResponse,
    )
    def delete_note(
        request: Request,
        ticker: str,
        note_id: UUID,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WorkspaceDeleteResponse:
        svc = _require_svc()
        try:
            svc.delete_note(
                user=user,
                ticker=ticker,
                note_id=note_id,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return WorkspaceDeleteResponse(status="DELETED", id=str(note_id))

    @router.get(
        "/api/v3/workspace/saved-screens",
        response_model=SavedScreensListResponse,
    )
    def list_saved_screens(
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> SavedScreensListResponse:
        svc = _require_svc()
        rows = svc.list_saved_screens(user=user)
        return SavedScreensListResponse(
            screens=[
                SavedScreenItem(
                    screen_id=s.screen_id,
                    name=s.name,
                    filters_json=s.filters_json,
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                )
                for s in rows
            ]
        )

    @router.post(
        "/api/v3/workspace/saved-screens",
        response_model=SavedScreenItem,
        status_code=201,
    )
    def create_saved_screen(
        request: Request,
        body: SavedScreenCreateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> SavedScreenItem:
        svc = _require_svc()
        try:
            s = svc.create_saved_screen(
                user=user,
                name=body.name,
                filters_json=body.filters_json,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return SavedScreenItem(
            screen_id=s.screen_id,
            name=s.name,
            filters_json=s.filters_json,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    @router.delete(
        "/api/v3/workspace/saved-screens/{screen_id}",
        response_model=WorkspaceDeleteResponse,
    )
    def delete_saved_screen(
        request: Request,
        screen_id: UUID,
        user: AuthenticatedUser = Depends(require_authenticated_mutation),
    ) -> WorkspaceDeleteResponse:
        svc = _require_svc()
        try:
            svc.delete_saved_screen(
                user=user,
                screen_id=screen_id,
                client_ip=extract_client_ip(request),
            )
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return WorkspaceDeleteResponse(status="DELETED", id=str(screen_id))

    @router.get("/api/v3/audit/my-activity", response_model=AuditLogsPageResponse)
    def get_my_activity(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: AuthenticatedUser = Depends(require_authenticated_user),
    ) -> AuditLogsPageResponse:
        store = _require_store()
        rows, total = list_my_audit_logs(
            store, user_id=user.user_id, limit=limit, offset=offset
        )
        return AuditLogsPageResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_to_audit_item(r) for r in rows],
        )

    @router.get("/api/v3/ops/audit-logs", response_model=AuditLogsPageResponse)
    def get_ops_audit_logs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        action: str | None = Query(None, max_length=80),
        actor_user_id: UUID | None = Query(None),
        _ops: AuthenticatedUser = Depends(require_role(UserRole.INTERNAL_OPS)),
    ) -> AuditLogsPageResponse:
        store = _require_store()
        rows, total = list_ops_audit_logs(
            store,
            limit=limit,
            offset=offset,
            action=action,
            actor_user_id=actor_user_id,
        )
        return AuditLogsPageResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_to_audit_item(r) for r in rows],
        )

    return router


__all__ = ["create_workspace_router"]
