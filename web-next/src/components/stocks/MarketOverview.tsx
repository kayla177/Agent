import type { StockDesk } from "@/lib/stocks-server";

type Market = NonNullable<StockDesk["market"]>;

// Tiny sparkline for an index tile (normalized base=100 series).
function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const W = 88, H = 26, P = 2;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const x = (i: number) => P + (i / (data.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / span) * (H - 2 * P);
  const up = data[data.length - 1] >= data[0];
  return (
    <svg className="idx-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
      <polyline
        fill="none"
        stroke={up ? "var(--green)" : "var(--red)"}
        strokeWidth="1.6"
        strokeLinejoin="round"
        points={data.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
      />
    </svg>
  );
}

// Market context strip: index tiles + a plain-English read + biggest movers.
export default function MarketOverview({ market }: { market: Market }) {
  const { indices, movers, read } = market;
  return (
    <section className="market-overview">
      {read && <p className="mkt-read">{read}</p>}

      {indices.length > 0 && (
        <div className="idx-row">
          {indices.map((i) => {
            const up = (i.pct ?? 0) >= 0;
            return (
              <div className="idx-tile" key={i.symbol}>
                <div className="idx-name">{i.name}</div>
                <div className="idx-figs">
                  <span className="idx-price">{i.price != null ? i.price.toFixed(2) : "—"}</span>
                  {i.pct != null && (
                    <span className={`idx-pct ${up ? "up" : "down"}`}>
                      {up ? "▲" : "▼"} {Math.abs(i.pct).toFixed(1)}%
                    </span>
                  )}
                </div>
                <Spark data={i.series} />
              </div>
            );
          })}
        </div>
      )}

      {movers.length > 0 && (
        <div className="movers">
          <span className="movers-label">Biggest movers today</span>
          {movers.map((m) => {
            const up = m.pct >= 0;
            return (
              <span className={`mover ${up ? "up" : "down"}`} key={m.symbol}>
                {m.symbol} {up ? "+" : ""}{m.pct.toFixed(1)}%
              </span>
            );
          })}
        </div>
      )}
    </section>
  );
}
