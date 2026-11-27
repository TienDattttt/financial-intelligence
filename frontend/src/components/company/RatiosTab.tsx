"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown, Link2 } from "lucide-react";
import {
  groupAndSortSummaryRatios,
  HEALTH_PILLAR_DEFS,
  HealthRadarChart,
  type PillarGroupId,
  type RatioTableSortField,
} from "@/components/charts/HealthRadarChart";
import { DuPontTreeChart } from "@/components/charts/DuPontTreeChart";
import { PeerBandChart } from "@/components/charts/PeerBandChart";
import { SectorBoxPlotChart } from "@/components/charts/SectorBoxPlotChart";
import { PercentileBar } from "@/components/financial/PercentileBar";
import { SparklineSvg } from "@/components/financial/SparklineSvg";
import { ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import {
  formatPeerGroupLabel,
  RATIO_CATEGORY_LABELS,
} from "@/lib/domain-labels";
import {
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPeriodLabel,
  formatRatioValue,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CompanyBenchmarksResponse,
  CompanyBenchmarksSummaryResponse,
  CompanyProfileResponse,
  PeriodType,
  RatioDictionaryItem,
  RatioName,
  StatementScope,
} from "@/types/api";
import type { CompanyTab } from "@/components/company/CompanyHeader";

export function RatiosTab({
  profile,
  metricParam,
  benchmarkMeta,
  ratiosDict,
  benchmarksData,
  benchmarksLoading,
  benchmarksError,
  benchmarksErrorMessage,
  onRetryBenchmarks,
  summaryData,
  summaryLoading,
  ratiosShowAll38,
  onChangeRatiosShowAll38,
  statementScope,
  periodType,
  periodEndDate,
  onNavigateTab,
  onOpenSourceDrawer,
}: {
  profile: CompanyProfileResponse;
  metricParam: RatioName;
  benchmarkMeta: RatioDictionaryItem | undefined;
  ratiosDict: readonly RatioDictionaryItem[];
  benchmarksData: CompanyBenchmarksResponse | undefined;
  benchmarksLoading: boolean;
  benchmarksError: boolean;
  benchmarksErrorMessage?: string;
  onRetryBenchmarks: () => void;
  summaryData: CompanyBenchmarksSummaryResponse | undefined;
  summaryLoading: boolean;
  ratiosShowAll38: boolean;
  onChangeRatiosShowAll38: (v: boolean) => void;
  statementScope: StatementScope;
  periodType: PeriodType;
  periodEndDate: string | null;
  onNavigateTab: (tab: CompanyTab, extra?: Record<string, string | null>) => void;
  onOpenSourceDrawer: (req: {
    ticker: string;
    ratioName: RatioName;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate: string | null;
  }) => void;
}) {
  const [ratioSearch, setRatioSearch] = React.useState("");
  const [ratioGroupFilter, setRatioGroupFilter] = React.useState<PillarGroupId | "ALL">("ALL");
  const [ratioSortBy, setRatioSortBy] = React.useState<RatioTableSortField>("name");
  const [ratioSortDir, setRatioSortDir] = React.useState<"asc" | "desc">("asc");

  const groupedRatioBuckets = React.useMemo(
    () =>
      groupAndSortSummaryRatios(summaryData?.items ?? [], {
        search: ratioSearch,
        groupId: ratioGroupFilter,
        sortBy: ratioSortBy,
        sortDir: ratioSortDir,
      }),
    [ratioGroupFilter, ratioSearch, ratioSortBy, ratioSortDir, summaryData?.items],
  );

  const toggleRatioSort = React.useCallback(
    (field: RatioTableSortField) => {
      if (ratioSortBy === field) {
        setRatioSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setRatioSortBy(field);
        setRatioSortDir(field === "name" ? "asc" : "desc");
      }
    },
    [ratioSortBy],
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3.5">
          <div className="flex w-full flex-col gap-1.5 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:gap-2">
            <label htmlFor="ratio-select" className="text-xs font-semibold text-ink">
              Chọn tỷ số phân tích:
            </label>
            <Select
              id="ratio-select"
              value={metricParam}
              onChange={(e) =>
                onNavigateTab("ratios", { metric: e.target.value })
              }
              className="w-full text-xs sm:w-auto sm:min-w-[260px]"
            >
              {Object.entries(RATIO_CATEGORY_LABELS).map(([catKey, catLbl]) => {
                const groupItems = ratiosDict.filter((r) => r.category === catKey);
                if (groupItems.length === 0) return null;
                return (
                  <optgroup key={catKey} label={catLbl}>
                    {groupItems.map((r) => (
                      <option key={r.ratio_name} value={r.ratio_name}>
                        {r.display_name_vn}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </Select>
          </div>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() =>
              onOpenSourceDrawer({
                ticker: profile.ticker,
                ratioName: metricParam,
                statementScope,
                periodType,
                periodEndDate: benchmarksData?.period_end_date ?? periodEndDate,
              })
            }
          >
            <Link2 className="size-3" />
            Truy nguồn chỉ số này
          </Button>
        </CardContent>
      </Card>

      {benchmarksLoading ? (
        <PageSkeleton />
      ) : benchmarksError ? (
        <ErrorState
          message={benchmarksErrorMessage ?? "Không thể tải dữ liệu so sánh ngành."}
          onRetry={onRetryBenchmarks}
        />
      ) : benchmarksData ? (
        <>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <HealthRadarChart
              ticker={profile.ticker}
              items={summaryData?.items ?? []}
              isLoading={summaryLoading}
            />
            <SectorBoxPlotChart
              ticker={profile.ticker}
              metricLabel={benchmarkMeta?.display_name_vn ?? metricParam}
              formatType={benchmarkMeta?.format_type ?? "NUMBER"}
              companyValue={benchmarksData.company_value}
              peerP25={benchmarksData.peer_p25}
              peerMedian={benchmarksData.peer_median}
              peerP75={benchmarksData.peer_p75}
              peerRanking={benchmarksData.peer_ranking}
              peerGroupLevel={benchmarksData.peer_group_level}
              peerCount={benchmarksData.peer_count}
            />
          </div>

          <PeerBandChart
            metricLabel={benchmarkMeta?.display_name_vn ?? metricParam}
            formatType={benchmarkMeta?.format_type ?? "NUMBER"}
            periodType={benchmarksData.period_type}
            history={benchmarksData.history}
            ticker={profile.ticker}
          />

          <Card>
            <CardHeader className="flex-wrap gap-2 py-2.5">
              <div>
                <CardTitle className="text-[14px]">
                  Bảng tỷ số phân nhóm theo 5 trụ cột nghiệp vụ
                </CardTitle>
                <p className="mt-0.5 text-xs text-ink-muted">
                  Nhấp tên chỉ số để cập nhật biểu đồ dải P25–P75 và Box Plot phía trên · Sắp xếp theo từng cột
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  value={ratioSearch}
                  onChange={(e) => setRatioSearch(e.target.value)}
                  placeholder="Tìm chỉ số..."
                  aria-label="Tìm kiếm chỉ số tài chính"
                  className="h-7 w-44 text-xs"
                />
                <div className="inline-flex rounded-md border border-border bg-surface-muted p-0.5 text-xs">
                  <button
                    type="button"
                    onClick={() => onChangeRatiosShowAll38(false)}
                    className={cn(
                      "rounded-sm px-2 py-1 font-medium transition-colors duration-150",
                      !ratiosShowAll38
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    12 cốt lõi
                  </button>
                  <button
                    type="button"
                    onClick={() => onChangeRatiosShowAll38(true)}
                    className={cn(
                      "rounded-sm px-2 py-1 font-medium transition-colors duration-150",
                      ratiosShowAll38
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    Tất cả {ratiosDict.length || 38}
                  </button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-3.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setRatioGroupFilter("ALL")}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-150",
                    ratioGroupFilter === "ALL"
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-surface text-ink-muted hover:text-ink",
                  )}
                >
                  Tất cả 5 nhóm
                </button>
                {HEALTH_PILLAR_DEFS.map((grp) => (
                  <button
                    key={grp.id}
                    type="button"
                    onClick={() => setRatioGroupFilter(grp.id)}
                    className={cn(
                      "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-150",
                      ratioGroupFilter === grp.id
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-surface text-ink-muted hover:text-ink",
                    )}
                  >
                    {grp.shortLabel}
                  </button>
                ))}
              </div>

              {summaryLoading ? (
                <p className="text-xs text-ink-muted">Đang tải bảng tỷ số theo nhóm...</p>
              ) : groupedRatioBuckets.length === 0 ? (
                <p className="text-xs text-ink-muted">
                  Không tìm thấy chỉ số phù hợp với bộ lọc hiện tại.
                </p>
              ) : (
                <>
                  <p className="text-[11px] text-ink-subtle md:hidden">
                    Vuốt ngang để xem phân vị, xu hướng &amp; truy nguồn →
                  </p>
                  <div className="overflow-x-auto rounded-md border border-border">
                    <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          <button
                            type="button"
                            onClick={() => toggleRatioSort("name")}
                            className="inline-flex items-center gap-1 hover:text-ink"
                          >
                            <span>Chỉ số</span>
                            {ratioSortBy === "name" ? (
                              ratioSortDir === "asc" ? (
                                <ArrowUp className="size-3" />
                              ) : (
                                <ArrowDown className="size-3" />
                              )
                            ) : (
                              <ArrowUpDown className="size-3 opacity-60" />
                            )}
                          </button>
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          <button
                            type="button"
                            onClick={() => toggleRatioSort("company_value")}
                            className="inline-flex items-center justify-end gap-1 hover:text-ink"
                          >
                            <span>Giá trị DN</span>
                            {ratioSortBy === "company_value" ? (
                              ratioSortDir === "asc" ? (
                                <ArrowUp className="size-3" />
                              ) : (
                                <ArrowDown className="size-3" />
                              )
                            ) : (
                              <ArrowUpDown className="size-3 opacity-60" />
                            )}
                          </button>
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          <button
                            type="button"
                            onClick={() => toggleRatioSort("peer_median")}
                            className="inline-flex items-center justify-end gap-1 hover:text-ink"
                          >
                            <span>Trung vị ngành</span>
                            {ratioSortBy === "peer_median" ? (
                              ratioSortDir === "asc" ? (
                                <ArrowUp className="size-3" />
                              ) : (
                                <ArrowDown className="size-3" />
                              )
                            ) : (
                              <ArrowUpDown className="size-3 opacity-60" />
                            )}
                          </button>
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          <button
                            type="button"
                            onClick={() => toggleRatioSort("percentile")}
                            className="inline-flex items-center gap-1 hover:text-ink"
                          >
                            <span>Phân vị ngành</span>
                            {ratioSortBy === "percentile" ? (
                              ratioSortDir === "asc" ? (
                                <ArrowUp className="size-3" />
                              ) : (
                                <ArrowDown className="size-3" />
                              )
                            ) : (
                              <ArrowUpDown className="size-3 opacity-60" />
                            )}
                          </button>
                        </th>
                        <th scope="col" className="px-3 py-2 text-center font-semibold">
                          Xu hướng
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          Nguồn
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {groupedRatioBuckets.map((bucket) => (
                        <React.Fragment key={bucket.id}>
                          <tr className="border-b border-border bg-surface-muted/60">
                            <th
                              colSpan={6}
                              scope="colgroup"
                              className="px-3 py-1.5 text-left text-[11px] font-semibold text-ink"
                            >
                              {bucket.label} ({bucket.items.length})
                            </th>
                          </tr>
                          {bucket.items.map((item) => {
                            const isActiveMetric = item.metric_name === metricParam;
                            return (
                              <tr
                                key={item.metric_name}
                                className={cn(
                                  "border-b border-border/60 hover:bg-surface-hover/40",
                                  isActiveMetric && "bg-accent-soft/45",
                                )}
                              >
                                <td className="px-3 py-2">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      onNavigateTab("ratios", {
                                        metric: item.metric_name,
                                      })
                                    }
                                    className={cn(
                                      "text-left font-medium hover:underline",
                                      isActiveMetric
                                        ? "font-semibold text-accent-ink"
                                        : "text-ink",
                                    )}
                                  >
                                    {item.display_name_vn}
                                  </button>
                                </td>
                                <td className="num px-3 py-2 text-right font-semibold text-ink">
                                  {formatRatioValue(item.company_value, item.format_type)}
                                </td>
                                <td className="num px-3 py-2 text-right text-ink-muted">
                                  {formatRatioValue(item.peer_median, item.format_type)}
                                </td>
                                <td className="px-3 py-2">
                                  <PercentileBar
                                    percentile={item.percentile_rank}
                                    direction={item.direction}
                                    peerGroupLevel={item.peer_group_level}
                                    peerCount={item.peer_count}
                                  />
                                </td>
                                <td className="px-3 py-2 text-center">
                                  <SparklineSvg values={item.sparkline ?? []} />
                                </td>
                                <td className="px-3 py-2 text-right">
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="xs"
                                    onClick={() =>
                                      onOpenSourceDrawer({
                                        ticker: profile.ticker,
                                        ratioName: item.metric_name,
                                        statementScope,
                                        periodType,
                                        periodEndDate:
                                          summaryData?.period_end_date ?? periodEndDate,
                                      })
                                    }
                                  >
                                    <Link2 className="size-3" />
                                    Truy nguồn
                                  </Button>
                                </td>
                              </tr>
                            );
                          })}
                        </React.Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2.5">
              <div>
                <CardTitle className="text-[14px]">
                  Xếp hạng trong nhóm so sánh ({benchmarksData.peer_ranking.length} DN)
                </CardTitle>
                <p className="mt-0.5 text-xs text-ink-muted">
                  {
                    formatPeerGroupLabel(
                      benchmarksData.peer_group_level,
                      benchmarksData.peer_count,
                    ).text
                  }
                </p>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <p className="mb-1.5 px-3 pt-2 text-[11px] text-ink-subtle md:hidden">
                Vuốt ngang để xem đầy đủ cột xếp hạng →
              </p>
              <div
                tabIndex={0}
                role="region"
                aria-label="Bảng xếp hạng trong nhóm so sánh"
                className="max-h-[360px] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                        Mã
                      </th>
                      <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                        Doanh nghiệp
                      </th>
                      <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                        Giá trị
                      </th>
                      <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                        Phân vị
                      </th>
                      <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                        Z-Score chuẩn hóa
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {benchmarksData.peer_ranking.map((peer, idx) => {
                      const isSelf = peer.ticker?.toUpperCase() === profile.ticker;
                      return (
                        <tr
                          key={`${peer.ticker ?? "peer"}-${idx}`}
                          className={cn(
                            "border-b border-border/60 hover:bg-surface-hover/40",
                            isSelf && "bg-accent-soft/50 font-semibold",
                          )}
                        >
                          <td className="px-3 py-2">
                            {peer.ticker ? (
                              <Link
                                href={`/companies/${peer.ticker}`}
                                className="font-mono text-accent-ink hover:underline"
                              >
                                {peer.ticker}
                              </Link>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="max-w-[240px] truncate px-3 py-2 text-ink">
                            {peer.company_name_vn ?? "—"}
                          </td>
                          <td className="num px-3 py-2 text-right">
                            {formatRatioValue(
                              peer.value,
                              benchmarkMeta?.format_type ?? "NUMBER",
                            )}
                          </td>
                          <td className="num px-3 py-2 text-right">
                            {peer.percentile_rank !== null &&
                            peer.percentile_rank !== undefined
                              ? `${formatNumber(peer.percentile_rank, 0)}%`
                              : "N/A"}
                          </td>
                          <td className="num px-3 py-2 text-right text-ink-muted">
                            {formatNumber(peer.robust_z_score, 2)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      ) : null}

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Phân tách DuPont 5 nhân tố (ROE = Gánh nặng thuế × Gánh nặng lãi vay × Biên EBIT × Vòng quay TS × Đòn bẩy)
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {profile.dupont_series.length === 0 ? (
            <p className="p-4 text-xs text-ink-muted">
              Chưa có chuỗi phân tách DuPont cho phạm vi này.
            </p>
          ) : (
            <>
              <DuPontTreeChart
                dupontSeries={profile.dupont_series}
                ratiosTable={profile.ratios_table}
                periodType={periodType}
                onSelectMetric={(ratioName, ped) =>
                  onOpenSourceDrawer({
                    ticker: profile.ticker,
                    ratioName,
                    statementScope,
                    periodType,
                    periodEndDate: ped,
                  })
                }
              />
              <p className="mb-1.5 px-3 pt-2 text-[11px] text-ink-subtle md:hidden">
                Vuốt ngang để xem đầy đủ 5 nhân tố DuPont →
              </p>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Gánh nặng thuế</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Gánh nặng lãi vay</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Biên EBIT</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Vòng quay TS</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Hệ số đòn bẩy</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">ROE tổng</th>
                  </tr>
                </thead>
                <tbody>
                  {[...profile.dupont_series]
                    .sort((a, b) =>
                      b.period_end_date.localeCompare(a.period_end_date),
                    )
                    .slice(0, 12)
                    .map((dp) => (
                      <tr
                        key={dp.period_end_date}
                        className="border-b border-border/60 hover:bg-surface-hover/40"
                      >
                        <td className="px-3 py-2 font-medium text-ink">
                          {formatPeriodLabel(dp.period_end_date, periodType)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(dp.tax_burden)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(dp.interest_burden)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(dp.ebit_margin)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatMultiple(dp.asset_turnover)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatMultiple(dp.equity_multiplier_total)}
                        </td>
                        <td className="num px-3 py-2 text-right font-semibold text-ink">
                          {formatPercent(dp.roe_total)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
