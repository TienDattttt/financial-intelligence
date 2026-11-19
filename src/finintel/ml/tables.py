"""Assemble Layer 4B ML scores, explanations, evaluation report and run manifest.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 2.1 (G1–G5), 5.3.4 and 6.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.explain import explain_distress
from finintel.ml.supervised.temporal_split import build_pit_ml_splits
from finintel.ml.supervised.train import (
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
    validation_start: date,
    test_start: date,
    approved_target: bool = False,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str = "VAS_TT200_2014",
    peer_benchmarks: pl.DataFrame | None = None,
    facts_sha256: str | None = None,
    issuers_sha256: str | None = None,
) -> MLArtifacts:
    """Train, calibrate, evaluate and score Layer 4B strictly from PIT real history.

    Guardrail G2 requires ``approved_target=True`` before supervised training runs.
    Guardrail G1 requires ``EXACT_FILING`` provenance across train/validation/test
    when ``require_exact_filing=True``. Fails fast when sufficient labeled classes
    or cross-sectional variation are absent.
    """
    if not approved_target:
        raise ContractViolationError(
            "Layer 4B supervised ML requires explicit target approval (approved_target=True)."
        )

    periods_as_of = prepare_periods(facts, issuers, as_of=as_of, include_ml_inputs=True)
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
    )
    archetypes = FinancialArchetypes().fit(splits["train"])
    training = train_distress_model(splits["train"], splits["validation"])
    evaluation_report = build_evaluation_report(
        training,
        train=splits["train"],
        validation=splits["validation"],
        test=splits["test"],
    )

    eligible = classical_as_of.filter(
        (pl.col("statement_scope") == statement_scope)
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("taxonomy_version") == taxonomy_version)
    )
    if require_exact_filing:
        eligible = eligible.filter(pl.col("_exact_filing_basis").fill_null(False))

    unsupervised_out = archetypes.transform(eligible)
    supervised_out = score_distress_model(training, eligible).drop("model_role")
    shap_out = explain_distress(training, eligible, peer_medians=benchmarks)

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
        "validation_start": validation_start.isoformat(),
        "test_start": test_start.isoformat(),
        "approved_target": approved_target,
        "require_exact_filing": require_exact_filing,
        "statement_scope": statement_scope,
        "taxonomy_version": taxonomy_version,
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
