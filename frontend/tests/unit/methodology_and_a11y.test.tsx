// @vitest-environment jsdom
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MethodologyPage from "@/app/methodology/page";
import { MethodologyFormulaCards } from "@/components/financial/MethodologyFormulaCards";

const mockReplace = vi.fn();
let mockTabParam: string | null = null;

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: mockReplace,
  }),
  useSearchParams: () => ({
    get: (key: string) => (key === "tab" ? mockTabParam : null),
  }),
}));

vi.mock("@/components/providers/app-providers", () => ({
  useAppState: () => ({
    snapshotId: "snap_test_001",
  }),
}));

vi.mock("@/hooks/use-finintel-queries", () => ({
  useMetaDictionary: () => ({
    isLoading: false,
    isError: false,
    data: {
      snapshot_id: "snap_test_001",
      ratios: [
        {
          ratio_name: "roe_total",
          display_name_vn: "ROE tổng hợp",
          category: "PROFITABILITY",
          format_type: "PERCENT",
          direction: "HIGHER_IS_BETTER",
          formula_display: "IS_NET_INCOME / BS_TOTAL_EQUITY",
          description_vn: "Tỷ suất sinh lời trên vốn chủ sở hữu.",
        },
      ],
      red_flags: Array.from({ length: 10 }, (_, idx) => {
        const num = String(idx + 1).padStart(2, "0");
        return {
          flag_code:
            idx === 9
              ? "RF_10_POST_AUDIT_PROFIT_DOWNGRADE"
              : `RF_${num}_SAMPLE_RULE`,
          title_vn:
            idx === 9
              ? "Sụt giảm trọng yếu lợi nhuận sau kiểm toán"
              : `Quy tắc kiểm tra ${num}`,
          default_severity: idx === 9 ? "HIGH" : "MEDIUM",
          methodology_ref: "VAS TT200/TT99",
          description_vn: "Điều kiện kích hoạt chuẩn Point-in-Time.",
        };
      }),
    },
  }),
  useModelCard: () => ({
    isLoading: false,
    isError: false,
    data: null,
  }),
  useSnapshotOverview: () => ({
    isLoading: false,
    isError: false,
    data: {
      snapshot_id: "snap_test_001",
      total_issuers: 685,
      total_ratio_periods: 18420,
      pass_periods: 17900,
      warning_periods: 520,
      quarantined_rows: 3,
    },
  }),
  useQualitySummary: () => ({
    isLoading: false,
    isError: false,
    data: {
      snapshot_id: "snap_test_001",
      total: 1,
      limit: 25,
      offset: 0,
      items: [
        {
          issuer_id: "ISS_FPT",
          period_end_date: "2026-09-30",
          period_type: "QUARTER",
          statement_scope: "CONSOLIDATED",
          quality_status: "PASS",
          warning_codes: [],
          info_codes: ["CROSS_PROVIDER_DISCREPANCY_WARN"],
          checked_at: "2026-10-25",
        },
      ],
    },
  }),
  useQualityQuarantine: () => ({
    isLoading: false,
    isError: false,
    data: {
      snapshot_id: "snap_test_001",
      total: 1,
      limit: 25,
      offset: 0,
      items: [
        {
          filing_id: "FIL_XYZ_2025_Q4",
          issuer_id: "ISS_XYZ",
          period_end_date: "2025-12-31",
          period_type: "QUARTER",
          rule_code: "BS_IDENTITY_MISMATCH",
          severity: "CRITICAL",
        },
      ],
    },
  }),
  useAllIssuers: () => ({
    data: [
      { issuer_id: "ISS_FPT", ticker: "FPT" },
      { issuer_id: "ISS_XYZ", ticker: "XYZ" },
    ],
  }),
}));

describe("TASK-12 (.tsx JSDOM): /methodology WAI-ARIA tabs, 10 red flags, and focusable scroll regions", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    mockTabParam = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders WAI-ARIA tablist + tabpanel, 10 red flags without RF_10 leaks, and focusable scroll regions", () => {
    const { container } = render(<MethodologyPage />);

    const tablist = screen.getByRole("tablist", {
      name: /Các mục tài liệu phương pháp/i,
    });
    expect(tablist).toBeTruthy();

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[0].getAttribute("tabindex")).toBe("0");
    expect(tabs[1].getAttribute("tabindex")).toBe("-1");
    expect(tabs[0].getAttribute("aria-controls")).toBe(
      "methodology-panel-dictionary",
    );

    const panel = screen.getByRole("tabpanel");
    expect(panel.id).toBe("methodology-panel-dictionary");

    expect(
      screen.getByText(/10 quy tắc phát hiện bất thường kế toán/i),
    ).toBeTruthy();
    expect(screen.getByText("Quy tắc 10")).toBeTruthy();
    expect(container.textContent).not.toContain("RF_10");

    const scrollWrappers = Array.from(
      container.querySelectorAll('div.overflow-x-auto:not([role="tablist"])'),
    );
    expect(scrollWrappers.length).toBeGreaterThanOrEqual(2);
    for (const wrapper of scrollWrappers) {
      expect(wrapper.getAttribute("tabindex")).toBe("0");
      expect(wrapper.getAttribute("role")).toBe("region");
      expect(wrapper.getAttribute("aria-label")).toBeTruthy();
    }

    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(mockReplace).toHaveBeenCalledWith("/methodology?tab=models", {
      scroll: false,
    });
  });

  it("renders Quality tab scroll regions and CROSS_PROVIDER_DISCREPANCY_WARN in Vietnamese", () => {
    mockTabParam = "quality";
    const { container } = render(<MethodologyPage />);

    const panel = screen.getByRole("tabpanel");
    expect(panel.id).toBe("methodology-panel-quality");
    expect(
      screen.getByText(/Chênh lệch số liệu giữa hai nguồn cung cấp/i),
    ).toBeTruthy();
    expect(container.textContent).not.toContain("CROSS_PROVIDER_DISCREPANCY_WARN");

    const scrollWrappers = Array.from(
      container.querySelectorAll('div.overflow-x-auto:not([role="tablist"])'),
    );
    expect(scrollWrappers.length).toBeGreaterThanOrEqual(2);
    for (const wrapper of scrollWrappers) {
      expect(wrapper.getAttribute("tabindex")).toBe("0");
      expect(wrapper.getAttribute("role")).toBe("region");
    }
  });

  it("renders Merton Distance-to-Default institutional formula card without internal tokens", () => {
    const { container } = render(<MethodologyFormulaCards />);
    expect(
      screen.getByText(/Merton Distance-to-Default \(Bharath & Shumway 2008\)/i),
    ).toBeTruthy();
    expect(screen.getByText(/EDF = Φ\(−DTD\)/i)).toBeTruthy();
    expect(container.textContent).not.toContain("BS_SHORT_TERM_BORROWINGS");
  });
});
