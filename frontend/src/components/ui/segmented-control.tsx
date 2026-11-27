"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: React.ReactNode;
  ariaLabel?: string;
  badge?: React.ReactNode;
  disabled?: boolean;
  title?: string;
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
  const scrollContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [scrollEdges, setScrollEdges] = React.useState<{
    left: boolean;
    right: boolean;
  }>({ left: false, right: false });

  const updateScrollEdges = React.useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) {
      setScrollEdges({ left: false, right: false });
      return;
    }
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setScrollEdges((prev) =>
      prev.left === left && prev.right === right ? prev : { left, right },
    );
  }, []);

  React.useEffect(() => {
    if (variant !== "underline") return;
    updateScrollEdges();
    const el = scrollContainerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      updateScrollEdges();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [options.length, updateScrollEdges, variant]);

  const rawActiveIndex = options.findIndex((opt) => opt.value === value);
  const firstEnabledIndex = options.findIndex((opt) => !opt.disabled);
  const activeIndex =
    rawActiveIndex >= 0 && !options[rawActiveIndex]?.disabled
      ? rawActiveIndex
      : Math.max(0, firstEnabledIndex);

  const findNextEnabledIndex = (startIdx: number, step: 1 | -1): number | null => {
    if (options.length === 0) return null;
    for (let offset = 1; offset <= options.length; offset += 1) {
      const candidate =
        (startIdx + step * offset + options.length * 2) % options.length;
      if (!options[candidate]?.disabled) {
        return candidate;
      }
    }
    return null;
  };

  const moveFocusAndSelect = (nextIdx: number) => {
    const target = options[nextIdx];
    if (!target || target.disabled) return;
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
      nextIdx = findNextEnabledIndex(idx, 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIdx = findNextEnabledIndex(idx, -1);
    } else if (event.key === "Home") {
      const first = options.findIndex((opt) => !opt.disabled);
      nextIdx = first >= 0 ? first : null;
    } else if (event.key === "End") {
      for (let i = options.length - 1; i >= 0; i -= 1) {
        if (!options[i]?.disabled) {
          nextIdx = i;
          break;
        }
      }
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
      <div className={cn("relative min-w-0", className)}>
        <div
          ref={scrollContainerRef}
          onScroll={updateScrollEdges}
          role={groupRole}
          aria-label={ariaLabel}
          className="scrollbar-none flex items-center gap-0.5 overflow-x-auto border-b border-border"
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
                disabled={opt.disabled}
                aria-disabled={opt.disabled ? "true" : undefined}
                title={opt.title}
                aria-selected={isRadio ? undefined : active}
                aria-checked={isRadio ? active : undefined}
                aria-controls={
                  !isRadio && controlsPanelId ? controlsPanelId(opt.value) : undefined
                }
                aria-label={opt.ariaLabel}
                tabIndex={tabIdx}
                onClick={() => {
                  if (!opt.disabled) onChange(opt.value);
                }}
                onKeyDown={(ev) => handleKeyDown(ev, idx)}
                className={cn(
                  "relative inline-flex min-h-6 min-w-6 shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                  opt.disabled
                    ? "cursor-not-allowed bg-disabled-bg/40 text-disabled-fg opacity-60"
                    : active
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
        {scrollEdges.left ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 w-6 bg-linear-to-r from-surface via-surface/80 to-transparent"
          />
        ) : null}
        {scrollEdges.right ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-linear-to-l from-surface via-surface/80 to-transparent"
          />
        ) : null}
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
            disabled={opt.disabled}
            aria-disabled={opt.disabled ? "true" : undefined}
            title={opt.title}
            aria-selected={isRadio ? undefined : active}
            aria-checked={isRadio ? active : undefined}
            aria-controls={
              !isRadio && controlsPanelId ? controlsPanelId(opt.value) : undefined
            }
            aria-label={opt.ariaLabel}
            tabIndex={tabIdx}
            onClick={() => {
              if (!opt.disabled) onChange(opt.value);
            }}
            onKeyDown={(ev) => handleKeyDown(ev, idx)}
            className={cn(
              "inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              sizeCls,
              opt.disabled
                ? "cursor-not-allowed bg-disabled-bg text-disabled-fg opacity-60"
                : active
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
