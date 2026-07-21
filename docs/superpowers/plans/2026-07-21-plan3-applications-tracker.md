# Plan 3 — Applications Tracker Tab + CRUD API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the `/applications` tracker tab in `web-next` — a stats row, a pipeline chart, a log-new-application form, and a full table with inline status updates and delete — backed by a Next.js JSON CRUD API over `prisma.applications`.

**Architecture:** The page is a server component reading Prisma directly for its initial render. Mutations go through Route Handlers under `/api/applications`, called from small client components that `router.refresh()` on success (App Router pattern). Shared logic (statuses, stats, formatting) lives in `src/lib/applications.ts`.

**Tech Stack:** Next.js 16 (App Router, Route Handlers), TypeScript, Prisma 6 (`prisma.applications`), the ported CSS theme.

## Global Constraints

- **Prisma model is `applications`, fields are snake_case:** `id: Int`, `company/role/url/status/applied_date/updated_date/notes: String`, `auto_detected: Int` (0/1, NOT boolean). Use these exact names.
- **Statuses (exact, ordered):** `applied, interview, offer, accepted, rejected`. Mirror the Python store's contract: a **manual** status update clears the auto-detected flag (`auto_detected = 0`); only Gmail sync (Plan 6) sets it to 1.
- **Dates** are ISO `YYYY-MM-DD` strings (match Python `_today()`): `new Date().toISOString().slice(0,10)`.
- **Chart color rule (from the dataviz skill):** the pipeline is a **single-hue** bar list (`var(--accent)`) — one series, identity via row labels — so no categorical palette to validate. Status **pills** use per-status tints but always show the status word (text-carried identity; a labeled status indicator, not a color-only categorical series).
- **No new heavy deps.** No charting library — bars are CSS/divs. No test framework — route/page verification is via `curl` against `npm run dev` (the established pattern; formal JS unit tests are out of scope until a harness exists).
- **Additive, under `web-next/` only.** Do not touch Python, the DB schema, or other tabs.
- **Server vs client:** the page + presentational components are server components (no hooks); only `LogForm`, `ApplicationRow`, `PlanetTheme` are `"use client"`.

---

## File structure (Plan 3)

```
web-next/src/
  lib/applications.ts                              # STATUSES, types, STATUS_META, today(), computeStats(), isStatus()
  app/globals.css                                  # (append) applications-view styles
  app/api/applications/route.ts                    # GET list, POST add
  app/api/applications/[id]/route.ts               # DELETE
  app/api/applications/[id]/status/route.ts        # PATCH status
  app/applications/page.tsx                        # server component: stats + pipeline + form + table
  components/applications/StatusPill.tsx           # labeled status pill (pure)
  components/applications/StatsRow.tsx             # KPI tiles (pure)
  components/applications/PipelineBars.tsx         # single-hue bars (pure)
  components/applications/LogForm.tsx              # "use client" — add form
  components/applications/ApplicationRow.tsx       # "use client" — inline status/delete
  components/applications/PlanetTheme.tsx          # "use client" — sets body data-planet
```

---

## Task 1: Foundation — `lib/applications.ts` + view CSS

**Files:**
- Create: `web-next/src/lib/applications.ts`
- Modify: `web-next/src/app/globals.css` (append)

**Interfaces produced:**
- `STATUSES: readonly ["applied","interview","offer","accepted","rejected"]`, `type Status`, `isStatus(s): s is Status`
- `type Application` (matches the Prisma row shape; `auto_detected: number`)
- `STATUS_META: Record<Status,{label,color}>`
- `today(): string`
- `type Stats`, `computeStats(apps): Stats`

- [ ] **Step 1: Write `web-next/src/lib/applications.ts`**

```ts
export const STATUSES = ["applied", "interview", "offer", "accepted", "rejected"] as const;
export type Status = (typeof STATUSES)[number];

export function isStatus(s: string): s is Status {
  return (STATUSES as readonly string[]).includes(s);
}

export type Application = {
  id: number;
  company: string;
  role: string;
  url: string;
  status: string;
  applied_date: string;
  updated_date: string;
  notes: string;
  auto_detected: number; // SQLite int, 0 | 1
};

// Per-status tint for the pill. The status WORD is always rendered beside it, so
// identity is text-carried (a labeled status indicator, not color-alone).
export const STATUS_META: Record<Status, { label: string; color: string }> = {
  applied:   { label: "applied",   color: "#4a90ff" },
  interview: { label: "interview", color: "#e0b15a" },
  offer:     { label: "offer",     color: "#b98cff" },
  accepted:  { label: "accepted",  color: "#7fc08a" },
  rejected:  { label: "rejected",  color: "#e0705a" },
};

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export type Stats = {
  total: number;
  active: number;       // applied + interview + offer
  interviews: number;
  responseRate: number; // % that got any response (status past "applied")
  counts: Record<Status, number>;
};

export function computeStats(apps: { status: string }[]): Stats {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0])) as Record<Status, number>;
  for (const a of apps) if (isStatus(a.status)) counts[a.status] += 1;
  const total = apps.length;
  const active = counts.applied + counts.interview + counts.offer;
  const responses = counts.interview + counts.offer + counts.accepted + counts.rejected;
  const responseRate = total ? Math.round((responses / total) * 100) : 0;
  return { total, active, interviews: counts.interview, responseRate, counts };
}
```

- [ ] **Step 2: Append the applications-view CSS to `web-next/src/app/globals.css`**

Append this block at the END of the file:

```css
/* ---------- Applications / tracker view ---------- */
.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 1rem 0 .5rem; }
.stat-tile { background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius); padding: 1rem 1.25rem; }
.stat-value { font-family: var(--serif); font-size: 2rem; color: #fff; line-height: 1.1; }
.stat-label { color: var(--muted); font-size: .8rem; letter-spacing: 2px; text-transform: lowercase; margin-top: .25rem; }

.pipeline { display: flex; flex-direction: column; gap: .5rem; max-width: 560px; }
.pipeline-row { display: grid; grid-template-columns: 90px 1fr 28px; align-items: center; gap: .75rem; }
.pipeline-label { color: var(--muted); font-size: .9rem; text-align: right; }
.pipeline-track { height: 10px; background: var(--panel-2); border-radius: 999px; overflow: hidden; }
.pipeline-fill { display: block; height: 100%; background: var(--accent); border-radius: 999px; min-width: 0; }
.pipeline-count { color: var(--text); font-variant-numeric: tabular-nums; }

.status-pill { display: inline-flex; align-items: center; gap: 2px; padding: 2px 10px; border-radius: 999px; border: 1px solid; font-size: .8rem; }

.log-form { margin: .5rem 0 1rem; }
.form-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: .6rem; margin-bottom: .75rem; }
.form-grid input, .form-grid select, table.apps select {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 10px; padding: .5rem .6rem; font: inherit;
}
button.primary { background: var(--accent); color: #04122b; border: none; border-radius: 10px; padding: .55rem 1rem; font: inherit; cursor: pointer; }
button.primary:disabled { opacity: .6; cursor: default; }

table.apps { width: 100%; border-collapse: collapse; margin-top: .5rem; }
table.apps th, table.apps td { text-align: left; padding: .6rem .5rem; border-bottom: 1px solid var(--border-soft); vertical-align: middle; }
table.apps th { color: var(--muted); font-weight: 400; font-size: .8rem; letter-spacing: 1px; text-transform: lowercase; }
table.apps button { background: var(--panel-2); border: 1px solid var(--border); color: var(--text); border-radius: 8px; padding: .35rem .6rem; cursor: pointer; margin-left: .4rem; }
table.apps button.danger { color: var(--red); border-color: transparent; }
table.apps button:disabled { opacity: .5; cursor: default; }
.banner.err { background: rgba(224,112,90,.12); border: 1px solid var(--red); color: var(--red); padding: .5rem .75rem; border-radius: 10px; margin-bottom: .5rem; }
```

- [ ] **Step 3: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: both succeed (nothing imports the lib yet; this just confirms no syntax/type error).

- [ ] **Step 4: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/lib/applications.ts web-next/src/app/globals.css
git commit -m "feat(web-next): applications lib (statuses, stats) + tracker-view CSS"
```

---

## Task 2: CRUD API route handlers

**Files:**
- Create: `web-next/src/app/api/applications/route.ts`
- Create: `web-next/src/app/api/applications/[id]/route.ts`
- Create: `web-next/src/app/api/applications/[id]/status/route.ts`

**Interfaces produced (the JSON API contract for later tasks):**
- `GET /api/applications` → `{ applications: Application[] }`
- `POST /api/applications` body `{company, role, url?, status?, notes?}` → `201 {application}` | `400 {error}`
- `PATCH /api/applications/:id/status` body `{status, auto_detected?}` → `200 {application}` | `400|404 {error}`
- `DELETE /api/applications/:id` → `200 {ok:true}` | `404 {error}`

**Consumes:** `prisma` (`@/lib/db`), `STATUSES/isStatus/today` (`@/lib/applications`).

- [ ] **Step 1: Write `web-next/src/app/api/applications/route.ts`**

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { STATUSES, isStatus, today } from "@/lib/applications";

export const dynamic = "force-dynamic"; // always hit the DB, never cache

export async function GET() {
  const applications = await prisma.applications.findMany({ orderBy: { id: "asc" } });
  return NextResponse.json({ applications });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const company = String(body.company ?? "").trim();
  const role = String(body.role ?? "").trim();
  const url = String(body.url ?? "").trim();
  const notes = String(body.notes ?? "").trim();
  const status = String(body.status ?? "applied").trim() || "applied";

  if (!company || !role) {
    return NextResponse.json({ error: "Company and role are required." }, { status: 400 });
  }
  if (!isStatus(status)) {
    return NextResponse.json({ error: `status must be one of ${STATUSES.join(", ")}` }, { status: 400 });
  }
  const d = today();
  const application = await prisma.applications.create({
    data: { company, role, url, notes, status, applied_date: d, updated_date: d, auto_detected: 0 },
  });
  return NextResponse.json({ application }, { status: 201 });
}
```

- [ ] **Step 2: Write `web-next/src/app/api/applications/[id]/status/route.ts`**

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { STATUSES, isStatus, today } from "@/lib/applications";

// Next 15/16: dynamic route params are async and must be awaited.
export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const appId = Number(id);
  if (!Number.isInteger(appId)) {
    return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  }
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const status = String(body.status ?? "").trim();
  const autoDetected = body.auto_detected ? 1 : 0; // manual update (default) clears the flag
  if (!isStatus(status)) {
    return NextResponse.json({ error: `status must be one of ${STATUSES.join(", ")}` }, { status: 400 });
  }
  try {
    const application = await prisma.applications.update({
      where: { id: appId },
      data: { status, updated_date: today(), auto_detected: autoDetected },
    });
    return NextResponse.json({ application });
  } catch {
    return NextResponse.json({ error: `No application with id ${appId}.` }, { status: 404 });
  }
}
```

- [ ] **Step 3: Write `web-next/src/app/api/applications/[id]/route.ts`**

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const appId = Number(id);
  if (!Number.isInteger(appId)) {
    return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  }
  try {
    await prisma.applications.delete({ where: { id: appId } });
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: `No application with id ${appId}.` }, { status: 404 });
  }
}
```

- [ ] **Step 4: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: success. If tsc complains about the `params` Promise shape, that is the correct Next 15/16 signature — do not change it; instead ensure the `await params` is present.

- [ ] **Step 5: Verify all four endpoints against a live server (curl)**

Start dev on a free port (3000 may be busy):
```bash
cd web-next && npm run dev -- -p 3007 > /tmp/next-p3.log 2>&1 &
sleep 8
BASE=http://localhost:3007
echo "GET:"; curl -s $BASE/api/applications | head -c 400; echo
echo "POST (add):"; NEWID=$(curl -s -X POST $BASE/api/applications -H 'Content-Type: application/json' \
  -d '{"company":"TestCo","role":"QA Verify","status":"applied","notes":"tmp"}' | node -e "process.stdin.on('data',d=>{try{console.log(JSON.parse(d).application.id)}catch(e){console.log('ERR:'+d)}})"); echo "new id=$NEWID"
echo "POST validation (missing role → 400):"; curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/api/applications -H 'Content-Type: application/json' -d '{"company":"X"}'
echo "PATCH status:"; curl -s -o /dev/null -w "%{http_code}\n" -X PATCH $BASE/api/applications/$NEWID/status -H 'Content-Type: application/json' -d '{"status":"interview"}'
echo "PATCH bad status → 400:"; curl -s -o /dev/null -w "%{http_code}\n" -X PATCH $BASE/api/applications/$NEWID/status -H 'Content-Type: application/json' -d '{"status":"nope"}'
echo "DELETE:"; curl -s -o /dev/null -w "%{http_code}\n" -X DELETE $BASE/api/applications/$NEWID
echo "DELETE missing → 404:"; curl -s -o /dev/null -w "%{http_code}\n" -X DELETE $BASE/api/applications/999999
kill %1 2>/dev/null
```
Expected: GET lists the 2 seed apps; POST returns a numeric id; missing-role POST → `400`; PATCH → `200`; bad-status PATCH → `400`; DELETE → `200`; missing DELETE → `404`. The test row is created then deleted, leaving the DB with its original 2 apps. If a curl fails, read `/tmp/next-p3.log`. If :3007 is also busy, use another port.

- [ ] **Step 6: Confirm the DB is back to 2 apps (no test leftover)**

Run: `sqlite3 data/control_center.db "SELECT COUNT(*) FROM applications;"` → `2`. If it shows 3, the cleanup DELETE didn't run — delete the TestCo row: `sqlite3 data/control_center.db "DELETE FROM applications WHERE company='TestCo';"`.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/api/applications
git commit -m "feat(web-next): applications CRUD API (list/add/status/delete)"
```

---

## Task 3: Presentational components (pure)

**Files:**
- Create: `web-next/src/components/applications/StatusPill.tsx`
- Create: `web-next/src/components/applications/StatsRow.tsx`
- Create: `web-next/src/components/applications/PipelineBars.tsx`

**Consumes:** `STATUS_META/isStatus/STATUSES/type Stats` from `@/lib/applications`. No hooks (usable inside server or client components).

- [ ] **Step 1: `StatusPill.tsx`**

```tsx
import { STATUS_META, isStatus } from "@/lib/applications";

export default function StatusPill({ status, auto }: { status: string; auto?: boolean }) {
  const meta = isStatus(status) ? STATUS_META[status] : null;
  const color = meta?.color ?? "#8b93a6";
  return (
    <span className="status-pill" style={{ color, borderColor: color, background: `${color}1f` }}>
      {meta?.label ?? status}
      {auto ? <span title="auto-detected from Gmail">✉</span> : null}
    </span>
  );
}
```

- [ ] **Step 2: `StatsRow.tsx`**

```tsx
import type { Stats } from "@/lib/applications";

export default function StatsRow({ stats }: { stats: Stats }) {
  const tiles = [
    { label: "total", value: String(stats.total) },
    { label: "active", value: String(stats.active) },
    { label: "interviews", value: String(stats.interviews) },
    { label: "response rate", value: `${stats.responseRate}%` },
  ];
  return (
    <div className="stat-row">
      {tiles.map((t) => (
        <div key={t.label} className="stat-tile">
          <div className="stat-value">{t.value}</div>
          <div className="stat-label">{t.label}</div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: `PipelineBars.tsx`** (single-hue; identity via labels)

```tsx
import { STATUSES, STATUS_META, type Stats } from "@/lib/applications";

export default function PipelineBars({ counts }: { counts: Stats["counts"] }) {
  const max = Math.max(1, ...STATUSES.map((s) => counts[s]));
  return (
    <div className="pipeline">
      {STATUSES.map((s) => {
        const n = counts[s];
        const pct = Math.round((n / max) * 100);
        return (
          <div key={s} className="pipeline-row">
            <span className="pipeline-label">{STATUS_META[s].label}</span>
            <span className="pipeline-track">
              <span className="pipeline-fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="pipeline-count">{n}</span>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd web-next && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/applications/StatusPill.tsx web-next/src/components/applications/StatsRow.tsx web-next/src/components/applications/PipelineBars.tsx
git commit -m "feat(web-next): tracker presentational components (pill, stats, pipeline)"
```

---

## Task 4: Client interactive components

**Files:**
- Create: `web-next/src/components/applications/LogForm.tsx`
- Create: `web-next/src/components/applications/ApplicationRow.tsx`
- Create: `web-next/src/components/applications/PlanetTheme.tsx`

**Consumes:** the CRUD API (Task 2), `StatusPill` (Task 3), `STATUSES/type Application` (Task 1).

- [ ] **Step 1: `PlanetTheme.tsx`** (sets the body accent per route)

```tsx
"use client";
import { useEffect } from "react";

export default function PlanetTheme({ planet }: { planet: string }) {
  useEffect(() => {
    const prev = document.body.dataset.planet;
    document.body.dataset.planet = planet;
    return () => { document.body.dataset.planet = prev ?? "earth"; };
  }, [planet]);
  return null;
}
```

- [ ] **Step 2: `LogForm.tsx`**

```tsx
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
    const res = await fetch("/api/applications", {
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
```

- [ ] **Step 3: `ApplicationRow.tsx`**

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { STATUSES, type Application } from "@/lib/applications";
import StatusPill from "./StatusPill";

export default function ApplicationRow({ app }: { app: Application }) {
  const router = useRouter();
  const [status, setStatus] = useState(app.status);
  const [busy, setBusy] = useState(false);

  async function setNewStatus() {
    if (status === app.status) return;
    setBusy(true);
    const res = await fetch(`/api/applications/${app.id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  async function remove() {
    if (!confirm("Delete this application?")) return;
    setBusy(true);
    const res = await fetch(`/api/applications/${app.id}`, { method: "DELETE" });
    setBusy(false);
    if (res.ok) router.refresh();
  }

  return (
    <tr>
      <td>{app.url ? <a href={app.url} target="_blank" rel="noopener">{app.company}</a> : app.company}</td>
      <td>{app.role}</td>
      <td><StatusPill status={app.status} auto={app.auto_detected === 1} /></td>
      <td className="muted">{app.applied_date}</td>
      <td className="muted">{app.updated_date}</td>
      <td className="muted">{app.notes}</td>
      <td>
        <select value={status} onChange={(e) => setStatus(e.target.value)} disabled={busy}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={setNewStatus} disabled={busy || status === app.status}>Set</button>
      </td>
      <td><button className="danger" onClick={remove} disabled={busy} aria-label="Delete">✕</button></td>
    </tr>
  );
}
```

- [ ] **Step 4: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: success.

- [ ] **Step 5: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/applications/LogForm.tsx web-next/src/components/applications/ApplicationRow.tsx web-next/src/components/applications/PlanetTheme.tsx
git commit -m "feat(web-next): tracker client components (log form, row, planet theme)"
```

---

## Task 5: Applications page assembly + end-to-end verification

**Files:**
- Create: `web-next/src/app/applications/page.tsx`

**Consumes:** everything from Tasks 1–4.

- [ ] **Step 1: Write `web-next/src/app/applications/page.tsx`**

```tsx
import { prisma } from "@/lib/db";
import { computeStats, type Application } from "@/lib/applications";
import StatsRow from "@/components/applications/StatsRow";
import PipelineBars from "@/components/applications/PipelineBars";
import LogForm from "@/components/applications/LogForm";
import ApplicationRow from "@/components/applications/ApplicationRow";
import PlanetTheme from "@/components/applications/PlanetTheme";

export const dynamic = "force-dynamic";

export default async function ApplicationsPage() {
  const apps = (await prisma.applications.findMany({ orderBy: { id: "asc" } })) as Application[];
  const stats = computeStats(apps);
  return (
    <>
      <PlanetTheme planet="saturn" />
      <h1>applications</h1>
      <StatsRow stats={stats} />
      <h2>pipeline</h2>
      <PipelineBars counts={stats.counts} />
      <h2>log a new application</h2>
      <LogForm />
      <h2>your applications ({apps.length})</h2>
      {apps.length === 0 ? (
        <p className="muted">Nothing logged yet. Add one above.</p>
      ) : (
        <table className="apps">
          <thead>
            <tr>
              <th>Company</th><th>Role</th><th>Status</th><th>Applied</th>
              <th>Updated</th><th>Notes</th><th>Update</th><th></th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => <ApplicationRow key={a.id} app={a} />)}
          </tbody>
        </table>
      )}
    </>
  );
}
```

- [ ] **Step 2: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: success; `/applications` shows as a dynamic route (`ƒ`).

- [ ] **Step 3: End-to-end verification against a live server**

```bash
cd web-next && npm run dev -- -p 3007 > /tmp/next-p3.log 2>&1 &
sleep 8
BASE=http://localhost:3007
echo "page renders stats + apps:"; curl -s $BASE/applications > /tmp/apps.html
grep -c "Stripe" /tmp/apps.html; grep -c "Figma" /tmp/apps.html   # each >= 1
grep -c "response rate" /tmp/apps.html                             # >= 1 (stats row)
grep -c "pipeline" /tmp/apps.html                                  # >= 1
grep -c "status-pill" /tmp/apps.html                               # >= 2 (a pill per app)
echo "add via API then confirm it appears on the page:"
curl -s -X POST $BASE/api/applications -H 'Content-Type: application/json' -d '{"company":"E2ECo","role":"Temp","status":"offer"}' -o /dev/null
curl -s $BASE/applications | grep -c "E2ECo"                       # >= 1
echo "clean up the E2E row:"
ID=$(sqlite3 ../data/control_center.db "SELECT id FROM applications WHERE company='E2ECo' LIMIT 1;")
curl -s -X DELETE $BASE/api/applications/$ID -o /dev/null
sqlite3 ../data/control_center.db "SELECT COUNT(*) FROM applications;"  # back to 2
kill %1 2>/dev/null
```
Expected: Stripe/Figma/response-rate/pipeline all match ≥ 1; ≥ 2 status pills; E2ECo appears after the POST and the count returns to 2 after cleanup. If anything fails, read `/tmp/next-p3.log`.

- [ ] **Step 4: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/applications/page.tsx
git commit -m "feat(web-next): applications tracker page (stats, pipeline, form, table)"
```

---

## Plan 3 verification summary

- `npm run build` + `npx tsc --noEmit` clean
- API: GET lists apps; POST validates + creates (400 on missing fields/bad status); PATCH updates status (404 on missing, 400 on bad status); DELETE removes (404 on missing)
- `/applications` renders stats row, single-hue pipeline bars, log form, and a table with one row per app; status pills show per-status tint + `✉` when `auto_detected === 1`
- Add/update/delete from the browser update the table without a full reload (`router.refresh()`), and persist to the same SQLite the Python app reads
- DB left with its original 2 apps after verification

## Notes / carry-forward to Plan 4
- The `/api/applications` + `router.refresh()` mutation pattern is the template for Plan 4's jobs apply/dismiss.
- `PlanetTheme` (client) sets `data-planet` per route — Plan 4 uses `planet="mars"`, and Plan 5 can lift theming into the layout/nav if a flash-free approach is wanted.
- No JS test harness yet — verification is curl/build. If Plan 4+ grows the API surface, consider adding Vitest + a route-handler test as its own task.

## Self-review notes
- **Spec coverage:** stats row, pipeline chart, log form, table w/ inline status + delete, CRUD API, `auto_detected` `✉` badge — all roadmap items covered.
- **Consistency:** manual status update clears `auto_detected` (matches Python store); dates ISO `YYYY-MM-DD`; snake_case Prisma fields used throughout.
- **Chart color:** single-hue bars (no categorical palette to validate); pills are labeled status indicators — resolves the dataviz CVD/lightness constraints by construction.
- **Server/client split:** page + StatsRow/PipelineBars/StatusPill are server-safe; only LogForm/ApplicationRow/PlanetTheme are client.
