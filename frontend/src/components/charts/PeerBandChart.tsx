"use client";

import * as React from "react";
import * as echarts from "echarts";
import { AlertTriangle } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { formatPeerGroupLabel } from "@/lib/domain-labels";
import {
  formatNumber,
  formatPeriodLabel,
  formatRatioValue,
  type RatioFormatType,
} from "@/lib/format";
import type { PeerBandHistoryPoint, PeriodType } from "@/types/api";

export function PeerBandChart({
  metricLabel,
  formatType,
  periodType,
  history,
  ticker,
}: {
  metricLabel: string;
  formatType: RatioFormatType;
  periodType: PeriodType;
  history: readonly PeerBandHistoryPoint[];
  ticker: string;
}) {
  const sorted = React.useMemo(
    () =>
      [...history].sort((a, b) =>
        a.period_end_date.localeCompare(b.period_end_date),
      ),
    [history],
  );

  const hasFallbackPeriods = React.useMemo(
    () =>
      sorted.some(
        (pt) =>
          pt.peer_group_level === "REGIME_FALLBACK" ||
          (pt.peer_count !== null && pt.peer_count !== undefined && pt.peer_count < 5),
      ),
    [sorted],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const labels = sorted.map((p) =>
      formatPeriodLabel(p.period_end_date, periodType),
    );
    const bandTuples = sorted.map((p, idx) => [
      idx,
      p.peer_p25 ?? null,
      p.peer_p75 ?? null,
    ]);

    const markAreas: [{ xAxis: number }, { xAxis: number }][] = [];
    sorted.forEach((pt, idx) => {
      const isWarn =
        pt.peer_group_level === "REGIME_FALLBACK" ||
        (pt.peer_count !== null && pt.peer_count !== undefined && pt.peer_count < 5);
      if (isWarn) {
        markAreas.push([{ xAxis: Math.max(0, idx - 0.45) }, { xAxis: idx + 0.45 }]);
      }
    });

    const formatAxisVal = (val: number) => {
      if (formatType === "PERCENT") return `${formatNumber(val * 100, 0)}%`;
      if (formatType === "CURRENCY_PER_SHARE") return formatNumber(val, 0);
      return formatNumber(val, 1);
    };

    return {
      animation: false,
      grid: { left: 56, right: 20, top: 34, bottom: 38 },
      legend: {
        top: 2,
        right: 8,
        textStyle: { color: "#5B6068", fontSize: 11 },
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#FFFFFF",
        borderColor: "#DAD7D0",
        textStyle: { color: "#1A1D21", fontSize: 12 },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const idx = first?.dataIndex ?? -1;
          const pt = sorted[idx];
          if (!pt) return "";
          const peerMeta = formatPeerGroupLabel(
            pt.peer_group_level,
            pt.peer_count ?? 0,
          );
          return [
            `<div style="font-weight:600;margin-bottom:4px">${formatPeriodLabel(pt.period_end_date, periodType)}</div>`,
            `<div>${ticker}: <b>${formatRatioValue(pt.value, formatType)}</b></div>`,
            `<div>P75 / Trung vị / P25: <span style="font-family:monospace">${formatRatioValue(pt.peer_p75, formatType)} / ${formatRatioValue(pt.peer_median, formatType)} / ${formatRatioValue(pt.peer_p25, formatType)}</span></div>`,
            `<div>Phân vị ngành: <b>${pt.percentile_rank !== null && pt.percentile_rank !== undefined ? `${formatNumber(pt.percentile_rank, 0)}%` : "N/A"}</b></div>`,
            `<div style="color:#5B6068;font-size:11px;margin-top:3px">${peerMeta.text}</div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLine: { lineStyle: { color: "#DAD7D0" } },
        axisLabel: { color: "#5B6068", fontSize: 11 },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "#EFEDE8" } },
        axisLabel: {
          color: "#5B6068",
          fontSize: 11,
          formatter: (val: number) => formatAxisVal(val),
        },
      },
      series: [
        {
          name: "Dải P25–P75 ngành",
          type: "custom",
          silent: true,
          z: 1,
          encode: { x: 0, y: [1, 2] },
          data: bandTuples,
          renderItem: (params, api) => {
            if (params.dataIndex !== 0) return null;
            const children: NonNullable<echarts.CustomSeriesRenderItemReturn>[] = [];
            let seg: { idx: number; p25: number; p75: number }[] = [];

            const flush = () => {
              if (seg.length >= 2) {
                const upper = seg.map((s) => api.coord([s.idx, s.p75]));
                const lower = [...seg].reverse().map((s) => api.coord([s.idx, s.p25]));
                children.push({
                  type: "polygon",
                  shape: { points: [...upper, ...lower] },
                  style: { fill: "rgba(91, 96, 104, 0.14)" },
                });
              }
              seg = [];
            };

            sorted.forEach((pt, idx) => {
              if (
                typeof pt.peer_p25 === "number" &&
                Number.isFinite(pt.peer_p25) &&
                typeof pt.peer_p75 === "number" &&
                Number.isFinite(pt.peer_p75)
              ) {
                seg.push({ idx, p25: pt.peer_p25, p75: pt.peer_p75 });
              } else {
                flush();
              }
            });
            flush();

            const coordSys = params.coordSys as unknown as {
              x: number;
              y: number;
              width: number;
              height: number;
            };
            return {
              type: "group",
              clipPath: {
                type: "rect",
                shape: {
                  x: coordSys.x,
                  y: coordSys.y,
                  width: coordSys.width,
                  height: coordSys.height,
                },
              },
              children,
            };
          },
          markArea:
            markAreas.length > 0
              ? {
                  silent: true,
                  itemStyle: { color: "rgba(183, 121, 31, 0.10)" },
                  data: markAreas,
                }
              : undefined,
        },
        {
          name: "Trung vị ngành",
          type: "line",
          z: 2,
          showSymbol: false,
          lineStyle: { color: "#5B6068", width: 1.5, type: "dashed" },
          itemStyle: { color: "#5B6068" },
          data: sorted.map((p) => p.peer_median ?? null),
        },
        {
          name: ticker,
          type: "line",
          z: 3,
          symbolSize: 6,
          lineStyle: { color: "#1F4E79", width: 2.5 },
          itemStyle: { color: "#1F4E79" },
          data: sorted.map((p) => p.value ?? null),
        },
      ],
    };
  }, [formatType, periodType, sorted, ticker]);

  const tableHeaders = [
    "Kỳ",
    ticker,
    "P25 ngành",
    "Trung vị ngành",
    "P75 ngành",
    "Phân vị",
    "Số DN nhóm",
  ];
  const tableRows = sorted.map((p) => [
    formatPeriodLabel(p.period_end_date, periodType),
    formatRatioValue(p.value, formatType),
    formatRatioValue(p.peer_p25, formatType),
    formatRatioValue(p.peer_median, formatType),
    formatRatioValue(p.peer_p75, formatType),
    p.percentile_rank !== null && p.percentile_rank !== undefined
      ? `${formatNumber(p.percentile_rank, 0)}%`
      : "N/A",
    String(p.peer_count ?? 0),
  ]);

  return (
    <div className="space-y-2">
      <EChartBox
        title={`${metricLabel} so với dải P25–P75 cùng ngành`}
        subtitle={`Đường liền nét: ${ticker} · Đường đứt nét: Trung vị ngành · Vùng xám: khoảng tứ phân vị P25–P75`}
        option={option}
        height={320}
        isEmpty={sorted.length === 0}
        emptyMessage="Không có chuỗi lịch sử so sánh ngành cho chỉ số này."
        tableHeaders={tableHeaders}
        tableRows={tableRows}
      />
      {hasFallbackPeriods ? (
        <div className="flex items-center gap-2 rounded border border-risk-mid/30 bg-risk-mid-soft px-3 py-1.5 text-xs text-risk-mid">
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
          <span>
            Vùng tô vàng nhạt đánh dấu các kỳ có nhóm ngành ít hơn 5 doanh nghiệp hoặc phải mở rộng ra toàn thị trường cùng chuẩn mực.
          </span>
        </div>
      ) : null}
    </div>
  );
}
