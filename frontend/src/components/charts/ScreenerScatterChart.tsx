"use client";

import * as React from "react";
import { ArrowLeftRight } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { type ChartPalette, formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import {
  formatMultiple,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import {
  formatRelativeRiskLiftLabel,
  formatTopRiskPercentileLabel,
  getScreenerRiskTierMeta,
  type ScreenerRiskTier,
} from "@/lib/saved-screens-store";
import type { ScreenerItem } from "@/types/api";

export type ScatterAxisMetricKey =
  | "altman_z_double_prime"
  | "calibrated_distress_prob"
  | "piotroski_f_score"
  | "beneish_m8_score"
  | "roe_total"
  | "roa"
  | "ebit_margin"
  | "core_operating_margin"
  | "accruals_to_assets"
  | "non_core_distortion_to_assets"
  | "core_to_ebt_ratio"
  | "debt_to_equity"
  | "interest_coverage"
  | "red_flag_count"
  | "critical_red_flag_count"
  | "altman_em_score"
  | "risk_market_percentile"
  | "relative_risk_lift";

export type ScatterAxisFormat = "NUMBER" | "PERCENT" | "MULTIPLE" | "INT";

export interface ScatterAxisMetricConfig {
  key: ScatterAxisMetricKey;
  label: string;
  axisTitle: string;
  format: ScatterAxisFormat;
  scale?: number;
  clampRange?: readonly [number, number];
  axisMin?: number;
  axisMax?: number;
  referenceLines?: readonly { value: number; label: string }[];
  keywords: string;
}

export const SCATTER_AXIS_METRICS: readonly ScatterAxisMetricConfig[] = [
  {
    key: "altman_z_double_prime",
    label: "Altman Z″",
    axisTitle: "Altman Z″ (giới hạn hiển thị [-6; 12])",
    format: "NUMBER",
    clampRange: [-6, 12],
    axisMin: -6,
    axisMax: 12,
    referenceLines: [
      { value: 1.1, label: "1,10" },
      { value: 2.6, label: "2,60" },
    ],
    keywords: "altman z score kiet que pha san",
  },
  {
    key: "calibrated_distress_prob",
    label: "Xác suất kiệt quệ 1Y (%)",
    axisTitle: "Xác suất kiệt quệ 1Y (%)",
    format: "PERCENT",
    scale: 100,
    axisMin: 0,
    keywords: "xac suat kiet que pd 1y risk",
  },
  {
    key: "piotroski_f_score",
    label: "Piotroski F-Score (0–9)",
    axisTitle: "Piotroski F-Score (0–9)",
    format: "INT",
    axisMin: 0,
    axisMax: 9,
    keywords: "piotroski f score suc khoe tai chinh",
  },
  {
    key: "beneish_m8_score",
    label: "Beneish M-Score",
    axisTitle: "Beneish M-Score (giới hạn [-6; 4])",
    format: "NUMBER",
    clampRange: [-6, 4],
    referenceLines: [{ value: -1.78, label: "−1,78" }],
    keywords: "beneish m score dieu chinh loi nhuan",
  },
  {
    key: "roe_total",
    label: "ROE (%)",
    axisTitle: "ROE (%, giới hạn [-100%; 100%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-100, 100],
    keywords: "roe ty suat sinh loi von chu so huu",
  },
  {
    key: "roa",
    label: "ROA (%)",
    axisTitle: "ROA (%, giới hạn [-50%; 60%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-50, 60],
    keywords: "roa ty suat sinh loi tong tai san",
  },
  {
    key: "ebit_margin",
    label: "Biên EBIT (%)",
    axisTitle: "Biên EBIT (%, giới hạn [-100%; 100%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-100, 100],
    keywords: "bien ebit loi nhuan hoat dong",
  },
  {
    key: "core_operating_margin",
    label: "Biên LN kinh doanh lõi (%)",
    axisTitle: "Biên LN kinh doanh lõi (%, giới hạn [-100%; 100%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-100, 100],
    keywords: "bien loi nhuan kinh doanh loi core operating margin",
  },
  {
    key: "accruals_to_assets",
    label: "Dồn tích / Tổng tài sản (%)",
    axisTitle: "Dồn tích / Tổng TS (%, giới hạn [-50%; 50%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-50, 50],
    keywords: "don tich accruals chat luong loi nhuan",
  },
  {
    key: "non_core_distortion_to_assets",
    label: "Biến dạng phi lõi / Tổng TS (%)",
    axisTitle: "Biến dạng phi lõi / TS (%, giới hạn [-50%; 50%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-50, 50],
    keywords: "bien dang phi loi non core distortion",
  },
  {
    key: "core_to_ebt_ratio",
    label: "LN lõi / EBT (%)",
    axisTitle: "LN lõi / EBT (%, giới hạn [-200%; 200%])",
    format: "PERCENT",
    scale: 100,
    clampRange: [-200, 200],
    keywords: "loi nhuan loi ebt chat luong",
  },
  {
    key: "debt_to_equity",
    label: "Nợ vay / VCSH (x)",
    axisTitle: "Nợ vay / VCSH (x, giới hạn [0; 10])",
    format: "MULTIPLE",
    clampRange: [0, 10],
    axisMin: 0,
    keywords: "no vay vcsh don bay debt equity",
  },
  {
    key: "interest_coverage",
    label: "EBIT / Lãi vay (x)",
    axisTitle: "EBIT / Lãi vay (x, giới hạn [-10; 30])",
    format: "MULTIPLE",
    clampRange: [-10, 30],
    keywords: "ebit lai vay kha nang chi tra",
  },
  {
    key: "red_flag_count",
    label: "Tổng số cờ đỏ",
    axisTitle: "Tổng số cờ đỏ kế toán",
    format: "INT",
    axisMin: 0,
    keywords: "co do ke toan red flag",
  },
  {
    key: "critical_red_flag_count",
    label: "Cờ đỏ nghiêm trọng",
    axisTitle: "Số cờ đỏ mức nghiêm trọng",
    format: "INT",
    axisMin: 0,
    keywords: "co do nghiem trong critical red flag",
  },
  {
    key: "altman_em_score",
    label: "Altman Z″ (EM chuẩn hóa)",
    axisTitle: "Altman Z″ EM (giới hạn [-6; 12])",
    format: "NUMBER",
    clampRange: [-6, 12],
    keywords: "altman em score thi truong moi noi",
  },
  {
    key: "risk_market_percentile",
    label: "Phân vị rủi ro thị trường",
    axisTitle: "Phân vị rủi ro toàn thị trường (0–100)",
    format: "NUMBER",
    axisMin: 0,
    axisMax: 100,
    keywords: "phan vi rui ro thi truong percentile",
  },
  {
    key: "relative_risk_lift",
    label: "Hệ số rủi ro so nền (×)",
    axisTitle: "Hệ số rủi ro so nền thị trường (×)",
    format: "MULTIPLE",
    axisMin: 0,
    keywords: "he so rui ro so nen lift",
  },
];

export const DEFAULT_SCATTER_X_AXIS: ScatterAxisMetricKey =
  "altman_z_double_prime";
export const DEFAULT_SCATTER_Y_AXIS: ScatterAxisMetricKey =
  "calibrated_distress_prob";

export function getScatterAxisMetric(
  key: ScatterAxisMetricKey,
): ScatterAxisMetricConfig {
  return (
    SCATTER_AXIS_METRICS.find((m) => m.key === key) ?? SCATTER_AXIS_METRICS[0]
  );
}

export function formatScatterMetricValue(
  rawValue: number | null | undefined,
  metric: ScatterAxisMetricConfig,
): string {
  if (rawValue === null || rawValue === undefined || !Number.isFinite(rawValue)) {
    return "N/A";
  }
  switch (metric.format) {
    case "PERCENT":
      return formatPercent(rawValue);
    case "MULTIPLE":
      return formatMultiple(rawValue);
    case "INT":
      return formatNumber(rawValue, 0);
    case "NUMBER":
    default:
      return formatNumber(rawValue, 2);
  }
}

function projectPlotValue(
  rawValue: number,
  metric: ScatterAxisMetricConfig,
): number {
  const scaled = rawValue * (metric.scale ?? 1);
  const rounded = Number(scaled.toFixed(2));
  if (!metric.clampRange) return rounded;
  return Math.max(metric.clampRange[0], Math.min(metric.clampRange[1], rounded));
}

export interface ScatterPlotPoint {
  value: [number, number];
  ticker: string;
  companyName: string;
  sector: string;
  rawX: number;
  rawY: number;
  riskTier: ScreenerRiskTier | "UNRATED";
  riskTierLabel: string;
  topRiskLabel: string | null;
  liftLabel: string | null;
  prob: number | null;
  redFlags: number;
}

export function getScatterPlottablePoints(
  items: readonly ScreenerItem[],
  xMetric: ScatterAxisMetricConfig,
  yMetric: ScatterAxisMetricConfig,
): ScatterPlotPoint[] {
  const result: ScatterPlotPoint[] = [];
  for (const it of items) {
    const rawX = it[xMetric.key];
    const rawY = it[yMetric.key];
    if (
      typeof rawX !== "number" ||
      !Number.isFinite(rawX) ||
      typeof rawY !== "number" ||
      !Number.isFinite(rawY)
    ) {
      continue;
    }
    const tierMeta = getScreenerRiskTierMeta(it.risk_tier);
    result.push({
      value: [projectPlotValue(rawX, xMetric), projectPlotValue(rawY, yMetric)],
      ticker: it.ticker,
      companyName: it.company_name_vn ?? it.ticker,
      sector: it.icb_name_level2 ?? "Khác",
      rawX,
      rawY,
      riskTier: tierMeta.tier,
      riskTierLabel: tierMeta.shortLabel,
      topRiskLabel: formatTopRiskPercentileLabel(it.risk_market_percentile),
      liftLabel: formatRelativeRiskLiftLabel(it.relative_risk_lift),
      prob:
        typeof it.calibrated_distress_prob === "number" &&
        Number.isFinite(it.calibrated_distress_prob)
          ? it.calibrated_distress_prob
          : null,
      redFlags: it.red_flag_count,
    });
  }
  return result;
}

export interface TierSeriesStyle {
  tier: ScreenerRiskTier | "UNRATED";
  seriesName: string;
  color: string;
  symbol: "circle" | "triangle" | "diamond";
  symbolSize: number;
  opacity: number;
}

export function getRiskTierSeriesStyles(
  palette: ChartPalette,
): readonly TierSeriesStyle[] {
  return [
    {
      tier: "R1",
      seriesName: "R1 · Rất thấp",
      color: palette.riskLow,
      symbol: "circle",
      symbolSize: 7,
      opacity: 0.86,
    },
    {
      tier: "R2",
      seriesName: "R2 · Thấp",
      color: palette.company,
      symbol: "circle",
      symbolSize: 8,
      opacity: 0.86,
    },
    {
      tier: "R3",
      seriesName: "R3 · Theo dõi",
      color: palette.riskMid,
      symbol: "triangle",
      symbolSize: 8,
      opacity: 0.88,
    },
    {
      tier: "R4",
      seriesName: "R4 · Cao",
      color: palette.riskHigh,
      symbol: "circle",
      symbolSize: 9,
      opacity: 0.78,
    },
    {
      tier: "R5",
      seriesName: "R5 · Rất cao",
      color: palette.riskHigh,
      symbol: "diamond",
      symbolSize: 11,
      opacity: 0.96,
    },
    {
      tier: "UNRATED",
      seriesName: "Chưa xếp bậc",
      color: palette.neutral,
      symbol: "circle",
      symbolSize: 7,
      opacity: 0.7,
    },
  ];
}

export function buildScreenerScatterOption(
  points: readonly ScatterPlotPoint[],
  xMetric: ScatterAxisMetricConfig,
  yMetric: ScatterAxisMetricConfig,
  palette: ChartPalette,
): EChartsOption {
  const groups: Record<ScreenerRiskTier | "UNRATED", ScatterPlotPoint[]> = {
    R1: [],
    R2: [],
    R3: [],
    R4: [],
    R5: [],
    UNRATED: [],
  };
  for (const pt of points) {
    groups[pt.riskTier].push(pt);
  }

  const tooltipFormatter = (raw: unknown) => {
    const p = raw as { data?: ScatterPlotPoint };
    const d = p.data;
    if (!d) return "";
    const tierBadge =
      d.riskTier === "UNRATED"
        ? "Chưa xếp bậc"
        : [d.riskTierLabel, d.topRiskLabel, d.liftLabel]
            .filter(Boolean)
            .join(" · ");
    return [
      `<div style="font-weight:600;margin-bottom:3px">${d.ticker} · ${d.companyName}</div>`,
      `<div style="color:${palette.inkMuted};font-size:11px;margin-bottom:4px">${d.sector}</div>`,
      `<div>X · ${xMetric.label}: <b>${formatScatterMetricValue(d.rawX, xMetric)}</b></div>`,
      `<div>Y · ${yMetric.label}: <b>${formatScatterMetricValue(d.rawY, yMetric)}</b></div>`,
      `<div>Tín nhiệm: <b>${tierBadge}</b></div>`,
      `<div>Cờ đỏ kế toán: <b>${d.redFlags}</b></div>`,
      `<div style="color:${palette.company};font-size:11px;margin-top:3px">Nhấp để mở hồ sơ ${d.ticker} →</div>`,
    ].join("");
  };

  const styles = getRiskTierSeriesStyles(palette);
  const activeStyles = styles.filter(
    (s) => s.tier !== "UNRATED" || groups.UNRATED.length > 0,
  );

  const xMarkLines =
    xMetric.referenceLines?.map((rl, idx) => ({
      xAxis: rl.value,
      label: {
        formatter: rl.label,
        position: idx % 2 === 0 ? ("insideEndTop" as const) : ("insideMiddleTop" as const),
        distance: idx % 2 === 0 ? 4 : 18,
      },
    })) ?? [];
  const yMarkLines =
    yMetric.referenceLines?.map((rl) => ({
      yAxis: rl.value,
      label: { formatter: rl.label, position: "insideEndTop" as const },
    })) ?? [];
  const allMarkLines = [...xMarkLines, ...yMarkLines];

  return {
    grid: { left: 68, right: 24, top: 64, bottom: 48 },
    legend: {
      top: 4,
      right: 12,
      left: 12,
      type: "scroll",
    },
    tooltip: {
      trigger: "item",
      formatter: tooltipFormatter,
    },
    xAxis: {
      type: "value",
      name: xMetric.axisTitle,
      nameLocation: "middle",
      nameGap: 28,
      ...(xMetric.axisMin !== undefined ? { min: xMetric.axisMin } : {}),
      ...(xMetric.axisMax !== undefined ? { max: xMetric.axisMax } : {}),
      axisLabel: {
        formatter: (v: number) =>
          xMetric.format === "PERCENT"
            ? `${formatChartNumber(v, 1, { trimTrailingZeros: true })}%`
            : formatChartNumber(v, 1, { trimTrailingZeros: true }),
      },
    },
    yAxis: {
      type: "value",
      name: yMetric.axisTitle,
      nameGap: 14,
      ...(yMetric.axisMin !== undefined ? { min: yMetric.axisMin } : {}),
      ...(yMetric.axisMax !== undefined ? { max: yMetric.axisMax } : {}),
      axisLabel: {
        formatter: (v: number) =>
          yMetric.format === "PERCENT"
            ? `${formatChartNumber(v, 1, { trimTrailingZeros: true })}%`
            : formatChartNumber(v, 1, { trimTrailingZeros: true }),
      },
    },
    series: activeStyles.map((style, idx) => ({
      name: style.seriesName,
      type: "scatter" as const,
      symbol: style.symbol,
      symbolSize: style.symbolSize,
      itemStyle: {
        color: style.color,
        opacity: style.opacity,
        ...(style.tier === "R5"
          ? { borderColor: palette.ink, borderWidth: 1 }
          : {}),
      },
      data: groups[style.tier],
      ...(idx === 0 && allMarkLines.length > 0
        ? {
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: {
                type: "dashed" as const,
                color: palette.inkSubtle,
                width: 1,
              },
              label: {
                fontSize: 10,
                color: palette.inkMuted,
                backgroundColor: palette.surface,
                padding: [1, 4],
                borderRadius: 2,
              },
              data: allMarkLines,
            },
          }
        : {}),
    })),
  };
}

export function ScreenerScatterChart({
  items,
  onSelectTicker,
}: {
  items: readonly ScreenerItem[];
  onSelectTicker: (ticker: string) => void;
}) {
  const { palette } = useChartTheme();
  const [xAxisKey, setXAxisKey] = React.useState<ScatterAxisMetricKey>(
    DEFAULT_SCATTER_X_AXIS,
  );
  const [yAxisKey, setYAxisKey] = React.useState<ScatterAxisMetricKey>(
    DEFAULT_SCATTER_Y_AXIS,
  );

  const xMetric = React.useMemo(
    () => getScatterAxisMetric(xAxisKey),
    [xAxisKey],
  );
  const yMetric = React.useMemo(
    () => getScatterAxisMetric(yAxisKey),
    [yAxisKey],
  );

  const axisOptions = React.useMemo<ComboboxOption[]>(
    () =>
      SCATTER_AXIS_METRICS.map((m) => ({
        value: m.key,
        label: m.label,
        keywords: m.keywords,
      })),
    [],
  );

  const plottable = React.useMemo(
    () => getScatterPlottablePoints(items, xMetric, yMetric),
    [items, xMetric, yMetric],
  );

  const option = React.useMemo<EChartsOption>(
    () => buildScreenerScatterOption(plottable, xMetric, yMetric, palette),
    [palette, plottable, xMetric, yMetric],
  );

  const tableHeaders = [
    "Mã",
    "Ngành",
    "Bậc R1–R5",
    xMetric.label,
    yMetric.label,
    "Cờ đỏ",
  ];
  const tableRows = plottable.map((pt) => [
    pt.ticker,
    pt.sector,
    pt.riskTierLabel,
    formatScatterMetricValue(pt.rawX, xMetric),
    formatScatterMetricValue(pt.rawY, yMetric),
    String(pt.redFlags),
  ]);

  const axisSelectors = (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-medium text-ink-muted">Trục X:</span>
        <Combobox
          id="scatter-x-axis"
          options={axisOptions}
          value={xAxisKey}
          onValueChange={(next) => {
            if (next) setXAxisKey(next as ScatterAxisMetricKey);
          }}
          ariaLabel="Chọn chỉ số trục X"
          searchPlaceholder="Tìm chỉ số trục X..."
          className="h-7 w-44 text-xs"
        />
      </div>
      <Button
        type="button"
        variant="outline"
        size="icon-xs"
        title="Hoán đổi trục X và trục Y"
        aria-label="Hoán đổi trục X và trục Y"
        onClick={() => {
          setXAxisKey(yAxisKey);
          setYAxisKey(xAxisKey);
        }}
      >
        <ArrowLeftRight className="size-3" />
      </Button>
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-medium text-ink-muted">Trục Y:</span>
        <Combobox
          id="scatter-y-axis"
          options={axisOptions}
          value={yAxisKey}
          onValueChange={(next) => {
            if (next) setYAxisKey(next as ScatterAxisMetricKey);
          }}
          ariaLabel="Chọn chỉ số trục Y"
          searchPlaceholder="Tìm chỉ số trục Y..."
          className="h-7 w-44 text-xs"
        />
      </div>
    </div>
  );

  return (
    <EChartBox
      title={`Phân tán ${xMetric.label} vs ${yMetric.label} theo bậc R1–R5 (${plottable.length} DN trên trang)`}
      subtitle="Màu sắc và hình dạng điểm phân theo bậc tín nhiệm R1–R5 · Nhấp vào điểm để mở hồ sơ doanh nghiệp"
      option={option}
      height={400}
      headerRight={axisSelectors}
      isEmpty={plottable.length === 0}
      emptyMessage={`Không có doanh nghiệp nào có đủ cặp giá trị ${xMetric.label} và ${yMetric.label} trong trang kết quả hiện tại.`}
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        const d = p.data as { ticker?: string } | undefined;
        if (d?.ticker) onSelectTicker(d.ticker);
      }}
    />
  );
}
