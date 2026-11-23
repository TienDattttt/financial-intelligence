import { cn } from "@/lib/utils";

export function ScoreDots({
  score,
  validComponents,
  max = 9,
}: {
  score: number | null | undefined;
  validComponents?: number | null;
  max?: number;
}) {
  if (score === null || score === undefined) {
    return (
      <span className="text-[11px] text-ink-subtle">Chưa đủ tiêu chí</span>
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
