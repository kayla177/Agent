"use client";
import { useState } from "react";

export type ResumeRow = { job_id: string; company: string; role: string };

// Apply is a two-part action: choose the résumé, THEN open the posting. The old
// one-click Apply marked a job applied without ever opening it, so the tracker
// claimed applications that had never happened.
export default function ApplyModal({
  jobId, jobTitle, jobCompany, jobUrl, resumes, hasMaster, onClose, onApplied,
}: {
  jobId: string;
  jobTitle: string;
  jobCompany: string;
  jobUrl: string;
  resumes: ResumeRow[];
  hasMaster: boolean;
  onClose: () => void;
  // `pdfError` is non-null when the application WAS recorded but the résumé PDF
  // could not be produced. The board shows it next to the green banner.
  onApplied: (applicationId: number, pdfError: string | null) => void;
}) {
  // "" means the master résumé.
  const tailored = resumes.find((r) => r.job_id === jobId) ?? null;
  const [choice, setChoice] = useState<string>(tailored ? tailored.job_id : "");
  const [busy, setBusy] = useState(false);
  const [genPhase, setGenPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

      onApplied(application_id, pdf_error ?? null);
    } catch {
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}>
      <div className="modal apply-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Apply — {jobTitle}</h2>
        <p className="muted">{jobCompany}</p>

        <label>
          Résumé to use
          <select value={choice} onChange={(e) => setChoice(e.target.value)} disabled={busy}>
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
        </label>

        {!tailored ? (
          <p className="muted">
            No résumé tailored for this role yet.{" "}
            <button className="link" onClick={generateTailored} disabled={busy || genPhase !== null}>
              {genPhase ? `generating… ${genPhase}` : "Generate one"}
            </button>{" "}
            — or apply with the master résumé now.
          </p>
        ) : null}

        {error ? <p className="banner err">{error}</p> : null}

        <div className="modal-actions">
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button className="primary" onClick={confirm} disabled={busy || (!hasMaster && !choice)}>
            {busy ? "Recording…" : "Download résumé, open posting & log it"}
          </button>
        </div>
        <p className="muted small">
          Opens the posting and your résumé PDF in new tabs, and logs the application. You can undo it.
        </p>
      </div>
    </div>
  );
}
