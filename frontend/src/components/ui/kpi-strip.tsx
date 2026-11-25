import * as React from "react";
import { cn } from "@/lib/utils";

export function KpiStrip({
  ariaLabel,
  columns = 4,
  className,
  children,
}: {
  ariaLabel: string;
  columns?: 3 | 4 | 5;
  className?: string;
  children: React.ReactNode;
}) {
  const colsClass =
    columns === 3
      ? "sm:grid-cols-3"
      : columns === 5
        ? "sm:grid-cols-2 lg:grid-cols-5"
        : "sm:grid-cols-2 lg:grid-cols-4";

  return (
    <section
      aria-label={ariaLabel}
      className={cn(
        "grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border",
        colsClass,
        className,
      )}
    >
      {children}
    </section>
  );
}

export function KpiCell({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("flex flex-col justify-between gap-1 bg-surface px-4 py-3", className)}>
      {children}
    </div>
  );
}
