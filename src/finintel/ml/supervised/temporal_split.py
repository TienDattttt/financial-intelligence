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

from collections.abc import Sequence
from datetime import date
from typing import Literal

import numpy as np
import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.merton_dtd import attach_merton_dtd_to_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.target import build_distress_targets, resolve_target_mode

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
    validation: pl.DataFrame | None = None,
) -> PeerStandardizationMode:
    """Select PIT_ICB when PIT-visible ICB coverage >= 80% on train (and validation if provided)."""
    train_cov = _pit_icb_coverage(train)
    if validation is None:
        return (
            "PIT_ICB"
            if train_cov >= _PIT_ICB_COVERAGE_THRESHOLD
            else "PIT_REGIME_SYMMETRIC"
        )
    valid_cov = _pit_icb_coverage(validation)
    if (
        train_cov >= _PIT_ICB_COVERAGE_THRESHOLD
        and valid_cov >= _PIT_ICB_COVERAGE_THRESHOLD
    ):
        return "PIT_ICB"
    return "PIT_REGIME_SYMMETRIC"


def _compute_pit_sector_z_by_availability(
    base_frame: pl.DataFrame,
    *,
    mode: PeerStandardizationMode,
) -> pl.DataFrame:
    """Compute ``sector_z_*`` using only peers with ``_period_available_at <= row._period_available_at`` (P2-10)."""
    cohort_cols = [
        col
        for col in (
            "accounting_regime",
            "statement_scope",
            "period_type",
            "period_end_date",
            "fiscal_year",
            "fiscal_quarter",
        )
        if col in base_frame.columns
    ]
    n_rows = base_frame.height
    z_arrays: dict[str, list[float | None]] = {
        z_name: [None] * n_rows for z_name in SECTOR_RAW_TO_Z.values()
    }
    icb3_col = (
        base_frame["icb_code_level3"].cast(pl.String).to_list()
        if mode == "PIT_ICB" and "icb_code_level3" in base_frame.columns
        else [None] * n_rows
    )
    icb2_col = (
        base_frame["icb_code_level2"].cast(pl.String).to_list()
        if mode == "PIT_ICB" and "icb_code_level2" in base_frame.columns
        else [None] * n_rows
    )

    staged = base_frame.with_row_index("_row_idx")
    raw_cols = {
        raw_name: [
            float(v)
            if v is not None and isinstance(v, (int, float)) and np.isfinite(float(v))
            else None
            for v in base_frame[raw_name].to_list()
        ]
        for raw_name in SECTOR_Z_RAW_METRICS
    }

    valid_avail = staged.filter(pl.col("_period_available_at").is_not_null())
    partitions = (
        valid_avail.partition_by(cohort_cols, maintain_order=False)
        if cohort_cols
        else [valid_avail]
    )

    def _z_from_pool(val: float, pool: list[float]) -> float:
        arr = np.asarray(pool, dtype=np.float64)
        q25, med, q75 = np.quantile(arr, [0.25, 0.50, 0.75], method="linear")
        scale = max(float(q75 - q25) * 0.7413011, 1e-6)
        return float(np.clip((val - float(med)) / scale, -5.0, 5.0))

    for sub in partitions:
        sub_sorted = sub.sort(["_period_available_at", "_row_idx"])
        dates_list = sub_sorted["_period_available_at"].to_list()
        idxs_list = sub_sorted["_row_idx"].to_list()
        m_len = len(idxs_list)
        for raw_name, z_name in SECTOR_RAW_TO_Z.items():
            vals_for_metric = raw_cols[raw_name]
            pool_all: list[float] = []
            pool_icb2: dict[str, list[float]] = {}
            pool_icb3: dict[str, list[float]] = {}
            pos = 0
            while pos < m_len:
                cur_d = dates_list[pos]
                end = pos
                while end < m_len and dates_list[end] == cur_d:
                    r_idx = int(idxs_list[end])
                    v = vals_for_metric[r_idx]
                    if v is not None:
                        pool_all.append(v)
                        if mode == "PIT_ICB":
                            c3 = icb3_col[r_idx]
                            if isinstance(c3, str) and c3.strip():
                                pool_icb3.setdefault(c3, []).append(v)
                            c2 = icb2_col[r_idx]
                            if isinstance(c2, str) and c2.strip():
                                pool_icb2.setdefault(c2, []).append(v)
                    end += 1

                if pool_all:
                    all_arr = np.asarray(pool_all, dtype=np.float64)
                    q25_all, med_all, q75_all = np.quantile(
                        all_arr, [0.25, 0.50, 0.75], method="linear"
                    )
                    scale_all = max(float(q75_all - q25_all) * 0.7413011, 1e-6)
                    med_all_f = float(med_all)
                    icb_cache: dict[tuple[int, str], tuple[float, float]] = {}

                    for k in range(pos, end):
                        r_idx = int(idxs_list[k])
                        v = vals_for_metric[r_idx]
                        if v is None:
                            continue
                        if mode == "PIT_ICB":
                            c3 = icb3_col[r_idx]
                            if (
                                isinstance(c3, str)
                                and c3.strip()
                                and len(pool_icb3.get(c3, ())) >= 8
                            ):
                                if (3, c3) not in icb_cache:
                                    arr3 = np.asarray(pool_icb3[c3], dtype=np.float64)
                                    p25, m3, p75 = np.quantile(
                                        arr3, [0.25, 0.50, 0.75], method="linear"
                                    )
                                    icb_cache[(3, c3)] = (
                                        float(m3),
                                        max(float(p75 - p25) * 0.7413011, 1e-6),
                                    )
                                m_u, s_u = icb_cache[(3, c3)]
                                z_arrays[z_name][r_idx] = float(
                                    np.clip((v - m_u) / s_u, -5.0, 5.0)
                                )
                                continue
                            c2 = icb2_col[r_idx]
                            if (
                                isinstance(c2, str)
                                and c2.strip()
                                and len(pool_icb2.get(c2, ())) >= 5
                            ):
                                if (2, c2) not in icb_cache:
                                    arr2 = np.asarray(pool_icb2[c2], dtype=np.float64)
                                    p25, m2, p75 = np.quantile(
                                        arr2, [0.25, 0.50, 0.75], method="linear"
                                    )
                                    icb_cache[(2, c2)] = (
                                        float(m2),
                                        max(float(p75 - p25) * 0.7413011, 1e-6),
                                    )
                                m_u, s_u = icb_cache[(2, c2)]
                                z_arrays[z_name][r_idx] = float(
                                    np.clip((v - m_u) / s_u, -5.0, 5.0)
                                )
                                continue
                        z_arrays[z_name][r_idx] = float(
                            np.clip((v - med_all_f) / scale_all, -5.0, 5.0)
                        )
                pos = end

    return base_frame.with_columns(
        *(
            pl.Series(z_name, z_arrays[z_name], dtype=pl.Float64)
            for z_name in SECTOR_RAW_TO_Z.values()
        )
    )


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

    if (
        "_period_available_at" in base_frame.columns
        and base_frame["_period_available_at"].null_count() < base_frame.height
        and {"accounting_regime", "statement_scope", "period_type", "period_end_date"}
        <= set(base_frame.columns)
    ):
        return _compute_pit_sector_z_by_availability(base_frame, mode=mode)

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


def purged_two_way_split(
    samples: pl.DataFrame,
    *,
    test_start: date,
    test_end: date | None = None,
    require_exact_filing: bool = False,
) -> dict[str, pl.DataFrame]:
    """Split into 2-way (Train_OOF -> Test) purging labels unavailable by ``test_start`` (B-02).

    Training rows precede ``test_start`` and satisfy BOTH ``_period_available_at <= test_start``
    and ``label_available_at <= test_start`` (under Mode B, symmetrically ``a_t + 365d <= test_start``
    for both ``y = 1`` and ``y = 0``). Test rows satisfy ``period_end_date >= test_start``
    (and ``< test_end`` when ``test_end`` is provided).
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
    if test_end is not None and test_start >= test_end:
        raise ContractViolationError("test_start must precede test_end.")
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
        & (observation < test_start)
        & label_time.is_not_null()
        & (label_time <= test_start)
        & feature_time.is_not_null()
        & (feature_time <= test_start)
    )
    test_expr = labeled & (observation >= test_start)
    if test_end is not None:
        test_expr = test_expr & (observation < test_end)
    test = samples.filter(test_expr)
    return {"train": train, "test": test}


def build_pit_two_way_split(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    test_start: date,
    as_of: date,
    test_end: date | None = None,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str | None = "VAS_TT200_2014",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    precomputed_as_of_scored: pl.DataFrame | None = None,
    statutory_delay_days: int = 0,
    market_prices: pl.DataFrame | None = None,
) -> dict[str, pl.DataFrame]:
    """Construct a 2-way PIT split (Train_OOF at ``test_start`` -> Test at ``as_of``) (B-02)."""
    if test_start > as_of:
        raise ContractViolationError("Test start must not exceed the PIT as_of date.")
    if test_end is not None and test_start >= test_end:
        raise ContractViolationError("test_start must precede test_end.")
    resolved_target_mode = resolve_target_mode(target_mode, regulatory_events)

    def _materialize_at(
        cutoff: date, *, precomputed_scored: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        if precomputed_scored is not None:
            scored = precomputed_scored
        else:
            periods = prepare_periods(
                facts,
                issuers,
                as_of=cutoff,
                include_ml_inputs=True,
                statutory_delay_days=statutory_delay_days,
            )
            scored = add_classical_scores(calculate_ratios(periods))
        if market_prices is not None and "merton_dtd" not in scored.columns:
            scored = attach_merton_dtd_to_periods(
                scored,
                market_prices,
                financial_facts=facts,
                statement_scope=statement_scope,
                as_of=cutoff,
            )
        scoped = scored.filter(pl.col("statement_scope") == statement_scope)
        if taxonomy_version is not None:
            scoped = scoped.filter(pl.col("taxonomy_version") == taxonomy_version)
        return build_distress_targets(
            scoped,
            mode=resolved_target_mode,
            as_of=cutoff,
            regulatory_events=regulatory_events,
        )

    train_raw = _materialize_at(test_start)
    test_raw = _materialize_at(as_of, precomputed_scored=precomputed_as_of_scored)

    pre_train = purged_two_way_split(
        train_raw,
        test_start=test_start,
        test_end=test_end,
        require_exact_filing=require_exact_filing,
    )["train"]
    peer_mode = resolve_peer_standardization_mode(pre_train, None)

    train_snapshot = attach_pit_sector_z_features(train_raw, mode=peer_mode)
    test_snapshot = attach_pit_sector_z_features(test_raw, mode=peer_mode)

    train = purged_two_way_split(
        train_snapshot,
        test_start=test_start,
        test_end=test_end,
        require_exact_filing=require_exact_filing,
    )["train"]
    test = purged_two_way_split(
        test_snapshot,
        test_start=test_start,
        test_end=test_end,
        require_exact_filing=require_exact_filing,
    )["test"]
    return {"train": train, "test": test}


def build_expanding_walk_forward_splits(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    test_years: Sequence[int] = (2021, 2022, 2023, 2024, 2025),
    as_of: date,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str | None = "VAS_TT200_2014",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    precomputed_as_of_scored: pl.DataFrame | None = None,
    statutory_delay_days: int = 0,
    market_prices: pl.DataFrame | None = None,
) -> dict[int, dict[str, pl.DataFrame]]:
    """Build 2-way Purged Expanding Walk-Forward PIT splits across ``test_years`` (B-02)."""
    if not test_years:
        raise ContractViolationError("test_years must be a nonempty sequence of years.")
    if precomputed_as_of_scored is None:
        periods_as_of = prepare_periods(
            facts,
            issuers,
            as_of=as_of,
            include_ml_inputs=True,
            statutory_delay_days=statutory_delay_days,
        )
        scored_as_of = add_classical_scores(calculate_ratios(periods_as_of))
    else:
        scored_as_of = precomputed_as_of_scored
    if market_prices is not None and "merton_dtd" not in scored_as_of.columns:
        scored_as_of = attach_merton_dtd_to_periods(
            scored_as_of,
            market_prices,
            financial_facts=facts,
            statement_scope=statement_scope,
            as_of=as_of,
        )

    folds: dict[int, dict[str, pl.DataFrame]] = {}
    for year in test_years:
        test_start = date(int(year), 1, 1)
        test_end = date(int(year) + 1, 1, 1)
        folds[int(year)] = build_pit_two_way_split(
            facts,
            issuers,
            test_start=test_start,
            test_end=test_end,
            as_of=as_of,
            require_exact_filing=require_exact_filing,
            statement_scope=statement_scope,
            taxonomy_version=taxonomy_version,
            target_mode=target_mode,
            regulatory_events=regulatory_events,
            precomputed_as_of_scored=scored_as_of,
            statutory_delay_days=statutory_delay_days,
            market_prices=market_prices,
        )
    return folds


def build_pit_ml_splits(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    validation_start: date | None = None,
    test_start: date,
    as_of: date,
    test_end: date | None = None,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str | None = "VAS_TT200_2014",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    precomputed_as_of_scored: pl.DataFrame | None = None,
    statutory_delay_days: int = 0,
) -> dict[str, pl.DataFrame]:
    """Construct fold-specific PIT feature/label snapshots without restatement or future-ICB leakage.

    When ``validation_start`` is ``None`` (recommended 2-way ``B-02`` mode), delegates
    to ``build_pit_two_way_split`` returning ``{"train": ..., "test": ...}``.
    When ``validation_start`` is supplied (legacy 3-way callers), materializes
    train at ``validation_start``, validation at ``test_start``, and test at ``as_of``.
    """
    if validation_start is None:
        return build_pit_two_way_split(
            facts,
            issuers,
            test_start=test_start,
            test_end=test_end,
            as_of=as_of,
            require_exact_filing=require_exact_filing,
            statement_scope=statement_scope,
            taxonomy_version=taxonomy_version,
            target_mode=target_mode,
            regulatory_events=regulatory_events,
            precomputed_as_of_scored=precomputed_as_of_scored,
            statutory_delay_days=statutory_delay_days,
        )
    if validation_start >= test_start:
        raise ContractViolationError("Validation start must precede test start.")
    if test_start > as_of:
        raise ContractViolationError("Test start must not exceed the PIT as_of date.")
    resolved_target_mode = resolve_target_mode(target_mode, regulatory_events)

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
            mode=resolved_target_mode,
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
