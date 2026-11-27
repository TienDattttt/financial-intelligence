// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockRouterReplace = vi.fn();
let mockSearchParams = new URLSearchParams("tickers=HPG");
let mockRole: "USER" | "RESEARCHER" | "INTERNAL_OPS" = "INTERNAL_OPS";

const mockGetSuggestedPeers = vi.fn();
const mockGetQaQueue = vi.fn();
const mockSubmitRevision = vi.fn();
const mockDecideTicket = vi.fn();
const mockGetIssuerLifecycle = vi.fn();
const mockRequestIssuerSync = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace, push: vi.fn() }),
  useSearchParams: () => mockSearchParams,
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

vi.mock("echarts", () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
  }),
}));

vi.mock("@/components/providers/app-providers", () => ({
  useAppState: () => ({
    statementScope: "CONSOLIDATED",
    periodType: "QUARTER",
    periodEndDate: "2026-03-31",
    queryPeriodEndDate: "2026-03-31",
    snapshotId: "snap_v3_ops_test",
    cacheSnapshotKey: "snap_v3_ops_test",
    publishedAt: "2026-04-25T08:00:00Z",
  }),
}));

vi.mock("@/lib/echarts-theme", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/echarts-theme")>();
  return {
    ...actual,
    useChartTheme: () => ({
      resolvedTheme: "light",
      palette: actual.LIGHT_CHART_PALETTE,
    }),
  };
});

vi.mock("@/hooks/use-finintel-queries", () => ({
  useAllIssuers: () => ({
    data: [
      { issuer_id: "HPG", ticker: "HPG", company_name_vn: "Tập đoàn Hòa Phát", exchange: "HOSE" },
      { issuer_id: "HSG", ticker: "HSG", company_name_vn: "Tập đoàn Hoa Sen", exchange: "HOSE" },
      { issuer_id: "NKG", ticker: "NKG", company_name_vn: "Thép Nam Kim", exchange: "HOSE" },
      { issuer_id: "TLH", ticker: "TLH", company_name_vn: "Thép Tiến Lên", exchange: "HOSE" },
      { issuer_id: "VGS", ticker: "VGS", company_name_vn: "Ống thép Việt Đức", exchange: "HNX" },
    ],
  }),
  useCompareCompanies: () => ({
    profiles: [],
    summaries: [],
  }),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    apiClient: {
      ...actual.apiClient,
      getSuggestedPeers: (...args: unknown[]) => mockGetSuggestedPeers(...args),
    },
  };
});

vi.mock("@/hooks/use-auth-workspace", () => ({
  useAuthSession: () => ({
    isLoading: false,
    data: {
      authenticated: true,
      auth_configured: true,
      user: {
        user_id: "00000000-0000-0000-0000-000000000001",
        email: "ops@finintel.vn",
        display_name: "Ops Reviewer",
        role: mockRole,
      },
    },
  }),
}));

vi.mock("@/lib/auth-api", () => ({
  opsApi: {
    getQaQueue: (...args: unknown[]) => mockGetQaQueue(...args),
    assignTicket: vi.fn(),
    submitRevision: (...args: unknown[]) => mockSubmitRevision(...args),
    decideTicket: (...args: unknown[]) => mockDecideTicket(...args),
    getIssuerLifecycle: (...args: unknown[]) => mockGetIssuerLifecycle(...args),
    requestIssuerSync: (...args: unknown[]) => mockRequestIssuerSync(...args),
  },
}));

import ComparePage from "@/app/compare/page";
import OpsQaPage from "@/app/ops/qa/page";
import { DuPontTreeChart } from "@/components/charts/DuPontTreeChart";
import { GrowthComboChart } from "@/components/charts/GrowthComboChart";
import { FinancialTable } from "@/components/financial/FinancialTable";

function renderWithQuery(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("V3-24 Analyst Depth & Ops QA UI", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearchParams = new URLSearchParams("tickers=HPG");
    mockRole = "INTERNAL_OPS";
    if (typeof globalThis.ResizeObserver === "undefined") {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
  });

  afterEach(() => {
    cleanup();
  });

  it("renders interactive DuPontTreeChart and opens SourceDrawer callback on node click", () => {
    const onSelectMetric = vi.fn();
    render(
      <DuPontTreeChart
        dupontSeries={[
          {
            period_end_date: "2025-12-31",
            fiscal_year: 2025,
            fiscal_quarter: 4,
            taxonomy_version: "VAS_TT200_2014",
            tax_burden: 0.82,
            interest_burden: 0.91,
            ebit_margin: 0.145,
            asset_turnover: 0.78,
            equity_multiplier_total: 1.92,
            roe_total: 0.162,
            dupont_valid_flag: true,
          },
        ]}
        ratiosTable={[{ period_end_date: "2025-12-31", roa: 0.084, net_profit_margin: 0.108 }]}
        periodType="QUARTER"
        onSelectMetric={onSelectMetric}
      />,
    );

    const ebitBtn = screen.getByRole("button", { name: /Truy nguồn Biên EBIT/i });
    fireEvent.click(ebitBtn);
    expect(onSelectMetric).toHaveBeenCalledWith("ebit_margin", "2025-12-31");

    const roeBtn = screen.getByRole("button", { name: /Truy nguồn ROE/i });
    fireEvent.click(roeBtn);
    expect(onSelectMetric).toHaveBeenCalledWith("roe_total", "2025-12-31");
  });

  it("displays backend-computed QoQ, MIXED_TAXONOMY guard, and Q4 seasonal warning on FinancialTable & GrowthComboChart", () => {
    render(
      <>
        <FinancialTable
          statementType="INCOME_STATEMENT"
          periodType="QUARTER"
          periods={[
            {
              period_end_date: "2026-03-31",
              fiscal_year: 2026,
              fiscal_quarter: 1,
              label: "Q1/2026",
              taxonomy_version: "VAS_TT99_2025",
              audit_status: "UNAUDITED",
            },
            {
              period_end_date: "2025-12-31",
              fiscal_year: 2025,
              fiscal_quarter: 4,
              label: "Q4/2025",
              taxonomy_version: "VAS_TT200_2014",
              audit_status: "AUDITED",
            },
          ]}
          rows={[
            {
              metric_id: "IS_NET_REVENUE",
              display_name_vn: "Doanh thu thuần",
              tt200_code: "10",
              tt99_code: "10",
              unit: "VND",
              is_total: true,
              comparable_across_taxonomy: true,
              sort_order: 10,
              values: { "2025-12-31": 125_000_000_000, "2026-03-31": 140_000_000_000 },
              qoq_values: { "2025-12-31": 0.25, "2026-03-31": null },
              qoq_flags: { "2025-12-31": "VALID", "2026-03-31": "MIXED_TAXONOMY" },
            },
          ]}
        />
        <GrowthComboChart
          ticker="HPG"
          globalPeriodType="QUARTER"
          localPeriodView="Quarterly"
          onChangeLocalPeriodView={vi.fn()}
          points={[
            {
              period_end_date: "2025-12-31",
              fiscal_year: 2025,
              fiscal_quarter: 4,
              taxonomy_version: "VAS_TT200_2014",
              IS_NET_REVENUE: 125_000_000_000,
              IS_NET_INCOME: 15_000_000_000,
              CF_OPERATING_CASH_FLOW: 18_000_000_000,
              revenue_growth_qoq: 0.25,
              revenue_growth_qoq_flag: "VALID",
            },
            {
              period_end_date: "2026-03-31",
              fiscal_year: 2026,
              fiscal_quarter: 1,
              taxonomy_version: "VAS_TT99_2025",
              IS_NET_REVENUE: 140_000_000_000,
              IS_NET_INCOME: 16_000_000_000,
              CF_OPERATING_CASH_FLOW: 19_000_000_000,
              revenue_growth_qoq: null,
              revenue_growth_qoq_flag: "MIXED_TAXONOMY",
            },
          ]}
        />
      </>,
    );

    const qoqBtn = screen.getByRole("button", { name: "Tăng trưởng QoQ (%)" });
    fireEvent.click(qoqBtn);

    expect(screen.getByText(/Cảnh báo mùa vụ Q4/i)).toBeTruthy();
    expect(screen.getByText("+25,0%")).toBeTruthy();
    expect(screen.getByTitle(/TT200 → TT99/i)).toBeTruthy();
    expect(screen.getByText(/Khóa QoQ qua TT200 → TT99/i)).toBeTruthy();
  });

  it("loads 4 similar peers via 'Gợi ý 4 đối thủ cùng ngành' on /compare", async () => {
    mockGetSuggestedPeers.mockResolvedValueOnce({
      snapshot_id: "snap_v3_ops_test",
      ticker: "HPG",
      statement_scope: "CONSOLIDATED",
      period_type: "QUARTER",
      period_end_date: "2026-03-31",
      peer_basis: "ICB_LEVEL_3",
      peers: [
        { issuer_id: "HSG", ticker: "HSG", peer_basis: "ICB_LEVEL_3", size_metric: "market_cap_vnd" },
        { issuer_id: "NKG", ticker: "NKG", peer_basis: "ICB_LEVEL_3", size_metric: "market_cap_vnd" },
        { issuer_id: "TLH", ticker: "TLH", peer_basis: "ICB_LEVEL_3", size_metric: "market_cap_vnd" },
        { issuer_id: "VGS", ticker: "VGS", peer_basis: "ICB_LEVEL_3", size_metric: "market_cap_vnd" },
      ],
    });

    renderWithQuery(<ComparePage />);
    const btn = screen.getByRole("button", { name: /Gợi ý 4 đối thủ cùng ngành/i });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockGetSuggestedPeers).toHaveBeenCalledWith(
        "HPG",
        expect.objectContaining({ limit: 4 }),
      );
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/compare?tickers=HPG,HSG,NKG,TLH,VGS",
      );
    });
  });

  it("enforces INTERNAL_OPS gate and supports immutable revision submission on /ops/qa", async () => {
    mockRole = "USER";
    const { unmount } = renderWithQuery(<OpsQaPage />);
    expect(screen.getByText(/Chỉ dành cho INTERNAL_OPS/i)).toBeTruthy();
    expect(mockGetQaQueue).not.toHaveBeenCalled();
    unmount();

    mockRole = "INTERNAL_OPS";
    const baseTicket = {
      ticket_id: "11111111-1111-1111-1111-111111111111",
      issuer_id: "P00",
      ticker: "P00",
      filing_id: "FIL-P00-BAD",
      statement_scope: "CONSOLIDATED",
      period_type: "QUARTER",
      period_end_date: "2025-12-31",
      taxonomy_version: "VAS_TT200_2014",
      rule_code: "BS_IDENTITY_MISMATCH",
      severity: "CRITICAL",
      details_json: "{}",
      status: "OPEN",
      decision: null,
      assigned_to_user_id: null,
      root_cause_note: "",
      latest_revision_no: 1,
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
      resolved_at: null,
      revisions: [
        {
          submission_id: "22222222-2222-2222-2222-222222222222",
          ticket_id: "11111111-1111-1111-1111-111111111111",
          revision_no: 1,
          source_url: null,
          raw_file_sha256: null,
          reason: "ORIGINAL_QUARANTINED_FILING",
          reingest_status: "QUARANTINED",
          dagster_run_id: null,
          submitted_by_user_id: null,
          submitted_at: "2026-04-01T00:00:00Z",
        },
      ],
    };

    mockGetQaQueue.mockResolvedValue({
      snapshot_id: "snap_v3_ops_test",
      total: 1,
      items: [baseTicket],
    });
    mockSubmitRevision.mockResolvedValueOnce({
      status: "REVISION_APPENDED",
      ticket: {
        ...baseTicket,
        status: "IN_REVIEW",
        latest_revision_no: 2,
      },
    });

    renderWithQuery(<OpsQaPage />);
    await waitFor(() => {
      expect(screen.getByText(/P00 · BS_IDENTITY_MISMATCH/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("URL công bố gốc"), {
      target: { value: "https://congbothongtin.ssc.gov.vn/bctc/p00.pdf" },
    });
    fireEvent.change(screen.getByLabelText("Lý do đính chính"), {
      target: { value: "Đính chính BCTC kiểm toán Q4/2025" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Gửi phiên bản đính chính/i }));

    await waitFor(() => {
      expect(mockSubmitRevision).toHaveBeenCalledWith(
        "11111111-1111-1111-1111-111111111111",
        expect.objectContaining({
          source_url: "https://congbothongtin.ssc.gov.vn/bctc/p00.pdf",
          reason: "Đính chính BCTC kiểm toán Q4/2025",
        }),
      );
      expect(screen.getByText(/nối phiên bản đính chính #2/i)).toBeTruthy();
    });
  });
});
