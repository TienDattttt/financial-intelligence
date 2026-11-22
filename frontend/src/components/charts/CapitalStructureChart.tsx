"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent, formatPeriodLabel, formatVndBillions } from "@/lib/format";
import type { CapitalStructurePoint, PeriodType } from "@/types/api";

export interface NormalizedCapitalPoint {
  periodEndDate: string;
  label: string;
  assetsBil: number | null;
  liabilitiesBil: number | null;
  equityBil: number | null;
  debtBil: number | null;
  assetsRaw: number | null;
  liabilitiesRaw: number | null;
  equityRaw: number | null;
  debtRaw: number | null;
}

function toBillions(raw: number | null | undefined): number | null {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return Number((raw / 1_000_000_000).toFixed(2));
}

export function transformCapitalStructureSeries(
  points: readonly CapitalStructurePoint[],
  periodType: PeriodType,
): NormalizedCapitalPoint[] {
  return [...points]
    .sort((a, b) => a.period_end_date.localeCompare(b.period_end_date))
    .map((pt) => ({
      periodEndDate: pt.period_end_date,
      label: formatPeriodLabel(pt.period_end_date, periodType),
      assetsBil: toBillions(pt.BS_TOTAL_ASSETS),
      liabilitiesBil: toBillions(pt.BS_TOTAL_LIABILITIES),
      equityBil: toBillions(pt.BS_TOTAL_EQUITY),
      debtBil: toBillions(pt.total_debt),
      assetsRaw:
        typeof pt.BS_TOTAL_ASSETS === "number" && Number.isFinite(pt.BS_TOTAL_ASSETS)
          ? pt.BS_TOTAL_ASSETS
          : null,
      liabilitiesRaw:
        typeof pt.BS_TOTAL_LIABILITIES === "number" &&
        Number.isFinite(pt.BS_TOTAL_LIABILITIES)
          ? pt.BS_TOTAL_LIABILITIES
          : null,
      equityRaw:
        typeof pt.BS_TOTAL_EQUITY === "number" && Number.isFinite(pt.BS_TOTAL_EQUITY)
          ? pt.BS_TOTAL_EQUITY
          : null,
      debtRaw:
        typeof pt.total_debt === "number" && Number.isFinite(pt.total_debt)
          ? pt.total_debt
          : null,
    }));
}

export function buildCapitalStructureTitle(
  rows: readonly NormalizedCapitalPoint[],
): string {
  if (rows.length === 0) {
    return "Cơ cấu Tài sản, Nợ phải trả và Vốn chủ sở hữu";
  }
  const latest = rows[rows.length - 1];
  const assetsStr =
    latest.assetsRaw !== null ? `${formatVndBillions(latest.assetsRaw)} tỷ` : "N/A";
  if (
    latest.liabilitiesRaw !== null &&
    latest.assetsRaw !== null &&
    latest.assetsRaw > 0
  ) {
    const liabRatio = latest.liabilitiesRaw / latest.assetsRaw;
    return `Cơ cấu vốn ${latest.label}: Tổng TS ${assetsStr} · Nợ/Tổng TS ${formatPercent(liabRatio)}`;
  }
  return `Cơ cấu vốn ${latest.label}: Tổng Tài sản ${assetsStr}`;
}

export function CapitalStructureChart({
  ticker,
  points,
  periodType,
}: {
  ticker: string;
  points: readonly CapitalStructurePoint[];
  periodType: PeriodType;
}) {
  const { palette } = useChartTheme();

  const rows = React.useMemo(
    () => transformCapitalStructureSeries(points, periodType),
    [periodType, points],
  );

  const chartTitle = React.useMemo(
    () => buildCapitalStructureTitle(rows),
    [rows],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const labels = rows.map((r) => r.label);
    const startIdx = Math.max(0, rows.length - 12);
    const startPct = rows.length > 0 ? Math.round((startIdx / rows.length) * 100) : 0;

    return {
      grid: { left: 60, right: 18, top: 34, bottom: 58 },
      legend: {
        top: 2,
        right: 8,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const row = rows[first?.dataIndex ?? -1];
          if (!row) return "";
          const debtToEquity =
            row.debtRaw !== null && row.equityRaw !== null && row.equityRaw > 0
              ? `${formatNumber(row.debtRaw / row.equityRaw, 2)}x`
              : "N/A";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${ticker} · ${row.label}</div>`,
            `<div>Tổng tài sản: <b>${formatVndBillions(row.assetsRaw)} tỷ</b></div>`,
            `<div>Vốn chủ sở hữu: <b>${formatVndBillions(row.equityRaw, { accountingNegative: true })} tỷ</b></div>`,
            `<div>Nợ phải trả: <b>${formatVndBillions(row.liabilitiesRaw)} tỷ</b></div>`,
            `<div>Nợ vay tài chính: <b>${formatVndBillions(row.debtRaw)} tỷ</b> (Nợ vay/VCSH: ${debtToEquity})</div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: labels,
      },
      yAxis: {
        type: "value",
        name: "tỷ đồng",
        axisLabel: {
          formatter: (val: number) => formatNumber(val, 0),
        },
      },
      dataZoom: [
        { type: "inside", start: startPct, end: 100 },
        {
          type: "slider",
          height: 18,
          bottom: 8,
          start: startPct,
          end: 100,
          borderColor: palette.border,
          fillerColor: palette.peerBandFill,
          textStyle: { color: palette.inkMuted, fontSize: 10 },
        },
      ],
      series: [
        {
          name: "Vốn chủ sở hữu",
          type: "bar",
          stack: "capital",
          barMaxWidth: 26,
          itemStyle: { color: palette.riskLow },
          data: rows.map((r) => r.equityBil),
        },
        {
          name: "Nợ phải trả",
          type: "bar",
          stack: "capital",
          barMaxWidth: 26,
          itemStyle: { color: palette.riskMid, borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.liabilitiesBil),
        },
        {
          name: "Tổng tài sản",
          type: "line",
          symbolSize: 5,
          lineStyle: { color: palette.company, width: 2 },
          itemStyle: { color: palette.company },
          data: rows.map((r) => r.assetsBil),
        },
        {
          name: "Nợ vay chịu lãi",
          type: "line",
          symbolSize: 5,
          lineStyle: { color: palette.riskHigh, width: 1.8, type: "dashed" },
          itemStyle: { color: palette.riskHigh },
          data: rows.map((r) => r.debtBil),
        },
      ],
    };
  }, [palette.border, palette.company, palette.inkMuted, palette.peerBandFill, palette.riskHigh, palette.riskLow, palette.riskMid, rows, ticker]);

  const tableHeaders = [
    "Kỳ",
    "Tổng tài sản (tỷ)",
    "Vốn chủ sở hữu (tỷ)",
    "Nợ phải trả (tỷ)",
    "Nợ vay chịu lãi (tỷ)",
  ];
  const tableRows = rows.map((r) => [
    r.label,
    formatVndBillions(r.assetsRaw),
    formatVndBillions(r.equityRaw, { accountingNegative: true }),
    formatVndBillions(r.liabilitiesRaw),
    formatVndBillions(r.debtRaw),
  ]);

  return (
    <EChartBox
      title={chartTitle}
      subtitle="Đơn vị: tỷ đồng · Cột xếp chồng VCSH + Nợ phải trả đối chiếu Tổng Tài sản và Nợ vay"
      option={option}
      height={320}
      isEmpty={rows.length === 0}
      emptyMessage="Chưa có chuỗi cơ cấu Tài sản / Nguồn vốn cho phạm vi báo cáo này."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
    />
  );
}
