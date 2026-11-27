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

import logging
import math
import os
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import duckdb
from fastapi import FastAPI, Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finintel.config import parse_cors_origins, resolve_runtime_config
from finintel.serving.cache import ValkeyResponseCache
from finintel.serving.observability import mark_current_process_dead
from finintel.serving.queries import DashboardQueryService
from finintel.storage.ducklake_store import ensure_ducklake_attached
from finintel.storage.repository import (
    _CACHE_LOCK,
    _POSTGRES_POINTER_CACHE,
    POSTGRES_POINTER_CACHE_TTL_SECONDS,
    ParquetRepository,
    _read_postgres_active_snapshot_pointer_cached,
)

logger = logging.getLogger("finintel.serving.dependencies")

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_DUCKDB_MEMORY_LIMIT = "2GB"
DEFAULT_STRESS_MAX_CONCURRENCY = 4
DEFAULT_STRESS_SEMAPHORE_TIMEOUT = 5.0
DEFAULT_CSP_POLICY = (
    "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
    "object-src 'none'; form-action 'self'"
)
DEFAULT_HSTS_VALUE = "max-age=31536000; includeSubDomains"

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


def validate_cors_origins(
    origins: Sequence[str],
    *,
    allow_credentials: bool = False,
) -> tuple[str, ...]:
    """Validate explicit CORS origins and reject wildcards (``*``), especially with credentials."""
    cleaned_items: list[str] = []
    for raw_item in origins:
        item = raw_item.strip().rstrip("/")
        if not item:
            continue
        if "*" in item:
            raise ValueError(
                "CORS wildcard '*' is forbidden; configure explicit origin(s) such as Vercel domain."
            )
        parsed = urlparse(item)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("CORS origin must not contain credentials/userinfo.")
        if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
            raise ValueError(
                f"CORS origin must not contain path, query, or fragment: {item!r}"
            )
        cleaned_items.append(item)

    validated = parse_cors_origins(cleaned_items)
    if allow_credentials and not validated:
        raise ValueError(
            "CORS allow_credentials=True requires at least one explicit non-wildcard origin."
        )
    return validated


def resolve_cors_allow_credentials(explicit: bool | None = None) -> bool:
    """Resolve whether CORS credentialed cookies are enabled (default False)."""
    if explicit is not None:
        return bool(explicit)
    raw = os.getenv("FININTEL_CORS_ALLOW_CREDENTIALS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_cors_origins(
    explicit: Sequence[str] | None = None,
    *,
    allow_credentials: bool = False,
) -> tuple[str, ...]:
    """Resolve allowlisted CORS origins; returns empty tuple (deny-by-default) when unset."""
    if explicit is not None:
        return validate_cors_origins(explicit, allow_credentials=allow_credentials)
    env_raw = os.getenv("FININTEL_CORS_ORIGINS")
    if env_raw is not None and env_raw.strip():
        raw_items = [item for item in env_raw.split(",") if item.strip()]
        return validate_cors_origins(raw_items, allow_credentials=allow_credentials)
    cfg_origins = resolve_runtime_config().cors_origins
    if not cfg_origins:
        return ()
    return validate_cors_origins(cfg_origins, allow_credentials=allow_credentials)


class SecurityHeadersMiddleware:
    """Pure ASGI OWASP security headers middleware (CSP, HSTS, nosniff, Referrer-Policy)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        csp_policy: str = DEFAULT_CSP_POLICY,
        hsts_value: str | None = DEFAULT_HSTS_VALUE,
    ) -> None:
        self.app = app
        self.csp_policy = csp_policy
        self.hsts_value = hsts_value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", self.csp_policy)
                if self.hsts_value:
                    headers.setdefault("Strict-Transport-Security", self.hsts_value)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault(
                    "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)


@dataclass(frozen=True)
class SnapshotSyncResult:
    """Outcome of checking PostgreSQL ``finintel_active_snapshot`` for rollover."""

    changed: bool
    previous_snapshot_id: str | None
    active_snapshot_id: str | None
    evicted_entries: int = 0


def _evict_l1b_for_active_snapshot(active_snapshot_id: str) -> int:
    """Evict L1B cache entries whose snapshot token does not match ``active_snapshot_id``."""
    from finintel.serving.queries._cache import (
        _ACTIVE_L1B_SNAPSHOT_BY_ROOT,
        _ALL_L1B_CACHES,
        _QUERY_CACHE_LOCK,
        _l1b_root_and_snapshot,
    )

    evicted = 0
    with _QUERY_CACHE_LOCK:
        for cache in _ALL_L1B_CACHES:
            stale_keys: list[tuple[object, ...]] = []
            for key in cache:
                parsed = _l1b_root_and_snapshot(key)
                if parsed is None:
                    stale_keys.append(key)
                    continue
                _, snap_token = parsed
                token_sid = (
                    snap_token[1]
                    if isinstance(snap_token, tuple) and len(snap_token) >= 2
                    else snap_token
                )
                if token_sid != active_snapshot_id:
                    stale_keys.append(key)
            for key in stale_keys:
                cache.pop(key, None)
                evicted += 1
        stale_roots = [
            root
            for root, tok in _ACTIVE_L1B_SNAPSHOT_BY_ROOT.items()
            if (tok[1] if isinstance(tok, tuple) and len(tok) >= 2 else tok)
            != active_snapshot_id
        ]
        for root in stale_roots:
            _ACTIVE_L1B_SNAPSHOT_BY_ROOT.pop(root, None)
    return evicted


def _resolve_sync_interval_seconds(explicit: float | None) -> float:
    if explicit is not None:
        if isinstance(explicit, bool) or not math.isfinite(explicit) or explicit < 0.0:
            raise ValueError(
                "sync_interval_seconds must be a non-negative finite float."
            )
        return float(explicit)
    raw = os.getenv("FININTEL_SNAPSHOT_SYNC_INTERVAL_SECONDS", "").strip()
    if not raw:
        return POSTGRES_POINTER_CACHE_TTL_SECONDS
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(
            "FININTEL_SNAPSHOT_SYNC_INTERVAL_SECONDS must be a non-negative finite float."
        ) from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(
            "FININTEL_SNAPSHOT_SYNC_INTERVAL_SECONDS must be a non-negative finite float."
        )
    return parsed


class SnapshotPointerSync:
    """Tracks PostgreSQL ``finintel_active_snapshot`` and evicts stale L1B caches on rollover."""

    def __init__(
        self,
        repository: ParquetRepository,
        *,
        sync_interval_seconds: float | None = None,
        initial_snapshot_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._sync_interval_seconds = _resolve_sync_interval_seconds(
            sync_interval_seconds
        )
        self._lock = threading.Lock()
        self._last_snapshot_id = initial_snapshot_id

    @property
    def last_snapshot_id(self) -> str | None:
        with self._lock:
            return self._last_snapshot_id

    def sync(self, *, force: bool = False) -> SnapshotSyncResult:
        """Check ``finintel_active_snapshot`` and evict stale L1B caches if rolled over."""
        if self._repository._catalog_driver != "postgres":
            return SnapshotSyncResult(
                changed=False,
                previous_snapshot_id=self._last_snapshot_id,
                active_snapshot_id=self._repository.snapshot_id,
                evicted_entries=0,
            )
        if not os.environ.get(self._repository._postgres_dsn_env, "").strip():
            return SnapshotSyncResult(
                changed=False,
                previous_snapshot_id=self._last_snapshot_id,
                active_snapshot_id=None,
                evicted_entries=0,
            )
        ttl = 0.0 if force else self._sync_interval_seconds
        if ttl == 0.0:
            cache_key = (
                self._repository._postgres_dsn_env,
                self._repository._catalog_alias,
            )
            with _CACHE_LOCK:
                _POSTGRES_POINTER_CACHE.pop(cache_key, None)
        pointer = _read_postgres_active_snapshot_pointer_cached(
            self._repository._postgres_dsn_env,
            self._repository._catalog_alias,
            ttl_seconds=ttl,
        )
        if pointer is None:
            return SnapshotSyncResult(
                changed=False,
                previous_snapshot_id=self._last_snapshot_id,
                active_snapshot_id=None,
                evicted_entries=0,
            )
        active_id = pointer.snapshot_id
        with self._lock:
            prev_id = self._last_snapshot_id
            if prev_id is None:
                self._last_snapshot_id = active_id
                return SnapshotSyncResult(
                    changed=False,
                    previous_snapshot_id=None,
                    active_snapshot_id=active_id,
                    evicted_entries=0,
                )
            if prev_id == active_id:
                return SnapshotSyncResult(
                    changed=False,
                    previous_snapshot_id=prev_id,
                    active_snapshot_id=active_id,
                    evicted_entries=0,
                )
            evicted = _evict_l1b_for_active_snapshot(active_id)
            self._last_snapshot_id = active_id
            logger.info(
                "Detected snapshot rollover %s -> %s; evicted %d L1B cache entries.",
                prev_id,
                active_id,
                evicted,
            )
            return SnapshotSyncResult(
                changed=True,
                previous_snapshot_id=prev_id,
                active_snapshot_id=active_id,
                evicted_entries=evicted,
            )


def _attach_ducklake_read_only_if_applicable(
    conn: duckdb.DuckDBPyConnection,
    repository: ParquetRepository,
) -> None:
    """Attach the configured DuckLake catalog in ``READ_ONLY`` mode when present.

    In PostgreSQL catalog mode (``ARCH-08``), attaching does not require local
    ``state.sqlite`` or ``data/metadata/`` because the active snapshot pointer is
    served from ``public.finintel_active_snapshot`` on PostgreSQL.
    """
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
    valkey_url: str | None = None,
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
        runtime_cfg = resolve_runtime_config(
            data_root=data_root,
            valkey_url=valkey_url,
        )
        repo = (
            repository
            if repository is not None
            else ParquetRepository.from_runtime_config(runtime_cfg)
        )
        response_cache = ValkeyResponseCache(url=runtime_cfg.valkey_url)

        duckdb_conn = duckdb.connect(":memory:")
        try:
            duckdb_conn.execute(f"SET threads = {threads};")
            duckdb_conn.execute(f"SET memory_limit = '{memory_limit}';")
            _attach_ducklake_read_only_if_applicable(duckdb_conn, repo)
            active_sid_init: str | None = None
            try:
                snap_meta = repo.get_snapshot_metadata(connection=duckdb_conn)
                active_sid = snap_meta.get("snapshot_id")
                if isinstance(active_sid, str) and active_sid.strip():
                    active_sid_init = active_sid.strip()
            except Exception as exc:
                logger.warning(
                    "Boot guard: unable to verify active_snapshot under data_root=%s (%s). "
                    "If launching the 685-issuer demo, set FININTEL_DATA_ROOT=data/universe_600_800.",
                    repo._source,
                    type(exc).__name__,
                )
            else:
                if active_sid_init is None:
                    logger.warning(
                        "Boot guard: active_snapshot is empty under data_root=%s; API will serve "
                        "empty datasets until a snapshot is published or FININTEL_DATA_ROOT is set "
                        "(e.g., FININTEL_DATA_ROOT=data/universe_600_800).",
                        repo._source,
                    )

            app.state.duckdb_conn = duckdb_conn
            app.state.duckdb_threads = threads
            app.state.duckdb_memory_limit = memory_limit
            app.state.repository = repo
            app.state.query_service = DashboardQueryService(repo)
            app.state.snapshot_sync = SnapshotPointerSync(
                repo, initial_snapshot_id=active_sid_init
            )
            app.state.response_cache = response_cache
            app.state.stress_semaphore = threading.Semaphore(stress_max_concurrency)
            app.state.stress_max_concurrency = int(stress_max_concurrency)
            app.state.stress_semaphore_timeout = float(stress_semaphore_timeout)
            yield
        finally:
            mark_current_process_dead(getattr(app.state, "_prometheus_live_gauges", ()))
            response_cache.close()
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
    sync = getattr(request.app.state, "snapshot_sync", None)
    if isinstance(sync, SnapshotPointerSync):
        sync.sync()
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


def get_response_cache(request: Request) -> ValkeyResponseCache:
    """Return the app-scoped Valkey/Redis L2 response cache (fail-open no-op if disabled)."""
    return cast(ValkeyResponseCache, request.app.state.response_cache)
