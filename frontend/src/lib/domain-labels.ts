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
  QUARTER: "Theo quý",
  ANNUAL: "Theo năm",
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
