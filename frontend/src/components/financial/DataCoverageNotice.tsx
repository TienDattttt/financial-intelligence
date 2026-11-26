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
      label: "BCTC, tỷ số & cờ đỏ kế toán",
      statusText: input.hasFinancialData ? "Đầy đủ" : "Chưa có dữ liệu kỳ chọn",
      available: input.hasFinancialData,
    },
    {
      id: "eod",
      label: "Giá lịch sử & Định giá",
      statusText:
        input.hasEodPrices === null
          ? "Đang kiểm tra"
          : input.hasEodPrices
            ? "Đầy đủ"
            : "Chưa có dữ liệu giá",
      available: input.hasEodPrices,
    },
    {
      id: "pd",
      label: "Dự báo rủi ro 1 năm",
      statusText: isNonCorporate
        ? "Không áp dụng cho nhóm tài chính"
        : input.hasSupervisedPd
          ? "Đầy đủ dữ liệu"
          : "Chưa đủ lịch sử công bố",
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
    "Hệ thống cung cấp đầy đủ báo cáo tài chính, bộ chỉ số phân tích, cảnh báo cờ đỏ, biểu đồ giá lịch sử và dự báo rủi ro 1 năm.";

  if (isNonCorporate) {
    noticeTitle = `${ticker} thuộc nhóm định chế tài chính / bảo hiểm`;
    noticeText =
      "Tổ chức áp dụng chuẩn mực kế toán đặc thù; bộ chỉ số và mô hình cảnh báo dành riêng cho doanh nghiệp phi tài chính không áp dụng trực tiếp.";
  } else if (!input.hasFinancialData) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa có dữ liệu kỳ báo cáo đang chọn`;
    noticeText =
      "Chưa đủ dữ liệu BCTC chuẩn hóa cho lát cắt báo cáo đang chọn. Thử chuyển giữa Hợp nhất / Riêng lẻ hoặc TTM / Theo năm trên thanh công cụ.";
  } else if (missingEod && missingPd) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Phạm vi dữ liệu công bố`;
    noticeText = `${ticker}${isUpcom ? " giao dịch trên sàn UPCOM" : ""} hiện chưa có đủ dữ liệu giá lịch sử và chuỗi kỳ công bố báo cáo để tính dự báo rủi ro 1 năm. Các báo cáo tài chính, chỉ số phân tích và cảnh báo cờ đỏ vẫn hiển thị đầy đủ.`;
  } else if (missingEod) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa có dữ liệu giá lịch sử`;
    noticeText = `${ticker}${isUpcom ? " trên sàn UPCOM" : ""} hiện chưa ghi nhận dữ liệu giá giao dịch lịch sử trong hệ thống (thường gặp ở cổ phiếu thanh khoản thấp hoặc đã hủy niêm yết). Các báo cáo tài chính, chỉ số phân tích, cảnh báo cờ đỏ và dự báo rủi ro vẫn hiển thị đầy đủ.`;
  } else if (missingPd) {
    noticeTitle = `${ticker}${exchange ? ` (${exchange})` : ""} — Chưa đủ lịch sử công bố để dự báo rủi ro 1 năm`;
    noticeText = `${ticker} chưa đủ chuỗi kỳ báo cáo có ngày công bố xác thực để tính dự báo rủi ro 1 năm. Các báo cáo tài chính, chỉ số phân tích, cảnh báo cờ đỏ và biểu đồ giá (nếu có) vẫn hiển thị đầy đủ.`;
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
