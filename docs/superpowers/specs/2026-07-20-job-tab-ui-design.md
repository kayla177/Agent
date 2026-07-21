# Job Tab UI — Design Spec

**Date:** 2026-07-20
**Status:** Approved (design), pending implementation plan
**Scope:** Redesign the `/jobs` page in the daily-agents web control center into a proper "balanced hub" UI. Backend scraper pipeline (fetch → filter → dedupe → freshness → rank → notify) and the record store are already built; this spec covers only the web presentation layer.

## Context

The job scraper agent persists enriched, fit-ranked postings to `agents/job_scraper/data/jobs.json`. The current `/jobs` page (`web/routers/jobs.py` + `web/templates/jobs.html`) is a functional filter-bar + table with full-page-reload apply/dismiss. We want a designed, app-like job tab that lets Kayla see the day's batch at a glance and triage or read into roles without page reloads — consistent with the existing "space / planets" theme where the jobs tab is **Mars** (accent `#e07a4a`).

## Goals / non-goals

- **Goal:** a balanced hub — a small overview strip on top, then a browsable + triageable list.
- **Goal:** fast, reload-free interactions (apply / dismiss / expand) via HTMX (already loaded in `base.html`).
- **Non-goal:** changing the scraper pipeline, the record store schema, or the application-tracker store. All data shown already exists on the enriched record.
- **Non-goal:** a kanban / drag board (overlaps with the Applications tab) or a full JD reader page.

## Chosen layout: dense fit-sorted list + inline expand

Rejected alternatives: **card grid** (prettiest but shows fewer roles) and **kanban by status** (heavier, overlaps Applications). The dense list shows the most roles at once and supports both quick triage and in-page reading.

### Overview strip (top)

- **Count chips:** `N new · N applied · N dismissed` (from record `status`).
- **Best-match callout:** a Mars-tinted hero line highlighting the single highest-`fit_score` role among `status == "new"` — `★ 92% — Software Engineer Intern @ Notion · SF → apply`. Clicking it applies that role. Hidden when there are no scored `new` roles.
- (Explicitly excluded per brainstorming: fit-distribution chart, refresh/run button.)

### Filter / sort bar

Keep the existing controls, restyled: sort by **fit** (default) or **date**; filter by **status** and **company**; **hide stale/ghost** toggle. Changing any control re-renders only the list via HTMX (no full-page reload).

### List rows (sorted by fit desc, unscored last)

Each row shows:
- Fit % color-coded — green (hi) / amber (mid) / red (lo); `—` when unscored.
- Title + company + location.
- Freshness badge (`🕒 4d ago`) and compensation badge when present.
- **Apply** (primary/Mars) and **Dismiss** buttons; a caret indicating expand state.

Row states:
- **Applied:** row dims and shows an `applied` pill; Apply/Dismiss hidden.
- **Dismissed:** removed from the default view; visible under the "dismissed" status filter.
- **Ghost/stale:** dimmed with a red freshness badge (`⚠ stale 120d`); hidden when "hide stale/ghost" is on.

### Expanded row (click to toggle, HTMX-loaded, no reload)

Shows for the selected role:
- **Fit reason** (prominent, Mars accent): the `fit_reason` string.
- **Description snippet** from `description`, with an "open full posting ↗" link to `url`.
- **Facts line:** posted date (`posted_at`), department, location + remote flag, compensation, and `also on: <sources>` when `also_on` is non-empty.

## Architecture

Presentation-only changes, following existing patterns (`run_detail.html` already does partial/streamed updates).

### Backend — `web/routers/jobs.py`

Add HTMX partial endpoints that return HTML fragments (not redirects), alongside the existing full-page `GET /jobs`:
- `GET /jobs/list` — the filtered/sorted list fragment (used by the filter bar).
- `GET /jobs/{pid}/detail` — the expanded detail panel for one role.
- `POST /jobs/apply` and `POST /jobs/dismiss` — return the updated single-row fragment (with `HX-Trigger` to refresh the count chips), instead of redirecting.

Posting ids contain `:` and `/` (Workday), so mutating/detail routes take the id as a form field or query param, not a path segment (matches the current apply/dismiss design). Reuse `jobstore.load_records` / `jobstore.set_status` and `tracker.add_application`. No store changes.

Best-match = `max(records where status == "new", key=fit_score)`, ignoring `None` scores.

### Templates

Split the current monolithic `jobs.html` into a shell + reusable partials in `web/templates/partials/`, so full-page and HTMX responses render identical markup:
- `web/templates/jobs.html` — page shell: overview strip + filter bar + `{% include %}` of the list.
- `web/templates/partials/_jobs_list.html` — the list (loops rows).
- `web/templates/partials/_job_row.html` — one row (re-rendered on apply/dismiss).
- `web/templates/partials/_job_detail.html` — the expanded detail panel.

### Data

All fields already exist on the enriched record: `fit_score`, `fit_reason`, `description`, `compensation`, `department`, `posted_at`, `age_days`, `remote`, `also_on`, `ghost`, `ghost_reason`, `status`, `url`, `company`, `title`, `location`.

### CSS — `web/static/app.css`

Extend the existing `.filters` / `.fit` / `table.apps` classes; add row/expand/strip/hero styles under a "Jobs view" section using theme variables (`--accent` = Mars orange under `body[data-planet="mars"]`). No new front-end libraries.

## Error / edge handling

- Empty store → friendly empty state ("Run the Job Scraper from the dashboard").
- No scored `new` roles → best-match callout hidden.
- Apply/dismiss on a missing id → fragment with an inline error (mirror current `_redirect(error)` behavior, adapted to fragments).
- Unscored roles (`fit_score is None`) → sort last, show `—`.

## Testing

Extend `tests/test_job_scraper.py` (TestClient + temp store, the established pattern):
- List fragment renders expected rows and fit values; sort/filter query params change the set.
- Detail fragment for a pid renders fit reason, description, and facts (comp/dept/also_on).
- `apply` returns a fragment containing the `applied` pill and writes an application to the tracker store; `dismiss` flips status and drops the row from the default view.
- Best-match selection picks the highest-fit `new` role and is absent when none are scored.

## Verification

- `uv run python tests/test_job_scraper.py` → all pass.
- `uv run uvicorn web.app:app --port 8000` → `/jobs`: apply/dismiss/expand update inline with no reload; count chips update; best-match callout applies the top role; filters re-render the list.
