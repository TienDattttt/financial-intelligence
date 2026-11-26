"use client";

import Link from "next/link";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Bookmark } from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ALTMAN_ZONE_META, BENEISH_FLAG_META } from "@/lib/domain-labels";
import { formatMultiple, formatNumber, formatPercent } from "@/lib/format";
import {
  formatAnomalyFlagMeta,
  formatRelativeRiskLiftLabel,
  formatTopRiskPercentileLabel,
  getScreenerRiskTierMeta,
  SCREENER_OPTIONAL_COLUMNS,
  type ScreenerOptionalColumnId,
} from "@/lib/saved-screens-store";
import { cn } from "@/lib/utils";
import { toggleWatchlistTicker } from "@/lib/watchlist-store";
import type { ScreenerItem, ScreenerSortField, SortDirection } from "@/types/api";

export interface ScreenerColumnMeta {
  align?: "left" | "right";
}

function SortableHeader({
  label,
  field,
  activeField,
  activeDir,
  onSort,
  align = "right",
}: {
  label: string;
  field: ScreenerSortField;
  activeField: ScreenerSortField;
  activeDir: SortDirection;
  onSort: (field: ScreenerSortField) => void;
  align?: "left" | "right";
}) {
  const isActive = activeField === field;
  const Icon = !isActive ? ArrowUpDown : activeDir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={() => onSort(field)}
      className={cn(
        "inline-flex w-full items-center gap-0.5 font-semibold hover:text-ink",
        align === "right" ? "justify-end" : "justify-start",
        isActive ? "text-accent-ink" : "text-ink-muted",
      )}
    >
      <span>{label}</span>
      <Icon className="size-3 shrink-0" aria-hidden="true" />
    </button>
  );
}

export function buildScreenerColumns({
  density,
  sortBy,
  sortDir,
  onSort,
  selectedTickers,
  onToggleTickerCompare,
  watchlistSet,
  visibleColumnSet,
}: {
  density: "compact" | "comfortable";
  sortBy: ScreenerSortField;
  sortDir: SortDirection;
  onSort: (field: ScreenerSortField) => void;
  selectedTickers: readonly string[];
  onToggleTickerCompare: (ticker: string) => void;
  watchlistSet: ReadonlySet<string>;
  visibleColumnSet: ReadonlySet<ScreenerOptionalColumnId>;
}): ColumnDef<ScreenerItem>[] {
  const optionalCols: Record<ScreenerOptionalColumnId, ColumnDef<ScreenerItem>> = {
    distress: {
      id: "distress",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Bậc rủi ro & XS 1 năm"
          field="calibrated_distress_prob"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => {
        const item = row.original;
        const p = item.calibrated_distress_prob;
        const tierMeta = getScreenerRiskTierMeta(item.risk_tier);
        const topRisk = formatTopRiskPercentileLabel(item.risk_market_percentile);
        const lift = formatRelativeRiskLiftLabel(item.relative_risk_lift);
        const subMeta = [topRisk, lift].filter(Boolean).join(" · ");
        if (density === "compact") {
          return (
            <div
              className="flex items-center justify-end gap-1.5 whitespace-nowrap"
              title={subMeta || undefined}
            >
              <span className="num font-semibold text-ink">
                {formatPercent(p)}
              </span>
              <RiskBadge tone={tierMeta.tone} label={tierMeta.shortLabel} />
            </div>
          );
        }
        return (
          <div className="flex flex-col items-end gap-0.5 whitespace-nowrap">
            <div className="flex items-center gap-1.5">
              <span className="num font-semibold text-ink">
                {formatPercent(p)}
              </span>
              <RiskBadge tone={tierMeta.tone} label={tierMeta.shortLabel} />
            </div>
            {subMeta ? (
              <span className="num text-[11px] text-ink-muted">{subMeta}</span>
            ) : null}
          </div>
        );
      },
    },
    risk_tier: {
      id: "risk_tier",
      meta: { align: "left" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-left font-semibold text-ink-muted">
          Bậc rủi ro
        </span>
      ),
      cell: ({ row }) => {
        const tierMeta = getScreenerRiskTierMeta(row.original.risk_tier);
        return <RiskBadge tone={tierMeta.tone} label={tierMeta.shortLabel} />;
      },
    },
    risk_market_percentile: {
      id: "risk_market_percentile",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Vị thế rủi ro
        </span>
      ),
      cell: ({ row }) => (
        <span className="num whitespace-nowrap text-ink">
          {formatTopRiskPercentileLabel(row.original.risk_market_percentile) ??
            "N/A"}
        </span>
      ),
    },
    relative_risk_lift: {
      id: "relative_risk_lift",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          So nền TT
        </span>
      ),
      cell: ({ row }) => (
        <span className="num whitespace-nowrap">
          {formatRelativeRiskLiftLabel(row.original.relative_risk_lift) ??
            "N/A"}
        </span>
      ),
    },
    market_base_rate: {
      id: "market_base_rate",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Tỷ lệ nền
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">{formatPercent(row.original.market_base_rate)}</span>
      ),
    },
    altman: {
      id: "altman",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Altman Z″"
          field="altman_z_double_prime"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => {
        const z = row.original.altman_z_double_prime;
        const zone = row.original.altman_zone
          ? ALTMAN_ZONE_META[row.original.altman_zone]
          : null;
        if (density === "compact") {
          return (
            <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
              <span className="num font-medium">{formatNumber(z, 2)}</span>
              {zone ? <RiskBadge tone={zone.tone} label={zone.shortLabel} /> : null}
            </div>
          );
        }
        return (
          <div className="flex flex-col items-end gap-0.5 whitespace-nowrap">
            <span className="num font-medium">{formatNumber(z, 2)}</span>
            {zone ? <RiskBadge tone={zone.tone} label={zone.shortLabel} /> : null}
          </div>
        );
      },
    },
    altman_em_score: {
      id: "altman_em_score",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Altman EM
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">{formatNumber(row.original.altman_em_score, 2)}</span>
      ),
    },
    f_score: {
      id: "f_score",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="F-Score"
          field="piotroski_f_score"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span className="num">
          {row.original.piotroski_f_score !== null &&
          row.original.piotroski_f_score !== undefined
            ? `${row.original.piotroski_f_score}/9`
            : "N/A"}
        </span>
      ),
    },
    beneish: {
      id: "beneish",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Beneish M"
          field="beneish_m8_score"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => {
        const m = row.original.beneish_m8_score;
        const flag = row.original.beneish_flag
          ? BENEISH_FLAG_META[row.original.beneish_flag]
          : null;
        if (density === "compact") {
          return (
            <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
              <span className="num">{formatNumber(m, 2)}</span>
              {flag && row.original.beneish_flag !== "VALID" ? (
                <RiskBadge tone={flag.tone} label={flag.shortLabel} />
              ) : null}
            </div>
          );
        }
        return (
          <div className="flex flex-col items-end gap-0.5 whitespace-nowrap">
            <span className="num">{formatNumber(m, 2)}</span>
            {flag && row.original.beneish_flag !== "VALID" ? (
              <RiskBadge tone={flag.tone} label={flag.shortLabel} />
            ) : null}
          </div>
        );
      },
    },
    cluster_archetype_name: {
      id: "cluster_archetype_name",
      meta: { align: "left" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-left font-semibold text-ink-muted">
          Nhóm đặc trưng
        </span>
      ),
      cell: ({ row }) => (
        <span
          className="block max-w-[200px] truncate text-left text-ink-muted"
          title={row.original.cluster_archetype_name ?? ""}
        >
          {row.original.cluster_archetype_name ?? "—"}
        </span>
      ),
    },
    anomaly_flag: {
      id: "anomaly_flag",
      meta: { align: "left" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-left font-semibold text-ink-muted">
          Ngoại lai
        </span>
      ),
      cell: ({ row }) => {
        const meta = formatAnomalyFlagMeta(row.original.anomaly_flag);
        return <RiskBadge tone={meta.tone} label={meta.label} />;
      },
    },
    red_flags: {
      id: "red_flags",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Cờ đỏ"
          field="red_flag_count"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span
          className={cn(
            "num font-medium",
            row.original.red_flag_count >= 2
              ? "text-risk-high"
              : row.original.red_flag_count === 1
                ? "text-risk-mid"
                : "text-ink-muted",
          )}
        >
          {row.original.red_flag_count}
        </span>
      ),
    },
    critical_red_flag_count: {
      id: "critical_red_flag_count",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Cờ nghiêm trọng
        </span>
      ),
      cell: ({ row }) => (
        <span
          className={cn(
            "num font-medium",
            row.original.critical_red_flag_count >= 1
              ? "text-risk-high"
              : "text-ink-muted",
          )}
        >
          {row.original.critical_red_flag_count}
        </span>
      ),
    },
    accruals_to_assets: {
      id: "accruals_to_assets",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Dồn tích/TS
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatPercent(row.original.accruals_to_assets)}
        </span>
      ),
    },
    core_operating_margin: {
      id: "core_operating_margin",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Biên LN lõi
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatPercent(row.original.core_operating_margin)}
        </span>
      ),
    },
    non_core_distortion_to_assets: {
      id: "non_core_distortion_to_assets",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          Biến dạng phi lõi
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatPercent(row.original.non_core_distortion_to_assets)}
        </span>
      ),
    },
    core_to_ebt_ratio: {
      id: "core_to_ebt_ratio",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          LN lõi/EBT
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatPercent(row.original.core_to_ebt_ratio)}
        </span>
      ),
    },
    roe_total: {
      id: "roe_total",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="ROE"
          field="roe_total"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span className="num">{formatPercent(row.original.roe_total)}</span>
      ),
    },
    roa: {
      id: "roa",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <span className="block text-right font-semibold text-ink-muted">
          ROA
        </span>
      ),
      cell: ({ row }) => (
        <span className="num">{formatPercent(row.original.roa)}</span>
      ),
    },
    ebit_margin: {
      id: "ebit_margin",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Biên EBIT"
          field="ebit_margin"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span className="num">{formatPercent(row.original.ebit_margin)}</span>
      ),
    },
    debt_to_equity: {
      id: "debt_to_equity",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Nợ/VCSH"
          field="debt_to_equity"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatMultiple(row.original.debt_to_equity)}
        </span>
      ),
    },
    interest_coverage: {
      id: "interest_coverage",
      meta: { align: "right" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="EBIT/Lãi vay"
          field="interest_coverage"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
        />
      ),
      cell: ({ row }) => (
        <span className="num">
          {formatMultiple(row.original.interest_coverage)}
        </span>
      ),
    },
  };

  const pinnedCols: ColumnDef<ScreenerItem>[] = [
    {
      id: "ticker",
      meta: { align: "left" } satisfies ScreenerColumnMeta,
      header: () => (
        <SortableHeader
          label="Mã"
          field="ticker"
          activeField={sortBy}
          activeDir={sortDir}
          onSort={onSort}
          align="left"
        />
      ),
      cell: ({ row }) => {
        const item = row.original;
        const checked = selectedTickers.includes(item.ticker);
        const watched = watchlistSet.has(item.ticker);
        return (
          <div className="flex items-center gap-1">
            <span className="relative inline-flex size-6 shrink-0 items-center justify-center">
              <input
                type="checkbox"
                checked={checked}
                disabled={!checked && selectedTickers.length >= 5}
                onChange={() => onToggleTickerCompare(item.ticker)}
                aria-label={`Chọn ${item.ticker} để so sánh`}
                className="peer absolute inset-0 size-6 cursor-pointer opacity-0 disabled:cursor-not-allowed"
              />
              <span
                aria-hidden="true"
                className="pointer-events-none inline-flex size-4 items-center justify-center rounded border border-border-strong bg-surface text-[10px] font-bold leading-none peer-checked:border-primary peer-checked:bg-primary peer-checked:text-primary-foreground peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-disabled:opacity-45"
              >
                {checked ? "✓" : null}
              </span>
            </span>
            <button
              type="button"
              aria-label={
                watched
                  ? `Bỏ theo dõi ${item.ticker}`
                  : `Theo dõi ${item.ticker}`
              }
              onClick={() => toggleWatchlistTicker(item.ticker)}
              className={cn(
                "inline-flex size-6 shrink-0 items-center justify-center rounded hover:bg-surface-muted",
                watched ? "text-accent-ink" : "text-ink-subtle",
              )}
            >
              <Bookmark
                className={cn("size-3.5", watched && "fill-current")}
              />
            </button>
            <Link
              href={`/companies/${item.ticker}`}
              className="inline-flex min-h-6 min-w-6 items-center px-0.5 font-mono font-semibold text-accent-ink hover:underline"
            >
              {item.ticker}
            </Link>
          </div>
        );
      },
    },
    {
      id: "company_name_vn",
      accessorKey: "company_name_vn",
      meta: { align: "left" } satisfies ScreenerColumnMeta,
      header: "Doanh nghiệp / Ngành",
      cell: ({ row }) => {
        const name = row.original.company_name_vn ?? "—";
        const sectorLabel = row.original.icb_name_level2 ?? "—";
        const exchange = row.original.exchange;
        if (density === "compact") {
          return (
            <div
              className="flex max-w-[140px] items-center gap-1.5 sm:max-w-[170px]"
              title={`${name} · ${sectorLabel}${exchange ? ` (${exchange})` : ""}`}
            >
              <span className="truncate font-medium text-ink">{name}</span>
              {exchange ? (
                <span className="shrink-0 font-mono text-[10px] text-ink-subtle">
                  {exchange}
                </span>
              ) : null}
            </div>
          );
        }
        return (
          <div className="max-w-[140px] sm:max-w-[165px]">
            <div className="truncate font-medium text-ink" title={name}>
              {name}
            </div>
            <div className="truncate text-[11px] text-ink-muted">
              {sectorLabel}
              {exchange ? ` · ${exchange}` : ""}
            </div>
          </div>
        );
      },
    },
  ];

  const selectedOptionalCols = SCREENER_OPTIONAL_COLUMNS.filter((opt) =>
    visibleColumnSet.has(opt.id),
  ).map((opt) => optionalCols[opt.id]);

  return [...pinnedCols, ...selectedOptionalCols];
}
