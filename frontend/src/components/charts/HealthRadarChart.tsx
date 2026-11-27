"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { useChartTheme } from "@/lib/echarts-theme";
import {
  formatNumber,
  getPercentileQuintile,
  normalizeSearchText,
  type RatioDirection,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BenchmarkSummaryItem } from "@/types/api";

export type PillarGroupId =
  | "PROFITABILITY"
  | "LIQUIDITY"
  | "LEVERAGE"
  | "EFFICIENCY_CF"
  | "GROWTH_QUALITY";

export interface HealthPillarDefinition {
  id: PillarGroupId;
  label: string;
  shortLabel: string;
  categories: readonly BenchmarkSummaryItem["category"][];
}

export const HEALTH_PILLAR_DEFS: readonly HealthPillarDefinition[] = [
  {
    id: "PROFITABILITY",
    label: "Khả năng sinh lời",
    shortLabel: "Sinh lời",
    categories: ["PROFITABILITY"],
  },
  {
    id: "LIQUIDITY",
    label: "Thanh khoản",
    shortLabel: "Thanh khoản",
    categories: ["LIQUIDITY"],
  },
  {
    id: "LEVERAGE",
    label: "Đòn bẩy & An toàn nợ",
    shortLabel: "An toàn nợ",
    categories: ["LEVERAGE"],
  },
  {
    id: "EFFICIENCY_CF",
    label: "Hiệu quả hoạt động & Dòng tiền",
    shortLabel: "Hiệu quả & CF",
    categories: ["EFFICIENCY", "CASH_FLOW"],
  },
  {
    id: "GROWTH_QUALITY",
    label: "Tăng trưởng & Chất lượng LN",
    shortLabel: "Tăng trưởng & CL",
    categories: ["GROWTH", "FORENSIC"],
  },
] as const;

export interface FivePillarScore {
  id: PillarGroupId;
  label: string;
  shortLabel: string;
  score: number | null;
  quintile: 1 | 2 | 3 | 4 | 5 | null;
  metricCount: number;
}

export function normalizeHealthPercentile(
  percentileRank: number | null | undefined,
  direction: RatioDirection,
): number | null {
  if (
    typeof percentileRank !== "number" ||
    !Number.isFinite(percentileRank) ||
    direction === "NEUTRAL"
  ) {
    return null;
  }
  const clamped = Math.max(0, Math.min(100, percentileRank));
  return direction === "LOWER_IS_BETTER" ? 100 - clamped : clamped;
}

export function computeFivePillarProfile(
  items: readonly BenchmarkSummaryItem[],
): FivePillarScore[] {
  return HEALTH_PILLAR_DEFS.map((pillar) => {
    const catSet = new Set(pillar.categories);
    const scores: number[] = [];
    for (const item of items) {
      if (!catSet.has(item.category)) continue;
      const normalized = normalizeHealthPercentile(
        item.percentile_rank,
        item.direction,
      );
      if (normalized !== null) {
        scores.push(normalized);
      }
    }
    if (scores.length === 0) {
      return {
        id: pillar.id,
        label: pillar.label,
        shortLabel: pillar.shortLabel,
        score: null,
        quintile: null,
        metricCount: 0,
      };
    }
    const avg = scores.reduce((sum, v) => sum + v, 0) / scores.length;
    const rounded = Number(avg.toFixed(1));
    return {
      id: pillar.id,
      label: pillar.label,
      shortLabel: pillar.shortLabel,
      score: rounded,
      quintile: getPercentileQuintile(rounded, "HIGHER_IS_BETTER"),
      metricCount: scores.length,
    };
  });
}

export type RatioTableSortField = "name" | "company_value" | "peer_median" | "percentile";

export interface GroupedRatioBucket {
  id: PillarGroupId;
  label: string;
  items: BenchmarkSummaryItem[];
}

export function groupAndSortSummaryRatios(
  items: readonly BenchmarkSummaryItem[],
  opts: {
    search?: string;
    groupId?: PillarGroupId | "ALL";
    sortBy?: RatioTableSortField;
    sortDir?: "asc" | "desc";
  } = {},
): GroupedRatioBucket[] {
  const q = normalizeSearchText(opts.search ?? "");
  const groupId = opts.groupId ?? "ALL";
  const sortBy = opts.sortBy ?? "name";
  const sortDir = opts.sortDir ?? "asc";
  const dirMult = sortDir === "asc" ? 1 : -1;

  const filtered = items.filter((item) => {
    if (!q) return true;
    return (
      normalizeSearchText(item.display_name_vn).includes(q) ||
      normalizeSearchText(item.metric_name).includes(q)
    );
  });

  const sortItems = (list: BenchmarkSummaryItem[]): BenchmarkSummaryItem[] =>
    [...list].sort((a, b) => {
      if (sortBy === "name") {
        return a.display_name_vn.localeCompare(b.display_name_vn, "vi") * dirMult;
      }
      const getNum = (item: BenchmarkSummaryItem): number | null => {
        if (sortBy === "company_value") return item.company_value ?? null;
        if (sortBy === "peer_median") return item.peer_median ?? null;
        return normalizeHealthPercentile(item.percentile_rank, item.direction);
      };
      const av = getNum(a);
      const bv = getNum(b);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return (av - bv) * dirMult;
    });

  return HEALTH_PILLAR_DEFS.filter((def) => groupId === "ALL" || def.id === groupId)
    .map((def) => {
      const catSet = new Set(def.categories);
      const bucketItems = sortItems(
        filtered.filter((item) => catSet.has(item.category)),
      );
      return {
        id: def.id,
        label: def.label,
        items: bucketItems,
      };
    })
    .filter((bucket) => bucket.items.length > 0);
}

const QUINTILE_TONE_CLASSES: Record<1 | 2 | 3 | 4 | 5, string> = {
  1: "border-risk-high/35 bg-risk-high-soft text-risk-high",
  2: "border-risk-mid/35 bg-risk-mid-soft text-risk-mid",
  3: "border-border bg-surface-muted text-ink-muted",
  4: "border-risk-low/30 bg-risk-low-soft/70 text-risk-low",
  5: "border-risk-low/40 bg-risk-low-soft text-risk-low",
};

export function HealthRadarChart({
  ticker,
  items,
  isLoading = false,
}: {
  ticker: string;
  items: readonly BenchmarkSummaryItem[];
  isLoading?: boolean;
}) {
  const { palette } = useChartTheme();

  const pillars = React.useMemo(() => computeFivePillarProfile(items), [items]);
  const validPillars = React.useMemo(
    () => pillars.filter((p): p is FivePillarScore & { score: number } => p.score !== null),
    [pillars],
  );

  const chartTitle = React.useMemo(() => {
    if (validPillars.length === 0) {
      return "Hồ sơ sức khỏe 5 trụ cột so với trung vị ngành";
    }
    const sorted = [...validPillars].sort((a, b) => b.score - a.score);
    const best = sorted[0];
    const worst = sorted[sorted.length - 1];
    if (best && worst && best.id !== worst.id) {
      return `Hồ sơ 5 trụ cột ${ticker}: Mạnh nhất ở ${best.shortLabel} (${formatNumber(best.score, 0)}/100) · Yếu nhất ở ${worst.shortLabel} (${formatNumber(worst.score, 0)}/100)`;
    }
    return `Hồ sơ sức khỏe 5 trụ cột ${ticker} (${formatNumber(best?.score, 0)}/100)`;
  }, [ticker, validPillars]);

  const option = React.useMemo<EChartsOption>(() => {
    return {
      legend: {
        top: 2,
        right: 8,
      },
      tooltip: {
        trigger: "item",
        formatter: () => {
          const rows = pillars.map((p) => {
            const qLabel = p.quintile ? `Mức ${p.quintile}/5` : "N/A";
            const scoreStr = p.score !== null ? `${formatNumber(p.score, 0)}/100` : "N/A";
            return `<div>${p.label}: <b>${scoreStr}</b> (${qLabel})</div>`;
          });
          return [
            `<div style="font-weight:600;margin-bottom:4px">${ticker} vs Trung vị ngành (50/100)</div>`,
            ...rows,
          ].join("");
        },
      },
      radar: {
        center: ["50%", "55%"],
        radius: "64%",
        splitNumber: 4,
        axisName: {
          color: palette.inkMuted,
          fontFamily: palette.fontFamily,
          fontSize: 11,
        },
        splitLine: {
          lineStyle: { color: palette.splitLine },
        },
        splitArea: {
          areaStyle: {
            color: [palette.surface, palette.surfaceMuted],
          },
        },
        axisLine: {
          lineStyle: { color: palette.border },
        },
        indicator: pillars.map((p) => ({
          name: p.shortLabel,
          min: 0,
          max: 100,
        })),
      },
      series: [
        {
          type: "radar",
          data: [
            {
              name: "Trung vị ngành (50)",
              value: pillars.map(() => 50),
              symbol: "none",
              lineStyle: { color: palette.peerMedian, width: 1.5, type: "dashed" },
              itemStyle: { color: palette.peerMedian },
              areaStyle: { color: "transparent" },
            },
            {
              name: ticker,
              value: pillars.map((p) => p.score ?? 0),
              symbol: "circle",
              symbolSize: 5,
              lineStyle: { color: palette.company, width: 2.2 },
              itemStyle: { color: palette.company },
              areaStyle: { color: palette.peerBandFill },
            },
          ],
        },
      ],
    };
  }, [palette, pillars, ticker]);

  const tableHeaders = ["Trụ cột", `Điểm ${ticker} (0–100)`, "Trung vị ngành", "Mức đánh giá", "Số chỉ số"];
  const tableRows = pillars.map((p) => [
    p.label,
    p.score !== null ? formatNumber(p.score, 1) : "N/A",
    "50,0",
    p.quintile ? `Mức ${p.quintile}/5` : "N/A",
    String(p.metricCount),
  ]);

  return (
    <div className="space-y-2">
      <EChartBox
        title={chartTitle}
        subtitle="Thang điểm 0–100 chuẩn hóa theo phân vị cùng ngành (Mức 1/5 yếu nhất → Mức 5/5 tốt nhất · Trung vị = 50)"
        option={option}
        height={320}
        isEmpty={!isLoading && validPillars.length === 0}
        emptyMessage={
          isLoading
            ? "Đang tính toán hồ sơ 5 trụ cột..."
            : "Chưa đủ dữ liệu phân vị ngành để dựng hồ sơ 5 trụ cột."
        }
        tableHeaders={tableHeaders}
        tableRows={tableRows}
      />

      {validPillars.length > 0 ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {pillars.map((p) => (
            <div
              key={p.id}
              className="rounded-lg border border-border bg-surface px-2.5 py-2 text-xs shadow-xs"
            >
              <p className="truncate text-[11px] text-ink-muted" title={p.label}>
                {p.shortLabel}
              </p>
              <div className="mt-1 flex items-center justify-between gap-1">
                <span className="num font-semibold text-ink">
                  {p.score !== null ? `${formatNumber(p.score, 0)}` : "N/A"}
                </span>
                {p.quintile ? (
                  <span
                    className={cn(
                      "whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] font-semibold",
                      QUINTILE_TONE_CLASSES[p.quintile],
                    )}
                  >
                    Mức {p.quintile}/5
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
