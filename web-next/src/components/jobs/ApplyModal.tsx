"use client";
import { useCallback, useState } from "react";
import RunStream from "@/components/dashboard/RunStream";
import {
  supportsAutofill, autofillUnavailableNote, verificationLine,
  HANDOFF_GROUPS, HANDOFF_GROUP_LABEL, handoffGroup, handoffReasonTag,
  labelIsUnverified,
  type HandoffGroup, type HandoffItem, type HandoffReport, type Verification,
} from "@/lib/jobs";

export type ResumeRow = { job_id: string; company: string; role: string };

const OFFLINE = "Could not reach the agent service (is FastAPI on :8001 running?).";

// THE ONE RULE for the whole assisted-apply feature: nothing ever submits an
// application. This component may offer to OPEN a form and FILL it; the only
// thing that sends it is the user, in the browser window, with her own hands.
// There is deliberately no "submit for me" control and no endpoint that would
// let there be one — `tests/test_applier_ui.py` scans this file for both.
//
// Apply is a two-part action: choose the résumé, THEN open the posting. The old
// one-click Apply marked a job applied without ever opening it, so the tracker
// claimed applications that had never happened.
export default function ApplyModal({
  jobId, jobTitle, jobCompany, jobUrl, jobAts, resumes, hasMaster, onClose, onApplied,
}: {
  jobId: string;
  jobTitle: string;
  jobCompany: string;
  jobUrl: string;
  // The posting's board, straight off the `jobs` row the scraper wrote. The ATS
  // is NOT re-detected here: `agents/job_scraper/ats.py` decided it when the
  // posting was fetched, and a second detector guessing from the URL is how the
  // UI and the agent end up disagreeing about what this form is.
  jobAts: string;
  resumes: ResumeRow[];
  hasMaster: boolean;
  onClose: () => void;
  // `pdfError` is non-null when the application WAS recorded but the résumé PDF
  // could not be produced. `verification` is non-null only on the assisted path,
  // and says whether anything actually vouched for the submission. Both are shown
  // by the board next to the green banner.
  onApplied: (
    applicationId: number,
    pdfError: string | null,
    verification: Verification | null,
  ) => void;
}) {
  // "" means the master résumé.
  const tailored = resumes.find((r) => r.job_id === jobId) ?? null;
  const [choice, setChoice] = useState<string>(tailored ? tailored.job_id : "");
  // Which of the two equal paths is selected. Kayla's ruling: these are equal
  // choices, not a primary with a fallback — she uses both depending on the
  // posting. Defaults to autofill where the board supports it.
  const [path, setPath] = useState<"autofill" | "manual">("autofill");
  const [busy, setBusy] = useState(false);
  const [genPhase, setGenPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // -- assisted-apply state --------------------------------------------------
  const [runId, setRunId] = useState<number | null>(null);
  const [runDone, setRunDone] = useState(false);
  const [report, setReport] = useState<HandoffReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [attachedName, setAttachedName] = useState<string | null>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  // Set once the human says she pressed Submit and the row was written.
  const [logged, setLogged] = useState<{ applicationId: number; pdfError: string | null } | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);

  // Only the fetch + run_id parse are guarded here — once the EventSource is
  // open, genPhase legitimately stays non-null while nodes report progress,
  // and the done/failed listeners below are what clear it. A `finally`
  // spanning the whole function would wipe that progress indicator the
  // instant the stream opened, so the reset lives in each early-return branch
  // instead (matching GenerateForm.tsx's identical fetch -> !res.ok -> SSE shape).
  async function generateTailored() {
    setGenPhase("starting");
    setError(null);
    let res: Response;
    try {
      res = await fetch("/agents/resume_generator/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: { job_id: jobId } }),
      });
    } catch {
      setGenPhase(null);
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
      return;
    }
    if (!res.ok) {
      setGenPhase(null);
      setError("Could not start résumé generation.");
      return;
    }
    const { run_id } = await res.json();
    const es = new EventSource(`/runs/${run_id}/events`);
    es.addEventListener("node", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setGenPhase(`${d.node} ${d.status === "finish" ? "✓" : "…"}`);
    });
    es.addEventListener("done", () => {
      es.close();
      setGenPhase(null);
      setChoice(jobId); // the tailored résumé now exists under this job id
    });
    es.addEventListener("failed", (e) => {
      es.close();
      setGenPhase(null);
      setError(JSON.parse((e as MessageEvent).data).error || "Résumé generation failed.");
    });
  }

  // The busy flag is reset in `finally` — guaranteed on every exit path,
  // including a rejected fetch — so a network failure can never leave the
  // modal stuck open with Cancel and the backdrop both disabled.
  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/data/jobs/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: jobId, resume_job_id: choice || null }),
      });
      if (!res.ok) {
        if (res.status === 409) {
          setError("This job was already applied to.");
        } else {
          setError("Could not record the application.");
        }
        return;
      }
      const { application_id, pdf_error } = await res.json();

      // Hand over the exact PDF to upload, then open the posting. Ordering note:
      // browsers block SUBSEQUENT popups from one user gesture, so it is the
      // FIRST window.open that is most likely to survive — the PDF goes first
      // deliberately, since the posting URL is also reachable from the row's
      // "open full posting" link while the PDF is not.
      //
      // Skip the PDF tab entirely when the server already told us the compile
      // failed: opening it would only render a 422 JSON error.
      if (!pdf_error) {
        const q = choice ? `?job_id=${encodeURIComponent(choice)}` : "";
        window.open(`/data/jobs/resume-pdf${q}`, "_blank", "noopener");
      }
      if (jobUrl) window.open(jobUrl, "_blank", "noopener");

      onApplied(application_id, pdf_error ?? null, null);
    } catch {
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
    } finally {
      setBusy(false);
    }
  }

  // --------------------------------------------------------------------------
  // Assisted apply
  // --------------------------------------------------------------------------

  // Starts the agent. It opens a visible browser window, fills what it can, and
  // stops — it does not submit, and this modal says so above the button that
  // starts it, not only in the report afterwards.
  async function startAutofill() {
    setBusy(true);
    setError(null);
    let res: Response;
    try {
      res = await fetch("/data/jobs/assisted-apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: jobId, resume_job_id: choice || null }),
      });
    } catch {
      setBusy(false);
      setError(OFFLINE);
      return;
    }
    let data: { run_id?: number; resume_filename?: string; pdf_error?: string | null; error?: string } = {};
    try {
      data = await res.json();
    } catch {
      // A body we cannot parse is still a response — do not claim the run
      // started when we have no run id to follow.
      data = {};
    }
    setBusy(false);
    if (!res.ok || !data.run_id) {
      setError(data.error || "Could not start assisted apply.");
      return;
    }
    setAttachedName(data.resume_filename || null);
    setAttachError(data.pdf_error ?? null);
    setRunId(data.run_id);
  }

  // Fetched when the run ends. `useCallback` keyed on the run id keeps the
  // identity stable, because RunStream re-opens its EventSource whenever its
  // onDone changes — an inline arrow here would reconnect on every render.
  const onRunDone = useCallback(() => {
    setRunDone(true);
    const id = runId;
    if (id === null) return;
    void (async () => {
      let res: Response;
      try {
        res = await fetch(`/data/jobs/assisted-apply/report?run_id=${id}`);
      } catch {
        setReportError(OFFLINE);
        return;
      }
      if (!res.ok) {
        setReportError(
          "The run finished but its checklist could not be loaded. Its full text " +
          "report is on the dashboard under this run.",
        );
        return;
      }
      try {
        setReport((await res.json()) as HandoffReport);
      } catch {
        setReportError("The run's checklist came back unreadable.");
      }
    })();
  }, [runId]);

  // The human says she pressed Submit. TWO steps, in this order:
  //   1. log it (the same endpoint Phase A uses, so Undo and the pinned résumé
  //      key work identically) — this is an OPTIMISTIC row, exactly as before;
  //   2. ask the backend to re-read the form window and verify it.
  // Step 2 failing changes nothing about step 1: the application is logged
  // either way, and the tracker shows it as unverified rather than as unsent.
  async function logAndVerify() {
    setBusy(true);
    setError(null);
    let applicationId = logged?.applicationId ?? 0;
    let pdfError = logged?.pdfError ?? null;
    try {
      if (!applicationId) {
        const res = await fetch("/data/jobs/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: jobId, resume_job_id: choice || null }),
        });
        if (!res.ok) {
          setError(
            res.status === 409
              ? "This job was already logged as applied — nothing further to record."
              : "Could not record the application.",
          );
          return;
        }
        const data = await res.json();
        applicationId = Number(data.application_id);
        pdfError = data.pdf_error ?? null;
        setLogged({ applicationId, pdfError });
      }

      const check = await fetch("/data/jobs/confirm-submission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: jobId, application_id: applicationId }),
      });
      if (!check.ok) {
        setVerification({
          checked: false,
          confirmed: false,
          reason: "the verification request was rejected",
        });
        return;
      }
      const v = await check.json();
      setVerification({
        checked: Boolean(v.checked),
        confirmed: Boolean(v.confirmed),
        reason: String(v.reason || ""),
      });
    } catch {
      // The row may or may not exist; say only what is known.
      setError(OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  async function closeWindow() {
    setBusy(true);
    try {
      await fetch("/data/jobs/assisted-apply/close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: jobId }),
      });
    } catch {
      setError("Could not reach the agent service to close the browser window.");
    } finally {
      setBusy(false);
    }
  }

  // Leaving the modal. Once an application has been logged the parent has to be
  // told, or the board keeps showing the role as un-applied until a reload.
  function dismiss() {
    if (busy) return;
    if (logged) {
      onApplied(logged.applicationId, logged.pdfError, verification);
      return;
    }
    onClose();
  }

  const canAutofill = supportsAutofill(jobAts);
  // The effective action, not merely the selected radio. On a board without
  // autofill the radiogroup never renders, so `path` keeps its "autofill"
  // default while the button actually calls `confirm` — keying the résumé guard
  // off `path` alone therefore dropped it entirely on those boards.
  const autofillSelected = path === "autofill" && canAutofill;

  return (
    <div className="modal-backdrop" onClick={dismiss}>
      <div className="modal apply-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Apply — {jobTitle}</h2>
        <p className="muted apply-subhead">
          {jobCompany}
          <span className="job-badge src">{jobAts}</span>
        </p>

        {/* `runId === null` rather than a `started` flag so TypeScript narrows it
            to a number in the branch that hands it to RunStream. */}
        {runId === null ? (
          <>
            <label className="apply-field-label" htmlFor="apply-resume">Résumé to use</label>
            <select
              id="apply-resume"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              disabled={busy}
            >
              <option value="" disabled={!hasMaster}>
                {hasMaster ? "master résumé" : "master résumé (not set)"}
              </option>
              {tailored ? (
                <option value={tailored.job_id}>tailored for this job ★</option>
              ) : null}
              {resumes
                .filter((r) => r.job_id !== jobId)
                .map((r) => (
                  <option key={r.job_id} value={r.job_id}>
                    reuse: {r.role || "(untitled)"}{r.company ? ` @ ${r.company}` : ""}
                  </option>
                ))}
            </select>

            {!tailored ? (
              <p className="muted">
                No résumé tailored for this role yet.{" "}
                <button className="link" onClick={generateTailored} disabled={busy || genPhase !== null}>
                  {genPhase ? `generating… ${genPhase}` : "Generate one"}
                </button>{" "}
                — or apply with the master résumé now.
              </p>
            ) : null}

            {canAutofill ? (
              <>
                <span className="apply-field-label" id="apply-path-label">How do you want to apply?</span>
                <div className="apply-paths" role="radiogroup" aria-labelledby="apply-path-label">
                  <label className={`apply-path ${path === "autofill" ? "on" : ""}`}>
                    <input
                      type="radio"
                      name="apply-path"
                      checked={path === "autofill"}
                      onChange={() => setPath("autofill")}
                      disabled={busy}
                    />
                    <span>
                      <strong>Let the agent fill the form</strong>
                      {/* Said BEFORE anything starts, not only in the report
                          afterwards. A user who learns this after a browser
                          window has appeared has already been surprised by it —
                          which is why a test pins this sentence ABOVE the
                          button that starts the run. */}
                      <span className="muted small">
                        Opens this {jobAts} form in a browser window on your screen and fills
                        what it can from your profile, attaching the résumé above last.
                      </span>
                      <span className="apply-never">
                        It fills the form. It does not submit it — and it never will.
                        Nothing is sent until you read every field yourself and press Submit
                        in that window. Work-authorization and self-identification questions
                        are always left for you.
                      </span>
                    </span>
                  </label>
                  <label className={`apply-path ${path === "manual" ? "on" : ""}`}>
                    <input
                      type="radio"
                      name="apply-path"
                      checked={path === "manual"}
                      onChange={() => setPath("manual")}
                      disabled={busy}
                    />
                    <span>
                      <strong>I&rsquo;ll fill it in myself</strong>
                      <span className="muted small">
                        Opens the posting and your résumé PDF in new tabs, and logs the
                        application. You can undo it.
                      </span>
                    </span>
                  </label>
                </div>
              </>
            ) : null}

            {error ? <p className="banner err">{error}</p> : null}

            {/* On a board without autofill this sentence otherwise lives nowhere:
                it is normally part of the "I'll fill it in myself" radio card,
                which never renders here because the radiogroup itself never
                renders. Same wording as that card — one phrasing, not two — and
                placed above its control, which is this whole feature's rule. */}
            {!canAutofill ? (
              <p className="muted small">
                Opens the posting and your résumé PDF in new tabs, and logs the
                application. You can undo it.
              </p>
            ) : null}

            <div className="modal-actions">
              <button onClick={dismiss} disabled={busy}>Cancel</button>
              <button
                className="primary"
                onClick={autofillSelected ? startAutofill : confirm}
                disabled={busy || (!autofillSelected && !hasMaster && !choice)}
              >
                {busy
                  ? autofillSelected ? "Starting…" : "Recording…"
                  : autofillSelected
                    ? "Open the form & autofill it"
                    : "Download résumé, open posting & log it"}
              </button>
            </div>

            {!canAutofill ? (
              <div className="apply-unavailable">
                <strong>Autofill isn&rsquo;t available for this posting</strong>
                <span className="muted small">{autofillUnavailableNote(jobAts)}</span>
              </div>
            ) : null}
          </>
        ) : (
          <>
            {/* Repeated during the run: the window is on screen being typed
                into, which is the moment the guarantee matters most. */}
            <p className="banner">
              The agent is filling this form. It will not submit it — you do that
              yourself, in the browser window it opened.
            </p>
            {attachedName ? (
              <p className="muted small">Attaching {attachedName} last, after the fields.</p>
            ) : null}
            {attachError ? (
              <p className="banner err">
                No résumé will be attached — the PDF could not be prepared: {attachError}
              </p>
            ) : null}

            <RunStream runId={runId} onDone={onRunDone} showOutput={false} />

            {runDone && report ? <Handoff report={report} /> : null}
            {runDone && !report && reportError ? <p className="banner err">{reportError}</p> : null}

            {error ? <p className="banner err">{error}</p> : null}
            {verification ? (
              <p className={`banner ${verification.confirmed ? "ok" : ""}`}>
                {verificationLine(verification)}
              </p>
            ) : null}

            <div className="modal-actions">
              <button onClick={dismiss} disabled={busy}>
                {logged ? "Done" : "Close"}
              </button>
              {runDone ? (
                <>
                  <button onClick={closeWindow} disabled={busy}>
                    Close the browser window
                  </button>
                  <button className="primary" onClick={logAndVerify} disabled={busy}>
                    {busy
                      ? "Checking…"
                      : verification
                        ? "Check the form again"
                        : "I pressed Submit — log it & verify"}
                  </button>
                </>
              ) : null}
            </div>
            {runDone ? (
              <p className="muted small">
                Press “I pressed Submit” only after you have actually sent the form.
                It logs the application and then re-reads the page to see whether the
                board shows a confirmation.
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

// The checklist. Ordered by what stops you submitting, not by what the agent did:
// blocking first, `done` collapsed last. All of it comes from the report's own
// `items` — nothing here parses the rendered text.
function Handoff({ report }: { report: HandoffReport }) {
  return (
    <div className="handoff">
      {report.error ? (
        <p className="banner err">
          The agent stopped early: {report.error} Treat the form as unfilled until you
          have checked it.
        </p>
      ) : null}
      <p className="handoff-headline">{report.headline}</p>
      <p>{report.summary_line}</p>
      {/* Directly under the count it qualifies — a caveat below the checklist
          would leave the number standing unqualified where she reads it. */}
      {report.required_caveat ? (
        <p className="muted small">{report.required_caveat}</p>
      ) : null}
      {report.resume_note ? <p className="muted small">Résumé: {report.resume_note}</p> : null}
      <p className="muted small">{report.instruction}</p>
      {report.form_url ? (
        <p className="muted small">
          <a href={report.form_url} target="_blank" rel="noopener noreferrer">
            open the form in your own browser ↗
          </a>
        </p>
      ) : null}

      {HANDOFF_GROUPS.map((group) => {
        const items = handoffGroup(report, group);
        if (!items.length) return null;
        return (
          <div key={group} className={`handoff-band ${group}`}>
            {group === "done" ? (
              <details>
                <summary>
                  {HANDOFF_GROUP_LABEL[group]} ({items.length})
                </summary>
                {items.map((i) => <HandoffLine key={i.key} item={i} group={group} />)}
              </details>
            ) : (
              <>
                <h4>
                  {HANDOFF_GROUP_LABEL[group]} ({items.length})
                </h4>
                {items.map((i) => <HandoffLine key={i.key} item={i} group={group} />)}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

const NO_LABEL = "(this question has no readable label on the page)";

function HandoffLine({ item, group }: { item: HandoffItem; group: HandoffGroup }) {
  const compact = group === "done";
  return (
    <div className={`handoff-item ${item.group}`}>
      <div className="handoff-label">
        {item.label || NO_LABEL}
        {item.required ? <span className="handoff-tag req">required</span> : null}
        {labelIsUnverified(item) ? (
          <span className="handoff-tag req">LABEL UNVERIFIED</span>
        ) : null}
        <span className="handoff-tag">{handoffReasonTag(item)}</span>
      </div>
      {/* The title of this row is a placeholder or a field name, so everything
          else on it was decided by classifying that string as a question. */}
      {labelIsUnverified(item) ? (
        <div className="muted small">
          the agent could not read a label for this field, so “{item.label}” is only
          the {item.label_source} of the box, not the question. Anything else on this
          row was decided from that text and may be wrong about what the field is
          asking — find it on the page and read it yourself.
        </div>
      ) : null}
      {compact ? (
        item.value || item.intended ? (
          <div className="muted small">{excerpt(item.value || item.intended, 60)}</div>
        ) : null
      ) : (
        <>
          {item.reason === "changed" ? (
            <div className="muted small">
              the page now holds “{excerpt(item.value, 220)}” — you asked it for “
              {excerpt(item.intended, 220)}”.
            </div>
          ) : null}
          {item.reason === "drafted" ? (
            <div className="muted small">typed in: “{excerpt(item.value, 220)}”</div>
          ) : null}
          {item.suggestion ? (
            <div className="muted small">
              SUGGESTION, NOT ENTERED: your profile implies “{excerpt(item.suggestion, 220)}”.
              The agent did not type it — confirm it yourself.
            </div>
          ) : null}
          {item.note ? <div className="muted small">{excerpt(item.note, 480)}</div> : null}
        </>
      )}
    </div>
  );
}

// One line, length-capped. The AI-draft marker is part of the value the report
// sent and is never stripped here; a truncated draft keeps the marker because the
// marker sits at the front of it.
function excerpt(text: string, cap: number): string {
  const flat = (text || "").split(/\s+/).filter(Boolean).join(" ");
  return flat.length <= cap ? flat : `${flat.slice(0, cap).trimEnd()} …`;
}
