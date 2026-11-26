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

  it("filters null/near-zero drivers cleanly with fallback and returns empty array for null/empty input", () => {
    expect(buildShapNarrativeVi(null)).toEqual([]);
    expect(buildShapNarrativeVi(undefined)).toEqual([]);
    expect(
      buildShapNarrativeVi({
        base_value: -1,
        prediction_score: -1,
        other_features_delta: 0,
        top_risk_increasing: [],
        top_risk_decreasing: [],
      }),
    ).toEqual([]);

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
        {
          feature: "roa",
          display_name_vn: "ROA",
          value: 0.08,
          peer_median: 0.06,
          shap_delta: 0.002, // < 0.005 threshold, filtered out
        },
      ],
      top_risk_decreasing: [],
    });
    expect(partial).toEqual([
      "Không có chỉ số định lượng đơn lẻ nào có đóng góp đáng kể trong kỳ này (các yếu tố đều ở mức trung tính hoặc thiếu dữ liệu đầu vào).",
    ]);
  });

  it("formats sector_z_* features in sigma units without percentage or day conversion", () => {
    const out = buildShapNarrativeVi({
      base_value: -2.0,
      prediction_score: -1.4,
      other_features_delta: 0,
      top_risk_increasing: [
        {
          feature: "sector_z_ebit_margin",
          display_name_vn: "Biên EBIT chuẩn hóa ngành (Z-Score)",
          value: -1.5,
          peer_median: 0,
          shap_delta: 0.42,
        },
        {
          feature: "sector_z_cash_conversion_cycle",
          display_name_vn: "Chu kỳ tiền mặt chuẩn hóa ngành (Z-Score)",
          value: 2.0,
          peer_median: 0,
          shap_delta: 0.31,
        },
      ],
      top_risk_decreasing: [
        {
          feature: "sector_z_asset_turnover",
          display_name_vn: "Vòng quay tài sản chuẩn hóa ngành (Z-Score)",
          value: 1.25,
          peer_median: 0,
          shap_delta: -0.25,
        },
      ],
    });
    expect(out[0]).toContain(`(${MINUS_SIGN}1,50 σ)`);
    expect(out[0]).toContain("(0,00 σ)");
    expect(out[0]).not.toContain("%");
    expect(out[0]).toContain("bất lợi hơn so với trung vị ngành");
    expect(out[1]).toContain("(+2,00 σ)");
    expect(out[1]).not.toContain("ngày");
    expect(out[1]).toContain("bất lợi hơn so với trung vị ngành");
    expect(out[2]).toContain("(+1,25 σ)");
    expect(out[2]).toContain("tích cực hơn trung vị ngành");
  });

  it("verifies value vs peer_median direction before claiming favorable or unfavorable", () => {
    const out = buildShapNarrativeVi({
      base_value: -2.0,
      prediction_score: -1.8,
      other_features_delta: 0,
      top_risk_increasing: [
        {
          feature: "roa",
          display_name_vn: "ROA",
          value: 0.12,
          peer_median: 0.08,
          shap_delta: 0.15,
        },
        {
          feature: "current_ratio",
          display_name_vn: "Thanh toán hiện hành",
          value: 1.5,
          peer_median: 1.5,
          shap_delta: 0.05,
        },
      ],
      top_risk_decreasing: [
        {
          feature: "debt_to_equity",
          display_name_vn: "Nợ vay / VCSH",
          value: 1.8,
          peer_median: 1.1,
          shap_delta: -0.1,
        },
      ],
    });
    expect(out[0]).toContain("cao hơn trung vị ngành");
    expect(out[0]).toContain("ghi nhận tác động tương tác trong mô hình (+0,15 log-odds)");
    expect(out[0]).not.toContain("bất lợi hơn");
    expect(out[0]).not.toContain("đóng góp làm tăng");
    expect(out[1]).toContain("tương đương trung vị ngành");
    expect(out[1]).toContain("ghi nhận tác động tương tác trong mô hình (+0,05 log-odds)");
    expect(out[2]).toContain("cao hơn trung vị ngành");
    expect(out[2]).toContain(`ghi nhận tác động tương tác trong mô hình (${MINUS_SIGN}0,10 log-odds)`);
    expect(out[2]).not.toContain("tích cực hơn");
    expect(out[2]).not.toContain("giúp giảm áp lực");
  });

  it("uses neutral model-interaction phrasing on counter-intuitive drivers and excludes (+0 log-odds) noise (U2)", () => {
    const out = buildShapNarrativeVi({
      base_value: -2.0,
      prediction_score: -1.7,
      other_features_delta: 0,
      top_risk_increasing: [
        {
          feature: "roa",
          display_name_vn: "ROA",
          value: 0.14,
          peer_median: 0.08,
          shap_delta: 0.18,
        },
        {
          feature: "ebit_margin",
          display_name_vn: "Biên EBIT",
          value: 0.11,
          peer_median: 0.09,
          shap_delta: 0.004,
        },
      ],
      top_risk_decreasing: [
        {
          feature: "dso",
          display_name_vn: "Số ngày phải thu bình quân",
          value: 95,
          peer_median: 42,
          shap_delta: -0.12,
        },
      ],
    });
    expect(out).toHaveLength(2);
    expect(out[0]).toBe(
      "ROA (14,0%) đang ở mức cao hơn trung vị ngành (8,0%), ghi nhận tác động tương tác trong mô hình (+0,18 log-odds).",
    );
    expect(out[1]).toBe(
      `Số ngày phải thu bình quân (95 ngày) đang ở mức cao hơn trung vị ngành (42 ngày), ghi nhận tác động tương tác trong mô hình (${MINUS_SIGN}0,12 log-odds).`,
    );
    expect(out.join(" ")).not.toContain("Biên EBIT");
    expect(out.join(" ")).not.toContain("+0,00 log-odds");
  });

  it("formats ML-05 VN-specific features (overdue/late count, consecutive loss, asset size pct) cleanly", () => {
    const out = buildShapNarrativeVi({
      base_value: -2.2,
      prediction_score: -0.9,
      other_features_delta: 0,
      top_risk_increasing: [
        {
          feature: "overdue_or_late_count_4q",
          display_name_vn: "Số quý nộp trễ hoặc quá hạn công bố BCTC (4Q)",
          value: 3,
          peer_median: 0,
          shap_delta: 0.55,
        },
        {
          feature: "consecutive_loss_quarters",
          display_name_vn: "Số quý lỗ ròng liên tiếp",
          value: 6,
          peer_median: 0,
          shap_delta: 0.48,
        },
        {
          feature: "cross_sectional_asset_size_pct",
          display_name_vn: "Phân vị quy mô tổng tài sản theo quý",
          value: 82.5,
          peer_median: 50.0,
          shap_delta: 0.18,
        },
      ],
      top_risk_decreasing: [
        {
          feature: "overdue_or_late_count_4q",
          display_name_vn: "Số quý nộp trễ hoặc quá hạn công bố BCTC (4Q)",
          value: 0,
          peer_median: 1,
          shap_delta: -0.22,
        },
      ],
    });
    expect(out[0]).toContain("(3 quý)");
    expect(out[0]).toContain("bất lợi hơn so với trung vị ngành (0 quý)");
    expect(out[1]).toContain("(6 quý)");
    expect(out[1]).toContain("bất lợi hơn so với trung vị ngành (0 quý)");
    expect(out[2]).toContain("(82,5%)");
    expect(out[2]).toContain("cao hơn trung vị ngành (50,0%)");
    expect(out[2]).not.toContain("bất lợi hơn");
    expect(out[3]).toContain("(0 quý)");
    expect(out[3]).toContain("tích cực hơn trung vị ngành (1 quý)");
  });
});
