"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { formatNumber, formatPercent, MINUS_SIGN } from "@/lib/format";
import { buildShapNarrativeVi } from "@/lib/narrative";
import type { ShapExplanationItem } from "@/types/api";

interface WaterfallStep {
  label: string;
  shortLabel: string;
  start: number;
  end: number;
  delta: number;
  kind: "base" | "increase" | "decrease" | "other" | "final";
  value: number | null;
  peerMedian: number | null;
  feature: string;
}

const STEP_COLORS: Record<WaterfallStep["kind"], string> = {
  base: "#5B6068",
  increase: "#A61B1B",
  decrease: "#2E7D5B",
  other: "#868B93",
  final: "#1F4E79",
};

function formatSignedLogOdds(val: number): string {
  const formatted = formatNumber(Math.abs(val), 2);
  if (Math.abs(val) < 0.005) return "0,00";
  return val >= 0 ? `+${formatted}` : `${MINUS_SIGN}${formatted}`;
}

export function ExplainWaterfallChart({
  shap,
  distressProb,
}: {
  shap: ShapExplanationItem | null | undefined;
  distressProb?: number | null;
}) {
  const steps = React.useMemo<WaterfallStep[]>(() => {
    if (!shap) return [];
    const result: WaterfallStep[] = [];
    let running = shap.base_value;

    result.push({
      label: "Điểm cơ sở thị trường",
      shortLabel: "Cơ sở",
      start: 0,
      end: shap.base_value,
      delta: shap.base_value,
      kind: "base",
      value: null,
      peerMedian: null,
      feature: "base_value",
    });

    const riskUp = [...shap.top_risk_increasing]
      .sort((a, b) => b.shap_delta - a.shap_delta)
      .slice(0, 5);
    for (const drv of riskUp) {
      const start = running;
      const end = start + drv.shap_delta;
      running = end;
      result.push({
        label: drv.display_name_vn,
        shortLabel:
          drv.display_name_vn.length > 20
            ? `${drv.display_name_vn.slice(0, 18)}…`
            : drv.display_name_vn,
        start,
        end,
        delta: drv.shap_delta,
        kind: "increase",
        value: drv.value ?? null,
        peerMedian: drv.peer_median ?? null,
        feature: drv.feature,
      });
    }

    const riskDown = [...shap.top_risk_decreasing]
      .sort((a, b) => a.shap_delta - b.shap_delta)
      .slice(0, 5);
    for (const drv of riskDown) {
      const start = running;
      const end = start + drv.shap_delta;
      running = end;
      result.push({
        label: drv.display_name_vn,
        shortLabel:
          drv.display_name_vn.length > 20
            ? `${drv.display_name_vn.slice(0, 18)}…`
            : drv.display_name_vn,
        start,
        end,
        delta: drv.shap_delta,
        kind: "decrease",
        value: drv.value ?? null,
        peerMedian: drv.peer_median ?? null,
        feature: drv.feature,
      });
    }

    if (Math.abs(shap.other_features_delta) >= 0.005) {
      const start = running;
      const end = start + shap.other_features_delta;
      running = end;
      result.push({
        label: "Các biến số còn lại",
        shortLabel: "Biến khác",
        start,
        end,
        delta: shap.other_features_delta,
        kind: "other",
        value: null,
        peerMedian: null,
        feature: "other_features",
      });
    }

    result.push({
      label: "Điểm dự báo (log-odds)",
      shortLabel: "Dự báo",
      start: 0,
      end: shap.prediction_score,
      delta: shap.prediction_score,
      kind: "final",
      value: null,
      peerMedian: null,
      feature: "prediction_score",
    });

    return result;
  }, [shap]);

  const narratives = React.useMemo(() => buildShapNarrativeVi(shap), [shap]);

  const option = React.useMemo<EChartsOption>(() => {
    const categories = steps.map((s) => s.shortLabel);
    const dataRows = steps.map((s, idx) => [idx, s.start, s.end, s.delta]);

    return {
      animation: false,
      grid: { left: 56, right: 20, top: 28, bottom: 72 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: "#FFFFFF",
        borderColor: "#DAD7D0",
        textStyle: { color: "#1A1D21", fontSize: 12 },
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
            lines.push(`<div>Giá trị DN: <b>${formatNumber(step.value, 3)}</b></div>`);
          }
          if (step.peerMedian !== null) {
            lines.push(
              `<div>Trung vị ngành: <b>${formatNumber(step.peerMedian, 3)}</b></div>`,
            );
          }
          return lines.join("");
        },
      },
      xAxis: {
        type: "category",
        data: categories,
        axisLine: { lineStyle: { color: "#DAD7D0" } },
        axisLabel: {
          color: "#5B6068",
          fontSize: 11,
          rotate: 28,
          interval: 0,
        },
      },
      yAxis: {
        type: "value",
        name: "log-odds",
        nameTextStyle: { color: "#5B6068", fontSize: 11 },
        splitLine: { lineStyle: { color: "#EFEDE8" } },
        axisLabel: { color: "#5B6068", fontSize: 11 },
      },
      series: [
        {
          name: "TreeSHAP log-odds",
          type: "custom",
          encode: { x: 0, y: [1, 2] },
          data: dataRows,
          renderItem: (params, api) => {
            const idx = Number(api.value(0));
            const startVal = Number(api.value(1));
            const endVal = Number(api.value(2));
            const step = steps[idx];
            if (!step) return null;

            const pStart = api.coord([idx, startVal]);
            const pEnd = api.coord([idx, endVal]);
            const band = api.size ? (api.size([1, 0]) as number[]) : [40, 0];
            const barWidth = Math.min(36, Math.max(12, band[0] * 0.56));
            const topY = Math.min(pStart[1], pEnd[1]);
            const rawHeight = Math.abs(pEnd[1] - pStart[1]);
            const height = Math.max(2, rawHeight);

            return {
              type: "rect",
              shape: {
                x: pStart[0] - barWidth / 2,
                y: topY,
                width: barWidth,
                height,
                r: 2,
              },
              style: {
                fill: STEP_COLORS[step.kind],
              },
            };
          },
        },
      ],
    };
  }, [steps]);

  const tableHeaders = ["Yếu tố (TreeSHAP)", "Giá trị DN", "Trung vị ngành", "Đóng góp (log-odds)"];
  const tableRows = steps.map((s) => [
    s.label,
    s.value !== null ? formatNumber(s.value, 3) : "—",
    s.peerMedian !== null ? formatNumber(s.peerMedian, 3) : "—",
    formatSignedLogOdds(s.delta),
  ]);

  const probSubtitle =
    distressProb !== null && distressProb !== undefined
      ? `Xác suất kiệt quệ đã hiệu chỉnh: ${formatPercent(distressProb)} · Đơn vị thanh: log-odds trước hàm liên kết`
      : "Đóng góp từng yếu tố theo thang log-odds của mô hình TreeSHAP";

  return (
    <div className="space-y-3">
      <EChartBox
        title="Các yếu tố dẫn dắt điểm rủi ro mô hình"
        subtitle={probSubtitle}
        option={option}
        height={310}
        isEmpty={steps.length === 0}
        emptyMessage="Chưa có dữ liệu giải thích TreeSHAP cho kỳ và phạm vi báo cáo đang chọn."
        tableHeaders={tableHeaders}
        tableRows={tableRows}
      />

      {narratives.length > 0 ? (
        <div className="rounded border border-border bg-surface p-3.5">
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
