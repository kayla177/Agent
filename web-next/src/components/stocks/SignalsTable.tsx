import type { StockDesk } from "@/lib/stocks-server";

// BUY / SELL / HOLD per ticker from a transparent RSI/trend heuristic. The signal
// word is always shown (color is decorative), and it's informational, not advice.
export default function SignalsTable({ signals }: { signals: StockDesk["signals"] }) {
  if (!signals.length) return <p className="chart-empty">No signals.</p>;
  return (
    <table className="mini">
      <thead>
        <tr><th>Symbol</th><th>Price</th><th>RSI</th><th>Signal</th></tr>
      </thead>
      <tbody>
        {signals.map((s) => (
          <tr key={s.symbol} title={s.reason}>
            <td>{s.symbol}</td>
            <td>{s.price != null ? `$${s.price.toFixed(2)}` : "—"}</td>
            <td>{s.rsi ?? "—"}</td>
            <td>
              <span className="sig" style={{ background: `${s.color}22`, color: s.color }}>
                {s.signal.toUpperCase()}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
