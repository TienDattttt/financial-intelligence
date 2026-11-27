import { ApiError } from "@/lib/api-client";
import type {
  AlertEventType,
  AlertSeverity,
  AlertSubscriptionResponse,
  AlertSubscriptionUpdateRequest,
  MarkAllReadResponse,
  NotificationReadResponse,
  NotificationsListResponse,
  WebhookTestRequest,
  WebhookTestResponse,
} from "@/types/api";

const INTERNAL_LEAK_RE =
  /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.(?:parquet|sqlite|ducklake|py)\b|Traceback|\b(?:postgres|postgresql|redis|valkey):\/\/|\b(?:BS|IS|CF)_[A-Z0-9_]+\b)/i;

function sanitizeAlertError(raw: string, status: number): string {
  const cleaned = raw.replace(/\s+/g, " ").trim();
  if (cleaned && !INTERNAL_LEAK_RE.test(cleaned)) {
    return cleaned.slice(0, 220);
  }
  if (status === 401) return "Vui lòng đăng nhập để sử dụng trung tâm cảnh báo.";
  if (status === 403) return "Bạn không có quyền thực hiện thao tác cảnh báo này.";
  if (status === 404) return "Không tìm thấy thông báo cảnh báo được yêu cầu.";
  if (status === 422) return "Cấu hình kênh cảnh báo hoặc URL Webhook chưa hợp lệ.";
  if (status === 503) return "Dịch vụ cảnh báo hiện chưa sẵn sàng trên máy chủ.";
  return "Không thể tải hoặc cập nhật dữ liệu cảnh báo.";
}

async function alertFetchJson<T>(
  input: string,
  init?: RequestInit & { mutating?: boolean },
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isMutating = init?.mutating ?? method !== "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (init?.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (isMutating) {
    headers["X-Requested-With"] = "FinIntel-Web";
  }

  const res = await fetch(input, {
    ...init,
    credentials: "include",
    headers: {
      ...headers,
      ...(init?.headers as Record<string, string> | undefined),
    },
  });

  if (!res.ok) {
    let detail = "";
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      if (typeof errBody.detail === "string") {
        detail = errBody.detail;
      }
    } catch {
      detail = "";
    }
    throw new ApiError(res.status, sanitizeAlertError(detail, res.status));
  }

  return (await res.json()) as T;
}

export const alertsApi = {
  listNotifications: (
    opts?: { unreadOnly?: boolean; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (opts?.unreadOnly) params.set("unread_only", "true");
    if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return alertFetchJson<NotificationsListResponse>(
      `/api/v3/alerts/notifications${qs ? `?${qs}` : ""}`,
      { signal, mutating: false },
    );
  },

  markNotificationRead: (notificationId: string) =>
    alertFetchJson<NotificationReadResponse>(
      `/api/v3/alerts/notifications/${encodeURIComponent(notificationId)}/read`,
      { method: "POST" },
    ),

  markAllRead: () =>
    alertFetchJson<MarkAllReadResponse>("/api/v3/alerts/notifications/read-all", {
      method: "POST",
    }),

  getSubscription: (signal?: AbortSignal) =>
    alertFetchJson<AlertSubscriptionResponse>("/api/v3/alerts/subscriptions", {
      signal,
      mutating: false,
    }),

  updateSubscription: (payload: AlertSubscriptionUpdateRequest) =>
    alertFetchJson<AlertSubscriptionResponse>("/api/v3/alerts/subscriptions", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  testWebhook: (payload: WebhookTestRequest = {}) =>
    alertFetchJson<WebhookTestResponse>("/api/v3/alerts/webhooks/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

export interface AlertEventTypeMeta {
  type: AlertEventType;
  label: string;
  description: string;
}

export const ALERT_EVENT_TYPE_META: readonly AlertEventTypeMeta[] = [
  {
    type: "NEW_FILING_PUBLISHED",
    label: "BCTC kỳ mới / cập nhật kiểm toán",
    description: "Phát hiện khi doanh nghiệp công bố báo cáo tài chính mới hoặc điều chỉnh sau kiểm toán.",
  },
  {
    type: "RISK_TIER_UPGRADE",
    label: "Thay đổi nhóm cảnh báo rủi ro (R1–R5)",
    description: "Thông báo khi nhóm cảnh báo rủi ro tài chính định lượng chuyển lên mức cao hơn.",
  },
  {
    type: "NEW_RED_FLAG_TRIGGERED",
    label: "Dấu hiệu bất thường kế toán mới",
    description: "Cảnh báo khi phát sinh quy tắc bất thường mới về dòng tiền, phải thu hoặc lợi nhuận.",
  },
  {
    type: "REGULATORY_EVENT_ISSUED",
    label: "Sự kiện pháp lý Sở GDCK",
    description: "Thông báo diện cảnh báo, kiểm soát, hạn chế giao dịch hoặc ý kiến kiểm toán ngoại trừ.",
  },
  {
    type: "MERTON_DTD_BREACH",
    label: "Merton DTD giảm dưới ngưỡng cấu hình",
    description: "Cảnh báo khi khoảng cách vỡ nợ cấu trúc Merton DTD giảm xuống dưới ngưỡng an toàn.",
  },
];

export const ALERT_SEVERITY_LABELS: Record<
  AlertSeverity,
  { label: string; badgeClass: string }
> = {
  CRITICAL: {
    label: "Khẩn cấp",
    badgeClass: "bg-risk-high-soft text-risk-high border border-risk-high/25",
  },
  HIGH: {
    label: "Cao",
    badgeClass: "bg-risk-high-soft text-risk-high border border-risk-high/20",
  },
  MEDIUM: {
    label: "Trung bình",
    badgeClass: "bg-risk-mid-soft text-risk-mid border border-risk-mid/25",
  },
  INFO: {
    label: "Thông tin",
    badgeClass: "bg-surface-muted text-ink-muted border border-border",
  },
};

export function sanitizeAlertTitleText(rawTitle: string): string {
  return rawTitle
    .replace(/\bRF_(?:0[1-9]|10)(?:_[A-Z0-9_]+)?\b/g, "quy tắc kiểm tra kế toán")
    .trim();
}
