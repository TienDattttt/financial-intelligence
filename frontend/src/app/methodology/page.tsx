"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  TierReliabilityChart,
  WalkForwardCharts,
} from "@/components/charts/ModelCardCharts";
import { MethodologyFormulaCards } from "@/components/financial/MethodologyFormulaCards";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  useAllIssuers,
  useMetaDictionary,
  useModelCard,
  useQualityQuarantine,
  useQualitySummary,
  useSnapshotOverview,
} from "@/hooks/use-finintel-queries";
import {
  formatCreditTrio,
  formatFormulaVi,
  formatPeerStandardizationMode,
  formatRedFlagCodeLabel,
  formatRegulatoryEventType,
  formatTargetModeLabel,
  getMlFeatureLabel,
  MODEL_ROLE_LABELS,
  QUALITY_RULE_LABELS,
  QUALITY_STATUS_META,
  RATIO_CATEGORY_LABELS,
  sanitizeModelLimitationText,
  SEVERITY_META,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
  normalizeSearchText,
} from "@/lib/format";

type MethodTab = "dictionary" | "models" | "quality";
const SCROLL_REGION_CLS =
  "overflow-x-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const METHOD_TABS: readonly { value: MethodTab; label: string }[] = [
  { value: "dictionary", label: "Từ điển chỉ số & Cờ đỏ" },
  { value: "models", label: "Phương pháp mô hình" },
  { value: "quality", label: "Chất lượng dữ liệu" },
];

const DIRECTION_LABELS: Record<string, string> = {
  HIGHER_IS_BETTER: "Cao hơn tích cực hơn",
  LOWER_IS_BETTER: "Thấp hơn tích cực hơn",
  NEUTRAL: "Trung tính (phụ thuộc bối cảnh)",
};

function MethodologyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { snapshotId, cacheSnapshotKey } = useAppState();
  const effectiveCacheKey = cacheSnapshotKey ?? snapshotId;

  const rawTab = searchParams.get("tab");
  const activeTab: MethodTab =
    rawTab === "models" || rawTab === "model"
      ? "models"
      : rawTab === "quality"
        ? "quality"
        : "dictionary";

  const [dictSearch, setDictSearch] = React.useState("");
  const [catFilter, setCatFilter] = React.useState("");
  const [qOffset, setQOffset] = React.useState(0);
  const [quarOffset, setQuarOffset] = React.useState(0);

  const dictQuery = useMetaDictionary(effectiveCacheKey);
  const modelCardQuery = useModelCard(effectiveCacheKey, {
    enabled: activeTab === "models",
  });
  const overviewQuery = useSnapshotOverview(effectiveCacheKey);
  const summaryQuery = useQualitySummary(25, qOffset, effectiveCacheKey);
  const quarantineQuery = useQualityQuarantine(25, quarOffset, effectiveCacheKey);
  const issuersQuery = useAllIssuers(effectiveCacheKey);

  const issuerTickerMap = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const iss of issuersQuery.data ?? []) {
      map.set(iss.issuer_id, iss.ticker);
    }
    return map;
  }, [issuersQuery.data]);

  const setTab = (tab: MethodTab) => {
    router.replace(tab === "dictionary" ? "/methodology" : `/methodology?tab=${tab}`, {
      scroll: false,
    });
  };

  const totalRatios = dictQuery.data?.ratios.length ?? 38;
  const totalRedFlags = dictQuery.data?.red_flags.length ?? 10;
  const isRatiosFiltered = Boolean(dictSearch.trim() || catFilter);

  const filteredRatios = React.useMemo(() => {
    const list = dictQuery.data?.ratios ?? [];
    const q = normalizeSearchText(dictSearch);
    return list.filter((r) => {
      if (catFilter && r.category !== catFilter) return false;
      if (!q) return true;
      return (
        normalizeSearchText(r.display_name_vn).includes(q) ||
        normalizeSearchText(r.ratio_name).includes(q) ||
        normalizeSearchText(r.description_vn).includes(q) ||
        normalizeSearchText(r.formula_display).includes(q)
      );
    });
  }, [catFilter, dictQuery.data?.ratios, dictSearch]);

  return (
    <div className="space-y-4">
      <SegmentedControl<MethodTab>
        ariaLabel="Các mục tài liệu phương pháp"
        variant="underline"
        value={activeTab}
        onChange={setTab}
        idPrefix="methodology"
        controlsPanelId={(t) => `methodology-panel-${t}`}
        options={METHOD_TABS}
      />

      <div
        id={`methodology-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`methodology-tab-${activeTab}`}
      >
      {activeTab === "dictionary" ? (
        dictQuery.isLoading ? (
          <PageSkeleton />
        ) : dictQuery.isError ? (
          <ErrorState message={dictQuery.error.message} onRetry={() => dictQuery.refetch()} />
        ) : (
          <div className="space-y-5">
            <Card>
              <CardHeader className="flex-wrap gap-2 py-2.5">
                <CardTitle className="text-[14px]">
                  {isRatiosFiltered
                    ? `Hiển thị ${filteredRatios.length} / ${totalRatios} tỷ số tài chính chuẩn hóa`
                    : `${totalRatios} tỷ số tài chính chuẩn hóa`}
                </CardTitle>
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  <Input
                    value={dictSearch}
                    onChange={(e) => setDictSearch(e.target.value)}
                    placeholder="Tìm tên chỉ số hoặc công thức..."
                    className="h-8 w-full text-xs sm:w-56"
                    aria-label="Tìm trong từ điển chỉ số"
                  />
                  <Select
                    value={catFilter}
                    onChange={(e) => setCatFilter(e.target.value)}
                    className="h-8 text-xs"
                    aria-label="Lọc theo nhóm chỉ số"
                  >
                    <option value="">Tất cả nhóm</option>
                    {Object.entries(RATIO_CATEGORY_LABELS).map(([k, lbl]) => (
                      <option key={k} value={k}>
                        {lbl}
                      </option>
                    ))}
                  </Select>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div
                  tabIndex={0}
                  role="region"
                  aria-label={`Bảng từ điển ${totalRatios} tỷ số tài chính`}
                  className={SCROLL_REGION_CLS}
                >
                  <table className="w-full min-w-[720px] border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Chỉ số
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Nhóm
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Công thức chuẩn hóa
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Chiều đánh giá
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Ý nghĩa phân tích
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRatios.map((r) => (
                        <tr
                          key={r.ratio_name}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-3 py-2">
                            <div className="font-medium text-ink">{r.display_name_vn}</div>
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {RATIO_CATEGORY_LABELS[r.category] ?? r.category}
                          </td>
                          <td className="px-3 py-2 text-xs text-ink">
                            {formatFormulaVi(r.formula_display)}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {DIRECTION_LABELS[r.direction] ?? r.direction}
                          </td>
                          <td className="max-w-md px-3 py-2 text-ink-muted">
                            {r.description_vn}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-2.5">
                <CardTitle className="text-[14px]">
                  {`${totalRedFlags} quy tắc phát hiện bất thường kế toán (Quy tắc 01 – ${String(totalRedFlags).padStart(2, "0")})`}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div
                  tabIndex={0}
                  role="region"
                  aria-label={`Bảng ${totalRedFlags} quy tắc phát hiện bất thường kế toán`}
                  className={SCROLL_REGION_CLS}
                >
                  <table className="w-full min-w-[720px] border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Mã quy tắc
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Tên dấu hiệu
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Mức độ
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Cơ sở phương pháp
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Điều kiện kích hoạt
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {(dictQuery.data?.red_flags ?? []).map((rf) => {
                        const sev = SEVERITY_META[rf.default_severity] ?? SEVERITY_META.MEDIUM;
                        return (
                          <tr
                            key={rf.flag_code}
                            className="border-b border-border/60 hover:bg-surface-muted/40"
                          >
                            <td className="px-3 py-2 font-mono font-semibold text-ink">
                              {formatRedFlagCodeLabel(rf.flag_code)}
                            </td>
                            <td className="px-3 py-2 font-medium text-ink">
                              {rf.title_vn}
                            </td>
                            <td className="px-3 py-2">
                              <RiskBadge tone={sev.tone} label={sev.label} />
                            </td>
                            <td className="px-3 py-2 text-ink-muted">
                              {rf.methodology_ref}
                            </td>
                            <td className="max-w-md px-3 py-2 text-ink-muted">
                              {rf.description_vn}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </div>
        )
      ) : activeTab === "models" ? (
        modelCardQuery.isLoading ? (
          <PageSkeleton />
        ) : modelCardQuery.isError ? (
          <ErrorState
            message={modelCardQuery.error.message}
            onRetry={() => modelCardQuery.refetch()}
          />
        ) : !modelCardQuery.data ? (
          <EmptyState
            title="Chưa có Hồ sơ kiểm định mô hình trong bản phát hành dữ liệu"
            description="Bản phát hành hiện tại chưa kèm báo cáo Walk-Forward và phân tầng tín nhiệm."
          />
        ) : (
          (() => {
            const card = modelCardQuery.data;
            const drift = card.taxonomy_drift_psi;
            const cutoffs = card.risk_tier_cutoffs;
            return (
              <div className="space-y-5">
                <section
                  aria-label="Thông số mô hình hiện hành"
                  className="grid grid-cols-2 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:divide-x sm:divide-y-0 lg:grid-cols-5"
                >
                  <div className="p-3">
                    <p className="text-[11px] text-ink-muted">Phiên bản &amp; Vai trò</p>
                    <p className="mt-1 font-mono text-sm font-semibold text-ink">
                      {card.model_version}
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-subtle">
                      {MODEL_ROLE_LABELS[card.model_role] ?? "Chỉ báo định lượng"}
                    </p>
                  </div>
                  <div className="p-3">
                    <p className="text-[11px] text-ink-muted">Chốt dữ liệu huấn luyện</p>
                    <p className="num mt-1 text-base font-semibold text-ink">
                      {formatDateVi(card.trained_as_of)}
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-subtle">
                      Phạm vi {STATEMENT_SCOPE_LABELS[card.statement_scope] ?? "Hợp nhất"}
                    </p>
                  </div>
                  <div className="p-3">
                    <p className="text-[11px] text-ink-muted">Số biến định lượng</p>
                    <p className="num mt-1 text-base font-semibold text-ink">
                      {formatNumber(card.feature_count, 0)} biến
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-subtle">
                      XGBoost đơn điệu + Hiệu chỉnh Platt
                    </p>
                  </div>
                  <div className="p-3">
                    <p className="text-[11px] text-ink-muted">Tỷ lệ biến cố nền (Train)</p>
                    <p className="num mt-1 text-base font-semibold text-ink">
                      {formatPercent(card.train_prevalence, { decimals: 2 })}
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-subtle">
                      Dùng tính hệ số rủi ro (× nền)
                    </p>
                  </div>
                  <div className="col-span-2 p-3 lg:col-span-1">
                    <p className="text-[11px] text-ink-muted">Ngưỡng cắt bậc R1–R5 (TTC)</p>
                    <p className="num mt-1 text-xs font-semibold text-ink">
                      R2 ≥ {formatPercent(cutoffs.R2_min_pd, { decimals: 2 })} · R3 ≥{" "}
                      {formatPercent(cutoffs.R3_min_pd, { decimals: 2 })}
                    </p>
                    <p className="num mt-0.5 text-xs font-semibold text-risk-high">
                      R4 ≥ {formatPercent(cutoffs.R4_min_pd, { decimals: 2 })} · R5 ≥{" "}
                      {formatPercent(cutoffs.R5_min_pd, { decimals: 2 })}
                    </p>
                  </div>
                </section>

                <Card>
                  <CardContent className="grid grid-cols-1 gap-3 p-3.5 text-xs md:grid-cols-2">
                    <div>
                      <span className="font-semibold text-ink">Biến cố mục tiêu 12 tháng: </span>
                      <span className="text-ink-muted">
                        {formatTargetModeLabel(card.target_mode)}
                      </span>
                    </div>
                    <div>
                      <span className="font-semibold text-ink">Chuẩn hóa đặc trưng ngành: </span>
                      <span className="text-ink-muted">
                        {formatPeerStandardizationMode(card.peer_standardization_mode)}
                      </span>
                    </div>
                  </CardContent>
                </Card>

                <WalkForwardCharts walkForward={card.walk_forward} />

                <TierReliabilityChart reliability={card.risk_tier_reliability} />

                <Card>
                  <CardHeader className="py-2.5">
                    <div>
                      <CardTitle className="text-[14px]">
                        Case Studies lịch sử Việt Nam — Đối chiếu cảnh báo R4/R5 và sự kiện pháp lý
                      </CardTitle>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        Phân biệt trường hợp mô hình cảnh báo sớm trước ngày sự kiện công bố (Sớm X quý) và trường hợp phân loại hồi cứu sau khi đã có sự kiện kiểm soát/cảnh báo. Nhấp vào mã doanh nghiệp để mở Hồ sơ chi tiết và lớp phủ giá EOD.
                      </p>
                    </div>
                  </CardHeader>
                  <CardContent className="p-0">
                    {card.case_studies.length === 0 ? (
                      <p className="p-4 text-xs text-ink-muted">
                        Chưa ghi nhận hồ sơ Case Study trong lát cắt dữ liệu hiện tại.
                      </p>
                    ) : (
                      <div tabIndex={0} role="region" aria-label="Bảng Case Studies cảnh báo sớm lịch sử" className={SCROLL_REGION_CLS}>
                        <table className="w-full min-w-[920px] border-collapse text-xs">
                          <thead>
                            <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Doanh nghiệp
                              </th>
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Cảnh báo / Kiểm soát đầu tiên
                              </th>
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Đình chỉ / Hủy niêm yết
                              </th>
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Mốc mô hình nâng R4/R5
                              </th>
                              <th scope="col" className="px-3 py-2 text-right font-semibold">
                                Cảnh báo sớm
                              </th>
                              <th scope="col" className="px-3 py-2 text-left font-semibold">
                                Top-3 yếu tố TreeSHAP dẫn dắt
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {card.case_studies.map((cs) => {
                              const warnMeta = formatRegulatoryEventType(
                                cs.first_warning_event_type,
                              );
                              const trio = formatCreditTrio({
                                risk_tier: cs.first_r4_r5_risk_tier,
                                risk_market_percentile: cs.first_r4_r5_market_percentile,
                                calibrated_distress_prob: cs.first_r4_r5_pd,
                                relative_risk_lift: cs.first_r4_r5_lift,
                              });
                              const r4r5Period =
                                cs.first_r4_r5_fiscal_year && cs.first_r4_r5_fiscal_quarter
                                  ? `Q${cs.first_r4_r5_fiscal_quarter}/${cs.first_r4_r5_fiscal_year}`
                                  : cs.first_r4_r5_period_end_date
                                    ? formatPeriodLabel(cs.first_r4_r5_period_end_date, "QUARTER")
                                    : "—";
                              return (
                                <tr
                                  key={cs.issuer_id}
                                  className="border-b border-border/60 align-top hover:bg-surface-muted/40"
                                >
                                  <td className="px-3 py-2">
                                    <Link
                                      href={`/companies/${cs.ticker}`}
                                      className="font-mono text-sm font-semibold text-accent-ink hover:underline"
                                    >
                                      {cs.ticker}
                                    </Link>
                                    {cs.exchange ? (
                                      <span className="ml-1.5 rounded border border-border bg-canvas px-1 py-0.5 text-[10px] text-ink-muted">
                                        {cs.exchange}
                                      </span>
                                    ) : null}
                                    <div className="mt-0.5 max-w-[180px] truncate text-[11px] text-ink-muted">
                                      {cs.company_name_vn ?? cs.ticker}
                                    </div>
                                  </td>
                                  <td className="px-3 py-2">
                                    {cs.first_warning_or_control_date ? (
                                      <>
                                        <div className="num font-medium text-ink">
                                          {formatDateVi(cs.first_warning_or_control_date)}
                                        </div>
                                        <div className="mt-1">
                                          <RiskBadge
                                            tone={warnMeta.tone}
                                            label={warnMeta.shortLabel}
                                          />
                                        </div>
                                      </>
                                    ) : (
                                      <span className="text-ink-subtle">Chưa ghi nhận</span>
                                    )}
                                  </td>
                                  <td className="px-3 py-2">
                                    {cs.forced_delisting_or_suspension_date ? (
                                      <>
                                        <div className="num font-medium text-risk-high">
                                          {formatDateVi(cs.forced_delisting_or_suspension_date)}
                                        </div>
                                        {cs.exchange_delisted_from ? (
                                          <div className="mt-0.5 text-[11px] text-ink-muted">
                                            Sàn {cs.exchange_delisted_from}
                                          </div>
                                        ) : null}
                                      </>
                                    ) : (
                                      <span className="text-ink-subtle">Không / Còn niêm yết</span>
                                    )}
                                  </td>
                                  <td className="px-3 py-2">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <RiskTierBadge riskTier={cs.first_r4_r5_risk_tier} compact />
                                      <span className="font-medium text-ink">{r4r5Period}</span>
                                    </div>
                                    <div className="num mt-1 text-[11px] text-ink-muted">
                                      1Y PD: <strong className="text-ink">{trio.pdLabel}</strong>
                                      {trio.liftLabel ? ` · ${trio.liftLabel}` : ""}
                                    </div>
                                    {trio.topPercent !== null ? (
                                      <div className="text-[11px] text-ink-subtle">
                                        {trio.topPercentLabel}
                                        {cs.first_r4_r5_available_at
                                          ? ` · Công bố ${formatDateVi(cs.first_r4_r5_available_at)}`
                                          : ""}
                                      </div>
                                    ) : null}
                                  </td>
                                  <td className="num px-3 py-2 text-right">
                                    {cs.early_warning_lead_quarters !== null &&
                                    cs.early_warning_lead_quarters !== undefined ? (
                                      (() => {
                                        const leadQ = cs.early_warning_lead_quarters;
                                        const leadD = cs.early_warning_lead_days;
                                        const isRetrospective =
                                          leadQ < 0 ||
                                          (leadD !== null && leadD !== undefined && leadD < 0);
                                        if (isRetrospective) {
                                          const afterLabel =
                                            leadD !== null && leadD !== undefined
                                              ? `Sau sự kiện ${formatNumber(Math.abs(leadD), 0)} ngày · Phân loại hồi cứu`
                                              : `Sau sự kiện ${Math.abs(leadQ)} quý · Phân loại hồi cứu`;
                                          return (
                                            <div className="flex flex-col items-end gap-1">
                                              <RiskBadge tone="mid" label={afterLabel} />
                                              <span className="text-[11px] text-ink-subtle">
                                                Trễ {Math.abs(leadQ)} quý so với sự kiện
                                              </span>
                                            </div>
                                          );
                                        }
                                        const earlyLabel =
                                          leadQ > 0 ? `Sớm ${leadQ} quý` : "Cùng kỳ cảnh báo";
                                        return (
                                          <div className="flex flex-col items-end gap-1">
                                            <RiskBadge tone="low" label={earlyLabel} />
                                            {leadD !== null && leadD !== undefined ? (
                                              <span className="text-[11px] text-ink-subtle">
                                                ({formatNumber(leadD, 0)} ngày)
                                              </span>
                                            ) : null}
                                          </div>
                                        );
                                      })()
                                    ) : (
                                      <RiskBadge
                                        tone="neutral"
                                        label="Chưa ghi nhận ngày sự kiện"
                                      />
                                    )}
                                  </td>
                                  <td className="px-3 py-2">
                                    {(cs.top_3_shap_drivers ?? []).length === 0 ? (
                                      <span className="text-ink-subtle">Chưa có giải thích SHAP</span>
                                    ) : (
                                      <ul className="space-y-0.5 text-[11px]">
                                        {(cs.top_3_shap_drivers ?? []).slice(0, 3).map((drv) => {
                                          const label =
                                            drv.display_name_vn &&
                                            drv.display_name_vn !== drv.feature
                                              ? drv.display_name_vn
                                              : getMlFeatureLabel(drv.feature);
                                          return (
                                            <li
                                              key={drv.feature}
                                              className="flex items-center justify-between gap-2"
                                            >
                                              <span className="max-w-[200px] truncate text-ink">
                                                {label}
                                              </span>
                                              <span className="num shrink-0 font-medium text-risk-high">
                                                +{formatNumber(drv.shap_delta, 2)}
                                              </span>
                                            </li>
                                          );
                                        })}
                                      </ul>
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

                <Card>
                  <CardHeader className="py-2.5">
                    <div>
                      <CardTitle className="text-[14px]">
                        Bảng chuyển đổi chuẩn mực{" "}
                        {TAXONOMY_LABELS[drift.reference_taxonomy] ?? "TT200/2014"} ↔{" "}
                        {TAXONOMY_LABELS[drift.target_taxonomy] ?? "TT99/2025"} &amp; Độ ổn định PSI
                      </CardTitle>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        Đánh giá mức dịch chuyển phân bố biến đầu vào và tỷ lệ thiếu dữ liệu giữa hai chuẩn mực VAS
                      </p>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3 p-3.5">
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <div className="rounded border border-border bg-canvas p-2.5">
                        <p className="text-[11px] text-ink-muted">Mẫu đối chiếu TT200 vs TT99</p>
                        <p className="num mt-0.5 text-sm font-semibold text-ink">
                          {formatNumber(drift.reference_rows, 0)} vs{" "}
                          {formatNumber(drift.target_rows, 0)}
                        </p>
                      </div>
                      <div className="rounded border border-border bg-canvas p-2.5">
                        <p className="text-[11px] text-ink-muted">PSI trung bình / tối đa</p>
                        <p className="num mt-0.5 text-sm font-semibold text-ink">
                          {formatNumber(drift.mean_feature_psi, 3)} /{" "}
                          {formatNumber(drift.max_feature_psi, 3)}
                        </p>
                      </div>
                      <div className="rounded border border-border bg-canvas p-2.5">
                        <p className="text-[11px] text-ink-muted">Biến có PSI &gt; 0,25</p>
                        <p className="num mt-0.5 text-sm font-semibold text-ink">
                          {formatNumber(drift.features_psi_gt_0_25.length, 0)} /{" "}
                          {formatNumber(drift.feature_drift.length, 0)}
                        </p>
                      </div>
                      <div className="rounded border border-border bg-canvas p-2.5">
                        <p className="text-[11px] text-ink-muted">PSI điểm số &amp; Tỷ lệ R4/R5</p>
                        <p className="num mt-0.5 text-sm font-semibold text-ink">
                          {drift.score_drift
                            ? `${formatNumber(drift.score_drift.score_psi, 3)} (${formatPercentPoints(drift.score_drift.r4_r5_share_delta, 1)})`
                            : "Chưa đủ mẫu TT99"}
                        </p>
                      </div>
                    </div>

                    {drift.feature_drift.length === 0 ? (
                      <p className="text-xs text-ink-muted">
                        Chưa có đủ dữ liệu TT99/2025 để tính PSI từng biến.
                      </p>
                    ) : (
                      <div
                        tabIndex={0}
                        role="region"
                        aria-label="Bảng độ ổn định PSI 23 biến đầu vào"
                        className="max-h-[340px] overflow-auto rounded-lg border border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <table className="w-full min-w-[680px] border-collapse text-xs">
                          <thead>
                            <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                                Đặc trưng mô hình
                              </th>
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                                Chỉ số PSI
                              </th>
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                                Thiếu TT200
                              </th>
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                                Thiếu TT99
                              </th>
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-right font-semibold">
                                Chênh lệch
                              </th>
                              <th scope="col" className="sticky top-0 bg-surface-muted px-3 py-2 text-left font-semibold">
                                Mức ổn định
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {drift.feature_drift.map((row) => {
                              const psiVal = row.psi;
                              const psiTone =
                                psiVal === null || psiVal === undefined
                                  ? ("neutral" as const)
                                  : psiVal > 0.25
                                    ? ("high" as const)
                                    : psiVal > 0.1
                                      ? ("mid" as const)
                                      : ("low" as const);
                              const psiLabel =
                                psiVal === null || psiVal === undefined
                                  ? "Chưa đủ mẫu"
                                  : psiVal > 0.25
                                    ? "Dịch chuyển cao"
                                    : psiVal > 0.1
                                      ? "Cần theo dõi"
                                      : "Ổn định";
                              return (
                                <tr
                                  key={row.feature}
                                  className="border-b border-border/60 hover:bg-surface-muted/40"
                                >
                                  <td className="px-3 py-2 font-medium text-ink">
                                    {getMlFeatureLabel(row.feature)}
                                  </td>
                                  <td className="num px-3 py-2 text-right font-medium text-ink">
                                    {formatNumber(row.psi, 3)}
                                  </td>
                                  <td className="num px-3 py-2 text-right text-ink-muted">
                                    {formatPercent(row.ref_missing_rate)}
                                  </td>
                                  <td className="num px-3 py-2 text-right text-ink-muted">
                                    {formatPercent(row.tgt_missing_rate)}
                                  </td>
                                  <td className="num px-3 py-2 text-right text-ink-muted">
                                    {formatPercentPoints(row.missing_rate_delta, 1)}
                                  </td>
                                  <td className="px-3 py-2">
                                    <RiskBadge tone={psiTone} label={psiLabel} />
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

                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Card className="md:col-span-2">
                    <CardHeader className="py-2.5">
                      <CardTitle className="text-[14px]">
                        Công bố giới hạn mô hình &amp; Kỷ luật dữ liệu Point-in-Time
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
                      <ul className="list-disc space-y-1.5 pl-5">
                        {card.limitations.map((lim, idx) => (
                          <li key={idx}>{sanitizeModelLimitationText(lim)}</li>
                        ))}
                      </ul>
                    </CardContent>
                  </Card>

                  <MethodologyFormulaCards />
                </div>
              </div>
            );
          })()
        )
      ) : overviewQuery.isLoading ? (
        <PageSkeleton />
      ) : overviewQuery.isError ? (
        <ErrorState
          message={overviewQuery.error.message}
          onRetry={() => overviewQuery.refetch()}
        />
      ) : (
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
                  <div tabIndex={0} role="region" aria-label="Bảng nhật ký kiểm tra chất lượng theo kỳ" className={SCROLL_REGION_CLS}>
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
                  <div tabIndex={0} role="region" aria-label="Bảng danh sách hồ sơ bị giữ lại kiểm định" className={SCROLL_REGION_CLS}>
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
      )}
      </div>
    </div>
  );
}

export default function MethodologyPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Dữ liệu, Từ điển chỉ số &amp; Phương pháp mô hình
        </h1>
        <p className="mt-0.5 text-xs text-ink-muted">
          Tài liệu minh bạch công thức các tỷ số tài chính chuẩn hóa, quy tắc phát hiện bất thường kế toán, mô hình định lượng và kiểm tra chất lượng dữ liệu
        </p>
      </div>
      <React.Suspense fallback={<PageSkeleton />}>
        <MethodologyContent />
      </React.Suspense>
    </div>
  );
}
