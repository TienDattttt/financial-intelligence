"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  QUALITY_RULE_LABELS,
  QUALITY_STATUS_META,
  SEVERITY_META,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import { formatDateVi, formatNumber, formatPeriodLabel } from "@/lib/format";
import type {
  AuditQuarantinePageResponse,
  QualitySummaryPageResponse,
  SnapshotOverviewResponse,
} from "@/types/api";

export function MethodologyQualityTab({
  overviewQuery,
  summaryQuery,
  quarantineQuery,
  issuerTickerMap,
  qOffset,
  setQOffset,
  quarOffset,
  setQuarOffset,
  scrollRegionClassName,
}: {
  overviewQuery: UseQueryResult<SnapshotOverviewResponse>;
  summaryQuery: UseQueryResult<QualitySummaryPageResponse>;
  quarantineQuery: UseQueryResult<AuditQuarantinePageResponse>;
  issuerTickerMap: Map<string, string>;
  qOffset: number;
  setQOffset: (next: number) => void;
  quarOffset: number;
  setQuarOffset: (next: number) => void;
  scrollRegionClassName: string;
}) {
  if (overviewQuery.isLoading) {
    return <PageSkeleton />;
  }
  if (overviewQuery.isError) {
    return (
      <ErrorState
        message={overviewQuery.error.message}
        onRetry={() => overviewQuery.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      {overviewQuery.data ? (
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-5">
          {[
            ["Tổng số DN", formatNumber(overviewQuery.data.total_issuers, 0)],
            ["Tổng số kỳ tỷ số", formatNumber(overviewQuery.data.total_ratio_periods, 0)],
            ["Kỳ đạt chuẩn", formatNumber(overviewQuery.data.pass_periods, 0)],
            ["Kỳ có cảnh báo", formatNumber(overviewQuery.data.warning_periods, 0)],
            ["Bản ghi loại trừ kiểm tra", formatNumber(overviewQuery.data.quarantined_rows, 0)],
          ].map(([lbl, val]) => (
            <div key={lbl} className="bg-surface p-3">
              <p className="text-[11px] text-ink-muted">{lbl}</p>
              <p className="num mt-1 text-lg font-semibold text-ink">{val}</p>
            </div>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Nhật ký kiểm tra chất lượng theo kỳ ({formatNumber(summaryQuery.data?.total ?? 0, 0)})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {summaryQuery.isLoading ? (
            <p className="p-4 text-xs text-ink-muted">Đang tải nhật ký chất lượng...</p>
          ) : (summaryQuery.data?.items.length ?? 0) === 0 ? (
            <div className="p-4">
              <EmptyState
                title="Không có bản ghi chi tiết chất lượng theo kỳ"
                description="Bản phát hành dữ liệu hiện tại không kèm bảng nhật ký chi tiết kiểm định."
              />
            </div>
          ) : (
            <>
              <div tabIndex={0} role="region" aria-label="Bảng nhật ký kiểm tra chất lượng theo kỳ" className={scrollRegionClassName}>
                <table className="w-full min-w-[680px] border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Mã DN</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Phạm vi</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Trạng thái</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Lưu ý</th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">Thời điểm kiểm</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaryQuery.data?.items.map((row, idx) => {
                      const ticker = issuerTickerMap.get(row.issuer_id) ?? row.issuer_id;
                      const statusMeta = QUALITY_STATUS_META[row.quality_status] ?? {
                        label: "Có cảnh báo",
                        tone: "mid" as const,
                      };
                      const displayCodes = [
                        ...row.warning_codes,
                        ...(row.info_codes ?? []).filter((c) => c in QUALITY_RULE_LABELS),
                      ];
                      const warningText =
                        displayCodes.length > 0
                          ? displayCodes
                              .map((c) => QUALITY_RULE_LABELS[c] ?? "Lưu ý kiểm định")
                              .join(" · ")
                          : "—";
                      return (
                        <tr
                          key={`${row.issuer_id}-${row.period_end_date}-${idx}`}
                          className="border-b border-border/60"
                        >
                          <td className="px-3 py-2 font-mono font-semibold text-ink">
                            {ticker}
                          </td>
                          <td className="px-3 py-2">
                            {formatPeriodLabel(row.period_end_date, row.period_type)}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {STATEMENT_SCOPE_LABELS[row.statement_scope] ??
                              "Báo cáo tài chính"}
                          </td>
                          <td className="px-3 py-2">
                            <RiskBadge tone={statusMeta.tone} label={statusMeta.label} />
                          </td>
                          <td className="px-3 py-2 text-ink-muted">{warningText}</td>
                          <td className="num px-3 py-2 text-right text-ink-muted">
                            {formatDateVi(row.checked_at)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-ink-muted">
                <span>
                  Hiển thị {qOffset + 1}–{Math.min(summaryQuery.data?.total ?? 0, qOffset + 25)}
                </span>
                <div className="flex gap-1.5">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={qOffset === 0}
                    onClick={() => setQOffset(Math.max(0, qOffset - 25))}
                  >
                    Trước
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={qOffset + 25 >= (summaryQuery.data?.total ?? 0)}
                    onClick={() => setQOffset(qOffset + 25)}
                  >
                    Sau
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Danh sách hồ sơ bị giữ lại kiểm định ({formatNumber(quarantineQuery.data?.total ?? 0, 0)})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {quarantineQuery.isLoading ? (
            <p className="p-4 text-xs text-ink-muted">Đang tải danh sách ngoại lệ...</p>
          ) : (quarantineQuery.data?.items.length ?? 0) === 0 ? (
            <div className="p-4">
              <EmptyState
                title="Không có hồ sơ ngoại lệ cần kiểm định"
                description="Toàn bộ hồ sơ trong bản phát hành dữ liệu hiện tại đều vượt qua kiểm tra kế toán đầu vào."
              />
            </div>
          ) : (
            <>
              <div tabIndex={0} role="region" aria-label="Bảng danh sách hồ sơ bị giữ lại kiểm định" className={scrollRegionClassName}>
                <table className="w-full min-w-[720px] border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Mã DN</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Hồ sơ</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Quy tắc</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Mức độ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {quarantineQuery.data?.items.map((row, idx) => {
                      const ticker = issuerTickerMap.get(row.issuer_id) ?? row.issuer_id;
                      const sevMeta = SEVERITY_META[row.severity] ?? SEVERITY_META.CRITICAL;
                      return (
                        <tr
                          key={`${row.filing_id}-${idx}`}
                          className="border-b border-border/60"
                        >
                          <td className="px-3 py-2 font-mono font-semibold text-ink">
                            {ticker}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            BCTC · {ticker} · {formatPeriodLabel(row.period_end_date, row.period_type)}
                          </td>
                          <td className="px-3 py-2">
                            {formatPeriodLabel(row.period_end_date, row.period_type)}
                          </td>
                          <td className="px-3 py-2 text-ink">
                            {QUALITY_RULE_LABELS[row.rule_code] ??
                              "Vi phạm quy tắc kiểm định đầu vào"}
                          </td>
                          <td className="px-3 py-2">
                            <RiskBadge tone={sevMeta.tone} label={sevMeta.label} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-ink-muted">
                <span>
                  Hiển thị {quarOffset + 1}–{Math.min(quarantineQuery.data?.total ?? 0, quarOffset + 25)}
                </span>
                <div className="flex gap-1.5">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={quarOffset === 0}
                    onClick={() => setQuarOffset(Math.max(0, quarOffset - 25))}
                  >
                    Trước
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={quarOffset + 25 >= (quarantineQuery.data?.total ?? 0)}
                    onClick={() => setQuarOffset(quarOffset + 25)}
                  >
                    Sau
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
