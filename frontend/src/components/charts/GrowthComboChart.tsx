"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent, formatPeriodLabel, formatVndBillions } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { GrowthPoint, PeriodType, PeriodView } from "@/types/api";

export interface NormalizedGrowthPoint {
  periodEndDate: string;
  label: string;
  revenueBil: number | null;
  netIncomeBil: number | null;
  cfoBil: number | null;
  revenueRaw: number | null;
  netIncomeRaw: number | null;
  cfoRaw: number | null;
  revenueGrowthQoq: number | null;
  revenueGrowthQoqFlag: string | null;
  netIncomeGrowthQoq: number | null;
  netIncomeGrowthQoqFlag: string | null;
}

function toBillions(raw: number | null | undefined): number | null {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return Number((raw / 1_000_000_000).toFixed(2));
}

export function transformGrowthSeries(
  points: readonly GrowthPoint[],
  periodType: PeriodType,
): NormalizedGrowthPoint[] {
  return [...points]
    .sort((a, b) => a.period_end_date.localeCompare(b.period_end_date))
    .map((pt) => ({
      periodEndDate: pt.period_end_date,
      label: formatPeriodLabel(pt.period_end_date, periodType),
      revenueBil: toBillions(pt.IS_NET_REVENUE),
      netIncomeBil: toBillions(pt.IS_NET_INCOME),
      cfoBil: toBillions(pt.CF_OPERATING_CASH_FLOW),
      revenueRaw:
        typeof pt.IS_NET_REVENUE === "number" && Number.isFinite(pt.IS_NET_REVENUE)
          ? pt.IS_NET_REVENUE
          : null,
      netIncomeRaw:
        typeof pt.IS_NET_INCOME === "number" && Number.isFinite(pt.IS_NET_INCOME)
          ? pt.IS_NET_INCOME
          : null,
      cfoRaw:
        typeof pt.CF_OPERATING_CASH_FLOW === "number" &&
        Number.isFinite(pt.CF_OPERATING_CASH_FLOW)
          ? pt.CF_OPERATING_CASH_FLOW
          : null,
      revenueGrowthQoq:
        typeof pt.revenue_growth_qoq === "number" &&
        Number.isFinite(pt.revenue_growth_qoq)
          ? pt.revenue_growth_qoq
          : null,
      revenueGrowthQoqFlag: pt.revenue_growth_qoq_flag ?? null,
      netIncomeGrowthQoq:
        typeof pt.net_income_growth_qoq === "number" &&
        Number.isFinite(pt.net_income_growth_qoq)
          ? pt.net_income_growth_qoq
          : null,
      netIncomeGrowthQoqFlag: pt.net_income_growth_qoq_flag ?? null,
    }));
}

export function buildGrowthChartTitle(
  rows: readonly NormalizedGrowthPoint[],
  localView: PeriodView,
): string {
  if (rows.length === 0) {
    return "Doanh thu thuần, Lợi nhuận sau thuế và Dòng tiền kinh doanh (CFO)";
  }
  const latest = rows[rows.length - 1];
  const prev = rows.length >= 2 ? rows[rows.length - 2] : null;
  const viewTag = localView === "Quarterly" ? "Quý rời rạc" : localView === "Annual" ? "Năm" : "TTM";
  const revText =
    latest.revenueRaw !== null ? `${formatVndBillions(latest.revenueRaw)} tỷ` : "N/A";
  let deltaText = "";
  if (localView === "Quarterly") {
    if (latest.revenueGrowthQoq !== null) {
      deltaText = ` (QoQ ${formatPercent(latest.revenueGrowthQoq, { signed: true, decimals: 1 })})`;
    } else if (latest.revenueGrowthQoqFlag === "MIXED_TAXONOMY") {
      deltaText = " (Khóa QoQ qua TT200 → TT99)";
    }
  } else if (
    latest.revenueRaw !== null &&
    prev?.revenueRaw !== null &&
    prev?.revenueRaw !== undefined &&
    Math.abs(prev.revenueRaw) > 1e-6
  ) {
    const chg = (latest.revenueRaw - prev.revenueRaw) / Math.abs(prev.revenueRaw);
    deltaText = ` (${formatPercent(chg, { signed: true, decimals: 1 })} so kỳ liền trước)`;
  }
  return `Tăng trưởng (${viewTag}): Doanh thu ${latest.label} đạt ${revText}${deltaText}`;
}

export function GrowthComboChart({
  ticker,
  points,
  globalPeriodType,
  localPeriodView,
  onChangeLocalPeriodView,
  isLoading = false,
}: {
  ticker: string;
  points: readonly GrowthPoint[];
  globalPeriodType: PeriodType;
  localPeriodView: PeriodView;
  onChangeLocalPeriodView?: (next: "TTM" | "Quarterly") => void;
  isLoading?: boolean;
}) {
  const { palette } = useChartTheme();

  const rows = React.useMemo(
    () => transformGrowthSeries(points, globalPeriodType),
    [globalPeriodType, points],
  );

  const chartTitle = React.useMemo(
    () => buildGrowthChartTitle(rows, localPeriodView),
    [localPeriodView, rows],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const labels = rows.map((r) => r.label);
    const hasZoom = rows.length > 6;
    const startIdx = Math.max(0, rows.length - 12);
    const startPct = rows.length > 0 ? Math.round((startIdx / rows.length) * 100) : 0;

    return {
      grid: {
        left: 60,
        right: 18,
        top: 34,
        bottom: hasZoom ? 58 : 34,
      },
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
          const qoqLine =
            localPeriodView === "Quarterly"
              ? `<div>Tăng trưởng QoQ doanh thu: <b>${
                  row.revenueGrowthQoq !== null
                    ? formatPercent(row.revenueGrowthQoq, { signed: true, decimals: 1 })
                    : row.revenueGrowthQoqFlag === "MIXED_TAXONOMY"
                      ? "N/A (Chuyển TT200 → TT99)"
                      : "N/A"
                }</b></div>`
              : "";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${ticker} · ${row.label}</div>`,
            `<div>Doanh thu thuần: <b>${formatVndBillions(row.revenueRaw)} tỷ</b></div>`,
            qoqLine,
            `<div>Lợi nhuận sau thuế: <b>${formatVndBillions(row.netIncomeRaw, { accountingNegative: true })} tỷ</b></div>`,
            `<div>Dòng tiền KD (CFO): <b>${formatVndBillions(row.cfoRaw, { accountingNegative: true })} tỷ</b></div>`,
          ]
            .filter(Boolean)
            .join("");
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
      ...(hasZoom
        ? {
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
          }
        : {}),
      series: [
        {
          name: "Doanh thu thuần",
          type: "bar",
          barMaxWidth: 26,
          itemStyle: { color: palette.company, borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.revenueBil),
        },
        {
          name: "Lợi nhuận sau thuế",
          type: "line",
          symbolSize: 6,
          lineStyle: { color: palette.riskLow, width: 2.2 },
          itemStyle: { color: palette.riskLow },
          data: rows.map((r) => r.netIncomeBil),
        },
        {
          name: "Dòng tiền KD (CFO)",
          type: "line",
          symbolSize: 6,
          lineStyle: { color: palette.riskMid, width: 2, type: "dashed" },
          itemStyle: { color: palette.riskMid },
          data: rows.map((r) => r.cfoBil),
        },
      ],
    };
  }, [localPeriodView, palette.border, palette.company, palette.inkMuted, palette.peerBandFill, palette.riskLow, palette.riskMid, rows, ticker]);

  const tableHeaders = [
    "Kỳ",
    "Doanh thu thuần (tỷ)",
    "Lợi nhuận sau thuế (tỷ)",
    "CFO (tỷ)",
  ];
  const tableRows = rows.map((r) => [
    r.label,
    formatVndBillions(r.revenueRaw),
    formatVndBillions(r.netIncomeRaw, { accountingNegative: true }),
    formatVndBillions(r.cfoRaw, { accountingNegative: true }),
  ]);

  const subtitle =
    localPeriodView === "Quarterly"
      ? "Đơn vị: tỷ đồng · Quý rời rạc — Lưu ý mùa vụ Q4 và khóa QoQ qua ranh giới TT200 → TT99"
      : localPeriodView === "Annual"
        ? "Đơn vị: tỷ đồng · Dữ liệu theo năm tài chính"
        : "Đơn vị: tỷ đồng · Dữ liệu lũy kế 4 quý trượt (TTM)";

  return (
    <EChartBox
      title={chartTitle}
      subtitle={subtitle}
      option={option}
      height={320}
      isEmpty={!isLoading && rows.length === 0}
      emptyMessage={
        isLoading
          ? "Đang tải chuỗi tăng trưởng..."
          : "Chưa có chuỗi Doanh thu / LNST / CFO cho phạm vi báo cáo này."
      }
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      headerRight={
        globalPeriodType === "QUARTER" && onChangeLocalPeriodView ? (
          <div
            className="inline-flex rounded-md border border-border bg-canvas p-0.5 text-[11px]"
            role="group"
            aria-label="Chế độ kỳ biểu đồ tăng trưởng"
          >
            {(
              [
                ["TTM", "TTM (4Q)"],
                ["Quarterly", "Quý rời rạc"],
              ] as const
            ).map(([mode, lbl]) => (
              <button
                key={mode}
                type="button"
                onClick={() => onChangeLocalPeriodView(mode)}
                aria-pressed={localPeriodView === mode}
                className={cn(
                  "rounded px-2 py-0.5 font-medium transition-colors duration-150",
                  localPeriodView === mode
                    ? "bg-primary text-primary-foreground shadow-xs"
                    : "text-ink-muted hover:text-ink",
                )}
              >
                {lbl}
              </button>
            ))}
          </div>
        ) : null
      }
    />
  );
}
