import { prisma } from "@/lib/db";
import { computeStats, type Application } from "@/lib/applications";
import StatsRow from "@/components/applications/StatsRow";
import PipelineBars from "@/components/applications/PipelineBars";
import LogForm from "@/components/applications/LogForm";
import ApplicationRow from "@/components/applications/ApplicationRow";
import PlanetTheme from "@/components/applications/PlanetTheme";

export const dynamic = "force-dynamic";

export default async function ApplicationsPage() {
  const apps = (await prisma.applications.findMany({ orderBy: { id: "asc" } })) as Application[];
  const stats = computeStats(apps);
  return (
    <>
      <PlanetTheme planet="saturn" />
      <h1>applications</h1>
      <StatsRow stats={stats} />
      <h2>pipeline</h2>
      <PipelineBars counts={stats.counts} />
      <h2>log a new application</h2>
      <LogForm />
      <h2>your applications ({apps.length})</h2>
      {apps.length === 0 ? (
        <p className="muted">Nothing logged yet. Add one above.</p>
      ) : (
        <table className="apps">
          <thead>
            <tr>
              <th>Company</th><th>Role</th><th>Status</th><th>Applied</th>
              <th>Updated</th><th>Notes</th><th>Update</th><th></th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => <ApplicationRow key={a.id} app={a} />)}
          </tbody>
        </table>
      )}
    </>
  );
}
