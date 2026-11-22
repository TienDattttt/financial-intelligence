"use client";

import * as React from "react";
import Link from "next/link";
import { Download, GitCompare, Plus, Trash2, Upload } from "lucide-react";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { useAllIssuers, useMarketChanges } from "@/hooks/use-finintel-queries";
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
import {
  addWatchlistTicker,
  exportWatchlistJson,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  importWatchlistJson,
  removeWatchlistTicker,
  subscribePreferences,
} from "@/lib/watchlist-store";

export default function WatchlistPage() {
  const { statementScope, periodType, snapshotId } = useAppState();
  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );

  const [tickerInput, setTickerInput] = React.useState("");
  const [importStatus, setImportStatus] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  const issuersQuery = useAllIssuers(snapshotId);
  const changesQuery = useMarketChanges(
    {
      tickers: watchlist,
      statementScope,
      periodType,
      limit: 50,
      enabled: watchlist.length > 0,
    },
    snapshotId,
  );

  const issuerMatches = React.useMemo(() => {
    const q = normalizeSearchText(tickerInput);
    if (!q) return [];
    const list = issuersQuery.data ?? [];
    return list
      .filter((iss) => {
        const tickerNorm = normalizeSearchText(iss.ticker);
        const nameNorm = normalizeSearchText(iss.company_name_vn ?? "");
        return tickerNorm.includes(q) || nameNorm.includes(q);
      })
      .slice(0, 8);
  }, [issuersQuery.data, tickerInput]);

  const handleAdd = (ticker: string) => {
    addWatchlistTicker(ticker);
    setTickerInput("");
    setImportStatus(null);
  };

  const handleExport = () => {
    const blob = new Blob([exportWatchlistJson()], {
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
    const res = importWatchlistJson(text);
    if (res.ok) {
      setImportStatus(`Đã nhập thành công ${res.count} mã cổ phiếu.`);
    } else {
      setImportStatus(res.error);
    }
    e.target.value = "";
  };

  const compareTickers = watchlist.slice(0, 5);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Danh sách theo dõi ({watchlist.length}/50)
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Lưu trữ cục bộ trên trình duyệt · So sánh biến động{" "}
            {PERIOD_TYPE_LABELS[periodType] ?? periodType} (
            {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope})
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {compareTickers.length >= 2 ? (
            <Link href={`/compare?tickers=${compareTickers.join(",")}`}>
              <Button size="sm">
                <GitCompare className="size-3.5" />
                So sánh ({compareTickers.length} mã đầu)
              </Button>
            </Link>
          ) : null}
          <Button variant="outline" size="sm" onClick={handleExport} disabled={watchlist.length === 0}>
            <Download className="size-3.5" />
            Xuất JSON
          </Button>
          <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
            <Upload className="size-3.5" />
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

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">Thêm doanh nghiệp vào danh sách</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-sm">
              <Input
                value={tickerInput}
                onChange={(e) => setTickerInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && tickerInput.trim()) {
                    e.preventDefault();
                    handleAdd(issuerMatches[0]?.ticker ?? tickerInput.trim());
                  }
                }}
                placeholder="Nhập mã hoặc tên DN (VD: HPG, FPT...)"
                aria-label="Tìm mã cổ phiếu để thêm vào danh sách theo dõi"
              />
            </div>
            <Button
              type="button"
              size="sm"
              disabled={!tickerInput.trim()}
              onClick={() => handleAdd(issuerMatches[0]?.ticker ?? tickerInput.trim())}
            >
              <Plus className="size-3.5" />
              Thêm mã
            </Button>
          </div>

          {issuerMatches.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {issuerMatches.map((iss) => (
                <button
                  key={iss.issuer_id}
                  type="button"
                  onClick={() => handleAdd(iss.ticker)}
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
            <p className="text-xs text-accent-ink">{importStatus}</p>
          ) : null}
        </CardContent>
      </Card>

      {watchlist.length === 0 ? (
        <EmptyState
          title="Danh sách theo dõi đang trống"
          description="Thêm mã cổ phiếu bằng ô tìm kiếm bên trên hoặc nhấn nút Theo dõi trên hồ sơ từng doanh nghiệp."
          action={
            <Link href="/screener">
              <Button variant="outline" size="sm">
                Đi tới bảng sàng lọc →
              </Button>
            </Link>
          }
        />
      ) : changesQuery.isLoading ? (
        <PageSkeleton />
      ) : changesQuery.isError ? (
        <ErrorState
          message={changesQuery.error.message}
          onRetry={() => changesQuery.refetch()}
        />
      ) : (
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
                      Bậc tín nhiệm &amp; Vị thế
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
                            <Link href={`/companies/${ticker}`}>{ticker}</Link>
                          </td>
                          <td colSpan={7} className="px-3 py-2 text-ink-subtle">
                            Không có đủ dữ liệu cho phạm vi/loại kỳ đang chọn.
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-xs"
                              aria-label={`Bỏ theo dõi ${ticker}`}
                              onClick={() => removeWatchlistTicker(ticker)}
                            >
                              <Trash2 className="size-3.5 text-ink-muted" />
                            </Button>
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
                          <div className="flex flex-wrap items-center gap-1">
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
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`Bỏ theo dõi ${row.ticker}`}
                            onClick={() => removeWatchlistTicker(row.ticker)}
                          >
                            <Trash2 className="size-3.5 text-ink-muted hover:text-risk-high" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
