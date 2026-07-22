# Restructure Design — a reliable career/jobs/stock agent loop

**Date:** 2026-07-21
**Status:** Approved direction; ready for phased implementation plans
**Scope:** Whole-project foundation pass — folder structure, backend/data layer, frontend navigation

---

## Context

`daily-agents` grew organically from a single morning-briefing agent into six agents
(morning_briefing, stock_digest, job_scraper, application_tracker, gmail_sync,
resume_generator) plus two web stacks. The frontend was migrated from a Jinja UI to
Next.js. The goal now is to make this an application the owner **relies on daily** for
job search, applications, and stock tracking — so it must be reliable and maintainable,
not just functional.

Three problems, confirmed by a full read of the codebase (file:line evidence in each
section below):

1. **Folder sprawl.** Two directories both read as "web" (`web/` = Python FastAPI
   service, `web-next/` = the real frontend). Loose root modules (`config.py`,
   `store_db.py`). Agent packages are structurally inconsistent (gmail_sync uses a flat
   `node.py` and no store; others use `nodes/` + `store.py`). Five near-identical
   `scripts/run_*.py` wrappers. Dead weight: `data/applications.json`,
   `scripts/migrate_json_to_sqlite.py`, an unused `jinja2` dependency, and a stale
   README that still documents port 8000 and the retired Jinja pages.

2. **Fragile data layer.** One SQLite file (`data/control_center.db`) but its schema is
   declared in **four places across two languages**: `store_db.py` (applications, jobs),
   `web/schema.sql` (runs, node_events), `agents/resume_generator/store.py`
   (experience_docs, resumes), and `web-next/prisma/schema.prisma` (re-declares all six).
   Every column is defined twice with nothing enforcing agreement. The `jobs` table is
   **dual-written** — mirrored columns plus a `data` JSON blob — by two separate
   implementations (`agents/job_scraper/store.py` and `web-next/src/lib/jobs-server.ts`).
   No migrations. The settings page hard-codes `http://127.0.0.1:8001/prefs`
   (`web-next/src/app/settings/page.tsx:7`) while the form POSTs a relative `/prefs`
   through a proxy rewrite — the same resource reached two ways ("endpoint doesn't know
   where to point").

3. **Confusing navigation.** Two parallel nav systems: the top **navbar**
   (`web-next/src/components/TopNav.tsx` — routes: dashboard/jobs/applications/resume/
   history/settings) and the hero **pill-nav** (`web-next/src/components/dashboard/
   DashboardHero.tsx` — agents: briefing/stocks/jobs/tracker). They collide: navbar
   "jobs" opens a page, pill "jobs" runs an agent; pill "tracker" ≈ navbar
   "applications"; pills "briefing" and "stocks" have **no page at all**. The stocks
   watchlist chart that existed in the retired Jinja dashboard
   (`git show 6aa979e^:web/static/charts.js` → `renderStocks`) was dropped in the port
   and replaced with a static "No quotes yet" placeholder (`page.tsx:33-37`).

**Intended outcome:** one clear navigation model, a single-writer data layer with no
schema drift, and a folder layout where each directory's purpose is obvious — delivered
in phased, individually-shippable steps so the app keeps working throughout.

---

## Decisions (locked)

| Area | Decision |
|---|---|
| **Navigation** | Option C — a **dashboard hub** with a persistent planet-hero shell; the four domain pills are **real routes** (`/`, `/stocks`, `/jobs`, `/tracker`). Navbar demoted to utilities (`history · settings`). Resume folds into `/tracker`. |
| **Data layer** | **Backend owns all writes.** One `schema.sql` is the single source of truth; Prisma is generated + drift-checked from it. All DB writes go through FastAPI; Next.js **reads via Prisma, mutates via API**. |
| **Sequencing** | **Phased.** P1 structure/cleanup → P2 data layer → P3 frontend hub. Each is its own branch/PR; the app stays working after every phase. |

### Resolved TBDs
- **Folder names:** rename the Python FastAPI service `web/` → `server/`. Keep `web-next/`
  as the frontend (renaming it churns `package.json`, Prisma's relative
  `file:../../data/...` url, `next.config.ts`, and all imports for little gain); its role
  is clarified in docs. This alone removes the "two webs" ambiguity (`server/` = backend,
  `web-next/` = frontend).
- **Reads:** run-history and all list/detail **reads stay in Next.js/Prisma**. Only
  **writes** move to FastAPI. This keeps server components fast and limits Phase-2 blast
  radius to mutation paths.

## Non-goals
- No switch to Postgres/ORM migrations frameworks (stay on SQLite + a generated Prisma
  mirror + a drift check).
- No visual redesign of the planet theme — it stays; we only reorganize where things live.
- No new agents or agent capabilities in this restructure.
- No auth/multi-user work (single-user local app).

---

## Target architecture

### A. Frontend — Option C routed hub (Phase 3)

**Shell + routing.** Introduce a shared hub layout that renders the planet hero once and
hosts the four domain routes beneath it:

```
/           → hub shell + briefing panel      (planet: earth)
/stocks     → hub shell + stocks panel (chart + digest)   (jupiter)
/jobs       → hub shell + jobs board          (mars)
/tracker    → hub shell + applications + resume (saturn)
```

- Implement with a Next.js **route group** (e.g. `src/app/(hub)/layout.tsx`) that renders
  the hero + pill-nav; each of `/`, `stocks`, `jobs`, `tracker` is a `page.tsx` inside
  the group. The pill-nav becomes `<Link>`-based (active state via `usePathname()`),
  replacing the current React-state switcher in `DashboardHero.tsx`.
- **Planet theming unifies** to one mechanism: the hub layout sets `body[data-planet]`
  from the active route. Delete the competing `PlanetTheme` component
  (`web-next/src/components/applications/PlanetTheme.tsx`) and `DashboardHero`'s ad-hoc
  effect; theme is derived from the route, so `history`/`settings` no longer leak the
  previous planet.
- **Navbar** (`TopNav.tsx`) shrinks to `history · settings`. `resume` link is removed;
  resume UI moves into the `/tracker` page (career). `applications` and `dashboard`
  labels retire (folded into the hub pills).

**Stocks chart.** Restore the watchlist bar chart on `/stocks`, reusing the retired
design (`git show 6aa979e^:web/static/charts.js`) reimplemented as a dependency-free
SVG bar chart (mirroring the approach already used for `PipelineDonut.tsx`). It needs a
**persisted quotes source** (see Phase 2/3 data note) — not a live per-request fetch.

**Component reorg.** Add `web-next/src/components/shared/` (or `ui/`) for cross-cutting
pieces currently misfiled: move `RunStream.tsx` (SSE viewer) and the theming logic there.
Fold `PipelineDonut` (dashboard) and `PipelineBars` (applications) — two views of the
same `computeStats` data — so there is one pipeline visualization used in both places.

**CSS split.** Break the single 334-line `globals.css` into: base/tokens + hero shell in
globals, and per-domain styles co-located with their route/components (CSS modules or
clearly-bannered partials). No visual change — pure reorganization.

### B. Backend / data — single schema, single writer (Phase 2)

**One schema, generated Prisma.**
- Canonical DDL lives in **one** `server/schema.sql` covering all six tables
  (applications, jobs, runs, node_events, experience_docs, resumes). Python `init_db`
  executes it; delete the DDL in `store_db.py`, `agents/resume_generator/store.py`, and
  the old `web/schema.sql`.
- Prisma's `schema.prisma` is regenerated from a freshly-initialized DB via
  `prisma db pull`. Add a **drift check** (a script / CI step: init a temp DB from
  `schema.sql`, `prisma db pull` into a temp schema, `git diff` against the committed
  `schema.prisma` — non-empty diff fails). Documented in `server/README` and wired to a
  `package.json` script.
- **One DB-path constant**: a single `config.DB_PATH` consumed by every Python store;
  drop the duplicate literal in `web/db.py`. Prisma keeps its relative
  `file:../../data/control_center.db` (points at the same file).

**Backend owns all writes.**
- FastAPI (`server/`, :8001) gains thin write endpoints that wrap the **existing** Python
  store functions (no new persistence logic):
  - Applications — `POST /applications`, `PATCH /applications/{id}/status`,
    `DELETE /applications/{id}` → `agents/application_tracker/store.py`
    (`add_application`, `update_status`, `delete_application`).
  - Jobs — `POST /jobs/apply`, `POST /jobs/dismiss` (or `PATCH /jobs/{id}/status`) →
    `agents/job_scraper/store.py` (`set_status`), plus the apply-creates-an-application
    flow. This **retires** `web-next/src/lib/jobs-server.ts` and its dual-write of the
    `data` blob — the Python store becomes the only writer of `jobs`.
  - Resume — `PATCH /resumes/{job_id}`, `POST /experience`, `DELETE /experience/{id}` →
    `agents/resume_generator/store.py` (`upsert_resume`/`set_resume_status`,
    `add_experience_doc`, `delete_experience_doc`). (`POST /experience/upload` already
    exists in `server/routers/resume.py`.)
- Next.js mutation routes under `web-next/src/app/api/**` are **replaced by proxy calls**
  to these FastAPI endpoints (via the existing `next.config.ts` rewrites, extended to
  cover `/applications`, `/jobs`, `/resumes`, `/experience`). Client components keep
  calling same-origin relative URLs; the rewrite forwards to :8001. The eight local
  Prisma-writing route handlers are deleted.
- **Reads unchanged**: server components and the history page keep reading via Prisma
  (`web-next/src/lib/*`).

**Fix `/prefs`.** Settings page stops hard-coding `http://127.0.0.1:8001/prefs`; it reads
through the same relative `/prefs` rewrite the form already uses. One path, one owner
(`server/routers/prefs.py`).

**Single agent metadata.** Add `planet`, `label`, `emoji` to `AgentSpec` in
`agents/registry.py` (the existing single registry of all six agents) and expose
`GET /agents`. Next.js consumes that instead of the hand-maintained, 4-of-6
`web-next/src/lib/agents.ts` — killing that drift.

**Quotes source (enables the stocks chart).** Add a small persisted quotes cache the
Stock Digest agent writes on each run (a `quotes` table or a cached JSON the digest
node produces), so `/stocks` can render without a live per-request market call. Defined
in `schema.sql`; written by `agents/stock_digest`; read by Next/Prisma. (Chart UI is
Phase 3; the data source lands with the Phase-2 schema work.)

### C. Folder structure — clarity & consistency (Phase 1)

- **Rename** `web/` → `server/` (update `python -m web` → `python -m server`,
  `next.config.ts` proxy targets, `ops/*.plist` if referenced, README).
- **Consistent agent packages.** Normalize `gmail_sync`: `node.py` → `nodes/` package
  matching the others; document the convention (each agent = `graph.py`, `state.py`,
  `nodes/`, optional `store.py`, and clearly-named helper modules). gmail_sync continues
  to reuse `application_tracker`'s store (documented, not duplicated).
- **Consolidate run scripts.** Replace the five `scripts/run_*.py` with one generic
  `scripts/run.py <agent_key> [--send]` driven by `agents/registry.py` (`get_spec` +
  `build_graph`). Update `ops/*.plist` to call it.
- **Delete dead code:** `data/applications.json`, `scripts/migrate_json_to_sqlite.py`,
  the `jinja2` dependency in `pyproject.toml`, and default Next scaffold assets
  (`web-next/public/*.svg`, boilerplate `web-next/README.md`).
- **Refresh docs:** rewrite root `README.md` to describe the real two-process
  architecture (`server/` on :8001 + `web-next/` on :3000, `python -m server` +
  `npm run dev`), the six agents, and the data model. Add a short `ARCHITECTURE.md`
  capturing the read/write split and schema ownership.

---

## Phased plan

Each phase is a branch off `main` and a PR; the app runs after each.

### Phase 1 — Structure & cleanup (low risk)
Renames (`web/`→`server/`), agent-package consistency (gmail_sync), run-script
consolidation, dead-code removal, README/ARCHITECTURE docs.
**Verify:** `python -m server` boots on :8001 (`/healthz` 200); `npm run dev` serves
:3000 and all tabs load; `scripts/run.py morning_briefing` runs; `pytest` green;
`ops/*.plist` paths updated.

### Phase 2 — Data layer (single schema + single writer)
One `server/schema.sql`; regenerate + drift-check Prisma; single `config.DB_PATH`; add
FastAPI write endpoints wrapping existing stores; extend `next.config.ts` rewrites;
delete Next write routes + `jobs-server.ts`; fix `/prefs`; unify agent metadata via
`GET /agents`; add the `quotes` source.
**Verify:** create/update/delete an application, apply/dismiss a job, edit a resume —
all round-trip through :8001 and reflect in Prisma reads; drift check passes; settings
save works via one path; `pytest` green (add store/endpoint tests).

### Phase 3 — Frontend hub (Option C, routed)
Hub route group + persistent hero; pill-nav as `<Link>`s to `/`, `/stocks`, `/jobs`,
`/tracker`; unified route-driven theming (delete `PlanetTheme` + ad-hoc effect); navbar
→ utilities; resume into `/tracker`; restore stocks chart from the `quotes` source; merge
pipeline visualizations; split CSS.
**Verify:** each route deep-links/refreshes/back-buttons correctly with the right planet
theme; `/stocks` shows the chart with real data; resume works under `/tracker`; no theme
leak on `history`/`settings`; Playwright walkthrough of all routes; lint + typecheck
clean.

---

## Risks & mitigations
- **Backend-owns-writes is the biggest lift.** Mitigate: endpoints are thin wrappers over
  already-tested store functions; migrate one resource at a time (applications → jobs →
  resume), keeping the app working between each.
- **Renames break imports / launchd.** Mitigate: contained to Phase 1, grep-verified,
  smoke-tested (`/healthz`, one agent run) before merge.
- **Prisma drift check friction.** Mitigate: make it a single `npm run db:check` script
  with clear output; document the "edit schema.sql → regenerate" loop.
- **Stocks chart needs data.** Mitigate: the `quotes` source is scheduled in Phase 2 so
  Phase 3 UI has something real to render.

## Open questions (for plan-time)
- Exact shape of the `quotes` source: dedicated table vs. cached JSON blob written by the
  digest node. (Lean: small `quotes` table keyed by symbol+date.)
- Whether to keep `web-next/` name or rename to `app/` in a later cosmetic pass (deferred;
  not in this scope).
