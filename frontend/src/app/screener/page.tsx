"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Bookmark,
  BookmarkPlus,
  Check,
  Columns3,
  Download,
  GitCompare,
  LayoutList,
  Pencil,
  RotateCcw,
  ScatterChart as ScatterIcon,
  SlidersHorizontal,
  Table2,
  Trash2,
  X,
} from "lucide-react";
import { ScreenerScatterChart } from "@/components/charts/ScreenerScatterChart";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { Input } from "@/components/ui/primitives";
import { useScreener } from "@/hooks/use-finintel-queries";
import {
  ALTMAN_ZONE_META,
  BENEISH_FLAG_META,
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import {
  formatMultiple,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import {
  BUILTIN_SCREENER_PRESETS,
  buildScreenerExportUrl,
  deleteSavedScreen,
  filtersToUrlPatch,
  formatAnomalyFlagMeta,
  formatRelativeRiskLiftLabel,
  formatScreenerExportError,
  formatTopRiskPercentileLabel,
  getSavedScreensServerSnapshot,
  getSavedScreensSnapshot,
  getScreenerRiskTierMeta,
  getVisibleColumnsServerSnapshot,
  getVisibleColumnsSnapshot,
  parseContentDispositionFilename,
  renameSavedScreen,
  resetVisibleColumns,
  saveScreenerView,
  SCREENER_COLUMN_GROUPS,
  SCREENER_OPTIONAL_COLUMNS,
  showAllScreenerColumns,
  subscribeColumnPrefs,
  subscribeSavedScreens,
  toggleVisibleColumn,
  toggleVisibleColumnGroup,
  VALID_RISK_TIERS,
  VALID_SORT_FIELDS,
  type ScreenerFilterState,
  type ScreenerOptionalColumnId,
} from "@/lib/saved-screens-store";
import {
  getDensityServerSnapshot,
  getDensitySnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  setTableDensity,
  subscribePreferences,
  toggleWatchlistTicker,
} from "@/lib/watchlist-store";
import { cn } from "@/lib/utils";
import type {
  ScreenerItem,
  ScreenerQueryParams,
  ScreenerSortField,
  SortDirection,
} from "@/types/api";

const PAGE_SIZE = 50;
const VALID_SORT_SET = new Set<ScreenerSortField>(VALID_SORT_FIELDS);
const VALID_RISK_TIER_SET = new Set<string>(VALID_RISK_TIERS);

interface ScreenerColumnMeta {
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

function ScreenerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    statementScope,
    periodType,
    periodEndDate,
    sectorsLevel2,
    snapshotId,
  } = useAppState();

  const density = React.useSyncExternalStore(
    subscribePreferences,
    getDensitySnapshot,
    getDensityServerSnapshot,
  );
  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );
  const savedScreens = React.useSyncExternalStore(
    subscribeSavedScreens,
    getSavedScreensSnapshot,
    getSavedScreensServerSnapshot,
  );
  const visibleColumns = React.useSyncExternalStore(
    subscribeColumnPrefs,
    getVisibleColumnsSnapshot,
    getVisibleColumnsServerSnapshot,
  );

  const watchlistSet = React.useMemo(() => new Set(watchlist), [watchlist]);
  const visibleColumnSet = React.useMemo(
    () => new Set<ScreenerOptionalColumnId>(visibleColumns),
    [visibleColumns],
  );

  const [viewMode, setViewMode] = React.useState<"TABLE" | "SCATTER">("TABLE");
  const [selectedTickers, setSelectedTickers] = React.useState<string[]>([]);
  const [mobileFiltersOpen, setMobileFiltersOpen] = React.useState(false);
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = React.useState(false);

  // Full CSV export state
  const [isExporting, setIsExporting] = React.useState(false);
  const [exportError, setExportError] = React.useState<string | null>(null);

  // Column customizer popover state
  const [columnsMenuOpen, setColumnsMenuOpen] = React.useState(false);
  const columnsMenuRef = React.useRef<HTMLDivElement | null>(null);

  // Saved screens UI state
  const [saveFormOpen, setSaveFormOpen] = React.useState(false);
  const [saveNameDraft, setSaveNameDraft] = React.useState("");
  const [saveFeedback, setSaveFeedback] = React.useState<string | null>(null);
  const [editingScreenId, setEditingScreenId] = React.useState<string | null>(null);
  const [editingNameDraft, setEditingNameDraft] = React.useState("");

  React.useEffect(() => {
    if (!mobileFiltersOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileFiltersOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileFiltersOpen]);

  React.useEffect(() => {
    if (!columnsMenuOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setColumnsMenuOpen(false);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (
        columnsMenuRef.current &&
        !columnsMenuRef.current.contains(e.target as Node)
      ) {
        setColumnsMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("mousedown", onMouseDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("mousedown", onMouseDown);
    };
  }, [columnsMenuOpen]);

  // Parse URL params safely
  const qParam = searchParams.get("q") ?? "";
  const sectorParam = searchParams.get("icb_code_level2") ?? "";
  const altmanParam = searchParams.get("altman_zone") ?? "";
  const rawRiskTier = searchParams.get("risk_tier") ?? "";
  const riskTierParam = VALID_RISK_TIER_SET.has(rawRiskTier) ? rawRiskTier : "";
  const beneishParam = searchParams.get("beneish_flag") ?? "";
  const minFParam = searchParams.get("min_f_score") ?? "";
  const minProbParam = searchParams.get("min_distress_prob") ?? "";
  const maxProbParam = searchParams.get("max_distress_prob") ?? "";
  const minRfParam = searchParams.get("min_red_flags") ?? "";
  const maxRfParam = searchParams.get("max_red_flags") ?? "";
  const rawSortBy = searchParams.get("sort_by") as ScreenerSortField | null;
  const sortBy: ScreenerSortField =
    rawSortBy && VALID_SORT_SET.has(rawSortBy)
      ? rawSortBy
      : "calibrated_distress_prob";
  const sortDir: SortDirection =
    searchParams.get("sort_dir") === "asc" ? "asc" : "desc";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const [qDraft, setQDraft] = React.useState(qParam);
  React.useEffect(() => {
    setQDraft(qParam);
  }, [qParam]);

  const currentFilterState = React.useMemo<ScreenerFilterState>(
    () => ({
      q: qParam,
      icb_code_level2: sectorParam,
      altman_zone: (altmanParam || "") as ScreenerFilterState["altman_zone"],
      risk_tier: (riskTierParam || "") as ScreenerFilterState["risk_tier"],
      beneish_flag: (beneishParam || "") as ScreenerFilterState["beneish_flag"],
      min_f_score: minFParam,
      min_distress_prob: minProbParam,
      max_distress_prob: maxProbParam,
      min_red_flags: minRfParam,
      max_red_flags: maxRfParam,
      sort_by: sortBy,
      sort_dir: sortDir,
    }),
    [
      altmanParam,
      beneishParam,
      maxProbParam,
      maxRfParam,
      minFParam,
      minProbParam,
      minRfParam,
      qParam,
      riskTierParam,
      sectorParam,
      sortBy,
      sortDir,
    ],
  );

  const updateParams = React.useCallback(
    (patch: Record<string, string | null>, resetPage = true) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (!v) next.delete(k);
        else next.set(k, v);
      }
      if (resetPage) {
        next.delete("page");
      }
      const qs = next.toString();
      router.replace(qs ? `/screener?${qs}` : "/screener", { scroll: false });
    },
    [router, searchParams],
  );

  // Debounce keyword search into URL
  React.useEffect(() => {
    const trimmed = qDraft.trim();
    if (trimmed === qParam) return;
    const id = setTimeout(() => {
      updateParams({ q: trimmed || null }, true);
    }, 250);
    return () => clearTimeout(id);
  }, [qDraft, qParam, updateParams]);

  const queryParams = React.useMemo<ScreenerQueryParams>(() => {
    const numOrNull = (raw: string) => {
      if (!raw) return null;
      const n = Number(raw);
      return Number.isFinite(n) ? n : null;
    };
    return {
      statement_scope: statementScope,
      period_type: periodType,
      period_end_date: periodEndDate,
      q: qParam || null,
      icb_code_level2: sectorParam || null,
      altman_zone: (altmanParam || null) as ScreenerQueryParams["altman_zone"],
      risk_tier: (riskTierParam || null) as ScreenerQueryParams["risk_tier"],
      beneish_flag: (beneishParam || null) as ScreenerQueryParams["beneish_flag"],
      min_f_score: numOrNull(minFParam),
      min_distress_prob: numOrNull(minProbParam),
      max_distress_prob: numOrNull(maxProbParam),
      min_red_flags: numOrNull(minRfParam),
      max_red_flags: numOrNull(maxRfParam),
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    };
  }, [
    altmanParam,
    beneishParam,
    maxProbParam,
    maxRfParam,
    minFParam,
    minProbParam,
    minRfParam,
    page,
    periodEndDate,
    periodType,
    qParam,
    riskTierParam,
    sectorParam,
    sortBy,
    sortDir,
    statementScope,
  ]);

  const screenerQuery = useScreener(queryParams, snapshotId);

  const handleSort = React.useCallback(
    (field: ScreenerSortField) => {
      if (sortBy === field) {
        updateParams({ sort_dir: sortDir === "desc" ? "asc" : "desc" }, true);
      } else {
        updateParams(
          { sort_by: field, sort_dir: field === "ticker" ? "asc" : "desc" },
          true,
        );
      }
    },
    [sortBy, sortDir, updateParams],
  );

  const toggleTickerCompare = React.useCallback((ticker: string) => {
    setSelectedTickers((prev) => {
      if (prev.includes(ticker)) return prev.filter((t) => t !== ticker);
      if (prev.length >= 5) return prev;
      return [...prev, ticker];
    });
  }, []);

  const items = screenerQuery.data?.items ?? [];
  const total = screenerQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const allColumns = React.useMemo<ColumnDef<ScreenerItem>[]>(() => {
    const optionalCols: Record<ScreenerOptionalColumnId, ColumnDef<ScreenerItem>> = {
      distress: {
        id: "distress",
        meta: { align: "right" } satisfies ScreenerColumnMeta,
        header: () => (
          <SortableHeader
            label="Tín nhiệm & 1Y PD"
            field="calibrated_distress_prob"
            activeField={sortBy}
            activeDir={sortDir}
            onSort={handleSort}
          />
        ),
        cell: ({ row }) => {
          const item = row.original;
          const p = item.calibrated_distress_prob;
          const tierMeta = getScreenerRiskTierMeta(item.risk_tier);
          const topRisk = formatTopRiskPercentileLabel(item.risk_market_percentile);
          const lift = formatRelativeRiskLiftLabel(item.relative_risk_lift);
          const subMeta = [topRisk, lift].filter(Boolean).join(" · ");
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
            Bậc tín nhiệm
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
            onSort={handleSort}
          />
        ),
        cell: ({ row }) => {
          const z = row.original.altman_z_double_prime;
          const zone = row.original.altman_zone
            ? ALTMAN_ZONE_META[row.original.altman_zone]
            : null;
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
            onSort={handleSort}
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
            onSort={handleSort}
          />
        ),
        cell: ({ row }) => {
          const m = row.original.beneish_m8_score;
          const flag = row.original.beneish_flag
            ? BENEISH_FLAG_META[row.original.beneish_flag]
            : null;
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
            onSort={handleSort}
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
            onSort={handleSort}
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
          <span className="block text-right font-semibold text-ink-muted">
            Biên EBIT
          </span>
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
            onSort={handleSort}
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
          <span className="block text-right font-semibold text-ink-muted">
            EBIT/Lãi vay
          </span>
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
            onSort={handleSort}
            align="left"
          />
        ),
        cell: ({ row }) => {
          const item = row.original;
          const checked = selectedTickers.includes(item.ticker);
          const watched = watchlistSet.has(item.ticker);
          return (
            <div className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={checked}
                disabled={!checked && selectedTickers.length >= 5}
                onChange={() => toggleTickerCompare(item.ticker)}
                aria-label={`Chọn ${item.ticker} để so sánh`}
                className="size-3.5 rounded border-border accent-accent-ink"
              />
              <button
                type="button"
                aria-label={
                  watched
                    ? `Bỏ theo dõi ${item.ticker}`
                    : `Theo dõi ${item.ticker}`
                }
                onClick={() => toggleWatchlistTicker(item.ticker)}
                className={cn(
                  "rounded p-0.5 hover:bg-surface-muted",
                  watched ? "text-accent-ink" : "text-ink-subtle",
                )}
              >
                <Bookmark
                  className={cn("size-3.5", watched && "fill-current")}
                />
              </button>
              <Link
                href={`/companies/${item.ticker}`}
                className="font-mono font-semibold text-accent-ink hover:underline"
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
        cell: ({ row }) => (
          <div className="max-w-[160px]">
            <div
              className="truncate font-medium text-ink"
              title={row.original.company_name_vn ?? ""}
            >
              {row.original.company_name_vn ?? "—"}
            </div>
            <div className="truncate text-[11px] text-ink-muted">
              {row.original.icb_name_level2 ?? "—"}
              {row.original.exchange ? ` · ${row.original.exchange}` : ""}
            </div>
          </div>
        ),
      },
    ];

    const selectedOptionalCols = SCREENER_OPTIONAL_COLUMNS.filter((opt) =>
      visibleColumnSet.has(opt.id),
    ).map((opt) => optionalCols[opt.id]);

    return [...pinnedCols, ...selectedOptionalCols];
  }, [
    handleSort,
    selectedTickers,
    sortBy,
    sortDir,
    toggleTickerCompare,
    visibleColumnSet,
    watchlistSet,
  ]);

  const table = useReactTable({
    data: items,
    columns: allColumns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
  });

  const handleExportCsv = React.useCallback(async () => {
    setIsExporting(true);
    setExportError(null);
    try {
      const exportUrl = buildScreenerExportUrl(queryParams);
      const res = await fetch(exportUrl, {
        headers: { Accept: "text/csv, application/json" },
      });
      if (!res.ok) {
        let detail = "";
        try {
          const payload = (await res.json()) as { detail?: unknown };
          if (typeof payload.detail === "string") detail = payload.detail;
        } catch {
          detail = "";
        }
        setExportError(formatScreenerExportError(res.status, detail));
        return;
      }

      const blob = await res.blob();
      const filename = parseContentDispositionFilename(
        res.headers.get("Content-Disposition"),
      );
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } catch {
      setExportError(formatScreenerExportError(null));
    } finally {
      setIsExporting(false);
    }
  }, [queryParams]);

  const applyFilterPreset = React.useCallback(
    (filters: Partial<ScreenerFilterState>) => {
      updateParams(filtersToUrlPatch(filters), true);
    },
    [updateParams],
  );

  const handleSaveCurrentScreen = (e: React.FormEvent) => {
    e.preventDefault();
    const res = saveScreenerView(saveNameDraft, currentFilterState);
    if (!res.ok) {
      setSaveFeedback(res.error);
      return;
    }
    setSaveFeedback(null);
    setSaveNameDraft("");
    setSaveFormOpen(false);
  };

  const handleRenameSubmit = (e: React.FormEvent, id: string) => {
    e.preventDefault();
    const res = renameSavedScreen(id, editingNameDraft);
    if (!res.ok) {
      setSaveFeedback(res.error);
      return;
    }
    setSaveFeedback(null);
    setEditingScreenId(null);
    setEditingNameDraft("");
  };

  const toPctDisplay = (decimalStr: string) => {
    if (!decimalStr) return "";
    const n = Number(decimalStr);
    return Number.isFinite(n) ? String(Math.round(n * 1000) / 10) : "";
  };

  const sectorOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả các ngành" },
      ...sectorsLevel2.map((sec) => ({
        value: sec.icb_code_level2,
        label: sec.icb_name_level2,
        hint: `(${sec.issuer_count})`,
      })),
    ],
    [sectorsLevel2],
  );

  const riskTierOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả bậc R1–R5" },
      { value: "R5", label: "R5 — Rủi ro rất cao" },
      { value: "R4", label: "R4 — Rủi ro cao" },
      { value: "R3", label: "R3 — Cần theo dõi" },
      { value: "R2", label: "R2 — Rủi ro thấp" },
      { value: "R1", label: "R1 — Rủi ro rất thấp" },
    ],
    [],
  );

  const altmanOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả" },
      { value: "SAFE_ZONE", label: "An toàn (Z″ > 2,60)" },
      { value: "GREY_ZONE", label: "Vùng xám (1,10–2,60)" },
      { value: "DISTRESS_ZONE", label: "Nguy cơ kiệt quệ (Z″ < 1,10)" },
    ],
    [],
  );

  const beneishOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả" },
      {
        value: "HIGH_MANIPULATION_RISK_SCREEN",
        label: "Nghi vấn điều chỉnh LN (> −1,78)",
      },
      { value: "VALID", label: "Bình thường" },
      { value: "MIXED_TAXONOMY", label: "Chuyển chuẩn mực (TT200→TT99)" },
    ],
    [],
  );

  const fScoreOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Không giới hạn" },
      ...[3, 4, 5, 6, 7, 8].map((v) => ({
        value: String(v),
        label: `Từ ${v}/9 trở lên`,
      })),
    ],
    [],
  );

  const activeFilterCount = React.useMemo(
    () =>
      [
        qParam,
        sectorParam,
        riskTierParam,
        altmanParam,
        beneishParam,
        minFParam,
        minProbParam,
        maxProbParam,
        minRfParam,
        maxRfParam,
      ].filter(Boolean).length,
    [
      altmanParam,
      beneishParam,
      maxProbParam,
      maxRfParam,
      minFParam,
      minProbParam,
      minRfParam,
      qParam,
      riskTierParam,
      sectorParam,
    ],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Sàng lọc &amp; Cảnh báo doanh nghiệp
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Phạm vi {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
            {PERIOD_TYPE_LABELS[periodType] ?? periodType} · Tìm thấy{" "}
            <strong className="num text-ink">{total}</strong> doanh nghiệp
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {BUILTIN_SCREENER_PRESETS.map((preset) => (
            <Button
              key={preset.id}
              type="button"
              variant="outline"
              size="xs"
              title={preset.description}
              onClick={() => applyFilterPreset(preset.filters)}
            >
              {preset.name}
            </Button>
          ))}
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => applyFilterPreset({})}
          >
            <RotateCcw className="size-3" />
            Đặt lại
          </Button>
        </div>
      </div>

      {/* Saved Screens Bar */}
      <div className="rounded-lg border border-border bg-surface px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="font-semibold text-ink-muted">
              Bộ lọc đã lưu:
            </span>
            {savedScreens.length === 0 ? (
              <span className="text-ink-subtle">
                Chưa có bộ lọc cá nhân. Nhấn &ldquo;Lưu bộ lọc&rdquo; để tái sử dụng nhanh.
              </span>
            ) : (
              savedScreens.map((scr) =>
                editingScreenId === scr.id ? (
                  <form
                    key={scr.id}
                    onSubmit={(e) => handleRenameSubmit(e, scr.id)}
                    className="inline-flex items-center gap-1 rounded border border-border-strong bg-canvas px-1.5 py-0.5"
                  >
                    <input
                      type="text"
                      value={editingNameDraft}
                      onChange={(e) => setEditingNameDraft(e.target.value)}
                      maxLength={60}
                      aria-label="Tên mới cho bộ lọc"
                      className="h-5 w-36 bg-transparent text-xs text-ink focus:outline-none"
                      autoFocus
                    />
                    <button
                      type="submit"
                      aria-label="Xác nhận đổi tên"
                      className="rounded p-0.5 text-risk-low hover:bg-surface-muted"
                    >
                      <Check className="size-3" />
                    </button>
                    <button
                      type="button"
                      aria-label="Hủy đổi tên"
                      onClick={() => {
                        setEditingScreenId(null);
                        setSaveFeedback(null);
                      }}
                      className="rounded p-0.5 text-ink-muted hover:bg-surface-muted"
                    >
                      <X className="size-3" />
                    </button>
                  </form>
                ) : (
                  <div
                    key={scr.id}
                    className="inline-flex items-center gap-1 rounded border border-border bg-canvas px-2 py-0.5 text-xs text-ink"
                  >
                    <button
                      type="button"
                      onClick={() => applyFilterPreset(scr.filters)}
                      className="font-medium hover:text-accent-ink hover:underline"
                    >
                      {scr.name}
                    </button>
                    <button
                      type="button"
                      aria-label={`Đổi tên bộ lọc ${scr.name}`}
                      onClick={() => {
                        setEditingScreenId(scr.id);
                        setEditingNameDraft(scr.name);
                        setSaveFeedback(null);
                      }}
                      className="rounded p-0.5 text-ink-subtle hover:bg-surface-muted hover:text-ink"
                    >
                      <Pencil className="size-3" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Xóa bộ lọc ${scr.name}`}
                      onClick={() => deleteSavedScreen(scr.id)}
                      className="rounded p-0.5 text-ink-subtle hover:bg-risk-high-soft hover:text-risk-high"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </div>
                ),
              )
            )}
          </div>

          <div className="flex items-center gap-1.5">
            {saveFormOpen ? (
              <form
                onSubmit={handleSaveCurrentScreen}
                className="flex flex-wrap items-center gap-1.5"
              >
                <Input
                  type="text"
                  value={saveNameDraft}
                  onChange={(e) => setSaveNameDraft(e.target.value)}
                  placeholder="Tên bộ lọc (VD: Thép đòn bẩy cao)..."
                  aria-label="Tên bộ lọc muốn lưu"
                  maxLength={60}
                  className="h-7 w-52 text-xs"
                  autoFocus
                />
                <Button type="submit" size="xs">
                  Lưu
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => {
                    setSaveFormOpen(false);
                    setSaveFeedback(null);
                  }}
                >
                  Hủy
                </Button>
              </form>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() => {
                  setSaveFormOpen(true);
                  setSaveFeedback(null);
                }}
              >
                <BookmarkPlus className="size-3" />
                Lưu bộ lọc
              </Button>
            )}
          </div>
        </div>
        {saveFeedback ? (
          <p role="alert" className="mt-1.5 text-xs text-risk-high">
            {saveFeedback}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 lg:hidden">
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={mobileFiltersOpen}
          aria-controls="screener-filter-drawer"
          onClick={() => setMobileFiltersOpen(true)}
        >
          <SlidersHorizontal className="size-3.5" />
          <span>Bộ lọc doanh nghiệp</span>
          {activeFilterCount > 0 ? (
            <span className="num ml-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[11px] font-semibold text-accent-ink">
              {activeFilterCount}
            </span>
          ) : null}
        </Button>
        {screenerQuery.isFetching && screenerQuery.isPlaceholderData ? (
          <span className="text-xs text-accent-ink">Đang cập nhật...</span>
        ) : null}
      </div>

      <div className="space-y-3">
        {/* Horizontal Filter Toolbar (Desktop >= lg) / Collapsible Drawer (Mobile < lg) */}
        <div
          id="screener-filter-drawer"
          role={mobileFiltersOpen ? "dialog" : undefined}
          aria-modal={mobileFiltersOpen ? true : undefined}
          aria-label={mobileFiltersOpen ? "Bộ lọc doanh nghiệp" : undefined}
          className={cn(
            "lg:block",
            mobileFiltersOpen
              ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
              : "hidden",
          )}
          onClick={() => setMobileFiltersOpen(false)}
        >
          <Card
            className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded-lg lg:border"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-3.5 py-2.5 lg:hidden">
              <span className="text-sm font-semibold text-ink">
                Bộ lọc doanh nghiệp
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Đóng bộ lọc"
                onClick={() => setMobileFiltersOpen(false)}
              >
                <X className="size-4" />
              </Button>
            </div>
            <CardContent className="space-y-3 p-3 text-xs">
              {/* Primary Horizontal Filter Row on Desktop */}
              <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-12 lg:items-end">
                <div className="space-y-1 lg:col-span-3">
                  <label htmlFor="scr-q" className="block text-[11px] font-semibold text-ink-muted">
                    Tìm mã / tên doanh nghiệp
                  </label>
                  <Input
                    id="scr-q"
                    value={qDraft}
                    onChange={(e) => setQDraft(e.target.value)}
                    placeholder="VD: HPG, thép..."
                  />
                </div>

                <div className="space-y-1 lg:col-span-3">
                  <label htmlFor="scr-sector" className="block text-[11px] font-semibold text-ink-muted">
                    Ngành ICB cấp 2
                  </label>
                  <Combobox
                    id="scr-sector"
                    options={sectorOptions}
                    value={sectorParam}
                    onValueChange={(next) =>
                      updateParams({ icb_code_level2: next || null })
                    }
                    placeholder="Tất cả các ngành"
                    searchPlaceholder="Tìm ngành (VD: thep, dien)..."
                    ariaLabel="Ngành ICB cấp 2"
                  />
                </div>

                <div className="space-y-1 lg:col-span-2">
                  <label htmlFor="scr-risk-tier" className="block text-[11px] font-semibold text-ink-muted">
                    Bậc tín nhiệm (R1–R5)
                  </label>
                  <Combobox
                    id="scr-risk-tier"
                    options={riskTierOptions}
                    value={riskTierParam}
                    onValueChange={(next) =>
                      updateParams({ risk_tier: next || null })
                    }
                    placeholder="Tất cả bậc R1–R5"
                    searchPlaceholder="Chọn bậc R1–R5..."
                    ariaLabel="Bậc tín nhiệm (R1–R5)"
                  />
                </div>

                <div className="space-y-1 lg:col-span-2">
                  <label htmlFor="scr-altman" className="block text-[11px] font-semibold text-ink-muted">
                    Phân vùng Altman Z″
                  </label>
                  <Combobox
                    id="scr-altman"
                    options={altmanOptions}
                    value={altmanParam}
                    onValueChange={(next) =>
                      updateParams({ altman_zone: next || null })
                    }
                    placeholder="Tất cả"
                    searchPlaceholder="Tìm phân vùng Altman..."
                    ariaLabel="Phân vùng Altman Z″"
                  />
                </div>

                <div className="hidden lg:col-span-2 lg:flex lg:justify-end">
                  <Button
                    type="button"
                    variant={advancedFiltersOpen ? "secondary" : "outline"}
                    size="default"
                    className="w-full"
                    aria-expanded={advancedFiltersOpen}
                    onClick={() => setAdvancedFiltersOpen((v) => !v)}
                  >
                    <SlidersHorizontal className="size-3.5" />
                    <span>Bộ lọc nâng cao</span>
                    {[beneishParam, minFParam, minProbParam, maxProbParam, minRfParam, maxRfParam].filter(Boolean).length > 0 ? (
                      <span className="num rounded-sm bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold text-accent-ink">
                        {[beneishParam, minFParam, minProbParam, maxProbParam, minRfParam, maxRfParam].filter(Boolean).length}
                      </span>
                    ) : null}
                  </Button>
                </div>
              </div>

              {/* Advanced Filters Row (always visible inside mobile drawer; toggleable on desktop) */}
              <div
                className={cn(
                  "grid grid-cols-1 gap-2.5 border-t border-border/70 pt-2.5 lg:grid-cols-12 lg:items-end",
                  !advancedFiltersOpen && "lg:hidden",
                )}
              >
                <div className="space-y-1 lg:col-span-3">
                  <label htmlFor="scr-beneish" className="block text-[11px] font-semibold text-ink-muted">
                    Beneish M-Score
                  </label>
                  <Combobox
                    id="scr-beneish"
                    options={beneishOptions}
                    value={beneishParam}
                    onValueChange={(next) =>
                      updateParams({ beneish_flag: next || null })
                    }
                    placeholder="Tất cả"
                    searchPlaceholder="Tìm trạng thái Beneish..."
                    ariaLabel="Beneish M-Score"
                  />
                </div>

                <div className="space-y-1 lg:col-span-3">
                  <label htmlFor="scr-fscore" className="block text-[11px] font-semibold text-ink-muted">
                    Piotroski F-Score tối thiểu
                  </label>
                  <Combobox
                    id="scr-fscore"
                    options={fScoreOptions}
                    value={minFParam}
                    onValueChange={(next) =>
                      updateParams({ min_f_score: next || null })
                    }
                    placeholder="Không giới hạn"
                    searchPlaceholder="Chọn ngưỡng F-Score..."
                    ariaLabel="Piotroski F-Score tối thiểu"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2 lg:col-span-3">
                  <div className="space-y-1">
                    <label htmlFor="scr-prob-min" className="block text-[11px] font-semibold text-ink-muted">
                      1Y PD từ (%)
                    </label>
                    <Input
                      id="scr-prob-min"
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={toPctDisplay(minProbParam)}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (!v) return updateParams({ min_distress_prob: null });
                        const n = Number(v);
                        if (Number.isFinite(n)) {
                          const dec = Math.max(0, Math.min(1, n / 100));
                          updateParams({ min_distress_prob: dec.toFixed(3) });
                        }
                      }}
                      placeholder="0"
                    />
                  </div>
                  <div className="space-y-1">
                    <label htmlFor="scr-prob-max" className="block text-[11px] font-semibold text-ink-muted">
                      Đến (%)
                    </label>
                    <Input
                      id="scr-prob-max"
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={toPctDisplay(maxProbParam)}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (!v) return updateParams({ max_distress_prob: null });
                        const n = Number(v);
                        if (Number.isFinite(n)) {
                          const dec = Math.max(0, Math.min(1, n / 100));
                          updateParams({ max_distress_prob: dec.toFixed(3) });
                        }
                      }}
                      placeholder="100"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 lg:col-span-3">
                  <div className="space-y-1">
                    <label htmlFor="scr-rf-min" className="block text-[11px] font-semibold text-ink-muted">
                      Cờ đỏ từ
                    </label>
                    <Input
                      id="scr-rf-min"
                      type="number"
                      min={0}
                      max={9}
                      value={minRfParam}
                      onChange={(e) =>
                        updateParams({ min_red_flags: e.target.value || null })
                      }
                      placeholder="0"
                    />
                  </div>
                  <div className="space-y-1">
                    <label htmlFor="scr-rf-max" className="block text-[11px] font-semibold text-ink-muted">
                      Đến
                    </label>
                    <Input
                      id="scr-rf-max"
                      type="number"
                      min={0}
                      max={9}
                      value={maxRfParam}
                      onChange={(e) =>
                        updateParams({ max_red_flags: e.target.value || null })
                      }
                      placeholder="9"
                    />
                  </div>
                </div>
              </div>

              <div className="pt-1 lg:hidden">
                <Button
                  type="button"
                  size="sm"
                  className="w-full"
                  onClick={() => setMobileFiltersOpen(false)}
                >
                  Xem kết quả ({total})
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Active Filter Chips */}
        {activeFilterCount > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-[11px] font-medium text-ink-muted">Đang lọc:</span>
            {qParam ? (
              <button
                type="button"
                onClick={() => updateParams({ q: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>Từ khóa: &ldquo;{qParam}&rdquo;</span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {sectorParam ? (
              <button
                type="button"
                onClick={() => updateParams({ icb_code_level2: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>
                  Ngành:{" "}
                  {sectorsLevel2.find((s) => s.icb_code_level2 === sectorParam)
                    ?.icb_name_level2 ?? sectorParam}
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {riskTierParam ? (
              <button
                type="button"
                onClick={() => updateParams({ risk_tier: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>Bậc: {riskTierParam}</span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {altmanParam ? (
              <button
                type="button"
                onClick={() => updateParams({ altman_zone: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>
                  Altman: {ALTMAN_ZONE_META[altmanParam]?.shortLabel ?? altmanParam}
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {beneishParam ? (
              <button
                type="button"
                onClick={() => updateParams({ beneish_flag: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>
                  Beneish: {BENEISH_FLAG_META[beneishParam]?.shortLabel ?? beneishParam}
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {minFParam ? (
              <button
                type="button"
                onClick={() => updateParams({ min_f_score: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>F-Score ≥ {minFParam}</span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {minProbParam || maxProbParam ? (
              <button
                type="button"
                onClick={() =>
                  updateParams({ min_distress_prob: null, max_distress_prob: null })
                }
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>
                  1Y PD: {toPctDisplay(minProbParam) || "0"}%–
                  {toPctDisplay(maxProbParam) || "100"}%
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            {minRfParam || maxRfParam ? (
              <button
                type="button"
                onClick={() =>
                  updateParams({ min_red_flags: null, max_red_flags: null })
                }
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>
                  Cờ đỏ: {minRfParam || "0"}–{maxRfParam || "9"}
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
          </div>
        ) : null}

        {/* Main Full-Width Screener Workspace */}
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Button
                type="button"
                size="xs"
                disabled={selectedTickers.length < 2 || selectedTickers.length > 5}
                onClick={() =>
                  router.push(`/compare?tickers=${selectedTickers.join(",")}`)
                }
              >
                <GitCompare className="size-3.5" />
                So sánh ({selectedTickers.length}/5)
              </Button>
              {selectedTickers.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setSelectedTickers([])}
                  className="text-ink-muted hover:text-ink hover:underline"
                >
                  Bỏ chọn
                </button>
              ) : (
                <span className="text-ink-subtle">
                  Tích 2–5 mã để mở bảng so sánh song song
                </span>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {screenerQuery.isFetching && screenerQuery.isPlaceholderData ? (
                <span className="hidden rounded border border-accent-ink/30 bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink lg:inline-block">
                  Đang cập nhật...
                </span>
              ) : null}

              {/* Column Customizer Popover */}
              <div className="relative" ref={columnsMenuRef}>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  aria-expanded={columnsMenuOpen}
                  aria-haspopup="dialog"
                  onClick={() => setColumnsMenuOpen((v) => !v)}
                >
                  <Columns3 className="size-3" />
                  <span>
                    Cột ({visibleColumns.length}/{SCREENER_OPTIONAL_COLUMNS.length})
                  </span>
                </Button>

                {columnsMenuOpen ? (
                  <div
                    role="dialog"
                    aria-label="Tùy chỉnh cột phân tích"
                    className="absolute right-0 z-30 mt-1.5 w-80 max-w-[90vw] rounded-lg border border-border-strong bg-surface p-3 text-xs text-ink shadow-popover"
                  >
                    <div className="flex items-center justify-between border-b border-border pb-2">
                      <span className="font-semibold text-ink">
                        Tùy chỉnh cột bảng sàng lọc
                      </span>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={showAllScreenerColumns}
                          className="text-[11px] font-medium text-accent-ink hover:underline"
                        >
                          Hiện tất cả
                        </button>
                        <button
                          type="button"
                          onClick={resetVisibleColumns}
                          className="text-[11px] text-ink-muted hover:text-ink hover:underline"
                        >
                          Mặc định
                        </button>
                      </div>
                    </div>

                    <div className="mt-2 max-h-80 space-y-3 overflow-y-auto pr-1">
                      {SCREENER_COLUMN_GROUPS.map((group) => {
                        const groupCols = SCREENER_OPTIONAL_COLUMNS.filter(
                          (c) => c.group === group.id,
                        );
                        const allChecked = groupCols.every((c) =>
                          visibleColumnSet.has(c.id),
                        );
                        return (
                          <div key={group.id} className="space-y-1">
                            <div className="flex items-center justify-between">
                              <span className="text-[11px] font-semibold text-ink-muted">
                                {group.label}
                              </span>
                              <button
                                type="button"
                                onClick={() => toggleVisibleColumnGroup(group.id)}
                                className="text-[11px] text-accent-ink hover:underline"
                              >
                                {allChecked ? "Bỏ nhóm" : "Chọn nhóm"}
                              </button>
                            </div>
                            <div className="space-y-1 pl-0.5">
                              {groupCols.map((col) => {
                                const checked = visibleColumnSet.has(col.id);
                                return (
                                  <label
                                    key={col.id}
                                    className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-surface-muted"
                                  >
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      onChange={() => toggleVisibleColumn(col.id)}
                                      className="size-3.5 rounded border-border accent-accent-ink"
                                    />
                                    <span className="text-ink">{col.label}</span>
                                  </label>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>

              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() =>
                  setTableDensity(density === "compact" ? "comfortable" : "compact")
                }
              >
                <LayoutList className="size-3" />
                {density === "compact" ? "Bảng gọn" : "Bảng thoáng"}
              </Button>

              <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setViewMode("TABLE")}
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2 py-1 font-medium",
                    viewMode === "TABLE"
                      ? "bg-primary text-primary-foreground"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  <Table2 className="size-3" />
                  Bảng
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("SCATTER")}
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2 py-1 font-medium",
                    viewMode === "SCATTER"
                      ? "bg-primary text-primary-foreground"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  <ScatterIcon className="size-3" />
                  Biểu đồ phân tán
                </button>
              </div>

              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={total === 0 || isExporting}
                onClick={handleExportCsv}
                title={`Xuất toàn bộ ${total} doanh nghiệp khớp bộ lọc ra tệp CSV`}
              >
                <Download className="size-3" />
                {isExporting ? "Đang xuất CSV..." : `Xuất CSV (${total})`}
              </Button>
            </div>
          </div>

          {exportError ? (
            <div
              role="alert"
              className="flex items-center justify-between gap-2 rounded-lg border border-risk-high/30 bg-risk-high-soft px-3 py-2 text-xs text-risk-high"
            >
              <span>{exportError}</span>
              <button
                type="button"
                aria-label="Đóng thông báo lỗi xuất CSV"
                onClick={() => setExportError(null)}
                className="rounded p-0.5 hover:bg-surface/40"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ) : null}

          {screenerQuery.isLoading ? (
            <PageSkeleton />
          ) : screenerQuery.isError ? (
            <ErrorState
              message={screenerQuery.error.message}
              onRetry={() => screenerQuery.refetch()}
            />
          ) : items.length === 0 ? (
            <EmptyState
              title="Không có doanh nghiệp nào khớp bộ lọc"
              description="Thử nới lỏng điều kiện lọc hoặc đặt lại bộ lọc mặc định."
              action={
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => applyFilterPreset({})}
                >
                  Đặt lại bộ lọc
                </Button>
              }
            />
          ) : viewMode === "SCATTER" ? (
            <ScreenerScatterChart
              items={items}
              onSelectTicker={(ticker) => router.push(`/companies/${ticker}`)}
            />
          ) : (
            <Card>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    {table.getHeaderGroups().map((headerGroup) => (
                      <tr
                        key={headerGroup.id}
                        className="border-b border-border bg-surface-muted/70 text-ink-muted"
                      >
                        {headerGroup.headers.map((header, idx) => {
                          const meta = header.column.columnDef.meta as
                            | ScreenerColumnMeta
                            | undefined;
                          const align =
                            meta?.align ?? (idx <= 1 ? "left" : "right");
                          return (
                            <th
                              key={header.id}
                              scope="col"
                              className={cn(
                                "px-1.5 py-2 font-semibold whitespace-nowrap first:pl-2.5 last:pr-2.5",
                                idx === 0 &&
                                  "sticky left-0 z-10 border-r border-border bg-surface-muted text-left",
                                idx !== 0 &&
                                  (align === "left"
                                    ? "text-left"
                                    : "text-right"),
                              )}
                            >
                              {header.isPlaceholder
                                ? null
                                : flexRender(
                                    header.column.columnDef.header,
                                    header.getContext(),
                                  )}
                            </th>
                          );
                        })}
                      </tr>
                    ))}
                  </thead>
                  <tbody>
                    {table.getRowModel().rows.map((row) => (
                      <tr
                        key={row.id}
                        className="border-b border-border/60 hover:bg-surface-muted/40"
                      >
                        {row.getVisibleCells().map((cell, idx) => {
                          const meta = cell.column.columnDef.meta as
                            | ScreenerColumnMeta
                            | undefined;
                          const align =
                            meta?.align ?? (idx <= 1 ? "left" : "right");
                          return (
                            <td
                              key={cell.id}
                              className={cn(
                                "px-1.5 first:pl-2.5 last:pr-2.5",
                                density === "compact" ? "py-1.5" : "py-2.5",
                                idx === 0 &&
                                  "sticky left-0 z-10 border-r border-border bg-surface text-left",
                                idx !== 0 &&
                                  (align === "left"
                                    ? "text-left"
                                    : "text-right"),
                              )}
                            >
                              {flexRender(
                                cell.column.columnDef.cell,
                                cell.getContext(),
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs text-ink-muted">
                <span className="num">
                  Trang {page} / {totalPages} · Hiển thị {(page - 1) * PAGE_SIZE + 1}–
                  {Math.min(total, page * PAGE_SIZE)} trong {total} DN
                </span>
                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={page <= 1}
                    onClick={() => updateParams({ page: String(page - 1) }, false)}
                  >
                    Trang trước
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={page >= totalPages}
                    onClick={() => updateParams({ page: String(page + 1) }, false)}
                  >
                    Trang sau
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <React.Suspense fallback={<PageSkeleton />}>
      <ScreenerContent />
    </React.Suspense>
  );
}
