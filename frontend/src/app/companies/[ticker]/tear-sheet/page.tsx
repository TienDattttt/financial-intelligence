"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, FileSpreadsheet, Printer } from "lucide-react";
import { resolveCrossFilteredMlScore } from "@/components/charts/ExplainWaterfallChart";
import { formatLatestValuationCards } from "@/components/charts/PriceRiskOverlayChart";
import { buildCompanyXlsxExportUrl } from "@/components/export/ExportActionMenu";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  useAllIssuers,
  useCompanyBenchmarksSummary,
  useCompanyFilings,
  useCompanyMarketOverlay,
  useCompanyProfile,
  useMetaDictionary,
} from "@/hooks/use-finintel-queries";
import {
  COMPLIANCE_DISCLAIMER_VI,
  COMPLIANCE_LAW_REFERENCES_VI,
  RISK_TIER_LEGAL_LABEL_VI,
} from "@/lib/compliance";
import {
  ALTMAN_ZONE_META,
  AUDIT_OPINION_LABELS,
  AUDIT_STATUS_LABELS,
  BENEISH_FLAG_META,
  PERIOD_TYPE_LABELS,
  SEVERITY_META,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
  formatCreditTrio,
  formatPeerGroupLabel,
  formatRedFlagCodeLabel,
} from "@/lib/domain-labels";
import {
  MINUS_SIGN,
  formatDateVi,
  formatNumber,
  formatPeriodLabel,
  formatRatioByType,
  formatRatioValue,
} from "@/lib/format";
import { buildShapNarrativeVi } from "@/lib/narrative";
import { cn } from "@/lib/utils";
import type { RatioDictionaryItem, RatioName } from "@/types/api";

function formatShapDelta(delta: number): string {
  const abs = formatNumber(Math.abs(delta), 2);
  if (Math.abs(delta) < 0.005) return "0,00";
  return delta >= 0 ? `+${abs}` : `${MINUS_SIGN}${abs}`;
}

export default function CompanyTearSheetPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = React.useMemo(
    () => decodeURIComponent(params.ticker ?? "").trim().toUpperCase(),
    [params.ticker],
  );

  const {
    statementScope,
    periodType,
    periodEndDate,
    queryPeriodEndDate = null,
    snapshotId,
    cacheSnapshotKey,
    publishedAt,
  } = useAppState();
  const querySnapshotKey = cacheSnapshotKey ?? snapshotId;
  const periodView = periodType === "ANNUAL" ? "Annual" : "TTM";

  const profileQuery = useCompanyProfile(
    ticker,
    statementScope,
    periodView,
    querySnapshotKey,
  );
  const summaryQuery = useCompanyBenchmarksSummary(
    ticker,
    {
      statementScope,
      periodType,
      periodEndDate: queryPeriodEndDate,
    },
    querySnapshotKey,
  );
  const filingsQuery = useCompanyFilings(
    ticker,
    { statementScope },
    querySnapshotKey,
  );
  const overlayQuery = useCompanyMarketOverlay(
    ticker,
    statementScope,
    querySnapshotKey,
  );
  const issuersQuery = useAllIssuers(querySnapshotKey);
  const dictQuery = useMetaDictionary(querySnapshotKey);

  const issuerMeta = React.useMemo(
    () =>
      (issuersQuery.data ?? []).find(
        (iss) => iss.ticker.toUpperCase() === ticker,
      ) ?? null,
    [issuersQuery.data, ticker],
  );

  const ratioDictMap = React.useMemo(() => {
    const map = new Map<RatioName, RatioDictionaryItem>();
    for (const r of dictQuery.data?.ratios ?? []) {
      map.set(r.ratio_name, r);
    }
    return map;
  }, [dictQuery.data?.ratios]);

  if (!ticker) {
    return (
      <EmptyState
        title="Thiếu mã chứng khoán"
        description="Vui lòng chọn doanh nghiệp từ trang Sàng lọc hoặc tìm kiếm."
      />
    );
  }

  if (profileQuery.isLoading) {
    return <PageSkeleton />;
  }

  if (profileQuery.isError) {
    return (
      <ErrorState
        message={profileQuery.error.message}
        onRetry={() => profileQuery.refetch()}
      />
    );
  }

  const profile = profileQuery.data;
  if (!profile) {
    return (
      <EmptyState
        title={`Không có dữ liệu hồ sơ cho ${ticker}`}
        description="Kiểm tra lại mã chứng khoán hoặc kỳ báo cáo đang chọn."
      />
    );
  }

  const sortedMl = [...profile.ml_scores].sort((a, b) =>
    a.period_end_date.localeCompare(b.period_end_date),
  );
  const { active: activeMl, defaultLatest: latestMl } =
    resolveCrossFilteredMlScore(sortedMl, null, periodEndDate);

  const creditTrio = formatCreditTrio({
    risk_tier: latestMl?.risk_tier,
    risk_market_percentile: latestMl?.risk_market_percentile,
    calibrated_distress_prob: latestMl?.calibrated_distress_prob,
    relative_risk_lift: latestMl?.relative_risk_lift,
    market_base_rate: latestMl?.market_base_rate,
  });
  const altmanMeta =
    (latestMl?.altman_zone && ALTMAN_ZONE_META[latestMl.altman_zone]) || null;
  const beneishMeta =
    (latestMl?.beneish_flag && BENEISH_FLAG_META[latestMl.beneish_flag]) || null;

  const activePeriodEnd =
    latestMl?.period_end_date ??
    summaryQuery.data?.period_end_date ??
    periodEndDate ??
    null;
  const activePeriodLabel = formatPeriodLabel(activePeriodEnd, periodType);

  const valuationCards = formatLatestValuationCards(
    overlayQuery.data?.latest_valuation,
  );
  const benchmarkItems = (summaryQuery.data?.items ?? []).slice(0, 12);

  const shapData = activeMl?.shap_top_drivers ?? null;
  const shapNarrative = buildShapNarrativeVi(shapData);
  const shapDrivers = [
    ...(shapData?.top_risk_increasing ?? []).slice(0, 3).map((d) => ({
      ...d,
      dirLabel: "Tăng rủi ro",
      tone: "high" as const,
    })),
    ...(shapData?.top_risk_decreasing ?? []).slice(0, 3).map((d) => ({
      ...d,
      dirLabel: "Giảm rủi ro",
      tone: "low" as const,
    })),
  ];

  const activeRedFlags = activePeriodEnd
    ? profile.red_flags.filter((rf) => rf.period_end_date === activePeriodEnd)
    : profile.red_flags.slice(0, 6);
  const latestFiling = filingsQuery.data?.items?.[0] ?? null;
  const resolvedSnapId = profile.snapshot_id ?? snapshotId ?? "N/A";
  const xlsxUrl = buildCompanyXlsxExportUrl(ticker, {
    statementScope,
    periodType,
    periodsLimit: 20,
  });

  return (
    <div className="space-y-4">
      {/* Screen-only Action Bar */}
      <div className="no-print flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3.5 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/companies/${ticker}`}
            className={buttonVariants({ variant: "outline", size: "xs" })}
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            <span>Quay lại hồ sơ {ticker}</span>
          </Link>
          <span className="text-xs text-ink-muted">
            Hồ sơ định lượng chuẩn A4 (1–2 trang · Tự động chuyển nền Sáng khi in PDF)
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={xlsxUrl}
            download
            className={buttonVariants({ variant: "outline", size: "xs" })}
          >
            <FileSpreadsheet className="size-3.5" aria-hidden="true" />
            <span>Tải Excel (.xlsx)</span>
          </a>
          <Button
            type="button"
            variant="default"
            size="xs"
            onClick={() => window.print()}
          >
            <Printer className="size-3.5" aria-hidden="true" />
            <span>Tải PDF / In hồ sơ</span>
          </Button>
        </div>
      </div>

      {/* Printable A4 Tear-Sheet Sheet */}
      <article
        aria-label={`Hồ sơ Tear-Sheet ${ticker}`}
        className="tear-sheet-page mx-auto max-w-[960px] space-y-4 rounded-lg border border-border bg-surface p-5 text-ink print:max-w-none print:rounded-none print:border-0 print:p-0"
      >
        {/* 1. Institutional Masthead & Executive Risk Conclusion (R1–R5) */}
        <header className="border-b border-border pb-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                FinIntel · Hồ sơ Phân tích Định lượng Doanh nghiệp Phi tài chính
              </p>
              <h1 className="mt-1 flex flex-wrap items-center gap-2 text-lg font-semibold text-ink">
                <span className="rounded-sm bg-primary px-2 py-0.5 font-mono text-sm text-primary-foreground">
                  {profile.ticker}
                </span>
                <span>{profile.company_name_vn ?? profile.ticker}</span>
                {issuerMeta?.exchange ? (
                  <span className="rounded-sm border border-border bg-surface-muted px-1.5 py-0.5 font-mono text-xs text-ink-muted">
                    {issuerMeta.exchange}
                  </span>
                ) : null}
              </h1>
              <p className="mt-1 text-xs text-ink-muted">
                Ngành:{" "}
                {[issuerMeta?.icb_name_level2, issuerMeta?.icb_name_level3]
                  .filter(Boolean)
                  .join(" / ") || "Chưa phân loại"}{" "}
                · {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
                {PERIOD_TYPE_LABELS[periodType] ?? periodType} · Kỳ{" "}
                <strong className="text-ink">{activePeriodLabel}</strong>
                {profile.latest_taxonomy_version
                  ? ` · ${TAXONOMY_LABELS[profile.latest_taxonomy_version] ?? profile.latest_taxonomy_version}`
                  : ""}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1 text-right">
              <RiskTierBadge riskTier={latestMl?.risk_tier} />
              <span className="text-[11px] text-ink-muted">
                {RISK_TIER_LEGAL_LABEL_VI}
              </span>
            </div>
          </div>

          <section
            aria-label="Kết luận rủi ro định lượng"
            className="mt-3 grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-md border border-border sm:grid-cols-4 sm:divide-y-0"
          >
            <div className="p-2.5">
              <p className="text-[11px] font-medium text-ink-muted">
                Xác suất kiệt quệ 1 năm
              </p>
              <p className="num mt-0.5 text-lg font-semibold text-ink">
                {creditTrio.pdLabel}
              </p>
              <p className="text-[11px] text-ink-muted">
                {creditTrio.topPercentLabel}
                {creditTrio.liftLabel ? ` · ${creditTrio.liftLabel}` : ""}
              </p>
            </div>
            <div className="p-2.5">
              <p className="text-[11px] font-medium text-ink-muted">
                Altman Z″ (EM)
              </p>
              <p className="num mt-0.5 text-lg font-semibold text-ink">
                {formatNumber(latestMl?.altman_z_double_prime, 2)}
              </p>
              <p className="text-[11px] text-ink-muted">
                {altmanMeta?.label ?? "Chưa phân vùng"}
              </p>
            </div>
            <div className="p-2.5">
              <p className="text-[11px] font-medium text-ink-muted">
                Piotroski F-Score
              </p>
              <p className="num mt-0.5 text-lg font-semibold text-ink">
                {latestMl?.piotroski_f_score !== null &&
                latestMl?.piotroski_f_score !== undefined
                  ? `${latestMl.piotroski_f_score}/9`
                  : "N/A"}
              </p>
              <p className="text-[11px] text-ink-muted">
                7–9 Tốt · 4–6 TB · 0–3 Yếu
              </p>
            </div>
            <div className="p-2.5">
              <p className="text-[11px] font-medium text-ink-muted">
                Beneish M-Score
              </p>
              <p className="num mt-0.5 text-lg font-semibold text-ink">
                {formatNumber(latestMl?.beneish_m8_score, 2)}
              </p>
              <p className="text-[11px] text-ink-muted">
                {beneishMeta?.shortLabel ?? "Chưa đánh giá"}
              </p>
            </div>
          </section>
        </header>

        {/* 2. PIT Valuation & EOD Market Signals */}
        <section aria-label="Định giá Point-in-Time và tín hiệu thị trường" className="break-inside-avoid">
          <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-accent-ink">
            1. Định giá Point-in-Time &amp; Khoảng cách vỡ nợ (EOD {valuationCards.tradeDateLabel})
          </h2>
          <div className="grid grid-cols-2 divide-x divide-y divide-border rounded-md border border-border sm:grid-cols-5 sm:divide-y-0">
            <div className="p-2">
              <p className="text-[11px] text-ink-muted">Giá đóng cửa thực tế</p>
              <p className="num text-sm font-semibold text-ink">{valuationCards.closeRawLabel}</p>
            </div>
            <div className="p-2">
              <p className="text-[11px] text-ink-muted">Vốn hóa PIT</p>
              <p className="num text-sm font-semibold text-ink">{valuationCards.marketCapLabel}</p>
            </div>
            <div className="p-2">
              <p className="text-[11px] text-ink-muted">P/E (TTM)</p>
              <p className="num text-sm font-semibold text-ink">{valuationCards.peLabel}</p>
            </div>
            <div className="p-2">
              <p className="text-[11px] text-ink-muted">P/B</p>
              <p className="num text-sm font-semibold text-ink">{valuationCards.pbLabel}</p>
            </div>
            <div className="p-2">
              <p className="text-[11px] text-ink-muted">Merton DTD</p>
              <p className="num text-sm font-semibold text-ink">{valuationCards.dtdLabel}</p>
            </div>
          </div>
        </section>

        {/* 3. Key Financial Ratios vs Sector Median */}
        <section aria-label="Bảng chỉ số chính so với trung vị ngành" className="break-inside-avoid">
          <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-accent-ink">
            2. Chỉ số tài chính trọng yếu vs Trung vị ngành
          </h2>
          {benchmarkItems.length === 0 ? (
            <p className="rounded-md border border-border px-3 py-2 text-xs text-ink-muted">
              Chưa có dữ liệu so sánh trung vị ngành cho kỳ báo cáo này.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted text-ink-muted">
                    <th scope="col" className="px-2.5 py-1.5 text-left font-semibold">
                      Chỉ tiêu
                    </th>
                    <th scope="col" className="px-2 py-1.5 text-right font-semibold">
                      Doanh nghiệp
                    </th>
                    <th scope="col" className="px-2 py-1.5 text-right font-semibold">
                      Trung vị ngành
                    </th>
                    <th scope="col" className="px-2 py-1.5 text-right font-semibold">
                      Khoảng P25–P75
                    </th>
                    <th scope="col" className="px-2 py-1.5 text-right font-semibold">
                      Phân vị
                    </th>
                    <th scope="col" className="px-2.5 py-1.5 text-left font-semibold">
                      Nhóm so sánh
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/70">
                  {benchmarkItems.map((item) => (
                    <tr key={item.metric_name}>
                      <td className="px-2.5 py-1.5 font-medium text-ink">
                        {item.display_name_vn}
                      </td>
                      <td className="num px-2 py-1.5 text-right font-semibold text-ink">
                        {formatRatioValue(item.company_value, item.format_type)}
                      </td>
                      <td className="num px-2 py-1.5 text-right text-ink-muted">
                        {formatRatioValue(item.peer_median, item.format_type)}
                      </td>
                      <td className="num px-2 py-1.5 text-right text-ink-muted">
                        {formatRatioValue(item.peer_p25, item.format_type)} –{" "}
                        {formatRatioValue(item.peer_p75, item.format_type)}
                      </td>
                      <td className="num px-2 py-1.5 text-right text-ink">
                        {item.percentile_rank !== null && item.percentile_rank !== undefined
                          ? `${formatNumber(item.percentile_rank, 0)}%`
                          : "N/A"}
                      </td>
                      <td className="px-2.5 py-1.5 text-ink-muted">
                        {formatPeerGroupLabel(item.peer_group_level, item.peer_count ?? 0).text}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 4 & 5. TreeSHAP Drivers & Accounting Red Flags */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <section aria-label="Yếu tố tác động TreeSHAP" className="break-inside-avoid">
            <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-accent-ink">
              3. Yếu tố định lượng tác động chính (TreeSHAP)
            </h2>
            {shapDrivers.length === 0 ? (
              <p className="rounded-md border border-border px-3 py-2 text-xs text-ink-muted">
                Chưa có dữ liệu giải thích TreeSHAP cho kỳ này.
              </p>
            ) : (
              <div className="space-y-2 rounded-md border border-border p-2.5 text-xs">
                {shapNarrative.length > 0 ? (
                  <ul className="list-disc space-y-1 pl-4 text-[11px] leading-4 text-ink-muted">
                    {shapNarrative.slice(0, 3).map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                ) : null}
                <table className="w-full border-collapse text-[11px]">
                  <thead>
                    <tr className="border-b border-border text-ink-muted">
                      <th scope="col" className="py-1 text-left font-semibold">
                        Biến số
                      </th>
                      <th scope="col" className="py-1 text-right font-semibold">
                        Giá trị
                      </th>
                      <th scope="col" className="py-1 text-right font-semibold">
                        Tác động
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {shapDrivers.map((drv) => {
                      const fmt = ratioDictMap.get(drv.feature as RatioName)?.format_type;
                      return (
                        <tr key={`${drv.dirLabel}-${drv.feature}`}>
                          <td className="py-1 pr-2">
                            <span className="font-medium text-ink">
                              {drv.display_name_vn}
                            </span>{" "}
                            <span
                              className={cn(
                                "text-[10px]",
                                drv.tone === "high" ? "text-risk-high" : "text-risk-low",
                              )}
                            >
                              ({drv.dirLabel})
                            </span>
                          </td>
                          <td className="num py-1 text-right text-ink-muted">
                            {formatRatioByType(drv.value, fmt)}
                          </td>
                          <td
                            className={cn(
                              "num py-1 pl-2 text-right font-semibold",
                              drv.tone === "high" ? "text-risk-high" : "text-risk-low",
                            )}
                          >
                            {formatShapDelta(drv.shap_delta)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-label="Cờ đỏ kế toán" className="break-inside-avoid">
            <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-accent-ink">
              4. Cờ đỏ kế toán đang ghi nhận ({activeRedFlags.length}/10)
            </h2>
            {activeRedFlags.length === 0 ? (
              <p className="rounded-md border border-risk-low/30 bg-risk-low-soft/40 px-3 py-2 text-xs text-risk-low">
                Không ghi nhận cờ đỏ bất thường kế toán trong kỳ này.
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-md border border-border">
                {activeRedFlags.slice(0, 6).map((rf) => {
                  const sev = SEVERITY_META[rf.severity] ?? SEVERITY_META.MEDIUM;
                  return (
                    <li
                      key={`${rf.flag_code}-${rf.period_end_date}`}
                      className="p-2 text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-ink">
                          {formatRedFlagCodeLabel(rf.flag_code)} ·{" "}
                          {rf.flag_title_vn ?? "Dấu hiệu bất thường kế toán"}
                        </span>
                        <RiskBadge tone={sev.tone} label={sev.shortLabel} />
                      </div>
                      {rf.explanation_text_vn ? (
                        <p className="mt-0.5 text-[11px] leading-4 text-ink-muted">
                          {rf.explanation_text_vn}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        {/* 6. Lineage & Statutory Legal Disclaimer */}
        <footer className="break-inside-avoid border-t border-border pt-3 text-[11px] leading-4 text-ink-muted">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              Truy vết dữ liệu: Mã phát hành{" "}
              <strong className="font-mono text-ink">{resolvedSnapId}</strong>
              {publishedAt ? ` · Cập nhật ${formatDateVi(publishedAt)}` : ""}
              {latestFiling?.audit_status
                ? ` · ${AUDIT_STATUS_LABELS[latestFiling.audit_status] ?? latestFiling.audit_status}`
                : ""}
              {latestFiling?.audit_opinion
                ? ` (${AUDIT_OPINION_LABELS[latestFiling.audit_opinion] ?? latestFiling.audit_opinion})`
                : ""}
            </span>
            <span>{COMPLIANCE_LAW_REFERENCES_VI.join(" · ")}</span>
          </div>
          <p className="mt-1.5 text-ink-subtle">{COMPLIANCE_DISCLAIMER_VI}</p>
        </footer>
      </article>
    </div>
  );
}
