"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { type Job, type Verification, verificationLine, JOB_STATUSES, byPriority, byFitDesc, byDateDesc, bestMatch, inCountries, isScreenedOut, ALL_COUNTRIES, COUNTRY_LABEL } from "@/lib/jobs";
import BestMatchHero from "./BestMatchHero";
import JobRow from "./JobRow";
import ApplyModal, { type ResumeRow } from "./ApplyModal";

type SortKey = "priority" | "fit" | "date";

const OFFLINE = "Could not reach the agent service (is FastAPI on :8001 running?).";

export default function JobsBoard({ jobs, resumes, hasMaster, initialCountries }: {
  jobs: Job[]; resumes: ResumeRow[]; hasMaster: boolean;
  // The effective config.JOB_COUNTRIES (plus UNKNOWN), resolved server-side.
  initialCountries: string[];
}) {
  const router = useRouter();
  const [sort, setSort] = useState<SortKey>("priority");
  const [statusFilter, setStatusFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [hideGhost, setHideGhost] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showAllCountries, setShowAllCountries] = useState(false);
  // Screened-out roles are hidden by DEFAULT but always reachable, mirroring the
  // country control. The gate is only ~two-thirds accurate, so this toggle is
  // the recovery path for a good role it wrongly rejected.
  const [showScreenedOut, setShowScreenedOut] = useState(false);
  const [applyFor, setApplyFor] = useState<Job | null>(null);
  const [undo, setUndo] = useState<{ jobId: string; applicationId: number } | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const [undoBlocked, setUndoBlocked] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  // Only ever set by the assisted-apply path: whether anything actually vouched
  // for the submission. Null means nothing checked (the manual flow), which is
  // NOT the same as "not submitted" — see ApplicationRow's ConfirmedMark.
  const [verification, setVerification] = useState<Verification | null>(null);

  const countries = showAllCountries ? ALL_COUNTRIES : initialCountries;
  // Label the preference option with what it actually contains, so the control
  // stays honest if JOB_COUNTRIES is something other than US+CA. UNKNOWN is
  // always in the list and is never a user-facing choice, so it isn't named.
  const prefLabel =
    initialCountries.filter((c) => c !== "UNKNOWN").map((c) => COUNTRY_LABEL[c] ?? c).join(" & ")
    || "preferred countries";

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
    if (!showScreenedOut) l = l.filter((j) => !isScreenedOut(j));
    l = l.filter((j) => inCountries(j, countries));
    l.sort(sort === "date" ? byDateDesc : sort === "fit" ? byFitDesc : byPriority);
    return l;
  }, [jobs, statusFilter, companyFilter, hideGhost, showScreenedOut, countries, sort]);

  // Only offer the reveal control when there is something to reveal, and say how
  // many — an always-on checkbox that does nothing reads as broken, and the
  // count is what tells the user whether the screen is being over-eager.
  const screenedOutCount = useMemo(
    () => jobs.filter((j) => isScreenedOut(j) && j.status !== "dismissed").length,
    [jobs],
  );

  // A rejected fetch (backend unreachable) is distinguished from a failed
  // response, because the two need different wording. Callers clear their busy
  // flag either way and never refresh on failure.
  async function post(path: string, body: Record<string, unknown>): Promise<"ok" | "failed" | "offline"> {
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return res.ok ? "ok" : "failed";
    } catch {
      return "offline";
    }
  }

  // dismiss/restore used to surface NOTHING on failure — the row simply didn't
  // change, which reads as an unresponsive button. Undo already reports both
  // failure modes; these now do the same.
  async function mutateRow(id: string, path: string, body: Record<string, unknown>, verb: string) {
    setBusyId(id);
    setRowError(null);
    const result = await post(path, body);
    setBusyId(null);
    if (result === "ok") {
      router.refresh();
      return;
    }
    setRowError(result === "offline" ? OFFLINE : `Could not ${verb} that role. Try again.`);
  }

  function dismiss(id: string) {
    return mutateRow(id, "/data/jobs/dismiss", { id }, "dismiss");
  }

  function restore(id: string) {
    return mutateRow(id, "/data/jobs/status", { id, status: "new" }, "restore");
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
      setUndoError(OFFLINE);
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
      {/* Shown ALONGSIDE the green banner, never instead of it: the application
          really was logged, but claiming unqualified success while the résumé
          PDF failed (and opening a tab that renders a 422) was dishonest. */}
      {pdfError ? (
        <p className="banner err">
          The application was logged, but your résumé PDF could not be prepared, so
          nothing was attached: {pdfError}
        </p>
      ) : null}
      {undoError ? <p className="banner err">{undoError}</p> : null}
      {rowError ? <p className="banner err">{rowError}</p> : null}
      {/* Shown next to the green banner, like pdfError: a verified application
          and an unverified one are both logged, and the difference has to be
          visible at the moment it is decided as well as later in the tracker. */}
      {verification ? (
        <p className={`banner ${verification.confirmed ? "ok" : ""}`}>
          {verificationLine(verification)}
        </p>
      ) : null}

      {applyFor ? (
        <ApplyModal
          jobId={applyFor.id}
          jobTitle={applyFor.title}
          jobCompany={applyFor.company}
          jobUrl={applyFor.url}
          jobAts={applyFor.ats}
          resumes={resumes}
          hasMaster={hasMaster}
          onClose={() => setApplyFor(null)}
          onApplied={(applicationId, applyPdfError, applyVerification) => {
            setApplyFor(null);
            setUndo({ jobId: applyFor.id, applicationId });
            setUndoError(null);
            setUndoBlocked(false);
            setPdfError(applyPdfError);
            setVerification(applyVerification);
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
          value={showAllCountries ? "all" : "pref"}
          onChange={(e) => setShowAllCountries(e.target.value === "all")}
        >
          <option value="pref">{prefLabel}</option>
          <option value="all">all countries</option>
        </select>
        <label>
          <input type="checkbox" checked={hideGhost} onChange={(e) => setHideGhost(e.target.checked)} />
          hide stale/ghost
        </label>
        {screenedOutCount > 0 ? (
          <label title="Roles the undergrad-eligibility screen rejected. The screen is imperfect — reveal these to check what it hid.">
            <input
              type="checkbox"
              checked={showScreenedOut}
              onChange={(e) => setShowScreenedOut(e.target.checked)}
            />
            show screened-out ({screenedOutCount})
          </label>
        ) : null}
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
