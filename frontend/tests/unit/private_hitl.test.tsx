// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockRole: "USER" | "RESEARCHER" | "INTERNAL_OPS" = "RESEARCHER";
let mockPathname = "/companies/CTD";

const mockDownloadTemplate = vi.fn();
const mockParseWorkbook = vi.fn();
const mockListDossiers = vi.fn();
const mockGetDossier = vi.fn();
const mockScoreDossier = vi.fn();
const mockDeleteDossier = vi.fn();
const mockSubmitFeedback = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
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

vi.mock("@/hooks/use-auth-workspace", () => ({
  useAuthSession: () => ({
    isLoading: false,
    data: {
      authenticated: true,
      user: {
        user_id: "11111111-1111-1111-1111-111111111111",
        email: "analyst@finintel.vn",
        display_name: "Chuyên viên phân tích",
        role: mockRole,
      },
    },
  }),
}));

vi.mock("@/lib/auth-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth-api")>();
  return {
    ...actual,
    privateCreditApi: {
      ...actual.privateCreditApi,
      downloadTemplate: (...args: unknown[]) => mockDownloadTemplate(...args),
      parseWorkbook: (...args: unknown[]) => mockParseWorkbook(...args),
      listDossiers: (...args: unknown[]) => mockListDossiers(...args),
      getDossier: (...args: unknown[]) => mockGetDossier(...args),
      scoreDossier: (...args: unknown[]) => mockScoreDossier(...args),
      deleteDossier: (...args: unknown[]) => mockDeleteDossier(...args),
    },
    hitlApi: {
      ...actual.hitlApi,
      submitFeedback: (...args: unknown[]) => mockSubmitFeedback(...args),
    },
  };
});

import PrivateScoringPage from "@/app/private-scoring/page";
import { RedFlagChecklist } from "@/components/financial/RedFlagChecklist";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  mockRole = "RESEARCHER";
  mockPathname = "/companies/CTD";
  vi.clearAllMocks();
  mockListDossiers.mockResolvedValue({ items: [] });
  mockDownloadTemplate.mockResolvedValue(new Blob(["PK\x03\x04"]));
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, "createObjectURL", {
      writable: true,
      value: vi.fn(() => "blob:mock-url"),
    });
  } else {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-url");
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, "revokeObjectURL", {
      writable: true,
      value: vi.fn(),
    });
  } else {
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  }
});

afterEach(() => {
  cleanup();
});

describe("V3-30 Private Scoring Workbench & HITL Triage", () => {
  it("restricts /private-scoring when user role is USER", () => {
    mockRole = "USER";
    renderWithQuery(<PrivateScoringPage />);
    expect(
      screen.getByText(/Không gian chấm điểm doanh nghiệp chưa niêm yết/i),
    ).toBeTruthy();
    expect(screen.getByText(/RESEARCHER/i)).toBeTruthy();
    expect(screen.queryByLabelText(/Tệp Excel BCTC/i)).toBeNull();
  });

  it("blocks scoring when uploaded workbook fails QG1 balance sheet identity", async () => {
    mockParseWorkbook.mockResolvedValue({
      dossier_id: "22222222-2222-2222-2222-222222222222",
      company_name: "Công ty TNHH Kiểm thử Lệch",
      tax_code: "0309998888",
      icb_code_level2: "1700",
      statement_scope: "SEPARATE",
      taxonomy_version: "VAS_TT99_2025",
      unit_multiplier: 1,
      source_filename: "imbalanced.xlsx",
      source_sha256: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
      periods: ["2024-12-31", "2025-12-31"],
      qg1: {
        status: "FAIL",
        discrepancies: [
          {
            period_end_date: "2025-12-31",
            rule_code: "BS_IDENTITY_MISMATCH",
            severity: "CRITICAL",
            message_vi:
              "Kỳ 2025-12-31: Tổng Tài sản không khớp Tổng Nguồn vốn (Nợ phải trả + VCSH), chênh lệch 50,000,000,000 VND.",
          },
        ],
        warnings: [],
        info_signals: [],
        can_score: false,
      },
      lines: [],
      retention_days: 30,
      retention_expires_at: "2026-12-27T00:00:00Z",
      processing_mode: "LOCAL_SERVER_ONLY",
      created_at: "2026-11-27T00:00:00Z",
    });

    renderWithQuery(<PrivateScoringPage />);

    fireEvent.change(screen.getByLabelText(/Tên doanh nghiệp/i), {
      target: { value: "Công ty TNHH Kiểm thử Lệch" },
    });
    const file = new File(["fake-xlsx"], "imbalanced.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(screen.getByLabelText(/Tệp Excel BCTC/i), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên & Kiểm tra QG1/i }));

    await waitFor(() => {
      expect(mockParseWorkbook).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText(/Phát hiện sai lệch kế toán QG1 — Chặn chấm điểm/i),
    ).toBeTruthy();

    const scoreBtn = screen.getByRole("button", {
      name: /Chấm điểm định lượng \(ACCOUNTING_ONLY_MODE\)/i,
    }) as HTMLButtonElement;
    expect(scoreBtn.disabled).toBe(true);
  });

  it("uploads valid workbook, runs isolated ACCOUNTING_ONLY_MODE scoring, and downloads template", async () => {
    const dossierId = "33333333-3333-3333-3333-333333333333";
    mockParseWorkbook.mockResolvedValue({
      dossier_id: dossierId,
      company_name: "Công ty TNHH Minh Long",
      tax_code: "0301234567",
      icb_code_level2: "1700",
      statement_scope: "SEPARATE",
      taxonomy_version: "VAS_TT99_2025",
      unit_multiplier: 1,
      source_filename: "minh_long.xlsx",
      source_sha256: "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
      periods: ["2024-12-31", "2025-12-31"],
      qg1: {
        status: "PASS",
        discrepancies: [],
        warnings: [],
        info_signals: [],
        can_score: true,
      },
      lines: [],
      retention_days: 30,
      retention_expires_at: "2026-12-27T00:00:00Z",
      processing_mode: "LOCAL_SERVER_ONLY",
      created_at: "2026-11-27T00:00:00Z",
    });

    mockScoreDossier.mockResolvedValue({
      dossier_id: dossierId,
      company_name: "Công ty TNHH Minh Long",
      tax_code: "0301234567",
      taxonomy_version: "VAS_TT99_2025",
      statement_scope: "SEPARATE",
      scored_period_end_date: "2025-12-31",
      qg1_status: "PASS",
      ratios: {
        roa: 0.128,
        roe_total: 0.285,
        debt_to_equity: 0.72,
        interest_coverage: 6.2,
      },
      ratio_flags: {},
      dupont: {
        tax_burden: 0.8,
        interest_burden: 0.98,
        ebit_margin: 0.16,
        asset_turnover: 1.0,
        equity_multiplier_total: 2.2,
        parent_profit_share: 1.0,
        equity_multiplier_parent: 2.2,
        roe_total: 0.285,
        roe_parent: 0.285,
        dupont_valid_flag: true,
      },
      classical: {
        altman_z_double_prime: 4.15,
        altman_em_score: 7.4,
        altman_zone: "SAFE_ZONE",
        piotroski_f_score: null,
        piotroski_valid_components_count: 6,
        beneish_m8_score: -2.4,
        beneish_flag: "VALID",
      },
      red_flags: [
        {
          period_end_date: "2025-12-31",
          flag_code: "RF_01",
          severity: "HIGH",
          flag_title_vn: "Lợi nhuận có dấu hiệu bất thường so với dòng tiền",
          explanation_text_vn: "OCF/LNST thấp hơn ngưỡng an toàn.",
          evidence_json: "{}",
        },
      ],
      ml_assessment: {
        ml_mode: "ACCOUNTING_ONLY_MODE",
        calibrated_distress_prob: 0.018,
        risk_tier: "R2",
        model_version: "accounting-only-calibrated-v1",
        accounting_feature_coverage: 0.86,
        missing_market_tt96_features: ["merton_dtd", "overdue_or_late_count_4q"],
        coverage_warning_vi:
          "Chế độ Kế toán thuần túy (ACCOUNTING_ONLY_MODE): không có dữ liệu giá thị trường.",
      },
      stress_test: {
        scenario_name: "CUSTOM",
        baseline_ebit_vnd: 176_000_000_000,
        stressed_ebit_vnd: 132_000_000_000,
        baseline_interest_coverage: 5.87,
        stressed_interest_coverage: 3.9,
        stressed_interest_coverage_flag: "VALID",
        baseline_cfo_vnd: 160_000_000_000,
        stressed_cfo_vnd: 120_000_000_000,
        baseline_debt_at_risk: false,
        stressed_debt_at_risk: false,
      },
      ducklake_contaminated: false,
      ml_labels_modified: false,
      elapsed_ms: 42.5,
    });

    renderWithQuery(<PrivateScoringPage />);

    fireEvent.click(screen.getByRole("button", { name: /Tải mẫu Excel TT99\/2025/i }));
    await waitFor(() => {
      expect(mockDownloadTemplate).toHaveBeenCalledWith("VAS_TT99_2025");
    });

    fireEvent.change(screen.getByLabelText(/Tên doanh nghiệp/i), {
      target: { value: "Công ty TNHH Minh Long" },
    });
    const file = new File(["valid-xlsx"], "minh_long.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(screen.getByLabelText(/Tệp Excel BCTC/i), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên & Kiểm tra QG1/i }));

    await waitFor(() => {
      expect(mockParseWorkbook).toHaveBeenCalledTimes(1);
    });
    const scoreBtn = await screen.findByRole("button", {
      name: /Chấm điểm định lượng \(ACCOUNTING_ONLY_MODE\)/i,
    });
    expect((scoreBtn as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(scoreBtn);
    await waitFor(() => {
      expect(mockScoreDossier).toHaveBeenCalledWith(dossierId, {
        interest_rate_shock_bps: 200,
        revenue_shock_pct: -0.15,
      });
    });

    expect(await screen.findByText(/Chế độ đánh giá: ACCOUNTING_ONLY_MODE/i)).toBeTruthy();
    expect(screen.getByText(/merton_dtd, overdue_or_late_count_4q/i)).toBeTruthy();
    expect(screen.getByText(/R2 · Rủi ro thấp/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Phản hồi chuyên gia/i })).toBeTruthy();
  });

  it("opens RedFlagChecklist HITL dialog and submits expert false-positive triage", async () => {
    mockSubmitFeedback.mockResolvedValue({
      feedback_id: "44444444-4444-4444-4444-444444444444",
      ticker: "CTD",
      icb_code_level2: "2300",
      period_end_date: "2025-12-31",
      signal_code: "RF_06",
      verdict: "BENIGN_DOMAIN_EXCEPTION",
      reason_category: "CONSTRUCTION_PROGRESS_BILLING",
      note: "Khoản phải thu theo tiến độ nghiệm thu hợp đồng EPC, thuyết minh trang 24.",
      citation_page: 24,
      created_at: "2026-11-27T00:00:00Z",
      ml_training_labels_untouched: true,
    });

    renderWithQuery(
      <RedFlagChecklist
        dictionary={[]}
        triggeredFlags={[
          {
            period_end_date: "2025-12-31",
            flag_code: "RF_06",
            severity: "MEDIUM",
            flag_title_vn: "Chu kỳ chuyển đổi tiền mặt kéo dài",
            explanation_text_vn: "DSO tăng do ghi nhận nghiệm thu cuối năm.",
            evidence_json: "{}",
          },
        ]}
        latestPeriodEndDate="2025-12-31"
        ticker="CTD"
        icbCodeLevel2="2300"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Phản hồi chuyên gia/i }));

    expect(
      await screen.findByRole("heading", {
        name: /Phản hồi chuyên gia về tín hiệu bất thường/i,
      }),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Trang thuyết minh tham chiếu/i), {
      target: { value: "24" },
    });
    fireEvent.change(screen.getByLabelText(/Lập luận chuyên môn/i), {
      target: {
        value: "Khoản phải thu theo tiến độ nghiệm thu hợp đồng EPC, thuyết minh trang 24.",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Lưu phản hồi/i }));

    await waitFor(() => {
      expect(mockSubmitFeedback).toHaveBeenCalledWith({
        ticker: "CTD",
        icb_code_level2: "2300",
        period_end_date: "2025-12-31",
        signal_code: "RF_06",
        verdict: "BENIGN_DOMAIN_EXCEPTION",
        reason_category: "CONSTRUCTION_PROGRESS_BILLING",
        note: "Khoản phải thu theo tiến độ nghiệm thu hợp đồng EPC, thuyết minh trang 24.",
        citation_page: 24,
      });
    });

    expect(
      await screen.findByText(/Đã ghi nhận phản hồi chuyên gia cho Quy tắc 06/i),
    ).toBeTruthy();
  });
});
