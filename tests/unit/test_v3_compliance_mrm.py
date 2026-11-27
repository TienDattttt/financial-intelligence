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


def test_tt99_mrm_gate_verdicts_boundaries_and_nan_inf_guard() -> None:
    """V3-02: evaluate_tt99_mrm_gate enforces sample/PSI/missing boundaries and NaN/inf safety."""
    from finintel.ml.mrm_tt99 import TT99GateVerdict, evaluate_tt99_mrm_gate

    # n < 30 -> INSUFFICIENT_TT99_SAMPLES
    rep0 = evaluate_tt99_mrm_gate(
        tt99_sample_count=0,
        feature_psi={},
        missing_rate_delta={},
    )
    assert rep0.verdict == TT99GateVerdict.INSUFFICIENT_TT99_SAMPLES
    assert rep0.shadow_mode_active is True
    assert rep0.auto_promote_allowed is False
    assert rep0.max_feature_psi is None
    assert rep0.max_missing_jump is None

    rep29 = evaluate_tt99_mrm_gate(
        tt99_sample_count=29,
        feature_psi={"roa": 0.10},
        missing_rate_delta={"roa": 0.02},
    )
    assert rep29.verdict == TT99GateVerdict.INSUFFICIENT_TT99_SAMPLES

    # 30 <= n < 150 with healthy drift -> SHADOW_MONITORING
    rep100 = evaluate_tt99_mrm_gate(
        tt99_sample_count=100,
        feature_psi={"roa": 0.24},
        missing_rate_delta={"roa": 0.14},
    )
    assert rep100.verdict == TT99GateVerdict.SHADOW_MONITORING
    assert rep100.shadow_mode_active is True
    assert rep100.auto_promote_allowed is False

    # n >= 150 with PSI < 0.25 and missing_jump < 0.15 -> READY_FOR_GA
    rep150 = evaluate_tt99_mrm_gate(
        tt99_sample_count=150,
        feature_psi={"roa": 0.24},
        missing_rate_delta={"roa": 0.14},
    )
    assert rep150.verdict == TT99GateVerdict.READY_FOR_GA
    assert rep150.shadow_mode_active is False
    assert rep150.auto_promote_allowed is True

    rep200_ok = evaluate_tt99_mrm_gate(
        tt99_sample_count=200,
        feature_psi={"roa": 0.24, "debt_to_equity": 0.11},
        missing_rate_delta={"roa": 0.14, "debt_to_equity": -0.05},
    )
    assert rep200_ok.verdict == TT99GateVerdict.READY_FOR_GA
    assert rep200_ok.max_feature_psi == pytest.approx(0.24)
    assert rep200_ok.max_missing_jump == pytest.approx(0.14)

    # PSI >= 0.25 -> DRIFT_BREACH (at n=200 and n=100)
    rep_psi_breach = evaluate_tt99_mrm_gate(
        tt99_sample_count=200,
        feature_psi={"roa": 0.26, "debt_to_equity": 0.10},
        missing_rate_delta={"roa": 0.05},
    )
    assert rep_psi_breach.verdict == TT99GateVerdict.DRIFT_BREACH
    assert rep_psi_breach.auto_promote_allowed is False
    assert rep_psi_breach.breached_psi_features == ("roa",)

    rep_shadow_breach = evaluate_tt99_mrm_gate(
        tt99_sample_count=100,
        feature_psi={"roa": 0.25},
        missing_rate_delta={"roa": 0.01},
    )
    assert rep_shadow_breach.verdict == TT99GateVerdict.DRIFT_BREACH

    # missing_jump >= 0.15 -> DRIFT_BREACH
    rep_miss_breach = evaluate_tt99_mrm_gate(
        tt99_sample_count=200,
        feature_psi={"roa": 0.12},
        missing_rate_delta={"roa": 0.16, "roe_parent": 0.04},
    )
    assert rep_miss_breach.verdict == TT99GateVerdict.DRIFT_BREACH
    assert rep_miss_breach.breached_missing_features == ("roa",)

    # NaN / inf guard: finite metrics still evaluated and JSON stays strictly finite
    rep_nan = evaluate_tt99_mrm_gate(
        tt99_sample_count=200,
        feature_psi={
            "roa": float("nan"),
            "ebit_margin": float("inf"),
            "roe_parent": 0.12,
        },
        missing_rate_delta={"roa": float("-inf"), "roe_parent": 0.03},
    )
    assert rep_nan.verdict == TT99GateVerdict.READY_FOR_GA
    assert rep_nan.max_feature_psi == pytest.approx(0.12)
    assert rep_nan.max_missing_jump == pytest.approx(0.03)
    assert rep_nan.non_finite_features == ("ebit_margin", "roa")
    json.dumps(rep_nan.to_dict(), allow_nan=False)

    with pytest.raises(ContractViolationError):
        evaluate_tt99_mrm_gate(
            tt99_sample_count=-1, feature_psi={}, missing_rate_delta={}
        )


def test_tt99_mrm_gate_wires_into_psi_model_card_and_serving_schema() -> None:
    """V3-02: compute_taxonomy_drift_psi, ModelCardResponse, and mrm_tt99 work seamlessly."""
    pytest.importorskip("pydantic")
    import polars as pl

    from finintel.ml.mrm_tt99 import evaluate_tt99_gate_from_psi_report
    from finintel.ml.supervised.train import compute_taxonomy_drift_psi
    from finintel.serving.schemas import ModelCardResponse, TT99MrmGateItem

    ref_df = pl.DataFrame({"roa": [0.05 + 0.001 * i for i in range(40)]})
    empty_tt99 = pl.DataFrame(schema={"roa": pl.Float64})

    psi_zero_tt99 = compute_taxonomy_drift_psi(ref_df, empty_tt99, features=("roa",))
    assert psi_zero_tt99["target_rows"] == 0
    assert "tt99_mrm_gate" in psi_zero_tt99
    assert psi_zero_tt99["tt99_mrm_gate"]["verdict"] == "INSUFFICIENT_TT99_SAMPLES"

    gate_from_report = evaluate_tt99_gate_from_psi_report(psi_zero_tt99)
    gate_item = TT99MrmGateItem(**gate_from_report)
    assert gate_item.verdict == "INSUFFICIENT_TT99_SAMPLES"
    assert gate_item.shadow_mode_active is True
    assert "tt99_mrm_gate" in ModelCardResponse.model_fields
