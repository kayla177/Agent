import type { Job } from "@/lib/jobs";
import { fitTier, FIT_COLOR, ageDays, parseAlsoOn } from "@/lib/jobs";

type Props = {
  job: Job;
  expanded: boolean;
  busy: boolean;
  onToggle: (id: string) => void;
  onApply: (id: string) => void;
  onDismiss: (id: string) => void;
};

export default function JobRow({ job, expanded, busy, onToggle, onApply, onDismiss }: Props) {
  const tier = fitTier(job.fit_score);
  const color = FIT_COLOR[tier];
  const age = ageDays(job.posted_at);
  const applied = job.status === "applied";
  const dim = applied || job.ghost === 1;
  const alsoOn = parseAlsoOn(job.also_on);

  return (
    <div className={`job-row${dim ? " dim" : ""}`}>
      <div className="job-head">
        <span className="fit-badge" style={{ color, borderColor: color }}>
          {job.fit_score === null ? "—" : Math.round(job.fit_score)}
        </span>
        <div className="job-main">
          <div className="job-title">{job.title}</div>
          <div className="job-sub">
            {job.company}{job.location ? ` · ${job.location}` : ""}
          </div>
          <div className="job-badges">
            {age !== null ? <span className="job-badge">🕒 {age}d ago</span> : null}
            {job.compensation ? <span className="job-badge">{job.compensation}</span> : null}
            {job.ghost === 1 ? <span className="job-badge stale">⚠ stale{age !== null ? ` ${age}d` : ""}</span> : null}
          </div>
        </div>
        <div className="job-actions">
          {applied ? (
            <span className="status-pill" style={{ color: "#7fc08a", borderColor: "#7fc08a", background: "#7fc08a1f" }}>applied</span>
          ) : (
            <>
              <button className="apply" disabled={busy} onClick={() => onApply(job.id)}>Apply</button>
              <button disabled={busy} onClick={() => onDismiss(job.id)}>Dismiss</button>
            </>
          )}
          <button className="job-caret" aria-label="Toggle details" onClick={() => onToggle(job.id)}>
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="job-detail">
          {job.fit_reason ? <div className="job-fit-reason">{job.fit_reason}</div> : null}
          {job.description ? (
            <p className="job-desc">
              {job.description.slice(0, 400)}
              {job.url ? <a href={job.url} target="_blank" rel="noopener noreferrer">open full posting ↗</a> : null}
            </p>
          ) : job.url ? (
            <p className="job-desc"><a href={job.url} target="_blank" rel="noopener noreferrer">open full posting ↗</a></p>
          ) : null}
          <div className="job-facts">
            {job.posted_at ? <span>posted {job.posted_at}</span> : null}
            {job.department ? <span>{job.department}</span> : null}
            {job.location ? <span>{job.location}{job.remote === 1 ? " · remote" : ""}</span> : null}
            {job.compensation ? <span>{job.compensation}</span> : null}
            {alsoOn.length ? <span>also on: {alsoOn.join(", ")}</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
