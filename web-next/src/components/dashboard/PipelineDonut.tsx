export type DonutSegment = { label: string; value: number; color: string };

/** Dependency-free SVG donut (matches the old ApexCharts donut, no chart lib). */
export default function PipelineDonut({ data }: { data: DonutSegment[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  if (!total) return <p className="chart-empty">No applications logged yet.</p>;

  const r = 52;
  const c = 2 * Math.PI * r;

  // Precompute each segment's arc length and cumulative offset via prefix sums
  // (no variable reassignment during render — keeps the React compiler happy).
  const positive = data.filter((d) => d.value > 0);
  const lens = positive.map((d) => (d.value / total) * c);
  const segments = positive.map((d, i) => ({
    ...d,
    len: lens[i],
    offset: lens.slice(0, i).reduce((a, b) => a + b, 0),
  }));

  return (
    <div className="donut-wrap">
      <svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label="Application pipeline by status">
        <g transform="translate(70,70) rotate(-90)">
          <circle r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="16" />
          {segments.map((s) => (
            <circle
              key={s.label}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth="16"
              strokeDasharray={`${s.len} ${c - s.len}`}
              strokeDashoffset={-s.offset}
            />
          ))}
        </g>
        <text x="70" y="66" textAnchor="middle" className="donut-total">{total}</text>
        <text x="70" y="84" textAnchor="middle" className="donut-total-label">total</text>
      </svg>
      <div className="donut-legend">
        {data.filter((d) => d.value > 0).map((d) => (
          <span key={d.label}>
            <span className="dot" style={{ background: d.color }} />
            {d.label}
            <span className="lg-count">{d.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
