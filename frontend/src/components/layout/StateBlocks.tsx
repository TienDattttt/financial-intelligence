"use client";

import * as React from "react";
import { AlertCircle, FileSearch, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";

export function PageSkeleton() {
  return (
    <div
      role="status"
      className="space-y-4"
      aria-busy="true"
      aria-label="Đang tải dữ liệu"
    >
      <div className="grid grid-cols-1 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-2 p-4">
            <Skeleton className="h-3.5 w-28" />
            <Skeleton className="h-7 w-20" />
            <Skeleton className="h-3 w-36" />
          </div>
        ))}
      </div>
      <Card>
        <CardContent className="space-y-3 p-4">
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-56 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div
      role="status"
      className="space-y-2 p-4"
      aria-busy="true"
      aria-label="Đang tải bảng"
    >
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border-strong bg-surface/60">
      <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
        <FileSearch className="size-7 text-ink-subtle" aria-hidden="true" />
        <p className="text-[15px] font-semibold text-ink">{title}</p>
        <p className="max-w-md text-[13px] text-ink-muted">{description}</p>
        {action ? <div className="mt-2">{action}</div> : null}
      </div>
    </div>
  );
}

export function ErrorState({
  title = "Không thể tải dữ liệu",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-risk-high/30 bg-risk-high-soft/50"
    >
      <div className="flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center">
        <div className="flex items-start gap-2.5">
          <AlertCircle
            className="mt-0.5 size-4 shrink-0 text-risk-high"
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-semibold text-risk-high">{title}</p>
            <p className="text-[13px] text-ink-muted">{message}</p>
          </div>
        </div>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="size-3.5" />
            Thử lại
          </Button>
        ) : null}
      </div>
    </div>
  );
}
