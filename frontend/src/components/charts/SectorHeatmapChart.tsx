"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Select } from "@/components/ui/primitives";
import { useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent } from "@/lib/format";
import type { SectorRiskItem } from "@/types/api";

type HeatmapMetric = "distress_prob" | "distress_zone_pct" | "avg_red_flags";

export function SectorHeatmapChart({
  sectors,
  onSelectSector,
}: {
  sectors: readonly SectorRiskItem[];
  onSelectSector: (icbCodeLevel2: string) => void;
}) {
  const [metric, setMetric] = React.useState<HeatmapMetric>("distress_prob");
  const { palette } = useChartTheme();

  const sorted = React.useMemo(() => {
    const getVal = (s: SectorRiskItem) => {
      if (metric === "distress_prob") return (s.median_distress_prob ?? 0) * 100;
      if (metric === "distress_zone_pct") {
        return s.issuer_count > 0 ? (s.distress_zone_count / s.issuer_count) * 100 : 0;
      }
      return s.avg_red_flags;
    };
    return [...sectors].sort((a, b) => getVal(b) - getVal(a));
  }, [metric, sectors]);

  const option = React.useMemo<EChartsOption>(() => {
    const getBarColor = (s: SectorRiskItem) => {
      if (metric === "distress_prob") {
        const p = s.median_distress_prob ?? 0;
        if (p >= 0.08) return palette.riskHigh;
        if (p >= 0.03) return palette.riskMid;
        return palette.riskLow;
      }
      if (metric === "distress_zone_pct") {
        const share = s.issuer_count > 0 ? s.distress_zone_count / s.issuer_count : 0;
        if (share >= 0.25) return palette.riskHigh;
        if (share >= 0.1) return palette.riskMid;
        return palette.riskLow;
      }
      if (s.avg_red_flags >= 1.5) return palette.riskHigh;
      if (s.avg_red_flags >= 0.7) return palette.riskMid;
      return palette.riskLow;
    };

    const categories = sorted.map(
      (s) => `${s.icb_name_level2} (${s.issuer_count})`,
    );

    const seriesData = sorted.map((s) => {
      let val = 0;
      if (metric === "distress_prob") {
        val = (s.median_distress_prob ?? 0) * 100;
      } else if (metric === "distress_zone_pct") {
        val = s.issuer_count > 0 ? (s.distress_zone_count / s.issuer_count) * 100 : 0;
      } else {
        val = s.avg_red_flags;
      }
      return {
        value: Number(val.toFixed(2)),
        itemStyle: { color: getBarColor(s), borderRadius: [0, 4, 4, 0] },
      };
    });

    const axisUnit = metric === "avg_red_flags" ? "" : "%";

    return {
      grid: { left: 190, right: 48, top: 14, bottom: 28 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const s = sorted[first?.dataIndex ?? -1];
          if (!s) return "";
          const zoneShare =
            s.issuer_count > 0 ? s.distress_zone_count / s.issuer_count : 0;
          return [
            `<div style="font-weight:600;margin-bottom:4px">${s.icb_name_level2}</div>`,
            `<div>Số doanh nghiệp: <b>${s.issuer_count}</b></div>`,
            `<div>Xác suất kiệt quệ (TV): <b>${formatPercent(s.median_distress_prob)}</b></div>`,
            `<div>Tỷ lệ vùng nguy cơ (Z″ &lt; 1,10): <b>${formatPercent(zoneShare)}</b> (${s.distress_zone_count} DN)</div>`,
            `<div>Cờ đỏ trung bình: <b>${formatNumber(s.avg_red_flags, 2)}</b> / DN</div>`,
            `<div style="color:${palette.company};font-size:11px;margin-top:4px">Nhấp để mở bộ lọc ngành này →</div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: `{value}${axisUnit}`,
        },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: categories,
        axisLabel: { color: palette.ink, fontSize: 11, width: 176, overflow: "truncate" },
      },
      series: [
        {
          name: "Rủi ro ngành",
          type: "bar",
          barMaxWidth: 18,
          data: seriesData,
          label: {
            show: true,
            position: "right",
            fontSize: 11,
            color: palette.inkMuted,
            formatter: (params: { value?: unknown }) => {
              const num = typeof params.value === "number" ? params.value : 0;
              return metric === "avg_red_flags"
                ? formatNumber(num, 2)
                : `${formatNumber(num, 1)}%`;
            },
          },
        },
      ],
    };
  }, [metric, palette, sorted]);

  const chartHeight = Math.min(540, Math.max(280, sorted.length * 26 + 56));

  const tableHeaders = [
    "Ngành (ICB cấp 2)",
    "Số DN",
    "Vùng nguy cơ",
    "XS kiệt quệ (TV)",
    "Beneish bị gắn cờ",
    "Cờ đỏ TB",
  ];
  const tableRows = sorted.map((s) => [
    s.icb_name_level2,
    String(s.issuer_count),
    `${s.distress_zone_count} (${formatPercent(s.issuer_count > 0 ? s.distress_zone_count / s.issuer_count : 0)})`,
    formatPercent(s.median_distress_prob),
    String(s.beneish_flagged_count),
    formatNumber(s.avg_red_flags, 2),
  ]);

  return (
    <EChartBox
      title="Phân bố rủi ro tài chính theo ngành ICB cấp 2"
      subtitle="Nhấp vào thanh ngành bất kỳ để sàng lọc danh sách doanh nghiệp thuộc ngành đó"
      option={option}
      height={chartHeight}
      isEmpty={sorted.length === 0}
      emptyMessage="Chưa có dữ liệu tổng hợp theo ngành cho lát cắt hiện tại."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        if (typeof p.dataIndex === "number" && sorted[p.dataIndex]) {
          onSelectSector(sorted[p.dataIndex].icb_code_level2);
        }
      }}
      headerRight={
        <Select
          aria-label="Chọn chỉ tiêu biểu đồ ngành"
          value={metric}
          onChange={(e) => setMetric(e.target.value as HeatmapMetric)}
          className="h-7 rounded-md text-xs"
        >
          <option value="distress_prob">Xác suất kiệt quệ (trung vị)</option>
          <option value="distress_zone_pct">Tỷ lệ DN vùng nguy cơ (%)</option>
          <option value="avg_red_flags">Số cờ đỏ trung bình / DN</option>
        </Select>
      }
    />
  );
}
