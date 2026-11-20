"""FastAPI lifespan, DuckDB read-only connection management, and sync cursor dependencies.

Verified Sources:
- FastAPI Lifespan Events & Sync Threadpool Concurrency (``fastapi==0.165.0``):
  https://fastapi.tiangolo.com/advanced/events/
  https://fastapi.tiangolo.com/async/
- DuckDB Python API & Multithreading (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
  https://duckdb.org/docs/stable/clients/python/overview
- Runtime API Verification (Python 3.11.9, duckdb==1.4.2):
  ``duckdb.connect(":memory:", read_only=True)`` raises ``CatalogException``; read-only
  serving connects to ``":memory:"``, sets explicit ``threads`` and ``memory_limit``,
  runs ``ATTACH ... (READ_ONLY)`` when a DuckLake catalog is present, opens thread-local
  cursors via synchronous ``def get_db_cursor``, and closes the parent connection cleanly
  in the lifespan ``finally`` block.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import duckdb
from fastapi import FastAPI, Request

from finintel.serving.queries import DashboardQueryService, resolve_data_root
from finintel.storage.ducklake_store import ensure_ducklake_attached
from finintel.storage.repository import ParquetRepository

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_DUCKDB_MEMORY_LIMIT = "2GB"
DEFAULT_STRESS_MAX_CONCURRENCY = 4
DEFAULT_STRESS_SEMAPHORE_TIMEOUT = 5.0

_MEMORY_LIMIT_RE = re.compile(r"^\d+(?:[KMGTP]i?B)$", re.IGNORECASE)


def resolve_duckdb_threads(explicit: int | None = None) -> int:
    """Resolve DuckDB worker thread limit from explicit config, env var, or safe default."""
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int):
            raise ValueError("duckdb_threads must be an integer between 1 and 64.")
        if not (1 <= explicit <= 64):
            raise ValueError("duckdb_threads must be between 1 and 64.")
        return explicit

    raw_env = os.getenv("FININTEL_DUCKDB_THREADS")
    if raw_env is not None and raw_env.strip():
        try:
            parsed = int(raw_env.strip())
        except ValueError as exc:
            raise ValueError(
                "FININTEL_DUCKDB_THREADS must be an integer between 1 and 64."
            ) from exc
        if not (1 <= parsed <= 64):
            raise ValueError("FININTEL_DUCKDB_THREADS must be between 1 and 64.")
        return parsed

    return min(4, os.cpu_count() or 2)


def resolve_duckdb_memory_limit(explicit: str | None = None) -> str:
    """Resolve DuckDB memory limit with strict format validation."""
    raw = (
        explicit
        if explicit is not None
        else os.getenv("FININTEL_DUCKDB_MEMORY_LIMIT", DEFAULT_DUCKDB_MEMORY_LIMIT)
    )
    cleaned = raw.strip()
    if not _MEMORY_LIMIT_RE.fullmatch(cleaned):
        raise ValueError(
            "DuckDB memory_limit must match '<digits><KB|MB|GB|TB|KiB|MiB|GiB|TiB>'."
        )
    return cleaned


def resolve_cors_origins(explicit: Sequence[str] | None = None) -> tuple[str, ...]:
    """Resolve allowlisted CORS origins; returns empty tuple (deny-by-default) when unset."""
    raw_items: Sequence[str]
    if explicit is not None:
        raw_items = explicit
    else:
        raw_env = os.getenv("FININTEL_CORS_ORIGINS", "")
        raw_items = [item for item in raw_env.split(",") if item.strip()]

    validated: list[str] = []
    for item in raw_items:
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid CORS origin configured: {origin!r}")
        if origin not in validated:
            validated.append(origin)
    return tuple(validated)


def _attach_ducklake_read_only_if_applicable(
    conn: duckdb.DuckDBPyConnection,
    repository: ParquetRepository,
) -> None:
    """Attach the configured DuckLake catalog in ``READ_ONLY`` mode when present."""
    if repository._backend == "parquet":
        return
    located = repository._find_ducklake_catalog()
    if located is None:
        if repository._backend == "ducklake":
            raise FileNotFoundError(
                "DuckLake catalog was explicitly configured but could not be located."
            )
        return
    cat_path, _state_path, lake_data = located
    ensure_ducklake_attached(
        conn,
        catalog_path=cat_path,
        data_path=lake_data,
        catalog_alias=repository._catalog_alias,
        catalog_driver=repository._catalog_driver,
        postgres_dsn_env=repository._postgres_dsn_env,
        read_only=True,
    )


def build_lifespan(
    *,
    data_root: Path | None = None,
    repository: ParquetRepository | None = None,
    duckdb_threads: int | None = None,
    duckdb_memory_limit: str | None = None,
    stress_max_concurrency: int = DEFAULT_STRESS_MAX_CONCURRENCY,
    stress_semaphore_timeout: float = DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a FastAPI lifespan context manager with explicit DuckDB setup & shutdown close."""
    if (
        isinstance(stress_max_concurrency, bool)
        or not isinstance(stress_max_concurrency, int)
        or stress_max_concurrency < 1
    ):
        raise ValueError("stress_max_concurrency must be a positive integer.")

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        threads = resolve_duckdb_threads(duckdb_threads)
        memory_limit = resolve_duckdb_memory_limit(duckdb_memory_limit)
        repo = (
            repository
            if repository is not None
            else ParquetRepository(data_root=resolve_data_root(data_root))
        )

        duckdb_conn = duckdb.connect(":memory:")
        try:
            duckdb_conn.execute(f"SET threads = {threads};")
            duckdb_conn.execute(f"SET memory_limit = '{memory_limit}';")
            _attach_ducklake_read_only_if_applicable(duckdb_conn, repo)

            app.state.duckdb_conn = duckdb_conn
            app.state.duckdb_threads = threads
            app.state.duckdb_memory_limit = memory_limit
            app.state.repository = repo
            app.state.query_service = DashboardQueryService(repo)
            app.state.stress_semaphore = threading.Semaphore(stress_max_concurrency)
            app.state.stress_semaphore_timeout = float(stress_semaphore_timeout)
            yield
        finally:
            duckdb_conn.close()

    return _lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Default FastAPI lifespan resolving data root and DuckDB settings from environment."""
    async with build_lifespan()(app):
        yield


def get_db_cursor(request: Request) -> Iterator[duckdb.DuckDBPyConnection]:
    """Synchronous FastAPI dependency yielding an isolated thread-local DuckDB cursor.

    Declared strictly as ``def`` (not ``async def``) so FastAPI runs cursor creation,
    query execution, and cursor cleanup safely on the AnyIO worker threadpool.
    """
    conn = cast(duckdb.DuckDBPyConnection, request.app.state.duckdb_conn)
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def get_query_service(request: Request) -> DashboardQueryService:
    """Synchronous FastAPI dependency returning the app-scoped ``DashboardQueryService``."""
    return cast(DashboardQueryService, request.app.state.query_service)


def get_stress_semaphore(request: Request) -> tuple[threading.Semaphore, float]:
    """Synchronous FastAPI dependency returning the stress-test concurrency semaphore."""
    sem = cast(threading.Semaphore, request.app.state.stress_semaphore)
    timeout = float(
        getattr(
            request.app.state,
            "stress_semaphore_timeout",
            DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
        )
    )
    return sem, timeout
