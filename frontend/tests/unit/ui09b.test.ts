import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildPriceOverlayOption,
  buildPriceOverlayTableRows,
  extractWarningDrawdownSummary,
  formatLatestValuationCards,
  formatPctFromPercentValue,
  PRICE_OVERLAY_CHART_SUBTITLE,
} from "@/components/charts/PriceRiskOverlayChart";
import { DARK_CHART_PALETTE, LIGHT_CHART_PALETTE } from "@/lib/echarts-theme";
import type {
  CompanyMarketOverlayResponse,
  DrawdownWindowMetrics,
  PriceOverlayPoint,
} from "@/lib/market-overlay-types";

function makeWindow(
  start: string,
  end: string,
  returnPct: number,
  drawdownPct: number,
): DrawdownWindowMetrics {
  return {
    start_trade_date: start,
    end_trade_date: end,
    target_end_date: end,
    session_count: 60,
    base_close_adj: 15_000,
    peak_close_adj: 15_200,
    peak_date: start,
    trough_close_adj: 7_500,
    trough_date: end,
    end_close_adj: 15_000 * (1 + returnPct / 100),
    return_pct: returnPct,
    drawdown_from_marker_pct: drawdownPct,
    max_peak_to_trough_drawdown_pct: drawdownPct,
  };
}

describe("UI-09b PIT Market Valuation & Price Risk Overlay Chart", () => {
  it("formats valid PIT Market Cap, P/E, and P/B and explains negative/missing flags cleanly in Vietnamese", () => {
    const validPoint: PriceOverlayPoint = {
      trade_date: "2026-11-20",
      close_adj: 45_000,
      close_raw: 45_000,
      volume: 1_200_000,
      shares_outstanding_market: 1_885_727_648,
      running_drawdown_pct: -8.5,
      drawdown_from_first_warning_pct: null,
      market_cap_vnd: 84_857_744_160_000,
      pe_ttm: 15.42,
      pb_ratio: 3.18,
      pe_flag: "VALID",
      pb_flag: "VALID",
      shares_source: "SHARES_OUTSTANDING_MARKET",
    };

    const validCards = formatLatestValuationCards(validPoint);
    expect(validCards.tradeDateLabel).toBe("20/11/2026");
    expect(validCards.marketCapLabel).toBe("84.857,7 tỷ");
    expect(validCards.peLabel).toBe("15,42x");
    expect(validCards.pbLabel).toBe("3,18x");
    expect(validCards.peValid).toBe(true);
    expect(validCards.pbValid).toBe(true);
    expect(validCards.sharesSourceLabel).not.toContain("SHARES_OUTSTANDING_MARKET");

    const negativePoint: PriceOverlayPoint = {
      ...validPoint,
      pe_ttm: null,
      pb_ratio: null,
      pe_flag: "NEGATIVE_EARNINGS",
      pb_flag: "NEGATIVE_EQUITY",
      shares_source: "BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO",
    };
    const negCards = formatLatestValuationCards(negativePoint);
    expect(negCards.peLabel).toBe("N/A");
    expect(negCards.pbLabel).toBe("N/A");
    expect(negCards.peValid).toBe(false);
    expect(negCards.pbValid).toBe(false);
    expect(negCards.peSubtext).not.toContain("NEGATIVE_EARNINGS");
    expect(negCards.pbSubtext).not.toContain("NEGATIVE_EQUITY");
    expect(negCards.marketCapSubtext).not.toContain("BS_ORDINARY_SHARES");

    const emptyCards = formatLatestValuationCards(null);
    expect(emptyCards.marketCapLabel).toBe("N/A");
    expect(emptyCards.peLabel).toBe("N/A");
    expect(emptyCards.pbLabel).toBe("N/A");
  });

  it("handles empty price_series gracefully without crashing", () => {
    const emptyPayload: CompanyMarketOverlayResponse = {
      snapshot_id: "snap_01",
      issuer_id: "iss_01",
      ticker: "XYZ",
      as_of: "2026-11-20",
      latest_valuation: null,
      price_series: [],
      model_markers: [],
      regulatory_markers: [],
      earliest_warning_summary: null,
    };

    expect(buildPriceOverlayOption(emptyPayload, LIGHT_CHART_PALETTE)).toEqual({});
    expect(buildPriceOverlayTableRows(emptyPayload)).toEqual([]);
    expect(extractWarningDrawdownSummary(emptyPayload)).toBeNull();
  });

  it("builds dual-marker ECharts overlay with close_adj line, dataZoom, and Light/Dark theme support", () => {
    const payload: CompanyMarketOverlayResponse = {
      snapshot_id: "snap_2026",
      issuer_id: "iss_hbc",
      ticker: "HBC",
      as_of: "2026-11-20",
      latest_valuation: {
        trade_date: "2026-11-20",
        close_adj: 2_800,
        close_raw: 2_800,
        volume: 350_000,
        shares_outstanding_market: 274_133_287,
        running_drawdown_pct: -65.9,
        drawdown_from_first_warning_pct: -65.9,
        market_cap_vnd: 767_573_203_600,
        pe_ttm: null,
        pb_ratio: 1.45,
        pe_flag: "NEGATIVE_EARNINGS",
        pb_flag: "VALID",
        shares_source: "SHARES_OUTSTANDING_MARKET",
      },
      price_series: [
        {
          trade_date: "2022-05-04",
          close_adj: 15_000,
          close_raw: 15_000,
          volume: 900_000,
          shares_outstanding_market: 274_133_287,
          running_drawdown_pct: 0,
          drawdown_from_first_warning_pct: 0,
          market_cap_vnd: 4_111_999_305_000,
          pe_ttm: 12.1,
          pb_ratio: 1.2,
          pe_flag: "VALID",
          pb_flag: "VALID",
          shares_source: "SHARES_OUTSTANDING_MARKET",
        },
        {
          trade_date: "2023-04-11",
          close_adj: 8_210,
          close_raw: 8_210,
          volume: 600_000,
          shares_outstanding_market: 274_133_287,
          running_drawdown_pct: -45.3,
          drawdown_from_first_warning_pct: -45.3,
          market_cap_vnd: 2_250_634_286_270,
          pe_ttm: null,
          pb_ratio: 0.9,
          pe_flag: "NEGATIVE_EARNINGS",
          pb_flag: "VALID",
          shares_source: "SHARES_OUTSTANDING_MARKET",
        },
        {
          trade_date: "2026-11-20",
          close_adj: 2_800,
          close_raw: 2_800,
          volume: 350_000,
          shares_outstanding_market: 274_133_287,
          running_drawdown_pct: -81.3,
          drawdown_from_first_warning_pct: -81.3,
          market_cap_vnd: 767_573_203_600,
          pe_ttm: null,
          pb_ratio: 1.45,
          pe_flag: "NEGATIVE_EARNINGS",
          pb_flag: "VALID",
          shares_source: "SHARES_OUTSTANDING_MARKET",
        },
      ],
      model_markers: [
        {
          marker_type: "MODEL_RISK_TIER",
          marker_kind: "FIRST_R4_UPGRADE",
          risk_tier: "R4",
          is_first_r4_or_r5: true,
          disclosure_date: "2022-05-02",
          date_basis: "PIT_DISCLOSURE",
          aligned_trade_date: "2022-05-04",
          period_end_date: "2022-03-31",
          close_adj_at_marker: 15_000,
          close_raw_at_marker: 15_000,
          calibrated_distress_prob: 0.125,
          risk_market_percentile: 94.2,
          relative_risk_lift: 4.5,
          drawdowns: {
            horizons: {
              "T+90d": makeWindow("2022-05-04", "2022-08-02", -24.5, -28.0),
              "T+365d": makeWindow("2022-05-04", "2023-05-04", -48.2, -52.0),
            },
            to_first_regulatory_event: makeWindow("2022-05-04", "2023-04-11", -45.3, -49.0),
            to_current: makeWindow("2022-05-04", "2026-11-20", -81.3, -84.0),
          },
        },
      ],
      regulatory_markers: [
        {
          marker_type: "REGULATORY_EVENT",
          event_type: "CONTROL",
          event_stage: "FIRST_ENTRY",
          event_date: "2023-04-11",
          effective_from: "2023-04-11",
          resolution_date: null,
          aligned_trade_date: "2023-04-11",
          close_adj_at_marker: 8_210,
          close_raw_at_marker: 8_210,
          raw_headline: "Chuyển sang diện kiểm soát",
          source_provider: "HOSE",
          source_url: "https://example.invalid/hose/hbc",
          drawdowns: null,
        },
      ],
      earliest_warning_summary: {
        warning_source: "MODEL_RISK_TIER",
        warning_label: "Model R4",
        warning_date: "2022-05-02",
        aligned_trade_date: "2022-05-04",
        base_close_adj: 15_000,
        latest_trade_date: "2026-11-20",
        latest_close_adj: 2_800,
        horizons: {
          "T+90d": makeWindow("2022-05-04", "2022-08-02", -24.5, -28.0),
          "T+365d": makeWindow("2022-05-04", "2023-05-04", -48.2, -52.0),
        },
        to_first_regulatory_event: makeWindow("2022-05-04", "2023-04-11", -45.3, -49.0),
        to_current: makeWindow("2022-05-04", "2026-11-20", -81.3, -84.0),
      },
    };

    const lightOpt = buildPriceOverlayOption(payload, LIGHT_CHART_PALETTE);
    const darkOpt = buildPriceOverlayOption(payload, DARK_CHART_PALETTE);
    expect(Array.isArray(lightOpt.series)).toBe(true);
    expect((lightOpt.series as unknown[]).length).toBe(3);
    expect(Array.isArray(lightOpt.dataZoom)).toBe(true);
    expect(JSON.stringify(lightOpt)).not.toContain("#6B4C9A");
    expect(JSON.stringify(darkOpt)).not.toContain("#6B4C9A");

    const summaryView = extractWarningDrawdownSummary(payload);
    expect(summaryView).not.toBeNull();
    expect(summaryView?.sourceTitle).toBe("Mô hình nâng lên R4");
    expect(summaryView?.windows.map((w) => w.returnText)).toEqual([
      "−24,5%",
      "−48,2%",
      "−45,3%",
      "−81,3%",
    ]);
    expect(formatPctFromPercentValue(-65.9)).toBe("−65,9%");

    const rows = buildPriceOverlayTableRows(payload);
    expect(rows).toHaveLength(3);
    expect(rows[0][0]).toBe("20/11/2026");
    expect(rows[0][4]).toBe("N/A");
  });

  it("uses clean Vietnamese terminology without raw close_adj column name in chart subtitle", () => {
    expect(PRICE_OVERLAY_CHART_SUBTITLE).not.toContain("close_adj");
    expect(PRICE_OVERLAY_CHART_SUBTITLE).toContain("Đường giá điều chỉnh chia tách/cổ tức");

    const chartSource = fs.readFileSync(
      path.resolve(__dirname, "../../src/components/charts/PriceRiskOverlayChart.tsx"),
      "utf8",
    );
    expect(chartSource).not.toContain("Đường giá từ close_adj");
  });
});
