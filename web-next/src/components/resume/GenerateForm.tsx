"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

type JobOption = {
  id: string;
  title: string;
  company: string;
  fit_score: number | null;
};

// Trigger the per-job resume generator through the slim FastAPI agent service.
// Mirrors SyncGmail: POST the run (with a job_id input), stream node progress
// over SSE, then refresh so the new draft shows in the list below.
export default function GenerateForm({
  jobs,
  poolReady,
}: {
  jobs: JobOption[];
  poolReady: boolean;
}) {
  const router = useRouter();
  const [jobId, setJobId] = useState(jobs[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  const [resultHtml, setResultHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (jobs.length === 0) {
    return <p className="muted">No scraped jobs with a description yet — run the job scraper first.</p>;
  }

  async function generate() {
    setBusy(true);
    setPhase("starting");
    setResultHtml(null);
    setError(null);

    let res: Response;
    try {
      res = await fetch("/agents/resume_generator/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: { job_id: jobId } }),
      });
    } catch {
      setBusy(false);
      setPhase(null);
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
      return;
    }
    if (!res.ok) {
      setBusy(false);
      setPhase(null);
      setError("Could not start resume generation.");
      return;
    }

    const { run_id } = await res.json();
    let done = false;
    const es = new EventSource(`/runs/${run_id}/events`);
    const finish = () => {
      es.close();
      setBusy(false);
      setPhase(null);
    };
    es.addEventListener("node", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setPhase(`${d.node} ${d.status === "finish" ? "✓" : "…"}`);
    });
    es.addEventListener("done", (e) => {
      done = true;
      const d = JSON.parse((e as MessageEvent).data);
      setResultHtml(d.html || "");
      finish();
      router.refresh();
    });
    es.addEventListener("failed", (e) => {
      done = true;
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "Resume generation failed.");
      finish();
    });
    es.onerror = () => {
      if (done || es.readyState !== EventSource.CLOSED) return;
      setError("Lost connection to the agent service.");
      finish();
    };
  }

  return (
    <div className="generate-form">
      {!poolReady ? (
        <div className="banner err">Add your resume/projects to the experience pool first.</div>
      ) : null}
      <div className="generate-row">
        <select value={jobId} onChange={(e) => setJobId(e.target.value)} disabled={busy}>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.title} @ {j.company}
              {j.fit_score != null ? ` · fit ${Math.round(j.fit_score)}` : ""}
            </option>
          ))}
        </select>
        <button className="primary" onClick={generate} disabled={busy}>
          {busy ? "Generating…" : "Generate resume"}
        </button>
        {busy && phase ? <span className="muted gen-phase">{phase}</span> : null}
      </div>
      {resultHtml !== null ? (
        <div className="banner ok gen-result" dangerouslySetInnerHTML={{ __html: resultHtml }} />
      ) : null}
      {error ? <p className="banner err">{error}</p> : null}
    </div>
  );
}
