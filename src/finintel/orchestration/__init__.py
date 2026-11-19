"""Dagster local single-node partitioned orchestration layer for FinIntel V2.

Verified Sources:
    - Dagster Official Documentation (Partitions, Concurrency Pools, Asset Checks):
      https://docs.dagster.io/guides/build/partitions-and-backfills/
      https://docs.dagster.io/guides/limit-concurrency
      https://docs.dagster.io/guides/test/asset-checks
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``).
"""

from __future__ import annotations

from finintel.orchestration.assets import (
    FHSC_BACKFILL_POLICY,
    FhscIngestConfig,
    NormalizeAssetConfig,
    PublishMartsConfig,
    lakehouse_maintenance_job,
    lakehouse_maintenance_schedule,
    normalized_financial_facts,
    published_financial_marts,
    raw_fhsc_filings,
    raw_public_disclosures,
    run_lakehouse_maintenance,
)
from finintel.orchestration.checks import (
    ALL_ASSET_CHECKS,
    check_quality_gate_1_critical_ratio,
    check_quality_gate_2_lineage_and_dupont,
)
from finintel.orchestration.definitions import ALL_ASSETS, DEFAULT_RESOURCES, defs
from finintel.orchestration.partitions import (
    CORPORATE_TICKERS,
    CORPORATE_TICKERS_PARTITION_NAME,
    DEFAULT_CONCURRENCY_POOL_LIMITS,
    FHSC_API_POOL,
    PUBLIC_DISCLOSURE_POOL,
    configure_concurrency_pools,
    load_corporate_tickers,
    sync_corporate_ticker_partitions,
)
from finintel.orchestration.resources import (
    DisclosureFetcherResource,
    FinIntelPathsResource,
    StateStoreResource,
    StorageResource,
)

__all__ = [
    "ALL_ASSETS",
    "ALL_ASSET_CHECKS",
    "CORPORATE_TICKERS",
    "CORPORATE_TICKERS_PARTITION_NAME",
    "DEFAULT_CONCURRENCY_POOL_LIMITS",
    "DEFAULT_RESOURCES",
    "DisclosureFetcherResource",
    "FHSC_API_POOL",
    "FHSC_BACKFILL_POLICY",
    "FhscIngestConfig",
    "FinIntelPathsResource",
    "NormalizeAssetConfig",
    "PUBLIC_DISCLOSURE_POOL",
    "PublishMartsConfig",
    "StateStoreResource",
    "StorageResource",
    "check_quality_gate_1_critical_ratio",
    "check_quality_gate_2_lineage_and_dupont",
    "configure_concurrency_pools",
    "defs",
    "lakehouse_maintenance_job",
    "lakehouse_maintenance_schedule",
    "load_corporate_tickers",
    "normalized_financial_facts",
    "published_financial_marts",
    "raw_fhsc_filings",
    "raw_public_disclosures",
    "run_lakehouse_maintenance",
    "sync_corporate_ticker_partitions",
]
