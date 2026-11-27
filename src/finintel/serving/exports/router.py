"""FastAPI router for V3 multi-sheet Excel (.xlsx) exports with snapshot lineage headers.

Verified Sources:
- spec/v3/SPEC-v3-export-and-tearsheet.md (§4, §5.1)
- FastAPI Official Documentation (``APIRouter``, ``Response``, ``Path``, ``Query``):
  https://fastapi.tiangolo.com/tutorial/bigger-applications/
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import Path as PathParam

from finintel.serving._api_helpers import _extract_snapshot_id
from finintel.serving.dependencies import get_db_cursor, get_query_service
from finintel.serving.dictionary import StatementType
from finintel.serving.exports.excel_builder import (
    MAX_SCREENER_EXPORT_ROWS,
    CompanyExportPayload,
    CompanyStatementExportSection,
    ScreenerExportPayload,
    build_company_workbook_bytes,
    build_screener_workbook_bytes,
    build_short_snapshot_id,
    sanitize_filename_token,
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
from finintel.serving.queries.company import RATIO_LINEAGE_CATALOG
from finintel.serving.routers.screener_v3 import ScreenerExtendedQueryParams
from finintel.serving.schemas import ScreenerSortField, SortDirection

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXPORT_CACHE_CONTROL = "private, max-age=300"
_TICKER_PATTERN = r"^[A-Za-z0-9]{1,10}$"
_ICB_CODE_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_STATEMENT_ORDER: tuple[StatementType, ...] = (
    "BALANCE_SHEET",
    "INCOME_STATEMENT",
    "CASH_FLOW",
)


def _read_issuer_export_meta(
    pinned: Any, ticker: str, cursor: duckdb.DuckDBPyConnection
) -> tuple[str | None, str | None, str | None]:
    try:
        cols = pinned.get_table_columns("issuers.parquet", connection=cursor)
    except FileNotFoundError:
        return None, None, None
    if not cols:
        return None, None, None
    wanted = [
        c for c in ("exchange", "icb_name_level2", "icb_name_level3") if c in cols
    ]
    if not wanted:
        return None, None, None
    frame = pinned.read_table(
        "issuers.parquet",
        columns=wanted,
        filters={"ticker": ticker},
        connection=cursor,
    )
    if frame.is_empty():
        return None, None, None
    row = frame.row(0, named=True)
    ex = str(row["exchange"]) if row.get("exchange") is not None else None
    icb2 = (
        str(row["icb_name_level2"]) if row.get("icb_name_level2") is not None else None
    )
    icb3 = (
        str(row["icb_name_level3"]) if row.get("icb_name_level3") is not None else None
    )
    return ex, icb2, icb3


def create_exports_router() -> APIRouter:
    """Build the ``/api/v3/exports/*`` FastAPI router."""
    router = APIRouter(prefix="/api/v3/exports", tags=["v3-exports"])

    @router.get(
        "/companies/{ticker}.xlsx",
        responses={
            200: {
                "content": {XLSX_MEDIA_TYPE: {}},
                "description": "4-sheet VAS & quantitative risk workbook (.xlsx) for one company.",
            },
            404: {"description": "Ticker not found in active snapshot."},
        },
    )
    def export_company_xlsx(
        request: Request,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        periods_limit: int = Query(20, ge=1, le=20),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> Response:
        clean_ticker = ticker.strip().upper()
        period_view: PeriodView = "Annual" if period_type == "ANNUAL" else "TTM"

        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            published_at = (
                str(meta["published_at"])
                if isinstance(meta.get("published_at"), str)
                else None
            )
            if snap_id is not None:
                request.state.snapshot_id = snap_id

            pinned_svc = DashboardQueryService(pinned)
            try:
                profile = pinned_svc.get_company_profile(
                    clean_ticker,
                    statement_scope=statement_scope,
                    period_view=period_view,
                    connection=cursor,
                )
            except (ValueError, LookupError) as exc:
                if isinstance(exc, LookupError) or "not found" in str(exc).lower():
                    raise HTTPException(
                        status_code=404,
                        detail=f"Ticker {clean_ticker} was not found in active snapshot.",
                    ) from exc
                raise

            exchange, icb2, icb3 = _read_issuer_export_meta(
                pinned, clean_ticker, cursor
            )

            statements: list[CompanyStatementExportSection] = []
            for stype in _STATEMENT_ORDER:
                _, periods_out, rows_out = pinned_svc.get_company_statements(
                    clean_ticker,
                    statement_type=stype,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    periods_limit=periods_limit,
                    connection=cursor,
                )
                statements.append(
                    CompanyStatementExportSection(
                        statement_type=stype,
                        periods=periods_out,
                        rows=rows_out,
                    )
                )

            benchmarks_summary: list[dict[str, object]] = []
            try:
                _, _, benchmarks_summary = pinned_svc.get_company_benchmarks_summary(
                    clean_ticker,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    metrics=list(RATIO_LINEAGE_CATALOG.keys()),
                    connection=cursor,
                )
            except (ValueError, LookupError):
                benchmarks_summary = []

            filings: list[dict[str, object]] = []
            try:
                _, filings = pinned_svc.get_company_filings(
                    clean_ticker,
                    statement_scope=statement_scope,
                    limit=100,
                    connection=cursor,
                )
            except (ValueError, LookupError):
                filings = []

            market_overlay: dict[str, Any] | None = None
            try:
                market_overlay = pinned_svc.get_company_market_overlay(
                    clean_ticker,
                    statement_scope=statement_scope,
                    port_data_root=service.repository._source,
                    connection=cursor,
                )
            except (ValueError, LookupError, FileNotFoundError):
                market_overlay = None

        xlsx_bytes = build_company_workbook_bytes(
            CompanyExportPayload(
                snapshot_id=snap_id,
                published_at=published_at,
                profile=profile,
                exchange=exchange,
                icb_name_level2=icb2,
                icb_name_level3=icb3,
                statements=tuple(statements),
                benchmarks_summary=benchmarks_summary,
                filings=filings,
                market_overlay=market_overlay,
            )
        )
        safe_ticker = sanitize_filename_token(clean_ticker, fallback="company")
        short_snap = build_short_snapshot_id(snap_id)
        filename = f"finintel-{safe_ticker}-{short_snap}.xlsx"
        headers: dict[str, str] = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": _EXPORT_CACHE_CONTROL,
        }
        if snap_id is not None:
            headers["X-Snapshot-ID"] = snap_id
        return Response(content=xlsx_bytes, media_type=XLSX_MEDIA_TYPE, headers=headers)

    @router.get(
        "/screener.xlsx",
        responses={
            200: {
                "content": {XLSX_MEDIA_TYPE: {}},
                "description": "Filtered market screener workbook (.xlsx), capped at 2,000 rows.",
            }
        },
    )
    def export_screener_xlsx(
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
        ext: ScreenerExtendedQueryParams = Depends(),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> Response:
        with service.repository.snapshot_session(connection=cursor) as pinned:
            meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = _extract_snapshot_id(meta)
            published_at = (
                str(meta["published_at"])
                if isinstance(meta.get("published_at"), str)
                else None
            )
            if snap_id is not None:
                request.state.snapshot_id = snap_id

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
                extra_filters=ext.to_domain(),
                connection=cursor,
            )

        total_rows = full_df.height
        capped_df = full_df.head(MAX_SCREENER_EXPORT_ROWS)
        resolved_date = period_end_date
        if resolved_date is None and not capped_df.is_empty():
            first_date = capped_df["period_end_date"][0]
            if isinstance(first_date, date):
                resolved_date = first_date

        xlsx_bytes = build_screener_workbook_bytes(
            ScreenerExportPayload(
                snapshot_id=snap_id,
                published_at=published_at,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=resolved_date,
                frame=capped_df,
                truncated_from_total=total_rows,
            )
        )
        short_snap = build_short_snapshot_id(snap_id)
        filename = f"finintel-screener-{short_snap}.xlsx"
        headers: dict[str, str] = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": _EXPORT_CACHE_CONTROL,
            "X-Export-Row-Count": str(capped_df.height),
            "X-Export-Truncated": (
                "true" if total_rows > MAX_SCREENER_EXPORT_ROWS else "false"
            ),
        }
        if snap_id is not None:
            headers["X-Snapshot-ID"] = snap_id
        return Response(content=xlsx_bytes, media_type=XLSX_MEDIA_TYPE, headers=headers)

    return router
