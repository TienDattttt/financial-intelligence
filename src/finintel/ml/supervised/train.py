"""Supervised training, unpenalized Platt OOF calibration, 2-way walk-forward & PSI audit.

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
    docs/refactor/02_ML_DATASET_AND_LABELING_PLAN.md (ML-06, GATE-ML-3..5, B-02, H-07, M-05).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
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
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import (  # type: ignore[import-untyped]
    StratifiedGroupKFold,
    StratifiedKFold,
)

from finintel.exceptions import ContractViolationError
from finintel.ml.supervised.temporal_split import (
    SECTOR_RAW_TO_Z,
    build_expanding_walk_forward_splits,
)
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
MODEL_VERSION = "xgboost-layer4b-v2"
ALTMAN_DISTRESS_CUTOFF = 1.10
PLATT_MIN_SLOPE = 0.15
PLATT_MAX_SLOPE = 5.0
LOGIT_CLIP_RANGE: tuple[float, float] = (-5.0, 5.0)
XGB_MAX_ESTIMATORS = 400
XGB_EARLY_STOPPING_ROUNDS = 25
XGB_MIN_ESTIMATORS = 15
XGB_MAX_REFIT_ESTIMATORS = 300
# Constant target weighted odds ratio r = (pos * spw) / neg across inner OOF folds & full train.
# Anchoring base_score = r / (1 + r) keeps the initial log-odds origin invariant across expanding
# folds (preventing XGBoost 2.x base_score=y_tr.mean() drift from distorting Platt calibration)
# while starting at weighted equilibrium from Tree #1 (avoiding max_delta_step floor clipping).
XGB_TARGET_EFFECTIVE_ODDS = 0.15
XGB_EQUILIBRIUM_BASE_SCORE: float = XGB_TARGET_EFFECTIVE_ODDS / (
    1.0 + XGB_TARGET_EFFECTIVE_ODDS
)
XGB_MAX_SCALE_POS_WEIGHT = 20.0
RISK_TIER_LABELS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5")
RISK_TIER_QUANTILES: tuple[float, float, float, float] = (0.50, 0.75, 0.90, 0.95)
DEFAULT_RISK_TIER_CUTOFFS: tuple[float, float, float, float] = (
    0.010,
    0.025,
    0.060,
    0.150,
)


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
    scaled_margin = slope * np.asarray(raw_margin, dtype=np.float64)
    finite_mask = np.isfinite(scaled_margin)
    if not np.any(finite_mask):
        return 0.0
    s_fin = scaled_margin[finite_mask]
    y_fin = np.asarray(y, dtype=np.float64)[finite_mask]
    base_rate = float(np.clip(np.mean(y_fin), 1e-6, 1.0 - 1e-6))
    logit_base = float(np.log(base_rate / (1.0 - base_rate)))
    center = -float(np.median(s_fin))
    lower = min(center - 30.0, logit_base - float(np.max(s_fin)) - 1.0)
    upper = max(center + 30.0, logit_base - float(np.min(s_fin)) + 1.0)

    def _bce_loss(intercept: float) -> float:
        z = s_fin + intercept
        return float(np.sum(np.logaddexp(0.0, z) - y_fin * z))

    result = minimize_scalar(
        _bce_loss,
        bounds=(lower, upper),
        method="bounded",
        options={"xatol": 1e-10},
    )
    return float(result.x) if np.isfinite(result.x) else center


def _fit_platt_params(
    raw_margin: NDArray[np.float64],
    y: NDArray[np.int8],
) -> tuple[float, float]:
    """Fit unpenalized 1D Platt scaling on raw_margin with positive slope clamped to [0.15, 5.0] (H-07)."""
    if raw_margin.size == 0 or np.unique(y).size < 2:
        base_rate = float(np.clip(np.mean(y) if y.size > 0 else 0.5, 1e-6, 1.0 - 1e-6))
        return 1.0, float(np.log(base_rate / (1.0 - base_rate)))
    if float(np.std(raw_margin)) < 1e-12:
        return 1.0, _solve_monotone_mle_intercept(raw_margin, y, slope=1.0)
    lr = LogisticRegression(
        penalty=None,
        solver="lbfgs",
        max_iter=1000,
        random_state=42,
    )
    lr.fit(raw_margin[:, None], y)
    raw_slope = float(lr.coef_[0, 0])
    raw_intercept = float(lr.intercept_[0])
    if not np.isfinite(raw_slope) or not np.isfinite(raw_intercept) or raw_slope <= 0.0:
        return 1.0, _solve_monotone_mle_intercept(raw_margin, y, slope=1.0)
    if raw_slope < PLATT_MIN_SLOPE:
        return PLATT_MIN_SLOPE, _solve_monotone_mle_intercept(
            raw_margin, y, slope=PLATT_MIN_SLOPE
        )
    if raw_slope > PLATT_MAX_SLOPE:
        return PLATT_MAX_SLOPE, _solve_monotone_mle_intercept(
            raw_margin, y, slope=PLATT_MAX_SLOPE
        )
    return raw_slope, raw_intercept


class MarginPlattCalibrator:
    """Unpenalized Platt scaling calibrator fitted on 1D log-odds raw_margin with OOF CV."""

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
        """Fit OOF and full-fold unpenalized Platt scaling directly on a 1D raw_margin vector."""
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
    """Fitted models, fair 3-model Platt calibrators, feature contract and OOF metrics."""

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
    logit_preprocessor: CrossSectionalPreprocessor | None = None
    calibrated_altman: MarginPlattCalibrator | None = None
    calibrated_logit: MarginPlattCalibrator | None = None
    train_prevalence: float = 0.0
    best_n_estimators: int = 100
    scale_pos_weight: float = 1.0
    min_child_weight: float = 10.0
    oof_strategy: str = "VALIDATION_HOLD_OUT"
    oof_sample_count: int = 0
    oof_positive_count: int = 0
    oof_calibrated_probs: NDArray[np.float64] = field(
        default_factory=lambda: np.empty(0, dtype=np.float64)
    )
    oof_targets: NDArray[np.int8] = field(
        default_factory=lambda: np.empty(0, dtype=np.int8)
    )
    altman_platt_slope: float = 1.0
    altman_platt_intercept: float = 0.0
    logit_platt_slope: float = 1.0
    logit_platt_intercept: float = 0.0
    risk_tier_cutoffs: tuple[float, float, float, float] = DEFAULT_RISK_TIER_CUTOFFS


def compute_ttc_risk_tier_cutoffs(
    oof_probs: NDArray[np.floating[Any]],
    *,
    base_rate: float,
) -> tuple[float, float, float, float]:
    """Freeze 4 strictly increasing Through-the-Cycle PD cutoffs (R1..R5) from OOF probabilities (H-07)."""
    arr = np.asarray(oof_probs, dtype=np.float64).reshape(-1)
    fin = arr[np.isfinite(arr)]
    safe_base = float(
        np.clip(
            base_rate if np.isfinite(base_rate) and base_rate > 0 else 0.02, 1e-4, 0.5
        )
    )
    fallback = (
        float(np.clip(0.5 * safe_base, 1e-4, 0.95)),
        float(np.clip(1.0 * safe_base, 2e-4, 0.96)),
        float(np.clip(2.5 * safe_base, 3e-4, 0.97)),
        float(np.clip(5.0 * safe_base, 4e-4, 0.98)),
    )
    if fin.size < 5:
        return fallback
    raw_q = [float(np.quantile(fin, q)) for q in RISK_TIER_QUANTILES]
    resolved: list[float] = []
    for idx, val in enumerate(raw_q):
        c = float(np.clip(val, 1e-6, 1.0 - 1e-6))
        if idx > 0 and c <= resolved[-1]:
            # Break flat quantile ties deterministically while staying inside (prev, 1.0)
            alt = max(fallback[idx], resolved[-1] * 1.25 + 1e-4)
            c = float(np.clip(alt, resolved[-1] + 1e-6, 1.0 - 1e-6 * (4 - idx)))
        resolved.append(c)
    return (resolved[0], resolved[1], resolved[2], resolved[3])


def assign_risk_tiers(
    probs: NDArray[np.floating[Any]],
    cutoffs: tuple[float, float, float, float],
) -> list[str | None]:
    """Assign Through-the-Cycle absolute credit tiers R1..R5 using frozen PD cutoffs (H-07)."""
    c1, c2, c3, c4 = cutoffs
    values = np.asarray(probs, dtype=np.float64).reshape(-1)
    tiers: list[str | None] = []
    for raw in values:
        val = float(raw)
        if not np.isfinite(val):
            tiers.append(None)
        elif val < c1:
            tiers.append("R1")
        elif val < c2:
            tiers.append("R2")
        elif val < c3:
            tiers.append("R3")
        elif val < c4:
            tiers.append("R4")
        else:
            tiers.append("R5")
    return tiers


def compute_cross_sectional_risk_percentile(frame: pl.DataFrame) -> pl.DataFrame:
    """Compute PIT cross-sectional risk_market_percentile (0..100) within each reporting period."""
    if frame.is_empty() or "calibrated_distress_prob" not in frame.columns:
        return frame
    group_cols = [
        col
        for col in ("statement_scope", "period_type", "period_end_date")
        if col in frame.columns
    ]
    finite_prob = (
        pl.when(pl.col("calibrated_distress_prob").is_finite())
        .then(pl.col("calibrated_distress_prob").cast(pl.Float64))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    if group_cols:
        rank_expr = finite_prob.rank(method="average").over(group_cols)
        count_expr = finite_prob.count().over(group_cols)
    else:
        rank_expr = finite_prob.rank(method="average")
        count_expr = finite_prob.count()
    pct_expr = (
        pl.when(finite_prob.is_not_null() & (count_expr > 0))
        .then((100.0 * rank_expr / count_expr.cast(pl.Float64)).clip(0.0, 100.0))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("risk_market_percentile")
    )
    return frame.with_columns(pct_expr)


def evaluate_risk_tier_reliability(
    y: NDArray[np.int8],
    probs: NDArray[np.floating[Any]],
    cutoffs: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Summarize empirical event rate and mean predicted PD across R1..R5 tiers."""
    y_arr = np.asarray(y, dtype=np.int8).reshape(-1)
    p_arr = np.asarray(probs, dtype=np.float64).reshape(-1)
    finite = np.isfinite(p_arr)
    y_fin = y_arr[finite]
    p_fin = p_arr[finite]
    tiers = assign_risk_tiers(p_fin, cutoffs)
    n_total = max(1, len(y_fin))
    rows: list[dict[str, Any]] = []
    observed_rates: list[float] = []
    for label in RISK_TIER_LABELS:
        mask = np.array([t == label for t in tiers], dtype=bool)
        cnt = int(mask.sum())
        pos = int((y_fin[mask] == 1).sum()) if cnt > 0 else 0
        emp_rate = float(pos / cnt) if cnt > 0 else None
        mean_pd = float(np.mean(p_fin[mask])) if cnt > 0 else None
        if emp_rate is not None:
            observed_rates.append(emp_rate)
        rows.append(
            {
                "risk_tier": label,
                "count": cnt,
                "share": float(cnt / n_total) if len(y_fin) > 0 else 0.0,
                "positive_count": pos,
                "empirical_event_rate": emp_rate,
                "mean_predicted_pd": mean_pd,
            }
        )
    monotone_non_decreasing = all(
        observed_rates[i] <= observed_rates[i + 1] + 1e-12
        for i in range(len(observed_rates) - 1)
    )
    return {
        "cutoffs": {
            "R2_min_pd": cutoffs[0],
            "R3_min_pd": cutoffs[1],
            "R4_min_pd": cutoffs[2],
            "R5_min_pd": cutoffs[3],
        },
        "tiers": rows,
        "is_monotone_non_decreasing": monotone_non_decreasing,
    }


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


def _compute_xgb_regularization(pos_count: int, neg_count: int) -> tuple[float, float]:
    """Compute fold-invariant equilibrium scale_pos_weight and leaf-floor min_child_weight.

    Sets ``scale_pos_weight = clamp(r * neg / pos, 1.0, XGB_MAX_SCALE_POS_WEIGHT)`` with
    constant target weighted odds ``r = XGB_TARGET_EFFECTIVE_ODDS`` so every expanding inner OOF
    fold and full-train refit share the same weighted prior ``XGB_EQUILIBRIUM_BASE_SCORE``.
    Scales ``min_child_weight`` proportionally to ``0.25 * scale_pos_weight`` so single-positive
    leaves remain mathematically forbidden.
    """
    total = max(1, pos_count + neg_count)
    raw_imbalance = (
        float(neg_count / max(1, pos_count)) if neg_count > pos_count else 1.0
    )
    scale_pos_weight = float(
        min(
            XGB_MAX_SCALE_POS_WEIGHT,
            max(1.0, XGB_TARGET_EFFECTIVE_ODDS * raw_imbalance),
        )
    )
    # Single positive max Hessian is 0.25 * scale_pos_weight. Require >= k_pos_per_leaf
    # positive-equivalent samples per leaf (>= 2.0 on tiny fixtures, up to 5.0 on real data).
    k_pos_per_leaf = min(5.0, max(2.0, 0.20 * float(pos_count)))
    min_base_weight = min(10.0, max(2.0, 0.05 * float(total)))
    min_child_weight = float(
        max(min_base_weight, k_pos_per_leaf * 0.25 * scale_pos_weight)
    )
    return scale_pos_weight, min_child_weight


def _compute_xgb_base_score(
    pos_count: int, neg_count: int, scale_pos_weight: float
) -> float:
    """Return the exact weighted-class equilibrium prior for XGBoost's initial margin.

    For imbalanced distress folds (``neg / pos`` in ``[1/r, 20/r]``), this equals the
    constant ``XGB_EQUILIBRIUM_BASE_SCORE = r / (1 + r)`` across every expanding inner OOF
    fold and full-train refit. For balanced datasets (``scale_pos_weight == 1.0``), it equals
    the true class proportion so Tree #1 always starts at weighted equilibrium.
    """
    weighted_pos = float(max(1, pos_count)) * float(scale_pos_weight)
    denom = weighted_pos + float(max(1, neg_count))
    return float(np.clip(weighted_pos / denom, 0.05, 0.95))


def _make_xgb_classifier(
    *,
    model_features: tuple[str, ...],
    scale_pos_weight: float,
    min_child_weight: float,
    n_estimators: int,
    base_score: float = XGB_EQUILIBRIUM_BASE_SCORE,
    early_stopping_rounds: int | None = None,
) -> Any:
    from xgboost import XGBClassifier

    kwargs: dict[str, Any] = {
        "objective": "binary:logistic",
        # Anchor initial margin at exact weighted equilibrium across all expanding inner OOF
        # folds and full-train refit (zero fold-prevalence origin shift into Platt calibration,
        # zero Hessian starvation at low prevalence, and zero max_delta_step floor clipping).
        "base_score": float(base_score),
        "max_depth": 3,
        "min_child_weight": min_child_weight,
        "subsample": 0.75,
        "colsample_bytree": 0.7,
        "learning_rate": 0.03,
        "n_estimators": n_estimators,
        "max_delta_step": 1.0,
        "reg_lambda": 10.0,
        "reg_alpha": 1.5,
        "scale_pos_weight": scale_pos_weight,
        "eval_metric": "aucpr",
        "monotone_constraints": _constraints(model_features),
        "random_state": 42,
        "n_jobs": 1,
    }
    if early_stopping_rounds is not None:
        kwargs["early_stopping_rounds"] = early_stopping_rounds
    return XGBClassifier(**kwargs)


def _make_logit_classifier() -> Any:
    """Shumway (2001) discrete-time hazard L2 logistic regression baseline."""
    return LogisticRegression(
        penalty="l2",
        C=0.1,
        class_weight="balanced",
        max_iter=1000,
        random_state=42,
    )


def _build_group_or_stratified_oof_folds(
    train: pl.DataFrame,
    y_train: NDArray[np.int8],
) -> tuple[str, list[tuple[NDArray[np.intp], NDArray[np.intp]]]]:
    """Build issuer-grouped StratifiedGroupKFold (or StratifiedKFold) inner OOF folds."""
    n_rows = len(y_train)
    if "issuer_id" in train.columns:
        groups = train.get_column("issuer_id").to_numpy()
        if groups.size == n_rows and not any(g is None for g in groups):
            pos_groups = set(groups[y_train == 1].tolist())
            neg_groups = set(groups[y_train == 0].tolist())
            all_groups = set(groups.tolist())
            if len(pos_groups) >= 2 and len(neg_groups) >= 2 and len(all_groups) >= 2:
                k_group = min(5, len(pos_groups), len(neg_groups), len(all_groups))
                if k_group >= 2:
                    sgkf = StratifiedGroupKFold(n_splits=k_group)
                    group_folds = [
                        (tr_idx, va_idx)
                        for tr_idx, va_idx in sgkf.split(
                            np.zeros((n_rows, 1)), y_train, groups
                        )
                        if np.unique(y_train[tr_idx]).size == 2
                        and np.unique(y_train[va_idx]).size == 2
                    ]
                    if group_folds:
                        return "STRATIFIED_GROUP_OOF", group_folds

    min_class = int(min((y_train == 0).sum(), (y_train == 1).sum()))
    k_strat = max(2, min(5, min_class))
    skf = StratifiedKFold(n_splits=k_strat, shuffle=True, random_state=42)
    strat_folds = [
        (tr_idx, va_idx)
        for tr_idx, va_idx in skf.split(np.zeros((n_rows, 1)), y_train)
        if np.unique(y_train[tr_idx]).size == 2 and np.unique(y_train[va_idx]).size == 2
    ]
    return "STRATIFIED_OOF", strat_folds


def _build_inner_oof_folds(
    train: pl.DataFrame,
    y_train: NDArray[np.int8],
) -> tuple[str, list[tuple[NDArray[np.intp], NDArray[np.intp]]]]:
    """Build inner Purged Temporal OOF folds inside train, with issuer-grouped fallback."""
    n_rows = len(y_train)
    all_idx = np.arange(n_rows, dtype=np.intp)

    # Priority 1: Purged Expanding Temporal OOF when temporal columns span enough history
    if {"period_end_date", "_period_available_at", "label_available_at"} <= set(
        train.columns
    ):
        obs = train.get_column("period_end_date").cast(pl.Date).to_numpy()
        avail = train.get_column("_period_available_at").cast(pl.Date).to_numpy()
        label_avail = train.get_column("label_available_at").cast(pl.Date).to_numpy()
        unique_dates = np.unique(obs)
        if unique_dates.size >= 4:
            issuers_arr = (
                train.get_column("issuer_id").to_numpy()
                if "issuer_id" in train.columns
                else None
            )
            n_blocks = min(5, max(2, int(unique_dates.size // 2)))
            blocks = [b for b in np.array_split(unique_dates, n_blocks) if b.size > 0]
            temporal_folds: list[tuple[NDArray[np.intp], NDArray[np.intp]]] = []
            for block in blocks[1:]:
                val_start = block[0]
                val_end = block[-1]
                va_mask = (obs >= val_start) & (obs <= val_end)
                tr_mask = (
                    (obs < val_start)
                    & (avail <= val_start)
                    & (label_avail <= val_start)
                )
                tr_idx = all_idx[tr_mask]
                va_idx = all_idx[va_mask]
                if tr_idx.size < 4 or va_idx.size < 2:
                    continue
                y_tr = y_train[tr_idx]
                y_va = y_train[va_idx]
                if int((y_tr == 1).sum()) < 2 or int((y_tr == 0).sum()) < 2:
                    continue
                if int((y_va == 1).sum()) < 1 or int((y_va == 0).sum()) < 1:
                    continue
                if issuers_arr is not None:
                    tr_pos_iss = len(set(issuers_arr[tr_idx][y_tr == 1].tolist()))
                    if tr_pos_iss < 2:
                        continue
                temporal_folds.append((tr_idx, va_idx))

            if temporal_folds:
                covered_va = np.concatenate([va for _, va in temporal_folds])
                cov_pos = int((y_train[covered_va] == 1).sum())
                cov_neg = int((y_train[covered_va] == 0).sum())
                if (len(temporal_folds) >= 2 and cov_pos >= 2 and cov_neg >= 2) or (
                    len(temporal_folds) == 1 and cov_pos >= 4 and cov_neg >= 4
                ):
                    return "PURGED_TEMPORAL_OOF", temporal_folds

    return _build_group_or_stratified_oof_folds(train, y_train)


def _optimal_f2_threshold(y: NDArray[np.int8], score: NDArray[np.float64]) -> float:
    """Select the F2-maximizing operating threshold strictly on validation/OOF in O(N log N).

    Uses Lipton, Elkan & Naryanaswamy (ECML-PKDD 2014) exact sort-and-scan via
    ``precision_recall_curve`` (evaluates ``score >= threshold`` for every unique score).
    """
    finite = np.isfinite(score)
    y_fin = y[finite]
    s_fin = score[finite]
    if y_fin.size == 0 or np.unique(y_fin).size < 2:
        return 0.5
    precisions, recalls, thresholds = precision_recall_curve(y_fin, s_fin)
    if thresholds.size == 0:
        return 0.5
    p_thr = precisions[:-1]
    r_thr = recalls[:-1]
    denom = 4.0 * p_thr + r_thr
    f2 = np.where(
        denom > 0.0, (5.0 * p_thr * r_thr) / np.where(denom > 0.0, denom, 1.0), 0.0
    )
    best_idx = int(np.argmax(f2))
    return float(thresholds[best_idx])


def _metrics(
    y: NDArray[np.int8],
    score: NDArray[np.float64],
    *,
    f2_threshold: float = 0.5,
    reference_prevalence: float | NDArray[np.floating[Any]] | None = None,
) -> dict[str, float | None]:
    finite = np.isfinite(score)
    y = y[finite]
    score = score[finite]
    if y.size == 0 or np.unique(y).size < 2:
        return {
            "pr_auc": None,
            "roc_auc": None,
            "brier_score": None,
            "brier_skill_score": None,
            "f2_score": None,
            "recall_at_top_5_percent": None,
            "recall_at_top_10_percent": None,
        }
    order = np.argsort(-score, kind="mergesort")
    cutoff_5 = max(1, int(np.ceil(len(score) * 0.05)))
    cutoff_10 = max(1, int(np.ceil(len(score) * 0.10)))
    total_pos = max(1, int(y.sum()))
    brier = float(brier_score_loss(y, score))
    # GATE-ML-3 / H-07: BSS climatology uses strictly historical train_prevalence
    # (scalar for a single fold, or per-row PIT train_prevalence_Y for pooled walk-forward),
    # NEVER y_test.mean() or future folds' prevalences.
    bss: float | None = None
    if reference_prevalence is not None:
        if isinstance(reference_prevalence, np.ndarray):
            ref_arr = np.asarray(reference_prevalence, dtype=np.float64).reshape(-1)
            if ref_arr.size == finite.size:
                ref_vec = ref_arr[finite]
            elif ref_arr.size == y.size:
                ref_vec = ref_arr
            else:
                raise ContractViolationError(
                    "Per-observation reference_prevalence length must match y."
                )
            if np.all(np.isfinite(ref_vec)):
                clim_brier = float(brier_score_loss(y, np.clip(ref_vec, 0.0, 1.0)))
                if clim_brier > 1e-12:
                    bss = float(1.0 - brier / clim_brier)
        elif np.isfinite(reference_prevalence):
            p0 = float(np.clip(reference_prevalence, 0.0, 1.0))
            clim_brier = float(
                brier_score_loss(y, np.full(len(y), p0, dtype=np.float64))
            )
            if clim_brier > 1e-12:
                bss = float(1.0 - brier / clim_brier)
    return {
        "pr_auc": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
        "brier_score": brier,
        "brier_skill_score": bss,
        "f2_score": float(
            fbeta_score(y, score >= f2_threshold, beta=2, zero_division=0)
        ),
        "recall_at_top_5_percent": float(y[order[:cutoff_5]].sum() / total_pos),
        "recall_at_top_10_percent": float(y[order[:cutoff_10]].sum() / total_pos),
    }


def _altman_margin(score: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
    """Negated Altman Z'' margin centered at 1.10 so larger values indicate higher risk."""
    values = np.asarray(score, dtype=np.float64)
    return -(values - ALTMAN_DISTRESS_CUTOFF)


def _altman_probability(score: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
    """Map Altman Z'' monotonically to [0, 1] centered at the 1.10 distress zone cutoff."""
    return _sigmoid(_altman_margin(score))


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
    matrix_xgb = result.preprocessor.transform(fold).select(result.features).to_numpy()
    logit_prep = (
        result.logit_preprocessor
        if result.logit_preprocessor is not None
        else result.preprocessor
    )
    matrix_logit = logit_prep.transform(fold).select(result.features).to_numpy()

    if result.calibrated_logit is not None:
        logit_margin = np.asarray(
            result.baseline_logit.decision_function(matrix_logit), dtype=np.float64
        )
        logit_prob = np.asarray(
            result.calibrated_logit.predict_margin_proba(logit_margin)[:, 1],
            dtype=np.float64,
        )
    else:
        logit_prob = np.asarray(
            result.baseline_logit.predict_proba(matrix_logit)[:, 1], dtype=np.float64
        )

    xgb_prob = np.asarray(
        result.calibrated.predict_proba(matrix_xgb)[:, 1], dtype=np.float64
    )
    raw_altman = np.asarray(
        fold.get_column("altman_z_double_prime").to_numpy(), dtype=np.float64
    )
    altman_finite = np.isfinite(raw_altman)
    altman_prob = np.full(len(raw_altman), np.nan, dtype=np.float64)
    if np.any(altman_finite):
        if result.calibrated_altman is not None:
            altman_prob[altman_finite] = result.calibrated_altman.predict_margin_proba(
                _altman_margin(raw_altman[altman_finite])
            )[:, 1]
        else:
            altman_prob[altman_finite] = _altman_probability(raw_altman[altman_finite])

    common_mask = altman_finite & np.isfinite(logit_prob) & np.isfinite(xgb_prob)
    return y, altman_prob, logit_prob, xgb_prob, common_mask


def train_distress_model(
    train: pl.DataFrame,
    validation: pl.DataFrame | None = None,
    *,
    features: tuple[str, ...] = BASE_FEATURES,
    target: str = "distress_target",
    min_unique_positive_issuers: int = 3,
) -> TrainingResult:
    """Fit anti-overfit Monotone XGBoost, clipped Logit, and fair 3-model Platt calibration.

    Primary 2-Way mode (``validation is None``, B-02):
      - Runs inner Purged Temporal OOF CV inside ``train`` (issuer-grouped fallback if short span).
      - Pass 1 activates real ``early_stopping_rounds`` (``eval_metric="aucpr"``) to select
        ``best_n_estimators``.
      - Pass 2 computes clean inner OOF margins for all 3 models (Altman Z'', Shumway Logit
        with ``clip_range=(-5.0, 5.0)``, and Monotone XGBoost with ``min_child_weight`` scaled
        to ``scale_pos_weight``) and fits unpenalized ``MarginPlattCalibrator(penalty=None)``
        identically for all 3 models on the same common sample.

    Legacy 3-Way mode (``validation is not None``):
      - Selects ``best_n_estimators`` via inner OOF inside ``train``, fits final models on
        ``train``, and calibrates all 3 models identically on ``validation``.
    """
    if train.is_empty():
        raise ContractViolationError(
            "Supervised training requires a nonempty train set."
        )
    if validation is not None and validation.is_empty():
        raise ContractViolationError(
            "Supervised training requires nonempty train and validation sets."
        )
    if set(features) - set(train.columns) or (
        validation is not None and set(features) - set(validation.columns)
    ):
        raise ContractViolationError(
            "Supervised input is missing one or more configured features."
        )
    if not features or len(set(features)) != len(features):
        raise ContractViolationError("Supervised features must be nonempty and unique.")
    if target not in train.columns or (
        validation is not None and target not in validation.columns
    ):
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
    if required - set(train.columns) or (
        validation is not None and required - set(validation.columns)
    ):
        raise ContractViolationError(
            "Supervised inputs require corporate regime and the Altman baseline feature."
        )
    train = _prepare_eval_fold(train, target=target)
    if validation is not None:
        validation = _prepare_eval_fold(validation, target=target)
    if train.is_empty() or (validation is not None and validation.is_empty()):
        raise ContractViolationError(
            "Onset filtering left an empty train or validation fold."
        )
    y_train = train[target].to_numpy().astype(np.int8)
    if np.unique(y_train).size != 2:
        raise ContractViolationError(
            "Train fold requires both distress target classes."
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
    scopes = set(train.get_column("statement_scope").drop_nulls().to_list())
    if len(scopes) != 1 or train.get_column("statement_scope").null_count():
        raise ContractViolationError(
            "Train fold requires one non-null statement_scope."
        )
    taxonomies = set(train.get_column("taxonomy_version").drop_nulls().to_list())
    if len(taxonomies) != 1 or train.get_column("taxonomy_version").null_count():
        raise ContractViolationError(
            "Train fold requires one non-null taxonomy_version."
        )
    if validation is not None:
        y_valid = validation[target].to_numpy().astype(np.int8)
        if np.unique(y_valid).size != 2:
            raise ContractViolationError(
                "Train and validation require both distress target classes."
            )
        min_valid_class = int(min((y_valid == 0).sum(), (y_valid == 1).sum()))
        if min_valid_class < 2:
            raise InsufficientModelData(
                "Probability calibration requires at least two observations per class in validation."
            )
        valid_scopes = set(
            validation.get_column("statement_scope").drop_nulls().to_list()
        )
        if (
            scopes != valid_scopes
            or validation.get_column("statement_scope").null_count()
        ):
            raise ContractViolationError(
                "Train and validation require one identical statement_scope."
            )
        valid_taxonomies = set(
            validation.get_column("taxonomy_version").drop_nulls().to_list()
        )
        if (
            len(valid_taxonomies) != 1
            or taxonomies != valid_taxonomies
            or validation.get_column("taxonomy_version").null_count()
        ):
            raise ContractViolationError(
                "Train and validation require compatible taxonomy versions."
            )

    pos_count = int((y_train == 1).sum())
    neg_count = int((y_train == 0).sum())
    train_prevalence = float(y_train.mean())
    scale_pos_weight, min_child_weight = _compute_xgb_regularization(
        pos_count, neg_count
    )

    prep = CrossSectionalPreprocessor(features).fit(train)
    logit_prep = CrossSectionalPreprocessor(features, clip_range=LOGIT_CLIP_RANGE).fit(
        train
    )
    model_features = _model_features(features, prep)

    # Inner OOF Pass 1: real early_stopping_rounds on inner purged eval_set (eval_metric="aucpr")
    def _run_pass1(
        candidate_folds: list[tuple[NDArray[np.intp], NDArray[np.intp]]],
    ) -> tuple[list[int], list[tuple[NDArray[np.intp], NDArray[np.intp]]]]:
        iters: list[int] = []
        valid_folds: list[tuple[NDArray[np.intp], NDArray[np.intp]]] = []
        for tr_idx, va_idx in candidate_folds:
            tr_df = train[tr_idx.tolist()]
            va_df = train[va_idx.tolist()]
            y_tr = y_train[tr_idx]
            y_va = y_train[va_idx]
            try:
                inner_prep = CrossSectionalPreprocessor(features).fit(tr_df)
            except InsufficientModelData:
                continue
            inner_feats = _model_features(features, inner_prep)
            x_tr_in = inner_prep.transform(tr_df).select(inner_feats).to_numpy()
            x_va_in = inner_prep.transform(va_df).select(inner_feats).to_numpy()
            spw_in, mcw_in = _compute_xgb_regularization(
                int((y_tr == 1).sum()), int((y_tr == 0).sum())
            )
            base_in = _compute_xgb_base_score(
                int((y_tr == 1).sum()), int((y_tr == 0).sum()), spw_in
            )
            inner_clf = _make_xgb_classifier(
                model_features=inner_feats,
                scale_pos_weight=spw_in,
                min_child_weight=mcw_in,
                n_estimators=XGB_MAX_ESTIMATORS,
                base_score=base_in,
                early_stopping_rounds=XGB_EARLY_STOPPING_ROUNDS,
            )
            inner_clf.fit(x_tr_in, y_tr, eval_set=[(x_va_in, y_va)], verbose=False)
            best_iter = int(getattr(inner_clf, "best_iteration", 59)) + 1
            iters.append(best_iter)
            valid_folds.append((tr_idx, va_idx))
        return iters, valid_folds

    oof_strategy, inner_folds = _build_inner_oof_folds(train, y_train)
    best_iterations, valid_inner_folds = _run_pass1(inner_folds)
    if oof_strategy == "PURGED_TEMPORAL_OOF":
        cov_va = (
            np.concatenate([va for _, va in valid_inner_folds])
            if valid_inner_folds
            else np.empty(0, dtype=np.intp)
        )
        cov_min_class = (
            int(min((y_train[cov_va] == 0).sum(), (y_train[cov_va] == 1).sum()))
            if cov_va.size > 0
            else 0
        )
        if cov_min_class < 2:
            oof_strategy, fallback_folds = _build_group_or_stratified_oof_folds(
                train, y_train
            )
            best_iterations, valid_inner_folds = _run_pass1(fallback_folds)

    best_n_estimators = (
        int(
            np.clip(
                np.rint(np.median(best_iterations)),
                XGB_MIN_ESTIMATORS,
                XGB_MAX_REFIT_ESTIMATORS,
            )
        )
        if best_iterations
        else 60
    )

    x_train = prep.transform(train).select(model_features).to_numpy()
    x_train_logit = logit_prep.transform(train).select(model_features).to_numpy()
    base_score_full = _compute_xgb_base_score(pos_count, neg_count, scale_pos_weight)
    classifier = _make_xgb_classifier(
        model_features=model_features,
        scale_pos_weight=scale_pos_weight,
        min_child_weight=min_child_weight,
        n_estimators=best_n_estimators,
        base_score=base_score_full,
    )
    classifier.fit(x_train, y_train)
    baseline = _make_logit_classifier()
    baseline.fit(x_train_logit, y_train)

    if validation is None:
        # 2-Way B-02 path: Pass 2 computes clean inner OOF margins at fixed best_n_estimators
        oof_xgb_margin = np.full(len(y_train), np.nan, dtype=np.float64)
        oof_logit_margin = np.full(len(y_train), np.nan, dtype=np.float64)
        raw_altman_train = np.asarray(
            train.get_column("altman_z_double_prime").to_numpy(), dtype=np.float64
        )
        oof_altman_margin = np.where(
            np.isfinite(raw_altman_train), _altman_margin(raw_altman_train), np.nan
        )

        for tr_idx, va_idx in valid_inner_folds:
            tr_df = train[tr_idx.tolist()]
            va_df = train[va_idx.tolist()]
            y_tr = y_train[tr_idx]
            inner_prep = CrossSectionalPreprocessor(features).fit(tr_df)
            inner_logit_prep = CrossSectionalPreprocessor(
                features, clip_range=LOGIT_CLIP_RANGE
            ).fit(tr_df)
            inner_feats = _model_features(features, inner_prep)
            x_tr_in = inner_prep.transform(tr_df).select(inner_feats).to_numpy()
            x_va_in = inner_prep.transform(va_df).select(inner_feats).to_numpy()
            x_tr_log = inner_logit_prep.transform(tr_df).select(inner_feats).to_numpy()
            x_va_log = inner_logit_prep.transform(va_df).select(inner_feats).to_numpy()
            spw_in, mcw_in = _compute_xgb_regularization(
                int((y_tr == 1).sum()), int((y_tr == 0).sum())
            )
            base_in = _compute_xgb_base_score(
                int((y_tr == 1).sum()), int((y_tr == 0).sum()), spw_in
            )
            oof_clf = _make_xgb_classifier(
                model_features=inner_feats,
                scale_pos_weight=spw_in,
                min_child_weight=mcw_in,
                n_estimators=best_n_estimators,
                base_score=base_in,
            )
            oof_clf.fit(x_tr_in, y_tr)
            oof_xgb_margin[va_idx] = np.asarray(
                oof_clf.predict(x_va_in, output_margin=True), dtype=np.float64
            )
            oof_log = _make_logit_classifier()
            oof_log.fit(x_tr_log, y_tr)
            oof_logit_margin[va_idx] = np.asarray(
                oof_log.decision_function(x_va_log), dtype=np.float64
            )

        oof_covered = np.isfinite(oof_xgb_margin) & np.isfinite(oof_logit_margin)
        oof_common = oof_covered & np.isfinite(oof_altman_margin)
        y_oof_common = y_train[oof_common]
        if int((y_oof_common == 1).sum()) >= 2 and int((y_oof_common == 0).sum()) >= 2:
            cal_mask = oof_common
        else:
            cal_mask = oof_covered
        y_cal = y_train[cal_mask]
        min_cal_class = int(min((y_cal == 0).sum(), (y_cal == 1).sum()))
        if min_cal_class < 2:
            raise InsufficientModelData(
                "Inner OOF probability calibration requires at least two observations per class."
            )
        cal_groups = (
            train.get_column("issuer_id").to_numpy()[cal_mask]
            if "issuer_id" in train.columns
            else None
        )

        calibrated = MarginPlattCalibrator(classifier).fit_margins(
            oof_xgb_margin[cal_mask], y_cal, groups=cal_groups
        )
        calibrated_logit = MarginPlattCalibrator(None).fit_margins(
            oof_logit_margin[cal_mask], y_cal, groups=cal_groups
        )
        alt_cal_mask = cal_mask & np.isfinite(oof_altman_margin)
        y_alt_cal = y_train[alt_cal_mask]
        alt_groups = (
            train.get_column("issuer_id").to_numpy()[alt_cal_mask]
            if "issuer_id" in train.columns
            else None
        )
        calibrated_altman = (
            MarginPlattCalibrator(None).fit_margins(
                oof_altman_margin[alt_cal_mask], y_alt_cal, groups=alt_groups
            )
            if int(min((y_alt_cal == 0).sum(), (y_alt_cal == 1).sum())) >= 2
            else None
        )

        oof_alt_prob = (
            calibrated_altman.oof_probabilities
            if calibrated_altman is not None and alt_cal_mask.sum() == cal_mask.sum()
            else _altman_probability(raw_altman_train[cal_mask])
        )
        oof_log_prob = calibrated_logit.oof_probabilities
        oof_xgb_prob = calibrated.oof_probabilities
        common_eval = (
            np.isfinite(oof_alt_prob)
            & np.isfinite(oof_log_prob)
            & np.isfinite(oof_xgb_prob)
        )
        thresholds = {
            "Baseline_1_AltmanZ": _optimal_f2_threshold(
                y_cal[common_eval], oof_alt_prob[common_eval]
            ),
            "Baseline_2_Logit": _optimal_f2_threshold(
                y_cal[common_eval], oof_log_prob[common_eval]
            ),
            "XGBoost": _optimal_f2_threshold(
                y_cal[common_eval], oof_xgb_prob[common_eval]
            ),
        }
        oof_metrics = _metrics(
            y_cal[common_eval],
            oof_xgb_prob[common_eval],
            f2_threshold=thresholds["XGBoost"],
            reference_prevalence=train_prevalence,
        )
        scores = {
            "Baseline_1_AltmanZ": _metrics(
                y_cal[common_eval],
                oof_alt_prob[common_eval],
                f2_threshold=thresholds["Baseline_1_AltmanZ"],
                reference_prevalence=train_prevalence,
            ),
            "Baseline_2_Logit": _metrics(
                y_cal[common_eval],
                oof_log_prob[common_eval],
                f2_threshold=thresholds["Baseline_2_Logit"],
                reference_prevalence=train_prevalence,
            ),
            "XGBoost": _metrics(
                y_cal[common_eval],
                oof_xgb_prob[common_eval],
                f2_threshold=thresholds["XGBoost"],
                reference_prevalence=train_prevalence,
            ),
        }
        tier_cutoffs = compute_ttc_risk_tier_cutoffs(
            oof_xgb_prob, base_rate=train_prevalence
        )
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
            logit_preprocessor=logit_prep,
            calibrated_altman=calibrated_altman,
            calibrated_logit=calibrated_logit,
            train_prevalence=train_prevalence,
            best_n_estimators=best_n_estimators,
            scale_pos_weight=scale_pos_weight,
            min_child_weight=min_child_weight,
            oof_strategy=oof_strategy,
            oof_sample_count=int(cal_mask.sum()),
            oof_positive_count=int((y_cal == 1).sum()),
            oof_calibrated_probs=oof_xgb_prob.copy(),
            oof_targets=y_cal.copy(),
            altman_platt_slope=calibrated_altman.slope
            if calibrated_altman is not None
            else 1.0,
            altman_platt_intercept=calibrated_altman.intercept
            if calibrated_altman is not None
            else 0.0,
            logit_platt_slope=calibrated_logit.slope,
            logit_platt_intercept=calibrated_logit.intercept,
            risk_tier_cutoffs=tier_cutoffs,
        )

    # Legacy 3-Way path (validation hold-out explicitly supplied): fair 3-model Platt calibration
    x_valid = prep.transform(validation).select(model_features).to_numpy()
    x_valid_logit = logit_prep.transform(validation).select(model_features).to_numpy()
    y_valid = validation[target].to_numpy().astype(np.int8)
    valid_groups = (
        validation.get_column("issuer_id").to_numpy()
        if "issuer_id" in validation.columns
        else None
    )
    calibrated = MarginPlattCalibrator(classifier).fit(
        x_valid, y_valid, groups=valid_groups
    )
    valid_logit_margin = np.asarray(
        baseline.decision_function(x_valid_logit), dtype=np.float64
    )
    calibrated_logit = MarginPlattCalibrator(None).fit_margins(
        valid_logit_margin, y_valid, groups=valid_groups
    )
    raw_altman_valid = np.asarray(
        validation.get_column("altman_z_double_prime").to_numpy(), dtype=np.float64
    )
    alt_valid_mask = np.isfinite(raw_altman_valid)
    y_alt_valid = y_valid[alt_valid_mask]
    alt_valid_groups = (
        valid_groups[alt_valid_mask] if valid_groups is not None else None
    )
    calibrated_altman = (
        MarginPlattCalibrator(None).fit_margins(
            _altman_margin(raw_altman_valid[alt_valid_mask]),
            y_alt_valid,
            groups=alt_valid_groups,
        )
        if int(min((y_alt_valid == 0).sum(), (y_alt_valid == 1).sum())) >= 2
        else None
    )

    preliminary = TrainingResult(
        model_features,
        prep,
        classifier,
        calibrated,
        baseline,
        {},
        platt_slope=calibrated.slope,
        platt_intercept=calibrated.intercept,
        logit_preprocessor=logit_prep,
        calibrated_altman=calibrated_altman,
        calibrated_logit=calibrated_logit,
        train_prevalence=train_prevalence,
        best_n_estimators=best_n_estimators,
        scale_pos_weight=scale_pos_weight,
        min_child_weight=min_child_weight,
        oof_strategy=f"VALIDATION_HOLD_OUT({oof_strategy})",
    )
    y_val, alt_prob, log_prob, xgb_prob, common = _score_three_models(
        preliminary, validation, target=target
    )
    oof_xgb_prob = calibrated.oof_probabilities
    oof_log_prob = calibrated_logit.oof_probabilities
    oof_alt_prob = np.full(len(y_val), np.nan, dtype=np.float64)
    if calibrated_altman is not None:
        oof_alt_prob[alt_valid_mask] = calibrated_altman.oof_probabilities
    else:
        oof_alt_prob = alt_prob
    common_oof = (
        common
        & np.isfinite(oof_alt_prob)
        & np.isfinite(oof_xgb_prob)
        & np.isfinite(oof_log_prob)
    )
    thresholds = {
        "Baseline_1_AltmanZ": _optimal_f2_threshold(
            y_val[common_oof], oof_alt_prob[common_oof]
        ),
        "Baseline_2_Logit": _optimal_f2_threshold(
            y_val[common_oof], oof_log_prob[common_oof]
        ),
        "XGBoost": _optimal_f2_threshold(y_val[common_oof], oof_xgb_prob[common_oof]),
    }
    oof_metrics = _metrics(
        y_val[common_oof],
        oof_xgb_prob[common_oof],
        f2_threshold=thresholds["XGBoost"],
        reference_prevalence=train_prevalence,
    )
    scores = {
        "Baseline_1_AltmanZ": _metrics(
            y_val[common],
            alt_prob[common],
            f2_threshold=thresholds["Baseline_1_AltmanZ"],
            reference_prevalence=train_prevalence,
        ),
        "Baseline_2_Logit": _metrics(
            y_val[common],
            log_prob[common],
            f2_threshold=thresholds["Baseline_2_Logit"],
            reference_prevalence=train_prevalence,
        ),
        "XGBoost": _metrics(
            y_val[common],
            xgb_prob[common],
            f2_threshold=thresholds["XGBoost"],
            reference_prevalence=train_prevalence,
        ),
    }
    tier_cutoffs = compute_ttc_risk_tier_cutoffs(
        oof_xgb_prob, base_rate=train_prevalence
    )
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
        logit_preprocessor=logit_prep,
        calibrated_altman=calibrated_altman,
        calibrated_logit=calibrated_logit,
        train_prevalence=train_prevalence,
        best_n_estimators=best_n_estimators,
        scale_pos_weight=scale_pos_weight,
        min_child_weight=min_child_weight,
        oof_strategy=f"VALIDATION_HOLD_OUT({oof_strategy})",
        oof_sample_count=int(len(y_valid)),
        oof_positive_count=int((y_valid == 1).sum()),
        oof_calibrated_probs=oof_xgb_prob.copy(),
        oof_targets=y_valid.copy(),
        altman_platt_slope=calibrated_altman.slope
        if calibrated_altman is not None
        else 1.0,
        altman_platt_intercept=calibrated_altman.intercept
        if calibrated_altman is not None
        else 0.0,
        logit_platt_slope=calibrated_logit.slope,
        logit_platt_intercept=calibrated_logit.intercept,
        risk_tier_cutoffs=tier_cutoffs,
    )


def evaluate_distress_models(
    result: TrainingResult,
    fold: pl.DataFrame,
    *,
    target: str = "distress_target",
    evaluation_role: str = "OUT_OF_SAMPLE_TEST",
) -> dict[str, Any]:
    """Evaluate calibrated Altman Z'', Shumway Logit and Monotone XGBoost on a common sample."""
    prepared = _prepare_eval_fold(fold, target=target)
    ref_prev = result.train_prevalence if result.train_prevalence > 0.0 else None
    if prepared.is_empty():
        empty_m = _metrics(
            np.empty(0, dtype=np.int8),
            np.empty(0, dtype=np.float64),
            reference_prevalence=ref_prev,
        )
        return {
            "evaluation_role": evaluation_role,
            "total_sample_count": 0,
            "common_sample_count": 0,
            "positive_count": 0,
            "unique_positive_issuers": 0,
            "prevalence": None,
            "reference_train_prevalence": ref_prev,
            "models": {
                "Baseline_1_AltmanZ": dict(empty_m),
                "Baseline_2_Logit": dict(empty_m),
                "XGBoost": dict(empty_m),
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
    issuers_arr = (
        prepared.get_column("issuer_id").to_numpy()
        if "issuer_id" in prepared.columns
        else None
    )
    unique_pos_issuers = (
        int(len(set(issuers_arr[common][y_common == 1].tolist())))
        if issuers_arr is not None
        else None
    )
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
            yr_pos_iss = (
                int(len(set(issuers_arr[year_mask][y_year == 1].tolist())))
                if issuers_arr is not None
                else None
            )
            by_year[str(year)] = {
                "sample_count": int(year_mask.sum()),
                "positive_count": int((y_year == 1).sum()),
                "unique_positive_issuers": yr_pos_iss,
                "Baseline_1_AltmanZ": _metrics(
                    y_year,
                    alt_prob[year_mask],
                    f2_threshold=alt_thr,
                    reference_prevalence=ref_prev,
                ),
                "Baseline_2_Logit": _metrics(
                    y_year,
                    log_prob[year_mask],
                    f2_threshold=log_thr,
                    reference_prevalence=ref_prev,
                ),
                "XGBoost": _metrics(
                    y_year,
                    xgb_prob[year_mask],
                    f2_threshold=xgb_thr,
                    reference_prevalence=ref_prev,
                ),
            }
    return {
        "evaluation_role": evaluation_role,
        "total_sample_count": int(len(y)),
        "common_sample_count": int(common.sum()),
        "dropped_missing_altman_count": int((~common).sum()),
        "positive_count": int((y_common == 1).sum()),
        "unique_positive_issuers": unique_pos_issuers,
        "prevalence": float(y_common.mean()) if y_common.size > 0 else None,
        "reference_train_prevalence": ref_prev,
        "frozen_validation_f2_thresholds": {
            "Baseline_1_AltmanZ": alt_thr,
            "Baseline_2_Logit": log_thr,
            "XGBoost": xgb_thr,
        },
        "models": {
            "Baseline_1_AltmanZ": _metrics(
                y_common,
                alt_prob[common],
                f2_threshold=alt_thr,
                reference_prevalence=ref_prev,
            ),
            "Baseline_2_Logit": _metrics(
                y_common,
                log_prob[common],
                f2_threshold=log_thr,
                reference_prevalence=ref_prev,
            ),
            "XGBoost": _metrics(
                y_common,
                xgb_prob[common],
                f2_threshold=xgb_thr,
                reference_prevalence=ref_prev,
            ),
        },
        "full_fold_models": {
            "Baseline_2_Logit": _metrics(
                y,
                log_prob,
                f2_threshold=log_thr,
                reference_prevalence=ref_prev,
            ),
            "XGBoost": _metrics(
                y,
                xgb_prob,
                f2_threshold=xgb_thr,
                reference_prevalence=ref_prev,
            ),
        },
        "risk_tier_reliability": evaluate_risk_tier_reliability(
            y, xgb_prob, result.risk_tier_cutoffs
        ),
        "by_fiscal_year": by_year,
    }


def build_evaluation_report(
    result: TrainingResult,
    *,
    train: pl.DataFrame,
    validation: pl.DataFrame | None = None,
    test: pl.DataFrame,
    target: str = "distress_target",
) -> dict[str, Any]:
    """Build the Spec 5.3.4 / GATE-ML-3..5 evaluation report separating OOF calibration and OOS test."""
    prepared_train = _prepare_eval_fold(train, target=target)
    y_train = prepared_train[target].to_numpy().astype(np.int8)
    train_eval = evaluate_distress_models(
        result,
        prepared_train,
        target=target,
        evaluation_role="IN_SAMPLE_TRAIN_OVERFITTING_DIAGNOSTIC",
    )
    if validation is not None:
        validation_eval = evaluate_distress_models(
            result,
            validation,
            target=target,
            evaluation_role="IN_SAMPLE_CALIBRATION_FOLD_DO_NOT_USE_AS_OOS_CLAIM",
        )
        validation_eval["oof_summary"] = dict(result.oof_validation_metrics)
    else:
        validation_eval = {
            "evaluation_role": "INNER_PURGED_OOF_CALIBRATION",
            "oof_strategy": result.oof_strategy,
            "sample_count": result.oof_sample_count,
            "positive_count": result.oof_positive_count,
            "models": dict(result.validation_metrics),
            "oof_summary": dict(result.oof_validation_metrics),
        }
    return {
        "model_version": MODEL_VERSION,
        "model_role": "SCREENING_INDICATOR",
        "target_name": target,
        "oof_strategy": result.oof_strategy,
        "best_n_estimators": result.best_n_estimators,
        "scale_pos_weight": result.scale_pos_weight,
        "min_child_weight": result.min_child_weight,
        "platt_slope": result.platt_slope,
        "platt_intercept": result.platt_intercept,
        "altman_platt_slope": result.altman_platt_slope,
        "altman_platt_intercept": result.altman_platt_intercept,
        "logit_platt_slope": result.logit_platt_slope,
        "logit_platt_intercept": result.logit_platt_intercept,
        "risk_tier_cutoffs": {
            "R2_min_pd": result.risk_tier_cutoffs[0],
            "R3_min_pd": result.risk_tier_cutoffs[1],
            "R4_min_pd": result.risk_tier_cutoffs[2],
            "R5_min_pd": result.risk_tier_cutoffs[3],
        },
        "f2_thresholds": dict(result.f2_thresholds),
        "train_summary": {
            "sample_count": int(len(y_train)),
            "positive_count": int((y_train == 1).sum()),
            "unique_positive_issuers": int(
                prepared_train.filter(pl.col(target) == 1)["issuer_id"].n_unique()
            )
            if "issuer_id" in prepared_train.columns
            else None,
            "prevalence": float(y_train.mean()) if y_train.size > 0 else None,
            "unique_issuers": int(prepared_train["issuer_id"].n_unique())
            if "issuer_id" in prepared_train.columns
            else None,
            "in_sample_models": train_eval["models"],
        },
        "validation": validation_eval,
        "test": evaluate_distress_models(
            result,
            test,
            target=target,
            evaluation_role="OUT_OF_SAMPLE_TEST",
        ),
    }


def filter_true_unseen_issuers(
    test_fold: pl.DataFrame,
    *,
    test_start: date,
    regulatory_events: pl.DataFrame | None = None,
    train_fold: pl.DataFrame | None = None,
    target: str = "distress_target",
) -> pl.DataFrame:
    """Return True Unseen Issuers slice excluding ALL (y=0 & y=1) rows of prior-event issuers (GATE-ML-4)."""
    if test_fold.is_empty() or "issuer_id" not in test_fold.columns:
        return test_fold
    prior_issuers: set[str] = set()
    if regulatory_events is not None and not regulatory_events.is_empty():
        prior_issuers.update(
            regulatory_events.filter(pl.col("event_date").cast(pl.Date) < test_start)
            .get_column("issuer_id")
            .drop_nulls()
            .unique()
            .to_list()
        )
    if (
        train_fold is not None
        and not train_fold.is_empty()
        and "issuer_id" in train_fold.columns
        and target in train_fold.columns
    ):
        prior_issuers.update(
            train_fold.filter(pl.col(target) == 1)
            .get_column("issuer_id")
            .drop_nulls()
            .unique()
            .to_list()
        )
    if not prior_issuers:
        return test_fold
    return test_fold.filter(~pl.col("issuer_id").is_in(sorted(prior_issuers)))


def cluster_bootstrap_metrics(
    y: NDArray[np.int8],
    score: NDArray[np.float64],
    issuer_ids: NDArray[Any],
    *,
    reference_prevalence: float | NDArray[np.floating[Any]] | None = None,
    f2_threshold: float = 0.5,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute issuer-clustered bootstrap 95% confidence intervals for OOS metrics (GATE-ML-4)."""
    finite = np.isfinite(score)
    y_fin = np.asarray(y, dtype=np.int8)[finite]
    s_fin = np.asarray(score, dtype=np.float64)[finite]
    iss_fin = np.asarray(issuer_ids)[finite]
    ref_is_array = isinstance(reference_prevalence, np.ndarray)
    ref_fin: float | NDArray[np.float64] | None
    if ref_is_array:
        ref_arr = np.asarray(reference_prevalence, dtype=np.float64).reshape(-1)
        if ref_arr.size != finite.size:
            raise ContractViolationError(
                "Per-observation reference_prevalence length must match y."
            )
        ref_fin = ref_arr[finite]
    else:
        ref_fin = (
            float(reference_prevalence) if reference_prevalence is not None else None
        )
    point = _metrics(
        y_fin,
        s_fin,
        f2_threshold=f2_threshold,
        reference_prevalence=ref_fin,
    )
    metric_keys = (
        "roc_auc",
        "pr_auc",
        "brier_score",
        "brier_skill_score",
        "recall_at_top_5_percent",
        "recall_at_top_10_percent",
    )
    if y_fin.size == 0 or np.unique(y_fin).size < 2:
        return {
            "point": point,
            "ci95": {k: None for k in metric_keys},
            "n_bootstrap_valid": 0,
            "n_clusters": 0,
        }

    unique_issuers = np.unique(iss_fin)
    cluster_indices = [np.flatnonzero(iss_fin == iss) for iss in unique_issuers]
    n_clusters = len(cluster_indices)
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {k: [] for k in metric_keys}
    valid_draws = 0
    for _ in range(n_bootstrap):
        drawn_clusters = rng.integers(0, n_clusters, size=n_clusters)
        idx = np.concatenate([cluster_indices[int(c)] for c in drawn_clusters])
        y_b = y_fin[idx]
        if np.unique(y_b).size < 2:
            continue
        ref_b = ref_fin[idx] if isinstance(ref_fin, np.ndarray) else ref_fin
        m_b = _metrics(
            y_b,
            s_fin[idx],
            f2_threshold=f2_threshold,
            reference_prevalence=ref_b,
        )
        valid_draws += 1
        for k in metric_keys:
            val = m_b.get(k)
            if val is not None and np.isfinite(val):
                samples[k].append(float(val))

    ci95: dict[str, list[float] | None] = {}
    for k in metric_keys:
        vals = samples[k]
        if len(vals) >= 10:
            lo, hi = np.percentile(vals, [2.5, 97.5])
            ci95[k] = [float(lo), float(hi)]
        else:
            ci95[k] = None
    return {
        "point": point,
        "ci95": ci95,
        "n_bootstrap_valid": valid_draws,
        "n_clusters": n_clusters,
    }


def run_expanding_walk_forward_evaluation(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    test_years: Sequence[int] = (2021, 2022, 2023, 2024, 2025),
    features: tuple[str, ...] = SECTOR_ADJUSTED_FEATURES,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str | None = "VAS_TT200_2014",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    min_unique_positive_issuers: int = 3,
    n_bootstrap: int = 1000,
    seed: int = 42,
    precomputed_as_of_scored: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Execute 2-Way Purged Expanding Walk-Forward (2021..2025) with Cluster Bootstrap CI & Unseen Issuers."""
    splits_by_year = build_expanding_walk_forward_splits(
        facts,
        issuers,
        test_years=test_years,
        as_of=as_of,
        require_exact_filing=require_exact_filing,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        target_mode=target_mode,
        regulatory_events=regulatory_events,
        precomputed_as_of_scored=precomputed_as_of_scored,
    )
    model_names = ("Baseline_1_AltmanZ", "Baseline_2_Logit", "XGBoost")
    metric_keys = (
        "roc_auc",
        "pr_auc",
        "brier_score",
        "brier_skill_score",
        "recall_at_top_5_percent",
        "recall_at_top_10_percent",
    )

    per_year: dict[str, Any] = {}
    pooled_y: list[NDArray[np.int8]] = []
    pooled_iss: list[NDArray[Any]] = []
    pooled_ref_prevs: list[NDArray[np.float64]] = []
    pooled_scores: dict[str, list[NDArray[np.float64]]] = {m: [] for m in model_names}
    unseen_y: list[NDArray[np.int8]] = []
    unseen_iss: list[NDArray[Any]] = []
    unseen_ref_prevs: list[NDArray[np.float64]] = []
    unseen_scores: dict[str, list[NDArray[np.float64]]] = {m: [] for m in model_names}

    for year in test_years:
        fold_pair = splits_by_year[int(year)]
        tr_df = fold_pair["train"]
        te_df = fold_pair["test"]
        trained = train_distress_model(
            tr_df,
            validation=None,
            features=features,
            min_unique_positive_issuers=min_unique_positive_issuers,
        )
        tr_prepared = _prepare_eval_fold(tr_df, target="distress_target")
        te_prepared = _prepare_eval_fold(te_df, target="distress_target")
        train_eval = evaluate_distress_models(
            trained,
            tr_prepared,
            evaluation_role="IN_SAMPLE_TRAIN_OVERFITTING_DIAGNOSTIC",
        )
        test_eval = evaluate_distress_models(
            trained,
            te_prepared,
            evaluation_role="OUT_OF_SAMPLE_TEST",
        )
        unseen_df = filter_true_unseen_issuers(
            te_prepared,
            test_start=date(int(year), 1, 1),
            regulatory_events=regulatory_events,
            train_fold=tr_prepared,
        )
        unseen_eval = evaluate_distress_models(
            trained,
            unseen_df,
            evaluation_role="OUT_OF_SAMPLE_TRUE_UNSEEN_ISSUERS",
        )

        # Accumulate pooled common-sample OOS predictions for Cluster Bootstrap 95% CI.
        # Each observation keeps its own strictly historical train_prevalence_Y (zero future-fold peek).
        if not te_prepared.is_empty():
            y_t, alt_p, log_p, xgb_p, common = _score_three_models(
                trained, te_prepared, target="distress_target"
            )
            if np.any(common):
                n_common = int(common.sum())
                pooled_y.append(y_t[common])
                pooled_iss.append(
                    te_prepared.get_column("issuer_id").to_numpy()[common]
                )
                pooled_ref_prevs.append(
                    np.full(n_common, trained.train_prevalence, dtype=np.float64)
                )
                pooled_scores["Baseline_1_AltmanZ"].append(alt_p[common])
                pooled_scores["Baseline_2_Logit"].append(log_p[common])
                pooled_scores["XGBoost"].append(xgb_p[common])

        if not unseen_df.is_empty():
            y_u, alt_u, log_u, xgb_u, common_u = _score_three_models(
                trained, unseen_df, target="distress_target"
            )
            if np.any(common_u):
                n_common_u = int(common_u.sum())
                unseen_y.append(y_u[common_u])
                unseen_iss.append(
                    unseen_df.get_column("issuer_id").to_numpy()[common_u]
                )
                unseen_ref_prevs.append(
                    np.full(n_common_u, trained.train_prevalence, dtype=np.float64)
                )
                unseen_scores["Baseline_1_AltmanZ"].append(alt_u[common_u])
                unseen_scores["Baseline_2_Logit"].append(log_u[common_u])
                unseen_scores["XGBoost"].append(xgb_u[common_u])

        per_year[str(year)] = {
            "train_rows": int(tr_prepared.height),
            "train_positive_rows": int((tr_prepared["distress_target"] == 1).sum()),
            "train_unique_positive_issuers": int(
                tr_prepared.filter(pl.col("distress_target") == 1)[
                    "issuer_id"
                ].n_unique()
            ),
            "train_prevalence": trained.train_prevalence,
            "oof_strategy": trained.oof_strategy,
            "best_n_estimators": trained.best_n_estimators,
            "scale_pos_weight": trained.scale_pos_weight,
            "min_child_weight": trained.min_child_weight,
            "platt_slope": trained.platt_slope,
            "platt_intercept": trained.platt_intercept,
            "train_models": train_eval["models"],
            "test": test_eval,
            "true_unseen_issuers": unseen_eval,
        }

    def _summarize_yearly(
        section_key: str,
    ) -> dict[str, dict[str, dict[str, float | None]]]:
        summary: dict[str, dict[str, dict[str, float | None]]] = {}
        for m in model_names:
            summary[m] = {}
            for k in metric_keys:
                vals = [
                    float(per_year[str(yr)][section_key]["models"][m][k])
                    for yr in test_years
                    if per_year[str(yr)][section_key]["models"][m].get(k) is not None
                ]
                if vals:
                    summary[m][k] = {
                        "mean": float(np.mean(vals)),
                        "std": float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
                        "n_years": len(vals),
                    }
                else:
                    summary[m][k] = {"mean": None, "std": None, "n_years": 0}
        return summary

    pooled_ref_vec = np.concatenate(pooled_ref_prevs) if pooled_ref_prevs else None
    pooled_ref_prev = (
        float(np.mean(pooled_ref_vec)) if pooled_ref_vec is not None else None
    )
    pooled_bootstrap: dict[str, Any] = {}
    if pooled_y:
        all_y = np.concatenate(pooled_y)
        all_iss = np.concatenate(pooled_iss)
        for m in model_names:
            pooled_bootstrap[m] = cluster_bootstrap_metrics(
                all_y,
                np.concatenate(pooled_scores[m]),
                all_iss,
                reference_prevalence=pooled_ref_vec,
                n_bootstrap=n_bootstrap,
                seed=seed,
            )

    unseen_ref_vec = np.concatenate(unseen_ref_prevs) if unseen_ref_prevs else None
    unseen_bootstrap: dict[str, Any] = {}
    if unseen_y:
        all_uy = np.concatenate(unseen_y)
        all_uiss = np.concatenate(unseen_iss)
        for m in model_names:
            unseen_bootstrap[m] = cluster_bootstrap_metrics(
                all_uy,
                np.concatenate(unseen_scores[m]),
                all_uiss,
                reference_prevalence=unseen_ref_vec,
                n_bootstrap=n_bootstrap,
                seed=seed,
            )

    return {
        "test_years": [int(y) for y in test_years],
        "require_exact_filing": require_exact_filing,
        "per_year": per_year,
        "yearly_summary_all_oos": _summarize_yearly("test"),
        "yearly_summary_true_unseen": _summarize_yearly("true_unseen_issuers"),
        "pooled_oos_cluster_bootstrap": pooled_bootstrap,
        "pooled_true_unseen_cluster_bootstrap": unseen_bootstrap,
        "pooled_reference_train_prevalence": pooled_ref_prev,
    }


def _population_stability_index(
    ref_vals: NDArray[np.float64],
    tgt_vals: NDArray[np.float64],
    *,
    n_bins: int = 10,
) -> float | None:
    """Compute Population Stability Index (PSI) with quantile bins learned strictly on reference."""
    ref_fin = ref_vals[np.isfinite(ref_vals)]
    tgt_fin = tgt_vals[np.isfinite(tgt_vals)]
    if ref_fin.size < 10 or tgt_fin.size < 10:
        return None
    quantiles = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique(np.quantile(ref_fin, quantiles))
    if edges.size < 3:
        return 0.0
    inner_edges = edges[1:-1]
    bins = np.concatenate([[-np.inf], inner_edges, [np.inf]])
    ref_counts, _ = np.histogram(ref_fin, bins=bins)
    tgt_counts, _ = np.histogram(tgt_fin, bins=bins)
    eps = 1e-4
    ref_pct = np.clip(ref_counts / max(1, ref_fin.size), eps, None)
    tgt_pct = np.clip(tgt_counts / max(1, tgt_fin.size), eps, None)
    ref_pct = ref_pct / ref_pct.sum()
    tgt_pct = tgt_pct / tgt_pct.sum()
    return float(np.sum((tgt_pct - ref_pct) * np.log(tgt_pct / ref_pct)))


def compute_taxonomy_drift_psi(
    reference_frame: pl.DataFrame,
    target_frame: pl.DataFrame,
    *,
    features: Sequence[str] = SECTOR_ADJUSTED_FEATURES,
    result: TrainingResult | None = None,
) -> dict[str, Any]:
    """Measure feature PSI, average *_missing rate, and score drift across TT200 -> TT99 (M-05)."""
    feature_drift: dict[str, dict[str, float | None]] = {}
    ref_missing_rates: list[float] = []
    tgt_missing_rates: list[float] = []
    psi_values: list[float] = []

    ref_imp = (
        result.preprocessor.impute(reference_frame)
        if result is not None and not reference_frame.is_empty()
        else None
    )
    tgt_imp = (
        result.preprocessor.impute(target_frame)
        if result is not None and not target_frame.is_empty()
        else None
    )

    for feat in features:
        if feat not in reference_frame.columns or feat not in target_frame.columns:
            continue
        r_raw = np.asarray(
            reference_frame.get_column(feat).cast(pl.Float64).to_numpy(),
            dtype=np.float64,
        )
        t_raw = np.asarray(
            target_frame.get_column(feat).cast(pl.Float64).to_numpy(),
            dtype=np.float64,
        )
        psi = _population_stability_index(r_raw, t_raw)
        if psi is not None:
            psi_values.append(psi)
        ind_col = f"{feat}_missing"
        if ref_imp is not None and tgt_imp is not None and ind_col in ref_imp.columns:
            r_miss = float(ref_imp.get_column(ind_col).mean() or 0.0)  # type: ignore[arg-type]
            t_miss = float(tgt_imp.get_column(ind_col).mean() or 0.0)  # type: ignore[arg-type]
        else:
            r_miss = float(np.mean(~np.isfinite(r_raw))) if r_raw.size > 0 else 0.0
            t_miss = float(np.mean(~np.isfinite(t_raw))) if t_raw.size > 0 else 0.0
        ref_missing_rates.append(r_miss)
        tgt_missing_rates.append(t_miss)
        feature_drift[feat] = {
            "psi": psi,
            "ref_missing_rate": r_miss,
            "tgt_missing_rate": t_miss,
            "missing_rate_delta": t_miss - r_miss,
        }

    score_drift: dict[str, Any] = {}
    if (
        result is not None
        and not reference_frame.is_empty()
        and not target_frame.is_empty()
    ):
        ref_scored = score_distress_model(result, reference_frame)
        tgt_scored = score_distress_model(result, target_frame)
        ref_p = ref_scored.get_column("calibrated_distress_prob").to_numpy()
        tgt_p = tgt_scored.get_column("calibrated_distress_prob").to_numpy()
        ref_r4_r5 = float(
            ref_scored.select(
                pl.col("risk_tier").is_in(["R4", "R5"]).fill_null(False).mean()
            ).item()
            or 0.0
        )
        tgt_r4_r5 = float(
            tgt_scored.select(
                pl.col("risk_tier").is_in(["R4", "R5"]).fill_null(False).mean()
            ).item()
            or 0.0
        )
        p90_cut = float(np.nanquantile(ref_p, 0.90))
        p95_cut = float(np.nanquantile(ref_p, 0.95))
        score_drift = {
            "score_psi": _population_stability_index(ref_p, tgt_p),
            "ref_mean_pd": float(np.nanmean(ref_p)),
            "tgt_mean_pd": float(np.nanmean(tgt_p)),
            "ref_r4_r5_share": ref_r4_r5,
            "tgt_r4_r5_share": tgt_r4_r5,
            "r4_r5_share_delta": tgt_r4_r5 - ref_r4_r5,
            "ref_p90_cutoff": p90_cut,
            "ref_share_above_p90": float(np.mean(ref_p >= p90_cut)),
            "tgt_share_above_p90": float(np.mean(tgt_p >= p90_cut)),
            "ref_p95_cutoff": p95_cut,
            "ref_share_above_p95": float(np.mean(ref_p >= p95_cut)),
            "tgt_share_above_p95": float(np.mean(tgt_p >= p95_cut)),
        }

    return {
        "reference_rows": int(reference_frame.height),
        "target_rows": int(target_frame.height),
        "mean_feature_psi": float(np.mean(psi_values)) if psi_values else None,
        "max_feature_psi": float(np.max(psi_values)) if psi_values else None,
        "features_psi_gt_0_25": sorted(
            f for f, v in feature_drift.items() if (v["psi"] or 0.0) > 0.25
        ),
        "mean_ref_missing_rate": float(np.mean(ref_missing_rates))
        if ref_missing_rates
        else None,
        "mean_tgt_missing_rate": float(np.mean(tgt_missing_rates))
        if tgt_missing_rates
        else None,
        "feature_drift": feature_drift,
        "score_drift": score_drift,
    }


def score_distress_model(result: TrainingResult, frame: pl.DataFrame) -> pl.DataFrame:
    """Return calibrated PD, TTC R1..R5 tier, PIT market percentile, base rate & lift."""
    present_keys = [name for name in ML_SCORES_KEY if name in frame.columns]
    if frame.is_empty():
        empty_scores = pl.DataFrame(
            schema={
                "distress_risk_score": pl.Float64,
                "calibrated_distress_prob": pl.Float64,
                "risk_tier": pl.String,
                "risk_market_percentile": pl.Float64,
                "market_base_rate": pl.Float64,
                "relative_risk_lift": pl.Float64,
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
    cutoffs = getattr(result, "risk_tier_cutoffs", DEFAULT_RISK_TIER_CUTOFFS)
    tiers = assign_risk_tiers(probability, cutoffs)
    raw_prev = float(getattr(result, "train_prevalence", 0.0))
    base_rate_val: float | None = (
        raw_prev if np.isfinite(raw_prev) and raw_prev > 0.0 else None
    )
    if base_rate_val is not None:
        base_rate_col = np.full(frame.height, base_rate_val, dtype=np.float64)
        lift_col = np.where(
            np.isfinite(probability), probability / base_rate_val, np.nan
        )
    else:
        base_rate_col = np.full(frame.height, np.nan, dtype=np.float64)
        lift_col = np.full(frame.height, np.nan, dtype=np.float64)

    output = pl.DataFrame(
        {
            "distress_risk_score": raw_margin,
            "calibrated_distress_prob": probability,
            "risk_tier": pl.Series("risk_tier", tiers, dtype=pl.String),
            "risk_market_percentile": pl.Series(
                "risk_market_percentile", [None] * frame.height, dtype=pl.Float64
            ),
            "market_base_rate": pl.Series(
                "market_base_rate",
                [float(v) if np.isfinite(v) else None for v in base_rate_col],
                dtype=pl.Float64,
            ),
            "relative_risk_lift": pl.Series(
                "relative_risk_lift",
                [float(v) if np.isfinite(v) else None for v in lift_col],
                dtype=pl.Float64,
            ),
            "model_version": [MODEL_VERSION] * frame.height,
            "model_role": ["SCREENING_INDICATOR"] * frame.height,
        }
    )
    combined = frame.select(present_keys).hstack(output) if present_keys else output
    return compute_cross_sectional_risk_percentile(combined)


def format_walk_forward_summary_table(
    wf_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return flat model-comparison rows for reporting and Model Card exports."""
    rows: list[dict[str, Any]] = []
    for scope_name, summary_key, boot_key in (
        ("ALL_OOS", "yearly_summary_all_oos", "pooled_oos_cluster_bootstrap"),
        (
            "TRUE_UNSEEN_ISSUERS",
            "yearly_summary_true_unseen",
            "pooled_true_unseen_cluster_bootstrap",
        ),
    ):
        yearly = wf_report.get(summary_key, {})
        boot = wf_report.get(boot_key, {})
        for model_name in ("Baseline_1_AltmanZ", "Baseline_2_Logit", "XGBoost"):
            m_yr = yearly.get(model_name, {})
            m_bt = boot.get(model_name, {})
            rows.append(
                {
                    "scope": scope_name,
                    "model": model_name,
                    "yearly_roc_auc_mean": m_yr.get("roc_auc", {}).get("mean"),
                    "yearly_roc_auc_std": m_yr.get("roc_auc", {}).get("std"),
                    "yearly_pr_auc_mean": m_yr.get("pr_auc", {}).get("mean"),
                    "yearly_pr_auc_std": m_yr.get("pr_auc", {}).get("std"),
                    "pooled_roc_auc": m_bt.get("point", {}).get("roc_auc"),
                    "pooled_roc_auc_ci95": m_bt.get("ci95", {}).get("roc_auc"),
                    "pooled_pr_auc": m_bt.get("point", {}).get("pr_auc"),
                    "pooled_pr_auc_ci95": m_bt.get("ci95", {}).get("pr_auc"),
                    "pooled_brier": m_bt.get("point", {}).get("brier_score"),
                    "pooled_bss": m_bt.get("point", {}).get("brier_skill_score"),
                    "pooled_recall_top5": m_bt.get("point", {}).get(
                        "recall_at_top_5_percent"
                    ),
                    "pooled_recall_top10": m_bt.get("point", {}).get(
                        "recall_at_top_10_percent"
                    ),
                }
            )
    return rows
