"use client";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import RunStream from "@/components/dashboard/RunStream";

// Kicks off an LLM refinement pass over the unscored backlog. Kept SEPARATE from
// "run scraper" because refinement costs ~6.5s per posting: the scraper button
// must stay fast, while this one is an explicit, slow, opt-in action. Styled as
// a secondary/muted action (vs. the accent-filled "run scraper" pill) so it
// reads as the less-common, costlier choice.
export default function ScoreBacklogButton() {
  const router = useRouter();
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  async function run() {
    setStarting(true);
    const res = await fetch("/agents/job_scraper/run?send=0", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: { backfill: true } }),
    });
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
      <button
        className="btn-secondary"
        onClick={run}
        disabled={busy}
        title="Score unscored postings with the local model (slow — ~6.5s per posting)"
      >
        {starting ? "starting…" : runId !== null ? "scoring…" : "◔ score backlog"}
      </button>
      {runId !== null ? <RunStream runId={runId} onDone={onDone} /> : null}
    </div>
  );
}
