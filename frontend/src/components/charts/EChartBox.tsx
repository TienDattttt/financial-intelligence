"use client";

import * as React from "react";
import * as echarts from "echarts";
import { BarChart3, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type EChartsOption = echarts.EChartsOption;

export interface ChartClickPayload {
  name?: string;
  dataIndex?: number;
  seriesName?: string;
  data?: unknown;
}

export function EChartBox({
  title,
  subtitle,
  sourceCaption = "Nguồn: BCTC đã công bố · FinIntel tính toán",
  option,
  height = 300,
  tableHeaders,
  tableRows,
  headerRight,
  onChartClick,
  emptyMessage = "Chưa có dữ liệu biểu đồ cho lát cắt này.",
  isEmpty = false,
  className,
}: {
  title: string;
  subtitle?: string;
  sourceCaption?: string;
  option: EChartsOption;
  height?: number;
  tableHeaders: readonly string[];
  tableRows: readonly (readonly string[])[];
  headerRight?: React.ReactNode;
  onChartClick?: (params: ChartClickPayload) => void;
  emptyMessage?: string;
  isEmpty?: boolean;
  className?: string;
}) {
  const [showTable, setShowTable] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<echarts.ECharts | null>(null);
  const onClickRef = React.useRef(onChartClick);

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

    const observer = new ResizeObserver(() => {
      instance.resize();
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
      instance.off("click", handleClick);
      instance.dispose();
      chartRef.current = null;
    };
  }, [showTable, isEmpty]);

  React.useEffect(() => {
    if (showTable || isEmpty || !chartRef.current) return;
    chartRef.current.setOption(option, { notMerge: true });
  }, [option, showTable, isEmpty]);

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div className="min-w-0">
          <CardTitle className="text-[14px] leading-5">{title}</CardTitle>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {headerRight}
          {!isEmpty ? (
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
        </div>
      </CardHeader>

      <CardContent className="p-3 sm:p-4">
        {isEmpty ? (
          <div
            className="flex items-center justify-center rounded border border-dashed border-border bg-canvas/50 px-4 text-center text-xs text-ink-muted"
            style={{ height }}
          >
            {emptyMessage}
          </div>
        ) : showTable ? (
          <div className="max-h-[340px] overflow-auto rounded border border-border">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                  {tableHeaders.map((hdr, idx) => (
                    <th
                      key={hdr}
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
                    className="border-b border-border/60 hover:bg-surface-muted/40"
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
