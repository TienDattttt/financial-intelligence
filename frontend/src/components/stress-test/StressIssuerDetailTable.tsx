"use client";

import Link from "next/link";
import { Download } from "lucide-react";
import { EmptyState } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatMultiple, formatVndBillions } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StressIssuerItem, StressTestResponse } from "@/types/api";

export function StressIssuerDetailTable({
  activeResult,
  activeTab,
  setActiveTab,
  issuerList,
  selectedSectorName,
  onClearSector,
  onExportIssuersCsv,
}: {
  activeResult: StressTestResponse;
  activeTab: "NEWLY" | "TOP";
  setActiveTab: (tab: "NEWLY" | "TOP") => void;
  issuerList: StressIssuerItem[];
  selectedSectorName: string | null;
  onClearSector: () => void;
  onExportIssuersCsv: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setActiveTab("NEWLY")}
            className={cn(
              "rounded px-2.5 py-1 font-medium",
              activeTab === "NEWLY"
                ? "bg-primary text-primary-foreground"
                : "text-ink-muted hover:text-ink",
            )}
          >
            {(() => {
              const totalNewly =
                activeResult.total_newly_distressed ??
                activeResult.newly_distressed_count;
              const shownNewly = activeResult.newly_distressed_issuers.length;
              return totalNewly > shownNewly
                ? `DN mới gặp khó (Top ${shownNewly} / ${totalNewly})`
                : `DN mới gặp khó (${totalNewly})`;
            })()}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("TOP")}
            className={cn(
              "rounded px-2.5 py-1 font-medium",
              activeTab === "TOP"
                ? "bg-primary text-primary-foreground"
                : "text-ink-muted hover:text-ink",
            )}
          >
            Nợ rủi ro lớn nhất ({activeResult.top_stressed_issuers.length})
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
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={issuerList.length === 0}
            onClick={onExportIssuersCsv}
          >
            <Download className="size-3" />
            Xuất CSV
          </Button>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        {issuerList.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="Không có doanh nghiệp trong nhóm này"
              description="Thử tăng mức cú sốc vĩ mô hoặc bỏ bộ lọc ngành để xem danh sách."
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
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Tổng nợ (tỷ)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    ICR (Cơ sở → Sốc)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    EBIT TTM (tỷ)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    CFO TTM (tỷ)
                  </th>
                </tr>
              </thead>
              <tbody>
                {issuerList.map((it) => (
                  <tr
                    key={it.issuer_id}
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
                        {it.company_name_vn ?? ""}
                      </div>
                    </td>
                    <td className="max-w-[150px] truncate px-3 py-2 text-ink-muted">
                      {it.icb_name_level2 ?? "—"}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {formatVndBillions(it.total_debt, { decimals: 0 })}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      <span className="text-ink-muted">
                        {formatMultiple(it.interest_coverage)} →{" "}
                      </span>
                      <strong
                        className={
                          (it.stressed_interest_coverage ?? 0) < 1
                            ? "text-risk-high"
                            : "text-ink"
                        }
                      >
                        {formatMultiple(it.stressed_interest_coverage)}
                      </strong>
                    </td>
                    <td className="num px-3 py-2 text-right">
                      <span className="text-ink-muted">
                        {formatVndBillions(it.ebit_ttm, {
                          accountingNegative: true,
                        })}{" "}
                        →{" "}
                      </span>
                      <span>
                        {formatVndBillions(it.stressed_ebit_ttm, {
                          accountingNegative: true,
                        })}
                      </span>
                    </td>
                    <td className="num px-3 py-2 text-right">
                      <span className="text-ink-muted">
                        {formatVndBillions(it.cfo_ttm, {
                          accountingNegative: true,
                        })}{" "}
                        →{" "}
                      </span>
                      <span>
                        {formatVndBillions(it.stressed_cfo_ttm, {
                          accountingNegative: true,
                        })}
                      </span>
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
