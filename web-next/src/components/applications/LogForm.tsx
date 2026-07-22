"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { STATUSES } from "@/lib/applications";

export default function LogForm() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    setPending(true);
    const res = await fetch("/data/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setPending(false);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Failed to add application.");
      return;
    }
    form.reset();
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="log-form">
      {error ? <div className="banner err">{error}</div> : null}
      <div className="form-grid">
        <input name="company" placeholder="Company" required />
        <input name="role" placeholder="Role" required />
        <input name="url" placeholder="URL (optional)" />
        <select name="status" defaultValue="applied">
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input name="notes" placeholder="Notes (optional)" />
      </div>
      <button type="submit" className="primary" disabled={pending}>
        {pending ? "Adding…" : "Add application"}
      </button>
    </form>
  );
}
