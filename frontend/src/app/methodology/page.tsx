"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import {
  useMetaDictionary,
  useQualityQuarantine,
  useQualitySummary,
  useSnapshotOverview,
} from "@/hooks/use-finintel-queries";
import { RATIO_CATEGORY_LABELS, SEVERITY_META } from "@/lib/domain-labels";
import {
  formatDateVi,
  formatNumber,
  formatPeriodLabel,
  normalizeSearchText,
} from "@/lib/format";
import { cn } from "@/lib/utils";

type MethodTab = "dictionary" | "models" | "quality";

const DIRECTION_LABELS: Record<string, string> = {
  HIGHER_IS_BETTER: "Cao hơn tích cực hơn",
  LOWER_IS_BETTER: "Thấp hơn tích cực hơn",
  NEUTRAL: "Trung tính (phụ thuộc bối cảnh)",
};

function MethodologyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { snapshotId } = useAppState();

  const rawTab = searchParams.get("tab");
  const activeTab: MethodTab =
    rawTab === "models" || rawTab === "quality" ? rawTab : "dictionary";

  const [dictSearch, setDictSearch] = React.useState("");
  const [catFilter, setCatFilter] = React.useState("");
  const [qOffset, setQOffset] = React.useState(0);
  const [quarOffset, setQuarOffset] = React.useState(0);

  const dictQuery = useMetaDictionary(snapshotId);
  const overviewQuery = useSnapshotOverview(snapshotId);
  const summaryQuery = useQualitySummary(25, qOffset, snapshotId);
  const quarantineQuery = useQualityQuarantine(25, quarOffset, snapshotId);

  const setTab = (tab: MethodTab) => {
    router.replace(tab === "dictionary" ? "/methodology" : `/methodology?tab=${tab}`, {
      scroll: false,
    });
  };

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
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Dữ liệu, Từ điển chỉ số &amp; Phương pháp mô hình
        </h1>
        <p className="mt-0.5 text-xs text-ink-muted">
          Tài liệu minh bạch công thức 38 tỷ số tài chính, 9 quy tắc kế toán điều tra, mô hình định lượng và kiểm tra chất lượng dữ liệu
        </p>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-border">
        {(
          [
            ["dictionary", "Từ điển chỉ số & Cờ đỏ"],
            ["models", "Phương pháp mô hình"],
            ["quality", "Chất lượng dữ liệu"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={cn(
              "-mb-px border-b-2 px-3.5 py-2 text-[13px] font-medium transition-colors",
              activeTab === key
                ? "border-accent-ink font-semibold text-accent-ink"
                : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {label}
          </button>
        ))}
      </div>

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
                  38 tỷ số tài chính chuẩn hóa ({filteredRatios.length})
                </CardTitle>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    value={dictSearch}
                    onChange={(e) => setDictSearch(e.target.value)}
                    placeholder="Tìm tên chỉ số hoặc công thức..."
                    className="h-7 w-56 text-xs"
                    aria-label="Tìm trong từ điển chỉ số"
                  />
                  <Select
                    value={catFilter}
                    onChange={(e) => setCatFilter(e.target.value)}
                    className="h-7 text-xs"
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
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
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
                            <div className="font-mono text-[11px] text-ink-subtle">
                              {r.ratio_name}
                            </div>
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {RATIO_CATEGORY_LABELS[r.category] ?? r.category}
                          </td>
                          <td className="px-3 py-2 font-mono text-[11px] text-ink">
                            {r.formula_display}
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
                  9 quy tắc phát hiện bất thường kế toán (RF_01 – RF_09)
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
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
                              {rf.flag_code.split("_").slice(0, 2).join("_")}
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Altman Z″ (Emerging Markets)</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
              <p>
                Công thức điều chỉnh cho doanh nghiệp phi tài chính tại thị trường mới nổi: sử dụng vốn lưu động ròng, lợi nhuận giữ lại, EBIT và tỷ lệ VCSH / Tổng nợ phải trả.
              </p>
              <p className="text-xs text-ink">
                Ngưỡng phân vùng: <strong>&gt; 2,60</strong> An toàn · <strong>1,10–2,60</strong> Vùng xám · <strong>&lt; 1,10</strong> Nguy cơ kiệt quệ.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Piotroski F-Score (Thang 9 điểm)</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
              <p>
                Đo lường 9 tiêu chí nhị phân qua 3 trụ cột: Sinh lời (ROA, CFO, ΔROA, Dồn tích), Đòn bẩy &amp; Thanh khoản (ΔNợ dài hạn, ΔHệ số thanh toán, Không phát hành pha loãng) và Hiệu quả (ΔBiên gộp, ΔVòng quay TS).
              </p>
              <p className="text-xs text-ink">
                Hệ thống ghi nhận rõ số tiêu chí có dữ liệu hợp lệ (ví dụ: 7/9 tiêu chí) để tránh suy diễn sai khi thiếu kỳ so sánh.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Beneish M-Score &amp; Chuyển chuẩn mực TT200→TT99</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
              <p>
                Mô hình 8 biến của Beneish (1999) sàng lọc dấu hiệu điều chỉnh lợi nhuận. Khi M-Score &gt; −1,78, doanh nghiệp được gắn cờ cần kiểm tra kỹ phải thu, khấu hao và dồn tích.
              </p>
              <p className="text-xs text-ink">
                Đối với giai đoạn giao thời chuẩn mực kế toán VAS TT200/2014 sang TT99/2025 (từ năm 2026), hệ thống tự động tách cờ chuyển chuẩn mực thay vì quy kết điều chỉnh lợi nhuận.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Core Earnings &amp; Stress Test IMF WP/15/216</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
              <p>
                Tách lợi nhuận hoạt động cốt lõi (Rouen, So &amp; Wang 2021) nhằm phát hiện doanh nghiệp phụ thuộc thu nhập tài chính hoặc thu nhập bất thường.
              </p>
              <p>
                Mô phỏng căng thẳng dòng tiền và lãi vay theo phương pháp Chow (2015, IMF WP/15/216): xác định tỷ trọng nợ vay rơi vào trạng thái khả năng trả lãi ICR &lt; 1,0x sau cú sốc.
              </p>
            </CardContent>
          </Card>

          <Card className="md:col-span-2">
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Mô hình Xác suất kiệt quệ (Calibrated TreeSHAP) &amp; Giới hạn phạm vi
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
              <p>
                Xác suất kiệt quệ được tính từ mô hình phân loại có hiệu chỉnh xác suất (calibrated probability) kết hợp giải thích TreeSHAP theo từng kỳ báo cáo (đóng góp biểu diễn ở không gian log-odds trước hàm liên kết).
              </p>
              <p className="text-xs text-ink">
                <strong>Giới hạn phạm vi:</strong> Hệ thống áp dụng thống nhất chuỗi tỷ số và chấm điểm Altman/Piotroski/Beneish cho nhóm <strong>doanh nghiệp phi tài chính niêm yết</strong>; các tổ chức tín dụng, bảo hiểm và chứng khoán sử dụng chuẩn BCTC đặc thù nên không chấm điểm ngang hàng. Toàn bộ dữ liệu tuân thủ kỷ luật thời điểm công bố (Point-in-Time), không dùng thông tin tương lai.
              </p>
            </CardContent>
          </Card>
        </div>
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
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ["Tổng số DN", formatNumber(overviewQuery.data.total_issuers, 0)],
                ["Tổng số kỳ tỷ số", formatNumber(overviewQuery.data.total_ratio_periods, 0)],
                ["Kỳ đạt chuẩn", formatNumber(overviewQuery.data.pass_periods, 0)],
                ["Kỳ có cảnh báo", formatNumber(overviewQuery.data.warning_periods, 0)],
                ["Bản ghi loại trừ kiểm tra", formatNumber(overviewQuery.data.quarantined_rows, 0)],
              ].map(([lbl, val]) => (
                <Card key={lbl}>
                  <CardContent className="p-3">
                    <p className="text-[11px] text-ink-muted">{lbl}</p>
                    <p className="num mt-1 text-lg font-semibold text-ink">{val}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : null}

          <Card>
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">
                Nhật ký kiểm tra chất lượng theo kỳ ({summaryQuery.data?.total ?? 0})
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
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-xs">
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
                        {summaryQuery.data?.items.map((row, idx) => (
                          <tr
                            key={`${row.issuer_id}-${row.period_end_date}-${idx}`}
                            className="border-b border-border/60"
                          >
                            <td className="px-3 py-2 font-mono text-ink">{row.issuer_id}</td>
                            <td className="px-3 py-2">
                              {formatPeriodLabel(row.period_end_date, row.period_type)}
                            </td>
                            <td className="px-3 py-2 text-ink-muted">{row.statement_scope}</td>
                            <td className="px-3 py-2">
                              <RiskBadge
                                tone={row.quality_status === "PASS" ? "low" : "mid"}
                                label={row.quality_status}
                              />
                            </td>
                            <td className="px-3 py-2 text-ink-muted">
                              {row.warning_codes.join(", ") || "—"}
                            </td>
                            <td className="num px-3 py-2 text-right text-ink-muted">
                              {formatDateVi(row.checked_at)}
                            </td>
                          </tr>
                        ))}
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
                Danh sách hồ sơ bị giữ lại kiểm định ({quarantineQuery.data?.total ?? 0})
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
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-xs">
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
                        {quarantineQuery.data?.items.map((row, idx) => (
                          <tr
                            key={`${row.filing_id}-${idx}`}
                            className="border-b border-border/60"
                          >
                            <td className="px-3 py-2 font-mono">{row.issuer_id}</td>
                            <td className="px-3 py-2 font-mono text-ink-muted">{row.filing_id}</td>
                            <td className="px-3 py-2">
                              {formatPeriodLabel(row.period_end_date, row.period_type)}
                            </td>
                            <td className="px-3 py-2 font-mono">{row.rule_code}</td>
                            <td className="px-3 py-2">
                              <RiskBadge tone="mid" label={row.severity} />
                            </td>
                          </tr>
                        ))}
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
  );
}

export default function MethodologyPage() {
  return (
    <React.Suspense fallback={<PageSkeleton />}>
      <MethodologyContent />
    </React.Suspense>
  );
}
