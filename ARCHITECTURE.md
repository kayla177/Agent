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
- `POST /data/jobs/assisted-apply` — start the job-applier agent on a posting (greenhouse
  /lever/ashby only); returns `{run_id}`. Records **no** application: the agent fills a
  form and stops, so nothing is applied until the human says she pressed Submit.
  `GET /data/jobs/assisted-apply/report?run_id=` returns that run's handoff as structured
  data; `POST /data/jobs/assisted-apply/close` closes the window it left open.
- `POST /data/jobs/confirm-submission` — re-read the still-open form and, on a positive
  match only, stamp `applications.confirmed_at`. Never un-confirms or deletes anything.
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

The `jobs` row carries both **mirrored columns** and a full-record **`data` JSON blob**. Five
functions in `job_scraper/store.py` write a jobs row — `upsert_records`, `set_status`,
`replace_record`, `touch_last_seen` and `sweep_ghosts` — and every one of them routes through
the single private `_write` helper, which derives the mirrored columns from the record via
`_mirror` and dumps the same record into `data` in one statement. So the invariant is "one
write path", not "one writer": add a sixth writer and it must go through `_write` too, or the
column and the blob can diverge. (The former Next-side dual-writer is gone; Next only reads.)
No migration framework — `CREATE TABLE IF NOT EXISTS` from `schema.sql` plus additive
`store_db.py:_migrate` guards; the drift-check guards the Prisma mirror.

## Agent execution flow

1. `POST /agents/{key}/run` → `server/runner.py:start_run` creates a `runs` row and
   launches the graph driver.
2. The driver consumes `graph.astream(...)`; each node event is **persisted** to
   `node_events` and **published** to live SSE subscribers.
3. On completion the run is marked `success`/`error` with the final `output_message`.
4. Agents' **domain writes** (applications/jobs/resumes) happen inside graph nodes via the
   per-agent `store.py`, separate from run bookkeeping.

**`job_applier` is the one exception to step 1–2**, and deliberately so. Its state carries a
live Playwright context across two nodes, and Playwright's sync API is thread-affine (a
greenlet switch), while `astream` runs sync nodes in the event loop's default executor — a
*pool*. So assisted apply is driven by `server/applier_run.py` on the single dedicated thread
owned by `agents/job_applier/session.py`, which also parks the finished run's browser window
so the later confirmation read happens on the same thread. Run bookkeeping is byte-identical
(`runs` row, `node_events`, the same events published to `runner.manager`), so `RunStream` and
`/runs/{id}/events` cannot tell the difference. `server/runner.py` itself is untouched — but
"untouched" was doing double duty here and one half of it was wrong: step 1 is not merely
*bypassed* for this agent, it is **refused**. `POST /agents/job_applier/run` used to resolve
the applier's spec like any other agent and hand it to the pooled `astream` driver (and, with
a JSON body, seed its state — including the `form_url` a cookie-carrying browser would
navigate to). It now returns 409 with a pointer to `POST /data/jobs/assisted-apply`, before a
`runs` row exists. The only correct entry point is that one.

The job scraper's pipeline is a linear chain: `fetch → filter → dedupe → backfill →
freshness → rank → notify`. It also accepts a `backfill` input flag (set via
`scripts/run.py job_scraper --backfill`, and by the launchd schedule and the "score backlog"
button) that, when true and a candidate profile exists, has `backfill_node` spend its bounded
LLM refinement pass on backlog rows still missing a country/score/non-baseline reason —
otherwise that node still re-injects those rows (so country and freshness still get
recomputed) but skips the LLM pass entirely.

The job scraper detects delisted postings directly rather than inferring it from age:
`fetch_node` records `observed_ids` (every posting id any source returned) and `fetched_ok` —
the **`(company, ats)` boards** whose fetch can be trusted as a complete picture. A board
earns that only if it did not raise, returned at least one posting (an empty result without an
exception, e.g. an ATS schema change, must not read as "healthy"), and did not come back
exactly at its own page cap (`ats.PAGE_CAPS`: the Workday and SmartRecruiters adapters request
one page and never paginate, so a capped-out result is a truncated first page and everything
past it would look delisted). Trust is per board, not per company, because every one of those
failure modes belongs to one board. Detection then happens two ways:
- `freshness_node` flags a posting passing through the pipeline this run (freshly fetched, or
  re-injected by `backfill_node`) as a ghost when its own `(company, ats)` is in `fetched_ok`
  but its id is absent from `observed_ids`.
- `store.sweep_ghosts`, called from `notify_node`, RE-DERIVES the ghost state of the whole
  store, since a fully-processed row (country set, score set, already refined) is never
  re-selected by `backfill_node` and so would otherwise never pass through `freshness_node`
  again. It flags a delisted posting, **clears** a delisting flag when the board shows the
  posting again (so one false positive is not permanent), and re-derives age/deadline
  staleness in both directions — the age half needs no fetch evidence, so it runs even when
  every board failed. It only touches `new`/`viewed` rows — an `applied` row going quiet is
  normal and is never relabeled — and returns a count per outcome, each logged separately.

The age/deadline/unlisted rule itself lives once, in `matching.stale_reason`, so the pipeline
path and the store path can never disagree. `ghost_reason` is a mirrored column, so the board
can say *why* a row is flagged instead of labelling every ghost "stale".

`notify_node` also refreshes `last_seen` on every observed posting after persisting —
`dedupe` drops already-seen postings before that point, so a posting that is still listed
would otherwise never be re-stamped. `last_seen` means **"observed in a live scrape"** and
`store.touch_last_seen` (fed by `observed_ids`) is its only writer: neither `set_status` nor a
`_rescored` backfill row may stamp it, or a UI click would forge the observation that delisting
detection is built on.

The jobs board's country filter is seeded from the effective `config.JOB_COUNTRIES`, read
server-side in `app/(hub)/jobs/page.tsx` via `AGENT_SERVICE_URL` + `/prefs` and degrading to
the default if that fetch fails, so the Settings control drives the board as well as the
Discord digest. `UNKNOWN` is always added to that list regardless of the pref — an
unclassifiable location is never dropped by the scraper and must never be silently hidden.

## Config & secrets

`config.py` resolves each editable pref as **env var → `data/prefs.json` → default**, and
`config.refresh()` lets the long-lived FastAPI process pick up a Settings save without a
restart. Secrets load from `.env` and are never returned by the API (the Settings page
shows only whether each is set).
