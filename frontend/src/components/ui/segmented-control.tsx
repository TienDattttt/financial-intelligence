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
  semantics = "tabs",
  idPrefix,
  controlsPanelId,
  className,
}: {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  size?: "xs" | "sm" | "md";
  variant?: "pill" | "underline";
  semantics?: "tabs" | "radiogroup";
  idPrefix?: string;
  controlsPanelId?: (value: T) => string;
  className?: string;
}) {
  const itemRefs = React.useRef<(HTMLButtonElement | null)[]>([]);
  const activeIndex = Math.max(
    0,
    options.findIndex((opt) => opt.value === value),
  );

  const moveFocusAndSelect = (nextIdx: number) => {
    const target = options[nextIdx];
    if (!target) return;
    onChange(target.value);
    const el = itemRefs.current[nextIdx];
    el?.focus();
  };

  const handleKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    idx: number,
  ) => {
    if (options.length === 0) return;
    let nextIdx: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIdx = (idx + 1) % options.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIdx = (idx - 1 + options.length) % options.length;
    } else if (event.key === "Home") {
      nextIdx = 0;
    } else if (event.key === "End") {
      nextIdx = options.length - 1;
    }
    if (nextIdx !== null) {
      event.preventDefault();
      moveFocusAndSelect(nextIdx);
    }
  };

  const isRadio = semantics === "radiogroup";
  const groupRole = isRadio ? "radiogroup" : "tablist";
  const itemRole = isRadio ? "radio" : "tab";

  if (variant === "underline") {
    return (
      <div
        role={groupRole}
        aria-label={ariaLabel}
        className={cn(
          "flex items-center gap-0.5 overflow-x-auto border-b border-border",
          className,
        )}
      >
        {options.map((opt, idx) => {
          const active = opt.value === value;
          const tabIdx = idx === activeIndex ? 0 : -1;
          return (
            <button
              key={opt.value}
              ref={(el) => {
                itemRefs.current[idx] = el;
              }}
              id={idPrefix ? `${idPrefix}-tab-${opt.value}` : undefined}
              type="button"
              role={itemRole}
              aria-selected={isRadio ? undefined : active}
              aria-checked={isRadio ? active : undefined}
              aria-controls={
                !isRadio && controlsPanelId ? controlsPanelId(opt.value) : undefined
              }
              aria-label={opt.ariaLabel}
              tabIndex={tabIdx}
              onClick={() => onChange(opt.value)}
              onKeyDown={(ev) => handleKeyDown(ev, idx)}
              className={cn(
                "relative inline-flex min-h-6 min-w-6 shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
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
      ? "min-h-6 min-w-6 px-2 py-1 text-[11px]"
      : size === "md"
        ? "min-h-7 min-w-7 px-3 py-1.5 text-[13px]"
        : "min-h-6 min-w-6 px-2.5 py-1 text-[12px]";

  return (
    <div
      role={groupRole}
      aria-label={ariaLabel}
      className={cn(
        "inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-md border border-border bg-surface-muted p-0.5",
        className,
      )}
    >
      {options.map((opt, idx) => {
        const active = opt.value === value;
        const tabIdx = idx === activeIndex ? 0 : -1;
        return (
          <button
            key={opt.value}
            ref={(el) => {
              itemRefs.current[idx] = el;
            }}
            id={idPrefix ? `${idPrefix}-tab-${opt.value}` : undefined}
            type="button"
            role={itemRole}
            aria-selected={isRadio ? undefined : active}
            aria-checked={isRadio ? active : undefined}
            aria-controls={
              !isRadio && controlsPanelId ? controlsPanelId(opt.value) : undefined
            }
            aria-label={opt.ariaLabel}
            tabIndex={tabIdx}
            onClick={() => onChange(opt.value)}
            onKeyDown={(ev) => handleKeyDown(ev, idx)}
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
