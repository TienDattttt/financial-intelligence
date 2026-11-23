"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { formatPeerGroupLabel } from "@/lib/domain-labels";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatRatioValue, type RatioFormatType } from "@/lib/format";
import type { PeerRankingItem } from "@/types/api";

export interface BoxPlotOutlier {
  ticker: string;
  companyName: string | null;
  value: number;
}

export interface SectorBoxPlotStats {
  /** [lowerWhisker, q1, median, q3, upperWhisker] */
  fiveNumber: [number, number, number, number, number];
  minObserved: number;
  maxObserved: number;
  sampleCount: number;
  outliers: BoxPlotOutlier[];
}

function quantile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  if (sorted.length === 1) return sorted[0];
  const pos = (sorted.length - 1) * p;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  return next !== undefined ? sorted[base] + rest * (next - sorted[base]) : sorted[base];
}

export function computeBoxPlotStats(
  peerRanking: readonly PeerRankingItem[],
  fallback?: {
    peerP25?: number | null;
    peerMedian?: number | null;
    peerP75?: number | null;
  },
): SectorBoxPlotStats | null {
  const validPeers = peerRanking
    .filter(
      (p): p is PeerRankingItem & { value: number } =>
        typeof p.value === "number" && Number.isFinite(p.value),
    )
    .sort((a, b) => a.value - b.value);

  if (validPeers.length >= 3) {
    const vals = validPeers.map((p) => p.value);
    const q1 = quantile(vals, 0.25);
    const median = quantile(vals, 0.5);
    const q3 = quantile(vals, 0.75);
    const iqr = q3 - q1;
    const lowFence = q1 - 1.5 * iqr;
    const highFence = q3 + 1.5 * iqr;

    const inFence = vals.filter((v) => v >= lowFence && v <= highFence);
    const lowerWhisker = inFence.length > 0 ? inFence[0] : vals[0];
    const upperWhisker = inFence.length > 0 ? inFence[inFence.length - 1] : vals[vals.length - 1];

    const outliers: BoxPlotOutlier[] = validPeers
      .filter((p) => p.value < lowFence || p.value > highFence)
      .map((p) => ({
        ticker: p.ticker ?? "—",
        companyName: p.company_name_vn ?? null,
        value: p.value,
      }));

    return {
      fiveNumber: [lowerWhisker, q1, median, q3, upperWhisker],
      minObserved: vals[0],
      maxObserved: vals[vals.length - 1],
      sampleCount: vals.length,
      outliers,
    };
  }

  const p25 = fallback?.peerP25;
  const med = fallback?.peerMedian;
  const p75 = fallback?.peerP75;
  if (
    typeof p25 === "number" &&
    Number.isFinite(p25) &&
    typeof med === "number" &&
    Number.isFinite(med) &&
    typeof p75 === "number" &&
    Number.isFinite(p75)
  ) {
    return {
      fiveNumber: [p25, p25, med, p75, p75],
      minObserved: p25,
      maxObserved: p75,
      sampleCount: validPeers.length,
      outliers: [],
    };
  }

  return null;
}

export function SectorBoxPlotChart({
  ticker,
  metricLabel,
  formatType,
  companyValue,
  peerP25,
  peerMedian,
  peerP75,
  peerRanking,
  peerGroupLevel,
  peerCount,
}: {
  ticker: string;
  metricLabel: string;
  formatType: RatioFormatType;
  companyValue: number | null | undefined;
  peerP25: number | null | undefined;
  peerMedian: number | null | undefined;
  peerP75: number | null | undefined;
  peerRanking: readonly PeerRankingItem[];
  peerGroupLevel: string | null | undefined;
  peerCount: number;
}) {
  const { palette } = useChartTheme();

  const stats = React.useMemo(
    () =>
      computeBoxPlotStats(peerRanking, {
        peerP25,
        peerMedian,
        peerP75,
      }),
    [peerMedian, peerP25, peerP75, peerRanking],
  );

  const peerMeta = React.useMemo(
    () => formatPeerGroupLabel(peerGroupLevel, peerCount),
    [peerCount, peerGroupLevel],
  );

  const chartTitle = React.useMemo(() => {
    if (!stats) return `Phân phối ngành: ${metricLabel}`;
    const compStr = formatRatioValue(companyValue, formatType);
    const medStr = formatRatioValue(stats.fiveNumber[2], formatType);
    return `Phân phối ngành ${metricLabel}: ${ticker} (${compStr}) vs trung vị ${medStr}`;
  }, [companyValue, formatType, metricLabel, stats, ticker]);

  const option = React.useMemo<EChartsOption>(() => {
    if (!stats) return {};
    const formatAxisVal = (val: number) => {
      if (formatType === "PERCENT") return `${formatNumber(val * 100, 0)}%`;
      if (formatType === "CURRENCY_PER_SHARE") return formatNumber(val, 0);
      return formatNumber(val, 1);
    };

    const hasCompanyVal =
      typeof companyValue === "number" && Number.isFinite(companyValue);

    return {
      grid: { left: 60, right: 24, top: 36, bottom: 38 },
      legend: {
        top: 2,
        right: 8,
      },
      tooltip: {
        trigger: "item",
        formatter: (raw: unknown) => {
          const item = raw as { seriesName?: string; data?: unknown } | undefined;
          if (item?.seriesName === ticker && hasCompanyVal) {
            return [
              `<div style="font-weight:600;margin-bottom:4px">${ticker}</div>`,
              `<div>Giá trị: <b>${formatRatioValue(companyValue, formatType)}</b></div>`,
              `<div>Trung vị ngành: <b>${formatRatioValue(stats.fiveNumber[2], formatType)}</b></div>`,
            ].join("");
          }
          if (item?.seriesName === "Giá trị ngoại lai") {
            const d = item.data as { meta?: BoxPlotOutlier } | undefined;
            if (d?.meta) {
              return [
                `<div style="font-weight:600;margin-bottom:4px">${d.meta.ticker}</div>`,
                `<div>${d.meta.companyName ?? "Doanh nghiệp cùng ngành"}</div>`,
                `<div>Giá trị: <b>${formatRatioValue(d.meta.value, formatType)}</b></div>`,
              ].join("");
            }
          }
          const [low, q1, med, q3, high] = stats.fiveNumber;
          return [
            `<div style="font-weight:600;margin-bottom:4px">${metricLabel} (${stats.sampleCount} DN)</div>`,
            `<div>Cận trên (Tukey): <b>${formatRatioValue(high, formatType)}</b></div>`,
            `<div>P75: <b>${formatRatioValue(q3, formatType)}</b></div>`,
            `<div>Trung vị (P50): <b>${formatRatioValue(med, formatType)}</b></div>`,
            `<div>P25: <b>${formatRatioValue(q1, formatType)}</b></div>`,
            `<div>Cận dưới (Tukey): <b>${formatRatioValue(low, formatType)}</b></div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: [metricLabel],
        axisLabel: { color: palette.ink, fontWeight: 500 },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          formatter: (val: number) => formatAxisVal(val),
        },
      },
      series: [
        {
          name: "Phân phối ngành",
          type: "boxplot",
          boxWidth: ["24%", "42%"],
          data: [stats.fiveNumber],
          itemStyle: {
            color: palette.peerBandFill,
            borderColor: palette.peerMedian,
            borderWidth: 1.5,
          },
        },
        ...(stats.outliers.length > 0
          ? [
              {
                name: "Giá trị ngoại lai",
                type: "scatter" as const,
                symbolSize: 6,
                itemStyle: { color: palette.neutral, opacity: 0.7 },
                data: stats.outliers.map((o) => ({
                  value: [0, o.value],
                  meta: o,
                })),
              },
            ]
          : []),
        ...(hasCompanyVal
          ? [
              {
                name: ticker,
                type: "scatter" as const,
                symbol: "diamond",
                symbolSize: 13,
                z: 5,
                itemStyle: {
                  color: palette.company,
                  borderColor: palette.surface,
                  borderWidth: 1.5,
                },
                data: [[0, companyValue]],
              },
            ]
          : []),
      ],
    };
  }, [companyValue, formatType, metricLabel, palette, stats, ticker]);

  const tableHeaders = ["Chỉ số thống kê", "Giá trị"];
  const tableRows = stats
    ? [
        [ticker, formatRatioValue(companyValue, formatType)],
        ["Thấp nhất (quan sát)", formatRatioValue(stats.minObserved, formatType)],
        ["Cận dưới (1,5×IQR)", formatRatioValue(stats.fiveNumber[0], formatType)],
        ["Phân vị P25", formatRatioValue(stats.fiveNumber[1], formatType)],
        ["Trung vị (P50)", formatRatioValue(stats.fiveNumber[2], formatType)],
        ["Phân vị P75", formatRatioValue(stats.fiveNumber[3], formatType)],
        ["Cận trên (1,5×IQR)", formatRatioValue(stats.fiveNumber[4], formatType)],
        ["Cao nhất (quan sát)", formatRatioValue(stats.maxObserved, formatType)],
        ["Số DN trong mẫu", String(stats.sampleCount)],
      ]
    : [];

  return (
    <EChartBox
      title={chartTitle}
      subtitle={`${peerMeta.text} · Hộp thể hiện P25–P75, râu Tukey 1,5×IQR và vị trí ${ticker}`}
      option={option}
      height={320}
      isEmpty={!stats}
      emptyMessage="Chưa đủ mẫu cùng ngành để dựng biểu đồ Box Plot cho chỉ số này."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
    />
  );
}
