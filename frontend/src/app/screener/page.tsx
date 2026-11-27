"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  Columns3,
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
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { ScreenerFilterDrawer } from "@/components/screener/ScreenerFilterDrawer";
import { ScreenerV3FilterChips } from "@/components/screener/ScreenerFinancialFilters";
import { ScreenerSavedBar } from "@/components/screener/ScreenerSavedBar";
import {
  buildScreenerColumns,
  type ScreenerColumnMeta,
} from "@/components/screener/screener-table-columns";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { type ComboboxOption } from "@/components/ui/combobox";
import { useScreener } from "@/hooks/use-finintel-queries";
import { observeResponseSnapshotHeader } from "@/lib/api-client";
import {
  ALTMAN_ZONE_META,
  BENEISH_FLAG_META,
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import {
  BUILTIN_SCREENER_PRESETS,
  buildScreenerExportUrl,
  countActiveScreenerFilters,
  filtersToApiQueryParams,
  filtersToUrlPatch,
  formatScreenerExportError,
  getSavedScreensServerSnapshot,
  getSavedScreensSnapshot,
  getVisibleColumnsServerSnapshot,
  getVisibleColumnsSnapshot,
  parseContentDispositionFilename,
  parseScreenerFilters,
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
  VALID_EXCHANGES,
  VALID_RISK_TIERS,
  type ScreenerFilterState,
  type ScreenerOptionalColumnId,
  type ScreenerRiskTier,
} from "@/lib/saved-screens-store";
import {
  getDensityServerSnapshot,
  getDensitySnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  setTableDensity,
  subscribePreferences,
} from "@/lib/watchlist-store";
import { cn } from "@/lib/utils";
import type {
  ExchangeCode,
  ScreenerQueryParams,
  ScreenerSortField,
} from "@/types/api";

const PAGE_SIZE = 50;
const RISK_TIER_ORDER = new Map<ScreenerRiskTier, number>(
  VALID_RISK_TIERS.map((tier, idx) => [tier, idx]),
);

function ScreenerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    statementScope,
    periodType,
    queryPeriodEndDate = null,
    sectorsLevel2,
    cacheSnapshotKey,
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

  // Column customizer & risk-tier multi-select popover states
  const [columnsMenuOpen, setColumnsMenuOpen] = React.useState(false);
  const columnsMenuRef = React.useRef<HTMLDivElement | null>(null);
  const [riskTierMenuOpen, setRiskTierMenuOpen] = React.useState(false);
  const riskTierMenuRef = React.useRef<HTMLDivElement | null>(null);

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

  React.useEffect(() => {
    if (!riskTierMenuOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRiskTierMenuOpen(false);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (
        riskTierMenuRef.current &&
        !riskTierMenuRef.current.contains(e.target as Node)
      ) {
        setRiskTierMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("mousedown", onMouseDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("mousedown", onMouseDown);
    };
  }, [riskTierMenuOpen]);

  // Parse URL params safely via shared store parser (F7, L9)
  const parsedFilters = React.useMemo(
    () => parseScreenerFilters(searchParams),
    [searchParams],
  );
  const qParam = parsedFilters.q;
  const exchangeParam = (parsedFilters.exchange ?? "") as ExchangeCode | "";
  const sectorParam = parsedFilters.icb_code_level2;
  const altmanParam = parsedFilters.altman_zone;
  const riskTiersParam = React.useMemo(
    () => parsedFilters.risk_tiers ?? [],
    [parsedFilters.risk_tiers],
  );
  const beneishParam = parsedFilters.beneish_flag;
  const minFParam = parsedFilters.min_f_score;
  const minProbParam = parsedFilters.min_distress_prob;
  const maxProbParam = parsedFilters.max_distress_prob;
  const minRfParam = parsedFilters.min_red_flags;
  const maxRfParam = parsedFilters.max_red_flags;
  const sortBy = parsedFilters.sort_by;
  const sortDir = parsedFilters.sort_dir;
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const [qDraft, setQDraft] = React.useState(qParam);
  React.useEffect(() => {
    setQDraft(qParam);
  }, [qParam]);

  const currentFilterState = parsedFilters;

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

  const handleSubmitKeyword = React.useCallback(
    (e?: React.FormEvent) => {
      e?.preventDefault();
      const trimmed = qDraft.trim();
      updateParams({ q: trimmed || null }, true);
    },
    [qDraft, updateParams],
  );

  const applyRiskTiers = React.useCallback(
    (nextTiers: readonly ScreenerRiskTier[]) => {
      const sorted = Array.from(new Set(nextTiers)).sort(
        (a, b) => (RISK_TIER_ORDER.get(a) ?? 9) - (RISK_TIER_ORDER.get(b) ?? 9),
      );
      if (sorted.length === 0) {
        updateParams({ risk_tier: null, risk_tiers: null }, true);
      } else if (sorted.length === 1) {
        updateParams({ risk_tier: sorted[0], risk_tiers: null }, true);
      } else {
        updateParams({ risk_tier: null, risk_tiers: sorted.join(",") }, true);
      }
    },
    [updateParams],
  );

  const queryParams = React.useMemo<ScreenerQueryParams>(
    () =>
      filtersToApiQueryParams(parsedFilters, {
        statement_scope: statementScope,
        period_type: periodType,
        period_end_date: queryPeriodEndDate,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    [page, parsedFilters, periodType, queryPeriodEndDate, statementScope],
  );

  const screenerQuery = useScreener(queryParams, cacheSnapshotKey);

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

  const allColumns = React.useMemo(
    () =>
      buildScreenerColumns({
        density,
        sortBy,
        sortDir,
        onSort: handleSort,
        selectedTickers,
        onToggleTickerCompare: toggleTickerCompare,
        watchlistSet,
        visibleColumnSet,
      }),
    [
      density,
      handleSort,
      selectedTickers,
      sortBy,
      sortDir,
      toggleTickerCompare,
      visibleColumnSet,
      watchlistSet,
    ],
  );

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
      observeResponseSnapshotHeader(res);
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

  const activeFilterCount = React.useMemo(
    () => countActiveScreenerFilters(parsedFilters),
    [parsedFilters],
  );

  const exchangeOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả sàn" },
      ...VALID_EXCHANGES.map((ex) => ({ value: ex, label: ex })),
    ],
    [],
  );

  const sectorOptions = React.useMemo<ComboboxOption[]>(
    () => [
      { value: "", label: "Tất cả ngành" },
      ...sectorsLevel2.map((sec) => ({
        value: sec.icb_code_level2,
        label: sec.icb_name_level2,
        hint:
          activeFilterCount === 0 && statementScope === "CONSOLIDATED"
            ? `(${sec.issuer_count})`
            : undefined,
      })),
    ],
    [activeFilterCount, sectorsLevel2, statementScope],
  );

  const riskTierOptions = React.useMemo<
    readonly { value: ScreenerRiskTier; label: string }[]
  >(
    () => [
      { value: "R5", label: "R5 — Rủi ro rất cao" },
      { value: "R4", label: "R4 — Rủi ro cao" },
      { value: "R3", label: "R3 — Cần theo dõi" },
      { value: "R2", label: "R2 — Rủi ro thấp" },
      { value: "R1", label: "R1 — Rủi ro rất thấp" },
    ],
    [],
  );

  const riskTierTriggerLabel = React.useMemo(() => {
    if (riskTiersParam.length === 0) return "Tất cả bậc R1–R5";
    if (riskTiersParam.length === 1) {
      return (
        riskTierOptions.find((o) => o.value === riskTiersParam[0])?.label ??
        riskTiersParam[0]
      );
    }
    return riskTiersParam.join(", ");
  }, [riskTierOptions, riskTiersParam]);

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
      <ScreenerSavedBar
        savedScreens={savedScreens}
        editingScreenId={editingScreenId}
        setEditingScreenId={setEditingScreenId}
        editingNameDraft={editingNameDraft}
        setEditingNameDraft={setEditingNameDraft}
        saveFormOpen={saveFormOpen}
        setSaveFormOpen={setSaveFormOpen}
        saveNameDraft={saveNameDraft}
        setSaveNameDraft={setSaveNameDraft}
        saveFeedback={saveFeedback}
        setSaveFeedback={setSaveFeedback}
        onApplyPreset={applyFilterPreset}
        onSaveCurrentScreen={handleSaveCurrentScreen}
        onRenameSubmit={handleRenameSubmit}
      />

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
        <ScreenerFilterDrawer
          mobileFiltersOpen={mobileFiltersOpen}
          setMobileFiltersOpen={setMobileFiltersOpen}
          qDraft={qDraft}
          setQDraft={setQDraft}
          onSubmitKeyword={handleSubmitKeyword}
          updateParams={updateParams}
          exchangeOptions={exchangeOptions}
          exchangeParam={exchangeParam}
          sectorOptions={sectorOptions}
          sectorParam={sectorParam}
          riskTierMenuRef={riskTierMenuRef}
          riskTierMenuOpen={riskTierMenuOpen}
          setRiskTierMenuOpen={setRiskTierMenuOpen}
          riskTierTriggerLabel={riskTierTriggerLabel}
          riskTiersParam={riskTiersParam}
          riskTierOptions={riskTierOptions}
          applyRiskTiers={applyRiskTiers}
          altmanOptions={altmanOptions}
          altmanParam={altmanParam}
          advancedFiltersOpen={advancedFiltersOpen}
          setAdvancedFiltersOpen={setAdvancedFiltersOpen}
          beneishOptions={beneishOptions}
          beneishParam={beneishParam}
          fScoreOptions={fScoreOptions}
          minFParam={minFParam}
          minProbParam={minProbParam}
          maxProbParam={maxProbParam}
          minRfParam={minRfParam}
          maxRfParam={maxRfParam}
          toPctDisplay={toPctDisplay}
          applyFilterPreset={applyFilterPreset}
          parsedFilters={parsedFilters}
          total={total}
        />

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
            {exchangeParam ? (
              <button
                type="button"
                onClick={() => updateParams({ exchange: null })}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>Sàn: {exchangeParam}</span>
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
            {riskTiersParam.map((tier) => (
              <button
                key={tier}
                type="button"
                onClick={() =>
                  applyRiskTiers(riskTiersParam.filter((t) => t !== tier))
                }
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
              >
                <span>Bậc: {tier}</span>
                <X className="size-3 text-ink-muted" />
              </button>
            ))}
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
                  XS rủi ro 1 năm: {toPctDisplay(minProbParam) || "0"}%–
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
                  Cờ đỏ: {minRfParam || "0"}–{maxRfParam || "10"}
                </span>
                <X className="size-3 text-ink-muted" />
              </button>
            ) : null}
            <ScreenerV3FilterChips
              filters={parsedFilters}
              updateParams={updateParams}
            />
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

              {viewMode === "TABLE" ? (
                <>
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
                                    const showRedundantHint =
                                      col.redundantWithDefault &&
                                      visibleColumnSet.has("distress");
                                    return (
                                      <label
                                        key={col.id}
                                        className="flex cursor-pointer items-center justify-between gap-2 rounded px-1.5 py-1 hover:bg-surface-muted"
                                      >
                                        <span className="flex items-center gap-2">
                                          <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={() => toggleVisibleColumn(col.id)}
                                            className="size-3.5 rounded border-border accent-accent-ink"
                                          />
                                          <span className="text-ink">{col.label}</span>
                                        </span>
                                        {showRedundantHint ? (
                                          <span className="shrink-0 text-[10px] text-ink-subtle">
                                            Đã gộp
                                          </span>
                                        ) : null}
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
                    aria-pressed={density === "compact"}
                    onClick={() =>
                      setTableDensity(density === "compact" ? "comfortable" : "compact")
                    }
                  >
                    <LayoutList className="size-3" />
                    {density === "compact" ? "Bảng gọn" : "Bảng thoáng"}
                  </Button>
                </>
              ) : null}

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
          ) : (
            <div className="space-y-3">
              {viewMode === "SCATTER" ? (
                <ScreenerScatterChart
                  items={items}
                  onSelectTicker={(ticker) => router.push(`/companies/${ticker}`)}
                />
              ) : (
                <Card>
                  <p className="border-b border-border/60 bg-surface-muted/40 px-3 py-1.5 text-[11px] text-ink-muted md:hidden">
                    Cuộn ngang để xem đầy đủ bậc rủi ro và các chỉ số →
                  </p>
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
                                      "text-left md:sticky md:left-0 md:z-10 md:border-r md:border-border md:bg-surface-muted",
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
                                    density === "compact"
                                      ? "py-1.5 whitespace-nowrap"
                                      : "py-2.5",
                                    idx === 0 &&
                                      "text-left md:sticky md:left-0 md:z-10 md:border-r md:border-border md:bg-surface",
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
                </Card>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-ink-muted">
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
            </div>
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
