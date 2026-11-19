"""Blocking pre-publish quality, PK uniqueness, and DuPont 5/6-factor audits (Stream B2 Part 1).

Verified Sources:
    docs/v2_specs/07_STREAM_B2_SQLMESH_AND_DAGSTER_PLATFORM.md (§3.1.2).
    src/finintel/quality/gate.py (verify_analytics_quality_gate: zero-orphan filing_id
    enforcement + DERIVED_Q4_INPUT & NEGATIVE_ROE propagation into quality_summary).
    src/finintel/analytics/dupont.py (five-factor ROE_total and six-factor ROE_parent
    multiplicative telescoping identities, strict tolerance <= 1e-9).
    src/finintel/storage/schemas.py (ANALYTICS_KEYS, validate_analytics_table).
"""

from __future__ import annotations

import logging
import math

import polars as pl

from finintel.exceptions import ContractViolationError, CriticalDataQualityError
from finintel.quality.gate import verify_analytics_quality_gate
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    QUALITY_SUMMARY_TABLE,
    validate_analytics_table,
)
from finintel.transform.incremental import TransformBundle

logger = logging.getLogger("finintel.transform.audits")

DUPONT_TOLERANCE: float = 1e-9


def _check_dupont_identities(
    ratios: pl.DataFrame, *, dupont_tol: float, run_id: str
) -> None:
    """Verify 5-factor and 6-factor DuPont identities within <= dupont_tol on valid rows."""
    if ratios.is_empty():
        return

    valid_rows = ratios.filter(pl.col("dupont_valid_flag").fill_null(False))
    if valid_rows.is_empty():
        return

    c = pl.col
    product_5 = (
        c("tax_burden")
        * c("interest_burden")
        * c("ebit_margin")
        * c("asset_turnover")
        * c("equity_multiplier_total")
    )
    diff_5 = (product_5 - c("roe_total")).abs()
    invalid_5_expr = (
        ~product_5.is_finite() | ~c("roe_total").is_finite() | (diff_5 > dupont_tol)
    ).fill_null(True)

    has_6_factor = (
        c("parent_profit_share").is_not_null()
        & c("equity_multiplier_parent").is_not_null()
    )
    product_6 = (
        c("tax_burden")
        * c("interest_burden")
        * c("ebit_margin")
        * c("asset_turnover")
        * c("parent_profit_share")
        * c("equity_multiplier_parent")
    )
    diff_6 = (product_6 - c("roe_parent")).abs()
    invalid_6_expr = has_6_factor & (
        ~product_6.is_finite() | ~c("roe_parent").is_finite() | (diff_6 > dupont_tol)
    ).fill_null(True)

    evaluated = valid_rows.select(
        invalid_5_expr.alias("_bad_5"),
        diff_5.alias("_diff_5"),
        invalid_6_expr.alias("_bad_6"),
        pl.when(has_6_factor).then(diff_6).otherwise(None).alias("_diff_6"),
    )
    bad_5_count = int(evaluated["_bad_5"].sum())
    bad_6_count = int(evaluated["_bad_6"].sum())
    if bad_5_count == 0 and bad_6_count == 0:
        return

    max_diff_5_raw = evaluated.filter(pl.col("_bad_5"))["_diff_5"].max()
    max_diff_6_raw = evaluated.filter(pl.col("_bad_6"))["_diff_6"].max()
    logger.error(
        "Blocking DuPont identity audit failed",
        extra={
            "event": "dupont_identity_violation",
            "run_id": run_id,
            "stage": "QUALITY_GATE_2",
            "details": {
                "dupont_tol": dupont_tol,
                "five_factor_violations": bad_5_count,
                "six_factor_violations": bad_6_count,
                "max_five_factor_residual": (
                    float(max_diff_5_raw)
                    if isinstance(max_diff_5_raw, (int, float))
                    else None
                ),
                "max_six_factor_residual": (
                    float(max_diff_6_raw)
                    if isinstance(max_diff_6_raw, (int, float))
                    else None
                ),
            },
        },
    )
    raise CriticalDataQualityError(
        f"DuPont identity audit failed (tolerance={dupont_tol:g}): "
        f"{bad_5_count} five-factor violation(s), {bad_6_count} six-factor violation(s)."
    )


def run_blocking_publish_audits(
    bundle: TransformBundle,
    *,
    run_id: str,
    dupont_tol: float = DUPONT_TOLERANCE,
) -> pl.DataFrame:
    """Execute all blocking pre-publish audits before any snapshot is published.

    Checks:
      1. Schema and PK uniqueness (``ANALYTICS_KEYS``) across ratios, benchmarks,
         red_flags, ml_scores, quality_summary, and audit_quarantine.
      2. Zero orphan ``filing_id`` references across ratios (including ``source_filing_ids``),
         red_flags, and ml_scores via ``verify_analytics_quality_gate``.
      3. Preserves ``DERIVED_Q4_INPUT`` and ``NEGATIVE_ROE`` propagation into
         ``quality_summary``.
      4. Five-factor AND six-factor DuPont identities within ``<= dupont_tol``
         (default ``1e-9``) on all rows with ``dupont_valid_flag == True``.

    Any violation raises ``CriticalDataQualityError`` before publication.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ContractViolationError("run_id must be a nonempty string.")
    if (
        isinstance(dupont_tol, bool)
        or not isinstance(dupont_tol, (int, float))
        or not math.isfinite(float(dupont_tol))
        or float(dupont_tol) < 0.0
    ):
        raise ContractViolationError("dupont_tol must be a finite non-negative float.")

    try:
        validate_analytics_table(QUALITY_SUMMARY_TABLE, bundle.quality_summary)
        validate_analytics_table(AUDIT_QUARANTINE_TABLE, bundle.audit_quarantine)
        updated_summary = verify_analytics_quality_gate(
            bundle.clean_facts,
            bundle.financial_ratios,
            benchmarks=bundle.industry_benchmarks,
            red_flags=bundle.accounting_red_flags,
            ml_scores=bundle.ml_scores,
            quality_summary=bundle.quality_summary,
            run_id=run_id,
        )
        validate_analytics_table(QUALITY_SUMMARY_TABLE, updated_summary)
    except ContractViolationError as exc:
        logger.error(
            "Blocking pre-publish schema or PK audit failed",
            extra={
                "event": "blocking_publish_audit_schema_pk_failed",
                "run_id": run_id,
                "stage": "QUALITY_GATE_2",
            },
        )
        raise CriticalDataQualityError(
            f"Blocking pre-publish audit failed schema/PK validation: {exc}"
        ) from exc

    _check_dupont_identities(
        bundle.financial_ratios,
        dupont_tol=float(dupont_tol),
        run_id=run_id,
    )

    logger.info(
        "Blocking pre-publish audits passed",
        extra={
            "event": "blocking_publish_audits_passed",
            "run_id": run_id,
            "stage": "QUALITY_GATE_2",
            "details": {
                "ratios_rows": bundle.financial_ratios.height,
                "benchmarks_rows": bundle.industry_benchmarks.height,
                "red_flags_rows": bundle.accounting_red_flags.height,
                "ml_scores_rows": bundle.ml_scores.height,
                "quality_summary_rows": updated_summary.height,
                "dupont_tol": float(dupont_tol),
            },
        },
    )
    return updated_summary
