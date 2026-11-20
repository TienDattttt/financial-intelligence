"""Blocking Dagster asset checks for QUALITY_GATE_1 and QUALITY_GATE_2.

Verified Sources:
    - Dagster Official Documentation — Asset Checks (``blocking=True``):
      https://docs.dagster.io/guides/test/asset-checks
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``@dg.asset_check(*, asset, name=None, description=None, blocking=False, ...)``
      - ``dg.AssetCheckResult(*, passed: bool, asset_key=None, check_name=None, metadata=None,
        severity=AssetCheckSeverity.ERROR, description=None)``
    - Internal single-source-of-truth audit contract:
      ``finintel.transform.run_blocking_publish_audits`` and ``finintel.quality.gate``.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

import dagster as dg
import polars as pl

from finintel.exceptions import ContractViolationError, CriticalDataQualityError
from finintel.orchestration.assets import published_financial_marts
from finintel.orchestration.resources import (
    FinIntelPathsResource,
    StorageResource,
)
from finintel.quality.rules import QualityGateConfig
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    OPTIONAL_ANALYTICS_SCHEMAS,
    QUALITY_SUMMARY_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.transform import (
    DUPONT_TOLERANCE,
    TransformBundle,
    TransformDiff,
    run_blocking_publish_audits,
)


def _max_critical_ratio_from_settings(paths: FinIntelPathsResource) -> float:
    settings = paths.load_settings_dict()
    quality_cfg = settings.get("quality")
    if isinstance(quality_cfg, Mapping):
        raw = quality_cfg.get("max_critical_issuer_ratio")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    return QualityGateConfig().max_critical_issuer_ratio


@dg.asset_check(
    asset=published_financial_marts,
    name="quality_gate_1_critical_ratio",
    blocking=True,
    description=(
        "Blocking QUALITY_GATE_1 check verifying active snapshot critical issuer "
        "quarantine ratio stays within the configured batch circuit-breaker threshold (<= 10%)."
    ),
)
def check_quality_gate_1_critical_ratio(
    paths: FinIntelPathsResource,
    storage: StorageResource,
) -> dg.AssetCheckResult:
    """Verify published snapshot critical-issuer ratio <= threshold (default 10%)."""
    threshold = _max_critical_ratio_from_settings(paths)
    repo = storage.build_repository(paths)
    try:
        with repo.snapshot_session() as pinned:
            snapshot_id = pinned.snapshot_id or ""
            facts = pinned.read_table("financial_facts.parquet")
            try:
                quarantine = pinned.read_table(AUDIT_QUARANTINE_TABLE)
            except (FileNotFoundError, ContractViolationError):
                quarantine = pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA)
            try:
                quality_summary = pinned.read_table(QUALITY_SUMMARY_TABLE)
            except (FileNotFoundError, ContractViolationError):
                quality_summary = pl.DataFrame(schema=QUALITY_SUMMARY_SCHEMA)
    except (FileNotFoundError, ContractViolationError) as exc:
        return dg.AssetCheckResult(
            passed=False,
            description=f"Unable to read active snapshot for QUALITY_GATE_1: {exc}",
            metadata={"threshold": threshold},
        )

    clean_ids = set(facts["issuer_id"].drop_nulls().cast(pl.String).to_list())
    critical_ids = set(quarantine["issuer_id"].drop_nulls().cast(pl.String).to_list())
    total_issuers = len(clean_ids | critical_ids)
    critical_issuers = len(critical_ids)
    critical_ratio = (
        float(critical_issuers / total_issuers) if total_issuers > 0 else 1.0
    )
    passed = not facts.is_empty() and total_issuers > 0 and critical_ratio <= threshold

    return dg.AssetCheckResult(
        passed=passed,
        description=(
            f"QUALITY_GATE_1: {critical_issuers}/{total_issuers} critical issuers "
            f"({critical_ratio:.2%}) vs threshold {threshold:.2%}."
        ),
        metadata={
            "snapshot_id": snapshot_id,
            "total_issuers": total_issuers,
            "critical_issuers": critical_issuers,
            "critical_issuer_ratio": round(critical_ratio, 6),
            "threshold": threshold,
            "quarantined_rows": quarantine.height,
            "quality_summary_rows": quality_summary.height,
        },
    )


@dg.asset_check(
    asset=published_financial_marts,
    name="quality_gate_2_lineage_and_dupont",
    blocking=True,
    description=(
        "Blocking QUALITY_GATE_2 check verifying zero orphan filing_ids, primary-key "
        "uniqueness, and DuPont 5-factor & 6-factor identity residual <= 1e-9 via "
        "finintel.transform.run_blocking_publish_audits()."
    ),
)
def check_quality_gate_2_lineage_and_dupont(
    context: dg.AssetCheckExecutionContext,
    paths: FinIntelPathsResource,
    storage: StorageResource,
) -> dg.AssetCheckResult:
    """Verify persisted snapshot passes blocking lineage, PK, and DuPont 5/6-factor audits."""
    repo = storage.build_repository(paths)
    try:
        with repo.snapshot_session() as pinned:
            snapshot_id = pinned.snapshot_id or ""
            issuers = pinned.read_table("issuers.parquet")
            clean_facts = pinned.read_table("financial_facts.parquet")
            ratios = pinned.read_table(RATIOS_TABLE)
            try:
                benchmarks = pinned.read_table(BENCHMARKS_TABLE)
            except (FileNotFoundError, ContractViolationError):
                benchmarks = pl.DataFrame(
                    schema=OPTIONAL_ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]
                )
            try:
                red_flags = pinned.read_table(RED_FLAGS_TABLE)
            except (FileNotFoundError, ContractViolationError):
                red_flags = pl.DataFrame(
                    schema=OPTIONAL_ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]
                )
            try:
                ml_scores = pinned.read_table(ML_SCORES_TABLE)
            except (FileNotFoundError, ContractViolationError):
                ml_scores = pl.DataFrame(schema=ML_SCORES_SCHEMA)
            try:
                quality_summary = pinned.read_table(QUALITY_SUMMARY_TABLE)
            except (FileNotFoundError, ContractViolationError):
                quality_summary = pl.DataFrame(schema=QUALITY_SUMMARY_SCHEMA)
            try:
                quarantine = pinned.read_table(AUDIT_QUARANTINE_TABLE)
            except (FileNotFoundError, ContractViolationError):
                quarantine = pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA)
    except (FileNotFoundError, ContractViolationError) as exc:
        return dg.AssetCheckResult(
            passed=False,
            description=f"Unable to read active snapshot for QUALITY_GATE_2: {exc}",
            metadata={"dupont_tolerance": DUPONT_TOLERANCE},
        )

    clean_ids = set(clean_facts["issuer_id"].drop_nulls().cast(pl.String).to_list())
    critical_ids = set(quarantine["issuer_id"].drop_nulls().cast(pl.String).to_list())
    total_issuers = len(clean_ids | critical_ids)
    critical_issuers = len(critical_ids)
    critical_ratio = (
        float(critical_issuers / total_issuers) if total_issuers > 0 else 0.0
    )

    placeholder_diff = TransformDiff(
        full_rebuild=False,
        benchmarks_full_rebuild=False,
        added_issuers=(),
        modified_issuers=(),
        deleted_issuers=(),
        dirty_issuers=(),
        dirty_slices=(),
        issuers_metadata_changed=False,
        transform_config_changed=False,
        facts_sha256="",
        issuers_metadata_sha256="",
        transform_config_sha256="",
    )
    check_run_id = f"check_{context.run.run_id}"
    bundle = TransformBundle(
        as_of=datetime.now(UTC).date(),
        run_id=check_run_id,
        issuers=issuers,
        clean_facts=clean_facts,
        financial_ratios=ratios,
        industry_benchmarks=benchmarks,
        accounting_red_flags=red_flags,
        ml_scores=ml_scores,
        quality_summary=quality_summary,
        audit_quarantine=quarantine,
        total_issuers=total_issuers,
        critical_issuers=critical_issuers,
        critical_issuer_ratio=critical_ratio,
        quarantined_filing_ids=tuple(
            sorted(
                quarantine["filing_id"].drop_nulls().cast(pl.String).unique().to_list()
            )
        ),
        diff=placeholder_diff,
        next_transform_state={},
    )

    try:
        verified_summary = run_blocking_publish_audits(
            bundle,
            run_id=check_run_id,
            dupont_tol=DUPONT_TOLERANCE,
        )
    except (CriticalDataQualityError, ContractViolationError) as exc:
        return dg.AssetCheckResult(
            passed=False,
            description=f"QUALITY_GATE_2 failed: {exc}",
            metadata={
                "snapshot_id": snapshot_id,
                "ratios_rows": ratios.height,
                "dupont_tolerance": DUPONT_TOLERANCE,
            },
        )

    dupont_valid_rows = (
        int(ratios.filter(pl.col("dupont_valid_flag") == True).height)  # noqa: E712
        if "dupont_valid_flag" in ratios.columns
        else 0
    )
    return dg.AssetCheckResult(
        passed=True,
        description=(
            f"QUALITY_GATE_2 passed: 0 orphan filing_ids, valid PKs, and "
            f"{dupont_valid_rows} DuPont rows within tolerance <= {DUPONT_TOLERANCE:.0e}."
        ),
        metadata={
            "snapshot_id": snapshot_id,
            "ratios_rows": ratios.height,
            "benchmarks_rows": benchmarks.height,
            "red_flags_rows": red_flags.height,
            "ml_scores_rows": ml_scores.height,
            "quality_summary_rows": verified_summary.height,
            "dupont_valid_rows": dupont_valid_rows,
            "orphan_filing_ids": 0,
            "dupont_tolerance": DUPONT_TOLERANCE,
        },
    )


ALL_ASSET_CHECKS = [
    check_quality_gate_1_critical_ratio,
    check_quality_gate_2_lineage_and_dupont,
]
