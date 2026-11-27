"""Unit tests for V3-01 & V3-02: VN 2026 compliance contracts and TT200->TT99 MRM drift gate."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from finintel.domain.compliance import (
    COMPLIANCE_DISCLAIMER_VI,
    CURRENT_CONSENT_POLICY_VERSION,
    DEFAULT_COMPLIANCE_NOTICE,
    RISK_TIER_LEGAL_LABEL_VI,
    ComplianceNotice,
    ConsentPolicyVersion,
    ConsentPurpose,
    ConsentRecord,
    DataDeletionPolicy,
    DataDeletionRequest,
)
from finintel.exceptions import ContractViolationError


def test_consent_purpose_enum_and_policy_version_contracts() -> None:
    """V3-01: ConsentPurpose exposes exact PDPL 91/2025 scopes and validates version metadata."""
    assert [p.value for p in ConsentPurpose] == [
        "ACCOUNT_CORE",
        "ALERT_NOTIFICATIONS",
        "CROSS_BORDER_LLM_RAG",
    ]
    assert CURRENT_CONSENT_POLICY_VERSION.version == "2026.01"
    assert CURRENT_CONSENT_POLICY_VERSION.requires_explicit_opt_in(
        ConsentPurpose.CROSS_BORDER_LLM_RAG
    )
    assert CURRENT_CONSENT_POLICY_VERSION.requires_explicit_opt_in(
        ConsentPurpose.ALERT_NOTIFICATIONS
    )
    assert not CURRENT_CONSENT_POLICY_VERSION.requires_explicit_opt_in(
        ConsentPurpose.ACCOUNT_CORE
    )

    serialized = json.dumps(
        CURRENT_CONSENT_POLICY_VERSION.to_dict(), ensure_ascii=False
    )
    restored = ConsentPolicyVersion.from_mapping(json.loads(serialized))
    assert restored == CURRENT_CONSENT_POLICY_VERSION

    with pytest.raises(ContractViolationError):
        ConsentPolicyVersion(
            version="   ",
            effective_date=date(2026, 1, 1),
            purposes=(ConsentPurpose.ACCOUNT_CORE,),
            summary_vi="Valid summary",
        )

    with pytest.raises(ContractViolationError):
        ConsentPolicyVersion(
            version="2026.02",
            effective_date=date(2026, 1, 1),
            purposes=(ConsentPurpose.ACCOUNT_CORE, ConsentPurpose.ACCOUNT_CORE),
            summary_vi="Duplicate purposes rejected",
        )


def test_consent_record_and_data_deletion_contracts() -> None:
    """V3-01: ConsentRecord tracks active/withdrawn state and DataDeletionRequest enforces UTC."""
    granted_at = datetime(2026, 1, 15, 8, 30, tzinfo=UTC)
    record = ConsentRecord(
        purpose=ConsentPurpose.CROSS_BORDER_LLM_RAG,
        policy_version="2026.01",
        granted=True,
        recorded_at=granted_at,
        source_channel="register_form",
    )
    assert record.is_active() is True
    payload = record.to_dict()
    assert payload["purpose"] == "CROSS_BORDER_LLM_RAG"
    assert payload["granted"] is True
    assert payload["withdrawn_at"] is None

    withdrawn = ConsentRecord(
        purpose=ConsentPurpose.CROSS_BORDER_LLM_RAG,
        policy_version="2026.01",
        granted=True,
        recorded_at=granted_at,
        withdrawn_at=datetime(2026, 2, 1, 9, 0, tzinfo=UTC),
    )
    assert withdrawn.is_active() is False

    with pytest.raises(ContractViolationError):
        ConsentRecord(
            purpose=ConsentPurpose.ACCOUNT_CORE,
            policy_version="2026.01",
            granted=True,
            recorded_at=datetime(2026, 1, 15, 8, 30),  # naive datetime rejected
        )

    policy = DataDeletionPolicy()
    assert policy.sla_days == 15
    assert policy.anonymize_audit_subject is True

    req = DataDeletionRequest(
        request_id="del_001",
        subject_id="usr_abc",
        requested_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        reason="User exercised erasure right under Law 91/2025",
    )
    assert req.status == "PENDING"
    assert req.to_dict()["subject_id"] == "usr_abc"

    with pytest.raises(ContractViolationError):
        DataDeletionPolicy(sla_days=0)


def test_compliance_notice_and_legal_docs_are_consistent_and_clean() -> None:
    """V3-01: ComplianceNotice and VN_REGULATORY_COMPLIANCE.md cover ND88, Law 56, and Law 91 TIA."""
    assert isinstance(DEFAULT_COMPLIANCE_NOTICE, ComplianceNotice)
    assert "88/2014" in COMPLIANCE_DISCLAIMER_VI
    assert "56/2024" in COMPLIANCE_DISCLAIMER_VI
    assert "R1–R5" in RISK_TIER_LEGAL_LABEL_VI
    for forbidden in ("tín nhiệm", "khuyến nghị mua", "khuyến nghị bán"):
        assert forbidden not in COMPLIANCE_DISCLAIMER_VI.lower()
        assert forbidden not in RISK_TIER_LEGAL_LABEL_VI.lower()

    notice_dict = DEFAULT_COMPLIANCE_NOTICE.to_dict()
    assert notice_dict["policy_version"] == CURRENT_CONSENT_POLICY_VERSION.version

    repo_root = Path(__file__).resolve().parents[2]
    doc_path = repo_root / "docs" / "compliance" / "VN_REGULATORY_COMPLIANCE.md"
    assert doc_path.is_file(), "VN_REGULATORY_COMPLIANCE.md must exist"
    doc_text = doc_path.read_text(encoding="utf-8")
    for required_token in (
        "88/2014/NĐ-CP",
        "56/2024/QH15",
        "91/2025/QH15",
        "Điều 20",
        "CROSS_BORDER_LLM_RAG",
        "TT99/2025",
    ):
        assert required_token in doc_text
