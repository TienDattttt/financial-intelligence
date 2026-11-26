"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import {
  computeFivePillarProfile,
  HEALTH_PILLAR_DEFS,
  type FivePillarScore,
} from "@/components/charts/HealthRadarChart";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import { formatNumber } from "@/lib/format";
import type { BenchmarkSummaryItem } from "@/types/api";

export interface ComparePillarCompanyInput {
  ticker: string;
  items: readonly BenchmarkSummaryItem[];
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function CompareFivePillarChart({
  companies,
  isLoading = false,
}: {
  companies: readonly ComparePillarCompanyInput[];
  isLoading?: boolean;
}) {
  const { palette } = useChartTheme();
  const [isNarrow, setIsNarrow] = React.useState(false);

  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 519px)");
    const sync = () => setIsNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const computedProfiles = React.useMemo(
    () =>
      companies.map((c) => ({
        ticker: c.ticker,
        pillars: computeFivePillarProfile(c.items),
      })),
    [companies],
  );

  const hasAnyScore = React.useMemo(
    () =>
      computedProfiles.some((c) =>
        c.pillars.some((p: FivePillarScore) => p.score !== null),
      ),
    [computedProfiles],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const xLabels = HEALTH_PILLAR_DEFS.map((p) => p.shortLabel);

    return {
      grid: isNarrow
        ? { left: 40, right: 12, top: 56, bottom: 48 }
        : { left: 48, right: 20, top: 48, bottom: 36 },
      legend: {
        top: 4,
        left: 8,
        right: 8,
        type: "plain",
        itemGap: 10,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const pIdx = first?.dataIndex ?? -1;
          const pillarDef = HEALTH_PILLAR_DEFS[pIdx];
          if (!pillarDef) return "";
          const rows = computedProfiles.map((comp, cIdx) => {
            const p = comp.pillars[pIdx];
            const color = palette.series[cIdx % palette.series.length];
            const scoreStr =
              p && p.score !== null
                ? `${formatNumber(p.score, 0)}/100 (Mức ${p.quintile ?? "—"}/5)`
                : "N/A";
            return [
              `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">`,
              `<span><span style="display:inline-block;width:8px;height:8px;border-radius:999px;background:${color};margin-right:6px"></span>${escapeHtml(comp.ticker)}</span>`,
              `<b>${scoreStr}</b>`,
              `</div>`,
            ].join("");
          });
          return [
            `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(pillarDef.label)}</div>`,
            `<div style="color:${palette.inkMuted};font-size:11px;margin-bottom:4px">Trung vị ngành: 50/100</div>`,
            ...rows,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: xLabels,
        axisLabel: {
          interval: 0,
          fontSize: isNarrow ? 10 : 11,
          rotate: isNarrow ? 20 : 0,
        },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        splitNumber: isNarrow ? 4 : 5,
        axisLabel: {
          formatter: (v: number) => formatChartNumber(v, 0),
        },
      },
      series: computedProfiles.map((comp, idx) => {
        const seriesColor = palette.series[idx % palette.series.length];
        return {
          name: comp.ticker,
          type: "bar" as const,
          barGap: "16%",
          barMaxWidth: 22,
          itemStyle: {
            color: seriesColor,
            borderRadius: [3, 3, 0, 0],
          },
          data: comp.pillars.map((p) => p.score ?? null),
          ...(idx === 0
            ? {
                markLine: {
                  silent: true,
                  symbol: "none",
                  lineStyle: {
                    color: palette.peerMedian,
                    width: 1.5,
                    type: "dashed" as const,
                  },
                  label: {
                    formatter: "Trung vị ngành (50)",
                    position: "insideEndTop" as const,
                    rotate: 0,
                    fontSize: 10,
                    color: palette.inkMuted,
                    backgroundColor: palette.surface,
                    padding: [1, 4],
                    borderRadius: 2,
                  },
                  data: [{ yAxis: 50 }],
                },
              }
            : {}),
        };
      }),
    };
  }, [
    computedProfiles,
    isNarrow,
    palette.inkMuted,
    palette.peerMedian,
    palette.series,
    palette.surface,
  ]);

  const tableHeaders = [
    "Trụ cột sức khỏe",
    ...computedProfiles.map((c) => c.ticker),
    "Trung vị ngành",
  ];

  const tableRows = HEALTH_PILLAR_DEFS.map((def, pIdx) => [
    def.label,
    ...computedProfiles.map((comp) => {
      const p = comp.pillars[pIdx];
      if (!p || p.score === null) return "N/A";
      const tierLabel = p.quintile ? `Mức ${p.quintile}/5` : "N/A";
      return `${formatNumber(p.score, 0)}/100 (${tierLabel})`;
    }),
    "50/100",
  ]);

  return (
    <EChartBox
      title="So sánh hồ sơ 5 trụ cột sức khỏe tài chính"
      subtitle="Thang điểm 0–100 chuẩn hóa theo phân vị cùng ngành (Mức 1/5 yếu nhất → Mức 5/5 tốt nhất · Đường đứt nét = Trung vị ngành 50)"
      option={option}
      height={320}
      isEmpty={!isLoading && !hasAnyScore}
      emptyMessage={
        isLoading
          ? "Đang tính toán hồ sơ 5 trụ cột..."
          : "Chưa đủ dữ liệu phân vị ngành để so sánh 5 trụ cột."
      }
      tableHeaders={tableHeaders}
      tableRows={tableRows}
    />
  );
}
