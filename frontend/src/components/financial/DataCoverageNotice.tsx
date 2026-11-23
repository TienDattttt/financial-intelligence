import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

export interface CompanyCoverageInput {
  ticker: string;
  exchange?: string | null;
  accountingRegime: string;
  hasFinancialData: boolean;
  hasSupervisedPd: boolean;
  hasEodPrices: boolean | null;
}

export interface CoverageModuleStatus {
  id: "financials" | "eod" | "pd" | "pdf";
  label: string;
  statusText: string;
  available: boolean | null;
}

export interface CompanyCoverageAssessment {
  isPartial: boolean;
  isUpcom: boolean;
  badgeLabel: string;
  noticeTitle: string;
  noticeText: string;
  modules: CoverageModuleStatus[];
}

export function assessCompanyDataCoverage(
  input: CompanyCoverageInput,
): CompanyCoverageAssessment {
  const ticker = input.ticker.trim().toUpperCase();
  const exchange = input.exchange?.trim().toUpperCase() || null;
  const isUpcom = exchange === "UPCOM";
  const isNonCorporate = input.accountingRegime !== "CORPORATE";
  const missingEod = input.hasEodPrices === false;
  const missingPd = !input.hasSupervisedPd;

  const isPartial =
    isNonCorporate || !input.hasFinancialData || missingEod || missingPd;

  const exchangePrefix = exchange ? `${exchange} · ` : "";
  const badgeLabel = isPartial
    ? `Phủ dữ liệu một phần (${exchangePrefix}không suy đoán)`
    : "Phủ dữ liệu đầy đủ";

  const modules: CoverageModuleStatus[] = [
    {
      id: "financials",
      label: "BCTC, 38 tỷ số & 9 cờ đỏ VAS",
      statusText: input.hasFinancialData ? "Đầy đủ" : "Chưa có dữ liệu kỳ chọn",
      available: input.hasFinancialData,
    },
    {
      id: "eod",
      label: "Giá EOD & Định giá PIT",
      statusText:
        input.hasEodPrices === null
          ? "Đang kiểm tra"
          : input.hasEodPrices
            ? "Đầy đủ"
            : "Chưa có dữ liệu EOD",
      available: input.hasEodPrices,
    },
    {
      id: "pd",
      label: "Điểm xác suất kiệt quệ (1Y PD)",
      statusText: isNonCorporate
        ? "Không áp dụng cho nhóm tài chính"
        : input.hasSupervisedPd
          ? "Đã chấm (PIT)"
          : "Chưa chấm (thiếu mốc công bố PIT)",
      available: isNonCorporate ? false : input.hasSupervisedPd,
    },
    {
      id: "pdf",
      label: "Thuyết minh PDF đối chiếu",
      statusText: "Theo gói doanh nghiệp trọng điểm tuyển chọn",
      available: null,
    },
  ];

  let noticeTitle = `Phạm vi dữ liệu công bố cho ${ticker}`;
  let noticeText =
    "Hệ thống cung cấp đầy đủ BCTC chuẩn hóa, 38 tỷ số tài chính, 9 cờ đỏ VAS, lớp phủ giá EOD và điểm xác suất kiệt quệ mô hình.";

  if (isNonCorporate) {
    noticeTitle = `${ticker} thuộc nhóm định chế tài chính / bảo hiểm`;
    noticeText =
      "Tổ chức áp dụng chuẩn mực kế toán đặc thù; bộ chỉ số và mô hình cảnh báo dành riêng cho doanh nghiệp phi tài chính không áp dụng trực tiếp.";
  } else if (!input.hasFinancialData) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa có dữ liệu kỳ báo cáo đang chọn`;
    noticeText =
      "Chưa đủ dữ liệu BCTC chuẩn hóa cho lát cắt báo cáo đang chọn. Thử chuyển giữa Hợp nhất / Riêng lẻ hoặc TTM / Theo năm trên thanh công cụ.";
  } else if (missingEod && missingPd) {
    noticeTitle = isUpcom
      ? `${ticker} (${exchange}) — Phạm vi dữ liệu công bố`
      : `${ticker}${exchange ? ` (${exchange})` : ""} — Phạm vi dữ liệu công bố`;
    noticeText = `${ticker}${isUpcom ? " giao dịch trên sàn UPCOM" : ""} hiện chưa có đủ dữ liệu giá giao dịch lịch sử (EOD) và chuỗi kỳ có mốc ngày công bố thực tế (PIT) xác thực. Tuân thủ nguyên tắc không suy đoán dữ liệu, hệ thống vẫn cung cấp đầy đủ BCTC chuẩn hóa, 38 tỷ số tài chính và 9 cờ đỏ VAS, đồng thời tạm ẩn điểm 1Y PD hoặc chuỗi giá cổ phiếu khi chưa có dữ liệu gốc.`;
  } else if (missingEod) {
    noticeTitle = isUpcom
      ? `${ticker} (${exchange}) — Chưa có chuỗi giá giao dịch EOD lịch sử`
      : `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa có chuỗi giá giao dịch EOD`;
    noticeText = `${ticker}${isUpcom ? " trên sàn UPCOM" : ""} hiện chưa ghi nhận chuỗi giá giao dịch EOD lịch sử trong kho dữ liệu (thường gặp ở cổ phiếu thanh khoản thấp hoặc đã hủy niêm yết). Hệ thống vẫn cung cấp đầy đủ BCTC, 38 tỷ số, 9 cờ đỏ VAS và điểm mô hình định lượng.`;
  } else if (missingPd) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa chấm điểm 1Y PD do thiếu mốc công bố PIT`;
    noticeText = `${ticker} chưa đủ chuỗi kỳ có ngày công bố thực tế (PIT) xác thực để chấm điểm xác suất kiệt quệ (1Y PD). Hệ thống không tự suy đoán ngày công bố và vẫn cung cấp đầy đủ BCTC, 38 tỷ số tài chính, 9 cờ đỏ VAS và chuỗi giá thị trường (nếu có).`;
  }

  return {
    isPartial,
    isUpcom,
    badgeLabel,
    noticeTitle,
    noticeText,
    modules,
  };
}

export function DataCoverageNotice(props: CompanyCoverageInput) {
  const assessment = assessCompanyDataCoverage(props);

  if (!assessment.isPartial) {
    return null;
  }

  return (
    <section
      aria-label="Phạm vi phủ dữ liệu doanh nghiệp"
      className="rounded-lg border border-border-strong bg-surface p-3.5 text-xs shadow-xs"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <Info
            className="mt-0.5 size-4 shrink-0 text-accent-ink"
            aria-hidden="true"
          />
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold text-ink">
                {assessment.noticeTitle}
              </h2>
              <Badge className="border border-risk-mid/30 bg-risk-mid-soft text-[11px] font-medium text-risk-mid">
                {assessment.badgeLabel}
              </Badge>
            </div>
            <p className="leading-5 text-ink-muted">{assessment.noticeText}</p>
          </div>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-2 border-t border-border/60 pt-2.5">
        {assessment.modules.map((mod) => {
          const Icon =
            mod.available === true
              ? CheckCircle2
              : mod.available === false
                ? AlertCircle
                : Info;
          return (
            <span
              key={mod.id}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
                mod.available === true
                  ? "border-risk-low/25 bg-risk-low-soft/50 text-risk-low"
                  : mod.available === false
                    ? "border-border bg-surface-muted text-ink-muted"
                    : "border-border bg-canvas text-ink-subtle",
              )}
            >
              <Icon className="size-3 shrink-0" aria-hidden="true" />
              <span className="font-medium text-ink">{mod.label}:</span>
              <span>{mod.statusText}</span>
            </span>
          );
        })}
      </div>
    </section>
  );
}
