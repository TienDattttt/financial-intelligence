import { AlertTriangle, CheckCircle2, MinusCircle, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import type { RiskTone } from "@/lib/domain-labels";
import { cn } from "@/lib/utils";

const TONE_STYLES: Record<RiskTone, string> = {
  low: "bg-risk-low-soft text-risk-low border border-risk-low/20",
  mid: "bg-risk-mid-soft text-risk-mid border border-risk-mid/25",
  high: "bg-risk-high-soft text-risk-high border border-risk-high/25",
  neutral: "bg-surface-muted text-ink-muted border border-border",
};

export function RiskBadge({
  tone,
  label,
  className,
}: {
  tone: RiskTone;
  label: string;
  className?: string;
}) {
  const Icon =
    tone === "low"
      ? CheckCircle2
      : tone === "mid"
        ? AlertTriangle
        : tone === "high"
          ? ShieldAlert
          : MinusCircle;

  return (
    <Badge className={cn(TONE_STYLES[tone], className)}>
      <Icon className="size-3 shrink-0" aria-hidden="true" />
      <span>{label}</span>
    </Badge>
  );
}
