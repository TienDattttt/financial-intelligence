"""VN 2025-2026 regulatory compliance & PDPL (Law 91/2025/QH15) domain contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from finintel.exceptions import ContractViolationError

DeletionRequestStatus = Literal["PENDING", "PROCESSING", "COMPLETED", "REJECTED"]

COMPLIANCE_DISCLAIMER_VI: str = (
    "Thông tin chỉ mang tính tham khảo phân tích định lượng từ dữ liệu lịch sử, "
    "không phải khuyến nghị đầu tư hay đánh giá tín dụng chính thức theo "
    "Nghị định 88/2014/NĐ-CP và Luật Chứng khoán 2019 (sửa đổi bởi Luật 56/2024/QH15)."
)
RISK_TIER_LEGAL_LABEL_VI: str = "Nhóm cảnh báo rủi ro tài chính định lượng (R1–R5)"


class ConsentPurpose(StrEnum):
    """Granular processing purposes under PDPL Law 91/2025/QH15."""

    ACCOUNT_CORE = "ACCOUNT_CORE"
    ALERT_NOTIFICATIONS = "ALERT_NOTIFICATIONS"
    CROSS_BORDER_LLM_RAG = "CROSS_BORDER_LLM_RAG"


def _require_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"{field_name} must be a non-blank string.")
    return value.strip()


def _require_utc_datetime(value: datetime, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ContractViolationError(f"{field_name} must be a timezone-aware datetime.")
    return value


@dataclass(frozen=True)
class ConsentPolicyVersion:
    """Versioned PDPL consent policy contract shared across Auth, Alerts, and RAG."""

    version: str
    effective_date: date
    purposes: tuple[ConsentPurpose, ...]
    summary_vi: str
    tia_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _require_non_blank(self.version, "version"))
        object.__setattr__(
            self, "summary_vi", _require_non_blank(self.summary_vi, "summary_vi")
        )
        if type(self.effective_date) is not date:
            raise ContractViolationError("effective_date must be a calendar date.")
        if not self.purposes or len(set(self.purposes)) != len(self.purposes):
            raise ContractViolationError("purposes must be non-empty and unique.")
        if self.tia_reference is not None:
            ref = _require_non_blank(self.tia_reference, "tia_reference")
            object.__setattr__(self, "tia_reference", ref)

    def requires_explicit_opt_in(self, purpose: ConsentPurpose) -> bool:
        return purpose in (
            ConsentPurpose.ALERT_NOTIFICATIONS,
            ConsentPurpose.CROSS_BORDER_LLM_RAG,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "effective_date": self.effective_date.isoformat(),
            "purposes": [p.value for p in self.purposes],
            "summary_vi": self.summary_vi,
            "tia_reference": self.tia_reference,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ConsentPolicyVersion:
        version_raw = raw.get("version")
        eff_raw = raw.get("effective_date")
        purposes_raw = raw.get("purposes")
        summary_raw = raw.get("summary_vi")
        tia_raw = raw.get("tia_reference")
        if (
            not isinstance(version_raw, str)
            or not isinstance(eff_raw, str)
            or not isinstance(purposes_raw, list)
            or not isinstance(summary_raw, str)
        ):
            raise ContractViolationError("Invalid ConsentPolicyVersion payload.")
        try:
            eff_date = date.fromisoformat(eff_raw)
            purposes = tuple(ConsentPurpose(str(item)) for item in purposes_raw)
        except ValueError as exc:
            raise ContractViolationError(
                "Invalid date or purpose in ConsentPolicyVersion."
            ) from exc
        return cls(
            version=version_raw,
            effective_date=eff_date,
            purposes=purposes,
            summary_vi=summary_raw,
            tia_reference=str(tia_raw) if tia_raw is not None else None,
        )


@dataclass(frozen=True)
class ConsentRecord:
    """Immutable audit record of a user's PDPL consent grant or withdrawal."""

    purpose: ConsentPurpose
    policy_version: str
    granted: bool
    recorded_at: datetime
    source_channel: str = "web"
    withdrawn_at: datetime | None = None

    def __post_init__(self) -> None:
        ver = _require_non_blank(self.policy_version, "policy_version")
        chan = _require_non_blank(self.source_channel, "source_channel")
        object.__setattr__(self, "policy_version", ver)
        object.__setattr__(self, "source_channel", chan)
        rec = _require_utc_datetime(self.recorded_at, "recorded_at")
        if (
            self.withdrawn_at is not None
            and _require_utc_datetime(self.withdrawn_at, "withdrawn_at") < rec
        ):
            raise ContractViolationError("withdrawn_at cannot precede recorded_at.")

    def is_active(self) -> bool:
        return self.granted and self.withdrawn_at is None

    def to_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose.value,
            "policy_version": self.policy_version,
            "granted": self.granted,
            "recorded_at": self.recorded_at.isoformat(),
            "source_channel": self.source_channel,
            "withdrawn_at": self.withdrawn_at.isoformat()
            if self.withdrawn_at
            else None,
            "active": self.is_active(),
        }


@dataclass(frozen=True)
class DataDeletionPolicy:
    """Retention and erasure SLA contract under PDPL Law 91/2025/QH15."""

    sla_days: int = 15
    audit_log_retention_days: int = 365
    anonymize_audit_subject: bool = True
    require_reauth: bool = True
    legal_basis: str = "Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15"

    def __post_init__(self) -> None:
        if self.sla_days < 1 or self.audit_log_retention_days < 1:
            raise ContractViolationError(
                "Deletion SLA and retention days must be >= 1."
            )
        object.__setattr__(
            self, "legal_basis", _require_non_blank(self.legal_basis, "legal_basis")
        )


@dataclass(frozen=True)
class DataDeletionRequest:
    """User erasure request payload processed by workspace identity deletion flow."""

    request_id: str
    subject_id: str
    requested_at: datetime
    reason: str | None = None
    status: DeletionRequestStatus = "PENDING"
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_id", _require_non_blank(self.request_id, "request_id")
        )
        object.__setattr__(
            self, "subject_id", _require_non_blank(self.subject_id, "subject_id")
        )
        req_at = _require_utc_datetime(self.requested_at, "requested_at")
        if (
            self.completed_at is not None
            and _require_utc_datetime(self.completed_at, "completed_at") < req_at
        ):
            raise ContractViolationError("completed_at cannot precede requested_at.")

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "subject_id": self.subject_id,
            "requested_at": self.requested_at.isoformat(),
            "reason": self.reason,
            "status": self.status,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }


@dataclass(frozen=True)
class ComplianceNotice:
    """Canonical legal disclaimer bundle shared across API, Exports, and UI."""

    disclaimer_vi: str
    risk_tier_legal_label_vi: str
    law_references: tuple[str, ...]
    policy_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "disclaimer_vi": self.disclaimer_vi,
            "risk_tier_legal_label_vi": self.risk_tier_legal_label_vi,
            "law_references": list(self.law_references),
            "policy_version": self.policy_version,
        }


CURRENT_CONSENT_POLICY_VERSION = ConsentPolicyVersion(
    version="2026.01",
    effective_date=date(2026, 1, 1),
    purposes=(
        ConsentPurpose.ACCOUNT_CORE,
        ConsentPurpose.ALERT_NOTIFICATIONS,
        ConsentPurpose.CROSS_BORDER_LLM_RAG,
    ),
    summary_vi="Chính sách xử lý dữ liệu cá nhân FinIntel V3.0 theo Luật 91/2025/QH15.",
    tia_reference="docs/compliance/VN_REGULATORY_COMPLIANCE.md#tia-dieu-20",
)
DEFAULT_DATA_DELETION_POLICY = DataDeletionPolicy()
DEFAULT_COMPLIANCE_NOTICE = ComplianceNotice(
    disclaimer_vi=COMPLIANCE_DISCLAIMER_VI,
    risk_tier_legal_label_vi=RISK_TIER_LEGAL_LABEL_VI,
    law_references=(
        "Nghị định 88/2014/NĐ-CP",
        "Luật Chứng khoán 54/2019/QH14 (sửa đổi Luật 56/2024/QH15)",
        "Luật 91/2025/QH15",
    ),
    policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
)
