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

  async function run() {
    setStarting(true);
    const res = await fetch("/agents/job_scraper/run?send=0", { method: "POST" });
    setStarting(false);
    if (res.ok) setRunId((await res.json()).run_id);
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
    </div>
  );
}
