"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Download, Plus, X } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { useAllIssuers, useCompareCompanies } from "@/hooks/use-finintel-queries";
import {
  formatCreditTrio,
  getRiskTierMeta,
  PERIOD_TYPE_LABELS,
  selectMlScoreForPeriod,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import {
  buildCsvContent,
  formatNumber,
  formatPercent,
  formatPeriodLabel,
  formatRatioValue,
  normalizeSearchText,
  type RatioDirection,
} from "@/lib/format";
import type { RatioName } from "@/types/api";

type CompareMetricKey =
  | "altman_z_double_prime"
  | "calibrated_distress_prob"
  | "roe_total"
  | "ebit_margin"
  | "debt_to_equity"
  | "interest_coverage"
  | "current_ratio"
  | "ocf_to_net_income";

const CHART_METRICS: {
  key: CompareMetricKey;
  label: string;
  isMl: boolean;
  isPct: boolean;
  decimals: number;
  unitSuffix?: string;
}[] = [
  { key: "altman_z_double_prime", label: "Altman Z″", isMl: true, isPct: false, decimals: 2 },
  {
    key: "calibrated_distress_prob",
    label: "Xác suất kiệt quệ (%)",
    isMl: true,
    isPct: true,
    decimals: 1,
  },
  { key: "roe_total", label: "ROE (%)", isMl: false, isPct: true, decimals: 1 },
  { key: "ebit_margin", label: "Biên EBIT (%)", isMl: false, isPct: true, decimals: 1 },
  {
    key: "debt_to_equity",
    label: "Nợ vay / VCSH (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "interest_coverage",
    label: "Khả năng trả lãi EBIT/Lãi vay (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "current_ratio",
    label: "Thanh toán hiện hành (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "ocf_to_net_income",
    label: "CFO / Lợi nhuận sau thuế (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
];

function CompareContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { statementScope, periodType, periodEndDate, snapshotId } = useAppState();
  const { palette } = useChartTheme();

  const tickers = React.useMemo(() => {
    const raw = searchParams.get("tickers") ?? "";
    const parsed = raw
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter((s) => /^[A-Z0-9]{1,10}$/.test(s));
    return Array.from(new Set(parsed)).slice(0, 5);
  }, [searchParams]);

  const [tickerInput, setTickerInput] = React.useState("");
  const [chartMetric, setChartMetric] =
    React.useState<CompareMetricKey>("calibrated_distress_prob");
  const [matrixMode, setMatrixMode] = React.useState<"VALUE" | "PERCENTILE">("VALUE");

  const issuersQuery = useAllIssuers(snapshotId);
  const { profiles, summaries } = useCompareCompanies(
    tickers,
    statementScope,
    periodType,
    periodEndDate,
    snapshotId,
  );

  const setTickersUrl = React.useCallback(
    (next: readonly string[]) => {
      const unique = Array.from(new Set(next)).slice(0, 5);
      if (unique.length === 0) {
        router.replace("/compare");
      } else {
        router.replace(`/compare?tickers=${unique.join(",")}`);
      }
    },
    [router],
  );

  const addTicker = (t: string) => {
    const clean = t.trim().toUpperCase();
    if (!/^[A-Z0-9]{1,10}$/.test(clean) || tickers.includes(clean)) return;
    if (tickers.length >= 5) return;
    setTickersUrl([...tickers, clean]);
    setTickerInput("");
  };

  const removeTicker = (t: string) => {
    setTickersUrl(tickers.filter((x) => x !== t));
  };

  const quickStartPairs = React.useMemo(() => {
    const available = new Set((issuersQuery.data ?? []).map((i) => i.ticker.toUpperCase()));
    const defaults = ["HPG", "FPT", "VNM", "MWG"];
    const valid = defaults.filter((d) => available.has(d));
    if (valid.length >= 2) return valid.slice(0, 3);
    return (issuersQuery.data ?? []).slice(0, 3).map((i) => i.ticker);
  }, [issuersQuery.data]);

  const issuerSuggestions = React.useMemo(() => {
    const q = normalizeSearchText(tickerInput);
    if (!q) return [];
    return (issuersQuery.data ?? [])
      .filter((iss) => {
        const tickerNorm = normalizeSearchText(iss.ticker);
        const nameNorm = normalizeSearchText(iss.company_name_vn ?? "");
        return !tickers.includes(iss.ticker) && (tickerNorm.includes(q) || nameNorm.includes(q));
      })
      .slice(0, 8);
  }, [issuersQuery.data, tickerInput, tickers]);

  const metricConfig =
    CHART_METRICS.find((m) => m.key === chartMetric) ?? CHART_METRICS[0];

  const chartData = React.useMemo(() => {
    const dateSet = new Set<string>();
    const perTickerMap = new Map<string, Map<string, number | null>>();

    tickers.forEach((ticker, idx) => {
      const profile = profiles[idx]?.data;
      const map = new Map<string, number | null>();
      if (profile) {
        if (metricConfig.isMl) {
          for (const row of profile.ml_scores) {
            dateSet.add(row.period_end_date);
            const rawVal =
              metricConfig.key === "altman_z_double_prime"
                ? row.altman_z_double_prime
                : row.calibrated_distress_prob;
            const scaled =
              rawVal !== null && rawVal !== undefined
                ? metricConfig.isPct
                  ? rawVal * 100
                  : rawVal
                : null;
            map.set(row.period_end_date, scaled ?? null);
          }
        } else {
          for (const row of profile.ratios_table) {
            const d = typeof row.period_end_date === "string" ? row.period_end_date : "";
            if (!d) continue;
            dateSet.add(d);
            const rawVal = row[metricConfig.key];
            const num =
              typeof rawVal === "number" && Number.isFinite(rawVal) ? rawVal : null;
            const scaled =
              num !== null ? (metricConfig.isPct ? num * 100 : num) : null;
            map.set(d, scaled);
          }
        }
      }
      perTickerMap.set(ticker, map);
    });

    const dates = Array.from(dateSet)
      .filter((d) => !periodEndDate || d <= periodEndDate)
      .sort()
      .slice(-12);
    return { dates, perTickerMap };
  }, [metricConfig, periodEndDate, profiles, tickers]);

  const chartOption = React.useMemo<EChartsOption>(() => {
    const xLabels = chartData.dates.map((d) => formatPeriodLabel(d, periodType));
    const suffix = metricConfig.unitSuffix ?? "";
    return {
      grid: { left: 54, right: 20, top: 36, bottom: 32 },
      legend: { top: 4, right: 8 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (val) => {
          if (typeof val !== "number" || !Number.isFinite(val)) return "N/A";
          if (metricConfig.isPct) {
            return `${formatChartNumber(val, 1)}%`;
          }
          return `${formatChartNumber(val, metricConfig.decimals)}${suffix}`;
        },
      },
      xAxis: {
        type: "category",
        data: xLabels,
      },
      yAxis: {
        type: "value",
        axisLabel: {
          formatter: (v: number) =>
            metricConfig.isPct
              ? `${formatChartNumber(v, 1, { trimTrailingZeros: true })}%`
              : `${formatChartNumber(v, metricConfig.decimals, { trimTrailingZeros: true })}${suffix}`,
        },
      },
      series: tickers.map((t, idx) => {
        const seriesColor = palette.series[idx % palette.series.length];
        return {
          name: t,
          type: "line",
          symbolSize: 5,
          lineStyle: { color: seriesColor, width: 2 },
          itemStyle: { color: seriesColor },
          data: chartData.dates.map((d) => chartData.perTickerMap.get(t)?.get(d) ?? null),
        };
      }),
    };
  }, [
    chartData,
    metricConfig.decimals,
    metricConfig.isPct,
    metricConfig.unitSuffix,
    palette.series,
    periodType,
    tickers,
  ]);

  const chartTableHeaders = ["Kỳ", ...tickers];
  const chartTableRows = chartData.dates.map((d) => [
    formatPeriodLabel(d, periodType),
    ...tickers.map((t) => {
      const v = chartData.perTickerMap.get(t)?.get(d);
      if (v === null || v === undefined) return "N/A";
      return metricConfig.isPct
        ? `${formatNumber(v, 1)}%`
        : `${formatNumber(v, metricConfig.decimals)}${metricConfig.unitSuffix ?? ""}`;
    }),
  ]);

  // Extract latest ML score + summary metrics for each ticker
  const matrixData = React.useMemo(() => {
    const ratioList = new Map<
      RatioName,
      {
        name: string;
        formatType: "PERCENT" | "MULTIPLE" | "DAYS" | "CURRENCY_PER_SHARE" | "NUMBER";
        direction: RatioDirection;
      }
    >();
    summaries.forEach((q) => {
      for (const item of q.data?.items ?? []) {
        if (!ratioList.has(item.metric_name)) {
          ratioList.set(item.metric_name, {
            name: item.display_name_vn,
            formatType: item.format_type,
            direction: item.direction,
          });
        }
      }
    });

    const latestMl = tickers.map((_, idx) =>
      selectMlScoreForPeriod(profiles[idx]?.data?.ml_scores, periodEndDate),
    );

    const latestFlagsCount = tickers.map((_, idx) => {
      const p = profiles[idx]?.data;
      if (!p) return 0;
      const targetDate = latestMl[idx]?.period_end_date ?? periodEndDate;
      return targetDate
        ? p.red_flags.filter((f) => f.period_end_date === targetDate).length
        : p.red_flags.length;
    });

    return { ratioList, latestMl, latestFlagsCount };
  }, [periodEndDate, profiles, summaries, tickers]);

  const handleExportCsv = () => {
    const headers = ["Chỉ tiêu", ...tickers];
    const rows: unknown[][] = [];
    rows.push([
      "Altman Z″",
      ...matrixData.latestMl.map((m) =>
        m?.altman_z_double_prime !== null && m?.altman_z_double_prime !== undefined
          ? formatNumber(m.altman_z_double_prime, 2)
          : "N/A",
      ),
    ]);
    rows.push([
      "Piotroski F-Score",
      ...matrixData.latestMl.map((m) =>
        m?.piotroski_f_score !== null && m?.piotroski_f_score !== undefined
          ? `${m.piotroski_f_score}/9`
          : "N/A",
      ),
    ]);
    rows.push([
      "Beneish M-Score",
      ...matrixData.latestMl.map((m) =>
        m?.beneish_m8_score !== null && m?.beneish_m8_score !== undefined
          ? formatNumber(m.beneish_m8_score, 2)
          : "N/A",
      ),
    ]);
    rows.push([
      "Bậc rủi ro (R1–R5)",
      ...matrixData.latestMl.map((m) => getRiskTierMeta(m?.risk_tier).shortLabel),
    ]);
    rows.push([
      "Vị thế rủi ro cùng kỳ",
      ...matrixData.latestMl.map(
        (m) =>
          formatCreditTrio({
            risk_tier: m?.risk_tier,
            risk_market_percentile: m?.risk_market_percentile,
            calibrated_distress_prob: m?.calibrated_distress_prob,
            relative_risk_lift: m?.relative_risk_lift,
            market_base_rate: m?.market_base_rate,
          }).topPercentLabel,
      ),
    ]);
    rows.push([
      "Xác suất kiệt quệ 1 năm",
      ...matrixData.latestMl.map((m) => formatPercent(m?.calibrated_distress_prob)),
    ]);
    rows.push([
      "Hệ số rủi ro so nền thị trường",
      ...matrixData.latestMl.map(
        (m) =>
          formatCreditTrio({
            relative_risk_lift: m?.relative_risk_lift,
          }).liftLabel ?? "N/A",
      ),
    ]);

    for (const [metricName, meta] of matrixData.ratioList.entries()) {
      rows.push([
        meta.name,
        ...tickers.map((_, idx) => {
          const item = summaries[idx]?.data?.items.find((x) => x.metric_name === metricName);
          if (!item) return "N/A";
          return matrixMode === "PERCENTILE"
            ? item.percentile_rank !== null && item.percentile_rank !== undefined
              ? `${formatNumber(item.percentile_rank, 0)}%`
              : "N/A"
            : formatRatioValue(item.company_value, item.format_type);
        }),
      ]);
    }

    const csv = buildCsvContent(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finintel-so-sanh-${tickers.join("-")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const anyLoading =
    tickers.length > 0 &&
    (profiles.some((p) => p.isLoading) || summaries.some((s) => s.isLoading));
  const firstError = profiles.find((p) => p.isError)?.error;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            So sánh doanh nghiệp ({tickers.length}/5)
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Phạm vi {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
            {PERIOD_TYPE_LABELS[periodType] ?? periodType}
            {periodEndDate ? ` · ${formatPeriodLabel(periodEndDate, periodType)}` : ""}
          </p>
        </div>
        {tickers.length > 0 ? (
          <Button variant="outline" size="sm" onClick={handleExportCsv}>
            <Download className="size-3.5" />
            Xuất CSV so sánh
          </Button>
        ) : null}
      </div>

      <Card className="rounded-lg shadow-xs">
        <CardContent className="space-y-3 p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            {tickers.map((t, idx) => (
              <span
                key={t}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-canvas px-2.5 py-1 text-xs font-medium text-ink"
              >
                <span
                  className="size-2 rounded-full"
                  style={{ backgroundColor: palette.series[idx % palette.series.length] }}
                />
                <Link href={`/companies/${t}`} className="font-mono font-semibold hover:underline">
                  {t}
                </Link>
                <button
                  type="button"
                  aria-label={`Xóa ${t} khỏi so sánh`}
                  onClick={() => removeTicker(t)}
                  className="text-ink-subtle hover:text-risk-high"
                >
                  <X className="size-3.5" />
                </button>
              </span>
            ))}

            {tickers.length < 5 ? (
              <div className="flex items-center gap-1.5">
                <Input
                  value={tickerInput}
                  onChange={(e) => setTickerInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && tickerInput.trim()) {
                      e.preventDefault();
                      addTicker(issuerSuggestions[0]?.ticker ?? tickerInput.trim());
                    }
                  }}
                  placeholder="Thêm mã so sánh (VD: FPT)"
                  className="h-7 w-48 text-xs"
                  aria-label="Nhập mã cổ phiếu để thêm vào bảng so sánh"
                />
                <Button
                  type="button"
                  size="xs"
                  disabled={!tickerInput.trim()}
                  onClick={() => addTicker(issuerSuggestions[0]?.ticker ?? tickerInput.trim())}
                >
                  <Plus className="size-3" />
                  Thêm
                </Button>
              </div>
            ) : null}
          </div>

          {issuerSuggestions.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {issuerSuggestions.map((iss) => (
                <button
                  key={iss.issuer_id}
                  type="button"
                  onClick={() => addTicker(iss.ticker)}
                  className="rounded border border-border bg-surface px-2 py-0.5 text-xs hover:border-accent-ink"
                >
                  <span className="font-mono font-semibold text-accent-ink">
                    {iss.ticker}
                  </span>{" "}
                  <span className="text-ink-muted">{iss.company_name_vn}</span>
                </button>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {tickers.length === 0 ? (
        <EmptyState
          title="Chọn 2 đến 5 doanh nghiệp để so sánh trực tiếp"
          description="Bạn có thể chọn từ bảng sàng lọc doanh nghiệp, danh sách theo dõi hoặc bắt đầu nhanh bằng nhóm gợi ý."
          action={
            quickStartPairs.length >= 2 ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setTickersUrl(quickStartPairs)}
              >
                So sánh nhanh: {quickStartPairs.join(", ")}
              </Button>
            ) : null
          }
        />
      ) : anyLoading ? (
        <PageSkeleton />
      ) : firstError ? (
        <ErrorState message={firstError.message} />
      ) : (
        <>
          <EChartBox
            title={`So sánh diễn biến: ${metricConfig.label}`}
            subtitle="Độ dài chuỗi tối đa 12 kỳ công bố gần nhất"
            option={chartOption}
            height={320}
            isEmpty={chartData.dates.length === 0}
            tableHeaders={chartTableHeaders}
            tableRows={chartTableRows}
            headerRight={
              <Select
                aria-label="Chọn chỉ số biểu đồ so sánh"
                value={chartMetric}
                onChange={(e) => setChartMetric(e.target.value as CompareMetricKey)}
                className="h-7 text-xs"
              >
                {CHART_METRICS.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </Select>
            }
          />

          <Card className="rounded-lg shadow-xs">
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Ma trận chỉ số sức khỏe tài chính &amp; định lượng
                {periodEndDate
                  ? ` (${formatPeriodLabel(periodEndDate, periodType)})`
                  : " kỳ mới nhất"}
              </CardTitle>
              <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setMatrixMode("VALUE")}
                  className={
                    matrixMode === "VALUE"
                      ? "rounded-md bg-primary px-2.5 py-1 font-medium text-primary-foreground shadow-xs"
                      : "rounded-md px-2.5 py-1 text-ink-muted transition-colors duration-150 hover:text-ink"
                  }
                >
                  Giá trị tuyệt đối
                </button>
                <button
                  type="button"
                  onClick={() => setMatrixMode("PERCENTILE")}
                  className={
                    matrixMode === "PERCENTILE"
                      ? "rounded-md bg-primary px-2.5 py-1 font-medium text-primary-foreground shadow-xs"
                      : "rounded-md px-2.5 py-1 text-ink-muted transition-colors duration-150 hover:text-ink"
                  }
                >
                  Phân vị ngành (%)
                </button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th
                        scope="col"
                        className="sticky left-0 z-10 min-w-[230px] border-r border-border bg-surface-muted px-3 py-2 text-left font-semibold"
                      >
                        Chỉ tiêu
                      </th>
                      {tickers.map((t) => (
                        <th key={t} scope="col" className="min-w-[120px] px-3 py-2 text-right">
                          <Link
                            href={`/companies/${t}`}
                            className="font-mono text-sm font-semibold text-accent-ink hover:underline"
                          >
                            {t}
                          </Link>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b border-border/60 bg-surface-muted/30">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface-muted/60 px-3 py-2 text-left font-semibold text-ink"
                      >
                        Altman Z″
                      </th>
                      {matrixData.latestMl.map((m, i) => (
                        <td key={tickers[i]} className="num px-3 py-2 text-right font-medium">
                          {formatNumber(m?.altman_z_double_prime, 2)}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/60">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink"
                      >
                        Piotroski F-Score
                      </th>
                      {matrixData.latestMl.map((m, i) => (
                        <td key={tickers[i]} className="num px-3 py-2 text-right">
                          {m?.piotroski_f_score !== null && m?.piotroski_f_score !== undefined
                            ? `${m.piotroski_f_score}/9`
                            : "N/A"}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/60">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink"
                      >
                        Beneish M-Score
                      </th>
                      {matrixData.latestMl.map((m, i) => (
                        <td key={tickers[i]} className="num px-3 py-2 text-right">
                          {formatNumber(m?.beneish_m8_score, 2)}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/60">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink"
                      >
                        Bậc rủi ro (R1–R5)
                      </th>
                      {matrixData.latestMl.map((m, i) => (
                        <td key={tickers[i]} className="px-3 py-2 text-right">
                          <div className="flex justify-end">
                            <RiskTierBadge riskTier={m?.risk_tier} />
                          </div>
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/60">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink"
                      >
                        Vị thế rủi ro cùng kỳ
                      </th>
                      {matrixData.latestMl.map((m, i) => {
                        const trio = formatCreditTrio({
                          risk_tier: m?.risk_tier,
                          risk_market_percentile: m?.risk_market_percentile,
                          calibrated_distress_prob: m?.calibrated_distress_prob,
                          relative_risk_lift: m?.relative_risk_lift,
                          market_base_rate: m?.market_base_rate,
                        });
                        return (
                          <td key={tickers[i]} className="num px-3 py-2 text-right text-ink-muted">
                            {trio.topPercentLabel}
                          </td>
                        );
                      })}
                    </tr>
                    <tr className="border-b border-border/60">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink"
                      >
                        Xác suất kiệt quệ 1 năm (so với TB)
                      </th>
                      {matrixData.latestMl.map((m, i) => {
                        const trio = formatCreditTrio({
                          risk_tier: m?.risk_tier,
                          risk_market_percentile: m?.risk_market_percentile,
                          calibrated_distress_prob: m?.calibrated_distress_prob,
                          relative_risk_lift: m?.relative_risk_lift,
                          market_base_rate: m?.market_base_rate,
                        });
                        return (
                          <td key={tickers[i]} className="num px-3 py-2 text-right font-medium">
                            <span>{trio.pdLabel}</span>
                            {trio.liftLabel ? (
                              <span className="ml-1.5 text-[11px] font-normal text-ink-muted">
                                ({trio.liftLabel})
                              </span>
                            ) : null}
                          </td>
                        );
                      })}
                    </tr>
                    <tr className="border-b border-border bg-surface-muted/30">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-r border-border bg-surface-muted/60 px-3 py-2 text-left font-semibold text-ink"
                      >
                        Số cờ đỏ đang kích hoạt
                      </th>
                      {matrixData.latestFlagsCount.map((cnt, i) => (
                        <td
                          key={tickers[i]}
                          className={`num px-3 py-2 text-right font-medium ${cnt >= 2 ? "text-risk-high" : "text-ink"}`}
                        >
                          {cnt}
                        </td>
                      ))}
                    </tr>

                    {Array.from(matrixData.ratioList.entries()).map(([metricName, meta]) => {
                      const rowItems = tickers.map((_, idx) =>
                        summaries[idx]?.data?.items.find((x) => x.metric_name === metricName),
                      );
                      const numericVals = rowItems.map((item) =>
                        !item
                          ? null
                          : matrixMode === "PERCENTILE"
                            ? typeof item.percentile_rank === "number" &&
                              Number.isFinite(item.percentile_rank)
                              ? item.percentile_rank
                              : null
                            : typeof item.company_value === "number" &&
                                Number.isFinite(item.company_value)
                              ? item.company_value
                              : null,
                      );
                      const validNums = numericVals.filter((v): v is number => v !== null);
                      const canHighlight = validNums.length >= 2;
                      const maxVal = canHighlight ? Math.max(...validNums) : null;
                      const minVal = canHighlight ? Math.min(...validNums) : null;
                      const hasSpread = maxVal !== null && minVal !== null && maxVal !== minVal;

                      return (
                        <tr
                          key={metricName}
                          className="border-b border-border/60 hover:bg-surface-hover/40"
                        >
                          <th
                            scope="row"
                            className="sticky left-0 z-10 border-r border-border bg-surface px-3 py-2 text-left font-normal text-ink"
                          >
                            {meta.name}
                          </th>
                          {tickers.map((t, idx) => {
                            const item = rowItems[idx];
                            const val = numericVals[idx];
                            const display = !item
                              ? "N/A"
                              : matrixMode === "PERCENTILE"
                                ? item.percentile_rank !== null &&
                                  item.percentile_rank !== undefined
                                  ? `${formatNumber(item.percentile_rank, 0)}%`
                                  : "N/A"
                                : formatRatioValue(item.company_value, item.format_type);

                            const isBest =
                              hasSpread &&
                              val !== null &&
                              ((meta.direction === "HIGHER_IS_BETTER" && val === maxVal) ||
                                (meta.direction === "LOWER_IS_BETTER" && val === minVal));
                            const isWorst =
                              hasSpread &&
                              val !== null &&
                              ((meta.direction === "HIGHER_IS_BETTER" && val === minVal) ||
                                (meta.direction === "LOWER_IS_BETTER" && val === maxVal));

                            return (
                              <td
                                key={t}
                                className={`num px-3 py-2 text-right ${
                                  isBest
                                    ? "bg-risk-low-soft/35 font-semibold text-risk-low"
                                    : isWorst
                                      ? "bg-risk-high-soft/25 text-risk-high"
                                      : "text-ink"
                                }`}
                              >
                                {display}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

export default function ComparePage() {
  return (
    <React.Suspense fallback={<PageSkeleton />}>
      <CompareContent />
    </React.Suspense>
  );
}
