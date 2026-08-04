"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { STATUSES, type Application } from "@/lib/applications";
import StatusPill from "./StatusPill";

// A generated résumé the application can be linked to (from the resumes table).
export type ResumeOption = { job_id: string; company: string; role: string };

// Verified vs optimistic. Every row here is written the moment the apply modal
// is confirmed, whether or not a form was ever really sent; `confirmed_at` is
// only stamped once an ATS confirmation page has been seen
// (agents/job_applier/confirm.py). The two states are therefore "verified" and
// "not verified" — NOT "submitted" and "not submitted". Saying "not submitted"
// here would be the same false claim in the opposite direction: nothing has
// checked most of these rows at all.
function ConfirmedMark({ confirmedAt }: { confirmedAt?: string | null }) {
  if (confirmedAt) {
    return (
      <span className="confirmed-mark" title={`Confirmation page seen ${confirmedAt}`}>
        ✓ verified
      </span>
    );
  }
  return (
    <span className="muted" title="Logged when you confirmed the apply modal; no ATS confirmation page has been checked">
      unverified
    </span>
  );
}

export default function ApplicationRow({
  app,
  resumeOptions,
}: {
  app: Application;
  resumeOptions: ResumeOption[];
}) {
  const router = useRouter();
  const [status, setStatus] = useState(app.status);
  const [busy, setBusy] = useState(false);
  const linked = resumeOptions.find((r) => r.job_id === app.resume_job_id) ?? null;

  async function setNewStatus() {
    if (status === app.status) return;
    setBusy(true);
    const res = await fetch(`/data/applications/${app.id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  async function setResume(resumeJobId: string) {
    setBusy(true);
    const res = await fetch(`/data/applications/${app.id}/resume`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_job_id: resumeJobId || null }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  async function remove() {
    if (!confirm("Delete this application?")) return;
    setBusy(true);
    const res = await fetch(`/data/applications/${app.id}`, { method: "DELETE" });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  return (
    <tr>
      <td>{app.url ? <a href={app.url} target="_blank" rel="noopener">{app.company}</a> : app.company}</td>
      <td>{app.role}</td>
      <td>
        <StatusPill status={app.status} auto={app.auto_detected === 1} />{" "}
        <ConfirmedMark confirmedAt={app.confirmed_at} />
      </td>
      <td className="muted">{app.applied_date}</td>
      <td className="muted">{app.updated_date}</td>
      <td className="muted">{app.notes}</td>
      <td className="resume-used-pick">
        {linked ? (
          <Link className="resume-used" href={`/resume?job=${encodeURIComponent(linked.job_id)}`}>
            {linked.role || "résumé"} ↗
          </Link>
        ) : null}
        {resumeOptions.length > 0 ? (
          <select
            value={app.resume_job_id ?? ""}
            onChange={(e) => setResume(e.target.value)}
            disabled={busy}
            aria-label="Résumé used to apply"
          >
            <option value="">— none —</option>
            {resumeOptions.map((r) => (
              <option key={r.job_id} value={r.job_id}>
                {r.role || "(untitled)"}{r.company ? ` @ ${r.company}` : ""}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted">no résumés</span>
        )}
      </td>
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
