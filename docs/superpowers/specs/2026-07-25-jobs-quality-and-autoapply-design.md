# Jobs Tab — Quality Fixes & Assisted Apply — Design Spec

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Scope:** Two phases. **Phase A** repairs the jobs vertical (dead fit scoring, unscorable
backlog, dishonest Apply button, no country filter) and wires the résumé ↔ tracker link that
already exists in the schema but is never populated. **Phase B** adds a fill-don't-submit
browser autofill agent for Greenhouse / Lever / Ashby.

**Supersedes nothing.** Builds on `2026-07-20-job-tab-ui-design.md` (the board layout stays)
and the six merged tracker/Next.js plans.

---

## Context — audit findings (2026-07-25)

Measured against the live `data/control_center.db` (523 job rows) and by running the real
pipeline against the local Ollama server.

### P0 — fit scoring is dead end to end

All **523** rows have `fit_score = NULL`. Causal chain:

1. `config.JOB_PROFILE` defaults to `""` (`config.py:105`).
2. With no profile the rank node instructs the model *"set every score to null"*
   (`agents/job_scraper/nodes/rank.py:49`).
3. `server/prefs.py` never exposes `JOB_PROFILE` or `JOB_MIN_FIT` in `current()` /
   `_validate()`, and `SettingsForm.tsx` has no field — **there is no product path to set a
   profile.**

Observable consequences: the fit badge always renders `—` (`JobRow.tsx:28`), "sort: fit" is a
no-op, and `bestMatch()` requires `fit_score !== null` (`jobs.ts:92`) so **`BestMatchHero` has
never rendered in production**. `fit_reason` confirms the cause — 378 rows read "No candidate
profile provided" / "no profile set".

Aggravating bug: `save_prefs` writes only the validated subset and `os.replace`s the whole
file (`prefs.py:91-96`), so a hand-added `JOB_PROFILE` in `prefs.json` is silently erased by
the next Settings save.

### P0 — the backlog can never be scored

`dedupe_node` drops every id already in the seen-store (`dedupe.py:49`) **before** `rank`
runs. The pipeline only ever scores brand-new postings, so fixing the profile fixes tomorrow's
jobs and leaves all 523 existing rows at `NULL` forever. There is no re-rank path.

### P1 — `last_seen` is a lie; `ghost` is never re-evaluated

`notify` upserts only `new` (`notify.py:88`), and `dedupe` already removed everything seen. So
existing rows' `last_seen` freezes — confirmed: 393 rows stuck at `2026-07-24`. Two effects:
a delisted posting is undetectable, and `ghost` is computed once at first sight, so a row that
ages past `JOB_MAX_AGE_DAYS` stays `ghost=0` permanently. Because the UI recomputes `ageDays`
client-side, a single row can show "🕒 90d ago" *and* no stale badge.

### P1 — the Apply button does not apply

`JobsBoard.mutate` → `POST /data/jobs/apply` creates an `applications` row and marks the job
`applied`. It **never opens the posting**. Clicking Apply asserts you applied to a job you have
not touched — the worst possible failure for a tracker whose value is pipeline truth. There is
no confirm and no undo; once dismissed, `actionable` is false (`JobRow.tsx:22`) so there is no
un-dismiss control at all.

### P1 — the résumé ↔ tracker link is built but orphaned

`applications.resume_job_id`, `set_resume_link()`, `PATCH /data/applications/{id}/resume`, and
the `ApplicationRow` picker all exist. But `/data/jobs/apply` calls `add_application()` with no
résumé (`jobs.py:33`), and `add_application` has no such parameter (`store.py:40`). All three
applications have it empty. **This is the seam assisted-apply needs.**

### P2 — smaller findings

- `viewed` is declared in both status enums with a pill color and written by nothing — dead state.
- `registry.py:128` lists 5 nodes for `resume_generator`; the graph has 6 (`latexify` missing).
- 14 tests exist, all green, all pure functions. Zero coverage of the routers, the rank node's
  profile branch, or any component — exactly the shape that let the all-NULL bug ship.
- Test docstrings say `uv run python tests/...`; `uv` is not installed, so the documented
  command fails.

### Measured facts that drove the design

| Measurement | Value |
|---|---|
| Ollama | running, `llama3.1:8b` + `deepseek-r1:8b` |
| `ANTHROPIC_API_KEY` | **empty** — no hosted model available |
| LLM rank throughput | **6.5 s/job** (32.7s for a batch of 5) → 523 rows ≈ 57 min |
| LLM null-score rate **with** a profile set | **3 of 5** returned `fit_score = null`, all sharing one reason string |
| Tectonic compile | **2.0 s**, valid 37 KB PDF |
| Jobs by status | 130 new · 392 dismissed · 1 applied |
| Jobs by ATS (new) | greenhouse 47 · ashby 29 · lever 24 · workday 23 · smartrecruiters 7 |
| Clearly non-US/CA in `new` | **22 of 130** (~17%) |
| Ambiguous locations | ~28 rows (`2 Locations`, `In-Office`, `Stamford Hub`, `Flexible - Any SpaceX Site`) |
| Scraper schedule | launchd, unattended, 08:00 + 17:00 daily |
| `playwright` / `pytest` installed | **no** |

The null-score measurement is the single most important one: a ranking feature that is ~60%
blank is worse than none, because `NULL` sinks to the bottom of sort-by-fit and makes a good
unscored job invisible.

---

## Goals / non-goals

- **Goal:** every job always has a fit score and an explainable reason.
- **Goal:** Apply tells the truth, records which résumé was used, and is undoable.
- **Goal:** only US/Canada roles are shown, reversibly.
- **Goal:** typed applicant profile that serves both fit scoring and form autofill.
- **Goal (Phase B):** fill Greenhouse/Lever/Ashby forms; the human submits.
- **Non-goal:** auto-submitting an application. Ever.
- **Non-goal:** Workday / SmartRecruiters autofill (multi-step wizards + account creation).
- **Non-goal:** changing the board layout, the SSE/run machinery, or the stocks/briefing agents.
- **Non-goal:** a migration framework. `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` guards, as today.

---

## Phase A design

### A1. Fit scoring — deterministic baseline + LLM refinement

New `agents/job_scraper/scoring.py`:

```
score_baseline(profile_keywords, posting) -> (score: int, reason: str)
```

Pure, instant, no I/O. Keyword overlap between the profile's extracted skill terms and the
posting's title + description, word-boundary anchored, normalized to 0–100 with a small
recency/target-role bonus. Reason is explainable: `"matched: Python, React, SQL"`.

`rank_node` becomes: baseline for **every** posting first, then LLM refinement overrides the
score **only when the model returns a usable integer**. The LLM keeps sole authority over the
`eligible` drop decision (unchanged). Net effect: every row ends up with a populated
`fit_score`, so the badge, sort-by-fit and `BestMatchHero` cannot go dead again. The column
stays nullable in SQL — `NULL` becomes "not yet processed by the backfill", which is exactly
what the backfill node selects on.

`fit_reason` gains a source marker so the UI can distinguish `"matched: Python, SQL"` (baseline)
from a model-written reason.

Profile source: `config.JOB_PROFILE or applicant_profile.summary`.

### A2. `applicant_profile` table + Profile UI

Single-row table (`id` always 1, enforced by the store — the `master_resume` pattern). New
`profile_store.py` at repo root beside `store_db.py`; sole writer is `server/routers/profile.py`.

```sql
CREATE TABLE IF NOT EXISTS applicant_profile (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name         TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    location          TEXT NOT NULL DEFAULT '',
    linkedin_url      TEXT NOT NULL DEFAULT '',
    github_url        TEXT NOT NULL DEFAULT '',
    portfolio_url     TEXT NOT NULL DEFAULT '',
    school            TEXT NOT NULL DEFAULT '',
    degree            TEXT NOT NULL DEFAULT '',
    grad_date         TEXT NOT NULL DEFAULT '',   -- ISO YYYY-MM
    us_work_auth      TEXT NOT NULL DEFAULT '',   -- citizen|permanent_resident|f1_opt|tn_eligible|needs_sponsorship
    ca_work_auth      TEXT NOT NULL DEFAULT '',
    needs_sponsorship INTEGER NOT NULL DEFAULT 0,
    summary           TEXT NOT NULL DEFAULT '',   -- free text; feeds fit scoring
    updated_at        TEXT NOT NULL DEFAULT ''
);
```

Typed fields exist specifically so **no LLM ever invents a phone number or a work-authorization
answer** into a form. Mirrored in `schema.prisma`, covered by `npm run db:check`, added to the
ARCHITECTURE ownership table. UI: a Profile section on `/settings`.

### A3. `prefs.py` fixes

Add `JOB_PROFILE` (str), `JOB_MIN_FIT` (int), `JOB_MAX_AGE_DAYS` (int), `JOB_DROP_GHOSTS` (bool),
and `JOB_COUNTRIES` (list of `"US"` / `"CA"` / `"OTHER"` codes, default `["US","CA"]`) to the
editable set and the Settings form. Change `save_prefs` to **merge over the existing overlay**
rather than replace it, so no key is ever silently destroyed.

### A4. Location classification

New `agents/job_scraper/locations.py`:

```
country_of(location: str) -> "US" | "CA" | "OTHER" | "UNKNOWN"
```

- **Word-boundary anchored, mandatory.** A naive substring check matched `Milwa(uk)ee` during the
  audit — the same trap `matching.py` already documents for role keywords.
- Allowlists: 50 US states + postal abbreviations + DC + major metros; provinces + abbreviations
  + metros; explicit non-NA country/city denylist.
- Multi-location strings split on `;` and `/`; a posting is **US/CA if any segment is**, so
  `Austin, Texas; Amsterdam` is kept — it is a real US job.
- `UNKNOWN` is **never dropped**.

New mirrored `country` column on `jobs` (+ Prisma mirror) so the board can filter the existing
523 rows, which a pipeline-only filter cannot reach.

**Store, don't drop.** Every posting is classified and persisted; `filter_node` no longer
excludes on location. Hiding happens in two places: the board filters to `JOB_COUNTRIES`
(default `["US","CA"]`) by default, and `notify` omits non-matching rows from the Discord
digest. A misclassification stays auditable in the DB and reversing is a UI toggle, not a
re-scrape. `UNKNOWN` rows get a small badge. The hardcoded `PREFER_LOCATIONS_ONLY` constant
(`filter.py:14`) is removed in favour of the pref.

### A5. `backfill` node — one node, three fixes

New node between `dedupe` and `freshness`. Selection is explicit:

- **Deterministic pass:** rows where `fit_score IS NULL` **or** `country IS NULL` — all 523 today.
- **LLM refinement pass:** of those, rows where `status != 'dismissed'` **and** the reason marker
  says the score is still baseline-only, capped per run.

Selected rows are merged into `new` tagged `_rescored: True`.

`freshness` and `rank` need **no changes** — they just see a longer list. `notify` persists
everything but announces only untagged rows, so the Discord digest stays "new roles only".

Because those rows traverse the whole tail of the pipeline, one node simultaneously scores the
backlog, **recomputes `ghost`** for rows that have since aged out, and **refreshes `last_seen`**.
Every future run is self-healing.

Cost control:
- Deterministic work (baseline score, `country`) covers **all 523** rows — instant, no LLM.
- LLM refinement is limited to **non-dismissed** rows (~131). No inference is ever spent on a
  job already rejected — that alone saves ~42 minutes.
- Per-run cap (default 60 LLM rows) and the node **`log`s what it skipped**, so a bounded pass
  never reads as "covered everything".
- LLM refinement runs only when the run is unattended (launchd) or explicitly requested via a
  `backfill` graph input. The interactive "▶ run scraper" button stays fast; a separate
  **"score backlog"** button in the jobs tab triggers a refinement run with SSE progress.

### A6. Honest Apply flow

Replaces the current one-click mutation.

1. Click **Apply** → modal opens (job summary + résumé picker). No writes yet.
2. Picker offers: this job's tailored résumé (if any), the master résumé, or reuse another
   job's résumé. When none is tailored, a **"Generate tailored résumé"** button starts the
   `resume_generator` run with SSE progress inside the modal — you may wait or proceed with
   master immediately.
3. Confirm → the chosen résumé is compiled to PDF and **downloaded to your machine** (so you
   have the exact file to upload on the ATS site — Phase A has no browser agent), the posting
   opens in a new tab, the tracker row is created with the résumé linked and the PDF cache key
   pinned, and the job flips to `applied`.
4. A prominent **Undo** deletes the application row and reverts the job status.

In Phase B this same modal gains an "autofill for me" path; the manual download remains the
fallback for Workday / SmartRecruiters and for any selector failure.

API changes:
- `add_application()` gains `resume_job_id` and `resume_pdf_key` parameters.
- `POST /data/jobs/apply` accepts `{id, resume_job_id}` and returns the created application id.
- New `POST /data/jobs/undo-apply` — deletes the application and reverts job status.
- New `POST /data/jobs/status` for un-dismiss, and a **Restore** button on dismissed rows.

### A7. Résumé PDF cache with pinned provenance

PDFs are cached at `data/resumes/<job_id>__<updated_at>.pdf` (gitignored), keyed by content
version. Because compiling is only 2.0s, this is about provenance, not speed — so the
application row **pins the key it used** via a new `applications.resume_pdf_key` column.
Without pinning, editing a résumé later silently destroys the record of what a company
actually received.

### A8. `viewed` status

Expanding a row's details sets `viewed` via `POST /data/jobs/status`. Rendered with a subtle
marker, **not** dimmed — dimming reads as rejected. Lets you stop re-reading the same postings
across sessions in a 130-row list.

### A9. Registry drift

Add the missing `latexify` to `resume_generator.node_order` (`registry.py:128`).

---

## Phase B design — assisted autofill

New agent `job_applier` (registry key), `agents/job_applier/`. Graph:

```
load_profile -> resolve_resume -> open_form -> map_fields -> draft_answers -> fill -> handoff
```

- **Fill, never submit.** `handoff` stops with the form filled and the browser open. The human
  reviews and clicks Submit. No node ever clicks a submit button.
- **Scope:** Greenhouse, Lever, Ashby — 100 of 130 new jobs, and all three serve *public*
  application forms with no login, which is exactly why they are tractable. Workday and
  SmartRecruiters fall back to the Phase A "open posting" flow.
- **Browser:** visible Chromium via Playwright, persistent context at gitignored
  `data/browser_profile/`. Headed so you watch and can intervene; isolated from your everyday
  browser so a bug cannot touch other tabs.
- **Deterministic fields** map from `applicant_profile` only — name, email, phone, location,
  links, school, degree, grad date, work authorization — plus the résumé PDF upload.
- **Free-text answers are LLM-drafted** ("Why this company?", relocation, etc.), grounded in the
  `resume_generator`'s existing `company_research` output rather than model guesswork. Every
  drafted field is flagged **"AI-drafted — review"** in the handoff checklist so nothing
  AI-written is submitted unnoticed. Caveat: with `ANTHROPIC_API_KEY` empty the drafting model
  is `llama3.1:8b`, so expect to rewrite most of them.
- **Confirmation detection.** After you submit, all three ATSs land on a confirmation page. The
  agent detects it and marks the application confirmed — upgrading A6's optimistic write to a
  verified one.
- **Failure is loud.** A changed selector or unmappable form yields "couldn't map these fields,
  here's the form" — never a wrong value and never a submit.

New deps: `playwright` + `playwright install chromium` (~150MB), installed with
`.venv/bin/pip` since `uv` is absent.

---

## Error handling

| Failure | Behavior |
|---|---|
| Ollama down | baseline score stands; `fit_reason` records "unrefined"; run succeeds |
| LLM returns junk / null score | baseline retained; never overwritten with `NULL` |
| Tectonic compile error | 422 + engine log (existing); apply modal offers the raw `.tex` |
| `country_of` uncertain | `UNKNOWN` → kept and badged, never hidden |
| Backfill cap reached | `log`s the skipped count; converges over later runs |
| Undo after job row vanished | 404, surfaced in the UI; no partial state |
| Playwright not installed | `job_applier` reports the one-line install command and exits cleanly |
| ATS selector miss | partial fill + explicit unmapped-field list; no submit |
| Confirmation page not found | application stays optimistic (A6 behavior); no false "confirmed" |

## Testing

Adopt **pytest** as a dev dependency; port the 14 existing checks; replace the
module-global-mutating `_use_temp_store` with a fixture. Fix the `uv run` docstrings.

New coverage:
- `locations.country_of` — the `Milwa(uk)ee` trap, multi-location `;`/`/` splitting, provinces
  and abbreviations, ambiguous → `UNKNOWN`.
- `scoring.score_baseline` — deterministic, monotonic in overlap, explainable reason.
- `rank_node` — baseline survives a null LLM score; LLM override applies when valid; `eligible`
  drop still works.
- `backfill_node` — selects the right rows, respects the cap, tags `_rescored`, excluded from the
  digest, refreshes `last_seen` and recomputes `ghost`.
- Routers via FastAPI `TestClient` on a temp DB — `apply` with/without a résumé, `undo-apply`,
  `status`, `dismiss`/restore, profile GET/PUT, `prefs` merge-not-replace.
- `job_applier` field mapping against **saved local HTML fixtures** of real Greenhouse/Lever/
  Ashby forms — never live sites.

`npm run db:check` must pass after every schema change (two in Phase A).

## Rollout

Phase A is independently valuable and ships first, and **the implementation plan that follows
this spec covers Phase A only**. Phase B gets its own plan once Phase A is merged and in use —
it depends on the `applicant_profile` table, the résumé PDF cache, and the apply modal all
existing, and it requires the Playwright install step.

Branch: `feature/jobs-quality-phase1` (current).
