// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BondMaturityCard } from "@/components/company/BondMaturityCard";
import { StressControlsDrawer } from "@/components/stress-test/StressControlsDrawer";
import { StressIssuerDetailTable } from "@/components/stress-test/StressIssuerDetailTable";
import {
  DEFAULT_SHOCKS,
  parseStressUrlParams,
  serializeStressUrlParams,
  SHOCK_BOUNDS,
} from "@/lib/stress-test-helpers";
import type {
  CompanyBondSignalsResponse,
  StressIssuerItem,
  StressTestResponse,
} from "@/types/api";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(() => {
  cleanup();
});

function makeBondSignal(
  overrides: Partial<CompanyBondSignalsResponse> = {},
): CompanyBondSignalsResponse {
  return {
    snapshot_id: "snap_v3_fx_bonds",
    issuer_id: "VN_NVL",
    ticker: "NVL",
    has_bond_data: true,
    has_outstanding_bonds: true,
    as_of_date: "2026-03-15",
    outstanding_bond_vnd: 12_000_000_000_000,
    bond_maturity_12m_vnd: 4_500_000_000_000,
    total_cash_and_st_investments_vnd: 2_000_000_000_000,
    cash_period_end_date: "2025-12-31",
    bond_maturity_to_cash_ratio: 2.25,
    bond_payment_delay_flag: true,
    liquidity_pressure_status: "PAYMENT_DELAY",
    source_provider: "hnx_cbonds",
    source_url: "https://cbonds.hnx.vn/api/v1/issuers/NVL/bond-signals",
    payload_sha256: "a".repeat(64),
    ...overrides,
  };
}

function makeIssuer(
  ticker: string,
  overrides: Partial<StressIssuerItem> = {},
): StressIssuerItem {
  return {
    issuer_id: `VN_${ticker}`,
    ticker,
    company_name_vn: `Công ty ${ticker}`,
    icb_code_level2: "8600",
    icb_name_level2: "Bất động sản",
    total_debt: 3_000_000_000_000,
    interest_coverage: 1.8,
    stressed_interest_coverage: 0.9,
    ebit_ttm: 180_000_000_000,
    stressed_ebit_ttm: 180_000_000_000,
    cfo_ttm: 120_000_000_000,
    stressed_cfo_ttm: 40_000_000_000,
    baseline_debt_at_risk: false,
    stressed_debt_at_risk: true,
    newly_distressed_under_shock: true,
    net_fx_debt_vnd: 1_000_000_000_000,
    fx_revaluation_loss_vnd: 100_000_000_000,
    fx_coverage_status: "REPORTED",
    ...overrides,
  };
}

describe("V3-33 FX Stress Test UI & BondMaturityCard", () => {
  it("round-trips and clamps fx depreciation parameter in stress-test URL helpers", () => {
    expect(SHOCK_BOUNDS.fxPct).toEqual({
      min: -30,
      max: 50,
      step: 1,
      defaultValue: 0,
    });

    const qs = serializeStressUrlParams({
      scope: "COMPANY",
      companyTicker: "NVL",
      watchlistTickers: [],
      shocks: { ...DEFAULT_SHOCKS, scenarioName: "Cú sốc FX +10%", fxPct: 10 },
    });
    expect(qs.get("fx")).toBe("10");

    const parsed = parseStressUrlParams(qs, DEFAULT_SHOCKS);
    expect(parsed.hasStressParams).toBe(true);
    expect(parsed.state.shocks.fxPct).toBe(10);

    const clamped = parseStressUrlParams(
      new URLSearchParams("scope=MARKET&fx=999"),
      DEFAULT_SHOCKS,
    );
    expect(clamped.state.shocks.fxPct).toBe(50);

    const negClamped = parseStressUrlParams(
      new URLSearchParams("scope=MARKET&fx=-99"),
      DEFAULT_SHOCKS,
    );
    expect(negClamped.state.shocks.fxPct).toBe(-30);
  });

  it("renders FX shock slider and honest disclosure note in StressControlsDrawer", () => {
    const setShocks = vi.fn();
    render(
      <StressControlsDrawer
        mobileControlsOpen={false}
        setMobileControlsOpen={vi.fn()}
        shocks={{ ...DEFAULT_SHOCKS, fxPct: 10 }}
        setShocks={setShocks}
        isExtreme={false}
        activeResult={null}
        activeIsCalculating={false}
        pinnedA={null}
        setPinnedA={vi.fn()}
      />,
    );

    const fxInput = screen.getByLabelText("Mất giá tỷ giá VND (USD/VND)") as HTMLInputElement;
    expect(fxInput.value).toBe("10");
    expect(
      screen.getByText(/chỉ áp dụng cho doanh nghiệp có công bố nợ ngoại tệ/i),
    ).toBeTruthy();

    fireEvent.change(fxInput, { target: { value: "15" } });
    expect(setShocks).toHaveBeenCalledTimes(1);
  });

  it("renders FX revaluation impact for REPORTED issuers and Chưa công bố for NOT_DISCLOSED issuers", () => {
    const reported = makeIssuer("NVL");
    const undisclosed = makeIssuer("FPT", {
      net_fx_debt_vnd: null,
      fx_revaluation_loss_vnd: null,
      fx_coverage_status: "NOT_DISCLOSED",
    });
    const res: StressTestResponse = {
      snapshot_id: "snap_1",
      scenario_name: "FX +10%",
      statement_scope: "CONSOLIDATED",
      period_type: "QUARTER",
      period_end_date: "2026-03-31",
      fx_depreciation_pct: 0.1,
      fx_disclosed_issuers_count: 1,
      total_issuers_evaluated: 2,
      total_debt_sum: 4_000_000_000_000,
      baseline_debt_at_risk_pct: 0,
      stressed_debt_at_risk_pct: 0.75,
      newly_distressed_count: 1,
      sector_summary: [],
      newly_distressed_issuers: [reported, undisclosed],
      top_stressed_issuers: [reported, undisclosed],
    };

    render(
      <StressIssuerDetailTable
        activeResult={res}
        activeTab="NEWLY"
        setActiveTab={vi.fn()}
        issuerList={[reported, undisclosed]}
        selectedSectorName={null}
        onClearSector={vi.fn()}
        onExportIssuersCsv={vi.fn()}
      />,
    );

    expect(screen.getByText(/100,0/)).toBeTruthy();
    expect(screen.getByText("Chưa công bố")).toBeTruthy();
    expect(document.body.textContent).not.toContain("NaN");
  });

  it("renders BondMaturityCard with vi-VN numbers, delay badge, source URL, and clean empty state", () => {
    const { rerender } = render(
      <BondMaturityCard
        data={makeBondSignal()}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("Áp lực đáo hạn trái phiếu 12 tháng")).toBeTruthy();
    expect(screen.getByText(/12\.000,0/)).toBeTruthy();
    expect(screen.getByText(/4\.500,0/)).toBeTruthy();
    expect(screen.getByText(/^2\.000,0/)).toBeTruthy();
    expect(screen.getByText("2,25x")).toBeTruthy();
    expect(screen.getByText("Chậm trả gốc/lãi")).toBeTruthy();
    const link = screen.getByRole("link", { name: /HNX Cbonds/i });
    expect(link.getAttribute("href")).toBe(
      "https://cbonds.hnx.vn/api/v1/issuers/NVL/bond-signals",
    );

    rerender(
      <BondMaturityCard
        data={makeBondSignal({
          has_outstanding_bonds: false,
          outstanding_bond_vnd: 0,
          bond_maturity_12m_vnd: 0,
          bond_maturity_to_cash_ratio: null,
          bond_payment_delay_flag: false,
          liquidity_pressure_status: "NO_BONDS",
        })}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText("Không có dư nợ trái phiếu lưu hành")).toBeTruthy();

    rerender(
      <BondMaturityCard
        data={undefined}
        isLoading={false}
        isError={true}
        errorMessage="Không thể tải tín hiệu trái phiếu."
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("keeps BondMaturityCard and stress-test UI free of banned DESIGN.md colors and backend tokens", () => {
    const root = path.resolve(__dirname, "../..");
    const cardSrc = fs.readFileSync(
      path.join(root, "src/components/company/BondMaturityCard.tsx"),
      "utf8",
    );
    expect(cardSrc).not.toMatch(/#6B4C9A|purple|cyan|backdrop-blur|AI-Powered/i);
    expect(cardSrc).not.toContain("BS_TOTAL_ASSETS");
    expect(cardSrc).not.toContain("REGIME_FALLBACK");
    expect(cardSrc).not.toContain(".parquet");
    expect(cardSrc).not.toContain("issuer_id");
  });
});
