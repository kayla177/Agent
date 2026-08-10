"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

const STATUS_COLOR: Record<string, string> = {
  draft: "var(--amber)",
  final: "var(--green)",
};

export type CoverLetter = {
  job_id: string;
  company: string;
  role: string;
  body: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type CoverLetterVersion = {
  id: number;
  job_id: string;
  body: string;
  status: string;
  created_at: string;
};

// View one drafted cover letter and flip draft<->final. Copy-to-clipboard is
// the PRIMARY action — the letter's destination is a textarea on an
// application form, not a file, so there is no download here (that's Phase 2's
// PDF). Body is plain text, rendered in a <pre> so paragraph breaks survive —
// no markdown renderer, mirroring ResumeCard's version history for past drafts
// (GET /data/cover-letters/{job_id}/versions), which is the snapshot guarantee
// made visible: drafting again never destroys the letter you had.
export default function CoverLetterCard({ letter }: { letter: CoverLetter }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [versions, setVersions] = useState<CoverLetterVersion[] | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(letter.body);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function setStatus(status: "draft" | "final") {
    setBusy(true);
    const res = await fetch(`/data/cover-letters/${encodeURIComponent(letter.job_id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  async function loadVersions() {
    setLoadingVersions(true);
    const res = await fetch(`/data/cover-letters/${encodeURIComponent(letter.job_id)}/versions`);
    setLoadingVersions(false);
    if (res.ok) setVersions((await res.json()).versions ?? []);
  }

  return (
    <details className="resume-card" id={`cover-letter-${letter.job_id}`}>
      <summary>
        <span className="resume-title">
          {letter.role || "(untitled)"} <span className="muted">@ {letter.company || "?"}</span>
        </span>
        <span
          className="status-pill"
          style={{ color: STATUS_COLOR[letter.status] ?? "var(--muted)", borderColor: STATUS_COLOR[letter.status] ?? "var(--border)" }}
        >
          {letter.status}
        </span>
        <span className="muted resume-updated">updated {letter.updated_at}</span>
      </summary>

      <pre className="resume-md letter-body">{letter.body}</pre>

      <div className="resume-actions">
        <button className="primary" disabled={busy} onClick={copy}>
          {copied ? "Copied ✓" : "Copy to clipboard"}
        </button>
        {letter.status === "draft" ? (
          <button disabled={busy} onClick={() => setStatus("final")}>
            Mark final
          </button>
        ) : (
          <button disabled={busy} onClick={() => setStatus("draft")}>
            Revert to draft
          </button>
        )}
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
                </div>
                <pre className="version-md letter-body">{v.body}</pre>
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
