import { prisma } from "@/lib/db";
import { computeStats, type Application } from "@/lib/applications";
import type { Resume } from "@/lib/resume";
import StatsRow from "@/components/applications/StatsRow";
import PipelineBars from "@/components/applications/PipelineBars";
import LogForm from "@/components/applications/LogForm";
import ApplicationRow from "@/components/applications/ApplicationRow";
import SyncGmail from "@/components/applications/SyncGmail";
import PoolManager from "@/components/resume/PoolManager";
import GenerateForm from "@/components/resume/GenerateForm";
import ResumeCard from "@/components/resume/ResumeCard";

export const dynamic = "force-dynamic";

// The career hub: application pipeline + résumés in one place (Option C — resume
// folds under /tracker rather than floating as its own nav item).
export default async function TrackerPage() {
  const [apps, docsRaw, resumes, jobsRaw] = await Promise.all([
    prisma.applications.findMany({ orderBy: { id: "asc" } }) as Promise<Application[]>,
    prisma.experience_docs.findMany({ orderBy: { id: "desc" } }),
    prisma.resumes.findMany({ orderBy: { updated_at: "desc" } }),
    prisma.jobs.findMany({
      where: { NOT: [{ description: null }, { description: "" }] },
      select: { id: true, title: true, company: true, fit_score: true },
    }),
  ]);
  const stats = computeStats(apps);
  const docs = docsRaw.map((d) => ({
    id: d.id, filename: d.filename, kind: d.kind, chars: d.text.length, added_at: d.added_at,
  }));
  const jobs = jobsRaw.sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1));
  const poolReady = docs.length > 0;

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
              <th>Updated</th><th>Notes</th><th>Update</th><th></th>
            </tr>
          </thead>
          <tbody>{apps.map((a) => <ApplicationRow key={a.id} app={a} />)}</tbody>
        </table>
      )}

      <h2>résumés</h2>
      <p className="muted">
        ATS-tailored résumés drafted from your real experience — the generator
        reframes and keyword-matches, it never invents.
      </p>
      <h2>experience pool ({docs.length})</h2>
      <PoolManager docs={docs} />
      <h2>generate</h2>
      <GenerateForm jobs={jobs} poolReady={poolReady} />
      <h2>generated résumés ({resumes.length})</h2>
      {resumes.length === 0 ? (
        <p className="muted">No résumés yet. Pick a job above and generate one.</p>
      ) : (
        <div className="resume-list">
          {(resumes as Resume[]).map((r) => <ResumeCard key={r.job_id} resume={r} />)}
        </div>
      )}
    </>
  );
}
