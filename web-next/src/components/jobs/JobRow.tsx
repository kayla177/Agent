import type { Job } from "@/lib/jobs";
import { fitTier, FIT_COLOR, JOB_STATUS_META, ageDays, parseAlsoOn, COUNTRY_LABEL, ghostLabel } from "@/lib/jobs";

type Props = {
  job: Job;
  expanded: boolean;
  busy: boolean;
  onToggle: (id: string) => void;
  onApply: (id: string) => void;
  onDismiss: (id: string) => void;
  onRestore: (id: string) => void;
};

export default function JobRow({ job, expanded, busy, onToggle, onApply, onDismiss, onRestore }: Props) {
  const tier = fitTier(job.fit_score);
  const color = FIT_COLOR[tier];
  const age = ageDays(job.posted_at);
  const applied = job.status === "applied";
  const dismissed = job.status === "dismissed";
  const dim = applied || dismissed || job.ghost === 1;
  const alsoOn = parseAlsoOn(job.also_on);
  const st = JOB_STATUS_META[job.status] ?? { label: job.status, color: "#8b93a6" };
  const actionable = !applied && !dismissed;

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
            <span className="status-pill" style={{ color: st.color, borderColor: st.color, background: `${st.color}1f` }}>{st.label}</span>
            {age !== null ? <span className="job-badge">🕒 {age}d ago</span> : null}
            {job.compensation ? <span className="job-badge">{job.compensation}</span> : null}
            {job.ats ? <span className="job-badge src">{job.ats}</span> : null}
            {job.country && job.country !== "US" ? (
              <span className="job-badge">{COUNTRY_LABEL[job.country] ?? job.country}</span>
            ) : null}
            {alsoOn.length ? <span className="job-badge">also on {alsoOn.length}</span> : null}
            {/* Say WHY it is flagged. Rendering every ghost as "stale" made a
                posting detected as removed from its board read as "stale 3d",
                which buried the headline fix of this whole branch. */}
            {job.ghost === 1 ? (
              <span className="job-badge stale" title={job.ghost_reason || undefined}>
                ⚠ {ghostLabel(job, age)}
              </span>
            ) : null}
          </div>
        </div>
        <div className="job-actions">
          {actionable ? (
            <>
              <button className="apply" disabled={busy} onClick={() => onApply(job.id)}>Apply</button>
              <button disabled={busy} onClick={() => onDismiss(job.id)}>Dismiss</button>
            </>
          ) : null}
          {dismissed ? (
            <button disabled={busy} onClick={() => onRestore(job.id)}>Restore</button>
          ) : null}
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
