"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { type Job, JOB_STATUSES, byFitDesc, byDateDesc, bestMatch } from "@/lib/jobs";
import BestMatchHero from "./BestMatchHero";
import JobRow from "./JobRow";

export default function JobsBoard({ jobs }: { jobs: Job[] }) {
  const router = useRouter();
  const [sort, setSort] = useState<"fit" | "date">("fit");
  const [statusFilter, setStatusFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [hideGhost, setHideGhost] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const c = { new: 0, applied: 0, dismissed: 0 };
    for (const j of jobs) if (j.status in c) c[j.status as keyof typeof c] += 1;
    return c;
  }, [jobs]);

  const companies = useMemo(
    () => Array.from(new Set(jobs.map((j) => j.company).filter(Boolean))).sort(),
    [jobs],
  );

  const best = useMemo(() => bestMatch(jobs), [jobs]);

  const list = useMemo(() => {
    let l = jobs.slice();
    l = statusFilter ? l.filter((j) => j.status === statusFilter) : l.filter((j) => j.status !== "dismissed");
    if (companyFilter) l = l.filter((j) => j.company === companyFilter);
    if (hideGhost) l = l.filter((j) => j.ghost !== 1);
    l.sort(sort === "date" ? byDateDesc : byFitDesc);
    return l;
  }, [jobs, statusFilter, companyFilter, hideGhost, sort]);

  async function mutate(kind: "apply" | "dismiss", id: string) {
    setBusyId(id);
    const res = await fetch(`/api/jobs/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    setBusyId(null);
    if (res.ok) router.refresh();
  }

  return (
    <>
      <div className="job-chips">
        <span className="job-chip"><strong>{counts.new}</strong> new</span>
        <span className="job-chip"><strong>{counts.applied}</strong> applied</span>
        <span className="job-chip"><strong>{counts.dismissed}</strong> dismissed</span>
      </div>

      {best ? <BestMatchHero job={best} onApply={(id) => mutate("apply", id)} /> : null}

      <div className="job-filters">
        <select value={sort} onChange={(e) => setSort(e.target.value as "fit" | "date")}>
          <option value="fit">sort: fit</option>
          <option value="date">sort: date</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">all statuses</option>
          {JOB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={companyFilter} onChange={(e) => setCompanyFilter(e.target.value)}>
          <option value="">all companies</option>
          {companies.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <label>
          <input type="checkbox" checked={hideGhost} onChange={(e) => setHideGhost(e.target.checked)} />
          hide stale/ghost
        </label>
      </div>

      {list.length === 0 ? (
        <p className="muted">No roles match these filters.</p>
      ) : (
        list.map((j) => (
          <JobRow
            key={j.id}
            job={j}
            expanded={expandedId === j.id}
            busy={busyId === j.id}
            onToggle={(id) => setExpandedId((cur) => (cur === id ? null : id))}
            onApply={(id) => mutate("apply", id)}
            onDismiss={(id) => mutate("dismiss", id)}
          />
        ))
      )}
    </>
  );
}
