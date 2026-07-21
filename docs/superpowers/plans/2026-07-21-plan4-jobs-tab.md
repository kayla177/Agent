# Plan 4 — Jobs Tab (React port of the job-tab design)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the `/jobs` tab in `web-next` per the approved design (`docs/superpowers/specs/2026-07-20-job-tab-ui-design.md`): an overview strip (count chips + best-match hero), a filter/sort bar, and a dense fit-sorted list with inline expand and reload-free apply/dismiss.

**Architecture:** A server component reads jobs from Prisma (selected columns only, not the blob) and hands them to a client `JobsBoard` that does filter/sort/expand in memory and calls `/api/jobs/{apply,dismiss}` for mutations, then `router.refresh()`. Apply mirrors the Python behavior: mark the job applied AND create a tracker application.

**Tech Stack:** Next.js 16 (App Router), TypeScript, Prisma 6 (`prisma.jobs`), the ported Mars theme.

## Global Constraints

- **`jobs.id` is a `String`** (e.g. `workday:stripe:123`, contains `:`/`/`). Mutation routes take the id in the **POST body**, NOT a path segment — and must NOT use any `Number(id)`/`Number.isInteger` guard (that would reject valid string ids). This is the carry-forward from Plan 3.
- **Dual-storage sync (critical):** the `jobs` row stores status in BOTH the mirrored `status` column AND inside the `data` JSON blob (the Python store's read source of truth). Any web-next write to a job's status MUST update both, or the Python scraper (which reads the blob via `load_records()`) will see stale status and could re-notify. Use the `setJobStatus` helper (Task 1) for every status change.
- **web-next reads only Prisma columns for display** — never parses the `data` blob. Freshness/age is computed from `posted_at` in TS (the blob's `age_days` is not a column). A generic `⚠ stale` badge is shown when `ghost === 1` (the blob's `ghost_reason` is not read).
- **Job statuses:** `new, viewed, applied, dismissed`. **Fit tiers:** hi ≥ 75 (green `#7fc08a`), mid ≥ 50 (amber `#e0b15a`), lo < 50 (red `#e0705a`), unscored `null` → `—` (muted, sorts last). Fit % is always shown as a number, so the tier color is secondary encoding.
- **Apply = job.status→applied + create an application** (company=job.company, role=job.title, url=job.url, status="applied", auto_detected=0) — mirrors `web/routers/jobs.py`. No duplicate-guard (matches the existing Python behavior).
- **Mars theme** (`planet="mars"`, accent `#e07a4a`). Additive under `web-next/` only.
- No new deps, no charting lib. No JS test harness — verify via curl/Playwright against `npm run dev` with **seeded sample jobs that are cleaned up afterward** (the jobs table starts empty).

---

## File structure (Plan 4)

```
web-next/src/
  lib/jobs.ts                          # Job type, JOB_STATUSES, fitTier/FIT_COLOR, ageDays, parseAlsoOn, sorts, bestMatch
  lib/jobs-server.ts                   # setJobStatus(id,status) — server-only, syncs column + data blob
  app/globals.css                      # (append) jobs-view CSS
  app/api/jobs/apply/route.ts          # POST {id}
  app/api/jobs/dismiss/route.ts        # POST {id}
  app/jobs/page.tsx                    # server: read jobs (select cols), render JobsBoard or empty state
  components/jobs/BestMatchHero.tsx    # pure: best-match callout
  components/jobs/JobRow.tsx           # pure: one row + expand panel (receives callbacks)
  components/jobs/JobsBoard.tsx        # "use client": filter/sort/expand state + mutations
```

---

## Task 1: Foundation — `lib/jobs.ts`, `lib/jobs-server.ts`, jobs CSS

**Files:**
- Create: `web-next/src/lib/jobs.ts`
- Create: `web-next/src/lib/jobs-server.ts`
- Modify: `web-next/src/app/globals.css` (append)

**Interfaces produced:** `type Job`, `JOB_STATUSES`, `fitTier()`, `FIT_COLOR`, `ageDays()`, `parseAlsoOn()`, `byFitDesc()`, `byDateDesc()`, `bestMatch()`; and `setJobStatus(id, status)`.

- [ ] **Step 1: Write `web-next/src/lib/jobs.ts`** (client-safe — no prisma import)

```ts
export const JOB_STATUSES = ["new", "viewed", "applied", "dismissed"] as const;

export type Job = {
  id: string;
  company: string;
  title: string;
  location: string;
  url: string;
  status: string;
  ats: string;
  posted_at: string | null;
  remote: number | null;
  compensation: string | null;
  department: string | null;
  description: string | null;
  fit_score: number | null;
  fit_reason: string | null;
  ghost: number;      // 0 | 1
  also_on: string;    // JSON array string
};

export type FitTier = "hi" | "mid" | "lo" | "none";

export function fitTier(score: number | null): FitTier {
  if (score === null || score === undefined) return "none";
  if (score >= 75) return "hi";
  if (score >= 50) return "mid";
  return "lo";
}

export const FIT_COLOR: Record<FitTier, string> = {
  hi: "#7fc08a", mid: "#e0b15a", lo: "#e0705a", none: "#8b93a6",
};

export function ageDays(postedAt: string | null): number | null {
  if (!postedAt) return null;
  const d = new Date(postedAt + "T00:00:00Z");
  if (isNaN(d.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86_400_000));
}

export function parseAlsoOn(s: string): string[] {
  try {
    const a = JSON.parse(s);
    return Array.isArray(a) ? a.map(String) : [];
  } catch {
    return [];
  }
}

// fit desc, unscored (null) last
export function byFitDesc(a: Job, b: Job): number {
  if (a.fit_score === null && b.fit_score === null) return 0;
  if (a.fit_score === null) return 1;
  if (b.fit_score === null) return -1;
  return b.fit_score - a.fit_score;
}

export function byDateDesc(a: Job, b: Job): number {
  return (b.posted_at ?? "").localeCompare(a.posted_at ?? "");
}

// highest-fit role among status === "new" with a non-null score; null if none
export function bestMatch(jobs: Job[]): Job | null {
  const scored = jobs.filter((j) => j.status === "new" && j.fit_score !== null);
  if (!scored.length) return null;
  return scored.reduce((best, j) => (j.fit_score! > best.fit_score! ? j : best));
}
```

- [ ] **Step 2: Write `web-next/src/lib/jobs-server.ts`** (server-only — imports prisma)

```ts
import { prisma } from "@/lib/db";
import { today } from "@/lib/applications";

// Update a job's status on BOTH the mirrored `status` column AND the `data` JSON
// blob (the Python store's read source of truth), so the two views never drift.
// Returns false if no row with that id exists.
export async function setJobStatus(id: string, status: string): Promise<boolean> {
  const rec = await prisma.jobs.findUnique({ where: { id } });
  if (!rec) return false;
  let blob: Record<string, unknown> = {};
  try { blob = JSON.parse(rec.data || "{}"); } catch { blob = {}; }
  const d = today();
  blob.id = id;
  blob.status = status;
  blob.last_seen = d;
  await prisma.jobs.update({
    where: { id },
    data: { status, last_seen: d, data: JSON.stringify(blob) },
  });
  return true;
}
```

- [ ] **Step 3: Append the jobs-view CSS to `web-next/src/app/globals.css`**

```css
/* ---------- Jobs view ---------- */
.job-chips { display: flex; gap: .75rem; margin: .5rem 0 1rem; }
.job-chip { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 999px; padding: .3rem .9rem; color: var(--muted); font-size: .85rem; }
.job-chip strong { color: var(--text); }

.best-match { display: flex; align-items: center; gap: .6rem; width: 100%; text-align: left; cursor: pointer;
  background: linear-gradient(90deg, rgba(224,122,74,.16), transparent); border: 1px solid var(--accent);
  border-radius: var(--radius); padding: .75rem 1rem; color: var(--text); font: inherit; margin-bottom: 1rem; }
.best-match .bm-star { color: var(--accent); font-size: 1.1rem; }
.best-match .bm-cta { margin-left: auto; color: var(--accent); white-space: nowrap; }

.job-filters { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; margin-bottom: 1rem; }
.job-filters select { background: var(--panel); border: 1px solid var(--border); color: var(--text); border-radius: 10px; padding: .4rem .6rem; font: inherit; }
.job-filters label { color: var(--muted); font-size: .85rem; display: inline-flex; align-items: center; gap: .35rem; }

.job-row { border: 1px solid var(--border-soft); border-radius: 14px; padding: .7rem .9rem; margin-bottom: .6rem; background: var(--panel); }
.job-row.dim { opacity: .55; }
.job-head { display: flex; align-items: center; gap: .8rem; }
.fit-badge { flex: none; width: 46px; height: 46px; border-radius: 12px; display: grid; place-items: center; font-weight: 600; font-variant-numeric: tabular-nums; border: 1px solid; }
.job-main { flex: 1; min-width: 0; }
.job-title { color: #fff; }
.job-sub { color: var(--muted); font-size: .85rem; }
.job-badges { display: flex; gap: .5rem; margin-top: .25rem; flex-wrap: wrap; }
.job-badge { font-size: .75rem; color: var(--muted); border: 1px solid var(--border-soft); border-radius: 999px; padding: 1px 8px; }
.job-badge.stale { color: var(--red); border-color: var(--red); }
.job-actions { display: flex; gap: .4rem; align-items: center; }
.job-actions button { background: var(--panel-2); border: 1px solid var(--border); color: var(--text); border-radius: 8px; padding: .4rem .7rem; cursor: pointer; font: inherit; }
.job-actions button.apply { background: var(--accent); color: #2a0f06; border: none; }
.job-actions button:disabled { opacity: .5; cursor: default; }
.job-caret { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 1rem; }

.job-detail { margin-top: .7rem; padding-top: .7rem; border-top: 1px solid var(--border-soft); }
.job-fit-reason { color: var(--accent); margin-bottom: .5rem; }
.job-desc { color: var(--text); font-size: .9rem; }
.job-desc a { margin-left: .4rem; white-space: nowrap; }
.job-facts { color: var(--muted); font-size: .82rem; margin-top: .5rem; display: flex; flex-wrap: wrap; gap: .75rem; }
```

- [ ] **Step 4: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/lib/jobs.ts web-next/src/lib/jobs-server.ts web-next/src/app/globals.css
git commit -m "feat(web-next): jobs lib (fit tiers, sorts, best-match) + status-sync helper + jobs CSS"
```

---

## Task 2: Apply / Dismiss API routes

**Files:**
- Create: `web-next/src/app/api/jobs/apply/route.ts`
- Create: `web-next/src/app/api/jobs/dismiss/route.ts`

**Interfaces produced:**
- `POST /api/jobs/apply` body `{id}` → `200 {ok:true}` | `400 {error}` (missing id) | `404 {error}` (no such job). Side effects: creates an application + sets job status to `applied` (column + blob).
- `POST /api/jobs/dismiss` body `{id}` → `200 {ok:true}` | `400` | `404`. Sets job status `dismissed`.

**Consumes:** `prisma` (`@/lib/db`), `setJobStatus` (`@/lib/jobs-server`), `today` (`@/lib/applications`).

- [ ] **Step 1: Write `web-next/src/app/api/jobs/apply/route.ts`**

```ts
import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { setJobStatus } from "@/lib/jobs-server";
import { today } from "@/lib/applications";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const id = String(body.id ?? "").trim();
  if (!id) return NextResponse.json({ error: "Missing id." }, { status: 400 });

  const rec = await prisma.jobs.findUnique({ where: { id } });
  if (!rec) return NextResponse.json({ error: `No job with id ${id}.` }, { status: 404 });

  const d = today();
  // Mirror web/routers/jobs.py: hand the role to the tracker, then mark applied.
  await prisma.applications.create({
    data: {
      company: rec.company, role: rec.title, url: rec.url, status: "applied",
      applied_date: d, updated_date: d, notes: "", auto_detected: 0,
    },
  });
  await setJobStatus(id, "applied");
  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 2: Write `web-next/src/app/api/jobs/dismiss/route.ts`**

```ts
import { NextResponse } from "next/server";
import { setJobStatus } from "@/lib/jobs-server";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const id = String(body.id ?? "").trim();
  if (!id) return NextResponse.json({ error: "Missing id." }, { status: 400 });
  const ok = await setJobStatus(id, "dismissed");
  if (!ok) return NextResponse.json({ error: `No job with id ${id}.` }, { status: 404 });
  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 3: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 4: Seed sample jobs, curl-verify apply/dismiss, then clean up**

Seed 3 jobs through the Python store (writes column + blob correctly):
```bash
cd /Users/kayla.li/.superset/Agent
uv run python - <<'PY'
from agents.job_scraper import store as js
js.replace_record({"id":"seed:notion:1","company":"Notion","title":"SWE Intern","location":"SF","url":"https://notion.so/j","status":"new","ats":"greenhouse","posted_at":"2026-07-18","remote":True,"compensation":"$8k/mo","department":"Eng","description":"Build blocks.","fit_score":92,"fit_reason":"Strong React match","ghost":False,"also_on":["lever"]})
js.replace_record({"id":"seed:acme:2","company":"Acme","title":"ML Intern","location":"NYC","url":"https://acme/j","status":"new","ats":"lever","posted_at":"2026-07-10","remote":False,"fit_score":60,"fit_reason":"Partial match","ghost":False,"also_on":[]})
js.replace_record({"id":"seed:old:3","company":"OldCo","title":"Legacy Dev","location":"Remote","url":"https://old/j","status":"new","posted_at":"2026-01-01","fit_score":None,"ghost":True,"also_on":[]})
print("seeded 3 jobs")
PY
```
Then verify the routes:
```bash
cd web-next && npm run dev -- -p 3007 > /tmp/next-p4.log 2>&1 &
sleep 8
BASE=http://localhost:3007
echo "missing id → 400:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/api/jobs/apply -H 'Content-Type: application/json' -d '{}'
echo "apply seed:acme:2 → 200:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/api/jobs/apply -H 'Content-Type: application/json' -d '{"id":"seed:acme:2"}'
echo "dismiss seed:old:3 → 200:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/api/jobs/dismiss -H 'Content-Type: application/json' -d '{"id":"seed:old:3"}'
echo "apply missing → 404:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/api/jobs/apply -H 'Content-Type: application/json' -d '{"id":"nope:x:9"}'
kill %1 2>/dev/null
# Confirm both the column AND the blob were updated (dual-storage sync):
sqlite3 ../data/control_center.db "SELECT id, status, json_extract(data,'\$.status') AS blob_status FROM jobs WHERE id IN ('seed:acme:2','seed:old:3');"
# Confirm apply created a tracker application for Acme:
sqlite3 ../data/control_center.db "SELECT company, role, status FROM applications WHERE company='Acme';"
```
Expected: 400, 200, 200, 404. The sqlite check shows `seed:acme:2|applied|applied` and `seed:old:3|dismissed|dismissed` (column == blob_status — sync works). The applications query shows `Acme|ML Intern|applied`.

- [ ] **Step 5: Clean up all seed data (jobs table back to empty, remove the test application)**

```bash
cd /Users/kayla.li/.superset/Agent
sqlite3 data/control_center.db "DELETE FROM jobs WHERE id LIKE 'seed:%'; DELETE FROM applications WHERE company='Acme' AND role='ML Intern';"
echo "jobs left: $(sqlite3 data/control_center.db "SELECT COUNT(*) FROM jobs;")  apps left: $(sqlite3 data/control_center.db "SELECT COUNT(*) FROM applications;")"
```
Expected: `jobs left: 0  apps left: 2` (back to the original 2 seed applications, jobs empty).

- [ ] **Step 6: Commit**

```bash
git add web-next/src/app/api/jobs
git commit -m "feat(web-next): jobs apply/dismiss API (status sync + tracker handoff)"
```

---

## Task 3: Presentational components — `BestMatchHero`, `JobRow`

**Files:**
- Create: `web-next/src/components/jobs/BestMatchHero.tsx`
- Create: `web-next/src/components/jobs/JobRow.tsx`

Both are pure (props + callbacks, no hooks); they render inside the client `JobsBoard`.

**Consumes:** `Job`, `fitTier`, `FIT_COLOR`, `ageDays`, `parseAlsoOn` from `@/lib/jobs`.

- [ ] **Step 1: Write `web-next/src/components/jobs/BestMatchHero.tsx`**

```tsx
import type { Job } from "@/lib/jobs";

export default function BestMatchHero({ job, onApply }: { job: Job; onApply: (id: string) => void }) {
  const pct = job.fit_score === null ? "" : `${Math.round(job.fit_score)}%`;
  return (
    <button className="best-match" onClick={() => onApply(job.id)}>
      <span className="bm-star">★</span>
      <span>
        <strong>{pct}</strong> — {job.title} @ {job.company}
        {job.location ? ` · ${job.location}` : ""}
      </span>
      <span className="bm-cta">→ apply</span>
    </button>
  );
}
```

- [ ] **Step 2: Write `web-next/src/components/jobs/JobRow.tsx`**

```tsx
import type { Job } from "@/lib/jobs";
import { fitTier, FIT_COLOR, ageDays, parseAlsoOn } from "@/lib/jobs";

type Props = {
  job: Job;
  expanded: boolean;
  busy: boolean;
  onToggle: (id: string) => void;
  onApply: (id: string) => void;
  onDismiss: (id: string) => void;
};

export default function JobRow({ job, expanded, busy, onToggle, onApply, onDismiss }: Props) {
  const tier = fitTier(job.fit_score);
  const color = FIT_COLOR[tier];
  const age = ageDays(job.posted_at);
  const applied = job.status === "applied";
  const dim = applied || job.ghost === 1;
  const alsoOn = parseAlsoOn(job.also_on);

  return (
    <div className={`job-row${dim ? " dim" : ""}`}>
      <div className="job-head">
        <span className="fit-badge" style={{ color, borderColor: color }}>
          {job.fit_score === null ? "—" : Math.round(job.fit_score)}
        </span>
        <div className="job-main">
          <div className="job-title">{job.title}</div>
          <div className="job-sub">
            {job.company}{job.location ? ` · ${job.location}` : ""}
          </div>
          <div className="job-badges">
            {age !== null ? <span className="job-badge">🕒 {age}d ago</span> : null}
            {job.compensation ? <span className="job-badge">{job.compensation}</span> : null}
            {job.ghost === 1 ? <span className="job-badge stale">⚠ stale{age !== null ? ` ${age}d` : ""}</span> : null}
          </div>
        </div>
        <div className="job-actions">
          {applied ? (
            <span className="status-pill" style={{ color: "#7fc08a", borderColor: "#7fc08a", background: "#7fc08a1f" }}>applied</span>
          ) : (
            <>
              <button className="apply" disabled={busy} onClick={() => onApply(job.id)}>Apply</button>
              <button disabled={busy} onClick={() => onDismiss(job.id)}>Dismiss</button>
            </>
          )}
          <button className="job-caret" aria-label="Toggle details" onClick={() => onToggle(job.id)}>
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="job-detail">
          {job.fit_reason ? <div className="job-fit-reason">{job.fit_reason}</div> : null}
          {job.description ? (
            <p className="job-desc">
              {job.description.slice(0, 400)}
              {job.url ? <a href={job.url} target="_blank" rel="noopener noreferrer">open full posting ↗</a> : null}
            </p>
          ) : job.url ? (
            <p className="job-desc"><a href={job.url} target="_blank" rel="noopener noreferrer">open full posting ↗</a></p>
          ) : null}
          <div className="job-facts">
            {job.posted_at ? <span>posted {job.posted_at}</span> : null}
            {job.department ? <span>{job.department}</span> : null}
            {job.location ? <span>{job.location}{job.remote === 1 ? " · remote" : ""}</span> : null}
            {job.compensation ? <span>{job.compensation}</span> : null}
            {alsoOn.length ? <span>also on: {alsoOn.join(", ")}</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd web-next && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/jobs/BestMatchHero.tsx web-next/src/components/jobs/JobRow.tsx
git commit -m "feat(web-next): jobs presentational components (best-match hero, row + detail)"
```

---

## Task 4: `JobsBoard` client orchestrator

**Files:**
- Create: `web-next/src/components/jobs/JobsBoard.tsx`

**Consumes:** `Job`, `JOB_STATUSES`, `byFitDesc`, `byDateDesc`, `bestMatch` (`@/lib/jobs`); `BestMatchHero`, `JobRow` (Task 3); the apply/dismiss API (Task 2).

- [ ] **Step 1: Write `web-next/src/components/jobs/JobsBoard.tsx`**

```tsx
"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { type Job, byFitDesc, byDateDesc, bestMatch } from "@/lib/jobs";
import BestMatchHero from "./BestMatchHero";
import JobRow from "./JobRow";

export default function JobsBoard({ jobs }: { jobs: Job[] }) {
  const router = useRouter();
  const [sort, setSort] = useState<"fit" | "date">("fit");
  const [statusFilter, setStatusFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [hideGhost, setHideGhost] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const c = { new: 0, applied: 0, dismissed: 0 };
    for (const j of jobs) if (j.status in c) c[j.status as keyof typeof c] += 1;
    return c;
  }, [jobs]);

  const companies = useMemo(
    () => Array.from(new Set(jobs.map((j) => j.company).filter(Boolean))).sort(),
    [jobs],
  );

  const best = useMemo(() => bestMatch(jobs), [jobs]);

  const list = useMemo(() => {
    let l = jobs.slice();
    l = statusFilter ? l.filter((j) => j.status === statusFilter) : l.filter((j) => j.status !== "dismissed");
    if (companyFilter) l = l.filter((j) => j.company === companyFilter);
    if (hideGhost) l = l.filter((j) => j.ghost !== 1);
    l.sort(sort === "date" ? byDateDesc : byFitDesc);
    return l;
  }, [jobs, statusFilter, companyFilter, hideGhost, sort]);

  async function mutate(kind: "apply" | "dismiss", id: string) {
    setBusyId(id);
    const res = await fetch(`/api/jobs/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    setBusyId(null);
    if (res.ok) router.refresh();
  }

  return (
    <>
      <div className="job-chips">
        <span className="job-chip"><strong>{counts.new}</strong> new</span>
        <span className="job-chip"><strong>{counts.applied}</strong> applied</span>
        <span className="job-chip"><strong>{counts.dismissed}</strong> dismissed</span>
      </div>

      {best ? <BestMatchHero job={best} onApply={(id) => mutate("apply", id)} /> : null}

      <div className="job-filters">
        <select value={sort} onChange={(e) => setSort(e.target.value as "fit" | "date")}>
          <option value="fit">sort: fit</option>
          <option value="date">sort: date</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">all statuses</option>
          {JOB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={companyFilter} onChange={(e) => setCompanyFilter(e.target.value)}>
          <option value="">all companies</option>
          {companies.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <label>
          <input type="checkbox" checked={hideGhost} onChange={(e) => setHideGhost(e.target.checked)} />
          hide stale/ghost
        </label>
      </div>

      {list.length === 0 ? (
        <p className="muted">No roles match these filters.</p>
      ) : (
        list.map((j) => (
          <JobRow
            key={j.id}
            job={j}
            expanded={expandedId === j.id}
            busy={busyId === j.id}
            onToggle={(id) => setExpandedId((cur) => (cur === id ? null : id))}
            onApply={(id) => mutate("apply", id)}
            onDismiss={(id) => mutate("dismiss", id)}
          />
        ))
      )}
    </>
  );
}
```

Note: the `JOB_STATUSES` import — add it to the import line: `import { type Job, JOB_STATUSES, byFitDesc, byDateDesc, bestMatch } from "@/lib/jobs";`

- [ ] **Step 2: Fix the import + type-check + build**

Ensure the import line reads:
```ts
import { type Job, JOB_STATUSES, byFitDesc, byDateDesc, bestMatch } from "@/lib/jobs";
```
Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/jobs/JobsBoard.tsx
git commit -m "feat(web-next): JobsBoard (chips, best-match, filter/sort, apply/dismiss)"
```

---

## Task 5: `/jobs` page + end-to-end verification

**Files:**
- Create: `web-next/src/app/jobs/page.tsx`

**Consumes:** `prisma`, `type Job`, `JobsBoard`, `PlanetTheme`.

- [ ] **Step 1: Write `web-next/src/app/jobs/page.tsx`**

```tsx
import { prisma } from "@/lib/db";
import type { Job } from "@/lib/jobs";
import JobsBoard from "@/components/jobs/JobsBoard";
import PlanetTheme from "@/components/applications/PlanetTheme";

export const dynamic = "force-dynamic";

// Only the columns the UI renders — never ship the `data` blob to the client.
const JOB_SELECT = {
  id: true, company: true, title: true, location: true, url: true, status: true,
  ats: true, posted_at: true, remote: true, compensation: true, department: true,
  description: true, fit_score: true, fit_reason: true, ghost: true, also_on: true,
} as const;

export default async function JobsPage() {
  const jobs = (await prisma.jobs.findMany({ select: JOB_SELECT })) as Job[];
  return (
    <>
      <PlanetTheme planet="mars" />
      <h1>jobs</h1>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet — run the Job Scraper from the dashboard.</p>
      ) : (
        <JobsBoard jobs={jobs} />
      )}
    </>
  );
}
```

- [ ] **Step 2: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0; `/jobs` is a dynamic route.

- [ ] **Step 3: End-to-end verification (seed → verify UI + interactions → clean up)**

Seed the same 3 sample jobs:
```bash
cd /Users/kayla.li/.superset/Agent
uv run python - <<'PY'
from agents.job_scraper import store as js
js.replace_record({"id":"seed:notion:1","company":"Notion","title":"SWE Intern","location":"SF","url":"https://notion.so/j","status":"new","ats":"greenhouse","posted_at":"2026-07-18","remote":True,"compensation":"$8k/mo","department":"Eng","description":"Build blocks.","fit_score":92,"fit_reason":"Strong React match","ghost":False,"also_on":["lever"]})
js.replace_record({"id":"seed:acme:2","company":"Acme","title":"ML Intern","location":"NYC","url":"https://acme/j","status":"new","ats":"lever","posted_at":"2026-07-10","remote":False,"fit_score":60,"fit_reason":"Partial match","ghost":False,"also_on":[]})
js.replace_record({"id":"seed:old:3","company":"OldCo","title":"Legacy Dev","location":"Remote","url":"https://old/j","status":"new","posted_at":"2026-01-01","fit_score":None,"ghost":True,"also_on":[]})
print("seeded")
PY
```
Then check the page renders the batch and the best-match:
```bash
cd web-next && npm run dev -- -p 3007 > /tmp/next-p4.log 2>&1 &
sleep 8
BASE=http://localhost:3007
curl -s $BASE/jobs > /tmp/jobs.html
echo "http:"; curl -s -o /dev/null -w "%{http_code}\n" $BASE/jobs
echo "Notion present:"; grep -o "Notion" /tmp/jobs.html | head -1
echo "best-match star present:"; grep -o "★" /tmp/jobs.html | head -1
echo "count chips present (new):"; grep -o "new" /tmp/jobs.html | head -1
echo "fit 92 present:"; grep -o ">92<" /tmp/jobs.html | head -1
echo "unscored dash present:"; grep -o "—" /tmp/jobs.html | head -1
kill %1 2>/dev/null
```
Expected: http 200; Notion, ★, "new", `>92<`, and `—` all found (best-match hero + the fit-92 Notion row + the unscored OldCo row rendered).

- [ ] **Step 4: Visual check with Playwright, then clean up**

Start the dev server, screenshot `/jobs`, and look at it (Mars theme, count chips, best-match hero, fit-colored badges, stale badge on OldCo). Use the Playwright MCP: `browser_navigate http://localhost:3007/jobs` then `browser_take_screenshot` (fullPage), Read the PNG, confirm the layout, then `browser_close`. Kill the dev server. Finally remove the seed data:
```bash
cd /Users/kayla.li/.superset/Agent
sqlite3 data/control_center.db "DELETE FROM jobs WHERE id LIKE 'seed:%';"
echo "jobs left: $(sqlite3 data/control_center.db "SELECT COUNT(*) FROM jobs;")"   # 0
rm -f web-next/plan4-jobs.png 2>/dev/null; rm -f plan4-jobs.png 2>/dev/null
```
Expected: the screenshot shows the Mars-themed jobs hub; jobs table back to 0 afterward.

- [ ] **Step 5: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/jobs/page.tsx
git commit -m "feat(web-next): jobs tab page (overview strip + board)"
```

---

## Plan 4 verification summary

- `npx tsc --noEmit` + `npm run build` clean
- API: apply/dismiss take `{id}` in body; 400 missing / 404 unknown; apply creates an application + sets status; **both the `status` column and the `data` blob update** (dual-storage sync verified via `json_extract`)
- Page: count chips, best-match hero (highest-fit new role), filter/sort bar, fit-colored rows (green/amber/red/—), freshness + comp + stale badges, inline expand with fit reason + description + facts; apply/dismiss update without full reload
- Empty state when no jobs; "no roles match" when filters exclude all
- All seed data removed; `jobs` table back to empty, `applications` back to 2

## Notes / carry-forward to Plan 5
- `PlanetTheme` now used by both `/applications` (saturn) and `/jobs` (mars); Plan 5 may lift per-route theming into the layout to avoid the mount flash.
- Apply reuses the same tracker-application shape as Plan 3's POST — if a dedupe guard is ever wanted, add it in one place.
- The jobs list is fetched whole and filtered client-side (fine for a daily batch). If the stored set grows large, revisit with server-side pagination/filtering.

## Self-review notes
- **Spec coverage:** overview strip (chips + best-match), filter/sort bar (fit/date, status, company, hide-ghost), fit-sorted list (unscored last), row states (applied/dismissed/ghost), expand panel (fit reason/description/facts), apply-creates-application — all covered.
- **Consistency:** apply mirrors `web/routers/jobs.py`; status writes sync column + blob (the one real cross-store risk), verified in Task 2.
- **String-id correctness:** ids travel in the POST body; no numeric-id guard (Plan 3 carry-forward honored).
- **Boundary:** web-next reads only Prisma columns; blob is written (for sync) but never read for display.
