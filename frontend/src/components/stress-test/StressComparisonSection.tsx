"use client";

import Link from "next/link";
import { Download, Pin, PinOff } from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatVndBillions,
  MINUS_SIGN,
} from "@/lib/format";
import type { computeScenarioComparison } from "@/lib/stress-test-helpers";
import { cn } from "@/lib/utils";
import type { StressTestResponse } from "@/types/api";

type StressComparison = ReturnType<typeof computeScenarioComparison>;

function formatSignedInteger(delta: number): string {
  if (delta === 0) return "0";
  const absFormatted = formatNumber(Math.abs(delta), 0);
  return delta < 0 ? `${MINUS_SIGN}${absFormatted}` : `+${absFormatted}`;
}

export function formatSignedBillionsDelta(deltaVnd: number): string {
  if (!Number.isFinite(deltaVnd)) return "N/A";
  const billions = deltaVnd / 1_000_000_000;
  if (Math.abs(billions) < 0.05) return "0,0 tỷ";
  const absFormatted = formatNumber(Math.abs(billions), 1);
  return billions < 0
    ? `${MINUS_SIGN}${absFormatted} tỷ`
    : `+${absFormatted} tỷ`;
}

export function StressComparisonSummaryCard({
  comparison,
  activeResult,
  activeIsCalculating,
  onPinA,
  onUnpinA,
}: {
  comparison: StressComparison;
  activeResult: StressTestResponse | null;
  activeIsCalculating: boolean;
  onPinA: (res: StressTestResponse) => void;
  onUnpinA: () => void;
}) {
  return (
    <Card className="border-accent-ink/40">
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div>
          <CardTitle className="text-[14px]">
            So sánh song song: Kịch bản A vs Kịch bản B
          </CardTitle>
          <p className="mt-0.5 text-xs text-ink-muted">
            Chênh lệch được tính theo chiều (Kịch bản B − Kịch bản A)
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={!activeResult || activeIsCalculating}
            onClick={() => activeResult && onPinA(activeResult)}
          >
            <Pin className="size-3" />
            Cập nhật A
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={onUnpinA}
          >
            <PinOff className="size-3" />
            Bỏ ghim A
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-3.5">
        {comparison.scopeMismatch ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-risk-mid/35 bg-risk-mid-soft px-3 py-2 text-xs text-risk-mid">
            <span>
              Lưu ý: Kịch bản A được ghim ở phạm vi hoặc tập DN khác với Kịch bản B hiện tại. Nên cập nhật A để so sánh trên cùng tập mẫu.
            </span>
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={!activeResult || activeIsCalculating}
              onClick={() => activeResult && onPinA(activeResult)}
            >
              Đồng bộ A theo phạm vi mới
            </Button>
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-border bg-canvas/60 p-3">
            <p className="text-[11px] font-semibold text-accent-ink">
              Kịch bản A (Đã ghim): {comparison.scenarioAName}
            </p>
            <p className="num mt-1.5 text-lg font-semibold text-ink">
              {formatPercent(comparison.debtAtRiskPctA)} nợ rủi ro
            </p>
            <p className="num mt-0.5 text-xs text-ink-muted">
              Quy mô:{" "}
              {formatVndBillions(comparison.debtAtRiskVndA, {
                decimals: 1,
                zeroAsZero: true,
              })}{" "}
              tỷ · {formatNumber(comparison.newlyDistressedCountA, 0)}{" "}
              DN mới khó
            </p>
          </div>

          <div className="rounded-lg border border-border bg-canvas/60 p-3">
            <p className="text-[11px] font-semibold text-risk-high">
              Kịch bản B (Hiện tại): {comparison.scenarioBName}
            </p>
            <p className="num mt-1.5 text-lg font-semibold text-ink">
              {formatPercent(comparison.debtAtRiskPctB)} nợ rủi ro
            </p>
            <p className="num mt-0.5 text-xs text-ink-muted">
              Quy mô:{" "}
              {formatVndBillions(comparison.debtAtRiskVndB, {
                decimals: 1,
                zeroAsZero: true,
              })}{" "}
              tỷ · {formatNumber(comparison.newlyDistressedCountB, 0)}{" "}
              DN mới khó
            </p>
          </div>

          <div className="rounded-lg border border-border bg-surface-muted/60 p-3">
            <p className="text-[11px] font-semibold text-ink">
              Chênh lệch (B − A)
            </p>
            <p
              className={cn(
                "num mt-1.5 text-lg font-semibold",
                comparison.debtAtRiskPctDelta > 0.0005
                  ? "text-risk-high"
                  : comparison.debtAtRiskPctDelta < -0.0005
                    ? "text-risk-low"
                    : "text-ink",
              )}
            >
              {formatPercentPoints(comparison.debtAtRiskPctDelta, 1)}
            </p>
            <p className="num mt-0.5 text-xs text-ink-muted">
              Nợ rủi ro:{" "}
              {formatSignedBillionsDelta(comparison.debtAtRiskVndDelta)}{" "}
              · DN mới:{" "}
              {formatSignedInteger(comparison.newlyDistressedCountDelta)}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function StressComparisonDeltaCard({
  comparison,
  compareTab,
  setCompareTab,
  filteredCompareIssuers,
  filteredCompareSectors,
  selectedSectorName,
  onClearSector,
  onExportCompareSectorsCsv,
}: {
  comparison: StressComparison;
  compareTab: "ISSUERS" | "SECTORS";
  setCompareTab: (tab: "ISSUERS" | "SECTORS") => void;
  filteredCompareIssuers: StressComparison["issuerDeltas"];
  filteredCompareSectors: StressComparison["sectorDeltas"];
  selectedSectorName: string | null;
  onClearSector: () => void;
  onExportCompareSectorsCsv: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setCompareTab("ISSUERS")}
            className={cn(
              "rounded px-2.5 py-1 font-medium",
              compareTab === "ISSUERS"
                ? "bg-primary text-primary-foreground"
                : "text-ink-muted hover:text-ink",
            )}
          >
            Chênh lệch DN suy thoái mới ({comparison.addedDistressedCount}{" "}
            thêm ở B · {comparison.resolvedDistressedCount} giảm ở B)
          </button>
          <button
            type="button"
            onClick={() => setCompareTab("SECTORS")}
            className={cn(
              "rounded px-2.5 py-1 font-medium",
              compareTab === "SECTORS"
                ? "bg-primary text-primary-foreground"
                : "text-ink-muted hover:text-ink",
            )}
          >
            Chênh lệch theo ngành ({filteredCompareSectors.length})
          </button>
        </div>

        <div className="flex items-center gap-2">
          {selectedSectorName ? (
            <Button
              type="button"
              variant="secondary"
              size="xs"
              onClick={onClearSector}
            >
              Đang lọc: {selectedSectorName} ×
            </Button>
          ) : null}
          {compareTab === "SECTORS" ? (
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={filteredCompareSectors.length === 0}
              onClick={onExportCompareSectorsCsv}
            >
              <Download className="size-3" />
              Xuất CSV ngành A vs B
            </Button>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="p-0">
        {compareTab === "ISSUERS" ? (
          filteredCompareIssuers.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="Không có khác biệt DN suy thoái mới giữa Kịch bản A và B"
                description="Cả hai kịch bản ghi nhận cùng tập doanh nghiệp mới chuyển sang ICR < 1 (hoặc chưa có DN nào chuyển sang trạng thái suy thoái mới)."
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th scope="col" className="px-3 py-2 text-left font-semibold">
                      Mã
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold">
                      Ngành
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold">
                      Biến động trạng thái (A → B)
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">
                      Tổng nợ (tỷ)
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">
                      ICR Cơ sở
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">
                      ICR Kịch bản A
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">
                      ICR Kịch bản B
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCompareIssuers.map((it) => (
                    <tr
                      key={it.ticker}
                      className="border-b border-border/60 hover:bg-surface-muted/40"
                    >
                      <td className="px-3 py-2">
                        <Link
                          href={`/companies/${it.ticker}`}
                          className="font-mono font-semibold text-accent-ink hover:underline"
                        >
                          {it.ticker}
                        </Link>
                        <div className="max-w-[180px] truncate text-[11px] text-ink-muted">
                          {it.companyNameVn}
                        </div>
                      </td>
                      <td className="max-w-[150px] truncate px-3 py-2 text-ink-muted">
                        {it.icbNameLevel2}
                      </td>
                      <td className="px-3 py-2">
                        <RiskBadge
                          tone={
                            it.status === "ADDED_IN_B"
                              ? "high"
                              : it.status === "REMOVED_IN_B"
                                ? "low"
                                : "neutral"
                          }
                          label={
                            it.status === "ADDED_IN_B"
                              ? "Mới phát sinh ở B"
                              : it.status === "REMOVED_IN_B"
                                ? "Thoát suy thoái ở B"
                                : "Cả A và B"
                          }
                        />
                      </td>
                      <td className="num px-3 py-2 text-right">
                        {formatVndBillions(it.totalDebt, { decimals: 0 })}
                      </td>
                      <td className="num px-3 py-2 text-right text-ink-muted">
                        {formatMultiple(it.icrBaseline)}
                      </td>
                      <td className="num px-3 py-2 text-right">
                        {formatMultiple(it.icrA)}
                      </td>
                      <td className="num px-3 py-2 text-right font-semibold">
                        <span
                          className={
                            (it.icrB ?? 0) < 1
                              ? "text-risk-high"
                              : "text-ink"
                          }
                        >
                          {formatMultiple(it.icrB)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                  <th scope="col" className="px-3 py-2 text-left font-semibold">
                    Ngành (ICB cấp 2)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Kịch bản A (%)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Kịch bản B (%)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Chênh lệch (B−A)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Nợ rủi ro B−A (tỷ)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    DN mới khó (A → B)
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredCompareSectors.map((s) => (
                  <tr
                    key={s.icbCodeLevel2}
                    className="border-b border-border/60 hover:bg-surface-muted/40"
                  >
                    <td className="px-3 py-2 font-medium text-ink">
                      {s.icbNameLevel2}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {formatPercent(s.debtAtRiskPctA)}
                    </td>
                    <td className="num px-3 py-2 text-right font-semibold text-ink">
                      {formatPercent(s.debtAtRiskPctB)}
                    </td>
                    <td
                      className={cn(
                        "num px-3 py-2 text-right font-medium",
                        s.debtAtRiskPctDelta > 0.0005
                          ? "text-risk-high"
                          : s.debtAtRiskPctDelta < -0.0005
                            ? "text-risk-low"
                            : "text-ink-muted",
                      )}
                    >
                      {formatPercentPoints(s.debtAtRiskPctDelta, 1)}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {formatSignedBillionsDelta(s.debtAtRiskVndDelta)}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {s.newlyDistressedA} → {s.newlyDistressedB} (
                      {formatSignedInteger(s.newlyDistressedDelta)})
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
