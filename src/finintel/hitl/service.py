"""HITL false-positive triage business logic & aggregation (V3-29).

Invariant (SR 11-7 MRM):
    HITL expert feedback is stored exclusively in ``finintel_app.hitl_flag_feedback``
    for quantitative rule calibration and NEVER mutates ML training labels or DuckLake.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import AuthenticatedUser
from finintel.hitl._store import HitlFeedbackRecord, HitlStoreProtocol
from finintel.hitl.schemas import (
    HitlFeedbackItem,
    HitlFeedbackRequest,
    HitlSummaryCell,
    HitlSummaryResponse,
)
from finintel.workspace.audit import record_audit_event

MRM_GOVERNANCE_NOTE = (
    "Theo chuẩn quản trị rủi ro mô hình SR 11-7, phản hồi chuyên gia HITL chỉ dùng để "
    "theo dõi tỷ lệ báo động giả và hiệu chỉnh từ điển ngành; hệ thống tuyệt đối không "
    "tự động ghi đè nhãn huấn luyện ML hay bảng DuckLake."
)


class HitlService:
    """Service layer managing expert flag triage and Ops aggregation."""

    def __init__(
        self,
        store: HitlStoreProtocol,
        *,
        audit_store: AppStoreProtocol | None = None,
    ) -> None:
        self._store = store
        self._audit_store = audit_store

    def submit_feedback(
        self,
        *,
        actor: AuthenticatedUser,
        payload: HitlFeedbackRequest,
        resolved_icb_level2: str | None = None,
        client_ip: str = "unknown",
    ) -> HitlFeedbackItem:
        clean_ticker = payload.ticker.strip().upper()
        clean_signal = payload.signal_code.strip().upper()
        clean_icb = (
            (payload.icb_code_level2 or resolved_icb_level2 or "UNCLASSIFIED")
            .strip()
            .upper()
        )
        rec = self._store.insert_feedback(
            user_id=actor.user_id,
            ticker=clean_ticker,
            icb_code_level2=clean_icb,
            period_end_date=payload.period_end_date,
            signal_code=clean_signal,
            verdict=payload.verdict,
            reason_category=payload.reason_category,
            note=payload.note.strip(),
            citation_page=payload.citation_page,
        )
        if self._audit_store is not None:
            record_audit_event(
                self._audit_store,
                actor_user_id=actor.user_id,
                actor_role=actor.role.value,
                action="HITL_FEEDBACK_SUBMITTED",
                resource_type="hitl_flag_feedback",
                resource_id=str(rec.feedback_id),
                client_ip=client_ip,
                metadata={
                    "ticker": rec.ticker,
                    "signal_code": rec.signal_code,
                    "verdict": rec.verdict,
                    "icb_code_level2": rec.icb_code_level2,
                },
            )
        return self._to_item(rec)

    def summarize(
        self,
        *,
        signal_code: str | None = None,
        icb_code_level2: str | None = None,
    ) -> HitlSummaryResponse:
        clean_sig = signal_code.strip().upper() if signal_code else None
        clean_icb = icb_code_level2.strip().upper() if icb_code_level2 else None
        records = self._store.list_feedback(
            signal_code=clean_sig,
            icb_code_level2=clean_icb,
        )
        grouped: dict[tuple[str, str], list[HitlFeedbackRecord]] = defaultdict(list)
        for rec in records:
            grouped[(rec.signal_code, rec.icb_code_level2)].append(rec)

        cells: list[HitlSummaryCell] = []
        total_benign = 0
        for (sig, icb), bucket in sorted(grouped.items()):
            total = len(bucket)
            confirmed = sum(1 for r in bucket if r.verdict == "CONFIRMED_RISK")
            benign = sum(1 for r in bucket if r.verdict == "BENIGN_DOMAIN_EXCEPTION")
            total_benign += benign
            reasons = Counter(r.reason_category for r in bucket)
            top_reasons = [str(name) for name, _ in reasons.most_common(3)]
            cells.append(
                HitlSummaryCell(
                    signal_code=sig,
                    icb_code_level2=icb,
                    total_count=total,
                    confirmed_risk_count=confirmed,
                    benign_exception_count=benign,
                    false_positive_rate=round(benign / total, 4) if total > 0 else 0.0,
                    top_reason_categories=top_reasons,
                )
            )

        total_all = len(records)
        overall_fp = round(total_benign / total_all, 4) if total_all > 0 else None
        return HitlSummaryResponse(
            total_feedback=total_all,
            overall_false_positive_rate=overall_fp,
            items=cells,
            mrm_governance_note=MRM_GOVERNANCE_NOTE,
        )

    @staticmethod
    def _to_item(rec: HitlFeedbackRecord) -> HitlFeedbackItem:
        return HitlFeedbackItem(
            feedback_id=rec.feedback_id,
            user_id=rec.user_id,
            ticker=rec.ticker,
            icb_code_level2=rec.icb_code_level2,
            period_end_date=rec.period_end_date,
            signal_code=rec.signal_code,
            verdict=rec.verdict,
            reason_category=rec.reason_category,
            note=rec.note,
            citation_page=rec.citation_page,
            created_at=rec.created_at,
            ml_training_labels_untouched=True,
        )
