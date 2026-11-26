"use client";

import Link from "next/link";
import { RiskTierBadge } from "@/components/financial/RiskBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CompareEntry, CompareMatrixData } from "@/lib/compare-helpers";
import { formatCreditTrio } from "@/lib/domain-labels";
import { formatNumber, formatPeriodLabel, formatRatioValue } from "@/lib/format";
import type { PeriodType } from "@/types/api";

export function CompareMatrixCard({
  activeTickers,
  activeEntries,
  matrixData,
  matrixMode,
  onMatrixModeChange,
  periodEndDate,
  periodType,
}: {
  activeTickers: string[];
  activeEntries: CompareEntry[];
  matrixData: CompareMatrixData;
  matrixMode: "VALUE" | "PERCENTILE";
  onMatrixModeChange: (mode: "VALUE" | "PERCENTILE") => void;
  periodEndDate: string | null;
  periodType: PeriodType;
}) {
  return (
    <Card className="rounded-lg shadow-xs">
      <CardHeader className="py-2.5">
        <CardTitle className="text-[14px]">
          Ma trận chỉ số sức khỏe tài chính &amp; định lượng
          {periodEndDate
            ? ` (${formatPeriodLabel(periodEndDate, periodType)})`
            : " kỳ mới nhất"}
        </CardTitle>
        <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
          <button
            type="button"
            onClick={() => onMatrixModeChange("VALUE")}
            className={
              matrixMode === "VALUE"
                ? "rounded-md bg-primary px-2.5 py-1 font-medium text-primary-foreground shadow-xs"
                : "rounded-md px-2.5 py-1 text-ink-muted transition-colors duration-150 hover:text-ink"
            }
          >
            Giá trị tuyệt đối
          </button>
          <button
            type="button"
            onClick={() => onMatrixModeChange("PERCENTILE")}
            className={
              matrixMode === "PERCENTILE"
                ? "rounded-md bg-primary px-2.5 py-1 font-medium text-primary-foreground shadow-xs"
                : "rounded-md px-2.5 py-1 text-ink-muted transition-colors duration-150 hover:text-ink"
            }
          >
            Phân vị ngành (%)
          </button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                <th
                  scope="col"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface-muted px-3 py-2 text-left font-semibold md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Chỉ tiêu
                </th>
                {activeTickers.map((t) => (
                  <th key={t} scope="col" className="min-w-[120px] px-3 py-2 text-right">
                    <Link
                      href={`/companies/${t}`}
                      className="font-mono text-sm font-semibold text-accent-ink hover:underline"
                    >
                      {t}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-border/60 bg-surface-muted/30">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface-muted/60 px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Altman Z″
                </th>
                {matrixData.latestMl.map((m, i) => (
                  <td key={activeTickers[i]} className="num px-3 py-2 text-right font-medium">
                    {formatNumber(m?.altman_z_double_prime, 2)}
                  </td>
                ))}
              </tr>
              <tr className="border-b border-border/60">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Piotroski F-Score
                </th>
                {matrixData.latestMl.map((m, i) => (
                  <td key={activeTickers[i]} className="num px-3 py-2 text-right">
                    {m?.piotroski_f_score !== null && m?.piotroski_f_score !== undefined
                      ? `${m.piotroski_f_score}/9`
                      : "N/A"}
                  </td>
                ))}
              </tr>
              <tr className="border-b border-border/60">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Beneish M-Score
                </th>
                {matrixData.latestMl.map((m, i) => (
                  <td key={activeTickers[i]} className="num px-3 py-2 text-right">
                    {formatNumber(m?.beneish_m8_score, 2)}
                  </td>
                ))}
              </tr>
              <tr className="border-b border-border/60">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Bậc rủi ro (R1–R5)
                </th>
                {matrixData.latestMl.map((m, i) => (
                  <td key={activeTickers[i]} className="px-3 py-2 text-right">
                    <div className="flex justify-end">
                      <RiskTierBadge riskTier={m?.risk_tier} />
                    </div>
                  </td>
                ))}
              </tr>
              <tr className="border-b border-border/60">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Vị thế rủi ro cùng kỳ
                </th>
                {matrixData.latestMl.map((m, i) => {
                  const trio = formatCreditTrio({
                    risk_tier: m?.risk_tier,
                    risk_market_percentile: m?.risk_market_percentile,
                    calibrated_distress_prob: m?.calibrated_distress_prob,
                    relative_risk_lift: m?.relative_risk_lift,
                    market_base_rate: m?.market_base_rate,
                  });
                  return (
                    <td key={activeTickers[i]} className="num px-3 py-2 text-right text-ink-muted">
                      {trio.topPercentLabel}
                    </td>
                  );
                })}
              </tr>
              <tr className="border-b border-border/60">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Xác suất kiệt quệ 1 năm (so với TB)
                </th>
                {matrixData.latestMl.map((m, i) => {
                  const trio = formatCreditTrio({
                    risk_tier: m?.risk_tier,
                    risk_market_percentile: m?.risk_market_percentile,
                    calibrated_distress_prob: m?.calibrated_distress_prob,
                    relative_risk_lift: m?.relative_risk_lift,
                    market_base_rate: m?.market_base_rate,
                  });
                  return (
                    <td key={activeTickers[i]} className="num px-3 py-2 text-right font-medium">
                      <span>{trio.pdLabel}</span>
                      {trio.liftLabel ? (
                        <span className="ml-1.5 text-[11px] font-normal text-ink-muted">
                          ({trio.liftLabel})
                        </span>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
              <tr className="border-b border-border bg-surface-muted/30">
                <th
                  scope="row"
                  className="min-w-[140px] max-w-[165px] border-r border-border bg-surface-muted/60 px-3 py-2 text-left font-semibold text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                >
                  Số cờ đỏ đang kích hoạt
                </th>
                {matrixData.latestFlagsCount.map((cnt, i) => (
                  <td
                    key={activeTickers[i]}
                    className={`num px-3 py-2 text-right font-medium ${cnt >= 2 ? "text-risk-high" : "text-ink"}`}
                  >
                    {cnt}
                  </td>
                ))}
              </tr>

              {Array.from(matrixData.ratioList.entries()).map(([metricName, meta]) => {
                const rowItems = activeEntries.map(({ summaryQuery }) =>
                  summaryQuery?.data?.items.find((x) => x.metric_name === metricName),
                );
                const numericVals = rowItems.map((item) =>
                  !item
                    ? null
                    : matrixMode === "PERCENTILE"
                      ? typeof item.percentile_rank === "number" &&
                        Number.isFinite(item.percentile_rank)
                        ? item.percentile_rank
                        : null
                      : typeof item.company_value === "number" &&
                          Number.isFinite(item.company_value)
                        ? item.company_value
                        : null,
                );
                const validNums = numericVals.filter((v): v is number => v !== null);
                const canHighlight = validNums.length >= 2;
                const maxVal = canHighlight ? Math.max(...validNums) : null;
                const minVal = canHighlight ? Math.min(...validNums) : null;
                const hasSpread = maxVal !== null && minVal !== null && maxVal !== minVal;

                return (
                  <tr
                    key={metricName}
                    className="border-b border-border/60 hover:bg-surface-hover/40"
                  >
                    <th
                      scope="row"
                      className="min-w-[140px] max-w-[165px] border-r border-border bg-surface px-3 py-2 text-left font-normal text-ink md:sticky md:left-0 md:z-10 md:min-w-[230px] md:max-w-none"
                    >
                      {meta.name}
                    </th>
                    {activeTickers.map((t, idx) => {
                      const item = rowItems[idx];
                      const val = numericVals[idx];
                      const display = !item
                        ? "N/A"
                        : matrixMode === "PERCENTILE"
                          ? item.percentile_rank !== null &&
                            item.percentile_rank !== undefined
                            ? `${formatNumber(item.percentile_rank, 0)}%`
                            : "N/A"
                          : formatRatioValue(item.company_value, item.format_type);

                      const isBest =
                        hasSpread &&
                        val !== null &&
                        ((meta.direction === "HIGHER_IS_BETTER" && val === maxVal) ||
                          (meta.direction === "LOWER_IS_BETTER" && val === minVal));
                      const isWorst =
                        hasSpread &&
                        val !== null &&
                        ((meta.direction === "HIGHER_IS_BETTER" && val === minVal) ||
                          (meta.direction === "LOWER_IS_BETTER" && val === maxVal));

                      return (
                        <td
                          key={t}
                          className={`num px-3 py-2 text-right ${
                            isBest
                              ? "bg-risk-low-soft/35 font-semibold text-risk-low"
                              : isWorst
                                ? "bg-risk-high-soft/25 text-risk-high"
                                : "text-ink"
                          }`}
                        >
                          {display}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
