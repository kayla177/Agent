"use client";
import { useEffect, useState } from "react";
import type { AgentMeta } from "@/lib/agents";
import RunStream from "./RunStream";

/** The featured agent block under the hero — Preview / Run & send + live SSE.
 *  Keyed by agent.key in the parent so switching planets resets its run state. */
function FeaturedAgent({ agent }: { agent: AgentMeta }) {
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  async function run(send: 0 | 1) {
    if (send === 1 && !confirm(`Run ${agent.name} and deliver to Discord?`)) return;
    setStarting(true);
    const res = await fetch(`/agents/${agent.key}/run?send=${send}`, { method: "POST" });
    setStarting(false);
    if (res.ok) setRunId((await res.json()).run_id);
  }

  const busy = starting || runId !== null;

  return (
    <div className="featured">
      <h3>{agent.emoji} {agent.name}</h3>
      <p>{agent.description}</p>
      <div className="card-actions">
        <button className="primary" onClick={() => run(0)} disabled={busy}>
          {starting ? "starting…" : runId !== null ? "running…" : "▶ Preview"}
        </button>
        <button onClick={() => run(1)} disabled={busy}>Run &amp; send</button>
      </div>
      {runId !== null ? <RunStream runId={runId} /> : null}
    </div>
  );
}

export default function DashboardHero({ agents }: { agents: AgentMeta[] }) {
  const [selected, setSelected] = useState(0);
  const agent = agents[selected];

  // Re-theme the whole page to the selected agent's planet (like the old dashboard.js).
  useEffect(() => {
    document.body.dataset.planet = agent.planet;
  }, [agent.planet]);

  return (
    <>
      <div className="hero">
        <div className="hero-title">agents</div>
        <div className="planet-far" />
        <div className="planet" />
        <div className="hero-sub">daily</div>
        <div className="pill-nav">
          {agents.map((a, i) => (
            <button
              key={a.key}
              type="button"
              className={i === selected ? "on" : ""}
              onClick={() => setSelected(i)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
      <FeaturedAgent key={agent.key} agent={agent} />
    </>
  );
}
