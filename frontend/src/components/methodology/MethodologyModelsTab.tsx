"use client";

import Link from "next/link";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  TierReliabilityChart,
  WalkForwardCharts,
} from "@/components/charts/ModelCardCharts";
import { MethodologyFormulaCards } from "@/components/financial/MethodologyFormulaCards";
import { RiskBadge, RiskTierBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getTt99GateVerdictMeta } from "@/lib/compliance";
import {
  formatCreditTrio,
  formatPeerStandardizationMode,
  formatRegulatoryEventType,
  formatTargetModeLabel,
  getMlFeatureLabel,
  MODEL_ROLE_LABELS,
  sanitizeModelLimitationText,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
} from "@/lib/format";
import type { ModelCardResponse } from "@/types/api";

export function MethodologyModelsTab({
  modelCardQuery,
  scrollRegionClassName,
}: {
  modelCardQuery: UseQueryResult<ModelCardResponse | null>;
  scrollRegionClassName: string;
}) {
  if (modelCardQuery.isLoading) {
    return <PageSkeleton />;
  }
  if (modelCardQuery.isError) {
    return (
      <ErrorState
        message={modelCardQuery.error.message}
        onRetry={() => modelCardQuery.refetch()}
      />
    );
  }
  if (!modelCardQuery.data) {
    return (
      <EmptyState
        title="Chưa có Hồ sơ kiểm định mô hình trong bản phát hành dữ liệu"
        description="Bản phát hành hiện tại chưa kèm báo cáo Walk-Forward và phân tầng cảnh báo rủi ro định lượng."
      />
    );
  }

  const card = modelCardQuery.data;
  const drift = card.taxonomy_drift_psi;
  const cutoffs = card.risk_tier_cutoffs;
  const tt99Gate = card.tt99_mrm_gate;
  const tt99GateMeta = getTt99GateVerdictMeta(tt99Gate?.verdict);

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
            <div tabIndex={0} role="region" aria-label="Bảng Case Studies cảnh báo sớm lịch sử" className={scrollRegionClassName}>
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
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 py-2.5">
          <div>
            <CardTitle className="text-[14px]">
              Bảng chuyển đổi chuẩn mực{" "}
              {TAXONOMY_LABELS[drift.reference_taxonomy] ?? "TT200/2014"} ↔{" "}
              {TAXONOMY_LABELS[drift.target_taxonomy] ?? "TT99/2025"} &amp; Độ ổn định PSI
            </CardTitle>
            <p className="mt-0.5 text-xs text-ink-muted">
              {tt99Gate?.summary_vi ?? tt99GateMeta.description}
            </p>
          </div>
          <RiskBadge tone={tt99GateMeta.tone} label={tt99GateMeta.label} />
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
}
