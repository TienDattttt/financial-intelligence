import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  clampShockValue,
  computePortfolioStressMetrics,
  computeScenarioComparison,
  DEFAULT_SHOCKS,
  normalizeTickerList,
  parseStressUrlParams,
  serializeStressUrlParams,
  SHOCK_BOUNDS,
  type StressUrlState,
} from "@/lib/stress-test-helpers";
import type { StressIssuerItem, StressTestResponse } from "@/types/api";

function makeIssuer(
  ticker: string,
  overrides: Partial<StressIssuerItem> = {},
): StressIssuerItem {
  return {
    issuer_id: `ISS_${ticker}`,
    ticker,
    company_name_vn: `Công ty ${ticker}`,
    icb_code_level2: "5500",
    icb_name_level2: "Bất động sản",
    total_debt: 1_000_000_000_000,
    ebit_ttm: 200_000_000_000,
    cfo_ttm: 180_000_000_000,
    interest_coverage: 1.8,
    stressed_ebit_ttm: 110_000_000_000,
    stressed_cfo_ttm: 90_000_000_000,
    stressed_interest_coverage: 0.85,
    baseline_debt_at_risk: false,
    stressed_debt_at_risk: true,
    newly_distressed_under_shock: true,
    ...overrides,
  };
}

function makeStressResponse(
  overrides: Partial<StressTestResponse> = {},
): StressTestResponse {
  return {
    snapshot_id: "snap-2026-q1",
    scenario_name: "Kịch bản mẫu",
    statement_scope: "CONSOLIDATED",
    period_type: "QUARTER",
    period_end_date: "2026-03-31",
    total_issuers_evaluated: 100,
    total_debt_sum: 100_000_000_000_000,
    baseline_debt_at_risk_pct: 0.12,
    stressed_debt_at_risk_pct: 0.2,
    newly_distressed_count: 2,
    sector_summary: [
      {
        icb_code_level2: "5500",
        icb_name_level2: "Bất động sản",
        issuer_count: 40,
        total_debt_sum: 60_000_000_000_000,
        baseline_debt_at_risk_pct: 0.15,
        stressed_debt_at_risk_pct: 0.25,
        newly_distressed_count: 2,
      },
      {
        icb_code_level2: "1700",
        icb_name_level2: "Tài nguyên cơ bản",
        issuer_count: 60,
        total_debt_sum: 40_000_000_000_000,
        baseline_debt_at_risk_pct: 0.075,
        stressed_debt_at_risk_pct: 0.125,
        newly_distressed_count: 0,
      },
    ],
    newly_distressed_issuers: [makeIssuer("NVL"), makeIssuer("DXG")],
    top_stressed_issuers: [
      makeIssuer("NVL"),
      makeIssuer("DXG"),
      makeIssuer("HPG", {
        icb_code_level2: "1700",
        icb_name_level2: "Tài nguyên cơ bản",
        total_debt: 5_000_000_000_000,
        stressed_interest_coverage: 1.35,
        stressed_debt_at_risk: false,
        newly_distressed_under_shock: false,
      }),
    ],
    ...overrides,
  };
}

describe("UI-06 Stress Test helpers, URL sync, portfolio aggregation, and A vs B comparison", () => {
  it("round-trips MARKET, COMPANY, and WATCHLIST URL states with all 5 shock parameters", () => {
    const states: StressUrlState[] = [
      {
        scope: "MARKET",
        companyTicker: null,
        watchlistTickers: [],
        shocks: {
          scenarioName: "Suy giảm cầu",
          revPct: -15,
          gmPp: -2.5,
          rateBps: 250,
          dsoDays: 30,
          dioDays: 15,
        },
      },
      {
        scope: "COMPANY",
        companyTicker: "HPG",
        watchlistTickers: [],
        shocks: {
          scenarioName: "Cú sốc thép",
          revPct: -20,
          gmPp: -3.5,
          rateBps: 150,
          dsoDays: 20,
          dioDays: 10,
        },
      },
      {
        scope: "WATCHLIST",
        companyTicker: null,
        watchlistTickers: ["HPG", "FPT", "VNM"],
        shocks: {
          scenarioName: "Danh mục phòng thủ",
          revPct: -10,
          gmPp: -1,
          rateBps: 300,
          dsoDays: 15,
          dioDays: 5,
        },
      },
    ];

    for (const original of states) {
      const qs = serializeStressUrlParams(original);
      const parsed = parseStressUrlParams(qs, DEFAULT_SHOCKS);
      expect(parsed.hasStressParams).toBe(true);
      expect(parsed.state).toEqual(original);
    }
  });

  it("clamps out-of-range, NaN, and malformed URL shock parameters safely", () => {
    expect(clampShockValue(-999, SHOCK_BOUNDS.revPct.min, SHOCK_BOUNDS.revPct.max)).toBe(-80);
    expect(clampShockValue(999, SHOCK_BOUNDS.revPct.min, SHOCK_BOUNDS.revPct.max)).toBe(50);
    expect(clampShockValue("NaN", -50, 20, 0)).toBe(0);
    expect(clampShockValue(Infinity, -500, 1500, 100)).toBe(100);
    expect(clampShockValue(-0, -90, 180, 0)).toBe(0);

    const maliciousParams = new URLSearchParams(
      "scope=COMPANY&tickers=hpg,invalid!,fpt&rev=-500&gm=999&rate=abc&dso=-200&dio=500",
    );
    const parsed = parseStressUrlParams(maliciousParams, DEFAULT_SHOCKS);
    expect(parsed.hasStressParams).toBe(true);
    expect(parsed.state.scope).toBe("COMPANY");
    expect(parsed.state.companyTicker).toBe("HPG");
    expect(parsed.state.shocks).toEqual({
      scenarioName: "Tùy chỉnh",
      revPct: -80,
      gmPp: 20,
      rateBps: 0,
      dsoDays: -90,
      dioDays: 180,
    });

    expect(
      normalizeTickerList([" fpt ", "HPG", "fpt", "bad ticker!", "vnm", "12345678901"]),
    ).toEqual(["FPT", "HPG", "VNM"]);
  });

  it("computes Watchlist portfolio total Debt-at-Risk and missing ticker count accurately", () => {
    const watchlistRes = makeStressResponse({
      total_issuers_evaluated: 2,
      total_debt_sum: 20_000_000_000_000, // 20,000 tỷ
      baseline_debt_at_risk_pct: 0.1, // 2,000 tỷ
      stressed_debt_at_risk_pct: 0.35, // 7,000 tỷ
      newly_distressed_count: 1,
    });

    const metrics = computePortfolioStressMetrics(watchlistRes, [
      "HPG",
      "NVL",
      "UNKNOWN_TICKER",
    ]);

    expect(metrics.requestedCount).toBe(2); // UNKNOWN_TICKER exceeds 10 chars -> filtered
    const metricsThree = computePortfolioStressMetrics(watchlistRes, [
      "HPG",
      "NVL",
      "VCB",
    ]);
    expect(metricsThree.requestedCount).toBe(3);
    expect(metricsThree.evaluatedCount).toBe(2);
    expect(metricsThree.missingCount).toBe(1);
    expect(metricsThree.totalDebtVnd).toBe(20_000_000_000_000);
    expect(metricsThree.baselineDebtAtRiskVnd).toBeCloseTo(2_000_000_000_000, 0);
    expect(metricsThree.stressedDebtAtRiskVnd).toBeCloseTo(7_000_000_000_000, 0);
    expect(metricsThree.deltaDebtAtRiskVnd).toBeCloseTo(5_000_000_000_000, 0);
    expect(metricsThree.deltaDebtAtRiskPct).toBeCloseTo(0.25, 6);
    expect(metricsThree.newlyDistressedCount).toBe(1);
  });

  it("computes Scenario A vs Scenario B deltas for Debt-at-Risk, sectors, and newly distressed issuers", () => {
    const scenarioA = makeStressResponse({
      scenario_name: "Kịch bản A (+200 bps)",
      total_debt_sum: 100_000_000_000_000,
      stressed_debt_at_risk_pct: 0.18, // 18,000 tỷ
      newly_distressed_count: 2,
      newly_distressed_issuers: [
        makeIssuer("NVL", { total_debt: 3_000_000_000_000, stressed_interest_coverage: 0.9 }),
        makeIssuer("DXG", { total_debt: 2_000_000_000_000, stressed_interest_coverage: 0.95 }),
      ],
      sector_summary: [
        {
          icb_code_level2: "5500",
          icb_name_level2: "Bất động sản",
          issuer_count: 40,
          total_debt_sum: 60_000_000_000_000,
          baseline_debt_at_risk_pct: 0.15,
          stressed_debt_at_risk_pct: 0.25,
          newly_distressed_count: 2,
        },
        {
          icb_code_level2: "1700",
          icb_name_level2: "Tài nguyên cơ bản",
          issuer_count: 60,
          total_debt_sum: 40_000_000_000_000,
          baseline_debt_at_risk_pct: 0.05,
          stressed_debt_at_risk_pct: 0.075,
          newly_distressed_count: 0,
        },
      ],
    });

    const scenarioB = makeStressResponse({
      scenario_name: "Kịch bản B (Khủng hoảng kép)",
      total_debt_sum: 100_000_000_000_000,
      stressed_debt_at_risk_pct: 0.29, // 29,000 tỷ
      newly_distressed_count: 3,
      newly_distressed_issuers: [
        makeIssuer("NVL", { total_debt: 3_000_000_000_000, stressed_interest_coverage: 0.55 }),
        makeIssuer("HPG", {
          icb_code_level2: "1700",
          icb_name_level2: "Tài nguyên cơ bản",
          total_debt: 5_000_000_000_000,
          stressed_interest_coverage: 0.82,
        }),
        makeIssuer("PDR", { total_debt: 1_500_000_000_000, stressed_interest_coverage: 0.78 }),
      ],
      top_stressed_issuers: [
        makeIssuer("HPG", {
          icb_code_level2: "1700",
          icb_name_level2: "Tài nguyên cơ bản",
          total_debt: 5_000_000_000_000,
          stressed_interest_coverage: 0.82,
        }),
        makeIssuer("NVL", { total_debt: 3_000_000_000_000, stressed_interest_coverage: 0.55 }),
        makeIssuer("DXG", {
          total_debt: 2_000_000_000_000,
          stressed_interest_coverage: 1.05,
          stressed_debt_at_risk: false,
          newly_distressed_under_shock: false,
        }),
      ],
      sector_summary: [
        {
          icb_code_level2: "5500",
          icb_name_level2: "Bất động sản",
          issuer_count: 40,
          total_debt_sum: 60_000_000_000_000,
          baseline_debt_at_risk_pct: 0.15,
          stressed_debt_at_risk_pct: 0.35,
          newly_distressed_count: 2,
        },
        {
          icb_code_level2: "1700",
          icb_name_level2: "Tài nguyên cơ bản",
          issuer_count: 60,
          total_debt_sum: 40_000_000_000_000,
          baseline_debt_at_risk_pct: 0.05,
          stressed_debt_at_risk_pct: 0.2,
          newly_distressed_count: 1,
        },
      ],
    });

    const diff = computeScenarioComparison(scenarioA, scenarioB);

    expect(diff.scopeMismatch).toBe(false);
    expect(diff.debtAtRiskPctDelta).toBeCloseTo(0.11, 6);
    expect(diff.debtAtRiskVndDelta).toBeCloseTo(11_000_000_000_000, 0);
    expect(diff.newlyDistressedCountDelta).toBe(1);
    expect(diff.addedDistressedCount).toBe(2); // HPG, PDR
    expect(diff.resolvedDistressedCount).toBe(1); // DXG

    const steelSector = diff.sectorDeltas.find((s) => s.icbCodeLevel2 === "1700");
    expect(steelSector).toBeDefined();
    expect(steelSector?.debtAtRiskPctDelta).toBeCloseTo(0.125, 6);
    expect(steelSector?.newlyDistressedDelta).toBe(1);

    const hpgRow = diff.issuerDeltas.find((it) => it.ticker === "HPG");
    expect(hpgRow?.status).toBe("ADDED_IN_B");
    expect(hpgRow?.icrB).toBeCloseTo(0.82, 2);

    const dxgRow = diff.issuerDeltas.find((it) => it.ticker === "DXG");
    expect(dxgRow?.status).toBe("REMOVED_IN_B");
    expect(dxgRow?.icrA).toBeCloseTo(0.95, 2);
    expect(dxgRow?.icrB).toBeCloseTo(1.05, 2);
  });

  it("removes dead useStressTestMutation hook and keeps UI-06 free of banned colors or backend tokens", () => {
    const root = path.resolve(__dirname, "../..");
    const queriesSrc = fs.readFileSync(
      path.join(root, "src/hooks/use-finintel-queries.ts"),
      "utf8",
    );
    const stressPageSrc = fs.readFileSync(
      path.join(root, "src/app/stress-test/page.tsx"),
      "utf8",
    );
    const chartSrc = fs.readFileSync(
      path.join(root, "src/components/charts/StressSectorBarChart.tsx"),
      "utf8",
    );

    expect(queriesSrc).not.toContain("useStressTestMutation");
    expect(chartSrc).not.toMatch(/#[0-9a-fA-F]{6}/);
    expect(stressPageSrc).not.toContain("BS_TOTAL_ASSETS");
    expect(stressPageSrc).not.toContain("REGIME_FALLBACK");
    expect(stressPageSrc).not.toContain(".parquet");
  });
});
