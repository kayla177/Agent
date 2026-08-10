import { prisma } from "@/lib/db";
import type { Resume } from "@/lib/resume";
import PoolManager from "@/components/resume/PoolManager";
import GenerateForm from "@/components/resume/GenerateForm";
import ResumeCard from "@/components/resume/ResumeCard";
import MasterResume from "@/components/resume/MasterResume";
import MasterCoverLetter from "@/components/resume/MasterCoverLetter";
import GenerateCoverLetter from "@/components/resume/GenerateCoverLetter";
import CoverLetterCard from "@/components/resume/CoverLetterCard";

export const dynamic = "force-dynamic";

// The résumé desk: master résumé (canonical) → tailored per-job drafts → the
// experience pool that feeds the generator. Its own tab (planet: neptune);
// the tracker links back here to record which résumé was used per application.
export default async function ResumePage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string }>;
}) {
  const focusJob = (await searchParams).job ?? "";
  const [master, docsRaw, resumes, jobsRaw, masterLetter, letters] = await Promise.all([
    prisma.master_resume.findFirst({ orderBy: { id: "asc" } }),
    prisma.experience_docs.findMany({ orderBy: { id: "desc" } }),
    prisma.resumes.findMany({ orderBy: { updated_at: "desc" } }),
    prisma.jobs.findMany({
      where: { NOT: [{ description: null }, { description: "" }] },
      select: { id: true, title: true, company: true, fit_score: true },
    }),
    prisma.master_cover_letter.findFirst({ orderBy: { id: "asc" } }),
    prisma.cover_letters.findMany({ orderBy: { updated_at: "desc" } }),
  ]);
  const docs = docsRaw.map((d) => ({
    id: d.id, filename: d.filename, kind: d.kind, chars: d.text.length, added_at: d.added_at,
  }));
  const jobs = jobsRaw.sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1));
  const poolReady = docs.length > 0 || Boolean(master?.latex?.trim() || master?.markdown?.trim());

  return (
    <>
      <h1>résumé</h1>
      <p className="muted">
        ATS-tailored résumés drafted from your real experience — the generator
        reframes and keyword-matches, it never invents.
      </p>

      <div className="rz-panels">
        <section className="panel">
          <div className="panel-head">
            <h2>master résumé</h2>
            <span className="panel-sub">your canonical résumé — tailored drafts start from this</span>
          </div>
          <MasterResume latex={master?.latex ?? ""} updatedAt={master?.updated_at ?? ""} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>tailored résumés</h2>
            <span className="panel-sub">generate an ATS résumé for a scraped job</span>
          </div>
          <GenerateForm jobs={jobs} poolReady={poolReady} />
          {resumes.length === 0 ? (
            <p className="muted">No résumés yet. Pick a job above and generate one.</p>
          ) : (
            <div className="resume-list">
              {(resumes as Resume[]).map((r) => (
                <ResumeCard key={r.job_id} resume={r} defaultOpen={r.job_id === focusJob} />
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>master cover letter</h2>
            <span className="panel-sub">a sample letter in your own voice — every draft imitates it</span>
          </div>
          <MasterCoverLetter body={masterLetter?.body ?? ""} updatedAt={masterLetter?.updated_at ?? ""} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>cover letters</h2>
            <span className="panel-sub">draft a cover letter for a scraped job</span>
          </div>
          <GenerateCoverLetter jobs={jobs} hasMaster={Boolean(masterLetter?.body?.trim())} />
          {letters.length === 0 ? (
            <p className="muted">No cover letters yet. Pick a job above and draft one.</p>
          ) : (
            <div className="resume-list">
              {letters.map((l) => (
                <CoverLetterCard key={l.job_id} letter={l} />
              ))}
            </div>
          )}
        </section>

        <details className="panel">
          <summary>
            <h2>sources · experience pool ({docs.length})</h2>
            <span className="panel-sub">supplementary docs the generator can draw from</span>
          </summary>
          <PoolManager docs={docs} />
        </details>
      </div>
    </>
  );
}
