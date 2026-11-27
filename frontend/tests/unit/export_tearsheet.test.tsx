// @vitest-environment jsdom
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CompanyTearSheetPage from "@/app/companies/[ticker]/tear-sheet/page";
import { CompanyHeader } from "@/components/company/CompanyHeader";
import {
  ExportActionMenu,
  buildCompanyXlsxExportUrl,
  buildScreenerXlsxExportUrl,
} from "@/components/export/ExportActionMenu";
import {
  COMPLIANCE_DISCLAIMER_VI,
  RISK_TIER_LEGAL_LABEL_VI,
} from "@/lib/compliance";

const FORBIDDEN_ND88_RE =
  /xếp\s+hạng\s+tín\s+nhiệm|(?:xếp\s+)?bậc\s+tín\s+nhiệm|phân\s+tầng\s+tín\s+nhiệm|\btín\s+nhiệm\b|khuyến\s+nghị\s+(?:mua|bán)(?:\s*\/\s*(?:mua|bán))?/i;

const LEAKED_BACKEND_TOKEN_RE =
  /\b(?:REGIME_FALLBACK|snapshot_id|issuer_id|BS_TOTAL_ASSETS|IS_NET_REVENUE)\b|\bRF_(?:0[1-9]|10)\b|\.parquet\b/i;

vi.mock("next/navigation", () => ({
  useParams: () => ({ ticker: "HBC" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
}));

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

vi.mock("@/components/providers/app-providers", () => ({
  useAppState: () => ({
    statementScope: "CONSOLIDATED",
    periodType: "QUARTER",
    periodEndDate: "2025-12-31",
    queryPeriodEndDate: "2025-12-31",
    snapshotId: "snap_v3_tear_001",
    cacheSnapshotKey: "snap_v3_tear_001",
    publishedAt: "2026-04-25T08:00:00Z",
  }),
}));

vi.mock("@/hooks/use-finintel-queries", () => ({
  useCompanyProfile: () => ({
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    data: {
      snapshot_id: "snap_v3_tear_001",
      ticker: "HBC",
      issuer_id: "ISS_HBC",
      company_name_vn: "Công ty Cổ phần Tập đoàn Xây dựng Hòa Bình",
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
      ratios_table: [],
      red_flags: [
        {
          period_end_date: "2025-12-31",
          flag_code: "RF_03_OTHER_RECEIVABLES_ANOMALY",
          severity: "HIGH",
          flag_title_vn: "Phải thu khác tăng đột biến so với tổng tài sản",
          explanation_text_vn: "Tỷ trọng phải thu khác vượt ngưỡng an toàn trong kỳ.",
          filing_id: "FIL_HBC_2025Q4",
        },
      ],
      ml_scores: [
        {
          issuer_id: "ISS_HBC",
          statement_scope: "CONSOLIDATED",
          period_type: "QUARTER",
          period_end_date: "2025-12-31",
          filing_id: "FIL_HBC_2025Q4",
          altman_z_double_prime: 0.85,
          altman_zone: "DISTRESS_ZONE",
          piotroski_f_score: 3,
          beneish_m8_score: -1.42,
          beneish_flag: "HIGH_MANIPULATION_RISK_SCREEN",
          calibrated_distress_prob: 0.245,
          risk_tier: "R4",
          risk_market_percentile: 91.5,
          market_base_rate: 0.045,
          relative_risk_lift: 5.4,
          shap_top_drivers: {
            base_value: -2.5,
            prediction_score: -1.1,
            other_features_delta: 0.1,
            top_risk_increasing: [
              {
                feature: "debt_to_equity",
                display_name_vn: "Nợ vay / VCSH",
                value: 2.8,
                peer_median: 0.9,
                shap_delta: 0.65,
              },
            ],
            top_risk_decreasing: [
              {
                feature: "gross_margin",
                display_name_vn: "Biên lợi nhuận gộp",
                value: 0.14,
                peer_median: 0.11,
                shap_delta: -0.18,
              },
            ],
          },
        },
      ],
    },
  }),
  useCompanyBenchmarksSummary: () => ({
    isLoading: false,
    isError: false,
    data: {
      snapshot_id: "snap_v3_tear_001",
      ticker: "HBC",
      items: [
        {
          metric_name: "roe_total",
          display_name_vn: "ROE Tổng",
          category: "PROFITABILITY",
          unit: "%",
          format_type: "PERCENT",
          direction: "HIGHER_IS_BETTER",
          company_value: -0.085,
          peer_median: 0.124,
          peer_p25: 0.052,
          peer_p75: 0.181,
          percentile_rank: 15,
          peer_group_level: "ICB_LEVEL_3",
          peer_count: 18,
        },
      ],
    },
  }),
  useCompanyFilings: () => ({
    isLoading: false,
    data: {
      items: [
        {
          filing_id: "FIL_HBC_2025Q4",
          audit_status: "AUDITED",
          audit_opinion: "QUALIFIED",
        },
      ],
    },
  }),
  useCompanyMarketOverlay: () => ({
    isLoading: false,
    data: {
      latest_valuation: {
        trade_date: "2026-04-24",
        close_raw: 7200,
        close_adj: 7200,
        volume: 1500000,
        shares_outstanding_market: 347000000,
        running_drawdown_pct: -25,
        drawdown_from_first_warning_pct: -22,
        market_cap_vnd: 2498400000000,
        pe_ttm: null,
        pb_ratio: 1.35,
        pe_flag: "NEGATIVE_EARNINGS",
        pb_flag: "VALID",
        shares_source: "MARKET",
        merton_dtd: 1.15,
        equity_volatility_1y: 0.48,
        naive_edf: 0.125,
        dtd_flag: "VALID",
      },
    },
  }),
  useAllIssuers: () => ({
    isLoading: false,
    data: [
      {
        issuer_id: "ISS_HBC",
        ticker: "HBC",
        exchange: "HOSE",
        icb_name_level2: "Xây dựng và Vật liệu",
        icb_name_level3: "Xây dựng",
      },
    ],
  }),
  useMetaDictionary: () => ({
    isLoading: false,
    data: {
      ratios: [
        {
          ratio_name: "debt_to_equity",
          format_type: "MULTIPLE",
        },
        {
          ratio_name: "gross_margin",
          format_type: "PERCENT",
        },
      ],
      red_flags: [],
    },
  }),
}));

describe("V3-15 ExportActionMenu & Institutional A4 Tear-Sheet", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("builds typed company and screener .xlsx export URLs without pagination caps", () => {
    expect(
      buildCompanyXlsxExportUrl("hbc", {
        statementScope: "CONSOLIDATED",
        periodType: "QUARTER",
        periodsLimit: 20,
      }),
    ).toBe(
      "/api/v3/exports/companies/HBC.xlsx?statement_scope=CONSOLIDATED&period_type=QUARTER&periods_limit=20",
    );

    const scrUrl = buildScreenerXlsxExportUrl({
      statement_scope: "CONSOLIDATED",
      period_type: "QUARTER",
      exchange: "HOSE",
      limit: 50,
      offset: 100,
    });
    const parsed = new URL(scrUrl, "https://finintel.local");
    expect(parsed.pathname).toBe("/api/v3/exports/screener.xlsx");
    expect(parsed.searchParams.get("exchange")).toBe("HOSE");
    expect(parsed.searchParams.has("limit")).toBe(false);
    expect(parsed.searchParams.has("offset")).toBe(false);
  });

  it("renders ExportActionMenu in CompanyHeader and triggers .xlsx download cleanly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([80, 75, 3, 4]), {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="finintel-HBC-snap01.xlsx"',
          "X-Snapshot-ID": "snap_v3_tear_001",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const createObjectUrlMock = vi.fn(() => "blob:mock-xlsx");
    const revokeObjectUrlMock = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectUrlMock,
      revokeObjectURL: revokeObjectUrlMock,
    });

    render(
      <CompanyHeader
        ticker="HBC"
        companyName="Tập đoàn Xây dựng Hòa Bình"
        exchange="HOSE"
        sectorLevel2="Xây dựng"
        riskTier="R4"
        statementScope="CONSOLIDATED"
        periodType="QUARTER"
        activePeriodEnd="2025-12-31"
        isWatched={false}
        onToggleWatchlist={vi.fn()}
        activeTab="overview"
        onChangeTab={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: /Xuất báo cáo/i });
    fireEvent.click(trigger);

    const tearSheetLink = screen.getByRole("menuitem", {
      name: /Mở Tear-Sheet \(A4 \/ PDF\)/i,
    });
    expect(tearSheetLink.getAttribute("href")).toBe("/companies/HBC/tear-sheet");

    const xlsxBtn = screen.getByRole("menuitem", { name: /Tải Excel \(\.xlsx\)/i });
    fireEvent.click(xlsxBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/api/v3/exports/companies/HBC.xlsx",
    );
  });

  it("supports CSV callback and error sanitization inside ExportActionMenu", async () => {
    const onDownloadCsv = vi.fn();
    const fetchMock = vi.fn().mockRejectedValue(new Error("Network error"));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ExportActionMenu
        xlsxUrl="/api/v3/exports/screener.xlsx"
        xlsxFallbackFilename="finintel-screener.xlsx"
        onDownloadCsv={onDownloadCsv}
        label="Xuất Excel"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Xuất Excel/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Tải CSV \(\.csv\)/i }));
    expect(onDownloadCsv).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /Xuất Excel/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Tải Excel \(\.xlsx\)/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Không thể kết nối máy chủ");
  });

  it("renders all 6 quantitative blocks, compliance disclaimer, and zero forbidden wording on Tear-Sheet", () => {
    const { container } = render(<CompanyTearSheetPage />);
    const text = container.textContent ?? "";

    // Block 1: Risk conclusion R1-R5 + legal label
    expect(text).toContain(RISK_TIER_LEGAL_LABEL_VI);
    expect(text).toContain("R4");
    expect(text).toContain("24,5%");
    expect(text).toContain("0,85");
    expect(text).toContain("3/9");

    // Block 2: PIT valuation
    expect(text).toContain("7.200 VND");
    expect(text).toContain("2.498,4 tỷ");
    expect(text).toContain("1,35x");
    expect(text).toContain("1,15 σ");

    // Block 3: Ratios vs sector
    expect(text).toContain("ROE Tổng");
    expect(text).toContain("12,4%");

    // Block 4: TreeSHAP drivers
    expect(text).toContain("Nợ vay / VCSH");
    expect(text).toContain("+0,65");

    // Block 5: Red flags (localized without raw RF_03 token)
    expect(text).toContain("Quy tắc 03");
    expect(text).toContain("Phải thu khác tăng đột biến");

    // Block 6: Snapshot lineage & Compliance disclaimer
    expect(text).toContain("snap_v3_tear_001");
    expect(text).toContain(COMPLIANCE_DISCLAIMER_VI);

    // Zero forbidden NĐ 88/2014 wording & zero leaked backend tokens
    expect(text).not.toMatch(FORBIDDEN_ND88_RE);
    expect(text).not.toMatch(LEAKED_BACKEND_TOKEN_RE);
  });
});
