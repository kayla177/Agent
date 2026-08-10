# Cover Letters Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draft a cover letter for a scraped job, in Kayla's own voice, from a master letter she owns — stored, versioned, and usable from the résumé tab.

**Architecture:** Mirrors `resume_generator` deliberately at every level: three tables shaped like the résumé's three, a store module with the same function names, a three-node LangGraph pipeline registered on the same tab, and three UI components that are counterparts of `MasterResume` / `GenerateForm` / `ResumeCard`. Nothing invents a new pattern where one already exists, because a reader who knows the résumé side should be able to read this without learning anything new.

**Tech Stack:** Python 3.12 (`.venv/bin/python`), LangGraph, SQLite via `store_db`, FastAPI (`server/`), Next.js 16 / React 19 (`web-next/`), pytest, Prisma (read-side mirror only).

**Spec:** `docs/superpowers/specs/2026-08-10-cover-letter-phase1-design.md`

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Phase 1 only.** PDF export is Phase 2, applier integration is Phase 3. Both were asked for and are deferred, not dropped. Do not add a `latexify` node, a `.tex` template, or any applier change.
- **`body` is plain text, not markdown.** A cover letter is prose pasted into a textarea, where `**Dear**` is not a bold word. Do not add a markdown renderer.
- **The master cover letter is a SAMPLE LETTER the model imitates**, not a placeholder template. Do not implement `{{placeholder}}` substitution.
- **The drafted letter carries NO AI-drafted marker in Phase 1.** Kayla reads it in the UI before it goes anywhere. Phase 3 must add one when it starts pasting into live forms — that is recorded in the spec and is not this plan's job.
- **No `_migrate` guards.** `store_db._migrate` adds COLUMNS to existing tables; `CREATE TABLE IF NOT EXISTS` in `schema.sql` covers new tables (`store_db.py:49`). All three tables are new.
- **Next.js only reads.** Every write goes through FastAPI (`ARCHITECTURE.md`). There are no Prisma writes.
- **Route naming mirrors the résumé's exactly**: singular `/data/cover-letter/master` for the one document she owns, plural `/data/cover-letters/{job_id}/…` for the per-job collection. This asymmetry matches `/data/resume/master` vs `/data/resumes/{job_id}/versions` and is deliberate — do not "correct" either one.
- **Truthfulness rules for `draft`:** never invent an employer, title, date, school, degree, metric or technology. Mirror the posting's wording only where it truthfully describes real experience.
- Python is `/Users/kayla.li/.superset/Agent/.venv/bin/python`. Do **NOT** use `uv`.
- pytest prints **no summary line** (`addopts = "-q"`). Count with `--junitxml` and read the exit code. **Baseline: 1991 passed** on this branch. (1997 is the count on `fix/honest-run-bookkeeping` / PR #26, which adds 6 tests and is NOT merged — do not use it.)
- Node commands run from `/Users/kayla.li/.superset/Agent/web-next`.
- Uvicorn is not reload-watching: restart `.venv/bin/python -m server` after adding routes. `:3000` serves a production build, so it needs `npm run build`, not just a restart.
- No test may launch a browser, hit the network, or reach the model. `tests/conftest.py` installs a suite-wide `ModelCalledInTest` guard — use it.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `schema.sql` | Add the three tables + one index. Source of truth. | 1 |
| `web-next/prisma/schema.prisma` | Read-side mirror of those tables; verified by `db:check`. | 1 |
| `agents/cover_letter_generator/store.py` | **Create.** All DB access for letters. Snapshots before overwrite. | 2 |
| `agents/cover_letter_generator/state.py` | **Create.** The `CoverLetterState` TypedDict. | 3 |
| `agents/cover_letter_generator/nodes/gather.py` | **Create.** Fail-fast gate: job id, job exists, master letter exists. | 3 |
| `agents/cover_letter_generator/nodes/draft.py` | **Create.** The only node that calls the model. | 3 |
| `agents/cover_letter_generator/nodes/save.py` | **Create.** Persist + build the run message. | 3 |
| `agents/cover_letter_generator/graph.py` | **Create.** `gather → draft → save`. | 3 |
| `agents/registry.py` | Register `cover_letter_generator` on the résumé tab. | 3 |
| `server/routers/resume.py` | Add the master GET/PUT, versions GET, status PATCH. | 4 |
| `web-next/src/components/resume/MasterCoverLetter.tsx` | **Create.** Editor for the sample letter. | 5 |
| `web-next/src/components/resume/GenerateCoverLetter.tsx` | **Create.** Pick a job, run the agent, stream events. | 5 |
| `web-next/src/components/resume/CoverLetterCard.tsx` | **Create.** The draft, copy-to-clipboard, versions, status. | 5 |
| `web-next/src/app/(hub)/resume/page.tsx` | Read the new tables, render the three components. | 5 |
| `tests/test_cover_letter_store.py` | **Create.** Store round-trips and version snapshotting. | 2 |
| `tests/test_cover_letter_agent.py` | **Create.** Gate refusals, draft refusals, save. | 3 |

---

### Task 1: The three tables and their Prisma mirror

**Files:**
- Modify: `schema.sql` (append after the `resume_versions` block, currently ending ~line 144)
- Modify: `web-next/prisma/schema.prisma` (append after `model resume_versions`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `master_cover_letter(id, body, updated_at)`, `cover_letters(job_id, company, role, body, status, created_at, updated_at)`, `cover_letter_versions(id, job_id, body, status, created_at)`. Task 2's store reads and writes exactly these column names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
def test_the_cover_letter_tables_exist_with_the_columns_the_store_uses():
    """Three new tables mirroring the résumé's three. `resumes.job_id` is a
    PRIMARY KEY, so that table holds exactly one document per job and a cover
    letter cannot share it without a breaking composite-key migration on a live
    table — which is why these are separate tables rather than a `kind` column."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(pathlib.Path("schema.sql").read_text())

    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    assert cols("master_cover_letter") == {"id", "body", "updated_at"}
    assert cols("cover_letters") == {
        "job_id", "company", "role", "body", "status", "created_at", "updated_at",
    }
    assert cols("cover_letter_versions") == {"id", "job_id", "body", "status", "created_at"}

    # One letter per job, same as one résumé per job.
    pk = [r[1] for r in conn.execute("PRAGMA table_info(cover_letters)") if r[5]]
    assert pk == ["job_id"]
```

If `tests/test_schema.py` does not already `import pathlib`, add it.

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/test_schema.py -k cover_letter_tables -v
```

Expected: FAIL with `sqlite3.OperationalError: no such table: master_cover_letter`.

- [ ] **Step 3: Add the tables to `schema.sql`**

Append after the `resume_versions` index:

```sql

-- ---------------------------------------------------------------------------
-- Cover letters. Deliberately separate tables rather than a `kind` column on
-- `resumes`: that table's job_id is a PRIMARY KEY, so it holds exactly one
-- document per job, and widening it to a composite key is a breaking migration
-- on a live table.
-- ---------------------------------------------------------------------------

-- The one sample letter the user owns, in her own voice. Each draft imitates its
-- voice, structure and rhythm rather than filling placeholders in it.
CREATE TABLE IF NOT EXISTS master_cover_letter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

-- `body` is PLAIN TEXT, not markdown: a cover letter is prose pasted into a
-- textarea, where "**Dear**" would land literally.
CREATE TABLE IF NOT EXISTS cover_letters (
    job_id      TEXT NOT NULL PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | final
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

-- Snapshot of a letter's body, appended before it is overwritten, so a
-- regenerate never destroys a draft.
CREATE TABLE IF NOT EXISTS cover_letter_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cover_letter_versions_job ON cover_letter_versions(job_id);
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
.venv/bin/python -m pytest tests/test_schema.py -k cover_letter_tables -v
```

Expected: PASS.

- [ ] **Step 5: Mirror in Prisma**

Append to `web-next/prisma/schema.prisma`:

```prisma
model master_cover_letter {
  id         Int    @id @default(autoincrement())
  body       String @default("")
  updated_at String @default("")
}

model cover_letters {
  job_id     String @id
  company    String @default("")
  role       String @default("")
  body       String @default("")
  status     String @default("draft")
  created_at String @default("")
  updated_at String @default("")
}

model cover_letter_versions {
  id         Int    @id @default(autoincrement())
  job_id     String
  body       String @default("")
  status     String @default("")
  created_at String @default("")

  @@index([job_id], map: "idx_cover_letter_versions_job")
}
```

- [ ] **Step 6: Prove the mirror matches, then apply to the live DB in the right order**

```bash
cd /Users/kayla.li/.superset/Agent/web-next && npm run db:check
```
Expected: `✓ schema.prisma matches schema.sql — no drift.`

Then — **Python first, Prisma second.** The tables reach the live database only when a Python process calls `init_db()`, never from `prisma generate`:

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -c "import store_db; store_db.init_db(); print('tables created')"
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/control_center.db')
for t in ('master_cover_letter','cover_letters','cover_letter_versions'):
    print(' ', t, c.execute(f'select count(*) from {t}').fetchone()[0], 'rows')"
cd web-next && npx prisma generate >/dev/null && echo "prisma client regenerated"
```

Reversing that order gives a résumé page that 500s with `no such table` until some Python process happens to run.

- [ ] **Step 7: Full suite, then commit**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/ --junitxml=/tmp/t1.xml -q; echo "exit=$?"
```
Expected: exit 0, **1992** tests.

```bash
git add schema.sql web-next/prisma/schema.prisma tests/test_schema.py
git commit -m "feat(cover-letter): three tables, mirroring the résumé's three

Separate tables rather than a kind column on resumes: that table's job_id is a
PRIMARY KEY, so it holds exactly one document per job, and widening it to a
composite key is a breaking migration on a live table.

body is plain text, not markdown -- a cover letter is prose pasted into a
textarea, where **Dear** would land literally.

No _migrate guards: _migrate adds COLUMNS to existing tables, and CREATE TABLE IF
NOT EXISTS covers new ones (store_db.py:49)."
```

---

### Task 2: The store

**Files:**
- Create: `agents/cover_letter_generator/__init__.py` (empty)
- Create: `agents/cover_letter_generator/store.py`
- Create: `tests/test_cover_letter_store.py`

**Interfaces:**
- Consumes: the tables from Task 1.
- Produces, and Tasks 3–5 call exactly these:
  - `get_master_cover_letter() -> dict` — always a dict; `{"id": 1, "body": "", "updated_at": ""}` shape when absent
  - `upsert_master_cover_letter(body: str) -> dict`
  - `upsert_cover_letter(job_id: str, *, company: str, role: str, body: str, status: str = "draft") -> dict`
  - `get_cover_letter(job_id: str) -> dict | None`
  - `set_cover_letter_status(job_id: str, status: str) -> dict | None`
  - `list_cover_letter_versions(job_id: str) -> list[dict]`
  - `STATUSES = ("draft", "final")`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cover_letter_store.py`:

```python
"""The cover-letter store. Mirrors resume_generator/store.py, including the one
behaviour that matters most: a regenerate SNAPSHOTS the outgoing letter before
overwriting it, so a draft is never destroyed by pressing the button again."""
from __future__ import annotations

import pytest

from agents.cover_letter_generator import store as cl


def test_the_master_letter_round_trips(temp_db):
    assert cl.get_master_cover_letter()["body"] == "", "absent reads as empty, not None"
    cl.upsert_master_cover_letter("Dear Hiring Manager,\n\nI build things.\n")
    got = cl.get_master_cover_letter()
    assert got["body"] == "Dear Hiring Manager,\n\nI build things.\n"
    assert got["updated_at"], "a save must stamp updated_at"


def test_saving_the_master_letter_twice_replaces_rather_than_appends(temp_db):
    cl.upsert_master_cover_letter("first")
    cl.upsert_master_cover_letter("second")
    assert cl.get_master_cover_letter()["body"] == "second"
    import store_db
    with store_db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM master_cover_letter").fetchone()[0]
    assert n == 1, "the master letter is a singleton, like master_resume"


def test_a_letter_round_trips_for_one_job(temp_db):
    cl.upsert_cover_letter("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                           body="Dear Acme,")
    got = cl.get_cover_letter("Acme:greenhouse:1")
    assert got["company"] == "Acme" and got["role"] == "SWE Intern"
    assert got["body"] == "Dear Acme," and got["status"] == "draft"
    assert got["created_at"] and got["updated_at"]


def test_no_letter_for_a_job_reads_as_none(temp_db):
    assert cl.get_cover_letter("nobody:greenhouse:0") is None


def test_regenerating_snapshots_the_outgoing_letter_instead_of_losing_it(temp_db):
    """The whole reason `cover_letter_versions` exists. Pressing generate twice
    must not destroy the draft you had."""
    cl.upsert_cover_letter("j", company="Acme", role="R", body="first draft")
    cl.upsert_cover_letter("j", company="Acme", role="R", body="second draft")

    assert cl.get_cover_letter("j")["body"] == "second draft"
    versions = cl.list_cover_letter_versions("j")
    assert [v["body"] for v in versions] == ["first draft"]


def test_created_at_survives_a_regenerate(temp_db):
    cl.upsert_cover_letter("j", company="A", role="R", body="one")
    first = cl.get_cover_letter("j")["created_at"]
    cl.upsert_cover_letter("j", company="A", role="R", body="two")
    assert cl.get_cover_letter("j")["created_at"] == first


def test_status_can_be_set_and_is_validated(temp_db):
    cl.upsert_cover_letter("j", company="A", role="R", body="x")
    assert cl.set_cover_letter_status("j", "final")["status"] == "final"
    with pytest.raises(ValueError):
        cl.upsert_cover_letter("k", company="A", role="R", body="x", status="nonsense")


def test_setting_status_on_a_missing_letter_is_none_not_a_crash(temp_db):
    assert cl.set_cover_letter_status("missing", "final") is None


def test_versions_are_newest_first(temp_db):
    for body in ("v1", "v2", "v3"):
        cl.upsert_cover_letter("j", company="A", role="R", body=body)
    assert [v["body"] for v in cl.list_cover_letter_versions("j")] == ["v2", "v1"]
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_store.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'agents.cover_letter_generator'`.

- [ ] **Step 3: Create the package and the store**

`agents/cover_letter_generator/__init__.py` — empty file.

Create `agents/cover_letter_generator/store.py`:

```python
"""All database access for cover letters.

Mirrors `agents/resume_generator/store.py` deliberately: same function shapes,
same snapshot-before-overwrite guarantee, same "absent reads as an empty dict"
convention for the singleton. A reader who knows the résumé store should need to
learn nothing here.

`body` is PLAIN TEXT, not markdown — a cover letter is prose pasted into a
textarea, where "**Dear**" would land literally.
"""

from __future__ import annotations

import datetime as dt

import store_db

STATUSES = ("draft", "final")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _ensure() -> None:
    """Create the tables if absent (via the canonical schema). Safe repeatedly."""
    store_db.init_db()


# --------------------------------------------------------------------------
# The master letter — the one document the user owns
# --------------------------------------------------------------------------


def get_master_cover_letter() -> dict:
    """The master letter, or an empty-bodied dict when none is saved yet.

    Returns a dict rather than None so every caller can read `["body"]` without
    a guard — the same convention `get_master_resume` uses.
    """
    _ensure()
    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT id, body, updated_at FROM master_cover_letter ORDER BY id LIMIT 1"
        ).fetchone()
    return dict(row) if row else {"id": 1, "body": "", "updated_at": ""}


def upsert_master_cover_letter(body: str) -> dict:
    """Replace the master letter. A singleton: always exactly one row."""
    _ensure()
    now = _now()
    with store_db.connect() as conn:
        row = conn.execute("SELECT id FROM master_cover_letter ORDER BY id LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO master_cover_letter (body, updated_at) VALUES (?, ?)",
                (body, now),
            )
        else:
            conn.execute(
                "UPDATE master_cover_letter SET body = ?, updated_at = ? WHERE id = ?",
                (body, now, row["id"]),
            )
    return get_master_cover_letter()


# --------------------------------------------------------------------------
# Per-job letters
# --------------------------------------------------------------------------


def upsert_cover_letter(
    job_id: str,
    *,
    company: str,
    role: str,
    body: str,
    status: str = "draft",
) -> dict:
    """Insert or replace the letter for `job_id`; preserves created_at on update.

    Before overwriting an existing letter, the current row is snapshotted into
    `cover_letter_versions`, so pressing generate again never destroys the draft
    you had. Same guarantee `upsert_resume` gives.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    now = _now()
    with store_db.connect() as conn:
        prev = conn.execute(
            "SELECT body, status, created_at FROM cover_letters WHERE job_id = ?", (job_id,)
        ).fetchone()
        if prev is not None:
            conn.execute(
                "INSERT INTO cover_letter_versions (job_id, body, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (job_id, prev["body"], prev["status"], now),
            )
        created = prev["created_at"] if prev is not None else now
        conn.execute(
            "INSERT INTO cover_letters (job_id, company, role, body, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET company=excluded.company, "
            "role=excluded.role, body=excluded.body, status=excluded.status, "
            "updated_at=excluded.updated_at",
            (job_id, company, role, body, status, created, now),
        )
    return get_cover_letter(job_id) or {}


def get_cover_letter(job_id: str) -> dict | None:
    _ensure()
    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT job_id, company, role, body, status, created_at, updated_at "
            "FROM cover_letters WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def set_cover_letter_status(job_id: str, status: str) -> dict | None:
    """Set draft/final. Returns None when there is no letter for that job."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE cover_letters SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _now(), job_id),
        )
        if cur.rowcount == 0:
            return None
    return get_cover_letter(job_id)


def list_cover_letter_versions(job_id: str) -> list[dict]:
    """Past bodies for one job, newest first."""
    _ensure()
    with store_db.connect() as conn:
        rows = conn.execute(
            "SELECT id, job_id, body, status, created_at FROM cover_letter_versions "
            "WHERE job_id = ? ORDER BY id DESC",
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_store.py -q
```
Expected: 9 passed.

- [ ] **Step 5: Full suite, then commit**

```bash
.venv/bin/python -m pytest tests/ --junitxml=/tmp/t2.xml -q; echo "exit=$?"
```
Expected: exit 0, **2001** tests.

```bash
git add agents/cover_letter_generator/ tests/test_cover_letter_store.py
git commit -m "feat(cover-letter): the store, snapshotting before every overwrite

Mirrors resume_generator/store.py: same function shapes, same
snapshot-before-overwrite guarantee, same absent-reads-as-empty-dict convention
for the singleton.

The version snapshot is the load-bearing part: pressing generate again must not
destroy the draft you had, which is pinned by a test rather than left to the
INSERT's shape."
```

---

### Task 3: The agent

**Files:**
- Create: `agents/cover_letter_generator/state.py`
- Create: `agents/cover_letter_generator/nodes/__init__.py` (empty)
- Create: `agents/cover_letter_generator/nodes/gather.py`
- Create: `agents/cover_letter_generator/nodes/draft.py`
- Create: `agents/cover_letter_generator/nodes/save.py`
- Create: `agents/cover_letter_generator/graph.py`
- Modify: `agents/registry.py`
- Create: `tests/test_cover_letter_agent.py`

**Interfaces:**
- Consumes: Task 2's store functions, verbatim signatures above. Also `agents.job_scraper.store.load_records() -> dict[str, dict]`, `profile_store.get_profile() -> dict`, `agents.resume_generator.store.get_resume(job_id) -> dict | None`, and `shell.model_router.llm(tier, prompt, *, system, temperature, max_tokens) -> str`.
- Produces: `build_cover_letter_graph(*, send: bool = False)`; registry key `cover_letter_generator` with `node_order=("gather", "draft", "save")`; state keys `job_id`, `job`, `master`, `resume_body`, `profile`, `body`, `error`, `message`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cover_letter_agent.py`:

```python
"""The cover-letter agent. Three nodes, and the refusals matter more than the
happy path: a letter with no voice to imitate could only be invented, which is
exactly what this feature must not do."""
from __future__ import annotations

from agents.cover_letter_generator import store as cl
from agents.cover_letter_generator.graph import build_cover_letter_graph
from agents.cover_letter_generator.nodes.draft import draft_node
from agents.cover_letter_generator.nodes.gather import gather_node
from agents.job_scraper import store as jobstore

JOB = {
    "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
    "title": "Software Engineer Intern", "location": "Austin, TX",
    "description": "You will write Python and talk to customers.", "status": "new",
}


def _seed(temp_db, *, master="Dear team,\n\nI like building things.\n\nKayla"):
    jobstore.replace_record(JOB)
    if master:
        cl.upsert_master_cover_letter(master)


def test_no_job_id_is_refused_because_this_is_per_job(temp_db):
    out = gather_node({})
    assert out["error"] == "no_job"
    assert "job" in out["message"].lower()


def test_an_unknown_job_id_is_refused(temp_db):
    _seed(temp_db)
    out = gather_node({"job_id": "nobody:greenhouse:0"})
    assert out["error"] == "no_job"


def test_no_master_letter_is_refused_because_there_is_no_voice_to_imitate(temp_db):
    """The refusal that matters most. Without a sample letter the model has
    nothing to imitate, and a letter invented from nothing is the failure mode
    this whole feature is designed to avoid. The message must point her at the
    résumé tab rather than just saying no."""
    _seed(temp_db, master="")
    out = gather_node({"job_id": JOB["id"]})
    assert out["error"] == "no_master"
    assert "résumé tab" in out["message"] or "resume tab" in out["message"]


def test_a_whitespace_only_master_letter_counts_as_absent(temp_db):
    _seed(temp_db, master="   \n\t\n  ")
    assert gather_node({"job_id": JOB["id"]})["error"] == "no_master"


def test_gather_loads_the_job_the_master_and_the_profile(temp_db):
    _seed(temp_db)
    out = gather_node({"job_id": JOB["id"]})
    assert not out.get("error")
    assert out["job"]["title"] == "Software Engineer Intern"
    assert "building things" in out["master"]
    assert "profile" in out


def test_gather_passes_the_tailored_resume_along_when_one_exists(temp_db):
    """Context, not a prerequisite: the letter must not claim experience the
    résumé does not show, but a missing résumé is fine."""
    from agents.resume_generator import store as rs
    _seed(temp_db)
    rs.upsert_resume(JOB["id"], company="Acme", role="SWE Intern",
                     markdown="- Built a thing at Steelcon", keywords=[])
    out = gather_node({"job_id": JOB["id"]})
    assert "Steelcon" in out["resume_body"]


def test_gather_is_fine_with_no_resume(temp_db):
    _seed(temp_db)
    assert gather_node({"job_id": JOB["id"]})["resume_body"] == ""


def test_draft_refuses_without_ever_calling_the_model_when_gather_failed(temp_db, monkeypatch):
    """A refusal test must prove the model was NOT called, not merely that the
    output was empty. `tests/conftest.py` installs a suite-wide guard; this
    additionally records calls so the assertion is about the call list."""
    calls = []
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: calls.append(a) or "invented")
    out = draft_node({"error": "no_master"})
    assert out == {}
    assert calls == [], "a short-circuited node must not reach the model"


def test_draft_stores_what_the_model_returned(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "Dear Acme,\n\nI want in.\n")
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["body"] == "Dear Acme,\n\nI want in.\n"
    assert not out.get("error")


def test_an_empty_model_reply_is_an_error_not_an_empty_letter(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "   \n  ")
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["error"] == "draft_failed"


def test_a_model_that_raises_is_an_error_not_a_crash(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod

    def boom(*a, **k):
        raise RuntimeError("ollama is not running")
    monkeypatch.setattr(draft_mod, "llm", boom)
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["error"] == "draft_failed"
    assert "ollama is not running" in out["message"]


def test_the_prompt_carries_the_master_letter_and_the_posting(temp_db, monkeypatch):
    """The two inputs that make this a tailored letter in her voice rather than a
    generic one."""
    seen = {}
    import agents.cover_letter_generator.nodes.draft as draft_mod

    def capture(tier, prompt, **kw):
        seen["prompt"] = prompt
        seen["system"] = kw.get("system", "")
        return "letter"
    monkeypatch.setattr(draft_mod, "llm", capture)
    draft_node({"job": JOB, "master": "MY-VOICE-MARKER", "resume_body": "RESUME-MARKER",
                "profile": {"full_name": "Kayla Li"}})
    assert "MY-VOICE-MARKER" in seen["prompt"]
    assert "Software Engineer Intern" in seen["prompt"]
    assert "RESUME-MARKER" in seen["prompt"]
    assert "never invent" in seen["system"].lower()


def test_the_whole_graph_saves_a_letter(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "Dear Acme,\n\nHire me.\n")
    _seed(temp_db)
    final = build_cover_letter_graph(send=False).invoke({"job_id": JOB["id"]})
    assert not final.get("error")
    stored = cl.get_cover_letter(JOB["id"])
    assert stored["body"] == "Dear Acme,\n\nHire me.\n"
    assert stored["company"] == "Acme" and stored["role"] == "Software Engineer Intern"
    assert "Acme" in final["message"]


def test_a_refused_run_saves_nothing_and_passes_its_message_out(temp_db):
    _seed(temp_db, master="")
    final = build_cover_letter_graph(send=False).invoke({"job_id": JOB["id"]})
    assert final["error"] == "no_master"
    assert cl.get_cover_letter(JOB["id"]) is None, "a refusal must not write a row"
    assert final["message"]


def test_the_agent_is_registered_on_the_resume_tab():
    from agents.registry import get_spec
    spec = get_spec("cover_letter_generator")
    assert spec.node_order == ("gather", "draft", "save")
    assert spec.planet == "jupiter" and spec.label == "resume"


def test_the_builder_accepts_and_ignores_send():
    """Same contract every registry builder has; there is no Discord delivery for
    a per-job document."""
    assert build_cover_letter_graph(send=True) is not None
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_agent.py -q
```
Expected: collection error — `No module named 'agents.cover_letter_generator.graph'`.

- [ ] **Step 3: Create the state**

`agents/cover_letter_generator/state.py`:

```python
"""Shared state for the cover-letter graph."""

from __future__ import annotations

from typing import TypedDict


class CoverLetterState(TypedDict, total=False):
    #: Input: which scraped job to write about.
    job_id: str
    #: The `jobs` record for `job_id`.
    job: dict
    #: The master letter, as the voice to imitate.
    master: str
    #: The tailored résumé's markdown for this job, or "" — context, not required.
    resume_body: str
    #: The applicant profile.
    profile: dict
    #: The drafted letter, plain text.
    body: str
    #: Set by any node that refuses; every later node returns early on it.
    error: str
    #: Human-facing summary, surfaced by the registry and the CLI.
    message: str
```

- [ ] **Step 4: Create the gather node**

`agents/cover_letter_generator/nodes/gather.py`:

```python
"""Gather node — the fail-fast gate.

Three refusals, and the third is the one that matters: without a master letter
there is no voice to imitate, and a letter invented from nothing is exactly what
this feature must not produce. Every later node returns early on `error`.
"""

from __future__ import annotations

import profile_store
from agents.cover_letter_generator import store as cl_store
from agents.cover_letter_generator.state import CoverLetterState
from agents.job_scraper import store as jobstore
from agents.resume_generator import store as resume_store


def gather_node(state: CoverLetterState) -> CoverLetterState:
    job_id = (state.get("job_id") or "").strip()
    if not job_id:
        return {
            "error": "no_job",
            "message": (
                "Cover letters are written per job. Trigger this from a specific "
                "job on the résumé tab."
            ),
        }

    job = jobstore.load_records().get(job_id)
    if not job:
        return {
            "error": "no_job",
            "message": f"No scraped job found for id '{job_id}'.",
        }

    master = (cl_store.get_master_cover_letter().get("body") or "").strip()
    if not master:
        return {
            "error": "no_master",
            "message": (
                "There is no master cover letter to work from yet. Write one you are "
                "happy with on the résumé tab first — each draft imitates its voice "
                "and structure, so without it a letter could only be invented."
            ),
        }

    # The tailored résumé is CONTEXT, not a prerequisite: it stops the letter
    # claiming experience the résumé does not show. Absent is fine.
    resume = resume_store.get_resume(job_id) or {}

    return {
        "job": job,
        "master": master,
        "resume_body": str(resume.get("markdown") or ""),
        "profile": profile_store.get_profile(),
    }
```

- [ ] **Step 5: Create the draft node**

`agents/cover_letter_generator/nodes/draft.py`:

```python
"""Draft node — the only node that calls the model.

Writes ONE letter in the user's own voice, imitating her master letter's
structure and rhythm. Deliberately no AI-drafted marker: she reads the letter in
the résumé tab before it goes anywhere, and a marker she must delete every time
is friction with no safety value. Phase 3 MUST add one when the applier starts
pasting letters into live forms.
"""

from __future__ import annotations

from agents.cover_letter_generator.state import CoverLetterState
from shell.model_router import llm

_MAX_TOKENS = 1200

_SYSTEM = (
    "You write a cover letter for a specific job, in the applicant's own voice.\n\n"
    "ABSOLUTE RULES:\n"
    "- Imitate the VOICE, STRUCTURE and PARAGRAPH RHYTHM of the sample letter you "
    "are given. It is how this person writes; match it.\n"
    "- Use ONLY facts present in the sample letter, the résumé excerpt, or the "
    "profile. Never invent an employer, title, date, school, degree, metric or "
    "technology. If you do not know something, leave it out.\n"
    "- Mirror the posting's wording only where it truthfully describes real "
    "experience.\n"
    "- Output ONLY the letter body as plain text. No markdown, no headings, no "
    "bullet points, no commentary, no placeholders like [Company].\n"
    "- Keep it to three or four short paragraphs."
)


def _prompt(job: dict, master: str, resume_body: str, profile: dict) -> str:
    lines = [
        f"TARGET ROLE: {job.get('title', '?')} at {job.get('company', '?')}",
        f"LOCATION: {job.get('location', '?')}",
        "",
        "POSTING:",
        str(job.get("description") or "(no description captured)")[:2000],
        "",
        f"APPLICANT: {profile.get('full_name', '')} — {profile.get('degree', '')}, "
        f"{profile.get('school', '')} (graduating {profile.get('grad_date', '')})",
    ]
    if resume_body:
        lines += [
            "",
            "RÉSUMÉ FOR THIS ROLE (do not contradict it, do not exceed it):",
            resume_body[:2000],
        ]
    lines += [
        "",
        "SAMPLE LETTER — imitate this voice and structure:",
        master,
        "",
        "Write the letter now.",
    ]
    return "\n".join(lines)


def draft_node(state: CoverLetterState) -> CoverLetterState:
    if state.get("error"):
        return {}

    try:
        raw = llm(
            "local",
            _prompt(
                state.get("job") or {},
                state.get("master", ""),
                state.get("resume_body", ""),
                state.get("profile") or {},
            ),
            system=_SYSTEM,
            temperature=0.4,
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — model unavailable / transport error
        return {
            "error": "draft_failed",
            "message": f"The cover letter could not be drafted ({type(exc).__name__}: {exc}).",
        }

    body = (raw or "").strip()
    if not body:
        return {
            "error": "draft_failed",
            "message": "The model returned an empty letter; nothing was saved.",
        }
    return {"body": body}
```

- [ ] **Step 6: Create the save node**

`agents/cover_letter_generator/nodes/save.py`:

```python
"""Save node — persist the letter and build the run's summary.

If an earlier node set `error`, this saves NOTHING and passes that node's message
through untouched: a refusal must not leave a row behind.
"""

from __future__ import annotations

from agents.cover_letter_generator import store as cl_store
from agents.cover_letter_generator.state import CoverLetterState


def save_node(state: CoverLetterState) -> CoverLetterState:
    if state.get("error"):
        return {}  # message already set by the refusing node

    job = state.get("job") or {}
    cl_store.upsert_cover_letter(
        state.get("job_id", ""),
        company=job.get("company", ""),
        role=job.get("title", ""),
        body=state.get("body", ""),
        status="draft",
    )
    words = len(state.get("body", "").split())
    return {
        "message": (
            f"Drafted a cover letter for {job.get('title', '?')} at "
            f"{job.get('company', '?')} ({words} words, saved as draft). "
            f"Read it on the résumé tab before you use it."
        )
    }
```

- [ ] **Step 7: Create the graph**

`agents/cover_letter_generator/graph.py`:

```python
"""LangGraph definition for the cover-letter agent.

Linear chain: gather -> draft -> save.

THREE nodes, not the résumé's six, and each omission is deliberate:
  * no `keywords` — ATS keyword-matching a letter produces prose that reads as
    keyword-stuffed, which costs more than it gains;
  * no `research` — the posting's description is already in the `jobs` row, and a
    letter needs the posting's own words more than a summary of them;
  * no `latexify` — PDF export is Phase 2.

`send` is accepted and ignored, the contract every registry builder has; there is
no Discord delivery for a per-job document. Invoke with `{"job_id": "<id>"}`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.cover_letter_generator.nodes.draft import draft_node
from agents.cover_letter_generator.nodes.gather import gather_node
from agents.cover_letter_generator.nodes.save import save_node
from agents.cover_letter_generator.state import CoverLetterState


def build_cover_letter_graph(*, send: bool = False):
    """Compile and return the cover-letter graph (`send` is accepted, unused)."""
    g = StateGraph(CoverLetterState)

    g.add_node("gather", gather_node)
    g.add_node("draft", draft_node)
    g.add_node("save", save_node)

    g.add_edge(START, "gather")
    g.add_edge("gather", "draft")
    g.add_edge("draft", "save")
    g.add_edge("save", END)

    return g.compile()
```

- [ ] **Step 8: Register it**

In `agents/registry.py`, add a builder beside `_resume_builder`:

```python
def _cover_letter_builder(*, send: bool = False):
    from agents.cover_letter_generator.graph import build_cover_letter_graph

    return build_cover_letter_graph(send=send)
```

and a spec immediately after the `resume_generator` entry:

```python
    AgentSpec(
        key="cover_letter_generator",
        display_name="Cover Letter",
        description="Draft a cover letter for a scraped job, in your own voice (per-job, not scheduled).",
        emoji="✉️",
        _builder=_cover_letter_builder,
        node_order=("gather", "draft", "save"),
        planet="jupiter", label="resume",
    ),
```

Match the surrounding entries' exact keyword style — read the `resume_generator` entry and copy its shape. If `AgentSpec` has an `output_key`, set it the same way `resume_generator` does.

- [ ] **Step 9: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_agent.py -q
```
Expected: 16 passed. If `test_the_agent_is_registered_on_the_resume_tab` fails on an attribute name, read `AgentSpec` in `agents/registry.py` and fix the TEST to use the real field names — do not rename the dataclass's fields.

- [ ] **Step 10: Full suite, then commit**

```bash
.venv/bin/python -m pytest tests/ --junitxml=/tmp/t3.xml -q; echo "exit=$?"
```
Expected: exit 0, **2018** tests — 16 new, plus ONE more because `tests/test_applier_graph.py:1161` is `@pytest.mark.parametrize("key", sorted(REGISTRY))`, so registering an agent auto-adds a case.

```bash
git add agents/cover_letter_generator/ agents/registry.py tests/test_cover_letter_agent.py
git commit -m "feat(cover-letter): the agent -- gather, draft, save

Three nodes, not the résumé's six, and each omission is deliberate: no keywords
(keyword-matching a letter reads as stuffed prose), no research (the description
is already in the jobs row), no latexify (phase 2).

gather refuses three ways, and the third matters most: with no master letter there
is no voice to imitate, and a letter invented from nothing is what this feature
exists to avoid. Its message points at the résumé tab rather than just saying no.

The refusal tests assert the model was NEVER CALLED, not merely that the output
was empty -- Phase B's lesson, where a refusal test passed while a mutation routed
the question straight to the model.

The draft carries NO AI-drafted marker: she reads it in the UI before it goes
anywhere. Phase 3 must add one when the applier starts pasting into live forms."
```

---

### Task 4: The FastAPI routes

**Files:**
- Modify: `server/routers/resume.py`
- Test: `tests/test_cover_letter_agent.py` (append)

**Interfaces:**
- Consumes: Task 2's store functions.
- Produces: `GET /data/cover-letter/master`, `PUT /data/cover-letter/master` (body `{"body": str}`), `GET /data/cover-letters/{job_id}/versions`, `PATCH /data/cover-letters/{job_id}` (body `{"status": "draft"|"final"}`). Task 5's components call exactly these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cover_letter_agent.py`:

```python
# ------------------------------------------------------------------ routes
#
# These use the EXISTING `client` fixture from tests/conftest.py, not a
# hand-rolled TestClient. It shares the temp DB *and* repoints
# `server.db.DB_PATH`, which matters: `server/db.py` caches DB_PATH at import, so
# a bare TestClient(app) would let the app's startup hook
# (`mark_stale_running_as_error()`) run against the REAL data/control_center.db.


def test_the_master_letter_can_be_read_and_written_over_http(client):
    assert client.get("/data/cover-letter/master").json()["cover_letter"]["body"] == ""
    r = client.put("/data/cover-letter/master", json={"body": "Dear team,"})
    assert r.status_code == 200
    assert client.get("/data/cover-letter/master").json()["cover_letter"]["body"] == "Dear team,"


def test_versions_are_served_for_one_job(client):
    cl.upsert_cover_letter("j", company="A", role="R", body="one")
    cl.upsert_cover_letter("j", company="A", role="R", body="two")
    got = client.get("/data/cover-letters/j/versions").json()
    assert [v["body"] for v in got["versions"]] == ["one"]


def test_status_can_be_patched_and_a_bad_status_is_rejected(client):
    cl.upsert_cover_letter("j", company="A", role="R", body="x")
    assert client.patch("/data/cover-letters/j", json={"status": "final"}).status_code == 200
    assert cl.get_cover_letter("j")["status"] == "final"
    assert client.patch("/data/cover-letters/j", json={"status": "nope"}).status_code == 422


def test_patching_a_missing_letter_is_a_404_not_a_500(client):
    assert client.patch("/data/cover-letters/missing", json={"status": "final"}).status_code == 404
```

**Use the `client` fixture, never `TestClient(app)` directly.** `tests/conftest.py:70` exists precisely
because `server/db.py` caches `DB_PATH` at import: a hand-rolled client lets the app's startup hook run
against the real `data/control_center.db`. The `client` fixture already depends on `temp_db`, so do not
request both.

- [ ] **Step 2: Run them and confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_agent.py -k "over_http or versions_are_served or status_can_be_patched or patching_a_missing" -q
```
Expected: 404s from the app because the routes do not exist yet.

- [ ] **Step 3: Add the routes**

In `server/routers/resume.py`, first read how the existing `/data/resume/master` GET and PUT are written — the response envelope (`{"resume": …}` vs bare), the Pydantic body models, and the router prefix — and mirror that shape exactly. Then add:

```python
class CoverLetterBody(BaseModel):
    body: str


class CoverLetterStatus(BaseModel):
    status: Literal["draft", "final"]


@router.get("/data/cover-letter/master")
def read_master_cover_letter():
    """The one sample letter the user owns. Absent reads as an empty body."""
    return {"cover_letter": cl_store.get_master_cover_letter()}


@router.put("/data/cover-letter/master")
def write_master_cover_letter(payload: CoverLetterBody):
    return {"cover_letter": cl_store.upsert_master_cover_letter(payload.body)}


@router.get("/data/cover-letters/{job_id}/versions")
def read_cover_letter_versions(job_id: str):
    return {"versions": cl_store.list_cover_letter_versions(job_id)}


@router.patch("/data/cover-letters/{job_id}")
def patch_cover_letter_status(job_id: str, payload: CoverLetterStatus):
    updated = cl_store.set_cover_letter_status(job_id, payload.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="no cover letter for that job")
    return {"cover_letter": updated}
```

Add `from agents.cover_letter_generator import store as cl_store` to the imports, and `Literal` from `typing` if absent. If the module's existing routes are registered without a `/data` prefix because the router already carries one, drop the prefix from these paths to match — the final URLs must be exactly those in the Interfaces block.

`Literal["draft", "final"]` is what makes a bad status a **422** without any hand-written validation; do not add a manual check.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_cover_letter_agent.py -q
```
Expected: 20 passed.

- [ ] **Step 5: Restart the API and check the routes are live**

Uvicorn is not reload-watching:

```bash
cd /Users/kayla.li/.superset/Agent
kill $(lsof -ti tcp:8001) 2>/dev/null; nohup .venv/bin/python -m server > /tmp/agent-server.log 2>&1 &
sleep 6
curl -s http://127.0.0.1:8001/data/cover-letter/master
curl -s http://127.0.0.1:8001/agents | python3 -c "import json,sys; print([a['key'] for a in json.load(sys.stdin)['agents']])"
```
Expected: a JSON envelope with an empty body, and `cover_letter_generator` in the agents list.

- [ ] **Step 6: Full suite, then commit**

```bash
.venv/bin/python -m pytest tests/ --junitxml=/tmp/t4.xml -q; echo "exit=$?"
```
Expected: exit 0, **2022** tests.

```bash
git add server/routers/resume.py tests/test_cover_letter_agent.py
git commit -m "feat(cover-letter): the write routes, since Next only reads

Singular /data/cover-letter/master beside plural /data/cover-letters/{job_id}/...
mirrors /data/resume/master vs /data/resumes/{job_id}/versions exactly: singular
for the one document she owns, plural for the per-job collection. Not a typo.

Literal[\"draft\",\"final\"] is what makes a bad status a 422, with no hand-written
validation to drift from the store's STATUSES."
```

---

### Task 5: The résumé tab

**Files:**
- Create: `web-next/src/components/resume/MasterCoverLetter.tsx`
- Create: `web-next/src/components/resume/GenerateCoverLetter.tsx`
- Create: `web-next/src/components/resume/CoverLetterCard.tsx`
- Modify: `web-next/src/app/(hub)/resume/page.tsx`

**Interfaces:**
- Consumes: Task 4's routes, and `POST /agents/cover_letter_generator/run` with `{"input": {"job_id": …}}` — read `GenerateForm.tsx` for the exact body shape it uses for the résumé and copy it.
- Produces: nothing later depends on.

- [ ] **Step 1: Read the three components you are mirroring**

Before writing anything, read all three in full:

```bash
cd /Users/kayla.li/.superset/Agent/web-next
wc -l src/components/resume/MasterResume.tsx src/components/resume/GenerateForm.tsx src/components/resume/ResumeCard.tsx
```

Each new component is the counterpart of one of these. Match their conventions: `"use client"`, `useRouter().refresh()` after a successful write, a `dirty` flag comparing local state to the prop, `busy`/`saved`/`error` state, and `RunStream` for run progress. **Do not invent a different pattern.**

- [ ] **Step 2: Create `MasterCoverLetter.tsx`**

Counterpart of `MasterResume.tsx`, minus the LaTeX and PDF parts (Phase 2):

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

// The master cover letter is a SAMPLE letter in the user's own voice. Each draft
// imitates its voice, structure and rhythm — it is not a placeholder template,
// so there is nothing to substitute here. Plain text, because a letter is prose
// pasted into a textarea where markdown would land literally.
export default function MasterCoverLetter({
  body,
  updatedAt,
}: {
  body: string;
  updatedAt: string;
}) {
  const router = useRouter();
  const [text, setText] = useState(body);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = text !== body;

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    const res = await fetch("/data/cover-letter/master", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    });
    setBusy(false);
    if (res.ok) {
      setSaved(true);
      router.refresh();
    } else {
      setError("Could not save the master cover letter.");
    }
  }

  return (
    <section className="panel">
      <h2>master cover letter</h2>
      <p className="muted small">
        One letter you are happy with, in your own voice. Every draft imitates its
        voice and structure — it is not a template, so write it as a real letter.
      </p>
      {!body ? (
        <p className="banner">
          Empty. Drafting is blocked until you write one, because a letter with no
          voice to imitate could only be invented.
        </p>
      ) : null}
      <textarea
        className="master-letter"
        rows={16}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"Dear Hiring Manager,\n\n..."}
      />
      <div className="master-actions">
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </button>
        {updatedAt ? <span className="muted small">updated {updatedAt}</span> : null}
        {saved ? <span className="muted small">saved</span> : null}
      </div>
      {error ? <p className="banner err">{error}</p> : null}
    </section>
  );
}
```

If `.master-letter` and `.master-actions` do not already exist in `globals.css`, reuse whatever classes `MasterResume.tsx` uses for its textarea and button row instead of adding new ones.

- [ ] **Step 3: Create `GenerateCoverLetter.tsx`**

Counterpart of `GenerateForm.tsx`. Read that file and mirror it: a job `<select>`, a button that POSTs the run, then `RunStream` for progress and `router.refresh()` on completion. The only differences are the endpoint (`/agents/cover_letter_generator/run`), the button label ("Draft cover letter"), and one extra guard:

```tsx
// Drafting needs a master letter — `gather` refuses without one. Saying so here,
// disabled, beats letting her start a run that can only fail.
disabled={busy || !jobId || !hasMaster}
```

with `hasMaster: boolean` as a prop, and a line explaining it when false.

- [ ] **Step 4: Create `CoverLetterCard.tsx`**

Counterpart of `ResumeCard.tsx`. **Copy to clipboard is the primary action** — the letter's destination is a textarea, so copying is the action, not downloading:

```tsx
  async function copy() {
    await navigator.clipboard.writeText(letter.body);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
```

Also render: company + role, `updated_at`, the body in a `<pre>` (plain text, preserving the paragraph breaks), a draft/final control calling `PATCH /data/cover-letters/{job_id}`, and version history from `GET /data/cover-letters/{job_id}/versions` in a `<details>` — mirroring how `ResumeCard` shows résumé versions.

- [ ] **Step 5: Wire them into the page**

In `web-next/src/app/(hub)/resume/page.tsx`, read the new tables via Prisma alongside the existing reads:

```tsx
  const masterLetter = await prisma.master_cover_letter.findFirst({ orderBy: { id: "asc" } });
  const letters = await prisma.cover_letters.findMany({ orderBy: { updated_at: "desc" } });
```

Then render, after the existing résumé sections:

```tsx
      <MasterCoverLetter
        body={masterLetter?.body ?? ""}
        updatedAt={masterLetter?.updated_at ?? ""}
      />
      <GenerateCoverLetter jobs={jobs} hasMaster={Boolean(masterLetter?.body)} />
      {letters.map((l) => <CoverLetterCard key={l.job_id} letter={l} />)}
```

Match the surrounding JSX's wrapper elements and headings — read the file first.

- [ ] **Step 6: Type-check, lint, build**

```bash
cd /Users/kayla.li/.superset/Agent/web-next
npx tsc --noEmit && echo "tsc clean"
npx eslint src && echo "eslint clean"
npm run db:check
cp -R .next /tmp/next-backup-cl 2>/dev/null
kill $(lsof -ti tcp:3000) 2>/dev/null
npm run build && (nohup npm run start > /tmp/next-server.log 2>&1 &)
```

Expected: tsc and eslint clean, `db:check` no drift, build succeeds. `:3000` serves a production build, so a restart alone would show nothing.

- [ ] **Step 7: Verify it by hand — this is the step no assertion replaces**

With `:8001` and `:3000` both running, open `http://localhost:3000/resume` and check, in order:

1. **Master cover letter section is present and empty**, showing the "drafting is blocked" banner.
2. **"Draft cover letter" is disabled** while the master is empty.
3. **Write a short letter, Save.** The banner disappears, `updated_at` appears, the button disables until you edit again.
4. **The draft button is now enabled.** Pick a job, draft it. Node events stream (`gather → draft → save`).
5. **A card appears** with the letter, its company and role, and copy-to-clipboard working.
6. **Press draft again for the same job.** The letter changes and the previous one appears under version history — the snapshot guarantee, visible.
7. **Toggle draft/final** and reload; the status persists.
8. At an 800px window the layout does not clip.

Report what you saw for each, and screenshot the section.

- [ ] **Step 8: Full suite, then commit**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/ --junitxml=/tmp/t5.xml -q; echo "exit=$?"
```
Expected: exit 0, **2022** tests (this task adds no Python tests).

```bash
git add web-next/src/components/resume/ "web-next/src/app/(hub)/resume/page.tsx"
git commit -m "feat(cover-letter): the résumé tab -- write a voice, draft, copy

Three components mirroring MasterResume / GenerateForm / ResumeCard, so a reader
who knows the résumé side learns nothing new.

Copy to clipboard is the PRIMARY action: the letter's destination is a textarea,
so copying is the action, not downloading. PDF is phase 2.

The draft button is disabled while the master letter is empty, because gather
refuses without one -- saying so up front beats starting a run that can only fail."
```

---

## Done when

- `2022 passed, 0 failures` from `.venv/bin/python -m pytest tests/` (1991 baseline + 30 new + 1 auto-added registry case).
- `npm run db:check` no drift · `tsc --noEmit` clean · `eslint src` 0 errors.
- `curl :8001/agents` lists `cover_letter_generator`.
- The manual walkthrough in Task 5 Step 7 done, including the version-history check — that one proves the snapshot guarantee end to end, which no unit test can show her.
- **Phase 2 (PDF) and Phase 3 (applier) untouched**: no `.tex` template, no `latexify` node, no change under `agents/job_applier/`.
