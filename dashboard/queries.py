"""Thin backwards-compatible re-export shim for Layer 5 dashboard queries.

Verified Sources:
- Spec 08 Stream B3 (§3.2): canonical headless ``DashboardQueryService`` lives in
  ``src/finintel/serving/queries.py`` so Layer 5 serving never imports ``dashboard/``.
- DuckDB Python API & Multithreading (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads

All public symbols previously exported from ``dashboard.queries`` are preserved here
as pure re-exports from ``finintel.serving.queries``.
"""

from __future__ import annotations

from finintel.serving.queries import (
    ALL_RATIOS,
    ALTMAN_ZONES,
    BENEISH_FLAGS,
    DEFAULT_DATA_ROOT,
    PEER_BAND_HISTORY_SCHEMA,
    PEER_GROUP_LEVELS,
    PERIOD_TYPES,
    PERIOD_VIEWS,
    RATIO_LINEAGE_CATALOG,
    SCREENER_SCHEMA,
    STATEMENT_SCOPES,
    AltmanZone,
    BeneishFlag,
    CompanyProfileData,
    DashboardQueryService,
    DenominatorMode,
    IndustryComparisonData,
    LineageComponent,
    PeerGroupLevel,
    PeriodType,
    PeriodView,
    QualityTablesPage,
    RatioLineageCard,
    RatioLineageSpec,
    SnapshotOverview,
    StatementScope,
    StressScenario,
    pagination_offset,
    pagination_total_pages,
    pagination_window,
    percent_to_decimal,
    resolve_data_root,
    simulate_financial_stress,
    summarize_stress_by_sector,
)

__all__ = [
    "ALL_RATIOS",
    "ALTMAN_ZONES",
    "BENEISH_FLAGS",
    "DEFAULT_DATA_ROOT",
    "PEER_BAND_HISTORY_SCHEMA",
    "PEER_GROUP_LEVELS",
    "PERIOD_TYPES",
    "PERIOD_VIEWS",
    "RATIO_LINEAGE_CATALOG",
    "SCREENER_SCHEMA",
    "STATEMENT_SCOPES",
    "AltmanZone",
    "BeneishFlag",
    "CompanyProfileData",
    "DashboardQueryService",
    "DenominatorMode",
    "IndustryComparisonData",
    "LineageComponent",
    "PeerGroupLevel",
    "PeriodType",
    "PeriodView",
    "QualityTablesPage",
    "RatioLineageCard",
    "RatioLineageSpec",
    "SnapshotOverview",
    "StatementScope",
    "StressScenario",
    "pagination_offset",
    "pagination_total_pages",
    "pagination_window",
    "percent_to_decimal",
    "resolve_data_root",
    "simulate_financial_stress",
    "summarize_stress_by_sector",
]
