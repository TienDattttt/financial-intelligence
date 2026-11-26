import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildPriceOverlayTableRows,
  formatLatestValuationCards,
} from "@/components/charts/PriceRiskOverlayChart";
import { formatRedFlagEvidence } from "@/components/financial/RedFlagChecklist";
import { getRedFlagPresetQuestion } from "@/components/rag/DocumentInvestigationPanel";
import {
  apiClient,
  ApiError,
  resetObservedSnapshotRegistryForTests,
  subscribeObservedSnapshotId,
} from "@/lib/api-client";
import {
  formatRedFlagCodeLabel,
  getDtdFlagMeta,
  getMlFeatureLabel,
  QUALITY_RULE_LABELS,
} from "@/lib/domain-labels";
import type { CompanyMarketOverlayResponse, PriceOverlayPoint } from "@/lib/market-overlay-types";

describe("TASK-10: X-Snapshot-ID atomic sync, Merton DTD card, and RF_10 forensic labels", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    resetObservedSnapshotRegistryForTests();
  });

  it("coalesces rapid X-Snapshot-ID response headers over a 150ms debounce window", async () => {
    vi.useFakeTimers();
    const observed: string[] = [];
    const unsubscribe = subscribeObservedSnapshotId((snapId) => {
      observed.push(snapId);
    }, 150);

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const snapHeader = url.includes("issuers") ? "snap_2026_002" : "snap_2026_001";
      return new Response(
        JSON.stringify(
          url.includes("health")
            ? { status: "ok", active_snapshot_id: snapHeader, storage_backend: "ducklake" }
            : { snapshot_id: snapHeader, total: 0, limit: 10, offset: 0, items: [] },
        ),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "X-Snapshot-ID": snapHeader,
          },
        },
      );
    });

    await apiClient.getHealth();
    await apiClient.getIssuersPage(10, 0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(observed).toEqual([]);

    vi.advanceTimersByTime(149);
    expect(observed).toEqual([]);

    vi.advanceTimersByTime(2);
    expect(observed).toEqual(["snap_2026_002"]);

    unsubscribe();
  });

  it("sanitizes leaked schema tokens and database DSNs from API error responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: "Select failed on ducklake_catalog for BS_TOTAL_ASSETS / RF_10_POST_AUDIT_PROFIT_DOWNGRADE",
        }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(apiClient.getSnapshotOverview()).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(ApiError);
      const msg = (err as ApiError).message;
      expect(msg).not.toContain("BS_TOTAL_ASSETS");
      expect(msg).not.toContain("RF_10");
      expect(msg).not.toContain("ducklake_catalog");
      return true;
    });
  });

  it("formats Merton DTD card, implied EDF, volatility, and non-debt/insufficient-session flags in Vietnamese", () => {
    const basePoint: PriceOverlayPoint = {
      trade_date: "2026-11-20",
      close_adj: 88_000,
      close_raw: 88_000,
      volume: 2_400_000,
      shares_outstanding_market: 1_460_000_000,
      running_drawdown_pct: -4.2,
      drawdown_from_first_warning_pct: null,
      market_cap_vnd: 128_480_000_000_000,
      pe_ttm: 18.4,
      pb_ratio: 4.1,
      pe_flag: "VALID",
      pb_flag: "VALID",
      shares_source: "SHARES_OUTSTANDING_MARKET",
      merton_dtd: 3.64,
      equity_volatility_1y: 0.275,
      naive_edf: 0.00014,
      dtd_flag: "VALID",
    };

    const validCards = formatLatestValuationCards(basePoint);
    expect(validCards.dtdValid).toBe(true);
    expect(validCards.dtdLabel).toBe("3,64 σ");
    expect(validCards.dtdSubtext).toContain("EDF");
    expect(validCards.dtdSubtext).toContain("27,5%");

    const debtFreeCards = formatLatestValuationCards({
      ...basePoint,
      merton_dtd: null,
      naive_edf: null,
      dtd_flag: "ZERO_FINANCIAL_DEBT",
    });
    expect(debtFreeCards.dtdValid).toBe(false);
    expect(debtFreeCards.dtdLabel).toBe("Không nợ vay");
    expect(debtFreeCards.dtdSubtext).not.toContain("ZERO_FINANCIAL_DEBT");
    expect(debtFreeCards.dtdSubtext).toContain("nợ vay tài chính");

    const thinCards = formatLatestValuationCards({
      ...basePoint,
      merton_dtd: null,
      equity_volatility_1y: null,
      naive_edf: null,
      dtd_flag: "INSUFFICIENT_TRADING_SESSIONS",
    });
    expect(thinCards.dtdLabel).toBe("N/A");
    expect(thinCards.dtdSubtext).not.toContain("INSUFFICIENT_TRADING_SESSIONS");
    expect(getDtdFlagMeta("STALE_BCTC_DEBT").explanation).toContain("180 ngày");

    const overlay: CompanyMarketOverlayResponse = {
      snapshot_id: "snap_01",
      issuer_id: "ISS_FPT",
      ticker: "FPT",
      as_of: "2026-11-20",
      latest_valuation: basePoint,
      price_series: [basePoint],
      model_markers: [],
      regulatory_markers: [],
      earliest_warning_summary: null,
    };
    const rows = buildPriceOverlayTableRows(overlay);
    expect(rows[0]).toContain("3,64 σ");
  });

  it("maps RF_10 evidence, preset PDF investigation query, ML Merton features, and provider reconciliation labels", () => {
    expect(formatRedFlagCodeLabel("RF_10_POST_AUDIT_PROFIT_DOWNGRADE")).toBe("Quy tắc 10");
    expect(getRedFlagPresetQuestion("RF_10_POST_AUDIT_PROFIT_DOWNGRADE")).toContain("kiểm toán");

    const evidence = formatRedFlagEvidence(
      JSON.stringify({
        ni_unaudited_4q_sum: 82_500_000_000,
        ni_audited_annual: 64_000_000_000,
        audit_earnings_diff_vnd: -18_500_000_000,
        audit_adjustment_pct: -0.2242,
        source_filing_ids: ["FIL_1", "FIL_2"],
      }),
    );
    expect(evidence).toEqual([
      ["Tổng LNST 4 quý tự lập", "82,5 tỷ"],
      ["LNST năm sau kiểm toán", "64,0 tỷ"],
      ["Chênh lệch LNST hậu kiểm toán", "(18,5) tỷ"],
      ["Tỷ lệ điều chỉnh hậu kiểm toán", "−22,4%"],
    ]);

    expect(getMlFeatureLabel("merton_dtd")).toContain("Merton");
    expect(getMlFeatureLabel("equity_volatility_1y")).toContain("1 năm");
    expect(getMlFeatureLabel("naive_edf")).toContain("EDF");
    expect(QUALITY_RULE_LABELS.CROSS_PROVIDER_DISCREPANCY_WARN).toContain("hai nguồn");
  });
});
