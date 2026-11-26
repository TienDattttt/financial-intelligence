// @vitest-environment jsdom
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { Combobox, matchesComboboxQuery, type ComboboxOption } from "@/components/ui/combobox";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { SourceDrawer } from "@/components/layout/SourceDrawer";
import { CommandSearch } from "@/components/layout/CommandSearch";
import type { SourceLookupTarget } from "@/components/providers/app-providers";
import type { IssuerItem, MetaDictionaryResponse, RatioLineageResponse } from "@/types/api";

const mockPush = vi.fn();
const mockCloseSourceDrawer = vi.fn();
const mockSetCommandOpen = vi.fn();

let mockSourceTarget: SourceLookupTarget | null = null;
let mockCommandOpen = false;
let mockLineageData: RatioLineageResponse | undefined = undefined;
let mockIssuersData: IssuerItem[] = [];

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
  }),
}));

vi.mock("@/components/providers/app-providers", () => ({
  useAppState: () => ({
    sourceTarget: mockSourceTarget,
    closeSourceDrawer: mockCloseSourceDrawer,
    snapshotId: "snap-test-1",
    commandOpen: mockCommandOpen,
    setCommandOpen: mockSetCommandOpen,
  }),
}));

vi.mock("@/hooks/use-finintel-queries", () => ({
  useRatioLineage: () => ({
    data: mockLineageData,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useMetaDictionary: () => ({
    data: {
      ratios: [
        {
          ratio_name: "roe_total",
          display_name_vn: "Tỷ suất sinh lời trên vốn chủ sở hữu (ROE)",
          category: "PROFITABILITY",
          format_type: "PERCENT",
          direction: "HIGHER_IS_BETTER",
          formula_display: "LNST / VCSH bình quân",
          description_vn: "Đo lường hiệu quả sinh lời trên vốn chủ sở hữu.",
        },
      ],
    } as unknown as MetaDictionaryResponse,
  }),
  useAllIssuers: () => ({
    data: mockIssuersData,
    isLoading: false,
  }),
}));

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
});

beforeEach(() => {
  mockSourceTarget = null;
  mockCommandOpen = false;
  mockLineageData = undefined;
  mockIssuersData = [];
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("UI-02 InfoTooltip (@base-ui/react/tooltip)", () => {
  it("renders tooltip via Portal outside overflow-x-auto containers on focus and closes on Escape", async () => {
    render(
      React.createElement(
        "div",
        { "data-testid": "scroll-table", style: { overflowX: "auto" } },
        React.createElement(InfoTooltip, {
          text: "Tỷ lệ nợ vay trên vốn chủ sở hữu.",
          label: "Giải thích Nợ/VCSH",
        }),
      ),
    );

    const trigger = screen.getByRole("button", { name: "Giải thích Nợ/VCSH" });
    expect(trigger).toBeTruthy();

    fireEvent.focus(trigger);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain("Tỷ lệ nợ vay trên vốn chủ sở hữu.");

    const scrollTable = screen.getByTestId("scroll-table");
    expect(scrollTable.contains(tooltip)).toBe(false);
    expect(document.body.contains(tooltip)).toBe(true);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("tooltip")).toBeNull();
    });

    // Also verify default label prop compatibility
    cleanup();
    render(React.createElement(InfoTooltip, { text: "Chỉ số chuẩn hóa theo TT200/TT99." }));
    const defaultTrigger = screen.getByRole("button", { name: "Giải thích chỉ số" });
    fireEvent.focus(defaultTrigger);
    expect(await screen.findByRole("tooltip")).toBeTruthy();
  });
});

describe("UI-02 Combobox (@base-ui/react/combobox)", () => {
  const sampleIndustries: ComboboxOption[] = [
    { value: "", label: "Tất cả các ngành" },
    { value: "1700", label: "Tài nguyên cơ bản (Thép & Kim loại)", hint: "(18)" },
    { value: "7500", label: "Điện, nước & xăng dầu khí đốt", hint: "(24)" },
    { value: "2300", label: "Xây dựng và Vật liệu", hint: "(45)" },
    { value: "8600", label: "Bất động sản", hint: "(62)" },
  ];

  it("matches accented and unaccented Vietnamese queries including đ/Đ", () => {
    expect(matchesComboboxQuery(sampleIndustries[1], "thep")).toBe(true);
    expect(matchesComboboxQuery(sampleIndustries[1], "Thép")).toBe(true);
    expect(matchesComboboxQuery(sampleIndustries[2], "dien")).toBe(true);
    expect(matchesComboboxQuery(sampleIndustries[2], "Điện")).toBe(true);
    expect(matchesComboboxQuery(sampleIndustries[4], "bat dong san")).toBe(true);
    expect(matchesComboboxQuery(sampleIndustries[4], "ngan hang")).toBe(false);
  });

  it("hides search input when options.length <= 6 unless searchable={true} (U8)", async () => {
    const onValueChange = vi.fn();

    render(
      React.createElement(Combobox, {
        id: "test-small-dropdown",
        options: sampleIndustries,
        value: "",
        onValueChange,
        ariaLabel: "Ngành ICB nhỏ",
      }),
    );

    const trigger = screen.getByRole("combobox", { name: "Ngành ICB nhỏ" });
    fireEvent.click(trigger);

    await screen.findByText("Điện, nước & xăng dầu khí đốt");
    expect(
      screen.queryByPlaceholderText("Tìm nhanh (hỗ trợ không dấu)..."),
    ).toBeNull();
  });

  it("filters via unaccented search, navigates with keyboard, and selects on Enter when searchable={true}", async () => {
    const onValueChange = vi.fn();

    render(
      React.createElement(Combobox, {
        id: "test-sector",
        options: sampleIndustries,
        value: "",
        onValueChange,
        ariaLabel: "Ngành ICB cấp 2",
        searchable: true,
      }),
    );

    const trigger = screen.getByRole("combobox", { name: "Ngành ICB cấp 2" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toContain("Tất cả các ngành");

    fireEvent.click(trigger);

    const searchInput = await screen.findByPlaceholderText("Tìm nhanh (hỗ trợ không dấu)...");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.body.contains(searchInput)).toBe(true);

    fireEvent.change(searchInput, { target: { value: "dien" } });

    await waitFor(() => {
      expect(screen.getByText("Điện, nước & xăng dầu khí đốt")).toBeTruthy();
      expect(screen.queryByText("Bất động sản")).toBeNull();
    });

    fireEvent.keyDown(searchInput, { key: "ArrowDown" });
    fireEvent.keyDown(searchInput, { key: "Enter" });

    await waitFor(() => {
      expect(onValueChange).toHaveBeenCalledWith("7500");
    });
  });

  it("closes popup on Escape without changing selection", async () => {
    const onValueChange = vi.fn();

    render(
      React.createElement(Combobox, {
        id: "test-sector-esc",
        options: sampleIndustries,
        value: "1700",
        onValueChange,
        ariaLabel: "Ngành ICB cấp 2",
        searchable: true,
      }),
    );

    const trigger = screen.getByRole("combobox", { name: "Ngành ICB cấp 2" });
    fireEvent.click(trigger);

    const searchInput = await screen.findByPlaceholderText("Tìm nhanh (hỗ trợ không dấu)...");
    fireEvent.keyDown(searchInput, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByPlaceholderText("Tìm nhanh (hỗ trợ không dấu)...")).toBeNull();
    });
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it("automatically shows search input when options.length > 6 without searchable prop (U8)", async () => {
    const sevenIndustries: ComboboxOption[] = [
      ...sampleIndustries,
      { value: "5300", label: "Bán lẻ", hint: "(19)" },
      { value: "9500", label: "Công nghệ thông tin", hint: "(12)" },
    ];
    const onValueChange = vi.fn();

    render(
      React.createElement(Combobox, {
        id: "test-seven-dropdown",
        options: sevenIndustries,
        value: "",
        onValueChange,
        ariaLabel: "Ngành ICB > 6",
      }),
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Ngành ICB > 6" }));
    const searchInput = await screen.findByPlaceholderText(
      "Tìm nhanh (hỗ trợ không dấu)...",
    );
    expect(document.body.contains(searchInput)).toBe(true);
    fireEvent.change(searchInput, { target: { value: "cong nghe" } });
    await waitFor(() => {
      expect(screen.getByText("Công nghệ thông tin")).toBeTruthy();
      expect(screen.queryByText("Bất động sản")).toBeNull();
    });
  });

  it("handles 38 financial metrics scale and displays empty state when no metric matches", async () => {
    const metrics38: ComboboxOption[] = Array.from({ length: 38 }, (_, i) => ({
      value: `metric_${i + 1}`,
      label:
        i === 14
          ? "Tỷ suất sinh lời trên vốn chủ sở hữu (ROE)"
          : `Chỉ số tài chính mẫu ${i + 1}`,
    }));
    const onValueChange = vi.fn();

    render(
      React.createElement(Combobox, {
        id: "test-metrics-38",
        options: metrics38,
        value: "metric_1",
        onValueChange,
        ariaLabel: "Chọn chỉ số tài chính",
      }),
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Chọn chỉ số tài chính" }));
    const searchInput = await screen.findByPlaceholderText("Tìm nhanh (hỗ trợ không dấu)...");

    fireEvent.change(searchInput, { target: { value: "khong ton tai xyz" } });
    await waitFor(() => {
      expect(screen.getByText("Không tìm thấy mục phù hợp.")).toBeTruthy();
    });

    fireEvent.change(searchInput, { target: { value: "von chu so huu" } });
    const matchedOption = await screen.findByText(
      "Tỷ suất sinh lời trên vốn chủ sở hữu (ROE)",
    );
    fireEvent.click(matchedOption);
    await waitFor(() => {
      expect(onValueChange).toHaveBeenCalledWith("metric_15");
    });
  });
});

describe("UI-02 SourceDrawer (@base-ui/react/dialog)", () => {
  it("does not mount dialog when closed and renders accessible modal drawer with lineage when open", async () => {
    mockSourceTarget = null;
    const { rerender } = render(React.createElement(SourceDrawer));
    expect(screen.queryByRole("dialog")).toBeNull();

    mockSourceTarget = {
      ticker: "HPG",
      ratioName: "roe_total",
      statementScope: "CONSOLIDATED",
      periodType: "QUARTER",
      periodEndDate: "2025-12-31",
    };
    mockLineageData = {
      ticker: "HPG",
      ratio_name: "roe_total",
      display_name_vn: "Tỷ suất sinh lời trên vốn chủ sở hữu (ROE)",
      period_end_date: "2025-12-31",
      period_type: "QUARTER",
      statement_scope: "CONSOLIDATED",
      taxonomy_version: "VAS_TT200_2014",
      ratio_value: 0.148,
      status_flag: "VALID",
      formula: "IS_NET_INCOME / Avg(BS_TOTAL_EQUITY_t, BS_TOTAL_EQUITY_{t-4})",
      denominator_mode: "AVERAGE_STOCK",
      anchor_filing_id: "FIL_HPG_2025_Q4",
      source_filing_ids: ["FIL_HPG_2025_Q4"],
      orphan_filing_ids: [],
      numerator_components: [
        {
          metric_id: "IS_NET_INCOME",
          period_end_date: "2025-12-31",
          period_type: "QUARTER",
          value: 12_500_000_000_000,
          taxonomy_version: "VAS_TT200_2014",
          derivation_method: "REPORTED_DIRECTLY",
          source_provider: "hose_pdf_extract",
          published_at: "2026-01-28",
          effective_available_at: "2026-01-28",
          published_at_basis: "EXACT_FILING",
          filing_id: "FIL_HPG_2025_Q4",
          canonical_content_sha256: "abcdef1234567890",
        },
      ],
      denominator_components: [],
    } as unknown as RatioLineageResponse;

    rerender(React.createElement(SourceDrawer));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText("14,8%")).toBeTruthy();
    expect(screen.getByText("Lợi nhuận sau thuế TNDN")).toBeTruthy();
    expect(dialog.textContent).not.toContain("BS_TOTAL_EQUITY");

    const closeBtn = screen.getByRole("button", { name: "Đóng ngăn kéo nguồn" });
    fireEvent.click(closeBtn);
    expect(mockCloseSourceDrawer).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(mockCloseSourceDrawer).toHaveBeenCalledTimes(2);
  });
});

describe("UI-02 CommandSearch (@base-ui/react/dialog)", () => {
  it("supports Ctrl+K shortcut, unaccented Vietnamese filtering, keyboard navigation, and Escape close", async () => {
    mockIssuersData = [
      {
        issuer_id: "1",
        ticker: "HPG",
        company_name_vn: "Công ty Cổ phần Tập đoàn Hòa Phát",
        exchange: "HOSE",
        icb_name_level2: "Tài nguyên cơ bản",
        icb_name_level3: "Thép",
      },
      {
        issuer_id: "2",
        ticker: "POW",
        company_name_vn: "Tổng Công ty Điện lực Dầu khí Việt Nam",
        exchange: "HOSE",
        icb_name_level2: "Điện, nước & xăng dầu khí đốt",
        icb_name_level3: "Sản xuất điện",
      },
    ] as unknown as IssuerItem[];

    mockCommandOpen = false;
    const { rerender } = render(React.createElement(CommandSearch));

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(mockSetCommandOpen).toHaveBeenCalledWith(true);

    mockCommandOpen = true;
    rerender(React.createElement(CommandSearch));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();

    const input = screen.getByPlaceholderText(
      "Nhập mã CK hoặc tên doanh nghiệp (hỗ trợ không dấu, VD: hoa phat)...",
    );
    await waitFor(() => {
      expect(document.activeElement).toBe(input);
    });

    fireEvent.change(input, { target: { value: "dien luc" } });
    await waitFor(() => {
      expect(screen.getByText("POW")).toBeTruthy();
      expect(screen.queryByText("HPG")).toBeNull();
    });

    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockSetCommandOpen).toHaveBeenCalledWith(false);
    expect(mockPush).toHaveBeenCalledWith("/companies/POW");

    const closeBtn = screen.getByRole("button", { name: "Đóng tìm kiếm" });
    fireEvent.click(closeBtn);
    expect(mockSetCommandOpen).toHaveBeenCalledWith(false);
  });
});
