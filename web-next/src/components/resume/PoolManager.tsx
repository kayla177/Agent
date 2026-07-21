"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { DOC_KINDS, type PoolDoc } from "@/lib/resume";

// Manage the experience pool: add background as pasted text or a .txt/.md file
// (read client-side — no server parsing), and delete docs. Binary formats
// (PDF/DOCX) still go through the Python CLI until the :8001 bridge lands.
export default function PoolManager({ docs }: { docs: PoolDoc[] }) {
  const router = useRouter();
  const [kind, setKind] = useState<string>("resume");
  const [filename, setFilename] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    setText(await file.text());
  }

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (!text.trim()) {
      setError("Paste some text or pick a .txt/.md file first.");
      return;
    }
    setPending(true);
    const res = await fetch("/api/resume/docs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: filename || "pasted.md", kind, text }),
    });
    setPending(false);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Failed to add document.");
      return;
    }
    setText("");
    setFilename("");
    router.refresh();
  }

  async function remove(id: number) {
    if (!confirm("Remove this document from the pool?")) return;
    const res = await fetch(`/api/resume/docs/${id}`, { method: "DELETE" });
    if (res.ok) router.refresh();
  }

  return (
    <div>
      <form onSubmit={add} className="log-form">
        {error ? <div className="banner err">{error}</div> : null}
        <div className="pool-add-head">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {DOC_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <input
            placeholder="Label (optional, e.g. resume.md)"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
          />
          <label className="file-btn">
            Load .txt/.md
            <input type="file" accept=".txt,.md,.markdown,text/plain" onChange={onFile} hidden />
          </label>
        </div>
        <textarea
          className="pool-text"
          placeholder="Paste your resume or a project write-up here…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
        />
        <button type="submit" className="primary" disabled={pending}>
          {pending ? "Adding…" : "Add to pool"}
        </button>
      </form>

      {docs.length === 0 ? (
        <p className="muted">Pool is empty. Add your resume and past projects above.</p>
      ) : (
        <table className="apps">
          <thead>
            <tr><th>Label</th><th>Kind</th><th>Size</th><th>Added</th><th></th></tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>{d.filename}</td>
                <td className="muted">{d.kind}</td>
                <td className="muted">{d.chars.toLocaleString()} chars</td>
                <td className="muted">{d.added_at}</td>
                <td>
                  <button className="danger" onClick={() => remove(d.id)} aria-label="Remove">✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
