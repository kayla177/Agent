import { prisma } from "@/lib/db";
import type { Job } from "@/lib/jobs";
import JobsBoard from "@/components/jobs/JobsBoard";
import RunScraperButton from "@/components/jobs/RunScraperButton";

export const dynamic = "force-dynamic";

// Only the columns the UI renders — never ship the `data` blob to the client.
const JOB_SELECT = {
  id: true, company: true, title: true, location: true, url: true, status: true,
  ats: true, posted_at: true, remote: true, compensation: true, department: true,
  description: true, fit_score: true, fit_reason: true, ghost: true, also_on: true,
} as const;

export default async function JobsPage() {
  const jobs = (await prisma.jobs.findMany({ select: JOB_SELECT })) as Job[];
  return (
    <>
      <div className="jobs-header">
        <h1>jobs</h1>
        <RunScraperButton />
      </div>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet — run the scraper above to pull fresh roles.</p>
      ) : (
        <JobsBoard jobs={jobs} />
      )}
    </>
  );
}
