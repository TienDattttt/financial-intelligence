"use client";

import * as React from "react";
import { Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { formatFormulaVi, RATIO_CATEGORY_LABELS } from "@/lib/domain-labels";
import { formatRatioByType, normalizeSearchText } from "@/lib/format";
import type {
  PeriodType,
  RatioDictionaryItem,
  RatioName,
  StatementScope,
} from "@/types/api";

export function SourcesTab({
  ticker,
  ratios,
  latestRatioRow,
  lineageFilter,
  onChangeLineageFilter,
  statementScope,
  periodType,
  periodEndDate,
  onOpenSourceDrawer,
}: {
  ticker: string;
  ratios: readonly RatioDictionaryItem[];
  latestRatioRow: Record<string, unknown> | null;
  lineageFilter: string;
  onChangeLineageFilter: (next: string) => void;
  statementScope: StatementScope;
  periodType: PeriodType;
  periodEndDate: string | null;
  onOpenSourceDrawer: (req: {
    ticker: string;
    ratioName: RatioName;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate: string | null;
  }) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <div>
          <CardTitle className="text-[14px]">
            Truy xuất nguồn gốc {ratios.length || 38} tỷ số tài chính
          </CardTitle>
          <p className="mt-0.5 text-xs text-ink-muted">
            Nhấn Truy nguồn để xem tử số, mẫu số, mã hồ sơ gốc và hàm băm kiểm chứng dữ liệu
          </p>
        </div>
        <Input
          value={lineageFilter}
          onChange={(e) => onChangeLineageFilter(e.target.value)}
          placeholder="Lọc theo tên chỉ số..."
          className="h-7 w-56 text-xs"
          aria-label="Lọc danh sách tỷ số để truy nguồn"
        />
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                <th scope="col" className="px-3 py-2 text-left font-semibold">Chỉ số</th>
                <th scope="col" className="px-3 py-2 text-left font-semibold">Nhóm</th>
                <th scope="col" className="px-3 py-2 text-left font-semibold">Công thức</th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">Giá trị kỳ mới nhất</th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">Kiểm chứng</th>
              </tr>
            </thead>
            <tbody>
              {ratios
                .filter((r) => {
                  const q = normalizeSearchText(lineageFilter);
                  if (!q) return true;
                  return (
                    normalizeSearchText(r.display_name_vn).includes(q) ||
                    normalizeSearchText(r.ratio_name).includes(q)
                  );
                })
                .map((r) => {
                  const raw = latestRatioRow?.[r.ratio_name];
                  const val =
                    typeof raw === "number" && Number.isFinite(raw) ? raw : null;
                  return (
                    <tr
                      key={r.ratio_name}
                      className="border-b border-border/60 hover:bg-surface-hover/40"
                    >
                      <td className="px-3 py-2 font-medium text-ink">
                        {r.display_name_vn}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {RATIO_CATEGORY_LABELS[r.category] ?? r.category}
                      </td>
                      <td className="px-3 py-2 text-xs text-ink-muted">
                        {formatFormulaVi(r.formula_display)}
                      </td>
                      <td className="num px-3 py-2 text-right font-medium text-ink">
                        {formatRatioByType(val, r.format_type)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            onOpenSourceDrawer({
                              ticker,
                              ratioName: r.ratio_name,
                              statementScope,
                              periodType,
                              periodEndDate:
                                (latestRatioRow?.period_end_date as string | undefined) ??
                                periodEndDate,
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
      </CardContent>
    </Card>
  );
}
