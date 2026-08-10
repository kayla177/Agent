"use client";
import { useState } from "react";

export type Profile = {
  full_name: string; email: string; phone: string; location: string;
  linkedin_url: string; github_url: string; portfolio_url: string;
  school: string; degree: string; grad_date: string;
  us_work_auth: string; ca_work_auth: string; needs_sponsorship: number;
  summary: string;
};

const WORK_AUTH = [
  { v: "", l: "— not set —" },
  { v: "citizen", l: "citizen" },
  { v: "permanent_resident", l: "permanent resident" },
  { v: "f1_opt", l: "F-1 / OPT" },
  { v: "tn_eligible", l: "TN eligible" },
  { v: "coop_permit", l: "co-op / study work permit" },
  { v: "needs_sponsorship", l: "needs sponsorship" },
];

// Typed fields, deliberately. Phase B's autofill maps these straight into ATS
// forms, so no model ever invents a phone number or a work-authorization answer
// into something you are about to submit.
export default function ProfileForm({ profile }: { profile: Profile }) {
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setMsg(null);
    const fd = new FormData(e.currentTarget);
    const body = {
      ...Object.fromEntries(fd),
      needs_sponsorship: fd.get("needs_sponsorship") !== null,
    };
    setPending(true);
    try {
      const res = await fetch("/data/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) setMsg({ ok: true, text: "Profile saved." });
      else setMsg({ ok: false, text: (await res.json().catch(() => ({}))).error ?? "Save failed." });
    } catch {
      setMsg({ ok: false, text: "Could not reach the agent service (is FastAPI on :8001 running?)." });
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="settings-form panel" onSubmit={onSubmit}>
      <h2>profile</h2>
      <p className="panel-sub">
        Who you are, for fit scoring and (later) filling application forms.
        <strong> Save profile</strong> below saves only this section.
      </p>
      {msg ? <div className={`banner ${msg.ok ? "ok" : "err"}`}>{msg.text}</div> : null}

      <label>Full name<input name="full_name" defaultValue={profile.full_name} /></label>
      <label>Email<input name="email" type="email" defaultValue={profile.email} /></label>
      <label>Phone<input name="phone" defaultValue={profile.phone} /></label>
      <label>Location<input name="location" defaultValue={profile.location} placeholder="Waterloo, ON, Canada" /></label>
      <label>LinkedIn URL<input name="linkedin_url" defaultValue={profile.linkedin_url} /></label>
      <label>GitHub URL<input name="github_url" defaultValue={profile.github_url} /></label>
      <label>Portfolio URL<input name="portfolio_url" defaultValue={profile.portfolio_url} /></label>
      <label>School<input name="school" defaultValue={profile.school} /></label>
      <label>Degree<input name="degree" defaultValue={profile.degree} /></label>
      <label>Graduation (YYYY-MM)<input name="grad_date" defaultValue={profile.grad_date} placeholder="2027-04" /></label>

      <label>US work authorization
        <select name="us_work_auth" defaultValue={profile.us_work_auth}>
          {WORK_AUTH.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </label>
      <label>Canada work authorization
        <select name="ca_work_auth" defaultValue={profile.ca_work_auth}>
          {WORK_AUTH.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </label>
      <label className="checkbox-row">
        <input type="checkbox" name="needs_sponsorship" defaultChecked={profile.needs_sponsorship === 1} />
        Will need visa sponsorship
      </label>

      <label>Summary (also drives job fit scores)
        <textarea name="summary" rows={4} defaultValue={profile.summary}
          placeholder="3rd-year CS undergrad at Waterloo. Python, TypeScript, React, SQL. Seeking SWE/ML co-op." />
      </label>

      <button type="submit" className="primary" disabled={pending}>
        {pending ? "Saving…" : "Save profile"}
      </button>
    </form>
  );
}
