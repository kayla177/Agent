import { prisma } from "@/lib/db";
import { getAgents } from "@/lib/agents-server";
import { getStocksDesk } from "@/lib/stocks-server";
import type { Run } from "@/lib/runs";
import AgentCard from "@/components/dashboard/AgentCard";
import WatchlistBars from "@/components/stocks/WatchlistBars";
import TrendLine from "@/components/stocks/TrendLine";
import SignalsTable from "@/components/stocks/SignalsTable";

export const dynamic = "force-dynamic";

export default async function StocksPage() {
  const [agents, recent, desk] = await Promise.all([
    getAgents(),
    prisma.runs.findMany({ where: { agent_key: "stock_digest" }, orderBy: { id: "desc" }, take: 1 }) as Promise<Run[]>,
    getStocksDesk(),
  ]);
  const stock = agents.find((a) => a.key === "stock_digest");
  const last = recent[0];

  return (
    <>
      <h1>stocks</h1>
      <p className="muted">Watchlist quotes, trend, and RSI/trend signals — informational, not advice.</p>

      {desk === null ? (
        <p className="chart-empty">Agent service offline — start it with <code>python -m server</code> (port 8001).</p>
      ) : (
        <>
          <div className="chart-grid">
            <div className="chart-card">
              <h3>watchlist today</h3>
              <WatchlistBars wl={desk.watchlist} />
            </div>
            <div className="chart-card">
              <h3>signals</h3>
              <SignalsTable signals={desk.signals} />
            </div>
          </div>
          <div className="chart-card" style={{ marginTop: "1.1rem" }}>
            <h3>price trend (normalized)</h3>
            <TrendLine trend={desk.trend} />
          </div>
        </>
      )}

      <h2>run</h2>
      {stock ? (
        <AgentCard agent={stock} last={last ? { status: last.status, started_at: last.started_at } : null} />
      ) : (
        <p className="chart-empty">Agent service offline.</p>
      )}
    </>
  );
}
