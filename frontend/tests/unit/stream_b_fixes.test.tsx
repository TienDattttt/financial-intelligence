// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const capturedSetOptions: unknown[] = [];
const mockRouterReplace = vi.fn();
let mockSearchParams = new URLSearchParams("tickers=FPT");
const mockIssuersData = [
  {
    issuer_id: "ISS_FOX",
    ticker: "FOX",
    company_name_vn: "CTCP Viễn thông FPT",
    exchange: "UPCOM",
    icb_code_level2: "9500",
    icb_name_level2: "Công nghệ thông tin",
  },
  {
    issuer_id: "ISS_FPT1",
    ticker: "FPT1",
    company_name_vn: "Công ty FPT1",
    exchange: "HOSE",
    icb_code_level2: "9500",
    icb_name_level2: "Công nghệ thông tin",
  },
  {
    issuer_id: "ISS_FPT",
    ticker: "FPT",
    company_name_vn: "CTCP FPT",
    exchange: "HOSE",
    icb_code_level2: "9500",
    icb_name_level2: "Công nghệ thông tin",
  },
  {
    issuer_id: "ISS_HPG",
    ticker: "HPG",
    company_name_vn: "CTCP Tập đoàn Hòa Phát",
    exchange: "HOSE",
    icb_code_level2: "1700",
    icb_name_level2: "Tài nguyên cơ bản",
  },
];

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace, push: vi.fn() }),
  useSearchParams: () => mockSearchParams,
}));

let useRealAppProviders = false;
let useRealCompareCompanies = false;

vi.mock("@/components/providers/app-providers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/providers/app-providers")>();
  return {
    ...actual,
    useAppState: () =>
      useRealAppProviders
        ? actual.useAppState()
        : {
            statementScope: "CONSOLIDATED",
            periodType: "QUARTER",
            periodEndDate: "2026-03-31",
            cacheSnapshotKey: "active",
          },
  };
});

vi.mock("@/hooks/use-finintel-queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-finintel-queries")>();
  return {
    ...actual,
    useAllIssuers: () => ({
      data: mockIssuersData,
      isLoading: false,
      isError: false,
    }),
    useCompareCompanies: (
      ...args: Parameters<typeof actual.useCompareCompanies>
    ) =>
      useRealCompareCompanies
        ? actual.useCompareCompanies(...args)
        : {
            profiles: [],
            summaries: [],
          },
    useMarketChanges: () => ({
      data: { items: [] },
      isLoading: false,
      isError: false,
    }),
  };
});

vi.mock("echarts", () => ({
  init: (el: HTMLElement) => {
    const marker = document.createElement("div");
    marker.setAttribute("data-echarts-mock", "true");
    el.appendChild(marker);
    return {
      on: vi.fn(),
      off: vi.fn(),
      setOption: vi.fn((opt: unknown) => {
        capturedSetOptions.push(opt);
      }),
      resize: vi.fn(),
      getDataURL: vi.fn(() => "data:image/png;base64,AA=="),
      getZr: () => ({ on: vi.fn(), off: vi.fn() }),
      containPixel: vi.fn(() => false),
      convertFromPixel: vi.fn(() => [0, 0]),
      dispose: () => {
        el.innerHTML = "";
      },
    };
  },
}));

import ComparePage from "@/app/compare/page";
import WatchlistPage from "@/app/watchlist/page";
import { CompareFivePillarChart } from "@/components/charts/CompareFivePillarChart";
import { EChartBox } from "@/components/charts/EChartBox";
import {
  ExplainWaterfallChart,
  resolveCrossFilteredMlScore,
  resolveMlFeatureFormatType,
} from "@/components/charts/ExplainWaterfallChart";
import { HealthRadarChart } from "@/components/charts/HealthRadarChart";
import {
  buildPriceOverlayOption,
  computePriceOverlayZoomRange,
} from "@/components/charts/PriceRiskOverlayChart";
import { ScoreDots } from "@/components/financial/ScoreDots";
import { AppProviders, useAppState } from "@/components/providers/app-providers";
import {
  shouldRetryFinintelQuery,
  useCompareCompanies,
} from "@/hooks/use-finintel-queries";
import { ApiError, apiClient } from "@/lib/api-client";
import {
  AUDIT_OPINION_LABELS,
  formatFormulaVi,
  formatPiotroskiNaDetail,
  PEER_STANDARDIZATION_LABELS,
  sanitizeModelLimitationText,
} from "@/lib/domain-labels";
import {
  DARK_CHART_PALETTE,
  getChartPalette,
  LIGHT_CHART_PALETTE,
} from "@/lib/echarts-theme";
import { formatPercentPoints, formatRatioByType } from "@/lib/format";
import {
  filtersToUrlPatch,
  parseScreenerFilters,
  saveScreenerView,
  VALID_SORT_FIELDS,
} from "@/lib/saved-screens-store";
import { computeScenarioComparison } from "@/lib/stress-test-helpers";
import { addWatchlistTicker, removeWatchlistTicker } from "@/lib/watchlist-store";
import type {
  BenchmarkSummaryItem,
  ShapExplanationItem,
  StressIssuerItem,
  StressTestResponse,
} from "@/types/api";

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

function makeBenchmarkSummary(
  metric_name: BenchmarkSummaryItem["metric_name"],
  category: BenchmarkSummaryItem["category"],
  percentile_rank: number,
  direction: BenchmarkSummaryItem["direction"] = "HIGHER_IS_BETTER",
): BenchmarkSummaryItem {
  return {
    metric_name,
    display_name_vn: metric_name,
    category,
    unit: "%",
    format_type: "PERCENT",
    direction,
    company_value: 0.15,
    peer_median: 0.1,
    peer_p25: 0.05,
    peer_p75: 0.2,
    percentile_rank,
    robust_z_score: 0.8,
    peer_group_level: "ICB_LEVEL_3",
    icb_code_used: "1750",
    peer_count: 25,
    sparkline: [0.1, 0.15],
  };
}

function makeStressIssuer(
  ticker: string,
  overrides: Partial<StressIssuerItem> = {},
): StressIssuerItem {
  return {
    issuer_id: `ISS_${ticker}`,
    ticker,
    company_name_vn: `Công ty ${ticker}`,
    icb_code_level2: "5500",
    icb_name_level2: "Bất động sản",
    total_debt: 1_500_000_000_000,
    ebit_ttm: 200_000_000_000,
    cfo_ttm: 150_000_000_000,
    interest_coverage: 1.6,
    stressed_ebit_ttm: 90_000_000_000,
    stressed_cfo_ttm: 70_000_000_000,
    stressed_interest_coverage: 0.8,
    baseline_debt_at_risk: false,
    stressed_debt_at_risk: true,
    newly_distressed_under_shock: true,
    fx_coverage_status: "NOT_DISCLOSED",
    ...overrides,
  };
}

function makeStressRes(
  overrides: Partial<StressTestResponse> = {},
): StressTestResponse {
  return {
    snapshot_id: "snap-test",
    scenario_name: "Kịch bản kiểm thử",
    statement_scope: "CONSOLIDATED",
    period_type: "QUARTER",
    period_end_date: "2026-03-31",
    fx_depreciation_pct: 0,
    fx_disclosed_issuers_count: 0,
    total_issuers_evaluated: 100,
    total_debt_sum: 100_000_000_000_000,
    baseline_debt_at_risk_pct: 0.1,
    stressed_debt_at_risk_pct: 0.2,
    newly_distressed_count: 1,
    sector_summary: [
      {
        icb_code_level2: "5500",
        icb_name_level2: "Bất động sản",
        issuer_count: 40,
        total_debt_sum: 60_000_000_000_000,
        baseline_debt_at_risk_pct: 0.1,
        stressed_debt_at_risk_pct: 0.2,
        newly_distressed_count: 1,
      },
    ],
    newly_distressed_issuers: [],
    top_stressed_issuers: [],
    ...overrides,
  };
}

describe("Stream B Regression Suite (Wave 1–3 Fixes)", () => {
  beforeEach(() => {
    capturedSetOptions.length = 0;
    mockRouterReplace.mockReset();
    mockSearchParams = new URLSearchParams("tickers=FPT");
  });

  afterEach(() => {
    useRealAppProviders = false;
    useRealCompareCompanies = false;
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("F4: suppresses React Query retries for 404/422 ApiError and retries once for 500/network", () => {
    expect(shouldRetryFinintelQuery(0, new ApiError(404, "Not Found"))).toBe(false);
    expect(shouldRetryFinintelQuery(1, new ApiError(404, "Not Found"))).toBe(false);
    expect(shouldRetryFinintelQuery(0, new ApiError(422, "Unprocessable"))).toBe(false);
    expect(shouldRetryFinintelQuery(0, new ApiError(500, "Server Error"))).toBe(true);
    expect(shouldRetryFinintelQuery(1, new ApiError(500, "Server Error"))).toBe(false);
    expect(shouldRetryFinintelQuery(0, new Error("Network failure"))).toBe(true);
    expect(shouldRetryFinintelQuery(1, new Error("Network failure"))).toBe(false);
  });

  it("F7 & L9: parses and serializes exchange, single risk_tier, multi risk_tiers, and new sort fields", () => {
    expect(VALID_SORT_FIELDS).toContain("ebit_margin");
    expect(VALID_SORT_FIELDS).toContain("interest_coverage");

    const single = parseScreenerFilters(
      new URLSearchParams("risk_tier=R4&exchange=HOSE&sort_by=ebit_margin&sort_dir=asc"),
    );
    expect(single.risk_tier).toBe("R4");
    expect(single.risk_tiers).toEqual(["R4"]);
    expect(single.exchange).toBe("HOSE");
    expect(single.sort_by).toBe("ebit_margin");
    expect(single.sort_dir).toBe("asc");

    const multi = parseScreenerFilters(
      new URLSearchParams("risk_tiers=R4,R5,INVALID&exchange=INVALID&sort_by=interest_coverage"),
    );
    expect(multi.risk_tier).toBe("");
    expect(multi.risk_tiers).toEqual(["R4", "R5"]);
    expect(multi.exchange).toBe("");
    expect(multi.sort_by).toBe("interest_coverage");

    const singlePatch = filtersToUrlPatch({
      risk_tiers: ["R4"],
      exchange: "HNX",
      sort_by: "ebit_margin",
    });
    expect(singlePatch.risk_tier).toBe("R4");
    expect(singlePatch.risk_tiers).toBeNull();
    expect(singlePatch.exchange).toBe("HNX");

    const multiPatch = filtersToUrlPatch({
      risk_tiers: ["R4", "R5", "R4"],
      exchange: "UPCOM",
      sort_by: "interest_coverage",
    });
    expect(multiPatch.risk_tier).toBeNull();
    expect(multiPatch.risk_tiers).toBe("R4,R5");
    expect(multiPatch.exchange).toBe("UPCOM");
    expect(multiPatch.sort_by).toBe("interest_coverage");
  });

  it("D4 & D6: formats Piotroski N/A reasons, renders ScoreDots cleanly, maps REVIEWED_NO_OPINION, PIT labels, and EBIT_t", () => {
    const allCodes = [
      "F1_ROA",
      "F2_CFO",
      "F3_DELTA_ROA",
      "F4_ACCRUAL",
      "F5_DELTA_LEVERAGE",
      "F6_DELTA_LIQUIDITY",
      "F7_NO_DILUTION",
      "F8_DELTA_MARGIN",
      "F9_DELTA_TURNOVER",
    ] as const;

    const guardDetail = formatPiotroskiNaDetail("TT200_TT99_TAXONOMY_GUARD", allCodes);
    expect(guardDetail.summary).toContain("TT200 → TT99");
    expect(guardDetail.detailLine).toBe("Thiếu 9/9 tiêu chí thành phần");
    expect(guardDetail.tooltip).not.toMatch(/TT200_TT99_TAXONOMY_GUARD|F1_ROA|F9_DELTA_TURNOVER/);

    const dilutionDetail = formatPiotroskiNaDetail("MISSING_DILUTION_DATA", ["F7_NO_DILUTION"]);
    expect(dilutionDetail.summary).toBe("Thiếu dữ liệu pha loãng cổ phiếu");
    expect(dilutionDetail.detailLine).toBe("Thiếu tiêu chí: Không phát hành pha loãng cổ phiếu");
    expect(dilutionDetail.tooltip).not.toContain("MISSING_DILUTION_DATA");
    expect(dilutionDetail.tooltip).not.toContain("F7_NO_DILUTION");

    render(
      React.createElement(ScoreDots, {
        score: null,
        validComponents: 8,
        naReason: "MISSING_DILUTION_DATA",
        missingCriteria: ["F7_NO_DILUTION"],
      }),
    );
    expect(screen.getByText("Thiếu dữ liệu pha loãng cổ phiếu")).toBeTruthy();
    expect(
      screen.getByText("Thiếu tiêu chí: Không phát hành pha loãng cổ phiếu"),
    ).toBeTruthy();

    expect(AUDIT_OPINION_LABELS.REVIEWED_NO_OPINION).toBe(
      "Đã soát xét (không nêu ý kiến)",
    );
    expect(PEER_STANDARDIZATION_LABELS.PIT_REGIME_SYMMETRIC).toBeTruthy();
    expect(PEER_STANDARDIZATION_LABELS.PIT_ICB).toBeTruthy();
    const sanitized = sanitizeModelLimitationText(
      "Chuẩn hóa theo PIT_REGIME_SYMMETRIC và PIT_ICB.",
    );
    expect(sanitized).not.toContain("PIT_REGIME_SYMMETRIC");
    expect(sanitized).not.toContain("PIT_ICB");

    const formulaVi = formatFormulaVi("EBIT_t / IS_INTEREST_EXPENSE_t");
    expect(formulaVi).toContain("Lợi nhuận trước lãi vay và thuế (EBIT) (kỳ hiện tại)");
    expect(formulaVi).toContain("Chi phí lãi vay (kỳ hiện tại)");
    expect(formulaVi).not.toContain("EBIT_t");
    expect(formulaVi).not.toContain("IS_INTEREST_EXPENSE_t");
  });

  it("D11: resolves issuer_icrs[ticker] -> itemA.stressed_interest_coverage -> itemB.scenario_a_icr priority", () => {
    const scenarioA = makeStressRes({
      issuer_icrs: { AAA: 2.1, BBB: 1.45 },
      newly_distressed_issuers: [],
      top_stressed_issuers: [
        makeStressIssuer("CCC", { stressed_interest_coverage: 1.12 }),
      ],
    });
    const scenarioB = makeStressRes({
      newly_distressed_issuers: [
        makeStressIssuer("AAA", {
          scenario_a_icr: 2.4, // stale value must NOT override fresh issuer_icrs[AAA] (2.1)
          stressed_interest_coverage: 0.72,
        }),
        makeStressIssuer("BBB", {
          scenario_a_icr: null, // uses scenarioA.issuer_icrs["BBB"] (1.45)
          stressed_interest_coverage: 0.65,
        }),
        makeStressIssuer("CCC", {
          scenario_a_icr: 1.9, // falls back to itemA.stressed_interest_coverage (1.12) first
          stressed_interest_coverage: 0.5,
        }),
        makeStressIssuer("DDD", {
          scenario_a_icr: 1.85, // falls back to itemB.scenario_a_icr when issuer_icrs & itemA lack DDD
          stressed_interest_coverage: 0.4,
        }),
        makeStressIssuer("EEE", {
          scenario_a_icr: undefined,
          stressed_interest_coverage: 0.3,
        }),
      ],
      top_stressed_issuers: [],
    });

    const comparison = computeScenarioComparison(scenarioA, scenarioB);
    const byTicker = new Map(comparison.issuerDeltas.map((r) => [r.ticker, r]));

    expect(byTicker.get("AAA")?.icrA).toBe(2.1);
    expect(byTicker.get("BBB")?.icrA).toBe(1.45);
    expect(byTicker.get("CCC")?.icrA).toBe(1.12);
    expect(byTicker.get("DDD")?.icrA).toBe(1.85);
    expect(byTicker.get("EEE")?.icrA).toBeNull();
    expect(byTicker.get("AAA")?.icrB).toBe(0.72);
  });

  it("F8: computes monotonic 1Y/3Y/5Y/ALL dataZoom ranges from trading dates", () => {
    const dates = [
      "2019-01-02",
      "2020-06-15",
      "2021-06-15",
      "2022-06-15",
      "2023-06-15",
      "2024-06-15",
      "2025-06-15",
      "2026-03-31",
    ];

    const rAll = computePriceOverlayZoomRange(dates, "ALL");
    const r5y = computePriceOverlayZoomRange(dates, "5Y");
    const r3y = computePriceOverlayZoomRange(dates, "3Y");
    const r1y = computePriceOverlayZoomRange(dates, "1Y");

    expect(rAll).toEqual({ start: 0, end: 100 });
    expect(r5y.end).toBe(100);
    expect(r3y.end).toBe(100);
    expect(r1y.end).toBe(100);
    expect(r5y.start).toBeGreaterThan(0);
    expect(r3y.start).toBeGreaterThan(r5y.start);
    expect(r1y.start).toBeGreaterThan(r3y.start);

    expect(computePriceOverlayZoomRange(["2026-03-31"], "1Y")).toEqual({
      start: 0,
      end: 100,
    });
  });

  it("V1: EChartBox switches to active Xuất CSV in table mode and extracts text from ReactNode <Link> cells", async () => {
    let capturedBlobText = "";
    const origBlob = globalThis.Blob;
    vi.stubGlobal(
      "Blob",
      class MockBlob extends origBlob {
        constructor(parts?: BlobPart[], options?: BlobPropertyBag) {
          super(parts, options);
          capturedBlobText = (parts ?? []).map((p) => String(p)).join("");
        }
      },
    );
    const createObjSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:mock-csv");
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(
      React.createElement(EChartBox, {
        title: "Phân bố Rủi ro & Sinh lời",
        option: {
          xAxis: { type: "value" },
          yAxis: { type: "value" },
          series: [],
        },
        tableHeaders: ["Mã", "ROE"],
        tableRows: [
          [
            React.createElement("a", { href: "/companies/FPT" }, "FPT"),
            "24,5%",
          ],
          [
            React.createElement("span", null, React.createElement("strong", null, "HPG")),
            "14,2%",
          ],
        ],
      }),
    );

    expect(screen.getByRole("button", { name: /Xuất PNG/i })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Xem dạng bảng/i }));
    const csvBtn = screen.getByRole("button", { name: /Xuất CSV/i });
    expect(csvBtn).toBeTruthy();
    expect((csvBtn as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByRole("button", { name: /Xuất PNG/i })).toBeNull();

    fireEvent.click(csvBtn);
    expect(createObjSpy).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeSpy).toHaveBeenCalledWith("blob:mock-csv");
    expect(capturedBlobText.startsWith("\uFEFFMã,ROE\r\n")).toBe(true);
    expect(capturedBlobText).toContain('FPT,"24,5%"');
    expect(capturedBlobText).toContain('HPG,"14,2%"');
  });

  it("V6: ExplainWaterfallChart renders horizontal bar chart (yAxis category) and preserves resolveCrossFilteredMlScore", () => {
    const shap: ShapExplanationItem = {
      base_value: -2.4,
      prediction_score: -1.1,
      other_features_delta: 0.05,
      top_risk_increasing: [
        {
          feature: "debt_to_equity",
          display_name_vn: "Nợ vay / VCSH",
          value: 2.3,
          peer_median: 0.9,
          shap_delta: 0.9,
        },
        {
          feature: "roa",
          display_name_vn: "ROA",
          value: 0.05,
          peer_median: 0.08,
          shap_delta: 0.002, // filtered noise
        },
      ],
      top_risk_decreasing: [
        {
          feature: "dso",
          display_name_vn: "Số ngày phải thu bình quân",
          value: 25,
          peer_median: 42,
          shap_delta: -0.35,
        },
      ],
    };

    render(
      React.createElement(ExplainWaterfallChart, {
        shap,
        distressProb: 0.12,
        periodLabel: "Q4/2025",
      }),
    );

    expect(capturedSetOptions.length).toBeGreaterThan(0);
    const opt = capturedSetOptions[capturedSetOptions.length - 1] as {
      xAxis?: { type?: string };
      yAxis?: { type?: string; inverse?: boolean; data?: string[] };
      series?: Array<{ type?: string; data?: Array<{ value: number }> }>;
    };
    expect(opt.xAxis?.type).toBe("value");
    expect(opt.yAxis?.type).toBe("category");
    expect(opt.yAxis?.inverse).toBe(true);
    expect(opt.yAxis?.data).toEqual([
      "Nợ vay / VCSH",
      "Số ngày phải thu bình quân",
      "Các biến số còn lại",
    ]);
    expect(opt.series?.[0]?.type).toBe("bar");
    expect(opt.series?.[0]?.data?.map((d) => d.value)).toEqual([0.9, -0.35, 0.05]);

    const scores = [
      { period_end_date: "2025-09-30", score: 1 },
      { period_end_date: "2025-12-31", score: 2 },
    ];
    const resolved = resolveCrossFilteredMlScore(scores, "2025-09-30", "2025-12-31");
    expect(resolved.active?.score).toBe(1);
    expect(resolved.defaultLatest?.score).toBe(2);
    expect(resolved.isCustomSelected).toBe(true);
  });

  it("V9 & V10: Light/Dark palettes expose 5 distinct non-purple/non-cyan colors and CompareFivePillarChart renders cleanly", () => {
    for (const palette of [
      LIGHT_CHART_PALETTE,
      DARK_CHART_PALETTE,
      getChartPalette("light"),
      getChartPalette("dark"),
    ]) {
      expect(palette.series).toHaveLength(5);
      expect(new Set(palette.series).size).toBe(5);
      expect(palette.series[0]).not.toBe(palette.series[4]);
      for (const hex of palette.series) {
        expect(hex.toUpperCase()).not.toMatch(/#6B4C9A|#00FFFF|#06B6D4|#22D3EE/);
      }
    }

    const fptItems: BenchmarkSummaryItem[] = [
      makeBenchmarkSummary("roe_total", "PROFITABILITY", 88),
      makeBenchmarkSummary("current_ratio", "LIQUIDITY", 72),
      makeBenchmarkSummary("debt_to_equity", "LEVERAGE", 25, "LOWER_IS_BETTER"),
      makeBenchmarkSummary("ocf_to_net_income", "CASH_FLOW", 81),
      makeBenchmarkSummary("revenue_growth_yoy", "GROWTH", 68),
    ];
    const hpgItems: BenchmarkSummaryItem[] = [
      makeBenchmarkSummary("roe_total", "PROFITABILITY", 54),
      makeBenchmarkSummary("current_ratio", "LIQUIDITY", 48),
      makeBenchmarkSummary("debt_to_equity", "LEVERAGE", 62, "LOWER_IS_BETTER"),
      makeBenchmarkSummary("ocf_to_net_income", "CASH_FLOW", 55),
      makeBenchmarkSummary("revenue_growth_yoy", "GROWTH", 42),
    ];

    render(
      React.createElement(CompareFivePillarChart, {
        companies: [
          { ticker: "FPT", items: fptItems },
          { ticker: "HPG", items: hpgItems },
        ],
      }),
    );

    expect(
      screen.getByText("So sánh hồ sơ 5 trụ cột sức khỏe tài chính"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Xem dạng bảng/i }));
    expect(screen.getByText("FPT")).toBeTruthy();
    expect(screen.getByText("HPG")).toBeTruthy();
    expect(screen.getAllByText("50/100").length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText("88/100 (Mức 5/5)")).toBeTruthy();
  });

  it("U5: HealthRadarChart uses Mức X/5 terminology instead of Q1–Q5", () => {
    const items: BenchmarkSummaryItem[] = [
      makeBenchmarkSummary("roe_total", "PROFITABILITY", 90),
      makeBenchmarkSummary("current_ratio", "LIQUIDITY", 70),
      makeBenchmarkSummary("debt_to_equity", "LEVERAGE", 15, "LOWER_IS_BETTER"),
      makeBenchmarkSummary("ocf_to_net_income", "CASH_FLOW", 65),
      makeBenchmarkSummary("revenue_growth_yoy", "GROWTH", 30),
    ];

    render(
      React.createElement(HealthRadarChart, {
        ticker: "FPT",
        items,
      }),
    );

    expect(
      screen.getByText(/Mức 1\/5 yếu nhất → Mức 5\/5 tốt nhất/),
    ).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\bQ[1-5]\b/);

    fireEvent.click(screen.getByRole("button", { name: /Xem dạng bảng/i }));
    expect(screen.getAllByText("Mức 5/5").length).toBeGreaterThanOrEqual(1);
    expect(document.body.textContent).not.toMatch(/\bQ[1-5]\/5\b/);
  });

  it("F7 & L9: supports max_red_flags=10 and isolates invalid URL params per field", () => {
    const partial = parseScreenerFilters(
      new URLSearchParams(
        "max_red_flags=10&sort_by=ebit_margin&altman_zone=BOGUS&min_f_score=6&sort_dir=asc",
      ),
    );
    expect(partial.max_red_flags).toBe("10");
    expect(partial.sort_by).toBe("ebit_margin");
    expect(partial.sort_dir).toBe("asc");
    expect(partial.min_f_score).toBe("6");
    expect(partial.altman_zone).toBe("");

    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
    const saved = saveScreenerView("10 cờ đỏ", { max_red_flags: "10" });
    expect(saved.ok).toBe(true);
    if (saved.ok) {
      expect(saved.screen.filters.max_red_flags).toBe("10");
    }
    expect(saveScreenerView("Không hợp lệ", { max_red_flags: "11" }).ok).toBe(false);
  });

  it("F1 & F2: ComparePage and WatchlistPage preserve exact-ticker precedence on Enter and button submit", () => {
    mockSearchParams = new URLSearchParams("tickers=FPT");
    render(React.createElement(ComparePage));

    const compareInput = screen.getByLabelText("Nhập mã cổ phiếu để thêm vào bảng so sánh");
    fireEvent.change(compareInput, { target: { value: "FPT" } });
    fireEvent.keyDown(compareInput, { key: "Enter" });
    expect(screen.getByRole("alert").textContent).toBe(
      "Mã FPT đã có trong danh sách so sánh.",
    );
    expect(mockRouterReplace).not.toHaveBeenCalled();

    fireEvent.change(compareInput, { target: { value: "FPT" } });
    fireEvent.click(screen.getByRole("button", { name: /^Thêm$/ }));
    expect(screen.getByRole("alert").textContent).toBe(
      "Mã FPT đã có trong danh sách so sánh.",
    );
    expect(mockRouterReplace).not.toHaveBeenCalled();

    fireEvent.change(compareInput, { target: { value: "VCB" } });
    fireEvent.keyDown(compareInput, { key: "Enter" });
    expect(screen.getByRole("alert").textContent).toBe(
      'Không tìm thấy doanh nghiệp mã "VCB" trong phạm vi dữ liệu FinIntel.',
    );
    expect(mockRouterReplace).not.toHaveBeenCalled();

    cleanup();
    removeWatchlistTicker("FPT");
    addWatchlistTicker("FPT");
    render(React.createElement(WatchlistPage));

    const watchInput = screen.getByLabelText("Tìm mã cổ phiếu để thêm vào danh sách theo dõi");
    fireEvent.change(watchInput, { target: { value: "FPT" } });
    fireEvent.keyDown(watchInput, { key: "Enter" });
    expect(screen.getByRole("alert").textContent).toBe(
      "Mã FPT đã có trong danh sách theo dõi.",
    );

    fireEvent.change(watchInput, { target: { value: "VCB" } });
    fireEvent.click(screen.getByRole("button", { name: /Thêm mã/i }));
    expect(screen.getByRole("alert").textContent).toBe(
      'Không tìm thấy doanh nghiệp mã "VCB" trong phạm vi dữ liệu FinIntel.',
    );
    removeWatchlistTicker("FPT");
  });

  it("U3 & U4: sub-half-ULP negative inputs format as positive zero without minus sign", () => {
    expect(formatRatioByType(-0.4, "CURRENCY_PER_SHARE")).toBe("0 đ/cp");
    expect(formatRatioByType(-0.004, "ZSCORE")).toBe("0,00 σ");
    expect(formatPercentPoints(-0.0004, 1)).toBe("0,0 điểm %");
  });

  it("Reviewer 2: delta_current_ratio_4q resolves to NUMBER and PriceRiskOverlayChart increases grid.top when narrow", () => {
    expect(resolveMlFeatureFormatType("delta_current_ratio_4q")).toBe("NUMBER");

    const sampleOverlay = {
      snapshot_id: "snap-1",
      issuer_id: "ISS_FPT",
      ticker: "FPT",
      as_of: "2026-03-31",
      latest_valuation: null,
      price_series: [
        {
          trade_date: "2026-03-30",
          close_adj: 100_000,
          close_raw: 100_000,
          volume: 1000,
          shares_outstanding_market: 1_000_000,
          running_drawdown_pct: 0,
          drawdown_from_first_warning_pct: null,
          market_cap_vnd: 100_000_000_000,
          pe_ttm: 15,
          pb_ratio: 3,
          pe_flag: "VALID",
          pb_flag: "VALID",
          shares_source: "SHARES_OUTSTANDING_MARKET",
        },
      ],
      model_markers: [],
      regulatory_markers: [],
      earliest_warning_summary: null,
    } satisfies Parameters<typeof buildPriceOverlayOption>[0];

    const desktopOpt = buildPriceOverlayOption(sampleOverlay, LIGHT_CHART_PALETTE, "ALL", false);
    const mobileOpt = buildPriceOverlayOption(sampleOverlay, LIGHT_CHART_PALETTE, "ALL", true);
    expect((desktopOpt.grid as { top?: number }).top).toBe(44);
    expect((mobileOpt.grid as { top?: number }).top).toBeGreaterThanOrEqual(56);
  });

  it("D11 hardening: explicit null in scenarioA.issuer_icrs does not fall back to stale itemB.scenario_a_icr", () => {
    const scenarioA = makeStressRes({
      scenario_name: "Kịch bản A",
      issuer_icrs: { NVL: null },
      newly_distressed_issuers: [makeStressIssuer("NVL", { stressed_interest_coverage: 0.7 })],
    });
    const scenarioB = makeStressRes({
      scenario_name: "Kịch bản B",
      newly_distressed_issuers: [
        makeStressIssuer("NVL", {
          stressed_interest_coverage: 0.5,
          scenario_a_icr: 1.85,
        }),
      ],
    });

    const cmp = computeScenarioComparison(scenarioA, scenarioB);
    expect(cmp.issuerDeltas).toHaveLength(1);
    expect(cmp.issuerDeltas[0]?.ticker).toBe("NVL");
    expect(cmp.issuerDeltas[0]?.icrA).toBeNull();
  });

  it("Challenger 1: computeScenarioComparison guards icrB against Object.prototype lookups on REMOVED_IN_B", () => {
    const scenarioA = makeStressRes({
      issuer_icrs: {},
      newly_distressed_issuers: [
        makeStressIssuer("toString", { stressed_interest_coverage: 0.5 }),
        makeStressIssuer("hasOwnProperty", { stressed_interest_coverage: 0.7 }),
      ],
    });
    const scenarioB = makeStressRes({
      issuer_icrs: {},
      newly_distressed_issuers: [],
      top_stressed_issuers: [],
    });

    const cmp = computeScenarioComparison(scenarioA, scenarioB);
    const byTicker = new Map(cmp.issuerDeltas.map((r) => [r.ticker, r]));

    expect(byTicker.get("toString")?.status).toBe("REMOVED_IN_B");
    expect(byTicker.get("toString")?.icrB).toBeNull();
    expect(byTicker.get("hasOwnProperty")?.status).toBe("REMOVED_IN_B");
    expect(byTicker.get("hasOwnProperty")?.icrB).toBeNull();
  });

  it("Reviewer 1: queryPeriodEndDate remains null when useMetaPeriods resolves so useCompareCompanies avoids a cold-load double-GET", async () => {
    useRealAppProviders = true;
    useRealCompareCompanies = true;

    let resolveMetaPeriods!: (val: Awaited<ReturnType<typeof apiClient.getMetaPeriods>>) => void;
    const pendingMetaPeriods = new Promise<Awaited<ReturnType<typeof apiClient.getMetaPeriods>>>(
      (resolve) => {
        resolveMetaPeriods = resolve;
      },
    );

    vi.spyOn(apiClient, "getHealth").mockResolvedValue({
      status: "ok",
      active_snapshot_id: "snap-cold-1",
      storage_backend: "ducklake",
      rag_available: false,
    });
    vi.spyOn(apiClient, "getMetaPeriods").mockReturnValue(pendingMetaPeriods);
    const profileSpy = vi.spyOn(apiClient, "getCompanyProfile").mockResolvedValue({
      snapshot_id: "snap-cold-1",
      issuer_id: "ISS_HPG",
      ticker: "HPG",
      company_name_vn: "Hòa Phát",
      exchange: "HOSE",
      icb_code_level2: "1700",
      icb_name_level2: "Tài nguyên cơ bản",
      statement_scope: "CONSOLIDATED",
      period_view: "TTM",
      ml_scores: [],
      red_flags: [],
      growth_series: [],
      ratios_table: [],
    } as unknown as Awaited<ReturnType<typeof apiClient.getCompanyProfile>>);
    const summarySpy = vi
      .spyOn(apiClient, "getCompanyBenchmarksSummary")
      .mockResolvedValue({
        snapshot_id: "snap-cold-1",
        ticker: "HPG",
        issuer_id: "ISS_HPG",
        statement_scope: "CONSOLIDATED",
        period_type: "QUARTER",
        period_end_date: "2026-09-30",
        items: [],
      });

    function ColdLoadHarness() {
      const {
        statementScope,
        periodType,
        periodEndDate,
        queryPeriodEndDate,
        setPeriodEndDate,
        cacheSnapshotKey,
      } = useAppState();
      const { summaries } = useCompareCompanies(
        ["HPG"],
        statementScope,
        periodType,
        queryPeriodEndDate,
        cacheSnapshotKey,
      );
      return React.createElement(
        "div",
        null,
        React.createElement("span", { "data-testid": "period-end" }, periodEndDate ?? "null"),
        React.createElement(
          "span",
          { "data-testid": "query-period-end" },
          queryPeriodEndDate ?? "null",
        ),
        React.createElement(
          "span",
          { "data-testid": "summary-status" },
          summaries[0]?.status ?? "none",
        ),
        React.createElement(
          "button",
          { type: "button", onClick: () => setPeriodEndDate("2026-06-30") },
          "Pick historical",
        ),
        React.createElement(
          "button",
          { type: "button", onClick: () => setPeriodEndDate("2026-09-30") },
          "Pick latest",
        ),
      );
    }

    render(React.createElement(AppProviders, null, React.createElement(ColdLoadHarness)));

    await waitFor(() => {
      expect(screen.getByTestId("summary-status").textContent).toBe("success");
    });
    expect(screen.getByTestId("period-end").textContent).toBe("null");
    expect(screen.getByTestId("query-period-end").textContent).toBe("null");
    expect(summarySpy).toHaveBeenCalledTimes(1);
    expect(profileSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveMetaPeriods({
        snapshot_id: "snap-cold-1",
        published_at: "2026-11-26",
        available_periods: {
          CONSOLIDATED: {
            QUARTER: ["2026-09-30", "2026-06-30"],
            ANNUAL: ["2025-12-31"],
          },
          SEPARATE: { QUARTER: [], ANNUAL: [] },
        },
        sectors_level2: [],
        freshness: null,
      } as unknown as Awaited<ReturnType<typeof apiClient.getMetaPeriods>>);
    });

    await waitFor(() => {
      expect(screen.getByTestId("period-end").textContent).toBe("2026-09-30");
    });
    expect(screen.getByTestId("query-period-end").textContent).toBe("null");
    expect(summarySpy).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Pick historical" }));
    await waitFor(() => {
      expect(screen.getByTestId("query-period-end").textContent).toBe("2026-06-30");
      expect(summarySpy).toHaveBeenCalledTimes(2);
    });

    fireEvent.click(screen.getByRole("button", { name: "Pick latest" }));
    await waitFor(() => {
      expect(screen.getByTestId("query-period-end").textContent).toBe("null");
    });
    expect(summarySpy).toHaveBeenCalledTimes(2);
  });
});
