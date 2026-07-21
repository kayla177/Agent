import { prisma } from "@/lib/db";
import { AGENTS } from "@/lib/agents";
import type { Run } from "@/lib/runs";
import AgentCard from "@/components/dashboard/AgentCard";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const recent = (await prisma.runs.findMany({ orderBy: { id: "desc" }, take: 8 })) as Run[];
  const lastByAgent = new Map<string, Run>();
  for (const r of recent) if (!lastByAgent.has(r.agent_key)) lastByAgent.set(r.agent_key, r);

  return (
    <>
      <h1>dashboard</h1>
      <div className="agent-grid">
        {AGENTS.map((a) => {
          const l = lastByAgent.get(a.key);
          return <AgentCard key={a.key} agent={a} last={l ? { status: l.status, started_at: l.started_at } : null} />;
        })}
      </div>

      <h2>recent runs</h2>
      {recent.length === 0 ? (
        <p className="muted">No runs yet — run an agent above.</p>
      ) : (
        <table className="apps">
          <thead><tr><th>Agent</th><th>Status</th><th>Started (UTC)</th></tr></thead>
          <tbody>
            {recent.map((r) => (
              <tr key={r.id}>
                <td>{r.agent_key}</td>
                <td><span className={`badge ${r.status}`}>{r.status}</span></td>
                <td className="muted">{r.started_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
