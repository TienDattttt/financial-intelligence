"use client";

import * as React from "react";
import { GitBranch, Link2 } from "lucide-react";
import { Select } from "@/components/ui/primitives";
import { formatMultiple, formatPercent, formatPeriodLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { DuPontPoint, PeriodType, RatioName } from "@/types/api";

export interface DuPontTreeChartProps {
  dupontSeries: readonly DuPontPoint[];
  ratiosTable?: readonly Record<string, unknown>[];
  periodType: PeriodType;
  onSelectMetric: (ratioName: RatioName, periodEndDate: string) => void;
}

interface TreeNodeButtonProps {
  title: string;
  formulaHint: string;
  valueText: string;
  tone: "primary" | "profitability" | "efficiency" | "leverage";
  ratioName: RatioName;
  periodEndDate: string;
  onSelect: (ratioName: RatioName, periodEndDate: string) => void;
  compact?: boolean;
}

const TONE_CLASSES: Record<TreeNodeButtonProps["tone"], string> = {
  primary:
    "border-blue-700/40 bg-blue-50/70 hover:border-blue-700 dark:border-blue-500/40 dark:bg-blue-950/30",
  profitability:
    "border-emerald-700/35 bg-emerald-50/60 hover:border-emerald-700 dark:border-emerald-500/35 dark:bg-emerald-950/25",
  efficiency:
    "border-amber-700/35 bg-amber-50/60 hover:border-amber-700 dark:border-amber-500/35 dark:bg-amber-950/25",
  leverage:
    "border-slate-400/50 bg-surface-muted/70 hover:border-slate-700 dark:border-slate-600/60 dark:bg-slate-900/50",
};

function readRatioFloat(
  ratiosTable: readonly Record<string, unknown>[] | undefined,
  periodEndDate: string,
  field: string,
): number | null {
  if (!ratiosTable || ratiosTable.length === 0) return null;
  const row = ratiosTable.find(
    (item) => String(item.period_end_date ?? "").slice(0, 10) === periodEndDate.slice(0, 10),
  );
  if (!row) return null;
  const raw = row[field];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function TreeNodeButton({
  title,
  formulaHint,
  valueText,
  tone,
  ratioName,
  periodEndDate,
  onSelect,
  compact = false,
}: TreeNodeButtonProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(ratioName, periodEndDate)}
      aria-label={`Truy nguồn ${title}: ${valueText}`}
      className={cn(
        "group flex w-full flex-col justify-between rounded-md border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-700",
        compact ? "px-3 py-2" : "px-3.5 py-2.5",
        TONE_CLASSES[tone],
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-[11px] font-semibold leading-tight text-ink">
          {title}
        </span>
        <Link2
          className="mt-0.5 size-3 shrink-0 text-ink-subtle transition-colors group-hover:text-blue-700"
          aria-hidden="true"
        />
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span className="num text-base font-semibold tracking-tight text-ink">
          {valueText}
        </span>
        <span className="text-[10px] text-ink-muted">{formulaHint}</span>
      </div>
    </button>
  );
}

export function DuPontTreeChart({
  dupontSeries,
  ratiosTable,
  periodType,
  onSelectMetric,
}: DuPontTreeChartProps) {
  const sortedSeries = React.useMemo(
    () =>
      [...dupontSeries].sort((a, b) =>
        b.period_end_date.localeCompare(a.period_end_date),
      ),
    [dupontSeries],
  );

  const [selectedPeriod, setSelectedPeriod] = React.useState<string>("");

  const activePoint =
    sortedSeries.find((item) => item.period_end_date === selectedPeriod) ??
    sortedSeries[0] ??
    null;

  if (!activePoint) {
    return (
      <p className="p-4 text-xs text-ink-muted">
        Chưa có dữ liệu sơ đồ cây DuPont cho phạm vi báo cáo này.
      </p>
    );
  }

  const roaVal = readRatioFloat(ratiosTable, activePoint.period_end_date, "roa");
  const netMarginVal = readRatioFloat(
    ratiosTable,
    activePoint.period_end_date,
    "net_profit_margin",
  );
  const validDuPont = activePoint.dupont_valid_flag !== false;
  const activeDate = activePoint.period_end_date;

  return (
    <div
      className="space-y-3 p-3.5"
      aria-label="Sơ đồ cây phân tách ROE DuPont 5 nhân tố"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 pb-2.5">
        <div className="flex items-center gap-2 text-xs text-ink-muted">
          <GitBranch className="size-3.5 text-blue-700" aria-hidden="true" />
          <span>
            Nhấn vào từng nút chỉ số để mở bảng truy vết công thức VAS trong SourceDrawer.
          </span>
        </div>
        <div className="flex items-center gap-2">
          <label
            htmlFor="dupont-tree-period-select"
            className="text-[11px] font-medium text-ink-muted"
          >
            Kỳ hiển thị:
          </label>
          <Select
            id="dupont-tree-period-select"
            value={activeDate}
            onChange={(e) => setSelectedPeriod(e.target.value)}
            className="h-7 text-xs"
          >
            {sortedSeries.slice(0, 12).map((dp) => (
              <option key={dp.period_end_date} value={dp.period_end_date}>
                {formatPeriodLabel(dp.period_end_date, periodType)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {!validDuPont ? (
        <div className="rounded border border-amber-500/40 bg-amber-50/70 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          Kỳ {formatPeriodLabel(activeDate, periodType)} có mẫu số vốn chủ sở hữu hoặc tổng tài sản không dương; một số nhánh phân tách hiển thị N/A theo quy tắc bảo toàn kế toán.
        </div>
      ) : null}

      <div className="space-y-3">
        {/* Cấp 1: ROE Tổng */}
        <div className="mx-auto max-w-sm">
          <TreeNodeButton
            title="ROE (Tổng vốn chủ sở hữu)"
            formulaHint="= ROA × Đòn bẩy"
            valueText={formatPercent(activePoint.roe_total)}
            tone="primary"
            ratioName="roe_total"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
        </div>

        {/* Cấp 2: ROA & Đòn bẩy tài chính */}
        <div className="grid gap-3 md:grid-cols-3">
          <div className="md:col-span-2">
            <TreeNodeButton
              title="ROA (Suất sinh lời trên tổng tài sản)"
              formulaHint="= Biên LN ròng × Vòng quay TS"
              valueText={formatPercent(roaVal)}
              tone="profitability"
              ratioName="roa"
              periodEndDate={activeDate}
              onSelect={onSelectMetric}
            />
          </div>
          <TreeNodeButton
            title="Hệ số đòn bẩy tài chính"
            formulaHint="Tổng TS / VCSH BQ"
            valueText={formatMultiple(activePoint.equity_multiplier_total)}
            tone="leverage"
            ratioName="liabilities_to_assets"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
        </div>

        {/* Cấp 3: Biên lợi nhuận ròng & Vòng quay tài sản */}
        <div className="grid gap-3 md:grid-cols-3">
          <div className="md:col-span-2">
            <TreeNodeButton
              title="Biên lợi nhuận ròng"
              formulaHint="= Gánh nặng thuế × Lãi vay × Biên EBIT"
              valueText={formatPercent(netMarginVal)}
              tone="profitability"
              ratioName="net_profit_margin"
              periodEndDate={activeDate}
              onSelect={onSelectMetric}
            />
          </div>
          <TreeNodeButton
            title="Vòng quay tổng tài sản"
            formulaHint="Doanh thu / Tổng TS BQ"
            valueText={formatMultiple(activePoint.asset_turnover)}
            tone="efficiency"
            ratioName="asset_turnover"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
        </div>

        {/* Cấp 4: 3 thành phần biên lợi nhuận DuPont 5 nhân tố */}
        <div className="grid gap-2.5 sm:grid-cols-3">
          <TreeNodeButton
            compact
            title="Gánh nặng thuế"
            formulaHint="LNST / LNTT"
            valueText={formatPercent(activePoint.tax_burden)}
            tone="profitability"
            ratioName="net_profit_margin"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
          <TreeNodeButton
            compact
            title="Gánh nặng lãi vay"
            formulaHint="LNTT / EBIT"
            valueText={formatPercent(activePoint.interest_burden)}
            tone="profitability"
            ratioName="interest_coverage"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
          <TreeNodeButton
            compact
            title="Biên EBIT"
            formulaHint="EBIT / Doanh thu thuần"
            valueText={formatPercent(activePoint.ebit_margin)}
            tone="profitability"
            ratioName="ebit_margin"
            periodEndDate={activeDate}
            onSelect={onSelectMetric}
          />
        </div>
      </div>
    </div>
  );
}
