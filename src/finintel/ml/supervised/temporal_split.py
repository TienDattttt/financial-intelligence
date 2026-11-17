"""Leakage-safe temporal splits for four-quarter distress labels.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, section 5.3.2.
    Every pre-validation or pre-test training label must have become
    available by that boundary, independent of its target quarter.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from finintel.exceptions import ContractViolationError


def purged_walk_forward_split(
    samples: pl.DataFrame,
    *,
    validation_start: date,
    test_start: date,
) -> dict[str, pl.DataFrame]:
    """Split by observation time and purge labels unavailable at each boundary.

    Training rows precede validation and have labels known by validation start.
    Validation rows lie before test start and have labels known by test start.
    Test rows begin at test start; their future labels may become available later.
    Null targets are excluded from all model-fitting/evaluation folds. This does
    not let delayed reports leak across a boundary: label availability is tested
    independently for each row, which may purge more than four calendar quarters.
    """
    required = {"period_end_date", "label_available_at", "distress_target"}
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
    labeled = pl.col("distress_target").is_not_null()
    train = samples.filter(
        labeled
        & (observation < validation_start)
        & label_time.is_not_null()
        & (label_time <= validation_start)
    )
    validation = samples.filter(
        labeled
        & (observation >= validation_start)
        & (observation < test_start)
        & label_time.is_not_null()
        & (label_time <= test_start)
    )
    test = samples.filter(labeled & (observation >= test_start))
    return {"train": train, "validation": validation, "test": test}
