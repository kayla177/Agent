import { prisma } from "@/lib/db";
import { getAgents } from "@/lib/agents-server";
import type { Run } from "@/lib/runs";
import AgentCard from "@/components/dashboard/AgentCard";

export const dynamic = "force-dynamic";

export default async function StocksPage() {
  const [agents, recent] = await Promise.all([
    getAgents(),
    prisma.runs.findMany({
      where: { agent_key: "stock_digest" }, orderBy: { id: "desc" }, take: 1,
    }) as Promise<Run[]>,
  ]);
  const stock = agents.find((a) => a.key === "stock_digest");
  const last = recent[0];

  return (
    <>
      <h1>stocks</h1>
      <div className="chart-grid">
        <div className="chart-card">
          <h3>watchlist today</h3>
          {/* Real bar chart lands with the persisted quotes source (Phase 3, 2g). */}
          <p className="chart-empty">
            No quotes yet — run the Stock Digest to load the watchlist chart.
          </p>
        </div>
        <div className="chart-card">
          <h3>digest</h3>
          {stock ? (
            <AgentCard
              agent={stock}
              last={last ? { status: last.status, started_at: last.started_at } : null}
            />
          ) : (
            <p className="chart-empty">Agent service offline.</p>
          )}
        </div>
      </div>
    </>
  );
}
