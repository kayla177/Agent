"use client";
import { useState } from "react";

type Source = { company: string; ats: string; token: string };
type Prefs = {
  WEATHER_LATITUDE: number; WEATHER_LONGITUDE: number; WEATHER_TIMEZONE: string;
  WEATHER_TEMP_UNIT: string; COMMUTE_ORIGIN: string; COMMUTE_DESTINATION: string;
  NEWS_TOPICS: string[]; NEWS_MAX_ITEMS_PER_TOPIC: number;
  STOCK_WATCHLIST: string[]; STOCK_HEADLINE_TOPICS: string[]; JOB_SOURCES: Source[];
  JOB_PROFILE: string; JOB_MIN_FIT: number; JOB_MAX_AGE_DAYS: number;
  JOB_DROP_GHOSTS: boolean; JOB_COUNTRIES: string[];
};
type Secret = { name: string; set: boolean };

export default function SettingsForm({ prefs, secrets }: { prefs: Prefs; secrets: Secret[] }) {
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const sourcesText = prefs.JOB_SOURCES.map((s) => `${s.company}, ${s.ats}, ${s.token}`).join("\n");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setMsg(null);
    const data = Object.fromEntries(new FormData(e.currentTarget));
    setPending(true);
    const res = await fetch("/prefs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setPending(false);
    if (res.ok) setMsg({ ok: true, text: "Saved." });
    else {
      const j = await res.json().catch(() => ({}));
      setMsg({ ok: false, text: j.error ?? "Save failed." });
    }
  }

  return (
    <form className="settings-form" onSubmit={onSubmit}>
      {msg ? <div className={`banner ${msg.ok ? "ok" : "err"}`}>{msg.text}</div> : null}

      <label>Weather latitude<input name="WEATHER_LATITUDE" defaultValue={prefs.WEATHER_LATITUDE} /></label>
      <label>Weather longitude<input name="WEATHER_LONGITUDE" defaultValue={prefs.WEATHER_LONGITUDE} /></label>
      <label>Weather timezone<input name="WEATHER_TIMEZONE" defaultValue={prefs.WEATHER_TIMEZONE} /></label>
      <label>Temperature unit
        <select name="WEATHER_TEMP_UNIT" defaultValue={prefs.WEATHER_TEMP_UNIT}>
          <option value="celsius">celsius</option>
          <option value="fahrenheit">fahrenheit</option>
        </select>
      </label>
      <label>Commute origin<input name="COMMUTE_ORIGIN" defaultValue={prefs.COMMUTE_ORIGIN} /></label>
      <label>Commute destination<input name="COMMUTE_DESTINATION" defaultValue={prefs.COMMUTE_DESTINATION} /></label>
      <label>News topics (one per line)<textarea name="NEWS_TOPICS" defaultValue={prefs.NEWS_TOPICS.join("\n")} /></label>
      <label>News items per topic<input name="NEWS_MAX_ITEMS_PER_TOPIC" defaultValue={prefs.NEWS_MAX_ITEMS_PER_TOPIC} /></label>
      <label>Stock watchlist (one ticker per line)<textarea name="STOCK_WATCHLIST" defaultValue={prefs.STOCK_WATCHLIST.join("\n")} /></label>
      <label>Stock headline topics (one per line)<textarea name="STOCK_HEADLINE_TOPICS" defaultValue={prefs.STOCK_HEADLINE_TOPICS.join("\n")} /></label>
      <label>Job sources (company, ats, token — one per line)<textarea name="JOB_SOURCES" defaultValue={sourcesText} /></label>
      <label>Candidate profile (drives job fit scores)
        <textarea name="JOB_PROFILE" rows={4} defaultValue={prefs.JOB_PROFILE}
          placeholder="3rd-year CS undergrad at Waterloo. Python, TypeScript, React, SQL. Seeking SWE/ML co-op." />
      </label>
      <label>Minimum fit score to keep (0 = keep all)
        <input name="JOB_MIN_FIT" defaultValue={prefs.JOB_MIN_FIT} />
      </label>
      <label>Flag postings older than (days)
        <input name="JOB_MAX_AGE_DAYS" defaultValue={prefs.JOB_MAX_AGE_DAYS} />
      </label>
      <label>Countries to show (one code per line: US, CA, OTHER)
        <textarea name="JOB_COUNTRIES" rows={3} defaultValue={prefs.JOB_COUNTRIES.join("\n")} />
      </label>
      <label className="checkbox-row">
        <input type="checkbox" name="JOB_DROP_GHOSTS" defaultChecked={prefs.JOB_DROP_GHOSTS} />
        Drop stale/ghost postings entirely (instead of just flagging them)
      </label>

      <button type="submit" className="primary" disabled={pending}>{pending ? "Saving…" : "Save settings"}</button>

      <h2>secrets</h2>
      <div>
        {secrets.map((s) => (
          <div key={s.name} className="secret-row">
            <span>{s.name}</span>
            <span className={s.set ? "set" : "unset"}>{s.set ? "set" : "not set"}</span>
          </div>
        ))}
      </div>
      <p className="muted">Secrets live in .env and are not editable here.</p>
    </form>
  );
}
