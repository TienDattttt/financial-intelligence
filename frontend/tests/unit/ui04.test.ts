import { describe, expect, it } from "vitest";
import {
  buildCapitalStructureTitle,
  transformCapitalStructureSeries,
} from "@/components/charts/CapitalStructureChart";
import { buildSafeExportFilename } from "@/components/charts/EChartBox";
import {
  buildGrowthChartTitle,
  transformGrowthSeries,
} from "@/components/charts/GrowthComboChart";
import {
  buildSectorTreemapNodes,
  buildSectorTreemapTitle,
} from "@/components/charts/SectorTreemapChart";
import { LIGHT_CHART_PALETTE } from "@/lib/echarts-theme";
import type {
  CapitalStructurePoint,
  GrowthPoint,
  SectorRiskItem,
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
