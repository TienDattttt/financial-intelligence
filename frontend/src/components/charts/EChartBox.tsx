"use client";

import * as React from "react";
import * as echarts from "echarts";
import { BarChart3, Download, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { applyChartThemeDefaults, getChartPalette, useChartTheme } from "@/lib/echarts-theme";
import { buildCsvContent } from "@/lib/format";
import { cn } from "@/lib/utils";

export type EChartsOption = echarts.EChartsOption;

export interface ChartClickPayload {
  name?: string;
  dataIndex?: number;
  seriesName?: string;
  data?: unknown;
}

export function buildSafeExportFilename(title: string): string {
  const base = title
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return `${base || "finintel-chart"}.png`;
}

function extractCellText(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map((item) => extractCellText(item)).join("");
  }
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) {
    return extractCellText(node.props.children);
  }
  return "";
}

export function EChartBox({
  title,
  subtitle,
  sourceCaption = "Nguồn: BCTC đã công bố · FinIntel tính toán",
  option,
  height = 300,
  tableHeaders,
  tableRows,
  csvRows,
  headerRight,
  onChartClick,
  emptyMessage = "Chưa có dữ liệu biểu đồ cho lát cắt này.",
  isEmpty = false,
  enableExportPng = true,
  enableTableToggle = true,
  className,
}: {
  title: string;
  subtitle?: string;
  sourceCaption?: string;
  option: EChartsOption;
  height?: number;
  tableHeaders: readonly string[];
  tableRows: readonly (readonly React.ReactNode[])[];
  csvRows?: readonly (readonly string[])[];
  headerRight?: React.ReactNode;
  onChartClick?: (params: ChartClickPayload) => void;
  emptyMessage?: string;
  isEmpty?: boolean;
  enableExportPng?: boolean;
  enableTableToggle?: boolean;
  className?: string;
}) {
  const [showTable, setShowTable] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<echarts.ECharts | null>(null);
  const onClickRef = React.useRef(onChartClick);
  const { theme, reducedMotion } = useChartTheme();

  const themedOption = React.useMemo(
    () => applyChartThemeDefaults(option, theme, reducedMotion),
    [option, reducedMotion, theme],
  );

  React.useEffect(() => {
    onClickRef.current = onChartClick;
  }, [onChartClick]);

  React.useEffect(() => {
    if (showTable || isEmpty || !containerRef.current) {
      if (chartRef.current) {
        chartRef.current.dispose();
        chartRef.current = null;
      }
      return;
    }

    const el = containerRef.current;
    const instance = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = instance;

    const handleClick = (params: unknown) => {
      if (!onClickRef.current || !params || typeof params !== "object") return;
      const p = params as Record<string, unknown>;
      onClickRef.current({
        name: typeof p.name === "string" ? p.name : undefined,
        dataIndex: typeof p.dataIndex === "number" ? p.dataIndex : undefined,
        seriesName: typeof p.seriesName === "string" ? p.seriesName : undefined,
        data: p.data,
      });
    };

    instance.on("click", handleClick);

    const zr = instance.getZr();
    const handleZrClick = (event: {
      target?: unknown;
      offsetX: number;
      offsetY: number;
    }) => {
      if (event.target || !onClickRef.current) return;
      const point: [number, number] = [event.offsetX, event.offsetY];
      try {
        if (!instance.containPixel("grid", point)) return;
        const coord = instance.convertFromPixel({ seriesIndex: 0 }, point);
        const xIdx = Array.isArray(coord) ? Math.round(Number(coord[0])) : NaN;
        if (Number.isInteger(xIdx) && xIdx >= 0) {
          onClickRef.current({ dataIndex: xIdx });
        }
      } catch {
        // Non-cartesian charts (e.g. treemap, radar) do not have a grid coordinate system
      }
    };
    zr.on("click", handleZrClick);

    const observer = new ResizeObserver(() => {
      instance.resize();
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
      zr.off("click", handleZrClick);
      instance.off("click", handleClick);
      instance.dispose();
      chartRef.current = null;
    };
  }, [showTable, isEmpty]);

  React.useEffect(() => {
    if (showTable || isEmpty || !chartRef.current) return;
    chartRef.current.setOption(themedOption, { notMerge: true });
  }, [themedOption, showTable, isEmpty]);

  const handleExportPng = React.useCallback(() => {
    const instance = chartRef.current;
    if (!instance || showTable || isEmpty) return;
    const palette = getChartPalette(theme);
    const dataUrl = instance.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: palette.surface,
    });
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = buildSafeExportFilename(title);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [isEmpty, showTable, theme, title]);

  const hasTableData = tableHeaders.length > 0 && tableRows.length > 0;

  const handleExportCsv = React.useCallback(() => {
    if (!showTable || isEmpty || !hasTableData) return;
    const exportRows =
      csvRows ?? tableRows.map((row) => row.map((cell) => extractCellText(cell)));
    const csv = buildCsvContent(tableHeaders, exportRows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = buildSafeExportFilename(title).replace(/\.png$/, ".csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [csvRows, hasTableData, isEmpty, showTable, tableHeaders, tableRows, title]);

  return (
    <Card className={cn("overflow-hidden rounded-lg shadow-xs transition-colors duration-150", className)}>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div className="min-w-0">
          <CardTitle className="text-[14px] leading-5">{title}</CardTitle>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {headerRight}
          {!isEmpty && enableTableToggle ? (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setShowTable((v) => !v)}
              aria-pressed={showTable}
            >
              {showTable ? (
                <>
                  <BarChart3 className="size-3" />
                  <span>Xem biểu đồ</span>
                </>
              ) : (
                <>
                  <Table2 className="size-3" />
                  <span>Xem dạng bảng</span>
                </>
              )}
            </Button>
          ) : null}
          {!isEmpty && enableExportPng ? (
            showTable ? (
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={handleExportCsv}
                disabled={!hasTableData}
                title={
                  hasTableData
                    ? "Tải xuống dữ liệu bảng dạng CSV"
                    : "Không có dữ liệu bảng để xuất CSV"
                }
                aria-label={`Xuất CSV: ${title}`}
              >
                <Download className="size-3" />
                <span>Xuất CSV</span>
              </Button>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={handleExportPng}
                title="Tải xuống biểu đồ dạng ảnh PNG"
                aria-label={`Xuất PNG: ${title}`}
              >
                <Download className="size-3" />
                <span>Xuất PNG</span>
              </Button>
            )
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="p-3 sm:p-4">
        {isEmpty ? (
          <div
            key="empty"
            className="flex items-center justify-center rounded-lg border border-dashed border-border bg-canvas/50 px-4 text-center text-xs text-ink-muted"
            style={{ height }}
          >
            {emptyMessage}
          </div>
        ) : showTable ? (
          <div
            key="table"
            className="max-h-[340px] overflow-auto rounded-lg border border-border"
          >
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                  {tableHeaders.map((hdr, idx) => (
                    <th
                      key={`${hdr}-${idx}`}
                      scope="col"
                      className={cn(
                        "sticky top-0 bg-surface-muted px-2.5 py-2 font-semibold",
                        idx === 0 ? "text-left" : "text-right",
                      )}
                    >
                      {hdr}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row, rIdx) => (
                  <tr
                    key={rIdx}
                    className="border-b border-border/60 transition-colors duration-150 hover:bg-surface-muted/40"
                  >
                    {row.map((cell, cIdx) => (
                      <td
                        key={cIdx}
                        className={cn(
                          "px-2.5 py-1.5",
                          cIdx === 0
                            ? "text-left font-medium text-ink"
                            : "num text-right text-ink",
                        )}
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div
            key="chart"
            ref={containerRef}
            className="w-full"
            style={{ height }}
            role="img"
            aria-label={title}
          />
        )}

        <p className="mt-2 border-t border-border/50 pt-1.5 text-[11px] text-ink-subtle">
          {sourceCaption}
        </p>
      </CardContent>
    </Card>
  );
}
