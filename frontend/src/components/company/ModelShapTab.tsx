"use client";

import * as React from "react";
import { RotateCcw } from "lucide-react";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { ExplainWaterfallChart } from "@/components/charts/ExplainWaterfallChart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getMlFeatureLabel, MODEL_ROLE_LABELS } from "@/lib/domain-labels";
import {
  formatNumber,
  formatPercent,
  formatPeriodLabel,
  MINUS_SIGN,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  MLScoreItem,
  PeriodType,
  RatioDictionaryItem,
  RatioName,
} from "@/types/api";

export function ModelShapTab({
  sortedMl,
  activeMl,
  activeMlPeriodLabel,
  isCustomMlPeriod,
  onResetToLatest,
  mlAltmanOption,
  mlDistressProbOption,
  onRiskPeriodClick,
  periodType,
  ratioDictMap,
}: {
  sortedMl: readonly MLScoreItem[];
  activeMl: MLScoreItem | null;
  activeMlPeriodLabel: string | null;
  isCustomMlPeriod: boolean;
  onResetToLatest: () => void;
  mlAltmanOption: EChartsOption;
  mlDistressProbOption: EChartsOption;
  onRiskPeriodClick: (payload: { dataIndex?: number }) => void;
  periodType: PeriodType;
  ratioDictMap: ReadonlyMap<RatioName, RatioDictionaryItem>;
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <EChartBox
          title="Diễn biến lịch sử điểm Altman Z″ (EM) — Nhấp kỳ để cập nhật TreeSHAP"
          subtitle="Đơn vị: điểm Z″ · Ngưỡng < 1,10 Nguy cơ · > 2,60 An toàn"
          option={mlAltmanOption}
          height={290}
          isEmpty={sortedMl.length === 0}
          onChartClick={onRiskPeriodClick}
          tableHeaders={["Kỳ", "Altman Z″", "F-Score", "Beneish M"]}
          tableRows={sortedMl.map((m) => [
            formatPeriodLabel(m.period_end_date, periodType),
            formatNumber(m.altman_z_double_prime, 2),
            m.piotroski_f_score !== null && m.piotroski_f_score !== undefined
              ? `${m.piotroski_f_score}/9`
              : "N/A",
            formatNumber(m.beneish_m8_score, 2),
          ])}
        />

        <EChartBox
          title="Diễn biến lịch sử Xác suất kiệt quệ mô hình — Nhấp kỳ để cập nhật TreeSHAP"
          subtitle="Đơn vị: phần trăm (%) · Xác suất kiệt quệ 1 năm đã hiệu chỉnh (1Y PD)"
          option={mlDistressProbOption}
          height={290}
          isEmpty={sortedMl.length === 0}
          onChartClick={onRiskPeriodClick}
          tableHeaders={["Kỳ", "1Y PD", "Bậc", "F-Score", "Beneish M"]}
          tableRows={sortedMl.map((m) => [
            formatPeriodLabel(m.period_end_date, periodType),
            formatPercent(m.calibrated_distress_prob),
            m.risk_tier ?? "N/A",
            m.piotroski_f_score !== null && m.piotroski_f_score !== undefined
              ? `${m.piotroski_f_score}/9`
              : "N/A",
            formatNumber(m.beneish_m8_score, 2),
          ])}
        />
      </div>

      <ExplainWaterfallChart
        shap={activeMl?.shap_top_drivers}
        distressProb={activeMl?.calibrated_distress_prob}
        periodLabel={activeMlPeriodLabel}
        isCustomPeriod={isCustomMlPeriod}
        onResetToLatest={onResetToLatest}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader className="flex-wrap gap-2 py-2.5">
            <CardTitle className="text-[14px]">
              Bảng chi tiết đóng góp TreeSHAP{" "}
              {activeMlPeriodLabel ? `(${activeMlPeriodLabel})` : "(Kỳ đang chọn)"}
            </CardTitle>
            {isCustomMlPeriod ? (
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={onResetToLatest}
              >
                <RotateCcw className="size-3" />
                <span>Về kỳ mới nhất</span>
              </Button>
            ) : null}
          </CardHeader>
          <CardContent className="p-0">
            {!activeMl?.shap_top_drivers ? (
              <p className="p-4 text-xs text-ink-muted">
                Chưa có dữ liệu đóng góp TreeSHAP cho kỳ đang chọn.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                      <th scope="col" className="px-3 py-2 text-left font-semibold">Yếu tố</th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">Giá trị DN</th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">Trung vị ngành</th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">Đóng góp (log-odds)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ...activeMl.shap_top_drivers.top_risk_increasing,
                      ...activeMl.shap_top_drivers.top_risk_decreasing,
                    ].map((drv) => {
                      const ratioFallback = ratioDictMap.get(
                        drv.feature as RatioName,
                      )?.display_name_vn;
                      const driverLabel =
                        drv.display_name_vn &&
                        drv.display_name_vn !== drv.feature
                          ? drv.display_name_vn
                          : (ratioFallback ?? getMlFeatureLabel(drv.feature));
                      return (
                        <tr
                          key={drv.feature}
                          className="border-b border-border/60 hover:bg-surface-hover/40"
                        >
                          <td className="px-3 py-2 font-medium text-ink">
                            {driverLabel}
                          </td>
                          <td className="num px-3 py-2 text-right">
                            {formatNumber(drv.value, 3)}
                          </td>
                          <td className="num px-3 py-2 text-right text-ink-muted">
                            {formatNumber(drv.peer_median, 3)}
                          </td>
                          <td
                            className={cn(
                              "num px-3 py-2 text-right font-semibold",
                              drv.shap_delta >= 0 ? "text-risk-high" : "text-risk-low",
                            )}
                          >
                            {drv.shap_delta >= 0 ? "+" : MINUS_SIGN}
                            {formatNumber(Math.abs(drv.shap_delta), 3)}
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

        <Card className="lg:col-span-5">
          <CardHeader className="py-2.5">
            <CardTitle className="text-[14px]">
              Phân cụm đặc trưng &amp; Quản trị mô hình
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 p-3.5 text-xs">
            <dl className="space-y-2">
              <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                <dt className="text-ink-muted">Nhóm cấu trúc tài chính</dt>
                <dd className="text-right font-medium text-ink">
                  {activeMl?.cluster_archetype_name ?? "Chưa phân cụm"}
                </dd>
              </div>
              <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                <dt className="text-ink-muted">Phân vị bất thường thống kê</dt>
                <dd className="num text-right font-medium text-ink">
                  {activeMl?.anomaly_percentile_rank !== null &&
                  activeMl?.anomaly_percentile_rank !== undefined
                    ? `${formatNumber(activeMl.anomaly_percentile_rank, 1)}%`
                    : "N/A"}
                </dd>
              </div>
              <div className="flex justify-between gap-2 border-b border-border/60 pb-1.5">
                <dt className="text-ink-muted">Phiên bản mô hình</dt>
                <dd className="font-mono text-right text-ink">
                  {activeMl?.model_version ?? "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-2 pb-1">
                <dt className="text-ink-muted">Vai trò điểm số</dt>
                <dd className="text-right text-ink">
                  {(activeMl?.model_role &&
                    MODEL_ROLE_LABELS[activeMl.model_role]) ??
                    "Đánh giá định lượng"}
                </dd>
              </div>
            </dl>
            <p className="rounded-md border border-border bg-canvas p-2.5 text-[11px] leading-4 text-ink-muted">
              Phạm vi áp dụng: Mô hình đánh giá doanh nghiệp phi tài chính niêm yết (không dùng so sánh trực tiếp ngân hàng/bảo hiểm).
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
