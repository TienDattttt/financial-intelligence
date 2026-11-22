import * as React from "react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { Card, CardContent } from "@/components/ui/card";
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
  children,
}: {
  title: string;
  tooltip: string;
  value: string;
  subValueText?: string | null;
  tone: RiskTone;
  badgeLabel: string;
  thresholdText: string;
  deltaText?: string | null;
  deltaTone?: "up" | "down" | "neutral";
  historyRangeText?: string | null;
  footerExtra?: string | null;
  children?: React.ReactNode;
}) {
  const deltaColor =
    deltaTone === "up"
      ? "text-up"
      : deltaTone === "down"
        ? "text-down"
        : "text-ink-muted";

  return (
    <Card className="flex flex-col justify-between">
      <CardContent className="p-3.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-xs font-medium text-ink-muted">
            <span>{title}</span>
            <InfoTooltip text={tooltip} label={`Giải thích ${title}`} />
          </span>
          <RiskBadge tone={tone} label={badgeLabel} />
        </div>

        <div className="mt-2 flex items-baseline justify-between gap-2">
          <div className="flex items-baseline gap-1.5">
            <span className="num text-[26px] font-semibold leading-8 tracking-tight text-ink">
              {value}
            </span>
            {subValueText ? (
              <span className="num rounded bg-surface-muted px-1.5 py-0.5 text-[11px] font-medium text-ink-muted">
                {subValueText}
              </span>
            ) : null}
          </div>
          {deltaText ? (
            <span className={cn("num text-xs font-medium", deltaColor)}>
              {deltaText}
            </span>
          ) : null}
        </div>

        {children ? <div className="mt-2">{children}</div> : null}

        <div className="mt-2.5 space-y-0.5 border-t border-border/60 pt-2 text-[11px] leading-4 text-ink-muted">
          <p>{thresholdText}</p>
          {historyRangeText ? <p className="num">{historyRangeText}</p> : null}
          {footerExtra ? <p className="text-ink-subtle">{footerExtra}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}
