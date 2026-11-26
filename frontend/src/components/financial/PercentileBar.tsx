import { formatPeerGroupLabel } from "@/lib/domain-labels";
import { formatNumber, getPercentileQuintile, type RatioDirection } from "@/lib/format";
import { cn } from "@/lib/utils";

const QUINTILE_COLORS: Record<1 | 2 | 3 | 4 | 5, string> = {
  1: "bg-q1",
  2: "bg-q2",
  3: "bg-q3",
  4: "bg-q4",
  5: "bg-q5",
};

export function PercentileBar({
  percentile,
  direction = "HIGHER_IS_BETTER",
  peerGroupLevel,
  peerCount = 0,
}: {
  percentile: number | null | undefined;
  direction?: RatioDirection;
  peerGroupLevel?: string | null;
  peerCount?: number;
}) {
  if (percentile === null || percentile === undefined || !Number.isFinite(percentile)) {
    return <span className="text-xs text-ink-subtle">N/A</span>;
  }

  const clamped = Math.max(0, Math.min(100, percentile));
  const quintile = getPercentileQuintile(clamped, direction) ?? 3;
  const peerInfo = formatPeerGroupLabel(peerGroupLevel, peerCount);
  const lowSample = peerInfo.isFallback || (peerCount > 0 && peerCount < 5);
  const effectivePct = direction === "LOWER_IS_BETTER" ? 100 - clamped : clamped;

  return (
    <div
      className="flex items-center gap-1.5 sm:gap-2"
      title={`Phân vị giá trị: ${formatNumber(clamped, 0)}% · ${peerInfo.text}`}
    >
      <div
        className={cn(
          "relative h-2.5 w-20 sm:w-28 overflow-hidden rounded-[2px] border border-border/80 bg-surface-muted",
          lowSample && "striped-fallback",
        )}
      >
        <div
          className={cn("h-full transition-[width] duration-150", QUINTILE_COLORS[quintile])}
          style={{ width: `${Math.max(4, effectivePct)}%` }}
        />
        <span className="pointer-events-none absolute inset-y-0 left-1/4 w-px bg-surface/80" />
        <span className="pointer-events-none absolute inset-y-0 left-1/2 w-px bg-surface/90" />
        <span className="pointer-events-none absolute inset-y-0 left-3/4 w-px bg-surface/80" />
      </div>
      <span className="num w-8 sm:w-9 text-right text-xs text-ink-muted">
        {formatNumber(clamped, 0)}%
      </span>
      {lowSample ? (
        <span
          className="text-[10px] font-medium text-risk-mid"
          title={peerInfo.text}
        >
          *
        </span>
      ) : null}
    </div>
  );
}
