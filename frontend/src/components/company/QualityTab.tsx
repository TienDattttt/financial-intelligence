"use client";

import * as React from "react";
import { RedFlagChecklist } from "@/components/financial/RedFlagChecklist";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import {
  AUDIT_OPINION_LABELS,
  AUDIT_STATUS_LABELS,
  REVISION_REASON_LABELS,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatPercent,
  formatPeriodLabel,
  formatVndBillions,
} from "@/lib/format";
import type {
  CompanyProfileResponse,
  CompanyFilingItem,
  PeriodType,
  RedFlagDictionaryItem,
} from "@/types/api";

const STATEMENT_TYPE_LABELS: Record<string, string> = {
  BS: "CĐKT",
  BALANCE_SHEET: "CĐKT",
  IS: "KQKD",
  INCOME_STATEMENT: "KQKD",
  CF: "LCTT",
  CASH_FLOW: "LCTT",
  NOTES: "Thuyết minh",
};

export function QualityTab({
  profile,
  periodType,
  redFlagDict,
  activePeriodEnd,
  onInvestigateRedFlag,
  filings,
  filingsLoading,
}: {
  profile: CompanyProfileResponse;
  periodType: PeriodType;
  redFlagDict: readonly RedFlagDictionaryItem[];
  activePeriodEnd: string | null;
  onInvestigateRedFlag: (flagCode: string, presetQuestion: string) => void;
  filings: readonly CompanyFilingItem[];
  filingsLoading: boolean;
}) {
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="flex items-center gap-1.5 text-[14px]">
            <span>Lợi nhuận hoạt động cốt lõi (Core Earnings)</span>
            <InfoTooltip
              text="Tách phần lợi nhuận bền vững từ hoạt động kinh doanh chính, loại trừ các khoản mục bất thường và phi cốt lõi (theo phương pháp Rouen, So & Wang 2021)."
              label="Giải thích Lợi nhuận hoạt động cốt lõi"
            />
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {profile.core_earnings_series.length === 0 ? (
            <p className="p-4 text-xs text-ink-muted">
              Chưa có chuỗi lợi nhuận cốt lõi cho phạm vi/kỳ này.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="px-3 py-2 text-left font-semibold">Kỳ</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">LN HĐ cốt lõi TTM (tỷ)</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Biên LN cốt lõi</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">LN cốt lõi / EBT</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Biến dạng phi lõi / Tổng TS</th>
                  </tr>
                </thead>
                <tbody>
                  {[...profile.core_earnings_series]
                    .sort((a, b) =>
                      b.period_end_date.localeCompare(a.period_end_date),
                    )
                    .slice(0, 10)
                    .map((ce) => (
                      <tr
                        key={ce.period_end_date}
                        className="border-b border-border/60 hover:bg-surface-hover/40"
                      >
                        <td className="px-3 py-2 font-medium text-ink">
                          {formatPeriodLabel(ce.period_end_date, periodType)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatVndBillions(ce.core_operating_profit_ttm, {
                            accountingNegative: true,
                          })}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(ce.core_operating_margin)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(ce.core_to_ebt_ratio)}
                        </td>
                        <td className="num px-3 py-2 text-right">
                          {formatPercent(ce.non_core_distortion_to_assets)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <RedFlagChecklist
        dictionary={redFlagDict}
        triggeredFlags={profile.red_flags}
        latestPeriodEndDate={activePeriodEnd}
        onInvestigateRedFlag={onInvestigateRedFlag}
      />

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Lịch sử hồ sơ công bố &amp; ý kiến kiểm toán ({filings.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {filingsLoading ? (
            <p className="p-4 text-xs text-ink-muted">Đang tải lịch sử hồ sơ...</p>
          ) : filings.length === 0 ? (
            <p className="p-4 text-xs text-ink-muted">Chưa có hồ sơ ghi nhận.</p>
          ) : (
            <div
              tabIndex={0}
              role="region"
              aria-label="Bảng lịch sử hồ sơ công bố và ý kiến kiểm toán"
              className="max-h-[360px] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Kỳ</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Phạm vi</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Loại BCTC</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Chuẩn</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Kiểm toán</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Ý kiến</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">Phiên bản</th>
                    <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">Công bố</th>
                  </tr>
                </thead>
                <tbody>
                  {filings.map((f) => (
                    <tr
                      key={`${f.filing_id}-${f.statement_type ?? ""}`}
                      className="border-b border-border/60 hover:bg-surface-hover/40"
                    >
                      <td className="px-3 py-2 font-medium text-ink">
                        {f.period_end_date ? formatDateVi(f.period_end_date) : "—"}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {(f.statement_scope &&
                          STATEMENT_SCOPE_LABELS[f.statement_scope]) ??
                          f.statement_scope ??
                          "—"}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {(f.statement_type &&
                          STATEMENT_TYPE_LABELS[f.statement_type.toUpperCase()]) ??
                          "—"}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {(f.taxonomy_version &&
                          TAXONOMY_LABELS[f.taxonomy_version]) ??
                          f.taxonomy_version ??
                          "—"}
                      </td>
                      <td className="px-3 py-2">
                        {(f.audit_status &&
                          AUDIT_STATUS_LABELS[f.audit_status]) ??
                          f.audit_status ??
                          "—"}
                      </td>
                      <td className="px-3 py-2">
                        {(f.audit_opinion &&
                          AUDIT_OPINION_LABELS[f.audit_opinion]) ??
                          f.audit_opinion ??
                          "—"}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {(f.revision_reason &&
                          REVISION_REASON_LABELS[f.revision_reason]) ??
                          f.revision_reason ??
                          "—"}
                        {f.has_restatement ? " · Hồi tố" : ""}
                      </td>
                      <td className="num px-3 py-2 text-right text-ink-muted">
                        {f.published_at ? formatDateVi(f.published_at) : "—"}
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
  );
}
