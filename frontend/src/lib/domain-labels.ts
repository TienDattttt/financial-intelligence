/**
 * Domain label mappings from backend technical enums to clean Vietnamese financial terminology
 * per PRODUCT.md §8.
 */

export type RiskTone = "low" | "mid" | "high" | "neutral";

export interface EnumDisplayMeta {
  label: string;
  shortLabel: string;
  tone: RiskTone;
  description: string;
}

export const ALTMAN_ZONE_META: Record<string, EnumDisplayMeta> = {
  SAFE_ZONE: {
    label: "An toàn",
    shortLabel: "An toàn",
    tone: "low",
    description: "Điểm Z″ > 2,60: sức khỏe cân đối kế toán ổn định, nguy cơ kiệt quệ thấp.",
  },
  GREY_ZONE: {
    label: "Vùng xám",
    shortLabel: "Vùng xám",
    tone: "mid",
    description: "Điểm Z″ từ 1,10 đến 2,60: cần theo dõi đòn bẩy và khả năng sinh lời.",
  },
  DISTRESS_ZONE: {
    label: "Nguy cơ kiệt quệ",
    shortLabel: "Nguy cơ",
    tone: "high",
    description: "Điểm Z″ < 1,10: áp lực thanh khoản hoặc nợ vay cao.",
  },
};

export const BENEISH_FLAG_META: Record<string, EnumDisplayMeta> = {
  VALID: {
    label: "Bình thường",
    shortLabel: "Bình thường",
    tone: "low",
    description: "M-Score ≤ −1,78: không phát hiện tín hiệu điều chỉnh lợi nhuận bất thường.",
  },
  HIGH_MANIPULATION_RISK_SCREEN: {
    label: "Nghi vấn điều chỉnh LN",
    shortLabel: "Nghi vấn",
    tone: "high",
    description: "M-Score > −1,78: cần kiểm tra ghi nhận doanh thu, phải thu và dồn tích.",
  },
  MIXED_TAXONOMY: {
    label: "Chuyển chuẩn mực",
    shortLabel: "TT200→TT99",
    tone: "neutral",
    description: "Hai kỳ so sánh khác chuẩn mực kế toán (TT200 vs TT99), chưa kết luận.",
  },
  MISSING_PRIOR_YEAR_BASE: {
    label: "Thiếu kỳ gốc năm trước",
    shortLabel: "Thiếu dữ liệu",
    tone: "neutral",
    description: "Chưa đủ dữ liệu kỳ so sánh năm trước để tính đủ 8 biến Beneish.",
  },
  MISSING_DEPRECIATION_DIRECT_CF: {
    label: "Thiếu khấu hao",
    shortLabel: "Thiếu khấu hao",
    tone: "neutral",
    description: "LCTT lập theo phương pháp trực tiếp chưa tách khấu hao TSCĐ.",
  },
  MISSING_OR_INVALID_INPUT: {
    label: "Không đủ dữ liệu",
    shortLabel: "Không đủ DL",
    tone: "neutral",
    description: "Thiếu chỉ tiêu đầu vào bắt buộc hoặc mẫu số bằng 0.",
  },
};

export const SEVERITY_META: Record<string, EnumDisplayMeta> = {
  CRITICAL: {
    label: "Nghiêm trọng",
    shortLabel: "Nghiêm trọng",
    tone: "high",
    description: "Ảnh hưởng trực tiếp tới khả năng trả nợ, vốn chủ sở hữu hoặc kiểm toán.",
  },
  HIGH: {
    label: "Cao",
    shortLabel: "Cao",
    tone: "high",
    description: "Bất thường rõ rệt về dòng tiền, phải thu khác hoặc phụ thuộc lợi nhuận phi lõi.",
  },
  MEDIUM: {
    label: "Cần lưu ý",
    shortLabel: "Lưu ý",
    tone: "mid",
    description: "Xu hướng suy giảm hiệu quả vốn lưu động so với lịch sử.",
  },
  LOW: {
    label: "Thấp",
    shortLabel: "Thấp",
    tone: "neutral",
    description: "Tín hiệu cảnh báo sớm mức nhẹ.",
  },
};

export function formatPeerGroupLabel(
  peerGroupLevel: string | null | undefined,
  peerCount: number,
  sectorName?: string | null,
): { text: string; isFallback: boolean } {
  if (peerGroupLevel === "ICB_LEVEL_3") {
    const suffix = sectorName ? `: ${sectorName}` : " (ICB cấp 3)";
    return { text: `Ngành cấp 3${suffix} (${peerCount} DN)`, isFallback: false };
  }
  if (peerGroupLevel === "ICB_LEVEL_2") {
    return {
      text: `Ngành cấp 2 — mở rộng do ngành cấp 3 ít DN (${peerCount} DN)`,
      isFallback: false,
    };
  }
  if (peerGroupLevel === "REGIME_FALLBACK") {
    return {
      text: `Nhóm so sánh mở rộng toàn thị trường cùng chuẩn mực (${peerCount} DN)`,
      isFallback: true,
    };
  }
  return { text: "Chưa có dữ liệu nhóm so sánh", isFallback: true };
}

export const STATEMENT_SCOPE_LABELS: Record<string, string> = {
  CONSOLIDATED: "Hợp nhất",
  SEPARATE: "Riêng lẻ",
};

export const PERIOD_TYPE_LABELS: Record<string, string> = {
  QUARTER: "TTM (4 quý trượt)",
  ANNUAL: "Theo năm (Annual)",
};

export const TAXONOMY_LABELS: Record<string, string> = {
  VAS_TT200_2014: "TT200/2014",
  VAS_TT99_2025: "TT99/2025",
};

export const AUDIT_STATUS_LABELS: Record<string, string> = {
  AUDITED: "Đã kiểm toán",
  REVIEWED: "Đã soát xét",
  UNAUDITED: "Tự lập",
};

export const AUDIT_OPINION_LABELS: Record<string, string> = {
  UNMODIFIED: "Chấp nhận toàn phần",
  UNQUALIFIED: "Chấp nhận toàn phần",
  EXCEPT_FOR: "Ngoại trừ",
  QUALIFIED: "Ngoại trừ",
  ADVERSE: "Trái ngược",
  DISCLAIMER: "Từ chối đưa ra ý kiến",
  NOT_APPLICABLE: "Chưa kiểm toán",
  UNKNOWN: "Chưa rõ",
};

export const REVISION_REASON_LABELS: Record<string, string> = {
  ORIGINAL: "Bản gốc",
  AUDITED_UPDATE: "Cập nhật sau kiểm toán",
  CORRECTION: "Điều chỉnh sai sót",
  RESTATED_REGIME_CHANGE: "Điều chỉnh hồi tố chuyển chuẩn mực",
};

export const RATIO_CATEGORY_LABELS: Record<string, string> = {
  PROFITABILITY: "Khả năng sinh lời",
  LIQUIDITY: "Thanh khoản",
  LEVERAGE: "Đòn bẩy tài chính",
  EFFICIENCY: "Hiệu quả hoạt động",
  CASH_FLOW: "Chất lượng dòng tiền",
  GROWTH: "Tăng trưởng",
  FORENSIC: "Chất lượng lợi nhuận & Điều tra",
};

export function getDistressRiskBadge(prob: number | null | undefined): EnumDisplayMeta {
  if (prob === null || prob === undefined || !Number.isFinite(prob)) {
    return {
      label: "Chưa đánh giá",
      shortLabel: "N/A",
      tone: "neutral",
      description: "Không áp dụng mô hình cho nhóm ngành/kỳ này.",
    };
  }
  if (prob < 0.03) {
    return {
      label: "Rủi ro thấp",
      shortLabel: "Thấp",
      tone: "low",
      description: "Xác suất kiệt quệ đã hiệu chỉnh nằm trong nhóm an toàn.",
    };
  }
  if (prob < 0.08) {
    return {
      label: "Cần theo dõi",
      shortLabel: "Trung bình",
      tone: "mid",
      description: "Xác suất kiệt quệ ở mức trung bình — cần xem các yếu tố SHAP.",
    };
  }
  return {
    label: "Rủi ro cao",
    shortLabel: "Cao",
    tone: "high",
    description: "Xác suất kiệt quệ cao đáng kể so với mặt bằng chung.",
  };
}

export const STATEMENT_METRIC_LABELS: Record<string, string> = {
  BS_BAD_DEBT_PROVISION_LT: "Dự phòng phải thu dài hạn khó đòi",
  BS_BAD_DEBT_PROVISION_ST: "Dự phòng phải thu ngắn hạn khó đòi",
  BS_CASH_AND_EQUIVALENTS: "Tiền và các khoản tương đương tiền",
  BS_CIP_LONG_TERM: "Chi phí xây dựng cơ bản dở dang",
  BS_CONVERTIBLE_BONDS: "Trái phiếu chuyển đổi",
  BS_CURRENT_ASSETS: "Tài sản ngắn hạn",
  BS_CURRENT_LIABILITIES: "Nợ ngắn hạn",
  BS_FIXED_ASSETS: "Tài sản cố định",
  BS_INTANGIBLE_FIXED_ASSETS: "Tài sản cố định vô hình",
  BS_INVENTORIES: "Hàng tồn kho",
  BS_LONG_TERM_BORROWINGS: "Vay và nợ thuê tài chính dài hạn",
  BS_LT_FINANCIAL_INVESTMENTS: "Đầu tư tài chính dài hạn",
  BS_LT_RECEIVABLES: "Các khoản phải thu dài hạn",
  BS_MINORITY_INTEREST: "Lợi ích cổ đông không kiểm soát",
  BS_NONCURRENT_ASSETS: "Tài sản dài hạn",
  BS_NONCURRENT_LIABILITIES: "Nợ dài hạn",
  BS_ORDINARY_SHARES_CAPITAL: "Vốn góp cổ phiếu phổ thông",
  BS_OTHER_NONCURRENT_ASSETS: "Tài sản dài hạn khác",
  BS_RETAINED_EARNINGS: "Lợi nhuận sau thuế chưa phân phối",
  BS_SHORT_TERM_BORROWINGS: "Vay và nợ thuê tài chính ngắn hạn",
  BS_SHORT_TERM_INVESTMENTS: "Đầu tư tài chính ngắn hạn",
  BS_SHORT_TERM_RECEIVABLES: "Các khoản phải thu ngắn hạn",
  BS_TANGIBLE_FIXED_ASSETS: "Tài sản cố định hữu hình",
  BS_TOTAL_ASSETS: "Tổng cộng tài sản",
  BS_TOTAL_EQUITY: "Vốn chủ sở hữu",
  BS_TOTAL_LIABILITIES: "Nợ phải trả",
  BS_TOTAL_LIABILITIES_AND_EQUITY: "Tổng cộng nguồn vốn",
  BS_TRADE_PAYABLES_ST: "Phải trả người bán ngắn hạn",
  BS_TRADE_RECEIVABLES_ALLOWANCE_LT: "Dự phòng phải thu khách hàng dài hạn",
  BS_TRADE_RECEIVABLES_ALLOWANCE_ST: "Dự phòng phải thu khách hàng ngắn hạn",
  BS_TRADE_RECEIVABLES_LT: "Phải thu dài hạn của khách hàng",
  BS_TRADE_RECEIVABLES_ST: "Phải thu ngắn hạn của khách hàng",
  BS_TREASURY_SHARES: "Cổ phiếu quỹ",
  CF_CAPITAL_EXPENDITURE: "Chi đầu tư TSCĐ và tài sản dài hạn",
  CF_DEPRECIATION_AMORTIZATION: "Khấu hao TSCĐ và BĐSĐT",
  CF_EQUITY_ISSUANCE_PROCEEDS: "Tiền thu từ phát hành cổ phiếu, góp vốn",
  CF_FINANCING_CASH_FLOW: "Lưu chuyển tiền thuần từ HĐ tài chính",
  CF_INVESTING_CASH_FLOW: "Lưu chuyển tiền thuần từ HĐ đầu tư",
  CF_NET_CASH_CHANGE: "Lưu chuyển tiền thuần trong kỳ",
  CF_OPERATING_CASH_FLOW: "Lưu chuyển tiền thuần từ HĐ kinh doanh",
  CF_PROCEEDS_FROM_BORROWINGS: "Tiền thu từ đi vay",
  CF_REPAYMENT_OF_BORROWINGS: "Tiền trả nợ gốc vay",
  CF_SUPPLIER_PAYMENTS: "Tiền chi trả người cung cấp",
  IS_ADMINISTRATIVE_EXPENSES: "Chi phí quản lý doanh nghiệp",
  IS_BORROWING_COSTS: "Chi phí đi vay",
  IS_CONTINUING_OPERATIONS_NET_INCOME: "LNST từ hoạt động liên tục",
  IS_COST_OF_GOODS_SOLD: "Giá vốn hàng bán",
  IS_CURRENT_INCOME_TAX: "Chi phí thuế TNDN hiện hành",
  IS_DEFERRED_INCOME_TAX: "Chi phí thuế TNDN hoãn lại",
  IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE: "Phát hành cổ phiếu không thu tiền có pha loãng",
  IS_EPS_BASIC_REPORTED: "Lãi cơ bản trên cổ phiếu",
  IS_FINANCIAL_EXPENSES: "Chi phí tài chính",
  IS_FINANCIAL_REVENUE: "Doanh thu hoạt động tài chính",
  IS_GROSS_PROFIT: "Lợi nhuận gộp",
  IS_GROSS_REVENUE: "Doanh thu bán hàng và cung cấp dịch vụ",
  IS_INTEREST_EXPENSE: "Chi phí lãi vay",
  IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS: "Lãi/lỗ thanh lý BĐS đầu tư",
  IS_NET_INCOME: "Lợi nhuận sau thuế TNDN",
  IS_NET_INCOME_PARENT: "Lợi nhuận sau thuế công ty mẹ",
  IS_NET_REVENUE: "Doanh thu thuần",
  IS_OPERATING_PROFIT: "Lợi nhuận thuần từ HĐKD",
  IS_OTHER_EXPENSES: "Chi phí khác",
  IS_OTHER_INCOME: "Thu nhập khác",
  IS_OTHER_PROFIT: "Lợi nhuận khác",
  IS_PROFIT_BEFORE_TAX: "Tổng lợi nhuận kế toán trước thuế",
  IS_REVENUE_DEDUCTIONS: "Các khoản giảm trừ doanh thu",
  IS_SELLING_EXPENSES: "Chi phí bán hàng",
};

export function getStatementMetricLabel(metricId: string): string {
  return STATEMENT_METRIC_LABELS[metricId] ?? "Chỉ tiêu báo cáo tài chính";
}

const FORMULA_TERM_REPLACEMENTS: Record<string, string> = {
  ...STATEMENT_METRIC_LABELS,
  TotalDebt: "Tổng nợ vay tài chính",
  CashAndSTInvestments: "Tiền & Đầu tư ngắn hạn",
  NetDebt: "Nợ vay ròng",
  ParentEquity: "VCSH công ty mẹ",
  InvestedCapital: "Vốn đầu tư",
  EstimatedShares: "Số cổ phiếu lưu hành ước tính",
  CoreOperatingProfit_TTM: "LN kinh doanh cốt lõi TTM",
  NOPAT_TTM: "LN hoạt động sau thuế (NOPAT)",
  EBITDA_TTM: "EBITDA TTM",
  EBIT_TTM: "EBIT TTM",
  inventory_turnover: "Vòng quay hàng tồn kho",
  receivables_turnover: "Vòng quay phải thu",
  payables_turnover: "Vòng quay phải trả",
};

export function formatFormulaVi(formula: string): string {
  const withSubscripts = formula.replace(
    /\b([A-Za-z][A-Za-z0-9_]*)_\{t-4\}/g,
    (_, base: string) =>
      `${FORMULA_TERM_REPLACEMENTS[base] ?? base} (cùng kỳ năm trước)`,
  );
  return withSubscripts.replace(/\b[A-Za-z][A-Za-z0-9_]*\b/g, (token) => {
    if (token in FORMULA_TERM_REPLACEMENTS) {
      return FORMULA_TERM_REPLACEMENTS[token];
    }
    const baseToken = token.replace(/_(t|prior)$/, "");
    if (baseToken in FORMULA_TERM_REPLACEMENTS) {
      const suffix = token.endsWith("_prior")
        ? " (cùng kỳ năm trước)"
        : " (kỳ hiện tại)";
      return `${FORMULA_TERM_REPLACEMENTS[baseToken]}${suffix}`;
    }
    return token;
  });
}

export const RATIO_STATUS_FLAG_META: Record<
  string,
  { label: string; tone: RiskTone }
> = {
  VALID: { label: "Hợp lệ", tone: "low" },
  DERIVED_Q4_INPUT: { label: "Có Q4 suy diễn", tone: "mid" },
  MIXED_TAXONOMY: { label: "Giao thời TT200→TT99", tone: "mid" },
  MISSING_DEPRECIATION_DIRECT_CF: {
    label: "Thiếu khấu hao (LCTT trực tiếp)",
    tone: "mid",
  },
  NEGATIVE_OR_ZERO_EQUITY: { label: "VCSH âm hoặc bằng 0", tone: "high" },
  ZERO_OR_NEGATIVE_DENOMINATOR: { label: "Mẫu số không dương", tone: "high" },
  NON_POSITIVE_INVESTED_CAPITAL: { label: "Vốn đầu tư không dương", tone: "high" },
  NON_POSITIVE_EBITDA: { label: "EBITDA không dương", tone: "high" },
  NON_POSITIVE_BASE_PERIOD: { label: "Kỳ gốc không dương", tone: "high" },
  NUMERIC_OVERFLOW: { label: "Vượt ngưỡng tính toán", tone: "high" },
  NO_INTEREST_EXPENSE: { label: "Không phát sinh lãi vay", tone: "neutral" },
  MISSING_INPUT: { label: "Thiếu chỉ tiêu đầu vào", tone: "neutral" },
  MISSING_PRIOR_YEAR_BASE: { label: "Thiếu dữ liệu cùng kỳ năm trước", tone: "neutral" },
  NOT_APPLICABLE_FOR_REGIME: { label: "Không áp dụng theo chuẩn ngành", tone: "neutral" },
};

export function getRatioStatusFlagMeta(flag: string | null | undefined): {
  label: string;
  tone: RiskTone;
} {
  if (!flag) return { label: "Hợp lệ", tone: "low" };
  return (
    RATIO_STATUS_FLAG_META[flag] ?? {
      label: "Cần kiểm tra bổ sung",
      tone: "mid",
    }
  );
}

export const DERIVATION_METHOD_LABELS: Record<string, string> = {
  REPORTED_DIRECTLY: "Công bố trực tiếp trên BCTC",
  DERIVED_YTD_DIFF: "Tách quý từ số liệu lũy kế",
  DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX: "Suy diễn Q4 (BCTC năm trừ 9 tháng)",
};

export const PUBLISHED_AT_BASIS_LABELS: Record<string, string> = {
  EXACT_FILING: "Theo ngày công bố thực tế",
  STATUTORY_LAG: "Ước tính theo hạn công bố TT96",
  LATEST_INPUT_AVAILABILITY: "Theo thành phần công bố muộn nhất",
  UNKNOWN: "Chưa rõ căn cứ ngày công bố",
};

export const SOURCE_PROVIDER_LABELS: Record<string, string> = {
  hose_pdf_extract: "BCTC niêm yết (PDF)",
  hnx_pdf_extract: "BCTC HNX (PDF)",
  vietcap: "Vietcap",
  iq_vietcap: "Vietcap IQ",
  cafef: "CafeF",
  vnstock: "Vnstock",
  fhsc: "FHSC",
  manual_curated: "Kiểm định thủ công",
};

export function formatSourceProviderLabel(provider: string | null | undefined): string {
  if (!provider) return "BCTC doanh nghiệp niêm yết";
  const key = provider.trim().toLowerCase();
  return SOURCE_PROVIDER_LABELS[key] ?? "BCTC doanh nghiệp niêm yết";
}

export const QUALITY_STATUS_META: Record<string, { label: string; tone: RiskTone }> = {
  PASS: { label: "Đạt chuẩn", tone: "low" },
  WARNING: { label: "Có cảnh báo", tone: "mid" },
};

export const QUALITY_RULE_LABELS: Record<string, string> = {
  BS_IDENTITY_MISMATCH: "Lệch cân đối Tổng Tài sản ≠ Nguồn vốn",
  SUBTOTAL_MISMATCH: "Lệch tổng cộng tiểu mục",
  UNIT_SCALE_JUMP: "Bất thường đơn vị tính (×1.000)",
  DUPLICATE_PRIMARY_KEY: "Trùng khóa bản ghi BCTC",
  MISSING_REQUIRED_METRICS: "Thiếu chỉ tiêu kế toán bắt buộc",
  CASH_FLOW_RECONCILIATION_WARN: "Chênh lệch đối chiếu lưu chuyển tiền",
  PARTIAL_STATEMENT: "Thiếu báo cáo lưu chuyển tiền hoặc khấu hao",
  DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX: "Q4 suy diễn từ BCTC năm trừ 9 tháng",
  DERIVED_Q4_INPUT: "Có thành phần Q4 suy diễn",
};

export const MODEL_ROLE_LABELS: Record<string, string> = {
  SCREENING_INDICATOR: "Chỉ báo tầm soát rủi ro",
  CALIBRATED_PD_MODEL: "Mô hình xác suất kiệt quệ hiệu chỉnh",
};

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
};

export function selectMlScoreForPeriod<T extends { period_end_date: string }>(
  scores: readonly T[] | null | undefined,
  periodEndDate: string | null | undefined,
): T | null {
  if (!scores || scores.length === 0) return null;
  const sorted = [...scores].sort((a, b) =>
    a.period_end_date.localeCompare(b.period_end_date),
  );
  if (!periodEndDate) {
    return sorted[sorted.length - 1] ?? null;
  }
  const exact = sorted.find((s) => s.period_end_date === periodEndDate);
  if (exact) return exact;
  let latestBeforeOrAt: T | null = null;
  for (const s of sorted) {
    if (s.period_end_date <= periodEndDate) {
      latestBeforeOrAt = s;
    }
  }
  return latestBeforeOrAt;
}
