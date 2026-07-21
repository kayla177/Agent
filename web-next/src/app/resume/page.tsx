import { prisma } from "@/lib/db";
import type { Resume } from "@/lib/resume";
import PlanetTheme from "@/components/applications/PlanetTheme";
import PoolManager from "@/components/resume/PoolManager";
import GenerateForm from "@/components/resume/GenerateForm";
import ResumeCard from "@/components/resume/ResumeCard";

export const dynamic = "force-dynamic";

export default async function ResumePage() {
  const [docsRaw, resumes, jobsRaw] = await Promise.all([
    prisma.experience_docs.findMany({ orderBy: { id: "desc" } }),
    prisma.resumes.findMany({ orderBy: { updated_at: "desc" } }),
    // Jobs with a description are the only ones worth tailoring against.
    prisma.jobs.findMany({
      where: { NOT: [{ description: null }, { description: "" }] },
      select: { id: true, title: true, company: true, fit_score: true },
    }),
  ]);
  // Drop the (potentially large) text from the pool list sent to the client.
  const docs = docsRaw.map((d) => ({
    id: d.id,
    filename: d.filename,
    kind: d.kind,
    chars: d.text.length,
    added_at: d.added_at,
  }));
  // Highest-fit first (nulls last) so the best matches are easy to pick.
  const jobs = jobsRaw.sort(
    (a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1),
  );
  const hasResume = docs.some((d) => d.kind === "resume") || docs.length > 0;

  return (
    <>
      <PlanetTheme planet="jupiter" />
      <h1>resume</h1>
      <p className="muted">
        Tailored, ATS-focused resumes drafted from your real experience — the
        generator reframes and keyword-matches, it never invents. Pick a scraped
        job and generate a draft, then view and edit it below.
      </p>

      <h2>experience pool ({docs.length})</h2>
      <PoolManager docs={docs} />

      <h2>generate</h2>
      <GenerateForm jobs={jobs} poolReady={hasResume} />

      <h2>generated resumes ({resumes.length})</h2>
      {resumes.length === 0 ? (
        <p className="muted">
          No resumes yet. Pick a job above and generate one.
        </p>
      ) : (
        <div className="resume-list">
          {(resumes as Resume[]).map((r) => (
            <ResumeCard key={r.job_id} resume={r} />
          ))}
        </div>
      )}
    </>
  );
}
