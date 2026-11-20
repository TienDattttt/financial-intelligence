"use client";

import * as React from "react";
import { AlertTriangle } from "lucide-react";
import { TAXONOMY_LABELS } from "@/lib/domain-labels";
import {
  formatPercent,
  formatVndBillions,
  formatVndPerShare,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  PeriodType,
  StatementPeriodItem,
  StatementRowItem,
  StatementType,
} from "@/types/api";

type ViewMode = "VALUE" | "COMMON_SIZE" | "YOY";

export function FinancialTable({
  statementType,
  periodType,
  periods,
  rows,
  compact = true,
}: {
  statementType: StatementType;
  periodType: PeriodType;
  periods: readonly StatementPeriodItem[];
  rows: readonly StatementRowItem[];
  compact?: boolean;
}) {
  const [viewMode, setViewMode] = React.useState<ViewMode>("VALUE");

  const baseMetricId =
    statementType === "BALANCE_SHEET" ? "BS_TOTAL_ASSETS" : "IS_NET_REVENUE";

  const baseRow = React.useMemo(
    () => rows.find((r) => r.metric_id === baseMetricId),
    [baseMetricId, rows],
  );

  // Map prior-year comparable date for YoY calculations
  const priorYearDateMap = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const p of periods) {
      const match = periods.find((cand) => {
        if (periodType === "ANNUAL") {
          return (p.fiscal_year ?? 0) - (cand.fiscal_year ?? 0) === 1;
        }
        return (
          (p.fiscal_year ?? 0) - (cand.fiscal_year ?? 0) === 1 &&
          p.fiscal_quarter === cand.fiscal_quarter
        );
      });
      if (match) {
        map.set(p.period_end_date, match.period_end_date);
      }
    }
    return map;
  }, [periodType, periods]);

  const hasTaxonomyTransition = React.useMemo(() => {
    const versions = new Set(
      periods.map((p) => p.taxonomy_version).filter(Boolean),
    );
    return versions.size > 1;
  }, [periods]);

  const rowHeightClass = compact ? "h-8 py-1" : "h-10 py-2";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex rounded border border-border bg-surface p-0.5 text-xs">
          {(
            [
              ["VALUE", "Giá trị (tỷ đồng)"],
              [
                "COMMON_SIZE",
                statementType === "BALANCE_SHEET"
                  ? "Tỷ trọng (% Tổng TS)"
                  : "Tỷ trọng (% Doanh thu)",
              ],
              ["YOY", "Tăng trưởng YoY (%)"],
            ] as const
          ).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              onClick={() => setViewMode(mode)}
              className={cn(
                "rounded px-2.5 py-1 font-medium transition-colors",
                viewMode === mode
                  ? "bg-accent-ink text-white"
                  : "text-ink-muted hover:text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {hasTaxonomyTransition ? (
          <div className="flex items-center gap-1.5 rounded border border-risk-mid/30 bg-risk-mid-soft px-2.5 py-1 text-xs text-risk-mid">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            <span>Chuỗi kỳ có chuyển chuẩn mực TT200 → TT99 (từ 2026)</span>
          </div>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded border border-border bg-surface">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-surface-muted/70 text-left text-xs font-semibold text-ink-muted">
              <th
                scope="col"
                className="sticky left-0 z-10 min-w-[260px] border-r border-border bg-surface-muted px-3 py-2"
              >
                Chỉ tiêu ({viewMode === "VALUE" ? "tỷ đồng" : "%"})
              </th>
              <th
                scope="col"
                className="w-20 border-r border-border/70 px-2 py-2 text-center font-mono text-[11px]"
                title="Mã chỉ tiêu theo TT200 / TT99"
              >
                Mã VAS
              </th>
              {periods.map((p, idx) => {
                const prevTax = idx > 0 ? periods[idx - 1]?.taxonomy_version : p.taxonomy_version;
                const isBoundary =
                  prevTax && p.taxonomy_version && prevTax !== p.taxonomy_version;
                return (
                  <th
                    key={p.period_end_date}
                    scope="col"
                    className={cn(
                      "min-w-[112px] px-3 py-2 text-right",
                      isBoundary && "border-l-2 border-l-risk-mid",
                    )}
                  >
                    <div className="font-semibold text-ink">{p.label}</div>
                    <div className="text-[10px] font-normal text-ink-subtle">
                      {p.taxonomy_version
                        ? TAXONOMY_LABELS[p.taxonomy_version] ?? p.taxonomy_version
                        : "—"}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const codeLabel =
                row.tt99_code && row.tt200_code && row.tt99_code !== row.tt200_code
                  ? `${row.tt200_code}→${row.tt99_code}`
                  : row.tt99_code ?? row.tt200_code ?? "—";
              const isPerShare = row.unit === "VND_PER_SHARE";

              return (
                <tr
                  key={row.metric_id}
                  className={cn(
                    "border-b border-border/60 hover:bg-surface-muted/40",
                    row.is_total && "bg-surface-muted/50 font-semibold",
                  )}
                >
                  <th
                    scope="row"
                    className={cn(
                      "sticky left-0 z-10 border-r border-border bg-surface px-3 text-left font-normal text-ink",
                      row.is_total && "bg-surface-muted/95 font-semibold",
                      rowHeightClass,
                    )}
                  >
                    <div className="flex items-center justify-between gap-1.5">
                      <span className="truncate" title={row.display_name_vn}>
                        {row.display_name_vn}
                      </span>
                      {!row.comparable_across_taxonomy ? (
                        <span
                          className="shrink-0 text-[10px] font-medium text-risk-mid"
                          title="Chỉ tiêu thay đổi cấu trúc giữa TT200 và TT99 — cần thận trọng khi so sánh qua năm 2026"
                        >
                          TT200≠TT99
                        </span>
                      ) : null}
                    </div>
                  </th>
                  <td className="border-r border-border/60 px-2 text-center font-mono text-[11px] text-ink-muted">
                    {codeLabel}
                  </td>
                  {periods.map((p, idx) => {
                    const val = row.values[p.period_end_date] ?? null;
                    const prevTax = idx > 0 ? periods[idx - 1]?.taxonomy_version : p.taxonomy_version;
                    const isBoundary =
                      prevTax && p.taxonomy_version && prevTax !== p.taxonomy_version;

                    let display = "N/A";
                    let colorClass = "text-ink";

                    if (viewMode === "VALUE") {
                      display = isPerShare
                        ? formatVndPerShare(val)
                        : formatVndBillions(val, { accountingNegative: true });
                    } else if (viewMode === "COMMON_SIZE") {
                      if (isPerShare || !baseRow) {
                        display = "–";
                      } else {
                        const baseVal = baseRow.values[p.period_end_date] ?? null;
                        if (val !== null && baseVal !== null && Math.abs(baseVal) > 1e-6) {
                          display = formatPercent(val / baseVal, { decimals: 1 });
                        }
                      }
                    } else {
                      const priorDate = priorYearDateMap.get(p.period_end_date);
                      const priorVal = priorDate ? row.values[priorDate] ?? null : null;
                      if (
                        val !== null &&
                        priorVal !== null &&
                        Math.abs(priorVal) > 1e-6
                      ) {
                        const yoy = (val - priorVal) / Math.abs(priorVal);
                        display = formatPercent(yoy, { signed: true, decimals: 1 });
                        if (yoy > 0.001) colorClass = "text-up";
                        else if (yoy < -0.001) colorClass = "text-down";
                      }
                    }

                    return (
                      <td
                        key={p.period_end_date}
                        className={cn(
                          "num px-3 text-right",
                          rowHeightClass,
                          colorClass,
                          row.is_total && "border-t border-border-strong",
                          row.is_total &&
                            (row.metric_id === "BS_TOTAL_ASSETS" ||
                              row.metric_id === "BS_TOTAL_LIABILITIES_AND_EQUITY" ||
                              row.metric_id === "IS_NET_INCOME" ||
                              row.metric_id === "CF_NET_CASH_CHANGE") &&
                            "underline decoration-double underline-offset-4",
                          isBoundary && "border-l-2 border-l-risk-mid",
                        )}
                      >
                        {display}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
