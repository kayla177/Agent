"use client";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import RunStream from "@/components/dashboard/RunStream";

// Run the job_scraper agent from the jobs panel and stream its progress; when it
// finishes, refresh the route so the new roles + counts + best-match appear.
export default function RunScraperButton() {
  const router = useRouter();
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The busy flag is reset in `finally` — guaranteed on every exit path,
  // including a rejected fetch (:8001 down) — so a network failure can never
  // strand the button permanently disabled.
  async function run() {
    setStarting(true);
    setError(null);
    try {
      const res = await fetch("/agents/job_scraper/run?send=0", { method: "POST" });
      if (!res.ok) {
        setError("Could not start the job scraper.");
        return;
      }
      setRunId((await res.json()).run_id);
    } catch {
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
    } finally {
      setStarting(false);
    }
  }

  const onDone = useCallback(() => {
    setRunId(null);
    router.refresh();
  }, [router]);

  const busy = starting || runId !== null;

  return (
    <div className="run-scraper">
      <button className="btn-run" onClick={run} disabled={busy}>
        {starting ? "starting…" : runId !== null ? "running…" : "▶ run scraper"}
      </button>
      {runId !== null ? <RunStream runId={runId} onDone={onDone} /> : null}
      {error ? <p className="banner err">{error}</p> : null}
    </div>
  );
}
