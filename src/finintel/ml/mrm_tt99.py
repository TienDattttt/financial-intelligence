"""TT200/2014 -> TT99/2025 Model Risk Management (MRM SR 11-7) drift gate.

Verified Sources:
    - Federal Reserve SR 11-7: Supervisory Guidance on Model Risk Management.
    - Circular 99/2025/TT-BTC (effective 2026-01-01) replacing Circular 200/2014/TT-BTC.
    - Population Stability Index (PSI) threshold 0.25 and missingness jump threshold 0.15.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from finintel.exceptions import ContractViolationError

DEFAULT_MIN_SHADOW_SAMPLES = 30
DEFAULT_MIN_GA_SAMPLES = 150
DEFAULT_PSI_BREACH_THRESHOLD = 0.25
DEFAULT_MISSING_JUMP_THRESHOLD = 0.15


class TT99GateVerdict(StrEnum):
    """Promotion & shadow-scoring verdict for VAS_TT99_2025 transition."""

    INSUFFICIENT_TT99_SAMPLES = "INSUFFICIENT_TT99_SAMPLES"
    SHADOW_MONITORING = "SHADOW_MONITORING"
    READY_FOR_GA = "READY_FOR_GA"
    DRIFT_BREACH = "DRIFT_BREACH"


@dataclass(frozen=True)
class TT99MrmGateReport:
    """Auditable MRM gate output exposed on Model Card & /methodology?tab=models."""

    verdict: TT99GateVerdict
    shadow_mode_active: bool
    auto_promote_allowed: bool
    tt99_sample_count: int
    min_shadow_samples: int
    min_ga_samples: int
    psi_breach_threshold: float
    missing_jump_threshold: float
    max_feature_psi: float | None
    max_missing_jump: float | None
    breached_psi_features: tuple[str, ...]
    breached_missing_features: tuple[str, ...]
    non_finite_features: tuple[str, ...]
    summary_vi: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "shadow_mode_active": self.shadow_mode_active,
            "auto_promote_allowed": self.auto_promote_allowed,
            "tt99_sample_count": self.tt99_sample_count,
            "min_shadow_samples": self.min_shadow_samples,
            "min_ga_samples": self.min_ga_samples,
            "psi_breach_threshold": self.psi_breach_threshold,
            "missing_jump_threshold": self.missing_jump_threshold,
            "max_feature_psi": self.max_feature_psi,
            "max_missing_jump": self.max_missing_jump,
            "breached_psi_features": list(self.breached_psi_features),
            "breached_missing_features": list(self.breached_missing_features),
            "non_finite_features": list(self.non_finite_features),
            "summary_vi": self.summary_vi,
        }


def _finite_float(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def evaluate_tt99_mrm_gate(
    *,
    tt99_sample_count: int,
    feature_psi: Mapping[str, float | None],
    missing_rate_delta: Mapping[str, float | None],
    min_ga_samples: int = DEFAULT_MIN_GA_SAMPLES,
    psi_breach_threshold: float = DEFAULT_PSI_BREACH_THRESHOLD,
    missing_jump_threshold: float = DEFAULT_MISSING_JUMP_THRESHOLD,
    min_shadow_samples: int = DEFAULT_MIN_SHADOW_SAMPLES,
) -> TT99MrmGateReport:
    """Evaluate TT200 -> TT99 drift gate with NaN/inf protection and honest sample gates."""
    if isinstance(tt99_sample_count, bool) or tt99_sample_count < 0:
        raise ContractViolationError(
            "tt99_sample_count must be a non-negative integer."
        )
    if min_shadow_samples < 1 or min_ga_samples < min_shadow_samples:
        raise ContractViolationError(
            "min_ga_samples must be >= min_shadow_samples >= 1."
        )
    if not math.isfinite(psi_breach_threshold) or psi_breach_threshold <= 0.0:
        raise ContractViolationError(
            "psi_breach_threshold must be a finite positive float."
        )
    if not math.isfinite(missing_jump_threshold) or missing_jump_threshold <= 0.0:
        raise ContractViolationError(
            "missing_jump_threshold must be a finite positive float."
        )

    finite_psis: dict[str, float] = {}
    finite_deltas: dict[str, float] = {}
    non_finite: set[str] = set()

    for feat, raw_psi in feature_psi.items():
        if raw_psi is None:
            continue
        val = _finite_float(raw_psi)
        if val is None:
            non_finite.add(str(feat))
        else:
            finite_psis[str(feat)] = val

    for feat, raw_delta in missing_rate_delta.items():
        if raw_delta is None:
            continue
        val = _finite_float(raw_delta)
        if val is None:
            non_finite.add(str(feat))
        else:
            finite_deltas[str(feat)] = val

    max_psi = max(finite_psis.values()) if finite_psis else None
    max_missing_jump = max(finite_deltas.values()) if finite_deltas else None
    breached_psi = tuple(
        sorted(k for k, v in finite_psis.items() if v >= psi_breach_threshold)
    )
    breached_missing = tuple(
        sorted(k for k, v in finite_deltas.items() if v >= missing_jump_threshold)
    )

    if tt99_sample_count < min_shadow_samples:
        verdict = TT99GateVerdict.INSUFFICIENT_TT99_SAMPLES
        summary_vi = (
            f"Chưa đủ mẫu BCTC TT99/2025 ({tt99_sample_count}/{min_shadow_samples} tối thiểu): "
            "giữ chế độ giám sát song song (Shadow), không tự động nâng cấp lên GA."
        )
    elif breached_psi or breached_missing:
        verdict = TT99GateVerdict.DRIFT_BREACH
        summary_vi = (
            f"Phát hiện dịch chuyển phân bố TT200→TT99 vượt ngưỡng MRM "
            f"(max PSI={max_psi if max_psi is not None else 'N/A'}, "
            f"max thiếu hụt={max_missing_jump if max_missing_jump is not None else 'N/A'}): "
            "chặn tự động nâng cấp GA."
        )
    elif tt99_sample_count < min_ga_samples:
        verdict = TT99GateVerdict.SHADOW_MONITORING
        summary_vi = (
            f"Đang theo dõi Shadow TT99/2025 ({tt99_sample_count}/{min_ga_samples} mẫu đạt chuẩn GA): "
            "biến động PSI và tỷ lệ thiếu dữ liệu hiện trong ngưỡng kiểm soát."
        )
    else:
        verdict = TT99GateVerdict.READY_FOR_GA
        summary_vi = (
            f"Đạt chuẩn kiểm định MRM TT99/2025 ({tt99_sample_count} mẫu, "
            f"max PSI < {psi_breach_threshold}, mức tăng thiếu hụt < {missing_jump_threshold}): "
            "đủ điều kiện vận hành chính thức."
        )

    return TT99MrmGateReport(
        verdict=verdict,
        shadow_mode_active=(verdict != TT99GateVerdict.READY_FOR_GA),
        auto_promote_allowed=(verdict == TT99GateVerdict.READY_FOR_GA),
        tt99_sample_count=int(tt99_sample_count),
        min_shadow_samples=int(min_shadow_samples),
        min_ga_samples=int(min_ga_samples),
        psi_breach_threshold=float(psi_breach_threshold),
        missing_jump_threshold=float(missing_jump_threshold),
        max_feature_psi=max_psi,
        max_missing_jump=max_missing_jump,
        breached_psi_features=breached_psi,
        breached_missing_features=breached_missing,
        non_finite_features=tuple(sorted(non_finite)),
        summary_vi=summary_vi,
    )


def evaluate_tt99_gate_from_psi_report(psi_report: Mapping[str, Any]) -> dict[str, Any]:
    """Build serialized TT99MrmGateReport from raw or formatted ``taxonomy_drift_psi``."""
    raw_target = psi_report.get("target_rows")
    tt99_count = (
        int(raw_target)
        if isinstance(raw_target, (int, float)) and raw_target >= 0
        else 0
    )
    feature_psi: dict[str, float | None] = {}
    missing_delta: dict[str, float | None] = {}

    raw_drift = psi_report.get("feature_drift")
    if isinstance(raw_drift, Mapping):
        for fname, fdata in raw_drift.items():
            if isinstance(fdata, Mapping):
                feature_psi[str(fname)] = _finite_float(fdata.get("psi"))
                missing_delta[str(fname)] = _finite_float(
                    fdata.get("missing_rate_delta")
                )
    elif isinstance(raw_drift, list):
        for entry in raw_drift:
            if isinstance(entry, Mapping) and entry.get("feature"):
                fname = str(entry["feature"])
                feature_psi[fname] = _finite_float(entry.get("psi"))
                missing_delta[fname] = _finite_float(entry.get("missing_rate_delta"))

    if not feature_psi and psi_report.get("max_feature_psi") is not None:
        feature_psi["__summary__"] = _finite_float(psi_report.get("max_feature_psi"))

    return evaluate_tt99_mrm_gate(
        tt99_sample_count=tt99_count,
        feature_psi=feature_psi,
        missing_rate_delta=missing_delta,
    ).to_dict()
