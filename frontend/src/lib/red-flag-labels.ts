export type EvidenceFormatType =
  | "PERCENT"
  | "VND_BILLIONS"
  | "MULTIPLE"
  | "DAYS"
  | "AUDIT_OPINION"
  | "BOOLEAN"
  | "NUMBER";

export const RED_FLAG_EVIDENCE_META: Record<
  string,
  { label: string; format: EvidenceFormatType }
> = {
  net_income_ttm: { label: "LNST TTM", format: "VND_BILLIONS" },
  cfo_ttm: { label: "CFO TTM", format: "VND_BILLIONS" },
  previous_net_income_ttm: { label: "LNST kỳ trước", format: "VND_BILLIONS" },
  previous_cfo_ttm: { label: "CFO kỳ trước", format: "VND_BILLIONS" },
  accruals_to_assets: { label: "Dồn tích / Tổng TS", format: "PERCENT" },
  revenue_growth_yoy: { label: "Tăng trưởng doanh thu YoY", format: "PERCENT" },
  trade_receivables_growth_yoy: { label: "Tăng trưởng phải thu KH YoY", format: "PERCENT" },
  dso: { label: "Số ngày thu tiền (DSO)", format: "DAYS" },
  dso_prior: { label: "DSO cùng kỳ trước", format: "DAYS" },
  dso_change_days: { label: "Biến động DSO", format: "DAYS" },
  BS_TOTAL_ASSETS: { label: "Tổng tài sản", format: "VND_BILLIONS" },
  BS_SHORT_TERM_RECEIVABLES: { label: "Phải thu ngắn hạn", format: "VND_BILLIONS" },
  BS_TRADE_RECEIVABLES_ST: { label: "Phải thu KH ngắn hạn", format: "VND_BILLIONS" },
  BS_LT_RECEIVABLES: { label: "Phải thu dài hạn", format: "VND_BILLIONS" },
  BS_TRADE_RECEIVABLES_LT: { label: "Phải thu KH dài hạn", format: "VND_BILLIONS" },
  BS_BAD_DEBT_PROVISION_ST: { label: "Dự phòng phải thu NH", format: "VND_BILLIONS" },
  BS_BAD_DEBT_PROVISION_LT: { label: "Dự phòng phải thu DH", format: "VND_BILLIONS" },
  BS_TRADE_RECEIVABLES_ALLOWANCE_ST: {
    label: "Dự phòng phải thu KH ngắn hạn",
    format: "VND_BILLIONS",
  },
  BS_TRADE_RECEIVABLES_ALLOWANCE_LT: {
    label: "Dự phòng phải thu KH dài hạn",
    format: "VND_BILLIONS",
  },
  CF_OPERATING_CASH_FLOW: { label: "CFO TTM", format: "VND_BILLIONS" },
  net_non_customer_receivables_st: {
    label: "Phải thu ngoài KH ngắn hạn (ròng)",
    format: "VND_BILLIONS",
  },
  net_non_customer_receivables_lt: {
    label: "Phải thu ngoài KH dài hạn (ròng)",
    format: "VND_BILLIONS",
  },
  other_receivables_to_assets: { label: "Phải thu ngoài KH / Tổng TS", format: "PERCENT" },
  inventory_to_assets: { label: "Tồn kho / Tổng TS", format: "PERCENT" },
  dio: { label: "Số ngày tồn kho (DIO)", format: "DAYS" },
  dio_prior: { label: "DIO cùng kỳ trước", format: "DAYS" },
  dio_change_days: { label: "Biến động DIO", format: "DAYS" },
  gross_margin: { label: "Biên lợi nhuận gộp", format: "PERCENT" },
  gross_margin_prior: { label: "Biên gộp cùng kỳ trước", format: "PERCENT" },
  interest_coverage: { label: "EBIT / Lãi vay", format: "MULTIPLE" },
  total_debt: { label: "Tổng nợ vay", format: "VND_BILLIONS" },
  current_ratio: { label: "Thanh toán hiện hành", format: "MULTIPLE" },
  debt_to_equity: { label: "Nợ vay / VCSH", format: "MULTIPLE" },
  BS_TOTAL_EQUITY: { label: "Vốn chủ sở hữu", format: "VND_BILLIONS" },
  parent_equity: { label: "VCSH công ty mẹ", format: "VND_BILLIONS" },
  audit_opinion: { label: "Ý kiến kiểm toán", format: "AUDIT_OPINION" },
  is_filed_late: { label: "Nộp BCTC trễ hạn", format: "BOOLEAN" },
  ebt_ttm: { label: "Lợi nhuận trước thuế TTM", format: "VND_BILLIONS" },
  core_operating_profit_ttm: { label: "LN kinh doanh lõi TTM", format: "VND_BILLIONS" },
  debt_cost_ttm: { label: "Chi phí đi vay TTM", format: "VND_BILLIONS" },
  core_to_ebt_ratio: { label: "LN lõi / EBT", format: "PERCENT" },
  non_core_distortion_to_assets: { label: "Biến dạng phi lõi / TS", format: "PERCENT" },
  financial_revenue_ttm: { label: "Doanh thu tài chính TTM", format: "VND_BILLIONS" },
  other_profit_ttm: { label: "Lợi nhuận khác TTM", format: "VND_BILLIONS" },
  ip_disposal_gain_loss_ttm: { label: "Lãi/lỗ thanh lý BĐSĐT", format: "VND_BILLIONS" },
  cip_to_assets_t: { label: "XDCB dở dang / Tổng TS", format: "PERCENT" },
  cip_to_assets_prior: { label: "XDCB dở dang / TS kỳ trước", format: "PERCENT" },
  cip_growth_yoy: { label: "Tăng trưởng XDCB dở dang", format: "PERCENT" },
  tangible_fixed_assets_growth_yoy: {
    label: "Tăng trưởng TSCĐ hữu hình",
    format: "PERCENT",
  },
  ni_unaudited_4q_sum: { label: "Tổng LNST 4 quý tự lập", format: "VND_BILLIONS" },
  ni_audited_annual: { label: "LNST năm sau kiểm toán", format: "VND_BILLIONS" },
  audit_earnings_diff_vnd: { label: "Chênh lệch LNST hậu kiểm toán", format: "VND_BILLIONS" },
  audit_adjustment_pct: { label: "Tỷ lệ điều chỉnh hậu kiểm toán", format: "PERCENT" },
};

export const RED_FLAG_CODE_LABELS: Record<string, string> = {
  RF_01: "Quy tắc 01",
  RF_02: "Quy tắc 02",
  RF_03: "Quy tắc 03",
  RF_04: "Quy tắc 04",
  RF_05: "Quy tắc 05",
  RF_06: "Quy tắc 06",
  RF_07: "Quy tắc 07",
  RF_08: "Quy tắc 08",
  RF_09: "Quy tắc 09",
  RF_10: "Quy tắc 10",
};

export function formatRedFlagCodeLabel(flagCode: string | null | undefined): string {
  if (!flagCode) return "Quy tắc cảnh báo";
  const shortCode = flagCode.trim().toUpperCase().split("_").slice(0, 2).join("_");
  if (shortCode in RED_FLAG_CODE_LABELS) {
    return RED_FLAG_CODE_LABELS[shortCode];
  }
  const match = /^RF_?0?(\d+)$/i.exec(shortCode);
  if (match) {
    return `Quy tắc ${match[1].padStart(2, "0")}`;
  }
  return "Quy tắc cảnh báo";
}
