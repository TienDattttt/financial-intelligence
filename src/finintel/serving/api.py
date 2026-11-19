"""Read-only analytical FastAPI service (10 HTTP routes under ``/api/v1``).

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

import logging
import math
import re
import threading
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

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
from finintel.serving.dependencies import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_STRESS_MAX_CONCURRENCY,
    DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
    build_lifespan,
    get_db_cursor,
    get_query_service,
    get_stress_semaphore,
    resolve_cors_origins,
)
from finintel.serving.queries import (
    AltmanZone,
    BeneishFlag,
    DashboardQueryService,
    PeriodType,
    PeriodView,
    StatementScope,
)
from finintel.serving.schemas import (
    AuditQuarantineItem,
    AuditQuarantinePageResponse,
    CapitalStructurePoint,
    CompanyBenchmarksResponse,
    CompanyProfileResponse,
    CoreEarningsPoint,
    DuPontPoint,
    GrowthPoint,
    HealthResponse,
    IssuerItem,
    IssuersPageResponse,
    LineageComponentItem,
    MLScoreItem,
    PeerBandHistoryPoint,
    PeerRankingItem,
    QualitySummaryItem,
    QualitySummaryPageResponse,
    RatioLineageResponse,
    RatioName,
    RedFlagItem,
    ScreenerItem,
    ScreenerResponse,
    SnapshotOverviewResponse,
    StressIssuerItem,
    StressScenarioRequest,
    StressSectorSummaryItem,
    StressTestResponse,
    parse_shap_top_drivers,
)
from finintel.storage.repository import ParquetRepository

logger = logging.getLogger("finintel.serving.api")

CACHE_CONTROL_HEADER = "private, max-age=0, must-revalidate"
STRESS_CACHE_CONTROL_HEADER = "private, no-store"
_TICKER_PATTERN = r"^[A-Z0-9]{1,10}$"
_ICB_CODE_PATTERN = r"^[A-Za-z0-9_-]{1,20}$"
_PATH_LEAK_RE = re.compile(
    r"([A-Za-z]:\\|\\\\|/(?:[A-Za-z0-9_.-]+/)+|\.parquet\b|\.sqlite\b|\.json\b|\.py\b|Traceback)",
    re.IGNORECASE,
)


class ResourceNotFoundError(LookupError):
    """Domain lookup failure mapped cleanly to HTTP 404 without leaking internals."""


def _sanitize_error_detail(message: str, *, fallback: str) -> str:
    """Strip any filesystem paths, extensions, or tracebacks from client error details."""
    cleaned = " ".join(message.split()).strip()
    if not cleaned or _PATH_LEAK_RE.search(cleaned):
        return fallback
    return cleaned[:200]


def _format_etag(snapshot_id: str) -> str:
    """Format an RFC 9110 strong quoted entity-tag."""
    safe = snapshot_id.replace('"', "")
    return f'"{safe}"'


def _if_none_match_matches(if_none_match: str | None, snapshot_id: str | None) -> bool:
    """Evaluate RFC 9110 §13.1.2 ``If-None-Match`` using weak comparison."""
    if not if_none_match or not snapshot_id:
        return False
    target = _format_etag(snapshot_id)
    for raw_token in if_none_match.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == target:
            return True
    return False


def _conditional_not_modified(
    if_none_match: str | None, snapshot_id: str | None
) -> Response | None:
    """Return HTTP 304 response when ``If-None-Match`` matches the pinned snapshot."""
    if snapshot_id is None:
        return None
    if _if_none_match_matches(if_none_match, snapshot_id):
        return Response(
            status_code=304,
            headers={
                "ETag": _format_etag(snapshot_id),
                "Cache-Control": CACHE_CONTROL_HEADER,
            },
        )
    return None


def _apply_cache_headers(response: Response, snapshot_id: str | None) -> None:
    response.headers["Cache-Control"] = CACHE_CONTROL_HEADER
    if snapshot_id is not None:
        response.headers["ETag"] = _format_etag(snapshot_id)


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


def _extract_snapshot_id(meta: dict[str, object]) -> str | None:
    raw = meta.get("snapshot_id")
    return str(raw) if isinstance(raw, str) and raw else None


def _extract_storage_backend(meta: dict[str, object]) -> str:
    raw = meta.get("storage_backend")
    return str(raw) if isinstance(raw, str) and raw else "parquet"


def create_app(
    data_root: Path | None = None,
    *,
    repository: ParquetRepository | None = None,
    duckdb_threads: int | None = None,
    duckdb_memory_limit: str | None = None,
    cors_origins: Sequence[str] | None = None,
    stress_max_concurrency: int = DEFAULT_STRESS_MAX_CONCURRENCY,
    stress_semaphore_timeout: float = DEFAULT_STRESS_SEMAPHORE_TIMEOUT,
) -> FastAPI:
    """Create a configured read-only analytical FastAPI application."""
    allowed_origins = resolve_cors_origins(cors_origins)
    app_lifespan = build_lifespan(
        data_root=data_root,
        repository=repository,
        duckdb_threads=duckdb_threads,
        duckdb_memory_limit=duckdb_memory_limit,
        stress_max_concurrency=stress_max_concurrency,
        stress_semaphore_timeout=stress_semaphore_timeout,
    )

    api = FastAPI(
        title="finintel Read-Only Analytical API",
        version="2.0.0",
        description=(
            "Thread-safe read-only serving API over immutable Layer 3 snapshots "
            "and DuckLake lakehouse tables."
        ),
        lifespan=app_lifespan,
    )

    # Deny-by-default CORS: only attach CORSMiddleware when explicit origins are configured.
    if allowed_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["If-None-Match", "Content-Type"],
            expose_headers=["ETag", "Cache-Control"],
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
        _request: Request, _exc: FileNotFoundError
    ) -> JSONResponse:
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
    def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled error while serving API request: %s", type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal serving error"},
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
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> HealthResponse | Response:
        try:
            with service.repository.snapshot_session(connection=cursor) as pinned:
                meta = pinned.get_snapshot_metadata(connection=cursor)
        except FileNotFoundError:
            _apply_cache_headers(response, None)
            return HealthResponse(
                status="degraded",
                active_snapshot_id=None,
                storage_backend="parquet",
            )

        snap_id = _extract_snapshot_id(meta)
        not_modified = _conditional_not_modified(if_none_match, snap_id)
        if not_modified is not None:
            return not_modified

        _apply_cache_headers(response, snap_id)
        return HealthResponse(
            status="ok",
            active_snapshot_id=snap_id,
            storage_backend=_extract_storage_backend(meta),
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
        response: Response,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> SnapshotOverviewResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            overview = DashboardQueryService(pinned).get_snapshot_overview(
                connection=cursor
            )

        _apply_cache_headers(response, overview.snapshot_id)
        return SnapshotOverviewResponse(
            snapshot_id=overview.snapshot_id,
            published_at=overview.published_at,
            storage_backend=overview.storage_backend,
            total_issuers=overview.total_issuers,
            total_ratio_periods=overview.total_ratio_periods,
            pass_periods=overview.pass_periods,
            warning_periods=overview.warning_periods,
            quarantined_rows=overview.quarantined_rows,
        )

    # ------------------------------------------------------------------
    # Route 3: GET /api/v1/issuers
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/issuers",
        response_model=IssuersPageResponse,
        tags=["issuers"],
    )
    def list_issuers(
        response: Response,
        limit: int = Query(200, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> IssuersPageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            pinned_svc = DashboardQueryService(pinned)
            total = pinned_svc.count_issuers(connection=cursor)
            page_df = pinned_svc.list_issuers(
                limit=limit, offset=offset, connection=cursor
            )

        items = [IssuerItem(**row) for row in _frame_to_records(page_df)]
        _apply_cache_headers(response, snap_id)
        return IssuersPageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=items,
        )

    # ------------------------------------------------------------------
    # Route 4: GET /api/v1/companies/{ticker}/profile
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/profile",
        response_model=CompanyProfileResponse,
        tags=["companies"],
    )
    def get_company_profile(
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_view: PeriodView = Query("TTM"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> CompanyProfileResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

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
        return CompanyProfileResponse(
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

    # ------------------------------------------------------------------
    # Route 5: GET /api/v1/companies/{ticker}/benchmarks
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/benchmarks",
        response_model=CompanyBenchmarksResponse,
        tags=["companies"],
    )
    def get_company_benchmarks(
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
    ) -> CompanyBenchmarksResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

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
        return CompanyBenchmarksResponse(
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

    # ------------------------------------------------------------------
    # Route 6: GET /api/v1/companies/{ticker}/lineage
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/companies/{ticker}/lineage",
        response_model=RatioLineageResponse,
        tags=["companies"],
    )
    def get_company_lineage(
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        ratio_name: RatioName = Query("roe_total"),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> RatioLineageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

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

        _apply_cache_headers(response, snap_id)
        return RatioLineageResponse(
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
                LineageComponentItem(
                    role=comp.role,
                    metric_id=comp.metric_id,
                    period_type=comp.period_type,
                    fiscal_year=comp.fiscal_year,
                    fiscal_quarter=comp.fiscal_quarter,
                    period_end_date=comp.period_end_date,
                    value=_finite_scalar(comp.value),
                    filing_id=comp.filing_id,
                    taxonomy_version=comp.taxonomy_version,
                    derivation_method=comp.derivation_method,
                    source_provider=comp.source_provider,
                    connector_name=comp.connector_name,
                    published_at=comp.published_at,
                    published_at_basis=comp.published_at_basis,
                    effective_available_at=comp.effective_available_at,
                    canonical_content_sha256=comp.canonical_content_sha256,
                )
                for comp in card.numerator_components
            ],
            denominator_components=[
                LineageComponentItem(
                    role=comp.role,
                    metric_id=comp.metric_id,
                    period_type=comp.period_type,
                    fiscal_year=comp.fiscal_year,
                    fiscal_quarter=comp.fiscal_quarter,
                    period_end_date=comp.period_end_date,
                    value=_finite_scalar(comp.value),
                    filing_id=comp.filing_id,
                    taxonomy_version=comp.taxonomy_version,
                    derivation_method=comp.derivation_method,
                    source_provider=comp.source_provider,
                    connector_name=comp.connector_name,
                    published_at=comp.published_at,
                    published_at_basis=comp.published_at_basis,
                    effective_available_at=comp.effective_available_at,
                    canonical_content_sha256=comp.canonical_content_sha256,
                )
                for comp in card.denominator_components
            ],
            orphan_filing_ids=list(card.orphan_filing_ids),
        )

    # ------------------------------------------------------------------
    # Route 7: GET /api/v1/screener
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/screener",
        response_model=ScreenerResponse,
        tags=["screener"],
    )
    def screen_market(
        response: Response,
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(None),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        icb_code_level2: str | None = Query(None, pattern=_ICB_CODE_PATTERN),
        min_f_score: int | None = Query(None, ge=0, le=9),
        altman_zone: AltmanZone | None = Query(None),
        beneish_flag: BeneishFlag | None = Query(None),
        max_distress_prob: float | None = Query(None, ge=0.0, le=1.0),
        max_red_flags: int | None = Query(None, ge=0),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> ScreenerResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            pinned_svc = DashboardQueryService(pinned)
            page_df, total = pinned_svc.screen_market(
                period_type=period_type,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                max_red_flags=max_red_flags,
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
        return ScreenerResponse(
            snapshot_id=snap_id,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_end,
            total=total,
            limit=limit,
            offset=offset,
            items=[ScreenerItem(**row) for row in _frame_to_records(page_df)],
        )

    # ------------------------------------------------------------------
    # Route 8: POST /api/v1/stress-test
    # ------------------------------------------------------------------
    @api.post(
        "/api/v1/stress-test",
        response_model=StressTestResponse,
        tags=["stress-test"],
    )
    def run_stress_test(
        payload: StressScenarioRequest,
        response: Response,
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        sem_cfg: tuple[threading.Semaphore, float] = Depends(get_stress_semaphore),
    ) -> StressTestResponse:
        semaphore, sem_timeout = sem_cfg
        acquired = semaphore.acquire(timeout=sem_timeout)
        if not acquired:
            raise HTTPException(
                status_code=503,
                detail="Stress-test simulator is busy; please retry shortly.",
                headers={"Retry-After": "1"},
            )
        try:
            with service.repository.snapshot_session(connection=cursor) as pinned:
                meta = pinned.get_snapshot_metadata(connection=cursor)
                snap_id = _extract_snapshot_id(meta)
                pinned_svc = DashboardQueryService(pinned)
                inputs_df = pinned_svc.get_stress_test_snapshot_inputs(
                    statement_scope=payload.statement_scope,
                    period_type=payload.period_type,
                    period_end_date=payload.period_end_date,
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
                return StressTestResponse(
                    snapshot_id=snap_id,
                    scenario_name=scenario.name,
                    statement_scope=payload.statement_scope,
                    period_type=payload.period_type,
                    period_end_date=payload.period_end_date,
                    total_issuers_evaluated=0,
                    total_debt_sum=0.0,
                    baseline_debt_at_risk_pct=0.0,
                    stressed_debt_at_risk_pct=0.0,
                    newly_distressed_count=0,
                    sector_summary=[],
                    newly_distressed_issuers=[],
                    top_stressed_issuers=[],
                )

            resolved_end = inputs_df["period_end_date"][0]
            stressed_df = simulate_financial_stress(inputs_df, scenario)
            sector_df = summarize_stress_by_sector(stressed_df)
            market_df = summarize_stress_by_sector(
                stressed_df.with_columns(
                    pl.lit("MARKET").alias("icb_code_level2"),
                    pl.lit("MARKET").alias("icb_name_level2"),
                )
            )
            market_row = market_df.row(0, named=True)

            issuer_cols = [
                "issuer_id",
                "ticker",
                "company_name_vn",
                "icb_code_level2",
                "icb_name_level2",
                "total_debt",
                "interest_coverage",
                "stressed_interest_coverage",
                "stressed_interest_coverage_flag",
                "ebit_ttm",
                "stressed_ebit_ttm",
                "borrowing_cost_ttm",
                "stressed_borrowing_cost_ttm",
                "cfo_ttm",
                "stressed_cfo_ttm",
                "baseline_debt_at_risk",
                "stressed_debt_at_risk",
                "newly_distressed_under_shock",
            ]
            newly_df = (
                stressed_df.filter(
                    pl.col("newly_distressed_under_shock").eq(True).fill_null(False)
                )
                .select(issuer_cols)
                .sort(
                    ["total_debt", "ticker"],
                    descending=[True, False],
                    nulls_last=True,
                )
                .head(payload.top_n)
            )
            top_df = (
                stressed_df.select(issuer_cols)
                .sort(
                    ["stressed_debt_at_risk", "total_debt", "ticker"],
                    descending=[True, True, False],
                    nulls_last=True,
                )
                .head(payload.top_n)
            )

            return StressTestResponse(
                snapshot_id=snap_id,
                scenario_name=scenario.name,
                statement_scope=payload.statement_scope,
                period_type=payload.period_type,
                period_end_date=(
                    resolved_end
                    if isinstance(resolved_end, date)
                    else payload.period_end_date
                ),
                total_issuers_evaluated=int(market_row["issuer_count"]),
                total_debt_sum=float(market_row["total_debt_sum"]),
                baseline_debt_at_risk_pct=float(
                    market_row["baseline_debt_at_risk_pct"]
                ),
                stressed_debt_at_risk_pct=float(
                    market_row["stressed_debt_at_risk_pct"]
                ),
                newly_distressed_count=int(market_row["newly_distressed_count"]),
                sector_summary=[
                    StressSectorSummaryItem(**row)
                    for row in _frame_to_records(sector_df)
                ],
                newly_distressed_issuers=[
                    StressIssuerItem(**row) for row in _frame_to_records(newly_df)
                ],
                top_stressed_issuers=[
                    StressIssuerItem(**row) for row in _frame_to_records(top_df)
                ],
            )
        finally:
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
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> QualitySummaryPageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            page_df, total = DashboardQueryService(pinned).get_quality_summary_page(
                limit=limit, offset=offset, connection=cursor
            )

        _apply_cache_headers(response, snap_id)
        return QualitySummaryPageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=[QualitySummaryItem(**row) for row in _frame_to_records(page_df)],
        )

    # ------------------------------------------------------------------
    # Route 10: GET /api/v1/quality/quarantine
    # ------------------------------------------------------------------
    @api.get(
        "/api/v1/quality/quarantine",
        response_model=AuditQuarantinePageResponse,
        tags=["quality"],
    )
    def get_quality_quarantine(
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> AuditQuarantinePageResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            not_modified = _conditional_not_modified(if_none_match, snap_id)
            if not_modified is not None:
                return not_modified

            page_df, total = DashboardQueryService(pinned).get_audit_quarantine_page(
                limit=limit, offset=offset, connection=cursor
            )

        _apply_cache_headers(response, snap_id)
        return AuditQuarantinePageResponse(
            snapshot_id=snap_id,
            total=total,
            limit=limit,
            offset=offset,
            items=[AuditQuarantineItem(**row) for row in _frame_to_records(page_df)],
        )

    return api


def run_server(
    *,
    host: str = DEFAULT_API_HOST,
    port: int = DEFAULT_API_PORT,
    data_root: Path | None = None,
) -> None:
    """Run the Layer 5 FastAPI server with uvicorn bound to ``127.0.0.1`` by default."""
    import uvicorn

    application = create_app(data_root=data_root)
    uvicorn.run(application, host=host, port=port)


app = create_app()
