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
import importlib.util
import io
import logging
import math
import os
import socket
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from finintel.serving.dependencies import (
    DEFAULT_STRESS_MAX_CONCURRENCY,
    DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
)
from finintel.serving.dictionary import (
    RATIO_DICTIONARY_META,
    RED_FLAG_DICTIONARY,
)
from finintel.serving.freshness import compose_etag_token
from finintel.serving.queries import RATIO_LINEAGE_CATALOG

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


def _extract_snapshot_id(meta: dict[str, object]) -> str | None:
    raw = meta.get("snapshot_id")
    return str(raw) if isinstance(raw, str) and raw else None


def _extract_storage_backend(meta: dict[str, object]) -> str:
    raw = meta.get("storage_backend")
    return str(raw) if isinstance(raw, str) and raw else "parquet"


def build_screener_cache_params(
    *,
    period_type: str,
    period_end_date: date | None,
    statement_scope: str,
    icb_code_level2: str | None,
    exchange: str | None,
    min_f_score: int | None,
    altman_zone: str | None,
    effective_tiers: tuple[str, ...] | None,
    beneish_flag: str | None,
    max_distress_prob: float | None,
    min_distress_prob: float | None,
    max_red_flags: int | None,
    min_red_flags: int | None,
    q: str | None,
    sort_by: str,
    sort_dir: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Assemble canonical Valkey L2 cache parameter dict for ``GET /api/v1/screener``."""
    return {
        "period_type": period_type,
        "period_end_date": period_end_date.isoformat() if period_end_date else None,
        "statement_scope": statement_scope,
        "icb_code_level2": icb_code_level2,
        "exchange": exchange,
        "min_f_score": min_f_score,
        "altman_zone": altman_zone,
        "risk_tiers": list(effective_tiers) if effective_tiers is not None else None,
        "beneish_flag": beneish_flag,
        "max_distress_prob": max_distress_prob,
        "min_distress_prob": min_distress_prob,
        "max_red_flags": max_red_flags,
        "min_red_flags": min_red_flags,
        "q": q,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "limit": limit,
        "offset": offset,
    }


def probe_rag_available(app_state: Any, *, ttl_seconds: float = 5.0) -> bool:
    """Fast, bounded probe for whether Port 2 Document RAG is ready to serve."""
    if not getattr(app_state, "_rag_router_mounted", False):
        return False
    if getattr(app_state, "_rag_injected", False):
        return getattr(app_state, "_rag_investigator", None) is not None
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        and not os.getenv("FININTEL_RAG_PREWARM_IN_TEST")
        and not os.getenv("FININTEL_RAG_PG_DSN", "").strip()
    ):
        return False
    now = time.monotonic()
    cached = getattr(app_state, "_rag_probe_cache", None)
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and (now - float(cached[0])) < ttl_seconds
    ):
        return bool(cached[1])
    if (
        importlib.util.find_spec("psycopg") is None
        or importlib.util.find_spec("pgvector") is None
    ):
        app_state._rag_probe_cache = (now, False)
        return False
    ok = False
    try:
        from finintel.rag.store import resolve_rag_pg_dsn

        dsn = resolve_rag_pg_dsn()
        if dsn:
            parsed = urlparse(dsn)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 5432
            with socket.create_connection((host, port), timeout=0.15):
                ok = True
    except (ImportError, OSError, ValueError):
        ok = False
    app_state._rag_probe_cache = (now, ok)
    return ok


def health_etag_source(
    snap_id: str | None, data_root: Path | None, rag_available: bool
) -> str | None:
    """Rotate health ETag when ``rag_available`` flips while preserving legacy ETag when False."""
    if snap_id is None:
        return None
    if not rag_available:
        return snap_id
    base = compose_etag_token(snap_id, data_root) or snap_id
    return f"{base}:rag1"


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


def build_meta_dictionary_kwargs() -> dict[str, Any]:
    """Build ``MetaDictionaryResponse`` constructor payload from dictionary metadata."""
    ratios = [
        {
            "ratio_name": name,
            "display_name_vn": RATIO_LINEAGE_CATALOG[name].display_name_vn,
            "category": entry.category,
            "unit": entry.unit,
            "format_type": entry.format_type,
            "direction": entry.direction,
            "formula_display": RATIO_LINEAGE_CATALOG[name].formula,
            "description_vn": entry.description_vn,
            "is_overview_core": entry.is_overview_core,
        }
        for name, entry in RATIO_DICTIONARY_META.items()
    ]
    red_flags = [
        {
            "flag_code": rf.flag_code,
            "title_vn": rf.title_vn,
            "default_severity": rf.default_severity,
            "methodology_ref": rf.methodology_ref,
            "description_vn": rf.description_vn,
        }
        for rf in RED_FLAG_DICTIONARY
    ]
    return {"ratios": ratios, "red_flags": red_flags}
