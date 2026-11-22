"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { ALTMAN_ZONE_META } from "@/lib/domain-labels";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent } from "@/lib/format";
import type { ScreenerItem } from "@/types/api";

interface ScatterPoint {
  value: [number, number];
  ticker: string;
  companyName: string;
  sector: string;
  rawZ: number;
  prob: number;
  zoneLabel: string;
  redFlags: number;
}

function clampZ(z: number): number {
  return Math.max(-6, Math.min(12, z));
}

export function ScreenerScatterChart({
  items,
  onSelectTicker,
}: {
  items: readonly ScreenerItem[];
  onSelectTicker: (ticker: string) => void;
}) {
  const { palette } = useChartTheme();

  const plottable = React.useMemo(
    () =>
      items.filter(
        (it) =>
          typeof it.altman_z_double_prime === "number" &&
          Number.isFinite(it.altman_z_double_prime) &&
          typeof it.calibrated_distress_prob === "number" &&
          Number.isFinite(it.calibrated_distress_prob),
      ),
    [items],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const groups: Record<
      "SAFE_ZONE" | "GREY_ZONE" | "DISTRESS_ZONE" | "OTHER",
      ScatterPoint[]
    > = {
      SAFE_ZONE: [],
      GREY_ZONE: [],
      DISTRESS_ZONE: [],
      OTHER: [],
    };

    for (const it of plottable) {
      const z = it.altman_z_double_prime ?? 0;
      const prob = it.calibrated_distress_prob ?? 0;
      const key =
        it.altman_zone === "SAFE_ZONE" ||
        it.altman_zone === "GREY_ZONE" ||
        it.altman_zone === "DISTRESS_ZONE"
          ? it.altman_zone
          : "OTHER";
      groups[key].push({
        value: [clampZ(z), Number((prob * 100).toFixed(2))],
        ticker: it.ticker,
        companyName: it.company_name_vn ?? it.ticker,
        sector: it.icb_name_level2 ?? "Khác",
        rawZ: z,
        prob,
        zoneLabel:
          (it.altman_zone && ALTMAN_ZONE_META[it.altman_zone]?.label) ||
          "Chưa phân vùng",
        redFlags: it.red_flag_count,
      });
    }

    const tooltipFormatter = (raw: unknown) => {
      const p = raw as { data?: ScatterPoint };
      const d = p.data;
      if (!d) return "";
      return [
        `<div style="font-weight:600;margin-bottom:3px">${d.ticker} · ${d.companyName}</div>`,
        `<div style="color:${palette.inkMuted};font-size:11px;margin-bottom:4px">${d.sector}</div>`,
        `<div>Altman Z″: <b>${formatNumber(d.rawZ, 2)}</b> (${d.zoneLabel})</div>`,
        `<div>Xác suất kiệt quệ: <b>${formatPercent(d.prob)}</b></div>`,
        `<div>Cờ đỏ kế toán: <b>${d.redFlags}</b></div>`,
        `<div style="color:${palette.company};font-size:11px;margin-top:3px">Nhấp để mở hồ sơ ${d.ticker} →</div>`,
      ].join("");
    };

    return {
      grid: { left: 56, right: 24, top: 40, bottom: 44 },
      legend: {
        top: 4,
        right: 12,
      },
      tooltip: {
        trigger: "item",
        formatter: tooltipFormatter,
      },
      xAxis: {
        type: "value",
        name: "Altman Z″ (giới hạn hiển thị [-6; 12])",
        nameLocation: "middle",
        nameGap: 26,
        min: -6,
        max: 12,
      },
      yAxis: {
        type: "value",
        name: "XS kiệt quệ (%)",
        min: 0,
        axisLabel: { formatter: "{value}%" },
      },
      series: [
        {
          name: "An toàn (> 2,60)",
          type: "scatter",
          symbolSize: 8,
          itemStyle: { color: palette.riskLow, opacity: 0.88 },
          data: groups.SAFE_ZONE,
        },
        {
          name: "Vùng xám (1,10–2,60)",
          type: "scatter",
          symbolSize: 8,
          itemStyle: { color: palette.riskMid, opacity: 0.88 },
          data: groups.GREY_ZONE,
        },
        {
          name: "Nguy cơ (< 1,10)",
          type: "scatter",
          symbolSize: 9,
          itemStyle: { color: palette.riskHigh, opacity: 0.92 },
          data: groups.DISTRESS_ZONE,
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { type: "dashed", color: palette.inkSubtle, width: 1 },
            label: { fontSize: 10, color: palette.inkMuted },
            data: [
              { xAxis: 1.1, label: { formatter: "1,10" } },
              { xAxis: 2.6, label: { formatter: "2,60" } },
            ],
          },
        },
        ...(groups.OTHER.length > 0
          ? [
              {
                name: "Khác",
                type: "scatter" as const,
                symbolSize: 7,
                itemStyle: { color: palette.neutral, opacity: 0.72 },
                data: groups.OTHER,
              },
            ]
          : []),
      ],
    };
  }, [palette, plottable]);

  const tableHeaders = [
    "Mã",
    "Ngành",
    "Altman Z″",
    "Phân vùng",
    "XS kiệt quệ",
    "Cờ đỏ",
  ];
  const tableRows = plottable.map((it) => [
    it.ticker,
    it.icb_name_level2 ?? "—",
    formatNumber(it.altman_z_double_prime, 2),
    (it.altman_zone && ALTMAN_ZONE_META[it.altman_zone]?.shortLabel) || "N/A",
    formatPercent(it.calibrated_distress_prob),
    String(it.red_flag_count),
  ]);

  return (
    <EChartBox
      title="Tương quan Altman Z″ và Xác suất kiệt quệ (trang hiện tại)"
      subtitle="Góc trên bên trái thể hiện nhóm vừa có Z″ thấp vừa có xác suất kiệt quệ mô hình cao · Nhấp vào điểm để mở hồ sơ"
      option={option}
      height={380}
      isEmpty={plottable.length === 0}
      emptyMessage="Không có doanh nghiệp nào có đủ cặp giá trị Altman Z″ và xác suất kiệt quệ trong kết quả lọc hiện tại."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        const d = p.data as { ticker?: string } | undefined;
        if (d?.ticker) onSelectTicker(d.ticker);
      }}
    />
  );
}
