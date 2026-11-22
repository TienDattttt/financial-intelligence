"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatPercent, formatVndBillions } from "@/lib/format";
import type { StressSectorSummaryItem } from "@/types/api";

export function StressSectorBarChart({
  sectors,
  selectedSectorCode,
  onToggleSector,
}: {
  sectors: readonly StressSectorSummaryItem[];
  selectedSectorCode: string | null;
  onToggleSector: (icbCodeLevel2: string) => void;
}) {
  const { palette } = useChartTheme();

  const sorted = React.useMemo(
    () =>
      [...sectors].sort(
        (a, b) => b.stressed_debt_at_risk_pct - a.stressed_debt_at_risk_pct,
      ),
    [sectors],
  );

  const option = React.useMemo<EChartsOption>(() => {
    const labels = sorted.map((s) => `${s.icb_name_level2} (${s.issuer_count})`);
    const baseData = sorted.map((s) => ({
      value: Number((s.baseline_debt_at_risk_pct * 100).toFixed(2)),
      itemStyle: {
        color:
          selectedSectorCode && selectedSectorCode !== s.icb_code_level2
            ? palette.peerMedianMuted
            : palette.peerMedian,
        borderRadius: [0, 3, 3, 0],
      },
    }));
    const stressedData = sorted.map((s) => ({
      value: Number((s.stressed_debt_at_risk_pct * 100).toFixed(2)),
      itemStyle: {
        color:
          selectedSectorCode && selectedSectorCode !== s.icb_code_level2
            ? palette.riskHighMuted
            : palette.riskHigh,
        borderRadius: [0, 3, 3, 0],
      },
    }));

    return {
      grid: { left: 185, right: 32, top: 34, bottom: 28 },
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
          const s = sorted[first?.dataIndex ?? -1];
          if (!s) return "";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${s.icb_name_level2}</div>`,
            `<div>Tỷ trọng nợ rủi ro cơ sở: <b>${formatPercent(s.baseline_debt_at_risk_pct)}</b></div>`,
            `<div>Tỷ trọng nợ rủi ro sau cú sốc: <b style="color:${palette.riskHigh}">${formatPercent(s.stressed_debt_at_risk_pct)}</b></div>`,
            `<div>DN mới chuyển sang ICR &lt; 1: <b>${s.newly_distressed_count}</b> / ${s.issuer_count} DN</div>`,
            `<div>Tổng dư nợ ngành: <b>${formatVndBillions(s.total_debt_sum, { decimals: 0 })} tỷ</b></div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "value",
        max: 100,
        axisLabel: { formatter: "{value}%" },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: labels,
        axisLabel: { color: palette.ink, fontSize: 11, width: 170, overflow: "truncate" },
      },
      series: [
        {
          name: "Cơ sở (ICR < 1)",
          type: "bar",
          barGap: "18%",
          barMaxWidth: 11,
          itemStyle: { color: palette.peerMedian },
          data: baseData,
        },
        {
          name: "Sau cú sốc",
          type: "bar",
          barMaxWidth: 11,
          itemStyle: { color: palette.riskHigh },
          data: stressedData,
        },
      ],
    };
  }, [palette, selectedSectorCode, sorted]);

  const height = Math.min(540, Math.max(280, sorted.length * 28 + 64));

  const tableHeaders = [
    "Ngành (ICB cấp 2)",
    "Số DN",
    "Nợ rủi ro cơ sở",
    "Nợ rủi ro sau sốc",
    "DN mới khó khăn",
    "Tổng dư nợ (tỷ)",
  ];
  const tableRows = sorted.map((s) => [
    s.icb_name_level2,
    String(s.issuer_count),
    formatPercent(s.baseline_debt_at_risk_pct),
    formatPercent(s.stressed_debt_at_risk_pct),
    String(s.newly_distressed_count),
    formatVndBillions(s.total_debt_sum, { decimals: 0 }),
  ]);

  return (
    <EChartBox
      title="Tỷ trọng dư nợ rủi ro (ICR < 1) theo ngành ICB cấp 2"
      subtitle="Nhấp vào một ngành để lọc danh sách doanh nghiệp bên dưới (nhấp lại để bỏ lọc)"
      option={option}
      height={height}
      isEmpty={sorted.length === 0}
      emptyMessage="Chưa có dữ liệu tổng hợp theo ngành cho kịch bản này."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        if (typeof p.dataIndex === "number" && sorted[p.dataIndex]) {
          onToggleSector(sorted[p.dataIndex].icb_code_level2);
        }
      }}
    />
  );
}
