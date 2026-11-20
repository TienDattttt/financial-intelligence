"""Dagster Definitions entrypoint for FinIntel V2 local single-node orchestration.

Verified Sources:
    - Dagster Official Documentation — Code Locations & Definitions:
      https://docs.dagster.io/guides/build/projects/
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``dg.Definitions(assets=..., asset_checks=..., jobs=..., schedules=..., resources=...)``
      - ``dg.Definitions.validate_loadable(defs)`` is a staticmethod accepting ``defs``.
"""

from __future__ import annotations

import dagster as dg

from finintel.orchestration.assets import (
    lakehouse_maintenance_job,
    lakehouse_maintenance_schedule,
    normalized_financial_facts,
    published_financial_marts,
    raw_fhsc_filings,
    raw_public_disclosures,
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

DEFAULT_RESOURCES: dict[str, object] = {
    "paths": FinIntelPathsResource(),
    "state": StateStoreResource(),
    "storage": StorageResource(),
    "disclosure_fetcher": DisclosureFetcherResource(),
}

defs = dg.Definitions(
    assets=ALL_ASSETS,
    asset_checks=ALL_ASSET_CHECKS,
    jobs=[lakehouse_maintenance_job],
    schedules=[lakehouse_maintenance_schedule],
    resources=DEFAULT_RESOURCES,
)
