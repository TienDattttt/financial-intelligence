"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Plus, ShieldAlert } from "lucide-react";
import { SectorTreemapChart } from "@/components/charts/SectorTreemapChart";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useAllIssuers,
  useMarketChanges,
  useSectorRiskSummary,
} from "@/hooks/use-finintel-queries";
import {
  ALTMAN_ZONE_META,
  formatCreditTrio,
  getRiskTierMeta,
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
  MINUS_SIGN,
} from "@/lib/format";
import {
  addWatchlistTicker,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  subscribePreferences,
} from "@/lib/watchlist-store";

const PREFERRED_SUGGESTIONS = ["HPG", "FPT", "VNM", "MWG", "GAS"];

export default function MarketOverviewPage() {
  const router = useRouter();
  const { statementScope, periodType, periodEndDate, availableDates, snapshotId } =
    useAppState();

  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );

  const sectorSummary = useSectorRiskSummary(
    { statementScope, periodType, periodEndDate },
    snapshotId,
  );
  const currentPeriodDate =
    sectorSummary.data?.period_end_date ?? periodEndDate ?? availableDates[0] ?? null;

  const prevPeriodDate = React.useMemo(() => {
    if (!currentPeriodDate || availableDates.length === 0) return null;
    const desc = [...availableDates].sort((a, b) => b.localeCompare(a));
    return desc.find((d) => d < currentPeriodDate) ?? null;
  }, [availableDates, currentPeriodDate]);

  const prevSectorSummary = useSectorRiskSummary(
    {
      statementScope,
      periodType,
      periodEndDate: prevPeriodDate ?? periodEndDate,
    },
    snapshotId,
  );
  const prevData = prevPeriodDate ? prevSectorSummary.data ?? null : null;
  const watchlistChanges = useMarketChanges(
    {
      tickers: watchlist,
      statementScope,
      periodType,
      limit: 20,
      enabled: watchlist.length > 0,
    },
    snapshotId,
  );
  const issuersQuery = useAllIssuers(snapshotId);

  const suggestedTickers = React.useMemo(() => {
    const issuers = issuersQuery.data ?? [];
    if (issuers.length === 0) return PREFERRED_SUGGESTIONS;
    const available = new Set(issuers.map((i) => i.ticker.toUpperCase()));
    const picks = PREFERRED_SUGGESTIONS.filter((t) => available.has(t));
    if (picks.length >= 5) return picks.slice(0, 5);
    for (const iss of issuers) {
      const t = iss.ticker.toUpperCase();
      if (!picks.includes(t)) picks.push(t);
      if (picks.length >= 5) break;
    }
    return picks;
  }, [issuersQuery.data]);

  const data = sectorSummary.data;

  const kpiCards = React.useMemo(() => {
    if (!data) return [];
    const total = data.total_issuers;
    const distressShare = total > 0 ? data.distress_zone_count / total : 0;
    const beneishShare = total > 0 ? data.beneish_flagged_count / total : 0;
    const redFlagShare = total > 0 ? data.high_red_flag_count / total : 0;

    const prevTotal = prevData?.total_issuers ?? 0;
    const hasPrev = Boolean(prevData && prevTotal > 0);
    const prevLabel = prevData?.period_end_date
      ? formatPeriodLabel(prevData.period_end_date, data.period_type)
      : "kỳ trước";

    const formatCountDelta = (
      currCount: number,
      prevCount: number | null,
      currShare: number,
      prevShare: number | null,
      higherIsWorse: boolean,
    ): { text: string; toneClass: string } | null => {
      if (!hasPrev || prevCount === null) return null;
      const diff = currCount - prevCount;
      const sign = diff > 0 ? "+" : diff < 0 ? MINUS_SIGN : "";
      const countPart =
        diff === 0 ? "0 DN" : `${sign}${formatNumber(Math.abs(diff), 0)} DN`;
      const ppPart =
        prevShare !== null ? ` (${formatPercentPoints(currShare - prevShare, 1)})` : "";
      const toneClass =
        diff === 0
          ? "text-ink-subtle"
          : higherIsWorse
            ? diff > 0
              ? "text-risk-high"
              : "text-risk-low"
            : diff > 0
              ? "text-up"
              : "text-down";
      return {
        text: `${countPart}${ppPart} so với ${prevLabel}`,
        toneClass,
      };
    };

    const totalDelta = hasPrev
      ? (() => {
          const diff = total - prevTotal;
          const sign = diff > 0 ? "+" : diff < 0 ? MINUS_SIGN : "";
          return {
            text:
              diff === 0
                ? `Không đổi so với ${prevLabel}`
                : `${sign}${formatNumber(Math.abs(diff), 0)} DN so với ${prevLabel}`,
            toneClass:
              diff > 0 ? "text-up" : diff < 0 ? "text-down" : "text-ink-subtle",
          };
        })()
      : null;

    return [
      {
        title: "Doanh nghiệp được đánh giá",
        value: formatNumber(total, 0),
        sub: `${data.safe_zone_count} An toàn · ${data.grey_zone_count} Vùng xám`,
        delta: totalDelta,
        href: "/screener",
        toneClass: "text-ink",
      },
      {
        title: "Vùng nguy cơ kiệt quệ (Z″ < 1,10)",
        value: formatNumber(data.distress_zone_count, 0),
        sub: `${formatPercent(distressShare)} số DN trong lát cắt`,
        delta: formatCountDelta(
          data.distress_zone_count,
          prevData?.distress_zone_count ?? null,
          distressShare,
          prevData && prevTotal > 0 ? prevData.distress_zone_count / prevTotal : null,
          true,
        ),
        href: "/screener?altman_zone=DISTRESS_ZONE",
        toneClass: "text-risk-high",
      },
      {
        title: "Nghi vấn điều chỉnh LN (M-Score)",
        value: formatNumber(data.beneish_flagged_count, 0),
        sub: `${formatPercent(beneishShare)} vượt ngưỡng Beneish > −1,78`,
        delta: formatCountDelta(
          data.beneish_flagged_count,
          prevData?.beneish_flagged_count ?? null,
          beneishShare,
          prevData && prevTotal > 0 ? prevData.beneish_flagged_count / prevTotal : null,
          true,
        ),
        href: "/screener?beneish_flag=HIGH_MANIPULATION_RISK_SCREEN",
        toneClass: "text-risk-mid",
      },
      {
        title: "Cảnh báo ≥ 2 cờ đỏ kế toán",
        value: formatNumber(data.high_red_flag_count, 0),
        sub: `${formatPercent(redFlagShare)} DN có nhiều tín hiệu bất thường`,
        delta: formatCountDelta(
          data.high_red_flag_count,
          prevData?.high_red_flag_count ?? null,
          redFlagShare,
          prevData && prevTotal > 0 ? prevData.high_red_flag_count / prevTotal : null,
          true,
        ),
        href: "/screener?min_red_flags=2",
        toneClass: "text-risk-high",
      },
    ];
  }, [data, prevData]);

  if (sectorSummary.isLoading) {
    return <PageSkeleton />;
  }

  if (sectorSummary.isError) {
    return (
      <ErrorState
        message={sectorSummary.error.message}
        onRetry={() => sectorSummary.refetch()}
      />
    );
  }

  if (!data) {
    return (
      <EmptyState
        title="Chưa có dữ liệu tổng quan thị trường"
        description="Vui lòng chọn kỳ dữ liệu hoặc phạm vi báo cáo khác trên thanh công cụ."
      />
    );
  }

  const effectiveLabel = data.period_end_date
    ? `${formatPeriodLabel(data.period_end_date, data.period_type)} (${formatDateVi(data.period_end_date)})`
    : "Kỳ mới nhất";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Tổng quan rủi ro thị trường
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Phạm vi {STATEMENT_SCOPE_LABELS[data.statement_scope] ?? data.statement_scope} ·{" "}
            {PERIOD_TYPE_LABELS[data.period_type] ?? data.period_type} · {effectiveLabel}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/screener" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Mở bảng sàng lọc
            <ArrowRight className="size-3.5" />
          </Link>
          <Link href="/stress-test" className={buttonVariants({ variant: "default", size: "sm" })}>
            Mô phỏng kịch bản sốc
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>

      <section
        aria-label="Chỉ số tổng hợp rủi ro"
        className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-4"
      >
        {kpiCards.map((kpi) => (
          <Link
            key={kpi.title}
            href={kpi.href}
            className="group flex flex-col justify-between bg-surface p-4 transition-colors duration-150 hover:bg-surface-hover/50"
          >
            <p className="text-xs font-medium text-ink-muted group-hover:text-accent-ink">
              {kpi.title}
            </p>
            <p className={`num mt-1.5 text-[26px] font-semibold leading-8 ${kpi.toneClass}`}>
              {kpi.value}
            </p>
            <div className="mt-1.5 space-y-0.5">
              <p className="num text-[11px] text-ink-muted">{kpi.sub}</p>
              {kpi.delta ? (
                <p className={`num text-[11px] font-medium ${kpi.delta.toneClass}`}>
                  {kpi.delta.text}
                </p>
              ) : null}
            </div>
          </Link>
        ))}
      </section>

      <SectorTreemapChart
        sectors={data.sectors}
        onSelectSector={(code) =>
          router.push(`/screener?icb_code_level2=${encodeURIComponent(code)}`)
        }
      />

      <section className="grid grid-cols-1 items-start gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-8">
          <CardHeader className="py-2.5">
            <div>
              <CardTitle className="flex items-center gap-1.5 text-[14px]">
                <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
                <span>Cần chú ý trong kỳ mới</span>
              </CardTitle>
              <p className="mt-0.5 text-xs text-ink-muted">
                Các doanh nghiệp có mức tăng xác suất kiệt quệ, chuyển vùng Altman hoặc phát sinh cờ đỏ mới
              </p>
            </div>
            <Link
              href="/screener?sort_by=calibrated_distress_prob&sort_dir=desc"
              className="text-xs font-medium text-accent-ink hover:underline"
            >
              Xem tất cả →
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {data.top_deteriorating.length === 0 ? (
              <p className="p-4 text-xs text-ink-muted">
                Không ghi nhận biến động suy giảm đáng kể giữa 2 kỳ liền kề.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/60 text-ink-muted">
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Mã
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Ngành
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Bậc rủi ro
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Chuyển vùng Altman
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        XS rủi ro 1 năm (Biến động)
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        Cờ đỏ mới
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_deteriorating.slice(0, 12).map((row) => {
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
                          key={row.issuer_id}
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
                          <td className="max-w-[140px] truncate px-3 py-2 text-ink-muted">
                            {row.icb_name_level2 ?? "—"}
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
                              <div className="mt-0.5 text-[10px] text-ink-subtle">
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
                              <span
                                className={
                                  (row.distress_prob_delta ?? 0) > 0.005
                                    ? "font-medium text-risk-high"
                                    : "text-ink"
                                }
                              >
                                {formatPercent(row.distress_prob_curr)}
                              </span>
                              <span className="ml-1 text-[11px] text-ink-subtle">
                                ({formatPercentPoints(row.distress_prob_delta, 1)})
                              </span>
                            </div>
                            {trio.liftLabel ? (
                              <div className="text-[10px] text-ink-muted">{trio.liftLabel}</div>
                            ) : null}
                          </td>
                          <td className="num px-3 py-2 text-right">
                            {row.new_red_flag_codes.length > 0 ? (
                              <span className="font-medium text-risk-high">
                                +{row.new_red_flag_codes.length}
                              </span>
                            ) : (
                              <span className="text-ink-subtle">
                                {row.red_flag_count_curr}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4 lg:col-span-4">
          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Phân bố vùng sức khỏe Altman Z″</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5 p-3.5 text-xs">
              {(() => {
                const total = Math.max(1, data.total_issuers);
                const bands = [
                  {
                    label: "Vùng an toàn (Z″ > 2,60)",
                    count: data.safe_zone_count,
                    pct: (data.safe_zone_count / total) * 100,
                    barClass: "bg-risk-low",
                    textClass: "text-risk-low",
                    href: "/screener?altman_zone=SAFE_ZONE",
                  },
                  {
                    label: "Vùng xám (1,10 – 2,60)",
                    count: data.grey_zone_count,
                    pct: (data.grey_zone_count / total) * 100,
                    barClass: "bg-risk-mid",
                    textClass: "text-risk-mid",
                    href: "/screener?altman_zone=GREY_ZONE",
                  },
                  {
                    label: "Vùng nguy cơ (Z″ < 1,10)",
                    count: data.distress_zone_count,
                    pct: (data.distress_zone_count / total) * 100,
                    barClass: "bg-risk-high",
                    textClass: "text-risk-high",
                    href: "/screener?altman_zone=DISTRESS_ZONE",
                  },
                ];
                return bands.map((b) => (
                  <Link
                    key={b.label}
                    href={b.href}
                    className="block rounded-md p-1.5 transition-colors hover:bg-surface-hover/60"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{b.label}</span>
                      <span className={`num font-semibold ${b.textClass}`}>
                        {formatNumber(b.count, 0)} DN ({formatPercent(b.pct / 100, { decimals: 1 })})
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
                      <div
                        className={`h-full rounded-full ${b.barClass}`}
                        style={{ width: `${Math.min(100, Math.max(2, b.pct))}%` }}
                      />
                    </div>
                  </Link>
                ));
              })()}
            </CardContent>
          </Card>

          <Card>
          <CardHeader className="py-2.5">
            <div>
              <CardTitle className="text-[14px]">
                Danh sách theo dõi ({watchlist.length})
              </CardTitle>
              <p className="mt-0.5 text-xs text-ink-muted">
                Lưu trên trình duyệt cá nhân · Tự động cập nhật thay đổi theo kỳ
              </p>
            </div>
            <Link
              href="/watchlist"
              className="text-xs font-medium text-accent-ink hover:underline"
            >
              Quản lý →
            </Link>
          </CardHeader>
          <CardContent className="p-3.5">
            {watchlist.length === 0 ? (
              <div className="space-y-3 rounded border border-dashed border-border bg-canvas p-4 text-center">
                <p className="text-xs text-ink-muted">
                  Bạn chưa theo dõi doanh nghiệp nào. Chọn nhanh mã phổ biến bên dưới hoặc nhấn ngôi sao trên trang hồ sơ DN.
                </p>
                <div className="flex flex-wrap justify-center gap-1.5">
                  {suggestedTickers.map((t) => (
                    <Button
                      key={t}
                      type="button"
                      variant="outline"
                      size="xs"
                      onClick={() => addWatchlistTicker(t)}
                    >
                      <Plus className="size-3" />
                      <span className="font-mono">{t}</span>
                    </Button>
                  ))}
                </div>
              </div>
            ) : watchlistChanges.isLoading ? (
              <p className="text-xs text-ink-muted">Đang tải biến động danh sách theo dõi...</p>
            ) : watchlistChanges.isError ? (
              <ErrorState
                message={watchlistChanges.error.message}
                onRetry={() => watchlistChanges.refetch()}
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/60 text-ink-muted">
                      <th scope="col" className="px-2.5 py-2 text-left font-semibold">
                        Mã
                      </th>
                      <th scope="col" className="px-2.5 py-2 text-left font-semibold">
                        Bậc &amp; Vị thế
                      </th>
                      <th scope="col" className="px-2.5 py-2 text-right font-semibold">
                        XS rủi ro 1 năm (so với TB)
                      </th>
                      <th scope="col" className="px-2.5 py-2 text-right font-semibold">
                        Cờ đỏ
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(watchlistChanges.data?.items ?? []).map((item) => {
                      const trio = formatCreditTrio({
                        risk_tier: item.risk_tier_curr,
                        risk_market_percentile: item.risk_market_percentile_curr,
                        calibrated_distress_prob: item.distress_prob_curr,
                        relative_risk_lift: item.relative_risk_lift_curr,
                        market_base_rate: item.market_base_rate_curr,
                      });
                      return (
                        <tr
                          key={item.ticker}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-2.5 py-2">
                            <Link
                              href={`/companies/${item.ticker}`}
                              className="font-mono font-semibold text-accent-ink hover:underline"
                            >
                              {item.ticker}
                            </Link>
                          </td>
                          <td className="px-2.5 py-2">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <RiskTierBadge riskTier={item.risk_tier_curr} compact />
                              {trio.topPercent !== null ? (
                                <span className="text-[11px] text-ink-muted">
                                  {trio.topPercentLabel}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td className="num px-2.5 py-2 text-right">
                            <div className="font-medium text-ink">{trio.pdLabel}</div>
                            {trio.liftLabel ? (
                              <div className="text-[10px] text-ink-muted">{trio.liftLabel}</div>
                            ) : null}
                          </td>
                          <td className="num px-2.5 py-2 text-right">
                            {item.red_flag_count_curr}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
        </div>
      </section>
    </div>
  );
}
