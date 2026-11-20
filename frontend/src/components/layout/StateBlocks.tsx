"use client";

import * as React from "react";
import { AlertCircle, FileSearch, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";

export function PageSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Đang tải dữ liệu">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="space-y-2 p-4">
              <Skeleton className="h-3.5 w-28" />
              <Skeleton className="h-7 w-20" />
              <Skeleton className="h-3 w-36" />
            </CardContent>
          </Card>
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
    <div className="space-y-2 p-4" aria-busy="true" aria-label="Đang tải bảng">
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
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center justify-center gap-2 py-10 text-center">
        <FileSearch className="size-8 text-ink-subtle" aria-hidden="true" />
        <p className="text-[15px] font-semibold text-ink">{title}</p>
        <p className="max-w-md text-[13px] text-ink-muted">{description}</p>
        {action ? <div className="mt-2">{action}</div> : null}
      </CardContent>
    </Card>
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
    <Card className="border-risk-high/30 bg-risk-high-soft/40">
      <CardContent className="flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center">
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
      </CardContent>
    </Card>
  );
}
