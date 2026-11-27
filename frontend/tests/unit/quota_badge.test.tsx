// @vitest-environment jsdom
import * as React from "react";
import fs from "node:fs";
import path from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  QuotaBadge,
  deriveRagQuotaGateState,
  formatQuotaResetTimeVi,
} from "@/components/quota/QuotaBadge";
import { DocumentInvestigationPanel } from "@/components/rag/DocumentInvestigationPanel";
import { authApi } from "@/lib/auth-api";
import type { MyQuotaResponse } from "@/types/api";

const FRONTEND_ROOT = path.resolve(__dirname, "../..");

function makeQuota(overrides?: Partial<MyQuotaResponse>): MyQuotaResponse {
  return {
    authenticated: true,
    role: "USER",
    quota_date: "2026-11-27",
    timezone: "Asia/Ho_Chi_Minh",
    reset_at: "2026-11-27T17:00:00Z", // 2026-11-28 00:00 Asia/Ho_Chi_Minh
    cross_border_rag_consent: true,
    circuit_breaker_open: false,
    circuit_breaker_reopen_at: null,
    meters: [
      {
        meter: "rag_query",
        used: 1,
        limit: 15,
        remaining: 14,
        unlimited: false,
        reset_at: "2026-11-27T17:00:00Z",
      },
      {
        meter: "xlsx_export",
        used: 0,
        limit: 10,
        remaining: 10,
        unlimited: false,
        reset_at: "2026-11-27T17:00:00Z",
      },
    ],
    ...overrides,
  };
}

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("V3-21 QuotaBadge & DocumentInvestigationPanel Quota Wiring", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("formats reset_at in Asia/Ho_Chi_Minh and derives gate states accurately", () => {
    expect(formatQuotaResetTimeVi("2026-11-27T17:00:00Z")).toBe("00:00 (UTC+7)");
    const normal = deriveRagQuotaGateState(makeQuota());
    expect(normal.blocked).toBe(false);
    expect(normal.remaining).toBe(14);
    expect(normal.limit).toBe(15);

    const exhausted = deriveRagQuotaGateState(
      makeQuota({
        meters: [
          {
            meter: "rag_query",
            used: 15,
            limit: 15,
            remaining: 0,
            unlimited: false,
            reset_at: "2026-11-27T17:00:00Z",
          },
        ],
      }),
    );
    expect(exhausted.isExhausted).toBe(true);
    expect(exhausted.blocked).toBe(true);
  });

  it("renders remaining quota, exhausted banner, consent link, circuit breaker, and guest states", () => {
    const { rerender } = render(<QuotaBadge quota={makeQuota()} />);
    expect(screen.getByText("14/15")).toBeTruthy();
    expect(screen.getByText(/00:00 \(UTC\+7\)/)).toBeTruthy();

    rerender(
      <QuotaBadge
        quota={makeQuota({
          meters: [
            {
              meter: "rag_query",
              used: 15,
              limit: 15,
              remaining: 0,
              unlimited: false,
              reset_at: "2026-11-27T17:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/Đã dùng hết/)).toBeTruthy();
    expect(screen.getByText("15/15")).toBeTruthy();

    rerender(
      <QuotaBadge quota={makeQuota({ cross_border_rag_consent: false })} />,
    );
    const profileLink = screen.getByRole("link", { name: /Bật trong Hồ sơ/i });
    expect(profileLink.getAttribute("href")).toBe("/profile");

    rerender(
      <QuotaBadge
        quota={makeQuota({
          circuit_breaker_open: true,
          circuit_breaker_reopen_at: "2026-11-27T17:00:00Z",
        })}
      />,
    );
    expect(screen.getByText(/tạm giới hạn truy vấn mới/i)).toBeTruthy();

    rerender(
      <QuotaBadge
        quota={makeQuota({
          authenticated: false,
          role: "ANONYMOUS",
          cross_border_rag_consent: false,
          meters: [],
        })}
      />,
    );
    const loginLink = screen.getByRole("link", { name: /Đăng nhập/i });
    expect(loginLink.getAttribute("href")).toBe("/auth/login");
  });

  it("integrates QuotaBadge inside DocumentInvestigationPanel and gates submission when quota exhausted", async () => {
    const exhaustedQuota = makeQuota({
      meters: [
        {
          meter: "rag_query",
          used: 15,
          limit: 15,
          remaining: 0,
          unlimited: false,
          reset_at: "2026-11-27T17:00:00Z",
        },
      ],
    });
    const docsPayload = {
      ticker: "HBC",
      total_documents: 1,
      documents: [
        {
          filing_id: "HBC_FY2023_CONSOLIDATED_abc",
          issuer_id: "HBC",
          ticker: "HBC",
          fiscal_year: 2023,
          fiscal_quarter: null,
          statement_scope: "CONSOLIDATED",
          doc_type: "AUDITED_ANNUAL_CONSOLIDATED_FS",
          cohort: "case_study",
          source_title: "BCTC kiểm toán 2023",
          source_url: "https://cafef1.mediacdn.vn/hbc-2023.pdf",
          pdf_sha256: "a".repeat(64),
          file_size_bytes: 1_000_000,
          total_pages: 50,
          digital_pages: 45,
          ocr_pages: 5,
          chunk_count: 100,
        },
      ],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v3/quota/me")) {
          return new Response(JSON.stringify(exhaustedQuota), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify(docsPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderWithQuery(<DocumentInvestigationPanel ticker="HBC" />);

    await waitFor(() => {
      expect(screen.getByText(/Đã dùng hết/)).toBeTruthy();
    });
    const submitBtn = screen.getByRole("button", {
      name: /Tra cứu Thuyết minh/i,
    }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it("authApi.getMyQuota sends credentials: include and enforces ND 88/2014 + Anti-AI-Slop rules", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(makeQuota()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const quota = await authApi.getMyQuota();
    expect(quota.role).toBe("USER");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v3/quota/me",
      expect.objectContaining({ credentials: "include" }),
    );

    const quotaBadgeSrc = fs.readFileSync(
      path.join(FRONTEND_ROOT, "src/components/quota/QuotaBadge.tsx"),
      "utf8",
    );
    expect(quotaBadgeSrc).not.toMatch(/tín nhiệm|✨|AI-Powered|backdrop-blur/i);
  });
});
