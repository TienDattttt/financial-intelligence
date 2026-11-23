"""Layer 5 headless serving package: DuckDB query facade and read-only FastAPI API.

Verified Sources:
- FastAPI Official Docs (``fastapi==0.165.0``): https://fastapi.tiangolo.com/
- DuckDB Python API & Multithreading (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
- RFC 9110 HTTP Conditional Requests: https://httpwg.org/specs/rfc9110.html#field.etag
"""

from __future__ import annotations

import importlib

_EXPORTS: dict[str, tuple[str, str]] = {
    "app": ("finintel.serving.api", "app"),
    "create_app": ("finintel.serving.api", "create_app"),
    "run_server": ("finintel.serving.api", "run_server"),
    "DEFAULT_API_HOST": ("finintel.serving.dependencies", "DEFAULT_API_HOST"),
    "DEFAULT_API_PORT": ("finintel.serving.dependencies", "DEFAULT_API_PORT"),
    "build_lifespan": ("finintel.serving.dependencies", "build_lifespan"),
    "get_db_cursor": ("finintel.serving.dependencies", "get_db_cursor"),
    "get_query_service": ("finintel.serving.dependencies", "get_query_service"),
    "lifespan": ("finintel.serving.dependencies", "lifespan"),
    "ALL_RATIOS": ("finintel.serving.queries", "ALL_RATIOS"),
    "ALTMAN_ZONES": ("finintel.serving.queries", "ALTMAN_ZONES"),
    "BENEISH_FLAGS": ("finintel.serving.queries", "BENEISH_FLAGS"),
    "DEFAULT_DATA_ROOT": ("finintel.serving.queries", "DEFAULT_DATA_ROOT"),
    "PEER_BAND_HISTORY_SCHEMA": (
        "finintel.serving.queries",
        "PEER_BAND_HISTORY_SCHEMA",
    ),
    "PEER_GROUP_LEVELS": ("finintel.serving.queries", "PEER_GROUP_LEVELS"),
    "PERIOD_TYPES": ("finintel.serving.queries", "PERIOD_TYPES"),
    "PERIOD_VIEWS": ("finintel.serving.queries", "PERIOD_VIEWS"),
    "RATIO_LINEAGE_CATALOG": ("finintel.serving.queries", "RATIO_LINEAGE_CATALOG"),
    "SCREENER_SCHEMA": ("finintel.serving.queries", "SCREENER_SCHEMA"),
    "STATEMENT_SCOPES": ("finintel.serving.queries", "STATEMENT_SCOPES"),
    "AltmanZone": ("finintel.serving.queries", "AltmanZone"),
    "BeneishFlag": ("finintel.serving.queries", "BeneishFlag"),
    "CompanyProfileData": ("finintel.serving.queries", "CompanyProfileData"),
    "DashboardQueryService": ("finintel.serving.queries", "DashboardQueryService"),
    "DenominatorMode": ("finintel.serving.queries", "DenominatorMode"),
    "IndustryComparisonData": ("finintel.serving.queries", "IndustryComparisonData"),
    "LineageComponent": ("finintel.serving.queries", "LineageComponent"),
    "PeerGroupLevel": ("finintel.serving.queries", "PeerGroupLevel"),
    "PeriodType": ("finintel.serving.queries", "PeriodType"),
    "PeriodView": ("finintel.serving.queries", "PeriodView"),
    "QualityTablesPage": ("finintel.serving.queries", "QualityTablesPage"),
    "RatioLineageCard": ("finintel.serving.queries", "RatioLineageCard"),
    "RatioLineageSpec": ("finintel.serving.queries", "RatioLineageSpec"),
    "SnapshotOverview": ("finintel.serving.queries", "SnapshotOverview"),
    "StatementScope": ("finintel.serving.queries", "StatementScope"),
    "StressScenario": ("finintel.serving.queries", "StressScenario"),
    "pagination_offset": ("finintel.serving.queries", "pagination_offset"),
    "pagination_total_pages": ("finintel.serving.queries", "pagination_total_pages"),
    "pagination_window": ("finintel.serving.queries", "pagination_window"),
    "percent_to_decimal": ("finintel.serving.queries", "percent_to_decimal"),
    "resolve_data_root": ("finintel.serving.queries", "resolve_data_root"),
    "simulate_financial_stress": (
        "finintel.serving.queries",
        "simulate_financial_stress",
    ),
    "summarize_stress_by_sector": (
        "finintel.serving.queries",
        "summarize_stress_by_sector",
    ),
    "AuditQuarantinePageResponse": (
        "finintel.serving.schemas",
        "AuditQuarantinePageResponse",
    ),
    "CompanyBenchmarksResponse": (
        "finintel.serving.schemas",
        "CompanyBenchmarksResponse",
    ),
    "CompanyProfileResponse": ("finintel.serving.schemas", "CompanyProfileResponse"),
    "HealthResponse": ("finintel.serving.schemas", "HealthResponse"),
    "IssuersPageResponse": ("finintel.serving.schemas", "IssuersPageResponse"),
    "QualitySummaryPageResponse": (
        "finintel.serving.schemas",
        "QualitySummaryPageResponse",
    ),
    "RatioLineageResponse": ("finintel.serving.schemas", "RatioLineageResponse"),
    "ScreenerResponse": ("finintel.serving.schemas", "ScreenerResponse"),
    "SnapshotOverviewResponse": (
        "finintel.serving.schemas",
        "SnapshotOverviewResponse",
    ),
    "StressScenarioRequest": ("finintel.serving.schemas", "StressScenarioRequest"),
    "StressTestResponse": ("finintel.serving.schemas", "StressTestResponse"),
    "parse_shap_top_drivers": ("finintel.serving.schemas", "parse_shap_top_drivers"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    spec = _EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = spec
    module = importlib.import_module(mod_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
