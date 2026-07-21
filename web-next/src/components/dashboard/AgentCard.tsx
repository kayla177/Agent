"use client";
import { useState } from "react";
import type { AgentMeta } from "@/lib/agents";
import RunStream from "./RunStream";

export default function AgentCard({ agent, last }: { agent: AgentMeta; last: { status: string; started_at: string } | null }) {
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  async function run() {
    setStarting(true);
    const res = await fetch(`/agents/${agent.key}/run?send=0`, { method: "POST" });
    setStarting(false);
    if (res.ok) {
      const d = await res.json();
      setRunId(d.run_id);
    }
  }

  return (
    <div className="agent-card">
      <h3>{agent.emoji} {agent.name}</h3>
      <p className="muted">{agent.description}</p>
      {last ? <p className="muted">last run: {last.status} · {last.started_at}</p> : <p className="muted">no runs yet</p>}
      <button className="primary" onClick={run} disabled={starting || runId !== null}>
        {starting ? "starting…" : runId !== null ? "running…" : "▶ Run (preview)"}
      </button>
      {runId !== null ? <RunStream runId={runId} /> : null}
    </div>
  );
}
