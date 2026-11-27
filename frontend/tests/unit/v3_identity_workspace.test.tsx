// @vitest-environment jsdom
import * as React from "react";
import fs from "node:fs";
import path from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/auth/login/page";
import RegisterPage from "@/app/auth/register/page";
import { authApi } from "@/lib/auth-api";
import {
  addWatchlistTicker,
  clearLocalWatchlist,
  formatUserRoleLabel,
  getPendingLocalImportTickers,
  getWatchlistSnapshot,
  validateSafeNextPath,
} from "@/lib/watchlist-store";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
  }),
  useSearchParams: () => ({
    get: () => null,
  }),
}));

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("V3-12 Identity & Server-Backed Workspace Frontend", () => {
  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      // Node 24 without --localstorage-file
    }
    clearLocalWatchlist();
    mockPush.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("preserves anonymous localStorage watchlist and computes deduplicated pending import tickers", () => {
    addWatchlistTicker("hpg");
    addWatchlistTicker("fpt");
    addWatchlistTicker("vnm");
    expect(getWatchlistSnapshot()).toEqual(["VNM", "FPT", "HPG"]);

    expect(getPendingLocalImportTickers(["FPT", "MWG"])).toEqual(["VNM", "HPG"]);
    clearLocalWatchlist();
    expect(getWatchlistSnapshot()).toEqual([]);

    expect(validateSafeNextPath("/watchlist")).toBe("/watchlist");
    expect(validateSafeNextPath("https://evil.example")).toBe("/watchlist");
    expect(validateSafeNextPath("//evil.example")).toBe("/watchlist");
    expect(formatUserRoleLabel("USER")).toBe("Tài khoản tiêu chuẩn");
    expect(formatUserRoleLabel("RESEARCHER")).toBe("Nhà nghiên cứu");
    expect(formatUserRoleLabel("INTERNAL_OPS")).toBe("Vận hành nội bộ");
  });

  it("sends credentials: include and X-Requested-With on mutating authApi calls with zero localStorage tokens", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          user_id: "00000000-0000-0000-0000-000000000001",
          email: "user@finintel.vn",
          display_name: "Test User",
          role: "USER",
          consents: [],
          active_sessions_count: 1,
          created_at: "2026-01-01T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await authApi.login({ email: "user@finintel.vn", password: "ValidPassword!2026" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.credentials).toBe("include");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Requested-With"]).toBe("FinIntel-Web");
    const storageLen = (() => {
      try {
        return window.localStorage?.length ?? 0;
      } catch {
        return 0;
      }
    })();
    expect(storageLen).toBe(0);
  });

  it("enforces WCAG 2.2 AA aria-describedby errors on LoginPage", async () => {
    renderWithQuery(<LoginPage />);
    const submitBtn = screen.getByRole("button", { name: /Đăng nhập/i });
    fireEvent.click(submitBtn);

    const emailInput = await screen.findByLabelText("Email");
    expect(emailInput.getAttribute("aria-invalid")).toBe("true");
    expect(emailInput.getAttribute("aria-describedby")).toBe("login-email-error");
    expect(screen.getByText(/Vui lòng nhập địa chỉ email hợp lệ/i)).toBeTruthy();
  });

  it("enforces PDPL Law 91/2025 ACCOUNT_CORE consent checkbox on RegisterPage", async () => {
    renderWithQuery(<RegisterPage />);
    const emailInput = screen.getByLabelText(/^Email \*/i);
    const pwInput = screen.getByLabelText(/^Mật khẩu \*/i);
    const confirmInput = screen.getByLabelText(/^Xác nhận mật khẩu \*/i);

    fireEvent.change(emailInput, { target: { value: "analyst@finintel.vn" } });
    fireEvent.change(pwInput, { target: { value: "StrongPassword!2026" } });
    fireEvent.change(confirmInput, { target: { value: "StrongPassword!2026" } });

    const coreCheckbox = screen.getByLabelText(/Bắt buộc:/i) as HTMLInputElement;
    const alertsCheckbox = screen.getByLabelText(/Nhận cảnh báo rủi ro/i) as HTMLInputElement;
    const ragCheckbox = screen.getByLabelText(/Cho phép truy vấn đối chiếu/i) as HTMLInputElement;
    expect(coreCheckbox.checked).toBe(false);
    expect(alertsCheckbox.checked).toBe(false);
    expect(ragCheckbox.checked).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /Tạo tài khoản/i }));
    await waitFor(() => {
      expect(coreCheckbox.getAttribute("aria-invalid")).toBe("true");
      expect(coreCheckbox.getAttribute("aria-describedby")).toBe("consent-core-error");
    });
  });

  it("contains zero localStorage token keys and zero ND 88/2014 prohibited wording in new V3-12 files", () => {
    const root = path.resolve(__dirname, "../..");
    const files = [
      "src/lib/auth-api.ts",
      "src/app/auth/login/page.tsx",
      "src/app/auth/register/page.tsx",
      "src/app/auth/reset-password/page.tsx",
      "src/app/profile/page.tsx",
      "src/app/watchlist/page.tsx",
      "src/components/watchlist/AnalystNotesPanel.tsx",
    ];
    for (const rel of files) {
      const content = fs.readFileSync(path.join(root, rel), "utf8");
      expect(content).not.toMatch(/localStorage\.setItem\([^)]*token/i);
      expect(content).not.toMatch(/tín nhiệm/i);
    }
  });
});
