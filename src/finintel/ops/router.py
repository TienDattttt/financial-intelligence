"""FastAPI routers for V3 Analyst Depth (Suggested Peers & Anomaly Drivers) and INTERNAL_OPS QA.

Verified Sources:
    spec/v3/SPEC-v3-analyst-depth-and-ops-qa.md (§1, §4, §5, §6)
    FastAPI APIRouter & Dependencies: https://fastapi.tiangolo.com/tutorial/bigger-applications/
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

import duckdb
import polars as pl
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam

from finintel.analytics.qoq_and_peers import (
    extract_top_anomaly_drivers,
    suggest_similar_peers,
)
from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_role,
    verify_csrf_request,
)
from finintel.ops._pg_store import PostgresOpsQaStore
from finintel.ops.lifecycle import IssuerLifecycleEventRecord, OpsLifecycleService
from finintel.ops.qa_service import (
    OpsQaError,
    OpsQaService,
    OpsQaStoreProtocol,
    OpsTicketRecord,
)
from finintel.ops.schemas import (
    AnomalyDriverItem,
    AnomalyDriversResponse,
    IssuerLifecycleEventItem,
    IssuerLifecycleItem,
    OpsAssignTicketRequest,
    OpsDecisionRequest,
    OpsIssuerLifecycleResponse,
    OpsIssuerSyncRequest,
    OpsIssuerSyncResponse,
    OpsQaQueueResponse,
    OpsQuarantineTicketItem,
    OpsRevisionItem,
    OpsSubmitRevisionRequest,
    OpsTicketMutationResponse,
    SuggestedPeerItem,
    SuggestedPeersResponse,
)
from finintel.serving.dependencies import get_db_cursor, get_query_service
from finintel.serving.queries import DashboardQueryService, PeriodType, StatementScope
from finintel.serving.queries.company import RATIO_LINEAGE_CATALOG
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    ML_SCORES_TABLE,
    PIT_VALUATION_MART_TABLE,
    RATIOS_TABLE,
)

_TICKER_PATTERN = r"^[A-Za-z0-9]{1,10}$"


def _read_optional_table(
    pinned: Any,
    table_name: str,
    *,
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    try:
        avail = pinned.get_table_columns(table_name, connection=connection)
    except FileNotFoundError:
        return pl.DataFrame()
    if not avail:
        return pl.DataFrame()
    selected = [c for c in columns if c in avail] if columns else list(avail)
    if not selected:
        return pl.DataFrame()
    try:
        return cast(
            pl.DataFrame,
            pinned.read_table(
                table_name,
                columns=selected,
                filters=filters,
                connection=connection,
            ),
        )
    except FileNotFoundError:
        return pl.DataFrame()


def _latest_by_issuer(
    frame: pl.DataFrame,
    *,
    date_col: str,
    target_date: date | None = None,
) -> pl.DataFrame:
    if (
        frame.is_empty()
        or date_col not in frame.columns
        or "issuer_id" not in frame.columns
    ):
        return frame
    scoped = (
        frame.filter(pl.col(date_col) == target_date)
        if target_date is not None
        else frame
    )
    if scoped.is_empty():
        scoped = frame
    return scoped.sort(date_col).unique(subset=["issuer_id"], keep="last")


def _to_ticket_item(rec: OpsTicketRecord) -> OpsQuarantineTicketItem:
    return OpsQuarantineTicketItem(
        ticket_id=rec.ticket_id,
        issuer_id=rec.issuer_id,
        ticker=rec.ticker,
        filing_id=rec.filing_id,
        statement_scope=rec.statement_scope,
        period_type=rec.period_type,
        period_end_date=rec.period_end_date,
        taxonomy_version=rec.taxonomy_version,
        rule_code=rec.rule_code,
        severity=rec.severity,
        details_json=rec.details_json,
        status=rec.status,
        decision=rec.decision,
        assigned_to_user_id=rec.assigned_to_user_id,
        root_cause_note=rec.root_cause_note,
        latest_revision_no=rec.latest_revision_no,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        resolved_at=rec.resolved_at,
        revisions=[
            OpsRevisionItem(
                submission_id=rev.submission_id,
                ticket_id=rev.ticket_id,
                revision_no=rev.revision_no,
                source_url=rev.source_url,
                raw_file_sha256=rev.raw_file_sha256,
                reason=rev.reason,
                reingest_status=rev.reingest_status,
                dagster_run_id=rev.dagster_run_id,
                submitted_by_user_id=rev.submitted_by_user_id,
                submitted_at=rev.submitted_at,
            )
            for rev in rec.revisions
        ],
    )


def _to_event_item(ev: IssuerLifecycleEventRecord) -> IssuerLifecycleEventItem:
    return IssuerLifecycleEventItem(
        event_id=ev.event_id,
        issuer_id=ev.issuer_id,
        ticker=ev.ticker,
        exchange=cast(Literal["HOSE", "HNX", "UPCOM", "DELISTED"], ev.exchange),
        event_type=cast(
            Literal[
                "IPO_LISTING",
                "EXCHANGE_TRANSFER",
                "DELISTING",
                "FY_END_CHANGE",
                "METADATA_SYNC",
            ],
            ev.event_type,
        ),
        effective_date=ev.effective_date,
        fy_end_month=ev.fy_end_month,
        previous_exchange=ev.previous_exchange,
        source_url=ev.source_url,
        notes=ev.notes,
        status=ev.status,
        created_at=ev.created_at,
    )


def create_analyst_depth_router() -> APIRouter:
    """Create read-only V3 analyst depth endpoints (peer auto-suggest & anomaly drivers)."""
    router = APIRouter(prefix="/api/v3/companies", tags=["v3-analyst-depth"])

    @router.get("/{ticker}/suggested-peers", response_model=SuggestedPeersResponse)
    def get_suggested_peers(
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(default=None),
        limit: int = Query(4, ge=1, le=10),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> SuggestedPeersResponse:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            snap_meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = (
                str(snap_meta["snapshot_id"])
                if snap_meta.get("snapshot_id") is not None
                else None
            )
            issuers = _read_optional_table(
                pinned,
                "issuers.parquet",
                columns=[
                    "issuer_id",
                    "ticker",
                    "exchange",
                    "company_name_vn",
                    "accounting_regime",
                    "icb_code_level2",
                    "icb_name_level2",
                    "icb_code_level3",
                    "icb_name_level3",
                    "delisted_date",
                ],
                connection=cursor,
            )
            if issuers.is_empty():
                raise HTTPException(
                    status_code=404, detail=f"Không tìm thấy mã {clean_ticker}."
                )

            mart = _read_optional_table(
                pinned,
                PIT_VALUATION_MART_TABLE,
                columns=[
                    "issuer_id",
                    "period_end_date",
                    "effective_trade_date",
                    "market_cap_vnd",
                ],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=cursor,
            )
            mart_latest = _latest_by_issuer(
                mart,
                date_col="effective_trade_date"
                if "effective_trade_date" in mart.columns
                else "period_end_date",
                target_date=None,
            )

            ratios = _read_optional_table(
                pinned,
                RATIOS_TABLE,
                columns=["issuer_id", "period_end_date", "BS_TOTAL_ASSETS"],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=cursor,
            )
            if "BS_TOTAL_ASSETS" not in ratios.columns:
                assets_facts = _read_optional_table(
                    pinned,
                    "financial_facts.parquet",
                    columns=["issuer_id", "period_end_date", "value"],
                    filters={
                        "metric_id": "BS_TOTAL_ASSETS",
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                    },
                    connection=cursor,
                )
                if not assets_facts.is_empty():
                    ratios = _latest_by_issuer(
                        assets_facts,
                        date_col="period_end_date",
                        target_date=period_end_date,
                    ).rename({"value": "BS_TOTAL_ASSETS"})
            else:
                ratios = _latest_by_issuer(
                    ratios, date_col="period_end_date", target_date=period_end_date
                )

            ml = _read_optional_table(
                pinned,
                ML_SCORES_TABLE,
                columns=[
                    "issuer_id",
                    "period_end_date",
                    "risk_tier",
                    "calibrated_distress_prob",
                ],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "model_role": "PRIMARY",
                },
                connection=cursor,
            )
            ml_latest = _latest_by_issuer(
                ml, date_col="period_end_date", target_date=period_end_date
            )

        try:
            peer_basis, peers = suggest_similar_peers(
                clean_ticker,
                issuers,
                valuation_mart=mart_latest,
                ratios=ratios,
                ml_scores=ml_latest,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

        resolved_date = period_end_date
        if (
            resolved_date is None
            and not ml_latest.is_empty()
            and "period_end_date" in ml_latest.columns
        ):
            raw_d = ml_latest.get_column("period_end_date").max()
            if isinstance(raw_d, date):
                resolved_date = raw_d

        return SuggestedPeersResponse(
            snapshot_id=snap_id,
            ticker=clean_ticker,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            peer_basis=peer_basis,
            peers=[
                SuggestedPeerItem(
                    issuer_id=p.issuer_id,
                    ticker=p.ticker,
                    company_name_vn=p.company_name_vn,
                    exchange=p.exchange,
                    icb_code_level2=p.icb_code_level2,
                    icb_name_level2=p.icb_name_level2,
                    icb_code_level3=p.icb_code_level3,
                    icb_name_level3=p.icb_name_level3,
                    peer_basis=p.peer_basis,
                    size_metric=p.size_metric,
                    size_ratio=p.size_ratio,
                    market_cap_vnd=p.market_cap_vnd,
                    bs_total_assets=p.bs_total_assets,
                    risk_tier=p.risk_tier,
                    calibrated_distress_prob=p.calibrated_distress_prob,
                )
                for p in peers
            ],
        )

    @router.get("/{ticker}/anomaly-drivers", response_model=AnomalyDriversResponse)
    def get_anomaly_drivers(
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: StatementScope = Query("CONSOLIDATED"),
        period_type: PeriodType = Query("QUARTER"),
        period_end_date: date | None = Query(default=None),
        top_k: int = Query(3, ge=1, le=10),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> AnomalyDriversResponse:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            snap_meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = (
                str(snap_meta["snapshot_id"])
                if snap_meta.get("snapshot_id") is not None
                else None
            )
            issuers = _read_optional_table(
                pinned,
                "issuers.parquet",
                columns=["issuer_id", "ticker"],
                filters={"ticker": clean_ticker},
                connection=cursor,
            )
            if issuers.is_empty():
                raise HTTPException(
                    status_code=404, detail=f"Không tìm thấy mã {clean_ticker}."
                )
            issuer_id = str(issuers.row(0, named=True)["issuer_id"])

            ml = _read_optional_table(
                pinned,
                ML_SCORES_TABLE,
                columns=[
                    "issuer_id",
                    "period_end_date",
                    "anomaly_percentile_rank",
                    "statistical_outlier_flag",
                ],
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "model_role": "PRIMARY",
                },
                connection=cursor,
            )
            bench = _read_optional_table(
                pinned,
                BENCHMARKS_TABLE,
                columns=[
                    "issuer_id",
                    "period_end_date",
                    "metric_or_ratio_name",
                    "value",
                    "peer_median",
                    "robust_z_score",
                    "peer_group_level",
                    "peer_count",
                ],
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=cursor,
            )

        ml_latest = _latest_by_issuer(
            ml, date_col="period_end_date", target_date=period_end_date
        )
        resolved_date = period_end_date
        anom_pct: float | None = None
        outlier_flag = False
        if not ml_latest.is_empty():
            mrow = ml_latest.row(0, named=True)
            if isinstance(mrow.get("period_end_date"), date):
                resolved_date = mrow["period_end_date"]
            raw_pct = mrow.get("anomaly_percentile_rank")
            if isinstance(raw_pct, (int, float)) and not isinstance(raw_pct, bool):
                anom_pct = float(raw_pct)
            outlier_flag = bool(mrow.get("statistical_outlier_flag"))

        if (
            resolved_date is not None
            and not bench.is_empty()
            and "period_end_date" in bench.columns
        ):
            scoped_bench = bench.filter(pl.col("period_end_date") == resolved_date)
            if not scoped_bench.is_empty():
                bench = scoped_bench

        is_outlier, drivers = extract_top_anomaly_drivers(
            bench,
            anomaly_percentile_rank=anom_pct,
            statistical_outlier_flag=outlier_flag,
            top_k=top_k,
        )
        return AnomalyDriversResponse(
            snapshot_id=snap_id,
            ticker=clean_ticker,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            anomaly_percentile_rank=anom_pct,
            statistical_outlier_flag=outlier_flag,
            is_multivariate_outlier=is_outlier,
            drivers=[
                AnomalyDriverItem(
                    metric_name=d.metric_name,
                    display_name_vn=(
                        RATIO_LINEAGE_CATALOG[d.metric_name].display_name_vn
                        if d.metric_name in RATIO_LINEAGE_CATALOG
                        else d.metric_name
                    ),
                    company_value=d.company_value,
                    peer_median=d.peer_median,
                    sector_z=d.sector_z,
                    abs_sector_z=d.abs_sector_z,
                    direction_vs_sector=cast(
                        Literal["ABOVE_MEDIAN", "BELOW_MEDIAN", "AT_MEDIAN"],
                        d.direction_vs_sector,
                    ),
                    peer_group_level=d.peer_group_level,
                    peer_count=d.peer_count,
                )
                for d in drivers
            ],
        )

    return router


def create_ops_router(
    *,
    get_qa_store: Any,
    get_audit_store: Any,
) -> APIRouter:
    """Create ``/api/v3/ops/*`` router strictly gated to ``INTERNAL_OPS``."""
    router = APIRouter(prefix="/api/v3/ops", tags=["v3-ops-qa"])
    require_ops = require_role(UserRole.INTERNAL_OPS)

    def _qa_service() -> OpsQaService:
        store = cast(OpsQaStoreProtocol | None, get_qa_store())
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ Ops QA chưa được cấu hình cơ sở dữ liệu.",
            )
        audit = cast(AppStoreProtocol | None, get_audit_store())
        return OpsQaService(store, audit_store=audit)

    def _lifecycle_service() -> OpsLifecycleService:
        store = cast(OpsQaStoreProtocol | None, get_qa_store())
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ vòng đời doanh nghiệp chưa được cấu hình cơ sở dữ liệu.",
            )
        audit = cast(AppStoreProtocol | None, get_audit_store())
        return OpsLifecycleService(store, audit_store=audit)

    @router.get("/qa-queue", response_model=OpsQaQueueResponse)
    def get_qa_queue(
        status: Literal["OPEN", "IN_REVIEW", "RESOLVED"] | None = Query(default=None),
        _user: AuthenticatedUser = Depends(require_ops),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpsQaQueueResponse:
        qa_svc = _qa_service()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            snap_meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = (
                str(snap_meta["snapshot_id"])
                if snap_meta.get("snapshot_id") is not None
                else None
            )
            quarantine = _read_optional_table(
                pinned, AUDIT_QUARANTINE_TABLE, connection=cursor
            )
            issuers = _read_optional_table(
                pinned,
                "issuers.parquet",
                columns=["issuer_id", "ticker"],
                connection=cursor,
            )

        ticker_by_id: dict[str, str] = {}
        if not issuers.is_empty():
            for irow in issuers.to_dicts():
                ticker_by_id[str(irow["issuer_id"])] = str(irow["ticker"])

        seed_rows: list[dict[str, object]] = []
        if not quarantine.is_empty():
            for qrow in quarantine.to_dicts():
                iid = str(qrow.get("issuer_id") or "")
                enriched_row: dict[str, object] = dict(qrow)
                enriched_row.setdefault("ticker", ticker_by_id.get(iid, iid))
                if not enriched_row["ticker"]:
                    enriched_row["ticker"] = iid
                else:
                    enriched_row["ticker"] = ticker_by_id.get(
                        iid, str(enriched_row["ticker"])
                    )
                seed_rows.append(enriched_row)

        items = qa_svc.list_queue(
            snapshot_quarantine_rows=seed_rows,
            status=status,
        )
        return OpsQaQueueResponse(
            snapshot_id=snap_id,
            total=len(items),
            items=[_to_ticket_item(t) for t in items],
        )

    @router.post(
        "/qa-queue/{ticket_id}/assign", response_model=OpsTicketMutationResponse
    )
    def assign_qa_ticket(
        request: Request,
        body: OpsAssignTicketRequest,
        ticket_id: UUID = PathParam(...),
        actor: AuthenticatedUser = Depends(require_ops),
        _: None = Depends(verify_csrf_request),
    ) -> OpsTicketMutationResponse:
        qa_svc = _qa_service()
        try:
            updated = qa_svc.assign_ticket(
                ticket_id=ticket_id,
                actor=actor,
                assigned_to_user_id=body.assigned_to_user_id,
                root_cause_note=body.root_cause_note,
                client_ip=extract_client_ip(request),
            )
        except OpsQaError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return OpsTicketMutationResponse(
            status="IN_REVIEW", ticket=_to_ticket_item(updated)
        )

    @router.post(
        "/qa-queue/{ticket_id}/submit-revision",
        response_model=OpsTicketMutationResponse,
    )
    def submit_qa_revision(
        request: Request,
        body: OpsSubmitRevisionRequest,
        ticket_id: UUID = PathParam(...),
        actor: AuthenticatedUser = Depends(require_ops),
        _: None = Depends(verify_csrf_request),
    ) -> OpsTicketMutationResponse:
        qa_svc = _qa_service()
        try:
            updated = qa_svc.submit_revision(
                ticket_id=ticket_id,
                actor=actor,
                source_url=body.source_url,
                raw_file_sha256=body.raw_file_sha256,
                reason=body.reason,
                payload_summary=body.payload_summary,
                client_ip=extract_client_ip(request),
            )
        except OpsQaError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return OpsTicketMutationResponse(
            status="REVISION_APPENDED", ticket=_to_ticket_item(updated)
        )

    @router.post(
        "/qa-queue/{ticket_id}/decision", response_model=OpsTicketMutationResponse
    )
    def decide_qa_ticket(
        request: Request,
        body: OpsDecisionRequest,
        ticket_id: UUID = PathParam(...),
        actor: AuthenticatedUser = Depends(require_ops),
        _: None = Depends(verify_csrf_request),
    ) -> OpsTicketMutationResponse:
        qa_svc = _qa_service()
        try:
            updated = qa_svc.decide_ticket(
                ticket_id=ticket_id,
                actor=actor,
                decision=body.decision,
                note=body.note,
                client_ip=extract_client_ip(request),
            )
        except OpsQaError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return OpsTicketMutationResponse(
            status="RESOLVED", ticket=_to_ticket_item(updated)
        )

    @router.get("/issuers/lifecycle", response_model=OpsIssuerLifecycleResponse)
    def get_issuers_lifecycle(
        exchange: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _actor: AuthenticatedUser = Depends(require_ops),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
    ) -> OpsIssuerLifecycleResponse:
        lc_svc = _lifecycle_service()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            snap_meta = pinned.get_snapshot_metadata(connection=cursor)
            snap_id = (
                str(snap_meta["snapshot_id"])
                if snap_meta.get("snapshot_id") is not None
                else None
            )
            issuers = _read_optional_table(pinned, "issuers.parquet", connection=cursor)

        views, total = lc_svc.list_lifecycle(
            issuers,
            exchange=exchange,
            q=q,
            limit=limit,
            offset=offset,
        )
        return OpsIssuerLifecycleResponse(
            snapshot_id=snap_id,
            total=total,
            items=[
                IssuerLifecycleItem(
                    issuer_id=v.issuer_id,
                    ticker=v.ticker,
                    company_name_vn=v.company_name_vn,
                    exchange=v.exchange,
                    accounting_regime=v.accounting_regime,
                    icb_name_level2=v.icb_name_level2,
                    icb_name_level3=v.icb_name_level3,
                    delisted_date=v.delisted_date,
                    fy_end_month=v.fy_end_month,
                    recent_events=[_to_event_item(ev) for ev in v.recent_events],
                )
                for v in views
            ],
        )

    @router.post(
        "/issuers/sync-request",
        response_model=OpsIssuerSyncResponse,
        status_code=201,
    )
    def request_issuer_sync(
        request: Request,
        body: OpsIssuerSyncRequest,
        actor: AuthenticatedUser = Depends(require_ops),
        _: None = Depends(verify_csrf_request),
    ) -> OpsIssuerSyncResponse:
        lc_svc = _lifecycle_service()
        try:
            event = lc_svc.record_sync_request(
                actor=actor,
                ticker=body.ticker,
                issuer_id=body.issuer_id,
                exchange=body.exchange,
                event_type=body.event_type,
                effective_date=body.effective_date,
                fy_end_month=body.fy_end_month,
                previous_exchange=body.previous_exchange,
                source_url=body.source_url,
                notes=body.notes,
                client_ip=extract_client_ip(request),
            )
        except OpsQaError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return OpsIssuerSyncResponse(
            status="QUEUED_FOR_SYNC", event=_to_event_item(event)
        )

    return router


def mount_ops_routers(api: FastAPI) -> None:
    """Mount V3 analyst-depth and INTERNAL_OPS QA routers onto ``api``."""
    lock = threading.Lock()

    def _resolve_audit_store() -> AppStoreProtocol | None:
        resolver = getattr(api.state, "_resolve_app_store", None)
        if callable(resolver):
            return cast(AppStoreProtocol | None, resolver())
        existing = getattr(api.state, "_app_store", None)
        return cast(AppStoreProtocol | None, existing)

    def _resolve_qa_store() -> OpsQaStoreProtocol | None:
        existing = getattr(api.state, "_ops_qa_store", None)
        if existing is not None:
            return cast(OpsQaStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_ops_qa_store", None)
            if existing is not None:
                return cast(OpsQaStoreProtocol, existing)
            from finintel.auth._pg_store import resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            pg_store = PostgresOpsQaStore(dsn=dsn)
            api.state._ops_qa_store = pg_store
            return pg_store

    api.state._resolve_ops_qa_store = _resolve_qa_store
    api.include_router(create_analyst_depth_router())
    api.include_router(
        create_ops_router(
            get_qa_store=_resolve_qa_store,
            get_audit_store=_resolve_audit_store,
        )
    )


__all__ = ["create_analyst_depth_router", "create_ops_router", "mount_ops_routers"]
