import type { StockDesk } from "@/lib/stocks-server";

const COLORS = ["#7fb0ff", "#e0b15a", "#7fc08a", "#c58cff", "#e0705a", "#4a90ff", "#ddca9c"];

// Normalized (base=100) price-trend, one polyline per ticker — dependency-free SVG.
export default function TrendLine({ trend }: { trend: StockDesk["trend"] }) {
  if (!trend.series.length) {
    return <p className="chart-empty">No price history — set TWELVE_DATA_API_KEY to enable the trend chart.</p>;
  }
  const W = 640, H = 240, P = 10;
  const all = trend.series.flatMap((s) => s.data);
  const min = Math.min(...all), max = Math.max(...all);
  const span = max - min || 1;
  const x = (i: number, n: number) => P + (n <= 1 ? 0 : (i / (n - 1)) * (W - 2 * P));
  const y = (v: number) => H - P - ((v - min) / span) * (H - 2 * P);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Normalized price trend per ticker">
        {trend.series.map((s, si) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={COLORS[si % COLORS.length]}
            strokeWidth="2"
            strokeLinejoin="round"
            points={s.data.map((v, i) => `${x(i, s.data.length).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
          />
        ))}
      </svg>
      <div className="trend-legend">
        {trend.series.map((s, si) => (
          <span key={s.name}>
            <i className="dot" style={{ background: COLORS[si % COLORS.length] }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
