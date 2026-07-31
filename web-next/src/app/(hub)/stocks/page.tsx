import { prisma } from "@/lib/db";
import { getAgents } from "@/lib/agents-server";
import { getStocksDesk } from "@/lib/stocks-server";
import type { Run } from "@/lib/runs";
import AgentCard from "@/components/dashboard/AgentCard";
import MarketOverview from "@/components/stocks/MarketOverview";
import VerdictCard from "@/components/stocks/VerdictCard";
import TrendLine from "@/components/stocks/TrendLine";
import StockGlossary from "@/components/stocks/StockGlossary";

export const dynamic = "force-dynamic";

function whenLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

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
      <p className="muted">
        Plain-English analysis of your watchlist — verdicts, explained signals, and market context.
        Informational, not financial advice.
      </p>

      {desk === null ? (
        <p className="chart-empty">Agent service offline — start it with <code>python -m server</code> (port 8001).</p>
      ) : (
        <>
          {desk.live ? (
            <p className="desk-note">
              Quick signal-only read (no AI write-up yet). Run the analysis below for full verdicts, risks, and catalysts.
            </p>
          ) : desk.generatedAt ? (
            <p className="desk-note">Last analyzed {whenLabel(desk.generatedAt)}.</p>
          ) : null}

          {desk.market && <MarketOverview market={desk.market} />}

          {desk.cards.length > 0 ? (
            <div className="verdict-grid">
              {desk.cards.map((c) => <VerdictCard key={c.symbol} card={c} />)}
            </div>
          ) : (
            <p className="chart-empty">No watchlist tickers configured.</p>
          )}

          <div className="chart-card" style={{ marginTop: "1.1rem" }}>
            <h3>price trend (normalized)</h3>
            <TrendLine trend={desk.trend} />
          </div>

          {desk.warnings.length > 0 && (
            <ul className="desk-warnings">
              {desk.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}

          <StockGlossary />
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
