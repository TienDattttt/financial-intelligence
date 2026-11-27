/**
 * Canonical Vietnamese legal compliance constants and TT99 MRM gate labels (V3-01..V3-03).
 * Complies with Decree 88/2014/ND-CP, Securities Law 56/2024/QH15, and PDPL Law 91/2025/QH15.
 */

import type { RiskTone } from "@/lib/domain-labels";

export const COMPLIANCE_DISCLAIMER_VI =
  "Thông tin chỉ mang tính tham khảo phân tích định lượng từ dữ liệu lịch sử, " +
  "không phải khuyến nghị đầu tư hay đánh giá tín dụng chính thức theo " +
  "Nghị định 88/2014/NĐ-CP và Luật Chứng khoán 2019 (sửa đổi bởi Luật 56/2024/QH15).";

export const RISK_TIER_LEGAL_LABEL_VI =
  "Nhóm cảnh báo rủi ro tài chính định lượng (R1–R5)";

export const COMPLIANCE_LAW_REFERENCES_VI: readonly string[] = [
  "Nghị định 88/2014/NĐ-CP",
  "Luật Chứng khoán 54/2019/QH14 (sửa đổi bởi Luật 56/2024/QH15)",
  "Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15",
];

export const PDPL_CONSENT_SUMMARY_VI =
  "Dữ liệu cá nhân được tách bạch theo từng mục đích đồng ý (tài khoản cốt lõi, thông báo cảnh báo, và truy vấn LLM xuyên biên giới) theo Luật 91/2025/QH15.";

export type TT99GateVerdict =
  | "INSUFFICIENT_TT99_SAMPLES"
  | "SHADOW_MONITORING"
  | "READY_FOR_GA"
  | "DRIFT_BREACH";

export interface TT99GateDisplayMeta {
  verdict: TT99GateVerdict;
  label: string;
  tone: RiskTone;
  description: string;
}

export const TT99_GATE_VERDICT_LABELS: Record<TT99GateVerdict, TT99GateDisplayMeta> = {
  INSUFFICIENT_TT99_SAMPLES: {
    verdict: "INSUFFICIENT_TT99_SAMPLES",
    label: "Chế độ Shadow · Chưa đủ mẫu TT99 (< 30)",
    tone: "neutral",
    description:
      "Dữ liệu BCTC theo chuẩn TT99/2025 chưa đạt 30 quan sát; chỉ chạy giám sát song song và giữ nguyên mô hình chuẩn TT200.",
  },
  SHADOW_MONITORING: {
    verdict: "SHADOW_MONITORING",
    label: "Đang theo dõi Shadow TT99 (30–149 mẫu)",
    tone: "mid",
    description:
      "Đã có mẫu TT99/2025 ban đầu trong ngưỡng ổn định, tiếp tục theo dõi đến khi đạt tối thiểu 150 quan sát.",
  },
  READY_FOR_GA: {
    verdict: "READY_FOR_GA",
    label: "Đạt chuẩn cổng MRM TT99 (Sẵn sàng GA)",
    tone: "low",
    description:
      "Đạt tối thiểu 150 quan sát TT99/2025 với max PSI < 0,25 và mức tăng thiếu dữ liệu < 15 điểm phần trăm.",
  },
  DRIFT_BREACH: {
    verdict: "DRIFT_BREACH",
    label: "Vượt ngưỡng dịch chuyển TT99 · Chặn nâng cấp",
    tone: "high",
    description:
      "Phát hiện PSI >= 0,25 hoặc mức tăng thiếu dữ liệu >= 15 điểm phần trăm; tự động chặn chuyển mô hình sang GA.",
  },
};

export function getTt99GateVerdictMeta(
  verdict: string | null | undefined,
): TT99GateDisplayMeta {
  if (
    verdict === "INSUFFICIENT_TT99_SAMPLES" ||
    verdict === "SHADOW_MONITORING" ||
    verdict === "READY_FOR_GA" ||
    verdict === "DRIFT_BREACH"
  ) {
    return TT99_GATE_VERDICT_LABELS[verdict];
  }
  return TT99_GATE_VERDICT_LABELS.INSUFFICIENT_TT99_SAMPLES;
}
