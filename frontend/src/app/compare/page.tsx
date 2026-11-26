"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Download, Plus, X } from "lucide-react";
import { CompareFivePillarChart } from "@/components/charts/CompareFivePillarChart";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { CompareMatrixCard } from "@/components/compare/CompareMatrixCard";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { useAllIssuers, useCompareCompanies } from "@/hooks/use-finintel-queries";
import {
  buildCompareCsv,
  CHART_METRICS,
  type CompareMetricKey,
  formatCompareAxisLabel,
  getComparePeriodKey,
} from "@/lib/compare-helpers";
import {
  PERIOD_TYPE_LABELS,
  selectMlScoreForPeriod,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import {
  formatNumber,
  formatPeriodLabel,
  normalizeSearchText,
  type RatioDirection,
} from "@/lib/format";
import type { RatioName } from "@/types/api";

function CompareContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { statementScope, periodType, periodEndDate, cacheSnapshotKey } = useAppState();
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
  const [tickerError, setTickerError] = React.useState<string | null>(null);
  const [dismissedErrors, setDismissedErrors] = React.useState<string[]>([]);
  const [chartMetric, setChartMetric] =
    React.useState<CompareMetricKey>("calibrated_distress_prob");
  const [matrixMode, setMatrixMode] = React.useState<"VALUE" | "PERCENTILE">("VALUE");

  const issuersQuery = useAllIssuers(cacheSnapshotKey);
  const { profiles, summaries } = useCompareCompanies(
    tickers,
    statementScope,
    periodType,
    periodEndDate,
    cacheSnapshotKey,
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
    if (!/^[A-Z0-9]{1,10}$/.test(clean)) {
      setTickerError("Mã cổ phiếu chỉ gồm 1–10 ký tự chữ hoặc số.");
      return;
    }
    if (tickers.includes(clean)) {
      setTickerError(`Mã ${clean} đã có trong danh sách so sánh.`);
      return;
    }
    if (tickers.length >= 5) {
      setTickerError("Đã chọn tối đa 5/5 doanh nghiệp để so sánh.");
      return;
    }
    if (issuersQuery.data && issuersQuery.data.length > 0) {
      const exists = issuersQuery.data.some(
        (iss) => iss.ticker.toUpperCase() === clean,
      );
      if (!exists) {
        setTickerError(
          `Không tìm thấy doanh nghiệp mã "${clean}" trong phạm vi dữ liệu FinIntel.`,
        );
        return;
      }
    }
    setTickerError(null);
    setTickersUrl([...tickers, clean]);
    setTickerInput("");
  };

  const removeTicker = (t: string) => {
    setTickerError(null);
    setDismissedErrors((prev) => prev.filter((x) => x !== t));
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
    const list = issuersQuery.data ?? [];
    const rankOf = (iss: (typeof list)[number]): number => {
      const tickerNorm = normalizeSearchText(iss.ticker);
      if (tickerNorm === q) return 0;
      if (tickerNorm.startsWith(q)) return 1;
      if (tickerNorm.includes(q)) return 2;
      return 3;
    };
    return list
      .filter((iss) => {
        const tickerNorm = normalizeSearchText(iss.ticker);
        const nameNorm = normalizeSearchText(iss.company_name_vn ?? "");
        return (
          !tickers.includes(iss.ticker.toUpperCase()) &&
          (tickerNorm.includes(q) || nameNorm.includes(q))
        );
      })
      .sort((a, b) => {
        const diff = rankOf(a) - rankOf(b);
        if (diff !== 0) return diff;
        return a.ticker.localeCompare(b.ticker);
      })
      .slice(0, 8);
  }, [issuersQuery.data, tickerInput, tickers]);

  const resolveSubmittedTicker = (): string => {
    const trimmed = tickerInput.trim();
    const clean = trimmed.toUpperCase();
    const allIssuers = issuersQuery.data ?? [];
    if (
      clean.length > 0 &&
      !/\s/.test(clean) &&
      (tickers.includes(clean) ||
        allIssuers.some((iss) => iss.ticker.toUpperCase() === clean) ||
        (/^[A-Z0-9]{1,5}$/.test(clean) &&
          issuerSuggestions[0]?.ticker.toUpperCase() !== clean))
    ) {
      return clean;
    }
    return issuerSuggestions[0]?.ticker ?? trimmed;
  };

  const activeEntries = React.useMemo(
    () =>
      tickers
        .map((ticker, idx) => ({
          ticker,
          idx,
          profileQuery: profiles[idx],
          summaryQuery: summaries[idx],
        }))
        .filter((entry) => !entry.profileQuery?.isError),
    [profiles, summaries, tickers],
  );
  const activeTickers = React.useMemo(
    () => activeEntries.map((e) => e.ticker),
    [activeEntries],
  );

  const failedEntries = React.useMemo(
    () =>
      tickers
        .map((ticker, idx) => ({
          ticker,
          error: profiles[idx]?.error ?? null,
          isError: Boolean(profiles[idx]?.isError),
        }))
        .filter((entry) => entry.isError),
    [profiles, tickers],
  );

  const metricConfig =
    CHART_METRICS.find((m) => m.key === chartMetric) ?? CHART_METRICS[0];

  const chartData = React.useMemo(() => {
    const dateSet = new Set<string>();
    const perTickerMap = new Map<string, Map<string, number | null>>();

    activeEntries.forEach(({ ticker, profileQuery }) => {
      const profile = profileQuery?.data;
      const map = new Map<string, number | null>();
      if (profile) {
        if (metricConfig.isMl) {
          for (const row of profile.ml_scores) {
            const key = getComparePeriodKey(
              row.period_end_date,
              periodType,
              (row as Record<string, unknown>).fiscal_year,
            );
            dateSet.add(key);
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
            map.set(key, scaled ?? null);
          }
        } else {
          for (const row of profile.ratios_table) {
            const d = typeof row.period_end_date === "string" ? row.period_end_date : "";
            if (!d) continue;
            const key = getComparePeriodKey(
              d,
              periodType,
              (row as Record<string, unknown>).fiscal_year,
            );
            dateSet.add(key);
            const rawVal = row[metricConfig.key];
            const num =
              typeof rawVal === "number" && Number.isFinite(rawVal) ? rawVal : null;
            const scaled =
              num !== null ? (metricConfig.isPct ? num * 100 : num) : null;
            map.set(key, scaled);
          }
        }
      }
      perTickerMap.set(ticker, map);
    });

    const maxBound = periodEndDate
      ? periodType === "ANNUAL"
        ? periodEndDate.slice(0, 4)
        : periodEndDate
      : null;

    const dates = Array.from(dateSet)
      .filter((d) => !maxBound || d <= maxBound)
      .sort()
      .slice(-12);
    return { dates, perTickerMap };
  }, [activeEntries, metricConfig, periodEndDate, periodType]);

  const chartOption = React.useMemo<EChartsOption>(() => {
    const xLabels = chartData.dates.map((d) => formatCompareAxisLabel(d, periodType));
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
      series: activeTickers.map((t, idx) => {
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
    activeTickers,
    chartData,
    metricConfig.decimals,
    metricConfig.isPct,
    metricConfig.unitSuffix,
    palette.series,
    periodType,
  ]);

  const chartTableHeaders = ["Kỳ", ...activeTickers];
  const chartTableRows = chartData.dates.map((d) => [
    formatCompareAxisLabel(d, periodType),
    ...activeTickers.map((t) => {
      const v = chartData.perTickerMap.get(t)?.get(d);
      if (v === null || v === undefined) return "N/A";
      return metricConfig.isPct
        ? `${formatNumber(v, 1)}%`
        : `${formatNumber(v, metricConfig.decimals)}${metricConfig.unitSuffix ?? ""}`;
    }),
  ]);

  // Extract latest ML score + summary metrics for each valid ticker
  const matrixData = React.useMemo(() => {
    const ratioList = new Map<
      RatioName,
      {
        name: string;
        formatType: "PERCENT" | "MULTIPLE" | "DAYS" | "CURRENCY_PER_SHARE" | "NUMBER";
        direction: RatioDirection;
      }
    >();
    activeEntries.forEach(({ summaryQuery }) => {
      for (const item of summaryQuery?.data?.items ?? []) {
        if (!ratioList.has(item.metric_name)) {
          ratioList.set(item.metric_name, {
            name: item.display_name_vn,
            formatType: item.format_type,
            direction: item.direction,
          });
        }
      }
    });

    const latestMl = activeEntries.map(({ profileQuery }) =>
      selectMlScoreForPeriod(profileQuery?.data?.ml_scores, periodEndDate),
    );

    const latestFlagsCount = activeEntries.map(({ profileQuery }, idx) => {
      const p = profileQuery?.data;
      if (!p) return 0;
      const targetDate = latestMl[idx]?.period_end_date ?? periodEndDate;
      return targetDate
        ? p.red_flags.filter((f) => f.period_end_date === targetDate).length
        : p.red_flags.length;
    });

    return { ratioList, latestMl, latestFlagsCount };
  }, [activeEntries, periodEndDate]);

  const handleExportCsv = () => {
    const csv = buildCompareCsv(activeTickers, activeEntries, matrixData, matrixMode);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finintel-so-sanh-${activeTickers.join("-")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const anyLoading =
    tickers.length > 0 &&
    (profiles.some((p) => p.isLoading) || summaries.some((s) => s.isLoading));
  const visibleFailedEntries = failedEntries.filter(
    (entry) => !dismissedErrors.includes(entry.ticker),
  );
  const allFailed = tickers.length > 0 && activeEntries.length === 0 && failedEntries.length > 0;

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
        {activeTickers.length > 0 ? (
          <Button variant="outline" size="sm" onClick={handleExportCsv}>
            <Download className="size-3.5" />
            Xuất CSV so sánh
          </Button>
        ) : null}
      </div>

      <Card className="rounded-lg shadow-xs">
        <CardContent className="space-y-3 p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            {tickers.map((t) => {
              const activeIdx = activeTickers.indexOf(t);
              const badgeColor =
                activeIdx >= 0
                  ? palette.series[activeIdx % palette.series.length]
                  : palette.neutral;
              return (
                <span
                  key={t}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-canvas px-2.5 py-1 text-xs font-medium text-ink"
                >
                  <span
                    className="size-2 rounded-full"
                    style={{ backgroundColor: badgeColor }}
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
              );
            })}

            {tickers.length < 5 ? (
              <div className="flex items-center gap-1.5">
                <Input
                  value={tickerInput}
                  onChange={(e) => {
                    setTickerInput(e.target.value);
                    if (tickerError) setTickerError(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && tickerInput.trim()) {
                      e.preventDefault();
                      addTicker(resolveSubmittedTicker());
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
                  onClick={() => addTicker(resolveSubmittedTicker())}
                >
                  <Plus className="size-3" />
                  Thêm
                </Button>
              </div>
            ) : (
              <span className="text-xs font-medium text-ink-muted">
                Đã chọn tối đa 5/5 doanh nghiệp
              </span>
            )}
          </div>

          {tickerError ? (
            <p role="alert" className="text-xs text-risk-high">
              {tickerError}
            </p>
          ) : null}

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
      ) : allFailed ? (
        <ErrorState
          message={
            failedEntries[0]?.error?.message ??
            "Không thể tải dữ liệu so sánh cho các mã đã chọn."
          }
          onRetry={() => setTickersUrl([])}
        />
      ) : (
        <>
          {visibleFailedEntries.length > 0 ? (
            <div
              role="alert"
              className="space-y-1.5 rounded-lg border border-risk-high/35 bg-risk-high-soft/50 p-3 text-xs"
            >
              {visibleFailedEntries.map((entry) => (
                <div
                  key={entry.ticker}
                  className="flex flex-wrap items-center justify-between gap-2"
                >
                  <span className="text-ink">
                    <strong className="font-mono text-risk-high">{entry.ticker}</strong>:{" "}
                    {entry.error?.message ?? "Không thể tải hồ sơ doanh nghiệp này."}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      onClick={() => removeTicker(entry.ticker)}
                    >
                      Loại bỏ {entry.ticker}
                    </Button>
                    <button
                      type="button"
                      aria-label={`Đóng cảnh báo ${entry.ticker}`}
                      onClick={() =>
                        setDismissedErrors((prev) =>
                          prev.includes(entry.ticker) ? prev : [...prev, entry.ticker],
                        )
                      }
                      className="rounded p-1 text-ink-muted hover:bg-surface/60 hover:text-ink"
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}

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

          <CompareFivePillarChart
            companies={activeEntries.map((e) => ({
              ticker: e.ticker,
              items: e.summaryQuery?.data?.items ?? [],
            }))}
            isLoading={summaries.some((s) => s.isLoading)}
          />

          <CompareMatrixCard
            activeTickers={activeTickers}
            activeEntries={activeEntries}
            matrixData={matrixData}
            matrixMode={matrixMode}
            onMatrixModeChange={setMatrixMode}
            periodEndDate={periodEndDate}
            periodType={periodType}
          />
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
