"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { STATUSES, type Application } from "@/lib/applications";
import StatusPill from "./StatusPill";

export default function ApplicationRow({ app }: { app: Application }) {
  const router = useRouter();
  const [status, setStatus] = useState(app.status);
  const [busy, setBusy] = useState(false);

  async function setNewStatus() {
    if (status === app.status) return;
    setBusy(true);
    const res = await fetch(`/api/applications/${app.id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  async function remove() {
    if (!confirm("Delete this application?")) return;
    setBusy(true);
    const res = await fetch(`/api/applications/${app.id}`, { method: "DELETE" });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  return (
    <tr>
      <td>{app.url ? <a href={app.url} target="_blank" rel="noopener">{app.company}</a> : app.company}</td>
      <td>{app.role}</td>
      <td><StatusPill status={app.status} auto={app.auto_detected === 1} /></td>
      <td className="muted">{app.applied_date}</td>
      <td className="muted">{app.updated_date}</td>
      <td className="muted">{app.notes}</td>
      <td>
        <select value={status} onChange={(e) => setStatus(e.target.value)} disabled={busy}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={setNewStatus} disabled={busy || status === app.status}>Set</button>
      </td>
      <td><button className="danger" onClick={remove} disabled={busy} aria-label="Delete">✕</button></td>
    </tr>
  );
}
