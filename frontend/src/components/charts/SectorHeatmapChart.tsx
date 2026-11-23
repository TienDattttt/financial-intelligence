"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import {
  computeSectorRiskThresholds,
  getSectorMetricValue,
  getSectorRiskColor,
} from "@/components/charts/SectorTreemapChart";
import { Select } from "@/components/ui/primitives";
import { type ChartPalette, useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent } from "@/lib/format";
import type { SectorRiskItem } from "@/types/api";

export type HeatmapMetric = "distress_prob" | "distress_zone_pct" | "avg_red_flags";

export interface SectorHeatmapRow {
  sector: SectorRiskItem;
  numericValue: number | null;
  plotValue: number;
  isMissing: boolean;
  color: string;
  displayLabel: string;
}

export function buildSectorHeatmapData(
  sectors: readonly SectorRiskItem[],
  metric: HeatmapMetric,
  palette: Pick<ChartPalette, "riskLow" | "riskMid" | "riskHigh" | "neutral">,
): { rows: SectorHeatmapRow[]; hasMissing: boolean } {
  const sorted = [...sectors].sort(
    (a, b) => getSectorMetricValue(b, metric) - getSectorMetricValue(a, metric),
  );
  const thresholds = computeSectorRiskThresholds(sorted, metric);

  let hasMissing = false;
  const rows: SectorHeatmapRow[] = sorted.map((s) => {
    const color = getSectorRiskColor(s, metric, palette, thresholds);
    if (metric === "distress_prob") {
      const p = s.median_distress_prob;
      if (p === null || p === undefined || !Number.isFinite(p)) {
        hasMissing = true;
        return {
          sector: s,
          numericValue: null,
          plotValue: 0,
          isMissing: true,
          color: palette.neutral,
          displayLabel: "N/A",
        };
      }
      const val = Number((p * 100).toFixed(2));
      return {
        sector: s,
        numericValue: val,
        plotValue: val,
        isMissing: false,
        color,
        displayLabel: `${formatNumber(val, 1)}%`,
      };
    }

    const val =
      metric === "distress_zone_pct"
        ? s.issuer_count > 0
          ? Number(((s.distress_zone_count / s.issuer_count) * 100).toFixed(2))
          : 0
        : Number(s.avg_red_flags.toFixed(2));
    return {
      sector: s,
      numericValue: val,
      plotValue: val,
      isMissing: false,
      color,
      displayLabel:
        metric === "avg_red_flags" ? formatNumber(val, 2) : `${formatNumber(val, 1)}%`,
    };
  });

  return { rows, hasMissing };
}

export function SectorHeatmapChart({
  sectors,
  onSelectSector,
}: {
  sectors: readonly SectorRiskItem[];
  onSelectSector: (icbCodeLevel2: string) => void;
}) {
  const [metric, setMetric] = React.useState<HeatmapMetric>("distress_prob");
  const { palette } = useChartTheme();

  const { rows } = React.useMemo(
    () => buildSectorHeatmapData(sectors, metric, palette),
    [metric, palette, sectors],
  );

  const sorted = React.useMemo(() => rows.map((r) => r.sector), [rows]);

  const option = React.useMemo<EChartsOption>(() => {
    const categories = rows.map(
      (r) => `${r.sector.icb_name_level2} (${r.sector.issuer_count})`,
    );

    const seriesData = rows.map((r) => ({
      value: r.plotValue,
      isMissing: r.isMissing,
      displayLabel: r.displayLabel,
      itemStyle: { color: r.color, borderRadius: [0, 4, 4, 0] },
    }));

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
            formatter: (params: { data?: unknown; value?: unknown }) => {
              const dataItem = params.data as
                | { isMissing?: boolean; displayLabel?: string }
                | undefined;
              if (dataItem?.isMissing) return "N/A";
              if (dataItem?.displayLabel) return dataItem.displayLabel;
              const num = typeof params.value === "number" ? params.value : 0;
              return metric === "avg_red_flags"
                ? formatNumber(num, 2)
                : `${formatNumber(num, 1)}%`;
            },
          },
        },
      ],
    };
  }, [metric, palette, rows, sorted]);

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

  const dynamicTitle = React.useMemo(() => {
    const top = rows[0];
    if (!top) return "Phân bố rủi ro tài chính theo ngành ICB cấp 2";
    if (metric === "distress_prob") {
      if (top.isMissing) {
        return "Phân bố rủi ro tài chính theo ngành ICB cấp 2";
      }
      return `Xếp hạng rủi ro ngành: ${top.sector.icb_name_level2} dẫn đầu (${formatPercent(top.sector.median_distress_prob)})`;
    }
    if (metric === "distress_zone_pct") {
      const share =
        top.sector.issuer_count > 0
          ? top.sector.distress_zone_count / top.sector.issuer_count
          : 0;
      return `Xếp hạng vùng nguy cơ: ${top.sector.icb_name_level2} chiếm ${formatPercent(share)} DN`;
    }
    return `Mật độ cờ đỏ ngành: ${top.sector.icb_name_level2} đạt ${formatNumber(top.sector.avg_red_flags, 2)} cờ/DN`;
  }, [metric, rows]);

  return (
    <EChartBox
      title={dynamicTitle}
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
