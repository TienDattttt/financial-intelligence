"""Open Research API endpoints (``/api/v3/open/*``) gated by X-API-Key and license flag."""

from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from finintel.api_gateway.keys import ApiGatewayStoreProtocol
from finintel.api_gateway.middleware import (
    DERIVED_RISK_LICENSE_SCOPE,
    RAW_REDISTRIBUTION_LICENSE_SCOPE,
    ApiKeyContext,
    apply_api_headers,
    build_api_key_dependency,
    build_snapshot_etag,
    matches_if_none_match,
    record_open_api_hit,
)
from finintel.api_gateway.rate_limiter import SlidingWindowRateLimiter
from finintel.domain.compliance import COMPLIANCE_DISCLAIMER_VI

if TYPE_CHECKING:
    from fastapi import APIRouter

_TICKER_PATTERN = r"^[A-Za-z0-9]{1,10}$"


def _extract_snap_id(meta: dict[str, Any] | Any) -> str | None:
    if isinstance(meta, dict):
        val = meta.get("snapshot_id")
        return str(val) if isinstance(val, str) and val else None
    return None


def create_open_research_router(
    *,
    get_store: Callable[[], ApiGatewayStoreProtocol | None],
    get_limiter: Callable[[], SlidingWindowRateLimiter],
) -> "APIRouter":
    """Build ``/api/v3/open/*`` router enforcing X-API-Key, rate limit, and raw data gate."""
    import polars as pl
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
    from fastapi import Path as PathParam

    from finintel.analytics.stress_test import (
        StressScenario,
        simulate_financial_stress,
        summarize_stress_by_sector,
    )
    from finintel.api_gateway._http_models import (
        OpenDerivedRiskResponse,
        OpenRawStatementsResponse,
        OpenScreenerResponse,
        OpenStressTestRequest,
        OpenStressTestResponse,
    )
    from finintel.serving.dependencies import get_db_cursor, get_query_service
    from finintel.serving.queries import (
        DashboardQueryService,
        Exchange,
        PeriodType,
        PeriodView,
        RiskTier,
        StatementScope,
    )

    router = APIRouter(prefix="/api/v3/open", tags=["v3-open-research-api"])

    require_derived_key = build_api_key_dependency(
        get_store=get_store,
        get_limiter=get_limiter,
        required_scope=DERIVED_RISK_LICENSE_SCOPE,
        requires_raw_redistribution=False,
    )
    require_raw_key = build_api_key_dependency(
        get_store=get_store,
        get_limiter=get_limiter,
        required_scope=RAW_REDISTRIBUTION_LICENSE_SCOPE,
        requires_raw_redistribution=True,
    )

    @router.get(
        "/companies/{ticker}/risk",
        response_model=OpenDerivedRiskResponse,
    )
    def get_open_company_risk(
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_view: PeriodView = Query("TTM"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        ctx: ApiKeyContext = Depends(require_derived_key),
        cursor: Any = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpenDerivedRiskResponse | Response:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snap_id(meta)
            etag = build_snapshot_etag(snap_id)
            if matches_if_none_match(if_none_match, etag):
                record_open_api_hit(
                    get_store(),
                    ctx,
                    endpoint="/api/v3/open/companies/{ticker}/risk",
                    status_code=304,
                    cache_hit=True,
                )
                return Response(
                    status_code=304,
                    headers=ctx.headers(snapshot_id=snap_id, etag=etag),
                )
            try:
                profile = DashboardQueryService(pinned).get_company_profile(
                    clean_ticker,
                    statement_scope=statement_scope,
                    period_view=period_view,
                    connection=cursor,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        apply_api_headers(response, ctx, snapshot_id=snap_id, etag=etag)
        record_open_api_hit(
            get_store(),
            ctx,
            endpoint="/api/v3/open/companies/{ticker}/risk",
            status_code=200,
        )
        return OpenDerivedRiskResponse(
            snapshot_id=snap_id,
            license_scope=DERIVED_RISK_LICENSE_SCOPE,
            ticker=profile.ticker,
            company_name_vn=profile.company_name_vn or profile.ticker,
            accounting_regime=profile.accounting_regime,
            statement_scope=profile.statement_scope,
            period_view=profile.period_view,
            latest_taxonomy_version=profile.latest_taxonomy_version,
            latest_quality_status=profile.latest_quality_status,
            ml_scores=profile.ml_scores.to_dicts(),
            red_flags=profile.red_flags.to_dicts(),
            ratios=profile.ratios_table.to_dicts(),
            disclaimer_vi=COMPLIANCE_DISCLAIMER_VI,
        )

    @router.get("/screener", response_model=OpenScreenerResponse)
    def get_open_screener(
        response: Response,
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(default=None),
        exchange: Exchange | None = Query(default=None),
        risk_tier: RiskTier | None = Query(default=None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        ctx: ApiKeyContext = Depends(require_derived_key),
        cursor: Any = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpenScreenerResponse | Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snap_id(meta)
            etag = build_snapshot_etag(snap_id)
            if matches_if_none_match(if_none_match, etag):
                record_open_api_hit(
                    get_store(),
                    ctx,
                    endpoint="/api/v3/open/screener",
                    status_code=304,
                    cache_hit=True,
                )
                return Response(
                    status_code=304,
                    headers=ctx.headers(snapshot_id=snap_id, etag=etag),
                )
            page_df, total = DashboardQueryService(pinned).screen_market(
                period_type=period_type,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                exchange=exchange,
                risk_tier=risk_tier,
                limit=limit,
                offset=offset,
                connection=cursor,
            )
            resolved_end = period_end_date
            if resolved_end is None and not page_df.is_empty():
                first = page_df["period_end_date"][0]
                if isinstance(first, date):
                    resolved_end = first

        apply_api_headers(response, ctx, snapshot_id=snap_id, etag=etag)
        record_open_api_hit(
            get_store(),
            ctx,
            endpoint="/api/v3/open/screener",
            status_code=200,
        )
        return OpenScreenerResponse(
            snapshot_id=snap_id,
            license_scope=DERIVED_RISK_LICENSE_SCOPE,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_end,
            total=total,
            limit=limit,
            offset=offset,
            items=page_df.to_dicts(),
            disclaimer_vi=COMPLIANCE_DISCLAIMER_VI,
        )

    @router.post("/stress-test", response_model=OpenStressTestResponse)
    def post_open_stress_test(
        body: OpenStressTestRequest,
        response: Response,
        ctx: ApiKeyContext = Depends(require_derived_key),
        cursor: Any = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpenStressTestResponse:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snap_id(meta)
            etag = build_snapshot_etag(snap_id)
            inputs_df = DashboardQueryService(pinned).get_stress_test_snapshot_inputs(
                statement_scope=body.statement_scope,
                period_type=body.period_type,
                period_end_date=body.period_end_date,
                tickers=body.tickers,
                connection=cursor,
            )

        apply_api_headers(response, ctx, snapshot_id=snap_id, etag=etag)
        scenario = StressScenario(
            name=body.scenario_name,
            interest_rate_shock_bps=body.interest_rate_shock_bps,
            revenue_shock_pct=body.revenue_shock_pct,
            gross_margin_shock_pp=body.gross_margin_shock_pp,
            dso_shock_days=body.dso_shock_days,
            dio_shock_days=body.dio_shock_days,
        )
        if inputs_df.is_empty():
            record_open_api_hit(
                get_store(), ctx, endpoint="/api/v3/open/stress-test", status_code=200
            )
            return OpenStressTestResponse(
                snapshot_id=snap_id,
                license_scope=DERIVED_RISK_LICENSE_SCOPE,
                scenario_name=scenario.name,
                statement_scope=body.statement_scope,
                period_type=body.period_type,
                period_end_date=body.period_end_date,
                issuers_stressed=0,
                newly_distressed_count=0,
                sectors=[],
                issuers=[],
                disclaimer_vi=COMPLIANCE_DISCLAIMER_VI,
            )

        stressed_df = simulate_financial_stress(inputs_df, scenario)
        sector_df = summarize_stress_by_sector(stressed_df)
        newly_distressed = int(
            stressed_df.filter(pl.col("newly_distressed_flag")).height
            if "newly_distressed_flag" in stressed_df.columns
            else 0
        )
        resolved_end = inputs_df["period_end_date"][0]
        record_open_api_hit(
            get_store(), ctx, endpoint="/api/v3/open/stress-test", status_code=200
        )
        return OpenStressTestResponse(
            snapshot_id=snap_id,
            license_scope=DERIVED_RISK_LICENSE_SCOPE,
            scenario_name=scenario.name,
            statement_scope=body.statement_scope,
            period_type=body.period_type,
            period_end_date=resolved_end if isinstance(resolved_end, date) else None,
            issuers_stressed=stressed_df.height,
            newly_distressed_count=newly_distressed,
            sectors=sector_df.to_dicts(),
            issuers=stressed_df.head(100).to_dicts(),
            disclaimer_vi=COMPLIANCE_DISCLAIMER_VI,
        )

    @router.get(
        "/companies/{ticker}/statements",
        response_model=OpenRawStatementsResponse,
    )
    def get_open_raw_statements(
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_type: str = Query("INCOME_STATEMENT"),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        periods_limit: int = Query(8, ge=1, le=20),
        ctx: ApiKeyContext = Depends(require_raw_key),
        cursor: Any = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpenRawStatementsResponse:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snap_id(meta)
            etag = build_snapshot_etag(snap_id)
            try:
                issuer_id, periods, rows = DashboardQueryService(
                    pinned
                ).get_company_statements(
                    clean_ticker,
                    statement_type=statement_type,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    periods_limit=periods_limit,
                    connection=cursor,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        apply_api_headers(
            response,
            ctx,
            snapshot_id=snap_id,
            etag=etag,
            license_scope=RAW_REDISTRIBUTION_LICENSE_SCOPE,
        )
        record_open_api_hit(
            get_store(),
            ctx,
            endpoint="/api/v3/open/companies/{ticker}/statements",
            status_code=200,
        )
        return OpenRawStatementsResponse(
            snapshot_id=snap_id,
            license_scope=RAW_REDISTRIBUTION_LICENSE_SCOPE,
            ticker=clean_ticker,
            issuer_id=issuer_id,
            statement_type=statement_type,
            statement_scope=statement_scope,
            period_type=period_type,
            periods=periods,
            rows=rows,
        )

    return router


__all__ = ["create_open_research_router"]
