"""Layer 4B surface API.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.1–5.3 and 6.4.
"""

from importlib import import_module
from typing import Any

from finintel.ml.classical_scores import add_classical_scores

__all__ = [
    "add_classical_scores",
    "CrossSectionalPreprocessor",
    "FinancialArchetypes",
    "UNSUPERVISED_FEATURE_SET",
    "build_distress_targets",
    "purged_walk_forward_split",
    "purged_two_way_split",
    "build_pit_ml_splits",
    "build_pit_two_way_split",
    "build_expanding_walk_forward_splits",
    "TrainingResult",
    "train_distress_model",
    "evaluate_distress_models",
    "build_evaluation_report",
    "cluster_bootstrap_metrics",
    "filter_true_unseen_issuers",
    "run_expanding_walk_forward_evaluation",
    "compute_taxonomy_drift_psi",
    "score_distress_model",
    "compute_shap_contributions",
    "explain_distress",
    "explain_global_importance",
    "MLArtifacts",
    "build_ml_artifacts",
]

_OPTIONAL_EXPORTS = {
    "CrossSectionalPreprocessor": "finintel.ml.unsupervised",
    "FinancialArchetypes": "finintel.ml.unsupervised",
    "UNSUPERVISED_FEATURE_SET": "finintel.ml.unsupervised",
    "build_distress_targets": "finintel.ml.supervised.target",
    "purged_walk_forward_split": "finintel.ml.supervised.temporal_split",
    "purged_two_way_split": "finintel.ml.supervised.temporal_split",
    "build_pit_ml_splits": "finintel.ml.supervised.temporal_split",
    "build_pit_two_way_split": "finintel.ml.supervised.temporal_split",
    "build_expanding_walk_forward_splits": "finintel.ml.supervised.temporal_split",
    "TrainingResult": "finintel.ml.supervised.train",
    "train_distress_model": "finintel.ml.supervised.train",
    "evaluate_distress_models": "finintel.ml.supervised.train",
    "build_evaluation_report": "finintel.ml.supervised.train",
    "cluster_bootstrap_metrics": "finintel.ml.supervised.train",
    "filter_true_unseen_issuers": "finintel.ml.supervised.train",
    "run_expanding_walk_forward_evaluation": "finintel.ml.supervised.train",
    "compute_taxonomy_drift_psi": "finintel.ml.supervised.train",
    "score_distress_model": "finintel.ml.supervised.train",
    "compute_shap_contributions": "finintel.ml.supervised.explain",
    "explain_distress": "finintel.ml.supervised.explain",
    "explain_global_importance": "finintel.ml.supervised.explain",
    "MLArtifacts": "finintel.ml.tables",
    "build_ml_artifacts": "finintel.ml.tables",
}


def __getattr__(name: str) -> Any:
    module_name = _OPTIONAL_EXPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name)
        return getattr(module, name)
    raise AttributeError(name)
