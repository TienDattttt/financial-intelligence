"""Leakage-safe temporal splits for four-quarter distress labels.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 2.1 (Guardrail G1) and 5.3.2.
    Every pre-validation or pre-test training label must have become
    available by that boundary, independent of its target quarter, and
    pre-boundary features must not absorb post-boundary filing restatements.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.target import build_distress_targets


def purged_walk_forward_split(
    samples: pl.DataFrame,
    *,
    validation_start: date,
    test_start: date,
    require_exact_filing: bool = False,
) -> dict[str, pl.DataFrame]:
    """Split by observation time and purge labels unavailable at each boundary.

    Training rows precede validation and have labels known by validation start.
    Validation rows lie before test start and have labels known by test start.
    Test rows begin at test start; their future labels may become available later.
    Null targets are excluded from all model-fitting/evaluation folds. When
    ``require_exact_filing`` is enabled (Guardrail G1), both feature and label
    provenance must come exclusively from ``EXACT_FILING`` disclosures.
    """
    required = {
        "period_end_date",
        "label_available_at",
        "distress_target",
        "_period_available_at",
    }
    if require_exact_filing:
        required.add("_exact_filing_basis")
    missing = required - set(samples.columns)
    if missing:
        raise ContractViolationError(
            f"Temporal split input is missing columns: {sorted(missing)}"
        )
    if validation_start >= test_start:
        raise ContractViolationError("Validation start must precede test start.")
    if samples.filter(
        pl.col("distress_target").is_not_null() & pl.col("label_available_at").is_null()
    ).height:
        raise ContractViolationError(
            "Labeled samples require a non-null label_available_at."
        )
    observation = pl.col("period_end_date").cast(pl.Date)
    label_time = pl.col("label_available_at").cast(pl.Date)
    feature_time = pl.col("_period_available_at").cast(pl.Date)
    labeled = pl.col("distress_target").is_not_null()
    if require_exact_filing:
        exact_guard = pl.col("_exact_filing_basis").fill_null(False)
        if "_label_exact_filing_basis" in samples.columns:
            exact_guard = exact_guard & pl.col("_label_exact_filing_basis").fill_null(
                False
            )
        labeled = labeled & exact_guard

    train = samples.filter(
        labeled
        & (observation < validation_start)
        & label_time.is_not_null()
        & (label_time <= validation_start)
        & feature_time.is_not_null()
        & (feature_time <= validation_start)
    )
    validation = samples.filter(
        labeled
        & (observation >= validation_start)
        & (observation < test_start)
        & label_time.is_not_null()
        & (label_time <= test_start)
        & feature_time.is_not_null()
        & (feature_time <= test_start)
    )
    test = samples.filter(labeled & (observation >= test_start))
    return {"train": train, "validation": validation, "test": test}


def build_pit_ml_splits(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    validation_start: date,
    test_start: date,
    as_of: date,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str | None = "VAS_TT200_2014",
) -> dict[str, pl.DataFrame]:
    """Construct fold-specific PIT feature/label snapshots without restatement leakage.

    Train features and labels are materialized strictly as of ``validation_start``;
    validation features and labels strictly as of ``test_start``; and test features
    and labels strictly as of ``as_of``. Post-boundary restatements can never
    overwrite pre-boundary features or purge valid historical filings.
    """
    if validation_start >= test_start:
        raise ContractViolationError("Validation start must precede test start.")
    if test_start > as_of:
        raise ContractViolationError("Test start must not exceed the PIT as_of date.")

    def _materialize_at(cutoff: date) -> pl.DataFrame:
        periods = prepare_periods(facts, issuers, as_of=cutoff, include_ml_inputs=True)
        scored = add_classical_scores(calculate_ratios(periods))
        scoped = scored.filter(pl.col("statement_scope") == statement_scope)
        if taxonomy_version is not None:
            scoped = scoped.filter(pl.col("taxonomy_version") == taxonomy_version)
        return build_distress_targets(scoped, as_of=cutoff)

    train_snapshot = _materialize_at(validation_start)
    valid_snapshot = _materialize_at(test_start)
    test_snapshot = _materialize_at(as_of)

    train = purged_walk_forward_split(
        train_snapshot,
        validation_start=validation_start,
        test_start=test_start,
        require_exact_filing=require_exact_filing,
    )["train"]
    validation = purged_walk_forward_split(
        valid_snapshot,
        validation_start=validation_start,
        test_start=test_start,
        require_exact_filing=require_exact_filing,
    )["validation"]
    test = purged_walk_forward_split(
        test_snapshot,
        validation_start=validation_start,
        test_start=test_start,
        require_exact_filing=require_exact_filing,
    )["test"]
    return {"train": train, "validation": validation, "test": test}
