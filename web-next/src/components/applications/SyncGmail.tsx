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
    let res: Response;
    try {
      res = await fetch("/agents/gmail_sync/run?send=0", { method: "POST" });
    } catch {
      setBusy(false);
      setError("Could not reach the agent service.");
      return;
    }
    if (!res.ok) {
      setBusy(false);
      setError("Could not start Gmail sync.");
      return;
    }
    const { run_id } = await res.json();
    let done = false;
    const es = new EventSource(`/runs/${run_id}/events`);
    const finish = () => {
      es.close();
      setBusy(false);
    };
    es.addEventListener("done", (e) => {
      done = true;
      const d = JSON.parse((e as MessageEvent).data);
      setResultHtml(d.html || "");
      finish();
      router.refresh();
    });
    es.addEventListener("failed", (e) => {
      done = true;
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "Gmail sync failed.");
      finish();
    });
    es.onerror = () => {
      // Ignore transient reconnect blips; only surface a real, closed failure.
      if (done || es.readyState !== EventSource.CLOSED) return;
      setError("Lost connection to the agent service.");
      finish();
    };
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
