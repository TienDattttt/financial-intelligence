"""Dagster Definitions entrypoint for FinIntel V2 local single-node orchestration.

Verified Sources:
    - Dagster Official Documentation — Code Locations & Definitions:
      https://docs.dagster.io/guides/build/projects/
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``dg.Definitions(assets=..., asset_checks=..., jobs=..., schedules=..., resources=...)``
      - ``dg.Definitions.validate_loadable(defs)`` is a staticmethod accepting ``defs``.
"""

from __future__ import annotations

import os
from pathlib import Path

import dagster as dg

from finintel.orchestration.assets import (
    downstream_publish_job,
    downstream_publish_sensor,
    fhsc_ingestion_job,
    lakehouse_maintenance_job,
    lakehouse_maintenance_schedule,
    normalized_financial_facts,
    published_financial_marts,
    quarterly_ml_retrain_job,
    quarterly_ml_retrain_schedule,
    raw_fhsc_filings,
    raw_public_disclosures,
    seasonal_ingestion_schedule,
)
from finintel.orchestration.checks import ALL_ASSET_CHECKS
from finintel.orchestration.resources import (
    DisclosureFetcherResource,
    FinIntelPathsResource,
    StateStoreResource,
    StorageResource,
)

ALL_ASSETS = [
    raw_fhsc_filings,
    raw_public_disclosures,
    normalized_financial_facts,
    published_financial_marts,
]


def build_default_resources() -> dict[str, object]:
    """Build default Dagster resources honoring ``FININTEL_DATA_ROOT`` if configured."""
    env_data_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
    if env_data_root:
        data_root_path = Path(env_data_root)
        paths_res = FinIntelPathsResource(data_root=str(data_root_path))
        state_res = StateStoreResource(
            state_path=str(data_root_path / "metadata" / "state.sqlite")
        )
    else:
        paths_res = FinIntelPathsResource()
        state_res = StateStoreResource()
    return {
        "paths": paths_res,
        "state": state_res,
        "storage": StorageResource(),
        "disclosure_fetcher": DisclosureFetcherResource(),
    }


DEFAULT_RESOURCES: dict[str, object] = build_default_resources()

defs = dg.Definitions(
    assets=ALL_ASSETS,
    asset_checks=ALL_ASSET_CHECKS,
    jobs=[
        fhsc_ingestion_job,
        downstream_publish_job,
        quarterly_ml_retrain_job,
        lakehouse_maintenance_job,
    ],
    schedules=[
        seasonal_ingestion_schedule,
        quarterly_ml_retrain_schedule,
        lakehouse_maintenance_schedule,
    ],
    sensors=[downstream_publish_sensor],
    resources=DEFAULT_RESOURCES,
)
