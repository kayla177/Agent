"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

// The one canonical résumé the tailored drafts start from. Edited as Markdown
// (PUT /data/resume/master); can be seeded from an uploaded file, which the
// Python service parses to text (/experience/parse, no pool row created).
export default function MasterResume({
  markdown,
  updatedAt,
}: {
  markdown: string;
  updatedAt: string;
}) {
  const router = useRouter();
  const [text, setText] = useState(markdown);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = text !== markdown;

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    const res = await fetch("/data/resume/master", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown: text }),
    });
    setBusy(false);
    if (res.ok) {
      setSaved(true);
      router.refresh();
    } else {
      setError("Could not save the master résumé.");
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    const fd = new FormData();
    fd.append("file", file);
    let res: Response;
    try {
      res = await fetch("/experience/parse", { method: "POST", body: fd });
    } catch {
      setBusy(false);
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
      return;
    }
    setBusy(false);
    input.value = "";
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Could not read that file.");
      return;
    }
    const { text: parsed } = await res.json();
    setText(parsed);
  }

  return (
    <div>
      {error ? <div className="banner err">{error}</div> : null}
      {!markdown && !dirty ? (
        <p className="master-empty">
          No master résumé yet — paste it below or upload an existing one, then save.
          Tailored drafts will start from this.
        </p>
      ) : null}
      <textarea
        className="resume-md"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={16}
        spellCheck={false}
        placeholder="# Your Name&#10;&#10;Paste your résumé as Markdown…"
      />
      <div className="master-actions">
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : dirty ? "Save master résumé" : saved ? "Saved ✓" : "Saved"}
        </button>
        <label className="file-btn">
          {busy ? "Reading…" : "Upload PDF/DOCX/TXT/MD"}
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown"
            onChange={onFile}
            disabled={busy}
            hidden
          />
        </label>
        {updatedAt ? <span className="muted">updated {updatedAt}</span> : null}
        {dirty ? <span className="muted">unsaved edits</span> : null}
      </div>
    </div>
  );
}
