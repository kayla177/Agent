"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { parseKeywords, type Resume, type ResumeVersion } from "@/lib/resume";
import { printResume } from "@/lib/printResume";
import { downloadPdfFromTex } from "@/lib/resumePdf";

const STATUS_COLOR: Record<string, string> = {
  draft: "var(--amber)",
  final: "var(--green)",
};

// View / edit / finalize one generated resume. The textarea holds the raw
// Markdown; saving writes it back via PATCH /data/resumes. Also exposes past
// versions (GET /data/resumes/{job_id}/versions) and a print-to-PDF export.
// `defaultOpen` (set from the tracker's ?job= deep link) expands + scrolls to it.
export default function ResumeCard({ resume, defaultOpen = false }: { resume: Resume; defaultOpen?: boolean }) {
  const router = useRouter();
  const cardRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (defaultOpen) cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [defaultOpen]);
  const [markdown, setMarkdown] = useState(resume.markdown);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [versions, setVersions] = useState<ResumeVersion[] | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const keywords = parseKeywords(resume.keywords);
  const dirty = markdown !== resume.markdown;
  const title = `${resume.role || "resume"} — ${resume.company || ""}`.trim();

  async function patch(payload: Record<string, unknown>) {
    setBusy(true);
    setSaved(false);
    const res = await fetch("/data/resumes", {
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

  async function loadVersions() {
    setLoadingVersions(true);
    const res = await fetch(`/data/resumes/${encodeURIComponent(resume.job_id)}/versions`);
    setLoadingVersions(false);
    if (res.ok) setVersions((await res.json()).versions ?? []);
  }

  const hasTex = Boolean(resume.latex && resume.latex.trim());

  // Prefer a real LaTeX PDF (their template); fall back to browser print when
  // there's no tailored .tex yet or it fails to compile.
  async function downloadPdf() {
    setBusy(true);
    if (hasTex) {
      const r = await downloadPdfFromTex(resume.latex, `${resume.role || "resume"}.pdf`);
      setBusy(false);
      if (!r.ok) printResume(markdown, title);
    } else {
      setBusy(false);
      printResume(markdown, title);
    }
  }

  return (
    <details className="resume-card" ref={cardRef} id={`resume-${resume.job_id}`} open={defaultOpen}>
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
        <button disabled={busy} onClick={downloadPdf} title={hasTex ? "Compiled from your LaTeX template" : "Browser print (no tailored .tex yet)"}>
          Download PDF{hasTex ? " (LaTeX)" : ""}
        </button>
        {dirty ? <span className="muted">unsaved edits</span> : null}
      </div>

      <details className="version-block" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && versions === null) loadVersions(); }}>
        <summary className="muted">version history</summary>
        {loadingVersions ? (
          <p className="muted">loading…</p>
        ) : versions && versions.length > 0 ? (
          <div className="version-list">
            {versions.map((v) => (
              <div key={v.id} className="version-item">
                <div className="version-head">
                  <span className="version-when">{v.created_at}</span>
                  <span className="status-pill" style={{ color: STATUS_COLOR[v.status] ?? "var(--muted)", borderColor: STATUS_COLOR[v.status] ?? "var(--border)" }}>{v.status}</span>
                  <button className="restore" onClick={() => setMarkdown(v.markdown)}>
                    Load into editor
                  </button>
                </div>
                <textarea className="version-md" value={v.markdown} readOnly rows={4} />
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">No earlier versions — this is the first draft.</p>
        )}
      </details>
    </details>
  );
}
