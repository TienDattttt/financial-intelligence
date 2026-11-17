"""Layer 4B surface API.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.1–5.3.
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
    "TrainingResult",
    "train_distress_model",
    "score_distress_model",
    "explain_distress",
    "explain_global_importance",
]

_OPTIONAL_EXPORTS = {
    "CrossSectionalPreprocessor",
    "FinancialArchetypes",
    "UNSUPERVISED_FEATURE_SET",
    "build_distress_targets",
    "purged_walk_forward_split",
    "TrainingResult",
    "train_distress_model",
    "score_distress_model",
    "explain_distress",
    "explain_global_importance",
}


def __getattr__(name: str) -> Any:
    if name in _OPTIONAL_EXPORTS:
        if name in {
            "CrossSectionalPreprocessor",
            "FinancialArchetypes",
            "UNSUPERVISED_FEATURE_SET",
        }:
            module_name = "finintel.ml.unsupervised"
        elif name == "build_distress_targets":
            module_name = "finintel.ml.supervised.target"
        elif name == "purged_walk_forward_split":
            module_name = "finintel.ml.supervised.temporal_split"
        elif name in {"TrainingResult", "train_distress_model", "score_distress_model"}:
            module_name = "finintel.ml.supervised.train"
        else:
            module_name = "finintel.ml.supervised.explain"
        module = import_module(module_name)
        return getattr(module, name)
    raise AttributeError(name)
