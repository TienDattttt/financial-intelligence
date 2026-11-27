"use client";

import * as React from "react";
import Link from "next/link";
import { Bookmark, GitCompare } from "lucide-react";
import {
  ExportActionMenu,
  buildCompanyXlsxExportUrl,
} from "@/components/export/ExportActionMenu";
import { RiskTierBadge } from "@/components/financial/RiskBadge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import { formatPeriodLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PeriodType, StatementScope } from "@/types/api";

export type CompanyTab =
  | "overview"
  | "statements"
  | "ratios"
  | "quality"
  | "model"
  | "documents"
  | "lineage";

export const COMPANY_TABS: readonly { id: CompanyTab; label: string }[] = [
  { id: "overview", label: "Tổng quan 1 trang" },
  { id: "statements", label: "Báo cáo tài chính" },
  { id: "ratios", label: "Tỷ số & Ngành" },
  { id: "quality", label: "Chất lượng LN & Cờ đỏ" },
  { id: "model", label: "Mô hình & SHAP" },
  { id: "documents", label: "Điều tra Thuyết minh (PDF)" },
  { id: "lineage", label: "Nguồn dữ liệu" },
];

export function CompanyHeader({
  ticker,
  companyName,
  exchange,
  sectorLevel2,
  sectorLevel3,
  latestTaxonomyVersion,
  riskTier,
  statementScope,
  periodType,
  activePeriodEnd,
  isWatched,
  onToggleWatchlist,
  activeTab,
  onChangeTab,
  ragAvailable = true,
}: {
  ticker: string;
  companyName: string;
  exchange?: string | null;
  sectorLevel2?: string | null;
  sectorLevel3?: string | null;
  latestTaxonomyVersion?: string | null;
  riskTier?: string | null;
  statementScope: StatementScope;
  periodType: PeriodType;
  activePeriodEnd?: string | null;
  isWatched: boolean;
  onToggleWatchlist: () => void;
  activeTab: CompanyTab;
  onChangeTab: (tab: CompanyTab) => void;
  ragAvailable?: boolean;
}) {
  const metaLine = [
    sectorLevel2 ?? "Chưa phân loại",
    sectorLevel3 ? `/ ${sectorLevel3}` : null,
    `· ${STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope}`,
    `· ${PERIOD_TYPE_LABELS[periodType] ?? periodType}`,
    activePeriodEnd ? `· ${formatPeriodLabel(activePeriodEnd, periodType)}` : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="md:sticky md:top-[52px] lg:top-[89px] md:z-20 overflow-hidden rounded-lg border border-border bg-surface">
      {/* Compact Single-Row Sticky Ticker Strip (~52px on desktop) */}
      <div className="flex flex-col items-stretch justify-between gap-2 px-3.5 py-2 sm:flex-row sm:flex-wrap sm:items-center lg:flex-nowrap">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 lg:flex-nowrap">
          <span className="shrink-0 rounded-sm bg-primary px-2 py-0.5 font-mono text-sm font-semibold text-primary-foreground">
            {ticker}
          </span>
          <h1
            className="min-w-0 flex-1 text-[15px] font-semibold leading-6 text-ink sm:flex-initial sm:max-w-[260px] sm:truncate xl:max-w-[320px]"
            title={companyName}
          >
            {companyName}
          </h1>
          {exchange ? (
            <Badge className="shrink-0 border border-border bg-surface-muted text-ink">
              {exchange}
            </Badge>
          ) : null}
          {riskTier ? <RiskTierBadge riskTier={riskTier} compact className="shrink-0" /> : null}
          {latestTaxonomyVersion ? (
            <Badge className="hidden shrink-0 border border-border bg-canvas text-ink-muted sm:inline-flex">
              {TAXONOMY_LABELS[latestTaxonomyVersion] ?? latestTaxonomyVersion}
            </Badge>
          ) : null}
          <span
            className="hidden min-w-0 truncate text-xs text-ink-muted xl:inline"
            title={metaLine}
          >
            · {metaLine}
          </span>
        </div>

        <div className="no-print flex shrink-0 flex-wrap items-center gap-1.5">
          <Button
            type="button"
            variant={isWatched ? "default" : "outline"}
            size="xs"
            onClick={onToggleWatchlist}
          >
            <Bookmark className={cn("size-3.5", isWatched && "fill-current")} />
            <span>{isWatched ? "Đang theo dõi" : "Theo dõi"}</span>
          </Button>
          <Link
            href={`/compare?tickers=${ticker}`}
            className={buttonVariants({ variant: "outline", size: "xs" })}
          >
            <GitCompare className="size-3.5" />
            <span>So sánh</span>
          </Link>
          <ExportActionMenu
            xlsxUrl={buildCompanyXlsxExportUrl(ticker, {
              statementScope,
              periodType,
              periodsLimit: 20,
            })}
            xlsxFallbackFilename={`finintel-${ticker}.xlsx`}
            tearSheetHref={`/companies/${ticker}/tear-sheet`}
            onPrintReport={() => window.print()}
          />
        </div>
      </div>

      {/* WAI-ARIA Underline Tab Bar */}
      <div className="no-print border-t border-border bg-canvas/40 px-2">
        <SegmentedControl
          ariaLabel="Các mục hồ sơ doanh nghiệp"
          variant="underline"
          value={activeTab}
          onChange={onChangeTab}
          idPrefix="company"
          controlsPanelId={(t) => `company-panel-${t}`}
          className="border-b-0"
          options={COMPANY_TABS.map((t) => ({
            value: t.id,
            label: t.label,
            disabled: t.id === "documents" && !ragAvailable,
            title:
              t.id === "documents" && !ragAvailable
                ? "Dịch vụ tra cứu Thuyết minh PDF hiện chưa bật"
                : undefined,
          }))}
        />
      </div>
    </div>
  );
}
