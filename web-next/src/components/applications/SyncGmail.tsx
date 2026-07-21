"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export default function SyncGmail() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [resultHtml, setResultHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setResultHtml(null);
    setError(null);
    const res = await fetch("/agents/gmail_sync/run?send=0", { method: "POST" });
    if (!res.ok) {
      setBusy(false);
      setError("Could not start Gmail sync.");
      return;
    }
    const { run_id } = await res.json();
    const es = new EventSource(`/runs/${run_id}/events`);
    es.addEventListener("done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setResultHtml(d.html || "");
      es.close();
      setBusy(false);
      router.refresh(); // reload the table so updated statuses + ✉ badges show
    });
    es.addEventListener("failed", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "Gmail sync failed.");
      es.close();
      setBusy(false);
    });
  }

  return (
    <div className="sync-gmail">
      <button className="primary" onClick={sync} disabled={busy}>
        {busy ? "Syncing Gmail…" : "✉️ Sync Gmail"}
      </button>
      {resultHtml !== null ? <div className="output" dangerouslySetInnerHTML={{ __html: resultHtml }} /> : null}
      {error ? <p className="banner err">{error}</p> : null}
    </div>
  );
}
