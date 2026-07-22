import type { StockDesk } from "@/lib/stocks-server";

// Watchlist daily % change — dependency-free CSS bars (green up / red down).
export default function WatchlistBars({ wl }: { wl: StockDesk["watchlist"] }) {
  if (!wl.series.length) return <p className="chart-empty">No quotes available right now.</p>;
  const max = Math.max(1, ...wl.series.map((v) => Math.abs(v)));
  return (
    <div className="bar-chart">
      {wl.series.map((v, i) => (
        <div className="bar-col" key={wl.labels[i]}>
          <span className="bar-pct">{v >= 0 ? "+" : ""}{v.toFixed(1)}%</span>
          <span
            className={`bar ${v >= 0 ? "up" : "down"}`}
            style={{ height: `${Math.max(2, (Math.abs(v) / max) * 100)}%` }}
          />
          <span className="bar-sym">{wl.labels[i]}</span>
        </div>
      ))}
    </div>
  );
}
