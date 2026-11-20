export function SparklineSvg({
  values,
  width = 64,
  height = 20,
}: {
  values: readonly (number | null | undefined)[];
  width?: number;
  height?: number;
}) {
  const valid = values
    .map((v, idx) => ({ v, idx }))
    .filter((pt): pt is { v: number; idx: number } => typeof pt.v === "number" && Number.isFinite(pt.v));

  if (valid.length < 2) {
    return <span className="text-[11px] text-ink-subtle">—</span>;
  }

  const min = Math.min(...valid.map((p) => p.v));
  const max = Math.max(...valid.map((p) => p.v));
  const span = max - min || 1;
  const maxIdx = Math.max(1, values.length - 1);

  const coords = valid.map(({ v, idx }) => {
    const x = 2 + (idx / maxIdx) * (width - 4);
    const y = height - 3 - ((v - min) / span) * (height - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = coords[coords.length - 1].split(",");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="inline-block align-middle"
      aria-hidden="true"
    >
      <polyline
        fill="none"
        stroke="#1F4E79"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={coords.join(" ")}
      />
      <circle cx={last[0]} cy={last[1]} r="2" fill="#1F4E79" />
    </svg>
  );
}
