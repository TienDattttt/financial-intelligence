import { describe, expect, it } from "vitest";
import {
  buildCapitalStructureTitle,
  transformCapitalStructureSeries,
} from "@/components/charts/CapitalStructureChart";
import { buildSafeExportFilename } from "@/components/charts/EChartBox";
import { resolveCrossFilteredMlScore } from "@/components/charts/ExplainWaterfallChart";
import {
  buildGrowthChartTitle,
  transformGrowthSeries,
} from "@/components/charts/GrowthComboChart";
import {
  computeFivePillarProfile,
  groupAndSortSummaryRatios,
} from "@/components/charts/HealthRadarChart";
import { computeBoxPlotStats } from "@/components/charts/SectorBoxPlotChart";
import {
  buildSectorTreemapNodes,
  buildSectorTreemapTitle,
} from "@/components/charts/SectorTreemapChart";
import {
  buildStatementLineTrend,
  formatAuditStatusBadge,
  getStatementRowHierarchy,
} from "@/components/financial/FinancialTable";
import { LIGHT_CHART_PALETTE } from "@/lib/echarts-theme";
import type {
  BenchmarkSummaryItem,
  CapitalStructurePoint,
  GrowthPoint,
  PeerRankingItem,
  SectorRiskItem,
  StatementPeriodItem,
  StatementRowItem,
} from "@/types/api";

describe("UI-04a: EChartBox Export, Sector Treemap, Growth Combo, Capital Structure", () => {
  it("sanitizes Vietnamese chart titles into safe PNG filenames", () => {
    expect(
      buildSafeExportFilename("Bản đồ rủi ro ngành: Bất động sản dẫn đầu (12,5%)"),
    ).toBe("ban-do-rui-ro-nganh-bat-dong-san-dan-dau-12-5.png");
    expect(buildSafeExportFilename("   ---   ")).toBe("finintel-chart.png");
  });

  it("builds proportional Sector Treemap nodes (area = issuer_count) with semantic palette colors", () => {
    const sectors: SectorRiskItem[] = [
      {
        icb_code_level2: "8600",
        icb_name_level2: "Bất động sản",
        issuer_count: 60,
        safe_zone_count: 15,
        grey_zone_count: 20,
        distress_zone_count: 25,
        beneish_flagged_count: 10,
        high_red_flag_count: 14,
        median_distress_prob: 0.11,
        avg_red_flags: 1.7,
        total_debt_sum: 250_000_000_000_000,
      },
      {
        icb_code_level2: "3500",
        icb_name_level2: "Thực phẩm & Đồ uống",
        issuer_count: 40,
        safe_zone_count: 30,
        grey_zone_count: 8,
        distress_zone_count: 2,
        beneish_flagged_count: 2,
        high_red_flag_count: 3,
        median_distress_prob: 0.015,
        avg_red_flags: 0.4,
        total_debt_sum: 45_000_000_000_000,
      },
      {
        icb_code_level2: "9999",
        icb_name_level2: "Ngành rỗng",
        issuer_count: 0,
        safe_zone_count: 0,
        grey_zone_count: 0,
        distress_zone_count: 0,
        beneish_flagged_count: 0,
        high_red_flag_count: 0,
        median_distress_prob: null,
        avg_red_flags: 0,
        total_debt_sum: 0,
      },
    ];

    const nodes = buildSectorTreemapNodes(sectors, "distress_prob", LIGHT_CHART_PALETTE);
    expect(nodes).toHaveLength(2);
    expect(nodes[0].icbCode).toBe("8600");
    expect(nodes[0].value).toBe(60);
    expect(nodes[0].color).toBe(LIGHT_CHART_PALETTE.riskHigh);
    expect(nodes[1].icbCode).toBe("3500");
    expect(nodes[1].color).toBe(LIGHT_CHART_PALETTE.riskLow);

    const title = buildSectorTreemapTitle(sectors, "distress_prob");
    expect(title).toContain("Bất động sản");
    expect(title).toContain("11,0%");
  });

  it("transforms growth_series chronologically into billions and derives conclusion title", () => {
    const raw: GrowthPoint[] = [
      {
        period_end_date: "2025-12-31",
        fiscal_year: 2025,
        fiscal_quarter: 4,
        IS_NET_REVENUE: 12_500_000_000_000,
        IS_NET_INCOME: 1_250_000_000_000,
        CF_OPERATING_CASH_FLOW: 1_800_000_000_000,
      },
      {
        period_end_date: "2025-09-30",
        fiscal_year: 2025,
        fiscal_quarter: 3,
        IS_NET_REVENUE: 10_000_000_000_000,
        IS_NET_INCOME: 900_000_000_000,
        CF_OPERATING_CASH_FLOW: -200_000_000_000,
      },
    ];

    const normalized = transformGrowthSeries(raw, "QUARTER");
    expect(normalized.map((r) => r.label)).toEqual(["Q3/2025", "Q4/2025"]);
    expect(normalized[0].cfoBil).toBe(-200);
    expect(normalized[1].revenueBil).toBe(12_500);

    const title = buildGrowthChartTitle(normalized, "TTM");
    expect(title).toContain("TTM");
    expect(title).toContain("Q4/2025");
    expect(title).toContain("12.500,0");
    expect(title).toContain("+25,0%");
  });

  it("transforms capital_structure_series chronologically into billions and computes liabilities ratio title", () => {
    const raw: CapitalStructurePoint[] = [
      {
        period_end_date: "2025-12-31",
        fiscal_year: 2025,
        fiscal_quarter: 4,
        BS_TOTAL_ASSETS: 20_000_000_000_000,
        BS_TOTAL_LIABILITIES: 12_000_000_000_000,
        BS_TOTAL_EQUITY: 8_000_000_000_000,
        total_debt: 7_500_000_000_000,
      },
    ];

    const normalized = transformCapitalStructureSeries(raw, "QUARTER");
    expect(normalized).toHaveLength(1);
    expect(normalized[0].assetsBil).toBe(20_000);
    expect(normalized[0].liabilitiesBil).toBe(12_000);
    expect(normalized[0].equityBil).toBe(8_000);
    expect(normalized[0].debtBil).toBe(7_500);

    const title = buildCapitalStructureTitle(normalized);
    expect(title).toContain("Q4/2025");
    expect(title).toContain("20.000,0");
    expect(title).toContain("60,0%");
  });
});

describe("UI-04b: 5-Pillar Profile, Box Plot, Ratio Grouping, Cross-Filtering, FinancialTable", () => {
  const sampleBenchmarkItems: BenchmarkSummaryItem[] = [
    {
      metric_name: "roe_total",
      display_name_vn: "ROE (Lợi nhuận / VCSH)",
      category: "PROFITABILITY",
      unit: "%",
      format_type: "PERCENT",
      direction: "HIGHER_IS_BETTER",
      company_value: 0.18,
      peer_median: 0.12,
      peer_p25: 0.08,
      peer_p75: 0.16,
      percentile_rank: 82,
      robust_z_score: 1.2,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [0.15, 0.18],
    },
    {
      metric_name: "current_ratio",
      display_name_vn: "Thanh toán hiện hành",
      category: "LIQUIDITY",
      unit: "x",
      format_type: "MULTIPLE",
      direction: "HIGHER_IS_BETTER",
      company_value: 1.4,
      peer_median: 1.5,
      peer_p25: 1.1,
      peer_p75: 2.0,
      percentile_rank: 45,
      robust_z_score: -0.2,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [1.3, 1.4],
    },
    {
      metric_name: "debt_to_equity",
      display_name_vn: "Nợ vay / VCSH",
      category: "LEVERAGE",
      unit: "x",
      format_type: "MULTIPLE",
      direction: "LOWER_IS_BETTER",
      company_value: 1.8,
      peer_median: 0.9,
      peer_p25: 0.5,
      peer_p75: 1.4,
      percentile_rank: 90,
      robust_z_score: 1.8,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [1.5, 1.8],
    },
    {
      metric_name: "dpo",
      display_name_vn: "Số ngày phải trả (DPO)",
      category: "EFFICIENCY",
      unit: "ngày",
      format_type: "DAYS",
      direction: "NEUTRAL",
      company_value: 45,
      peer_median: 40,
      peer_p25: 30,
      peer_p75: 55,
      percentile_rank: 60,
      robust_z_score: 0.3,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [42, 45],
    },
    {
      metric_name: "ocf_to_net_income",
      display_name_vn: "CFO / Lợi nhuận sau thuế",
      category: "CASH_FLOW",
      unit: "x",
      format_type: "MULTIPLE",
      direction: "HIGHER_IS_BETTER",
      company_value: 1.25,
      peer_median: 0.85,
      peer_p25: 0.4,
      peer_p75: 1.1,
      percentile_rank: 78,
      robust_z_score: 0.9,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [1.0, 1.25],
    },
    {
      metric_name: "accruals_to_assets",
      display_name_vn: "Dồn tích / Tổng tài sản",
      category: "FORENSIC",
      unit: "%",
      format_type: "PERCENT",
      direction: "LOWER_IS_BETTER",
      company_value: -0.02,
      peer_median: 0.01,
      peer_p25: -0.01,
      peer_p75: 0.04,
      percentile_rank: 20,
      robust_z_score: -0.8,
      peer_group_level: "ICB_LEVEL_3",
      icb_code_used: "1750",
      peer_count: 20,
      sparkline: [-0.01, -0.02],
    },
  ];

  it("computes 5-Pillar Health Profile inverting LOWER_IS_BETTER and ignoring NEUTRAL ratios", () => {
    const pillars = computeFivePillarProfile(sampleBenchmarkItems);
    expect(pillars).toHaveLength(5);

    const byId = new Map(pillars.map((p) => [p.id, p]));
    expect(byId.get("PROFITABILITY")?.score).toBe(82);
    expect(byId.get("PROFITABILITY")?.quintile).toBe(5);
    expect(byId.get("LIQUIDITY")?.score).toBe(45);
    expect(byId.get("LIQUIDITY")?.quintile).toBe(3);
    // debt_to_equity percentile 90 with LOWER_IS_BETTER => health score 10 (Q1)
    expect(byId.get("LEVERAGE")?.score).toBe(10);
    expect(byId.get("LEVERAGE")?.quintile).toBe(1);
    // EFFICIENCY_CF excludes NEUTRAL dpo, uses only ocf_to_net_income (78)
    expect(byId.get("EFFICIENCY_CF")?.score).toBe(78);
    expect(byId.get("EFFICIENCY_CF")?.metricCount).toBe(1);
    // FORENSIC accruals_to_assets percentile 20 with LOWER_IS_BETTER => health score 80 (Q5)
    expect(byId.get("GROWTH_QUALITY")?.score).toBe(80);
    expect(byId.get("GROWTH_QUALITY")?.quintile).toBe(5);
  });

  it("groups ratios into 5 business categories with diacritic-insensitive search and sorting", () => {
    const allBuckets = groupAndSortSummaryRatios(sampleBenchmarkItems, {
      sortBy: "percentile",
      sortDir: "desc",
    });
    expect(allBuckets).toHaveLength(5);

    const searched = groupAndSortSummaryRatios(sampleBenchmarkItems, {
      search: "dong tien",
    });
    expect(searched).toHaveLength(0);

    const searchedLoiNhuan = groupAndSortSummaryRatios(sampleBenchmarkItems, {
      search: "loi nhuan",
    });
    expect(searchedLoiNhuan.map((b) => b.id)).toEqual([
      "PROFITABILITY",
      "EFFICIENCY_CF",
    ]);

    const onlyLeverage = groupAndSortSummaryRatios(sampleBenchmarkItems, {
      groupId: "LEVERAGE",
    });
    expect(onlyLeverage).toHaveLength(1);
    expect(onlyLeverage[0].items[0].metric_name).toBe("debt_to_equity");
  });

  it("computes Tukey box plot statistics, detects outliers, and handles sparse fallback", () => {
    const peers: PeerRankingItem[] = [
      { ticker: "A", value: 0.05 },
      { ticker: "B", value: 0.08 },
      { ticker: "C", value: 0.1 },
      { ticker: "D", value: 0.12 },
      { ticker: "E", value: 0.14 },
      { ticker: "F", value: 0.15 },
      { ticker: "OUT", value: 0.65 },
    ];
    const stats = computeBoxPlotStats(peers);
    expect(stats).not.toBeNull();
    expect(stats?.sampleCount).toBe(7);
    expect(stats?.fiveNumber[2]).toBeCloseTo(0.12, 4);
    expect(stats?.outliers.map((o) => o.ticker)).toEqual(["OUT"]);

    const sparse = computeBoxPlotStats([{ ticker: "A", value: 0.1 }], {
      peerP25: 0.06,
      peerMedian: 0.1,
      peerP75: 0.15,
    });
    expect(sparse?.fiveNumber).toEqual([0.06, 0.06, 0.1, 0.15, 0.15]);
  });

  it("resolves cross-filtered ML score for clicked quarter and supports reset to latest", () => {
    const scores = [
      { period_end_date: "2024-06-30", prob: 0.09 },
      { period_end_date: "2024-09-30", prob: 0.06 },
      { period_end_date: "2024-12-31", prob: 0.03 },
    ];

    const defaultRes = resolveCrossFilteredMlScore(scores, null, null);
    expect(defaultRes.active?.period_end_date).toBe("2024-12-31");
    expect(defaultRes.isCustomSelected).toBe(false);

    const clickedRes = resolveCrossFilteredMlScore(scores, "2024-06-30", "2024-12-31");
    expect(clickedRes.active?.period_end_date).toBe("2024-06-30");
    expect(clickedRes.active?.prob).toBe(0.09);
    expect(clickedRes.isCustomSelected).toBe(true);
    expect(clickedRes.defaultLatest?.period_end_date).toBe("2024-12-31");
  });

  it("maps FinancialTable parent-child hierarchy, clean Vietnamese audit badges, and 12-quarter trend", () => {
    expect(
      getStatementRowHierarchy("BS_TOTAL_ASSETS", "BALANCE_SHEET", true),
    ).toEqual({ depth: 0, parentMetricId: null });
    expect(
      getStatementRowHierarchy("BS_CURRENT_ASSETS", "BALANCE_SHEET", true),
    ).toEqual({ depth: 1, parentMetricId: "BS_TOTAL_ASSETS" });
    expect(
      getStatementRowHierarchy("BS_TRADE_RECEIVABLES_ST", "BALANCE_SHEET", false),
    ).toEqual({ depth: 3, parentMetricId: "BS_SHORT_TERM_RECEIVABLES" });

    const audited = formatAuditStatusBadge("AUDITED");
    const reviewed = formatAuditStatusBadge("REVIEWED");
    const unaudited = formatAuditStatusBadge("UNAUDITED");
    const unknown = formatAuditStatusBadge("UNKNOWN_RAW_FLAG");

    expect(audited).toEqual({ label: "Đã kiểm toán", tone: "low" });
    expect(reviewed).toEqual({ label: "Đã soát xét", tone: "mid" });
    expect(unaudited).toEqual({ label: "Tự lập", tone: "neutral" });
    expect(unknown).toEqual({ label: "Chưa rõ", tone: "neutral" });
    expect(unknown.label).not.toContain("UNKNOWN");

    const periods: StatementPeriodItem[] = Array.from({ length: 14 }, (_, idx) => {
      const year = 2022 + Math.floor(idx / 4);
      const q = (idx % 4) + 1;
      const month = String(q * 3).padStart(2, "0");
      return {
        period_end_date: `${year}-${month}-30`,
        fiscal_year: year,
        fiscal_quarter: q,
        label: `Q${q}/${year}`,
        taxonomy_version: "VAS_TT200_2014",
        audit_status: q === 4 ? "AUDITED" : "UNAUDITED",
      };
    }).reverse();

    const row: StatementRowItem = {
      metric_id: "IS_NET_REVENUE",
      display_name_vn: "Doanh thu thuần",
      tt200_code: "10",
      tt99_code: "10",
      unit: "VND",
      is_total: true,
      comparable_across_taxonomy: true,
      sort_order: 1000,
      values: Object.fromEntries(
        periods.map((p, i) => [p.period_end_date, (14 - i) * 1_000_000_000_000]),
      ),
    };

    const trend = buildStatementLineTrend(row, periods, "QUARTER", 12);
    expect(trend).toHaveLength(12);
    expect(trend[0].periodEndDate < trend[11].periodEndDate).toBe(true);
    expect(trend[11].chartValue).toBe(14_000);
  });
});
