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
  if (
    lowered === "overdue_or_late_count_4q" ||
    lowered === "consecutive_loss_quarters"
  ) {
    return `${formatNumber(Math.round(val), 0)} quý`;
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
    lowered.includes("distortion")
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

function describePeerComparison(
  feature: string,
  value: number,
  peerMedian: number,
  riskDirection: "increasing" | "decreasing",
): string {
  if (Math.abs(value - peerMedian) <= 1e-9) {
    return "tương đương trung vị ngành";
  }
  const direction = METRIC_DIRECTIONS[feature.toLowerCase()] ?? "NEUTRAL";
  const isHigher = value > peerMedian;
  if (direction === "HIGHER_IS_BETTER") {
    const isFavorable = isHigher;
    if (riskDirection === "increasing" && !isFavorable) {
      return "bất lợi hơn so với trung vị ngành";
    }
    if (riskDirection === "decreasing" && isFavorable) {
      return "tích cực hơn trung vị ngành";
    }
  } else if (direction === "LOWER_IS_BETTER") {
    const isFavorable = !isHigher;
    if (riskDirection === "increasing" && !isFavorable) {
      return "bất lợi hơn so với trung vị ngành";
    }
    if (riskDirection === "decreasing" && isFavorable) {
      return "tích cực hơn trung vị ngành";
    }
  }
  return isHigher ? "cao hơn trung vị ngành" : "thấp hơn trung vị ngành";
}

function formatLogOddsDelta(delta: number): string {
  const absStr = formatNumber(Math.abs(delta), 2);
  return delta >= 0 ? `+${absStr}` : `${MINUS_SIGN}${absStr}`;
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

  for (const drv of shap.top_risk_increasing.slice(0, 3)) {
    const hasVal =
      drv.value !== null && drv.value !== undefined && Number.isFinite(drv.value);
    const hasMed =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median);
    const valStr = hasVal ? formatDriverValue(drv.feature, drv.value as number) : null;
    const medStr = hasMed
      ? formatDriverValue(drv.feature, drv.peer_median as number)
      : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (valStr && medStr) {
      const relation = describePeerComparison(
        drv.feature,
        drv.value as number,
        drv.peer_median as number,
        "increasing",
      );
      sentences.push(
        `${drv.display_name_vn} (${valStr}) ${relation} (${medStr}), đóng góp làm tăng điểm rủi ro (${deltaStr} log-odds).`,
      );
    } else if (valStr) {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) đóng góp làm tăng điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    } else {
      sentences.push(
        `${drv.display_name_vn} góp phần làm tăng điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    }
  }

  for (const drv of shap.top_risk_decreasing.slice(0, 2)) {
    const hasVal =
      drv.value !== null && drv.value !== undefined && Number.isFinite(drv.value);
    const hasMed =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median);
    const valStr = hasVal ? formatDriverValue(drv.feature, drv.value as number) : null;
    const medStr = hasMed
      ? formatDriverValue(drv.feature, drv.peer_median as number)
      : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (valStr && medStr) {
      const relation = describePeerComparison(
        drv.feature,
        drv.value as number,
        drv.peer_median as number,
        "decreasing",
      );
      sentences.push(
        `${drv.display_name_vn} (${valStr}) ${relation} (${medStr}), giúp giảm áp lực rủi ro (${deltaStr} log-odds).`,
      );
    } else if (valStr) {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) giúp giảm điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    }
  }

  return sentences;
}
