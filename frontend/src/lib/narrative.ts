import {
  formatNumber,
  formatPercentPoints,
  MINUS_SIGN,
} from "@/lib/format";

export interface ShapDriverInput {
  feature: string;
  display_name_vn: string;
  value?: number | null;
  peer_median?: number | null;
  shap_delta: number;
}

export interface ShapExplanationInput {
  base_value: number;
  prediction_score: number;
  other_features_delta: number;
  top_risk_increasing: readonly ShapDriverInput[];
  top_risk_decreasing: readonly ShapDriverInput[];
}

type MetricDirection = "HIGHER_IS_BETTER" | "LOWER_IS_BETTER" | "NEUTRAL";

const METRIC_DIRECTIONS: Record<string, MetricDirection> = {
  roa: "HIGHER_IS_BETTER",
  roe_total: "HIGHER_IS_BETTER",
  roe_parent: "HIGHER_IS_BETTER",
  roic: "HIGHER_IS_BETTER",
  ebit_margin: "HIGHER_IS_BETTER",
  ebitda_margin: "HIGHER_IS_BETTER",
  gross_margin: "HIGHER_IS_BETTER",
  operating_margin: "HIGHER_IS_BETTER",
  net_profit_margin: "HIGHER_IS_BETTER",
  core_operating_margin: "HIGHER_IS_BETTER",
  core_to_ebt_ratio: "HIGHER_IS_BETTER",
  current_ratio: "HIGHER_IS_BETTER",
  quick_ratio: "HIGHER_IS_BETTER",
  cash_ratio: "HIGHER_IS_BETTER",
  ocf_to_current_liabilities: "HIGHER_IS_BETTER",
  ocf_to_net_income: "HIGHER_IS_BETTER",
  interest_coverage: "HIGHER_IS_BETTER",
  asset_turnover: "HIGHER_IS_BETTER",
  inventory_turnover: "HIGHER_IS_BETTER",
  receivables_turnover: "HIGHER_IS_BETTER",
  fcf_to_revenue: "HIGHER_IS_BETTER",
  equity_to_assets: "HIGHER_IS_BETTER",
  altman_z_double_prime: "HIGHER_IS_BETTER",
  piotroski_f_score: "HIGHER_IS_BETTER",
  revenue_growth_yoy: "HIGHER_IS_BETTER",
  ebit_growth_yoy: "HIGHER_IS_BETTER",
  net_income_parent_growth_yoy: "HIGHER_IS_BETTER",
  sector_z_asset_turnover: "HIGHER_IS_BETTER",
  sector_z_ebit_margin: "HIGHER_IS_BETTER",
  liabilities_to_assets: "LOWER_IS_BETTER",
  financial_debt_to_assets: "LOWER_IS_BETTER",
  debt_to_equity: "LOWER_IS_BETTER",
  net_debt_to_equity: "LOWER_IS_BETTER",
  net_debt_to_ebitda: "LOWER_IS_BETTER",
  cash_conversion_cycle: "LOWER_IS_BETTER",
  dso: "LOWER_IS_BETTER",
  dio: "LOWER_IS_BETTER",
  accruals_to_assets: "LOWER_IS_BETTER",
  non_core_distortion_to_assets: "LOWER_IS_BETTER",
  sector_z_cash_conversion_cycle: "LOWER_IS_BETTER",
  sector_z_debt_to_equity: "LOWER_IS_BETTER",
  overdue_or_late_count_4q: "LOWER_IS_BETTER",
  consecutive_loss_quarters: "LOWER_IS_BETTER",
  cross_sectional_asset_size_pct: "NEUTRAL",
  delta_roa_4q: "HIGHER_IS_BETTER",
  delta_liabilities_to_assets_4q: "LOWER_IS_BETTER",
  delta_current_ratio_4q: "NEUTRAL",
  payables_turnover: "NEUTRAL",
  dpo: "NEUTRAL",
  merton_dtd: "HIGHER_IS_BETTER",
  equity_volatility_1y: "LOWER_IS_BETTER",
  naive_edf: "LOWER_IS_BETTER",
};

function formatZScore(val: number): string {
  const clean = Math.abs(val) < 0.005 ? 0 : val;
  const absStr = new Intl.NumberFormat("vi-VN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(clean));
  if (clean === 0) return `${absStr} σ`;
  return clean > 0 ? `+${absStr} σ` : `${MINUS_SIGN}${absStr} σ`;
}

function formatDriverValue(feature: string, val: number): string {
  const lowered = feature.toLowerCase();
  if (lowered.startsWith("sector_z_")) {
    return formatZScore(val);
  }
  if (lowered === "merton_dtd") {
    return `${formatNumber(val, 2)} σ`;
  }
  if (
    lowered === "overdue_or_late_count_4q" ||
    lowered === "consecutive_loss_quarters"
  ) {
    return `${formatNumber(Math.round(val), 0)} quý`;
  }
  if (lowered === "piotroski_f_score") {
    return `${formatNumber(Math.round(val), 0)}/9`;
  }
  if (lowered === "cross_sectional_asset_size_pct" || lowered.endsWith("_pct")) {
    return `${new Intl.NumberFormat("vi-VN", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(val)}%`;
  }
  if (lowered === "delta_roa_4q" || lowered === "delta_liabilities_to_assets_4q") {
    return formatPercentPoints(val, 1);
  }
  if (lowered === "delta_current_ratio_4q") {
    const clean = Math.abs(val) < 0.005 ? 0 : val;
    const formatted = formatNumber(clean, 2);
    return clean > 0 ? `+${formatted}` : formatted;
  }
  if (
    lowered.includes("margin") ||
    lowered.includes("roa") ||
    lowered.includes("roe") ||
    lowered.includes("roic") ||
    lowered.includes("to_assets") ||
    lowered.includes("growth") ||
    (lowered.includes("ratio") && lowered.includes("core_to_ebt")) ||
    lowered.includes("fcf_to_revenue") ||
    lowered.includes("distortion") ||
    lowered === "equity_volatility_1y" ||
    lowered === "naive_edf"
  ) {
    return `${formatNumber(val * 100, 1)}%`;
  }
  if (
    lowered.includes("dso") ||
    lowered.includes("dio") ||
    lowered.includes("dpo") ||
    lowered.includes("cycle")
  ) {
    return `${formatNumber(val, 0)} ngày`;
  }
  return formatNumber(val, 2);
}

interface PeerComparisonResult {
  relation: string;
  isCounterIntuitive: boolean;
  isEqual: boolean;
  isNeutralDirection: boolean;
}

function describePeerComparison(
  feature: string,
  value: number,
  peerMedian: number,
  riskDirection: "increasing" | "decreasing",
): PeerComparisonResult {
  if (Math.abs(value - peerMedian) <= 1e-9) {
    return {
      relation: "tương đương trung vị ngành",
      isCounterIntuitive: false,
      isEqual: true,
      isNeutralDirection: false,
    };
  }
  const direction = METRIC_DIRECTIONS[feature.toLowerCase()] ?? "NEUTRAL";
  const isHigher = value > peerMedian;
  if (direction === "HIGHER_IS_BETTER") {
    const isFavorable = isHigher;
    if (riskDirection === "increasing" && !isFavorable) {
      return {
        relation: "bất lợi hơn so với trung vị ngành",
        isCounterIntuitive: false,
        isEqual: false,
        isNeutralDirection: false,
      };
    }
    if (riskDirection === "decreasing" && isFavorable) {
      return {
        relation: "tích cực hơn trung vị ngành",
        isCounterIntuitive: false,
        isEqual: false,
        isNeutralDirection: false,
      };
    }
    return {
      relation: isHigher ? "cao hơn trung vị ngành" : "thấp hơn trung vị ngành",
      isCounterIntuitive: true,
      isEqual: false,
      isNeutralDirection: false,
    };
  }
  if (direction === "LOWER_IS_BETTER") {
    const isFavorable = !isHigher;
    if (riskDirection === "increasing" && !isFavorable) {
      return {
        relation: "bất lợi hơn so với trung vị ngành",
        isCounterIntuitive: false,
        isEqual: false,
        isNeutralDirection: false,
      };
    }
    if (riskDirection === "decreasing" && isFavorable) {
      return {
        relation: "tích cực hơn trung vị ngành",
        isCounterIntuitive: false,
        isEqual: false,
        isNeutralDirection: false,
      };
    }
    return {
      relation: isHigher ? "cao hơn trung vị ngành" : "thấp hơn trung vị ngành",
      isCounterIntuitive: true,
      isEqual: false,
      isNeutralDirection: false,
    };
  }
  return {
    relation: isHigher ? "cao hơn trung vị ngành" : "thấp hơn trung vị ngành",
    isCounterIntuitive: false,
    isEqual: false,
    isNeutralDirection: true,
  };
}

function formatLogOddsDelta(delta: number): string {
  const absStr = formatNumber(Math.abs(delta), 2);
  return delta >= 0 ? `+${absStr}` : `${MINUS_SIGN}${absStr}`;
}

function isMaterialValidDriver(drv: ShapDriverInput): boolean {
  return (
    drv.value !== null &&
    drv.value !== undefined &&
    Number.isFinite(drv.value) &&
    Number.isFinite(drv.shap_delta) &&
    Math.abs(drv.shap_delta) >= 0.005
  );
}

/**
 * Deterministic rule-based Vietnamese explanation for TreeSHAP drivers.
 * Zero LLM generation — 100% reproducible from numerical inputs.
 */
export function buildShapNarrativeVi(
  shap: ShapExplanationInput | null | undefined,
): string[] {
  if (!shap) return [];
  const sentences: string[] = [];

  const validIncreasing = shap.top_risk_increasing
    .filter(isMaterialValidDriver)
    .slice(0, 3);
  const validDecreasing = shap.top_risk_decreasing
    .filter(isMaterialValidDriver)
    .slice(0, 2);

  for (const drv of validIncreasing) {
    const hasMed =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median);
    const valStr = formatDriverValue(drv.feature, drv.value as number);
    const medStr = hasMed
      ? formatDriverValue(drv.feature, drv.peer_median as number)
      : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (medStr) {
      const cmp = describePeerComparison(
        drv.feature,
        drv.value as number,
        drv.peer_median as number,
        "increasing",
      );
      if (cmp.isCounterIntuitive) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) đang ở mức ${cmp.relation} (${medStr}), ghi nhận tác động tương tác trong mô hình (${deltaStr} log-odds).`,
        );
      } else if (cmp.isEqual) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), ghi nhận tác động tương tác trong mô hình (${deltaStr} log-odds).`,
        );
      } else if (cmp.isNeutralDirection) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), ghi nhận tác động trong mô hình (${deltaStr} log-odds).`,
        );
      } else {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), đóng góp làm tăng điểm rủi ro (${deltaStr} log-odds).`,
        );
      }
    } else {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) đóng góp làm tăng điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    }
  }

  for (const drv of validDecreasing) {
    const hasMed =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median);
    const valStr = formatDriverValue(drv.feature, drv.value as number);
    const medStr = hasMed
      ? formatDriverValue(drv.feature, drv.peer_median as number)
      : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (medStr) {
      const cmp = describePeerComparison(
        drv.feature,
        drv.value as number,
        drv.peer_median as number,
        "decreasing",
      );
      if (cmp.isCounterIntuitive) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) đang ở mức ${cmp.relation} (${medStr}), ghi nhận tác động tương tác trong mô hình (${deltaStr} log-odds).`,
        );
      } else if (cmp.isEqual) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), ghi nhận tác động tương tác trong mô hình (${deltaStr} log-odds).`,
        );
      } else if (cmp.isNeutralDirection) {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), ghi nhận tác động trong mô hình (${deltaStr} log-odds).`,
        );
      } else {
        sentences.push(
          `${drv.display_name_vn} (${valStr}) ${cmp.relation} (${medStr}), giúp giảm áp lực rủi ro (${deltaStr} log-odds).`,
        );
      }
    } else {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) giúp giảm điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    }
  }

  if (
    sentences.length === 0 &&
    shap.top_risk_increasing.length + shap.top_risk_decreasing.length > 0
  ) {
    return [
      "Không có chỉ số định lượng đơn lẻ nào có đóng góp đáng kể trong kỳ này (các yếu tố đều ở mức trung tính hoặc thiếu dữ liệu đầu vào).",
    ];
  }

  return sentences;
}
