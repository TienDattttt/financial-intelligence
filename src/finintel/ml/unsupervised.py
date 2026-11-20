"""Fit corporate financial archetypes and a multivariate outlier screen.

Verified Sources:
    https://scikit-learn.org/stable/modules/preprocessing.html
    https://scikit-learn.org/stable/modules/decomposition.html
    https://scikit-learn.org/stable/modules/outlier_detection.html
    https://github.com/yzhao062/pyod
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, section 5.2.
    Reverse sklearn IsolationForest scores so larger values are more unusual.
    Estimate percentiles against the training scores, clip scaled unsupervised
    features to [-5.0, 5.0] when configured, and guarantee unique cluster names.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sklearn.cluster import KMeans  # type: ignore[import-untyped]
from sklearn.decomposition import PCA  # type: ignore[import-untyped]
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]
from sklearn.metrics import silhouette_score  # type: ignore[import-untyped]
from sklearn.preprocessing import RobustScaler  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError
from finintel.storage.schemas import ML_SCORES_KEY

UNSUPERVISED_FEATURE_SET = (
    "roa",
    "ebit_margin",
    "roic",
    "current_ratio",
    "cash_ratio",
    "liabilities_to_assets",
    "financial_debt_to_assets",
    "interest_coverage",
    "asset_turnover",
    "cash_conversion_cycle",
    "fcf_to_revenue",
    "accruals_to_assets",
)
INDUSTRY = "icb_code_level2"
_NEUTRAL_ARCHETYPE_LABEL = "Nhóm vận hành cân bằng / trung bình ngành"
_NEUTRAL_CLUSTER_THRESHOLD = 0.35
_FINANCIAL_SIGNAL = {
    "roa": "Sinh lời trên tài sản",
    "ebit_margin": "Biên EBIT",
    "roic": "Sinh lời trên vốn đầu tư",
    "current_ratio": "Thanh khoản hiện hành",
    "cash_ratio": "Thanh khoản tiền mặt",
    "liabilities_to_assets": "Nợ phải trả trên tài sản",
    "financial_debt_to_assets": "Nợ vay trên tài sản",
    "interest_coverage": "Khả năng trả lãi vay",
    "asset_turnover": "Vòng quay tài sản",
    "cash_conversion_cycle": "Chu kỳ chuyển đổi tiền mặt",
    "fcf_to_revenue": "Dòng tiền tự do trên doanh thu",
    "accruals_to_assets": "Dồn tích trên tài sản",
}


class InsufficientModelData(ContractViolationError):
    """The eligible training records lack enough variation or outcomes."""


def _build_unique_archetype_names(
    relative_centers: NDArray[np.float64],
    feature_names: tuple[str, ...] = UNSUPERVISED_FEATURE_SET,
) -> tuple[str, ...]:
    """Assign deterministic, 100% unique Vietnamese archetype labels to K-Means centers."""
    used: set[str] = set()
    names: list[str] = []
    width = len(feature_names)

    for cluster_idx, rel in enumerate(relative_centers):
        order = np.argsort(-np.abs(rel), kind="mergesort")

        def _fmt_signal(feat_idx: int, vector: NDArray[np.float64] = rel) -> str:
            feat = feature_names[feat_idx]
            direction = "cao" if float(vector[feat_idx]) >= 0.0 else "thấp"
            return f"{_FINANCIAL_SIGNAL[feat]} {direction}"

        is_neutral = float(np.max(np.abs(rel))) < _NEUTRAL_CLUSTER_THRESHOLD
        if is_neutral:
            candidate = _NEUTRAL_ARCHETYPE_LABEL
        else:
            candidate = " · ".join(_fmt_signal(int(i)) for i in order[:2])

        if candidate in used:
            resolved: str | None = None
            if is_neutral:
                if float(np.max(np.abs(rel))) > 1e-12:
                    for extra_count in range(1, min(3, width) + 1):
                        detail = " · ".join(
                            _fmt_signal(int(i)) for i in order[:extra_count]
                        )
                        alt = f"{_NEUTRAL_ARCHETYPE_LABEL} ({detail})"
                        if alt not in used:
                            resolved = alt
                            break
            else:
                for take in range(3, width + 1):
                    alt = " · ".join(_fmt_signal(int(i)) for i in order[:take])
                    if alt not in used:
                        resolved = alt
                        break
            if resolved is None:
                resolved = f"{candidate} (Nhóm {cluster_idx + 1})"
            candidate = resolved

        used.add(candidate)
        names.append(candidate)
    return tuple(names)


class CrossSectionalPreprocessor:
    """Fit clipping, contextual imputation and robust scaling on training rows."""

    def __init__(
        self,
        features: tuple[str, ...],
        *,
        clip_range: tuple[float, float] | None = None,
    ) -> None:
        if clip_range is not None:
            low, high = clip_range
            if not (np.isfinite(low) and np.isfinite(high) and low < high):
                raise ContractViolationError(
                    "clip_range must be a finite (low, high) pair with low < high."
                )
        self.features = features
        self.clip_range = clip_range
        self.indicators = tuple(f"{name}_missing" for name in features)
        self._scaler = RobustScaler()
        self._p95: float | None = None
        self._bounds: dict[str, tuple[float | None, float | None]] = {}
        self._medians: dict[str, float | None] = {}
        self._industry: pl.DataFrame | None = None

    def _validate_corporate(self, frame: pl.DataFrame) -> None:
        if "accounting_regime" not in frame.columns:
            raise ContractViolationError(
                "ML features require accounting_regime for corporate-only routing."
            )
        if frame.filter(
            pl.col("accounting_regime").ne("CORPORATE").fill_null(True)
        ).height:
            raise ContractViolationError(
                "Non-corporate rows must be excluded before ML preprocessing."
            )

    def fit(self, train: pl.DataFrame) -> "CrossSectionalPreprocessor":
        """Learn winsor bounds, imputation values and scale from corporate training rows."""
        self._validate_corporate(train)
        missing_features = set(self.features) - set(train.columns)
        if missing_features:
            raise ContractViolationError(
                f"Training data is missing ML features: {sorted(missing_features)}"
            )
        if train.is_empty():
            raise InsufficientModelData("ML preprocessing requires training rows.")
        clean = self._clean(train)
        self._p95 = (
            clean["interest_coverage"].quantile(0.95, interpolation="linear")
            if "interest_coverage" in self.features
            else None
        )
        self._bounds = {
            name: (
                clean[name].quantile(0.01, interpolation="linear"),
                clean[name].quantile(0.99, interpolation="linear"),
            )
            for name in self.features
        }
        adjusted = self._adjust(clean)
        self._medians = adjusted.select(pl.col(self.features).median()).row(
            0, named=True
        )
        if any(self._medians[name] is None for name in self.features):
            raise InsufficientModelData(
                "Every ML feature needs observed training data."
            )
        self._industry = (
            adjusted.filter(pl.col(INDUSTRY).is_not_null())
            .group_by(INDUSTRY)
            .agg(pl.col(self.features).median().name.suffix("_industry_median"))
        )
        self._scaler.fit(self.impute(train).select(self.features).to_numpy())
        return self

    def _clean(self, frame: pl.DataFrame) -> pl.DataFrame:
        optional = {
            INDUSTRY: pl.String,
            "interest_coverage_flag": pl.String,
            "ebit_ttm": pl.Float64,
        }
        return frame.with_columns(
            *(
                pl.when(pl.col(name).is_finite())
                .then(pl.col(name).cast(pl.Float64))
                .alias(name)
                for name in self.features
            ),
            *(
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in optional.items()
                if name not in frame.columns
            ),
        )

    def _adjust(self, clean: pl.DataFrame) -> pl.DataFrame:
        values = []
        for name, (lower, upper) in self._bounds.items():
            value = (
                pl.col(name).clip(lower, upper) if lower is not None else pl.col(name)
            )
            if name == "interest_coverage" and self._p95 is not None:
                favorable = (
                    pl.col("interest_coverage_flag") == "NO_INTEREST_EXPENSE"
                ) & (pl.col("ebit_ttm") > 0)
                value = pl.when(favorable).then(self._p95).otherwise(value)
            values.append(value.alias(name))
        return clean.with_columns(values)

    def impute(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Return clipped, imputed values in original units and missing indicators."""
        self._validate_corporate(frame)
        missing_features = set(self.features) - set(frame.columns)
        if missing_features:
            raise ContractViolationError(
                f"Input data is missing ML features: {sorted(missing_features)}"
            )
        if self._industry is None:
            raise ContractViolationError(
                "Fit the ML preprocessor before transforming data."
            )
        adjusted = self._adjust(self._clean(frame))
        # Record missingness only for values still null after contextual NO_INTEREST_EXPENSE replacement.
        missing = adjusted.select(
            *(
                pl.col(name).is_null().cast(pl.Float64).alias(indicator)
                for name, indicator in zip(self.features, self.indicators, strict=True)
            )
        )
        return (
            adjusted.select(INDUSTRY, *self.features)
            .join(self._industry, on=INDUSTRY, how="left", maintain_order="left")
            .select(
                *(
                    pl.coalesce(
                        name,
                        f"{name}_industry_median",
                        pl.lit(self._medians[name]),
                    ).alias(name)
                    for name in self.features
                ),
            )
            .hstack(missing)
        )

    def transform(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Apply the training fit while retaining feature order and missing indicators."""
        imputed = self.impute(frame)
        if imputed.is_empty():
            return imputed
        scaled = self._scaler.transform(imputed.select(self.features).to_numpy())
        if self.clip_range is not None:
            scaled = np.clip(scaled, self.clip_range[0], self.clip_range[1])
        return pl.DataFrame(scaled, schema=list(self.features), orient="row").hstack(
            imputed.select(self.indicators)
        )


class FinancialArchetypes:
    """Hold a fitted preprocessor, PCA, K-means, and Isolation Forest."""

    def __init__(self) -> None:
        self.preprocessor = CrossSectionalPreprocessor(
            UNSUPERVISED_FEATURE_SET, clip_range=(-5.0, 5.0)
        )
        self._pca = PCA(n_components=0.80, svd_solver="full", random_state=42)
        self._forest = IsolationForest(
            n_estimators=200,
            max_samples="auto",
            contamination="auto",
            random_state=42,
        )
        self._clusters: KMeans | None = None
        self._training_scores: NDArray[np.float64] = np.empty(0)
        self.component_names: tuple[str, ...] = ()
        self._archetypes: tuple[str, ...] = ()

    def fit(self, train: pl.DataFrame) -> "FinancialArchetypes":
        """Choose 3–5 PCA axes and the most separated valid cluster count on train."""
        if train.height < 4:
            raise InsufficientModelData(
                "Financial archetypes require at least four rows for clustering."
            )
        if len(set(self.preprocessor.features)) != len(self.preprocessor.features):
            raise ContractViolationError(
                "Financial archetype feature names must be unique."
            )
        preprocessor = CrossSectionalPreprocessor(
            UNSUPERVISED_FEATURE_SET, clip_range=(-5.0, 5.0)
        )
        matrix = (
            preprocessor.fit(train)
            .transform(train)
            .select(UNSUPERVISED_FEATURE_SET)
            .to_numpy()
        )
        if matrix.shape[1] != len(UNSUPERVISED_FEATURE_SET):
            raise ContractViolationError(
                "Preprocessing indicators must not enter PCA or clustering."
            )
        if np.unique(matrix, axis=0).shape[0] < 3:
            raise InsufficientModelData(
                "Financial archetypes require three distinct training rows."
            )
        # Fit full-SVD PCA once and slice to target axes (avoids redundant 2nd full SVD).
        pca = PCA(svd_solver="full", random_state=42)
        full_embedding = pca.fit_transform(matrix)
        cum_var = np.cumsum(pca.explained_variance_ratio_)
        n_for_80 = int(np.searchsorted(cum_var, 0.80, side="left")) + 1
        axes = min(5, train.height, full_embedding.shape[1], max(3, n_for_80))
        pca.n_components = axes
        pca.n_components_ = axes
        pca.components_ = pca.components_[:axes].copy()
        pca.explained_variance_ = pca.explained_variance_[:axes].copy()
        pca.explained_variance_ratio_ = pca.explained_variance_ratio_[:axes].copy()
        pca.singular_values_ = pca.singular_values_[:axes].copy()
        embedding = full_embedding[:, :axes]
        candidates = []
        distinct = np.unique(embedding, axis=0).shape[0]
        sample_size = min(len(embedding), 5000) if len(embedding) > 5000 else None
        rng_seed = 42 if sample_size is not None else None
        for k in range(3, min(6, train.height - 1, distinct) + 1):
            model = KMeans(n_clusters=k, n_init=20, random_state=42).fit(embedding)
            if 2 <= np.unique(model.labels_).size < train.height:
                candidates.append(
                    (
                        float(
                            silhouette_score(
                                embedding,
                                model.labels_,
                                sample_size=sample_size,
                                random_state=rng_seed,
                            )
                        ),
                        model,
                    )
                )
        if not candidates:
            raise InsufficientModelData(
                "Financial archetypes require distinguishable clusters."
            )
        _, clusters = max(candidates, key=lambda candidate: candidate[0])
        width = len(UNSUPERVISED_FEATURE_SET)
        top = np.argsort(-np.abs(pca.components_[:, :width]), axis=1, kind="mergesort")[
            :, :2
        ]
        component_names = tuple(
            " / ".join(
                _FINANCIAL_SIGNAL[UNSUPERVISED_FEATURE_SET[int(index)]]
                for index in pair
            )
            for pair in top
        )
        centers = np.asarray(
            pca.inverse_transform(clusters.cluster_centers_)[:, :width],
            dtype=np.float64,
        )
        relative_centers = centers - centers.mean(axis=0, keepdims=True)
        archetypes = _build_unique_archetype_names(relative_centers)
        forest = IsolationForest(
            n_estimators=200, max_samples="auto", contamination="auto", random_state=42
        )
        forest.fit(matrix)
        training_scores = np.sort(-forest.score_samples(matrix))
        self.preprocessor = preprocessor
        self._pca = pca
        self._clusters = clusters
        self._forest = forest
        self._training_scores = training_scores
        self.component_names = component_names
        self._archetypes = archetypes
        return self

    def transform(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Score fitted axes, archetypes and empirical training percentiles."""
        if self._clusters is None:
            raise ContractViolationError("Fit financial archetypes before scoring.")
        present_keys = [name for name in ML_SCORES_KEY if name in frame.columns]
        transformed = self.preprocessor.transform(frame)
        if transformed.is_empty():
            empty_scores = pl.DataFrame(
                schema={
                    "pca_pc1": pl.Float64,
                    "pca_pc2": pl.Float64,
                    "pca_pc3": pl.Float64,
                    "cluster_id": pl.Int32,
                    "cluster_archetype_name": pl.String,
                    "anomaly_score_raw": pl.Float64,
                    "anomaly_percentile_rank": pl.Float64,
                    "statistical_outlier_flag": pl.Boolean,
                    "anomaly_flag": pl.String,
                }
            )
            return (
                frame.select(present_keys).hstack(empty_scores)
                if present_keys
                else empty_scores
            )
        matrix = transformed.select(UNSUPERVISED_FEATURE_SET).to_numpy()
        pca = self._pca.transform(matrix)
        clusters = self._clusters.predict(pca)
        anomaly = -self._forest.score_samples(matrix)
        ranks = (
            100.0
            * np.searchsorted(self._training_scores, anomaly, side="right")
            / len(self._training_scores)
        )
        output = pl.DataFrame(
            {
                "pca_pc1": pca[:, 0],
                "pca_pc2": pca[:, 1],
                "pca_pc3": pca[:, 2],
                "cluster_id": clusters.astype(np.int32),
                "cluster_archetype_name": [
                    self._archetypes[index] for index in clusters
                ],
                "anomaly_score_raw": anomaly,
                "anomaly_percentile_rank": ranks,
                "statistical_outlier_flag": ranks >= 95,
            }
        ).with_columns(
            pl.when(pl.col("statistical_outlier_flag"))
            .then(pl.lit("STATISTICAL_MULTIVARIATE_OUTLIER"))
            .alias("anomaly_flag")
        )
        return frame.select(present_keys).hstack(output) if present_keys else output
