"use client";

import * as React from "react";
import Link from "next/link";
import {
  CloudUpload,
  Download,
  FileText,
  FolderPlus,
  GitCompare,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { AnalystNotesPanel } from "@/components/watchlist/AnalystNotesPanel";
import {
  useAuthSession,
  useOptionalQueryClient,
  useServerWatchlists,
  WORKSPACE_WATCHLISTS_QUERY_KEY,
} from "@/hooks/use-auth-workspace";
import { useAllIssuers, useMarketChanges } from "@/hooks/use-finintel-queries";
import { ApiError } from "@/lib/api-client";
import { workspaceApi } from "@/lib/auth-api";
import {
  ALTMAN_ZONE_META,
  formatCreditTrio,
  getRiskTierMeta,
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import {
  formatPeriodLabel,
  formatPercent,
  formatPercentPoints,
  normalizeSearchText,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  addWatchlistTicker,
  clearLocalWatchlist,
  exportWatchlistJson,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  importWatchlistJson,
  removeWatchlistTicker,
  subscribePreferences,
} from "@/lib/watchlist-store";

export default function WatchlistPage() {
  const queryClient = useOptionalQueryClient();
  const { statementScope, periodType, cacheSnapshotKey } = useAppState();
  const localWatchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );

  const sessionQuery = useAuthSession();
  const isAuthenticated = sessionQuery.data?.authenticated === true;
  const serverWatchlistsQuery = useServerWatchlists(isAuthenticated);

  const serverWatchlists = React.useMemo(
    () => serverWatchlistsQuery.data?.watchlists ?? [],
    [serverWatchlistsQuery.data],
  );
  const serverLimits = serverWatchlistsQuery.data?.limits ?? null;

  const [selectedWatchlistId, setSelectedWatchlistId] = React.useState<string | null>(null);
  const activeServerWatchlist = React.useMemo(() => {
    if (!isAuthenticated || serverWatchlists.length === 0) return null;
    if (selectedWatchlistId) {
      const found = serverWatchlists.find((w) => w.watchlist_id === selectedWatchlistId);
      if (found) return found;
    }
    return serverWatchlists.find((w) => w.is_default) ?? serverWatchlists[0];
  }, [isAuthenticated, selectedWatchlistId, serverWatchlists]);

  const watchlist = isAuthenticated
    ? (activeServerWatchlist?.items ?? [])
    : localWatchlist;
  const maxItems = isAuthenticated
    ? (serverLimits?.max_items_per_watchlist ?? 50)
    : 50;

  const pendingLocalTickers = React.useMemo(() => {
    if (!isAuthenticated || localWatchlist.length === 0) return [];
    const serverSet = new Set(
      (activeServerWatchlist?.items ?? []).map((t) => t.trim().toUpperCase()),
    );
    return localWatchlist.filter((t) => !serverSet.has(t.trim().toUpperCase()));
  }, [isAuthenticated, activeServerWatchlist, localWatchlist]);

  const [tickerInput, setTickerInput] = React.useState("");
  const [tickerError, setTickerError] = React.useState<string | null>(null);
  const [importStatus, setImportStatus] = React.useState<string | null>(null);
  const [newWatchlistName, setNewWatchlistName] = React.useState("");
  const [creatingWatchlist, setCreatingWatchlist] = React.useState(false);
  const [selectedNoteTicker, setSelectedNoteTicker] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  const issuersQuery = useAllIssuers(cacheSnapshotKey);
  const changesQuery = useMarketChanges(
    {
      tickers: watchlist,
      statementScope,
      periodType,
      limit: 50,
      enabled: watchlist.length > 0,
    },
    cacheSnapshotKey,
  );

  const issuerMatches = React.useMemo(() => {
    const q = normalizeSearchText(tickerInput);
    if (!q) return [];
    const list = issuersQuery.data ?? [];
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
  }, [issuersQuery.data, tickerInput]);

  const resolveSubmittedTicker = (): string => {
    const trimmed = tickerInput.trim();
    const clean = trimmed.toUpperCase();
    const allIssuers = issuersQuery.data ?? [];
    if (
      clean.length > 0 &&
      !/\s/.test(clean) &&
      (watchlist.includes(clean) ||
        allIssuers.some((iss) => iss.ticker.toUpperCase() === clean) ||
        (/^[A-Z0-9]{1,5}$/.test(clean) &&
          issuerMatches[0]?.ticker.toUpperCase() !== clean))
    ) {
      return clean;
    }
    return issuerMatches[0]?.ticker ?? trimmed;
  };

  const refreshServerWatchlists = async () => {
    await queryClient?.invalidateQueries({ queryKey: WORKSPACE_WATCHLISTS_QUERY_KEY });
  };

  const handleAdd = async (rawTicker: string) => {
    const clean = rawTicker.trim().toUpperCase();
    if (!/^[A-Z0-9]{1,10}$/.test(clean)) {
      setTickerError("Mã cổ phiếu chỉ gồm 1–10 ký tự chữ hoặc số.");
      return;
    }
    if (watchlist.includes(clean)) {
      setTickerError(`Mã ${clean} đã có trong danh sách theo dõi.`);
      return;
    }
    if (watchlist.length >= maxItems) {
      setTickerError(`Danh sách theo dõi đã đạt tối đa ${maxItems} mã.`);
      return;
    }
    if (issuersQuery.data && issuersQuery.data.length > 0) {
      const exists = issuersQuery.data.some(
        (iss) => iss.ticker.toUpperCase() === clean,
      );
      if (!exists) {
        setTickerError(
          `Không tìm thấy doanh nghiệp mã "${clean}" trong phạm vi dữ liệu FinIntel.`,
        );
        return;
      }
    }

    if (isAuthenticated) {
      setBusy(true);
      try {
        if (activeServerWatchlist) {
          await workspaceApi.addTicker(activeServerWatchlist.watchlist_id, clean);
        } else {
          const res = await workspaceApi.importLocalWatchlist([clean]);
          setSelectedWatchlistId(res.watchlist.watchlist_id);
        }
        await refreshServerWatchlists();
      } catch (err) {
        setTickerError(
          err instanceof ApiError ? err.message : "Không thể thêm mã vào danh mục.",
        );
        setBusy(false);
        return;
      } finally {
        setBusy(false);
      }
    } else {
      addWatchlistTicker(clean);
    }

    setTickerInput("");
    setTickerError(null);
    setImportStatus(null);
  };

  const handleRemove = async (ticker: string) => {
    if (isAuthenticated && activeServerWatchlist) {
      setBusy(true);
      try {
        await workspaceApi.removeTicker(activeServerWatchlist.watchlist_id, ticker);
        await refreshServerWatchlists();
      } catch (err) {
        setTickerError(
          err instanceof ApiError ? err.message : "Không thể xóa mã khỏi danh mục.",
        );
      } finally {
        setBusy(false);
      }
      return;
    }
    removeWatchlistTicker(ticker);
  };

  const handleCreateWatchlist = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const cleanName = newWatchlistName.trim();
    if (!cleanName) return;
    setBusy(true);
    setTickerError(null);
    try {
      const created = await workspaceApi.createWatchlist(cleanName);
      setNewWatchlistName("");
      setCreatingWatchlist(false);
      setSelectedWatchlistId(created.watchlist_id);
      await refreshServerWatchlists();
    } catch (err) {
      setTickerError(
        err instanceof ApiError ? err.message : "Không thể tạo danh mục mới.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteWatchlist = async (watchlistId: string) => {
    setBusy(true);
    setTickerError(null);
    try {
      await workspaceApi.deleteWatchlist(watchlistId);
      setSelectedWatchlistId(null);
      await refreshServerWatchlists();
    } catch (err) {
      setTickerError(
        err instanceof ApiError ? err.message : "Không thể xóa danh mục.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleSyncLocalToServer = async () => {
    if (localWatchlist.length === 0) return;
    setBusy(true);
    setTickerError(null);
    try {
      const res = await workspaceApi.importLocalWatchlist(
        localWatchlist,
        activeServerWatchlist?.name,
      );
      setSelectedWatchlistId(res.watchlist.watchlist_id);
      clearLocalWatchlist();
      setImportStatus(
        `Đã đồng bộ ${res.added_count} mã lên tài khoản (bỏ qua ${res.skipped_duplicates} mã trùng).`,
      );
      await refreshServerWatchlists();
    } catch (err) {
      setTickerError(
        err instanceof ApiError ? err.message : "Không thể đồng bộ từ trình duyệt.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleExport = () => {
    const payload = isAuthenticated
      ? JSON.stringify(watchlist, null, 2)
      : exportWatchlistJson();
    const blob = new Blob([payload], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "finintel-watchlist.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    if (isAuthenticated) {
      try {
        const parsed = JSON.parse(text) as unknown;
        if (!Array.isArray(parsed)) {
          setImportStatus("Tệp JSON cần là mảng mã cổ phiếu.");
        } else {
          const res = await workspaceApi.importLocalWatchlist(
            parsed.map((item) => String(item)),
            activeServerWatchlist?.name,
          );
          setSelectedWatchlistId(res.watchlist.watchlist_id);
          setImportStatus(`Đã nhập ${res.added_count} mã vào tài khoản.`);
          await refreshServerWatchlists();
        }
      } catch {
        setImportStatus("Không thể đọc tệp JSON danh sách theo dõi.");
      }
    } else {
      const res = importWatchlistJson(text);
      if (res.ok) {
        setImportStatus(`Đã nhập thành công ${res.count} mã cổ phiếu.`);
      } else {
        setImportStatus(res.error);
      }
    }
    e.target.value = "";
  };

  const compareTickers = watchlist.slice(0, 5);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Danh sách theo dõi ({watchlist.length}/{maxItems})
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            {isAuthenticated ? (
              <>
                Đồng bộ trên tài khoản FinIntel ·{" "}
                {serverWatchlists.length}/{serverLimits?.max_watchlists ?? 1} danh mục
              </>
            ) : (
              <>
                Lưu trữ cục bộ trên trình duyệt ·{" "}
                <Link href="/auth/login?next=/watchlist" className="font-medium text-accent-ink underline underline-offset-2 hover:text-primary">
                  Đăng nhập để đồng bộ đa danh mục &amp; ghi chú
                </Link>
              </>
            )}{" "}
            · So sánh biến động {PERIOD_TYPE_LABELS[periodType] ?? periodType} (
            {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope})
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {compareTickers.length >= 2 ? (
            <Link
              href={`/compare?tickers=${compareTickers.join(",")}`}
              className={buttonVariants({ variant: "default", size: "sm" })}
            >
              <GitCompare className="size-3.5" aria-hidden="true" />
              So sánh ({compareTickers.length} mã đầu)
            </Link>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={handleExport}
            disabled={watchlist.length === 0}
          >
            <Download className="size-3.5" aria-hidden="true" />
            Xuất JSON
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload className="size-3.5" aria-hidden="true" />
            Nhập JSON
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleImportFile}
          />
        </div>
      </div>

      {isAuthenticated ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3.5 py-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            {serverWatchlists.map((wl) => {
              const active = activeServerWatchlist?.watchlist_id === wl.watchlist_id;
              return (
                <div key={wl.watchlist_id} className="inline-flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setSelectedWatchlistId(wl.watchlist_id)}
                    aria-pressed={active}
                    className={cn(
                      "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
                      active
                        ? "border-accent-ink bg-accent-soft text-accent-ink"
                        : "border-border bg-canvas text-ink-muted hover:bg-surface-hover hover:text-ink",
                    )}
                  >
                    <span>{wl.name}</span>
                    <span className="num text-[11px] opacity-80">({wl.items.length})</span>
                  </button>
                  {active && serverWatchlists.length > 1 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      disabled={busy}
                      aria-label={`Xóa danh mục ${wl.name}`}
                      onClick={() => handleDeleteWatchlist(wl.watchlist_id)}
                    >
                      <Trash2 className="size-3 text-risk-high" aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
              );
            })}

            {!creatingWatchlist ? (
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={
                  busy ||
                  (serverLimits !== null &&
                    serverWatchlists.length >= serverLimits.max_watchlists)
                }
                onClick={() => setCreatingWatchlist(true)}
              >
                <FolderPlus className="size-3.5" aria-hidden="true" />
                <span>Danh mục mới</span>
              </Button>
            ) : (
              <form onSubmit={handleCreateWatchlist} className="flex items-center gap-1.5">
                <Input
                  value={newWatchlistName}
                  onChange={(e) => setNewWatchlistName(e.target.value)}
                  maxLength={60}
                  placeholder="Tên danh mục..."
                  aria-label="Tên danh mục theo dõi mới"
                  className="h-7 w-40 text-xs"
                />
                <Button type="submit" size="xs" disabled={busy || !newWatchlistName.trim()}>
                  Tạo
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => {
                    setCreatingWatchlist(false);
                    setNewWatchlistName("");
                  }}
                >
                  Hủy
                </Button>
              </form>
            )}
          </div>

          {pendingLocalTickers.length > 0 ? (
            <Button
              type="button"
              variant="secondary"
              size="xs"
              disabled={busy}
              onClick={handleSyncLocalToServer}
            >
              <CloudUpload className="size-3.5" aria-hidden="true" />
              <span>Đồng bộ {pendingLocalTickers.length} mã từ trình duyệt</span>
            </Button>
          ) : null}
        </div>
      ) : null}

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">Thêm doanh nghiệp vào danh sách</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-sm">
              <Input
                value={tickerInput}
                onChange={(e) => {
                  setTickerInput(e.target.value);
                  if (tickerError) setTickerError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && tickerInput.trim()) {
                    e.preventDefault();
                    void handleAdd(resolveSubmittedTicker());
                  }
                }}
                placeholder="Nhập mã hoặc tên DN (VD: HPG, FPT...)"
                aria-label="Tìm mã cổ phiếu để thêm vào danh sách theo dõi"
              />
            </div>
            <Button
              type="button"
              size="sm"
              disabled={!tickerInput.trim() || busy}
              onClick={() => void handleAdd(resolveSubmittedTicker())}
            >
              <Plus className="size-3.5" aria-hidden="true" />
              Thêm mã
            </Button>
          </div>

          {tickerError ? (
            <p role="alert" className="text-xs text-risk-high">
              {tickerError}
            </p>
          ) : null}

          {issuerMatches.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {issuerMatches.map((iss) => (
                <button
                  key={iss.issuer_id}
                  type="button"
                  onClick={() => void handleAdd(iss.ticker)}
                  className="inline-flex items-center gap-1.5 rounded border border-border bg-canvas px-2.5 py-1 text-xs hover:border-accent-ink hover:bg-accent-soft/40"
                >
                  <span className="font-mono font-semibold text-accent-ink">
                    {iss.ticker}
                  </span>
                  <span className="max-w-[200px] truncate text-ink-muted">
                    {iss.company_name_vn}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          {importStatus ? (
            <p role="status" className="text-xs text-accent-ink">
              {importStatus}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {watchlist.length === 0 ? (
        <EmptyState
          title="Danh sách theo dõi đang trống"
          description="Thêm mã cổ phiếu bằng ô tìm kiếm bên trên hoặc nhấn nút Theo dõi trên hồ sơ từng doanh nghiệp."
          action={
            <Link
              href="/screener"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Đi tới bảng sàng lọc →
            </Link>
          }
        />
      ) : (
        <div className="space-y-3">
          {changesQuery.isError ? (
            <ErrorState
              message={changesQuery.error.message}
              onRetry={() => changesQuery.refetch()}
            />
          ) : null}

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Biến động rủi ro giữa kỳ gần nhất và kỳ liền trước
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Mã
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Doanh nghiệp
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Kỳ so sánh
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Nhóm rủi ro &amp; Vị thế
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Chuyển vùng Altman
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        1Y PD (Trước → Nay · × nền)
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        Biến động
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        Cờ đỏ
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        Thao tác
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {watchlist.map((ticker) => {
                      const row = changesQuery.data?.items.find((it) => it.ticker === ticker);
                      if (!row) {
                        return (
                          <tr key={ticker} className="border-b border-border/60">
                            <td className="px-3 py-2 font-mono font-semibold text-accent-ink">
                              <Link href={`/companies/${ticker}`} className="hover:underline">
                                {ticker}
                              </Link>
                            </td>
                            <td colSpan={7} className="px-3 py-2 text-ink-subtle">
                              {changesQuery.isLoading
                                ? "Đang tải dữ liệu biến động..."
                                : changesQuery.isError
                                  ? "Không thể tải dữ liệu biến động cho phạm vi/loại kỳ đang chọn."
                                  : "Không có đủ dữ liệu cho phạm vi/loại kỳ đang chọn."}
                            </td>
                            <td className="px-3 py-2 text-right">
                              <div className="inline-flex items-center gap-1">
                                {isAuthenticated ? (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="xs"
                                    aria-label={`Ghi chú ${ticker}`}
                                    onClick={() => setSelectedNoteTicker(ticker)}
                                  >
                                    <FileText className="size-3.5" aria-hidden="true" />
                                    <span>Ghi chú</span>
                                  </Button>
                                ) : null}
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="xs"
                                  disabled={busy}
                                  aria-label={`Bỏ theo dõi ${ticker}`}
                                  onClick={() => void handleRemove(ticker)}
                                  className="text-ink-muted hover:text-risk-high"
                                >
                                  <Trash2 className="size-3.5" aria-hidden="true" />
                                  <span>Xóa</span>
                                </Button>
                              </div>
                            </td>
                          </tr>
                        );
                      }

                      const prevMeta = row.altman_zone_prev
                        ? ALTMAN_ZONE_META[row.altman_zone_prev]
                        : null;
                      const currMeta = row.altman_zone_curr
                        ? ALTMAN_ZONE_META[row.altman_zone_curr]
                        : null;
                      const prevTier = getRiskTierMeta(row.risk_tier_prev);
                      const trio = formatCreditTrio({
                        risk_tier: row.risk_tier_curr,
                        risk_market_percentile: row.risk_market_percentile_curr,
                        calibrated_distress_prob: row.distress_prob_curr,
                        relative_risk_lift: row.relative_risk_lift_curr,
                        market_base_rate: row.market_base_rate_curr,
                      });

                      return (
                        <tr
                          key={ticker}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-3 py-2">
                            <Link
                              href={`/companies/${row.ticker}`}
                              className="font-mono font-semibold text-accent-ink hover:underline"
                            >
                              {row.ticker}
                            </Link>
                          </td>
                          <td className="max-w-[220px] truncate px-3 py-2">
                            <div className="truncate font-medium text-ink">
                              {row.company_name_vn ?? row.ticker}
                            </div>
                            <div className="truncate text-[11px] text-ink-muted">
                              {row.icb_name_level2 ?? "—"}
                            </div>
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {row.prev_period_end_date
                              ? `${formatPeriodLabel(row.prev_period_end_date, periodType)} → `
                              : ""}
                            {formatPeriodLabel(row.curr_period_end_date, periodType)}
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-2 whitespace-nowrap">
                              {prevTier.tier ? (
                                <span className="font-mono text-[11px] text-ink-muted">
                                  {prevTier.badgeText} →
                                </span>
                              ) : null}
                              <RiskTierBadge riskTier={row.risk_tier_curr} compact />
                            </div>
                            {trio.topPercent !== null ? (
                              <div className="mt-0.5 text-[11px] text-ink-muted">
                                {trio.topPercentLabel}
                              </div>
                            ) : null}
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap items-center gap-1">
                              <span className="text-ink-muted">
                                {prevMeta?.shortLabel ?? "N/A"} →
                              </span>
                              <RiskBadge
                                tone={currMeta?.tone ?? "neutral"}
                                label={currMeta?.shortLabel ?? "N/A"}
                              />
                            </div>
                          </td>
                          <td className="num px-3 py-2 text-right">
                            <div>
                              {formatPercent(row.distress_prob_prev)} →{" "}
                              <strong className="text-ink">
                                {formatPercent(row.distress_prob_curr)}
                              </strong>
                            </div>
                            {trio.liftLabel ? (
                              <div className="text-[11px] text-ink-muted">{trio.liftLabel}</div>
                            ) : null}
                          </td>
                          <td className="num px-3 py-2 text-right">
                            <span
                              className={
                                (row.distress_prob_delta ?? 0) > 0.005
                                  ? "font-medium text-risk-high"
                                  : (row.distress_prob_delta ?? 0) < -0.005
                                    ? "font-medium text-risk-low"
                                    : "text-ink-muted"
                              }
                            >
                              {formatPercentPoints(row.distress_prob_delta, 1)}
                            </span>
                          </td>
                          <td className="num px-3 py-2 text-right">
                            <span>{row.red_flag_count_curr}</span>
                            {row.new_red_flag_codes.length > 0 ? (
                              <span className="ml-1 text-[11px] font-medium text-risk-high">
                                (+{row.new_red_flag_codes.length})
                              </span>
                            ) : null}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <div className="inline-flex items-center gap-1">
                              {isAuthenticated ? (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="xs"
                                  aria-label={`Ghi chú ${row.ticker}`}
                                  onClick={() => setSelectedNoteTicker(row.ticker)}
                                >
                                  <FileText className="size-3.5" aria-hidden="true" />
                                  <span>Ghi chú</span>
                                </Button>
                              ) : null}
                              <Button
                                type="button"
                                variant="ghost"
                                size="xs"
                                disabled={busy}
                                aria-label={`Bỏ theo dõi ${row.ticker}`}
                                onClick={() => void handleRemove(row.ticker)}
                                className="text-ink-muted hover:text-risk-high"
                              >
                                <Trash2 className="size-3.5" aria-hidden="true" />
                                <span>Xóa</span>
                              </Button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {isAuthenticated && selectedNoteTicker ? (
            <AnalystNotesPanel
              ticker={selectedNoteTicker}
              onClose={() => setSelectedNoteTicker(null)}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}
