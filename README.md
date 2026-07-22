# daily-agents

A personal, single-user platform that runs a loop of **LangGraph agents** for a
job/career + markets workflow, with a **Next.js** control center to trigger them,
watch runs live, and browse the data they produce. Hybrid model setup: a local
LLM (Ollama) for cheap/private steps, a hosted API for hard reasoning.

## Agents

| Key | What it does |
|---|---|
| `morning_briefing` | Weather, commute/traffic, calendar, and a news catch-up. |
| `stock_digest` | Watchlist quotes, technical indicators, news sentiment (info only). |
| `job_scraper` | New co-op/intern/new-grad roles from official ATS boards, ranked by fit. |
| `application_tracker` | Application pipeline, follow-up reminders, interviews. |
| `gmail_sync` | Scans recent email and auto-advances application statuses. |
| `resume_generator` | ATS-tailored résumé drafts per scraped job. |

## Architecture (two processes, one SQLite file)

```
server/        Python FastAPI agent service — :8001
  __main__.py    launch: python -m server
  app.py         app factory (agent-only: run triggers, SSE, prefs, uploads)
  runner.py      drives graph.astream -> SSE + SQLite persistence
  db.py          run history (runs + node_events)
  schema.sql     run-history DDL
  prefs.py       read/write data/prefs.json
  routers/       runs, prefs, resume
web-next/      Next.js frontend — :3000  (owns all UI; reads SQLite via Prisma)
agents/        the six LangGraph agents; registry.py = uniform descriptor
shell/         reusable platform: model_router (LiteLLM), discord_client
scripts/run.py generic CLI runner: python scripts/run.py <agent_key> [--send]
ops/*.plist    launchd schedules
config.py      preferences (env -> data/prefs.json -> defaults) + .env secrets
data/          local-only runtime state (gitignored): control_center.db, prefs.json
docs/          design specs & plans (see docs/superpowers/specs)
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the request-routing and data-ownership
details (and the in-progress restructure toward a single-writer data layer).

## Run it (two terminals)

```bash
# 1) agent service (FastAPI) on :8001
python -m server            # (venv active; or .venv/bin/python -m server)

# 2) frontend (Next.js) on :3000
cd web-next && npm run dev
```

Open **http://localhost:3000**. The frontend reads/writes the SQLite DB and calls the
agent service on :8001 to run agents and stream results. Agents that use the local LLM
also need **Ollama** running (`ollama serve`).

> `uv` works too (`uv run python -m server`) if installed, but is not required — a plain
> venv is enough.

## Run one agent from the CLI

```bash
python scripts/run.py morning_briefing          # build + print only
python scripts/run.py job_scraper --send        # + deliver to Discord
# résumé generator has its own richer CLI:
python scripts/run_resume_generator.py --list
```

## Models (Ollama)

- `llama3.1:8b`  → role **local** (fast instruct: summarize/format)
- `deepseek-r1:8b` → role **reasoner** (slow reasoning; deliberate use)
- hosted (e.g. `anthropic/claude-sonnet-5`) → role **smart**

Switch any role in `config.py` (`MODEL_ROLES`).

## Setup

1. `cp .env.example .env` and fill in values.
2. Edit personal prefs in `config.py` (or via the Settings tab once running):
   `WEATHER_LATITUDE/LONGITUDE/TIMEZONE`, `COMMUTE_ORIGIN/DESTINATION`, `NEWS_TOPICS`,
   `STOCK_WATCHLIST`, job sources.
3. **Discord:** create a bot, invite it, set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID`.
4. **Google Maps:** enable the Routes API → `GOOGLE_MAPS_API_KEY`.
5. **Google (Calendar + Gmail):** create a Desktop OAuth client, download the JSON, then
   one-time consent: `python -m agents.morning_briefing.nodes.calendar --auth`
   (Gmail sync reuses the same token).

## Schedule (launchd)

```bash
cp ops/com.kayla.daily-agents.briefing.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kayla.daily-agents.briefing.plist
```

The plists call `scripts/run.py <agent_key> --send`. The Mac must be awake at the
scheduled time (`sudo pmset repeat wake MTWRFSU 06:58:00`). Logs land in the project root.
