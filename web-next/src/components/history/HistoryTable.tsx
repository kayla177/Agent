"use client";
import { Fragment, useState } from "react";
import type { Run } from "@/lib/runs";

export default function HistoryTable({ runs }: { runs: Run[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <table className="apps">
      <thead>
        <tr><th>Agent</th><th>Status</th><th>Started (UTC)</th><th>Finished</th><th></th></tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <Fragment key={r.id}>
            <tr onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: "pointer" }}>
              <td>{r.agent_key}</td>
              <td><span className={`badge ${r.status}`}>{r.status}</span></td>
              <td className="muted">{r.started_at}</td>
              <td className="muted">{r.finished_at ?? "—"}</td>
              <td>{open === r.id ? "▾" : "▸"}</td>
            </tr>
            {open === r.id ? (
              <tr>
                <td colSpan={5}>
                  {r.error ? (
                    <pre className="output err">{r.error}</pre>
                  ) : (
                    <pre className="output" style={{ whiteSpace: "pre-wrap" }}>{r.output_message || "No output."}</pre>
                  )}
                </td>
              </tr>
            ) : null}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
