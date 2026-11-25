"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: React.ReactNode;
  ariaLabel?: string;
  badge?: React.ReactNode;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = "sm",
  variant = "pill",
  className,
}: {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  size?: "xs" | "sm" | "md";
  variant?: "pill" | "underline";
  className?: string;
}) {
  if (variant === "underline") {
    return (
      <div
        role="tablist"
        aria-label={ariaLabel}
        className={cn(
          "flex items-center gap-0.5 overflow-x-auto border-b border-border",
          className,
        )}
      >
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="tab"
              aria-selected={active}
              aria-label={opt.ariaLabel}
              onClick={() => onChange(opt.value)}
              className={cn(
                "relative inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                active
                  ? "text-ink after:absolute after:inset-x-0 after:bottom-0 after:h-[2px] after:bg-accent-ink"
                  : "text-ink-muted hover:text-ink",
              )}
            >
              <span>{opt.label}</span>
              {opt.badge}
            </button>
          );
        })}
      </div>
    );
  }

  const sizeCls =
    size === "xs"
      ? "px-2 py-0.5 text-[11px]"
      : size === "md"
        ? "px-3 py-1.5 text-[13px]"
        : "px-2.5 py-1 text-[12px]";

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-muted p-0.5",
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={opt.ariaLabel}
            onClick={() => onChange(opt.value)}
            className={cn(
              "inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              sizeCls,
              active
                ? "bg-primary text-primary-foreground shadow-xs"
                : "text-ink-muted hover:bg-surface hover:text-ink",
            )}
          >
            <span>{opt.label}</span>
            {opt.badge}
          </button>
        );
      })}
    </div>
  );
}
