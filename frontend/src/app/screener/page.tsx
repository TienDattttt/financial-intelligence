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
  Download,
  GitCompare,
  LayoutList,
  RotateCcw,
  ScatterChart as ScatterIcon,
  SlidersHorizontal,
  Table2,
  X,
} from "lucide-react";
import { ScreenerScatterChart } from "@/components/charts/ScreenerScatterChart";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { useScreener } from "@/hooks/use-finintel-queries";
import {
  ALTMAN_ZONE_META,
  BENEISH_FLAG_META,
  getDistressRiskBadge,
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import {
  buildCsvContent,
  formatMultiple,
  formatNumber,
  formatPercent,
} from "@/lib/format";
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

const VALID_SORT_FIELDS = new Set<ScreenerSortField>([
  "ticker",
  "calibrated_distress_prob",
  "altman_z_double_prime",
  "piotroski_f_score",
  "beneish_m8_score",
  "red_flag_count",
  "roe_total",
  "debt_to_equity",
]);

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
        "inline-flex w-full items-center gap-1 font-semibold hover:text-ink",
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
  const watchlistSet = React.useMemo(() => new Set(watchlist), [watchlist]);

  const [viewMode, setViewMode] = React.useState<"TABLE" | "SCATTER">("TABLE");
  const [selectedTickers, setSelectedTickers] = React.useState<string[]>([]);
  const [mobileFiltersOpen, setMobileFiltersOpen] = React.useState(false);

  React.useEffect(() => {
    if (!mobileFiltersOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileFiltersOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileFiltersOpen]);

  // Parse URL params safely
  const qParam = searchParams.get("q") ?? "";
  const sectorParam = searchParams.get("icb_code_level2") ?? "";
  const altmanParam = searchParams.get("altman_zone") ?? "";
  const beneishParam = searchParams.get("beneish_flag") ?? "";
  const minFParam = searchParams.get("min_f_score") ?? "";
  const minProbParam = searchParams.get("min_distress_prob") ?? "";
  const maxProbParam = searchParams.get("max_distress_prob") ?? "";
  const minRfParam = searchParams.get("min_red_flags") ?? "";
  const maxRfParam = searchParams.get("max_red_flags") ?? "";
  const rawSortBy = searchParams.get("sort_by") as ScreenerSortField | null;
  const sortBy: ScreenerSortField =
    rawSortBy && VALID_SORT_FIELDS.has(rawSortBy)
      ? rawSortBy
      : "calibrated_distress_prob";
  const sortDir: SortDirection =
    searchParams.get("sort_dir") === "asc" ? "asc" : "desc";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const [qDraft, setQDraft] = React.useState(qParam);
  React.useEffect(() => {
    setQDraft(qParam);
  }, [qParam]);

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

  const columns = React.useMemo<ColumnDef<ScreenerItem>[]>(
    () => [
      {
        id: "ticker",
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
            <div className="flex items-center gap-1.5">
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
        accessorKey: "company_name_vn",
        header: "Doanh nghiệp / Ngành",
        cell: ({ row }) => (
          <div className="max-w-[200px]">
            <div className="truncate font-medium text-ink" title={row.original.company_name_vn ?? ""}>
              {row.original.company_name_vn ?? "—"}
            </div>
            <div className="truncate text-[11px] text-ink-muted">
              {row.original.icb_name_level2 ?? "—"}
              {row.original.exchange ? ` · ${row.original.exchange}` : ""}
            </div>
          </div>
        ),
      },
      {
        id: "altman",
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
            <div className="flex flex-col items-end gap-0.5">
              <span className="num font-medium">{formatNumber(z, 2)}</span>
              {zone ? <RiskBadge tone={zone.tone} label={zone.shortLabel} /> : null}
            </div>
          );
        },
      },
      {
        id: "f_score",
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
      {
        id: "beneish",
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
            <div className="flex flex-col items-end gap-0.5">
              <span className="num">{formatNumber(m, 2)}</span>
              {flag && row.original.beneish_flag !== "VALID" ? (
                <RiskBadge tone={flag.tone} label={flag.shortLabel} />
              ) : null}
            </div>
          );
        },
      },
      {
        id: "distress",
        header: () => (
          <SortableHeader
            label="XS kiệt quệ"
            field="calibrated_distress_prob"
            activeField={sortBy}
            activeDir={sortDir}
            onSort={handleSort}
          />
        ),
        cell: ({ row }) => {
          const p = row.original.calibrated_distress_prob;
          const badge = getDistressRiskBadge(p);
          return (
            <div className="flex flex-col items-end gap-0.5">
              <span className="num font-semibold">{formatPercent(p)}</span>
              <RiskBadge tone={badge.tone} label={badge.shortLabel} />
            </div>
          );
        },
      },
      {
        id: "red_flags",
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
      {
        id: "roe_total",
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
      {
        id: "ebit_margin",
        header: () => <span className="block text-right font-semibold text-ink-muted">Biên EBIT</span>,
        cell: ({ row }) => (
          <span className="num">{formatPercent(row.original.ebit_margin)}</span>
        ),
      },
      {
        id: "debt_to_equity",
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
          <span className="num">{formatMultiple(row.original.debt_to_equity)}</span>
        ),
      },
      {
        id: "interest_coverage",
        header: () => (
          <span className="block text-right font-semibold text-ink-muted">EBIT/Lãi vay</span>
        ),
        cell: ({ row }) => (
          <span className="num">{formatMultiple(row.original.interest_coverage)}</span>
        ),
      },
    ],
    [handleSort, selectedTickers, sortBy, sortDir, toggleTickerCompare, watchlistSet],
  );

  const table = useReactTable({
    data: items,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
  });

  const handleExportCsv = () => {
    const headers = [
      "Mã",
      "Tên doanh nghiệp",
      "Sàn",
      "Ngành ICB cấp 2",
      "Altman Z\"",
      "Phân vùng Altman",
      "Piotroski F-Score",
      "Beneish M-Score",
      "XS kiệt quệ (%)",
      "Số cờ đỏ",
      "ROE (%)",
      "Biên EBIT (%)",
      "Nợ/VCSH (x)",
      "EBIT/Lãi vay (x)",
    ];
    const rows = items.map((it) => [
      it.ticker,
      it.company_name_vn ?? "",
      it.exchange ?? "",
      it.icb_name_level2 ?? "",
      it.altman_z_double_prime ?? "",
      (it.altman_zone && ALTMAN_ZONE_META[it.altman_zone]?.label) || "",
      it.piotroski_f_score ?? "",
      it.beneish_m8_score ?? "",
      it.calibrated_distress_prob !== null && it.calibrated_distress_prob !== undefined
        ? (it.calibrated_distress_prob * 100).toFixed(2)
        : "",
      it.red_flag_count,
      it.roe_total !== null && it.roe_total !== undefined
        ? (it.roe_total * 100).toFixed(2)
        : "",
      it.ebit_margin !== null && it.ebit_margin !== undefined
        ? (it.ebit_margin * 100).toFixed(2)
        : "",
      it.debt_to_equity ?? "",
      it.interest_coverage ?? "",
    ]);

    const csv = buildCsvContent(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finintel-screener-trang-${page}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const applyPreset = (patch: Record<string, string | null>) => {
    const base: Record<string, string | null> = {
      q: null,
      icb_code_level2: null,
      altman_zone: null,
      beneish_flag: null,
      min_f_score: null,
      min_distress_prob: null,
      max_distress_prob: null,
      min_red_flags: null,
      max_red_flags: null,
      sort_by: "calibrated_distress_prob",
      sort_dir: "desc",
    };
    updateParams({ ...base, ...patch }, true);
  };

  const toPctDisplay = (decimalStr: string) => {
    if (!decimalStr) return "";
    const n = Number(decimalStr);
    return Number.isFinite(n) ? String(Math.round(n * 1000) / 10) : "";
  };

  const activeFilterCount = React.useMemo(
    () =>
      [
        qParam,
        sectorParam,
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
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() =>
              applyPreset({
                altman_zone: "DISTRESS_ZONE",
                sort_by: "calibrated_distress_prob",
                sort_dir: "desc",
              })
            }
          >
            Rủi ro kiệt quệ cao
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() =>
              applyPreset({
                beneish_flag: "HIGH_MANIPULATION_RISK_SCREEN",
                sort_by: "beneish_m8_score",
                sort_dir: "desc",
              })
            }
          >
            Nghi vấn điều chỉnh LN
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() =>
              applyPreset({
                min_red_flags: "2",
                sort_by: "red_flag_count",
                sort_dir: "desc",
              })
            }
          >
            Nhiều cờ đỏ (≥2)
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() =>
              applyPreset({
                altman_zone: "SAFE_ZONE",
                min_f_score: "6",
                max_red_flags: "1",
                sort_by: "roe_total",
                sort_dir: "desc",
              })
            }
          >
            Tài chính vững mạnh
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => applyPreset({})}
          >
            <RotateCcw className="size-3" />
            Đặt lại
          </Button>
        </div>
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

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        {/* Left Filter Rail (Desktop) / Collapsible Drawer (Mobile < lg) */}
        <div
          id="screener-filter-drawer"
          role={mobileFiltersOpen ? "dialog" : undefined}
          aria-modal={mobileFiltersOpen ? true : undefined}
          aria-label={mobileFiltersOpen ? "Bộ lọc doanh nghiệp" : undefined}
          className={cn(
            "lg:block lg:w-64 lg:shrink-0",
            mobileFiltersOpen
              ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
              : "hidden",
          )}
          onClick={() => setMobileFiltersOpen(false)}
        >
          <Card
            className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded lg:border"
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
            <CardContent className="space-y-3 p-3.5 text-xs">
            <div className="space-y-1">
              <label htmlFor="scr-q" className="font-semibold text-ink">
                Tìm mã / tên doanh nghiệp
              </label>
              <Input
                id="scr-q"
                value={qDraft}
                onChange={(e) => setQDraft(e.target.value)}
                placeholder="VD: HPG, thép..."
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-sector" className="font-semibold text-ink">
                Ngành ICB cấp 2
              </label>
              <Select
                id="scr-sector"
                value={sectorParam}
                onChange={(e) =>
                  updateParams({ icb_code_level2: e.target.value || null })
                }
                className="w-full"
              >
                <option value="">Tất cả các ngành</option>
                {sectorsLevel2.map((sec) => (
                  <option key={sec.icb_code_level2} value={sec.icb_code_level2}>
                    {sec.icb_name_level2} ({sec.issuer_count})
                  </option>
                ))}
              </Select>
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-altman" className="font-semibold text-ink">
                Phân vùng Altman Z″
              </label>
              <Select
                id="scr-altman"
                value={altmanParam}
                onChange={(e) =>
                  updateParams({ altman_zone: e.target.value || null })
                }
                className="w-full"
              >
                <option value="">Tất cả</option>
                <option value="SAFE_ZONE">An toàn (Z″ &gt; 2,60)</option>
                <option value="GREY_ZONE">Vùng xám (1,10–2,60)</option>
                <option value="DISTRESS_ZONE">Nguy cơ kiệt quệ (Z″ &lt; 1,10)</option>
              </Select>
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-beneish" className="font-semibold text-ink">
                Beneish M-Score
              </label>
              <Select
                id="scr-beneish"
                value={beneishParam}
                onChange={(e) =>
                  updateParams({ beneish_flag: e.target.value || null })
                }
                className="w-full"
              >
                <option value="">Tất cả</option>
                <option value="HIGH_MANIPULATION_RISK_SCREEN">
                  Nghi vấn điều chỉnh LN (&gt; −1,78)
                </option>
                <option value="VALID">Bình thường</option>
                <option value="MIXED_TAXONOMY">Chuyển chuẩn mực (TT200→TT99)</option>
              </Select>
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-fscore" className="font-semibold text-ink">
                Piotroski F-Score tối thiểu
              </label>
              <Select
                id="scr-fscore"
                value={minFParam}
                onChange={(e) =>
                  updateParams({ min_f_score: e.target.value || null })
                }
                className="w-full"
              >
                <option value="">Không giới hạn</option>
                {[3, 4, 5, 6, 7, 8].map((v) => (
                  <option key={v} value={String(v)}>
                    Từ {v}/9 trở lên
                  </option>
                ))}
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="scr-prob-min" className="font-semibold text-ink">
                  XS kiệt quệ từ (%)
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
                <label htmlFor="scr-prob-max" className="font-semibold text-ink">
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

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="scr-rf-min" className="font-semibold text-ink">
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
                <label htmlFor="scr-rf-max" className="font-semibold text-ink">
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

        {/* Main Screener Workspace */}
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-border bg-surface px-3 py-2">
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
                      ? "bg-accent-ink text-white"
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
                      ? "bg-accent-ink text-white"
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
                disabled={items.length === 0}
                onClick={handleExportCsv}
              >
                <Download className="size-3" />
                Xuất CSV
              </Button>
            </div>
          </div>

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
                <Button type="button" variant="outline" size="sm" onClick={() => applyPreset({})}>
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
                        {headerGroup.headers.map((header, idx) => (
                          <th
                            key={header.id}
                            scope="col"
                            className={cn(
                              "px-2.5 py-2 font-semibold whitespace-nowrap",
                              idx === 0 &&
                                "sticky left-0 z-10 border-r border-border bg-surface-muted text-left",
                              idx === 1 && "text-left",
                              idx >= 2 && "text-right",
                            )}
                          >
                            {header.isPlaceholder
                              ? null
                              : flexRender(
                                  header.column.columnDef.header,
                                  header.getContext(),
                                )}
                          </th>
                        ))}
                      </tr>
                    ))}
                  </thead>
                  <tbody>
                    {table.getRowModel().rows.map((row) => (
                      <tr
                        key={row.id}
                        className="border-b border-border/60 hover:bg-surface-muted/40"
                      >
                        {row.getVisibleCells().map((cell, idx) => (
                          <td
                            key={cell.id}
                            className={cn(
                              "px-2.5",
                              density === "compact" ? "py-1.5" : "py-2.5",
                              idx === 0 &&
                                "sticky left-0 z-10 border-r border-border bg-surface text-left",
                              idx === 1 && "text-left",
                              idx >= 2 && "text-right",
                            )}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
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
