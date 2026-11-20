"""Leakage-safe temporal splits and PIT-symmetric peer Z-scores for distress labels.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 2.1 (Guardrail G1), 4.4 and 5.3.2.
    Shumway, T. (2001). "Forecasting Bankruptcy More Accurately: A Simple Hazard Model."
        The Journal of Business, Vol. 74, No. 1, pp. 101-124.
    Falkenstein, E., Boral, A., & Carty, L. V. (2000). "RiskCalc For Private Companies:
        Moody's Default Model." Moody's Investors Service Global Credit Research.
    Every pre-validation or pre-test training label must have become available by
    that boundary, independent of its target quarter, pre-boundary features must
    not absorb post-boundary filing restatements, and peer Z-scores must be
    computed strictly within each fold's PIT cutoff with symmetric cohort logic.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.target import build_distress_targets

PeerStandardizationMode = Literal["PIT_ICB", "PIT_REGIME_SYMMETRIC"]
_PIT_ICB_COVERAGE_THRESHOLD = 0.80
SECTOR_RAW_TO_Z: dict[str, str] = {
    "cash_conversion_cycle": "sector_z_cash_conversion_cycle",
    "asset_turnover": "sector_z_asset_turnover",
    "debt_to_equity": "sector_z_debt_to_equity",
    "ebit_margin": "sector_z_ebit_margin",
}
SECTOR_Z_RAW_METRICS: tuple[str, ...] = tuple(SECTOR_RAW_TO_Z.keys())


def _pit_icb_coverage(frame: pl.DataFrame) -> float:
    """Compute fraction of rows with PIT-visible ICB Level 2 or Level 3 classification."""
    if frame.is_empty():
        return 0.0
    exprs: list[pl.Expr] = []
    for col_name in ("icb_code_level2", "icb_code_level3"):
        if col_name in frame.columns:
            exprs.append(
                pl.col(col_name).is_not_null()
                & (pl.col(col_name).cast(pl.String).str.strip_chars() != "")
            )
    if not exprs:
        return 0.0
    visible = pl.any_horizontal(*exprs)
    mean_val = frame.select(visible.cast(pl.Float64).mean()).item()
    return float(mean_val) if mean_val is not None else 0.0


def resolve_peer_standardization_mode(
    train: pl.DataFrame,
    validation: pl.DataFrame,
) -> PeerStandardizationMode:
    """Select PIT_ICB when PIT-visible ICB coverage >= 80% on both train & validation, else PIT_REGIME_SYMMETRIC."""
    train_cov = _pit_icb_coverage(train)
    valid_cov = _pit_icb_coverage(validation)
    if (
        train_cov >= _PIT_ICB_COVERAGE_THRESHOLD
        and valid_cov >= _PIT_ICB_COVERAGE_THRESHOLD
    ):
        return "PIT_ICB"
    return "PIT_REGIME_SYMMETRIC"


def attach_pit_sector_z_features(
    frame: pl.DataFrame,
    *,
    mode: PeerStandardizationMode = "PIT_REGIME_SYMMETRIC",
) -> pl.DataFrame:
    """Compute the 4 sector_z_* features strictly within the provided PIT snapshot slice."""
    existing_z = [col for col in SECTOR_RAW_TO_Z.values() if col in frame.columns]
    base_frame = frame.drop(*existing_z) if existing_z else frame
    if base_frame.is_empty() or (set(SECTOR_Z_RAW_METRICS) - set(base_frame.columns)):
        return base_frame.with_columns(
            *(
                pl.lit(None, dtype=pl.Float64).alias(z_col)
                for z_col in SECTOR_RAW_TO_Z.values()
            )
        )

    if mode == "PIT_REGIME_SYMMETRIC":
        bench_input = base_frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("icb_code_level2"),
            pl.lit(None, dtype=pl.String).alias("icb_code_level3"),
        )
    else:
        bench_input = base_frame.with_columns(
            *(
                pl.col(col_name).cast(pl.String)
                if col_name in base_frame.columns
                else pl.lit(None, dtype=pl.String).alias(col_name)
                for col_name in ("icb_code_level2", "icb_code_level3")
            )
        )

    benchmarks = benchmark_ratios(bench_input, metrics=SECTOR_Z_RAW_METRICS)
    join_keys = [
        "issuer_id",
        "accounting_regime",
        "statement_scope",
        "period_type",
        "period_end_date",
    ]
    result = base_frame
    for raw_name, z_name in SECTOR_RAW_TO_Z.items():
        metric_slice = benchmarks.filter(
            pl.col("metric_or_ratio_name") == raw_name
        ).select(
            *join_keys,
            pl.col("robust_z_score").cast(pl.Float64).alias(z_name),
        )
        result = result.join(
            metric_slice,
            on=join_keys,
            how="left",
            validate="1:1",
        )
    return result


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
    target_mode: str = "FUNDAMENTAL_DISTRESS",
    regulatory_events: pl.DataFrame | None = None,
    precomputed_as_of_scored: pl.DataFrame | None = None,
) -> dict[str, pl.DataFrame]:
    """Construct fold-specific PIT feature/label snapshots without restatement or future-ICB leakage.

    Train features and labels are materialized strictly as of ``validation_start``;
    validation features and labels strictly as of ``test_start``; and test features
    and labels strictly as of ``as_of``. Peer standardization mode (``PIT_ICB`` vs
    ``PIT_REGIME_SYMMETRIC``) is resolved from PIT-visible ICB coverage on train
    and validation and applied symmetrically across all folds.
    """
    if validation_start >= test_start:
        raise ContractViolationError("Validation start must precede test start.")
    if test_start > as_of:
        raise ContractViolationError("Test start must not exceed the PIT as_of date.")

    def _materialize_at(
        cutoff: date, *, precomputed_scored: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        if precomputed_scored is not None:
            scored = precomputed_scored
        else:
            periods = prepare_periods(
                facts, issuers, as_of=cutoff, include_ml_inputs=True
            )
            scored = add_classical_scores(calculate_ratios(periods))
        scoped = scored.filter(pl.col("statement_scope") == statement_scope)
        if taxonomy_version is not None:
            scoped = scoped.filter(pl.col("taxonomy_version") == taxonomy_version)
        return build_distress_targets(
            scoped,
            mode=target_mode,
            as_of=cutoff,
            regulatory_events=regulatory_events,
        )

    train_raw = _materialize_at(validation_start)
    valid_raw = _materialize_at(test_start)
    test_raw = _materialize_at(as_of, precomputed_scored=precomputed_as_of_scored)

    pre_train = purged_walk_forward_split(
        train_raw,
        validation_start=validation_start,
        test_start=test_start,
        require_exact_filing=require_exact_filing,
    )["train"]
    pre_valid = purged_walk_forward_split(
        valid_raw,
        validation_start=validation_start,
        test_start=test_start,
        require_exact_filing=require_exact_filing,
    )["validation"]
    peer_mode = resolve_peer_standardization_mode(pre_train, pre_valid)

    train_snapshot = attach_pit_sector_z_features(train_raw, mode=peer_mode)
    valid_snapshot = attach_pit_sector_z_features(valid_raw, mode=peer_mode)
    test_snapshot = attach_pit_sector_z_features(test_raw, mode=peer_mode)

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
