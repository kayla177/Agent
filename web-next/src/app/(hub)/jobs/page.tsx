import { prisma } from "@/lib/db";
import type { Job } from "@/lib/jobs";
import JobsBoard from "@/components/jobs/JobsBoard";
import RunScraperButton from "@/components/jobs/RunScraperButton";
import ScoreBacklogButton from "@/components/jobs/ScoreBacklogButton";

export const dynamic = "force-dynamic";

// Only the columns the UI renders — never ship the `data` blob to the client.
const JOB_SELECT = {
  id: true, company: true, title: true, location: true, url: true, status: true,
  ats: true, posted_at: true, remote: true, compensation: true, department: true,
  description: true, fit_score: true, fit_reason: true, ghost: true, also_on: true,
  country: true,
} as const;

export default async function JobsPage() {
  const [jobs, resumes, master] = await Promise.all([
    prisma.jobs.findMany({ select: JOB_SELECT }) as Promise<Job[]>,
    prisma.resumes.findMany({ select: { job_id: true, company: true, role: true } }),
    prisma.master_resume.findFirst({ select: { latex: true } }),
  ]);
  return (
    <>
      <div className="jobs-header">
        <h1>jobs</h1>
        <div className="jobs-header-actions">
          <ScoreBacklogButton />
          <RunScraperButton />
        </div>
      </div>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet — run the scraper above to pull fresh roles.</p>
      ) : (
        <JobsBoard jobs={jobs} resumes={resumes} hasMaster={Boolean(master?.latex?.trim())} />
      )}
    </>
  );
}
