// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AlertsCenterPage from "@/app/alerts/page";
import { NotificationBell } from "@/components/alerts/NotificationBell";
import { COMPLIANCE_DISCLAIMER_VI } from "@/lib/compliance";

const FORBIDDEN_ND88_RE =
  /xếp\s+hạng\s+tín\s+nhiệm|(?:xếp\s+)?bậc\s+tín\s+nhiệm|phân\s+tầng\s+tín\s+nhiệm|\btín\s+nhiệm\b|khuyến\s+nghị\s+(?:mua|bán)(?:\s*\/\s*(?:mua|bán))?/i;

const LEAKED_BACKEND_TOKEN_RE =
  /\b(?:REGIME_FALLBACK|snapshot_id|issuer_id|BS_TOTAL_ASSETS|IS_NET_REVENUE)\b|\bRF_(?:0[1-9]|10)\b|\.parquet\b/i;

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

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const SAMPLE_NOTIFS = {
  items: [
    {
      notification_id: "11111111-1111-1111-1111-111111111111",
      ticker: "HPG",
      event_type: "RISK_TIER_UPGRADE",
      severity: "HIGH",
      title_vi: "HPG: Nhóm cảnh báo rủi ro định lượng chuyển từ R2 lên R4",
      company_url: "/companies/HPG",
      evidence: { previous_tier: "R2", current_tier: "R4" },
      is_read: false,
      read_at: null,
      created_at: "2026-04-22T08:00:00Z",
    },
    {
      notification_id: "22222222-2222-2222-2222-222222222222",
      ticker: "HBC",
      event_type: "NEW_RED_FLAG_TRIGGERED",
      severity: "CRITICAL",
      title_vi: "HBC: Phát sinh dấu hiệu cảnh báo kế toán mới — RF_10_POST_AUDIT",
      company_url: "/companies/HBC",
      evidence: {},
      is_read: true,
      read_at: "2026-04-22T09:00:00Z",
      created_at: "2026-04-21T08:00:00Z",
    },
  ],
  total: 2,
  unread_count: 1,
  limit: 50,
  offset: 0,
};

const SAMPLE_SUBSCRIPTION = {
  subscription_id: "33333333-3333-3333-3333-333333333333",
  watchlist_id: null,
  event_types: [
    "NEW_FILING_PUBLISHED",
    "RISK_TIER_UPGRADE",
    "NEW_RED_FLAG_TRIGGERED",
    "REGULATORY_EVENT_ISSUED",
    "MERTON_DTD_BREACH",
  ],
  in_app_enabled: true,
  email_digest_enabled: true,
  email_frequency: "DAILY_DIGEST",
  telegram_enabled: false,
  telegram_chat_id: null,
  webhook_enabled: true,
  webhook_url: "https://hooks.example.com/finintel",
  webhook_secret_configured: true,
  merton_dtd_threshold: 1.5,
  is_active: true,
  has_alert_consent: false,
  updated_at: "2026-04-22T08:00:00Z",
};

describe("V3-18 NotificationBell & /alerts UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders unread badge, supports keyboard Escape close, and marks read via CSRF mutation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/v3/alerts/notifications?") || url === "/api/v3/alerts/notifications") {
        return new Response(JSON.stringify(SAMPLE_NOTIFS), { status: 200 });
      }
      if (url.endsWith("/read") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            notification_id: "11111111-1111-1111-1111-111111111111",
            is_read: true,
            read_at: "2026-04-22T10:00:00Z",
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQuery(<NotificationBell isAuthenticated={true} />);

    const badge = await screen.findByTestId("notification-unread-badge");
    expect(badge.textContent).toBe("1");

    const bellBtn = screen.getByRole("button", {
      name: /Thông báo cảnh báo \(1 chưa đọc\)/i,
    });
    fireEvent.click(bellBtn);

    const dialog = await screen.findByRole("dialog", {
      name: /Hộp thư cảnh báo nhanh/i,
    });
    expect(dialog.textContent).toContain("HPG");
    expect(dialog.textContent).not.toMatch(FORBIDDEN_ND88_RE);
    expect(dialog.textContent).not.toMatch(LEAKED_BACKEND_TOKEN_RE);

    const markBtn = screen.getByRole("button", {
      name: /Đánh dấu đã đọc cảnh báo HPG/i,
    });
    fireEvent.click(markBtn);

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([u, i]) =>
          String(u).includes("/read") && (i as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const headers = (postCall?.[1]?.headers ?? {}) as Record<string, string>;
      expect(headers["X-Requested-With"]).toBe("FinIntel-Web");
    });

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("renders /alerts center with PDPL consent banner, channel config, webhook test, and zero forbidden wording", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v3/auth/session") {
        return new Response(
          JSON.stringify({
            authenticated: true,
            auth_configured: true,
            user: {
              user_id: "99999999-9999-9999-9999-999999999999",
              email: "analyst@finintel.local",
              display_name: "Analyst",
              role: "USER",
              consents: [],
              active_sessions_count: 1,
              created_at: "2026-04-01T00:00:00Z",
            },
          }),
          { status: 200 },
        );
      }
      if (url === "/api/v3/workspace/watchlists") {
        return new Response(
          JSON.stringify({
            watchlists: [
              {
                watchlist_id: "44444444-4444-4444-4444-444444444444",
                name: "Theo dõi chính",
                is_default: true,
                items: ["HPG", "FPT"],
                created_at: "2026-04-01T00:00:00Z",
                updated_at: "2026-04-01T00:00:00Z",
              },
            ],
            limits: {
              max_watchlists: 5,
              max_items_per_watchlist: 50,
              max_notes: 100,
              max_saved_screens: 10,
            },
          }),
          { status: 200 },
        );
      }
      if (url.startsWith("/api/v3/alerts/notifications")) {
        return new Response(JSON.stringify(SAMPLE_NOTIFS), { status: 200 });
      }
      if (url === "/api/v3/alerts/subscriptions") {
        if (init?.method === "PUT") {
          return new Response(
            JSON.stringify({ ...SAMPLE_SUBSCRIPTION, has_alert_consent: true }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify(SAMPLE_SUBSCRIPTION), { status: 200 });
      }
      if (url === "/api/v3/alerts/webhooks/test" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            delivered: true,
            http_status: 200,
            signature_header_prefix: "sha256=abcdef12345",
            detail: "Gửi thử Webhook thành công.",
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = renderWithQuery(<AlertsCenterPage />);

    await screen.findByText(/Chưa bật đồng ý nhận thông báo cảnh báo/i);
    expect(container.textContent).toContain(COMPLIANCE_DISCLAIMER_VI);
    expect(container.textContent).toContain("HPG");
    expect(container.textContent).not.toMatch(FORBIDDEN_ND88_RE);
    expect(container.textContent).not.toMatch(LEAKED_BACKEND_TOKEN_RE);

    fireEvent.click(screen.getByRole("tab", { name: /Cấu hình kênh nhận/i }));

    const testBtn = await screen.findByRole("button", {
      name: /Gửi thử Webhook/i,
    });
    fireEvent.click(testBtn);
    await screen.findByText(/Gửi thử Webhook thành công\./i);

    fireEvent.click(
      screen.getByRole("button", { name: /Lưu cấu hình cảnh báo/i }),
    );
    await screen.findByText(/Đã lưu cấu hình kênh cảnh báo sớm\./i);
  });
});
