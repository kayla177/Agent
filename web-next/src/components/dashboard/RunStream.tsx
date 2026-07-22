"use client";
import { useEffect, useState } from "react";

type NodeState = { node: string; status: string };

export default function RunStream({ runId, onDone }: { runId: number; onDone?: () => void }) {
  const [nodes, setNodes] = useState<NodeState[]>([]);
  const [outputHtml, setOutputHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const es = new EventSource(`/runs/${runId}/events`);
    es.addEventListener("node", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as NodeState;
      setNodes((cur) => {
        const i = cur.findIndex((n) => n.node === d.node);
        if (i >= 0) { const next = cur.slice(); next[i] = d; return next; }
        return [...cur, d];
      });
    });
    es.addEventListener("done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setOutputHtml(d.html || "");
      es.close();
      onDone?.();
    });
    es.addEventListener("failed", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "run failed");
      es.close();
      onDone?.();
    });
    return () => es.close();
  }, [runId, onDone]);

  const icon = (s: string) => (s === "finish" ? "✓" : s === "error" ? "✗" : s === "start" ? "◐" : "·");

  return (
    <div className="run-stream">
      <div className="node-log">
        {nodes.map((n) => (
          <div key={n.node} className={`node-line ${n.status}`}>
            <span className="ico">{icon(n.status)}</span>{n.node}
          </div>
        ))}
      </div>
      {outputHtml !== null ? <div className="output" dangerouslySetInnerHTML={{ __html: outputHtml }} /> : null}
      {error ? <pre className="output err">{error}</pre> : null}
    </div>
  );
}
