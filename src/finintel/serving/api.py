"""Read-only analytical FastAPI service (``/api/v1`` and ``/api/v2`` routes).

Verified Sources:
- FastAPI Official Documentation (Lifespan, Sync Threadpool Concurrency, CORS):
  https://fastapi.tiangolo.com/advanced/events/
  https://fastapi.tiangolo.com/async/
- DuckDB Python API & Multithreading (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
  https://duckdb.org/docs/stable/clients/python/overview
- RFC 9110 HTTP Semantics (``ETag``, ``If-None-Match`` weak/strong comparison -> ``304``,
  ``Cache-Control: private, max-age=0, must-revalidate``):
  https://httpwg.org/specs/rfc9110.html#field.etag
  https://httpwg.org/specs/rfc9110.html#field.if-none-match
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216 (invoked strictly via ``simulate_financial_stress`` and
  ``summarize_stress_by_sector`` with zero duplicated financial math).
- Runtime API Verification (Python 3.11.9, fastapi==0.165.0, pydantic==2.14.4,
  duckdb==1.4.2, polars==1.35.2).
"""

from __future__ import annotations

import contextvars
import importlib
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import duckdb
import polars as pl
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finintel.analytics.stress_test import (
    StressScenario,
    simulate_financial_stress,
    summarize_stress_by_sector,
)
from finintel.exceptions import ContractViolationError
from finintel.serving._api_helpers import (
    STRESS_CACHE_CONTROL_HEADER,
    RagAvailabilityMiddleware,
    StressBulkheadMiddleware,
    _dataframe_to_safe_csv_bytes,
    _extract_snapshot_id,
    _extract_storage_backend,
    _finite_scalar,
    _frame_to_records,
    _is_rag_unavailable_error,
    _rag_unavailable_response,
    build_meta_dictionary_kwargs,
    build_screener_cache_params,
    health_etag_source,
    probe_rag_available,
)
from finintel.serving._api_helpers import (
    _sanitize_csv_cell as _sanitize_csv_cell,
)
from finintel.serving._stress_helpers import (
    build_empty_stress_response_kwargs,
    build_stress_response_kwargs,
)
from finintel.serving.cache import (
    ValkeyResponseCache,
    build_snapshot_cache_key,
)
from finintel.serving.dependencies import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_STRESS_MAX_CONCURRENCY,
    DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
    build_lifespan,
    get_db_cursor,
    get_query_service,
    get_response_cache,
    get_stress_semaphore,
    resolve_cors_origins,
)
from finintel.serving.observability import (
    SPAN_ANALYTICS_STRESS_TEST,
    attach_observability,
    trace_span,
)
from finintel.serving.dictionary import StatementType
from finintel.serving.freshness import (
    build_composite_cache_token,
    build_freshness_item,
    format_composite_etag,
    if_none_match_matches,
    resolve_serving_data_root,
    sanitize_error_detail,
)
from finintel.serving.queries import (
    AltmanZone,
    BeneishFlag,
    DashboardQueryService,
    Exchange,
    PeriodType,
    PeriodView,
    RiskTier,
    StatementScope,
)
from finintel.serving.queries._helpers import parse_screener_risk_tiers
from finintel.serving.schemas import (
    AuditQuarantineItem,
    AuditQuarantinePageResponse,
    BenchmarkSummaryItem,
    CapitalStructurePoint,
    CompanyBenchmarksResponse,
    CompanyBenchmarksSummaryResponse,
    CompanyFilingItem,
    CompanyFilingsResponse,
    CompanyMarketOverlayResponse,
    CompanyProfileResponse,
    CompanyStatementsResponse,
    CoreEarningsPoint,
    DuPontPoint,
    GrowthPoint,
    HealthResponse,
    IssuerItem,
    IssuersPageResponse,
    LineageComponentItem,
    LivenessResponse,
    MarketChangeItem,
    MarketChangesResponse,
    MetaDictionaryResponse,
    MetaPeriodsResponse,
    MLScoreItem,
    ModelCardResponse,
    PeerBandHistoryPoint,
    PeerRankingItem,
    QualitySummaryItem,
    QualitySummaryPageResponse,
    RatioLineageResponse,
    RatioName,
    ReadinessResponse,
    RedFlagItem,
    ScreenerItem,
    ScreenerResponse,
    ScreenerSortField,
    SectorOptionItem,
    SectorRiskItem,
    SectorRiskSummaryResponse,
    SnapshotOverviewResponse,
    SortDirection,
    StatementPeriodItem,
    StatementRowItem,
    StressScenarioRequest,
    StressTestResponse,
    parse_shap_top_drivers,
)
from finintel.storage.repository import ParquetRepository

logger = logging.getLogger("finintel.serving.api")

CACHE_CONTROL_HEADER = "private, max-age=0, must-revalidate"
_TICKER_PATTERN = r"^[A-Z0-9]{1,10}$"
_ICB_CODE_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_REQUEST_DATA_ROOT: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "finintel_request_data_root", default=None
)


class ResourceNotFoundError(LookupError):
    """Domain lookup failure mapped cleanly to HTTP 404 without leaking internals."""


def _sanitize_error_detail(message: str, *, fallback: str) -> str:
    """Strip filesystem paths, DSNs, SQL, or internal tokens from client error details."""
    return sanitize_error_detail(message, fallback=fallback)


def _format_etag(snapshot_id: str) -> str:
    """Format an RFC 9110 strong quoted composite entity-tag."""
    return format_composite_etag(snapshot_id, _REQUEST_DATA_ROOT.get())


def _conditional_not_modified(
    if_none_match: str | None, snapshot_id: str | None
) -> Response | None:
    """Return HTTP 304 response when ``If-None-Match`` matches the composite ETag."""
    if snapshot_id is None:
        return None
    quoted_etag = _format_etag(snapshot_id)
    if if_none_match_matches(if_none_match, quoted_etag):
        return Response(
            status_code=304,
            headers={
                "ETag": quoted_etag,
                "Cache-Control": CACHE_CONTROL_HEADER,
            },
        )
    return None


def _apply_cache_headers(response: Response, snapshot_id: str | None) -> None:
    response.headers["Cache-Control"] = CACHE_CONTROL_HEADER
    if snapshot_id is not None:
        response.headers["ETag"] = _format_etag(snapshot_id)


def _attach_lineage_state(request: Request, meta: Mapping[str, object]) -> None:
    repo = getattr(request.app.state, "repository", None)
    _REQUEST_DATA_ROOT.set(resolve_serving_data_root(getattr(repo, "_source", None)))
    snap_id = meta.get("snapshot_id")
    if isinstance(snap_id, str) and snap_id:
        request.state.snapshot_id = snap_id
    run_id = meta.get("dagster_run_id")
    if isinstance(run_id, str) and run_id:
        request.state.dagster_run_id = run_id
    git_sha = meta.get("git_commit_sha")
    if isinstance(git_sha, str) and git_sha:
        request.state.git_commit_sha = git_sha


def _cached_json_response(
    cache: ValkeyResponseCache,
    snap_id: str | None,
    route: str,
    params: Mapping[str, Any] | None = None,
    *,
    cache_control: str = CACHE_CONTROL_HEADER,
) -> tuple[str | None, Response | None]:
    if not cache.enabled or snap_id is None:
        return None, None
    composite_snap = build_composite_cache_token(snap_id, _REQUEST_DATA_ROOT.get())
    key = build_snapshot_cache_key(composite_snap or snap_id, route, params)
    raw = cache.get_bytes(key)
    if raw is None:
        return key, None
    return key, Response(
        content=raw,
        media_type="application/json",
        headers={
            "Cache-Control": cache_control,
            "ETag": _format_etag(snap_id),
            "X-Cache": "HIT",
        },
    )


_ModelT = TypeVar("_ModelT")


def _store_cached_model(
    cache: ValkeyResponseCache,
    cache_key: str | None,
    response: Response,
    model: _ModelT,
    *,
    snap_id: str | None = None,
    cache_control: str = CACHE_CONTROL_HEADER,
) -> _ModelT | Response:
    if cache_key is None or not cache.enabled:
        return model
    try:
        # Single-pass Rust Pydantic JSON serialization avoids double serialization.
        raw = cast(Any, model).model_dump_json().encode("utf-8")
        cache.set_bytes(cache_key, raw)
        headers = {
            "Cache-Control": cache_control,
            "X-Cache": "MISS",
        }
        if snap_id is not None:
            headers["ETag"] = _format_etag(snap_id)
        return Response(content=raw, media_type="application/json", headers=headers)
    except Exception:
        return model


def _mount_rag_router(
    api: FastAPI,
    *,
    valkey_url: str | None = None,
    rag_store: Any | None = None,
    rag_investigator: Any | None = None,
) -> None:
    """Mount Port 2 Document RAG routes lazily without importing optional ML/DB deps at boot."""
    store_lock = threading.Lock()
    investigator_lock = threading.Lock()
    api.state._rag_store = rag_store
    api.state._rag_investigator = rag_investigator
    api.state._rag_injected = rag_store is not None or rag_investigator is not None
    api.state._rag_probe_cache = None
    api.state._rag_router_mounted = False

    def _get_store() -> Any:
        existing = getattr(api.state, "_rag_store", None)
        if existing is not None:
            return existing
        with store_lock:
            existing = getattr(api.state, "_rag_store", None)
            if existing is not None:
                return existing
            from finintel.rag.store import PgVectorRagStore, resolve_rag_pg_dsn

            dsn = resolve_rag_pg_dsn()
            if "connect_timeout=" not in dsn and "://" in dsn:
                sep = "&" if "?" in dsn else "?"
                dsn = f"{dsn}{sep}connect_timeout=3"
            store = PgVectorRagStore(dsn=dsn)
            api.state._rag_store = store
            return store

    def _build_investigator_sync(*, warm_models: bool = False) -> Any:
        from finintel.rag.embedder import BgeM3Embedder
        from finintel.rag.investigator import GroundedDocumentInvestigator
        from finintel.rag.retriever import HybridPgVectorRetriever

        store = _get_store()
        embedder = BgeM3Embedder()
        if warm_models:
            embedder._ensure_loaded()
        retriever = HybridPgVectorRetriever(store=store, embedder=embedder)
        gemini_bundle: Any | None = None
        if warm_models:
            try:
                from finintel.rag._gemini import create_verified_gemini_bundle

                gemini_bundle = create_verified_gemini_bundle()
            except Exception as exc:
                logger.warning(
                    "RAG Gemini pre-warm skipped (%s); will probe lazily.",
                    type(exc).__name__,
                )
        return GroundedDocumentInvestigator(
            retriever=retriever,
            gemini_bundle=gemini_bundle,
            valkey_url=valkey_url,
        )

    def _get_investigator() -> Any:
        existing = getattr(api.state, "_rag_investigator", None)
        if existing is not None:
            return existing
        with investigator_lock:
            existing = getattr(api.state, "_rag_investigator", None)
            if existing is not None:
                return existing
            investigator = _build_investigator_sync(warm_models=False)
            api.state._rag_investigator = investigator
            return investigator

    def _prewarm_rag_worker() -> None:
        try:
            from finintel.rag.store import resolve_rag_pg_dsn

            dsn = resolve_rag_pg_dsn()
        except Exception as exc:
            logger.info(
                "RAG pre-warm skipped (RAG store config unavailable: %s).",
                type(exc).__name__,
            )
            return
        if not dsn:
            return
        try:
            psycopg_mod: Any = importlib.import_module("psycopg")
            check_dsn = dsn
            if "connect_timeout=" not in check_dsn and "://" in check_dsn:
                sep = "&" if "?" in check_dsn else "?"
                check_dsn = f"{check_dsn}{sep}connect_timeout=1"
            with psycopg_mod.connect(check_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except Exception as exc:
            logger.info(
                "RAG pre-warm skipped (Postgres unreachable or deps absent: %s).",
                type(exc).__name__,
            )
            return

        with investigator_lock:
            if getattr(api.state, "_rag_investigator", None) is not None:
                return
            try:
                api.state._rag_investigator = _build_investigator_sync(warm_models=True)
                api.state._rag_probe_cache = (time.monotonic(), True)
                logger.info("RAG embedder and Gemini bundle pre-warmed successfully.")
            except Exception as exc:
                logger.warning("RAG pre-warm failed (%s).", type(exc).__name__)

    def _schedule_rag_prewarm() -> None:
        if rag_store is not None or rag_investigator is not None:
            return
        if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv(
            "FININTEL_RAG_PREWARM_IN_TEST"
        ):
            return
        thread = threading.Thread(
            target=_prewarm_rag_worker,
            name="finintel-rag-prewarm",
            daemon=True,
        )
        thread.start()

    api.state._rag_prewarm_fn = _schedule_rag_prewarm
    api.state._rag_prewarm_worker = _prewarm_rag_worker

    try:
        from finintel.rag.router import create_rag_router

        api.include_router(
            create_rag_router(
                get_store=_get_store,
                get_investigator=_get_investigator,
            )
        )
        api.state._rag_router_mounted = True
    except ImportError as exc:
        api.state._rag_router_mounted = False
        logger.warning(
            "Port 2 RAG router mounted in 503 fallback mode (%s).",
            type(exc).__name__,
        )

        @api.get("/api/v2/documents/{ticker}/list", tags=["documents-rag"])
        async def _rag_list_unavailable(ticker: str) -> JSONResponse:
            _ = ticker
            return _rag_unavailable_response()

        @api.post("/api/v2/documents/{ticker}/investigate", tags=["documents-rag"])
        async def _rag_investigate_unavailable(ticker: str) -> JSONResponse:
            _ = ticker
            return _rag_unavailable_response()


def create_app(
    data_root: Path | None = None,
    *,
    repository: ParquetRepository | None = None,
    duckdb_threads: int | None = None,
    duckdb_memory_limit: str | None = None,
    cors_origins: Sequence[str] | None = None,
    stress_max_concurrency: int = DEFAULT_STRESS_MAX_CONCURRENCY,
    stress_semaphore_timeout: float = DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
    valkey_url: str | None = None,
    rag_store: Any | None = None,
    rag_investigator: Any | None = None,
) -> FastAPI:
    """Create a configured read-only analytical FastAPI application."""
    allowed_origins = resolve_cors_origins(cors_origins)
    base_lifespan = build_lifespan(
        data_root=data_root,
        repository=repository,
        duckdb_threads=duckdb_threads,
        duckdb_memory_limit=duckdb_memory_limit,
        stress_max_concurrency=stress_max_concurrency,
        stress_semaphore_timeout=stress_semaphore_timeout,
        valkey_url=valkey_url,
    )

    @asynccontextmanager
    async def app_lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        async with base_lifespan(app_instance):
            prewarm_fn = getattr(app_instance.state, "_rag_prewarm_fn", None)
            if callable(prewarm_fn):
                prewarm_fn()
            yield

    api = FastAPI(
        title="finintel Read-Only Analytical API",
        version="2.0.0",
        description=(
            "Thread-safe read-only serving API over immutable Layer 3 snapshots "
            "and DuckLake lakehouse tables."
        ),
        lifespan=app_lifespan,
    )

    api.add_middleware(StressBulkheadMiddleware)
    api.add_middleware(RagAvailabilityMiddleware)
    attach_observability(api)

    # Deny-by-default CORS: only attach CORSMiddleware when explicit origins are configured.
    if allowed_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["If-None-Match", "Content-Type", "X-Request-ID"],
            expose_headers=[
                "ETag",
                "Cache-Control",
                "X-Request-ID",
                "X-Snapshot-ID",
                "X-Dagster-Run-ID",
                "X-Git-Commit-SHA",
                "X-Cache",
            ],
        )

    @api.exception_handler(ResourceNotFoundError)
    def _handle_not_found(
        _request: Request, exc: ResourceNotFoundError
    ) -> JSONResponse:
        detail = _sanitize_error_detail(
            str(exc), fallback="Requested resource was not found in active snapshot."
        )
        return JSONResponse(status_code=404, content={"detail": detail})

    @api.exception_handler(FileNotFoundError)
    def _handle_file_not_found(
        request: Request, _exc: FileNotFoundError
    ) -> JSONResponse:
        if request.url.path.startswith("/api/v2/documents/"):
            return _rag_unavailable_response()
        return JSONResponse(
            status_code=404,
            content={
                "detail": "Active snapshot or requested dataset is not available."
            },
        )

    @api.exception_handler(ValueError)
    def _handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
        raw = str(exc)
        lowered = raw.lower()
        if "not found" in lowered or "no ratio row found" in lowered:
            detail = _sanitize_error_detail(
                raw, fallback="Requested resource was not found in active snapshot."
            )
            return JSONResponse(status_code=404, content={"detail": detail})
        detail = _sanitize_error_detail(raw, fallback="Invalid query parameters.")
        return JSONResponse(status_code=422, content={"detail": detail})

    @api.exception_handler(ContractViolationError)
    def _handle_contract_violation(
        _request: Request, exc: ContractViolationError
    ) -> JSONResponse:
        detail = _sanitize_error_detail(
            str(exc), fallback="Snapshot data contract violation for requested query."
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    @api.exception_handler(Exception)
    def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        if request.url.path.startswith(
            "/api/v2/documents/"
        ) and _is_rag_unavailable_error(exc):
            logger.warning(
                "RAG endpoint unavailable (%s): %s",
                type(exc).__name__,
                request.url.path,
            )
            return _rag_unavailable_response()
        logger.exception(
            "Unhandled error while serving API request: %s", type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal serving error"},
        )

    # ------------------------------------------------------------------
    # Ops Probes: GET /health/live (event-loop only) & GET /health/ready
    # ------------------------------------------------------------------
    @api.get(
        "/health/live",
        response_model=LivenessResponse,
        tags=["health"],
    )
    async def get_health_live(response: Response) -> LivenessResponse:
        response.headers["Cache-Control"] = STRESS_CACHE_CONTROL_HEADER
        return LivenessResponse(status="alive")

    @api.get(
        "/health/ready",
        response_model=ReadinessResponse,
        tags=["health"],
    )
    def get_health_ready(
        request: Request,
        response: Response,
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> ReadinessResponse | JSONResponse:
        try:
            with service.repository.snapshot_session(connection=cursor) as pinned:
                meta = pinned.verify_active_snapshot_readable(connection=cursor)
        except (
            FileNotFoundError,
            ContractViolationError,
            duckdb.Error,
            OSError,
        ) as exc:
            body = ReadinessResponse(
                status="unready",
                active_snapshot_id=None,
                storage_backend="parquet",
                duckdb_ok=False,
                snapshot_readable=False,
                cache_enabled=cache.enabled,
                detail=_sanitize_error_detail(
                    str(exc), fallback="Active snapshot storage is unavailable."
                ),
            )
            return JSONResponse(
                status_code=503,
                content=body.model_dump(mode="json"),
                headers={"Cache-Control": STRESS_CACHE_CONTROL_HEADER},
            )

        _attach_lineage_state(request, meta)
        snap_id = _extract_snapshot_id(meta)
        _apply_cache_headers(response, snap_id)
        return ReadinessResponse(
            status="ready",
            active_snapshot_id=snap_id,
            storage_backend=_extract_storage_backend(meta),
            duckdb_ok=True,
            snapshot_readable=True,
            cache_enabled=cache.enabled,
        )

    # ------------------------------------------------------------------
    # Route 1: GET /api/v1/health
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/health",
        response_model=HealthResponse,
        tags=["health"],
    )
    def get_health(
        request: Request,
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> HealthResponse | Response:
        try:
            with service.repository.snapshot_session(connection=cursor) as pinned:
                meta = pinned.verify_active_snapshot_readable(connection=cursor)
        except (FileNotFoundError, ContractViolationError, duckdb.Error, OSError):
            degraded = HealthResponse(
                status="degraded",
                active_snapshot_id=None,
                storage_backend="parquet",
                rag_available=False,
            )
            return JSONResponse(
                status_code=503,
                content=degraded.model_dump(mode="json"),
                headers={"Cache-Control": STRESS_CACHE_CONTROL_HEADER},
            )

        _attach_lineage_state(request, meta)
        snap_id = _extract_snapshot_id(meta)
        rag_available = probe_rag_available(request.app.state)
        etag_key = health_etag_source(snap_id, _REQUEST_DATA_ROOT.get(), rag_available)
        not_modified = _conditional_not_modified(if_none_match, etag_key)
        if not_modified is not None:
            return not_modified

        _apply_cache_headers(response, etag_key)
        return HealthResponse(
            status="ok",
            active_snapshot_id=snap_id,
            storage_backend=_extract_storage_backend(meta),
            rag_available=rag_available,
        )

    # ------------------------------------------------------------------
    # Route 2: GET /api/v1/snapshot/overview
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/snapshot/overview",
        response_model=SnapshotOverviewResponse,
        tags=["snapshot"],
    )
    def get_snapshot_overview(
        request: Request,
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> SnapshotOverviewResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache, snap_id, "v1/snapshot/overview"
            )
            if hit is not None:
                return hit

            overview = DashboardQueryService(pinned).get_snapshot_overview(
                connection=cursor
            )

        _apply_cache_headers(response, overview.snapshot_id)
        body = SnapshotOverviewResponse(
            snapshot_id=overview.snapshot_id,
            published_at=overview.published_at,
            storage_backend=overview.storage_backend,
            total_issuers=overview.total_issuers,
            total_ratio_periods=overview.total_ratio_periods,
            pass_periods=overview.pass_periods,
            warning_periods=overview.warning_periods,
            quarantined_rows=overview.quarantined_rows,
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 3: GET /api/v1/issuers
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/issuers",
        response_model=IssuersPageResponse,
        tags=["issuers"],
    )
    def list_issuers(
        request: Request,
        response: Response,
        limit: int = Query(200, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> IssuersPageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/issuers",
                {"limit": limit, "offset": offset},
            )
            if hit is not None:
                return hit

            pinned_svc = DashboardQueryService(pinned)
            total = pinned_svc.count_issuers(connection=cursor)
            page_df = pinned_svc.list_issuers(
                limit=limit, offset=offset, connection=cursor
            )

        items = [IssuerItem(**row) for row in _frame_to_records(page_df)]
        _apply_cache_headers(response, snap_id)
        body = IssuersPageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=items,
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 4: GET /api/v1/companies/{ticker}/profile
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/profile",
        response_model=CompanyProfileResponse,
        tags=["companies"],
    )
    def get_company_profile(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_view: PeriodView = Query("TTM"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyProfileResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/companies/profile",
                {
                    "ticker": ticker.strip().upper(),
                    "statement_scope": statement_scope,
                    "period_view": period_view,
                },
            )
            if hit is not None:
                return hit

            profile = DashboardQueryService(pinned).get_company_profile(
                ticker,
                statement_scope=statement_scope,
                period_view=period_view,
                connection=cursor,
            )

        ratios_bounded = profile.ratios_table.tail(200)
        core_cols = (
            "period_end_date",
            "fiscal_year",
            "fiscal_quarter",
            "taxonomy_version",
            "core_operating_profit_ttm",
            "core_operating_profit_flag",
            "core_operating_margin",
            "core_operating_margin_flag",
            "non_core_distortion_to_assets",
            "non_core_distortion_to_assets_flag",
            "core_to_ebt_ratio",
            "core_to_ebt_ratio_flag",
        )
        core_df = ratios_bounded.select(
            *(col for col in core_cols if col in ratios_bounded.columns)
        )

        ml_items: list[MLScoreItem] = []
        for row in _frame_to_records(profile.ml_scores.tail(200)):
            raw_shap = row.get("shap_top_drivers_json")
            ml_items.append(
                MLScoreItem(
                    **row,
                    shap_top_drivers=parse_shap_top_drivers(
                        str(raw_shap) if raw_shap is not None else None
                    ),
                )
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyProfileResponse(
            snapshot_id=snap_id,
            ticker=profile.ticker,
            issuer_id=profile.issuer_id,
            company_name_vn=profile.company_name_vn,
            accounting_regime=profile.accounting_regime,
            statement_scope=statement_scope,
            period_view=profile.period_view,
            latest_taxonomy_version=profile.latest_taxonomy_version,
            latest_quality_status=profile.latest_quality_status,
            latest_warning_codes=list(profile.latest_warning_codes),
            growth_series=[
                GrowthPoint(**row)
                for row in _frame_to_records(profile.growth_series.tail(200))
            ],
            capital_structure_series=[
                CapitalStructurePoint(**row)
                for row in _frame_to_records(profile.capital_structure_series.tail(200))
            ],
            dupont_series=[
                DuPontPoint(**row)
                for row in _frame_to_records(profile.dupont_series.tail(200))
            ],
            core_earnings_series=[
                CoreEarningsPoint(**row) for row in _frame_to_records(core_df)
            ],
            red_flags=[
                RedFlagItem(**row)
                for row in _frame_to_records(profile.red_flags.head(200))
            ],
            ratios_table=_frame_to_records(ratios_bounded),
            ml_scores=ml_items,
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 5: GET /api/v1/companies/{ticker}/benchmarks
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/benchmarks",
        response_model=CompanyBenchmarksResponse,
        tags=["companies"],
    )
    def get_company_benchmarks(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        metric_name: RatioName = Query("roe_total"),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        include_history: bool = Query(True),
        peer_limit: int = Query(200, ge=1, le=200),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyBenchmarksResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/companies/benchmarks",
                {
                    "ticker": ticker.strip().upper(),
                    "metric_name": metric_name,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": (
                        period_end_date.isoformat()
                        if period_end_date is not None
                        else None
                    ),
                    "include_history": include_history,
                    "peer_limit": peer_limit,
                },
            )
            if hit is not None:
                return hit

            pinned_svc = DashboardQueryService(pinned)
            comparison = pinned_svc.get_industry_comparison(
                ticker,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=period_end_date,
                metric_name=metric_name,
                connection=cursor,
            )
            history_df = (
                pinned_svc.get_peer_band_history(
                    ticker,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    metric_name=metric_name,
                    connection=cursor,
                )
                if include_history
                else pl.DataFrame()
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyBenchmarksResponse(
            snapshot_id=snap_id,
            ticker=comparison.ticker,
            issuer_id=comparison.issuer_id,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=comparison.period_end_date,
            metric_name=metric_name,
            peer_group_level=comparison.peer_group_level,
            icb_code_used=comparison.icb_code_used,
            peer_count=comparison.peer_count,
            company_value=_finite_scalar(comparison.company_value),
            peer_median=_finite_scalar(comparison.peer_median),
            peer_p25=_finite_scalar(comparison.peer_p25),
            peer_p75=_finite_scalar(comparison.peer_p75),
            peer_iqr=_finite_scalar(comparison.peer_iqr),
            percentile_rank=_finite_scalar(comparison.percentile_rank),
            robust_z_score=_finite_scalar(comparison.robust_z_score),
            industry_snapshot_date=comparison.industry_snapshot_date,
            peer_ranking=[
                PeerRankingItem(**row)
                for row in _frame_to_records(comparison.peer_ranking.head(peer_limit))
            ],
            history=[
                PeerBandHistoryPoint(**row)
                for row in _frame_to_records(history_df.tail(200))
            ],
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 6: GET /api/v1/companies/{ticker}/lineage
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/lineage",
        response_model=RatioLineageResponse,
        tags=["companies"],
    )
    def get_company_lineage(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        ratio_name: RatioName = Query("roe_total"),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> RatioLineageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/companies/lineage",
                {
                    "ticker": ticker.strip().upper(),
                    "ratio_name": ratio_name,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": (
                        period_end_date.isoformat()
                        if period_end_date is not None
                        else None
                    ),
                },
            )
            if hit is not None:
                return hit

            pinned_svc = DashboardQueryService(pinned)
            resolved_end = period_end_date
            if resolved_end is None:
                periods = pinned_svc.list_ratio_periods(
                    ticker, statement_scope=statement_scope, connection=cursor
                ).filter(pl.col("period_type") == period_type)
                if periods.is_empty():
                    raise ResourceNotFoundError(
                        f"No ratio periods found for {ticker} {statement_scope} {period_type}."
                    )
                first_date = periods["period_end_date"][0]
                if not isinstance(first_date, date):
                    raise ResourceNotFoundError(
                        f"No valid ratio period date found for {ticker}."
                    )
                resolved_end = first_date

            card = pinned_svc.get_ratio_lineage(
                ticker,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=resolved_end,
                ratio_name=ratio_name,
                connection=cursor,
            )

        def _to_lineage_item(comp: Any) -> LineageComponentItem:
            return LineageComponentItem(
                **{
                    **comp.__dict__,
                    "value": _finite_scalar(comp.value),
                }
            )

        _apply_cache_headers(response, snap_id)
        body = RatioLineageResponse(
            snapshot_id=snap_id,
            ticker=card.ticker,
            issuer_id=card.issuer_id,
            statement_scope=statement_scope,
            period_type=period_type,
            fiscal_year=card.fiscal_year,
            fiscal_quarter=card.fiscal_quarter,
            period_end_date=card.period_end_date,
            ratio_name=ratio_name,
            display_name_vn=card.display_name_vn,
            formula=card.formula,
            ratio_value=_finite_scalar(card.ratio_value),
            status_flag=card.status_flag,
            taxonomy_version=card.taxonomy_version,
            denominator_mode=card.denominator_mode,
            anchor_filing_id=card.anchor_filing_id,
            source_filing_ids=list(card.source_filing_ids),
            numerator_components=[
                _to_lineage_item(comp) for comp in card.numerator_components
            ],
            denominator_components=[
                _to_lineage_item(comp) for comp in card.denominator_components
            ],
            orphan_filing_ids=list(card.orphan_filing_ids),
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 7: GET /api/v1/screener
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/screener",
        response_model=ScreenerResponse,
        tags=["screener"],
    )
    def screen_market(
        request: Request,
        response: Response,
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        icb_code_level2: str | None = Query(None, pattern=_ICB_CODE_PATTERN),
        exchange: Exchange | None = Query(None),
        min_f_score: int | None = Query(None, ge=0, le=9),
        altman_zone: AltmanZone | None = Query(None),
        risk_tier: RiskTier | None = Query(None),
        risk_tiers: str | None = Query(None, max_length=32),
        beneish_flag: BeneishFlag | None = Query(None),
        max_distress_prob: float | None = Query(None, ge=0.0, le=1.0),
        min_distress_prob: float | None = Query(None, ge=0.0, le=1.0),
        max_red_flags: int | None = Query(None, ge=0),
        min_red_flags: int | None = Query(None, ge=0),
        q: str | None = Query(None, max_length=80),
        sort_by: ScreenerSortField = Query("calibrated_distress_prob"),
        sort_dir: SortDirection = Query("desc"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> ScreenerResponse | Response:
        effective_tiers = parse_screener_risk_tiers(risk_tier, risk_tiers)
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/screener",
                build_screener_cache_params(
                    period_type=period_type,
                    period_end_date=period_end_date,
                    statement_scope=statement_scope,
                    icb_code_level2=icb_code_level2,
                    exchange=exchange,
                    min_f_score=min_f_score,
                    altman_zone=altman_zone,
                    effective_tiers=effective_tiers,
                    beneish_flag=beneish_flag,
                    max_distress_prob=max_distress_prob,
                    min_distress_prob=min_distress_prob,
                    max_red_flags=max_red_flags,
                    min_red_flags=min_red_flags,
                    q=q,
                    sort_by=sort_by,
                    sort_dir=sort_dir,
                    limit=limit,
                    offset=offset,
                ),
            )
            if hit is not None:
                return hit

            pinned_svc = DashboardQueryService(pinned)
            page_df, total = pinned_svc.screen_market(
                period_type=period_type,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                exchange=exchange,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                risk_tier=risk_tier,
                risk_tiers=risk_tiers,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                q=q,
                sort_by=sort_by,
                sort_dir=sort_dir,
                limit=limit,
                offset=offset,
                connection=cursor,
            )
            resolved_end = period_end_date
            if resolved_end is None:
                if not page_df.is_empty():
                    first = page_df["period_end_date"][0]
                    if isinstance(first, date):
                        resolved_end = first
                else:
                    available = pinned_svc.list_available_periods(
                        statement_scope=statement_scope,
                        period_type=period_type,
                        connection=cursor,
                    )
                    resolved_end = available[0] if available else None

        _apply_cache_headers(response, snap_id)
        body = ScreenerResponse(
            snapshot_id=snap_id,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_end,
            total=total,
            limit=limit,
            offset=offset,
            items=[ScreenerItem(**row) for row in _frame_to_records(page_df)],
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 7b: GET /api/v1/screener/export (UI-05a full CSV export)
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/screener/export",
        tags=["screener"],
        responses={
            200: {
                "content": {"text/csv; charset=utf-8": {}},
                "description": "Full filtered screener dataset as UTF-8 BOM CSV.",
            }
        },
    )
    def export_screener_csv(
        request: Request,
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        icb_code_level2: str | None = Query(None, pattern=_ICB_CODE_PATTERN),
        exchange: Exchange | None = Query(None),
        min_f_score: int | None = Query(None, ge=0, le=9),
        altman_zone: AltmanZone | None = Query(None),
        risk_tier: RiskTier | None = Query(None),
        risk_tiers: str | None = Query(None, max_length=32),
        beneish_flag: BeneishFlag | None = Query(None),
        max_distress_prob: float | None = Query(None, ge=0.0, le=1.0),
        min_distress_prob: float | None = Query(None, ge=0.0, le=1.0),
        max_red_flags: int | None = Query(None, ge=0),
        min_red_flags: int | None = Query(None, ge=0),
        q: str | None = Query(None, max_length=80),
        sort_by: ScreenerSortField = Query("calibrated_distress_prob"),
        sort_dir: SortDirection = Query("desc"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            full_df = DashboardQueryService(pinned).export_screener(
                period_type=period_type,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                exchange=exchange,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                risk_tier=risk_tier,
                risk_tiers=risk_tiers,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                q=q,
                sort_by=sort_by,
                sort_dir=sort_dir,
                connection=cursor,
            )

        csv_bytes = _dataframe_to_safe_csv_bytes(full_df)
        headers: dict[str, str] = {
            "Content-Disposition": 'attachment; filename="finintel-screener.csv"',
            "Cache-Control": CACHE_CONTROL_HEADER,
        }
        if snap_id is not None:
            headers["ETag"] = _format_etag(snap_id)
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )

    # ------------------------------------------------------------------
    # Route 8: POST /api/v1/stress-test (event-loop bulkhead via StressBulkheadMiddleware)
    # ------------------------------------------------------------------
    @api.post(
        "/api/v1/stress-test",
        response_model=StressTestResponse,
        tags=["stress-test"],
    )
    def run_stress_test(
        request: Request,
        payload: StressScenarioRequest,
        response: Response,
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        sem_cfg: tuple[threading.Semaphore, float] = Depends(get_stress_semaphore),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> StressTestResponse | Response:
        semaphore, sem_timeout = sem_cfg
        already_held = bool(getattr(request.state, "_stress_permit_held", False))
        if not already_held:
            if not semaphore.acquire(timeout=sem_timeout):
                raise HTTPException(
                    status_code=503,
                    detail="Stress-test simulator is busy; please retry shortly.",
                    headers={"Retry-After": "1"},
                )
        try:
            with service.repository.snapshot_session(connection=cursor) as pinned:
                meta = pinned.get_snapshot_metadata(connection=cursor)
                _attach_lineage_state(request, meta)
                snap_id = _extract_snapshot_id(meta)
                cache_key, hit = _cached_json_response(
                    cache,
                    snap_id,
                    "v1/stress-test",
                    payload.model_dump(mode="json"),
                    cache_control=STRESS_CACHE_CONTROL_HEADER,
                )
                if hit is not None:
                    return hit

                pinned_svc = DashboardQueryService(pinned)
                inputs_df = pinned_svc.get_stress_test_snapshot_inputs(
                    statement_scope=payload.statement_scope,
                    period_type=payload.period_type,
                    period_end_date=payload.period_end_date,
                    tickers=payload.tickers,
                    connection=cursor,
                )

            response.headers["Cache-Control"] = STRESS_CACHE_CONTROL_HEADER
            if snap_id is not None:
                response.headers["ETag"] = _format_etag(snap_id)

            scenario = StressScenario(
                name=payload.scenario_name,
                interest_rate_shock_bps=payload.interest_rate_shock_bps,
                revenue_shock_pct=payload.revenue_shock_pct,
                gross_margin_shock_pp=payload.gross_margin_shock_pp,
                dso_shock_days=payload.dso_shock_days,
                dio_shock_days=payload.dio_shock_days,
            )

            if inputs_df.is_empty():
                empty_body = StressTestResponse(
                    **build_empty_stress_response_kwargs(
                        snapshot_id=snap_id,
                        scenario_name=scenario.name,
                        statement_scope=payload.statement_scope,
                        period_type=payload.period_type,
                        period_end_date=payload.period_end_date,
                    )
                )
                return _store_cached_model(
                    cache,
                    cache_key,
                    response,
                    empty_body,
                    snap_id=snap_id,
                    cache_control=STRESS_CACHE_CONTROL_HEADER,
                )

            resolved_end = inputs_df["period_end_date"][0]
            with trace_span(
                SPAN_ANALYTICS_STRESS_TEST,
                {
                    "stress.scenario_name": scenario.name,
                    "stress.statement_scope": payload.statement_scope,
                    "stress.period_type": payload.period_type,
                    "stress.issuer_count": inputs_df.height,
                },
            ):
                stressed_df = simulate_financial_stress(inputs_df, scenario)
                sector_df = summarize_stress_by_sector(stressed_df)
                market_df = summarize_stress_by_sector(
                    stressed_df.with_columns(
                        pl.lit("MARKET").alias("icb_code_level2"),
                        pl.lit("MARKET").alias("icb_name_level2"),
                    )
                )

            body = StressTestResponse(
                **build_stress_response_kwargs(
                    snapshot_id=snap_id,
                    scenario_name=scenario.name,
                    statement_scope=payload.statement_scope,
                    period_type=payload.period_type,
                    resolved_end=resolved_end,
                    fallback_period_end_date=payload.period_end_date,
                    stressed_df=stressed_df,
                    sector_df=sector_df,
                    market_df=market_df,
                    top_n=payload.top_n,
                    include_all_newly_distressed=payload.include_all_newly_distressed,
                    reference_icrs=payload.reference_icrs,
                )
            )
            return _store_cached_model(
                cache,
                cache_key,
                response,
                body,
                snap_id=snap_id,
                cache_control=STRESS_CACHE_CONTROL_HEADER,
            )
        finally:
            if not already_held:
                semaphore.release()

    # ------------------------------------------------------------------
    # Route 9: GET /api/v1/quality/summary
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/quality/summary",
        response_model=QualitySummaryPageResponse,
        tags=["quality"],
    )
    def get_quality_summary(
        request: Request,
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> QualitySummaryPageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/quality/summary",
                {"limit": limit, "offset": offset},
            )
            if hit is not None:
                return hit

            page_df, total = DashboardQueryService(pinned).get_quality_summary_page(
                limit=limit, offset=offset, connection=cursor
            )

        _apply_cache_headers(response, snap_id)
        body = QualitySummaryPageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=[QualitySummaryItem(**row) for row in _frame_to_records(page_df)],
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # Route 10: GET /api/v1/quality/quarantine
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/quality/quarantine",
        response_model=AuditQuarantinePageResponse,
        tags=["quality"],
    )
    def get_quality_quarantine(
        request: Request,
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> AuditQuarantinePageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v1/quality/quarantine",
                {"limit": limit, "offset": offset},
            )
            if hit is not None:
                return hit

            page_df, total = DashboardQueryService(pinned).get_audit_quarantine_page(
                limit=limit, offset=offset, connection=cursor
            )

        _apply_cache_headers(response, snap_id)
        body = AuditQuarantinePageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=[AuditQuarantineItem(**row) for row in _frame_to_records(page_df)],
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    # ------------------------------------------------------------------
    # V2 Read-Only UI Extension Routes (/api/v2/*)
    # ------------------------------------------------------------------
    @api.get(
        "/api/v2/meta/periods",
        response_model=MetaPeriodsResponse,
        tags=["v2-meta"],
    )
    def get_v2_meta_periods(
        request: Request,
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> MetaPeriodsResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(cache, snap_id, "v2/meta/periods")
            if hit is not None:
                return hit

            published_at, available_periods, sectors = DashboardQueryService(
                pinned
            ).get_meta_periods_and_sectors(connection=cursor)

        q_periods = available_periods.get("CONSOLIDATED", {}).get("QUARTER") or []
        freshness = build_freshness_item(
            snapshot_id=snap_id,
            published_at=published_at,
            data_root=_REQUEST_DATA_ROOT.get(),
            bctc_latest_period_end_date=q_periods[0] if q_periods else None,
        )
        _apply_cache_headers(response, snap_id)
        body = MetaPeriodsResponse(
            snapshot_id=snap_id,
            published_at=published_at,
            available_periods=available_periods,
            sectors_level2=[SectorOptionItem(**s) for s in sectors],
            freshness=freshness,
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/meta/dictionary",
        response_model=MetaDictionaryResponse,
        tags=["v2-meta"],
    )
    def get_v2_meta_dictionary(
        request: Request,
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> MetaDictionaryResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(cache, snap_id, "v2/meta/dictionary")
            if hit is not None:
                return hit

        _apply_cache_headers(response, snap_id)
        body = MetaDictionaryResponse(**build_meta_dictionary_kwargs())
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/meta/model-card",
        response_model=ModelCardResponse,
        tags=["v2-meta"],
    )
    def get_v2_meta_model_card(
        request: Request,
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> ModelCardResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(cache, snap_id, "v2/meta/model-card")
            if hit is not None:
                return hit

            card_payload = DashboardQueryService(pinned).get_model_card(
                connection=cursor
            )

        _apply_cache_headers(response, snap_id)
        body = ModelCardResponse(snapshot_id=snap_id, **card_payload)
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/companies/{ticker}/benchmarks/summary",
        response_model=CompanyBenchmarksSummaryResponse,
        tags=["v2-companies"],
    )
    def get_v2_company_benchmarks_summary(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        metrics: list[RatioName] | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyBenchmarksSummaryResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/companies/benchmarks/summary",
                {
                    "ticker": ticker.strip().upper(),
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": (
                        period_end_date.isoformat()
                        if period_end_date is not None
                        else None
                    ),
                    "metrics": list(metrics) if metrics is not None else None,
                },
            )
            if hit is not None:
                return hit

            issuer_id, resolved_end, raw_items = DashboardQueryService(
                pinned
            ).get_company_benchmarks_summary(
                ticker,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=period_end_date,
                metrics=metrics,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyBenchmarksSummaryResponse(
            snapshot_id=snap_id,
            ticker=ticker.strip().upper(),
            issuer_id=issuer_id,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_end,
            items=[BenchmarkSummaryItem(**item) for item in raw_items],  # type: ignore[arg-type]
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/companies/{ticker}/statements",
        response_model=CompanyStatementsResponse,
        tags=["v2-companies"],
    )
    def get_v2_company_statements(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_type: StatementType = Query("INCOME_STATEMENT"),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        periods_limit: int = Query(8, ge=1, le=20),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyStatementsResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/companies/statements",
                {
                    "ticker": ticker.strip().upper(),
                    "statement_type": statement_type,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "periods_limit": periods_limit,
                },
            )
            if hit is not None:
                return hit

            issuer_id, periods, rows = DashboardQueryService(
                pinned
            ).get_company_statements(
                ticker,
                statement_type=statement_type,
                statement_scope=statement_scope,
                period_type=period_type,
                periods_limit=periods_limit,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyStatementsResponse(
            snapshot_id=snap_id,
            ticker=ticker.strip().upper(),
            issuer_id=issuer_id,
            statement_type=statement_type,
            statement_scope=statement_scope,
            period_type=period_type,
            periods=[StatementPeriodItem(**p) for p in periods],  # type: ignore[arg-type]
            rows=[StatementRowItem(**r) for r in rows],  # type: ignore[arg-type]
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/companies/{ticker}/filings",
        response_model=CompanyFilingsResponse,
        tags=["v2-companies"],
    )
    def get_v2_company_filings(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope | None = Query(None),
        limit: int = Query(100, ge=1, le=200),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyFilingsResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/companies/filings",
                {
                    "ticker": ticker.strip().upper(),
                    "statement_scope": statement_scope,
                    "limit": limit,
                },
            )
            if hit is not None:
                return hit

            issuer_id, items = DashboardQueryService(pinned).get_company_filings(
                ticker,
                statement_scope=statement_scope,
                limit=limit,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyFilingsResponse(
            snapshot_id=snap_id,
            ticker=ticker.strip().upper(),
            issuer_id=issuer_id,
            items=[CompanyFilingItem(**item) for item in items],  # type: ignore[arg-type]
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/companies/{ticker}/market-overlay",
        response_model=CompanyMarketOverlayResponse,
        tags=["v2-companies"],
    )
    def get_v2_company_market_overlay(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        as_of: date | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyMarketOverlayResponse | Response:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/companies/market-overlay",
                {
                    "ticker": clean_ticker,
                    "statement_scope": statement_scope,
                    "as_of": as_of.isoformat() if as_of is not None else None,
                },
            )
            if hit is not None:
                return hit

            overlay_payload = DashboardQueryService(pinned).get_company_market_overlay(
                clean_ticker,
                statement_scope=statement_scope,
                as_of=as_of,
                port_data_root=service.repository._source,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = CompanyMarketOverlayResponse(snapshot_id=snap_id, **overlay_payload)
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/sectors/risk-summary",
        response_model=SectorRiskSummaryResponse,
        tags=["v2-market"],
    )
    def get_v2_sector_risk_summary(
        request: Request,
        response: Response,
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> SectorRiskSummaryResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/sectors/risk-summary",
                {
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": (
                        period_end_date.isoformat()
                        if period_end_date is not None
                        else None
                    ),
                },
            )
            if hit is not None:
                return hit

            summary = DashboardQueryService(pinned).get_sector_risk_summary(
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=period_end_date,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = SectorRiskSummaryResponse(
            snapshot_id=snap_id,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=summary["period_end_date"],
            total_issuers=summary["total_issuers"],
            safe_zone_count=summary["safe_zone_count"],
            grey_zone_count=summary["grey_zone_count"],
            distress_zone_count=summary["distress_zone_count"],
            beneish_flagged_count=summary["beneish_flagged_count"],
            high_red_flag_count=summary["high_red_flag_count"],
            sectors=[SectorRiskItem(**s) for s in summary["sectors"]],
            top_deteriorating=[
                MarketChangeItem(**item)  # type: ignore[arg-type]
                for item in summary["top_deteriorating"]
            ],
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    @api.get(
        "/api/v2/market/changes",
        response_model=MarketChangesResponse,
        tags=["v2-market"],
    )
    def get_v2_market_changes(
        request: Request,
        response: Response,
        tickers: str | None = Query(None, max_length=600),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        limit: int = Query(30, ge=1, le=100),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> MarketChangesResponse | Response:
        parsed_tickers: list[str] | None = None
        if tickers is not None and tickers.strip():
            raw_tokens = [
                tok.strip().upper() for tok in tickers.split(",") if tok.strip()
            ]
            for tok in raw_tokens:
                if not re.fullmatch(_TICKER_PATTERN, tok):
                    raise ValueError(f"Invalid ticker format in tickers list: {tok!r}")
            parsed_tickers = raw_tokens[:50]

        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            _attach_lineage_state(request, meta)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            cache_key, hit = _cached_json_response(
                cache,
                snap_id,
                "v2/market/changes",
                {
                    "tickers": parsed_tickers,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "limit": limit,
                },
            )
            if hit is not None:
                return hit

            items = DashboardQueryService(pinned).get_market_changes(
                tickers=parsed_tickers,
                statement_scope=statement_scope,
                period_type=period_type,
                limit=limit,
                connection=cursor,
            )

        _apply_cache_headers(response, snap_id)
        body = MarketChangesResponse(
            snapshot_id=snap_id,
            statement_scope=statement_scope,
            period_type=period_type,
            items=[MarketChangeItem(**item) for item in items],  # type: ignore[arg-type]
        )
        return _store_cached_model(cache, cache_key, response, body, snap_id=snap_id)

    _mount_rag_router(
        api,
        valkey_url=valkey_url,
        rag_store=rag_store,
        rag_investigator=rag_investigator,
    )

    return api


def run_server(
    *,
    host: str = DEFAULT_API_HOST,
    port: int = DEFAULT_API_PORT,
    data_root: Path | None = None,
    engine: Literal["granian", "uvicorn"] | None = None,
    workers: int | None = None,
) -> None:
    """Run the Layer 5 FastAPI server with Granian (Rust ASGI) or Uvicorn."""
    if data_root is not None:
        os.environ["FININTEL_DATA_ROOT"] = str(data_root)
    default_engine: str = "uvicorn" if os.name == "nt" else "granian"
    raw_engine: str = (
        engine
        if engine is not None
        else (os.getenv("FININTEL_SERVER_ENGINE") or default_engine)
    )
    resolved_engine = raw_engine.strip().lower()
    raw_workers = (
        workers
        if workers is not None
        else int(os.getenv("FININTEL_WORKERS", "1") or "1")
    )
    resolved_workers = max(1, int(raw_workers))

    if resolved_engine == "granian":
        try:
            from granian import Granian
            from granian.constants import Interfaces

            server = Granian(
                target="finintel.serving.api:app",
                address=host,
                port=int(port),
                interface=Interfaces.ASGI,
                workers=resolved_workers,
            )
            server.serve()
            return
        except ImportError:
            logger.warning("Granian is not installed; falling back to Uvicorn.")

    import uvicorn

    if resolved_workers > 1:
        uvicorn.run(
            "finintel.serving.api:app",
            host=host,
            port=port,
            workers=resolved_workers,
        )
    else:
        application = create_app(data_root=data_root)
        uvicorn.run(application, host=host, port=port)


app = create_app()
