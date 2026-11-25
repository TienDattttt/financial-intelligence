"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import type { EChartsOption } from "@/components/charts/EChartBox";
import { resolveCrossFilteredMlScore } from "@/components/charts/ExplainWaterfallChart";
import {
  CompanyHeader,
  type CompanyTab,
} from "@/components/company/CompanyHeader";

const COMPANY_TABS: readonly { id: CompanyTab; label: string }[] = [
  { id: "overview", label: "Tổng quan 1 trang" },
  { id: "statements", label: "Báo cáo tài chính" },
  { id: "ratios", label: "Tỷ số & Ngành" },
  { id: "quality", label: "Chất lượng LN & Cờ đỏ" },
  { id: "model", label: "Mô hình & SHAP" },
  { id: "documents", label: "Điều tra Thuyết minh (PDF)" },
  { id: "lineage", label: "Nguồn dữ liệu" },
];
import { FinancialsTab } from "@/components/company/FinancialsTab";
import { ModelShapTab } from "@/components/company/ModelShapTab";
import { OverviewTab } from "@/components/company/OverviewTab";
import { QualityTab } from "@/components/company/QualityTab";
import { RatiosTab } from "@/components/company/RatiosTab";
import { SourcesTab } from "@/components/company/SourcesTab";
import { DataCoverageNotice } from "@/components/financial/DataCoverageNotice";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import {
  DocumentInvestigationPanel,
  type DocumentInvestigationPreset,
} from "@/components/rag/DocumentInvestigationPanel";
import {
  useAllIssuers,
  useCompanyBenchmarks,
  useCompanyBenchmarksSummary,
  useCompanyFilings,
  useCompanyMarketOverlay,
  useCompanyProfile,
  useCompanyStatements,
  useMetaDictionary,
} from "@/hooks/use-finintel-queries";
import { formatCreditTrio } from "@/lib/domain-labels";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPeriodLabel } from "@/lib/format";
import {
  getDensityServerSnapshot,
  getDensitySnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  setLastViewedTicker,
  subscribePreferences,
  toggleWatchlistTicker,
} from "@/lib/watchlist-store";
import type { RatioDictionaryItem, RatioName, StatementType } from "@/types/api";

function computeRangeStats(values: readonly (number | null | undefined)[]) {
  const nums = values
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .slice(-10);
  if (nums.length === 0) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median =
    sorted.length % 2 === 1
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;
  return {
    min: sorted[0],
    median,
    max: sorted[sorted.length - 1],
  };
}

function CompanyProfileContent() {
  const params = useParams<{ ticker: string }>();
  const ticker = React.useMemo(
    () => decodeURIComponent(params.ticker ?? "").trim().toUpperCase(),
    [params.ticker],
  );
  return <CompanyProfileBody key={ticker} ticker={ticker} />;
}

function CompanyProfileBody({ ticker }: { ticker: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    statementScope,
    periodType,
    periodEndDate,
    periodView,
    snapshotId,
    openSourceDrawer,
    closeSourceDrawer,
  } = useAppState();
  const { palette } = useChartTheme();

  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );
  const density = React.useSyncExternalStore(
    subscribePreferences,
    getDensitySnapshot,
    getDensityServerSnapshot,
  );
  const isWatched = watchlist.includes(ticker);

  const rawTab = searchParams.get("tab") as CompanyTab | null;
  const activeTab: CompanyTab =
    rawTab && COMPANY_TABS.some((t) => t.id === rawTab) ? rawTab : "overview";

  const metricParam = (searchParams.get("metric") as RatioName | null) ?? "roe_total";

  const [showAll38, setShowAll38] = React.useState(false);
  const [trafficMode, setTrafficMode] = React.useState<"VALUE" | "PERCENTILE">("VALUE");
  const [growthLocalView, setGrowthLocalView] = React.useState<"TTM" | "Quarterly">("TTM");
  const mlContextKey = `${ticker}|${statementScope}|${periodType}`;
  const [selectedMlState, setSelectedMlState] = React.useState<{
    key: string;
    periodEnd: string;
  } | null>(null);
  const selectedMlPeriodEnd =
    selectedMlState?.key === mlContextKey ? selectedMlState.periodEnd : null;
  const setSelectedMlPeriodEnd = React.useCallback(
    (periodEnd: string | null) => {
      setSelectedMlState(periodEnd ? { key: mlContextKey, periodEnd } : null);
    },
    [mlContextKey],
  );
  const [ratiosShowAll38, setRatiosShowAll38] = React.useState(true);
  const [statementType, setStatementType] =
    React.useState<StatementType>("INCOME_STATEMENT");
  const [periodsLimit, setPeriodsLimit] = React.useState<number>(12);
  const [lineageFilter, setLineageFilter] = React.useState("");
  const [ragPreset, setRagPreset] =
    React.useState<DocumentInvestigationPreset | null>(null);

  React.useEffect(() => {
    closeSourceDrawer();
  }, [ticker, closeSourceDrawer]);

  const effectiveGrowthView = periodType === "ANNUAL" ? "Annual" : growthLocalView;

  const profileQuery = useCompanyProfile(ticker, statementScope, periodView, snapshotId);
  const growthProfileQuery = useCompanyProfile(
    ticker,
    statementScope,
    effectiveGrowthView,
    snapshotId,
  );
  const dictQuery = useMetaDictionary(snapshotId);
  const issuersQuery = useAllIssuers(snapshotId);
  const filingsQuery = useCompanyFilings(ticker, { statementScope }, snapshotId);
  const overlayQuery = useCompanyMarketOverlay(ticker, statementScope, snapshotId);

  const loadedTicker = profileQuery.data?.ticker;
  React.useEffect(() => {
    if (loadedTicker) {
      setLastViewedTicker(loadedTicker);
    }
  }, [loadedTicker]);

  const allMetricNames = React.useMemo(
    () => (dictQuery.data?.ratios ?? []).map((r) => r.ratio_name),
    [dictQuery.data?.ratios],
  );

  const shouldFetchAllSummary =
    (activeTab === "overview" && showAll38) ||
    (activeTab === "ratios" && ratiosShowAll38);

  const summaryQuery = useCompanyBenchmarksSummary(
    ticker,
    {
      statementScope,
      periodType,
      periodEndDate,
      metrics:
        shouldFetchAllSummary && allMetricNames.length > 0
          ? allMetricNames
          : undefined,
      enabled: activeTab === "overview" || activeTab === "ratios",
    },
    snapshotId,
  );

  const statementsQuery = useCompanyStatements(
    ticker,
    {
      statementType,
      statementScope,
      periodType,
      periodsLimit,
      enabled: activeTab === "statements",
    },
    snapshotId,
  );

  const benchmarksQuery = useCompanyBenchmarks(
    ticker,
    {
      metricName: metricParam,
      statementScope,
      periodType,
      periodEndDate,
      enabled: activeTab === "ratios",
    },
    snapshotId,
  );

  const setTabAndParams = React.useCallback(
    (tab: CompanyTab, extra?: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      if (tab === "overview") next.delete("tab");
      else next.set("tab", tab);
      if (extra) {
        for (const [k, v] of Object.entries(extra)) {
          if (!v) next.delete(k);
          else next.set(k, v);
        }
      }
      const qs = next.toString();
      router.replace(qs ? `/companies/${ticker}?${qs}` : `/companies/${ticker}`, {
        scroll: false,
      });
    },
    [router, searchParams, ticker],
  );

  const handleInvestigateRedFlag = React.useCallback(
    (flagCode: string, presetQuestion: string) => {
      setRagPreset({
        flagCode,
        question: presetQuestion,
        requestNonce: Date.now(),
      });
      setTabAndParams("documents");
    },
    [setTabAndParams],
  );

  const issuerMeta = React.useMemo(
    () =>
      (issuersQuery.data ?? []).find(
        (iss) => iss.ticker.toUpperCase() === ticker,
      ) ?? null,
    [issuersQuery.data, ticker],
  );

  const ratioDictItems = dictQuery.data?.ratios;
  const ratioDictMap = React.useMemo(() => {
    const map = new Map<RatioName, RatioDictionaryItem>();
    for (const r of ratioDictItems ?? []) {
      map.set(r.ratio_name, r);
    }
    return map;
  }, [ratioDictItems]);

  const sortedMl = React.useMemo(() => {
    return [...(profileQuery.data?.ml_scores ?? [])].sort((a, b) =>
      a.period_end_date.localeCompare(b.period_end_date),
    );
  }, [profileQuery.data?.ml_scores]);

  const {
    active: activeMl,
    defaultLatest: latestMl,
    isCustomSelected: isCustomMlPeriod,
  } = React.useMemo(
    () =>
      resolveCrossFilteredMlScore(
        sortedMl,
        selectedMlPeriodEnd,
        periodEndDate,
      ),
    [periodEndDate, selectedMlPeriodEnd, sortedMl],
  );

  const prevMl = React.useMemo(() => {
    if (!latestMl) return null;
    const idx = sortedMl.findIndex(
      (m) => m.period_end_date === latestMl.period_end_date,
    );
    return idx > 0 ? sortedMl[idx - 1] ?? null : null;
  }, [latestMl, sortedMl]);

  const handleRiskPeriodClick = React.useCallback(
    (payload: { dataIndex?: number }) => {
      if (typeof payload.dataIndex !== "number") return;
      const target = sortedMl[payload.dataIndex];
      if (!target) return;
      setSelectedMlPeriodEnd(target.period_end_date);
    },
    [setSelectedMlPeriodEnd, sortedMl],
  );

  const activeMlPeriodLabel = activeMl
    ? formatPeriodLabel(activeMl.period_end_date, periodType)
    : null;

  const activePeriodEnd =
    latestMl?.period_end_date ??
    summaryQuery.data?.period_end_date ??
    periodEndDate ??
    null;

  const zStats = React.useMemo(
    () => computeRangeStats(sortedMl.map((m) => m.altman_z_double_prime)),
    [sortedMl],
  );
  const probStats = React.useMemo(
    () => computeRangeStats(sortedMl.map((m) => m.calibrated_distress_prob)),
    [sortedMl],
  );

  const latestRatioRow = React.useMemo(() => {
    const table = [...(profileQuery.data?.ratios_table ?? [])].sort((a, b) =>
      String(a.period_end_date ?? "").localeCompare(String(b.period_end_date ?? "")),
    );
    if (table.length === 0) return null;
    if (periodEndDate) {
      const match = table.find((r) => String(r.period_end_date ?? "") === periodEndDate);
      if (match) return match;
    }
    return table[table.length - 1] ?? null;
  }, [periodEndDate, profileQuery.data?.ratios_table]);

  // Single-Y-axis ECharts options with vi-VN number formatting
  const mlAltmanOption = React.useMemo<EChartsOption>(() => {
    const labels = sortedMl.map((m) => formatPeriodLabel(m.period_end_date, periodType));
    const selectedIdx = activeMl
      ? sortedMl.findIndex((m) => m.period_end_date === activeMl.period_end_date)
      : -1;
    const selectedVal =
      selectedIdx >= 0 ? sortedMl[selectedIdx]?.altman_z_double_prime : null;

    return {
      grid: { left: 52, right: 24, top: 28, bottom: 32 },
      tooltip: {
        trigger: "axis",
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const m = sortedMl[first?.dataIndex ?? -1];
          if (!m) return "";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${formatPeriodLabel(m.period_end_date, periodType)}</div>`,
            `<div>Altman Z″ (EM): <b>${formatNumber(m.altman_z_double_prime, 2)}</b></div>`,
            `<div style="color:${palette.company};font-size:11px;margin-top:3px">Nhấp để xem TreeSHAP kỳ này</div>`,
          ].join("");
        },
      },
      xAxis: { type: "category", data: labels },
      yAxis: {
        type: "value",
        name: "Altman Z″",
        axisLabel: {
          formatter: (v: number) => formatChartNumber(v, 1, { trimTrailingZeros: true }),
        },
      },
      series: [
        {
          name: "Altman Z″",
          type: "line",
          symbolSize: 7,
          lineStyle: { color: palette.company, width: 2 },
          itemStyle: { color: palette.company },
          data: sortedMl.map((m) => m.altman_z_double_prime ?? null),
          ...(selectedIdx >= 0 &&
          typeof selectedVal === "number" &&
          Number.isFinite(selectedVal)
            ? {
                markPoint: {
                  symbolSize: 12,
                  symbol: "circle",
                  itemStyle: {
                    color: palette.company,
                    borderColor: palette.surface,
                    borderWidth: 2,
                  },
                  label: { show: false },
                  data: [{ name: "Kỳ đang chọn", coord: [selectedIdx, selectedVal] }],
                },
              }
            : {}),
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { type: "dashed", width: 1 },
            label: { fontSize: 10 },
            data: [
              {
                yAxis: 1.1,
                lineStyle: { color: palette.riskHigh },
                label: { formatter: "1,10 (Nguy cơ)", color: palette.riskHigh, position: "insideEndTop" },
              },
              {
                yAxis: 2.6,
                lineStyle: { color: palette.riskLow },
                label: { formatter: "2,60 (An toàn)", color: palette.riskLow, position: "insideEndTop" },
              },
            ],
          },
        },
      ],
    };
  }, [activeMl, palette.company, palette.riskHigh, palette.riskLow, palette.surface, periodType, sortedMl]);

  const mlDistressProbOption = React.useMemo<EChartsOption>(() => {
    const labels = sortedMl.map((m) => formatPeriodLabel(m.period_end_date, periodType));
    const selectedIdx = activeMl
      ? sortedMl.findIndex((m) => m.period_end_date === activeMl.period_end_date)
      : -1;
    const rawSelected =
      selectedIdx >= 0 ? sortedMl[selectedIdx]?.calibrated_distress_prob : null;
    const selectedPct =
      typeof rawSelected === "number" && Number.isFinite(rawSelected)
        ? Number((rawSelected * 100).toFixed(2))
        : null;

    return {
      grid: { left: 54, right: 20, top: 28, bottom: 32 },
      tooltip: {
        trigger: "axis",
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const m = sortedMl[first?.dataIndex ?? -1];
          if (!m) return "";
          const trio = formatCreditTrio({
            risk_tier: m.risk_tier,
            risk_market_percentile: m.risk_market_percentile,
            calibrated_distress_prob: m.calibrated_distress_prob,
            relative_risk_lift: m.relative_risk_lift,
            market_base_rate: m.market_base_rate,
          });
          return [
            `<div style="font-weight:600;margin-bottom:4px">${formatPeriodLabel(m.period_end_date, periodType)}</div>`,
            `<div>1Y PD mô hình: <b style="color:${palette.riskHigh}">${trio.pdLabel}</b>${trio.liftLabel ? ` (${trio.liftLabel})` : ""}</div>`,
            `<div>Bậc &amp; Vị thế: <b>${trio.tierMeta.shortLabel}</b> · ${trio.topPercentLabel}</div>`,
            `<div style="color:${palette.company};font-size:11px;margin-top:3px">Nhấp để xem TreeSHAP kỳ này</div>`,
          ].join("");
        },
      },
      xAxis: { type: "category", data: labels },
      yAxis: {
        type: "value",
        name: "XS kiệt quệ (%)",
        min: 0,
        axisLabel: {
          formatter: (v: number) =>
            `${formatChartNumber(v, 1, { trimTrailingZeros: true })}%`,
        },
      },
      series: [
        {
          name: "XS kiệt quệ (%)",
          type: "line",
          symbolSize: 7,
          lineStyle: { color: palette.riskHigh, width: 2 },
          itemStyle: { color: palette.riskHigh },
          data: sortedMl.map((m) =>
            m.calibrated_distress_prob !== null && m.calibrated_distress_prob !== undefined
              ? Number((m.calibrated_distress_prob * 100).toFixed(2))
              : null,
          ),
          ...(selectedIdx >= 0 && selectedPct !== null
            ? {
                markPoint: {
                  symbolSize: 12,
                  symbol: "circle",
                  itemStyle: {
                    color: palette.riskHigh,
                    borderColor: palette.surface,
                    borderWidth: 2,
                  },
                  label: { show: false },
                  data: [{ name: "Kỳ đang chọn", coord: [selectedIdx, selectedPct] }],
                },
              }
            : {}),
        },
      ],
    };
  }, [activeMl, palette.company, palette.riskHigh, palette.surface, periodType, sortedMl]);

  if (profileQuery.isLoading) {
    return <PageSkeleton />;
  }

  if (profileQuery.isError) {
    return (
      <ErrorState
        message={profileQuery.error.message}
        onRetry={() => profileQuery.refetch()}
      />
    );
  }

  const profile = profileQuery.data;
  if (!profile) {
    return (
      <EmptyState
        title={`Không tìm thấy hồ sơ doanh nghiệp ${ticker}`}
        description="Kiểm tra lại mã chứng khoán hoặc chọn doanh nghiệp từ thanh tìm kiếm (Ctrl+K)."
      />
    );
  }

  const activeRedFlags = activePeriodEnd
    ? profile.red_flags.filter((f) => f.period_end_date === activePeriodEnd)
    : profile.red_flags;
  const latestFiling = filingsQuery.data?.items[0] ?? null;
  const benchmarkMeta = ratioDictMap.get(metricParam);

  return (
    <div className="space-y-4">
      <CompanyHeader
        ticker={profile.ticker}
        companyName={profile.company_name_vn ?? issuerMeta?.company_name_vn ?? profile.ticker}
        exchange={issuerMeta?.exchange}
        sectorLevel2={issuerMeta?.icb_name_level2}
        sectorLevel3={issuerMeta?.icb_name_level3}
        latestTaxonomyVersion={profile.latest_taxonomy_version}
        riskTier={latestMl?.risk_tier}
        statementScope={statementScope}
        periodType={periodType}
        activePeriodEnd={activePeriodEnd}
        isWatched={isWatched}
        onToggleWatchlist={() => toggleWatchlistTicker(profile.ticker)}
        activeTab={activeTab}
        onChangeTab={(tab) => setTabAndParams(tab)}
      />

      <DataCoverageNotice
        ticker={profile.ticker}
        exchange={issuerMeta?.exchange}
        accountingRegime={profile.accounting_regime}
        hasFinancialData={profile.ratios_table.length > 0 || sortedMl.length > 0}
        hasSupervisedPd={
          latestMl?.calibrated_distress_prob !== null &&
          latestMl?.calibrated_distress_prob !== undefined
        }
        hasEodPrices={
          overlayQuery.isLoading || overlayQuery.isError
            ? null
            : (overlayQuery.data?.price_series.length ?? 0) > 0
        }
      />

      {activeTab === "overview" ? (
        <OverviewTab
          profile={profile}
          sortedMl={sortedMl}
          latestMl={latestMl}
          prevMl={prevMl}
          activeMl={activeMl}
          activeMlPeriodLabel={activeMlPeriodLabel}
          isCustomMlPeriod={isCustomMlPeriod}
          onResetMlPeriod={() => setSelectedMlPeriodEnd(null)}
          mlDistressProbOption={mlDistressProbOption}
          onRiskPeriodClick={handleRiskPeriodClick}
          zStats={zStats}
          probStats={probStats}
          activeRedFlags={activeRedFlags}
          latestFiling={latestFiling}
          filingsLoading={filingsQuery.isLoading}
          overlayData={overlayQuery.data}
          overlayLoading={overlayQuery.isLoading}
          overlayError={overlayQuery.isError}
          overlayErrorMessage={overlayQuery.error?.message}
          onRetryOverlay={() => overlayQuery.refetch()}
          growthSeries={growthProfileQuery.data?.growth_series ?? profile.growth_series}
          growthLoading={growthProfileQuery.isLoading && !growthProfileQuery.data}
          effectiveGrowthView={effectiveGrowthView}
          onChangeGrowthLocalView={setGrowthLocalView}
          summaryData={summaryQuery.data}
          summaryLoading={summaryQuery.isLoading}
          summaryError={summaryQuery.isError}
          summaryErrorMessage={summaryQuery.error?.message}
          onRetrySummary={() => summaryQuery.refetch()}
          showAll38={showAll38}
          onChangeShowAll38={setShowAll38}
          trafficMode={trafficMode}
          onChangeTrafficMode={setTrafficMode}
          ratioDictMap={ratioDictMap}
          statementScope={statementScope}
          periodType={periodType}
          periodEndDate={periodEndDate}
          onNavigateTab={setTabAndParams}
          onInvestigateRedFlag={handleInvestigateRedFlag}
          onOpenSourceDrawer={openSourceDrawer}
        />
      ) : activeTab === "statements" ? (
        <FinancialsTab
          statementType={statementType}
          onChangeStatementType={setStatementType}
          periodType={periodType}
          periodsLimit={periodsLimit}
          onChangePeriodsLimit={setPeriodsLimit}
          isLoading={statementsQuery.isLoading}
          isError={statementsQuery.isError}
          errorMessage={statementsQuery.error?.message}
          onRetry={() => statementsQuery.refetch()}
          data={statementsQuery.data}
          compact={density === "compact"}
        />
      ) : activeTab === "ratios" ? (
        <RatiosTab
          profile={profile}
          metricParam={metricParam}
          benchmarkMeta={benchmarkMeta}
          ratiosDict={dictQuery.data?.ratios ?? []}
          benchmarksData={benchmarksQuery.data}
          benchmarksLoading={benchmarksQuery.isLoading}
          benchmarksError={benchmarksQuery.isError}
          benchmarksErrorMessage={benchmarksQuery.error?.message}
          onRetryBenchmarks={() => benchmarksQuery.refetch()}
          summaryData={summaryQuery.data}
          summaryLoading={summaryQuery.isLoading}
          ratiosShowAll38={ratiosShowAll38}
          onChangeRatiosShowAll38={setRatiosShowAll38}
          statementScope={statementScope}
          periodType={periodType}
          periodEndDate={periodEndDate}
          onNavigateTab={setTabAndParams}
          onOpenSourceDrawer={openSourceDrawer}
        />
      ) : activeTab === "quality" ? (
        <QualityTab
          profile={profile}
          periodType={periodType}
          redFlagDict={dictQuery.data?.red_flags ?? []}
          activePeriodEnd={activePeriodEnd}
          onInvestigateRedFlag={handleInvestigateRedFlag}
          filings={filingsQuery.data?.items ?? []}
          filingsLoading={filingsQuery.isLoading}
        />
      ) : activeTab === "model" ? (
        <ModelShapTab
          sortedMl={sortedMl}
          activeMl={activeMl}
          activeMlPeriodLabel={activeMlPeriodLabel}
          isCustomMlPeriod={isCustomMlPeriod}
          onResetToLatest={() => setSelectedMlPeriodEnd(null)}
          mlAltmanOption={mlAltmanOption}
          mlDistressProbOption={mlDistressProbOption}
          onRiskPeriodClick={handleRiskPeriodClick}
          periodType={periodType}
          ratioDictMap={ratioDictMap}
        />
      ) : activeTab === "documents" ? (
        <DocumentInvestigationPanel
          key={`${profile.ticker}-${ragPreset?.requestNonce ?? 0}`}
          ticker={profile.ticker}
          companyName={profile.company_name_vn ?? issuerMeta?.company_name_vn ?? null}
          initialPreset={ragPreset}
        />
      ) : (
        <SourcesTab
          ticker={profile.ticker}
          ratios={dictQuery.data?.ratios ?? []}
          latestRatioRow={latestRatioRow}
          lineageFilter={lineageFilter}
          onChangeLineageFilter={setLineageFilter}
          statementScope={statementScope}
          periodType={periodType}
          periodEndDate={periodEndDate}
          onOpenSourceDrawer={openSourceDrawer}
        />
      )}
    </div>
  );
}

export default function CompanyProfilePage() {
  return (
    <React.Suspense fallback={<PageSkeleton />}>
      <CompanyProfileContent />
    </React.Suspense>
  );
}
