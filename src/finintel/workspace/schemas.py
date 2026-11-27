"""Pydantic HTTP schemas for ``/api/v3/workspace/*``, ``/api/v3/audit/*``, and ``/api/v3/ops/audit-logs``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class WatchlistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class WatchlistRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class ImportLocalWatchlistRequest(BaseModel):
    tickers: list[str] = Field(..., max_length=200)
    watchlist_name: str | None = Field(default=None, max_length=60)


class WatchlistResponseItem(BaseModel):
    watchlist_id: UUID
    name: str
    is_default: bool
    items: list[str]
    created_at: datetime
    updated_at: datetime


class WatchlistLimitsInfo(BaseModel):
    max_watchlists: int
    max_items_per_watchlist: int
    max_notes: int
    max_saved_screens: int


class WatchlistsListResponse(BaseModel):
    watchlists: list[WatchlistResponseItem]
    limits: WatchlistLimitsInfo


class ImportLocalWatchlistResponse(BaseModel):
    watchlist: WatchlistResponseItem
    added_count: int
    skipped_duplicates: int
    truncated_by_limit: int


class AnalystNoteCreateRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    content: str = Field(..., min_length=1, max_length=4000)
    period_end_date: date | None = None


class AnalystNoteUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    period_end_date: date | None = None
    clear_period_end_date: bool = False


class AnalystNoteItem(BaseModel):
    note_id: UUID
    ticker: str
    period_end_date: date | None = None
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


class AnalystNotesListResponse(BaseModel):
    ticker: str
    notes: list[AnalystNoteItem]


class SavedScreenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    filters_json: dict[str, object]


class SavedScreenItem(BaseModel):
    screen_id: UUID
    name: str
    filters_json: dict[str, object]
    created_at: datetime
    updated_at: datetime


class SavedScreensListResponse(BaseModel):
    screens: list[SavedScreenItem]


class WorkspaceDeleteResponse(BaseModel):
    status: Literal["DELETED"]
    id: str


class AuditLogItemResponse(BaseModel):
    audit_id: int
    actor_user_id: UUID | None = None
    actor_subject_hash: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip_hash: str
    metadata_json: dict[str, object]
    created_at: datetime


class AuditLogsPageResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogItemResponse]


__all__ = [
    "AnalystNoteCreateRequest",
    "AnalystNoteItem",
    "AnalystNoteUpdateRequest",
    "AnalystNotesListResponse",
    "AuditLogItemResponse",
    "AuditLogsPageResponse",
    "ImportLocalWatchlistRequest",
    "ImportLocalWatchlistResponse",
    "SavedScreenCreateRequest",
    "SavedScreenItem",
    "SavedScreensListResponse",
    "WatchlistCreateRequest",
    "WatchlistLimitsInfo",
    "WatchlistRenameRequest",
    "WatchlistResponseItem",
    "WatchlistsListResponse",
    "WorkspaceDeleteResponse",
]
