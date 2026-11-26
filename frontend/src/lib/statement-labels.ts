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
  EBIT: "Lợi nhuận trước lãi vay và thuế (EBIT)",
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
