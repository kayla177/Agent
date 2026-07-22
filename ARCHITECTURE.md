# Architecture

Two processes share one SQLite file (`data/control_center.db`, WAL mode).

```
Browser ──► Next.js (web-next, :3000) ──► SQLite  (reads, via Prisma)
                     │
                     └─ same-origin proxy (next.config.ts rewrites) ──► FastAPI (server, :8001)
                                                                          │
                                                                          ├─ runs agents (LangGraph)
                                                                          ├─ streams run events (SSE)
                                                                          └─ prefs + file uploads
Agents (LangGraph nodes) ──► SQLite  (domain writes, via the Python stores)
launchd ──► scripts/run.py <agent_key> --send
```

## Who serves what

**FastAPI (`server/`, `127.0.0.1:8001`)** — agent-only service. Endpoints:
- `POST /agents/{key}/run` — start a run, returns `{run_id}` (throttled ~60s unless forced)
- `GET /runs/{id}/events` — SSE; replays persisted `node_events`, then live queue
- `GET|POST /prefs` — read/write `data/prefs.json` (via `server/prefs.py` → `config.refresh()`)
- `POST /experience/upload` — PDF/DOCX parse into the résumé experience pool
- `GET /healthz`

**Next.js (`web-next/`, `:3000`)** — owns all UI. Server components read the DB directly
via Prisma. A few paths are proxied to :8001 by `next.config.ts` rewrites
(`/agents/*`, `/runs/*`, `/prefs`, `/experience/*`).

### Request routing — current state
- **Reads + list/detail:** Next.js server components + Prisma (`web-next/src/lib/*`).
- **Agent runs / SSE / prefs / uploads:** proxied to FastAPI :8001.
- **Domain mutations (today):** handled locally by Next.js API routes writing Prisma
  (`web-next/src/app/api/**`) — applications, jobs apply/dismiss, résumés, experience docs.

> **In progress (restructure Phase 2 — see `docs/superpowers/specs/2026-07-21-restructure-design.md`):**
> mutations move to FastAPI so the **backend owns all writes** and Next.js only reads via
> Prisma + calls the API to mutate. This removes the dual-writer described below.

## Data model & schema ownership

Six tables in `data/control_center.db`:

| Table | Written by | Read by |
|---|---|---|
| `applications` | `agents/application_tracker/store.py`; Next API | both sides |
| `jobs` | `agents/job_scraper/store.py`; `web-next/src/lib/jobs-server.ts` | both sides |
| `runs` | `server/db.py` | Python; Next (history) |
| `node_events` | `server/db.py` | Python; Next (via `runs` relation) |
| `experience_docs` | `agents/resume_generator/store.py`; Next API | both sides |
| `resumes` | `agents/resume_generator/store.py`; Next API | both sides |

**Schema ownership today is split** (a known issue targeted by Phase 2): DDL lives in
`store_db.py` (applications, jobs), `server/schema.sql` (runs, node_events), and
`agents/resume_generator/store.py` (experience_docs, resumes); `web-next/prisma/schema.prisma`
**re-declares all six** as a hand-kept mirror (equivalent to `prisma db pull`). No migration
framework — tables are created with `CREATE TABLE IF NOT EXISTS` by whichever side runs first.

The `jobs` row carries both **mirrored columns** and a full-record **`data` JSON blob**;
these are kept in sync by two writers (`job_scraper/store.py` and `jobs-server.ts`) — the
dual-writer fragility Phase 2 removes by routing all writes through the Python store.

## Agent execution flow

1. `POST /agents/{key}/run` → `server/runner.py:start_run` creates a `runs` row and
   launches the graph driver.
2. The driver consumes `graph.astream(...)`; each node event is **persisted** to
   `node_events` and **published** to live SSE subscribers.
3. On completion the run is marked `success`/`error` with the final `output_message`.
4. Agents' **domain writes** (applications/jobs/resumes) happen inside graph nodes via the
   per-agent `store.py`, separate from run bookkeeping.

## Config & secrets

`config.py` resolves each editable pref as **env var → `data/prefs.json` → default**, and
`config.refresh()` lets the long-lived FastAPI process pick up a Settings save without a
restart. Secrets load from `.env` and are never returned by the API (the Settings page
shows only whether each is set).
