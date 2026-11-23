import type { components, operations } from "./api.generated";

export type Schema = components["schemas"];

export type HealthResponse = Schema["HealthResponse"];
export type SnapshotOverviewResponse = Schema["SnapshotOverviewResponse"];
export type IssuerItem = Schema["IssuerItem"];
export type IssuersPageResponse = Schema["IssuersPageResponse"];
export type CompanyProfileResponse = Schema["CompanyProfileResponse"];
export type MLScoreItem = Schema["MLScoreItem"];
export type ShapDriverItem = Schema["ShapDriverItem"];
export type ShapExplanationItem = Schema["ShapExplanationItem"];
export type GrowthPoint = Schema["GrowthPoint"];
export type CapitalStructurePoint = Schema["CapitalStructurePoint"];
export type DuPontPoint = Schema["DuPontPoint"];
export type CoreEarningsPoint = Schema["CoreEarningsPoint"];
export type RedFlagItem = Schema["RedFlagItem"];
export type CompanyBenchmarksResponse = Schema["CompanyBenchmarksResponse"];
export type PeerRankingItem = Schema["PeerRankingItem"];
export type PeerBandHistoryPoint = Schema["PeerBandHistoryPoint"];
export type RatioLineageResponse = Schema["RatioLineageResponse"];
export type LineageComponentItem = Schema["LineageComponentItem"];
export type ScreenerItem = Schema["ScreenerItem"];
export type ScreenerResponse = Schema["ScreenerResponse"];
export type StressScenarioRequest = Schema["StressScenarioRequest"];
export type StressSectorSummaryItem = Schema["StressSectorSummaryItem"];
export type StressIssuerItem = Schema["StressIssuerItem"];
export type StressTestResponse = Schema["StressTestResponse"];
export type QualitySummaryItem = Schema["QualitySummaryItem"];
export type QualitySummaryPageResponse = Schema["QualitySummaryPageResponse"];
export type AuditQuarantineItem = Schema["AuditQuarantineItem"];
export type AuditQuarantinePageResponse = Schema["AuditQuarantinePageResponse"];

export type SectorOptionItem = Schema["SectorOptionItem"];
export type MetaPeriodsResponse = Schema["MetaPeriodsResponse"];
export type RatioDictionaryItem = Schema["RatioDictionaryItem"];
export type RedFlagDictionaryItem = Schema["RedFlagDictionaryItem"];
export type MetaDictionaryResponse = Schema["MetaDictionaryResponse"];
export type BenchmarkSummaryItem = Schema["BenchmarkSummaryItem"];
export type CompanyBenchmarksSummaryResponse = Schema["CompanyBenchmarksSummaryResponse"];
export type StatementPeriodItem = Schema["StatementPeriodItem"];
export type StatementRowItem = Schema["StatementRowItem"];
export type CompanyStatementsResponse = Schema["CompanyStatementsResponse"];
export type CompanyFilingItem = Schema["CompanyFilingItem"];
export type CompanyFilingsResponse = Schema["CompanyFilingsResponse"];
export type SectorRiskItem = Schema["SectorRiskItem"];
export type MarketChangeItem = Schema["MarketChangeItem"];
export type SectorRiskSummaryResponse = Schema["SectorRiskSummaryResponse"];
export type MarketChangesResponse = Schema["MarketChangesResponse"];
export type RiskTierCutoffsItem = Schema["RiskTierCutoffsItem"];
export type RiskTierReliabilityRowItem = Schema["RiskTierReliabilityRowItem"];
export type RiskTierReliabilityReportItem = Schema["RiskTierReliabilityReportItem"];
export type ModelMetricsItem = Schema["ModelMetricsItem"];
export type BootstrapCI95Item = Schema["BootstrapCI95Item"];
export type WalkForwardYearItem = Schema["WalkForwardYearItem"];
export type WalkForwardModelSummaryItem = Schema["WalkForwardModelSummaryItem"];
export type WalkForwardReportItem = Schema["WalkForwardReportItem"];
export type TaxonomyScoreDriftItem = Schema["TaxonomyScoreDriftItem"];
export type FeatureDriftItem = Schema["FeatureDriftItem"];
export type TaxonomyDriftPSIItem = Schema["TaxonomyDriftPSIItem"];
export type HistoricalCaseStudyItem = Schema["HistoricalCaseStudyItem"];
export type ModelCardResponse = Schema["ModelCardResponse"];
export type DrawdownWindowMetricsItem = Schema["DrawdownWindowMetricsItem"];
export type MarkerDrawdownBundleItem = Schema["MarkerDrawdownBundleItem"];
export type ModelRiskMarkerItem = Schema["ModelRiskMarkerItem"];
export type RegulatoryEventMarkerItem = Schema["RegulatoryEventMarkerItem"];
export type PriceOverlayPointItem = Schema["PriceOverlayPointItem"];
export type WarningSummaryItem = Schema["WarningSummaryItem"];
export type CompanyMarketOverlayResponse = Schema["CompanyMarketOverlayResponse"];
export type DocumentFileItem = Schema["DocumentFileItem"];
export type DocumentListResponse = Schema["DocumentListResponse"];
export type CitationItem = Schema["CitationItem"];
export type InvestigateResponse = Schema["InvestigateResponse"];
export interface InvestigateRequest {
  question: string;
  fiscal_year?: number | null;
  statement_scope?: string | null;
  top_k?: number;
  use_cache?: boolean;
}

export type StatementScope = CompanyBenchmarksResponse["statement_scope"];
export type PeriodType = CompanyBenchmarksResponse["period_type"];
export type PeriodView = CompanyProfileResponse["period_view"];
export type RatioName = CompanyBenchmarksResponse["metric_name"];
export type StatementType = CompanyStatementsResponse["statement_type"];

export type ScreenerQueryParams = NonNullable<
  operations["screen_market_api_v1_screener_get"]["parameters"]["query"]
>;
export type ScreenerSortField = NonNullable<ScreenerQueryParams["sort_by"]>;
export type SortDirection = NonNullable<ScreenerQueryParams["sort_dir"]>;
