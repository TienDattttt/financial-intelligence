"""Supervised training, raw-margin Platt OOF calibration and out-of-sample evaluation.

Verified Sources:
    https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html
    https://scikit-learn.org/stable/modules/calibration.html
    https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html
    https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html
    Platt, J. C. (1999). "Probabilistic Outputs for Support Vector Machines and Comparisons
        to Regularized Likelihood Methods." Advances in Large Margin Classifiers, MIT Press, pp. 61-74.
    Niculescu-Mizil, A., & Caruana, R. (2005). "Predicting Good Probabilities With Supervised
        Learning." Proceedings of the 22nd International Conference on Machine Learning (ICML 2005), pp. 625-632.
    Shumway, T. (2001). "Forecasting Bankruptcy More Accurately: A Simple Hazard Model."
        The Journal of Business, Vol. 74, No. 1, pp. 101-124.
    Falkenstein, E., Boral, A., & Carty, L. V. (2000). "RiskCalc For Private Companies:
        Moody's Default Model." Moody's Investors Service Global Credit Research.
    Layer 4 section 5.3 fixes the target, monotone directions, hyperparameters
    and model-comparison metrics. Calibration is fit on raw_margin (log-odds) on
    validation via Out-of-Fold CV, while empirical model comparison is evaluated
    out-of-sample on test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import (  # type: ignore[import-untyped]
    average_precision_score,
    brier_score_loss,
    fbeta_score,
    roc_auc_score,
)
from sklearn.model_selection import (  # type: ignore[import-untyped]
    StratifiedGroupKFold,
    StratifiedKFold,
)

from finintel.exceptions import ContractViolationError
from finintel.ml.supervised.temporal_split import SECTOR_RAW_TO_Z
from finintel.ml.unsupervised import (
    CrossSectionalPreprocessor,
    InsufficientModelData,
)
from finintel.storage.schemas import ML_SCORES_KEY

NEGATIVE = frozenset(
    "roa roe_parent roic ebit_margin current_ratio quick_ratio cash_ratio interest_coverage "
    "ocf_to_current_liabilities fcf_to_revenue altman_z_double_prime piotroski_f_score "
    "sector_z_asset_turnover sector_z_ebit_margin".split()
)
POSITIVE = frozenset(
    "liabilities_to_assets financial_debt_to_assets debt_to_equity net_debt_to_ebitda "
    "accruals_to_assets sector_z_cash_conversion_cycle sector_z_debt_to_equity".split()
)
UNCONSTRAINED = frozenset(
    "asset_turnover cash_conversion_cycle revenue_growth_yoy".split()
)
BASE_FEATURES: tuple[str, ...] = tuple(
    sorted((NEGATIVE | POSITIVE | UNCONSTRAINED) - set(SECTOR_RAW_TO_Z.values()))
)
SECTOR_ADJUSTED_FEATURES: tuple[str, ...] = tuple(
    sorted(
        (set(BASE_FEATURES) - set(SECTOR_RAW_TO_Z.keys()))
        | set(SECTOR_RAW_TO_Z.values())
    )
)
MODEL_VERSION = "xgboost-layer4b-v1"
ALTMAN_DISTRESS_CUTOFF = 1.10


def _sigmoid(z: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
    """Numerically stable logistic function on log-odds margins."""
    clipped = np.clip(np.asarray(z, dtype=np.float64), -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _solve_monotone_mle_intercept(
    raw_margin: NDArray[np.float64],
    y: NDArray[np.int8],
    *,
    slope: float = 1.0,
) -> float:
    """Find the exact 1D MLE intercept minimizing binary cross-entropy of sigmoid(slope * margin + b)."""
    if raw_margin.size == 0 or y.size == 0:
        return 0.0
    y_float = y.astype(np.float64)
    scaled_margin = slope * raw_margin

    def _bce_loss(intercept: float) -> float:
        z = np.clip(scaled_margin + intercept, -60.0, 60.0)
        return float(np.sum(np.logaddexp(0.0, z) - y_float * z))

    result = minimize_scalar(_bce_loss, bounds=(-30.0, 30.0), method="bounded")
    return float(result.x) if np.isfinite(result.x) else 0.0


def _fit_platt_params(
    raw_margin: NDArray[np.float64],
    y: NDArray[np.int8],
) -> tuple[float, float]:
    """Fit 1D Platt scaling on raw_margin with monotone MLE intercept fallback when w <= 0."""
    if raw_margin.size == 0 or np.unique(y).size < 2:
        base_rate = float(np.clip(np.mean(y) if y.size > 0 else 0.5, 1e-6, 1.0 - 1e-6))
        return 1.0, float(np.log(base_rate / (1.0 - base_rate)))
    lr = LogisticRegression(
        penalty="l2",
        C=1.0,
        solver="lbfgs",
        max_iter=1000,
        random_state=42,
    )
    lr.fit(raw_margin[:, None], y)
    raw_slope = float(lr.coef_[0, 0])
    raw_intercept = float(lr.intercept_[0])
    if not np.isfinite(raw_slope) or not np.isfinite(raw_intercept) or raw_slope <= 0.0:
        return 1.0, _solve_monotone_mle_intercept(raw_margin, y, slope=1.0)
    if raw_slope > 10.0:
        return 10.0, _solve_monotone_mle_intercept(raw_margin, y, slope=10.0)
    return raw_slope, raw_intercept


class MarginPlattCalibrator:
    """Platt scaling calibrator fitted directly on XGBoost log-odds raw_margin with OOF CV."""

    def __init__(self, classifier: Any) -> None:
        self.classifier = classifier
        self.slope: float = 1.0
        self.intercept: float = 0.0
        self.oof_probabilities: NDArray[np.float64] = np.empty(0, dtype=np.float64)
        self._is_fitted: bool = False

    def fit(
        self,
        x_valid: NDArray[np.floating[Any]],
        y_valid: NDArray[np.int8],
        *,
        groups: NDArray[Any] | list[Any] | None = None,
    ) -> "MarginPlattCalibrator":
        """Compute OOF calibrated probabilities on validation and fit final Platt parameters."""
        raw_margin = np.asarray(
            self.classifier.predict(x_valid, output_margin=True), dtype=np.float64
        )
        return self.fit_margins(raw_margin, y_valid, groups=groups)

    def fit_margins(
        self,
        raw_margin: NDArray[np.floating[Any]],
        y_valid: NDArray[np.int8],
        *,
        groups: NDArray[Any] | list[Any] | None = None,
    ) -> "MarginPlattCalibrator":
        """Fit OOF and full-fold Platt scaling directly on a 1D raw_margin vector."""
        margins = np.asarray(raw_margin, dtype=np.float64).reshape(-1)
        targets = np.asarray(y_valid, dtype=np.int8).reshape(-1)
        if margins.size != targets.size or margins.size == 0:
            raise ContractViolationError(
                "MarginPlattCalibrator requires equal-length nonempty margins and targets."
            )
        min_valid_class = int(min((targets == 0).sum(), (targets == 1).sum()))
        if min_valid_class < 2:
            raise InsufficientModelData(
                "Probability calibration requires at least two observations per class in validation."
            )

        oof_prob = np.empty(len(targets), dtype=np.float64)
        split_indices: list[tuple[NDArray[np.intp], NDArray[np.intp]]] = []
        if groups is not None:
            group_arr = np.asarray(groups)
            if group_arr.size == len(targets) and not any(g is None for g in group_arr):
                pos_groups = set(group_arr[targets == 1].tolist())
                neg_groups = set(group_arr[targets == 0].tolist())
                all_groups = set(group_arr.tolist())
                n_pos_groups = len(pos_groups)
                n_neg_groups = len(neg_groups)
                if n_pos_groups >= 2 and n_neg_groups >= 2 and len(all_groups) >= 2:
                    k_group = min(5, n_pos_groups, n_neg_groups, len(all_groups))
                    if k_group >= 2:
                        sgkf = StratifiedGroupKFold(n_splits=k_group)
                        split_indices = list(
                            sgkf.split(margins[:, None], targets, group_arr)
                        )

        if not split_indices:
            k_strat = max(2, min(5, min_valid_class))
            skf = StratifiedKFold(n_splits=k_strat, shuffle=True, random_state=42)
            split_indices = list(skf.split(margins[:, None], targets))

        for tr_idx, te_idx in split_indices:
            y_tr = targets[tr_idx]
            m_tr = margins[tr_idx]
            m_te = margins[te_idx]
            if np.unique(y_tr).size < 2:
                base_rate = float(np.mean(y_tr)) if y_tr.size > 0 else 0.5
                oof_prob[te_idx] = np.full(len(te_idx), base_rate, dtype=np.float64)
            else:
                fold_a, fold_b = _fit_platt_params(m_tr, y_tr)
                oof_prob[te_idx] = _sigmoid(fold_a * m_te + fold_b)

        self.slope, self.intercept = _fit_platt_params(margins, targets)
        self.oof_probabilities = oof_prob
        self._is_fitted = True
        return self

    def predict_margin_proba(
        self, raw_margin: NDArray[np.floating[Any]]
    ) -> NDArray[np.float64]:
        """Map raw_margin (log-odds) to calibrated [P(y=0), P(y=1)] probabilities."""
        if not self._is_fitted:
            raise ContractViolationError(
                "Fit MarginPlattCalibrator before predicting probabilities."
            )
        margins = np.asarray(raw_margin, dtype=np.float64).reshape(-1)
        p1 = _sigmoid(self.slope * margins + self.intercept)
        return np.column_stack([1.0 - p1, p1])

    def predict_proba(self, matrix: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
        """Predict raw_margin from classifier and map to calibrated [P(y=0), P(y=1)]."""
        if not self._is_fitted:
            raise ContractViolationError(
                "Fit MarginPlattCalibrator before predicting probabilities."
            )
        raw_margin = np.asarray(
            self.classifier.predict(matrix, output_margin=True), dtype=np.float64
        )
        return self.predict_margin_proba(raw_margin)


@dataclass(frozen=True)
class TrainingResult:
    """Fitted model, validation calibrator, feature contract and calibration metrics."""

    features: tuple[str, ...]
    preprocessor: CrossSectionalPreprocessor
    classifier: Any
    calibrated: Any
    baseline_logit: Any
    validation_metrics: dict[str, dict[str, float | None]]
    f2_thresholds: dict[str, float] = field(default_factory=dict)
    platt_slope: float = 1.0
    platt_intercept: float = 0.0
    oof_validation_metrics: dict[str, float | None] = field(default_factory=dict)


def _model_features(
    features: tuple[str, ...], prep: CrossSectionalPreprocessor
) -> tuple[str, ...]:
    return (*features, *prep.indicators)


def _constraints(features: tuple[str, ...]) -> tuple[int, ...]:
    indicators = {name for name in features if name.endswith("_missing")}
    unknown = set(features) - NEGATIVE - POSITIVE - UNCONSTRAINED - indicators
    if unknown:
        raise ContractViolationError(
            f"No monotonicity rule defined for features: {sorted(unknown)}"
        )
    return tuple(
        -1 if name in NEGATIVE else 1 if name in POSITIVE else 0 for name in features
    )


def _optimal_f2_threshold(y: NDArray[np.int8], score: NDArray[np.float64]) -> float:
    """Select the F2-maximizing operating threshold strictly on validation."""
    finite = np.isfinite(score)
    y_fin = y[finite]
    s_fin = score[finite]
    if y_fin.size == 0 or np.unique(y_fin).size < 2:
        return 0.5
    candidates = np.unique(s_fin)
    best_thr = 0.5
    best_f2 = -1.0
    for thr in candidates:
        val = float(fbeta_score(y_fin, s_fin >= thr, beta=2, zero_division=0))
        if val > best_f2:
            best_f2 = val
            best_thr = float(thr)
    return best_thr


def _metrics(
    y: NDArray[np.int8],
    score: NDArray[np.float64],
    *,
    f2_threshold: float = 0.5,
) -> dict[str, float | None]:
    finite = np.isfinite(score)
    y = y[finite]
    score = score[finite]
    if y.size == 0 or np.unique(y).size < 2:
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
        "f2_score": float(
            fbeta_score(y, score >= f2_threshold, beta=2, zero_division=0)
        ),
        "recall_at_top_10_percent": float(y[selected].sum() / max(1, int(y.sum()))),
    }


def _altman_probability(score: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
    """Map Altman Z'' monotonically to [0, 1] centered at the 1.10 distress zone cutoff."""
    values = np.asarray(score, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(np.clip(values - ALTMAN_DISTRESS_CUTOFF, -60.0, 60.0)))


def _prepare_eval_fold(fold: pl.DataFrame, *, target: str) -> pl.DataFrame:
    if fold.filter(pl.col("accounting_regime").ne("CORPORATE").fill_null(True)).height:
        raise ContractViolationError("Supervised models accept corporate records only.")
    filtered = fold
    if "already_distressed_at_t" in filtered.columns:
        filtered = filtered.filter(~pl.col("already_distressed_at_t").fill_null(True))
    return filtered.filter(pl.col(target).is_not_null())


def _score_three_models(
    result: TrainingResult, fold: pl.DataFrame, *, target: str
) -> tuple[
    NDArray[np.int8],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.bool_],
]:
    y = fold[target].to_numpy().astype(np.int8)
    matrix = result.preprocessor.transform(fold).select(result.features).to_numpy()
    logit_prob = np.asarray(
        result.baseline_logit.predict_proba(matrix)[:, 1], dtype=np.float64
    )
    xgb_prob = np.asarray(
        result.calibrated.predict_proba(matrix)[:, 1], dtype=np.float64
    )
    raw_altman = np.asarray(
        fold.get_column("altman_z_double_prime").to_numpy(), dtype=np.float64
    )
    common_mask = (
        np.isfinite(raw_altman) & np.isfinite(logit_prob) & np.isfinite(xgb_prob)
    )
    altman_prob = np.asarray(
        np.where(np.isfinite(raw_altman), _altman_probability(raw_altman), np.nan),
        dtype=np.float64,
    )
    return y, altman_prob, logit_prob, xgb_prob, common_mask


def train_distress_model(
    train: pl.DataFrame,
    validation: pl.DataFrame,
    *,
    features: tuple[str, ...] = BASE_FEATURES,
    target: str = "distress_target",
    min_unique_positive_issuers: int = 3,
) -> TrainingResult:
    """Fit train-only preprocessing and XGBoost; calibrate on disjoint validation via OOF Platt scaling.

    Preprocessor bounds, imputation statistics and scaling are learned only on
    train. The validation fold is used for MarginPlattCalibrator OOF probability
    calibration; out-of-sample model claims must be evaluated on a separate test
    fold via ``evaluate_distress_models`` / ``build_evaluation_report``.
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
    required = {
        "accounting_regime",
        "altman_z_double_prime",
        "_period_available_at",
        "statement_scope",
        "taxonomy_version",
        target,
    }
    if required - set(train.columns) or required - set(validation.columns):
        raise ContractViolationError(
            "Supervised inputs require corporate regime and the Altman baseline feature."
        )
    train = _prepare_eval_fold(train, target=target)
    validation = _prepare_eval_fold(validation, target=target)
    if train.is_empty() or validation.is_empty():
        raise ContractViolationError(
            "Onset filtering left an empty train or validation fold."
        )
    y_train = train[target].to_numpy().astype(np.int8)
    y_valid = validation[target].to_numpy().astype(np.int8)
    if np.unique(y_train).size != 2 or np.unique(y_valid).size != 2:
        raise ContractViolationError(
            "Train and validation require both distress target classes."
        )
    if "issuer_id" in train.columns:
        unique_pos_issuers = (
            train.filter(pl.col(target) == 1)
            .get_column("issuer_id")
            .drop_nulls()
            .n_unique()
        )
        if unique_pos_issuers < min_unique_positive_issuers:
            raise InsufficientModelData(
                f"Supervised training requires at least {min_unique_positive_issuers} "
                f"unique positive issuers in train, got {unique_pos_issuers}."
            )
    min_valid_class = int(min((y_valid == 0).sum(), (y_valid == 1).sum()))
    if min_valid_class < 2:
        raise InsufficientModelData(
            "Probability calibration requires at least two observations per class in validation."
        )
    scopes = set(train.get_column("statement_scope").drop_nulls().to_list())
    valid_scopes = set(validation.get_column("statement_scope").drop_nulls().to_list())
    if (
        len(scopes) != 1
        or scopes != valid_scopes
        or train.get_column("statement_scope").null_count()
        or validation.get_column("statement_scope").null_count()
    ):
        raise ContractViolationError(
            "Train and validation require one identical statement_scope."
        )
    taxonomies = set(train.get_column("taxonomy_version").drop_nulls().to_list())
    valid_taxonomies = set(
        validation.get_column("taxonomy_version").drop_nulls().to_list()
    )
    if (
        len(taxonomies) != 1
        or len(valid_taxonomies) != 1
        or taxonomies != valid_taxonomies
        or train.get_column("taxonomy_version").null_count()
        or validation.get_column("taxonomy_version").null_count()
    ):
        raise ContractViolationError(
            "Train and validation require compatible taxonomy versions."
        )
    prep = CrossSectionalPreprocessor(features).fit(train)
    model_features = _model_features(features, prep)
    x_train = prep.transform(train).select(model_features).to_numpy()
    x_valid = prep.transform(validation).select(model_features).to_numpy()
    pos_count = int((y_train == 1).sum())
    neg_count = int((y_train == 0).sum())
    scale_pos_weight = float(neg_count / pos_count) if neg_count > pos_count else 1.0

    from xgboost import XGBClassifier

    classifier = XGBClassifier(
        objective="binary:logistic",
        max_depth=4,
        min_child_weight=10,
        subsample=0.8,
        colsample_bytree=0.8,
        learning_rate=0.05,
        reg_lambda=5.0,
        reg_alpha=1.0,
        scale_pos_weight=scale_pos_weight,
        eval_metric="aucpr",
        monotone_constraints=_constraints(model_features),
        random_state=42,
        n_jobs=1,
    )
    classifier.fit(x_train, y_train)
    valid_groups = (
        validation.get_column("issuer_id").to_numpy()
        if "issuer_id" in validation.columns
        else None
    )
    calibrated = MarginPlattCalibrator(classifier).fit(
        x_valid, y_valid, groups=valid_groups
    )
    baseline = LogisticRegression(
        penalty="l2", C=0.1, class_weight="balanced", max_iter=1000, random_state=42
    )
    baseline.fit(x_train, y_train)

    preliminary = TrainingResult(
        model_features,
        prep,
        classifier,
        calibrated,
        baseline,
        {},
        platt_slope=calibrated.slope,
        platt_intercept=calibrated.intercept,
    )
    y_val, alt_prob, log_prob, xgb_prob, common = _score_three_models(
        preliminary, validation, target=target
    )
    oof_xgb_prob = calibrated.oof_probabilities
    common_oof = common & np.isfinite(oof_xgb_prob)
    thresholds = {
        "Baseline_1_AltmanZ": _optimal_f2_threshold(y_val[common], alt_prob[common]),
        "Baseline_2_Logit": _optimal_f2_threshold(y_val[common], log_prob[common]),
        "XGBoost": _optimal_f2_threshold(y_val[common_oof], oof_xgb_prob[common_oof]),
    }
    oof_metrics = _metrics(
        y_val[common_oof],
        oof_xgb_prob[common_oof],
        f2_threshold=thresholds["XGBoost"],
    )
    scores = {
        "Baseline_1_AltmanZ": _metrics(
            y_val[common],
            alt_prob[common],
            f2_threshold=thresholds["Baseline_1_AltmanZ"],
        ),
        "Baseline_2_Logit": _metrics(
            y_val[common],
            log_prob[common],
            f2_threshold=thresholds["Baseline_2_Logit"],
        ),
        "XGBoost": _metrics(
            y_val[common],
            xgb_prob[common],
            f2_threshold=thresholds["XGBoost"],
        ),
    }
    return TrainingResult(
        model_features,
        prep,
        classifier,
        calibrated,
        baseline,
        scores,
        thresholds,
        platt_slope=calibrated.slope,
        platt_intercept=calibrated.intercept,
        oof_validation_metrics=oof_metrics,
    )


def evaluate_distress_models(
    result: TrainingResult,
    fold: pl.DataFrame,
    *,
    target: str = "distress_target",
    evaluation_role: str = "OUT_OF_SAMPLE_TEST",
) -> dict[str, Any]:
    """Evaluate Altman Z'', L2 Logit and calibrated XGBoost on a common sample."""
    prepared = _prepare_eval_fold(fold, target=target)
    if prepared.is_empty():
        return {
            "evaluation_role": evaluation_role,
            "total_sample_count": 0,
            "common_sample_count": 0,
            "positive_count": 0,
            "prevalence": None,
            "models": {
                "Baseline_1_AltmanZ": _metrics(
                    np.empty(0, dtype=np.int8), np.empty(0, dtype=np.float64)
                ),
                "Baseline_2_Logit": _metrics(
                    np.empty(0, dtype=np.int8), np.empty(0, dtype=np.float64)
                ),
                "XGBoost": _metrics(
                    np.empty(0, dtype=np.int8), np.empty(0, dtype=np.float64)
                ),
            },
            "full_fold_models": {},
            "by_fiscal_year": {},
        }
    y, alt_prob, log_prob, xgb_prob, common = _score_three_models(
        result, prepared, target=target
    )
    alt_thr = result.f2_thresholds.get("Baseline_1_AltmanZ", 0.5)
    log_thr = result.f2_thresholds.get("Baseline_2_Logit", 0.5)
    xgb_thr = result.f2_thresholds.get("XGBoost", 0.5)
    y_common = y[common]
    by_year: dict[str, Any] = {}
    if "fiscal_year" in prepared.columns:
        years = prepared.get_column("fiscal_year").to_numpy()
        unique_years = sorted(
            int(v)
            for v in prepared.get_column("fiscal_year").drop_nulls().unique().to_list()
        )
        for year in unique_years:
            year_mask = (years == year) & common
            y_year = y[year_mask]
            by_year[str(year)] = {
                "sample_count": int(year_mask.sum()),
                "positive_count": int((y_year == 1).sum()),
                "Baseline_1_AltmanZ": _metrics(
                    y_year, alt_prob[year_mask], f2_threshold=alt_thr
                ),
                "Baseline_2_Logit": _metrics(
                    y_year, log_prob[year_mask], f2_threshold=log_thr
                ),
                "XGBoost": _metrics(y_year, xgb_prob[year_mask], f2_threshold=xgb_thr),
            }
    return {
        "evaluation_role": evaluation_role,
        "total_sample_count": int(len(y)),
        "common_sample_count": int(common.sum()),
        "dropped_missing_altman_count": int((~common).sum()),
        "positive_count": int((y_common == 1).sum()),
        "prevalence": float(y_common.mean()) if y_common.size > 0 else None,
        "frozen_validation_f2_thresholds": {
            "Baseline_1_AltmanZ": alt_thr,
            "Baseline_2_Logit": log_thr,
            "XGBoost": xgb_thr,
        },
        "models": {
            "Baseline_1_AltmanZ": _metrics(
                y_common, alt_prob[common], f2_threshold=alt_thr
            ),
            "Baseline_2_Logit": _metrics(
                y_common, log_prob[common], f2_threshold=log_thr
            ),
            "XGBoost": _metrics(y_common, xgb_prob[common], f2_threshold=xgb_thr),
        },
        "full_fold_models": {
            "Baseline_2_Logit": _metrics(y, log_prob, f2_threshold=log_thr),
            "XGBoost": _metrics(y, xgb_prob, f2_threshold=xgb_thr),
        },
        "by_fiscal_year": by_year,
    }


def build_evaluation_report(
    result: TrainingResult,
    *,
    train: pl.DataFrame,
    validation: pl.DataFrame,
    test: pl.DataFrame,
    target: str = "distress_target",
) -> dict[str, Any]:
    """Build the Spec 5.3.4 evaluation report separating calibration and OOS test."""
    prepared_train = _prepare_eval_fold(train, target=target)
    y_train = prepared_train[target].to_numpy().astype(np.int8)
    validation_eval = evaluate_distress_models(
        result,
        validation,
        target=target,
        evaluation_role="IN_SAMPLE_CALIBRATION_FOLD_DO_NOT_USE_AS_OOS_CLAIM",
    )
    validation_eval["oof_summary"] = dict(result.oof_validation_metrics)
    return {
        "model_version": MODEL_VERSION,
        "model_role": "SCREENING_INDICATOR",
        "target_name": target,
        "platt_slope": result.platt_slope,
        "platt_intercept": result.platt_intercept,
        "f2_thresholds": dict(result.f2_thresholds),
        "train_summary": {
            "sample_count": int(len(y_train)),
            "positive_count": int((y_train == 1).sum()),
            "prevalence": float(y_train.mean()) if y_train.size > 0 else None,
            "unique_issuers": int(prepared_train["issuer_id"].n_unique())
            if "issuer_id" in prepared_train.columns
            else None,
        },
        "validation": validation_eval,
        "test": evaluate_distress_models(
            result,
            test,
            target=target,
            evaluation_role="OUT_OF_SAMPLE_TEST",
        ),
    }


def score_distress_model(result: TrainingResult, frame: pl.DataFrame) -> pl.DataFrame:
    """Return calibrated screening probabilities while preserving row order and keys."""
    present_keys = [name for name in ML_SCORES_KEY if name in frame.columns]
    if frame.is_empty():
        empty_scores = pl.DataFrame(
            schema={
                "distress_risk_score": pl.Float64,
                "calibrated_distress_prob": pl.Float64,
                "model_version": pl.String,
                "model_role": pl.String,
            }
        )
        return (
            frame.select(present_keys).hstack(empty_scores)
            if present_keys
            else empty_scores
        )
    matrix = result.preprocessor.transform(frame).select(result.features).to_numpy()
    raw_margin = np.asarray(
        result.classifier.predict(matrix, output_margin=True), dtype=np.float64
    )
    if isinstance(result.calibrated, MarginPlattCalibrator):
        probability = np.asarray(
            result.calibrated.predict_margin_proba(raw_margin)[:, 1], dtype=np.float64
        )
    else:
        probability = np.asarray(
            result.calibrated.predict_proba(matrix)[:, 1], dtype=np.float64
        )
    output = pl.DataFrame(
        {
            "distress_risk_score": raw_margin,
            "calibrated_distress_prob": probability,
            "model_version": [MODEL_VERSION] * frame.height,
            "model_role": ["SCREENING_INDICATOR"] * frame.height,
        }
    )
    return frame.select(present_keys).hstack(output) if present_keys else output
