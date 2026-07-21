import { prisma } from "@/lib/db";
import type { Resume } from "@/lib/resume";
import PlanetTheme from "@/components/applications/PlanetTheme";
import PoolManager from "@/components/resume/PoolManager";
import ResumeCard from "@/components/resume/ResumeCard";

export const dynamic = "force-dynamic";

export default async function ResumePage() {
  const [docsRaw, resumes] = await Promise.all([
    prisma.experience_docs.findMany({ orderBy: { id: "desc" } }),
    prisma.resumes.findMany({ orderBy: { updated_at: "desc" } }),
  ]);
  // Drop the (potentially large) text from the pool list sent to the client.
  const docs = docsRaw.map((d) => ({
    id: d.id,
    filename: d.filename,
    kind: d.kind,
    chars: d.text.length,
    added_at: d.added_at,
  }));

  return (
    <>
      <PlanetTheme planet="jupiter" />
      <h1>resume</h1>
      <p className="muted">
        Tailored, ATS-focused resumes drafted from your real experience — the
        generator reframes and keyword-matches, it never invents. Drafts are
        created with the CLI (<code>run_resume_generator.py --job-id …</code>);
        the in-app “Generate” button arrives with the FastAPI service split.
      </p>

      <h2>experience pool ({docs.length})</h2>
      <PoolManager docs={docs} />

      <h2>generated resumes ({resumes.length})</h2>
      {resumes.length === 0 ? (
        <p className="muted">
          No resumes yet. Generate one from a scraped job with the CLI, then
          view and edit it here.
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
