# daily-agents

A personal multi-agent platform that runs on a **hybrid** model setup — a local
LLM (Ollama) for cheap/private steps, with a hosted API available for hard
reasoning later. Built on **LangGraph**: each agent is a graph that plugs into a
shared shell (model router + Discord delivery + scheduler).

**Phase 1 agent:** Morning Briefing — weather, commute/traffic, calendar, and a
news catch-up, delivered to Discord on a 7am schedule.

## Layout

```
shell/            reusable platform
  model_router.py   LiteLLM wrapper; roles: local / reasoner / smart
  discord_client.py REST delivery to a Discord channel
agents/morning_briefing/
  graph.py          LangGraph: parallel fan-out -> synthesize -> deliver
  state.py          shared TypedDict
  nodes/            weather, commute, calendar, news, synthesize, deliver
scripts/run_briefing.py   run once (--send to deliver)
ops/*.plist               launchd job (7am)
config.py                 preferences + secret loading (.env)
agents/registry.py        uniform descriptor over all agents (used by the web UI)
web/                       FastAPI control center (dashboard, live runs, settings)
  app.py                    app factory; launch with `uv run python -m web`
  runner.py                 drives graph.astream -> SSE + SQLite persistence
  db.py / schema.sql        run history (runs + node_events)
  prefs.py                  read/write data/prefs.json (editable preferences)
  routers/ templates/ static/
data/                      local-only runtime state (gitignored): prefs.json, SQLite
```

## Web control center

A local, single-user web UI to trigger any agent, watch it run node-by-node in
real time, read the rendered output, browse run history, and edit preferences.

```bash
uv run python -m web        # http://127.0.0.1:8000  (or: uv run uvicorn web.app:app)
```

- **Dashboard** — run any agent in *Preview* (no Discord) or *Run & send* mode.
- **Run detail** — live node execution via SSE + the rendered briefing.
- **History** — every run, filterable by agent (stored in `data/control_center.db`).
- **Settings** — edit preferences (location, addresses, news topics, watchlist,
  job sources) without touching code. Saved to `data/prefs.json`, which
  `config.py` overlays on top of its defaults; **secrets stay in `.env`** (the
  page shows only whether each is set). Edits apply on the next run — no restart.

## Models (Ollama)

- `llama3.1:8b`  -> role **local** (fast instruct: summarize/format)
- `deepseek-r1:8b` -> role **reasoner** (slow reasoning; deliberate use)

Switch any role to a hosted model in `config.py` (`MODEL_ROLES`).

## Setup

1. `cp .env.example .env` and fill in values (see below).
2. Edit personal prefs in `config.py`: `WEATHER_LATITUDE/LONGITUDE/TIMEZONE`,
   `COMMUTE_ORIGIN/DESTINATION`, `NEWS_TOPICS`.
3. **Discord:** create a bot, invite it to your server, put `DISCORD_BOT_TOKEN`
   and `DISCORD_CHANNEL_ID` in `.env`.
4. **Google Maps:** enable Routes API, create an API key -> `GOOGLE_MAPS_API_KEY`.
5. **Google Calendar:** create a Desktop OAuth client, download the JSON to
   `google_oauth_client.json`, then run the one-time consent:
   `uv run python -m agents.morning_briefing.nodes.calendar --auth`

## Run

```bash
uv run python scripts/run_briefing.py          # print only
uv run python scripts/run_briefing.py --send    # + deliver to Discord
```

## Schedule (7am)

```bash
cp ops/com.kayla.daily-agents.briefing.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kayla.daily-agents.briefing.plist
```

The Mac must be awake at 7am. To auto-wake daily:
`sudo pmset repeat wake MTWRFSU 06:58:00`

Logs: `briefing.log` / `briefing.error.log` in the project root.
