"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Briefcase,
  Building2,
  Check,
  Download,
  Globe2,
  Link2,
  Pin,
  PinOff,
  RotateCcw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { StressSectorBarChart } from "@/components/charts/StressSectorBarChart";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { useAllIssuers } from "@/hooks/use-finintel-queries";
import { apiClient } from "@/lib/api-client";
import { PERIOD_TYPE_LABELS, STATEMENT_SCOPE_LABELS } from "@/lib/domain-labels";
import {
  buildCsvContent,
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatVndBillions,
  MINUS_SIGN,
  normalizeSearchText,
} from "@/lib/format";
import {
  clampShockValue,
  computePortfolioStressMetrics,
  computeScenarioComparison,
  DEFAULT_SHOCKS,
  parseStressUrlParams,
  serializeStressUrlParams,
  SHOCK_BOUNDS,
  STRESS_PRESETS,
  type ShockUiState,
  type StressScope,
} from "@/lib/stress-test-helpers";
import { cn } from "@/lib/utils";
import {
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  subscribePreferences,
} from "@/lib/watchlist-store";
import type { StressScenarioRequest, StressTestResponse } from "@/types/api";

function DualShockControl({
  id,
  label,
  unit,
  min,
  max,
  step,
  value,
  onChange,
}: {
  id: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (next: number) => void;
}) {
  const span = max - min;
  const hasZeroInside = min < 0 && max > 0 && span > 0;
  const zeroPct = hasZeroInside ? ((0 - min) / span) * 100 : null;

  return (
    <div className="space-y-1.5 border-b border-border/60 pb-2.5 last:border-b-0 last:pb-0">
      <div className="flex items-center justify-between gap-2 text-xs">
        <label htmlFor={id} className="font-medium text-ink">
          {label}
        </label>
        <div className="flex items-center gap-1">
          <Input
            id={id}
            type="number"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(e) => onChange(clampShockValue(e.target.value, min, max, 0))}
            className="num h-7 w-20 px-1.5 text-right text-xs"
          />
          <span className="w-8 text-[11px] text-ink-muted">{unit}</span>
        </div>
      </div>
      <div className="relative pt-0.5">
        {zeroPct !== null ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-0 h-2.5 w-px bg-border-strong"
            style={{ left: `${zeroPct}%` }}
          />
        ) : null}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          aria-label={`${label} thanh trượt`}
          onChange={(e) => onChange(clampShockValue(e.target.value, min, max, 0))}
          className="relative z-10 w-full accent-accent-ink"
        />
      </div>
      <div className="num relative h-3.5 text-[10px] text-ink-subtle">
        <span className="absolute left-0 top-0">{min}</span>
        {zeroPct !== null ? (
          <span
            className="absolute top-0 -translate-x-1/2 font-medium text-ink-muted"
            style={{ left: `${zeroPct}%` }}
          >
            0
          </span>
        ) : null}
        <span className="absolute right-0 top-0">
          {max > 0 ? `+${max}` : max}
        </span>
      </div>
    </div>
  );
}

function formatSignedInteger(delta: number): string {
  if (delta === 0) return "0";
  const absFormatted = formatNumber(Math.abs(delta), 0);
  return delta < 0 ? `${MINUS_SIGN}${absFormatted}` : `+${absFormatted}`;
}

function formatSignedBillionsDelta(deltaVnd: number): string {
  if (!Number.isFinite(deltaVnd)) return "N/A";
  const billions = deltaVnd / 1_000_000_000;
  if (Math.abs(billions) < 0.05) return "0,0 tỷ";
  const absFormatted = formatNumber(Math.abs(billions), 1);
  return billions < 0
    ? `${MINUS_SIGN}${absFormatted} tỷ`
    : `+${absFormatted} tỷ`;
}

function StressTestContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { statementScope, periodType, periodEndDate, snapshotId } = useAppState();

  const localWatchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );

  const [initialUrlParsed] = React.useState(() =>
    parseStressUrlParams(searchParams, STRESS_PRESETS[1].values),
  );

  const [scope, setScope] = React.useState<StressScope>(
    initialUrlParsed.state.scope,
  );
  const [companyTicker, setCompanyTicker] = React.useState<string | null>(
    initialUrlParsed.state.companyTicker,
  );
  const [sharedWatchlistTickers, setSharedWatchlistTickers] = React.useState<
    string[] | null
  >(
    initialUrlParsed.state.scope === "WATCHLIST" &&
      initialUrlParsed.state.watchlistTickers.length > 0
      ? initialUrlParsed.state.watchlistTickers
      : null,
  );
  const [shocks, setShocks] = React.useState<ShockUiState>(
    initialUrlParsed.state.shocks,
  );

  const [companyQuery, setCompanyQuery] = React.useState("");
  const [result, setResult] = React.useState<StressTestResponse | null>(null);
  const [pinnedA, setPinnedA] = React.useState<StressTestResponse | null>(null);
  const [isCalculating, setIsCalculating] = React.useState(false);
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [selectedSectorCode, setSelectedSectorCode] = React.useState<string | null>(
    null,
  );
  const [activeTab, setActiveTab] = React.useState<"NEWLY" | "TOP">("NEWLY");
  const [compareTab, setCompareTab] = React.useState<"ISSUERS" | "SECTORS">(
    "ISSUERS",
  );
  const [mobileControlsOpen, setMobileControlsOpen] = React.useState(false);
  const [copiedLink, setCopiedLink] = React.useState(false);

  const issuersQuery = useAllIssuers(snapshotId);

  const effectiveWatchlistTickers = React.useMemo(
    () => sharedWatchlistTickers ?? localWatchlist,
    [localWatchlist, sharedWatchlistTickers],
  );

  const usingSharedWatchlist = React.useMemo(() => {
    if (scope !== "WATCHLIST" || !sharedWatchlistTickers) return false;
    if (sharedWatchlistTickers.length !== localWatchlist.length) return true;
    return sharedWatchlistTickers.some((t, idx) => t !== localWatchlist[idx]);
  }, [localWatchlist, scope, sharedWatchlistTickers]);

  const activeTickers = React.useMemo<string[] | null>(() => {
    if (scope === "MARKET") return null;
    if (scope === "COMPANY") {
      return companyTicker ? [companyTicker] : [];
    }
    return effectiveWatchlistTickers;
  }, [companyTicker, effectiveWatchlistTickers, scope]);

  const canRunSimulation =
    scope === "MARKET" || (activeTickers !== null && activeTickers.length > 0);

  // Sync URL query string whenever scenario parameters, scope, or selected tickers change
  React.useEffect(() => {
    const nextParams = serializeStressUrlParams({
      scope,
      companyTicker,
      watchlistTickers: effectiveWatchlistTickers,
      shocks,
    });
    const nextQs = nextParams.toString();
    const currentQs = searchParams.toString();
    if (nextQs !== currentQs) {
      router.replace(`${pathname}?${nextQs}`, { scroll: false });
    }
  }, [
    companyTicker,
    effectiveWatchlistTickers,
    pathname,
    router,
    scope,
    searchParams,
    shocks,
  ]);

  React.useEffect(() => {
    if (!mobileControlsOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileControlsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileControlsOpen]);

  const payload = React.useMemo<
    StressScenarioRequest & { tickers?: string[] | null }
  >(() => {
    const base: StressScenarioRequest & { tickers?: string[] | null } = {
      scenario_name: shocks.scenarioName.trim() || "Tùy chỉnh",
      statement_scope: statementScope,
      period_type: periodType,
      period_end_date: periodEndDate,
      revenue_shock_pct: Number((shocks.revPct / 100).toFixed(4)),
      gross_margin_shock_pp: Number((shocks.gmPp / 100).toFixed(4)),
      interest_rate_shock_bps: shocks.rateBps,
      dso_shock_days: shocks.dsoDays,
      dio_shock_days: shocks.dioDays,
      top_n: 100,
    };
    if (scope !== "MARKET" && activeTickers && activeTickers.length > 0) {
      base.tickers = activeTickers;
    }
    return base;
  }, [activeTickers, periodEndDate, periodType, scope, shocks, statementScope]);

  React.useEffect(() => {
    if (!canRunSimulation) {
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setIsCalculating(true);
      setErrorMsg(null);
      try {
        const res = await apiClient.postStressTest(payload, controller.signal);
        if (!controller.signal.aborted) {
          setResult(res);
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setErrorMsg(
            err instanceof Error
              ? err.message
              : "Không thể chạy mô phỏng kịch bản sốc.",
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsCalculating(false);
        }
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [canRunSimulation, payload]);

  const activeResult = canRunSimulation ? result : null;
  const activeErrorMsg = canRunSimulation ? errorMsg : null;
  const activeIsCalculating = canRunSimulation ? isCalculating : false;

  const isExtreme =
    shocks.revPct <= -40 || shocks.rateBps >= 600 || shocks.dsoDays >= 90;

  const selectedCompanyMeta = React.useMemo(() => {
    if (!companyTicker || !issuersQuery.data) return null;
    return (
      issuersQuery.data.find((it) => it.ticker === companyTicker) ?? null
    );
  }, [companyTicker, issuersQuery.data]);

  const companyMatches = React.useMemo(() => {
    const list = issuersQuery.data ?? [];
    const q = normalizeSearchText(companyQuery);
    if (!q) return list.slice(0, 8);
    return list
      .filter((iss) => {
        const tickerNorm = normalizeSearchText(iss.ticker);
        const nameNorm = normalizeSearchText(iss.company_name_vn ?? "");
        return tickerNorm.includes(q) || nameNorm.includes(q);
      })
      .slice(0, 8);
  }, [companyQuery, issuersQuery.data]);

  const portfolioMetrics = React.useMemo(() => {
    if (!activeResult || scope !== "WATCHLIST") return null;
    return computePortfolioStressMetrics(activeResult, effectiveWatchlistTickers);
  }, [activeResult, effectiveWatchlistTickers, scope]);

  const singleCompanyRow = React.useMemo(() => {
    if (!activeResult || scope !== "COMPANY") return null;
    return (
      activeResult.newly_distressed_issuers[0] ??
      activeResult.top_stressed_issuers[0] ??
      null
    );
  }, [activeResult, scope]);

  const comparison = React.useMemo(() => {
    if (!pinnedA || !activeResult) return null;
    return computeScenarioComparison(pinnedA, activeResult);
  }, [activeResult, pinnedA]);

  const issuerList = React.useMemo(() => {
    if (!activeResult) return [];
    const base =
      activeTab === "NEWLY"
        ? activeResult.newly_distressed_issuers
        : activeResult.top_stressed_issuers;
    if (!selectedSectorCode) return base;
    return base.filter((it) => it.icb_code_level2 === selectedSectorCode);
  }, [activeResult, activeTab, selectedSectorCode]);

  const selectedSectorName = React.useMemo(() => {
    if (!selectedSectorCode || !activeResult) return null;
    return (
      activeResult.sector_summary.find((s) => s.icb_code_level2 === selectedSectorCode)
        ?.icb_name_level2 ?? selectedSectorCode
    );
  }, [activeResult, selectedSectorCode]);

  const filteredCompareIssuers = React.useMemo(() => {
    if (!comparison) return [];
    if (!selectedSectorName) return comparison.issuerDeltas;
    return comparison.issuerDeltas.filter(
      (it) => it.icbNameLevel2 === selectedSectorName,
    );
  }, [comparison, selectedSectorName]);

  const filteredCompareSectors = React.useMemo(() => {
    if (!comparison) return [];
    if (!selectedSectorCode) return comparison.sectorDeltas;
    return comparison.sectorDeltas.filter(
      (s) => s.icbCodeLevel2 === selectedSectorCode,
    );
  }, [comparison, selectedSectorCode]);

  const handleCopyShareLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopiedLink(true);
      setTimeout(() => setCopiedLink(false), 1800);
    } catch {
      setCopiedLink(false);
    }
  };

  const handleExportCompareSectorsCsv = () => {
    const headers = [
      "Ngành (ICB cấp 2)",
      "Nợ rủi ro Kịch bản A (%)",
      "Nợ rủi ro Kịch bản B (%)",
      "Chênh lệch B-A (đ%)",
      "Nợ rủi ro A (tỷ)",
      "Nợ rủi ro B (tỷ)",
      "Chênh lệch nợ rủi ro B-A (tỷ)",
      "DN mới khó khăn A",
      "DN mới khó khăn B",
    ];
    const rows = filteredCompareSectors.map((s) => [
      s.icbNameLevel2,
      (s.debtAtRiskPctA * 100).toFixed(2),
      (s.debtAtRiskPctB * 100).toFixed(2),
      (s.debtAtRiskPctDelta * 100).toFixed(2),
      (s.debtAtRiskVndA / 1e9).toFixed(1),
      (s.debtAtRiskVndB / 1e9).toFixed(1),
      (s.debtAtRiskVndDelta / 1e9).toFixed(1),
      s.newlyDistressedA,
      s.newlyDistressedB,
    ]);
    const csv = buildCsvContent(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "finintel-stress-compare-sectors.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportIssuersCsv = () => {
    const headers = [
      "Mã",
      "Doanh nghiệp",
      "Ngành",
      "Tổng nợ vay (tỷ)",
      "ICR cơ sở (x)",
      "ICR sau sốc (x)",
      "EBIT TTM (tỷ)",
      "EBIT sau sốc (tỷ)",
      "CFO TTM (tỷ)",
      "CFO sau sốc (tỷ)",
    ];
    const rows = issuerList.map((it) => [
      it.ticker,
      it.company_name_vn ?? "",
      it.icb_name_level2 ?? "",
      it.total_debt !== null && it.total_debt !== undefined
        ? (it.total_debt / 1e9).toFixed(1)
        : "",
      it.interest_coverage ?? "",
      it.stressed_interest_coverage ?? "",
      it.ebit_ttm !== null && it.ebit_ttm !== undefined
        ? (it.ebit_ttm / 1e9).toFixed(1)
        : "",
      it.stressed_ebit_ttm !== null && it.stressed_ebit_ttm !== undefined
        ? (it.stressed_ebit_ttm / 1e9).toFixed(1)
        : "",
      it.cfo_ttm !== null && it.cfo_ttm !== undefined
        ? (it.cfo_ttm / 1e9).toFixed(1)
        : "",
      it.stressed_cfo_ttm !== null && it.stressed_cfo_ttm !== undefined
        ? (it.stressed_cfo_ttm / 1e9).toFixed(1)
        : "",
    ]);

    const csv = buildCsvContent(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finintel-stress-test.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Mô phỏng kịch bản sốc tài chính
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Phạm vi {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
            {PERIOD_TYPE_LABELS[periodType] ?? periodType} · Nợ rủi ro được định nghĩa khi hệ số khả năng trả lãi ICR &lt; 1,0x ·{" "}
            <Link href="/methodology" className="text-accent-ink hover:underline">
              Xem phương pháp
            </Link>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {STRESS_PRESETS.map((p) => (
            <Button
              key={p.label}
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setShocks(p.values)}
            >
              {p.label}
            </Button>
          ))}
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={handleCopyShareLink}
          >
            {copiedLink ? (
              <>
                <Check className="size-3 text-risk-low" />
                <span>Đã sao chép</span>
              </>
            ) : (
              <>
                <Link2 className="size-3" />
                <span>Chia sẻ liên kết</span>
              </>
            )}
          </Button>
        </div>
      </div>

      {/* 3 Simulation Scopes Selector */}
      <Card>
        <CardContent className="space-y-3 p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-ink">
                Phạm vi mô phỏng:
              </span>
              <div
                role="tablist"
                aria-label="Phạm vi mô phỏng kịch bản sốc"
                className="inline-flex flex-wrap rounded-md border border-border bg-canvas p-0.5 text-xs"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={scope === "MARKET"}
                  onClick={() => {
                    setScope("MARKET");
                    setSelectedSectorCode(null);
                  }}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition-colors duration-150",
                    scope === "MARKET"
                      ? "bg-primary text-primary-foreground"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  <Globe2 className="size-3.5" aria-hidden="true" />
                  <span>Toàn thị trường</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={scope === "COMPANY"}
                  onClick={() => {
                    setScope("COMPANY");
                    setSelectedSectorCode(null);
                  }}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition-colors duration-150",
                    scope === "COMPANY"
                      ? "bg-primary text-primary-foreground"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  <Building2 className="size-3.5" aria-hidden="true" />
                  <span>1 Doanh nghiệp cụ thể</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={scope === "WATCHLIST"}
                  onClick={() => {
                    setScope("WATCHLIST");
                    setSelectedSectorCode(null);
                  }}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition-colors duration-150",
                    scope === "WATCHLIST"
                      ? "bg-primary text-primary-foreground"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  <Briefcase className="size-3.5" aria-hidden="true" />
                  <span>Danh mục Watchlist ({effectiveWatchlistTickers.length})</span>
                </button>
              </div>
            </div>

            {scope === "COMPANY" && companyTicker ? (
              <div className="flex items-center gap-2 text-xs">
                <span className="rounded border border-accent-ink/30 bg-accent-soft px-2 py-0.5 font-mono font-semibold text-accent-ink">
                  {companyTicker}
                </span>
                <span className="max-w-[260px] truncate text-ink-muted">
                  {selectedCompanyMeta?.company_name_vn ?? ""}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  aria-label="Đổi doanh nghiệp"
                  onClick={() => {
                    setCompanyTicker(null);
                    setCompanyQuery("");
                  }}
                >
                  <X className="size-3.5" />
                </Button>
              </div>
            ) : null}
          </div>

          {scope === "COMPANY" ? (
            <div className="space-y-2 border-t border-border/60 pt-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-full max-w-md">
                  <Search
                    className="pointer-events-none absolute top-2 left-2.5 size-3.5 text-ink-subtle"
                    aria-hidden="true"
                  />
                  <Input
                    value={companyQuery}
                    onChange={(e) => setCompanyQuery(e.target.value)}
                    placeholder="Tìm theo mã hoặc tên doanh nghiệp (VD: HPG, FPT, VNM)..."
                    aria-label="Tìm doanh nghiệp để chạy Stress Test"
                    className="pl-8"
                  />
                </div>
                {issuersQuery.isLoading ? (
                  <span className="text-xs text-ink-muted">
                    Đang tải danh sách doanh nghiệp...
                  </span>
                ) : null}
              </div>

              {companyMatches.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {companyMatches.map((iss) => {
                    const isSelected = companyTicker === iss.ticker;
                    return (
                      <button
                        key={iss.issuer_id}
                        type="button"
                        onClick={() => {
                          setCompanyTicker(iss.ticker);
                          setCompanyQuery("");
                        }}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors duration-150",
                          isSelected
                            ? "border-accent-ink bg-accent-soft text-accent-ink"
                            : "border-border bg-surface hover:border-accent-ink/50 hover:bg-surface-muted/60",
                        )}
                      >
                        <span className="font-mono font-semibold">
                          {iss.ticker}
                        </span>
                        <span className="max-w-[170px] truncate text-ink-muted">
                          {iss.company_name_vn ?? ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}

          {scope === "WATCHLIST" ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                {effectiveWatchlistTickers.length > 0 ? (
                  <>
                    <span className="text-ink-muted">Mã trong danh mục:</span>
                    {effectiveWatchlistTickers.slice(0, 12).map((t) => (
                      <Link
                        key={t}
                        href={`/companies/${t}`}
                        className="rounded border border-border bg-surface-muted px-2 py-0.5 font-mono font-semibold text-accent-ink hover:border-accent-ink"
                      >
                        {t}
                      </Link>
                    ))}
                    {effectiveWatchlistTickers.length > 12 ? (
                      <span className="text-ink-muted">
                        +{effectiveWatchlistTickers.length - 12} mã khác
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-ink-muted">
                    Danh sách theo dõi trên trình duyệt hiện đang trống.
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {usingSharedWatchlist ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    onClick={() => setSharedWatchlistTickers(null)}
                  >
                    Dùng Watchlist cá nhân ({localWatchlist.length} mã)
                  </Button>
                ) : null}
                <Link
                  href="/watchlist"
                  className={buttonVariants({ variant: "ghost", size: "xs" })}
                >
                  Quản lý Watchlist →
                </Link>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2 lg:hidden">
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={mobileControlsOpen}
          aria-controls="stress-controls-drawer"
          onClick={() => setMobileControlsOpen(true)}
        >
          <SlidersHorizontal className="size-3.5" />
          <span>Tham số cú sốc vĩ mô</span>
        </Button>
      </div>

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        {/* Left Shock Controls (Desktop) / Collapsible Drawer (Mobile < lg) */}
        <div
          id="stress-controls-drawer"
          role={mobileControlsOpen ? "dialog" : undefined}
          aria-modal={mobileControlsOpen ? true : undefined}
          aria-label={mobileControlsOpen ? "Tham số cú sốc vĩ mô" : undefined}
          className={cn(
            "lg:block lg:w-72 lg:shrink-0",
            mobileControlsOpen
              ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
              : "hidden",
          )}
          onClick={() => setMobileControlsOpen(false)}
        >
          <Card
            className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded-lg lg:border"
            onClick={(e) => e.stopPropagation()}
          >
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Tham số cú sốc vĩ mô</CardTitle>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => setShocks(DEFAULT_SHOCKS)}
                >
                  <RotateCcw className="size-3" />
                  Về 0
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="lg:hidden"
                  aria-label="Đóng bảng tham số sốc"
                  onClick={() => setMobileControlsOpen(false)}
                >
                  <X className="size-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-3.5">
              <div className="space-y-1">
                <label
                  htmlFor="scenario-name-input"
                  className="text-[11px] font-medium text-ink-muted"
                >
                  Tên kịch bản hiện tại (Kịch bản B)
                </label>
                <Input
                  id="scenario-name-input"
                  value={shocks.scenarioName}
                  maxLength={60}
                  onChange={(e) =>
                    setShocks((s) => ({ ...s, scenarioName: e.target.value }))
                  }
                  placeholder="Nhập tên kịch bản..."
                  className="h-7 text-xs"
                />
              </div>

              <DualShockControl
                id="shock-rev"
                label="Biến động doanh thu"
                unit="%"
                min={SHOCK_BOUNDS.revPct.min}
                max={SHOCK_BOUNDS.revPct.max}
                step={SHOCK_BOUNDS.revPct.step}
                value={shocks.revPct}
                onChange={(v) =>
                  setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", revPct: v }))
                }
              />
              <DualShockControl
                id="shock-gm"
                label="Biến động biên gộp"
                unit="đ%"
                min={SHOCK_BOUNDS.gmPp.min}
                max={SHOCK_BOUNDS.gmPp.max}
                step={SHOCK_BOUNDS.gmPp.step}
                value={shocks.gmPp}
                onChange={(v) =>
                  setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", gmPp: v }))
                }
              />
              <DualShockControl
                id="shock-rate"
                label="Cú sốc lãi suất vay"
                unit="bps"
                min={SHOCK_BOUNDS.rateBps.min}
                max={SHOCK_BOUNDS.rateBps.max}
                step={SHOCK_BOUNDS.rateBps.step}
                value={shocks.rateBps}
                onChange={(v) =>
                  setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", rateBps: v }))
                }
              />
              <DualShockControl
                id="shock-dso"
                label="Tăng số ngày thu tiền (DSO)"
                unit="ngày"
                min={SHOCK_BOUNDS.dsoDays.min}
                max={SHOCK_BOUNDS.dsoDays.max}
                step={SHOCK_BOUNDS.dsoDays.step}
                value={shocks.dsoDays}
                onChange={(v) =>
                  setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dsoDays: v }))
                }
              />
              <DualShockControl
                id="shock-dio"
                label="Tăng số ngày tồn kho (DIO)"
                unit="ngày"
                min={SHOCK_BOUNDS.dioDays.min}
                max={SHOCK_BOUNDS.dioDays.max}
                step={SHOCK_BOUNDS.dioDays.step}
                value={shocks.dioDays}
                onChange={(v) =>
                  setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dioDays: v }))
                }
              />

              {isExtreme ? (
                <div className="flex items-start gap-1.5 rounded border border-risk-mid/35 bg-risk-mid-soft p-2 text-[11px] text-risk-mid">
                  <AlertTriangle
                    className="mt-0.5 size-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  <span>
                    Mức sốc cực đoan so với lịch sử doanh nghiệp niêm yết Việt Nam; kết quả phản ánh kịch bản căng thẳng hệ thống.
                  </span>
                </div>
              ) : null}

              <div className="space-y-1.5 border-t border-border/60 pt-2">
                <p className="text-[11px] font-medium text-ink-muted">
                  Đối chiếu song song A vs B
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    disabled={!activeResult}
                    onClick={() => activeResult && setPinnedA(activeResult)}
                  >
                    <Pin className="size-3.5" />
                    {pinnedA ? "Cập nhật kịch bản A" : "So sánh A vs B (Ghim A)"}
                  </Button>
                  {pinnedA ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label="Thoát chế độ so sánh A vs B"
                      onClick={() => setPinnedA(null)}
                    >
                      <PinOff className="size-3.5" />
                    </Button>
                  ) : null}
                </div>
              </div>

              <div className="pt-1 lg:hidden">
                <Button
                  type="button"
                  size="sm"
                  className="w-full"
                  onClick={() => setMobileControlsOpen(false)}
                >
                  Xem kết quả mô phỏng
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Results Panel */}
        <div className="min-w-0 flex-1 space-y-4">
          {!canRunSimulation ? (
            scope === "COMPANY" ? (
              <EmptyState
                title="Chọn 1 doanh nghiệp để mô phỏng cú sốc"
                description="Nhập mã chứng khoán hoặc tên doanh nghiệp ở thanh tìm kiếm phía trên để đánh giá tác động lên EBIT, CFO và khả năng trả lãi (ICR)."
              />
            ) : (
              <EmptyState
                title="Danh mục Watchlist đang trống"
                description="Thêm doanh nghiệp vào Danh sách theo dõi để mô phỏng tổng dư nợ chịu rủi ro của cả danh mục."
                action={
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Link
                      href="/watchlist"
                      className={buttonVariants({ variant: "outline", size: "sm" })}
                    >
                      Mở Danh sách theo dõi →
                    </Link>
                    <Link
                      href="/screener"
                      className={buttonVariants({ variant: "ghost", size: "sm" })}
                    >
                      Đi tới Bảng sàng lọc
                    </Link>
                  </div>
                }
              />
            )
          ) : activeErrorMsg && !activeResult ? (
            <ErrorState message={activeErrorMsg} />
          ) : !activeResult ? (
            <PageSkeleton />
          ) : activeResult.total_issuers_evaluated === 0 ? (
            <EmptyState
              title="Không có dữ liệu phù hợp trong kỳ/phạm vi đang chọn"
              description="Doanh nghiệp hoặc danh mục được chọn chưa có đủ số liệu TTM/Nợ vay trong phạm vi báo cáo này. Hãy thử chuyển sang Hợp nhất/Riêng lẻ hoặc mã khác."
            />
          ) : (
            <div className="relative space-y-4">
              {activeIsCalculating ? (
                <div className="flex items-center justify-end">
                  <span className="rounded border border-accent-ink/30 bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-ink">
                    Đang tính lại...
                  </span>
                </div>
              ) : null}

              {activeErrorMsg ? <ErrorState message={activeErrorMsg} /> : null}

              {/* Top KPI Strip */}
              {scope === "WATCHLIST" && portfolioMetrics ? (
                <div className="grid grid-cols-1 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tổng nợ chịu rủi ro danh mục (ICR &lt; 1)
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-risk-high">
                      {formatVndBillions(portfolioMetrics.stressedDebtAtRiskVnd, {
                        decimals: 1,
                      })}{" "}
                      tỷ
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Tỷ trọng: {formatPercent(portfolioMetrics.stressedDebtAtRiskPct)} (cơ sở{" "}
                      {formatPercent(portfolioMetrics.baselineDebtAtRiskPct)} ·{" "}
                      {formatPercentPoints(portfolioMetrics.deltaDebtAtRiskPct, 1)})
                    </p>
                  </div>
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      DN mới khó khăn trong danh mục
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatNumber(portfolioMetrics.newlyDistressedCount, 0)}
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Đã đánh giá {formatNumber(portfolioMetrics.evaluatedCount, 0)} /{" "}
                      {formatNumber(portfolioMetrics.requestedCount, 0)} mã
                      {portfolioMetrics.missingCount > 0
                        ? ` (${portfolioMetrics.missingCount} mã thiếu DL kỳ này)`
                        : ""}
                    </p>
                  </div>
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tổng dư nợ vay danh mục (tỷ đồng)
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatVndBillions(portfolioMetrics.totalDebtVnd, {
                        decimals: 0,
                      })}{" "}
                      tỷ
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Tăng thêm nợ rủi ro:{" "}
                      {formatSignedBillionsDelta(portfolioMetrics.deltaDebtAtRiskVnd)}
                    </p>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tỷ trọng nợ rủi ro (ICR &lt; 1)
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-risk-high">
                      {formatPercent(activeResult.stressed_debt_at_risk_pct)}
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Cơ sở: {formatPercent(activeResult.baseline_debt_at_risk_pct)} · Thay đổi{" "}
                      {formatPercentPoints(
                        activeResult.stressed_debt_at_risk_pct -
                          activeResult.baseline_debt_at_risk_pct,
                        1,
                      )}
                    </p>
                  </div>
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      DN mới gặp khó khăn trả lãi
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatNumber(activeResult.newly_distressed_count, 0)}
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Trên tổng {formatNumber(activeResult.total_issuers_evaluated, 0)} DN được đánh giá
                    </p>
                  </div>
                  <div className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tổng dư nợ vay đánh giá (tỷ đồng)
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatVndBillions(activeResult.total_debt_sum, { decimals: 0 })} tỷ
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Nợ rủi ro sau sốc:{" "}
                      {formatVndBillions(
                        activeResult.total_debt_sum * activeResult.stressed_debt_at_risk_pct,
                        { decimals: 0 },
                      )}{" "}
                      tỷ đồng
                    </p>
                  </div>
                </div>
              )}

              {/* Dedicated 1-Company Breakdown Card */}
              {scope === "COMPANY" && singleCompanyRow ? (
                <Card>
                  <CardHeader className="flex-wrap gap-2 py-2.5">
                    <div>
                      <CardTitle className="text-[14px]">
                        Chi tiết tác động lên {singleCompanyRow.ticker} —{" "}
                        {singleCompanyRow.company_name_vn ?? ""}
                      </CardTitle>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        Ngành: {singleCompanyRow.icb_name_level2 ?? "—"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <RiskBadge
                        tone={
                          singleCompanyRow.newly_distressed_under_shock
                            ? "high"
                            : singleCompanyRow.stressed_debt_at_risk
                              ? "mid"
                              : "low"
                        }
                        label={
                          singleCompanyRow.newly_distressed_under_shock
                            ? "Mới chuyển sang ICR < 1"
                            : singleCompanyRow.stressed_debt_at_risk
                              ? "Duy trì áp lực trả lãi (ICR < 1)"
                              : "Đủ khả năng trả lãi (ICR ≥ 1)"
                        }
                      />
                      <Link
                        href={`/companies/${singleCompanyRow.ticker}`}
                        className={buttonVariants({ variant: "outline", size: "xs" })}
                      >
                        Xem hồ sơ DN →
                      </Link>
                    </div>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-3 p-3.5 sm:grid-cols-4">
                    <div className="rounded border border-border bg-canvas/60 p-2.5">
                      <p className="text-[11px] text-ink-muted">ICR (Cơ sở → Sốc)</p>
                      <p className="num mt-1 text-sm font-semibold text-ink">
                        {formatMultiple(singleCompanyRow.interest_coverage)} →{" "}
                        <span
                          className={
                            (singleCompanyRow.stressed_interest_coverage ?? 0) < 1
                              ? "text-risk-high"
                              : "text-risk-low"
                          }
                        >
                          {formatMultiple(singleCompanyRow.stressed_interest_coverage)}
                        </span>
                      </p>
                    </div>
                    <div className="rounded border border-border bg-canvas/60 p-2.5">
                      <p className="text-[11px] text-ink-muted">EBIT TTM (tỷ đồng)</p>
                      <p className="num mt-1 text-sm font-semibold text-ink">
                        {formatVndBillions(singleCompanyRow.ebit_ttm, {
                          accountingNegative: true,
                        })}{" "}
                        →{" "}
                        {formatVndBillions(singleCompanyRow.stressed_ebit_ttm, {
                          accountingNegative: true,
                        })}
                      </p>
                    </div>
                    <div className="rounded border border-border bg-canvas/60 p-2.5">
                      <p className="text-[11px] text-ink-muted">CFO TTM (tỷ đồng)</p>
                      <p className="num mt-1 text-sm font-semibold text-ink">
                        {formatVndBillions(singleCompanyRow.cfo_ttm, {
                          accountingNegative: true,
                        })}{" "}
                        →{" "}
                        {formatVndBillions(singleCompanyRow.stressed_cfo_ttm, {
                          accountingNegative: true,
                        })}
                      </p>
                    </div>
                    <div className="rounded border border-border bg-canvas/60 p-2.5">
                      <p className="text-[11px] text-ink-muted">Tổng dư nợ vay</p>
                      <p className="num mt-1 text-sm font-semibold text-ink">
                        {formatVndBillions(singleCompanyRow.total_debt, {
                          decimals: 1,
                        })}{" "}
                        tỷ
                      </p>
                    </div>
                  </CardContent>
                </Card>
              ) : null}

              {/* Side-by-Side Scenario A vs Scenario B Comparison Panel */}
              {comparison && pinnedA ? (
                <Card className="border-accent-ink/40">
                  <CardHeader className="flex-wrap gap-2 py-2.5">
                    <div>
                      <CardTitle className="text-[14px]">
                        So sánh song song: Kịch bản A vs Kịch bản B
                      </CardTitle>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        Chênh lệch được tính theo chiều (Kịch bản B − Kịch bản A)
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        onClick={() => setPinnedA(activeResult)}
                      >
                        <Pin className="size-3" />
                        Cập nhật A
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        onClick={() => setPinnedA(null)}
                      >
                        <PinOff className="size-3" />
                        Bỏ ghim A
                      </Button>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3 p-3.5">
                    {comparison.scopeMismatch ? (
                      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-risk-mid/35 bg-risk-mid-soft px-3 py-2 text-xs text-risk-mid">
                        <span>
                          Lưu ý: Kịch bản A được ghim ở phạm vi hoặc tập DN khác với Kịch bản B hiện tại. Nên cập nhật A để so sánh trên cùng tập mẫu.
                        </span>
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() => setPinnedA(activeResult)}
                        >
                          Đồng bộ A theo phạm vi mới
                        </Button>
                      </div>
                    ) : null}

                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                      <div className="rounded-lg border border-border bg-canvas/60 p-3">
                        <p className="text-[11px] font-semibold text-accent-ink">
                          Kịch bản A (Đã ghim): {comparison.scenarioAName}
                        </p>
                        <p className="num mt-1.5 text-lg font-semibold text-ink">
                          {formatPercent(comparison.debtAtRiskPctA)} nợ rủi ro
                        </p>
                        <p className="num mt-0.5 text-xs text-ink-muted">
                          Quy mô:{" "}
                          {formatVndBillions(comparison.debtAtRiskVndA, {
                            decimals: 1,
                          })}{" "}
                          tỷ · {formatNumber(comparison.newlyDistressedCountA, 0)}{" "}
                          DN mới khó
                        </p>
                      </div>

                      <div className="rounded-lg border border-border bg-canvas/60 p-3">
                        <p className="text-[11px] font-semibold text-risk-high">
                          Kịch bản B (Hiện tại): {comparison.scenarioBName}
                        </p>
                        <p className="num mt-1.5 text-lg font-semibold text-ink">
                          {formatPercent(comparison.debtAtRiskPctB)} nợ rủi ro
                        </p>
                        <p className="num mt-0.5 text-xs text-ink-muted">
                          Quy mô:{" "}
                          {formatVndBillions(comparison.debtAtRiskVndB, {
                            decimals: 1,
                          })}{" "}
                          tỷ · {formatNumber(comparison.newlyDistressedCountB, 0)}{" "}
                          DN mới khó
                        </p>
                      </div>

                      <div className="rounded-lg border border-border bg-surface-muted/60 p-3">
                        <p className="text-[11px] font-semibold text-ink">
                          Chênh lệch (B − A)
                        </p>
                        <p
                          className={cn(
                            "num mt-1.5 text-lg font-semibold",
                            comparison.debtAtRiskPctDelta > 0.0005
                              ? "text-risk-high"
                              : comparison.debtAtRiskPctDelta < -0.0005
                                ? "text-risk-low"
                                : "text-ink",
                          )}
                        >
                          {formatPercentPoints(comparison.debtAtRiskPctDelta, 1)}
                        </p>
                        <p className="num mt-0.5 text-xs text-ink-muted">
                          Nợ rủi ro:{" "}
                          {formatSignedBillionsDelta(comparison.debtAtRiskVndDelta)}{" "}
                          · DN mới:{" "}
                          {formatSignedInteger(comparison.newlyDistressedCountDelta)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ) : null}

              <StressSectorBarChart
                sectors={activeResult.sector_summary}
                selectedSectorCode={selectedSectorCode}
                onToggleSector={(code) =>
                  setSelectedSectorCode((prev) => (prev === code ? null : code))
                }
                comparisonA={pinnedA}
                scenarioBName={activeResult.scenario_name}
              />

              {/* Delta Table when A vs B Comparison Active */}
              {comparison ? (
                <Card>
                  <CardHeader className="flex-wrap gap-2 py-2.5">
                    <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
                      <button
                        type="button"
                        onClick={() => setCompareTab("ISSUERS")}
                        className={cn(
                          "rounded px-2.5 py-1 font-medium",
                          compareTab === "ISSUERS"
                            ? "bg-primary text-primary-foreground"
                            : "text-ink-muted hover:text-ink",
                        )}
                      >
                        Chênh lệch DN suy thoái mới ({comparison.addedDistressedCount}{" "}
                        thêm ở B · {comparison.resolvedDistressedCount} giảm ở B)
                      </button>
                      <button
                        type="button"
                        onClick={() => setCompareTab("SECTORS")}
                        className={cn(
                          "rounded px-2.5 py-1 font-medium",
                          compareTab === "SECTORS"
                            ? "bg-primary text-primary-foreground"
                            : "text-ink-muted hover:text-ink",
                        )}
                      >
                        Chênh lệch theo ngành ({filteredCompareSectors.length})
                      </button>
                    </div>

                    <div className="flex items-center gap-2">
                      {selectedSectorName ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="xs"
                          onClick={() => setSelectedSectorCode(null)}
                        >
                          Đang lọc: {selectedSectorName} ×
                        </Button>
                      ) : null}
                      {compareTab === "SECTORS" ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          disabled={filteredCompareSectors.length === 0}
                          onClick={handleExportCompareSectorsCsv}
                        >
                          <Download className="size-3" />
                          Xuất CSV ngành A vs B
                        </Button>
                      ) : null}
                    </div>
                  </CardHeader>

                  <CardContent className="p-0">
                    {compareTab === "ISSUERS" ? (
                      filteredCompareIssuers.length === 0 ? (
                        <div className="p-6">
                          <EmptyState
                            title="Không có khác biệt DN suy thoái mới giữa Kịch bản A và B"
                            description="Cả hai kịch bản ghi nhận cùng tập doanh nghiệp mới chuyển sang ICR < 1 (hoặc chưa có DN nào chuyển sang trạng thái suy thoái mới)."
                          />
                        </div>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full border-collapse text-xs">
                            <thead>
                              <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                                <th scope="col" className="px-3 py-2 text-left font-semibold">
                                  Mã
                                </th>
                                <th scope="col" className="px-3 py-2 text-left font-semibold">
                                  Ngành
                                </th>
                                <th scope="col" className="px-3 py-2 text-left font-semibold">
                                  Biến động trạng thái (A → B)
                                </th>
                                <th scope="col" className="px-3 py-2 text-right font-semibold">
                                  Tổng nợ (tỷ)
                                </th>
                                <th scope="col" className="px-3 py-2 text-right font-semibold">
                                  ICR Cơ sở
                                </th>
                                <th scope="col" className="px-3 py-2 text-right font-semibold">
                                  ICR Kịch bản A
                                </th>
                                <th scope="col" className="px-3 py-2 text-right font-semibold">
                                  ICR Kịch bản B
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {filteredCompareIssuers.map((it) => (
                                <tr
                                  key={it.ticker}
                                  className="border-b border-border/60 hover:bg-surface-muted/40"
                                >
                                  <td className="px-3 py-2">
                                    <Link
                                      href={`/companies/${it.ticker}`}
                                      className="font-mono font-semibold text-accent-ink hover:underline"
                                    >
                                      {it.ticker}
                                    </Link>
                                    <div className="max-w-[180px] truncate text-[11px] text-ink-muted">
                                      {it.companyNameVn}
                                    </div>
                                  </td>
                                  <td className="max-w-[150px] truncate px-3 py-2 text-ink-muted">
                                    {it.icbNameLevel2}
                                  </td>
                                  <td className="px-3 py-2">
                                    <RiskBadge
                                      tone={
                                        it.status === "ADDED_IN_B"
                                          ? "high"
                                          : it.status === "REMOVED_IN_B"
                                            ? "low"
                                            : "neutral"
                                      }
                                      label={
                                        it.status === "ADDED_IN_B"
                                          ? "Mới phát sinh ở B"
                                          : it.status === "REMOVED_IN_B"
                                            ? "Thoát suy thoái ở B"
                                            : "Cả A và B"
                                      }
                                    />
                                  </td>
                                  <td className="num px-3 py-2 text-right">
                                    {formatVndBillions(it.totalDebt, { decimals: 0 })}
                                  </td>
                                  <td className="num px-3 py-2 text-right text-ink-muted">
                                    {formatMultiple(it.icrBaseline)}
                                  </td>
                                  <td className="num px-3 py-2 text-right">
                                    {formatMultiple(it.icrA)}
                                  </td>
                                  <td className="num px-3 py-2 text-right font-semibold">
                                    <span
                                      className={
                                        (it.icrB ?? 0) < 1
                                          ? "text-risk-high"
                                          : "text-ink"
                                      }
                                    >
                                      {formatMultiple(it.icrB)}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full border-collapse text-xs">
                          <thead>
                            <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Ngành (ICB cấp 2)
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                Kịch bản A (%)
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                Kịch bản B (%)
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                Chênh lệch (B−A)
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                Nợ rủi ro B−A (tỷ)
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                DN mới khó (A → B)
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {filteredCompareSectors.map((s) => (
                              <tr
                                key={s.icbCodeLevel2}
                                className="border-b border-border/60 hover:bg-surface-muted/40"
                              >
                                <td className="px-3 py-2 font-medium text-ink">
                                  {s.icbNameLevel2}
                                </td>
                                <td className="num px-3 py-2 text-right">
                                  {formatPercent(s.debtAtRiskPctA)}
                                </td>
                                <td className="num px-3 py-2 text-right font-semibold text-ink">
                                  {formatPercent(s.debtAtRiskPctB)}
                                </td>
                                <td
                                  className={cn(
                                    "num px-3 py-2 text-right font-medium",
                                    s.debtAtRiskPctDelta > 0.0005
                                      ? "text-risk-high"
                                      : s.debtAtRiskPctDelta < -0.0005
                                        ? "text-risk-low"
                                        : "text-ink-muted",
                                  )}
                                >
                                  {formatPercentPoints(s.debtAtRiskPctDelta, 1)}
                                </td>
                                <td className="num px-3 py-2 text-right">
                                  {formatSignedBillionsDelta(s.debtAtRiskVndDelta)}
                                </td>
                                <td className="num px-3 py-2 text-right">
                                  {s.newlyDistressedA} → {s.newlyDistressedB} (
                                  {formatSignedInteger(s.newlyDistressedDelta)})
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : null}

              {/* Standard Scenario Issuer Detail Table */}
              <Card>
                <CardHeader className="flex-wrap gap-2 py-2.5">
                  <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => setActiveTab("NEWLY")}
                      className={cn(
                        "rounded px-2.5 py-1 font-medium",
                        activeTab === "NEWLY"
                          ? "bg-primary text-primary-foreground"
                          : "text-ink-muted hover:text-ink",
                      )}
                    >
                      DN mới gặp khó ({activeResult.newly_distressed_issuers.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setActiveTab("TOP")}
                      className={cn(
                        "rounded px-2.5 py-1 font-medium",
                        activeTab === "TOP"
                          ? "bg-primary text-primary-foreground"
                          : "text-ink-muted hover:text-ink",
                      )}
                    >
                      Nợ rủi ro lớn nhất ({activeResult.top_stressed_issuers.length})
                    </button>
                  </div>

                  <div className="flex items-center gap-2">
                    {selectedSectorName ? (
                      <Button
                        type="button"
                        variant="secondary"
                        size="xs"
                        onClick={() => setSelectedSectorCode(null)}
                      >
                        Đang lọc: {selectedSectorName} ×
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      disabled={issuerList.length === 0}
                      onClick={handleExportIssuersCsv}
                    >
                      <Download className="size-3" />
                      Xuất CSV
                    </Button>
                  </div>
                </CardHeader>

                <CardContent className="p-0">
                  {issuerList.length === 0 ? (
                    <div className="p-6">
                      <EmptyState
                        title="Không có doanh nghiệp trong nhóm này"
                        description="Thử tăng mức cú sốc vĩ mô hoặc bỏ bộ lọc ngành để xem danh sách."
                      />
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              Mã
                            </th>
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              Ngành
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              Tổng nợ (tỷ)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              ICR (Cơ sở → Sốc)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              EBIT TTM (tỷ)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              CFO TTM (tỷ)
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {issuerList.map((it) => (
                            <tr
                              key={it.issuer_id}
                              className="border-b border-border/60 hover:bg-surface-muted/40"
                            >
                              <td className="px-3 py-2">
                                <Link
                                  href={`/companies/${it.ticker}`}
                                  className="font-mono font-semibold text-accent-ink hover:underline"
                                >
                                  {it.ticker}
                                </Link>
                                <div className="max-w-[180px] truncate text-[11px] text-ink-muted">
                                  {it.company_name_vn ?? ""}
                                </div>
                              </td>
                              <td className="max-w-[150px] truncate px-3 py-2 text-ink-muted">
                                {it.icb_name_level2 ?? "—"}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {formatVndBillions(it.total_debt, { decimals: 0 })}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatMultiple(it.interest_coverage)} →{" "}
                                </span>
                                <strong
                                  className={
                                    (it.stressed_interest_coverage ?? 0) < 1
                                      ? "text-risk-high"
                                      : "text-ink"
                                  }
                                >
                                  {formatMultiple(it.stressed_interest_coverage)}
                                </strong>
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatVndBillions(it.ebit_ttm, {
                                    accountingNegative: true,
                                  })}{" "}
                                  →{" "}
                                </span>
                                <span>
                                  {formatVndBillions(it.stressed_ebit_ttm, {
                                    accountingNegative: true,
                                  })}
                                </span>
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatVndBillions(it.cfo_ttm, {
                                    accountingNegative: true,
                                  })}{" "}
                                  →{" "}
                                </span>
                                <span>
                                  {formatVndBillions(it.stressed_cfo_ttm, {
                                    accountingNegative: true,
                                  })}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function StressTestPage() {
  return (
    <React.Suspense fallback={<PageSkeleton />}>
      <StressTestContent />
    </React.Suspense>
  );
}
