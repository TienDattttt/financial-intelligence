"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { formatChartNumber, useChartTheme } from "@/lib/echarts-theme";
import {
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatVndBillions,
} from "@/lib/format";
import type { StressSectorSummaryItem, StressTestResponse } from "@/types/api";

interface MergedSectorRow {
  icb_code_level2: string;
  icb_name_level2: string;
  issuer_count: number;
  total_debt_sum: number;
  baseline_debt_at_risk_pct: number;
  pctA: number;
  pctB: number;
  newlyA: number;
  newlyB: number;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function StressSectorBarChart({
  sectors,
  selectedSectorCode,
  onToggleSector,
  comparisonA = null,
  scenarioBName = "Kịch bản B (Hiện tại)",
}: {
  sectors: readonly StressSectorSummaryItem[];
  selectedSectorCode: string | null;
  onToggleSector: (icbCodeLevel2: string) => void;
  comparisonA?: StressTestResponse | null;
  scenarioBName?: string;
}) {
  const { palette } = useChartTheme();
  const isCompareMode = comparisonA !== null;
  const [isNarrow, setIsNarrow] = React.useState(false);

  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 519px)");
    const sync = () => setIsNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const mergedRows = React.useMemo<MergedSectorRow[]>(() => {
    if (!comparisonA) {
      return [...sectors]
        .sort((a, b) => b.stressed_debt_at_risk_pct - a.stressed_debt_at_risk_pct)
        .map((s) => ({
          icb_code_level2: s.icb_code_level2,
          icb_name_level2: s.icb_name_level2,
          issuer_count: s.issuer_count,
          total_debt_sum: s.total_debt_sum,
          baseline_debt_at_risk_pct: s.baseline_debt_at_risk_pct,
          pctA: s.baseline_debt_at_risk_pct,
          pctB: s.stressed_debt_at_risk_pct,
          newlyA: 0,
          newlyB: s.newly_distressed_count,
        }));
    }

    const mapA = new Map(
      comparisonA.sector_summary.map((s) => [s.icb_code_level2, s]),
    );
    const mapB = new Map(sectors.map((s) => [s.icb_code_level2, s]));
    const codes = Array.from(new Set([...mapA.keys(), ...mapB.keys()]));

    return codes
      .map((code) => {
        const a = mapA.get(code);
        const b = mapB.get(code);
        return {
          icb_code_level2: code,
          icb_name_level2: b?.icb_name_level2 ?? a?.icb_name_level2 ?? code,
          issuer_count: b?.issuer_count ?? a?.issuer_count ?? 0,
          total_debt_sum: b?.total_debt_sum ?? a?.total_debt_sum ?? 0,
          baseline_debt_at_risk_pct:
            b?.baseline_debt_at_risk_pct ?? a?.baseline_debt_at_risk_pct ?? 0,
          pctA: a?.stressed_debt_at_risk_pct ?? 0,
          pctB: b?.stressed_debt_at_risk_pct ?? 0,
          newlyA: a?.newly_distressed_count ?? 0,
          newlyB: b?.newly_distressed_count ?? 0,
        };
      })
      .sort((x, y) => Math.max(y.pctB, y.pctA) - Math.max(x.pctB, x.pctA));
  }, [comparisonA, sectors]);

  const seriesALabel = comparisonA
    ? `Kịch bản A: ${comparisonA.scenario_name}`
    : "Cơ sở (ICR < 1)";
  const seriesBLabel = comparisonA
    ? `Kịch bản B: ${scenarioBName}`
    : "Sau cú sốc";

  const option = React.useMemo<EChartsOption>(() => {
    const labels = mergedRows.map((s) => `${s.icb_name_level2} (${s.issuer_count})`);
    const firstColor = isCompareMode ? palette.company : palette.peerMedian;
    const secondColor = palette.riskHigh;

    const firstData = mergedRows.map((s) => {
      const dimmed =
        selectedSectorCode !== null && selectedSectorCode !== s.icb_code_level2;
      return {
        value: Number((s.pctA * 100).toFixed(2)),
        itemStyle: {
          color: firstColor,
          opacity: dimmed ? 0.28 : 1,
          borderRadius: [0, 3, 3, 0],
        },
      };
    });

    const secondData = mergedRows.map((s) => {
      const dimmed =
        selectedSectorCode !== null && selectedSectorCode !== s.icb_code_level2;
      return {
        value: Number((s.pctB * 100).toFixed(2)),
        itemStyle: {
          color: secondColor,
          opacity: dimmed ? 0.28 : 1,
          borderRadius: [0, 3, 3, 0],
        },
      };
    });

    return {
      grid: isNarrow
        ? { left: 118, right: 16, top: 46, bottom: 28 }
        : { left: 220, right: 32, top: 38, bottom: 28 },
      legend: isNarrow
        ? {
            top: 2,
            left: 4,
            right: 4,
            itemGap: 10,
            textStyle: { fontSize: 10 },
          }
        : {
            top: 2,
            right: 8,
          },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const s = mergedRows[first?.dataIndex ?? -1];
          if (!s) return "";
          if (isCompareMode) {
            const deltaPct = s.pctB - s.pctA;
            return [
              `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(s.icb_name_level2)}</div>`,
              `<div>Kịch bản A: <b style="color:${palette.company}">${formatPercent(s.pctA)}</b> (${s.newlyA} DN mới khó)</div>`,
              `<div>Kịch bản B: <b style="color:${palette.riskHigh}">${formatPercent(s.pctB)}</b> (${s.newlyB} DN mới khó)</div>`,
              `<div>Chênh lệch (B − A): <b>${formatPercentPoints(deltaPct, 1)}</b></div>`,
              `<div>Tổng dư nợ ngành: <b>${formatVndBillions(s.total_debt_sum, { decimals: 0 })} tỷ</b></div>`,
            ].join("");
          }
          return [
            `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(s.icb_name_level2)}</div>`,
            `<div>Tỷ trọng nợ rủi ro cơ sở: <b>${formatPercent(s.pctA)}</b></div>`,
            `<div>Tỷ trọng nợ rủi ro sau cú sốc: <b style="color:${palette.riskHigh}">${formatPercent(s.pctB)}</b></div>`,
            `<div>DN mới chuyển sang ICR &lt; 1: <b>${s.newlyB}</b> / ${s.issuer_count} DN</div>`,
            `<div>Tổng dư nợ ngành: <b>${formatVndBillions(s.total_debt_sum, { decimals: 0 })} tỷ</b></div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: 100,
        splitNumber: isNarrow ? 3 : 5,
        axisLabel: {
          hideOverlap: true,
          fontSize: isNarrow ? 10 : 11,
          formatter: (v: number) =>
            `${formatChartNumber(v, 1, { trimTrailingZeros: true })}%`,
        },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: labels,
        axisLabel: {
          color: palette.ink,
          fontSize: isNarrow ? 10 : 11,
          width: isNarrow ? 105 : 205,
          overflow: isNarrow ? "truncate" : "break",
          ellipsis: "…",
          lineHeight: 14,
          interval: 0,
        },
      },
      series: [
        {
          name: seriesALabel,
          type: "bar",
          barGap: "18%",
          barMaxWidth: 11,
          itemStyle: { color: firstColor },
          data: firstData,
        },
        {
          name: seriesBLabel,
          type: "bar",
          barMaxWidth: 11,
          itemStyle: { color: secondColor },
          data: secondData,
        },
      ],
    };
  }, [
    isCompareMode,
    isNarrow,
    mergedRows,
    palette.company,
    palette.ink,
    palette.peerMedian,
    palette.riskHigh,
    selectedSectorCode,
    seriesALabel,
    seriesBLabel,
  ]);

  const height = Math.min(620, Math.max(300, mergedRows.length * 36 + 68));

  const topRow = mergedRows[0];
  const chartTitle = isCompareMode
    ? topRow
      ? `Đối chiếu A vs B theo ngành: ${topRow.icb_name_level2} chịu rủi ro cao nhất (${formatPercent(Math.max(topRow.pctA, topRow.pctB))})`
      : "Đối chiếu tỷ trọng dư nợ rủi ro theo ngành (Kịch bản A vs B)"
    : topRow && topRow.pctB > 0
      ? `${topRow.icb_name_level2} có tỷ trọng nợ rủi ro cao nhất (${formatPercent(topRow.pctB)})`
      : "Tỷ trọng dư nợ rủi ro (ICR < 1) theo ngành ICB cấp 2";

  const chartSubtitle = isCompareMode
    ? "Thanh xanh: Kịch bản A · Thanh đỏ: Kịch bản B (Hiện tại) · Nhấp vào ngành để lọc bảng chi tiết"
    : "Nhấp vào một ngành để lọc danh sách doanh nghiệp bên dưới (nhấp lại để bỏ lọc)";

  const tableHeaders = isCompareMode
    ? [
        "Ngành (ICB cấp 2)",
        "Số DN",
        "Kịch bản A",
        "Kịch bản B",
        "Chênh lệch (B−A)",
        "DN mới khó (A→B)",
        "Tổng dư nợ (tỷ)",
      ]
    : [
        "Ngành (ICB cấp 2)",
        "Số DN",
        "Nợ rủi ro cơ sở",
        "Nợ rủi ro sau sốc",
        "DN mới khó khăn",
        "Tổng dư nợ (tỷ)",
      ];

  const tableRows = isCompareMode
    ? mergedRows.map((s) => [
        s.icb_name_level2,
        formatNumber(s.issuer_count, 0),
        formatPercent(s.pctA),
        formatPercent(s.pctB),
        formatPercentPoints(s.pctB - s.pctA, 1),
        `${s.newlyA} → ${s.newlyB}`,
        formatVndBillions(s.total_debt_sum, { decimals: 0 }),
      ])
    : mergedRows.map((s) => [
        s.icb_name_level2,
        formatNumber(s.issuer_count, 0),
        formatPercent(s.pctA),
        formatPercent(s.pctB),
        formatNumber(s.newlyB, 0),
        formatVndBillions(s.total_debt_sum, { decimals: 0 }),
      ]);

  return (
    <EChartBox
      title={chartTitle}
      subtitle={chartSubtitle}
      option={option}
      height={height}
      isEmpty={mergedRows.length === 0}
      emptyMessage="Chưa có dữ liệu tổng hợp theo ngành cho kịch bản này."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        if (typeof p.dataIndex === "number" && mergedRows[p.dataIndex]) {
          onToggleSector(mergedRows[p.dataIndex].icb_code_level2);
        }
      }}
    />
  );
}
