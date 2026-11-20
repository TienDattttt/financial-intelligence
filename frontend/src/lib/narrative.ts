import { formatNumber, MINUS_SIGN } from "@/lib/format";

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

function formatDriverValue(feature: string, val: number): string {
  const lowered = feature.toLowerCase();
  if (
    lowered.includes("margin") ||
    lowered.includes("roa") ||
    lowered.includes("roe") ||
    lowered.includes("roic") ||
    lowered.includes("to_assets") ||
    lowered.includes("growth") ||
    lowered.includes("ratio") && lowered.includes("core_to_ebt") ||
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
    const valStr =
      drv.value !== null && drv.value !== undefined && Number.isFinite(drv.value)
        ? formatDriverValue(drv.feature, drv.value)
        : null;
    const medStr =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median)
        ? formatDriverValue(drv.feature, drv.peer_median)
        : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (valStr && medStr) {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) bất lợi hơn so với trung vị ngành (${medStr}), đóng góp làm tăng điểm rủi ro (${deltaStr} log-odds).`,
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
    const valStr =
      drv.value !== null && drv.value !== undefined && Number.isFinite(drv.value)
        ? formatDriverValue(drv.feature, drv.value)
        : null;
    const medStr =
      drv.peer_median !== null &&
      drv.peer_median !== undefined &&
      Number.isFinite(drv.peer_median)
        ? formatDriverValue(drv.feature, drv.peer_median)
        : null;
    const deltaStr = formatLogOddsDelta(drv.shap_delta);

    if (valStr && medStr) {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) tích cực hơn trung vị ngành (${medStr}), giúp giảm áp lực rủi ro (${deltaStr} log-odds).`,
      );
    } else if (valStr) {
      sentences.push(
        `${drv.display_name_vn} (${valStr}) giúp giảm điểm rủi ro mô hình (${deltaStr} log-odds).`,
      );
    }
  }

  return sentences;
}
