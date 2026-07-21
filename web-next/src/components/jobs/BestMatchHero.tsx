import type { Job } from "@/lib/jobs";

export default function BestMatchHero({ job, onApply }: { job: Job; onApply: (id: string) => void }) {
  const pct = job.fit_score === null ? "" : `${Math.round(job.fit_score)}%`;
  return (
    <button className="best-match" onClick={() => onApply(job.id)}>
      <span className="bm-star">★</span>
      <span>
        <strong>{pct}</strong> — {job.title} @ {job.company}
        {job.location ? ` · ${job.location}` : ""}
      </span>
      <span className="bm-cta">→ apply</span>
    </button>
  );
}
