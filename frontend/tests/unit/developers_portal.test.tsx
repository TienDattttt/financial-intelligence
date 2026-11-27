// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockAuthenticated = true;
let mockRole: "USER" | "RESEARCHER" | "INTERNAL_OPS" = "RESEARCHER";

const mockListKeys = vi.fn();
const mockCreateKey = vi.fn();
const mockRotateKey = vi.fn();
const mockRevokeKey = vi.fn();
const mockGetUsage = vi.fn();
const mockListWebhooks = vi.fn();
const mockCreateWebhook = vi.fn();
const mockDeleteWebhook = vi.fn();
const mockTestWebhook = vi.fn();

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
      authenticated: mockAuthenticated,
      auth_configured: true,
      user: mockAuthenticated
        ? {
            user_id: "00000000-0000-0000-0000-000000000002",
            email: "researcher@finintel.vn",
            display_name: "Minh Researcher",
            role: mockRole,
          }
        : null,
    },
  }),
}));

vi.mock("@/lib/auth-api", () => ({
  b2bApi: {
    listKeys: (...args: unknown[]) => mockListKeys(...args),
    createKey: (...args: unknown[]) => mockCreateKey(...args),
    rotateKey: (...args: unknown[]) => mockRotateKey(...args),
    revokeKey: (...args: unknown[]) => mockRevokeKey(...args),
    getUsage: (...args: unknown[]) => mockGetUsage(...args),
    listWebhooks: (...args: unknown[]) => mockListWebhooks(...args),
    createWebhook: (...args: unknown[]) => mockCreateWebhook(...args),
    deleteWebhook: (...args: unknown[]) => mockDeleteWebhook(...args),
    testWebhook: (...args: unknown[]) => mockTestWebhook(...args),
  },
}));

import DevelopersPortalPage from "@/app/developers/page";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DevelopersPortalPage />
    </QueryClientProvider>,
  );
}

describe("V3-27 /developers Open Research API Portal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthenticated = true;
    mockRole = "RESEARCHER";
  });

  afterEach(() => {
    cleanup();
  });

  it("blocks USER role with clear Vietnamese researcher-approval guidance without calling B2B APIs", () => {
    mockRole = "USER";
    renderPage();

    expect(screen.getByText(/Cần quyền Nhà nghiên cứu \(RESEARCHER\)/i)).toBeTruthy();
    expect(screen.getByText(/Hướng dẫn xét duyệt/i)).toBeTruthy();
    expect(mockListKeys).not.toHaveBeenCalled();
  });

  it("prompts unauthenticated visitors to sign in", () => {
    mockAuthenticated = false;
    renderPage();

    expect(screen.getByText(/Vui lòng đăng nhập bằng tài khoản Nhà nghiên cứu/i)).toBeTruthy();
    expect(mockListKeys).not.toHaveBeenCalled();
  });

  it("displays one-time plaintext API key on creation and hides it after refresh/dismiss", async () => {
    mockListKeys.mockResolvedValue({
      items: [],
      raw_redistribution_enabled: false,
      default_rate_limit_per_min: 30,
      default_daily_limit: 1000,
    });
    mockGetUsage.mockResolvedValue({
      from_date: "2026-11-14",
      to_date: "2026-11-27",
      total_requests: 42,
      total_cache_hits: 15,
      daily: [
        {
          usage_date: "2026-11-27",
          key_id: "k1",
          key_prefix: "fi_res_12345678",
          endpoint: "/api/v3/open/companies/{ticker}/risk",
          status_class: "2xx",
          request_count: 42,
          cache_hits: 15,
        },
      ],
    });
    mockListWebhooks.mockResolvedValue({ items: [] });

    const secretOnce = "fi_res_12345678_secret_only_shown_once_value";
    mockCreateKey.mockResolvedValueOnce({
      key: {
        key_id: "11111111-1111-1111-1111-111111111111",
        name: "Luận văn HOSE",
        key_prefix: "fi_res_12345678",
        scopes: ["read:derived_risk", "write:webhooks"],
        rate_limit_per_min: 30,
        daily_limit: 1000,
        is_active: true,
        last_used_at: null,
        expires_at: null,
        revoked_at: null,
        created_at: "2026-11-27T00:00:00Z",
      },
      plaintext_key: secretOnce,
    });

    const { unmount } = renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Chỉ chỉ báo phái sinh/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Tên API Key"), {
      target: { value: "Luận văn HOSE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Tạo API Key" }));

    await waitFor(() => {
      expect(screen.getByText(secretOnce)).toBeTruthy();
      expect(screen.getByText(/chỉ hiển thị duy nhất một lần/i)).toBeTruthy();
    });

    // Simulate page refresh: new mount only gets metadata from GET /api/v3/b2b/keys, never plaintext
    unmount();
    mockListKeys.mockResolvedValue({
      items: [
        {
          key_id: "11111111-1111-1111-1111-111111111111",
          name: "Luận văn HOSE",
          key_prefix: "fi_res_12345678",
          scopes: ["read:derived_risk"],
          rate_limit_per_min: 30,
          daily_limit: 1000,
          is_active: true,
          last_used_at: null,
          expires_at: null,
          revoked_at: null,
          created_at: "2026-11-27T00:00:00Z",
        },
      ],
      raw_redistribution_enabled: false,
      default_rate_limit_per_min: 30,
      default_daily_limit: 1000,
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Luận văn HOSE")).toBeTruthy();
    });
    expect(screen.queryByText(secretOnce)).toBeNull();
    expect(screen.getByText(/fi_res_12345678_••••••••/)).toBeTruthy();

    // Switch code snippet tab to cURL
    fireEvent.click(screen.getByRole("tab", { name: "cURL" }));
    expect(screen.getByText(/If-None-Match/i)).toBeTruthy();

    // Rotate key & register webhook
    const rotatedSecret = "fi_res_87654321_rotated_secret_value_once";
    mockRotateKey.mockResolvedValueOnce({
      key: {
        key_id: "22222222-2222-2222-2222-222222222222",
        name: "Luận văn HOSE",
        key_prefix: "fi_res_87654321",
        scopes: ["read:derived_risk"],
        rate_limit_per_min: 30,
        daily_limit: 1000,
        is_active: true,
        last_used_at: null,
        expires_at: null,
        revoked_at: null,
        created_at: "2026-11-27T01:00:00Z",
      },
      plaintext_key: rotatedSecret,
    });
    fireEvent.click(screen.getByRole("button", { name: "Xoay khóa Luận văn HOSE" }));
    await waitFor(() => {
      expect(screen.getByText(rotatedSecret)).toBeTruthy();
    });

    mockCreateWebhook.mockResolvedValueOnce({
      webhook: {
        endpoint_id: "33333333-3333-3333-3333-333333333333",
        label: "Lab Hook",
        url: "https://hooks.example.com/finintel",
        event_types: ["RISK_TIER_UPGRADE"],
        secret_configured: true,
        is_active: true,
        last_status: null,
        last_error: null,
        last_delivered_at: null,
        created_at: "2026-11-27T01:00:00Z",
      },
      secret_hmac: "whsec_demo_secret_123456",
    });
    fireEvent.change(screen.getByLabelText("Nhãn Webhook"), { target: { value: "Lab Hook" } });
    fireEvent.change(screen.getByLabelText("URL HTTPS Webhook"), { target: { value: "https://hooks.example.com/finintel" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng ký Webhook" }));
    await waitFor(() => {
      expect(screen.getByText(/whsec_demo_secret_123456/)).toBeTruthy();
    });
  });
});
