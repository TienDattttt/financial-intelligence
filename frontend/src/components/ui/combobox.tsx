"use client";

import * as React from "react";
import { Combobox as ComboboxPrimitive } from "@base-ui/react/combobox";
import { Check, ChevronDown, Search } from "lucide-react";
import { normalizeSearchText } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface ComboboxOption {
  value: string;
  label: string;
  keywords?: string;
  hint?: string;
  disabled?: boolean;
}

export interface ComboboxProps {
  id?: string;
  options: readonly ComboboxOption[];
  value: string;
  onValueChange: (nextValue: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  ariaLabel?: string;
  disabled?: boolean;
  searchable?: boolean;
  className?: string;
  popupClassName?: string;
}

export function matchesComboboxQuery(
  option: ComboboxOption,
  query: string,
): boolean {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) return true;
  const haystack = normalizeSearchText(
    option.keywords ? `${option.label} ${option.keywords}` : option.label,
  );
  return haystack.includes(normalizedQuery);
}

export function Combobox({
  id,
  options,
  value,
  onValueChange,
  placeholder = "Chọn mục...",
  searchPlaceholder = "Tìm nhanh (hỗ trợ không dấu)...",
  emptyText = "Không tìm thấy mục phù hợp.",
  ariaLabel,
  disabled = false,
  searchable,
  className,
  popupClassName,
}: ComboboxProps) {
  const showSearch = searchable ?? options.length > 6;
  const selectedOption = React.useMemo(
    () => options.find((opt) => opt.value === value) ?? null,
    [options, value],
  );

  return (
    <ComboboxPrimitive.Root<ComboboxOption>
      items={options as ComboboxOption[]}
      value={selectedOption}
      onValueChange={(next) => {
        onValueChange(next ? next.value : "");
      }}
      itemToStringLabel={(item) => item.label}
      itemToStringValue={(item) => item.value}
      isItemEqualToValue={(item, current) => item.value === current.value}
      filter={(item, query) => matchesComboboxQuery(item, query)}
      autoHighlight
      disabled={disabled}
    >
      <ComboboxPrimitive.Trigger
        id={id}
        type="button"
        aria-label={ariaLabel}
        disabled={disabled}
        className={cn(
          "flex h-8 w-full items-center justify-between gap-2 rounded-md border border-border-strong bg-surface px-2.5 text-left text-[13px] text-ink shadow-xs transition-colors duration-150 hover:border-accent-ink/50 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:bg-disabled-bg disabled:text-disabled-fg",
          className,
        )}
      >
        <ComboboxPrimitive.Value placeholder={placeholder}>
          {(selected: ComboboxOption | null) => (
            <span
              className={cn(
                "truncate",
                selected ? "text-ink" : "text-ink-subtle",
              )}
            >
              {selected ? selected.label : placeholder}
            </span>
          )}
        </ComboboxPrimitive.Value>
        <ComboboxPrimitive.Icon className="shrink-0 text-ink-muted">
          <ChevronDown className="size-3.5" aria-hidden="true" />
        </ComboboxPrimitive.Icon>
      </ComboboxPrimitive.Trigger>

      <ComboboxPrimitive.Portal>
        <ComboboxPrimitive.Positioner
          side="bottom"
          align="start"
          sideOffset={4}
          className="z-50"
        >
          <ComboboxPrimitive.Popup
            className={cn(
              "w-[var(--anchor-width)] min-w-[220px] max-w-[320px] overflow-hidden rounded-lg border border-border-strong bg-surface text-ink shadow-popover transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-reduce:transition-none",
              popupClassName,
            )}
          >
            {showSearch ? (
              <div className="flex items-center gap-1.5 border-b border-border bg-surface-muted/40 px-2.5 py-1.5">
                <Search
                  className="size-3.5 shrink-0 text-ink-muted"
                  aria-hidden="true"
                />
                <ComboboxPrimitive.Input
                  placeholder={searchPlaceholder}
                  aria-label={searchPlaceholder}
                  className="h-7 w-full bg-transparent text-xs text-ink placeholder:text-ink-subtle focus:outline-none"
                />
              </div>
            ) : null}

            <ComboboxPrimitive.Empty className="text-xs text-ink-muted">
              <div className="px-3 py-2.5">{emptyText}</div>
            </ComboboxPrimitive.Empty>

            <ComboboxPrimitive.List className="max-h-60 overflow-y-auto p-1">
              {(item: ComboboxOption) => (
                <ComboboxPrimitive.Item
                  key={item.value || "__all__"}
                  value={item}
                  disabled={item.disabled}
                  className="flex cursor-pointer items-center justify-between gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-xs text-ink outline-none select-none data-[highlighted]:bg-accent-soft data-[highlighted]:text-accent-ink data-[selected]:font-semibold data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
                >
                  <span className="truncate">{item.label}</span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    {item.hint ? (
                      <span className="num text-[11px] text-ink-muted">
                        {item.hint}
                      </span>
                    ) : null}
                    <ComboboxPrimitive.ItemIndicator className="text-accent-ink">
                      <Check className="size-3.5" aria-hidden="true" />
                    </ComboboxPrimitive.ItemIndicator>
                  </span>
                </ComboboxPrimitive.Item>
              )}
            </ComboboxPrimitive.List>
          </ComboboxPrimitive.Popup>
        </ComboboxPrimitive.Positioner>
      </ComboboxPrimitive.Portal>
    </ComboboxPrimitive.Root>
  );
}
