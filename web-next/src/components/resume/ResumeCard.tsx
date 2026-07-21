"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { parseKeywords, type Resume } from "@/lib/resume";

const STATUS_COLOR: Record<string, string> = {
  draft: "var(--amber)",
  final: "var(--green)",
};

// View / edit / finalize one generated resume. The textarea holds the raw
// Markdown; saving writes it back via PATCH /api/resumes.
export default function ResumeCard({ resume }: { resume: Resume }) {
  const router = useRouter();
  const [markdown, setMarkdown] = useState(resume.markdown);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const keywords = parseKeywords(resume.keywords);
  const dirty = markdown !== resume.markdown;

  async function patch(payload: Record<string, unknown>) {
    setBusy(true);
    setSaved(false);
    const res = await fetch("/api/resumes", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jobId: resume.job_id, ...payload }),
    });
    setBusy(false);
    if (res.ok) {
      setSaved(true);
      router.refresh();
    }
  }

  return (
    <details className="resume-card">
      <summary>
        <span className="resume-title">
          {resume.role || "(untitled)"} <span className="muted">@ {resume.company || "?"}</span>
        </span>
        <span
          className="status-pill"
          style={{ color: STATUS_COLOR[resume.status] ?? "var(--muted)", borderColor: STATUS_COLOR[resume.status] ?? "var(--border)" }}
        >
          {resume.status}
        </span>
        <span className="muted resume-updated">updated {resume.updated_at}</span>
      </summary>

      {keywords.length > 0 ? (
        <div className="kw-row">
          {keywords.map((k) => <span key={k} className="kw-chip">{k}</span>)}
        </div>
      ) : null}

      <textarea
        className="resume-md"
        value={markdown}
        onChange={(e) => setMarkdown(e.target.value)}
        rows={20}
        spellCheck={false}
      />

      <div className="resume-actions">
        <button className="primary" disabled={busy || !dirty} onClick={() => patch({ markdown })}>
          {busy ? "Saving…" : dirty ? "Save changes" : saved ? "Saved ✓" : "Saved"}
        </button>
        {resume.status === "draft" ? (
          <button disabled={busy || dirty} onClick={() => patch({ status: "final" })}>
            Mark final
          </button>
        ) : (
          <button disabled={busy} onClick={() => patch({ status: "draft" })}>
            Revert to draft
          </button>
        )}
        {dirty ? <span className="muted">unsaved edits</span> : null}
      </div>
    </details>
  );
}
