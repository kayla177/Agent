# Cover letters, Phase 1 — draft, template, résumé tab

**Written 2026-08-10.** Kayla asked for cover-letter drafting plus a function on the résumé tab. The
full request — drafting, a reusable template, PDF export, and letting the applier paste a letter into a
form — is five largely independent subsystems, so it was decomposed. **This spec covers Phase 1 only.**

| phase | scope | status |
|---|---|---|
| **1** | tables, agent, master template, résumé-tab UI, plain text | **this spec** |
| 2 | PDF export via the existing Tectonic path | later, own spec |
| 3 | applier pastes the letter into cover-letter fields | later, own spec |

Phase 1 is independently useful: a letter you can draft and paste today.

---

## Decisions Kayla made

- **An in-app agent plus résumé-tab UI**, not a Claude Code skill.
- **The master cover letter is a SAMPLE LETTER the model imitates**, not a placeholder template. She
  writes one real letter she is happy with; each draft keeps her voice, structure and paragraph rhythm
  and rewrites the content for the specific job. Same relationship `latexify` has to her master
  résumé. A placeholder template was rejected because every letter would then share the same
  sentences, which reads as boilerplate.
- **PDF is Phase 2, applier integration is Phase 3.** Both were asked for and both are deliberately
  deferred, not dropped.

---

## 1. Data layer

`resumes.job_id` is a **PRIMARY KEY** (`schema.sql:110`), so that table holds exactly one document per
job. A cover letter cannot share it without changing to a composite key, which is a breaking migration
on a live table. So Phase 1 adds three tables mirroring the résumé's three:

```sql
CREATE TABLE IF NOT EXISTS master_cover_letter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT NOT NULL DEFAULT '',   -- the sample letter, in her voice
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cover_letters (
    job_id      TEXT NOT NULL PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | final
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cover_letter_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cover_letter_versions_job ON cover_letter_versions(job_id);
```

**`body` is plain text, not markdown.** A cover letter is prose that gets pasted into a textarea, and
markdown syntax would land there literally — `**Dear**` is not a bold word on a Greenhouse form.

**No `_migrate` guards are needed.** Corrected after checking `store_db.py:49`: `_migrate` exists only
to ADD COLUMNS to tables that already exist, and its own docstring says `CREATE TABLE IF NOT EXISTS`
in `schema.sql` "covers fresh databases **and new tables**". All three tables here are new, so
`schema.sql` alone is sufficient and adding guards would be dead code. (`CREATE INDEX IF NOT EXISTS`
guards the index *name* rather than its column, but that only bites when indexing a newly-*added*
column on a pre-existing table, which is not this case.)

**Deployment ordering still applies**, from `ARCHITECTURE.md`: the tables reach a live database only
when a Python process starts and calls `init_db()` — never from `npx prisma generate`, which just
rewrites a TypeScript client. So run one Python entry point *before* regenerating Prisma and rebuilding
Next, or the résumé page 500s with `no such table` until some Python process happens to run.

`web-next/prisma/schema.prisma` mirrors all three, verified by `npm run db:check`.

## 2. Store

`agents/cover_letter_generator/store.py`, mirroring `resume_generator/store.py`'s shape:

```
get_master_cover_letter() -> dict
upsert_master_cover_letter(body) -> dict
upsert_cover_letter(job_id, *, company, role, body, status) -> dict   # snapshots first
get_cover_letter(job_id) -> dict | None
set_cover_letter_status(job_id, status) -> dict | None
list_cover_letter_versions(job_id) -> list[dict]
```

`upsert_cover_letter` appends the current body to `cover_letter_versions` **before** overwriting, so a
regenerate never destroys a draft — the same guarantee `upsert_resume` gives.

## 3. The agent

`agents/cover_letter_generator/`, registered in `agents/registry.py` as:

```python
key="cover_letter_generator", display_name="Cover Letter",
description="Draft a cover letter for a scraped job, in your own voice (per-job, not scheduled).",
emoji="✉️", node_order=("gather", "draft", "save"),
planet="jupiter", label="resume",
```

`planet="jupiter"` and `label="resume"` put it on the same tab as the résumé generator.

**Three nodes, deliberately not six.** The résumé's `keywords` node is omitted: ATS keyword-matching a
letter produces prose that reads as keyword-stuffed, which costs more than it gains. `research` is
omitted because the job description is already in the `jobs` row and a letter needs the posting's own
words more than a summary of them. `latexify` is Phase 2.

`build_cover_letter_graph(send=False)` accepts and ignores `send`, exactly as the résumé builder does —
there is no Discord delivery for a per-job document.

### `gather` — the fail-fast gate

Sets `error` (which every downstream node honours) when:

- **no job id** — this is per-job, like the résumé generator;
- **unknown job id** — nothing to write about;
- **no master cover letter** — there is no voice to imitate, and a letter invented from nothing is
  exactly what this feature must not produce. The message points at the résumé tab's new editor.

### `draft` — the only node that calls the model

Reads:

- the **job record** — title, company, description, location;
- the **applicant profile** (`profile_store`) — `full_name`, `degree`, `school`, `grad_date`.
  `linkedin_url` / `github_url` / `portfolio_url` are deliberately NOT passed to the model — a cover
  letter is prose, not a document with a links section, so there is nowhere truthful to put them.
  *(Corrected after implementation: this originally said the profile's "links" were read.)*
- the **master cover letter** — as the voice, structure and rhythm to imitate;
- the **tailored résumé for this job if one exists** (`resume_generator.store.get_resume(job_id)`) —
  so the letter cannot claim experience the résumé does not show. Absent is fine; it is context, not a
  prerequisite.

Truthfulness rules are the résumé draft's, restated for prose: never invent an employer, title, date,
school, degree, metric or technology; mirror the posting's wording only where it truthfully describes
real experience. A model failure or an empty reply sets `error` rather than saving an empty letter.

The drafted body carries **no AI-drafted marker**. That marker exists in the applier because text is
typed into a live form unread; here the letter is shown to Kayla in the UI before it goes anywhere,
and a marker she must delete every time is friction with no safety value. **Phase 3 must add the
marker when it starts pasting into forms** — recorded here so that decision is not lost.

### `save`

Writes via `upsert_cover_letter` (snapshotting first), then builds the run's `message`.

## 4. Résumé tab

Three components beside the existing four (`MasterResume`, `GenerateForm`, `ResumeCard`,
`PoolManager`), each mirroring its résumé counterpart:

- **`MasterCoverLetter`** — a textarea to write and save the sample letter, with `updated_at`. Mirrors
  `MasterResume`. When empty, it says so and explains that drafting is blocked until it is filled,
  matching `gather`'s refusal.
- **`GenerateCoverLetter`** — pick a scraped job, POST the run, stream node events via its own inline
  `EventSource` against `/runs/{run_id}/events`. Mirrors `GenerateForm`, which does the same rather than
  using `RunStream`. *(Corrected after implementation: this originally said `RunStream`; the code — and
  the component it mirrors — never used it.)*
- **`CoverLetterCard`** — the draft, with **copy to clipboard** as the primary action (the letter's
  destination is a textarea, so copying is the action, not downloading), plus version history and a
  draft/final status control. Mirrors `ResumeCard`.

Placed after the résumé sections on `/resume`, under their own heading.

**All writes go through FastAPI.** Next.js only reads (`ARCHITECTURE.md`), so the new routes live in
`server/routers/resume.py`: `GET|PUT /data/cover-letter/master`,
`GET /data/cover-letters/{job_id}/versions`, `PATCH /data/cover-letters/{job_id}` for status. The run
itself starts via the existing `POST /agents/cover_letter_generator/run` with `{"job_id": …}`, the same
path `GenerateForm` uses.

The **singular** `/data/cover-letter/master` beside the **plural** `/data/cover-letters/{job_id}/…` is
deliberate, not a typo: it mirrors the existing `/data/resume/master` and `/data/resumes/{job_id}/versions`
exactly. Singular for the one document she owns, plural for the collection keyed by job. Do not
"correct" one to match the other — that would make the cover-letter API inconsistent with the résumé
API it sits next to.

Uvicorn does not reload-watch: **restart `python -m server` after adding the routes**, and rebuild
Next (`:3000` serves a production build, so a restart alone changes nothing).

## 5. Testing

- **Store**: round-trip the master letter; a regenerate snapshots the previous body into
  `cover_letter_versions` rather than losing it.
- **`gather`**: each of the three refusals sets `error` with a message naming the cause. The
  no-master-letter case is the one worth pinning hardest — it is the difference between imitating a
  voice and inventing one.
- **`draft` refusals must prove the model was never called**, not merely that the output was empty.
  `tests/conftest.py`'s suite-wide `ModelCalledInTest` guard makes that testable, and Phase B's lesson
  applies directly: a refusal test that only checks the answer is blank passes while a mutation routes
  the question straight to the model.
- **`db:check`** for Prisma drift, and `tsc`/`eslint` for the components.
- No test may launch a browser, hit the network, or reach the model.

## 6. Out of scope for Phase 1

- **PDF export** (Phase 2). Needs a letter `.tex` template; the master résumé's template is a résumé
  layout and is the wrong shape for a letter. Either Kayla supplies one or Phase 2 generates a plain
  one she edits.
- **Applier integration** (Phase 3). The applier currently refuses cover-letter fields on purpose:
  `file_upload` is blocking, and a textarea would be drafted generically. Wiring the stored letter in
  touches the surface guarded by ten source-scan assertions, and it should happen after Kayla has read
  a few real drafts.
- **Discord delivery.** Per-job and on demand, like the résumé generator.
- **Editing the drafted letter in the UI.** Copy it, or regenerate. Version history means a regenerate
  is safe.
