# Phase B session handoff — assisted apply

**Written 2026-08-04.** Everything from the session that built Phase B, so it survives a terminal
restart. The blow-by-blow ledger and all ten task reports live under `.superpowers/sdd/` which is
**gitignored** (`.gitignore:36`) — this document is the durable version.

- **Merged:** PR [#21](https://github.com/kayla177/Agent/pull/21) → `main` as `7d15f16` (2026-08-04)
- **Branch:** `feature/jobs-autoapply-phase-b`, 35 commits, `a535f49..d786acb`
- **Tests:** 14 → **1925** passing
- **Plan (kept current throughout):** `docs/superpowers/plans/2026-07-31-jobs-autoapply-phase-b.md`
- **Spec:** `docs/superpowers/specs/2026-07-25-jobs-quality-and-autoapply-design.md`
- **Phase A** (the prerequisite) merged earlier as `a535f49` via PR #20 — 262 tests, jobs-tab quality

---

## 1. What this feature does

A `job_applier` agent opens a Greenhouse/Lever/Ashby application form in a **visible** browser, fills
what it can derive from your stored profile, drafts free-text answers with the local Ollama model
(clearly marked as AI-drafted), then **stops** so you review and press Submit yourself.

**THE ONE RULE: no code path may ever click a submit button.** This is the invariant the whole design
serves. Enforced three ways:

1. An AST source scan over `agents/job_applier/**` (globbed recursively, so new modules are covered
   automatically).
2. A runtime backstop: `_single_locator` is the only caller of `page.locator`, and it refuses any
   control matching `_is_submitish`.
3. `_ENTER_CHARS` refuses the typing retry for any value containing `\n` or `\r` — see §4.

The scan is **honest, not complete**. Its residue is documented in `_UNSCANNABLE`
(`tests/test_applier_locate.py`): a selector held in a plain variable, and dynamic `getattr` access,
cannot be caught by AST. Do not let a later change imply the scan is a proof.

### Pipeline

```
load_profile → fetch_form → resolve → draft → fill → handoff
```

Registered as `job_applier` in `agents/registry.py`. Short-circuit on `state["error"]` is centralised
in `_step` (stronger than `resume_generator`'s per-node convention — a node cannot forget). One
deliberate exception: `handoff` runs even on error, because a run that could not open a browser still
owes you a report saying so.

### Module map

| File | Responsibility |
|---|---|
| `agents/job_applier/browser.py` | Headed persistent Chromium context (`data/browser_profile`), idempotent close |
| `agents/job_applier/locate_dom.py` | Pure DOM parsing + `PageLocator` adapter; question discovery, label matching |
| `agents/job_applier/schema_greenhouse.py` | `Question` dataclass, EEO term list, `is_eeo_label` |
| `agents/job_applier/resolver.py` | **PURE** — maps a question to an answer or a refusal. No I/O, ever |
| `agents/job_applier/drafting.py` | Free-text drafting via local model, default-deny |
| `agents/job_applier/nodes/fill.py` | The only module that writes to the page. Read-back on every write |
| `agents/job_applier/nodes/handoff.py` | The report you read. Structured dataclass + `render_text()` |
| `agents/job_applier/confirm.py` | Post-submit confirmation detection (pure) |
| `agents/job_applier/session.py` | Dedicated thread owning the browser (see §6) |
| `server/applier_run.py` | Single-thread graph driver; bookkeeping byte-identical to `runner.py` |

---

## 2. Your rulings (decisions that are yours, not mine)

**Résumé auto-attach — 2026-08-01.** The agent attaches your chosen résumé, **ordered last**, because
Greenhouse and Lever often run a parse-and-prefill on upload that would overwrite fields already
filled. Verified by filename read-back. Label-matched, and it **refuses rather than guesses** when a
form has several file inputs (résumé / cover letter / transcript) — a résumé in the transcript slot is
worse than an empty slot.

`resolver.py` still classifies `file_upload` as blocking and stays pure; the attach is a separate
explicit path in the executor driven by the file you picked, not a resolver answer. Its note in
`resolver.py` was corrected — it used to say "nothing is uploaded automatically", which your ruling
made false.

**US/Canada only.** Carried from Phase A: `JOB_COUNTRIES = ["US", "CA"]`. Store-don't-drop — non-matching
postings are tagged, not deleted.

**Work authorization is never auto-typed**, even when the resolver could resolve it from your profile.
A wrong work-authorization answer is a misrepresentation on a legal document. The value is offered as
a suggestion in the note for you to confirm.

---

## 3. Wrong values it would have typed into your real applications

Four distinct classes. **Every one was found by reading the agent's output, not its code** — which is
the single most useful lesson from this phase.

| Principle | What it would have done |
|---|---|
| A label asking **about** an attribute is not asking **for** it | "How do you pronounce your name?" → filled with your name |
| A label naming a **different instance** of an attribute | "High School Name" → filled with your university. Worse: "Year of High School Graduation" → *selected* the university's grad year from a dropdown |
| The same stem as **event-noun vs level-adjective** | 11 phrasings — "Graduate School", "Graduate school GPA", "Post-graduate studies" → filled with a **date** |
| A question wanting a different **medium** than prose | "Prompt 1: … (90 seconds max)" wants a YouTube URL → would have drafted a paragraph |

Fixes: `name_meta`, `school_level`, a `graduation`/`graduate` split plus a new `gpa` kind ordered
first, and section-heading-based `not_prose` detection.

**Also caught:**

- A **swallowed write on a pre-populated field** was reported as a reformat. If a form arrives
  pre-filled (browser autofill, ATS session restore, apply-with-LinkedIn) and React swallows the new
  value, the old value reads back — and the code called that "the field accepted the value but
  reformatted it" and skipped the retry. A stale wrong value with a reassuring note.
- An **element selector that addressed two live elements**, because uniqueness was computed over
  visible controls only, so a hidden twin freed its `name`.
- A **wrong-value path in the merge** that was green only because the three captured fixtures contain
  zero questions the resolver answers *and* drafting claims. A fixture accident mistaken for an
  invariant; on a real form your own value would be replaced with `""`.

### Still open (needs a schema change, not a resolver change)

A profile whose `school` **is** a graduate school is still offered for "Undergraduate School".
Blanking both breaks the primary case; fixing it properly needs a profile field that doesn't exist
(a level, or a second institution).

---

## 4. The submit hole was never a click — twice

**HTML implicit submission.** Enter in a text input inside a `<form>` submits it. No button, no click.
Playwright sends Enter for **both** `\n` and `\r` (driver alias map: `["Enter", ["\n", "\r"]]`).

- **Round 1 (Task 6):** the typing retry `press_sequentially`'d values containing newlines. Blocked
  `\n`; review found `\r` still open.
- **Round 2 (final review):** `Locator.type("yes\n")` reaches Enter through the same alias map, and
  **no newline gate consulted it** — the gate lived only inside `_write_text`. The same bug,
  reopenable under a different method name, **with a green source scan**. `Locator.uncheck()` — a real
  click — was also unguarded.

The refusal is now **tag-blind**: any value containing `\n`/`\r` refuses the typing retry, with no
textarea exemption. The exemption relied on `control.tag` from the single DOM snapshot the whole graph
runs on — taken *before* the model calls, addressed by `[id="…"]` selectors that are not tag-scoped —
so a snapshot "textarea" can be a live single-line `<input>` and `count() == 1` cannot tell. Nothing
is lost: attempt 1 (`fill()`) dispatches no key events, so multi-line values fill normally.

**Also closed:** `apply_url` returned the caller's override with no scheme or host check, and the
browser uses a persistent profile carrying your live ATS cookies. `POST /agents/job_applier/run` with
a body bypassed the thread design entirely and could point that authenticated browser anywhere. Now
verified refusing `evil.example.com`, `file:///etc/passwd` and `localhost:8001`; the generic endpoint
409s for `job_applier`. Not reachable from any UI affordance — it needed a hand-crafted request.

---

## 5. Known limits — disclosed, not hidden

All fail **safe** (blank + explanation), never a wrong value or a submission.

**Use Greenhouse or Lever on your first real run, not Ashby.**

1. **Ashby marks required-ness only via a per-deploy hashed CSS class** (`_required_f7cvd_91`), so 8 of
   its required questions read as optional. Not fixed — the hash changes per deploy and there is one
   captured Ashby form to validate a reader against. Instead the report now **hedges** for boards where
   required-ness could not be determined, rather than asserting "the form will not submit without
   these". On Ashby, read the `NEEDS YOUR REVIEW` band as if it were `REQUIRED`.
2. **Ashby's required Location field** renders as an optional question labelled "Start typing…", with a
   note falsely saying your profile has no location. Its `<label for=…>` points at a missing id, so the
   placeholder tier wins. Repairable structurally (the dangling `for` equals the enclosing
   `_fieldEntry`'s `data-field-path`) but left undone: firing it auto-fills a `role="combobox"` that a
   typed string does not actually select, which leaves a *silently invalid* field. Needs a live check.
3. **Confirmation-page fixtures are SYNTHETIC** and have never been compared against a real post-submit
   page — capturing one requires actually submitting. Only the *refusals* are measured, against the
   three captured live apply forms. **Your first real submission validates the phrase lists.** A missing
   ✓ means "not recognised yet", not "not submitted".
4. **Production's form-readiness wait is weaker than the one every fixture was captured under.**
   `fetch_form` uses `domcontentloaded` + `wait_for_selector`; the capture probe also waited for
   `networkidle`. So "it works on the fixtures" under-predicts production.
5. **`schema_greenhouse.parse_questions` has no production callers.** DOM discovery covers all three
   boards from one path; scraped rows carry no `absolute_url` or questions payload, so the API route
   would need a live fetch at run time. `Question`, `is_eeo_label` and `_EEO_TERMS` from that module
   **are** load-bearing — do not delete those.
6. **`withheld_eeo()` returns 0 on all three captured boards**, so that band only fires under synthetic
   tests.
7. **Nothing has run against a real browser** except the read-only fixture-capture probe and one
   `file://` smoke test. The live tag re-read, the override refusal, the 409, and the single-thread
   driver's stream shapes all rest on stubs and fixtures.

---

## 6. Architecture notes worth keeping

**Playwright's sync API is greenlet-bound and thread-affine.** Sync LangGraph nodes run in asyncio's
default `ThreadPoolExecutor` — measured: nodes ran on `asyncio_0` while the loop was on `MainThread`,
and two sequential sync nodes *shared* that worker. Consequences:

- Reading the handed-over page from a **FastAPI request handler could never work** (different thread).
  The obvious shape of the confirmation trigger would have shipped a button that cannot succeed.
- `fetch_form` opening on one pool worker and `fill` typing from another is a real but **probabilistic**
  hazard, not a certainty — asyncio reuses idle threads.

So the applier runs its whole graph on **one dedicated thread** (`agents/job_applier/session.py` +
`server/applier_run.py`), with bookkeeping byte-identical so `RunStream` cannot tell the difference.
The generic driver was left untouched (six working agents, live servers). **Any future agent holding a
thread-affine object across two sync nodes has the same hazard** — documented in `ARCHITECTURE.md`.

**Browser lifetime:** closed on every failing path (a node raising — including the last node — a node
setting `error`, or a page that will not load), and **deliberately left OPEN on success**. Closing it on
a successful fill would discard every field just typed, one keystroke before the only action that
matters, and make confirmation detection impossible. `graph.release_browser(state)` is exported for
whoever ends the session; `/data/jobs/assisted-apply/close` is the UI escape hatch.

**KNOWN LIMIT:** a node that *replaces* the context in state would orphan the old one, because
`release_browser` only reads `state["browser"]`. No node does today.

**`FillReport` has FOUR statuses** — `filled`, `blank`, `changed`, `attached` — plus `superseded`.
`changed` exists because a phone mask rewriting `5550100` as `(555) 0100` genuinely accepted the value.
`superseded` exists because the resolver and the attach path both report the résumé field, and rendering
both showed "Resume/CV — blank, attach it yourself" for a slot that was successfully attached.

**Database:** `applications.confirmed_at TEXT` (nullable, no default) landed in production. `_migrate`
must run **BEFORE** `executescript(schema.sql)` in `store_db.init_db()`, because
`CREATE INDEX IF NOT EXISTS` guards the index *name*, not the column. Deployment ordering: run one
Python store call before regenerating Prisma, or the applications page 500s on `no such column`.

Pre-existing drifts found in passing, unrelated to this work: `jobs` and `master_resume` have migrated
columns in a different *order* than `schema.sql` declares, and production's `resumes.job_id` is nullable
where the script says `NOT NULL`.

---

## 7. Process lessons

**Prose asserting a property the code lacks was the dominant defect class — eleven findings.** Not wrong
logic, but wrong *explanations* of correct logic, which is exactly what survives review and misleads the
next reader. Worst variant: a **test enforcing a false claim** — the handoff told you "the browser window
is still open" across all eight failure routes including where it had been closed or never opened, with a
test pinning that headline. The usual remedy (pin the claim) was what locked the bug in.

**Word boundaries were wrong three times.** `\b` after `pronounc` excluded `mispronounce`; `\b` after
`school` excluded `high schooling`/`schooler`; and — subtlest — a mutant correctly measured as
*equivalent* in one round became load-bearing the next, when `graduate school` joined the alternation and
`undergraduate school` turned out to contain it. **Whether a boundary is load-bearing is a property of
the vocabulary, not the regex, and an equivalence measurement is only valid for the alternation it was
measured against.**

**Mutation harnesses lied three times, from two different agents, all one family — a harness reporting
confidently about what it did not measure.** (a) Grepping stdout for `failed` when this pytest has
`addopts = "-q"` and prints **no summary line** → 10 false GREENs. (b) A first-occurrence string replace
that edited a docstring instead of the constant → a false survivor. (c) Stale hardcoded anchors reported
as survivors. Rules now: **use exit codes and `--junitxml`**, derive anchors from source, make anchor
errors fatal, and self-verify the harness on a mutation you know must fail. And choose the mutation *set*
deliberately — one agent's "19/19 caught" was true of the 19 it ran, and the omission was the defect: it
skipped the mutations aimed at claims its own docstrings made.

**Tests that cannot fail shipped five times** in this codebase. Asserting an *outcome* is not asserting
that something never *happened*: a refusal test that checked the answer was blank passed while a mutation
routed the question straight to the model, because `draft_one`'s `except Exception` swallowed the guard's
`AssertionError`. Refusal tests now install a deliberately **fabricating** model and assert the call list
is empty.

**⚠️ SAFETY RULE, learned the hard way: mutation testing must never rewrite a file in place while a
loaded `launchd` job imports it.** The harness rewrites `store_db.py`; four of its mutations make that
file mis-migrate `confirmed_at`; the launchd agents were loaded throughout. Had the scraper fired inside
one of those windows, **production would have been migrated by a mutant.** It didn't — both observable
fingerprints are absent from the live schema — but that was luck. Copy the repo (rsync) or unload the
agents first.

Relatedly: **the production migration ran by scheduler, not by decision.** launchd's jobscraper called
`store_db.init_db()` with the edited schema on disk. Landed state verified correct read-only.

**A stale long-running server reports missing code as a broken attribute.** Your résumé bug was
`module 'config' has no attribute 'OLLAMA_NUM_CTX'` — the attribute was right there in `config.py`. The
:8001 process had started two days before that line was added and Python caches modules at import. Any
time you see `module 'X' has no attribute 'Y'` and grep shows `Y` present, the process is older than the
line.

---

## 8. Corrections I made during the session

Recorded because the plan cites measurements, and a wrong one that looks measured is worse than none.

- **Greenhouse IS server-rendered.** I reported it as a JS shell with 0 inputs; my probe hadn't followed
  the 301 from `boards.greenhouse.io` to `job-boards.greenhouse.io`. Only Ashby genuinely needs a browser
  to capture. My Lever counts were wrong too (74/51/8, not 69/50/3).
- **"Year of High School Graduation already resolves correctly" was false.** I told an implementer the
  ordering fix was proven by the neighbouring rule. It blanked only because my probe's profile held
  `"May 2027"`, which matches no dropdown option; with a bare `"2027"` the pre-fix resolver *selected* it.
  Saved by date formatting, not rule ordering. The implementer caught this and ordered the new rule ahead
  of `grad_date` accordingly.
- **The Lever fixture contains no EEO block.** My 386 grep hits were all inside inline `<style>`; the
  markup has zero.
- **The blocking questions were not silently absent**, as I said — they were in `unreadable_questions()`
  with empty labels. Real gap, overstated.
- **I relayed a reviewer's Lever 36/11 measurement that did not reproduce.** Real answer: positional vs
  containment differs on 0/65 Lever controls, 20/20 Ashby, 15/15 Greenhouse.
- **I said the PII scrub was pinned so "salary ranges, dates and headcounts survive byte-identical".**
  That test pinned one string; `$5,550,100` was being destroyed because it contains the phone's digit run.
- **Two of my rulings were wrong and implementers correctly refused them:** "the browser must always be
  closed" (would close the window on success) and "Phase A found two registries drifted from their
  graphs" (false — `deliver` is conditional on `send`, so a test written against the default would have
  broken three working agents).

---

## 9. Outstanding actions

- [ ] **Disconnect the Prisma integration.** `Prisma Compute Deploy` fails on `main` and on every commit:
      it looks for `/build/app/package.json` but the only `package.json` is `web-next/package.json`. It can
      never succeed — the datasource is `provider = "sqlite"` pointing at a local file, and this app is
      loopback-only with local Ollama. Nothing to deploy. Prisma Data Platform console → project →
      Settings → disconnect the repo, or GitHub → Settings → Integrations → Applications → Prisma.
      **Not caused by Phase B** — it also fails on `a535f49`.
- [ ] **`~/.superset/Agent-resume-tab` holds `main`** and is two merges stale — `git pull` there.
- [ ] Optional: delete the merged `feature/jobs-autoapply-phase-b` on the remote. 9 worktrees exist,
      several on long-finished branches.
- [ ] First real run: **Greenhouse or Lever, not Ashby.** Take the closing count as a floor. Use
      `/data/jobs/assisted-apply/close` from the modal rather than closing the window by hand. Do **not**
      trigger via `POST /agents/job_applier/run` with a body — the dashboard card is the safe path.

### Restarting the servers

```bash
# agent API (:8001) — required after any Python change
cd ~/.superset/Agent && kill $(lsof -ti tcp:8001); nohup .venv/bin/python -m server > /tmp/agent-server.log 2>&1 &

# web UI (:3000) — runs `npm run start`, a PRODUCTION build, so a restart alone
# changes nothing; it needs a rebuild first. Back up .next so a failed build
# cannot leave the site broken.
cd ~/.superset/Agent/web-next
cp -R .next /tmp/next-backup
kill $(lsof -ti tcp:3000); npm run build && nohup npm run start > /tmp/next-server.log 2>&1 &
```

Both were restarted on 2026-08-04 and verified: `job_applier` registered, all pages 200, `/tracker`
rendering the confirmed/unverified markers, and the client bundle carrying *"It fills the form. It does
not submit it — and it never will."*

---

## 10. Verification baseline

`1925 passed` · `tsc --noEmit` clean · `eslint` clean · `check:jobs` 14/14 · `db:check` no drift · DB
fingerprint unchanged across the whole plan · no test launches Chromium, hits the network, or reaches the
model (suite-wide `ModelCalledInTest` guard in `tests/conftest.py`).

`.venv/bin/python -m pytest tests/` — **this pytest prints no summary line** (`addopts = "-q"`). Count with
`--junitxml` and read the exit code. `uv` is **not** installed.

A skill was extracted from this work and lives at `.claude/skills/verifying-inherited-claims/SKILL.md`
(committed `db80060`) — for when a number arrives from context and your output will multiply it.
