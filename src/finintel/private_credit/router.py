"""FastAPI router for isolated private-issuer VAS parsing & credit scoring (V3-28, V3-29)."""

import importlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from finintel.auth._store import AppStoreProtocol, InMemoryAppStore
from finintel.auth.dependencies import (
    AuthenticatedUser,
    UserRole,
    extract_client_ip,
    require_role,
    verify_csrf_request,
)
from finintel.private_credit._qg1 import StatementScopeStr, TaxonomyVersionStr
from finintel.private_credit._store import (
    InMemoryPrivateCreditStore,
    PostgresPrivateCreditStore,
    PrivateCreditDossierRecord,
    PrivateCreditStoreProtocol,
)
from finintel.private_credit._xlsx_guards import (
    PrivateCreditValidationError,
    build_vas_template_xlsx,
    parse_multipart_upload,
)
from finintel.private_credit.scorer import score_private_dossier
from finintel.private_credit.vas_excel_parser import parse_vas_excel_workbook
from finintel.workspace.audit import record_audit_event

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from finintel.private_credit.schemas import (
        PrivateDossierPreviewResponse,
    )

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _to_preview_response(
    rec: PrivateCreditDossierRecord,
) -> "PrivateDossierPreviewResponse":
    from finintel.private_credit.schemas import (
        PrivateDossierPreviewResponse,
        PrivateParsedLineItem,
        Qg1DiscrepancyItem,
        Qg1ReportResponse,
    )

    qg1_raw = rec.qg1_report_json
    raw_discs = qg1_raw.get("discrepancies")
    discrepancies: list[Qg1DiscrepancyItem] = []
    if isinstance(raw_discs, list):
        for item in raw_discs:
            if isinstance(item, dict):
                discrepancies.append(Qg1DiscrepancyItem(**item))
    raw_warns = qg1_raw.get("warnings")
    raw_info = qg1_raw.get("info_signals")
    qg1 = Qg1ReportResponse(
        status=cast(Literal["PASS", "WARNING", "FAIL"], rec.qg1_status),
        discrepancies=discrepancies,
        warnings=[str(w) for w in raw_warns] if isinstance(raw_warns, list) else [],
        info_signals=[str(i) for i in raw_info] if isinstance(raw_info, list) else [],
        can_score=rec.qg1_status != "FAIL",
    )

    grouped: dict[tuple[str, str, str, str], dict[str, float | None]] = {}
    for f in rec.facts:
        key = (f.statement_type, f.vas_code, f.metric_id, f.line_label_vn)
        grouped.setdefault(key, {})[f.period_end_date.isoformat()] = f.value
    lines = [
        PrivateParsedLineItem(
            statement_type=cast(
                Literal["BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW"], stmt
            ),
            vas_code=code,
            metric_id=mid,
            line_label_vn=label,
            values_by_period=vals,
        )
        for (stmt, code, mid, label), vals in grouped.items()
    ]

    return PrivateDossierPreviewResponse(
        dossier_id=rec.dossier_id,
        company_name=rec.company_name,
        tax_code=rec.tax_code,
        icb_code_level2=rec.icb_code_level2,
        statement_scope=rec.statement_scope,
        taxonomy_version=rec.taxonomy_version,
        unit_multiplier=rec.unit_multiplier,
        source_filename=rec.source_filename,
        source_sha256=rec.source_sha256,
        periods=list(rec.periods),
        qg1=qg1,
        lines=lines,
        retention_days=30,
        retention_expires_at=rec.retention_expires_at,
        processing_mode="LOCAL_SERVER_ONLY",
        created_at=rec.created_at,
    )


def _resolve_ml_training_result(api_state: Any) -> Any | None:
    """Resolve optional in-memory or persisted ``TrainingResult`` without failing offline mode."""
    existing = getattr(api_state, "_ml_training_result", None)
    if existing is not None:
        return existing
    if getattr(api_state, "_ml_training_checked", False):
        return None
    setattr(api_state, "_ml_training_checked", True)

    raw_dir = os.getenv("FININTEL_ML_ARTIFACT_DIR", "").strip()
    candidate_dir: Path | None = Path(raw_dir) if raw_dir else None
    if candidate_dir is None:
        svc = getattr(api_state, "query_service", None)
        repo = getattr(svc, "repository", None)
        source = getattr(repo, "_source", None)
        if isinstance(source, Path):
            from finintel.serving.freshness import resolve_serving_data_root

            root = resolve_serving_data_root(source)
            if root is not None:
                candidate_dir = root / "ml_artifacts"

    if candidate_dir is None or not candidate_dir.is_dir():
        return None
    try:
        inc_mod = importlib.import_module("finintel.transform.incremental")
        bundle = inc_mod.load_ml_artifact_bundle(candidate_dir)
    except Exception:
        return None
    training = getattr(bundle, "training", None) if bundle is not None else None
    if training is not None:
        setattr(api_state, "_ml_training_result", training)
    return training


def create_private_credit_router(
    *,
    get_store: Callable[[], PrivateCreditStoreProtocol | None],
    get_app_store: Callable[[], AppStoreProtocol | None] | None = None,
) -> "APIRouter":
    """Create ``/api/v3/private-credit/*`` router gated to ``RESEARCHER`` / ``INTERNAL_OPS``."""
    from fastapi import (
        APIRouter,
        Body,
        Depends,
        HTTPException,
        Query,
        Request,
        Response,
    )

    from finintel.private_credit.schemas import (
        PrivateDossierDeleteResponse,
        PrivateDossierListResponse,
        PrivateDossierPreviewResponse,
        PrivateDossierSummaryItem,
        PrivateScoreRequest,
        PrivateScoreResponse,
    )

    router = APIRouter(prefix="/api/v3/private-credit", tags=["v3-private-credit"])
    require_credit_role = require_role(UserRole.RESEARCHER, UserRole.INTERNAL_OPS)

    def _require_store() -> PrivateCreditStoreProtocol:
        store = get_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Dịch vụ thẩm định doanh nghiệp tư nhân chưa được cấu hình cơ sở dữ liệu.",
            )
        return store

    @router.get("/template.xlsx")
    def download_vas_template(
        taxonomy: TaxonomyVersionStr = Query("VAS_TT99_2025"),
        _user: AuthenticatedUser = Depends(require_credit_role),
    ) -> Response:
        try:
            payload = build_vas_template_xlsx(taxonomy_version=taxonomy)
        except PrivateCreditValidationError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None
        return Response(
            content=payload,
            media_type=_XLSX_MIME,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="finintel_vas_template_{taxonomy}.xlsx"'
                ),
                "Cache-Control": "private, max-age=300",
            },
        )

    @router.post(
        "/parse", response_model=PrivateDossierPreviewResponse, status_code=201
    )
    async def parse_private_statement(
        request: Request,
        user: AuthenticatedUser = Depends(require_credit_role),
        _: None = Depends(verify_csrf_request),
    ) -> PrivateDossierPreviewResponse:
        store = _require_store()
        file_bytes, filename, fields = await parse_multipart_upload(request)
        company_name = fields.get("company_name", "")
        tax_code = fields.get("tax_code") or None
        icb_code_level2 = fields.get("icb_code_level2") or None
        raw_scope = fields.get("statement_scope", "SEPARATE").upper()
        if raw_scope not in ("CONSOLIDATED", "SEPARATE"):
            raise HTTPException(status_code=422, detail="Phạm vi BCTC không hợp lệ.")
        raw_tax = fields.get("taxonomy_version", "AUTO").upper()
        if raw_tax not in ("VAS_TT200_2014", "VAS_TT99_2025", "AUTO"):
            raise HTTPException(
                status_code=422, detail="Chuẩn mực kế toán VAS không hợp lệ."
            )
        try:
            unit_mult = int(fields.get("unit_multiplier", "1"))
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Hệ số đơn vị tiền tệ không hợp lệ."
            ) from None

        try:
            parsed = parse_vas_excel_workbook(
                file_bytes,
                filename=filename,
                company_name=company_name,
                tax_code=tax_code,
                icb_code_level2=icb_code_level2,
                statement_scope=cast(StatementScopeStr, raw_scope),
                taxonomy_version=cast(
                    Literal["VAS_TT200_2014", "VAS_TT99_2025", "AUTO"], raw_tax
                ),
                unit_multiplier=unit_mult,
            )
        except PrivateCreditValidationError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None

        qg1_json: dict[str, object] = {
            "discrepancies": [
                {
                    "period_end_date": d.period_end_date.isoformat(),
                    "rule_code": d.rule_code,
                    "severity": d.severity,
                    "message_vi": d.message_vi,
                    "expected_value_vnd": d.expected_value_vnd,
                    "actual_value_vnd": d.actual_value_vnd,
                    "abs_residual_vnd": d.abs_residual_vnd,
                    "missing_metrics": list(d.missing_metrics),
                }
                for d in parsed.qg1.discrepancies
            ],
            "warnings": list(parsed.qg1.warnings),
            "info_signals": list(parsed.qg1.info_signals),
        }
        rec = store.create_dossier(
            user_id=user.user_id,
            company_name=parsed.company_name,
            tax_code=parsed.tax_code,
            icb_code_level2=parsed.icb_code_level2,
            statement_scope=parsed.statement_scope,
            taxonomy_version=parsed.taxonomy_version,
            unit_multiplier=parsed.unit_multiplier,
            source_filename=parsed.source_filename,
            source_sha256=parsed.source_sha256,
            qg1_status=parsed.qg1.status,
            qg1_report_json=qg1_json,
            periods=parsed.periods,
            facts=parsed.facts,
        )
        app_store = get_app_store() if get_app_store is not None else None
        if app_store is not None:
            record_audit_event(
                app_store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="PRIVATE_CREDIT_DOSSIER_PARSED",
                resource_type="private_credit_dossier",
                resource_id=str(rec.dossier_id),
                client_ip=extract_client_ip(request),
                metadata={
                    "qg1_status": rec.qg1_status,
                    "taxonomy": rec.taxonomy_version,
                },
            )
        return _to_preview_response(rec)

    @router.get("/dossiers", response_model=PrivateDossierListResponse)
    def list_dossiers(
        user: AuthenticatedUser = Depends(require_credit_role),
    ) -> PrivateDossierListResponse:
        store = _require_store()
        recs = store.list_dossiers(user_id=user.user_id)
        return PrivateDossierListResponse(
            items=[
                PrivateDossierSummaryItem(
                    dossier_id=r.dossier_id,
                    company_name=r.company_name,
                    tax_code=r.tax_code,
                    taxonomy_version=r.taxonomy_version,
                    qg1_status=cast(Literal["PASS", "WARNING", "FAIL"], r.qg1_status),
                    periods=list(r.periods),
                    has_score=r.latest_score_json is not None,
                    retention_expires_at=r.retention_expires_at,
                    created_at=r.created_at,
                )
                for r in recs
            ]
        )

    @router.get("/dossiers/{dossier_id}", response_model=PrivateDossierPreviewResponse)
    def get_dossier(
        dossier_id: UUID,
        user: AuthenticatedUser = Depends(require_credit_role),
    ) -> PrivateDossierPreviewResponse:
        store = _require_store()
        rec = store.get_dossier(dossier_id=dossier_id, user_id=user.user_id)
        if rec is None:
            raise HTTPException(
                status_code=404, detail="Không tìm thấy hồ sơ doanh nghiệp tư nhân."
            )
        return _to_preview_response(rec)

    @router.post("/dossiers/{dossier_id}/score", response_model=PrivateScoreResponse)
    def score_dossier(
        request: Request,
        dossier_id: UUID,
        body: PrivateScoreRequest = Body(default_factory=PrivateScoreRequest),
        user: AuthenticatedUser = Depends(require_credit_role),
        _: None = Depends(verify_csrf_request),
    ) -> PrivateScoreResponse:
        store = _require_store()
        rec = store.get_dossier(dossier_id=dossier_id, user_id=user.user_id)
        if rec is None:
            raise HTTPException(
                status_code=404, detail="Không tìm thấy hồ sơ doanh nghiệp tư nhân."
            )
        ml_model = _resolve_ml_training_result(request.app.state)
        try:
            result = score_private_dossier(
                rec, request=body, ml_training_result=ml_model
            )
        except PrivateCreditValidationError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from None

        store.save_score_result(
            dossier_id=dossier_id,
            user_id=user.user_id,
            score_json=result.model_dump(mode="json"),
        )
        return result

    @router.delete(
        "/dossiers/{dossier_id}", response_model=PrivateDossierDeleteResponse
    )
    def delete_dossier(
        request: Request,
        dossier_id: UUID,
        user: AuthenticatedUser = Depends(require_credit_role),
        _: None = Depends(verify_csrf_request),
    ) -> PrivateDossierDeleteResponse:
        store = _require_store()
        if not store.delete_dossier(dossier_id=dossier_id, user_id=user.user_id):
            raise HTTPException(
                status_code=404, detail="Không tìm thấy hồ sơ doanh nghiệp tư nhân."
            )
        app_store = get_app_store() if get_app_store is not None else None
        if app_store is not None:
            record_audit_event(
                app_store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="PRIVATE_CREDIT_DOSSIER_DELETED",
                resource_type="private_credit_dossier",
                resource_id=str(dossier_id),
                client_ip=extract_client_ip(request),
            )
        return PrivateDossierDeleteResponse(status="DELETED", dossier_id=dossier_id)

    return router


def mount_private_credit_router(
    api: "FastAPI",
    *,
    private_credit_store: PrivateCreditStoreProtocol | None = None,
    ml_training_result: Any | None = None,
) -> None:
    """Mount ``/api/v3/private-credit/*`` lazily sharing ``finintel_app`` store state."""
    lock = threading.Lock()
    if private_credit_store is not None:
        api.state._private_credit_store = private_credit_store
    if ml_training_result is not None:
        api.state._ml_training_result = ml_training_result

    def _resolve_app_store() -> AppStoreProtocol | None:
        resolver = getattr(api.state, "_resolve_app_store", None)
        if callable(resolver):
            return cast(AppStoreProtocol | None, resolver())
        return cast(AppStoreProtocol | None, getattr(api.state, "_app_store", None))

    def _resolve_private_store() -> PrivateCreditStoreProtocol | None:
        existing = getattr(api.state, "_private_credit_store", None)
        if existing is not None:
            return cast(PrivateCreditStoreProtocol, existing)
        with lock:
            existing = getattr(api.state, "_private_credit_store", None)
            if existing is not None:
                return cast(PrivateCreditStoreProtocol, existing)
            app_store = _resolve_app_store()
            if isinstance(app_store, InMemoryAppStore):
                mem_store = InMemoryPrivateCreditStore()
                api.state._private_credit_store = mem_store
                return mem_store
            from finintel.auth._pg_store import resolve_app_pg_dsn

            dsn = resolve_app_pg_dsn()
            if not dsn:
                return None
            pg_store = PostgresPrivateCreditStore(dsn=dsn)
            api.state._private_credit_store = pg_store
            return pg_store

    api.state._resolve_private_credit_store = _resolve_private_store
    api.include_router(
        create_private_credit_router(
            get_store=_resolve_private_store,
            get_app_store=_resolve_app_store,
        )
    )


def mount_private_hitl_routers(api: "FastAPI", **kwargs: Any) -> None:
    """Mount both private-credit and HITL routers onto ``api`` in a single call."""
    from finintel.hitl.router import mount_hitl_router

    mount_private_credit_router(
        api,
        private_credit_store=kwargs.get("private_credit_store"),
        ml_training_result=kwargs.get("ml_training_result"),
    )
    mount_hitl_router(api, hitl_store=kwargs.get("hitl_store"))


__all__ = [
    "create_private_credit_router",
    "mount_private_credit_router",
    "mount_private_hitl_routers",
]
