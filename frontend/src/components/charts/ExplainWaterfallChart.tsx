"use client";

import * as React from "react";
import { RotateCcw } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Button } from "@/components/ui/button";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import {
  formatNumber,
  formatPercent,
  formatRatioByType,
  MINUS_SIGN,
} from "@/lib/format";
import { buildShapNarrativeVi } from "@/lib/narrative";
import type {
  RatioDictionaryItem,
  RatioName,
  ShapExplanationItem,
} from "@/types/api";

export function resolveCrossFilteredMlScore<T extends { period_end_date: string }>(
  scores: readonly T[],
  selectedPeriodEndDate: string | null | undefined,
  globalPeriodEndDate: string | null | undefined,
): {
  active: T | null;
  defaultLatest: T | null;
  prev: T | null;
  isCustomSelected: boolean;
} {
  if (scores.length === 0) {
    return { active: null, defaultLatest: null, prev: null, isCustomSelected: false };
  }
  const sorted = [...scores].sort((a, b) =>
    a.period_end_date.localeCompare(b.period_end_date),
  );
  let defaultIdx = sorted.length - 1;
  if (globalPeriodEndDate) {
    const exact = sorted.findIndex((m) => m.period_end_date === globalPeriodEndDate);
    if (exact >= 0) {
      defaultIdx = exact;
    } else {
      const le = sorted.reduce(
        (best, m, i) => (m.period_end_date <= globalPeriodEndDate ? i : best),
        -1,
      );
      if (le >= 0) defaultIdx = le;
    }
  }
  const defaultLatest = sorted[defaultIdx] ?? null;

  let activeIdx = defaultIdx;
  if (selectedPeriodEndDate) {
    const pickedIdx = sorted.findIndex(
      (m) => m.period_end_date === selectedPeriodEndDate,
    );
    if (pickedIdx >= 0) {
      activeIdx = pickedIdx;
    }
  }
  const active = sorted[activeIdx] ?? null;
  const prev = activeIdx > 0 ? sorted[activeIdx - 1] ?? null : null;
  const isCustomSelected = Boolean(
    active && defaultLatest && active.period_end_date !== defaultLatest.period_end_date,
  );
  return { active, defaultLatest, prev, isCustomSelected };
}

export function resolveMlFeatureFormatType(
  feature: string,
  ratioDictMap?: ReadonlyMap<RatioName, RatioDictionaryItem>,
): string {
  const dictEntry = ratioDictMap?.get(feature as RatioName);
  if (dictEntry?.format_type) {
    return dictEntry.format_type;
  }
  const lowered = feature.toLowerCase();
  if (lowered.startsWith("sector_z_")) return "ZSCORE";
  if (lowered === "merton_dtd") return "SIGMA";
  if (lowered === "piotroski_f_score") return "SCORE_9";
  if (
    lowered === "overdue_or_late_count_4q" ||
    lowered === "consecutive_loss_quarters"
  ) {
    return "QUARTERS";
  }
  if (lowered === "cross_sectional_asset_size_pct" || lowered.endsWith("_pct")) {
    return "PERCENTILE";
  }
  if (
    lowered === "delta_roa_4q" ||
    lowered === "delta_liabilities_to_assets_4q"
  ) {
    return "PERCENT_POINTS";
  }
  if (lowered === "delta_current_ratio_4q") {
    return "NUMBER";
  }
  if (
    lowered.includes("margin") ||
    lowered.includes("roa") ||
    lowered.includes("roe") ||
    lowered.includes("roic") ||
    lowered.includes("to_assets") ||
    lowered.includes("growth") ||
    lowered.includes("fcf_to_revenue") ||
    lowered.includes("distortion") ||
    lowered.includes("core_to_ebt") ||
    lowered === "equity_volatility_1y" ||
    lowered === "naive_edf"
  ) {
    return "PERCENT";
  }
  if (
    lowered.includes("dso") ||
    lowered.includes("dio") ||
    lowered.includes("dpo") ||
    lowered.includes("cycle")
  ) {
    return "DAYS";
  }
  if (
    lowered.includes("turnover") ||
    lowered.includes("coverage") ||
    lowered.includes("to_equity") ||
    lowered.includes("to_ebitda") ||
    lowered.includes("current_ratio") ||
    lowered.includes("quick_ratio") ||
    lowered.includes("cash_ratio") ||
    lowered.includes("ocf_to_")
  ) {
    return "MULTIPLE";
  }
  if (lowered === "eps_ttm_estimated") {
    return "CURRENCY_PER_SHARE";
  }
  return "NUMBER";
}

interface ShapBarStep {
  label: string;
  delta: number;
  kind: "increase" | "decrease" | "other";
  value: number | null;
  peerMedian: number | null;
  feature: string;
  formatType: string;
}

function formatSignedLogOdds(val: number): string {
  const formatted = formatNumber(Math.abs(val), 2);
  if (Math.abs(val) < 0.005) return "0,00";
  return val >= 0 ? `+${formatted}` : `${MINUS_SIGN}${formatted}`;
}

export function ExplainWaterfallChart({
  shap,
  distressProb,
  periodLabel,
  isCustomPeriod = false,
  onResetToLatest,
  ratioDictMap,
}: {
  shap: ShapExplanationItem | null | undefined;
  distressProb?: number | null;
  periodLabel?: string | null;
  isCustomPeriod?: boolean;
  onResetToLatest?: () => void;
  ratioDictMap?: ReadonlyMap<RatioName, RatioDictionaryItem>;
}) {
  const { palette } = useChartTheme();

  const steps = React.useMemo<ShapBarStep[]>(() => {
    if (!shap) return [];
    const result: ShapBarStep[] = [];

    const riskUp = [...shap.top_risk_increasing]
      .filter((drv) => Number.isFinite(drv.shap_delta) && Math.abs(drv.shap_delta) >= 0.005)
      .sort((a, b) => b.shap_delta - a.shap_delta)
      .slice(0, 5);
    for (const drv of riskUp) {
      result.push({
        label: drv.display_name_vn,
        delta: drv.shap_delta,
        kind: "increase",
        value: drv.value ?? null,
        peerMedian: drv.peer_median ?? null,
        feature: drv.feature,
        formatType: resolveMlFeatureFormatType(drv.feature, ratioDictMap),
      });
    }

    const riskDown = [...shap.top_risk_decreasing]
      .filter((drv) => Number.isFinite(drv.shap_delta) && Math.abs(drv.shap_delta) >= 0.005)
      .sort((a, b) => a.shap_delta - b.shap_delta)
      .slice(0, 5);
    for (const drv of riskDown) {
      result.push({
        label: drv.display_name_vn,
        delta: drv.shap_delta,
        kind: "decrease",
        value: drv.value ?? null,
        peerMedian: drv.peer_median ?? null,
        feature: drv.feature,
        formatType: resolveMlFeatureFormatType(drv.feature, ratioDictMap),
      });
    }

    if (Math.abs(shap.other_features_delta) >= 0.005) {
      result.push({
        label: "Các biến số còn lại",
        delta: shap.other_features_delta,
        kind: "other",
        value: null,
        peerMedian: null,
        feature: "other_features",
        formatType: "NUMBER",
      });
    }

    return result;
  }, [ratioDictMap, shap]);

  const narratives = React.useMemo(() => buildShapNarrativeVi(shap), [shap]);

  const option = React.useMemo<EChartsOption>(() => {
    const categories = steps.map((s) => s.label);

    return {
      grid: { left: 12, right: 24, top: 14, bottom: 38, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const idx = first?.dataIndex ?? -1;
          const step = steps[idx];
          if (!step) return "";
          const lines = [
            `<div style="font-weight:600;margin-bottom:4px">${step.label}</div>`,
            `<div>Đóng góp: <b>${formatSignedLogOdds(step.delta)} log-odds</b></div>`,
          ];
          if (step.value !== null) {
            lines.push(
              `<div>Giá trị DN: <b>${formatRatioByType(step.value, step.formatType)}</b></div>`,
            );
          }
          if (step.peerMedian !== null) {
            lines.push(
              `<div>Trung vị ngành: <b>${formatRatioByType(step.peerMedian, step.formatType)}</b></div>`,
            );
          }
          return lines.join("");
        },
      },
      xAxis: {
        type: "value",
        name: "Đóng góp (log-odds)",
        nameLocation: "middle",
        nameGap: 24,
        axisLabel: {
          formatter: (val: number) => formatChartNumber(val, 2, { trimTrailingZeros: true }),
        },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: categories,
        axisLabel: {
          width: 165,
          overflow: "truncate",
          ellipsis: "…",
        },
      },
      series: [
        {
          name: "TreeSHAP log-odds",
          type: "bar",
          barMaxWidth: 18,
          data: steps.map((step) => ({
            value: step.delta,
            itemStyle: {
              color:
                step.kind === "other"
                  ? palette.waterfall.other
                  : step.delta >= 0
                    ? palette.riskHigh
                    : palette.riskLow,
              borderRadius: step.delta >= 0 ? [0, 3, 3, 0] : [3, 0, 0, 3],
            },
          })),
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: palette.borderStrong, type: "solid", width: 1.5 },
            label: { show: false },
            data: [{ xAxis: 0 }],
          },
        },
      ],
    };
  }, [palette.borderStrong, palette.riskHigh, palette.riskLow, palette.waterfall.other, steps]);

  const tableHeaders = ["Yếu tố (TreeSHAP)", "Giá trị DN", "Trung vị ngành", "Đóng góp (log-odds)"];
  const tableRows = steps.map((s) => [
    s.label,
    s.value !== null ? formatRatioByType(s.value, s.formatType) : "—",
    s.peerMedian !== null ? formatRatioByType(s.peerMedian, s.formatType) : "—",
    formatSignedLogOdds(s.delta),
  ]);

  const probSubtitle =
    distressProb !== null && distressProb !== undefined
      ? `Xác suất kiệt quệ đã hiệu chỉnh: ${formatPercent(distressProb)} · Cơ sở ${shap ? formatSignedLogOdds(shap.base_value) : "N/A"} → Dự báo ${shap ? formatSignedLogOdds(shap.prediction_score) : "N/A"} log-odds`
      : "Đóng góp từng yếu tố theo thang log-odds của mô hình TreeSHAP";

  return (
    <div className="space-y-3">
      <EChartBox
        title={
          periodLabel
            ? `Các yếu tố dẫn dắt điểm rủi ro mô hình (${periodLabel})`
            : "Các yếu tố dẫn dắt điểm rủi ro mô hình"
        }
        subtitle={probSubtitle}
        option={option}
        height={310}
        isEmpty={steps.length === 0}
        emptyMessage="Chưa có dữ liệu giải thích TreeSHAP cho kỳ và phạm vi báo cáo đang chọn."
        tableHeaders={tableHeaders}
        tableRows={tableRows}
        headerRight={
          periodLabel ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={
                  isCustomPeriod
                    ? "rounded-full border border-accent-ink/30 bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink"
                    : "rounded-full border border-border bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-ink-muted"
                }
              >
                {isCustomPeriod ? `Đang xem: ${periodLabel}` : `Kỳ: ${periodLabel}`}
              </span>
              {isCustomPeriod && onResetToLatest ? (
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={onResetToLatest}
                >
                  <RotateCcw className="size-3" />
                  <span>Về kỳ mới nhất</span>
                </Button>
              ) : null}
            </div>
          ) : null
        }
      />

      {narratives.length > 0 ? (
        <div className="rounded-lg border border-border bg-surface p-3.5 shadow-xs">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <h3 className="font-semibold text-ink">
              Diễn giải định lượng theo quy tắc (TreeSHAP)
            </h3>
            <div className="flex items-center gap-3 text-[11px] text-ink-muted">
              <span className="inline-flex items-center gap-1">
                <span className="size-2 rounded-sm bg-risk-high" /> Tăng rủi ro
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="size-2 rounded-sm bg-risk-low" /> Giảm rủi ro
              </span>
            </div>
          </div>
          <ul className="space-y-1.5 text-[13px] leading-5 text-ink">
            {narratives.map((sentence, idx) => (
              <li key={idx} className="flex items-start gap-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent-ink" />
                <span>{sentence}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
