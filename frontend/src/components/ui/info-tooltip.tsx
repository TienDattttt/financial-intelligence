"use client";

import * as React from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

export function InfoTooltip({
  text,
  label = "Giải thích chỉ số",
}: {
  text: string;
  label?: string;
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <span
      className="relative inline-flex items-center align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onBlur={() => setOpen(false)}
        className="inline-flex size-4 items-center justify-center rounded-full text-ink-subtle hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Info className="size-3.5" />
      </button>
      {open ? (
        <span
          role="tooltip"
          className={cn(
            "absolute bottom-full left-1/2 z-50 mb-1.5 w-64 -translate-x-1/2 rounded border border-border bg-surface p-2.5 text-left text-xs font-normal leading-4 text-ink shadow-[0_4px_16px_rgba(26,29,33,0.12)]",
          )}
        >
          {text}
        </span>
      ) : null}
    </span>
  );
}
