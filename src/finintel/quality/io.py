"""Curated batch I/O helpers for Layer 6 quality gates.

Keeps all Polars I/O inside ``finintel.quality`` so Layer 0 pipeline handlers
remain pure orchestration wrappers without DataFrame manipulation.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.quality.gate import (
    QualityGateResult,
    evaluate_quality_gate,
    verify_analytics_quality_gate,
)
from finintel.quality.rules import QualityGateConfig
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)

CLEAN_FACTS_TABLE = "clean_financial_facts.parquet"
QUALITY_META_FILE = "_quality_meta.json"


def config_from_mapping(raw: Mapping[str, object]) -> QualityGateConfig:
    """Parse optional quality gate thresholds from ``settings.yaml``."""
    defaults = QualityGateConfig()
    if not raw:
        return defaults
    allowed = {
        "bs_identity_rel_tol",
        "subtotal_rel_tol",
        "unit_scale_upper_factor",
        "unit_scale_lower_factor",
        "cash_flow_rel_tol",
        "max_critical_issuer_ratio",
        "required_metrics",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ContractViolationError(
            f"Unsupported quality gate settings: {', '.join(sorted(unknown))}."
        )

    def _float(key: str, default: float) -> float:
        val = raw.get(key, default)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ContractViolationError(f"Quality setting {key} must be numeric.")
        return float(val)

    req_raw = raw.get("required_metrics", defaults.required_metrics)
    if not isinstance(req_raw, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in req_raw
    ):
        raise ContractViolationError(
            "Quality setting required_metrics must be a list of metric IDs."
        )
    return QualityGateConfig(
        bs_identity_rel_tol=_float("bs_identity_rel_tol", defaults.bs_identity_rel_tol),
        subtotal_rel_tol=_float("subtotal_rel_tol", defaults.subtotal_rel_tol),
        unit_scale_upper_factor=_float(
            "unit_scale_upper_factor", defaults.unit_scale_upper_factor
        ),
        unit_scale_lower_factor=_float(
            "unit_scale_lower_factor", defaults.unit_scale_lower_factor
        ),
        cash_flow_rel_tol=_float("cash_flow_rel_tol", defaults.cash_flow_rel_tol),
        max_critical_issuer_ratio=_float(
            "max_critical_issuer_ratio", defaults.max_critical_issuer_ratio
        ),
        required_metrics=tuple(req_raw),
    )


def _write_quarantine_forensics(destination: Path, quarantine: pl.DataFrame) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    quarantine.write_parquet(destination / AUDIT_QUARANTINE_TABLE)


def run_quality_gate_1_batch(
    *,
    facts_path: Path,
    issuers_path: Path | None,
    output_dir: Path,
    run_id: str,
    config: QualityGateConfig | None = None,
) -> QualityGateResult:
    """Run Layer 6 QUALITY_GATE_1 on a normalized Parquet batch and persist outputs atomically."""
    try:
        facts = pl.read_parquet(facts_path)
        issuers = (
            pl.read_parquet(issuers_path)
            if issuers_path is not None and issuers_path.is_file()
            else None
        )
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ContractViolationError(
            "Quality gate 1 requires readable financial_facts and issuers Parquet files."
        ) from exc

    output_dir.parent.mkdir(parents=True, exist_ok=True)

    def _persist_on_trip(quarantine: pl.DataFrame) -> None:
        # Preserve forensic quarantine records on disk even when the circuit breaker trips.
        _write_quarantine_forensics(output_dir, quarantine)

    result = evaluate_quality_gate(
        facts,
        issuers=issuers,
        config=config,
        on_quarantine=_persist_on_trip,
        run_id=run_id,
    )

    with TemporaryDirectory(prefix=".quality-", dir=output_dir.parent) as temporary:
        staging = Path(temporary) / "batch"
        staging.mkdir()
        result.clean_facts.write_parquet(staging / CLEAN_FACTS_TABLE)
        result.quality_summary.write_parquet(staging / QUALITY_SUMMARY_TABLE)
        result.audit_quarantine.write_parquet(staging / AUDIT_QUARANTINE_TABLE)
        (staging / QUALITY_META_FILE).write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "facts_path": str(facts_path),
                    "total_issuers": result.total_issuers,
                    "critical_issuers": result.critical_issuers,
                    "critical_issuer_ratio": result.critical_issuer_ratio,
                    "quarantined_filing_ids": list(result.quarantined_filing_ids),
                    "checked_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.rename(output_dir)

    return result


def run_quality_gate_2_batch(
    *,
    clean_facts_path: Path,
    analytics_dir: Path,
    quality_dir: Path,
    run_id: str,
) -> Path:
    """Run Layer 6 QUALITY_GATE_2 validating Layer 4 outputs and zero-orphan lineage."""
    ratios_path = analytics_dir / RATIOS_TABLE
    if not ratios_path.is_file():
        raise ContractViolationError(
            "Quality gate 2 requires financial_ratios.parquet from the ANALYTICS stage."
        )
    try:
        clean_facts = pl.read_parquet(clean_facts_path)
        ratios = pl.read_parquet(ratios_path)
        benchmarks = (
            pl.read_parquet(analytics_dir / BENCHMARKS_TABLE)
            if (analytics_dir / BENCHMARKS_TABLE).is_file()
            else None
        )
        red_flags = (
            pl.read_parquet(analytics_dir / RED_FLAGS_TABLE)
            if (analytics_dir / RED_FLAGS_TABLE).is_file()
            else None
        )
        ml_scores = (
            pl.read_parquet(analytics_dir / ML_SCORES_TABLE)
            if (analytics_dir / ML_SCORES_TABLE).is_file()
            else None
        )
        summary_path = quality_dir / QUALITY_SUMMARY_TABLE
        existing_summary = (
            pl.read_parquet(summary_path) if summary_path.is_file() else None
        )
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ContractViolationError(
            "Quality gate 2 encountered unreadable curated Parquet inputs."
        ) from exc

    updated_summary = verify_analytics_quality_gate(
        clean_facts,
        ratios,
        benchmarks=benchmarks,
        red_flags=red_flags,
        ml_scores=ml_scores,
        quality_summary=existing_summary,
        run_id=run_id,
    )

    quality_dir.mkdir(parents=True, exist_ok=True)
    target = quality_dir / QUALITY_SUMMARY_TABLE
    with TemporaryDirectory(prefix=".qg2-", dir=quality_dir) as temporary:
        staged = Path(temporary) / QUALITY_SUMMARY_TABLE
        updated_summary.write_parquet(staged)
        staged.replace(target)
    return target
