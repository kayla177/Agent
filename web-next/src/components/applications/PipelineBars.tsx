import { STATUSES, STATUS_META, type Stats } from "@/lib/applications";

export default function PipelineBars({ counts }: { counts: Stats["counts"] }) {
  const max = Math.max(1, ...STATUSES.map((s) => counts[s]));
  return (
    <div className="pipeline">
      {STATUSES.map((s) => {
        const n = counts[s];
        const pct = Math.round((n / max) * 100);
        return (
          <div key={s} className="pipeline-row">
            <span className="pipeline-label">{STATUS_META[s].label}</span>
            <span className="pipeline-track">
              <span className="pipeline-fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="pipeline-count">{n}</span>
          </div>
        );
      })}
    </div>
  );
}
