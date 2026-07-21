"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { DOC_KINDS, type PoolDoc } from "@/lib/resume";

// Manage the experience pool: add background as pasted text (Prisma) or by
// uploading a file — PDF/DOCX/TXT/MD are parsed server-side by the Python
// service (/experience/upload, same parser as the CLI) — and delete docs.
export default function PoolManager({ docs }: { docs: PoolDoc[] }) {
  const router = useRouter();
  const [kind, setKind] = useState<string>("resume");
  const [filename, setFilename] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Upload the raw file (incl. PDF/DOCX) to the Python service, which parses and
  // stores it. The agent service (:8001) must be running.
  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    setError(null);
    setPending(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind);
    let res: Response;
    try {
      res = await fetch("/experience/upload", { method: "POST", body: fd });
    } catch {
      setPending(false);
      setError("Could not reach the agent service (is FastAPI on :8001 running?).");
      return;
    }
    setPending(false);
    input.value = ""; // allow re-picking the same file
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Failed to upload file.");
      return;
    }
    router.refresh();
  }

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (!text.trim()) {
      setError("Paste some text, or upload a file instead.");
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
            {pending ? "Uploading…" : "Upload PDF/DOCX/TXT/MD"}
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md,.markdown"
              onChange={onFile}
              disabled={pending}
              hidden
            />
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
