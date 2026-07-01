# CLAUDE.md — daily-agents

Read this first. It captures **what we're building and why**, the conventions to
follow, and the gotchas that aren't obvious from the code. Keep it up to date as
the project evolves.

## The vision

A **personal, local-first multi-agent platform** that helps Kayla with daily life.
Each "agent" is a small, focused automation that gathers information and delivers
it to her (mainly via a Discord bot, and viewable in a local web control center).

The guiding idea: **run on a local LLM by default** (private, free, offline-capable),
and reach for a hosted model only where quality genuinely demands it.

Original seed ideas that shaped the roadmap:
1. A morning briefing (weather, commute, calendar, news) — *built*
2. Daily market/stock info — *built*
3. A job scraper for co-op/intern roles from official sources — *built*
4. Résumé/cover-letter tailoring for applications — *built*
5. Tracking applications + follow-ups — *built*

## Core principles (don't violate these without discussion)

- **Hybrid models, local-first.** `config.py` maps logical roles to models:
  `local` = `ollama/llama3.1:8b` (fast; default for summarize/format), `reasoner`
  = `ollama/deepseek-r1:8b` (slow reasoning; use deliberately), `smart` =
  hosted Claude (only when quality needs it; needs `ANTHROPIC_API_KEY`). Prefer
  `local`. Where an agent wants `smart`, make it **fall back to local** when no key
  is set (see `agents/resume_tailor/store.py::smart_role`) so everything works offline.
- **Each agent is a LangGraph graph on a shared shell.** Never duplicate the shell.
  `shell/model_router.py` (`llm(role, prompt, ...)`) and `shell/discord_client.py`
  (`send_message(text)`) are the shared services every agent reuses.
- **Facts are deterministic; the LLM only phrases.** Never let a model invent
  numbers, prices, job postings, or résumé facts. Prompts that touch the résumé
  forbid fabrication. Stock output is **information, not financial advice** (no
  buy/sell calls). The job pipeline reads **official ATS APIs**, not scraped HTML.
- **No irreversible outward actions on the user's behalf.** The job/résumé agents
  **never auto-submit** applications (ToS/ban risk) — they prepare drafts and links.
- **Privacy.** Secrets live in `.env`; personal data (résumé, applications, drafts,
  SQLite, prefs) lives under `data/`. Both are gitignored — never commit them.

## Architecture

```
shell/            reusable platform services
  model_router.py   LiteLLM wrapper — llm(role, prompt, system=, temperature=, max_tokens=)
  discord_client.py REST delivery to a Discord channel (chunks long messages)
agents/<name>/     one folder per agent, each a self-contained LangGraph graph
  state.py          TypedDict(total=False) for the graph's state
  graph.py          build_<name>_graph(*, send: bool) -> compiled graph
  nodes/            node fns: take state, return a partial dict, degrade gracefully
agents/registry.py  AgentSpec registry the web UI drives generically
web/                local FastAPI + Jinja/HTMX/SSE control center (uv run python -m web)
  routers/, templates/, static/, runner.py (streams graph.astream), db.py (SQLite history)
config.py           model roles + preferences; data/prefs.json overlays defaults (env > overlay > default)
scripts/run_<name>.py  CLI entrypoint per agent (--send delivers; default is print-only)
ops/*.plist         launchd schedules (macOS)
data/               local runtime state (gitignored): prefs.json, SQLite, resume.md, drafts/, seen.json
```

An agent's final output goes in `state["message"]` (Discord-flavored markdown);
the registry + web runner rely on that convention.

## How to add a new agent (the established pattern)

1. `agents/<name>/` with `state.py`, `nodes/` (each wrapped in try/except so one
   failure never kills the run), and `graph.py` exposing `build_<name>_graph(*, send=True)`
   that ends with output in `state["message"]`.
2. Reuse `shell/` — do **not** edit the shell or `config.py` from an agent.
3. Register an `AgentSpec` in `agents/registry.py` so it appears on the dashboard
   (scheduled/empty-state agents). Input-driven agents (e.g. résumé tailor) instead
   get their own web page/router + a nav link, and are NOT in the registry.
4. Add `scripts/run_<name>.py` (sys.path insert + `--send` flag, default print-only).
5. Add an `ops/*.plist` if it should run on a schedule.
6. Test on the **local** model first; only require `smart` where quality demands it,
   and provide a local fallback.

## The agents (current state)

- **Morning Briefing** (`morning_briefing`) — weather (Open-Meteo), commute (Google
  Maps Routes), calendar (Google Calendar OAuth), news (Google News RSS). Scheduled 7am.
- **Job Scraper** (`job_scraper`) — co-op/intern/new-grad roles via Greenhouse/Lever/
  Ashby public JSON APIs; keyword + freshness filtering; dedupe store; Discord ping.
- **Stock Digest** (`stock_digest`) — watchlist quotes + local technical indicators +
  news sentiment, synthesized as an info digest (NOT advice).
- **Application Tracker** (`application_tracker`) — pipeline counts, stale follow-up
  reminders, upcoming interviews; logged via a web form.
- **Résumé Tailor** (`resume_tailor`) — tailors `data/resume.md` + drafts a cover
  letter for a pasted job description; smart-or-local fallback; its own `/resume` page.

## Gotchas (things that will bite you)

- **⚠️ Two repos existed.** The canonical repo is **`kayla177/Agent`** (this one).
  An early parallel local copy lived at `~/dev/daily-agents` with an unrelated git
  history and a broken remote — ignore it; do all real work here.
- **Ollama runs as a persistent service** (`brew services start ollama`) so scheduled
  agents always find it. If local LLM calls fail, check that it's up (`:11434`).
- **DeepSeek-R1 burns its token budget "thinking"** — bad for quick formatting. That's
  why `llama3.1:8b` is the default `local` model, and R1 is a separate `reasoner` role.
- **The web control center never delivers unless run with `send=True`.** "Preview"
  mode (send=False) is read-only and safe.
- Settings edits write `data/prefs.json`; `config.py` overlays it (env > overlay >
  default). A save applies on the next run — secrets stay in `.env`, never the UI.

## Running it

```bash
uv run python -m web                       # web control center (127.0.0.1:8000)
uv run python scripts/run_<agent>.py       # run one agent (print-only)
uv run python scripts/run_<agent>.py --send # + deliver to Discord
```
