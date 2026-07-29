# Architecture

Two processes share one SQLite file (`data/control_center.db`, WAL mode).

```
Browser ──► Next.js (web-next, :3000) ──► SQLite  (READS only, via Prisma)
                     │
                     └─ same-origin proxy (next.config.ts rewrites) ──► FastAPI (server, :8001)
                                                                          │
                                                                          ├─ runs agents (LangGraph)
                                                                          ├─ streams run events (SSE)
                                                                          ├─ ALL DB writes (/data/*)
                                                                          ├─ agent metadata (/agents)
                                                                          └─ prefs + file uploads
Agents (LangGraph nodes) ──► SQLite  (domain writes, via the Python stores)
launchd ──► scripts/run.py <agent_key> --send
```

**The backend is the single DB writer.** Next.js only *reads* (Prisma); every
mutation is proxied to FastAPI, which writes through the Python stores.

## Who serves what

**FastAPI (`server/`, `127.0.0.1:8001`)** — agent-only service. Endpoints:
- `POST /agents/{key}/run` — start a run, returns `{run_id}` (throttled ~60s unless forced)
- `GET /runs/{id}/events` — SSE; replays persisted `node_events`, then live queue
- `GET|POST /prefs` — read/write `data/prefs.json` (via `server/prefs.py` → `config.refresh()`)
- `POST /experience/upload` — PDF/DOCX parse into the résumé experience pool
- `POST|PATCH|DELETE /data/*` — all DB mutations (applications, jobs apply/dismiss,
  résumés, experience docs), wrapping the Python stores
- `GET|PUT /data/profile` — applicant profile (typed autofill fields + fit-scoring summary)
- `POST /data/jobs/{apply,dismiss,status,undo-apply}` — job status + application logging
- `GET /data/jobs/resume-pdf` — compiled résumé PDF (content-versioned cache)
- `GET|PUT /data/resume/master` — master résumé; `GET /data/resumes/{job_id}/versions`
- `POST /data/render`, `POST /data/resume/pdf` — markdown render + LaTeX→PDF (Tectonic)
- `GET /stocks/desk` — latest persisted `stock_analysis` (no model call on page load).
  Not proxied — `web-next/src/lib/stocks-server.ts` fetches `:8001` directly, server-side.
- `GET /agents` — UI metadata for every agent (sourced from `agents/registry.py`)
- `GET /healthz`

Routes live in `server/routers/` (`runs`, `prefs`, `resume`, `applications`, `jobs`,
`stocks`), included by `server/app.py`. **Uvicorn is not reload-watching** — restart
`.venv/bin/python -m server` after adding or changing a route.

**Next.js (`web-next/`, `:3000`)** — owns all UI. Server components read the DB directly
via Prisma. Mutations and agent actions are proxied to :8001 by `next.config.ts` rewrites
(`/agents/*`, `/runs/*`, `/prefs`, `/experience/*`, `/data/*`).

### Request routing
- **Reads + list/detail:** Next.js server components + Prisma (`web-next/src/lib/*`).
- **All mutations:** proxied to FastAPI `/data/*` (single writer). There are no
  Prisma writes from Next.js.
- **Agent runs / SSE / prefs / uploads / agent metadata:** proxied to FastAPI :8001.

## Data model & schema ownership

Ten tables in `data/control_center.db`. **`schema.sql` (repo root) is the single source of
truth** — `store_db.init_db()` applies it, and `server/db.py` + the agent stores delegate
there. `web-next/prisma/schema.prisma` mirrors it and is verified by `npm run db:check`
(builds a temp DB from `schema.sql`, diffs against the Prisma datamodel; fails on drift).

| Table | Written by (single writer) | Read by |
|---|---|---|
| `applications` | `agents/application_tracker/store.py` (via `/data/applications`) | both sides |
| `jobs` | `agents/job_scraper/store.py` (via `/data/jobs/*`) | both sides |
| `runs` | `server/db.py` | Python; Next (history) |
| `node_events` | `server/db.py` | Python; Next (via `runs` relation) |
| `experience_docs` | `agents/resume_generator/store.py` (via `/data/resume/docs`) | both sides |
| `resumes` | `agents/resume_generator/store.py` (via `/data/resumes`) | both sides |
| `master_resume` | `agents/resume_generator/store.py` (via `PUT /data/resume/master`) | both sides |
| `resume_versions` | `agents/resume_generator/store.py` (inside graph nodes) | Python; Next (via `/data/resumes/{job_id}/versions`) |
| `stock_analysis` | `agents/stock_digest/store.py` (inside graph nodes) | Python; Next (via `/stocks/desk`) |
| `applicant_profile` | `profile_store.py` (via `PUT /data/profile`) | both sides |

The `jobs` row carries both **mirrored columns** and a full-record **`data` JSON blob**;
`job_scraper/store.py:set_status` updates both together, so the single writer keeps them in
sync (the former Next-side dual-writer is gone). No migration framework — `CREATE TABLE IF
NOT EXISTS` from `schema.sql`; the drift-check guards the Prisma mirror.

## Agent execution flow

1. `POST /agents/{key}/run` → `server/runner.py:start_run` creates a `runs` row and
   launches the graph driver.
2. The driver consumes `graph.astream(...)`; each node event is **persisted** to
   `node_events` and **published** to live SSE subscribers.
3. On completion the run is marked `success`/`error` with the final `output_message`.
4. Agents' **domain writes** (applications/jobs/resumes) happen inside graph nodes via the
   per-agent `store.py`, separate from run bookkeeping.

The job scraper's pipeline is a linear chain: `fetch → filter → dedupe → backfill →
freshness → rank → notify`. It also accepts a `backfill` input flag (set via
`scripts/run.py job_scraper --backfill`, and by the launchd schedule and the "score backlog"
button) that, when true and a candidate profile exists, has `backfill_node` spend its bounded
LLM refinement pass on backlog rows still missing a country/score/non-baseline reason —
otherwise that node still re-injects those rows (so country and freshness still get
recomputed) but skips the LLM pass entirely.

The job scraper detects delisted postings directly rather than inferring it from age:
`fetch_node` records `observed_ids` (every posting id any source returned) and `fetched_ok`
(companies whose **every** configured source both fetched without error **and returned at
least one posting** — an empty result without an exception, e.g. an ATS schema change, must
not be read as "healthy", even when a second source for the same company did return
postings). Detection then happens two ways:
- `freshness_node` flags a posting passing through the pipeline this run (freshly fetched, or
  re-injected by `backfill_node`) as a ghost when its company is in `fetched_ok` but its id is
  absent from `observed_ids`.
- `store.sweep_delisted`, called from `notify_node`, flags the same signal directly against
  the WHOLE store, since a fully-processed row (country set, score set, already refined) is
  never re-selected by `backfill_node` and so would otherwise never pass through
  `freshness_node` again. It only touches `new`/`viewed` rows — an `applied` row going quiet
  is normal and is never relabeled.

`notify_node` also refreshes `last_seen` on every observed posting after persisting —
`dedupe` drops already-seen postings before that point, so a posting that is still listed
would otherwise never be re-stamped.

## Config & secrets

`config.py` resolves each editable pref as **env var → `data/prefs.json` → default**, and
`config.refresh()` lets the long-lived FastAPI process pick up a Settings save without a
restart. Secrets load from `.env` and are never returned by the API (the Settings page
shows only whether each is set).
