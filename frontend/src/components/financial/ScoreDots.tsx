import { formatPiotroskiNaDetail } from "@/lib/domain-labels";
import { cn } from "@/lib/utils";

export function ScoreDots({
  score,
  validComponents,
  naReason,
  missingCriteria,
  max = 9,
}: {
  score: number | null | undefined;
  validComponents?: number | null;
  naReason?: string | null;
  missingCriteria?: readonly string[] | null;
  max?: number;
}) {
  if (score === null || score === undefined) {
    const detail = formatPiotroskiNaDetail(naReason, missingCriteria);
    const hasMissingDetail = Boolean(detail.detailLine);
    return (
      <div className="space-y-0.5" title={detail.tooltip}>
        <p className="text-[11px] leading-4 text-ink-muted">{detail.summary}</p>
        {hasMissingDetail ? (
          <p className="text-[11px] leading-4 text-ink-subtle">{detail.detailLine}</p>
        ) : validComponents !== null &&
          validComponents !== undefined &&
          validComponents < max ? (
          <p className="num text-[11px] leading-4 text-ink-subtle">
            Đạt {validComponents}/{max} tiêu chí
          </p>
        ) : null}
      </div>
    );
  }
  const filledColor =
    score >= 7 ? "bg-risk-low" : score >= 4 ? "bg-risk-mid" : "bg-risk-high";

  return (
    <div className="flex items-center justify-between gap-2">
      <div
        role="img"
        className="flex items-center gap-1"
        aria-label={`Điểm ${score} trên ${max}`}
      >
        {Array.from({ length: max }).map((_, idx) => (
          <span
            key={idx}
            className={cn(
              "size-2 rounded-full",
              idx < score ? filledColor : "bg-border",
            )}
          />
        ))}
      </div>
      {validComponents !== null && validComponents !== undefined && validComponents < max ? (
        <span className="num text-[11px] text-risk-mid">
          Đạt {validComponents}/{max} tiêu chí
        </span>
      ) : null}
    </div>
  );
}
