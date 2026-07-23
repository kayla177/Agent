import { prisma } from "@/lib/db";
import { computeStats, type Application } from "@/lib/applications";
import StatsRow from "@/components/applications/StatsRow";
import PipelineBars from "@/components/applications/PipelineBars";
import LogForm from "@/components/applications/LogForm";
import ApplicationRow, { type ResumeOption } from "@/components/applications/ApplicationRow";
import SyncGmail from "@/components/applications/SyncGmail";

export const dynamic = "force-dynamic";

// The application pipeline. Résumés live on their own tab (/resume); each row
// here records which generated résumé was used to apply, so interview prep can
// recall the exact résumé sent.
export default async function TrackerPage() {
  const [apps, resumesRaw] = await Promise.all([
    prisma.applications.findMany({ orderBy: { id: "asc" } }) as Promise<Application[]>,
    prisma.resumes.findMany({
      orderBy: { updated_at: "desc" },
      select: { job_id: true, company: true, role: true },
    }),
  ]);
  const stats = computeStats(apps);
  const resumeOptions: ResumeOption[] = resumesRaw;

  return (
    <>
      <h1>tracker</h1>
      <StatsRow stats={stats} />
      <SyncGmail />
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
              <th>Updated</th><th>Notes</th><th>Résumé used</th><th>Update</th><th></th>
            </tr>
          </thead>
          <tbody>{apps.map((a) => <ApplicationRow key={a.id} app={a} resumeOptions={resumeOptions} />)}</tbody>
        </table>
      )}
    </>
  );
}
