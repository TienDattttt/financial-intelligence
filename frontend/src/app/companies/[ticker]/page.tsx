"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Bookmark,
  CheckCircle2,
  FileSearch,
  GitCompare,
  Link2,
  Printer,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";
import { CapitalStructureChart } from "@/components/charts/CapitalStructureChart";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import {
  ExplainWaterfallChart,
  resolveCrossFilteredMlScore,
} from "@/components/charts/ExplainWaterfallChart";
import { GrowthComboChart } from "@/components/charts/GrowthComboChart";
import {
  groupAndSortSummaryRatios,
  HEALTH_PILLAR_DEFS,
  HealthRadarChart,
  type PillarGroupId,
  type RatioTableSortField,
} from "@/components/charts/HealthRadarChart";
import { PeerBandChart } from "@/components/charts/PeerBandChart";
import {
  PitValuationStrip,
  PriceRiskOverlayChart,
} from "@/components/charts/PriceRiskOverlayChart";
import { SectorBoxPlotChart } from "@/components/charts/SectorBoxPlotChart";
import { FinancialTable } from "@/components/financial/FinancialTable";
import { PercentileBar } from "@/components/financial/PercentileBar";
import { RedFlagChecklist } from "@/components/financial/RedFlagChecklist";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { ScoreDots } from "@/components/financial/ScoreDots";
import { SparklineSvg } from "@/components/financial/SparklineSvg";
import { VerdictCard } from "@/components/financial/VerdictCard";
import {
  DocumentInvestigationPanel,
  type DocumentInvestigationPreset,
  getRedFlagPresetQuestion,
} from "@/components/rag/DocumentInvestigationPanel";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { Badge, Input, Select } from "@/components/ui/primitives";
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
import {
  ALTMAN_ZONE_META,
  AUDIT_OPINION_LABELS,
  AUDIT_STATUS_LABELS,
  BENEISH_FLAG_META,
  formatCreditTrio,
  formatFormulaVi,
  formatPeerGroupLabel,
  getMlFeatureLabel,
  MODEL_ROLE_LABELS,
  PERIOD_TYPE_LABELS,
  RATIO_CATEGORY_LABELS,
  REVISION_REASON_LABELS,
  SEVERITY_META,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import { useChartTheme } from "@/lib/echarts-theme";
import {
  formatDateVi,
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
  formatRatioValue,
  formatVndBillions,
  MINUS_SIGN,
  normalizeSearchText,
} from "@/lib/format";
import {
  getDensityServerSnapshot,
  getDensitySnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  setLastViewedTicker,
  subscribePreferences,
  toggleWatchlistTicker,
} from "@/lib/watchlist-store";
import { cn } from "@/lib/utils";
import type { RatioDictionaryItem, RatioName, StatementType } from "@/types/api";

type CompanyTab =
  | "overview"
  | "statements"
  | "ratios"
  | "quality"
  | "model"
  | "documents"
  | "lineage";

const TABS: { id: CompanyTab; label: string }[] = [
  { id: "overview", label: "Tổng quan 1 trang" },
  { id: "statements", label: "Báo cáo tài chính" },
  { id: "ratios", label: "Tỷ số & Ngành" },
  { id: "quality", label: "Chất lượng LN & Cờ đỏ" },
  { id: "model", label: "Mô hình & SHAP" },
  { id: "documents", label: "Điều tra Thuyết minh (PDF)" },
  { id: "lineage", label: "Nguồn dữ liệu" },
];

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
  const router = useRouter();
  const searchParams = useSearchParams();
  const ticker = React.useMemo(
    () => decodeURIComponent(params.ticker ?? "").trim().toUpperCase(),
    [params.ticker],
  );

  const {
    statementScope,
    periodType,
    periodEndDate,
    periodView,
    snapshotId,
    openSourceDrawer,
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
    rawTab && TABS.some((t) => t.id === rawTab) ? rawTab : "overview";

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
  const [ratioSearch, setRatioSearch] = React.useState("");
  const [ratioGroupFilter, setRatioGroupFilter] = React.useState<PillarGroupId | "ALL">("ALL");
  const [ratioSortBy, setRatioSortBy] = React.useState<RatioTableSortField>("name");
  const [ratioSortDir, setRatioSortDir] = React.useState<"asc" | "desc">("asc");
  const [ratiosShowAll38, setRatiosShowAll38] = React.useState(true);
  const [statementType, setStatementType] =
    React.useState<StatementType>("INCOME_STATEMENT");
  const [periodsLimit, setPeriodsLimit] = React.useState<number>(12);
  const [lineageFilter, setLineageFilter] = React.useState("");
  const [ragPreset, setRagPreset] =
    React.useState<DocumentInvestigationPreset | null>(null);

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
    const list = [...(profileQuery.data?.ml_scores ?? [])].sort((a, b) =>
      a.period_end_date.localeCompare(b.period_end_date),
    );
    return list;
  }, [profileQuery.data?.ml_scores]);

  // Align latestMl with selected global periodEndDate, plus cross-filtered activeMl for TreeSHAP
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

  const groupedRatioBuckets = React.useMemo(
    () =>
      groupAndSortSummaryRatios(summaryQuery.data?.items ?? [], {
        search: ratioSearch,
        groupId: ratioGroupFilter,
        sortBy: ratioSortBy,
        sortDir: ratioSortDir,
      }),
    [
      ratioGroupFilter,
      ratioSearch,
      ratioSortBy,
      ratioSortDir,
      summaryQuery.data?.items,
    ],
  );

  const toggleRatioSort = React.useCallback(
    (field: RatioTableSortField) => {
      if (ratioSortBy === field) {
        setRatioSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setRatioSortBy(field);
        setRatioSortDir(field === "name" ? "asc" : "desc");
      }
    },
    [ratioSortBy],
  );

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

  // Latest ratios row for lineage tab
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

  // Historical ML Trend options for `overview` & `model` tabs — strictly single-Y-axis per chart (no mixed units)
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
      xAxis: {
        type: "category",
        data: labels,
      },
      yAxis: {
        type: "value",
        name: "Altman Z″",
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
      grid: { left: 52, right: 20, top: 28, bottom: 32 },
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
      xAxis: {
        type: "category",
        data: labels,
      },
      yAxis: {
        type: "value",
        name: "XS kiệt quệ (%)",
        min: 0,
        axisLabel: { formatter: "{value}%" },
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

  const altmanMeta =
    (latestMl?.altman_zone && ALTMAN_ZONE_META[latestMl.altman_zone]) || {
      label: "Chưa phân vùng",
      shortLabel: "N/A",
      tone: "neutral" as const,
      description: "Chưa đủ dữ liệu tính Altman Z″.",
    };

  const beneishMeta =
    (latestMl?.beneish_flag && BENEISH_FLAG_META[latestMl.beneish_flag]) || {
      label: "Chưa đánh giá",
      shortLabel: "N/A",
      tone: "neutral" as const,
      description: "Chưa có kết quả kiểm tra Beneish M-Score.",
    };

  const creditTrio = formatCreditTrio({
    risk_tier: latestMl?.risk_tier,
    risk_market_percentile: latestMl?.risk_market_percentile,
    calibrated_distress_prob: latestMl?.calibrated_distress_prob,
    relative_risk_lift: latestMl?.relative_risk_lift,
    market_base_rate: latestMl?.market_base_rate,
  });

  const fScore = latestMl?.piotroski_f_score ?? null;
  const fTone =
    fScore === null
      ? "neutral"
      : fScore >= 7
        ? "low"
        : fScore >= 4
          ? "mid"
          : "high";
  const fLabel =
    fScore === null
      ? "N/A"
      : fScore >= 7
        ? "Tốt"
        : fScore >= 4
          ? "Trung bình"
          : "Yếu";

  const zDelta =
    latestMl?.altman_z_double_prime !== null &&
    latestMl?.altman_z_double_prime !== undefined &&
    prevMl?.altman_z_double_prime !== null &&
    prevMl?.altman_z_double_prime !== undefined
      ? latestMl.altman_z_double_prime - prevMl.altman_z_double_prime
      : null;

  const probDelta =
    latestMl?.calibrated_distress_prob !== null &&
    latestMl?.calibrated_distress_prob !== undefined &&
    prevMl?.calibrated_distress_prob !== null &&
    prevMl?.calibrated_distress_prob !== undefined
      ? latestMl.calibrated_distress_prob - prevMl.calibrated_distress_prob
      : null;

  const fDelta =
    latestMl?.piotroski_f_score !== null &&
    latestMl?.piotroski_f_score !== undefined &&
    prevMl?.piotroski_f_score !== null &&
    prevMl?.piotroski_f_score !== undefined
      ? latestMl.piotroski_f_score - prevMl.piotroski_f_score
      : null;

  const activeRedFlags = activePeriodEnd
    ? profile.red_flags.filter((f) => f.period_end_date === activePeriodEnd)
    : profile.red_flags;

  const latestFiling = filingsQuery.data?.items[0] ?? null;

  const benchmarkMeta = ratioDictMap.get(metricParam);

  return (
    <div className="space-y-4">
      {/* Sticky Company Header */}
      <Card className="rounded-lg md:sticky md:top-[52px] md:z-20 bg-surface shadow-xs">
        <CardContent className="flex flex-col justify-between gap-3 p-4 lg:flex-row lg:items-center">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-primary px-2 py-0.5 font-mono text-sm font-semibold text-primary-foreground">
                {profile.ticker}
              </span>
              <h1 className="truncate text-lg font-semibold text-ink">
                {profile.company_name_vn ?? issuerMeta?.company_name_vn ?? profile.ticker}
              </h1>
              {issuerMeta?.exchange ? (
                <Badge className="border border-border bg-surface-muted text-ink">
                  {issuerMeta.exchange}
                </Badge>
              ) : null}
              {profile.latest_taxonomy_version ? (
                <Badge className="border border-border bg-canvas text-ink-muted">
                  {TAXONOMY_LABELS[profile.latest_taxonomy_version] ??
                    profile.latest_taxonomy_version}
                </Badge>
              ) : null}
            </div>
            <p className="text-xs text-ink-muted">
              {issuerMeta?.icb_name_level2 ?? "Chưa phân loại ngành"}
              {issuerMeta?.icb_name_level3 ? ` · ${issuerMeta.icb_name_level3}` : ""} ·{" "}
              {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
              {PERIOD_TYPE_LABELS[periodType] ?? periodType}
              {activePeriodEnd
                ? ` · Kỳ đánh giá: ${formatPeriodLabel(activePeriodEnd, periodType)}`
                : ""}
            </p>
          </div>

          <div className="no-print flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant={isWatched ? "default" : "outline"}
              size="sm"
              onClick={() => toggleWatchlistTicker(profile.ticker)}
            >
              <Bookmark className={cn("size-3.5", isWatched && "fill-current")} />
              {isWatched ? "Đang theo dõi" : "Theo dõi"}
            </Button>
            <Link href={`/compare?tickers=${profile.ticker}`}>
              <Button variant="outline" size="sm">
                <GitCompare className="size-3.5" />
                So sánh
              </Button>
            </Link>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => window.print()}
            >
              <Printer className="size-3.5" />
              In báo cáo
            </Button>
          </div>
        </CardContent>
      </Card>

      {profile.accounting_regime !== "CORPORATE" || sortedMl.length === 0 ? (
        <div className="flex items-start gap-2 rounded border border-risk-mid/35 bg-risk-mid-soft p-3 text-xs text-risk-mid">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            {profile.accounting_regime !== "CORPORATE"
              ? "Tổ chức này thuộc nhóm định chế tài chính/bảo hiểm áp dụng chuẩn mực kế toán đặc thù; các chỉ số chấm điểm doanh nghiệp phi tài chính không áp dụng trực tiếp."
              : "Chưa đủ dữ liệu chuỗi kỳ để chấm điểm mô hình cho lát cắt báo cáo đang chọn. Thử chuyển giữa Hợp nhất / Riêng lẻ hoặc Theo quý / Theo năm."}
          </span>
        </div>
      ) : null}

      {/* Navigation Tabs */}
      <div className="no-print flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTabAndParams(t.id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
              activeTab === t.id
                ? "border-accent-ink font-semibold text-accent-ink"
                : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* TAB 1: 1-PAGE STOCKREPORT OVERVIEW */}
      {activeTab === "overview" ? (
        <div className="space-y-5">
          {/* Tier 1: 4 Verdict Cards */}
          <section
            aria-label="Tổng hợp điểm sức khỏe tài chính"
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
          >
            <VerdictCard
              title="Altman Z″ (EM)"
              tooltip={altmanMeta.description}
              value={formatNumber(latestMl?.altman_z_double_prime, 2)}
              tone={altmanMeta.tone}
              badgeLabel={altmanMeta.shortLabel}
              thresholdText="Ngưỡng: < 1,10 Nguy cơ · > 2,60 An toàn"
              deltaText={
                zDelta !== null
                  ? `${zDelta >= 0 ? "+" : MINUS_SIGN}${formatNumber(Math.abs(zDelta), 2)} so kỳ trước`
                  : null
              }
              deltaTone={
                zDelta === null
                  ? "neutral"
                  : zDelta > 0.05
                    ? "up"
                    : zDelta < -0.05
                      ? "down"
                      : "neutral"
              }
              historyRangeText={
                zStats
                  ? `10 kỳ: Thấp ${formatNumber(zStats.min, 2)} · TV ${formatNumber(zStats.median, 2)} · Cao ${formatNumber(zStats.max, 2)}`
                  : null
              }
            />

            <VerdictCard
              title="Piotroski F-Score"
              tooltip="Thang điểm 0–9 đánh giá 3 trụ cột: Sinh lời, Đòn bẩy/Thanh khoản và Hiệu quả hoạt động."
              value={fScore !== null ? `${fScore}/9` : "N/A"}
              tone={fTone}
              badgeLabel={fLabel}
              thresholdText="7–9 Tốt · 4–6 Trung bình · 0–3 Yếu"
              deltaText={
                fDelta !== null
                  ? `${fDelta >= 0 ? "+" : MINUS_SIGN}${Math.abs(fDelta)} điểm`
                  : null
              }
              deltaTone={
                fDelta === null
                  ? "neutral"
                  : fDelta > 0
                    ? "up"
                    : fDelta < 0
                      ? "down"
                      : "neutral"
              }
            >
              <ScoreDots
                score={fScore}
                validComponents={latestMl?.piotroski_valid_components_count}
              />
            </VerdictCard>

            <VerdictCard
              title="Beneish M-Score"
              tooltip="Mô hình 8 biến phát hiện nguy cơ điều chỉnh lợi nhuận bất thường so với kỳ trước."
              value={formatNumber(latestMl?.beneish_m8_score, 2)}
              tone={beneishMeta.tone}
              badgeLabel={beneishMeta.shortLabel}
              thresholdText="Ngưỡng sàng lọc: ≤ −1,78 Bình thường"
              footerExtra={beneishMeta.description}
            />

            <VerdictCard
              title="Xác suất kiệt quệ 1 năm (1Y PD)"
              tooltip={creditTrio.tierMeta.description}
              value={creditTrio.pdLabel}
              subValueText={creditTrio.liftLabel}
              tone={creditTrio.tierMeta.tone}
              badgeLabel={creditTrio.tierMeta.shortLabel}
              thresholdText={creditTrio.baseRateLabel ?? creditTrio.tierMeta.description}
              deltaText={
                probDelta !== null ? formatPercentPoints(probDelta, 1) : null
              }
              deltaTone={
                probDelta === null
                  ? "neutral"
                  : probDelta > 0.005
                    ? "down"
                    : probDelta < -0.005
                      ? "up"
                      : "neutral"
              }
              historyRangeText={
                probStats
                  ? `10 kỳ: Thấp ${formatPercent(probStats.min)} · TV ${formatPercent(probStats.median)} · Cao ${formatPercent(probStats.max)}`
                  : null
              }
            >
              <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                <RiskTierBadge riskTier={latestMl?.risk_tier} compact />
                <span className="rounded bg-surface-muted px-1.5 py-0.5 font-medium text-ink-muted">
                  {creditTrio.topPercentLabel}
                </span>
              </div>
            </VerdictCard>
          </section>

          {/* Tier 1b: PIT Market Valuation & Early-Warning Price Overlay (UI-09b) */}
          <PitValuationStrip
            latestValuation={overlayQuery.data?.latest_valuation}
            isLoading={overlayQuery.isLoading}
          />

          <PriceRiskOverlayChart
            ticker={profile.ticker}
            overlay={overlayQuery.data}
            isLoading={overlayQuery.isLoading}
            isError={overlayQuery.isError}
            errorMessage={overlayQuery.error?.message}
            onRetry={() => overlayQuery.refetch()}
          />

          {/* Tier 2: SHAP Drivers (60%) + Red Flags & Filings (40%) */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
            <div className="space-y-4 lg:col-span-7">
              <ExplainWaterfallChart
                shap={activeMl?.shap_top_drivers}
                distressProb={activeMl?.calibrated_distress_prob}
                periodLabel={activeMlPeriodLabel}
                isCustomPeriod={isCustomMlPeriod}
                onResetToLatest={() => setSelectedMlPeriodEnd(null)}
              />

              <EChartBox
                title="Diễn biến Xác suất kiệt quệ theo kỳ — Nhấp kỳ để xem TreeSHAP"
                subtitle="Đơn vị: phần trăm (%) · Nhấp vào một điểm bất kỳ để cập nhật biểu đồ giải thích phía trên"
                option={mlDistressProbOption}
                height={190}
                isEmpty={sortedMl.length === 0}
                onChartClick={handleRiskPeriodClick}
                tableHeaders={["Kỳ", "1Y PD", "Bậc", "Vị thế PIT", "Altman Z″"]}
                tableRows={sortedMl.map((m) => {
                  const rowTrio = formatCreditTrio({
                    risk_tier: m.risk_tier,
                    risk_market_percentile: m.risk_market_percentile,
                    calibrated_distress_prob: m.calibrated_distress_prob,
                    relative_risk_lift: m.relative_risk_lift,
                  });
                  return [
                    formatPeriodLabel(m.period_end_date, periodType),
                    rowTrio.liftLabel ? `${rowTrio.pdLabel} (${rowTrio.liftLabel})` : rowTrio.pdLabel,
                    rowTrio.tierMeta.badgeText,
                    rowTrio.topPercentLabel,
                    formatNumber(m.altman_z_double_prime, 2),
                  ];
                })}
                headerRight={
                  isCustomMlPeriod ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      onClick={() => setSelectedMlPeriodEnd(null)}
                    >
                      <RotateCcw className="size-3" />
                      <span>Về kỳ mới nhất</span>
                    </Button>
                  ) : null
                }
              />
            </div>

            <div className="space-y-4 lg:col-span-5">
              <Card>
                <CardHeader className="py-2.5">
                  <CardTitle className="flex items-center gap-1.5 text-[14px]">
                    <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
                    <span>Cờ đỏ kế toán đang kích hoạt ({activeRedFlags.length}/9)</span>
                  </CardTitle>
                  <button
                    type="button"
                    onClick={() => setTabAndParams("quality")}
                    className="text-xs font-medium text-accent-ink hover:underline"
                  >
                    Xem 9 quy tắc →
                  </button>
                </CardHeader>
                <CardContent className="p-3.5">
                  {activeRedFlags.length === 0 ? (
                    <div className="flex items-center gap-2 rounded border border-risk-low/30 bg-risk-low-soft/40 p-3 text-xs text-risk-low">
                      <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
                      <span>Không ghi nhận cờ đỏ bất thường kế toán trong kỳ này.</span>
                    </div>
                  ) : (
                    <ul className="space-y-2">
                      {activeRedFlags.slice(0, 4).map((rf) => {
                        const sev = SEVERITY_META[rf.severity] ?? SEVERITY_META.MEDIUM;
                        const shortCode = rf.flag_code.split("_").slice(0, 2).join("_");
                        const presetQ = getRedFlagPresetQuestion(shortCode);
                        return (
                          <li
                            key={`${rf.flag_code}-${rf.period_end_date}`}
                            className="rounded border border-border bg-canvas p-2.5 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-semibold text-ink">
                                {rf.flag_title_vn ?? "Dấu hiệu bất thường kế toán"}
                              </span>
                              <RiskBadge tone={sev.tone} label={sev.shortLabel} />
                            </div>
                            {rf.explanation_text_vn ? (
                              <p className="mt-1 text-[11px] leading-4 text-ink-muted">
                                {rf.explanation_text_vn}
                              </p>
                            ) : null}
                            {presetQ ? (
                              <div className="mt-2">
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="xs"
                                  onClick={() =>
                                    handleInvestigateRedFlag(shortCode, presetQ)
                                  }
                                >
                                  <FileSearch className="size-3" aria-hidden="true" />
                                  <span>Kiểm tra Thuyết minh gốc</span>
                                </Button>
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="py-2.5">
                  <CardTitle className="text-[14px]">
                    Kiểm toán &amp; Hồ sơ công bố gần nhất
                  </CardTitle>
                  <button
                    type="button"
                    onClick={() => setTabAndParams("quality")}
                    className="text-xs font-medium text-accent-ink hover:underline"
                  >
                    Lịch sử hồ sơ →
                  </button>
                </CardHeader>
                <CardContent className="p-3.5 text-xs">
                  {filingsQuery.isLoading ? (
                    <p className="text-ink-muted">Đang tải thông tin kiểm toán...</p>
                  ) : !latestFiling ? (
                    <p className="text-ink-muted">
                      Chưa ghi nhận chi tiết hồ sơ cho phạm vi báo cáo này.
                    </p>
                  ) : (
                    <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
                      <div>
                        <dt className="text-ink-muted">Kỳ hồ sơ</dt>
                        <dd className="font-medium text-ink">
                          {latestFiling.period_end_date
                            ? formatDateVi(latestFiling.period_end_date)
                            : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-ink-muted">Trạng thái kiểm toán</dt>
                        <dd className="font-medium text-ink">
                          {(latestFiling.audit_status &&
                            AUDIT_STATUS_LABELS[latestFiling.audit_status]) ??
                            latestFiling.audit_status ??
                            "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-ink-muted">Ý kiến kiểm toán</dt>
                        <dd className="font-medium text-ink">
                          {(latestFiling.audit_opinion &&
                            AUDIT_OPINION_LABELS[latestFiling.audit_opinion]) ??
                            latestFiling.audit_opinion ??
                            "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-ink-muted">Phiên bản &amp; Hồi tố</dt>
                        <dd className="font-medium text-ink">
                          {(latestFiling.revision_reason &&
                            REVISION_REASON_LABELS[latestFiling.revision_reason]) ??
                            latestFiling.revision_reason ??
                            "Bản gốc"}
                          {latestFiling.has_restatement ? " · Có điều chỉnh" : ""}
                        </dd>
                      </div>
                    </dl>
                  )}
                </CardContent>
              </Card>
            </div>
          </section>

          {/* Tier 2b: Core Financial Visual Storytelling (Growth + Capital Structure) */}
          <section
            aria-label="Diễn biến tăng trưởng và cơ cấu vốn"
            className="grid grid-cols-1 gap-4 xl:grid-cols-2"
          >
            <GrowthComboChart
              ticker={profile.ticker}
              points={
                growthProfileQuery.data?.growth_series ?? profile.growth_series
              }
              globalPeriodType={periodType}
              localPeriodView={effectiveGrowthView}
              onChangeLocalPeriodView={setGrowthLocalView}
              isLoading={
                growthProfileQuery.isLoading && !growthProfileQuery.data
              }
            />
            <CapitalStructureChart
              ticker={profile.ticker}
              points={profile.capital_structure_series}
              periodType={periodType}
            />
          </section>

          {/* Tier 3: Stockopedia-style Traffic Light Peer Benchmark Table */}
          <Card>
            <CardHeader className="flex-wrap gap-2 py-2.5">
              <div>
                <CardTitle className="text-[14px]">
                  Bảng tỷ số &amp; Phân vị so với nhóm ngành
                </CardTitle>
                <p className="mt-0.5 text-xs text-ink-muted">
                  Nhấp tên chỉ số để xem lịch sử dải P25–P75 · Nhấp Truy nguồn để kiểm chứng tử số/mẫu số và hồ sơ gốc
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setShowAll38(false)}
                    className={cn(
                      "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                      !showAll38
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    12 chỉ số cốt lõi
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowAll38(true)}
                    className={cn(
                      "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                      showAll38
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    Tất cả 38 chỉ số
                  </button>
                </div>

                <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setTrafficMode("VALUE")}
                    className={cn(
                      "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                      trafficMode === "VALUE"
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    Giá trị tuyệt đối
                  </button>
                  <button
                    type="button"
                    onClick={() => setTrafficMode("PERCENTILE")}
                    className={cn(
                      "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                      trafficMode === "PERCENTILE"
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    Chỉ xem phân vị
                  </button>
                </div>
              </div>
            </CardHeader>

            <CardContent className="p-0">
              {summaryQuery.isLoading ? (
                <p className="p-4 text-xs text-ink-muted">Đang tải bảng tỷ số ngành...</p>
              ) : summaryQuery.isError ? (
                <div className="p-4">
                  <ErrorState
                    message={summaryQuery.error.message}
                    onRetry={() => summaryQuery.refetch()}
                  />
                </div>
              ) : (summaryQuery.data?.items.length ?? 0) === 0 ? (
                <div className="p-4">
                  <EmptyState
                    title="Chưa có dữ liệu so sánh ngành cho kỳ này"
                    description="Thử chuyển phạm vi báo cáo hoặc loại kỳ trên thanh công cụ phía trên."
                  />
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Chỉ số
                        </th>
                        {trafficMode === "VALUE" ? (
                          <>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              Giá trị DN
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              Trung vị ngành
                            </th>
                          </>
                        ) : (
                          <th scope="col" className="px-3 py-2 text-left font-semibold">
                            Nhóm so sánh
                          </th>
                        )}
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Phân vị ngành
                        </th>
                        <th scope="col" className="px-3 py-2 text-center font-semibold">
                          Xu hướng 8 kỳ
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          Nguồn
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {(summaryQuery.data?.items ?? []).map((item) => {
                        const dict = ratioDictMap.get(item.metric_name);
                        const peerInfo = formatPeerGroupLabel(
                          item.peer_group_level,
                          item.peer_count,
                        );
                        return (
                          <tr
                            key={item.metric_name}
                            className="border-b border-border/60 hover:bg-surface-muted/40"
                          >
                            <td className="px-3 py-2">
                              <div className="flex items-center gap-1.5">
                                <button
                                  type="button"
                                  onClick={() =>
                                    setTabAndParams("ratios", {
                                      metric: item.metric_name,
                                    })
                                  }
                                  className="text-left font-medium text-accent-ink hover:underline"
                                >
                                  {item.display_name_vn}
                                </button>
                                {dict ? (
                                  <InfoTooltip
                                    text={`${dict.description_vn} (Công thức: ${dict.formula_display})`}
                                    label={`Giải thích ${item.display_name_vn}`}
                                  />
                                ) : null}
                              </div>
                            </td>

                            {trafficMode === "VALUE" ? (
                              <>
                                <td className="num px-3 py-2 text-right font-semibold text-ink">
                                  {formatRatioValue(
                                    item.company_value,
                                    item.format_type,
                                  )}
                                </td>
                                <td className="num px-3 py-2 text-right text-ink-muted">
                                  {formatRatioValue(item.peer_median, item.format_type)}
                                </td>
                              </>
                            ) : (
                              <td className="px-3 py-2 text-ink-muted">
                                {peerInfo.text}
                              </td>
                            )}

                            <td className="px-3 py-2">
                              <PercentileBar
                                percentile={item.percentile_rank}
                                direction={item.direction}
                                peerGroupLevel={item.peer_group_level}
                                peerCount={item.peer_count}
                              />
                            </td>

                            <td className="px-3 py-2 text-center">
                              <SparklineSvg values={item.sparkline ?? []} />
                            </td>

                            <td className="px-3 py-2 text-right">
                              <Button
                                type="button"
                                variant="ghost"
                                size="xs"
                                onClick={() =>
                                  openSourceDrawer({
                                    ticker: profile.ticker,
                                    ratioName: item.metric_name,
                                    statementScope,
                                    periodType,
                                    periodEndDate:
                                      summaryQuery.data?.period_end_date ??
                                      periodEndDate,
                                  })
                                }
                              >
                                <Link2 className="size-3" />
                                Truy nguồn
                              </Button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : activeTab === "statements" ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="inline-flex rounded-lg border border-border bg-surface p-0.5 text-xs">
              {(
                [
                  ["INCOME_STATEMENT", "Kết quả kinh doanh"],
                  ["BALANCE_SHEET", "Cân đối kế toán"],
                  ["CASH_FLOW", "Lưu chuyển tiền tệ"],
                ] as const
              ).map(([st, label]) => (
                <button
                  key={st}
                  type="button"
                  onClick={() => setStatementType(st)}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 font-medium transition-colors duration-150",
                    statementType === st
                      ? "bg-primary text-primary-foreground shadow-xs"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {periodType === "QUARTER" ? (
                <span className="text-[11px] text-ink-muted">
                  Hiển thị số liệu quý rời rạc (theo từng kỳ công bố)
                </span>
              ) : null}
              <Select
                aria-label="Số kỳ báo cáo hiển thị"
                value={String(periodsLimit)}
                onChange={(e) => setPeriodsLimit(Number(e.target.value) || 8)}
                className="h-8 text-xs"
              >
                <option value="8">8 kỳ gần nhất</option>
                <option value="12">12 kỳ gần nhất</option>
              </Select>
            </div>
          </div>

          {statementsQuery.isLoading ? (
            <PageSkeleton />
          ) : statementsQuery.isError ? (
            <ErrorState
              message={statementsQuery.error.message}
              onRetry={() => statementsQuery.refetch()}
            />
          ) : !statementsQuery.data || statementsQuery.data.rows.length === 0 ? (
            <EmptyState
              title="Chưa có dữ liệu báo cáo tài chính"
              description="Không tìm thấy bảng báo cáo cho loại báo cáo và phạm vi đã chọn."
            />
          ) : (
            <FinancialTable
              statementType={statementsQuery.data.statement_type}
              periodType={statementsQuery.data.period_type}
              periods={statementsQuery.data.periods}
              rows={statementsQuery.data.rows}
              compact={density === "compact"}
            />
          )}
        </div>
      ) : activeTab === "ratios" ? (
        <div className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <label
                  htmlFor="ratio-select"
                  className="text-xs font-semibold text-ink"
                >
                  Chọn tỷ số phân tích:
                </label>
                <Select
                  id="ratio-select"
                  value={metricParam}
                  onChange={(e) =>
                    setTabAndParams("ratios", { metric: e.target.value })
                  }
                  className="min-w-[260px] text-xs"
                >
                  {Object.entries(RATIO_CATEGORY_LABELS).map(([catKey, catLbl]) => {
                    const groupItems = (dictQuery.data?.ratios ?? []).filter(
                      (r) => r.category === catKey,
                    );
                    if (groupItems.length === 0) return null;
                    return (
                      <optgroup key={catKey} label={catLbl}>
                        {groupItems.map((r) => (
                          <option key={r.ratio_name} value={r.ratio_name}>
                            {r.display_name_vn}
                          </option>
                        ))}
                      </optgroup>
                    );
                  })}
                </Select>
              </div>
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() =>
                  openSourceDrawer({
                    ticker: profile.ticker,
                    ratioName: metricParam,
                    statementScope,
                    periodType,
                    periodEndDate:
                      benchmarksQuery.data?.period_end_date ?? periodEndDate,
                  })
                }
              >
                <Link2 className="size-3" />
                Truy nguồn chỉ số này
              </Button>
            </CardContent>
          </Card>

          {benchmarksQuery.isLoading ? (
            <PageSkeleton />
          ) : benchmarksQuery.isError ? (
            <ErrorState
              message={benchmarksQuery.error.message}
              onRetry={() => benchmarksQuery.refetch()}
            />
          ) : benchmarksQuery.data ? (
            <>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <HealthRadarChart
                  ticker={profile.ticker}
                  items={summaryQuery.data?.items ?? []}
                  isLoading={summaryQuery.isLoading}
                />
                <SectorBoxPlotChart
                  ticker={profile.ticker}
                  metricLabel={benchmarkMeta?.display_name_vn ?? metricParam}
                  formatType={benchmarkMeta?.format_type ?? "NUMBER"}
                  companyValue={benchmarksQuery.data.company_value}
                  peerP25={benchmarksQuery.data.peer_p25}
                  peerMedian={benchmarksQuery.data.peer_median}
                  peerP75={benchmarksQuery.data.peer_p75}
                  peerRanking={benchmarksQuery.data.peer_ranking}
                  peerGroupLevel={benchmarksQuery.data.peer_group_level}
                  peerCount={benchmarksQuery.data.peer_count}
                />
              </div>

              <PeerBandChart
                metricLabel={benchmarkMeta?.display_name_vn ?? metricParam}
                formatType={benchmarkMeta?.format_type ?? "NUMBER"}
                periodType={benchmarksQuery.data.period_type}
                history={benchmarksQuery.data.history}
                ticker={profile.ticker}
              />

              <Card>
                <CardHeader className="flex-wrap gap-2 py-2.5">
                  <div>
                    <CardTitle className="text-[14px]">
                      Bảng tỷ số phân nhóm theo 5 trụ cột nghiệp vụ
                    </CardTitle>
                    <p className="mt-0.5 text-xs text-ink-muted">
                      Nhấp tên chỉ số để cập nhật biểu đồ dải P25–P75 và Box Plot phía trên · Sắp xếp theo từng cột
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={ratioSearch}
                      onChange={(e) => setRatioSearch(e.target.value)}
                      placeholder="Tìm chỉ số..."
                      aria-label="Tìm kiếm chỉ số tài chính"
                      className="h-7 w-44 text-xs"
                    />
                    <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
                      <button
                        type="button"
                        onClick={() => setRatiosShowAll38(false)}
                        className={cn(
                          "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                          !ratiosShowAll38
                            ? "bg-primary text-primary-foreground shadow-xs"
                            : "text-ink-muted hover:text-ink",
                        )}
                      >
                        12 cốt lõi
                      </button>
                      <button
                        type="button"
                        onClick={() => setRatiosShowAll38(true)}
                        className={cn(
                          "rounded-md px-2 py-1 font-medium transition-colors duration-150",
                          ratiosShowAll38
                            ? "bg-primary text-primary-foreground shadow-xs"
                            : "text-ink-muted hover:text-ink",
                        )}
                      >
                        Tất cả 38
                      </button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3 p-3.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setRatioGroupFilter("ALL")}
                      className={cn(
                        "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-150",
                        ratioGroupFilter === "ALL"
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-surface text-ink-muted hover:text-ink",
                      )}
                    >
                      Tất cả 5 nhóm
                    </button>
                    {HEALTH_PILLAR_DEFS.map((grp) => (
                      <button
                        key={grp.id}
                        type="button"
                        onClick={() => setRatioGroupFilter(grp.id)}
                        className={cn(
                          "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-150",
                          ratioGroupFilter === grp.id
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border bg-surface text-ink-muted hover:text-ink",
                        )}
                      >
                        {grp.shortLabel}
                      </button>
                    ))}
                  </div>

                  {summaryQuery.isLoading ? (
                    <p className="text-xs text-ink-muted">Đang tải bảng tỷ số theo nhóm...</p>
                  ) : groupedRatioBuckets.length === 0 ? (
                    <p className="text-xs text-ink-muted">
                      Không tìm thấy chỉ số phù hợp với bộ lọc hiện tại.
                    </p>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              <button
                                type="button"
                                onClick={() => toggleRatioSort("name")}
                                className="inline-flex items-center gap-1 hover:text-ink"
                              >
                                <span>Chỉ số</span>
                                {ratioSortBy === "name" ? (
                                  ratioSortDir === "asc" ? (
                                    <ArrowUp className="size-3" />
                                  ) : (
                                    <ArrowDown className="size-3" />
                                  )
                                ) : (
                                  <ArrowUpDown className="size-3 opacity-60" />
                                )}
                              </button>
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              <button
                                type="button"
                                onClick={() => toggleRatioSort("company_value")}
                                className="inline-flex items-center justify-end gap-1 hover:text-ink"
                              >
                                <span>Giá trị DN</span>
                                {ratioSortBy === "company_value" ? (
                                  ratioSortDir === "asc" ? (
                                    <ArrowUp className="size-3" />
                                  ) : (
                                    <ArrowDown className="size-3" />
                                  )
                                ) : (
                                  <ArrowUpDown className="size-3 opacity-60" />
                                )}
                              </button>
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              <button
                                type="button"
                                onClick={() => toggleRatioSort("peer_median")}
                                className="inline-flex items-center justify-end gap-1 hover:text-ink"
                              >
                                <span>Trung vị ngành</span>
                                {ratioSortBy === "peer_median" ? (
                                  ratioSortDir === "asc" ? (
                                    <ArrowUp className="size-3" />
                                  ) : (
                                    <ArrowDown className="size-3" />
                                  )
                                ) : (
                                  <ArrowUpDown className="size-3 opacity-60" />
                                )}
                              </button>
                            </th>
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              <button
                                type="button"
                                onClick={() => toggleRatioSort("percentile")}
                                className="inline-flex items-center gap-1 hover:text-ink"
                              >
                                <span>Phân vị ngành</span>
                                {ratioSortBy === "percentile" ? (
                                  ratioSortDir === "asc" ? (
                                    <ArrowUp className="size-3" />
                                  ) : (
                                    <ArrowDown className="size-3" />
                                  )
                                ) : (
                                  <ArrowUpDown className="size-3 opacity-60" />
                                )}
                              </button>
                            </th>
                            <th scope="col" className="px-3 py-2 text-center font-semibold">
                              Xu hướng
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              Nguồn
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {groupedRatioBuckets.map((bucket) => (
                            <React.Fragment key={bucket.id}>
                              <tr className="border-b border-border bg-surface-muted/60">
                                <th
                                  colSpan={6}
                                  scope="colgroup"
                                  className="px-3 py-1.5 text-left text-[11px] font-semibold text-ink"
                                >
                                  {bucket.label} ({bucket.items.length})
                                </th>
                              </tr>
                              {bucket.items.map((item) => {
                                const isActiveMetric = item.metric_name === metricParam;
                                return (
                                  <tr
                                    key={item.metric_name}
                                    className={cn(
                                      "border-b border-border/60 hover:bg-surface-muted/40",
                                      isActiveMetric && "bg-accent-soft/45",
                                    )}
                                  >
                                    <td className="px-3 py-2">
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setTabAndParams("ratios", {
                                            metric: item.metric_name,
                                          })
                                        }
                                        className={cn(
                                          "text-left font-medium hover:underline",
                                          isActiveMetric
                                            ? "font-semibold text-accent-ink"
                                            : "text-ink",
                                        )}
                                      >
                                        {item.display_name_vn}
                                      </button>
                                    </td>
                                    <td className="num px-3 py-2 text-right font-semibold text-ink">
                                      {formatRatioValue(
                                        item.company_value,
                                        item.format_type,
                                      )}
                                    </td>
                                    <td className="num px-3 py-2 text-right text-ink-muted">
                                      {formatRatioValue(
                                        item.peer_median,
                                        item.format_type,
                                      )}
                                    </td>
                                    <td className="px-3 py-2">
                                      <PercentileBar
                                        percentile={item.percentile_rank}
                                        direction={item.direction}
                                        peerGroupLevel={item.peer_group_level}
                                        peerCount={item.peer_count}
                                      />
                                    </td>
                                    <td className="px-3 py-2 text-center">
                                      <SparklineSvg values={item.sparkline ?? []} />
                                    </td>
                                    <td className="px-3 py-2 text-right">
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="xs"
                                        onClick={() =>
                                          openSourceDrawer({
                                            ticker: profile.ticker,
                                            ratioName: item.metric_name,
                                            statementScope,
                                            periodType,
                                            periodEndDate:
                                              summaryQuery.data?.period_end_date ??
                                              periodEndDate,
                                          })
                                        }
                                      >
                                        <Link2 className="size-3" />
                                        Truy nguồn
                                      </Button>
                                    </td>
                                  </tr>
                                );
                              })}
                            </React.Fragment>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="py-2.5">
                  <div>
                    <CardTitle className="text-[14px]">
                      Xếp hạng trong nhóm so sánh ({benchmarksQuery.data.peer_ranking.length} DN)
                    </CardTitle>
                    <p className="mt-0.5 text-xs text-ink-muted">
                      {
                        formatPeerGroupLabel(
                          benchmarksQuery.data.peer_group_level,
                          benchmarksQuery.data.peer_count,
                        ).text
                      }
                    </p>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div
                    tabIndex={0}
                    role="region"
                    aria-label="Bảng xếp hạng trong nhóm so sánh"
                    className="max-h-[360px] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                          <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                            Mã
                          </th>
                          <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                            Doanh nghiệp
                          </th>
                          <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                            Giá trị
                          </th>
                          <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                            Phân vị
                          </th>
                          <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                            Z-Score chuẩn hóa
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {benchmarksQuery.data.peer_ranking.map((peer, idx) => {
                          const isSelf =
                            peer.ticker?.toUpperCase() === profile.ticker;
                          return (
                            <tr
                              key={`${peer.ticker ?? "peer"}-${idx}`}
                              className={cn(
                                "border-b border-border/60 hover:bg-surface-muted/40",
                                isSelf && "bg-accent-soft/50 font-semibold",
                              )}
                            >
                              <td className="px-3 py-2">
                                {peer.ticker ? (
                                  <Link
                                    href={`/companies/${peer.ticker}`}
                                    className="font-mono text-accent-ink hover:underline"
                                  >
                                    {peer.ticker}
                                  </Link>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td className="max-w-[240px] truncate px-3 py-2 text-ink">
                                {peer.company_name_vn ?? "—"}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {formatRatioValue(
                                  peer.value,
                                  benchmarkMeta?.format_type ?? "NUMBER",
                                )}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {peer.percentile_rank !== null &&
                                peer.percentile_rank !== undefined
                                  ? `${formatNumber(peer.percentile_rank, 0)}%`
                                  : "N/A"}
                              </td>
                              <td className="num px-3 py-2 text-right text-ink-muted">
                                {formatNumber(peer.robust_z_score, 2)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </>
          ) : null}

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Phân tách DuPont 5 nhân tố (ROE = Gánh nặng thuế × Gánh nặng lãi vay × Biên EBIT × Vòng quay TS × Đòn bẩy)
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {profile.dupont_series.length === 0 ? (
                <p className="p-4 text-xs text-ink-muted">
                  Chưa có chuỗi phân tách DuPont cho phạm vi này.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Gánh nặng thuế</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Gánh nặng lãi vay</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Biên EBIT</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Vòng quay TS</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Hệ số đòn bẩy</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">ROE tổng</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...profile.dupont_series]
                        .sort((a, b) =>
                          b.period_end_date.localeCompare(a.period_end_date),
                        )
                        .slice(0, 12)
                        .map((dp) => (
                          <tr
                            key={dp.period_end_date}
                            className="border-b border-border/60 hover:bg-surface-muted/40"
                          >
                            <td className="px-3 py-2 font-medium text-ink">
                              {formatPeriodLabel(dp.period_end_date, periodType)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(dp.tax_burden)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(dp.interest_burden)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(dp.ebit_margin)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatMultiple(dp.asset_turnover)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatMultiple(dp.equity_multiplier_total)}
                            </td>
                            <td className="num px-3 py-2 text-right font-semibold text-ink">
                              {formatPercent(dp.roe_total)}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : activeTab === "quality" ? (
        <div className="space-y-5">
          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Lợi nhuận hoạt động cốt lõi (Core Earnings · Rouen, So &amp; Wang 2021)
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {profile.core_earnings_series.length === 0 ? (
                <p className="p-4 text-xs text-ink-muted">
                  Chưa có chuỗi lợi nhuận cốt lõi cho phạm vi/kỳ này.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">LN HĐ cốt lõi TTM (tỷ)</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Biên LN cốt lõi</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">LN cốt lõi / EBT</th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">Biến dạng phi lõi / Tổng TS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...profile.core_earnings_series]
                        .sort((a, b) =>
                          b.period_end_date.localeCompare(a.period_end_date),
                        )
                        .slice(0, 10)
                        .map((ce) => (
                          <tr
                            key={ce.period_end_date}
                            className="border-b border-border/60 hover:bg-surface-muted/40"
                          >
                            <td className="px-3 py-2 font-medium text-ink">
                              {formatPeriodLabel(ce.period_end_date, periodType)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatVndBillions(ce.core_operating_profit_ttm, {
                                accountingNegative: true,
                              })}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(ce.core_operating_margin)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(ce.core_to_ebt_ratio)}
                            </td>
                            <td className="num px-3 py-2 text-right">
                              {formatPercent(ce.non_core_distortion_to_assets)}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <RedFlagChecklist
            dictionary={dictQuery.data?.red_flags ?? []}
            triggeredFlags={profile.red_flags}
            latestPeriodEndDate={activePeriodEnd}
            onInvestigateRedFlag={handleInvestigateRedFlag}
          />

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Lịch sử hồ sơ công bố &amp; ý kiến kiểm toán ({filingsQuery.data?.items.length ?? 0})
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {filingsQuery.isLoading ? (
                <p className="p-4 text-xs text-ink-muted">Đang tải lịch sử hồ sơ...</p>
              ) : (filingsQuery.data?.items.length ?? 0) === 0 ? (
                <p className="p-4 text-xs text-ink-muted">Chưa có hồ sơ ghi nhận.</p>
              ) : (
                <div
                  tabIndex={0}
                  role="region"
                  aria-label="Bảng lịch sử hồ sơ công bố và ý kiến kiểm toán"
                  className="max-h-[360px] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Kỳ</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Phạm vi</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Chuẩn</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Kiểm toán</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Ý kiến</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Phiên bản</th>
                        <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">Công bố</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filingsQuery.data?.items.map((f) => (
                        <tr
                          key={`${f.filing_id}-${f.statement_type ?? ""}`}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-3 py-2 font-medium text-ink">
                            {f.period_end_date ? formatDateVi(f.period_end_date) : "—"}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {(f.statement_scope &&
                              STATEMENT_SCOPE_LABELS[f.statement_scope]) ??
                              f.statement_scope ??
                              "—"}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {(f.taxonomy_version &&
                              TAXONOMY_LABELS[f.taxonomy_version]) ??
                              f.taxonomy_version ??
                              "—"}
                          </td>
                          <td className="px-3 py-2">
                            {(f.audit_status &&
                              AUDIT_STATUS_LABELS[f.audit_status]) ??
                              f.audit_status ??
                              "—"}
                          </td>
                          <td className="px-3 py-2">
                            {(f.audit_opinion &&
                              AUDIT_OPINION_LABELS[f.audit_opinion]) ??
                              f.audit_opinion ??
                              "—"}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {(f.revision_reason &&
                              REVISION_REASON_LABELS[f.revision_reason]) ??
                              f.revision_reason ??
                              "—"}
                            {f.has_restatement ? " · Hồi tố" : ""}
                          </td>
                          <td className="num px-3 py-2 text-right text-ink-muted">
                            {f.published_at ? formatDateVi(f.published_at) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : activeTab === "model" ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <EChartBox
              title="Diễn biến lịch sử điểm Altman Z″ (EM) — Nhấp kỳ để cập nhật TreeSHAP"
              subtitle="Đơn vị: điểm Z″ · Ngưỡng < 1,10 Nguy cơ · > 2,60 An toàn"
              option={mlAltmanOption}
              height={290}
              isEmpty={sortedMl.length === 0}
              onChartClick={handleRiskPeriodClick}
              tableHeaders={["Kỳ", "Altman Z″", "F-Score", "Beneish M"]}
              tableRows={sortedMl.map((m) => [
                formatPeriodLabel(m.period_end_date, periodType),
                formatNumber(m.altman_z_double_prime, 2),
                m.piotroski_f_score !== null && m.piotroski_f_score !== undefined
                  ? `${m.piotroski_f_score}/9`
                  : "N/A",
                formatNumber(m.beneish_m8_score, 2),
              ])}
            />

            <EChartBox
              title="Diễn biến lịch sử Xác suất kiệt quệ mô hình — Nhấp kỳ để cập nhật TreeSHAP"
              subtitle="Đơn vị: phần trăm (%) · Xác suất kiệt quệ 1 năm đã hiệu chỉnh (1Y PD)"
              option={mlDistressProbOption}
              height={290}
              isEmpty={sortedMl.length === 0}
              onChartClick={handleRiskPeriodClick}
              tableHeaders={["Kỳ", "1Y PD", "Bậc", "F-Score", "Beneish M"]}
              tableRows={sortedMl.map((m) => [
                formatPeriodLabel(m.period_end_date, periodType),
                formatPercent(m.calibrated_distress_prob),
                m.risk_tier ?? "N/A",
                m.piotroski_f_score !== null && m.piotroski_f_score !== undefined
                  ? `${m.piotroski_f_score}/9`
                  : "N/A",
                formatNumber(m.beneish_m8_score, 2),
              ])}
            />
          </div>

          <ExplainWaterfallChart
            shap={activeMl?.shap_top_drivers}
            distressProb={activeMl?.calibrated_distress_prob}
            periodLabel={activeMlPeriodLabel}
            isCustomPeriod={isCustomMlPeriod}
            onResetToLatest={() => setSelectedMlPeriodEnd(null)}
          />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
            <Card className="lg:col-span-7">
              <CardHeader className="flex-wrap gap-2 py-2.5">
                <CardTitle className="text-[14px]">
                  Bảng chi tiết đóng góp TreeSHAP{" "}
                  {activeMlPeriodLabel ? `(${activeMlPeriodLabel})` : "(Kỳ đang chọn)"}
                </CardTitle>
                {isCustomMlPeriod ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    onClick={() => setSelectedMlPeriodEnd(null)}
                  >
                    <RotateCcw className="size-3" />
                    <span>Về kỳ mới nhất</span>
                  </Button>
                ) : null}
              </CardHeader>
              <CardContent className="p-0">
                {!activeMl?.shap_top_drivers ? (
                  <p className="p-4 text-xs text-ink-muted">
                    Chưa có dữ liệu đóng góp TreeSHAP cho kỳ đang chọn.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                          <th scope="col" className="px-3 py-2 text-left font-semibold">Yếu tố</th>
                          <th scope="col" className="px-3 py-2 text-right font-semibold">Giá trị DN</th>
                          <th scope="col" className="px-3 py-2 text-right font-semibold">Trung vị ngành</th>
                          <th scope="col" className="px-3 py-2 text-right font-semibold">Đóng góp (log-odds)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          ...activeMl.shap_top_drivers.top_risk_increasing,
                          ...activeMl.shap_top_drivers.top_risk_decreasing,
                        ].map((drv) => {
                          const ratioFallback = ratioDictMap.get(
                            drv.feature as RatioName,
                          )?.display_name_vn;
                          const driverLabel =
                            drv.display_name_vn &&
                            drv.display_name_vn !== drv.feature
                              ? drv.display_name_vn
                              : (ratioFallback ?? getMlFeatureLabel(drv.feature));
                          return (
                            <tr
                              key={drv.feature}
                              className="border-b border-border/60 hover:bg-surface-muted/40"
                            >
                              <td className="px-3 py-2 font-medium text-ink">
                                {driverLabel}
                              </td>
                            <td className="num px-3 py-2 text-right">
                              {formatNumber(drv.value, 3)}
                            </td>
                            <td className="num px-3 py-2 text-right text-ink-muted">
                              {formatNumber(drv.peer_median, 3)}
                            </td>
                              <td
                                className={cn(
                                  "num px-3 py-2 text-right font-semibold",
                                  drv.shap_delta >= 0 ? "text-risk-high" : "text-risk-low",
                                )}
                              >
                                {drv.shap_delta >= 0 ? "+" : MINUS_SIGN}
                                {formatNumber(Math.abs(drv.shap_delta), 3)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="lg:col-span-5">
              <CardHeader className="py-2.5">
                <CardTitle className="text-[14px]">
                  Phân cụm đặc trưng &amp; Quản trị mô hình
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 p-3.5 text-xs">
                <dl className="space-y-2">
                  <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                    <dt className="text-ink-muted">Nhóm cấu trúc tài chính</dt>
                    <dd className="text-right font-medium text-ink">
                      {activeMl?.cluster_archetype_name ?? "Chưa phân cụm"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                    <dt className="text-ink-muted">Phân vị bất thường thống kê</dt>
                    <dd className="num text-right font-medium text-ink">
                      {activeMl?.anomaly_percentile_rank !== null &&
                      activeMl?.anomaly_percentile_rank !== undefined
                        ? `${formatNumber(activeMl.anomaly_percentile_rank, 1)}%`
                        : "N/A"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                    <dt className="text-ink-muted">Phiên bản mô hình</dt>
                    <dd className="font-mono text-right text-ink">
                      {activeMl?.model_version ?? "—"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2 pb-1">
                    <dt className="text-ink-muted">Vai trò điểm số</dt>
                    <dd className="text-right text-ink">
                      {(activeMl?.model_role &&
                        MODEL_ROLE_LABELS[activeMl.model_role]) ??
                        "Đánh giá định lượng"}
                    </dd>
                  </div>
                </dl>
                <p className="rounded border border-border bg-canvas p-2.5 text-[11px] leading-4 text-ink-muted">
                  Phạm vi áp dụng: Mô hình đánh giá doanh nghiệp phi tài chính niêm yết (không dùng so sánh trực tiếp ngân hàng/bảo hiểm).
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      ) : activeTab === "documents" ? (
        <DocumentInvestigationPanel
          key={`${profile.ticker}-${ragPreset?.requestNonce ?? 0}`}
          ticker={profile.ticker}
          companyName={
            profile.company_name_vn ?? issuerMeta?.company_name_vn ?? null
          }
          initialPreset={ragPreset}
        />
      ) : (
        /* TAB 7: LINEAGE REGISTRY */
        <Card>
          <CardHeader className="flex-wrap gap-2 py-2.5">
            <div>
              <CardTitle className="text-[14px]">
                Truy xuất nguồn gốc 38 tỷ số tài chính
              </CardTitle>
              <p className="mt-0.5 text-xs text-ink-muted">
                Nhấn Truy nguồn để xem tử số, mẫu số, mã hồ sơ gốc và hàm băm kiểm chứng dữ liệu
              </p>
            </div>
            <Input
              value={lineageFilter}
              onChange={(e) => setLineageFilter(e.target.value)}
              placeholder="Lọc theo tên chỉ số..."
              className="h-7 w-56 text-xs"
              aria-label="Lọc danh sách tỷ số để truy nguồn"
            />
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="px-3 py-2 text-left font-semibold">Chỉ số</th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold">Nhóm</th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold">Công thức</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Giá trị kỳ mới nhất</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Kiểm chứng</th>
                  </tr>
                </thead>
                <tbody>
                  {(dictQuery.data?.ratios ?? [])
                    .filter((r) => {
                      const q = normalizeSearchText(lineageFilter);
                      if (!q) return true;
                      return (
                        normalizeSearchText(r.display_name_vn).includes(q) ||
                        normalizeSearchText(r.ratio_name).includes(q)
                      );
                    })
                    .map((r) => {
                      const raw = latestRatioRow?.[r.ratio_name];
                      const val =
                        typeof raw === "number" && Number.isFinite(raw) ? raw : null;
                      return (
                        <tr
                          key={r.ratio_name}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-3 py-2 font-medium text-ink">
                            {r.display_name_vn}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {RATIO_CATEGORY_LABELS[r.category] ?? r.category}
                          </td>
                          <td className="px-3 py-2 text-xs text-ink-muted">
                            {formatFormulaVi(r.formula_display)}
                          </td>
                          <td className="num px-3 py-2 text-right font-medium text-ink">
                            {formatRatioValue(val, r.format_type)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              type="button"
                              variant="outline"
                              size="xs"
                              onClick={() =>
                                openSourceDrawer({
                                  ticker: profile.ticker,
                                  ratioName: r.ratio_name,
                                  statementScope,
                                  periodType,
                                  periodEndDate:
                                    (latestRatioRow?.period_end_date as string | undefined) ??
                                    periodEndDate,
                                })
                              }
                            >
                              <Link2 className="size-3" />
                              Truy nguồn
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
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
