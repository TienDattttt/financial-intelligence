"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Select } from "@/components/ui/primitives";
import {
  formatEvaluationSourceLabel,
  formatModelDisplayName,
  getRiskTierMeta,
  RISK_TIER_ORDER,
} from "@/lib/domain-labels";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent } from "@/lib/format";
import type {
  ModelMetricsItem,
  RiskTierReliabilityReportItem,
  WalkForwardModelSummaryItem,
  WalkForwardReportItem,
  WalkForwardYearItem,
} from "@/types/api";

export type WalkForwardMetricKey =
  | "roc_auc"
  | "pr_auc"
  | "brier_skill_score"
  | "recall_at_top_10_percent";

export const WALK_FORWARD_METRIC_META: Record<
  WalkForwardMetricKey,
  { label: string; isPercent: boolean; decimals: number; yMin?: number; yMax?: number }
> = {
  roc_auc: {
    label: "ROC-AUC (Khả năng phân biệt)",
    isPercent: false,
    decimals: 3,
    yMin: 0.4,
    yMax: 1.0,
  },
  pr_auc: {
    label: "PR-AUC (Độ chính xác nhóm hiếm)",
    isPercent: false,
    decimals: 3,
    yMin: 0,
  },
  brier_skill_score: {
    label: "Brier Skill Score (Chất lượng hiệu chỉnh)",
    isPercent: false,
    decimals: 3,
  },
  recall_at_top_10_percent: {
    label: "Recall @ Top 10% (Độ phủ cảnh báo)",
    isPercent: true,
    decimals: 1,
    yMin: 0,
    yMax: 100,
  },
};

const MODEL_ORDER = ["Baseline_1_AltmanZ", "Baseline_2_Logit", "XGBoost"] as const;

export interface WalkForwardComparisonPoint {
  modelKey: string;
  modelLabel: string;
  point: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  yearlyMean: number | null;
  yearlyStd: number | null;
  nClusters: number;
}

function readMetricFromItem(
  metrics: ModelMetricsItem | undefined,
  metric: WalkForwardMetricKey,
): number | null {
  if (!metrics) return null;
  const raw = metrics[metric];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

export function buildWalkForwardComparisonData(
  summaries: readonly WalkForwardModelSummaryItem[],
  metric: WalkForwardMetricKey,
): WalkForwardComparisonPoint[] {
  const byModel = new Map(summaries.map((s) => [s.model, s]));
  const orderedKeys = [
    ...MODEL_ORDER.filter((k) => byModel.has(k)),
    ...summaries.map((s) => s.model).filter((k) => !MODEL_ORDER.includes(k as (typeof MODEL_ORDER)[number])),
  ];

  return orderedKeys.map((modelKey) => {
    const item = byModel.get(modelKey);
    const point = readMetricFromItem(item?.pooled_point, metric);
    const rawCi = item?.pooled_ci95?.[metric];
    const ciLow =
      Array.isArray(rawCi) && rawCi.length >= 2 && typeof rawCi[0] === "number" && Number.isFinite(rawCi[0])
        ? rawCi[0]
        : null;
    const ciHigh =
      Array.isArray(rawCi) && rawCi.length >= 2 && typeof rawCi[1] === "number" && Number.isFinite(rawCi[1])
        ? rawCi[1]
        : null;

    let yearlyMean: number | null = null;
    let yearlyStd: number | null = null;
    if (item) {
      if (metric === "roc_auc") {
        yearlyMean = item.yearly_roc_auc_mean ?? null;
        yearlyStd = item.yearly_roc_auc_std ?? null;
      } else if (metric === "pr_auc") {
        yearlyMean = item.yearly_pr_auc_mean ?? null;
        yearlyStd = item.yearly_pr_auc_std ?? null;
      } else if (metric === "brier_skill_score") {
        yearlyMean = item.yearly_bss_mean ?? null;
        yearlyStd = item.yearly_bss_std ?? null;
      } else if (metric === "recall_at_top_10_percent") {
        yearlyMean = item.yearly_recall_top10_mean ?? null;
      }
    }

    return {
      modelKey,
      modelLabel: formatModelDisplayName(modelKey),
      point,
      ciLow,
      ciHigh,
      yearlyMean,
      yearlyStd,
      nClusters: item?.n_clusters ?? 0,
    };
  });
}

export function buildWalkForwardYearlySeries(
  perYear: readonly WalkForwardYearItem[],
  metric: WalkForwardMetricKey,
  useTrueUnseen: boolean,
): {
  years: string[];
  series: { modelKey: string; modelLabel: string; values: (number | null)[] }[];
} {
  const sorted = [...perYear].sort((a, b) => a.year - b.year);
  const years = sorted.map((y) => String(y.year));
  const keys = new Set<string>();
  for (const yr of sorted) {
    const map = useTrueUnseen ? yr.true_unseen_models : yr.models;
    for (const k of Object.keys(map)) keys.add(k);
  }
  const orderedKeys = [
    ...MODEL_ORDER.filter((k) => keys.has(k)),
    ...Array.from(keys).filter((k) => !MODEL_ORDER.includes(k as (typeof MODEL_ORDER)[number])),
  ];

  const series = orderedKeys.map((modelKey) => ({
    modelKey,
    modelLabel: formatModelDisplayName(modelKey),
    values: sorted.map((yr) => {
      const map = useTrueUnseen ? yr.true_unseen_models : yr.models;
      return readMetricFromItem(map[modelKey], metric);
    }),
  }));

  return { years, series };
}

export interface TierReliabilityChartRow {
  tier: string;
  tierLabel: string;
  count: number;
  sharePct: number;
  positiveCount: number;
  empiricalRatePct: number | null;
  predictedPdPct: number | null;
}

export function buildTierReliabilityData(
  report: RiskTierReliabilityReportItem | null | undefined,
): TierReliabilityChartRow[] {
  if (!report || !report.tiers) return [];
  const byTier = new Map(report.tiers.map((r) => [r.risk_tier, r]));
  const rows: TierReliabilityChartRow[] = [];
  for (const tier of RISK_TIER_ORDER) {
    const row = byTier.get(tier);
    if (!row) continue;
    const meta = getRiskTierMeta(tier);
    rows.push({
      tier,
      tierLabel: meta.shortLabel,
      count: row.count,
      sharePct: row.share * 100,
      positiveCount: row.positive_count,
      empiricalRatePct:
        typeof row.empirical_event_rate === "number" && Number.isFinite(row.empirical_event_rate)
          ? row.empirical_event_rate * 100
          : null,
      predictedPdPct:
        typeof row.mean_predicted_pd === "number" && Number.isFinite(row.mean_predicted_pd)
          ? row.mean_predicted_pd * 100
          : null,
    });
  }
  return rows;
}

export function WalkForwardCharts({
  walkForward,
}: {
  walkForward: WalkForwardReportItem;
}) {
  const { palette } = useChartTheme();
  const [metric, setMetric] = React.useState<WalkForwardMetricKey>("roc_auc");
  const [cohort, setCohort] = React.useState<"ALL_OOS" | "TRUE_UNSEEN">("ALL_OOS");

  const useTrueUnseen = cohort === "TRUE_UNSEEN";
  const meta = WALK_FORWARD_METRIC_META[metric];

  const comparison = React.useMemo(
    () =>
      buildWalkForwardComparisonData(
        useTrueUnseen ? walkForward.summary_true_unseen : walkForward.summary_all_oos,
        metric,
      ),
    [metric, useTrueUnseen, walkForward.summary_all_oos, walkForward.summary_true_unseen],
  );

  const yearly = React.useMemo(
    () => buildWalkForwardYearlySeries(walkForward.per_year, metric, useTrueUnseen),
    [metric, useTrueUnseen, walkForward.per_year],
  );

  const scaleVal = React.useCallback(
    (v: number | null) => {
      if (v === null) return null;
      return meta.isPercent ? Number((v * 100).toFixed(2)) : Number(v.toFixed(4));
    },
    [meta.isPercent],
  );

  const formatMetricValue = React.useCallback(
    (v: number | null) => {
      if (v === null) return "N/A";
      return meta.isPercent ? formatPercent(v) : formatNumber(v, meta.decimals);
    },
    [meta.decimals, meta.isPercent],
  );

  const comparisonOption = React.useMemo<EChartsOption>(() => {
    const categories = comparison.map((c) => c.modelLabel);
    const barValues = comparison.map((c, idx) => ({
      value: scaleVal(c.point),
      itemStyle: {
        color:
          c.modelKey === "XGBoost"
            ? palette.company
            : palette.series[(idx + 1) % palette.series.length],
        borderRadius: [4, 4, 0, 0],
      },
    }));

    const whiskerData = comparison
      .map((c, idx) => {
        const low = scaleVal(c.ciLow);
        const high = scaleVal(c.ciHigh);
        if (low === null || high === null) return null;
        return [idx, low, high];
      })
      .filter((row): row is [number, number, number] => row !== null);

    const series: NonNullable<EChartsOption["series"]> = [
      {
        name: meta.label,
        type: "bar",
        barMaxWidth: 44,
        data: barValues,
      },
    ];
    if (whiskerData.length > 0) {
      series.push({
        name: "Bootstrap 95% CI",
        type: "custom",
        data: whiskerData,
        z: 10,
        renderItem: (_params, api) => {
          const xIdx = Number(api.value(0));
          const lowPt = api.coord([xIdx, Number(api.value(1))]);
          const highPt = api.coord([xIdx, Number(api.value(2))]);
          const capHalf = 7;
          return {
            type: "group",
            children: [
              {
                type: "line",
                shape: {
                  x1: highPt[0],
                  y1: highPt[1],
                  x2: lowPt[0],
                  y2: lowPt[1],
                },
                style: { stroke: palette.ink, lineWidth: 1.75 },
              },
              {
                type: "line",
                shape: {
                  x1: highPt[0] - capHalf,
                  y1: highPt[1],
                  x2: highPt[0] + capHalf,
                  y2: highPt[1],
                },
                style: { stroke: palette.ink, lineWidth: 1.75 },
              },
              {
                type: "line",
                shape: {
                  x1: lowPt[0] - capHalf,
                  y1: lowPt[1],
                  x2: lowPt[0] + capHalf,
                  y2: lowPt[1],
                },
                style: { stroke: palette.ink, lineWidth: 1.75 },
              },
            ],
          };
        },
      });
    }

    return {
      grid: { left: 56, right: 20, top: 32, bottom: 42 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const row = comparison[first?.dataIndex ?? -1];
          if (!row) return "";
          const ciText =
            row.ciLow !== null && row.ciHigh !== null
              ? `[${formatMetricValue(row.ciLow)} – ${formatMetricValue(row.ciHigh)}]`
              : "N/A";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${row.modelLabel}</div>`,
            `<div>Ước lượng gộp ngoài mẫu: <b>${formatMetricValue(row.point)}</b></div>`,
            `<div>Khoảng tin cậy Bootstrap 95%: <b>${ciText}</b></div>`,
            row.yearlyMean !== null
              ? `<div>Trung bình theo năm: ${formatMetricValue(row.yearlyMean)}</div>`
              : "",
            `<div>Số cụm doanh nghiệp: ${formatNumber(row.nClusters, 0)} DN</div>`,
          ]
            .filter(Boolean)
            .join("");
        },
      },
      xAxis: {
        type: "category",
        data: categories,
      },
      yAxis: {
        type: "value",
        name: meta.isPercent ? "%" : "Điểm",
        min: meta.yMin,
        max: meta.yMax,
        axisLabel: {
          formatter: meta.isPercent ? "{value}%" : "{value}",
        },
      },
      series,
    };
  }, [comparison, formatMetricValue, meta.isPercent, meta.label, meta.yMax, meta.yMin, palette.company, palette.ink, palette.series, scaleVal]);

  const yearlyOption = React.useMemo<EChartsOption>(() => {
    return {
      grid: { left: 56, right: 20, top: 38, bottom: 34 },
      legend: { top: 4, right: 8 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: yearly.years,
      },
      yAxis: {
        type: "value",
        name: meta.isPercent ? "%" : "Điểm",
        min: meta.yMin,
        max: meta.yMax,
        axisLabel: {
          formatter: meta.isPercent ? "{value}%" : "{value}",
        },
      },
      series: yearly.series.map((s, idx) => {
        const color =
          s.modelKey === "XGBoost"
            ? palette.company
            : palette.series[(idx + 1) % palette.series.length];
        return {
          name: s.modelLabel,
          type: "line",
          symbolSize: 7,
          lineStyle: { color, width: s.modelKey === "XGBoost" ? 2.5 : 1.75 },
          itemStyle: { color },
          data: s.values.map((v) => scaleVal(v)),
        };
      }),
    };
  }, [meta.isPercent, meta.yMax, meta.yMin, palette.company, palette.series, scaleVal, yearly.series, yearly.years]);

  const yearRangeLabel =
    yearly.years.length > 0
      ? `${yearly.years[0]}–${yearly.years[yearly.years.length - 1]}`
      : "2021–2025";

  const controls = (
    <div className="flex flex-wrap items-center gap-1.5">
      <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-[11px]">
        <button
          type="button"
          onClick={() => setCohort("ALL_OOS")}
          className={
            cohort === "ALL_OOS"
              ? "rounded-md bg-primary px-2 py-0.5 font-medium text-primary-foreground shadow-xs"
              : "rounded-md px-2 py-0.5 text-ink-muted hover:text-ink"
          }
        >
          Toàn bộ OOS
        </button>
        <button
          type="button"
          onClick={() => setCohort("TRUE_UNSEEN")}
          className={
            cohort === "TRUE_UNSEEN"
              ? "rounded-md bg-primary px-2 py-0.5 font-medium text-primary-foreground shadow-xs"
              : "rounded-md px-2 py-0.5 text-ink-muted hover:text-ink"
          }
        >
          DN chưa từng thấy
        </button>
      </div>
      <Select
        aria-label="Chọn chỉ số đánh giá Walk-Forward"
        value={metric}
        onChange={(e) => setMetric(e.target.value as WalkForwardMetricKey)}
        className="h-7 text-xs"
      >
        {Object.entries(WALK_FORWARD_METRIC_META).map(([k, cfg]) => (
          <option key={k} value={k}>
            {cfg.label}
          </option>
        ))}
      </Select>
    </div>
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink">
            Kiểm định cuốn chiếu Walk-Forward ({yearRangeLabel}) — So sánh 3 mô hình trên tập giao mẫu
          </h2>
          <p className="text-xs text-ink-muted">
            Thanh sai số thể hiện khoảng tin cậy Bootstrap 95% theo cụm doanh nghiệp
          </p>
        </div>
        {controls}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <EChartBox
          title={`So sánh gộp ngoài mẫu (${yearRangeLabel}): ${meta.label}`}
          subtitle={
            useTrueUnseen
              ? "Lát cắt doanh nghiệp hoàn toàn chưa xuất hiện trong tập huấn luyện"
              : "Toàn bộ quan sát kiểm định ngoài mẫu kèm khoảng tin cậy Bootstrap 95%"
          }
          sourceCaption="Nguồn: Báo cáo kiểm định Model Card lưu trong hệ thống · FinIntel"
          option={comparisonOption}
          height={290}
          isEmpty={comparison.length === 0}
          tableHeaders={["Mô hình", "Ước lượng gộp", "CI 95% (Thấp – Cao)", "TB theo năm", "Số cụm DN"]}
          tableRows={comparison.map((row) => [
            row.modelLabel,
            formatMetricValue(row.point),
            row.ciLow !== null && row.ciHigh !== null
              ? `${formatMetricValue(row.ciLow)} – ${formatMetricValue(row.ciHigh)}`
              : "N/A",
            formatMetricValue(row.yearlyMean),
            formatNumber(row.nClusters, 0),
          ])}
        />

        <EChartBox
          title={`Diễn biến từng năm Walk-Forward (${yearRangeLabel})`}
          subtitle={meta.label}
          sourceCaption="Nguồn: Báo cáo kiểm định Model Card lưu trong hệ thống · FinIntel"
          option={yearlyOption}
          height={290}
          isEmpty={yearly.years.length === 0}
          tableHeaders={["Năm kiểm định", ...yearly.series.map((s) => s.modelLabel)]}
          tableRows={yearly.years.map((yr, yrIdx) => [
            yr,
            ...yearly.series.map((s) => formatMetricValue(s.values[yrIdx] ?? null)),
          ])}
        />
      </div>
    </div>
  );
}

export function TierReliabilityChart({
  reliability,
}: {
  reliability: RiskTierReliabilityReportItem;
}) {
  const { palette } = useChartTheme();
  const rows = React.useMemo(() => buildTierReliabilityData(reliability), [reliability]);

  const option = React.useMemo<EChartsOption>(() => {
    return {
      grid: { left: 56, right: 20, top: 38, bottom: 38 },
      legend: { top: 4, right: 8 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const row = rows[first?.dataIndex ?? -1];
          if (!row) return "";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${row.tierLabel}</div>`,
            `<div>Tỷ lệ biến cố thực tế: <b>${row.empiricalRatePct !== null ? formatNumber(row.empiricalRatePct, 2) + "%" : "N/A"}</b></div>`,
            `<div>1Y PD dự báo trung bình: <b>${row.predictedPdPct !== null ? formatNumber(row.predictedPdPct, 2) + "%" : "N/A"}</b></div>`,
            `<div>Số quan sát: ${formatNumber(row.count, 0)} (${formatNumber(row.positiveCount, 0)} biến cố)</div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: rows.map((r) => r.tier),
      },
      yAxis: {
        type: "value",
        name: "Tỷ lệ (%)",
        min: 0,
        axisLabel: { formatter: "{value}%" },
      },
      series: [
        {
          name: "Tỷ lệ biến cố thực tế (%)",
          type: "bar",
          barMaxWidth: 28,
          itemStyle: { color: palette.riskHigh, borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) =>
            r.empiricalRatePct !== null ? Number(r.empiricalRatePct.toFixed(2)) : null,
          ),
        },
        {
          name: "1Y PD dự báo trung bình (%)",
          type: "bar",
          barMaxWidth: 28,
          itemStyle: { color: palette.company, borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) =>
            r.predictedPdPct !== null ? Number(r.predictedPdPct.toFixed(2)) : null,
          ),
        },
      ],
    };
  }, [palette.company, palette.riskHigh, rows]);

  const monotoneText = reliability.is_monotone_non_decreasing
    ? "Đơn điệu tăng từ R1 đến R5 (đạt chuẩn hiệu chuẩn)"
    : "Có dao động cục bộ ở bậc ít mẫu";

  return (
    <EChartBox
      title={`Độ tin cậy phân tầng tín nhiệm R1 → R5 (${monotoneText})`}
      subtitle={`Nguồn đánh giá: ${formatEvaluationSourceLabel(reliability.evaluation_source)} · Đơn vị: %`}
      sourceCaption="Nguồn: Báo cáo hiệu chuẩn phân tầng Model Card · FinIntel"
      option={option}
      height={300}
      isEmpty={rows.length === 0}
      tableHeaders={[
        "Bậc tín nhiệm",
        "Số quan sát",
        "Tỷ trọng",
        "Số biến cố thực tế",
        "Tỷ lệ biến cố thực tế",
        "1Y PD dự báo TB",
      ]}
      tableRows={rows.map((r) => [
        r.tierLabel,
        formatNumber(r.count, 0),
        `${formatNumber(r.sharePct, 1)}%`,
        formatNumber(r.positiveCount, 0),
        r.empiricalRatePct !== null ? `${formatNumber(r.empiricalRatePct, 2)}%` : "N/A",
        r.predictedPdPct !== null ? `${formatNumber(r.predictedPdPct, 2)}%` : "N/A",
      ])}
    />
  );
}
