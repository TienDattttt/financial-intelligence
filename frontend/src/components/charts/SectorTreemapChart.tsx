"use client";

import * as React from "react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Select } from "@/components/ui/primitives";
import { type ChartPalette, useChartTheme } from "@/lib/echarts-theme";
import { formatNumber, formatPercent, formatVndBillions } from "@/lib/format";
import type { SectorRiskItem } from "@/types/api";

export type TreemapRiskMetric = "distress_prob" | "distress_zone_pct" | "avg_red_flags";

export interface SectorTreemapNode {
  name: string;
  value: number;
  icbCode: string;
  riskMetricValue: number;
  distressShare: number;
  medianDistressProb: number | null;
  avgRedFlags: number;
  distressZoneCount: number;
  beneishFlaggedCount: number;
  highRedFlagCount: number;
  totalDebtSum: number;
  color: string;
}

export function getSectorMetricValue(
  s: SectorRiskItem,
  metric: TreemapRiskMetric,
): number {
  if (metric === "distress_prob") return (s.median_distress_prob ?? 0) * 100;
  if (metric === "distress_zone_pct") {
    return s.issuer_count > 0 ? (s.distress_zone_count / s.issuer_count) * 100 : 0;
  }
  return s.avg_red_flags;
}

export function getSectorRiskColor(
  s: SectorRiskItem,
  metric: TreemapRiskMetric,
  palette: Pick<ChartPalette, "riskLow" | "riskMid" | "riskHigh" | "neutral">,
): string {
  if (metric === "distress_prob") {
    const p = s.median_distress_prob;
    if (p === null || p === undefined || !Number.isFinite(p)) return palette.neutral;
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
}

export function buildSectorTreemapNodes(
  sectors: readonly SectorRiskItem[],
  metric: TreemapRiskMetric,
  palette: Pick<ChartPalette, "riskLow" | "riskMid" | "riskHigh" | "neutral">,
): SectorTreemapNode[] {
  return [...sectors]
    .filter((s) => s.issuer_count > 0)
    .sort((a, b) => b.issuer_count - a.issuer_count)
    .map((s) => {
      const distressShare =
        s.issuer_count > 0 ? s.distress_zone_count / s.issuer_count : 0;
      return {
        name: s.icb_name_level2,
        value: s.issuer_count,
        icbCode: s.icb_code_level2,
        riskMetricValue: Number(getSectorMetricValue(s, metric).toFixed(2)),
        distressShare,
        medianDistressProb: s.median_distress_prob ?? null,
        avgRedFlags: s.avg_red_flags,
        distressZoneCount: s.distress_zone_count,
        beneishFlaggedCount: s.beneish_flagged_count,
        highRedFlagCount: s.high_red_flag_count,
        totalDebtSum: s.total_debt_sum,
        color: getSectorRiskColor(s, metric, palette),
      };
    });
}

export function buildSectorTreemapTitle(
  sectors: readonly SectorRiskItem[],
  metric: TreemapRiskMetric,
): string {
  const valid = sectors.filter((s) => s.issuer_count > 0);
  if (valid.length === 0) {
    return "Bản đồ quy mô & mật độ rủi ro theo ngành ICB cấp 2";
  }
  const topRisk = [...valid].sort(
    (a, b) => getSectorMetricValue(b, metric) - getSectorMetricValue(a, metric),
  )[0];
  if (!topRisk) return "Bản đồ quy mô & mật độ rủi ro theo ngành ICB cấp 2";

  if (metric === "distress_prob") {
    return `${topRisk.icb_name_level2} dẫn đầu rủi ro ngành (${formatPercent(topRisk.median_distress_prob)} XS kiệt quệ trung vị)`;
  }
  if (metric === "distress_zone_pct") {
    const share =
      topRisk.issuer_count > 0
        ? topRisk.distress_zone_count / topRisk.issuer_count
        : 0;
    return `${topRisk.icb_name_level2} có tỷ trọng DN vùng nguy cơ cao nhất (${formatPercent(share)})`;
  }
  return `${topRisk.icb_name_level2} ghi nhận mật độ cờ đỏ cao nhất (${formatNumber(topRisk.avg_red_flags, 2)} cờ/DN)`;
}

export function SectorTreemapChart({
  sectors,
  onSelectSector,
}: {
  sectors: readonly SectorRiskItem[];
  onSelectSector: (icbCodeLevel2: string) => void;
}) {
  const [metric, setMetric] = React.useState<TreemapRiskMetric>("distress_prob");
  const { theme, palette } = useChartTheme();

  const nodes = React.useMemo(
    () => buildSectorTreemapNodes(sectors, metric, palette),
    [metric, palette, sectors],
  );

  const chartTitle = React.useMemo(
    () => buildSectorTreemapTitle(sectors, metric),
    [metric, sectors],
  );

  const tileLabelColor = theme === "dark" ? palette.canvas : palette.surface;

  const option = React.useMemo<EChartsOption>(() => {
    const treemapData = nodes.map((node) => ({
      name: node.name,
      value: node.value,
      icbCode: node.icbCode,
      nodeMeta: node,
      itemStyle: {
        color: node.color,
        borderColor: palette.surface,
        borderWidth: 2,
        gapWidth: 2,
      },
    }));

    return {
      tooltip: {
        trigger: "item",
        formatter: (raw: unknown) => {
          const item = raw as { data?: { nodeMeta?: SectorTreemapNode } } | undefined;
          const node = item?.data?.nodeMeta;
          if (!node) return "";
          return [
            `<div style="font-weight:600;margin-bottom:4px">${node.name}</div>`,
            `<div>Số doanh nghiệp (diện tích): <b>${node.value}</b></div>`,
            `<div>Xác suất kiệt quệ (TV): <b>${formatPercent(node.medianDistressProb)}</b></div>`,
            `<div>Vùng nguy cơ (Z″ &lt; 1,10): <b>${node.distressZoneCount} DN</b> (${formatPercent(node.distressShare)})</div>`,
            `<div>Cờ đỏ trung bình: <b>${formatNumber(node.avgRedFlags, 2)}</b> / DN</div>`,
            `<div>Tổng nợ vay ngành: <b>${formatVndBillions(node.totalDebtSum)} tỷ</b></div>`,
            `<div style="color:${palette.company};font-size:11px;margin-top:4px">Nhấp vào ô ngành để sàng lọc doanh nghiệp →</div>`,
          ].join("");
        },
      },
      series: [
        {
          name: "Ngành ICB cấp 2",
          type: "treemap",
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          left: 4,
          right: 4,
          top: 6,
          bottom: 6,
          label: {
            show: true,
            color: tileLabelColor,
            fontFamily: palette.fontFamily,
            fontSize: 11,
            fontWeight: 600,
            lineHeight: 15,
            formatter: (params: unknown) => {
              const p = params as { data?: { nodeMeta?: SectorTreemapNode } };
              const n = p.data?.nodeMeta;
              if (!n) return "";
              const metricStr =
                metric === "distress_prob"
                  ? formatPercent(n.medianDistressProb)
                  : metric === "distress_zone_pct"
                    ? formatPercent(n.distressShare)
                    : `${formatNumber(n.avgRedFlags, 2)} cờ`;
              return `${n.name}\n${n.value} DN · ${metricStr}`;
            },
          },
          upperLabel: { show: false },
          itemStyle: {
            borderColor: palette.surface,
            borderWidth: 2,
          },
          emphasis: {
            itemStyle: {
              borderColor: palette.ink,
              borderWidth: 2,
            },
          },
          data: treemapData,
        },
      ],
    };
  }, [metric, nodes, palette.company, palette.fontFamily, palette.ink, palette.surface, tileLabelColor]);

  const sortedForTable = React.useMemo(
    () =>
      [...nodes].sort((a, b) => b.riskMetricValue - a.riskMetricValue),
    [nodes],
  );

  const tableHeaders = [
    "Ngành (ICB cấp 2)",
    "Số DN",
    "Vùng nguy cơ",
    "XS kiệt quệ (TV)",
    "Beneish bị gắn cờ",
    "Cờ đỏ TB",
    "Tổng nợ vay (tỷ)",
  ];

  const tableRows = sortedForTable.map((n) => [
    n.name,
    String(n.value),
    `${n.distressZoneCount} (${formatPercent(n.distressShare)})`,
    formatPercent(n.medianDistressProb),
    String(n.beneishFlaggedCount),
    formatNumber(n.avgRedFlags, 2),
    formatVndBillions(n.totalDebtSum),
  ]);

  return (
    <EChartBox
      title={chartTitle}
      subtitle="Diện tích tỷ lệ theo số doanh nghiệp · Màu sắc thể hiện mức độ rủi ro · Nhấp vào ô ngành để mở bảng sàng lọc"
      option={option}
      height={380}
      isEmpty={nodes.length === 0}
      emptyMessage="Chưa có dữ liệu tổng hợp theo ngành cho lát cắt hiện tại."
      tableHeaders={tableHeaders}
      tableRows={tableRows}
      onChartClick={(p) => {
        const data = p.data as { icbCode?: unknown } | undefined;
        if (typeof data?.icbCode === "string" && data.icbCode.length > 0) {
          onSelectSector(data.icbCode);
        }
      }}
      headerRight={
        <div className="flex flex-wrap items-center gap-2">
          <div className="hidden items-center gap-2 text-[11px] text-ink-muted sm:flex">
            <span className="inline-flex items-center gap-1">
              <span className="size-2.5 rounded-xs bg-risk-low" /> Thấp
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-2.5 rounded-xs bg-risk-mid" /> Trung bình
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-2.5 rounded-xs bg-risk-high" /> Cao
            </span>
          </div>
          <Select
            aria-label="Chọn chỉ tiêu tô màu bản đồ ngành"
            value={metric}
            onChange={(e) => setMetric(e.target.value as TreemapRiskMetric)}
            className="h-7 rounded-md text-xs"
          >
            <option value="distress_prob">Xác suất kiệt quệ (trung vị)</option>
            <option value="distress_zone_pct">Tỷ lệ DN vùng nguy cơ (%)</option>
            <option value="avg_red_flags">Số cờ đỏ trung bình / DN</option>
          </Select>
        </div>
      }
    />
  );
}
