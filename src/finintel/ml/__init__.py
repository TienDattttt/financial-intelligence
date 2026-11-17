"""Layer 4B surface API.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.1–5.3.
"""

from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.unsupervised import (
    CrossSectionalPreprocessor,
    FinancialArchetypes,
    UNSUPERVISED_FEATURE_SET,
)

__all__ = [
    "CrossSectionalPreprocessor",
    "FinancialArchetypes",
    "UNSUPERVISED_FEATURE_SET",
    "add_classical_scores",
]
