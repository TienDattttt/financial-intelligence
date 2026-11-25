import * as React from "react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import type { RiskTone } from "@/lib/domain-labels";
import { cn } from "@/lib/utils";

export function VerdictCard({
  title,
  tooltip,
  value,
  subValueText,
  tone,
  badgeLabel,
  thresholdText,
  deltaText,
  deltaTone = "neutral",
  historyRangeText,
  footerExtra,
  flat = false,
  children,
}: {
  title: string;
  tooltip: string;
  value: string;
  subValueText?: string | null;
  tone: RiskTone;
  badgeLabel?: string | null;
  thresholdText: string;
  deltaText?: string | null;
  deltaTone?: "up" | "down" | "neutral";
  historyRangeText?: string | null;
  footerExtra?: string | null;
  flat?: boolean;
  children?: React.ReactNode;
}) {
  const deltaColor =
    deltaTone === "up"
      ? "text-up"
      : deltaTone === "down"
        ? "text-down"
        : "text-ink-muted";

  return (
    <div
      className={cn(
        "flex flex-col justify-between bg-surface p-3.5 text-foreground",
        !flat && "rounded-lg border border-border",
      )}
    >
      <div>
        <div className="flex items-start justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1 text-xs font-medium leading-snug text-ink-muted">
            <span>{title}</span>
            <InfoTooltip text={tooltip} label={`Giải thích ${title}`} />
          </span>
          {badgeLabel ? (
            <RiskBadge tone={tone} label={badgeLabel} className="shrink-0" />
          ) : null}
        </div>

        <div className="mt-1.5 flex items-baseline justify-between gap-2">
          <div className="flex flex-wrap items-baseline gap-1.5">
            <span className="num text-[24px] font-semibold leading-7 tracking-tight text-ink">
              {value}
            </span>
            {subValueText ? (
              <span className="num rounded-sm bg-surface-muted px-1.5 py-0.5 text-[11px] font-medium text-ink-muted whitespace-nowrap">
                {subValueText}
              </span>
            ) : null}
          </div>
          {deltaText ? (
            <span className={cn("num shrink-0 text-xs font-medium", deltaColor)}>
              {deltaText}
            </span>
          ) : null}
        </div>

        {children ? <div className="mt-2">{children}</div> : null}
      </div>

      <div className="mt-2.5 space-y-0.5 border-t border-border/60 pt-2 text-[11px] leading-4 text-ink-muted">
        <p>{thresholdText}</p>
        {historyRangeText ? <p className="num">{historyRangeText}</p> : null}
        {footerExtra ? <p className="text-ink-subtle">{footerExtra}</p> : null}
      </div>
    </div>
  );
}
