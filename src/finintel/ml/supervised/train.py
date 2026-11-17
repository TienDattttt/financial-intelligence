"""Supervised training, validation-only calibration and baseline evaluation.

Verified Sources:
    https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html
    https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html
    https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html
    Layer 4 section 5.3 fixes the target, monotone directions, hyperparameters
    and model-comparison metrics. Calibration fit is restricted to validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from sklearn.calibration import CalibratedClassifierCV  # type: ignore[import-untyped]
from sklearn.frozen import FrozenEstimator  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import (  # type: ignore[import-untyped]
    average_precision_score,
    brier_score_loss,
    fbeta_score,
    roc_auc_score,
)

from finintel.exceptions import ContractViolationError
from finintel.ml.unsupervised import CrossSectionalPreprocessor

NEGATIVE = frozenset(
    "roa roe_parent roic ebit_margin current_ratio quick_ratio cash_ratio interest_coverage "
    "ocf_to_current_liabilities fcf_to_revenue altman_z_double_prime piotroski_f_score".split()
)
POSITIVE = frozenset(
    "liabilities_to_assets financial_debt_to_assets debt_to_equity net_debt_to_ebitda "
    "accruals_to_assets".split()
)
UNCONSTRAINED = frozenset(
    "asset_turnover cash_conversion_cycle revenue_growth_yoy".split()
)
BASE_FEATURES = tuple(sorted(NEGATIVE | POSITIVE | UNCONSTRAINED))


@dataclass(frozen=True)
class TrainingResult:
    """Fitted model, validation calibrator, feature contract and evaluation metrics."""

    features: tuple[str, ...]
    preprocessor: CrossSectionalPreprocessor
    classifier: Any
    calibrated: Any
    baseline_logit: Any
    validation_metrics: dict[str, dict[str, float | None]]


def _constraints(features: tuple[str, ...]) -> tuple[int, ...]:
    unknown = set(features) - NEGATIVE - POSITIVE - UNCONSTRAINED
    if unknown:
        raise ContractViolationError(
            f"No monotonicity rule defined for features: {sorted(unknown)}"
        )
    return tuple(
        -1 if name in NEGATIVE else 1 if name in POSITIVE else 0 for name in features
    )


def _metrics(y: np.ndarray, score: np.ndarray) -> dict[str, float | None]:
    if np.unique(y).size < 2:
        return {
            "pr_auc": None,
            "roc_auc": None,
            "brier_score": None,
            "f2_score": None,
            "recall_at_top_10_percent": None,
        }
    cutoff = max(1, int(np.ceil(len(score) * 0.10)))
    selected = np.argsort(-score)[:cutoff]
    return {
        "pr_auc": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
        "brier_score": float(brier_score_loss(y, score)),
        "f2_score": float(fbeta_score(y, score >= 0.5, beta=2, zero_division=0)),
        "recall_at_top_10_percent": float(y[selected].sum() / max(1, y.sum())),
    }


def train_distress_model(
    train: pl.DataFrame,
    validation: pl.DataFrame,
    *,
    features: tuple[str, ...] = BASE_FEATURES,
    target: str = "distress_target",
) -> TrainingResult:
    """Fit train-only preprocessing and XGBoost; calibrate on disjoint validation.

    Preprocessor bounds, imputation statistics and scaling are learned only on
    train. The validation fold is used for probability calibration and model
    selection reporting, never for fitting the classifier or preprocessing.
    """
    if train.is_empty() or validation.is_empty():
        raise ContractViolationError(
            "Supervised training requires nonempty train and validation sets."
        )
    if set(features) - set(train.columns) or set(features) - set(validation.columns):
        raise ContractViolationError(
            "Supervised input is missing one or more configured features."
        )
    if not features or len(set(features)) != len(features):
        raise ContractViolationError("Supervised features must be nonempty and unique.")
    if target not in train.columns or target not in validation.columns:
        raise ContractViolationError(
            f"Supervised input is missing target column {target!r}."
        )
    required = {"accounting_regime", "altman_z_double_prime", target}
    if required - set(train.columns) or required - set(validation.columns):
        raise ContractViolationError(
            "Supervised inputs require corporate regime and the Altman baseline feature."
        )
    if (
        train.filter(pl.col("accounting_regime") != "CORPORATE").height
        or validation.filter(pl.col("accounting_regime") != "CORPORATE").height
    ):
        raise ContractViolationError("Supervised models accept corporate records only.")
    y_train = train[target].to_numpy().astype(np.int8)
    y_valid = validation[target].to_numpy().astype(np.int8)
    if np.unique(y_train).size != 2 or np.unique(y_valid).size != 2:
        raise ContractViolationError(
            "Train and validation require both distress target classes."
        )
    prep = CrossSectionalPreprocessor(features).fit(train)
    x_train = prep.transform(train).select(features).to_numpy()
    x_valid = prep.transform(validation).select(features).to_numpy()
    from xgboost import XGBClassifier  # type: ignore[import-untyped]

    classifier = XGBClassifier(
        objective="binary:logistic",
        max_depth=4,
        min_child_weight=10,
        subsample=0.8,
        colsample_bytree=0.8,
        learning_rate=0.05,
        reg_lambda=5.0,
        reg_alpha=1.0,
        eval_metric="aucpr",
        monotone_constraints=_constraints(features),
        random_state=42,
        n_jobs=1,
    )
    classifier.fit(x_train, y_train)
    calibrated = CalibratedClassifierCV(FrozenEstimator(classifier), method="sigmoid")
    calibrated.fit(x_valid, y_valid)
    baseline = LogisticRegression(
        penalty="l2", C=0.1, class_weight="balanced", max_iter=1000, random_state=42
    )
    baseline.fit(x_train, y_train)
    scores = {
        "Baseline_1_AltmanZ": _metrics(
            y_valid, -validation["altman_z_double_prime"].to_numpy().astype(float)
        ),
        "Baseline_2_Logit": _metrics(y_valid, baseline.predict_proba(x_valid)[:, 1]),
        "XGBoost": _metrics(y_valid, calibrated.predict_proba(x_valid)[:, 1]),
    }
    return TrainingResult(features, prep, classifier, calibrated, baseline, scores)


def score_distress_model(result: TrainingResult, frame: pl.DataFrame) -> pl.DataFrame:
    """Return calibrated screening probabilities while preserving row order."""
    matrix = result.preprocessor.transform(frame).select(result.features).to_numpy()
    probability = result.calibrated.predict_proba(matrix)[:, 1]
    raw_margin = result.classifier.predict(matrix, output_margin=True)
    return pl.DataFrame(
        {
            "distress_risk_score": raw_margin,
            "calibrated_distress_prob": probability,
            "model_version": ["xgboost-layer4b-v1"] * frame.height,
            "model_role": ["SCREENING_INDICATOR"] * frame.height,
        }
    )
