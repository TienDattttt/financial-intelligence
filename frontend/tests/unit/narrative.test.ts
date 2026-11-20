import { describe, expect, it } from "vitest";
import { MINUS_SIGN } from "@/lib/format";
import { buildShapNarrativeVi, type ShapExplanationInput } from "@/lib/narrative";

describe("narrative.ts deterministic TreeSHAP explanation", () => {
  const sampleShap: ShapExplanationInput = {
    base_value: -2.4,
    prediction_score: -1.15,
    other_features_delta: 0.05,
    top_risk_increasing: [
      {
        feature: "debt_to_equity",
        display_name_vn: "Nợ vay / Vốn chủ sở hữu",
        value: 2.35,
        peer_median: 0.85,
        shap_delta: 0.92,
      },
      {
        feature: "gross_margin",
        display_name_vn: "Biên lợi nhuận gộp",
        value: 0.082,
        peer_median: 0.165,
        shap_delta: 0.45,
      },
    ],
    top_risk_decreasing: [
      {
        feature: "dso",
        display_name_vn: "Số ngày phải thu bình quân",
        value: 28,
        peer_median: 46,
        shap_delta: -0.34,
      },
    ],
  };

  it("produces deterministic reproducible Vietnamese sentences in log-odds units", () => {
    const first = buildShapNarrativeVi(sampleShap);
    const second = buildShapNarrativeVi(sampleShap);
    expect(first).toEqual(second);
    expect(first).toHaveLength(3);
    expect(first[0]).toContain("Nợ vay / Vốn chủ sở hữu (2,35)");
    expect(first[0]).toContain("trung vị ngành (0,85)");
    expect(first[0]).toContain("+0,92 log-odds");
    expect(first[1]).toContain("Biên lợi nhuận gộp (8,2%)");
    expect(first[2]).toContain("Số ngày phải thu bình quân (28 ngày)");
    expect(first[2]).toContain(`${MINUS_SIGN}0,34 log-odds`);
  });

  it("handles missing values cleanly and returns empty array for null input", () => {
    expect(buildShapNarrativeVi(null)).toEqual([]);
    expect(buildShapNarrativeVi(undefined)).toEqual([]);

    const partial = buildShapNarrativeVi({
      base_value: -1,
      prediction_score: -0.5,
      other_features_delta: 0,
      top_risk_increasing: [
        {
          feature: "current_ratio",
          display_name_vn: "Thanh toán hiện hành",
          value: null,
          peer_median: null,
          shap_delta: 0.4,
        },
      ],
      top_risk_decreasing: [],
    });
    expect(partial).toEqual([
      "Thanh toán hiện hành góp phần làm tăng điểm rủi ro mô hình (+0,40 log-odds).",
    ]);
  });
});
