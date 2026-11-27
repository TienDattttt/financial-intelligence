"use client";

import * as React from "react";
import Link from "next/link";
import { Bell, Check, CheckCheck, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import {
  useAlertNotifications,
  useMarkAllNotificationsReadMutation,
  useMarkNotificationReadMutation,
} from "@/hooks/use-alerts";
import {
  ALERT_SEVERITY_LABELS,
  sanitizeAlertTitleText,
} from "@/lib/alerts-api";
import { formatDateVi } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AlertSeverity } from "@/types/api";

export function NotificationBell({
  isAuthenticated,
}: {
  isAuthenticated: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);
  const popoverId = "alerts-bell-popover";

  const notificationsQuery = useAlertNotifications(isAuthenticated, {
    unreadOnly: false,
    limit: 8,
    offset: 0,
  });
  const markReadMutation = useMarkNotificationReadMutation();
  const markAllMutation = useMarkAllNotificationsReadMutation();

  const unreadCount = notificationsQuery.data?.unread_count ?? 0;
  const items = notificationsQuery.data?.items ?? [];

  React.useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (
        containerRef.current &&
        target instanceof Node &&
        !containerRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const ariaLabel =
    isAuthenticated && unreadCount > 0
      ? `Thông báo cảnh báo (${unreadCount} chưa đọc)`
      : "Thông báo cảnh báo";

  return (
    <div ref={containerRef} className="relative inline-flex shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={popoverId}
        onClick={() => setOpen((prev) => !prev)}
        className="relative inline-flex size-8 items-center justify-center rounded-md border border-border bg-surface text-ink-muted transition-colors duration-150 hover:bg-surface-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Bell className="size-4" aria-hidden="true" />
        {isAuthenticated && unreadCount > 0 ? (
          <span
            data-testid="notification-unread-badge"
            className="num absolute -right-1 -top-1 inline-flex min-w-4 items-center justify-center rounded-full bg-risk-high px-1 text-[10px] font-semibold leading-4 text-white"
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          id={popoverId}
          role="dialog"
          aria-label="Hộp thư cảnh báo nhanh"
          className="absolute right-0 top-10 z-50 w-[min(92vw,360px)] rounded-lg border border-border-strong bg-surface text-ink shadow-popover outline-none"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold text-ink">
                Cảnh báo sớm
              </span>
              {isAuthenticated && unreadCount > 0 ? (
                <Badge className="num bg-risk-high-soft text-risk-high">
                  {unreadCount} chưa đọc
                </Badge>
              ) : null}
            </div>
            {isAuthenticated && unreadCount > 0 ? (
              <button
                type="button"
                disabled={markAllMutation.isPending}
                onClick={() => markAllMutation.mutate()}
                className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium text-accent-ink transition-colors hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                <CheckCheck className="size-3.5" aria-hidden="true" />
                Đọc tất cả
              </button>
            ) : null}
          </div>

          <div className="max-h-80 overflow-y-auto divide-y divide-border">
            {!isAuthenticated ? (
              <div className="space-y-2 p-4 text-center text-xs text-ink-muted">
                <p>
                  Đăng nhập để theo dõi cảnh báo biến động rủi ro và nhận thông báo
                  qua Telegram, Email hoặc Webhook.
                </p>
                <Link
                  href="/auth/login"
                  onClick={() => setOpen(false)}
                  className="inline-flex h-7 items-center justify-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Đăng nhập ngay
                </Link>
              </div>
            ) : notificationsQuery.isLoading ? (
              <div className="p-4 text-center text-xs text-ink-muted" role="status">
                Đang tải thông báo cảnh báo...
              </div>
            ) : items.length === 0 ? (
              <div className="p-4 text-center text-xs text-ink-muted">
                Chưa có cảnh báo mới cho danh mục theo dõi của bạn.
              </div>
            ) : (
              items.map((item) => {
                const sevKey = (
                  item.severity in ALERT_SEVERITY_LABELS
                    ? item.severity
                    : "INFO"
                ) as AlertSeverity;
                const sevMeta = ALERT_SEVERITY_LABELS[sevKey];
                return (
                  <div
                    key={item.notification_id}
                    className={cn(
                      "flex items-start justify-between gap-2 px-3 py-2.5 text-xs transition-colors",
                      item.is_read ? "bg-surface" : "bg-canvas/80",
                    )}
                  >
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge className={sevMeta.badgeClass}>
                          {sevMeta.label}
                        </Badge>
                        <Link
                          href={item.company_url || `/companies/${item.ticker}`}
                          onClick={() => setOpen(false)}
                          className="font-mono text-[11px] font-semibold text-accent-ink hover:underline"
                        >
                          {item.ticker}
                        </Link>
                        <span className="num text-[11px] text-ink-subtle">
                          {formatDateVi(item.created_at)}
                        </span>
                      </div>
                      <p
                        className={cn(
                          "leading-snug",
                          item.is_read
                            ? "text-ink-muted"
                            : "font-medium text-ink",
                        )}
                      >
                        {sanitizeAlertTitleText(item.title_vi)}
                      </p>
                    </div>
                    {!item.is_read ? (
                      <button
                        type="button"
                        aria-label={`Đánh dấu đã đọc cảnh báo ${item.ticker}`}
                        disabled={markReadMutation.isPending}
                        onClick={() =>
                          markReadMutation.mutate(item.notification_id)
                        }
                        className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <Check className="size-3.5" aria-hidden="true" />
                      </button>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>

          <div className="flex items-center justify-between border-t border-border bg-surface-muted/50 px-3 py-2">
            <Link
              href="/alerts"
              onClick={() => setOpen(false)}
              className="inline-flex items-center gap-1 text-xs font-medium text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span>Trung tâm cảnh báo &amp; Kênh nhận</span>
              <ExternalLink className="size-3" aria-hidden="true" />
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
