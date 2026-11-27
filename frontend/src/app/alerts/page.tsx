"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Bell,
  Check,
  CheckCheck,
  Mail,
  Send,
  ShieldAlert,
  Webhook,
} from "lucide-react";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Input, Select } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  useAlertNotifications,
  useAlertSubscription,
  useMarkAllNotificationsReadMutation,
  useMarkNotificationReadMutation,
  useTestAlertWebhookMutation,
  useUpdateAlertSubscriptionMutation,
} from "@/hooks/use-alerts";
import { useAuthSession, useServerWatchlists } from "@/hooks/use-auth-workspace";
import {
  ALERT_EVENT_TYPE_META,
  ALERT_SEVERITY_LABELS,
  sanitizeAlertTitleText,
} from "@/lib/alerts-api";
import { ApiError } from "@/lib/api-client";
import { COMPLIANCE_DISCLAIMER_VI } from "@/lib/compliance";
import { formatDateVi } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AlertEventType, AlertSeverity } from "@/types/api";

type AlertsTab = "inbox" | "channels";
type InboxFilter = "all" | "unread";

export default function AlertsCenterPage() {
  const [activeTab, setActiveTab] = React.useState<AlertsTab>("inbox");
  const [inboxFilter, setInboxFilter] = React.useState<InboxFilter>("all");

  const authSession = useAuthSession();
  const isAuthenticated = authSession.data?.authenticated === true;
  const watchlistsQuery = useServerWatchlists(isAuthenticated);
  const notificationsQuery = useAlertNotifications(isAuthenticated, {
    unreadOnly: inboxFilter === "unread",
    limit: 50,
    offset: 0,
  });
  const subscriptionQuery = useAlertSubscription(isAuthenticated);

  const markReadMutation = useMarkNotificationReadMutation();
  const markAllMutation = useMarkAllNotificationsReadMutation();
  const updateSubMutation = useUpdateAlertSubscriptionMutation();
  const testWebhookMutation = useTestAlertWebhookMutation();

  const [draft, setDraft] = React.useState<{
    watchlistId?: string;
    eventTypes?: AlertEventType[];
    inAppEnabled?: boolean;
    emailDigestEnabled?: boolean;
    emailFrequency?: "DAILY_DIGEST" | "INSTANT";
    telegramEnabled?: boolean;
    telegramChatId?: string;
    webhookEnabled?: boolean;
    webhookUrl?: string;
    webhookSecret?: string;
    mertonThreshold?: string;
  }>({});
  const [saveFeedback, setSaveFeedback] = React.useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);
  const [webhookTestFeedback, setWebhookTestFeedback] = React.useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);

  const subData = subscriptionQuery.data;
  const watchlistId = draft.watchlistId ?? subData?.watchlist_id ?? "";
  const eventTypes = draft.eventTypes ?? subData?.event_types ?? [
    "NEW_FILING_PUBLISHED",
    "RISK_TIER_UPGRADE",
    "NEW_RED_FLAG_TRIGGERED",
    "REGULATORY_EVENT_ISSUED",
    "MERTON_DTD_BREACH",
  ];
  const inAppEnabled = draft.inAppEnabled ?? subData?.in_app_enabled ?? true;
  const emailDigestEnabled =
    draft.emailDigestEnabled ?? subData?.email_digest_enabled ?? false;
  const emailFrequency =
    draft.emailFrequency ?? subData?.email_frequency ?? "DAILY_DIGEST";
  const telegramEnabled =
    draft.telegramEnabled ?? subData?.telegram_enabled ?? false;
  const telegramChatId =
    draft.telegramChatId ?? subData?.telegram_chat_id ?? "";
  const webhookEnabled =
    draft.webhookEnabled ?? subData?.webhook_enabled ?? false;
  const webhookUrl = draft.webhookUrl ?? subData?.webhook_url ?? "";
  const webhookSecret = draft.webhookSecret ?? "";
  const mertonThreshold =
    draft.mertonThreshold ??
    (subData ? String(subData.merton_dtd_threshold) : "1.5");

  const toggleEventType = (evType: AlertEventType) => {
    const next = eventTypes.includes(evType)
      ? eventTypes.filter((item) => item !== evType)
      : [...eventTypes, evType];
    setDraft((prev) => ({ ...prev, eventTypes: next }));
  };

  const handleSaveSubscription = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaveFeedback(null);
    const parsedThreshold = Number(mertonThreshold);
    if (!Number.isFinite(parsedThreshold) || parsedThreshold < 0 || parsedThreshold > 10) {
      setSaveFeedback({
        kind: "error",
        text: "Ngưỡng Merton DTD phải nằm trong khoảng 0,0 đến 10,0.",
      });
      return;
    }
    try {
      await updateSubMutation.mutateAsync({
        watchlist_id: watchlistId || null,
        event_types: eventTypes,
        in_app_enabled: inAppEnabled,
        email_digest_enabled: emailDigestEnabled,
        email_frequency: emailFrequency,
        telegram_enabled: telegramEnabled,
        telegram_chat_id: telegramChatId.trim() || null,
        webhook_enabled: webhookEnabled,
        webhook_url: webhookUrl.trim() || null,
        webhook_secret_hmac: webhookSecret.trim() || null,
        merton_dtd_threshold: parsedThreshold,
        is_active: true,
      });
      setDraft({});
      setSaveFeedback({
        kind: "success",
        text: "Đã lưu cấu hình kênh cảnh báo sớm.",
      });
    } catch (err) {
      setSaveFeedback({
        kind: "error",
        text:
          err instanceof ApiError
            ? err.message
            : "Không thể lưu cấu hình cảnh báo.",
      });
    }
  };

  const handleTestWebhook = async () => {
    setWebhookTestFeedback(null);
    try {
      const res = await testWebhookMutation.mutateAsync({
        webhook_url: webhookUrl.trim() || null,
        webhook_secret_hmac: webhookSecret.trim() || null,
      });
      setWebhookTestFeedback({
        kind: res.delivered ? "success" : "error",
        text: res.detail,
      });
    } catch (err) {
      setWebhookTestFeedback({
        kind: "error",
        text:
          err instanceof ApiError
            ? err.message
            : "Kiểm tra Webhook không thành công.",
      });
    }
  };

  if (authSession.isLoading) {
    return <PageSkeleton />;
  }

  if (!isAuthenticated) {
    return (
      <div className="space-y-4">
        <div className="border-b border-border pb-3">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Trung tâm Cảnh báo Sớm
          </h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Giám sát tự động biến động BCTC, nhóm cảnh báo rủi ro, cờ đỏ kế toán và sự kiện Sở GDCK cho danh mục theo dõi.
          </p>
        </div>
        <EmptyState
          title="Đăng nhập để kích hoạt Cảnh báo Sớm"
          description="Cảnh báo In-App, Telegram Bot miễn phí, Email tổng hợp hàng ngày và Webhook bảo mật được gắn riêng với tài khoản và danh mục theo dõi của bạn."
          actions={
            <>
              <Link href="/auth/login" className={buttonVariants({ size: "sm" })}>
                Đăng nhập
              </Link>
              <Link
                href="/auth/register"
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                Tạo tài khoản mới
              </Link>
            </>
          }
        />
        <p className="text-[11px] text-ink-subtle">{COMPLIANCE_DISCLAIMER_VI}</p>
      </div>
    );
  }

  const notifications = notificationsQuery.data?.items ?? [];
  const unreadCount = notificationsQuery.data?.unread_count ?? 0;
  const sub = subscriptionQuery.data;
  const hasConsent = sub?.has_alert_consent ?? true;

  return (
    <div className="space-y-4">
      <div className="flex flex-col justify-between gap-3 border-b border-border pb-3 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Trung tâm Cảnh báo Sớm
          </h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Phát hiện biến động định lượng giữa các kỳ dữ liệu cho doanh nghiệp trong danh mục theo dõi.
          </p>
        </div>
        <SegmentedControl
          ariaLabel="Chế độ xem cảnh báo"
          value={activeTab}
          onChange={setActiveTab}
          variant="pill"
          options={[
            {
              value: "inbox",
              label: "Hộp thư cảnh báo",
              badge: unreadCount > 0 ? (
                <span className="num rounded-sm bg-risk-high-soft px-1.5 py-0.5 text-[11px] font-semibold text-risk-high">
                  {unreadCount}
                </span>
              ) : undefined,
            },
            { value: "channels", label: "Cấu hình kênh nhận" },
          ]}
        />
      </div>

      {!hasConsent ? (
        <div
          role="status"
          className="flex flex-col justify-between gap-2 rounded-lg border border-risk-mid/35 bg-risk-mid-soft/60 px-4 py-3 text-xs text-ink sm:flex-row sm:items-center"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 size-4 shrink-0 text-risk-mid"
              aria-hidden="true"
            />
            <div>
              <p className="font-semibold text-ink">
                Chưa bật đồng ý nhận thông báo cảnh báo (Luật 91/2025/QH15)
              </p>
              <p className="text-ink-muted">
                Hệ thống chỉ gửi thông báo qua In-App, Telegram, Email hoặc Webhook sau khi bạn cấp quyền đồng ý xử lý dữ liệu thông báo tại trang Hồ sơ.
              </p>
            </div>
          </div>
          <Link
            href="/profile"
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            Bật đồng ý tại Hồ sơ
          </Link>
        </div>
      ) : null}

      {activeTab === "inbox" ? (
        <Card>
          <CardHeader className="flex flex-col gap-2 border-b border-border sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <Bell className="size-4 text-accent-ink" aria-hidden="true" />
              <CardTitle className="text-[15px]">
                Danh sách thông báo ({notificationsQuery.data?.total ?? 0})
              </CardTitle>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <SegmentedControl
                ariaLabel="Lọc trạng thái đọc"
                value={inboxFilter}
                onChange={setInboxFilter}
                size="xs"
                options={[
                  { value: "all", label: "Tất cả" },
                  { value: "unread", label: `Chưa đọc (${unreadCount})` },
                ]}
              />
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={unreadCount === 0 || markAllMutation.isPending}
                onClick={() => markAllMutation.mutate()}
              >
                <CheckCheck className="size-3.5" aria-hidden="true" />
                Đánh dấu đọc tất cả
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {notificationsQuery.isLoading ? (
              <div className="p-4">
                <PageSkeleton />
              </div>
            ) : notificationsQuery.isError ? (
              <div className="p-4">
                <ErrorState
                  message={
                    notificationsQuery.error instanceof ApiError
                      ? notificationsQuery.error.message
                      : "Không thể tải danh sách cảnh báo."
                  }
                  onRetry={() => notificationsQuery.refetch()}
                />
              </div>
            ) : notifications.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  title={
                    inboxFilter === "unread"
                      ? "Không còn thông báo chưa đọc"
                      : "Chưa ghi nhận cảnh báo nào"
                  }
                  description="Khi doanh nghiệp trong danh mục theo dõi phát sinh BCTC mới, cờ đỏ kế toán, thay đổi nhóm cảnh báo rủi ro hoặc sự kiện Sở GDCK, hệ thống sẽ hiển thị tại đây."
                  action={
                    <Link
                      href="/watchlist"
                      className={buttonVariants({ variant: "outline", size: "sm" })}
                    >
                      Quản lý danh mục theo dõi
                    </Link>
                  }
                />
              </div>
            ) : (
              <ul className="divide-y divide-border" aria-label="Hộp thư cảnh báo">
                {notifications.map((item) => {
                  const sevKey = (
                    item.severity in ALERT_SEVERITY_LABELS
                      ? item.severity
                      : "INFO"
                  ) as AlertSeverity;
                  const sevMeta = ALERT_SEVERITY_LABELS[sevKey];
                  return (
                    <li
                      key={item.notification_id}
                      className={cn(
                        "flex flex-col justify-between gap-3 px-4 py-3 sm:flex-row sm:items-center",
                        item.is_read ? "bg-surface" : "bg-canvas/70",
                      )}
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge className={sevMeta.badgeClass}>
                            {sevMeta.label}
                          </Badge>
                          <Link
                            href={item.company_url || `/companies/${item.ticker}`}
                            className="font-mono text-xs font-semibold text-accent-ink hover:underline"
                          >
                            {item.ticker}
                          </Link>
                          <span className="num text-xs text-ink-subtle">
                            {formatDateVi(item.created_at)}
                          </span>
                          {!item.is_read ? (
                            <span className="rounded-sm bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent-ink">
                              Mới
                            </span>
                          ) : null}
                        </div>
                        <p
                          className={cn(
                            "text-[13px]",
                            item.is_read
                              ? "text-ink-muted"
                              : "font-medium text-ink",
                          )}
                        >
                          {sanitizeAlertTitleText(item.title_vi)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Link
                          href={item.company_url || `/companies/${item.ticker}`}
                          className={buttonVariants({ variant: "outline", size: "xs" })}
                        >
                          Xem hồ sơ
                        </Link>
                        {!item.is_read ? (
                          <Button
                            type="button"
                            variant="secondary"
                            size="xs"
                            disabled={markReadMutation.isPending}
                            onClick={() =>
                              markReadMutation.mutate(item.notification_id)
                            }
                          >
                            <Check className="size-3.5" aria-hidden="true" />
                            Đã đọc
                          </Button>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : (
        <form onSubmit={handleSaveSubscription} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="border-b border-border">
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <ShieldAlert
                    className="size-4 text-accent-ink"
                    aria-hidden="true"
                  />
                  Phạm vi danh mục &amp; Sự kiện kích hoạt
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 p-4">
                <label className="block space-y-1 text-xs">
                  <span className="font-medium text-ink">Danh mục áp dụng</span>
                  <Select
                    aria-label="Chọn danh mục theo dõi nhận cảnh báo"
                    value={watchlistId}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, watchlistId: e.target.value }))
                    }
                    className="w-full"
                  >
                    <option value="">Tất cả danh mục theo dõi</option>
                    {(watchlistsQuery.data?.watchlists ?? []).map((wl) => (
                      <option key={wl.watchlist_id} value={wl.watchlist_id}>
                        {wl.name} ({wl.items.length} mã)
                      </option>
                    ))}
                  </Select>
                </label>

                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium text-ink">
                    Loại sự kiện theo dõi
                  </legend>
                  <div className="space-y-2">
                    {ALERT_EVENT_TYPE_META.map((meta) => {
                      const checked = eventTypes.includes(meta.type);
                      return (
                        <label
                          key={meta.type}
                          className="flex items-start gap-2.5 rounded-md border border-border bg-canvas/50 p-2.5 text-xs hover:bg-surface-hover/50"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleEventType(meta.type)}
                            className="mt-0.5 size-4 rounded border-border-strong accent-primary"
                          />
                          <span>
                            <span className="block font-medium text-ink">
                              {meta.label}
                            </span>
                            <span className="block text-ink-muted">
                              {meta.description}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>

                <label className="block space-y-1 text-xs">
                  <span className="font-medium text-ink">
                    Ngưỡng cảnh báo Merton DTD (mặc định 1,5)
                  </span>
                  <Input
                    type="number"
                    step="0.1"
                    min="0"
                    max="10"
                    aria-label="Ngưỡng cảnh báo Merton DTD"
                    value={mertonThreshold}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        mertonThreshold: e.target.value,
                      }))
                    }
                    className="num max-w-40"
                  />
                </label>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="border-b border-border">
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <Send className="size-4 text-accent-ink" aria-hidden="true" />
                  Kênh phân phối thông báo
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 p-4">
                <label className="flex items-center justify-between gap-3 rounded-md border border-border p-3 text-xs">
                  <div>
                    <span className="block font-semibold text-ink">
                      Thông báo trong ứng dụng (In-App)
                    </span>
                    <span className="text-ink-muted">
                      Hiển thị ngay tại biểu tượng chuông và trang /alerts.
                    </span>
                  </div>
                  <input
                    type="checkbox"
                    aria-label="Bật thông báo trong ứng dụng"
                    checked={inAppEnabled}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        inAppEnabled: e.target.checked,
                      }))
                    }
                    className="size-4 accent-primary"
                  />
                </label>

                <div className="space-y-2 rounded-md border border-border p-3 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <span className="block font-semibold text-ink">
                        Telegram Bot (Ưu tiên tức thời · Miễn phí)
                      </span>
                      <span className="text-ink-muted">
                        Nhận cảnh báo tức thời qua bot Telegram không giới hạn lượt gửi.
                      </span>
                    </div>
                    <input
                      type="checkbox"
                      aria-label="Bật kênh Telegram Bot"
                      checked={telegramEnabled}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          telegramEnabled: e.target.checked,
                        }))
                      }
                      className="size-4 accent-primary"
                    />
                  </div>
                  {telegramEnabled ? (
                    <label className="block space-y-1 pt-1">
                      <span className="font-medium text-ink-muted">
                        Telegram Chat ID
                      </span>
                      <Input
                        type="text"
                        placeholder="Ví dụ: 99887766"
                        aria-label="Telegram Chat ID"
                        value={telegramChatId}
                        onChange={(e) =>
                          setDraft((prev) => ({
                            ...prev,
                            telegramChatId: e.target.value,
                          }))
                        }
                      />
                    </label>
                  ) : null}
                </div>

                <div className="space-y-2 rounded-md border border-border p-3 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-start gap-2">
                      <Mail
                        className="mt-0.5 size-4 shrink-0 text-ink-muted"
                        aria-hidden="true"
                      />
                      <div>
                        <span className="block font-semibold text-ink">
                          Email tổng hợp (Resend Free Digest)
                        </span>
                        <span className="text-ink-muted">
                          Gộp 1 bản tin cuối ngày để tiết kiệm hạn mức gửi miễn phí.
                        </span>
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      aria-label="Bật kênh Email"
                      checked={emailDigestEnabled}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          emailDigestEnabled: e.target.checked,
                        }))
                      }
                      className="size-4 accent-primary"
                    />
                  </div>
                  {emailDigestEnabled ? (
                    <label className="block space-y-1 pt-1">
                      <span className="font-medium text-ink-muted">Tần suất gửi</span>
                      <Select
                        aria-label="Tần suất gửi Email"
                        value={emailFrequency}
                        onChange={(e) =>
                          setDraft((prev) => ({
                            ...prev,
                            emailFrequency:
                              e.target.value === "INSTANT"
                                ? "INSTANT"
                                : "DAILY_DIGEST",
                          }))
                        }
                        className="w-full"
                      >
                        <option value="DAILY_DIGEST">
                          Tổng hợp hàng ngày (Khuyên dùng)
                        </option>
                        <option value="INSTANT">Gửi ngay khi phát sinh</option>
                      </Select>
                    </label>
                  ) : null}
                </div>

                <div className="space-y-2.5 rounded-md border border-border p-3 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-start gap-2">
                      <Webhook
                        className="mt-0.5 size-4 shrink-0 text-ink-muted"
                        aria-hidden="true"
                      />
                      <div>
                        <span className="block font-semibold text-ink">
                          Outgoing Webhook (HTTPS + HMAC-SHA256)
                        </span>
                        <span className="text-ink-muted">
                          Chỉ chấp nhận HTTPS cổng 443; chặn IP nội bộ và chuyển hướng 3xx.
                        </span>
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      aria-label="Bật Outgoing Webhook"
                      checked={webhookEnabled}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          webhookEnabled: e.target.checked,
                        }))
                      }
                      className="size-4 accent-primary"
                    />
                  </div>
                  {webhookEnabled || webhookUrl ? (
                    <div className="space-y-2 pt-1">
                      <label className="block space-y-1">
                        <span className="font-medium text-ink-muted">
                          Webhook Endpoint URL (HTTPS)
                        </span>
                        <Input
                          type="url"
                          placeholder="https://hooks.example.com/finintel"
                          aria-label="Webhook URL"
                          value={webhookUrl}
                          onChange={(e) =>
                            setDraft((prev) => ({
                              ...prev,
                              webhookUrl: e.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="block space-y-1">
                        <span className="font-medium text-ink-muted">
                          HMAC Secret{" "}
                          {sub?.webhook_secret_configured
                            ? "(đã cấu hình — để trống nếu giữ nguyên)"
                            : "(tùy chọn)"}
                        </span>
                        <Input
                          type="password"
                          autoComplete="new-password"
                          placeholder="Nhập khóa ký X-FinIntel-Signature"
                          aria-label="Webhook HMAC Secret"
                          value={webhookSecret}
                          onChange={(e) =>
                            setDraft((prev) => ({
                              ...prev,
                              webhookSecret: e.target.value,
                            }))
                          }
                        />
                      </label>
                      <div className="flex flex-wrap items-center gap-2 pt-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          disabled={testWebhookMutation.isPending || !webhookUrl.trim()}
                          onClick={handleTestWebhook}
                        >
                          Gửi thử Webhook
                        </Button>
                        {webhookTestFeedback ? (
                          <span
                            role="status"
                            className={cn(
                              "text-xs",
                              webhookTestFeedback.kind === "success"
                                ? "text-risk-low"
                                : "text-risk-high",
                            )}
                          >
                            {webhookTestFeedback.text}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface px-4 py-3">
            <div className="text-xs">
              {saveFeedback ? (
                <span
                  role="status"
                  className={cn(
                    "font-medium",
                    saveFeedback.kind === "success"
                      ? "text-risk-low"
                      : "text-risk-high",
                  )}
                >
                  {saveFeedback.text}
                </span>
              ) : (
                <span className="text-ink-muted">
                  Mọi thông báo được chống trùng lặp theo mã chứng khoán, kỳ báo cáo và loại sự kiện.
                </span>
              )}
            </div>
            <Button type="submit" size="sm" disabled={updateSubMutation.isPending}>
              {updateSubMutation.isPending ? "Đang lưu..." : "Lưu cấu hình cảnh báo"}
            </Button>
          </div>
        </form>
      )}

      <p className="text-[11px] text-ink-subtle">{COMPLIANCE_DISCLAIMER_VI}</p>
    </div>
  );
}
