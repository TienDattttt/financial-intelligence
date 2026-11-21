import { afterEach, describe, expect, it, vi } from "vitest";
import { formatRedFlagEvidence } from "@/components/financial/RedFlagChecklist";
import {
  DERIVATION_METHOD_LABELS,
  formatFormulaVi,
  formatSourceProviderLabel,
  getRatioStatusFlagMeta,
  getStatementMetricLabel,
  MODEL_ROLE_LABELS,
  PERIOD_TYPE_LABELS,
  PUBLISHED_AT_BASIS_LABELS,
  QUALITY_RULE_LABELS,
  QUALITY_STATUS_META,
  selectMlScoreForPeriod,
} from "@/lib/domain-labels";
import { MINUS_SIGN } from "@/lib/format";
import {
  getLastViewedTickerSnapshot,
  setLastViewedTicker,
} from "@/lib/watchlist-store";

describe("UI-03 domain labels, lineage formatting, and sync helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps global period labels strictly TTM / Annual without exposing Quarterly", () => {
    expect(PERIOD_TYPE_LABELS.QUARTER).toBe("TTM (4 quý trượt)");
    expect(PERIOD_TYPE_LABELS.ANNUAL).toBe("Theo năm (Annual)");
    expect(Object.values(PERIOD_TYPE_LABELS).join(" ")).not.toContain("Quarterly");
  });

  it("maps VAS metric IDs and ratio formulas to Vietnamese without leaking BS_TOTAL_EQUITY", () => {
    expect(getStatementMetricLabel("BS_TOTAL_EQUITY")).toBe("Vốn chủ sở hữu");
    expect(getStatementMetricLabel("IS_NET_INCOME")).toBe(
      "Lợi nhuận sau thuế TNDN",
    );

    const translated = formatFormulaVi(
      "IS_NET_INCOME / Avg(BS_TOTAL_EQUITY_t, BS_TOTAL_EQUITY_{t-4})",
    );
    expect(translated).toContain("Lợi nhuận sau thuế TNDN");
    expect(translated).toContain("Vốn chủ sở hữu (kỳ hiện tại)");
    expect(translated).not.toContain("BS_TOTAL_EQUITY");
    expect(translated).not.toContain("IS_NET_INCOME");
  });

  it("maps ratio status_flag values to distinct badge tones and Vietnamese labels", () => {
    expect(getRatioStatusFlagMeta("VALID")).toEqual({
      label: "Hợp lệ",
      tone: "low",
    });
    expect(getRatioStatusFlagMeta("DERIVED_Q4_INPUT")).toEqual({
      label: "Có Q4 suy diễn",
      tone: "mid",
    });
    expect(getRatioStatusFlagMeta("NEGATIVE_OR_ZERO_EQUITY")).toEqual({
      label: "VCSH âm hoặc bằng 0",
      tone: "high",
    });
    expect(getRatioStatusFlagMeta("MISSING_INPUT")).toEqual({
      label: "Thiếu chỉ tiêu đầu vào",
      tone: "neutral",
    });
  });

  it("maps provenance and quality/model enums to clean Vietnamese labels", () => {
    expect(DERIVATION_METHOD_LABELS.REPORTED_DIRECTLY).toBe(
      "Công bố trực tiếp trên BCTC",
    );
    expect(PUBLISHED_AT_BASIS_LABELS.EXACT_FILING).toBe(
      "Theo ngày công bố thực tế",
    );
    expect(formatSourceProviderLabel("hose_pdf_extract")).toBe(
      "BCTC niêm yết (PDF)",
    );
    expect(QUALITY_STATUS_META.PASS.label).toBe("Đạt chuẩn");
    expect(QUALITY_RULE_LABELS.BS_IDENTITY_MISMATCH).toContain("Tổng Tài sản");
    expect(MODEL_ROLE_LABELS.SCREENING_INDICATOR).toBe(
      "Chỉ báo tầm soát rủi ro",
    );
  });

  it("formats red-flag evidence JSON into Vietnamese labels and units without raw keys", () => {
    const raw = JSON.stringify({
      source_filing_ids: ["FIL_HPG_2025_Q4"],
      net_non_customer_receivables_st: 12_500_000_000_000,
      other_receivables_to_assets: 0.312,
      dso_change_days: 22,
      audit_opinion: "EXCEPT_FOR",
      is_filed_late: true,
      BS_TOTAL_EQUITY: -450_000_000_000,
    });
    const formatted = formatRedFlagEvidence(raw);
    const map = new Map(formatted);

    expect(map.get("Phải thu ngoài KH ngắn hạn (ròng)")).toBe("12.500,0 tỷ");
    expect(map.get("Phải thu ngoài KH / Tổng TS")).toBe("31,2%");
    expect(map.get("Biến động DSO")).toBe("22 ngày");
    expect(map.get("Ý kiến kiểm toán")).toBe("Ngoại trừ");
    expect(map.get("Nộp BCTC trễ hạn")).toBe("Có");
    expect(map.get("Vốn chủ sở hữu")).toBe("(450,0) tỷ");

    const joinedKeys = formatted.map(([k]) => k).join(" ");
    expect(joinedKeys).not.toContain("BS_TOTAL_EQUITY");
    expect(joinedKeys).not.toContain("source_filing_ids");
    expect(joinedKeys).not.toContain("is_filed_late");
  });

  it("aligns ML score selection with selected global periodEndDate", () => {
    const scores = [
      { period_end_date: "2024-12-31", score: 0.1 },
      { period_end_date: "2025-06-30", score: 0.2 },
      { period_end_date: "2025-12-31", score: 0.35 },
    ];

    expect(selectMlScoreForPeriod(scores, null)?.period_end_date).toBe("2025-12-31");
    expect(selectMlScoreForPeriod(scores, "2025-06-30")?.score).toBe(0.2);
    expect(selectMlScoreForPeriod(scores, "2025-09-30")?.period_end_date).toBe(
      "2025-06-30",
    );
    expect(selectMlScoreForPeriod(scores, "2023-12-31")).toBeNull();
  });

  it("remembers last viewed ticker in localStorage without hardcoding HPG", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => {
          store.set(k, v);
        },
      },
    });

    setLastViewedTicker("fpt");
    expect(getLastViewedTickerSnapshot()).toBe("FPT");
    expect(store.get("finintel:last-ticker:v1")).toBe("FPT");

    setLastViewedTicker("invalid ticker!");
    expect(getLastViewedTickerSnapshot()).toBe("FPT");

    expect(MINUS_SIGN).toBe("\u2212");
  });
});
