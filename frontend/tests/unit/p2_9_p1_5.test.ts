// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { PRICE_OVERLAY_CHART_SUBTITLE } from "@/components/charts/PriceRiskOverlayChart";
import { buildSectorHeatmapData } from "@/components/charts/SectorHeatmapChart";
import {
  buildSectorTreemapNodes,
  buildSectorTreemapTitle,
  computeSectorRiskThresholds,
  getSectorRiskColor,
} from "@/components/charts/SectorTreemapChart";
import {
  assessCompanyDataCoverage,
  DataCoverageNotice,
} from "@/components/financial/DataCoverageNotice";
import { RedFlagChecklist } from "@/components/financial/RedFlagChecklist";
import { AppShell } from "@/components/layout/AppShell";
import {
  DocumentInvestigationPanel,
  FORENSIC_PRESET_QUERIES,
} from "@/components/rag/DocumentInvestigationPanel";
import CompanyProfilePage from "@/app/companies/[ticker]/page";
import { apiClient } from "@/lib/api-client";
import { formatRedFlagCodeLabel } from "@/lib/domain-labels";
import { LIGHT_CHART_PALETTE } from "@/lib/echarts-theme";
import type {
  CompanyProfileResponse,
  IssuerItem,
  MetaDictionaryResponse,
  SectorRiskItem,
} from "@/types/api";

const WORKSPACE_FRONTEND_ROOT = path.resolve(__dirname, "../..");

let mockTicker = "HBC";
let mockPathname = "/companies/HBC";
let mockSearchParamsStr = "";
const mockRouterReplace = vi.fn((url: string) => {
  const qIdx = url.indexOf("?");
  mockSearchParamsStr = qIdx >= 0 ? url.slice(qIdx + 1) : "";
});
const mockRouterPush = vi.fn();
const mockCloseSourceDrawer = vi.fn();
const mockOpenSourceDrawer = vi.fn();
const mockSetCommandOpen = vi.fn();

const mockIssuers: IssuerItem[] = [
  {
    issuer_id: "iss-hbc",
    ticker: "HBC",
    exchange: "HOSE",
    company_name_vn: "Công ty Cổ phần Tập đoàn Xây dựng Hòa Bình",
    accounting_regime: "CORPORATE",
    icb_code_level2: "2300",
    icb_name_level2: "Xây dựng và Vật liệu",
    icb_code_level3: "2357",
    icb_name_level3: "Xây dựng",
    industry_snapshot_date: "2026-11-19",
    delisted_date: null,
  },
  {
    issuer_id: "iss-fpt",
    ticker: "FPT",
    exchange: "HOSE",
    company_name_vn: "Công ty Cổ phần FPT",
    accounting_regime: "CORPORATE",
    icb_code_level2: "9500",
    icb_name_level2: "Công nghệ thông tin",
    icb_code_level3: "9530",
    icb_name_level3: "Phần mềm & Dịch vụ CNTT",
    industry_snapshot_date: "2026-11-19",
    delisted_date: null,
  },
  {
    issuer_id: "iss-sda",
    ticker: "SDA",
    exchange: "UPCOM",
    company_name_vn: "Công ty Cổ phần Simco Sông Đà",
    accounting_regime: "CORPORATE",
    icb_code_level2: "2300",
    icb_name_level2: "Xây dựng và Vật liệu",
    icb_code_level3: "2357",
    icb_name_level3: "Xây dựng",
    industry_snapshot_date: "2026-11-19",
    delisted_date: null,
  },
];

function makeProfile(ticker: string, opts?: { hasPd?: boolean }): CompanyProfileResponse {
  const hasPd = opts?.hasPd ?? true;
  return {
    snapshot_id: "snap-test",
    ticker,
    issuer_id: `iss-${ticker.toLowerCase()}`,
    company_name_vn: mockIssuers.find((i) => i.ticker === ticker)?.company_name_vn ?? ticker,
    accounting_regime: "CORPORATE",
    statement_scope: "CONSOLIDATED",
    period_view: "TTM",
    latest_taxonomy_version: "VAS_TT200_2014",
    latest_quality_status: "PASS",
    latest_warning_codes: [],
    growth_series: [],
    capital_structure_series: [],
    dupont_series: [],
    core_earnings_series: [],
    red_flags:
      ticker === "HBC"
        ? [
            {
              period_end_date: "2025-12-31",
              flag_code: "RF_07_AUDIT_OR_FILING_WARNING",
              severity: "HIGH",
              flag_title_vn: "Cảnh báo ý kiến kiểm toán & hoạt động liên tục",
              explanation_text_vn: "Kiểm toán viên nêu vấn đề cần nhấn mạnh.",
              evidence_json: null,
              filing_id: "HBC_FY2025",
            },
          ]
        : [],
    ratios_table: [{ period_end_date: "2025-12-31", roe_total: 0.12 }],
    ml_scores: [
      {
        issuer_id: `iss-${ticker.toLowerCase()}`,
        statement_scope: "CONSOLIDATED",
        period_type: "QUARTER",
        period_end_date: "2025-12-31",
        filing_id: `${ticker}_2025_Q4`,
        model_role: "OUT_OF_SAMPLE_LIVE",
        altman_z_double_prime: 2.1,
        altman_em_score: 2.1,
        altman_zone: "GREY_ZONE",
        piotroski_f_score: 5,
        piotroski_valid_components_count: 8,
        beneish_m8_score: -2.1,
        beneish_flag: "VALID",
        calibrated_distress_prob: hasPd ? 0.045 : null,
        risk_tier: hasPd ? "R3" : null,
        risk_market_percentile: hasPd ? 65 : null,
        market_base_rate: hasPd ? 0.03 : null,
        relative_risk_lift: hasPd ? 1.5 : null,
        model_version: "v2.0",
        shap_top_drivers: null,
      },
    ],
  };
}

let mockOverlayHasPrices = true;

vi.mock("next/navigation", () => ({
  useParams: () => ({ ticker: mockTicker }),
  useRouter: () => ({
    push: mockRouterPush,
    replace: mockRouterReplace,
  }),
  usePathname: () => mockPathname,
  useSearchParams: () => new URLSearchParams(mockSearchParamsStr),
}));

vi.mock("@/components/providers/app-providers", () => ({
  useAppState: () => ({
    statementScope: "CONSOLIDATED",
    setStatementScope: vi.fn(),
    periodType: "QUARTER",
    setPeriodType: vi.fn(),
    periodEndDate: "2025-12-31",
    setPeriodEndDate: vi.fn(),
    periodView: "TTM",
    snapshotId: "snap-test",
    publishedAt: "2026-11-20",
    availableDates: ["2025-12-31"],
    sectorsLevel2: [],
    sourceTarget: null,
    openSourceDrawer: mockOpenSourceDrawer,
    closeSourceDrawer: mockCloseSourceDrawer,
    commandOpen: false,
    setCommandOpen: mockSetCommandOpen,
    themePreference: "light",
    resolvedTheme: "light",
    setThemePreference: vi.fn(),
  }),
}));

vi.mock("@/components/charts/EChartBox", () => ({
  EChartBox: ({ title, emptyMessage, isEmpty }: { title: string; emptyMessage?: string; isEmpty?: boolean }) =>
    React.createElement(
      "div",
      { "data-testid": "mock-echart-box" },
      title,
      isEmpty && emptyMessage ? ` — ${emptyMessage}` : "",
    ),
  buildSafeExportFilename: (t: string) => t,
}));

vi.mock("@/components/layout/CommandSearch", () => ({
  CommandSearch: () => null,
}));

vi.mock("@/components/layout/SourceDrawer", () => ({
  SourceDrawer: () => null,
}));

const mockUseCompanyDocuments = vi.fn();

vi.mock("@/hooks/use-finintel-queries", () => ({
  useCompanyProfile: (ticker: string) => ({
    data: makeProfile(ticker, { hasPd: ticker !== "SDA" }),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useMetaDictionary: () => ({
    data: {
      ratios: [],
      red_flags: [
        {
          flag_code: "RF_03_OTHER_RECEIVABLES_ANOMALY",
          title_vn: "Phải thu ngoài khách hàng tăng bất thường",
          default_severity: "HIGH",
          methodology_ref: "VAS TT200",
          description_vn: "Tỷ trọng phải thu khác cao bất thường.",
        },
        {
          flag_code: "RF_07_AUDIT_OR_FILING_WARNING",
          title_vn: "Cảnh báo ý kiến kiểm toán & hoạt động liên tục",
          default_severity: "CRITICAL",
          methodology_ref: "VSA 705/570",
          description_vn: "Ý kiến kiểm toán ngoại trừ hoặc nhấn mạnh.",
        },
      ],
    } as unknown as MetaDictionaryResponse,
    isLoading: false,
  }),
  useAllIssuers: () => ({
    data: mockIssuers,
    isLoading: false,
  }),
  useCompanyFilings: () => ({
    data: { items: [] },
    isLoading: false,
  }),
  useCompanyMarketOverlay: (ticker: string) => ({
    data: {
      snapshot_id: "snap-test",
      issuer_id: `iss-${ticker.toLowerCase()}`,
      ticker,
      as_of: "2026-11-20",
      latest_valuation: null,
      price_series: mockOverlayHasPrices
        ? [
            {
              trade_date: "2026-11-20",
              close_adj: 20_000,
              close_raw: 20_000,
              volume: 100_000,
              shares_outstanding_market: 100_000_000,
              running_drawdown_pct: -5,
              drawdown_from_first_warning_pct: null,
              market_cap_vnd: 2_000_000_000_000,
              pe_ttm: 10,
              pb_ratio: 1.5,
              pe_flag: "VALID",
              pb_flag: "VALID",
              shares_source: "SHARES_OUTSTANDING_MARKET",
            },
          ]
        : [],
      model_markers: [],
      regulatory_markers: [],
      earliest_warning_summary: null,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCompanyBenchmarksSummary: () => ({
    data: { items: [], period_end_date: "2025-12-31" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCompanyStatements: () => ({
    data: null,
    isLoading: false,
    isError: false,
  }),
  useCompanyBenchmarks: () => ({
    data: null,
    isLoading: false,
    isError: false,
  }),
  useCompanyDocuments: (ticker: string) => mockUseCompanyDocuments(ticker),
}));

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(React.createElement(QueryClientProvider, { client: qc }, ui));
}

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

beforeEach(() => {
  mockTicker = "HBC";
  mockPathname = "/companies/HBC";
  mockSearchParamsStr = "";
  mockOverlayHasPrices = true;
  mockUseCompanyDocuments.mockImplementation((ticker: string) => ({
    data: {
      ticker,
      total_documents: 1,
      documents: [
        {
          filing_id: `${ticker}_FY2025_CONSOLIDATED_11111111`,
          issuer_id: `iss-${ticker}`,
          ticker,
          fiscal_year: 2025,
          fiscal_quarter: null,
          statement_scope: "CONSOLIDATED",
          doc_type: "AUDITED_ANNUAL_CONSOLIDATED_FS",
          cohort: "case_study",
          source_title: `BCTC hợp nhất 2025 ${ticker}`,
          source_url: "https://example.com/fs.pdf",
          pdf_sha256: "a".repeat(64),
          file_size_bytes: 1_000_000,
          total_pages: 50,
          digital_pages: 40,
          ocr_pages: 10,
          chunk_count: 80,
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("P2-9 Navigation State Reset & SourceDrawer Close", () => {
  it("resets ragPreset and closes SourceDrawer when navigating from HBC to FPT so HBC preset never auto-fires on FPT", async () => {
    const investigateSpy = vi
      .spyOn(apiClient, "postCompanyInvestigate")
      .mockResolvedValue({
        ticker: "HBC",
        question: "test",
        answer: "Kết quả [1].",
        abstained: false,
        abstention_reason: null,
        citations: [],
        retrieved_chunks_count: 1,
        top_vector_similarity: 0.8,
        cached: false,
        latency_ms: 12,
        model_used: "gemini-2.5-flash",
        disclaimer: "Chỉ tham khảo.",
      });

    const { rerender } = renderWithQuery(React.createElement(CompanyProfilePage));

    // User triggers red flag investigation on HBC
    const jumpBtn = screen.getByRole("button", { name: /Kiểm tra Thuyết minh gốc/i });
    fireEvent.click(jumpBtn);

    // Navigate via Ctrl+K from /companies/HBC -> /companies/FPT (component instance stays mounted)
    investigateSpy.mockClear();
    mockCloseSourceDrawer.mockClear();
    mockTicker = "FPT";
    mockPathname = "/companies/FPT";
    mockSearchParamsStr = "";

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(CompanyProfilePage),
      ),
    );

    await waitFor(() => {
      expect(mockCloseSourceDrawer).toHaveBeenCalled();
    });

    // Now user opens the PDF investigation tab on FPT
    mockSearchParamsStr = "tab=documents";
    rerender(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(CompanyProfilePage),
      ),
    );

    const questionInput = (await screen.findByLabelText(
      /Câu hỏi phân tích thuyết minh chuyên sâu/i,
    )) as HTMLTextAreaElement;
    expect(questionInput.value).toBe("");
    expect(investigateSpy).not.toHaveBeenCalled();
  });

  it("closes SourceDrawer in AppShell whenever pathname changes", async () => {
    mockPathname = "/companies/HBC";
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(AppShell, null, React.createElement("div", null, "Body")),
      ),
    );

    mockCloseSourceDrawer.mockClear();
    mockPathname = "/companies/FPT";
    rerender(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(AppShell, null, React.createElement("div", null, "Body")),
      ),
    );

    await waitFor(() => {
      expect(mockCloseSourceDrawer).toHaveBeenCalledTimes(1);
    });
  });
});

describe("P2-9 Vietnamese Localization (Zero Valkey HIT/MISS, look-ahead, RF_*)", () => {
  it("localizes cache hit/miss and removes RF_03/07/09 tokens from DocumentInvestigationPanel", async () => {
    for (const preset of FORENSIC_PRESET_QUERIES) {
      expect(preset.shortLabel).not.toMatch(/\bRF_0?[1-9]\b/);
    }

    const investigateSpy = vi
      .spyOn(apiClient, "postCompanyInvestigate")
      .mockResolvedValueOnce({
        ticker: "HBC",
        question: "Kiểm tra kiểm toán",
        answer: "Báo cáo nêu ý kiến ngoại trừ [1].",
        abstained: false,
        abstention_reason: null,
        citations: [],
        retrieved_chunks_count: 1,
        top_vector_similarity: 0.81,
        cached: true,
        latency_ms: 2.4,
        model_used: "gemini-2.5-flash",
        disclaimer: "Tổng hợp từ PDF gốc.",
      })
      .mockResolvedValueOnce({
        ticker: "HBC",
        question: "Kiểm tra xây dựng cơ bản",
        answer: "Chi phí XDCB dở dang [1].",
        abstained: false,
        abstention_reason: null,
        citations: [],
        retrieved_chunks_count: 1,
        top_vector_similarity: 0.78,
        cached: false,
        latency_ms: 420,
        model_used: "gemini-2.5-flash",
        disclaimer: "Tổng hợp từ PDF gốc.",
      });

    renderWithQuery(React.createElement(DocumentInvestigationPanel, { ticker: "HBC" }));

    const buttons = screen.getAllByRole("button");
    for (const btn of buttons) {
      expect(btn.textContent ?? "").not.toMatch(/\bRF_0?[1-9]\b/);
    }

    const input = screen.getByLabelText(/Câu hỏi phân tích thuyết minh chuyên sâu/i);
    fireEvent.change(input, { target: { value: "Kiểm tra kiểm toán" } });
    fireEvent.click(screen.getByRole("button", { name: /Tra cứu Thuyết minh/i }));

    await waitFor(() => {
      expect(investigateSpy).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("Dữ liệu đệm sẵn (Cache)")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Valkey/i);

    fireEvent.change(input, { target: { value: "Kiểm tra xây dựng cơ bản" } });
    fireEvent.click(screen.getByRole("button", { name: /Tra cứu Thuyết minh/i }));

    await waitFor(() => {
      expect(investigateSpy).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText("Truy xuất mới")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Valkey/i);
  });

  it("localizes RF_* codes in RedFlagChecklist and /methodology without leaking RF_01..RF_09", () => {
    expect(formatRedFlagCodeLabel("RF_03_OTHER_RECEIVABLES_ANOMALY")).toBe("Quy tắc 03");
    expect(formatRedFlagCodeLabel("RF_07_AUDIT_OR_FILING_WARNING")).toBe("Quy tắc 07");
    expect(formatRedFlagCodeLabel("RF_09_FROZEN_CIP_CAPITALIZATION")).toBe("Quy tắc 09");

    const { container } = render(
      React.createElement(RedFlagChecklist, {
        dictionary: [
          {
            flag_code: "RF_03_OTHER_RECEIVABLES_ANOMALY",
            title_vn: "Phải thu ngoài khách hàng tăng bất thường",
            default_severity: "HIGH",
            methodology_ref: "VAS TT200",
            description_vn: "Phải thu khác cao.",
          },
          {
            flag_code: "RF_07_AUDIT_OR_FILING_WARNING",
            title_vn: "Cảnh báo ý kiến kiểm toán",
            default_severity: "CRITICAL",
            methodology_ref: "VSA 705",
            description_vn: "Ý kiến ngoại trừ.",
          },
        ],
        triggeredFlags: [
          {
            period_end_date: "2025-12-31",
            flag_code: "RF_07_AUDIT_OR_FILING_WARNING",
            severity: "CRITICAL",
            flag_title_vn: "Cảnh báo ý kiến kiểm toán",
            explanation_text_vn: "Ý kiến ngoại trừ.",
            evidence_json: null,
            filing_id: "F1",
          },
        ],
        latestPeriodEndDate: "2025-12-31",
      }),
    );

    expect(container.textContent).toContain("Quy tắc 07");
    expect(container.textContent).toContain("Quy tắc 03");
    expect(container.textContent).not.toMatch(/\bRF_0?[1-9]\b/);

    const methodologySrc = fs.readFileSync(
      path.join(WORKSPACE_FRONTEND_ROOT, "src/app/methodology/page.tsx"),
      "utf8",
    );
    expect(methodologySrc).not.toMatch(/\(RF_01/);
    expect(methodologySrc).toContain("formatRedFlagCodeLabel");
  });

  it("localizes PIT (0% look-ahead) in PriceRiskOverlayChart subtitle", () => {
    expect(PRICE_OVERLAY_CHART_SUBTITLE).not.toMatch(/look-ahead/i);
    expect(PRICE_OVERLAY_CHART_SUBTITLE).toContain("chuẩn thời điểm công bố");
  });
});

describe("P2-9 SectorHeatmapChart & SectorTreemapChart Null Handling & Dynamic Thresholds", () => {
  const mixedSectors: SectorRiskItem[] = [
    {
      icb_code_level2: "8600",
      icb_name_level2: "Bất động sản",
      issuer_count: 50,
      safe_zone_count: 10,
      grey_zone_count: 15,
      distress_zone_count: 25,
      beneish_flagged_count: 8,
      high_red_flag_count: 12,
      median_distress_prob: 0.06,
      avg_red_flags: 1.6,
      total_debt_sum: 200_000_000_000_000,
    },
    {
      icb_code_level2: "3500",
      icb_name_level2: "Thực phẩm & Đồ uống",
      issuer_count: 35,
      safe_zone_count: 25,
      grey_zone_count: 8,
      distress_zone_count: 2,
      beneish_flagged_count: 1,
      high_red_flag_count: 2,
      median_distress_prob: 0.01,
      avg_red_flags: 0.3,
      total_debt_sum: 30_000_000_000_000,
    },
    {
      icb_code_level2: "7500",
      icb_name_level2: "Tiện ích",
      issuer_count: 20,
      safe_zone_count: 12,
      grey_zone_count: 6,
      distress_zone_count: 2,
      beneish_flagged_count: 1,
      high_red_flag_count: 1,
      median_distress_prob: 0.02,
      avg_red_flags: 0.5,
      total_debt_sum: 20_000_000_000_000,
    },
    {
      icb_code_level2: "4500",
      icb_name_level2: "Dịch vụ thiếu PD",
      issuer_count: 8,
      safe_zone_count: 4,
      grey_zone_count: 4,
      distress_zone_count: 0,
      beneish_flagged_count: 0,
      high_red_flag_count: 0,
      median_distress_prob: null,
      avg_red_flags: 0.1,
      total_debt_sum: 5_000_000_000_000,
    },
  ];

  it("renders null median_distress_prob as neutral (never green) with N/A and sorts to bottom in SectorHeatmapChart", () => {
    const heatmap = buildSectorHeatmapData(mixedSectors, "distress_prob", LIGHT_CHART_PALETTE);
    const missingRow = heatmap.rows.find((r) => r.sector.icb_code_level2 === "4500");
    expect(missingRow).toBeDefined();
    expect(missingRow!.isMissing).toBe(true);
    expect(missingRow!.color).toBe(LIGHT_CHART_PALETTE.neutral);
    expect(missingRow!.color).not.toBe(LIGHT_CHART_PALETTE.riskLow);
    expect(missingRow!.displayLabel).toBe("N/A");
    // Missing sector sorts after all non-null sectors
    expect(heatmap.rows[heatmap.rows.length - 1].sector.icb_code_level2).toBe("4500");
  });

  it("uses dynamic cross-sector thresholds without hardcoded 0.03/0.08 in both SectorHeatmapChart and SectorTreemapChart", () => {
    const heatmapSrc = fs.readFileSync(
      path.join(WORKSPACE_FRONTEND_ROOT, "src/components/charts/SectorHeatmapChart.tsx"),
      "utf8",
    );
    const treemapSrc = fs.readFileSync(
      path.join(WORKSPACE_FRONTEND_ROOT, "src/components/charts/SectorTreemapChart.tsx"),
      "utf8",
    );
    expect(heatmapSrc).not.toMatch(/0\.03|0\.08/);
    expect(treemapSrc).not.toMatch(/0\.03|0\.08/);

    const thresholds = computeSectorRiskThresholds(mixedSectors, "distress_prob");
    expect(
      getSectorRiskColor(mixedSectors[3], "distress_prob", LIGHT_CHART_PALETTE, thresholds),
    ).toBe(LIGHT_CHART_PALETTE.neutral);

    const nodes = buildSectorTreemapNodes(mixedSectors, "distress_prob", LIGHT_CHART_PALETTE);
    const nullNode = nodes.find((n) => n.icbCode === "4500");
    expect(nullNode?.color).toBe(LIGHT_CHART_PALETTE.neutral);
    expect(nodes.find((n) => n.icbCode === "8600")?.color).toBe(LIGHT_CHART_PALETTE.riskHigh);
    expect(nodes.find((n) => n.icbCode === "3500")?.color).toBe(LIGHT_CHART_PALETTE.riskLow);

    // All-null sectors fall back cleanly without "N/A XS kiệt quệ"
    const allNullSectors = [mixedSectors[3]];
    const title = buildSectorTreemapTitle(allNullSectors, "distress_prob");
    expect(title).not.toContain("N/A");
  });
});

describe("P1-5 UI Transparent Data Coverage Scope (UPCOM / Partial Coverage)", () => {
  it("detects partial coverage for UPCOM issuers missing EOD prices and supervised PD and renders clear Vietnamese notice", () => {
    const assessment = assessCompanyDataCoverage({
      ticker: "SDA",
      exchange: "UPCOM",
      accountingRegime: "CORPORATE",
      hasFinancialData: true,
      hasSupervisedPd: false,
      hasEodPrices: false,
    });

    expect(assessment.isPartial).toBe(true);
    expect(assessment.badgeLabel).toContain("UPCOM");
    expect(assessment.noticeText).toContain("báo cáo tài chính");
    expect(assessment.noticeText).toContain("chỉ số phân tích");
    expect(assessment.noticeText).toContain("cờ đỏ");
    expect(assessment.noticeText).not.toMatch(/REGIME_FALLBACK|EXACT_FILING|STATUTORY_LAG|\.parquet|✨/);

    const { container } = render(
      React.createElement(DataCoverageNotice, {
        ticker: "SDA",
        exchange: "UPCOM",
        accountingRegime: "CORPORATE",
        hasFinancialData: true,
        hasSupervisedPd: false,
        hasEodPrices: false,
      }),
    );

    expect(container.textContent).toContain("Phủ dữ liệu một phần");
    expect(container.textContent).toContain("Chưa có dữ liệu giá");
    expect(container.textContent).toContain("Chưa đủ lịch sử công bố");
  });

  it("renders DataCoverageNotice on CompanyProfilePage for UPCOM ticker missing EOD and PD", async () => {
    mockTicker = "SDA";
    mockPathname = "/companies/SDA";
    mockOverlayHasPrices = false;

    renderWithQuery(React.createElement(CompanyProfilePage));

    expect(await screen.findByText(/Phủ dữ liệu một phần/i)).toBeTruthy();
    expect(document.body.textContent).toContain("SDA");
    expect(document.body.textContent).toContain("UPCOM");
    expect(document.body.textContent).toContain("Chưa đủ lịch sử công bố");
  });

  it("marks full coverage cleanly without partial warning banner when EOD and supervised PD are present", () => {
    const assessment = assessCompanyDataCoverage({
      ticker: "FPT",
      exchange: "HOSE",
      accountingRegime: "CORPORATE",
      hasFinancialData: true,
      hasSupervisedPd: true,
      hasEodPrices: true,
    });

    expect(assessment.isPartial).toBe(false);
    expect(assessment.badgeLabel).toBe("Phủ dữ liệu đầy đủ");
  });
});
