"use client";

import * as React from "react";
import {
  CheckCircle2,
  FileSearch,
  Link2,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";
import { CapitalStructureChart } from "@/components/charts/CapitalStructureChart";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { ExplainWaterfallChart } from "@/components/charts/ExplainWaterfallChart";
import { GrowthComboChart } from "@/components/charts/GrowthComboChart";
import {
  PitValuationStrip,
  PriceRiskOverlayChart,
} from "@/components/charts/PriceRiskOverlayChart";
import { BondMaturityCard } from "@/components/company/BondMaturityCard";
import { PercentileBar } from "@/components/financial/PercentileBar";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ScoreDots } from "@/components/financial/ScoreDots";
import { SparklineSvg } from "@/components/financial/SparklineSvg";
import { VerdictCard } from "@/components/financial/VerdictCard";
import { EmptyState, ErrorState } from "@/components/layout/StateBlocks";
import { getRedFlagPresetQuestion } from "@/components/rag/DocumentInvestigationPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  ALTMAN_ZONE_META,
  AUDIT_OPINION_LABELS,
  AUDIT_STATUS_LABELS,
  BENEISH_FLAG_META,
  formatCreditTrio,
  formatPeerGroupLabel,
  REVISION_REASON_LABELS,
  SEVERITY_META,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
  formatRatioValue,
  MINUS_SIGN,
} from "@/lib/format";
import type { CompanyMarketOverlayResponse } from "@/lib/market-overlay-types";
import type {
  CompanyBenchmarksSummaryResponse,
  CompanyBondSignalsResponse,
  CompanyProfileResponse,
  CompanyFilingItem,
  MLScoreItem,
  PeriodType,
  RatioDictionaryItem,
  RatioName,
  RedFlagItem,
  StatementScope,
} from "@/types/api";
import type { CompanyTab } from "@/components/company/CompanyHeader";

interface RangeStats {
  min: number;
  median: number;
  max: number;
}

export function OverviewTab({
  profile,
  sortedMl,
  latestMl,
  prevMl,
  activeMl,
  activeMlPeriodLabel,
  isCustomMlPeriod,
  onResetMlPeriod,
  mlDistressProbOption,
  mlDistressItems,
  onRiskPeriodClick,
  onDistressPeriodClick,
  zStats,
  probStats,
  activeRedFlags,
  latestFiling,
  filingsLoading,
  overlayData,
  overlayLoading,
  overlayError,
  overlayErrorMessage,
  onRetryOverlay,
  growthSeries,
  growthLoading,
  effectiveGrowthView,
  onChangeGrowthLocalView,
  summaryData,
  summaryLoading,
  summaryError,
  summaryErrorMessage,
  onRetrySummary,
  showAll38,
  onChangeShowAll38,
  trafficMode,
  onChangeTrafficMode,
  ratioDictMap,
  statementScope,
  periodType,
  periodEndDate,
  onNavigateTab,
  onInvestigateRedFlag,
  onOpenSourceDrawer,
  bondSignals,
  bondLoading = false,
  bondError = false,
  bondErrorMessage,
  onRetryBond,
}: {
  profile: CompanyProfileResponse;
  sortedMl: readonly MLScoreItem[];
  latestMl: MLScoreItem | null;
  prevMl: MLScoreItem | null;
  activeMl: MLScoreItem | null;
  activeMlPeriodLabel: string | null;
  isCustomMlPeriod: boolean;
  onResetMlPeriod: () => void;
  mlDistressProbOption: EChartsOption;
  mlDistressItems?: readonly MLScoreItem[];
  onRiskPeriodClick: (payload: { dataIndex?: number }) => void;
  onDistressPeriodClick?: (payload: { dataIndex?: number }) => void;
  zStats: RangeStats | null;
  probStats: RangeStats | null;
  activeRedFlags: readonly RedFlagItem[];
  latestFiling: CompanyFilingItem | null;
  filingsLoading: boolean;
  overlayData: CompanyMarketOverlayResponse | undefined;
  overlayLoading: boolean;
  overlayError: boolean;
  overlayErrorMessage?: string;
  onRetryOverlay: () => void;
  growthSeries: CompanyProfileResponse["growth_series"];
  growthLoading: boolean;
  effectiveGrowthView: "TTM" | "Quarterly" | "Annual";
  onChangeGrowthLocalView: (v: "TTM" | "Quarterly") => void;
  summaryData: CompanyBenchmarksSummaryResponse | undefined;
  summaryLoading: boolean;
  summaryError: boolean;
  summaryErrorMessage?: string;
  onRetrySummary: () => void;
  showAll38: boolean;
  onChangeShowAll38: (v: boolean) => void;
  trafficMode: "VALUE" | "PERCENTILE";
  onChangeTrafficMode: (v: "VALUE" | "PERCENTILE") => void;
  ratioDictMap: ReadonlyMap<RatioName, RatioDictionaryItem>;
  statementScope: StatementScope;
  periodType: PeriodType;
  periodEndDate: string | null;
  onNavigateTab: (tab: CompanyTab, extra?: Record<string, string | null>) => void;
  onInvestigateRedFlag: (flagCode: string, presetQuestion: string) => void;
  onOpenSourceDrawer: (req: {
    ticker: string;
    ratioName: RatioName;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate: string | null;
  }) => void;
  bondSignals?: CompanyBondSignalsResponse;
  bondLoading?: boolean;
  bondError?: boolean;
  bondErrorMessage?: string;
  onRetryBond?: () => void;
}) {
  const altmanMeta =
    (latestMl?.altman_zone && ALTMAN_ZONE_META[latestMl.altman_zone]) || {
      label: "Chưa phân vùng",
      shortLabel: "N/A",
      tone: "neutral" as const,
      description: "Chưa đủ dữ liệu tính Altman Z″.",
    };

  const beneishMeta =
    (latestMl?.beneish_flag && BENEISH_FLAG_META[latestMl.beneish_flag]) || {
      label: "Chưa đánh giá",
      shortLabel: "N/A",
      tone: "neutral" as const,
      description: "Chưa có kết quả kiểm tra Beneish M-Score.",
    };

  const creditTrio = formatCreditTrio({
    risk_tier: latestMl?.risk_tier,
    risk_market_percentile: latestMl?.risk_market_percentile,
    calibrated_distress_prob: latestMl?.calibrated_distress_prob,
    relative_risk_lift: latestMl?.relative_risk_lift,
    market_base_rate: latestMl?.market_base_rate,
  });

  const fScore = latestMl?.piotroski_f_score ?? null;
  const fTone =
    fScore === null
      ? "neutral"
      : fScore >= 7
        ? "low"
        : fScore >= 4
          ? "mid"
          : "high";
  const fLabel =
    fScore === null
      ? "N/A"
      : fScore >= 7
        ? "Tốt"
        : fScore >= 4
          ? "Trung bình"
          : "Yếu";

  const zDelta =
    latestMl?.altman_z_double_prime !== null &&
    latestMl?.altman_z_double_prime !== undefined &&
    prevMl?.altman_z_double_prime !== null &&
    prevMl?.altman_z_double_prime !== undefined
      ? latestMl.altman_z_double_prime - prevMl.altman_z_double_prime
      : null;

  const probDelta =
    latestMl?.calibrated_distress_prob !== null &&
    latestMl?.calibrated_distress_prob !== undefined &&
    prevMl?.calibrated_distress_prob !== null &&
    prevMl?.calibrated_distress_prob !== undefined
      ? latestMl.calibrated_distress_prob - prevMl.calibrated_distress_prob
      : null;

  const fDelta =
    latestMl?.piotroski_f_score !== null &&
    latestMl?.piotroski_f_score !== undefined &&
    prevMl?.piotroski_f_score !== null &&
    prevMl?.piotroski_f_score !== undefined
      ? latestMl.piotroski_f_score - prevMl.piotroski_f_score
      : null;

  return (
    <div className="space-y-4">
      {/* Tier 1: Flat 4-Column Executive Verdict Strip */}
      <section
        aria-label="Tổng hợp điểm sức khỏe tài chính"
        className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4"
      >
        <VerdictCard
          flat
          title="Altman Z″ (EM)"
          tooltip={altmanMeta.description}
          value={formatNumber(latestMl?.altman_z_double_prime, 2)}
          tone={altmanMeta.tone}
          badgeLabel={altmanMeta.shortLabel}
          thresholdText="Ngưỡng: < 1,10 Nguy cơ · > 2,60 An toàn"
          deltaText={
            zDelta !== null
              ? `${zDelta >= 0 ? "+" : MINUS_SIGN}${formatNumber(Math.abs(zDelta), 2)} so kỳ trước`
              : null
          }
          deltaTone={
            zDelta === null
              ? "neutral"
              : zDelta > 0.05
                ? "up"
                : zDelta < -0.05
                  ? "down"
                  : "neutral"
          }
          historyRangeText={
            zStats
              ? `10 kỳ: Thấp ${formatNumber(zStats.min, 2)} · TV ${formatNumber(zStats.median, 2)} · Cao ${formatNumber(zStats.max, 2)}`
              : null
          }
        />

        <VerdictCard
          flat
          title="Piotroski F-Score"
          tooltip="Thang điểm 0–9 đánh giá 3 trụ cột: Sinh lời, Đòn bẩy/Thanh khoản và Hiệu quả hoạt động."
          value={fScore !== null ? `${fScore}/9` : "N/A"}
          tone={fTone}
          badgeLabel={fLabel}
          thresholdText="7–9 Tốt · 4–6 Trung bình · 0–3 Yếu"
          deltaText={
            fDelta !== null
              ? `${fDelta >= 0 ? "+" : MINUS_SIGN}${Math.abs(fDelta)} điểm`
              : null
          }
          deltaTone={
            fDelta === null
              ? "neutral"
              : fDelta > 0
                ? "up"
                : fDelta < 0
                  ? "down"
                  : "neutral"
          }
        >
          <ScoreDots
            score={fScore}
            validComponents={latestMl?.piotroski_valid_components_count}
            naReason={latestMl?.piotroski_na_reason}
            missingCriteria={latestMl?.piotroski_missing_criteria}
          />
        </VerdictCard>

        <VerdictCard
          flat
          title="Beneish M-Score"
          tooltip="Mô hình 8 biến phát hiện nguy cơ điều chỉnh lợi nhuận bất thường so với kỳ trước."
          value={formatNumber(latestMl?.beneish_m8_score, 2)}
          tone={beneishMeta.tone}
          badgeLabel={beneishMeta.shortLabel}
          thresholdText="Ngưỡng sàng lọc: ≤ −1,78 Bình thường"
          footerExtra={beneishMeta.description}
        />

        <VerdictCard
          flat
          title="Xác suất kiệt quệ 1 năm"
          tooltip={creditTrio.tierMeta.description}
          value={creditTrio.pdLabel}
          subValueText={creditTrio.liftLabel}
          tone={creditTrio.tierMeta.tone}
          badgeLabel={creditTrio.tierMeta.shortLabel}
          thresholdText={creditTrio.baseRateLabel ?? creditTrio.tierMeta.description}
          deltaText={
            probDelta !== null ? formatPercentPoints(probDelta, 1) : null
          }
          deltaTone={
            probDelta === null
              ? "neutral"
              : probDelta > 0.005
                ? "down"
                : probDelta < -0.005
                  ? "up"
                  : "neutral"
          }
          historyRangeText={
            probStats
              ? `10 kỳ: Thấp ${formatPercent(probStats.min)} · TV ${formatPercent(probStats.median)} · Cao ${formatPercent(probStats.max)}`
              : null
          }
        >
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="rounded-sm bg-surface-muted px-1.5 py-0.5 font-medium text-ink-muted whitespace-nowrap">
              {creditTrio.topPercentLabel}
            </span>
          </div>
        </VerdictCard>
      </section>

      {/* Tier 1b: Primary Price & Early-Warning Overlay + Flat PIT Valuation Strip */}
      <PriceRiskOverlayChart
        ticker={profile.ticker}
        overlay={overlayData}
        isLoading={overlayLoading}
        isError={overlayError}
        errorMessage={overlayErrorMessage}
        onRetry={onRetryOverlay}
      />

      <PitValuationStrip
        latestValuation={overlayData?.latest_valuation}
        isLoading={overlayLoading}
      />

      {/* Tier 2: SHAP Drivers + Red Flags & Filings */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-7">
          <ExplainWaterfallChart
            shap={activeMl?.shap_top_drivers}
            distressProb={activeMl?.calibrated_distress_prob}
            periodLabel={activeMlPeriodLabel}
            isCustomPeriod={isCustomMlPeriod}
            onResetToLatest={onResetMlPeriod}
            ratioDictMap={ratioDictMap}
          />

          <EChartBox
            title="Diễn biến Xác suất kiệt quệ theo kỳ — Nhấp kỳ để xem yếu tố tác động"
            subtitle="Đơn vị: phần trăm (%) · Nhấp vào một điểm bất kỳ để cập nhật biểu đồ giải thích phía trên"
            option={mlDistressProbOption}
            height={190}
            isEmpty={(mlDistressItems ?? sortedMl).length === 0}
            onChartClick={onDistressPeriodClick ?? onRiskPeriodClick}
            tableHeaders={["Kỳ", "XS 1 năm", "Bậc", "Xếp hạng cùng kỳ", "Altman Z″"]}
            tableRows={(mlDistressItems ?? sortedMl).map((m) => {
              const rowTrio = formatCreditTrio({
                risk_tier: m.risk_tier,
                risk_market_percentile: m.risk_market_percentile,
                calibrated_distress_prob: m.calibrated_distress_prob,
                relative_risk_lift: m.relative_risk_lift,
              });
              return [
                formatPeriodLabel(m.period_end_date, periodType),
                rowTrio.liftLabel
                  ? `${rowTrio.pdLabel} (${rowTrio.liftLabel})`
                  : rowTrio.pdLabel,
                rowTrio.tierMeta.badgeText,
                rowTrio.topPercentLabel,
                formatNumber(m.altman_z_double_prime, 2),
              ];
            })}
            headerRight={
              isCustomMlPeriod ? (
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={onResetMlPeriod}
                >
                  <RotateCcw className="size-3" />
                  <span>Về kỳ mới nhất</span>
                </Button>
              ) : null
            }
          />
        </div>

        <div className="space-y-4 lg:col-span-5">
          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="flex items-center gap-1.5 text-[14px]">
                <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
                <span>Cờ đỏ kế toán đang kích hoạt ({activeRedFlags.length}/10)</span>
              </CardTitle>
              <button
                type="button"
                onClick={() => onNavigateTab("quality")}
                className="inline-flex min-h-6 items-center px-1 text-xs font-medium text-accent-ink hover:underline"
              >
                Xem 10 quy tắc →
              </button>
            </CardHeader>
            <CardContent className="p-3.5">
              {activeRedFlags.length === 0 ? (
                <div className="flex items-center gap-2 rounded-md border border-risk-low/30 bg-risk-low-soft/40 p-3 text-xs text-risk-low">
                  <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
                  <span>Không ghi nhận cờ đỏ bất thường kế toán trong kỳ này.</span>
                </div>
              ) : (
                <ul className="divide-y divide-border overflow-hidden rounded-md border border-border">
                  {activeRedFlags.slice(0, 4).map((rf) => {
                    const sev = SEVERITY_META[rf.severity] ?? SEVERITY_META.MEDIUM;
                    const shortCode = rf.flag_code.split("_").slice(0, 2).join("_");
                    const presetQ = getRedFlagPresetQuestion(shortCode);
                    return (
                      <li
                        key={`${rf.flag_code}-${rf.period_end_date}`}
                        className="bg-canvas/40 p-2.5 text-xs"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-ink">
                            {rf.flag_title_vn ?? "Dấu hiệu bất thường kế toán"}
                          </span>
                          <RiskBadge tone={sev.tone} label={sev.shortLabel} />
                        </div>
                        {rf.explanation_text_vn ? (
                          <p className="mt-1 text-[11px] leading-4 text-ink-muted">
                            {rf.explanation_text_vn}
                          </p>
                        ) : null}
                        {presetQ ? (
                          <div className="mt-2">
                            <Button
                              type="button"
                              variant="outline"
                              size="xs"
                              onClick={() =>
                                onInvestigateRedFlag(shortCode, presetQ)
                              }
                            >
                              <FileSearch className="size-3" aria-hidden="true" />
                              <span>Kiểm tra Thuyết minh gốc</span>
                            </Button>
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Kiểm toán &amp; Hồ sơ công bố gần nhất
              </CardTitle>
              <button
                type="button"
                onClick={() => onNavigateTab("quality")}
                className="inline-flex min-h-6 items-center px-1 text-xs font-medium text-accent-ink hover:underline"
              >
                Lịch sử hồ sơ →
              </button>
            </CardHeader>
            <CardContent className="p-3.5 text-xs">
              {filingsLoading ? (
                <p className="text-ink-muted">Đang tải thông tin kiểm toán...</p>
              ) : !latestFiling ? (
                <p className="text-ink-muted">
                  Chưa ghi nhận chi tiết hồ sơ cho phạm vi báo cáo này.
                </p>
              ) : (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
                  <div>
                    <dt className="text-ink-muted">Kỳ hồ sơ</dt>
                    <dd className="font-medium text-ink">
                      {latestFiling.period_end_date
                        ? formatDateVi(latestFiling.period_end_date)
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Trạng thái kiểm toán</dt>
                    <dd className="font-medium text-ink">
                      {(latestFiling.audit_status &&
                        AUDIT_STATUS_LABELS[latestFiling.audit_status]) ??
                        latestFiling.audit_status ??
                        "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Ý kiến kiểm toán</dt>
                    <dd className="font-medium text-ink">
                      {(latestFiling.audit_opinion &&
                        AUDIT_OPINION_LABELS[latestFiling.audit_opinion]) ??
                        latestFiling.audit_opinion ??
                        "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-ink-muted">Phiên bản &amp; Hồi tố</dt>
                    <dd className="font-medium text-ink">
                      {(latestFiling.revision_reason &&
                        REVISION_REASON_LABELS[latestFiling.revision_reason]) ??
                        latestFiling.revision_reason ??
                        "Bản gốc"}
                      {latestFiling.has_restatement ? " · Có điều chỉnh" : ""}
                    </dd>
                  </div>
                </dl>
              )}
            </CardContent>
          </Card>

          <BondMaturityCard
            data={bondSignals}
            isLoading={bondLoading}
            isError={bondError}
            errorMessage={bondErrorMessage}
            onRetry={onRetryBond ?? (() => undefined)}
          />
        </div>
      </section>

      {/* Tier 2b: Core Financial Visual Storytelling (Growth + Capital Structure) */}
      <section
        aria-label="Diễn biến tăng trưởng và cơ cấu vốn"
        className="grid grid-cols-1 gap-4 xl:grid-cols-2"
      >
        <GrowthComboChart
          ticker={profile.ticker}
          points={growthSeries}
          globalPeriodType={periodType}
          localPeriodView={effectiveGrowthView}
          onChangeLocalPeriodView={onChangeGrowthLocalView}
          isLoading={growthLoading}
        />
        <CapitalStructureChart
          ticker={profile.ticker}
          points={profile.capital_structure_series}
          periodType={periodType}
        />
      </section>

      {/* Tier 3: Peer Benchmark Table */}
      <Card>
        <CardHeader className="flex-wrap gap-2 py-2.5">
          <div>
            <CardTitle className="text-[14px]">
              Bảng tỷ số &amp; Phân vị so với nhóm ngành
            </CardTitle>
            <p className="mt-0.5 text-xs text-ink-muted">
              Nhấp tên chỉ số để xem lịch sử dải P25–P75 · Nhấp Truy nguồn để kiểm chứng tử số/mẫu số và hồ sơ gốc
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl
              ariaLabel="Phạm vi hiển thị số lượng chỉ số"
              semantics="radiogroup"
              size="xs"
              value={showAll38 ? "ALL_38" : "CORE_12"}
              onChange={(v) => onChangeShowAll38(v === "ALL_38")}
              options={[
                { value: "CORE_12", label: "12 chỉ số cốt lõi" },
                {
                  value: "ALL_38",
                  label: `Tất cả ${ratioDictMap.size || 38} chỉ số`,
                },
              ]}
            />

            <SegmentedControl
              ariaLabel="Chế độ hiển thị giá trị hoặc phân vị"
              semantics="radiogroup"
              size="xs"
              value={trafficMode}
              onChange={onChangeTrafficMode}
              options={[
                { value: "VALUE", label: "Giá trị tuyệt đối" },
                { value: "PERCENTILE", label: "Chỉ xem phân vị" },
              ]}
            />
          </div>
        </CardHeader>

        <CardContent className="p-0">
          {summaryLoading ? (
            <p className="p-4 text-xs text-ink-muted">Đang tải bảng tỷ số ngành...</p>
          ) : summaryError ? (
            <div className="p-4">
              <ErrorState
                message={summaryErrorMessage ?? "Không thể tải bảng tỷ số ngành."}
                onRetry={onRetrySummary}
              />
            </div>
          ) : (summaryData?.items.length ?? 0) === 0 ? (
            <div className="p-4">
              <EmptyState
                title="Chưa có dữ liệu so sánh ngành cho kỳ này"
                description="Thử chuyển phạm vi báo cáo hoặc loại kỳ trên thanh công cụ phía trên."
              />
            </div>
          ) : (
            <>
              <p className="mb-1.5 px-3 pt-2 text-[11px] text-ink-subtle md:hidden">
                Vuốt ngang để xem phân vị, xu hướng &amp; truy nguồn →
              </p>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="px-3 py-2 text-left font-semibold">
                      Chỉ số
                    </th>
                    {trafficMode === "VALUE" ? (
                      <>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          Giá trị DN
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-semibold">
                          Trung vị ngành
                        </th>
                      </>
                    ) : (
                      <th scope="col" className="px-3 py-2 text-left font-semibold">
                        Nhóm so sánh
                      </th>
                    )}
                    <th scope="col" className="px-3 py-2 text-left font-semibold">
                      Phân vị ngành
                    </th>
                    <th scope="col" className="px-3 py-2 text-center font-semibold">
                      Xu hướng 8 kỳ
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">
                      Nguồn
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(summaryData?.items ?? []).map((item) => {
                    const dict = ratioDictMap.get(item.metric_name);
                    const peerInfo = formatPeerGroupLabel(
                      item.peer_group_level,
                      item.peer_count,
                    );
                    return (
                      <tr
                        key={item.metric_name}
                        className="border-b border-border/60 hover:bg-surface-hover/40"
                      >
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() =>
                                onNavigateTab("ratios", {
                                  metric: item.metric_name,
                                })
                              }
                              className="inline-flex min-h-6 items-center text-left font-medium text-accent-ink hover:underline"
                            >
                              {item.display_name_vn}
                            </button>
                            {dict ? (
                              <InfoTooltip
                                text={`${dict.description_vn} (Công thức: ${dict.formula_display})`}
                                label={`Giải thích ${item.display_name_vn}`}
                              />
                            ) : null}
                          </div>
                        </td>

                        {trafficMode === "VALUE" ? (
                          <>
                            <td className="num px-3 py-2 text-right font-semibold text-ink">
                              {formatRatioValue(item.company_value, item.format_type)}
                            </td>
                            <td className="num px-3 py-2 text-right text-ink-muted">
                              {formatRatioValue(item.peer_median, item.format_type)}
                            </td>
                          </>
                        ) : (
                          <td className="px-3 py-2 text-ink-muted">
                            {peerInfo.text}
                          </td>
                        )}

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
