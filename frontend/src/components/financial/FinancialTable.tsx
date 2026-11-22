"use client";

import * as React from "react";
import { AlertTriangle, ChevronDown, ChevronRight, LineChart, X } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { Button } from "@/components/ui/button";
import { AUDIT_STATUS_LABELS, TAXONOMY_LABELS } from "@/lib/domain-labels";
import { useChartTheme } from "@/lib/echarts-theme";
import {
  formatNumber,
  formatPercent,
  formatPeriodLabel,
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

export interface StatementRowHierarchy {
  depth: 0 | 1 | 2 | 3;
  parentMetricId: string | null;
}

const STATEMENT_HIERARCHY_MAP: Record<string, StatementRowHierarchy> = {
  // Balance Sheet
  BS_TOTAL_ASSETS: { depth: 0, parentMetricId: null },
  BS_CURRENT_ASSETS: { depth: 1, parentMetricId: "BS_TOTAL_ASSETS" },
  BS_CASH_AND_EQUIVALENTS: { depth: 2, parentMetricId: "BS_CURRENT_ASSETS" },
  BS_SHORT_TERM_INVESTMENTS: { depth: 2, parentMetricId: "BS_CURRENT_ASSETS" },
  BS_SHORT_TERM_RECEIVABLES: { depth: 2, parentMetricId: "BS_CURRENT_ASSETS" },
  BS_TRADE_RECEIVABLES_ST: { depth: 3, parentMetricId: "BS_SHORT_TERM_RECEIVABLES" },
  BS_BAD_DEBT_PROVISION_ST: { depth: 3, parentMetricId: "BS_SHORT_TERM_RECEIVABLES" },
  BS_TRADE_RECEIVABLES_ALLOWANCE_ST: {
    depth: 3,
    parentMetricId: "BS_SHORT_TERM_RECEIVABLES",
  },
  BS_INVENTORIES: { depth: 2, parentMetricId: "BS_CURRENT_ASSETS" },

  BS_NONCURRENT_ASSETS: { depth: 1, parentMetricId: "BS_TOTAL_ASSETS" },
  BS_LT_RECEIVABLES: { depth: 2, parentMetricId: "BS_NONCURRENT_ASSETS" },
  BS_TRADE_RECEIVABLES_LT: { depth: 3, parentMetricId: "BS_LT_RECEIVABLES" },
  BS_BAD_DEBT_PROVISION_LT: { depth: 3, parentMetricId: "BS_LT_RECEIVABLES" },
  BS_TRADE_RECEIVABLES_ALLOWANCE_LT: { depth: 3, parentMetricId: "BS_LT_RECEIVABLES" },
  BS_FIXED_ASSETS: { depth: 2, parentMetricId: "BS_NONCURRENT_ASSETS" },
  BS_TANGIBLE_FIXED_ASSETS: { depth: 3, parentMetricId: "BS_FIXED_ASSETS" },
  BS_INTANGIBLE_FIXED_ASSETS: { depth: 3, parentMetricId: "BS_FIXED_ASSETS" },
  BS_CIP_LONG_TERM: { depth: 2, parentMetricId: "BS_NONCURRENT_ASSETS" },
  BS_LT_FINANCIAL_INVESTMENTS: { depth: 2, parentMetricId: "BS_NONCURRENT_ASSETS" },
  BS_OTHER_NONCURRENT_ASSETS: { depth: 2, parentMetricId: "BS_NONCURRENT_ASSETS" },

  BS_TOTAL_LIABILITIES_AND_EQUITY: { depth: 0, parentMetricId: null },
  BS_TOTAL_LIABILITIES: { depth: 1, parentMetricId: "BS_TOTAL_LIABILITIES_AND_EQUITY" },
  BS_CURRENT_LIABILITIES: { depth: 2, parentMetricId: "BS_TOTAL_LIABILITIES" },
  BS_TRADE_PAYABLES_ST: { depth: 3, parentMetricId: "BS_CURRENT_LIABILITIES" },
  BS_SHORT_TERM_BORROWINGS: { depth: 3, parentMetricId: "BS_CURRENT_LIABILITIES" },
  BS_NONCURRENT_LIABILITIES: { depth: 2, parentMetricId: "BS_TOTAL_LIABILITIES" },
  BS_LONG_TERM_BORROWINGS: { depth: 3, parentMetricId: "BS_NONCURRENT_LIABILITIES" },
  BS_CONVERTIBLE_BONDS: { depth: 3, parentMetricId: "BS_NONCURRENT_LIABILITIES" },

  BS_TOTAL_EQUITY: { depth: 1, parentMetricId: "BS_TOTAL_LIABILITIES_AND_EQUITY" },
  BS_ORDINARY_SHARES_CAPITAL: { depth: 2, parentMetricId: "BS_TOTAL_EQUITY" },
  BS_TREASURY_SHARES: { depth: 2, parentMetricId: "BS_TOTAL_EQUITY" },
  BS_RETAINED_EARNINGS: { depth: 2, parentMetricId: "BS_TOTAL_EQUITY" },
  BS_MINORITY_INTEREST: { depth: 2, parentMetricId: "BS_TOTAL_EQUITY" },

  // Income Statement
  IS_GROSS_REVENUE: { depth: 1, parentMetricId: "IS_NET_REVENUE" },
  IS_REVENUE_DEDUCTIONS: { depth: 1, parentMetricId: "IS_NET_REVENUE" },
  IS_NET_REVENUE: { depth: 0, parentMetricId: null },
  IS_COST_OF_GOODS_SOLD: { depth: 1, parentMetricId: "IS_GROSS_PROFIT" },
  IS_GROSS_PROFIT: { depth: 0, parentMetricId: null },
  IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS: {
    depth: 1,
    parentMetricId: "IS_OPERATING_PROFIT",
  },
  IS_FINANCIAL_REVENUE: { depth: 1, parentMetricId: "IS_OPERATING_PROFIT" },
  IS_FINANCIAL_EXPENSES: { depth: 1, parentMetricId: "IS_OPERATING_PROFIT" },
  IS_INTEREST_EXPENSE: { depth: 2, parentMetricId: "IS_FINANCIAL_EXPENSES" },
  IS_BORROWING_COSTS: { depth: 2, parentMetricId: "IS_FINANCIAL_EXPENSES" },
  IS_SELLING_EXPENSES: { depth: 1, parentMetricId: "IS_OPERATING_PROFIT" },
  IS_ADMINISTRATIVE_EXPENSES: { depth: 1, parentMetricId: "IS_OPERATING_PROFIT" },
  IS_OPERATING_PROFIT: { depth: 0, parentMetricId: null },
  IS_OTHER_INCOME: { depth: 1, parentMetricId: "IS_OTHER_PROFIT" },
  IS_OTHER_EXPENSES: { depth: 1, parentMetricId: "IS_OTHER_PROFIT" },
  IS_OTHER_PROFIT: { depth: 0, parentMetricId: null },
  IS_PROFIT_BEFORE_TAX: { depth: 0, parentMetricId: null },
  IS_CURRENT_INCOME_TAX: { depth: 1, parentMetricId: "IS_NET_INCOME" },
  IS_DEFERRED_INCOME_TAX: { depth: 1, parentMetricId: "IS_NET_INCOME" },
  IS_NET_INCOME: { depth: 0, parentMetricId: null },
  IS_CONTINUING_OPERATIONS_NET_INCOME: { depth: 1, parentMetricId: "IS_NET_INCOME" },
  IS_NET_INCOME_PARENT: { depth: 1, parentMetricId: "IS_NET_INCOME" },
  IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE: { depth: 1, parentMetricId: "IS_NET_INCOME" },
  IS_EPS_BASIC_REPORTED: { depth: 0, parentMetricId: null },

  // Cash Flow
  CF_SUPPLIER_PAYMENTS: { depth: 1, parentMetricId: "CF_OPERATING_CASH_FLOW" },
  CF_DEPRECIATION_AMORTIZATION: { depth: 1, parentMetricId: "CF_OPERATING_CASH_FLOW" },
  CF_OPERATING_CASH_FLOW: { depth: 0, parentMetricId: null },
  CF_CAPITAL_EXPENDITURE: { depth: 1, parentMetricId: "CF_INVESTING_CASH_FLOW" },
  CF_INVESTING_CASH_FLOW: { depth: 0, parentMetricId: null },
  CF_EQUITY_ISSUANCE_PROCEEDS: { depth: 1, parentMetricId: "CF_FINANCING_CASH_FLOW" },
  CF_PROCEEDS_FROM_BORROWINGS: { depth: 1, parentMetricId: "CF_FINANCING_CASH_FLOW" },
  CF_REPAYMENT_OF_BORROWINGS: { depth: 1, parentMetricId: "CF_FINANCING_CASH_FLOW" },
  CF_FINANCING_CASH_FLOW: { depth: 0, parentMetricId: null },
  CF_NET_CASH_CHANGE: { depth: 0, parentMetricId: null },
};

export function getStatementRowHierarchy(
  metricId: string,
  _statementType: StatementType,
  isTotal: boolean,
): StatementRowHierarchy {
  const mapped = STATEMENT_HIERARCHY_MAP[metricId];
  if (mapped) return mapped;
  return { depth: isTotal ? 0 : 1, parentMetricId: null };
}

export function formatAuditStatusBadge(
  auditStatus: string | null | undefined,
): { label: string; tone: "low" | "mid" | "neutral" } {
  const normalized = auditStatus?.trim().toUpperCase() ?? "";
  if (normalized === "AUDITED") {
    return {
      label: AUDIT_STATUS_LABELS.AUDITED ?? "Đã kiểm toán",
      tone: "low",
    };
  }
  if (normalized === "REVIEWED") {
    return {
      label: AUDIT_STATUS_LABELS.REVIEWED ?? "Đã soát xét",
      tone: "mid",
    };
  }
  if (normalized === "UNAUDITED") {
    return {
      label: AUDIT_STATUS_LABELS.UNAUDITED ?? "Tự lập",
      tone: "neutral",
    };
  }
  return { label: "Chưa rõ", tone: "neutral" };
}

export interface StatementTrendPoint {
  periodEndDate: string;
  label: string;
  rawValue: number | null;
  chartValue: number | null;
}

export function buildStatementLineTrend(
  row: StatementRowItem,
  periods: readonly StatementPeriodItem[],
  periodType: PeriodType,
  maxPeriods = 12,
): StatementTrendPoint[] {
  const isPerShare = row.unit === "VND_PER_SHARE";
  const chronological = [...periods]
    .sort((a, b) => a.period_end_date.localeCompare(b.period_end_date))
    .slice(-maxPeriods);

  return chronological.map((p) => {
    const raw = row.values[p.period_end_date] ?? null;
    const validRaw = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    const chartVal =
      validRaw === null
        ? null
        : isPerShare
          ? Math.round(validRaw)
          : Number((validRaw / 1_000_000_000).toFixed(2));
    return {
      periodEndDate: p.period_end_date,
      label: p.label || formatPeriodLabel(p.period_end_date, periodType),
      rawValue: validRaw,
      chartValue: chartVal,
    };
  });
}

const AUDIT_BADGE_CLASSES: Record<"low" | "mid" | "neutral", string> = {
  low: "border-risk-low/30 bg-risk-low-soft text-risk-low",
  mid: "border-risk-mid/30 bg-risk-mid-soft text-risk-mid",
  neutral: "border-border bg-canvas text-ink-muted",
};

const DEPTH_PADDING_CLASSES: Record<0 | 1 | 2 | 3, string> = {
  0: "pl-3",
  1: "pl-5",
  2: "pl-8",
  3: "pl-11",
};

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
  const [collapsedParents, setCollapsedParents] = React.useState<Set<string>>(
    () => new Set(),
  );
  const [selectedMetricId, setSelectedMetricId] = React.useState<string | null>(null);
  const { palette } = useChartTheme();

  React.useEffect(() => {
    if (!selectedMetricId) return;
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setSelectedMetricId(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedMetricId]);

  const baseMetricId =
    statementType === "BALANCE_SHEET" ? "BS_TOTAL_ASSETS" : "IS_NET_REVENUE";

  const baseRow = React.useMemo(
    () => rows.find((r) => r.metric_id === baseMetricId),
    [baseMetricId, rows],
  );

  const hierarchyMap = React.useMemo(() => {
    const rowSet = new Set(rows.map((r) => r.metric_id));
    const map = new Map<string, StatementRowHierarchy>();
    const childrenSet = new Set<string>();
    for (const row of rows) {
      const h = getStatementRowHierarchy(row.metric_id, statementType, row.is_total);
      const validParent =
        h.parentMetricId && rowSet.has(h.parentMetricId) ? h.parentMetricId : null;
      map.set(row.metric_id, {
        depth: validParent ? h.depth : h.depth === 0 ? 0 : Math.min(h.depth, 1) as 0 | 1,
        parentMetricId: validParent,
      });
      if (validParent) {
        childrenSet.add(validParent);
      }
    }
    return { map, childrenSet };
  }, [rows, statementType]);

  const visibleRows = React.useMemo(() => {
    const isHidden = (metricId: string): boolean => {
      let cur = hierarchyMap.map.get(metricId)?.parentMetricId ?? null;
      let guard = 0;
      while (cur && guard < 6) {
        if (collapsedParents.has(cur)) return true;
        cur = hierarchyMap.map.get(cur)?.parentMetricId ?? null;
        guard += 1;
      }
      return false;
    };
    return rows.filter((r) => !isHidden(r.metric_id));
  }, [collapsedParents, hierarchyMap.map, rows]);

  const toggleParentCollapse = React.useCallback((metricId: string) => {
    setCollapsedParents((prev) => {
      const next = new Set(prev);
      if (next.has(metricId)) next.delete(metricId);
      else next.add(metricId);
      return next;
    });
  }, []);

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

  const selectedRow = React.useMemo(
    () =>
      selectedMetricId
        ? rows.find((r) => r.metric_id === selectedMetricId) ?? null
        : null,
    [rows, selectedMetricId],
  );

  const trendPoints = React.useMemo(
    () =>
      selectedRow
        ? buildStatementLineTrend(selectedRow, periods, periodType, 12)
        : [],
    [periodType, periods, selectedRow],
  );

  const trendOption = React.useMemo<EChartsOption>(() => {
    if (!selectedRow || trendPoints.length === 0) return {};
    const isPerShare = selectedRow.unit === "VND_PER_SHARE";
    const unitLabel = isPerShare ? "đồng/cp" : "tỷ đồng";
    return {
      grid: { left: 60, right: 20, top: 28, bottom: 34 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (raw: unknown) => {
          const list = Array.isArray(raw) ? raw : [raw];
          const first = list[0] as { dataIndex?: number } | undefined;
          const pt = trendPoints[first?.dataIndex ?? -1];
          if (!pt) return "";
          const valStr = isPerShare
            ? formatVndPerShare(pt.rawValue)
            : `${formatVndBillions(pt.rawValue, { accountingNegative: true })} tỷ`;
          return [
            `<div style="font-weight:600;margin-bottom:4px">${pt.label}</div>`,
            `<div>${selectedRow.display_name_vn}: <b>${valStr}</b></div>`,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: trendPoints.map((pt) => pt.label),
      },
      yAxis: {
        type: "value",
        name: unitLabel,
        axisLabel: {
          formatter: (val: number) => formatNumber(val, 0),
        },
      },
      series: [
        {
          name: selectedRow.display_name_vn,
          type: "bar",
          barMaxWidth: 28,
          itemStyle: {
            color: (params: { value?: unknown }) => {
              const v = typeof params.value === "number" ? params.value : 0;
              return v < 0 ? palette.riskHigh : palette.company;
            },
            borderRadius: [3, 3, 0, 0],
          },
          data: trendPoints.map((pt) => pt.chartValue),
        },
      ],
    };
  }, [palette.company, palette.riskHigh, selectedRow, trendPoints]);

  const rowHeightClass = compact ? "h-8 py-1" : "h-10 py-2";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-border bg-surface p-0.5 text-xs">
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
                  "rounded-md px-2.5 py-1 font-medium transition-colors duration-150",
                  viewMode === mode
                    ? "bg-primary text-primary-foreground shadow-xs"
                    : "text-ink-muted hover:text-ink",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <span className="text-[11px] text-ink-muted">
            Nhấp vào bất kỳ dòng chỉ tiêu nào để xem biểu đồ xu hướng 12 kỳ
          </span>
        </div>

        {hasTaxonomyTransition ? (
          <div className="flex items-center gap-1.5 rounded-lg border border-risk-mid/30 bg-risk-mid-soft px-2.5 py-1 text-xs text-risk-mid">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            <span>Chuỗi kỳ có chuyển chuẩn mực TT200 → TT99 (từ 2026)</span>
          </div>
        ) : null}
      </div>

      {selectedRow ? (
        <div
          role="region"
          aria-label={`Biểu đồ xu hướng ${selectedRow.display_name_vn}`}
          className="rounded-lg border border-accent-ink/30 bg-surface p-3 shadow-xs"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-accent-ink">
              <LineChart className="size-3.5" aria-hidden="true" />
              <span>
                Xu hướng {trendPoints.length} kỳ: {selectedRow.display_name_vn}
              </span>
            </div>
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setSelectedMetricId(null)}
            >
              <X className="size-3" />
              <span>Đóng biểu đồ</span>
            </Button>
          </div>
          <EChartBox
            title={`Diễn biến ${selectedRow.display_name_vn} (${trendPoints.length} kỳ gần nhất)`}
            subtitle={`Đơn vị: ${selectedRow.unit === "VND_PER_SHARE" ? "đồng/cổ phiếu" : "tỷ đồng"} · Nhấp phím Esc hoặc nút Đóng để thu gọn`}
            option={trendOption}
            height={250}
            isEmpty={trendPoints.every((pt) => pt.rawValue === null)}
            emptyMessage="Chưa có số liệu qua các kỳ cho dòng chỉ tiêu này."
            tableHeaders={[
              "Kỳ",
              selectedRow.unit === "VND_PER_SHARE"
                ? `${selectedRow.display_name_vn} (đ/cp)`
                : `${selectedRow.display_name_vn} (tỷ)`,
            ]}
            tableRows={trendPoints.map((pt) => [
              pt.label,
              selectedRow.unit === "VND_PER_SHARE"
                ? formatVndPerShare(pt.rawValue)
                : formatVndBillions(pt.rawValue, { accountingNegative: true }),
            ])}
          />
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-border bg-surface shadow-xs">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-surface-muted/70 text-left text-xs font-semibold text-ink-muted">
              <th
                scope="col"
                className="sticky left-0 z-10 min-w-[280px] border-r border-border bg-surface-muted px-3 py-2"
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
                const auditBadge = formatAuditStatusBadge(p.audit_status);
                return (
                  <th
                    key={p.period_end_date}
                    scope="col"
                    className={cn(
                      "min-w-[120px] px-3 py-2 text-right align-top",
                      isBoundary && "border-l-2 border-l-risk-mid",
                    )}
                  >
                    <div className="font-semibold text-ink">{p.label}</div>
                    <div className="mt-0.5 text-[10px] font-normal text-ink-subtle">
                      {p.taxonomy_version
                        ? TAXONOMY_LABELS[p.taxonomy_version] ?? p.taxonomy_version
                        : "—"}
                    </div>
                    <div className="mt-1 flex justify-end">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-full border px-1.5 py-0 text-[10px] font-medium leading-4",
                          AUDIT_BADGE_CLASSES[auditBadge.tone],
                        )}
                      >
                        {auditBadge.label}
                      </span>
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => {
              const codeLabel =
                row.tt99_code && row.tt200_code && row.tt99_code !== row.tt200_code
                  ? `${row.tt200_code}→${row.tt99_code}`
                  : row.tt99_code ?? row.tt200_code ?? "—";
              const isPerShare = row.unit === "VND_PER_SHARE";
              const h = hierarchyMap.map.get(row.metric_id) ?? {
                depth: row.is_total ? 0 : 1,
                parentMetricId: null,
              };
              const hasChildren = hierarchyMap.childrenSet.has(row.metric_id);
              const isCollapsed = collapsedParents.has(row.metric_id);
              const isSelected = selectedMetricId === row.metric_id;

              return (
                <tr
                  key={row.metric_id}
                  onClick={() =>
                    setSelectedMetricId((prev) =>
                      prev === row.metric_id ? null : row.metric_id,
                    )
                  }
                  className={cn(
                    "cursor-pointer border-b border-border/60 transition-colors duration-150 hover:bg-surface-muted/50",
                    row.is_total && "bg-surface-muted/50 font-semibold",
                    isSelected && "bg-accent-soft/70",
                  )}
                >
                  <th
                    scope="row"
                    className={cn(
                      "sticky left-0 z-10 border-r border-border bg-surface pr-3 text-left font-normal text-ink",
                      DEPTH_PADDING_CLASSES[h.depth],
                      row.is_total && "bg-surface-muted/95 font-semibold",
                      isSelected && "bg-accent-soft",
                      rowHeightClass,
                    )}
                  >
                    <div className="flex items-center justify-between gap-1.5">
                      <div className="flex min-w-0 items-center gap-1">
                        {hasChildren ? (
                          <button
                            type="button"
                            onClick={(ev) => {
                              ev.stopPropagation();
                              toggleParentCollapse(row.metric_id);
                            }}
                            aria-expanded={!isCollapsed}
                            aria-label={`${isCollapsed ? "Mở rộng" : "Thu gọn"} ${row.display_name_vn}`}
                            className="inline-flex size-4 shrink-0 items-center justify-center rounded text-ink-muted hover:bg-surface-muted hover:text-ink"
                          >
                            {isCollapsed ? (
                              <ChevronRight className="size-3.5" />
                            ) : (
                              <ChevronDown className="size-3.5" />
                            )}
                          </button>
                        ) : h.depth >= 2 ? (
                          <span
                            aria-hidden="true"
                            className="inline-block w-3 shrink-0 text-center text-[10px] text-ink-subtle"
                          >
                            └
                          </span>
                        ) : null}
                        <span
                          className={cn(
                            "truncate",
                            row.is_total && "font-semibold",
                            isSelected && "text-accent-ink",
                          )}
                          title={`${row.display_name_vn} — Nhấp để xem xu hướng 12 kỳ`}
                        >
                          {row.display_name_vn}
                        </span>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {!row.comparable_across_taxonomy ? (
                          <span
                            className="text-[10px] font-medium text-risk-mid"
                            title="Chỉ tiêu thay đổi cấu trúc giữa TT200 và TT99 — cần thận trọng khi so sánh qua năm 2026"
                          >
                            TT200≠TT99
                          </span>
                        ) : null}
                        <LineChart
                          className={cn(
                            "size-3",
                            isSelected ? "text-accent-ink" : "text-ink-subtle/60",
                          )}
                          aria-hidden="true"
                        />
                      </div>
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
