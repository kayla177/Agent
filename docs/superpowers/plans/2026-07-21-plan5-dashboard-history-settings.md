# Plan 5 — Dashboard + History + Settings + FastAPI Slim-Down

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Complete the Next.js migration — build the Dashboard (planet cards + live agent-run streaming), History (run log), and Settings (prefs) tabs — and slim FastAPI to an agent-only service on `:8001` (data-only SSE, JSON `/prefs`, no Jinja), retiring the old server-rendered UI.

**Architecture:** Next.js (`:3000`) owns all five tabs. FastAPI (`:8001`) keeps only: `POST /agents/{key}/run` (returns JSON `{run_id}`), `GET /runs/{id}/events` (SSE emitting data-only node/done/failed events), and `GET|POST /prefs`. The browser reaches these through the Next.js proxy rewrites (same-origin, so no CORS needed). History/Dashboard read the `runs` table via Prisma; agent runs stream over SSE.

**Tech Stack:** FastAPI + LangGraph (slimmed), stdlib SSE; Next.js 16 (App Router, EventSource), Prisma 6.

## Global Constraints

- **FastAPI is agent-only after this plan.** It serves ONLY `/agents/{key}/run`, `/runs/{id}/events`, `/prefs` (GET+POST), and `/healthz`. No Jinja, no static, no page/applications/jobs/charts routers. Port **8001** (matches the proxy rewrites added in Plan 2).
- **SSE is data-only.** Node events carry `{node, status}` (status ∈ start|finish|error|update) — no HTML. The `done` event carries `{html, status}` where `html` is markdown rendered server-side via `web/markdown.py` (markdown_it, NOT Jinja). The `failed` event carries `{error}`. The Next.js client renders node lines and injects the done HTML.
- **Settings reuse the Python validation.** `POST /prefs` reuses `web/prefs.save_prefs` (validation + `config.refresh()` in-process), so a saved change is picked up on the next run with no restart. Do not duplicate the validation in TS.
- **No CORS.** The browser only hits `:3000`; Next.js rewrites proxy to `:8001` server-side. Server components fetch `http://127.0.0.1:8001` directly.
- **Additive/rework under `web-next/` + `web/`.** The Python changes are confined to `web/` (+ delete dead template/static/router files); no agent-logic or store changes.
- **Agent metadata is hardcoded in Next.js** (`lib/agents.ts`) — the 4 agents + planets are stable; no `/agents` list endpoint needed.
- Verify via curl/Playwright against running servers. Triggering an agent executes it (may error without Ollama/creds) — that's fine; we verify the run lifecycle (run_id + a run row + SSE events + error handling), not agent success.

---

## File structure (Plan 5)

```
web/                                   # PYTHON (slim-down)
  markdown.py                          # NEW: render_markdown (extracted from templating.py)
  app.py                               # MODIFY: only runs + prefs routers; no Jinja/static
  __main__.py                          # MODIFY: port 8001
  routers/runs.py                      # REWRITE: JSON trigger + data-only SSE (no templates)
  routers/prefs.py                     # NEW: GET/POST /prefs (JSON, reuses web/prefs.py)
  (deleted in Task 5) templating.py, templates/, static/, routers/{pages,applications,jobs,settings,charts}.py

web-next/src/                          # NEXT.JS
  lib/agents.ts                        # agent metadata (key/label/planet/emoji/name/description)
  lib/runs.ts                          # Run type
  app/globals.css                      # (append) dashboard/runs/settings CSS
  next.config.ts                       # add /prefs rewrite
  app/page.tsx                         # REPLACE placeholder with real dashboard
  app/history/page.tsx                 # NEW
  app/settings/page.tsx                # NEW
  components/dashboard/AgentCard.tsx   # "use client" — Run button + RunStream
  components/dashboard/RunStream.tsx   # "use client" — EventSource → node log + output
  components/history/HistoryTable.tsx  # "use client" — expandable run rows
  components/settings/SettingsForm.tsx # "use client" — prefs form → POST /prefs
```

---

## Task 1: History tab (Prisma runs)

**Files:**
- Create: `web-next/src/lib/runs.ts`
- Create: `web-next/src/app/history/page.tsx`
- Create: `web-next/src/components/history/HistoryTable.tsx`
- Modify: `web-next/src/app/globals.css` (append the `.badge` styles from the block below — full dashboard/runs/settings CSS is added here once)

**Consumes:** `prisma.runs` (introspected in Plan 2: `id, agent_key, status, send, started_at, finished_at, output_message, error`).

- [ ] **Step 1: `web-next/src/lib/runs.ts`**

```ts
export type Run = {
  id: number;
  agent_key: string;
  status: string;
  send: number;
  started_at: string;
  finished_at: string | null;
  output_message: string | null;
  error: string | null;
};
```

- [ ] **Step 2: Append the Plan-5 CSS to `web-next/src/app/globals.css`**

```css
/* ---------- Dashboard / runs ---------- */
.agent-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin: 1rem 0; }
.agent-card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius); padding: 1.1rem 1.25rem; }
.agent-card h3 { margin: 0 0 .25rem; font-family: var(--serif); font-weight: 400; font-size: 1.15rem; }
.agent-card .primary { margin-top: .6rem; }
.run-stream { margin-top: .75rem; }
.node-log { display: flex; flex-direction: column; gap: .2rem; }
.node-line { color: var(--muted); font-size: .85rem; }
.node-line.finish { color: var(--green); }
.node-line.error { color: var(--red); }
.node-line .ico { display: inline-block; width: 1.3em; }
.output { margin-top: .6rem; background: var(--panel-2); border: 1px solid var(--border-soft); border-radius: 10px; padding: .75rem 1rem; font-size: .9rem; overflow-x: auto; }
.output.err { color: var(--red); white-space: pre-wrap; }
.badge { border-radius: 999px; padding: 1px 10px; font-size: .78rem; border: 1px solid var(--border); color: var(--muted); }
.badge.success { color: var(--green); border-color: var(--green); }
.badge.error { color: var(--red); border-color: var(--red); }
.badge.running { color: var(--amber); border-color: var(--amber); }

/* ---------- Settings ---------- */
.settings-form { max-width: 660px; display: flex; flex-direction: column; gap: 1rem; }
.settings-form label { display: flex; flex-direction: column; gap: .3rem; color: var(--muted); font-size: .85rem; }
.settings-form input, .settings-form textarea, .settings-form select {
  background: var(--panel); border: 1px solid var(--border); color: var(--text); border-radius: 10px; padding: .5rem .6rem; font: inherit;
}
.settings-form textarea { min-height: 4.5rem; resize: vertical; }
.secret-row { display: flex; justify-content: space-between; padding: .35rem 0; border-bottom: 1px solid var(--border-soft); color: var(--muted); font-size: .9rem; }
.secret-row .set { color: var(--green); }
.secret-row .unset { color: var(--red); }
```

- [ ] **Step 3: `web-next/src/components/history/HistoryTable.tsx`**

```tsx
"use client";
import { Fragment, useState } from "react";
import type { Run } from "@/lib/runs";

export default function HistoryTable({ runs }: { runs: Run[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <table className="apps">
      <thead>
        <tr><th>Agent</th><th>Status</th><th>Started (UTC)</th><th>Finished</th><th></th></tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <Fragment key={r.id}>
            <tr onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: "pointer" }}>
              <td>{r.agent_key}</td>
              <td><span className={`badge ${r.status}`}>{r.status}</span></td>
              <td className="muted">{r.started_at}</td>
              <td className="muted">{r.finished_at ?? "—"}</td>
              <td>{open === r.id ? "▾" : "▸"}</td>
            </tr>
            {open === r.id ? (
              <tr>
                <td colSpan={5}>
                  {r.error ? (
                    <pre className="output err">{r.error}</pre>
                  ) : (
                    <pre className="output" style={{ whiteSpace: "pre-wrap" }}>{r.output_message || "No output."}</pre>
                  )}
                </td>
              </tr>
            ) : null}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: `web-next/src/app/history/page.tsx`**

```tsx
import { prisma } from "@/lib/db";
import type { Run } from "@/lib/runs";
import HistoryTable from "@/components/history/HistoryTable";

export const dynamic = "force-dynamic";

export default async function HistoryPage() {
  const runs = (await prisma.runs.findMany({ orderBy: { id: "desc" }, take: 50 })) as Run[];
  return (
    <>
      <h1>history</h1>
      {runs.length === 0 ? (
        <p className="muted">No runs yet.</p>
      ) : (
        <HistoryTable runs={runs} />
      )}
    </>
  );
}
```

- [ ] **Step 5: Type-check + build + verify with a seeded run**

```bash
cd web-next && npx tsc --noEmit && npm run build
```
Seed a couple of run rows, then check the page:
```bash
cd /Users/kayla.li/.superset/Agent
sqlite3 data/control_center.db "INSERT INTO runs (agent_key,status,send,started_at,finished_at,output_message) VALUES ('job_scraper','success',0,'2026-07-20T09:00:00+00:00','2026-07-20T09:00:05+00:00','**Found 3 roles.**'),('morning_briefing','error',0,'2026-07-20T07:00:00+00:00',NULL,NULL);"
cd web-next && npm run dev -- -p 3007 > /tmp/next-p5.log 2>&1 &
sleep 8
curl -s localhost:3007/history > /tmp/hist.html
grep -c "job_scraper" /tmp/hist.html   # >= 1
grep -c "badge" /tmp/hist.html         # >= 2 (status badges)
kill %1 2>/dev/null
# clean up the seed runs
sqlite3 data/control_center.db "DELETE FROM runs WHERE started_at LIKE '2026-07-20T0%';"
echo "runs left: $(sqlite3 data/control_center.db "SELECT COUNT(*) FROM runs;")"
```
Expected: build clean; job_scraper + ≥2 badges present; seed runs removed afterward.

- [ ] **Step 6: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/lib/runs.ts web-next/src/app/history web-next/src/components/history web-next/src/app/globals.css
git commit -m "feat(web-next): history tab (run log from Prisma) + dashboard/settings CSS"
```

---

## Task 2: FastAPI slim-down + runs API rework + /prefs

**Files:**
- Create: `web/markdown.py`
- Create: `web/routers/prefs.py`
- Rewrite: `web/routers/runs.py`
- Modify: `web/app.py`
- Modify: `web/__main__.py`

**Interfaces produced:** `POST /agents/{key}/run?send=0` → `{run_id}`; `GET /runs/{id}/events` (SSE data-only); `GET /prefs` → `{prefs, secrets}`; `POST /prefs` (JSON) → `{saved}` | `400 {error}`.

- [ ] **Step 1: `web/markdown.py`** (extract the renderer so runs.py doesn't need Jinja)

```python
"""Server-side markdown → HTML for agent output blocks (Discord-flavored md)."""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"linkify": True, "breaks": True}).enable("linkify")


def render_markdown(text: str | None) -> str:
    return _md.render(text or "")
```

- [ ] **Step 2: Rewrite `web/routers/runs.py`** (JSON trigger + data-only SSE; drops templates + run_output)

```python
"""Run lifecycle: trigger an agent (JSON run_id), stream its events over SSE.

Data-only SSE — node events carry {node, status}; the terminal `done` event
carries server-rendered markdown HTML; `failed` carries the error. The Next.js
client renders node lines and injects the done HTML. Every event is persisted,
so a reload/late subscriber replays from SQLite then attaches to the live queue.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from web import db, runner
from web.markdown import render_markdown

router = APIRouter()

_THROTTLE_SECONDS = 60


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def _recent_active_run(agent_key: str) -> int | None:
    last = db.latest_run(agent_key)
    if not last:
        return None
    if last["status"] == "running":
        return int(last["id"])
    try:
        started = dt.datetime.fromisoformat(last["started_at"])
        age = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
    except ValueError:
        return None
    return int(last["id"]) if age < _THROTTLE_SECONDS else None


@router.post("/agents/{agent_key}/run")
async def trigger_run(agent_key: str, send: str = "0", force: str = "0"):
    do_send = send in ("1", "true", "on")
    if force not in ("1", "true", "on"):
        reuse = _recent_active_run(agent_key)
        if reuse is not None:
            return JSONResponse({"run_id": reuse, "reused": True})
    run_id = runner.start_run(agent_key, do_send)
    return JSONResponse({"run_id": run_id})


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, request: Request):
    last_seen = 0
    hdr = request.headers.get("last-event-id")
    if hdr and hdr.isdigit():
        last_seen = int(hdr)

    async def gen():
        nonlocal last_seen
        queue = runner.manager.subscribe(run_id)
        try:
            for ev in db.get_node_events(run_id, after_id=last_seen):
                yield _sse("node", {"node": ev["node"], "status": ev["status"]}, event_id=ev["id"])
                last_seen = max(last_seen, ev["id"])

            run = db.get_run(run_id)
            if run and run["status"] != "running":
                yield _terminal_frame(run)
                return

            while True:
                ev = await queue.get()
                if ev is None:
                    break
                etype = ev.get("type")
                if etype in ("node_start", "node_finish"):
                    db_id = ev.get("db_id")
                    if db_id and db_id <= last_seen:
                        continue
                    status = "start" if etype == "node_start" else ("error" if ev.get("error") else "finish")
                    yield _sse("node", {"node": ev["node"], "status": status}, event_id=db_id)
                    if db_id:
                        last_seen = max(last_seen, db_id)
                elif etype == "run_finished":
                    yield _sse("done", {"html": render_markdown(ev.get("message", "")), "status": "success"})
                elif etype == "run_error":
                    yield _sse("failed", {"error": ev.get("error", "unknown error")})
        finally:
            runner.manager.unsubscribe(run_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _terminal_frame(run: dict) -> str:
    if run["status"] == "error":
        return _sse("failed", {"error": run["error"] or "run failed"})
    return _sse("done", {"html": render_markdown(run["output_message"] or ""), "status": run["status"]})
```

- [ ] **Step 3: `web/routers/prefs.py`** (JSON settings API; reuses `web/prefs.py`)

```python
"""JSON settings API — read effective prefs + secret presence; save the overlay.

POST reuses web.prefs.save_prefs (validation + config.refresh() in-process), so
a saved change is reflected on the next run with no restart. The multiline
string fields mirror the old settings form (one item per line; job sources as
'company, ats, token').
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from web import prefs as prefstore

router = APIRouter()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _parse_sources(text: str) -> list[dict]:
    out: list[dict] = []
    for ln in _lines(text):
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 3:
            raise ValueError(f"job source line '{ln}' must be 'company, ats, token'")
        out.append({"company": parts[0], "ats": parts[1], "token": parts[2]})
    return out


@router.get("/prefs")
def get_prefs():
    return JSONResponse({"prefs": prefstore.current(), "secrets": prefstore.secret_status()})


@router.post("/prefs")
async def post_prefs(request: Request):
    body = await request.json()

    def g(key: str) -> str:
        return str(body.get(key, "")).strip()

    payload = {
        "WEATHER_LATITUDE": g("WEATHER_LATITUDE"),
        "WEATHER_LONGITUDE": g("WEATHER_LONGITUDE"),
        "WEATHER_TIMEZONE": g("WEATHER_TIMEZONE"),
        "WEATHER_TEMP_UNIT": g("WEATHER_TEMP_UNIT"),
        "COMMUTE_ORIGIN": g("COMMUTE_ORIGIN"),
        "COMMUTE_DESTINATION": g("COMMUTE_DESTINATION"),
        "NEWS_TOPICS": _lines(g("NEWS_TOPICS")),
        "NEWS_MAX_ITEMS_PER_TOPIC": g("NEWS_MAX_ITEMS_PER_TOPIC"),
        "STOCK_WATCHLIST": _lines(g("STOCK_WATCHLIST")),
        "STOCK_HEADLINE_TOPICS": _lines(g("STOCK_HEADLINE_TOPICS")),
    }
    try:
        payload["JOB_SOURCES"] = _parse_sources(g("JOB_SOURCES"))
        clean = prefstore.save_prefs(payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"saved": clean})
```

- [ ] **Step 4: Rewrite `web/app.py`** (agent-only surface)

```python
"""FastAPI agent service for the daily-agents control center.

Agent-only: triggers agent runs, streams their events (SSE), and reads/writes
the prefs overlay. The web UI is the Next.js app (web-next) — this service has
no Jinja, no static files, no page routes. The Next.js dev server proxies
/agents/*, /runs/*, and /prefs here (same-origin, so no CORS needed).

Launch::  uv run python -m web    # 127.0.0.1:8001
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402

import store_db  # noqa: E402
from web import db  # noqa: E402
from web.routers import prefs, runs  # noqa: E402

app = FastAPI(title="daily-agents agent service")

app.include_router(runs.router)
app.include_router(prefs.router)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    store_db.init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
```

- [ ] **Step 5: Update `web/__main__.py`** (port 8001)

```python
"""`python -m web` — run the agent service on 127.0.0.1:8001."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="127.0.0.1", port=8001, reload=False)
```

- [ ] **Step 6: Verify the slimmed service**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -m web > /tmp/fastapi8001.log 2>&1 &
sleep 4
echo "healthz:"; curl -s localhost:8001/healthz
echo ""; echo "old page routes gone (expect 404):"
curl -s -o /dev/null -w "/ %{http_code}\n" localhost:8001/
curl -s -o /dev/null -w "/jobs %{http_code}\n" localhost:8001/jobs
curl -s -o /dev/null -w "/applications %{http_code}\n" localhost:8001/applications
echo "GET /prefs:"; curl -s localhost:8001/prefs | head -c 200; echo
echo "trigger a run (returns run_id JSON):"
RID=$(curl -s -X POST "localhost:8001/agents/application_tracker/run?send=0" | node -e "process.stdin.on('data',d=>{try{console.log(JSON.parse(d).run_id)}catch(e){console.log('ERR '+d)}})")
echo "run_id=$RID"
echo "SSE (first ~2s of events):"; curl -s --max-time 2 "localhost:8001/runs/$RID/events" | head -c 400; echo
echo "run row created:"; sqlite3 data/control_center.db "SELECT id, agent_key, status FROM runs WHERE id=$RID;"
kill %1 2>/dev/null
```
Expected: `{"ok":true}`; `/`, `/jobs`, `/applications` all `404` (page routes removed); `/prefs` returns JSON with `prefs`/`secrets`; a numeric `run_id`; the SSE stream emits `event: node` (and possibly `done`/`failed` — the agent may error without Ollama, which is fine — the lifecycle works); a run row exists. Leave the run rows (they're real history) — do not delete.

Note: the application_tracker run reads the SQLite store and synthesizes; if the LLM backend is down it will emit a `failed` event, which still exercises the SSE error path correctly.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web/markdown.py web/routers/prefs.py web/routers/runs.py web/app.py web/__main__.py
git commit -m "feat(web): slim FastAPI to agent-only service on :8001 (JSON trigger, data-only SSE, /prefs)"
```

---

## Task 3: Settings tab

**Files:**
- Create: `web-next/src/app/settings/page.tsx`
- Create: `web-next/src/components/settings/SettingsForm.tsx`
- Modify: `web-next/next.config.ts` (add `/prefs` rewrite)

**Consumes:** FastAPI `GET|POST /prefs` (Task 2).

- [ ] **Step 1: Add the `/prefs` proxy rewrite to `web-next/next.config.ts`**

In the `rewrites()` array (alongside `/agents/:path*` and `/runs/:path*`), add:
```ts
      { source: "/prefs", destination: "http://127.0.0.1:8001/prefs" },
```

- [ ] **Step 2: `web-next/src/components/settings/SettingsForm.tsx`**

```tsx
"use client";
import { useState } from "react";

type Source = { company: string; ats: string; token: string };
type Prefs = {
  WEATHER_LATITUDE: number; WEATHER_LONGITUDE: number; WEATHER_TIMEZONE: string;
  WEATHER_TEMP_UNIT: string; COMMUTE_ORIGIN: string; COMMUTE_DESTINATION: string;
  NEWS_TOPICS: string[]; NEWS_MAX_ITEMS_PER_TOPIC: number;
  STOCK_WATCHLIST: string[]; STOCK_HEADLINE_TOPICS: string[]; JOB_SOURCES: Source[];
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
```

Note: reuse the `.banner.ok`/`.banner.err` classes. If `.banner.ok` isn't defined in globals.css, add `.banner.ok { background: rgba(127,192,138,.12); border:1px solid var(--green); color: var(--green); padding:.5rem .75rem; border-radius:10px; }` in this step (the `.banner.err` was added in Plan 3).

- [ ] **Step 3: `web-next/src/app/settings/page.tsx`**

```tsx
import SettingsForm from "@/components/settings/SettingsForm";

export const dynamic = "force-dynamic";

async function loadPrefs() {
  try {
    const res = await fetch("http://127.0.0.1:8001/prefs", { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const data = await loadPrefs();
  return (
    <>
      <h1>settings</h1>
      {!data ? (
        <p className="muted">Agent service offline — start it with <code>uv run python -m web</code> (port 8001).</p>
      ) : (
        <SettingsForm prefs={data.prefs} secrets={data.secrets} />
      )}
    </>
  );
}
```

- [ ] **Step 4: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 5: Verify round-trip (FastAPI + Next both up)**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -m web > /tmp/fastapi8001.log 2>&1 &      # :8001
(cd web-next && npm run dev -- -p 3007 > /tmp/next-p5.log 2>&1 &)
sleep 9
echo "settings page renders a field:"; curl -s localhost:3007/settings | grep -c "Weather timezone"   # >= 1
echo "POST via the proxy saves (change timezone):"
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:3007/prefs -H 'Content-Type: application/json' -d '{"WEATHER_TIMEZONE":"America/New_York","WEATHER_TEMP_UNIT":"celsius","NEWS_TOPICS":"technology","NEWS_MAX_ITEMS_PER_TOPIC":"4","STOCK_WATCHLIST":"","STOCK_HEADLINE_TOPICS":"","JOB_SOURCES":"","WEATHER_LATITUDE":"43.4643","WEATHER_LONGITUDE":"-80.5204","COMMUTE_ORIGIN":"","COMMUTE_DESTINATION":""}'
echo "overlay written:"; sqlite3 /dev/null ""; cat data/prefs.json | grep -o '"WEATHER_TIMEZONE": "America/New_York"' | head -1
echo "bad ATS → 400:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:3007/prefs -H 'Content-Type: application/json' -d '{"JOB_SOURCES":"Foo, notanats, tok"}'
lsof -ti:8001 | xargs kill 2>/dev/null; lsof -ti:3007 | xargs kill 2>/dev/null
```
Expected: settings page shows the timezone field; the proxied POST returns `200` and `data/prefs.json` now contains `America/New_York`; a bad-ATS POST returns `400` (prefs.py validation). (Leave the prefs.json change — it's a valid saved pref; or restore the timezone if you prefer.)

- [ ] **Step 6: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/settings web-next/src/components/settings web-next/next.config.ts
git commit -m "feat(web-next): settings tab (prefs form ↔ FastAPI /prefs)"
```

---

## Task 4: Dashboard tab + live SSE streaming

**Files:**
- Create: `web-next/src/lib/agents.ts`
- Create: `web-next/src/components/dashboard/RunStream.tsx`
- Create: `web-next/src/components/dashboard/AgentCard.tsx`
- Modify: `web-next/src/app/page.tsx` (replace the Plan-2 placeholder)

**Consumes:** FastAPI `POST /agents/{key}/run` + `GET /runs/{id}/events` (Task 2, proxied); `prisma.runs` for recent runs.

- [ ] **Step 1: `web-next/src/lib/agents.ts`**

```ts
export type AgentMeta = {
  key: string; label: string; planet: string; emoji: string; name: string; description: string;
};

export const AGENTS: AgentMeta[] = [
  { key: "morning_briefing", label: "briefing", planet: "earth", emoji: "🌅", name: "Morning Briefing", description: "Weather, commute & traffic, calendar, and news catch-up." },
  { key: "stock_digest", label: "stocks", planet: "jupiter", emoji: "📈", name: "Stock Digest", description: "Quotes, technical indicators, and news sentiment (info only, not advice)." },
  { key: "job_scraper", label: "jobs", planet: "mars", emoji: "🧑‍💻", name: "Job Scraper", description: "New co-op / intern / new-grad roles from official ATS boards." },
  { key: "application_tracker", label: "tracker", planet: "saturn", emoji: "📋", name: "Application Tracker", description: "Your application pipeline, follow-up reminders, and interviews." },
];
```

- [ ] **Step 2: `web-next/src/components/dashboard/RunStream.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";

type NodeState = { node: string; status: string };

export default function RunStream({ runId }: { runId: number }) {
  const [nodes, setNodes] = useState<NodeState[]>([]);
  const [outputHtml, setOutputHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const es = new EventSource(`/runs/${runId}/events`);
    es.addEventListener("node", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as NodeState;
      setNodes((cur) => {
        const i = cur.findIndex((n) => n.node === d.node);
        if (i >= 0) { const next = cur.slice(); next[i] = d; return next; }
        return [...cur, d];
      });
    });
    es.addEventListener("done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setOutputHtml(d.html || "");
      es.close();
    });
    es.addEventListener("failed", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "run failed");
      es.close();
    });
    return () => es.close();
  }, [runId]);

  const icon = (s: string) => (s === "finish" ? "✓" : s === "error" ? "✗" : s === "start" ? "◐" : "·");

  return (
    <div className="run-stream">
      <div className="node-log">
        {nodes.map((n) => (
          <div key={n.node} className={`node-line ${n.status}`}>
            <span className="ico">{icon(n.status)}</span>{n.node}
          </div>
        ))}
      </div>
      {outputHtml !== null ? <div className="output" dangerouslySetInnerHTML={{ __html: outputHtml }} /> : null}
      {error ? <pre className="output err">{error}</pre> : null}
    </div>
  );
}
```

Note: the done HTML is our own agent output rendered by the trusted local FastAPI (markdown_it) — `dangerouslySetInnerHTML` is acceptable here (single-user local app, first-party content).

- [ ] **Step 3: `web-next/src/components/dashboard/AgentCard.tsx`**

```tsx
"use client";
import { useState } from "react";
import type { AgentMeta } from "@/lib/agents";
import RunStream from "./RunStream";

export default function AgentCard({ agent, last }: { agent: AgentMeta; last: { status: string; started_at: string } | null }) {
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  async function run() {
    setStarting(true);
    const res = await fetch(`/agents/${agent.key}/run?send=0`, { method: "POST" });
    setStarting(false);
    if (res.ok) {
      const d = await res.json();
      setRunId(d.run_id);
    }
  }

  return (
    <div className="agent-card">
      <h3>{agent.emoji} {agent.name}</h3>
      <p className="muted">{agent.description}</p>
      {last ? <p className="muted">last run: {last.status} · {last.started_at}</p> : <p className="muted">no runs yet</p>}
      <button className="primary" onClick={run} disabled={starting || runId !== null}>
        {starting ? "starting…" : runId !== null ? "running…" : "▶ Run (preview)"}
      </button>
      {runId !== null ? <RunStream runId={runId} /> : null}
    </div>
  );
}
```

- [ ] **Step 4: Replace `web-next/src/app/page.tsx`** (the real dashboard)

```tsx
import { prisma } from "@/lib/db";
import { AGENTS } from "@/lib/agents";
import type { Run } from "@/lib/runs";
import AgentCard from "@/components/dashboard/AgentCard";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const recent = (await prisma.runs.findMany({ orderBy: { id: "desc" }, take: 8 })) as Run[];
  const lastByAgent = new Map<string, Run>();
  for (const r of recent) if (!lastByAgent.has(r.agent_key)) lastByAgent.set(r.agent_key, r);

  return (
    <>
      <h1>dashboard</h1>
      <div className="agent-grid">
        {AGENTS.map((a) => {
          const l = lastByAgent.get(a.key);
          return <AgentCard key={a.key} agent={a} last={l ? { status: l.status, started_at: l.started_at } : null} />;
        })}
      </div>

      <h2>recent runs</h2>
      {recent.length === 0 ? (
        <p className="muted">No runs yet — run an agent above.</p>
      ) : (
        <table className="apps">
          <thead><tr><th>Agent</th><th>Status</th><th>Started (UTC)</th></tr></thead>
          <tbody>
            {recent.map((r) => (
              <tr key={r.id}>
                <td>{r.agent_key}</td>
                <td><span className={`badge ${r.status}`}>{r.status}</span></td>
                <td className="muted">{r.started_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
```

- [ ] **Step 5: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 6: End-to-end verify (both servers) + Playwright visual (controller does the screenshot)**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -m web > /tmp/fastapi8001.log 2>&1 &
(cd web-next && npm run dev -- -p 3007 > /tmp/next-p5.log 2>&1 &)
sleep 9
echo "dashboard renders 4 agent cards:"; curl -s localhost:3007/ > /tmp/dash.html
grep -c "Application Tracker" /tmp/dash.html   # >= 1
grep -c "Job Scraper" /tmp/dash.html           # >= 1
grep -c "agent-card" /tmp/dash.html            # >= 4
echo "trigger a run through the proxy (dashboard's fetch path):"
curl -s -X POST "localhost:3007/agents/application_tracker/run?send=0" -w "\n%{http_code}\n"
```
Then the controller: navigate Playwright to `localhost:3007/` , click a "▶ Run (preview)" button, wait ~3s, screenshot, confirm the node log streams (◐/✓ lines) and either output or a failed message appears. Then kill both servers. (Leave run rows as history.)

Expected: 4 agent cards (Morning Briefing, Stock Digest, Job Scraper, Application Tracker); the proxied POST returns `{"run_id":N}` and `200`; the SSE node log streams live in the card.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/lib/agents.ts web-next/src/components/dashboard web-next/src/app/page.tsx
git commit -m "feat(web-next): dashboard tab (agent cards + live SSE run streaming)"
```

---

## Task 5: Retire the Jinja UI + cleanup

**Files (delete):** `web/templating.py`, `web/templates/` (dir), `web/static/` (dir), `web/routers/pages.py`, `web/routers/applications.py`, `web/routers/jobs.py`, `web/routers/settings.py`, `web/routers/charts.py`. Also remove the stale `data/control_center.db.bak` backup and the now-unread JSON stores if present.

- [ ] **Step 1: Confirm nothing still imports the doomed modules**

Run:
```bash
cd /Users/kayla.li/.superset/Agent
grep -rn "templating\|routers.pages\|routers.applications\|routers import jobs\|routers.charts\|routers.settings\|Jinja2Templates\|StaticFiles" web/app.py web/__main__.py web/routers/runs.py web/routers/prefs.py web/markdown.py 2>/dev/null || echo "no references in the kept files"
```
Expected: no matches in the kept files (app.py imports only prefs+runs; runs.py imports web.markdown; nothing imports templating).

- [ ] **Step 2: Delete the dead files**

```bash
cd /Users/kayla.li/.superset/Agent
git rm -r web/templates web/static web/templating.py \
  web/routers/pages.py web/routers/applications.py web/routers/jobs.py \
  web/routers/settings.py web/routers/charts.py
```

- [ ] **Step 3: Verify the service still imports + starts clean**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -c "import web.app; print('imports ok')"
uv run python -m web > /tmp/fastapi_final.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "healthz %{http_code}\n" localhost:8001/healthz
curl -s -o /dev/null -w "/prefs %{http_code}\n" localhost:8001/prefs
lsof -ti:8001 | xargs kill 2>/dev/null
```
Expected: `imports ok`; healthz `200`; `/prefs` `200`. No import error from the removed modules.

- [ ] **Step 4: Remove stale local artifacts (gitignored, best-effort)**

```bash
cd /Users/kayla.li/.superset/Agent
rm -f data/control_center.db.bak 2>/dev/null && echo "removed db backup" || true
```
(The JSON stores `data/applications.json` and `agents/job_scraper/data/jobs.json` are gitignored and already superseded by SQLite; leave or remove at will — they are no longer read.)

- [ ] **Step 5: Full green sweep (both apps)**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python tests/test_stores_sqlite.py >/tmp/a.log 2>&1; echo "stores: exit $?"
uv run python tests/test_job_scraper.py >/tmp/b.log 2>&1; echo "job_scraper: exit $?"
(cd web-next && npm run build >/tmp/c.log 2>&1); echo "web-next build: exit $?"
```
Expected: all exit 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git commit -m "chore(web): retire Jinja UI (templates, static, page routers) — Next.js owns the frontend"
```

---

## Plan 5 verification summary

- FastAPI on `:8001` serves ONLY `/healthz`, `/agents/{key}/run`, `/runs/{id}/events`, `/prefs`; old page routes 404; `web/templates`, `web/static`, and the page routers are gone.
- SSE is data-only; the Next.js dashboard streams node progress live and renders the final output.
- Dashboard (4 planet cards + recent runs + live run streaming), History (run log with expandable output), and Settings (prefs form ↔ `/prefs`, secret presence) all render on `:3000`.
- `npx tsc --noEmit` + `npm run build` clean; Python test suites pass.
- Both processes run side by side: `uv run python -m web` (:8001) + `cd web-next && npm run dev` (:3000).

## Notes / carry-forward to Plan 6 (Gmail)
- The `/prefs` + proxy pattern and the SSE streaming are the templates Plan 6 reuses (the Gmail "Sync" button triggers an agent run and streams progress like the dashboard cards).
- Deferred UX from Plan 4 (error toast on mutation failure) can be folded into a shared toast component here or in Plan 6.
- `application_tracker` gains a `scan_gmail` path in Plan 6; the dashboard Run button already exercises its run lifecycle.

## Self-review notes
- **Spec coverage:** dashboard (cards + SSE + recent runs), history (run table), settings (prefs form + secrets), FastAPI slim-down (port, agent-only, data-only SSE, /prefs), Jinja retirement — all covered.
- **SSE contract change** is self-consistent: FastAPI emits `{node,status}`/`{html,status}`/`{error}`; RunStream consumes exactly those. The `_node_line_html`/Jinja dependency is removed and `render_markdown` moved to `web/markdown.py`.
- **Settings correctness:** POST reuses `prefs.save_prefs` (validation + `config.refresh()`); no TS duplication; bad input → 400.
- **No CORS needed** (same-origin via proxy); server components fetch `:8001` directly.
- **Sequencing:** History (no dep) → FastAPI rework (enables settings+dashboard) → Settings → Dashboard → retire Jinja. The Prisma-backed tabs (applications/jobs from Plans 3–4) keep working throughout since they never touched FastAPI.
