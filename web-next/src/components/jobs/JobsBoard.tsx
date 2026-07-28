"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { type Job, JOB_STATUSES, byPriority, byFitDesc, byDateDesc, bestMatch, inCountries, DEFAULT_COUNTRIES } from "@/lib/jobs";
import BestMatchHero from "./BestMatchHero";
import JobRow from "./JobRow";
import ApplyModal, { type ResumeRow } from "./ApplyModal";

type SortKey = "priority" | "fit" | "date";

export default function JobsBoard({ jobs, resumes, hasMaster }: {
  jobs: Job[]; resumes: ResumeRow[]; hasMaster: boolean;
}) {
  const router = useRouter();
  const [sort, setSort] = useState<SortKey>("priority");
  const [statusFilter, setStatusFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [hideGhost, setHideGhost] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [countries, setCountries] = useState<string[]>(DEFAULT_COUNTRIES);
  const [applyFor, setApplyFor] = useState<Job | null>(null);
  const [undo, setUndo] = useState<{ jobId: string; applicationId: number } | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const [undoBlocked, setUndoBlocked] = useState(false);

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
    l = l.filter((j) => inCountries(j, countries));
    l.sort(sort === "date" ? byDateDesc : sort === "fit" ? byFitDesc : byPriority);
    return l;
  }, [jobs, statusFilter, companyFilter, hideGhost, countries, sort]);

  // A rejected fetch (backend unreachable) is treated exactly like a failed
  // response: callers already handle `false` by clearing their busy flag
  // without refreshing, and never touching the DB.
  async function post(path: string, body: Record<string, unknown>) {
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  async function dismiss(id: string) {
    setBusyId(id);
    const ok = await post("/data/jobs/dismiss", { id });
    setBusyId(null);
    if (ok) router.refresh();
  }

  async function restore(id: string) {
    setBusyId(id);
    const ok = await post("/data/jobs/status", { id, status: "new" });
    setBusyId(null);
    if (ok) router.refresh();
  }

  // Expanding a row marks it viewed so you stop re-reading the same postings.
  function toggle(id: string) {
    setExpandedId((cur) => {
      const next = cur === id ? null : id;
      const job = jobs.find((j) => j.id === id);
      if (next === id && job?.status === "new") {
        // Marking a row viewed is incidental — a network failure here must
        // never surface an error or escape as an unhandled rejection.
        void post("/data/jobs/status", { id, status: "viewed" })
          .then(() => router.refresh())
          .catch(() => {});
      }
      return next;
    });
  }

  // Undo needs the actual status code (not just post()'s ok/false), so it makes
  // its own request rather than going through the shared helper. The busy flag
  // is reset in `finally` so it can never stick on a throw.
  async function doUndo() {
    if (!undo || undoBusy || undoBlocked) return;
    setUndoBusy(true);
    setUndoError(null);
    try {
      const res = await fetch("/data/jobs/undo-apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: undo.jobId, application_id: undo.applicationId }),
      });
      if (res.ok) {
        setUndo(null);
        router.refresh();
        return;
      }
      if (res.status === 404) {
        // Already undone (e.g. from another tab) — nothing left to revert, but
        // say so instead of letting the banner vanish silently.
        setUndo(null);
        setUndoError("This application was already undone.");
        return;
      }
      if (res.status === 409) {
        // Belongs to a different job, or predates job-linking — not retryable,
        // so disable Undo rather than leave a message the button contradicts.
        setUndoError("Can't undo this application here — it belongs to a different job, or predates job-linking.");
        setUndoBlocked(true);
        return;
      }
      setUndoError("Could not undo the application. Try again.");
    } catch {
      setUndoError("Could not reach the agent service (is FastAPI on :8001 running?).");
    } finally {
      setUndoBusy(false);
    }
  }

  return (
    <>
      {undo ? (
        <div className="banner ok undo-banner">
          Application logged.{" "}
          <button className="link" onClick={doUndo} disabled={undoBusy || undoBlocked}>
            {undoBusy ? "Undoing…" : "Undo"}
          </button>
        </div>
      ) : null}
      {undoError ? <p className="banner err">{undoError}</p> : null}

      {applyFor ? (
        <ApplyModal
          jobId={applyFor.id}
          jobTitle={applyFor.title}
          jobCompany={applyFor.company}
          jobUrl={applyFor.url}
          resumes={resumes}
          hasMaster={hasMaster}
          onClose={() => setApplyFor(null)}
          onApplied={(applicationId) => {
            setApplyFor(null);
            setUndo({ jobId: applyFor.id, applicationId });
            setUndoError(null);
            setUndoBlocked(false);
            router.refresh();
          }}
        />
      ) : null}

      <div className="job-chips">
        <span className="job-chip"><strong>{counts.new}</strong> new</span>
        <span className="job-chip"><strong>{counts.applied}</strong> applied</span>
        <span className="job-chip"><strong>{counts.dismissed}</strong> dismissed</span>
      </div>

      {best ? <BestMatchHero job={best} onApply={(id) => setApplyFor(jobs.find((j) => j.id === id) ?? null)} /> : null}

      <div className="job-filters">
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
          <option value="priority">sort: freshest</option>
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
        <select
          value={countries.includes("OTHER") ? "all" : "na"}
          onChange={(e) => setCountries(e.target.value === "all" ? ["US", "CA", "OTHER", "UNKNOWN"] : DEFAULT_COUNTRIES)}
        >
          <option value="na">US &amp; Canada</option>
          <option value="all">all countries</option>
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
            onToggle={toggle}
            onApply={(id) => setApplyFor(jobs.find((j) => j.id === id) ?? null)}
            onDismiss={dismiss}
            onRestore={restore}
          />
        ))
      )}
    </>
  );
}
