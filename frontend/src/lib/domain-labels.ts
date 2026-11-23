/**
 * Domain label mappings from backend technical enums to clean Vietnamese financial terminology
 * per PRODUCT.md §8 and DESIGN.md §2.6.
 */

import { formatNumber, formatPercent } from "@/lib/format";

export type RiskTone = "low" | "mid" | "high" | "neutral";
export type CreditRiskTier = "R1" | "R2" | "R3" | "R4" | "R5";

export interface EnumDisplayMeta {
  label: string;
  shortLabel: string;
  tone: RiskTone;
  description: string;
}

export interface RiskTierDisplayMeta extends EnumDisplayMeta {
  tier: CreditRiskTier;
  badgeText: string;
  rankOrder: number;
}

export const RISK_TIER_ORDER: readonly CreditRiskTier[] = ["R1", "R2", "R3", "R4", "R5"];

export const RISK_TIER_META: Record<CreditRiskTier, RiskTierDisplayMeta> = {
  R1: {
    tier: "R1",
    badgeText: "R1",
    label: "R1 · Rủi ro rất thấp",
    shortLabel: "R1 · Rất thấp",
    tone: "low",
    rankOrder: 1,
    description:
      "Bậc tín nhiệm R1 (chuẩn toàn chu kỳ TTC): xác suất kiệt quệ 1 năm nằm ở vùng an toàn nhất.",
  },
  R2: {
    tier: "R2",
    badgeText: "R2",
    label: "R2 · Rủi ro thấp",
    shortLabel: "R2 · Thấp",
    tone: "low",
    rankOrder: 2,
    description:
      "Bậc tín nhiệm R2 (chuẩn toàn chu kỳ TTC): rủi ro thấp, cấu trúc tài chính duy trì ổn định.",
  },
  R3: {
    tier: "R3",
    badgeText: "R3",
    label: "R3 · Rủi ro trung bình",
    shortLabel: "R3 · Trung bình",
    tone: "mid",
    rankOrder: 3,
    description:
      "Bậc tín nhiệm R3 (chuẩn toàn chu kỳ TTC): mức trung bình, cần theo dõi xu hướng đòn bẩy và dòng tiền.",
  },
  R4: {
    tier: "R4",
    badgeText: "R4",
    label: "R4 · Rủi ro cao",
    shortLabel: "R4 · Cao",
    tone: "high",
    rankOrder: 4,
    description:
      "Bậc tín nhiệm R4 (chuẩn toàn chu kỳ TTC): cảnh báo sớm rủi ro cao, cần rà soát cờ đỏ kế toán và thanh khoản.",
  },
  R5: {
    tier: "R5",
    badgeText: "R5",
    label: "R5 · Rủi ro rất cao",
    shortLabel: "R5 · Rất cao",
    tone: "high",
    rankOrder: 5,
    description:
      "Bậc tín nhiệm R5 (chuẩn toàn chu kỳ TTC): vùng căng thẳng tài chính nghiêm trọng, rủi ro kiệt quệ rất cao.",
  },
};

export function isCreditRiskTier(value: string | null | undefined): value is CreditRiskTier {
  return value === "R1" || value === "R2" || value === "R3" || value === "R4" || value === "R5";
}

export function getRiskTierMeta(tier: string | null | undefined): EnumDisplayMeta & {
  tier: CreditRiskTier | null;
  badgeText: string;
  rankOrder: number;
} {
  if (isCreditRiskTier(tier)) {
    return RISK_TIER_META[tier];
  }
  return {
    tier: null,
    badgeText: "N/A",
    label: "Chưa xếp bậc",
    shortLabel: "Chưa xếp bậc",
    tone: "neutral",
    rankOrder: 0,
    description: "Chưa có dữ liệu xếp bậc tín nhiệm R1–R5 cho kỳ hoặc phạm vi báo cáo này.",
  };
}

export interface CreditTrioDisplay {
  tierMeta: ReturnType<typeof getRiskTierMeta>;
  topPercent: number | null;
  topPercentLabel: string;
  pdLabel: string;
  liftLabel: string | null;
  baseRateLabel: string | null;
  compactSummary: string;
}

export function formatCreditTrio(input: {
  risk_tier?: string | null;
  risk_market_percentile?: number | null;
  calibrated_distress_prob?: number | null;
  relative_risk_lift?: number | null;
  market_base_rate?: number | null;
}): CreditTrioDisplay {
  const tierMeta = getRiskTierMeta(input.risk_tier);
  const pct = input.risk_market_percentile;
  const validPct = typeof pct === "number" && Number.isFinite(pct);
  const topPercent = validPct
    ? Math.max(0.1, 100 - Math.min(100, Math.max(0, pct)))
    : null;
  const topPercentLabel =
    topPercent !== null
      ? `Top ${formatNumber(topPercent, topPercent < 10 ? 1 : 0)}% rủi ro nhất`
      : "Chưa có vị thế kỳ";
  const pdLabel = formatPercent(input.calibrated_distress_prob);
  const lift = input.relative_risk_lift;
  const liftLabel =
    typeof lift === "number" && Number.isFinite(lift)
      ? `${formatNumber(lift, 1)}× nền`
      : null;
  const baseRate = input.market_base_rate;
  const baseRateLabel =
    typeof baseRate === "number" && Number.isFinite(baseRate)
      ? `Mức nền thị trường ${formatPercent(baseRate)}`
      : null;

  const compactSummary = [
    tierMeta.tier ? tierMeta.badgeText : null,
    topPercent !== null ? topPercentLabel : null,
    liftLabel,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");

  return {
    tierMeta,
    topPercent,
    topPercentLabel,
    pdLabel,
    liftLabel,
    baseRateLabel,
    compactSummary: compactSummary || "Chưa đủ dữ liệu đánh giá",
  };
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

/**
 * @deprecated Use `getRiskTierMeta` + `formatCreditTrio` (R1–R5 TTC + Top X% PIT + 1Y PD × nền).
 * Retained temporarily without hardcoded cutoffs solely for unmerged parallel branches.
 */
export function getDistressRiskBadge(
  prob: number | null | undefined,
  riskTier?: string | null,
): EnumDisplayMeta {
  if (isCreditRiskTier(riskTier)) {
    return RISK_TIER_META[riskTier];
  }
  if (prob === null || prob === undefined || !Number.isFinite(prob)) {
    return {
      label: "Chưa đánh giá",
      shortLabel: "N/A",
      tone: "neutral",
      description: "Không áp dụng mô hình cho nhóm ngành/kỳ này.",
    };
  }
  return {
    label: `1Y PD ${formatPercent(prob)}`,
    shortLabel: formatPercent(prob),
    tone: "neutral",
    description: "Xác suất kiệt quệ 1 năm đã hiệu chỉnh theo mô hình; xem bậc tín nhiệm R1–R5.",
  };
}

export interface ValuationFlagDisplayMeta {
  label: string;
  explanation: string;
  isValid: boolean;
}

export const VALUATION_FLAG_META: Record<string, ValuationFlagDisplayMeta> = {
  VALID: {
    label: "Hợp lệ",
    explanation: "Định giá Point-in-Time hợp lệ từ giá đóng cửa chưa điều chỉnh và BCTC đã công bố.",
    isValid: true,
  },
  NEGATIVE_EARNINGS: {
    label: "Lợi nhuận âm",
    explanation:
      "Không áp dụng P/E do lợi nhuận sau thuế công ty mẹ 4 quý gần nhất âm hoặc bằng 0.",
    isValid: false,
  },
  NEGATIVE_EQUITY: {
    label: "VCSH âm",
    explanation: "Không áp dụng P/B do vốn chủ sở hữu công ty mẹ âm hoặc bằng 0.",
    isValid: false,
  },
  MISSING_EARNINGS: {
    label: "Thiếu dữ liệu lợi nhuận",
    explanation: "Chưa có dữ liệu lợi nhuận sau thuế công ty mẹ 4 quý gần nhất tại ngày giao dịch.",
    isValid: false,
  },
  MISSING_EQUITY: {
    label: "Thiếu dữ liệu VCSH",
    explanation: "Chưa có dữ liệu vốn chủ sở hữu công ty mẹ tại ngày giao dịch.",
    isValid: false,
  },
  MISSING_SHARES: {
    label: "Thiếu số lượng cổ phiếu",
    explanation: "Chưa có dữ liệu số cổ phiếu lưu hành hợp lệ tại ngày giao dịch.",
    isValid: false,
  },
};

export function getValuationFlagMeta(flag: string | null | undefined): ValuationFlagDisplayMeta {
  if (!flag) {
    return {
      label: "Chưa có dữ liệu",
      explanation: "Chưa ghi nhận dữ liệu định giá tại thời điểm này.",
      isValid: false,
    };
  }
  return (
    VALUATION_FLAG_META[flag] ?? {
      label: "Không áp dụng",
      explanation: "Không đủ điều kiện tính chỉ số định giá tại thời điểm giao dịch.",
      isValid: false,
    }
  );
}

export const SHARES_SOURCE_LABELS: Record<string, string> = {
  ESTIMATED_SHARES_OUTSTANDING: "Số CP ước tính từ vốn góp phổ thông trên BCTC",
  BS_ORDINARY_SHARES_EXPLICIT_ZERO_TREASURY: "Vốn góp cổ phiếu phổ thông (không có cổ phiếu quỹ)",
  BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO:
    "Vốn góp cổ phiếu phổ thông (cổ phiếu quỹ mặc định 0)",
  SHARES_OUTSTANDING_MARKET: "Số cổ phiếu lưu hành thực tế trên thị trường",
  MISSING_SHARES: "Thiếu dữ liệu số cổ phiếu lưu hành",
};

export function formatSharesSourceLabel(source: string | null | undefined): string {
  if (!source) return "Dữ liệu cổ phiếu lưu hành Point-in-Time";
  return SHARES_SOURCE_LABELS[source] ?? "Dữ liệu cổ phiếu lưu hành Point-in-Time";
}

export const DATE_BASIS_LABELS: Record<string, string> = {
  PIT_DISCLOSURE: "Ngày công bố BCTC thực tế",
  PERIOD_END_FALLBACK: "Ngày kết thúc kỳ báo cáo",
};

export function formatDateBasisLabel(basis: string | null | undefined): string {
  if (!basis) return "Ngày công bố dữ liệu";
  return DATE_BASIS_LABELS[basis] ?? "Ngày công bố dữ liệu";
}

export const REGULATORY_EVENT_TYPE_META: Record<
  string,
  { label: string; shortLabel: string; tone: RiskTone }
> = {
  CONTROL: {
    label: "Cảnh báo / Kiểm soát",
    shortLabel: "Kiểm soát",
    tone: "mid",
  },
  TRADING_RESTRICTION: {
    label: "Hạn chế giao dịch",
    shortLabel: "Hạn chế GD",
    tone: "high",
  },
  SUSPENSION: {
    label: "Đình chỉ giao dịch",
    shortLabel: "Đình chỉ",
    tone: "high",
  },
  FORCED_DELISTING: {
    label: "Hủy niêm yết bắt buộc",
    shortLabel: "Hủy niêm yết",
    tone: "high",
  },
  ADVERSE_OR_DISCLAIMER_AUDIT: {
    label: "Kiểm toán trái ngược / Từ chối",
    shortLabel: "Ý kiến kiểm toán",
    tone: "high",
  },
};

export function formatRegulatoryEventType(eventType: string | null | undefined): {
  label: string;
  shortLabel: string;
  tone: RiskTone;
} {
  if (!eventType) {
    return { label: "Sự kiện giám sát", shortLabel: "Giám sát", tone: "mid" };
  }
  return (
    REGULATORY_EVENT_TYPE_META[eventType] ?? {
      label: "Sự kiện giám sát thị trường",
      shortLabel: "Giám sát",
      tone: "mid",
    }
  );
}

export const REGULATORY_EVENT_STAGE_LABELS: Record<string, string> = {
  FIRST_ENTRY: "Lần đầu áp dụng",
  ESCALATION: "Nâng mức chế tài",
};

export function formatRegulatoryEventStage(stage: string | null | undefined): string {
  if (!stage) return "Ghi nhận sự kiện";
  return REGULATORY_EVENT_STAGE_LABELS[stage] ?? "Ghi nhận sự kiện";
}

export const MODEL_COMPARISON_LABELS: Record<string, string> = {
  Baseline_1_AltmanZ: "Altman Z″ (Hiệu chỉnh)",
  Baseline_2_Logit: "Logit / Shumway",
  XGBoost: "XGBoost (Đơn điệu + Platt)",
};

export function formatModelDisplayName(modelKey: string | null | undefined): string {
  if (!modelKey) return "Mô hình định lượng";
  return MODEL_COMPARISON_LABELS[modelKey] ?? "Mô hình định lượng";
}

export const EVALUATION_SOURCE_LABELS: Record<string, string> = {
  WALK_FORWARD_POOLED_OOS: "Kiểm định cuốn chiếu ngoài mẫu (Walk-Forward 2021–2025)",
  OUT_OF_SAMPLE_TEST: "Tập kiểm thử ngoài mẫu",
  INNER_OOF_CALIBRATION: "Hiệu chuẩn chéo ngoài mẫu nội bộ",
};

export function formatEvaluationSourceLabel(source: string | null | undefined): string {
  if (!source) return "Kiểm định ngoài mẫu";
  return EVALUATION_SOURCE_LABELS[source] ?? "Kiểm định ngoài mẫu";
}

export const PEER_STANDARDIZATION_LABELS: Record<string, string> = {
  MARKET_ONLY_BEFORE_2026:
    "Chuẩn hóa toàn thị trường phi tài chính (do trước 2026 chưa có lịch sử phân ngành theo năm)",
  SECTOR_Z_SCORE: "Chuẩn hóa Z-Score theo phân ngành ICB cùng kỳ",
  HYBRID: "Chuẩn hóa kết hợp theo ngành ICB và toàn thị trường",
};

export function formatPeerStandardizationMode(mode: string | null | undefined): string {
  if (!mode) return "Chuẩn hóa thống kê cùng kỳ công bố";
  return PEER_STANDARDIZATION_LABELS[mode] ?? "Chuẩn hóa thống kê cùng kỳ công bố";
}

export const TARGET_MODE_LABELS: Record<string, string> = {
  REGULATORY_DISTRESS: "Biến cố kiểm soát, hạn chế/đình chỉ giao dịch, hủy niêm yết hoặc kiểm toán ngoại trừ nặng (12 tháng)",
};

export function formatTargetModeLabel(mode: string | null | undefined): string {
  if (!mode) return "Biến cố căng thẳng tài chính 12 tháng";
  return TARGET_MODE_LABELS[mode] ?? "Biến cố căng thẳng tài chính 12 tháng";
}

export const ML_FEATURE_LABELS: Record<string, string> = {
  roa: "Tỷ suất sinh lời trên tài sản (ROA)",
  roe_parent: "ROE công ty mẹ",
  roic: "Tỷ suất sinh lời trên vốn đầu tư (ROIC)",
  ebit_margin: "Biên lợi nhuận hoạt động (EBIT)",
  current_ratio: "Hệ số thanh toán hiện hành",
  quick_ratio: "Hệ số thanh toán nhanh",
  cash_ratio: "Hệ số thanh toán tiền mặt",
  interest_coverage: "Khả năng thanh toán lãi vay (EBIT / Lãi vay)",
  ocf_to_current_liabilities: "CFO / Nợ ngắn hạn",
  fcf_to_revenue: "Dòng tiền tự do / Doanh thu",
  altman_z_double_prime: "Điểm Altman Z″ (Thị trường mới nổi)",
  piotroski_f_score: "Điểm Piotroski F-Score",
  liabilities_to_assets: "Nợ phải trả / Tổng tài sản",
  financial_debt_to_assets: "Nợ vay tài chính / Tổng tài sản",
  debt_to_equity: "Nợ vay / Vốn chủ sở hữu",
  net_debt_to_ebitda: "Nợ vay ròng / EBITDA",
  accruals_to_assets: "Chênh lệch dồn tích / Tổng tài sản",
  asset_turnover: "Vòng quay tổng tài sản",
  cash_conversion_cycle: "Chu kỳ chuyển hóa tiền mặt (CCC)",
  revenue_growth_yoy: "Tăng trưởng doanh thu thuần (YoY)",
  cross_sectional_asset_size_pct: "Phân vị quy mô tổng tài sản cùng kỳ",
  overdue_or_late_count_4q: "Số kỳ nộp trễ BCTC trong 4 quý gần nhất",
  consecutive_loss_quarters: "Số quý lỗ sau thuế liên tiếp",
  sector_z_asset_turnover: "Vòng quay tổng tài sản (chuẩn hóa cùng kỳ)",
  sector_z_ebit_margin: "Biên EBIT (chuẩn hóa cùng kỳ)",
  sector_z_cash_conversion_cycle: "Chu kỳ tiền mặt CCC (chuẩn hóa cùng kỳ)",
  sector_z_debt_to_equity: "Nợ vay / VCSH (chuẩn hóa cùng kỳ)",
  delta_roa_4q: "Biến động ROA 4 quý",
  delta_liabilities_to_assets_4q: "Biến động Nợ / Tổng tài sản 4 quý",
  delta_current_ratio_4q: "Biến động thanh toán hiện hành 4 quý",
};

export function getMlFeatureLabel(feature: string | null | undefined): string {
  if (!feature) return "Chỉ tiêu định lượng";
  return ML_FEATURE_LABELS[feature] ?? "Chỉ tiêu định lượng mô hình";
}

export function sanitizeModelLimitationText(raw: string): string {
  return raw
    .replace(/\(\s*CORPORATE\s*\)/gi, "(doanh nghiệp phi tài chính)")
    .replace(/\bCORPORATE\b/g, "doanh nghiệp phi tài chính")
    .replace(
      /\bMARKET_ONLY_BEFORE_2026\b/g,
      PEER_STANDARDIZATION_LABELS.MARKET_ONLY_BEFORE_2026,
    )
    .replace(/\bSECTOR_Z_SCORE\b/g, PEER_STANDARDIZATION_LABELS.SECTOR_Z_SCORE)
    .replace(/\brevision_no\s*=\s*1\b/gi, "phiên bản đơn kỳ ban đầu")
    .replace(/\bVAS_TT200_2014\b/g, "TT200/2014")
    .replace(/\bVAS_TT99_2025\b/g, "TT99/2025")
    .replace(/\bREGIME_FALLBACK\b/g, "toàn thị trường cùng chuẩn mực")
    .replace(/\bissuer_id\b/g, "mã doanh nghiệp")
    .replace(/\.parquet\b/gi, "");
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
