"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

// The master cover letter is a SAMPLE letter in the user's own voice. Each draft
// imitates its voice, structure and rhythm — it is not a placeholder template,
// so there is nothing to substitute here. Plain text, because a letter is prose
// pasted into a textarea where markdown would land literally.
export default function MasterCoverLetter({
  body,
  updatedAt,
}: {
  body: string;
  updatedAt: string;
}) {
  const router = useRouter();
  const [text, setText] = useState(body);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = text !== body;

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    const res = await fetch("/data/cover-letter/master", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    });
    setBusy(false);
    if (res.ok) {
      setSaved(true);
      router.refresh();
    } else {
      setError("Could not save the master cover letter.");
    }
  }

  return (
    <div>
      {error ? <p className="banner err">{error}</p> : null}
      {!body ? (
        <p className="banner">
          Empty. Drafting is blocked until you write one, because a letter with no
          voice to imitate could only be invented.
        </p>
      ) : null}
      <textarea
        className="resume-md"
        rows={16}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"Dear Hiring Manager,\n\n..."}
        spellCheck={false}
      />
      <div className="master-actions">
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : dirty ? "Save master cover letter" : saved ? "Saved ✓" : "Saved"}
        </button>
        {updatedAt ? <span className="muted">updated {updatedAt}</span> : null}
        {dirty ? <span className="muted">unsaved edits</span> : null}
      </div>
    </div>
  );
}
