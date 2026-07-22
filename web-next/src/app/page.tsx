import { prisma } from "@/lib/db";
import { AGENTS } from "@/lib/agents";
import type { Run } from "@/lib/runs";
import { STATUSES, STATUS_META, computeStats } from "@/lib/applications";
import DashboardHero from "@/components/dashboard/DashboardHero";
import PipelineDonut, { type DonutSegment } from "@/components/dashboard/PipelineDonut";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [recent, apps] = await Promise.all([
    prisma.runs.findMany({ orderBy: { id: "desc" }, take: 8 }) as Promise<Run[]>,
    prisma.applications.findMany({ select: { status: true } }),
  ]);

  const { counts } = computeStats(apps);
  const pipeline: DonutSegment[] = STATUSES.map((s) => ({
    label: STATUS_META[s].label,
    value: counts[s],
    color: STATUS_META[s].color,
  }));

  return (
    <>
      <DashboardHero agents={AGENTS} />

      <h2>snapshot</h2>
      <div className="chart-grid">
        <div className="chart-card">
          <h3>application pipeline</h3>
          <PipelineDonut data={pipeline} />
        </div>
        <div className="chart-card">
          <h3>watchlist today</h3>
          {/* No persisted quotes in the data layer yet — run Stock Digest to populate. */}
          <p className="chart-empty">No quotes yet — run the Stock Digest agent above.</p>
        </div>
      </div>

      <h2>recent runs</h2>
      {recent.length === 0 ? (
        <p className="muted">No runs yet — pick a planet and preview an agent above.</p>
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
