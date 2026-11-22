"use client";

import * as React from "react";
import { Tooltip } from "@base-ui/react/tooltip";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

export function InfoTooltip({
  text,
  label = "Giải thích chỉ số",
}: {
  text: string;
  label?: string;
}) {
  return (
    <Tooltip.Provider delay={150} closeDelay={0}>
      <Tooltip.Root>
        <Tooltip.Trigger
          type="button"
          aria-label={label}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex size-4 items-center justify-center rounded-full text-ink-subtle transition-colors duration-150 hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Info className="size-3.5" aria-hidden="true" />
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Positioner
            side="top"
            align="center"
            sideOffset={6}
            className="z-50"
          >
            <Tooltip.Popup
              role="tooltip"
              className={cn(
                "max-w-64 rounded-[var(--radius-md)] border border-border bg-surface px-2.5 py-2 text-left text-xs font-normal leading-4 text-ink shadow-[0_4px_16px_rgba(15,23,42,0.12)] transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-reduce:transition-none dark:shadow-[0_4px_16px_rgba(0,0,0,0.48)]",
              )}
            >
              {text}
            </Tooltip.Popup>
          </Tooltip.Positioner>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
