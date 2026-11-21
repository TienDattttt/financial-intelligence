"""Assemble Layer 4B ML scores, explanations, evaluation report and run manifest.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 2.1 (G1-G5), 5.3.4 and 6.4.
    Shumway, T. (2001). "Forecasting Bankruptcy More Accurately: A Simple Hazard Model."
        The Journal of Business, Vol. 74, No. 1, pp. 101-124.
    Platt, J. C. (1999) & Niculescu-Mizil, A., & Caruana, R. (2005) (ICML 2005, pp. 625-632).
    Lundberg, S. M. et al. (2020). Nature Machine Intelligence, Vol. 2, pp. 56-67.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.explain import explain_distress
from finintel.ml.supervised.target import resolve_target_mode
from finintel.ml.supervised.temporal_split import (
    attach_pit_sector_z_features,
    build_pit_ml_splits,
    resolve_peer_standardization_mode,
)
from finintel.ml.supervised.train import (
    BASE_FEATURES,
    SECTOR_ADJUSTED_FEATURES,
    build_evaluation_report,
    score_distress_model,
    train_distress_model,
)
from finintel.ml.unsupervised import FinancialArchetypes
from finintel.storage.schemas import (
    ML_SCORES_KEY,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    validate_analytics_table,
)

_ML_PACKAGES = ("polars", "numpy", "scipy", "scikit-learn", "xgboost", "shap")


@dataclass(frozen=True)
class MLArtifacts:
    """Validated Layer 4B Parquet table, OOS evaluation report and provenance manifest."""

    scores_table: pl.DataFrame
    evaluation_report: dict[str, Any]
    run_manifest: dict[str, Any]


def _installed_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for pkg in _ML_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            versions[pkg] = None
    return versions


def build_ml_artifacts(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    validation_start: date | None = None,
    test_start: date,
    approved_target: bool = False,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str = "VAS_TT200_2014",
    scored_taxonomies: tuple[str, ...] = ("VAS_TT200_2014", "VAS_TT99_2025"),
    feature_set: Literal["sector_adjusted", "base"] = "sector_adjusted",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    min_unique_positive_issuers: int = 3,
    peer_benchmarks: pl.DataFrame | None = None,
    facts_sha256: str | None = None,
    issuers_sha256: str | None = None,
    precomputed_ratios_as_of: pl.DataFrame | None = None,
) -> MLArtifacts:
    """Train, calibrate, evaluate and score Layer 4B strictly from PIT real history.

    Guardrail G2 requires ``approved_target=True`` before supervised training runs.
    Guardrail G1 requires ``EXACT_FILING`` provenance across train/validation/test
    when ``require_exact_filing=True``. Classical scores (Altman Z'', Piotroski F,
    Beneish M) are computed for both QUARTER and ANNUAL corporate records, whereas
    Unsupervised and Supervised ML models score strictly on QUARTER records across
    ``scored_taxonomies``. When ``target_mode`` is omitted, ``REGULATORY_DISTRESS``
    activates automatically whenever ``regulatory_events`` is supplied. When
    ``validation_start`` is ``None`` (B-02 2-way mode), inner Purged Temporal OOF
    inside ``train`` handles early stopping and fair 3-model Platt calibration.
    """
    if not approved_target:
        raise ContractViolationError(
            "Layer 4B supervised ML requires explicit target approval (approved_target=True)."
        )
    if not scored_taxonomies or any(
        not isinstance(item, str) or not item.strip() for item in scored_taxonomies
    ):
        raise ContractViolationError(
            "Layer 4B scored_taxonomies must be a nonempty tuple of taxonomy strings."
        )
    if feature_set not in ("sector_adjusted", "base"):
        raise ContractViolationError(
            f"Unsupported feature_set {feature_set!r}; expected 'sector_adjusted' or 'base'."
        )
    resolved_target_mode = resolve_target_mode(target_mode, regulatory_events)
    selected_features = (
        SECTOR_ADJUSTED_FEATURES if feature_set == "sector_adjusted" else BASE_FEATURES
    )

    if precomputed_ratios_as_of is not None:
        ratios_as_of = precomputed_ratios_as_of
    else:
        periods_as_of = prepare_periods(
            facts, issuers, as_of=as_of, include_ml_inputs=True
        )
        ratios_as_of = calculate_ratios(periods_as_of)
    classical_as_of = add_classical_scores(ratios_as_of)
    benchmarks = (
        peer_benchmarks
        if peer_benchmarks is not None
        else benchmark_ratios(ratios_as_of)
    )

    splits = build_pit_ml_splits(
        facts,
        issuers,
        validation_start=validation_start,
        test_start=test_start,
        as_of=as_of,
        require_exact_filing=require_exact_filing,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        target_mode=resolved_target_mode,
        regulatory_events=regulatory_events,
        precomputed_as_of_scored=classical_as_of,
    )
    validation_fold = splits.get("validation")
    peer_standardization_mode = resolve_peer_standardization_mode(
        splits["train"], validation_fold
    )
    archetypes = FinancialArchetypes().fit(splits["train"])
    training = train_distress_model(
        splits["train"],
        validation_fold,
        features=selected_features,
        min_unique_positive_issuers=min_unique_positive_issuers,
    )
    evaluation_report = build_evaluation_report(
        training,
        train=splits["train"],
        validation=validation_fold,
        test=splits["test"],
    )
    evaluation_report.update(
        {
            "scored_taxonomies": list(scored_taxonomies),
            "scored_period_types": ["QUARTER"],
            "feature_set": feature_set,
            "peer_standardization_mode": peer_standardization_mode,
            "target_mode": resolved_target_mode,
            "split_mode": "TWO_WAY_PURGED_OOF"
            if validation_fold is None
            else "THREE_WAY_HOLD_OUT",
            "platt_slope": training.platt_slope,
            "platt_intercept": training.platt_intercept,
            "f2_thresholds": dict(training.f2_thresholds),
        }
    )

    eligible_quarters = classical_as_of.filter(
        (pl.col("statement_scope") == statement_scope)
        & (pl.col("period_type") == "QUARTER")
        & pl.col("taxonomy_version").is_in(list(scored_taxonomies))
    )
    eligible_with_z = attach_pit_sector_z_features(
        eligible_quarters, mode=peer_standardization_mode
    )
    eligible = (
        eligible_with_z.filter(pl.col("_exact_filing_basis").fill_null(False))
        if require_exact_filing
        else eligible_with_z
    )

    unsupervised_out = archetypes.transform(eligible)
    supervised_out = score_distress_model(training, eligible).drop("model_role")
    shap_out = explain_distress(
        training,
        eligible,
        peer_medians=benchmarks,
        peer_standardization_mode=peer_standardization_mode,
    )

    join_keys = list(ML_SCORES_KEY)
    assembled = (
        classical_as_of.join(unsupervised_out, on=join_keys, how="left", validate="1:1")
        .join(supervised_out, on=join_keys, how="left", validate="1:1")
        .join(shap_out, on=join_keys, how="left", validate="1:1")
        .select(
            *(
                pl.col(name).cast(dtype, strict=True).alias(name)
                for name, dtype in ML_SCORES_SCHEMA.items()
            )
        )
        .sort(join_keys)
    )
    validate_analytics_table(ML_SCORES_TABLE, assembled)

    run_manifest: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "validation_start": validation_start.isoformat()
        if validation_start is not None
        else None,
        "test_start": test_start.isoformat(),
        "split_mode": "TWO_WAY_PURGED_OOF"
        if validation_fold is None
        else "THREE_WAY_HOLD_OUT",
        "oof_strategy": training.oof_strategy,
        "best_n_estimators": training.best_n_estimators,
        "train_prevalence": training.train_prevalence,
        "approved_target": approved_target,
        "require_exact_filing": require_exact_filing,
        "statement_scope": statement_scope,
        "taxonomy_version": taxonomy_version,
        "scored_taxonomies": list(scored_taxonomies),
        "scored_period_types": ["QUARTER"],
        "feature_set": feature_set,
        "peer_standardization_mode": peer_standardization_mode,
        "target_mode": resolved_target_mode,
        "platt_slope": training.platt_slope,
        "platt_intercept": training.platt_intercept,
        "f2_thresholds": dict(training.f2_thresholds),
        "random_seed": 42,
        "facts_sha256": facts_sha256,
        "issuers_sha256": issuers_sha256,
        "split_counts": {
            fold_name: {
                "rows": int(fold_df.height),
                "unique_issuers": int(fold_df["issuer_id"].n_unique())
                if "issuer_id" in fold_df.columns
                else 0,
                "positive_labels": int(
                    (fold_df.get_column("distress_target").to_numpy() == 1).sum()
                )
                if not fold_df.is_empty()
                else 0,
            }
            for fold_name, fold_df in splits.items()
        },
        "package_versions": _installed_versions(),
    }
    return MLArtifacts(
        scores_table=assembled,
        evaluation_report=evaluation_report,
        run_manifest=run_manifest,
    )
