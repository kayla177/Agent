"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { downloadPdfFromTex } from "@/lib/resumePdf";

// The master résumé is the user's real LaTeX (.tex) — their template and the
// source of truth for format + content. Edited here as raw LaTeX; saved via
// PUT /data/resume/master; "Download PDF" compiles it with Tectonic on :8001.
export default function MasterResume({
  latex,
  updatedAt,
}: {
  latex: string;
  updatedAt: string;
}) {
  const router = useRouter();
  const [tex, setTex] = useState(latex);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = tex !== latex;

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    const res = await fetch("/data/resume/master", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latex: tex }),
    });
    setBusy(false);
    if (res.ok) {
      setSaved(true);
      router.refresh();
    } else {
      setError("Could not save the master résumé.");
    }
  }

  async function downloadPdf() {
    setBusy(true);
    setError(null);
    const r = await downloadPdfFromTex(tex, "master-resume.pdf");
    setBusy(false);
    if (!r.ok) setError(r.error + (r.log ? `\n\n${r.log}` : ""));
  }

  // .tex is plain text — read it in the browser, no server parse needed.
  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setTex(String(reader.result ?? ""));
    reader.readAsText(file);
    input.value = "";
  }

  return (
    <div>
      {error ? <div className="banner err" style={{ whiteSpace: "pre-wrap" }}>{error}</div> : null}
      {!latex && !dirty ? (
        <p className="master-empty">
          Paste your résumé’s LaTeX source below (or upload the <code>.tex</code>), then save.
          Tailored drafts start from this, and Download PDF compiles it in your exact format.
        </p>
      ) : null}
      <textarea
        className="resume-md"
        value={tex}
        onChange={(e) => setTex(e.target.value)}
        rows={18}
        spellCheck={false}
        placeholder="\documentclass[letterpaper,11pt]{article}&#10;…your résumé .tex…"
      />
      <div className="master-actions">
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Working…" : dirty ? "Save master résumé" : saved ? "Saved ✓" : "Saved"}
        </button>
        <button onClick={downloadPdf} disabled={busy || !tex.trim()}>Download PDF</button>
        <label className="file-btn">
          Upload .tex
          <input type="file" accept=".tex,.txt" onChange={onFile} hidden />
        </label>
        {updatedAt ? <span className="muted">updated {updatedAt}</span> : null}
        {dirty ? <span className="muted">unsaved edits</span> : null}
      </div>
    </div>
  );
}
