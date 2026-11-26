"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Briefcase,
  Building2,
  Check,
  Globe2,
  Link2,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { StressSectorBarChart } from "@/components/charts/StressSectorBarChart";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import {
  formatSignedBillionsDelta,
  StressComparisonDeltaCard,
  StressComparisonSummaryCard,
} from "@/components/stress-test/StressComparisonSection";
import { StressControlsDrawer } from "@/components/stress-test/StressControlsDrawer";
import { StressIssuerDetailTable } from "@/components/stress-test/StressIssuerDetailTable";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
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
  normalizeSearchText,
} from "@/lib/format";
import {
  computePortfolioStressMetrics,
  computeScenarioComparison,
  parseStressUrlParams,
  serializeStressUrlParams,
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

const PRIORITY_CORPORATE_TICKERS = [
  "HPG",
  "FPT",
  "VNM",
  "MWG",
  "GAS",
  "MSN",
  "VIC",
  "VHM",
] as const;

const ICR_TOOLTIP_TEXT =
  "ICR (Interest Coverage Ratio = EBIT / Chi phí lãi vay): số lần lợi nhuận hoạt động bù đắp chi phí lãi vay. Doanh nghiệp có ICR < 1,0x đối mặt nguy cơ thiếu hụt dòng tiền trả lãi.";

function StressTestContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { statementScope, periodType, periodEndDate, cacheSnapshotKey } = useAppState();

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

  const issuersQuery = useAllIssuers(cacheSnapshotKey);

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

  const referenceIcrs = pinnedA?.issuer_icrs;
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
    if (referenceIcrs) {
      base.reference_icrs = referenceIcrs;
    }
    return base;
  }, [
    activeTickers,
    periodEndDate,
    periodType,
    referenceIcrs,
    scope,
    shocks,
    statementScope,
  ]);

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

  const autoTabKeyRef = React.useRef<string>("");
  React.useEffect(() => {
    if (
      scope === "WATCHLIST" &&
      activeResult &&
      activeResult.newly_distressed_issuers.length === 0 &&
      activeResult.top_stressed_issuers.length > 0
    ) {
      const nextKey = `${scope}:${activeResult.scenario_name}:${activeResult.newly_distressed_issuers.length}:${activeResult.top_stressed_issuers.length}`;
      if (autoTabKeyRef.current !== nextKey) {
        autoTabKeyRef.current = nextKey;
        setActiveTab("TOP");
      }
    }
  }, [activeResult, scope]);

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
    if (!q) {
      const byTicker = new Map(list.map((it) => [it.ticker.toUpperCase(), it]));
      const prioritized: typeof list = [];
      const seen = new Set<string>();
      for (const t of PRIORITY_CORPORATE_TICKERS) {
        const match = byTicker.get(t);
        if (match && !seen.has(match.issuer_id)) {
          prioritized.push(match);
          seen.add(match.issuer_id);
        }
      }
      for (const item of list) {
        if (prioritized.length >= 8) break;
        if (!seen.has(item.issuer_id)) {
          prioritized.push(item);
          seen.add(item.issuer_id);
        }
      }
      return prioritized;
    }
    const rankOf = (iss: (typeof list)[number]): number => {
      const tickerNorm = normalizeSearchText(iss.ticker);
      if (tickerNorm === q) return 0;
      if (tickerNorm.startsWith(q)) return 1;
      if (tickerNorm.includes(q)) return 2;
      return 3;
    };
    return list
      .filter((iss) => {
        const tickerNorm = normalizeSearchText(iss.ticker);
        const nameNorm = normalizeSearchText(iss.company_name_vn ?? "");
        return tickerNorm.includes(q) || nameNorm.includes(q);
      })
      .sort((a, b) => {
        const diff = rankOf(a) - rankOf(b);
        if (diff !== 0) return diff;
        return a.ticker.localeCompare(b.ticker);
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
      "Chênh lệch B-A (điểm %)",
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

      <div className="sticky top-[52px] z-20 -mx-3.5 flex flex-wrap items-center justify-between gap-2 border-b border-border bg-canvas px-3.5 py-2 lg:static lg:mx-0 lg:hidden lg:border-0 lg:bg-transparent lg:p-0">
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
        <StressControlsDrawer
          mobileControlsOpen={mobileControlsOpen}
          setMobileControlsOpen={setMobileControlsOpen}
          shocks={shocks}
          setShocks={setShocks}
          isExtreme={isExtreme}
          activeResult={activeResult}
          activeIsCalculating={activeIsCalculating}
          pinnedA={pinnedA}
          setPinnedA={setPinnedA}
        />

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
                    <p className="flex items-center gap-1 text-xs font-medium text-ink-muted">
                      <span>Tổng nợ chịu rủi ro danh mục (ICR &lt; 1)</span>
                      <InfoTooltip label="Giải thích chỉ số ICR" text={ICR_TOOLTIP_TEXT} />
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-risk-high">
                      {formatVndBillions(portfolioMetrics.stressedDebtAtRiskVnd, {
                        decimals: 1,
                        zeroAsZero: true,
                      })}{" "}
                      tỷ đồng
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
                    <p className="flex items-center gap-1 text-xs font-medium text-ink-muted">
                      <span>Tỷ trọng nợ rủi ro (ICR &lt; 1)</span>
                      <InfoTooltip label="Giải thích chỉ số ICR" text={ICR_TOOLTIP_TEXT} />
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
                        { decimals: 0, zeroAsZero: true },
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
                      <p className="flex items-center gap-1 text-[11px] text-ink-muted">
                        <span>ICR (Cơ sở → Sốc)</span>
                        <InfoTooltip label="Giải thích chỉ số ICR" text={ICR_TOOLTIP_TEXT} />
                      </p>
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
                <StressComparisonSummaryCard
                  comparison={comparison}
                  activeResult={activeResult}
                  activeIsCalculating={activeIsCalculating}
                  onPinA={setPinnedA}
                  onUnpinA={() => setPinnedA(null)}
                />
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
                <StressComparisonDeltaCard
                  comparison={comparison}
                  compareTab={compareTab}
                  setCompareTab={setCompareTab}
                  filteredCompareIssuers={filteredCompareIssuers}
                  filteredCompareSectors={filteredCompareSectors}
                  selectedSectorName={selectedSectorName}
                  onClearSector={() => setSelectedSectorCode(null)}
                  onExportCompareSectorsCsv={handleExportCompareSectorsCsv}
                />
              ) : null}

              {/* Standard Scenario Issuer Detail Table */}
              <StressIssuerDetailTable
                activeResult={activeResult}
                activeTab={activeTab}
                setActiveTab={setActiveTab}
                issuerList={issuerList}
                selectedSectorName={selectedSectorName}
                onClearSector={() => setSelectedSectorCode(null)}
                onExportIssuersCsv={handleExportIssuersCsv}
              />
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
