"""PostgreSQL workspace queries (strictly scoped by ``WHERE user_id = %(user_id)s``)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from finintel.auth._store import (
    AnalystNoteRecord,
    DuplicateResourceError,
    SavedScreenRecord,
    WatchlistRecord,
)


class PostgresWorkspaceMixin:
    """Mixin implementing Watchlist, Analyst Notes, and Saved Screens SQL operations."""

    def _connect(self) -> Any:
        raise NotImplementedError

    def _is_unique_violation(self, exc: Exception) -> bool:
        return getattr(exc, "sqlstate", None) == "23505"

    def list_watchlists(self, user_id: UUID) -> list[WatchlistRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT watchlist_id, user_id, name, is_default, created_at, updated_at
                    FROM watchlists
                    WHERE user_id = %(user_id)s
                    ORDER BY is_default DESC, created_at ASC
                    """,
                    {"user_id": user_id},
                )
                wl_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT watchlist_id, ticker
                    FROM watchlist_items
                    WHERE user_id = %(user_id)s
                    ORDER BY added_at ASC
                    """,
                    {"user_id": user_id},
                )
                item_rows = cur.fetchall()

        items_by_wl: dict[UUID, list[str]] = {}
        for wid, ticker in item_rows:
            items_by_wl.setdefault(UUID(str(wid)), []).append(str(ticker))

        return [
            WatchlistRecord(
                watchlist_id=UUID(str(row[0])),
                user_id=UUID(str(row[1])),
                name=str(row[2]),
                is_default=bool(row[3]),
                created_at=row[4],
                updated_at=row[5],
                items=tuple(items_by_wl.get(UUID(str(row[0])), [])),
            )
            for row in wl_rows
        ]

    def create_watchlist(
        self, *, user_id: UUID, name: str, is_default: bool = False
    ) -> WatchlistRecord:
        wid = uuid4()
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO watchlists (watchlist_id, user_id, name, is_default)
                        VALUES (%(watchlist_id)s, %(user_id)s, %(name)s, %(is_default)s)
                        RETURNING watchlist_id, user_id, name, is_default, created_at, updated_at
                        """,
                        {
                            "watchlist_id": wid,
                            "user_id": user_id,
                            "name": name,
                            "is_default": is_default,
                        },
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if self._is_unique_violation(exc):
                    raise DuplicateResourceError(
                        "Watchlist name already exists."
                    ) from exc
                raise
        assert row is not None
        return WatchlistRecord(
            watchlist_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            name=str(row[2]),
            is_default=bool(row[3]),
            created_at=row[4],
            updated_at=row[5],
            items=(),
        )

    def update_watchlist(
        self, *, user_id: UUID, watchlist_id: UUID, name: str
    ) -> WatchlistRecord | None:
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE watchlists
                        SET name = %(name)s, updated_at = NOW()
                        WHERE watchlist_id = %(watchlist_id)s AND user_id = %(user_id)s
                        RETURNING watchlist_id, user_id, name, is_default, created_at, updated_at
                        """,
                        {
                            "watchlist_id": watchlist_id,
                            "user_id": user_id,
                            "name": name,
                        },
                    )
                    row = cur.fetchone()
                    if row is None:
                        conn.rollback()
                        return None
                    cur.execute(
                        """
                        SELECT ticker
                        FROM watchlist_items
                        WHERE watchlist_id = %(watchlist_id)s AND user_id = %(user_id)s
                        ORDER BY added_at ASC
                        """,
                        {"watchlist_id": watchlist_id, "user_id": user_id},
                    )
                    items = tuple(str(r[0]) for r in cur.fetchall())
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if self._is_unique_violation(exc):
                    raise DuplicateResourceError(
                        "Watchlist name already exists."
                    ) from exc
                raise
        return WatchlistRecord(
            watchlist_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            name=str(row[2]),
            is_default=bool(row[3]),
            created_at=row[4],
            updated_at=row[5],
            items=items,
        )

    def delete_watchlist(self, *, user_id: UUID, watchlist_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM watchlists
                    WHERE watchlist_id = %(watchlist_id)s AND user_id = %(user_id)s
                    """,
                    {"watchlist_id": watchlist_id, "user_id": user_id},
                )
                deleted = int(cur.rowcount or 0) > 0
            conn.commit()
        return deleted

    def add_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM watchlists
                    WHERE watchlist_id = %(watchlist_id)s AND user_id = %(user_id)s
                    """,
                    {"watchlist_id": watchlist_id, "user_id": user_id},
                )
                if cur.fetchone() is None:
                    return False
                cur.execute(
                    """
                    INSERT INTO watchlist_items (watchlist_id, user_id, ticker)
                    VALUES (%(watchlist_id)s, %(user_id)s, %(ticker)s)
                    ON CONFLICT (watchlist_id, ticker) DO NOTHING
                    """,
                    {
                        "watchlist_id": watchlist_id,
                        "user_id": user_id,
                        "ticker": ticker,
                    },
                )
                added = int(cur.rowcount or 0) > 0
            conn.commit()
        return added

    def remove_watchlist_item(
        self, *, user_id: UUID, watchlist_id: UUID, ticker: str
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM watchlist_items
                    WHERE watchlist_id = %(watchlist_id)s
                      AND user_id = %(user_id)s
                      AND ticker = %(ticker)s
                    """,
                    {
                        "watchlist_id": watchlist_id,
                        "user_id": user_id,
                        "ticker": ticker,
                    },
                )
                removed = int(cur.rowcount or 0) > 0
            conn.commit()
        return removed

    def list_notes(
        self, *, user_id: UUID, ticker: str, limit: int, offset: int
    ) -> list[AnalystNoteRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT note_id, user_id, ticker, period_end_date, title, content,
                           created_at, updated_at
                    FROM analyst_notes
                    WHERE user_id = %(user_id)s AND ticker = %(ticker)s
                    ORDER BY updated_at DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    {
                        "user_id": user_id,
                        "ticker": ticker,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                rows = cur.fetchall()
        return [
            AnalystNoteRecord(
                note_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])),
                ticker=str(r[2]),
                period_end_date=r[3],
                title=str(r[4]),
                content=str(r[5]),
                created_at=r[6],
                updated_at=r[7],
            )
            for r in rows
        ]

    def count_notes(self, *, user_id: UUID) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM analyst_notes WHERE user_id = %(user_id)s",
                    {"user_id": user_id},
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def create_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        period_end_date: date | None,
        title: str,
        content: str,
    ) -> AnalystNoteRecord:
        nid = uuid4()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analyst_notes (note_id, user_id, ticker, period_end_date, title, content)
                    VALUES (%(note_id)s, %(user_id)s, %(ticker)s, %(period_end_date)s, %(title)s, %(content)s)
                    RETURNING note_id, user_id, ticker, period_end_date, title, content, created_at, updated_at
                    """,
                    {
                        "note_id": nid,
                        "user_id": user_id,
                        "ticker": ticker,
                        "period_end_date": period_end_date,
                        "title": title,
                        "content": content,
                    },
                )
                r = cur.fetchone()
            conn.commit()
        assert r is not None
        return AnalystNoteRecord(
            note_id=UUID(str(r[0])),
            user_id=UUID(str(r[1])),
            ticker=str(r[2]),
            period_end_date=r[3],
            title=str(r[4]),
            content=str(r[5]),
            created_at=r[6],
            updated_at=r[7],
        )

    def update_note(
        self,
        *,
        user_id: UUID,
        ticker: str,
        note_id: UUID,
        title: str | None,
        content: str | None,
        period_end_date: date | None,
        clear_period_end_date: bool = False,
    ) -> AnalystNoteRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE analyst_notes
                    SET title = COALESCE(%(title)s, title),
                        content = COALESCE(%(content)s, content),
                        period_end_date = CASE
                            WHEN %(clear_period)s THEN NULL
                            ELSE COALESCE(%(period_end_date)s, period_end_date)
                        END,
                        updated_at = NOW()
                    WHERE note_id = %(note_id)s
                      AND user_id = %(user_id)s
                      AND ticker = %(ticker)s
                    RETURNING note_id, user_id, ticker, period_end_date, title, content, created_at, updated_at
                    """,
                    {
                        "note_id": note_id,
                        "user_id": user_id,
                        "ticker": ticker,
                        "title": title,
                        "content": content,
                        "period_end_date": period_end_date,
                        "clear_period": clear_period_end_date,
                    },
                )
                r = cur.fetchone()
            conn.commit()
        if r is None:
            return None
        return AnalystNoteRecord(
            note_id=UUID(str(r[0])),
            user_id=UUID(str(r[1])),
            ticker=str(r[2]),
            period_end_date=r[3],
            title=str(r[4]),
            content=str(r[5]),
            created_at=r[6],
            updated_at=r[7],
        )

    def delete_note(self, *, user_id: UUID, ticker: str, note_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM analyst_notes
                    WHERE note_id = %(note_id)s
                      AND user_id = %(user_id)s
                      AND ticker = %(ticker)s
                    """,
                    {"note_id": note_id, "user_id": user_id, "ticker": ticker},
                )
                deleted = int(cur.rowcount or 0) > 0
            conn.commit()
        return deleted

    def list_saved_screens(self, user_id: UUID) -> list[SavedScreenRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT screen_id, user_id, name, filters_json, created_at, updated_at
                    FROM saved_screens
                    WHERE user_id = %(user_id)s
                    ORDER BY updated_at DESC
                    """,
                    {"user_id": user_id},
                )
                rows = cur.fetchall()
        return [
            SavedScreenRecord(
                screen_id=UUID(str(r[0])),
                user_id=UUID(str(r[1])),
                name=str(r[2]),
                filters_json=dict(r[3]) if isinstance(r[3], dict) else {},
                created_at=r[4],
                updated_at=r[5],
            )
            for r in rows
        ]

    def create_saved_screen(
        self, *, user_id: UUID, name: str, filters_json: dict[str, object]
    ) -> SavedScreenRecord:
        sid = uuid4()
        payload = json.dumps(filters_json, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO saved_screens (screen_id, user_id, name, filters_json)
                    VALUES (%(screen_id)s, %(user_id)s, %(name)s, %(filters_json)s::jsonb)
                    ON CONFLICT (user_id, name) DO UPDATE
                    SET filters_json = EXCLUDED.filters_json,
                        updated_at = NOW()
                    RETURNING screen_id, user_id, name, filters_json, created_at, updated_at
                    """,
                    {
                        "screen_id": sid,
                        "user_id": user_id,
                        "name": name,
                        "filters_json": payload,
                    },
                )
                r = cur.fetchone()
            conn.commit()
        assert r is not None
        return SavedScreenRecord(
            screen_id=UUID(str(r[0])),
            user_id=UUID(str(r[1])),
            name=str(r[2]),
            filters_json=dict(r[3]) if isinstance(r[3], dict) else {},
            created_at=r[4],
            updated_at=r[5],
        )

    def delete_saved_screen(self, *, user_id: UUID, screen_id: UUID) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM saved_screens
                    WHERE screen_id = %(screen_id)s AND user_id = %(user_id)s
                    """,
                    {"screen_id": screen_id, "user_id": user_id},
                )
                deleted = int(cur.rowcount or 0) > 0
            conn.commit()
        return deleted


__all__ = ["PostgresWorkspaceMixin"]
