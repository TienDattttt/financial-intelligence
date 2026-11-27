"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, Clock, ShieldCheck } from "lucide-react";
import { Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { MyQuotaResponse, QuotaMeterStatus } from "@/types/api";

export type QuotaMeterName = QuotaMeterStatus["meter"];

export interface RagQuotaGateState {
  isExhausted: boolean;
  isCircuitOpen: boolean;
  missingConsent: boolean;
  blocked: boolean;
  remaining: number | null;
  limit: number | null;
  used: number;
  unlimited: boolean;
  resetLabel: string;
}

export function formatQuotaResetTimeVi(
  isoTimestamp: string | null | undefined,
): string {
  if (!isoTimestamp) return "00:00 (UTC+7)";
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) return "00:00 (UTC+7)";
  const hhmm = new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${hhmm} (UTC+7)`;
}

export function deriveRagQuotaGateState(
  quota: MyQuotaResponse | null | undefined,
  meter: QuotaMeterName = "rag_query",
): RagQuotaGateState {
  if (!quota) {
    return {
      isExhausted: false,
      isCircuitOpen: false,
      missingConsent: false,
      blocked: false,
      remaining: null,
      limit: null,
      used: 0,
      unlimited: false,
      resetLabel: "00:00 (UTC+7)",
    };
  }
  const resetLabel = formatQuotaResetTimeVi(
    quota.circuit_breaker_reopen_at ?? quota.reset_at,
  );
  const meterItem = quota.meters.find((m) => m.meter === meter);
  const unlimited = Boolean(meterItem?.unlimited);
  const limit = meterItem?.limit ?? null;
  const remaining = meterItem?.remaining ?? null;
  const used = meterItem?.used ?? 0;
  const isCircuitOpen = Boolean(quota.circuit_breaker_open);
  const isExhausted =
    quota.authenticated && !unlimited && remaining !== null && remaining <= 0;
  const missingConsent =
    quota.authenticated && !quota.cross_border_rag_consent;

  return {
    isExhausted,
    isCircuitOpen,
    missingConsent,
    blocked: isCircuitOpen || isExhausted || missingConsent,
    remaining,
    limit,
    used,
    unlimited,
    resetLabel,
  };
}

export interface QuotaBadgeProps {
  quota: MyQuotaResponse | null | undefined;
  isLoading?: boolean;
  meter?: QuotaMeterName;
  className?: string;
}

export function QuotaBadge({
  quota,
  isLoading = false,
  meter = "rag_query",
  className,
}: QuotaBadgeProps) {
  if (isLoading) {
    return (
      <Skeleton
        aria-label="Đang kiểm tra hạn mức tra cứu hôm nay"
        className={cn("h-7 w-64 rounded-md", className)}
      />
    );
  }

  if (!quota) {
    return null;
  }

  const gate = deriveRagQuotaGateState(quota, meter);

  if (gate.isCircuitOpen) {
    return (
      <div
        role="status"
        aria-live="polite"
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-md border border-risk-high/35 bg-risk-high-soft px-2.5 py-1.5 text-[11px] font-medium text-risk-high",
          className,
        )}
      >
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        <span>
          Hệ thống tạm giới hạn truy vấn mới để bảo vệ ngân sách miễn phí · Mở lại lúc{" "}
          <span className="num font-semibold">{gate.resetLabel}</span>
        </span>
      </div>
    );
  }

  if (gate.missingConsent) {
    return (
      <div
        role="status"
        aria-live="polite"
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-md border border-risk-mid/35 bg-risk-mid-soft/50 px-2.5 py-1.5 text-[11px] text-risk-mid",
          className,
        )}
      >
        <ShieldCheck className="size-3.5 shrink-0" aria-hidden="true" />
        <span>
          Chưa bật đồng ý đối chiếu thuyết minh (Luật 91/2025).{" "}
          <Link
            href="/profile"
            className="font-semibold text-ink underline underline-offset-2 hover:text-accent-ink"
          >
            Bật trong Hồ sơ
          </Link>
        </span>
      </div>
    );
  }

  if (!quota.authenticated) {
    return (
      <div
        role="status"
        className={cn(
          "inline-flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-surface-muted px-2.5 py-1 text-[11px] text-ink-muted",
          className,
        )}
      >
        <Clock className="size-3 shrink-0 text-ink-subtle" aria-hidden="true" />
        <span>
          Chế độ khách ·{" "}
          <Link
            href="/auth/login"
            className="font-medium text-accent-ink underline underline-offset-2"
          >
            Đăng nhập
          </Link>{" "}
          để dùng <span className="num font-semibold text-ink">15</span> lượt tra cứu/ngày
        </span>
      </div>
    );
  }

  if (gate.unlimited) {
    return (
      <div
        role="status"
        className={cn(
          "inline-flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-surface-muted px-2.5 py-1 text-[11px] text-ink-muted",
          className,
        )}
      >
        <Clock className="size-3 shrink-0 text-accent-ink" aria-hidden="true" />
        <span>
          Hạn mức cá nhân: <strong className="font-semibold text-ink">Không giới hạn</strong> · Làm mới lúc{" "}
          <span className="num">{gate.resetLabel}</span>
        </span>
      </div>
    );
  }

  if (gate.isExhausted) {
    return (
      <div
        role="status"
        aria-live="polite"
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-md border border-risk-mid/40 bg-risk-mid-soft/50 px-2.5 py-1.5 text-[11px] font-medium text-risk-mid",
          className,
        )}
      >
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        <span>
          Đã dùng hết{" "}
          <span className="num font-semibold">
            {gate.used}/{gate.limit ?? 0}
          </span>{" "}
          lượt miễn phí hôm nay · Làm mới lúc{" "}
          <span className="num font-semibold">{gate.resetLabel}</span>
        </span>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-surface-muted px-2.5 py-1 text-[11px] text-ink-muted",
        className,
      )}
    >
      <Clock className="size-3 shrink-0 text-accent-ink" aria-hidden="true" />
      <span>
        Còn{" "}
        <strong className="num font-semibold text-ink">
          {gate.remaining ?? 0}/{gate.limit ?? 0}
        </strong>{" "}
        lượt tra cứu miễn phí hôm nay · Làm mới lúc{" "}
        <span className="num">{gate.resetLabel}</span>
      </span>
    </div>
  );
}
