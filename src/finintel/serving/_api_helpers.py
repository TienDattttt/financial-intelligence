"""Extracted pure helper utilities and ASGI middlewares for Layer 5 FastAPI serving.

Verified Sources:
- FastAPI & Starlette Pure ASGI Middleware Pattern:
  https://www.starlette.io/middleware/#pure-asgi-middleware
- OWASP CSV Injection (Formula Injection) Prevention Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/CSV_Injection_Prevention_Cheat_Sheet.html
- Runtime API Verification (Python 3.11.9, fastapi==0.165.0, polars==1.35.2).
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import threading
from datetime import date
from typing import Any

import polars as pl
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from finintel.serving.dependencies import (
    DEFAULT_STRESS_MAX_CONCURRENCY,
    DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
)

logger = logging.getLogger("finintel.serving.api")

STRESS_CACHE_CONTROL_HEADER = "private, no-store"
_CSV_FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_RAG_UNAVAILABLE_DETAIL = (
    "Dịch vụ tra cứu Thuyết minh PDF hiện tạm thời chưa sẵn sàng. Vui lòng thử lại sau."
)


def _finite_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Replace any non-finite floats (NaN/Inf) with NULL vectorially before JSON export."""
    if df.is_empty():
        return df
    float_cols = [name for name, dtype in df.schema.items() if dtype.is_float()]
    if not float_cols:
        return df
    return df.with_columns(
        pl.when(pl.col(name).is_finite()).then(pl.col(name)).otherwise(None).alias(name)
        for name in float_cols
    )


def _finite_scalar(value: float | None) -> float | None:
    if value is None:
        return None
    return value if math.isfinite(value) else None


def _frame_to_records(df: pl.DataFrame) -> list[dict[str, Any]]:
    """Serialize a bounded Polars slice to JSON-safe dicts via ``to_dicts()``."""
    if df.is_empty():
        return []
    return _finite_frame(df).to_dicts()


def _sanitize_csv_cell(value: object) -> str | int | float:
    """Apply OWASP CSV formula injection guard while preserving numeric negatives."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else ""
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES) or text.lstrip().startswith(
        _CSV_FORMULA_PREFIXES
    ):
        return f"'{text}"
    return text


def _dataframe_to_safe_csv_bytes(df: pl.DataFrame) -> bytes:
    """Serialize a Polars DataFrame to UTF-8 BOM CSV with OWASP formula sanitization."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(df.columns)
    for row in df.rows():
        writer.writerow([_sanitize_csv_cell(cell) for cell in row])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


class StressBulkheadMiddleware:
    """Pure ASGI event-loop bulkhead for ``POST /api/v1/stress-test`` (NFR-03).

    Uses an event-loop-native ``asyncio.Semaphore`` (instant 0-sleep wakeup) paired
    with ``app.state.stress_semaphore`` BEFORE FastAPI dispatches into the AnyIO
    threadpool or opens a DuckDB cursor.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._loop_sems: dict[int, asyncio.Semaphore] = {}

    def _get_async_semaphore(self, app_state: Any) -> asyncio.Semaphore:
        loop_id = id(asyncio.get_running_loop())
        sem = self._loop_sems.get(loop_id)
        if sem is None:
            max_conc = int(
                getattr(
                    app_state,
                    "stress_max_concurrency",
                    DEFAULT_STRESS_MAX_CONCURRENCY,
                )
            )
            sem = asyncio.Semaphore(max(1, max_conc))
            if len(self._loop_sems) > 16:
                self._loop_sems.clear()
            self._loop_sems[loop_id] = sem
        return sem

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/stress-test"
        ):
            await self.app(scope, receive, send)
            return

        fastapi_app = scope.get("app")
        app_state = getattr(fastapi_app, "state", None)
        thread_sem = getattr(app_state, "stress_semaphore", None)
        if not isinstance(thread_sem, threading.Semaphore):
            await self.app(scope, receive, send)
            return
        sem_timeout = float(
            getattr(
                app_state,
                "stress_semaphore_timeout",
                DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
            )
        )
        async_sem = self._get_async_semaphore(app_state)
        try:
            await asyncio.wait_for(async_sem.acquire(), timeout=max(0.001, sem_timeout))
        except TimeoutError:
            resp = JSONResponse(
                status_code=503,
                content={
                    "detail": "Stress-test simulator is busy; please retry shortly."
                },
                headers={"Retry-After": "1"},
            )
            await resp(scope, receive, send)
            return

        # Fast-path non-blocking check against app.state.stress_semaphore (keeps
        # unit-test manual semaphore pre-acquisition 100% compatible without polling).
        if not thread_sem.acquire(blocking=False):
            async_sem.release()
            resp = JSONResponse(
                status_code=503,
                content={
                    "detail": "Stress-test simulator is busy; please retry shortly."
                },
                headers={"Retry-After": "1"},
            )
            await resp(scope, receive, send)
            return

        state_dict = scope.setdefault("state", {})
        state_dict["_stress_permit_held"] = True
        try:
            await self.app(scope, receive, send)
        finally:
            state_dict["_stress_permit_held"] = False
            thread_sem.release()
            async_sem.release()


def _is_rag_unavailable_error(exc: Exception) -> bool:
    """Return True when a RAG dependency, vector DB, or LLM backend is unavailable."""
    if isinstance(
        exc,
        (ImportError, OSError, TimeoutError, ConnectionError, RuntimeError),
    ):
        return True
    mod_name = type(exc).__module__ or ""
    return mod_name.startswith(
        (
            "psycopg",
            "pgvector",
            "redis",
            "httpx",
            "google",
            "torch",
            "sentence_transformers",
        )
    )


def _rag_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": _RAG_UNAVAILABLE_DETAIL},
        headers={
            "Cache-Control": STRESS_CACHE_CONTROL_HEADER,
            "Retry-After": "5",
        },
    )


class RagAvailabilityMiddleware:
    """Pure ASGI guard converting missing optional deps / unreachable Postgres into HTTP 503 on ``/api/v2/documents/*``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if not path.startswith("/api/v2/documents/"):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            if not _is_rag_unavailable_error(exc):
                raise
            logger.warning(
                "RAG endpoint unavailable (%s): %s",
                type(exc).__name__,
                path,
            )
            resp = _rag_unavailable_response()
            await resp(scope, receive, send)
