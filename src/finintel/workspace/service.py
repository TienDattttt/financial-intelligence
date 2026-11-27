"""Workspace business logic: Multi-Watchlist, Analyst Notes, and Saved Screens (V3-11)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from finintel.auth._store import (
    AnalystNoteRecord,
    AppStoreProtocol,
    DuplicateResourceError,
    SavedScreenRecord,
    WatchlistRecord,
)
from finintel.auth.dependencies import AuthenticatedUser, UserRole
from finintel.workspace.audit import record_audit_event

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}$")


@dataclass(frozen=True)
class WorkspaceRoleLimits:
    max_watchlists: int
    max_items_per_watchlist: int
    max_notes: int
    max_saved_screens: int


ROLE_WORKSPACE_LIMITS: dict[UserRole, WorkspaceRoleLimits] = {
    UserRole.USER: WorkspaceRoleLimits(
        max_watchlists=1,
        max_items_per_watchlist=50,
        max_notes=100,
        max_saved_screens=15,
    ),
    UserRole.RESEARCHER: WorkspaceRoleLimits(
        max_watchlists=10,
        max_items_per_watchlist=100,
        max_notes=500,
        max_saved_screens=50,
    ),
    UserRole.INTERNAL_OPS: WorkspaceRoleLimits(
        max_watchlists=20,
        max_items_per_watchlist=200,
        max_notes=1000,
        max_saved_screens=100,
    ),
}


class WorkspaceError(Exception):
    """Domain workspace error mapped cleanly to HTTP status codes."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_ticker(raw_ticker: str) -> str:
    clean = raw_ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean):
        raise WorkspaceError(422, "Mã chứng khoán chỉ gồm 1–10 ký tự chữ hoặc số.")
    return clean


@dataclass(frozen=True)
class ImportLocalResult:
    watchlist: WatchlistRecord
    added_count: int
    skipped_duplicates: int
    truncated_by_limit: int


class WorkspaceService:
    """User-isolated workspace operations backed by ``AppStoreProtocol``."""

    def __init__(self, store: AppStoreProtocol) -> None:
        self._store = store

    @staticmethod
    def limits_for(role: UserRole) -> WorkspaceRoleLimits:
        return ROLE_WORKSPACE_LIMITS[role]

    def list_watchlists(
        self, *, user: AuthenticatedUser
    ) -> tuple[list[WatchlistRecord], WorkspaceRoleLimits]:
        return self._store.list_watchlists(user.user_id), self.limits_for(user.role)

    def create_watchlist(
        self,
        *,
        user: AuthenticatedUser,
        name: str,
        client_ip: str = "",
    ) -> WatchlistRecord:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 60:
            raise WorkspaceError(422, "Tên danh sách theo dõi phải từ 1–60 ký tự.")
        limits = self.limits_for(user.role)
        existing = self._store.list_watchlists(user.user_id)
        if len(existing) >= limits.max_watchlists:
            raise WorkspaceError(
                403,
                f"Vai trò hiện tại cho phép tối đa {limits.max_watchlists} danh sách theo dõi.",
            )
        try:
            created = self._store.create_watchlist(
                user_id=user.user_id,
                name=clean_name,
                is_default=(len(existing) == 0),
            )
        except DuplicateResourceError as exc:
            raise WorkspaceError(
                409, "Đã tồn tại danh sách theo dõi có tên này."
            ) from exc

        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_WATCHLIST_CREATE",
            resource_type="watchlist",
            resource_id=str(created.watchlist_id),
            client_ip=client_ip,
        )
        return created

    def rename_watchlist(
        self,
        *,
        user: AuthenticatedUser,
        watchlist_id: UUID,
        name: str,
        client_ip: str = "",
    ) -> WatchlistRecord:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 60:
            raise WorkspaceError(422, "Tên danh sách theo dõi phải từ 1–60 ký tự.")
        try:
            updated = self._store.update_watchlist(
                user_id=user.user_id,
                watchlist_id=watchlist_id,
                name=clean_name,
            )
        except DuplicateResourceError as exc:
            raise WorkspaceError(
                409, "Đã tồn tại danh sách theo dõi có tên này."
            ) from exc
        if updated is None:
            raise WorkspaceError(404, "Không tìm thấy danh sách theo dõi.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_WATCHLIST_RENAME",
            resource_type="watchlist",
            resource_id=str(watchlist_id),
            client_ip=client_ip,
        )
        return updated

    def delete_watchlist(
        self,
        *,
        user: AuthenticatedUser,
        watchlist_id: UUID,
        client_ip: str = "",
    ) -> None:
        deleted = self._store.delete_watchlist(
            user_id=user.user_id, watchlist_id=watchlist_id
        )
        if not deleted:
            raise WorkspaceError(404, "Không tìm thấy danh sách theo dõi.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_WATCHLIST_DELETE",
            resource_type="watchlist",
            resource_id=str(watchlist_id),
            client_ip=client_ip,
        )

    def add_ticker(
        self,
        *,
        user: AuthenticatedUser,
        watchlist_id: UUID,
        ticker: str,
        client_ip: str = "",
    ) -> WatchlistRecord:
        clean_ticker = normalize_ticker(ticker)
        limits = self.limits_for(user.role)
        watchlists = self._store.list_watchlists(user.user_id)
        target = next((w for w in watchlists if w.watchlist_id == watchlist_id), None)
        if target is None:
            raise WorkspaceError(404, "Không tìm thấy danh sách theo dõi.")
        if clean_ticker not in target.items:
            if len(target.items) >= limits.max_items_per_watchlist:
                raise WorkspaceError(
                    403,
                    f"Mỗi danh sách cho phép tối đa {limits.max_items_per_watchlist} mã.",
                )
            self._store.add_watchlist_item(
                user_id=user.user_id,
                watchlist_id=watchlist_id,
                ticker=clean_ticker,
            )
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="WORKSPACE_WATCHLIST_ITEM_ADD",
                resource_type="watchlist_item",
                resource_id=f"{watchlist_id}:{clean_ticker}",
                client_ip=client_ip,
            )
        refreshed = self._store.list_watchlists(user.user_id)
        return next(w for w in refreshed if w.watchlist_id == watchlist_id)

    def remove_ticker(
        self,
        *,
        user: AuthenticatedUser,
        watchlist_id: UUID,
        ticker: str,
        client_ip: str = "",
    ) -> None:
        clean_ticker = normalize_ticker(ticker)
        watchlists = self._store.list_watchlists(user.user_id)
        target = next((w for w in watchlists if w.watchlist_id == watchlist_id), None)
        if target is None:
            raise WorkspaceError(404, "Không tìm thấy danh sách theo dõi.")
        removed = self._store.remove_watchlist_item(
            user_id=user.user_id,
            watchlist_id=watchlist_id,
            ticker=clean_ticker,
        )
        if not removed:
            raise WorkspaceError(404, "Mã cổ phiếu không có trong danh sách này.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_WATCHLIST_ITEM_REMOVE",
            resource_type="watchlist_item",
            resource_id=f"{watchlist_id}:{clean_ticker}",
            client_ip=client_ip,
        )

    def import_local_watchlist(
        self,
        *,
        user: AuthenticatedUser,
        raw_tickers: list[str],
        watchlist_name: str | None = None,
        client_ip: str = "",
    ) -> ImportLocalResult:
        limits = self.limits_for(user.role)
        deduped: list[str] = []
        seen: set[str] = set()
        skipped_dups = 0
        for raw in raw_tickers:
            clean = raw.strip().upper()
            if not _TICKER_RE.fullmatch(clean):
                continue
            if clean in seen:
                skipped_dups += 1
                continue
            seen.add(clean)
            deduped.append(clean)

        existing = self._store.list_watchlists(user.user_id)
        clean_name = (watchlist_name or "").strip()[:60]
        target: WatchlistRecord | None = None
        if clean_name:
            target = next(
                (w for w in existing if w.name.lower() == clean_name.lower()),
                None,
            )
        if target is None and existing:
            target = next((w for w in existing if w.is_default), existing[0])
        if target is None:
            target = self._store.create_watchlist(
                user_id=user.user_id,
                name=clean_name or "Danh sách mặc định",
                is_default=True,
            )

        existing_items = set(target.items)
        remaining_slots = max(0, limits.max_items_per_watchlist - len(existing_items))
        added_count = 0
        truncated = 0
        for ticker in deduped:
            if ticker in existing_items:
                skipped_dups += 1
                continue
            if remaining_slots <= 0:
                truncated += 1
                continue
            if self._store.add_watchlist_item(
                user_id=user.user_id,
                watchlist_id=target.watchlist_id,
                ticker=ticker,
            ):
                added_count += 1
                remaining_slots -= 1
                existing_items.add(ticker)

        refreshed = self._store.list_watchlists(user.user_id)
        updated_target = next(
            w for w in refreshed if w.watchlist_id == target.watchlist_id
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_WATCHLIST_IMPORT",
            resource_type="watchlist",
            resource_id=str(target.watchlist_id),
            client_ip=client_ip,
            metadata={
                "added_count": added_count,
                "skipped_duplicates": skipped_dups,
                "truncated_by_limit": truncated,
            },
        )
        return ImportLocalResult(
            watchlist=updated_target,
            added_count=added_count,
            skipped_duplicates=skipped_dups,
            truncated_by_limit=truncated,
        )

    def list_notes(
        self, *, user: AuthenticatedUser, ticker: str, limit: int = 50, offset: int = 0
    ) -> list[AnalystNoteRecord]:
        clean_ticker = normalize_ticker(ticker)
        return self._store.list_notes(
            user_id=user.user_id,
            ticker=clean_ticker,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )

    def create_note(
        self,
        *,
        user: AuthenticatedUser,
        ticker: str,
        period_end_date: date | None,
        title: str,
        content: str,
        client_ip: str = "",
    ) -> AnalystNoteRecord:
        clean_ticker = normalize_ticker(ticker)
        clean_content = content.strip()
        if not clean_content:
            raise WorkspaceError(422, "Nội dung ghi chú không được để trống.")
        limits = self.limits_for(user.role)
        if self._store.count_notes(user_id=user.user_id) >= limits.max_notes:
            raise WorkspaceError(
                403,
                f"Đã đạt giới hạn tối đa {limits.max_notes} ghi chú phân tích.",
            )
        note = self._store.create_note(
            user_id=user.user_id,
            ticker=clean_ticker,
            period_end_date=period_end_date,
            title=title.strip()[:120],
            content=clean_content[:4000],
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_NOTE_CREATE",
            resource_type="analyst_note",
            resource_id=str(note.note_id),
            client_ip=client_ip,
            metadata={"ticker": clean_ticker},
        )
        return note

    def update_note(
        self,
        *,
        user: AuthenticatedUser,
        ticker: str,
        note_id: UUID,
        title: str | None,
        content: str | None,
        period_end_date: date | None,
        clear_period_end_date: bool = False,
        client_ip: str = "",
    ) -> AnalystNoteRecord:
        clean_ticker = normalize_ticker(ticker)
        clean_content = content.strip() if content is not None else None
        if clean_content is not None and not clean_content:
            raise WorkspaceError(422, "Nội dung ghi chú không được để trống.")
        updated = self._store.update_note(
            user_id=user.user_id,
            ticker=clean_ticker,
            note_id=note_id,
            title=title.strip()[:120] if title is not None else None,
            content=clean_content[:4000] if clean_content is not None else None,
            period_end_date=period_end_date,
            clear_period_end_date=clear_period_end_date,
        )
        if updated is None:
            raise WorkspaceError(404, "Không tìm thấy ghi chú phân tích.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_NOTE_UPDATE",
            resource_type="analyst_note",
            resource_id=str(note_id),
            client_ip=client_ip,
        )
        return updated

    def delete_note(
        self,
        *,
        user: AuthenticatedUser,
        ticker: str,
        note_id: UUID,
        client_ip: str = "",
    ) -> None:
        clean_ticker = normalize_ticker(ticker)
        deleted = self._store.delete_note(
            user_id=user.user_id, ticker=clean_ticker, note_id=note_id
        )
        if not deleted:
            raise WorkspaceError(404, "Không tìm thấy ghi chú phân tích.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_NOTE_DELETE",
            resource_type="analyst_note",
            resource_id=str(note_id),
            client_ip=client_ip,
        )

    def list_saved_screens(self, *, user: AuthenticatedUser) -> list[SavedScreenRecord]:
        return self._store.list_saved_screens(user.user_id)

    def create_saved_screen(
        self,
        *,
        user: AuthenticatedUser,
        name: str,
        filters_json: dict[str, object],
        client_ip: str = "",
    ) -> SavedScreenRecord:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 60:
            raise WorkspaceError(422, "Tên bộ lọc phải từ 1–60 ký tự.")
        limits = self.limits_for(user.role)
        existing = self._store.list_saved_screens(user.user_id)
        is_overwrite = any(s.name.lower() == clean_name.lower() for s in existing)
        if not is_overwrite and len(existing) >= limits.max_saved_screens:
            raise WorkspaceError(
                403,
                f"Đã đạt giới hạn tối đa {limits.max_saved_screens} bộ lọc đã lưu.",
            )
        screen = self._store.create_saved_screen(
            user_id=user.user_id,
            name=clean_name,
            filters_json=filters_json,
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_SAVED_SCREEN_UPSERT",
            resource_type="saved_screen",
            resource_id=str(screen.screen_id),
            client_ip=client_ip,
        )
        return screen

    def delete_saved_screen(
        self,
        *,
        user: AuthenticatedUser,
        screen_id: UUID,
        client_ip: str = "",
    ) -> None:
        deleted = self._store.delete_saved_screen(
            user_id=user.user_id, screen_id=screen_id
        )
        if not deleted:
            raise WorkspaceError(404, "Không tìm thấy bộ lọc đã lưu.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="WORKSPACE_SAVED_SCREEN_DELETE",
            resource_type="saved_screen",
            resource_id=str(screen_id),
            client_ip=client_ip,
        )


__all__ = [
    "ImportLocalResult",
    "ROLE_WORKSPACE_LIMITS",
    "WorkspaceError",
    "WorkspaceRoleLimits",
    "WorkspaceService",
    "normalize_ticker",
]
