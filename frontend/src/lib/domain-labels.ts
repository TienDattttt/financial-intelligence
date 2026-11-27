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
      "Nhóm cảnh báo rủi ro định lượng R1 (chuẩn toàn chu kỳ TTC): xác suất kiệt quệ 1 năm nằm ở vùng an toàn nhất.",
  },
  R2: {
    tier: "R2",
    badgeText: "R2",
    label: "R2 · Rủi ro thấp",
    shortLabel: "R2 · Thấp",
    tone: "low",
    rankOrder: 2,
    description:
      "Nhóm cảnh báo rủi ro định lượng R2 (chuẩn toàn chu kỳ TTC): rủi ro thấp, cấu trúc tài chính duy trì ổn định.",
  },
  R3: {
    tier: "R3",
    badgeText: "R3",
    label: "R3 · Rủi ro trung bình",
    shortLabel: "R3 · Trung bình",
    tone: "mid",
    rankOrder: 3,
    description:
      "Nhóm cảnh báo rủi ro định lượng R3 (chuẩn toàn chu kỳ TTC): mức trung bình, cần theo dõi xu hướng đòn bẩy và dòng tiền.",
  },
  R4: {
    tier: "R4",
    badgeText: "R4",
    label: "R4 · Rủi ro cao",
    shortLabel: "R4 · Cao",
    tone: "high",
    rankOrder: 4,
    description:
      "Nhóm cảnh báo rủi ro định lượng R4 (chuẩn toàn chu kỳ TTC): cảnh báo sớm rủi ro cao, cần rà soát cờ đỏ kế toán và thanh khoản.",
  },
  R5: {
    tier: "R5",
    badgeText: "R5",
    label: "R5 · Rủi ro rất cao",
    shortLabel: "R5 · Rất cao",
    tone: "high",
    rankOrder: 5,
    description:
      "Nhóm cảnh báo rủi ro định lượng R5 (chuẩn toàn chu kỳ TTC): vùng căng thẳng tài chính nghiêm trọng, rủi ro kiệt quệ rất cao.",
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
    label: "Chưa phân nhóm",
    shortLabel: "Chưa phân nhóm",
    tone: "neutral",
    rankOrder: 0,
    description: "Chưa có dữ liệu phân nhóm cảnh báo rủi ro định lượng R1–R5 cho kỳ hoặc phạm vi báo cáo này.",
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
  const safePercent = topPercent !== null ? Math.min(99.9, topPercent) : null;
  const topPercentLabel =
    topPercent === null || safePercent === null
      ? "Chưa có vị thế kỳ"
      : topPercent > 50
        ? `An toàn hơn ${formatNumber(safePercent, safePercent > 90 ? 1 : 0)}% DN`
        : `Top ${formatNumber(topPercent, topPercent < 10 ? 1 : 0)}% rủi ro nhất`;
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
  REVIEWED_NO_OPINION: "Đã soát xét (không nêu ý kiến)",
  EXCEPT_FOR: "Ngoại trừ",
  QUALIFIED: "Ngoại trừ",
  ADVERSE: "Trái ngược",
  DISCLAIMER: "Từ chối đưa ra ý kiến",
  NOT_APPLICABLE: "Chưa kiểm toán",
  UNKNOWN: "Chưa rõ",
};

export const PIOTROSKI_NA_REASON_LABELS: Record<string, string> = {
  TT200_TT99_TAXONOMY_GUARD: "Không tính do giao chuẩn TT200 → TT99",
  MISSING_DILUTION_DATA: "Thiếu dữ liệu pha loãng cổ phiếu",
  MISSING_PRIOR_YEAR_BASE: "Thiếu dữ liệu kỳ gốc năm trước",
  INSUFFICIENT_COMPONENTS: "Chưa đủ 9 tiêu chí thành phần",
};

export const PIOTROSKI_CRITERIA_LABELS: Record<string, string> = {
  F1_ROA: "ROA dương",
  F2_CFO: "Dòng tiền HĐKD (CFO) dương",
  F3_DELTA_ROA: "Cải thiện ROA so với năm trước",
  F4_ACCRUAL: "Chất lượng tiền mặt (CFO > LNST)",
  F4_ACCRUALS: "Chất lượng tiền mặt (CFO > LNST)",
  F5_DELTA_LEVERAGE: "Tỷ lệ nợ dài hạn giảm hoặc ổn định",
  F6_DELTA_LIQUIDITY: "Thanh toán hiện hành cải thiện",
  F7_NO_DILUTION: "Không phát hành pha loãng cổ phiếu",
  F8_DELTA_MARGIN: "Biên lợi nhuận gộp cải thiện",
  F9_DELTA_TURNOVER: "Vòng quay tổng tài sản cải thiện",
};

export interface PiotroskiNaDetail {
  summary: string;
  detailLine: string | null;
  tooltip: string;
}

export function formatPiotroskiNaDetail(
  reason?: string | null,
  missingCriteria?: readonly string[] | null,
): PiotroskiNaDetail {
  const summary =
    (reason && PIOTROSKI_NA_REASON_LABELS[reason]) ||
    "Chưa đủ tiêu chí thành phần";
  const mappedCriteria = Array.from(
    new Set(
      (missingCriteria ?? [])
        .map((code) => PIOTROSKI_CRITERIA_LABELS[code])
        .filter((label): label is string => Boolean(label)),
    ),
  );
  if (mappedCriteria.length === 0) {
    return {
      summary,
      detailLine: null,
      tooltip: summary,
    };
  }
  const detailLine =
    mappedCriteria.length <= 2
      ? `Thiếu tiêu chí: ${mappedCriteria.join(", ")}`
      : `Thiếu ${mappedCriteria.length}/9 tiêu chí thành phần`;
  const tooltip = `${summary} · Thiếu: ${mappedCriteria.join(", ")}`;
  return {
    summary:
      reason && PIOTROSKI_NA_REASON_LABELS[reason]
        ? summary
        : `Thiếu ${mappedCriteria.length} tiêu chí`,
    detailLine,
    tooltip,
  };
}

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
    description: "Xác suất kiệt quệ 1 năm đã hiệu chỉnh theo mô hình; xem nhóm cảnh báo rủi ro định lượng R1–R5.",
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

export const DTD_FLAG_META: Record<string, ValuationFlagDisplayMeta> = {
  VALID: {
    label: "Hợp lệ",
    explanation: "Khoảng cách vỡ nợ cấu trúc thị trường tính từ vốn hóa và dư nợ vay công bố.",
    isValid: true,
  },
  ZERO_FINANCIAL_DEBT: {
    label: "Không nợ vay",
    explanation: "Doanh nghiệp không có dư nợ vay tài chính trong kỳ báo cáo đã công bố.",
    isValid: false,
  },
  MISSING_BCTC_DEBT: {
    label: "Chưa có BCTC công bố",
    explanation: "Chưa có báo cáo cân đối kế toán hợp lệ đã công bố tại ngày giao dịch.",
    isValid: false,
  },
  STALE_BCTC_DEBT: {
    label: "BCTC quá 180 ngày",
    explanation: "Báo cáo tài chính gần nhất đã quá 180 ngày kể từ thời điểm công bố.",
    isValid: false,
  },
  INSUFFICIENT_TRADING_SESSIONS: {
    label: "Chưa đủ phiên giao dịch",
    explanation: "Chưa đủ 60 phiên giao dịch liên tục hoặc cổ phiếu đang tạm ngừng giao dịch.",
    isValid: false,
  },
  MISSING_SHARES: {
    label: "Thiếu số cổ phiếu",
    explanation: "Chưa có dữ liệu số cổ phiếu lưu hành hợp lệ tại ngày giao dịch.",
    isValid: false,
  },
};

export function getDtdFlagMeta(flag: string | null | undefined): ValuationFlagDisplayMeta {
  if (!flag) {
    return {
      label: "Chưa có dữ liệu",
      explanation: "Chưa đủ dữ liệu giá cổ phiếu và dư nợ vay để tính Merton DTD.",
      isValid: false,
    };
  }
  return (
    DTD_FLAG_META[flag] ?? {
      label: "Không áp dụng",
      explanation: "Chưa đủ điều kiện tính khoảng cách vỡ nợ Merton tại phiên này.",
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
  XGBoost_Merton_Ablation: "XGBoost + Merton DTD (Đối chứng thị trường)",
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
  PIT_REGIME_SYMMETRIC: "So sánh cùng kỳ theo chuẩn mực kế toán (đối xứng)",
  PIT_ICB: "So sánh cùng kỳ theo ngành ICB",
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
  ocf_to_total_liabilities: "CFO / Tổng nợ phải trả",
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
  merton_dtd: "Khoảng cách vỡ nợ cấu trúc thị trường (Merton DTD)",
  equity_volatility_1y: "Độ biến động giá cổ phiếu 1 năm (σE)",
  naive_edf: "Xác suất vỡ nợ ngụ ý cấu trúc (Merton EDF)",
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
    .replace(
      /\bPIT_REGIME_SYMMETRIC\b/g,
      PEER_STANDARDIZATION_LABELS.PIT_REGIME_SYMMETRIC,
    )
    .replace(/\bPIT_ICB\b/g, PEER_STANDARDIZATION_LABELS.PIT_ICB)
    .replace(/\brevision_no\s*=\s*1\b/gi, "phiên bản đơn kỳ ban đầu")
    .replace(/\bVAS_TT200_2014\b/g, "TT200/2014")
    .replace(/\bVAS_TT99_2025\b/g, "TT99/2025")
    .replace(/\bREGIME_FALLBACK\b/g, "toàn thị trường cùng chuẩn mực")
    .replace(/\bissuer_id\b/g, "mã doanh nghiệp")
    .replace(/\.parquet\b/gi, "");
}

export {
  STATEMENT_METRIC_LABELS,
  getStatementMetricLabel,
  formatFormulaVi,
} from "@/lib/statement-labels";

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
  CROSS_PROVIDER_DISCREPANCY_WARN: "Chênh lệch số liệu giữa hai nguồn cung cấp cùng cấp kiểm toán",
};

export const MODEL_ROLE_LABELS: Record<string, string> = {
  SCREENING_INDICATOR: "Chỉ báo tầm soát rủi ro",
  CALIBRATED_PD_MODEL: "Mô hình xác suất kiệt quệ hiệu chỉnh",
};

export {
  type EvidenceFormatType,
  RED_FLAG_EVIDENCE_META,
  RED_FLAG_CODE_LABELS,
  formatRedFlagCodeLabel,
} from "@/lib/red-flag-labels";

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
