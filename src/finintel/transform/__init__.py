"""Stream B2 Native Polars Incremental Transform & Blocking Audit Engine.

Verified Sources:
    Budiu, M., Chajed, T., McSherry, F., Ryzhyk, L., & Tannen, V. (2023).
    "DBSP: Automatic Incremental View Maintenance for Rich Query Languages."
    PVLDB Vol. 16, No. 7.
    docs/v2_specs/07_STREAM_B2_SQLMESH_AND_DAGSTER_PLATFORM.md (§3.1).
"""

from finintel.transform.audits import (
    DUPONT_TOLERANCE,
    run_blocking_publish_audits,
)
from finintel.transform.incremental import (
    TRANSFORM_STATE_FILENAME,
    MLTransformConfig,
    TransformBundle,
    TransformDiff,
    load_transform_state,
    run_incremental_analytics_transform,
    write_transform_state,
)

__all__ = [
    "DUPONT_TOLERANCE",
    "MLTransformConfig",
    "TRANSFORM_STATE_FILENAME",
    "TransformBundle",
    "TransformDiff",
    "load_transform_state",
    "run_blocking_publish_audits",
    "run_incremental_analytics_transform",
    "write_transform_state",
]
