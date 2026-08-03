# Phase B — Assisted Apply (fill, never submit) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A `job_applier` agent that opens a Greenhouse / Lever / Ashby application form in a visible browser, fills every field it can derive deterministically from `applicant_profile`, drafts the free-text answers with the local model clearly marked as AI-drafted, and then **stops** — handing the user a review checklist. The human clicks Submit. Afterwards the agent detects the ATS confirmation page and upgrades the tracker row from optimistically-applied to confirmed.

**Spec:** `docs/superpowers/specs/2026-07-25-jobs-quality-and-autoapply-design.md` (Phase B section)
**Base:** `main` @ `a535f49` (Phase A merged). Branch: `feature/jobs-autoapply-phase-b`.

## The one rule

**No code path may click a submit button.** Not behind a flag, not "if confirmed", not in a test against a live site. The agent fills and stops. Every task below inherits this.

---

## Research findings that shape the design

Measured 2026-07-31 against live public boards:

| ATS | Form schema as JSON? | Consequence |
|---|---|---|
| **Greenhouse** | **Yes.** `GET /v1/boards/{token}/jobs/{id}?questions=true` returns `questions[]` with `label`, `required`, and `fields[].type` (`input_text`, `input_file`, `textarea`, …). A real posting returned 12. | Field mapping is **schema-driven**: deterministic, unit-testable without a browser, and robust to CSS changes. |
| Lever | No. `api.lever.co/v0/postings/{token}` has `applyUrl` but no `customQuestions`. | DOM label-heuristic mapping. |
| Ashby | No. `api.ashbyhq.com/posting-api/job-board/{token}` has `applyUrl` but no `applicationFormDefinition`. | DOM label-heuristic mapping. |

**Design consequence:** the field *resolver* (profile → answers) is shared and pure; the field *locator* (answer → page element) is per-ATS. Greenhouse resolves against a fetched schema; Lever and Ashby resolve against the live DOM's labels. That split keeps the risky, untestable part as small as possible.

## Global Constraints

- **Never submit.** No `click()` on a submit control, ever.
- **Never invent a value.** Only `applicant_profile`'s typed fields may fill an identity/authorization field. A field with no profile value is left empty and listed in the handoff. This is why those columns are typed rather than free text.
- **Work-authorization answers are never model-drafted.** If `us_work_auth` / `ca_work_auth` are unset, the corresponding questions stay blank and are reported as blocking.
- `uv` is NOT installed. Python is `.venv/bin/python`; tests via `.venv/bin/python -m pytest`.
- **Tests never touch a live ATS.** All fill/locate tests run against saved HTML fixtures in `tests/fixtures/ats/`. Read-only GETs to public board APIs are allowed in a dev probe, never in a test.
- **Never write to `data/control_center.db`** in tests; use the `temp_db` / `client` fixtures. Verify any migration on a copy first.
- WAL-aware DB fingerprint before/after any task that could touch storage: `scratchpad/dbfp.sh`. File mtime is not evidence.
- Frontend: no JS test harness; verify with `npm run lint`, `npx tsc --noEmit`, `npm run check:jobs`. Do NOT run `npm run build` while a production server is live.
- Current baseline: **262 pytest tests green** at plan time (267 after Task 1). Must grow, not shrink.
- **Row counts in this plan go stale.** The user's launchd scraper is ACTIVE across 261 sources, so
  `jobs`/`new` move between tasks (551→559 during Task 1 alone). Re-read the fingerprint at the
  start of every task; attribute a delta to the scraper before suspecting your own work, and never
  read an unchanged count as proof a task was clean.

## Test data — obviously fake, and structurally unable to reach a real person

The user's real profile is intentionally incomplete (name, email, phone, links, grad date, and both work-auth fields are blank) and real values arrive later. Development uses this fixture, which must **never** be written into `data/control_center.db`:

```python
FAKE_PROFILE = {
    "full_name": "Testy McTestface",
    "email": "testy.mctestface@example.invalid",   # .invalid is RFC 2606 reserved
    "phone": "+1-555-0100",                        # 555-01xx is reserved for fiction
    "location": "Waterloo, ON, Canada",
    "linkedin_url": "https://www.linkedin.com/in/example-invalid",
    "github_url": "https://github.com/example-invalid",
    "portfolio_url": "",
    "school": "University of Waterloo",
    "degree": "Computer Engineering (3rd year)",
    "grad_date": "2027-04",
    "us_work_auth": "tn_eligible",
    "ca_work_auth": "citizen",
    "needs_sponsorship": 0,
    "summary": "3rd-year Computer Engineering. Python, React, Next.js.",
}
```

`.invalid` and `555-01xx` are reserved precisely so test data cannot reach a real inbox or phone. Live behaviour reads the real profile; only fixtures use this.

---

## File structure

```
agents/job_applier/
  __init__.py
  state.py                  # ApplierState TypedDict
  graph.py                  # load_profile -> fetch_form -> resolve -> fill -> handoff
  browser.py                # visible Chromium, persistent context, graceful absence
  resolver.py               # PURE: profile + question -> answer | BLANK(reason)
  schema_greenhouse.py      # questions API -> normalized Question list
  locate_dom.py             # normalized Question -> page element (Lever/Ashby + GH fallback)
  drafting.py               # LLM free-text, always flagged AI-drafted
  nodes/
    load_profile.py  fetch_form.py  resolve.py  fill.py  handoff.py
tests/fixtures/ats/
  greenhouse-questions.json  greenhouse-form.html  lever-form.html  ashby-form.html
tests/
  test_applier_resolver.py  test_applier_schema.py  test_applier_locate.py
  test_applier_drafting.py  test_applier_graph.py   test_applier_browser.py
```

---

## Task 1: Playwright bootstrap + graceful absence

**Files:** Create `agents/job_applier/__init__.py`, `agents/job_applier/browser.py`; Test: `tests/test_applier_browser.py`; Modify: `pyproject.toml`

**Interfaces:**
- Produces: `browser.launch_context() -> BrowserContext` (visible Chromium, persistent profile at `data/browser_profile/`); `browser.PLAYWRIGHT_MISSING_HINT: str`; `browser.is_available() -> bool`

- [ ] **Step 1: Write the failing test**

```python
"""Browser bootstrap. The agent must degrade with a usable instruction when
Playwright is absent, not traceback — it is an optional heavy dependency
(~150MB of Chromium) and the rest of the platform must keep working without it."""
from __future__ import annotations
import builtins
import pytest
from agents.job_applier import browser


def test_is_available_false_when_import_fails(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert browser.is_available() is False


def test_hint_names_both_install_steps():
    hint = browser.PLAYWRIGHT_MISSING_HINT
    assert ".venv/bin/pip install playwright" in hint
    assert "playwright install chromium" in hint
    assert "uv" not in hint, "uv is not installed on this machine"


def test_launch_raises_a_clear_error_when_unavailable(monkeypatch):
    monkeypatch.setattr(browser, "is_available", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        browser.launch_context()
    assert "playwright install chromium" in str(exc.value)


def test_profile_dir_is_under_data_and_gitignored():
    assert browser.PROFILE_DIR.name == "browser_profile"
    assert browser.PROFILE_DIR.parent.name == "data"
```

- [ ] **Step 2: Run it, confirm ImportError/AttributeError**

Run: `.venv/bin/python -m pytest tests/test_applier_browser.py -v`

- [ ] **Step 3: Install the dependency**

> **Measured 2026-07-31:** Chromium is **344 MB** on disk (`chromium-1228`) plus ~42 MB for the
> `playwright` pip package — not the ~150 MB this plan originally estimated. It may already be
> cached by the Playwright MCP plugin, in which case no download occurs. Verify rather than
> assume; see `.claude/skills/verifying-inherited-claims/SKILL.md`.

```bash
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
```
Add `playwright>=1.40` to `pyproject.toml` dependencies. Report the download size.

- [ ] **Step 4: Write `browser.py`**

Headed (`headless=False`) persistent context at `config.PROJECT_ROOT / "data" / "browser_profile"`. `data/` is already gitignored, so the profile is never committed. Import Playwright lazily inside the functions so importing this module never requires it.

- [ ] **Step 5: Tests pass; commit**

```bash
git add pyproject.toml agents/job_applier/ tests/test_applier_browser.py
git commit -m "feat(applier): visible-Chromium bootstrap that degrades without Playwright"
```

---

## Task 2: Greenhouse form schema

**Files:** Create `agents/job_applier/schema_greenhouse.py`, `tests/fixtures/ats/greenhouse-questions.json`; Test: `tests/test_applier_schema.py`

**Interfaces:**
- Produces: `Question` dataclass (`key`, `label`, `required`, `kind` in `text|textarea|file|select|checkbox`, `options`); `parse_questions(payload: dict) -> list[Question]`; `form_url(job: dict) -> str`

- [ ] **Step 1: Capture a real payload as a fixture (dev probe, not a test)**

```bash
.venv/bin/python -c "
import httpx, json, pathlib
r = httpx.get('https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs', timeout=25).json()
jid = r['jobs'][0]['id']
d = httpx.get(f'https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs/{jid}?questions=true', timeout=25).json()
p = pathlib.Path('tests/fixtures/ats/greenhouse-questions.json'); p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=2)); print('captured', len(d.get('questions', [])), 'questions')"
```

- [ ] **Step 2: Write the failing test**

```python
"""Greenhouse exposes the application form as structured JSON, so mapping is
schema-driven rather than DOM-guessing. Parsed from a captured fixture — this
test must never hit the network."""
from __future__ import annotations
import json, pathlib
from agents.job_applier.schema_greenhouse import parse_questions

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ats" / "greenhouse-questions.json"


def _questions():
    return parse_questions(json.loads(FIXTURE.read_text()))


def test_parses_the_identity_questions():
    by = {q.label.lower(): q for q in _questions()}
    for label in ("first name", "last name", "email", "phone"):
        assert label in by, f"missing {label}"
        assert by[label].required is True
        assert by[label].kind == "text"


def test_resume_is_a_file_question():
    q = next(q for q in _questions() if "resume" in q.label.lower())
    assert q.kind == "file"
    assert q.required is True


def test_multi_field_question_picks_the_primary_input():
    """Greenhouse models Resume/CV as fields=[input_file, textarea]. A file
    upload beats a paste-in textarea, so `kind` must be `file`, not `textarea`."""
    q = next(q for q in _questions() if "resume" in q.label.lower())
    assert q.kind == "file"


def test_every_question_has_a_stable_key():
    ks = [q.key for q in _questions()]
    assert all(ks) and len(ks) == len(set(ks)), "keys must exist and be unique"


def test_empty_or_missing_questions_is_not_an_error():
    assert parse_questions({}) == []
    assert parse_questions({"questions": []}) == []
```

- [ ] **Step 3: Implement, run, commit**

`kind` precedence when a question has several fields: `file` > `select` > `checkbox` > `textarea` > `text`. Document why (a file upload is the real answer; the textarea is a fallback Greenhouse offers).

---

## Task 3: The resolver — pure, and the safety boundary

**Files:** Create `agents/job_applier/resolver.py`; Test: `tests/test_applier_resolver.py`

This is where "never invent a value" is enforced. Keep it pure: no browser, no network, no model.

**Interfaces:**
- Produces: `Answer` dataclass (`question`, `value`, `source` in `profile|drafted|blank`, `note`); `resolve(questions, profile) -> list[Answer]`; `BLOCKING_KINDS: frozenset`

- [ ] **Step 1: Write the failing test**

```python
"""The resolver is the safety boundary: it may only answer from typed profile
fields. Anything it cannot answer becomes source='blank' with a reason, which
the handoff shows the user. It must never guess a name, an email, or — above
all — a work-authorization answer."""
from __future__ import annotations
from agents.job_applier.resolver import resolve
from agents.job_applier.schema_greenhouse import Question

FAKE = {
    "full_name": "Testy McTestface",
    "email": "testy.mctestface@example.invalid",
    "phone": "+1-555-0100",
    "linkedin_url": "https://www.linkedin.com/in/example-invalid",
    "us_work_auth": "", "ca_work_auth": "", "needs_sponsorship": 0,
    "school": "University of Waterloo", "degree": "Computer Engineering (3rd year)",
    "grad_date": "2027-04", "github_url": "", "portfolio_url": "", "location": "",
    "summary": "",
}

def q(label, required=True, kind="text"):
    return Question(key=label.lower().replace(" ", "_"), label=label, required=required, kind=kind, options=[])


def test_splits_full_name_across_first_and_last():
    a = {x.question.label: x for x in resolve([q("First Name"), q("Last Name")], FAKE)}
    assert a["First Name"].value == "Testy" and a["First Name"].source == "profile"
    assert a["Last Name"].value == "McTestface"


def test_single_name_field_gets_the_whole_name():
    assert resolve([q("Full Name")], FAKE)[0].value == "Testy McTestface"


def test_email_and_phone_come_from_the_profile():
    a = {x.question.label: x.value for x in resolve([q("Email"), q("Phone")], FAKE)}
    assert a["Email"] == "testy.mctestface@example.invalid"
    assert a["Phone"] == "+1-555-0100"


def test_unset_profile_field_is_blank_with_a_reason_not_invented():
    ans = resolve([q("GitHub Profile")], FAKE)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "profile" in ans.note.lower()


def test_work_authorization_is_never_drafted_or_guessed():
    """With both work-auth fields unset, the answer must be blank and marked
    blocking. A wrong answer here goes out on a real application."""
    ans = resolve([q("Are you legally authorized to work in the United States?")], FAKE)[0]
    assert ans.source == "blank"
    assert ans.value == ""
    assert "authorization" in ans.note.lower()


def test_work_authorization_is_used_when_the_profile_sets_it():
    prof = {**FAKE, "us_work_auth": "tn_eligible"}
    ans = resolve([q("Are you legally authorized to work in the United States?")], prof)[0]
    assert ans.source == "profile"
    assert ans.value


def test_free_text_question_is_left_for_the_drafting_node():
    ans = resolve([q("Why do you want to work here?", kind="textarea")], FAKE)[0]
    assert ans.source == "blank"
    assert "draft" in ans.note.lower()


def test_resolver_touches_no_io():
    """Pure function: importing and calling it must not need a browser, a
    network, or a model. Guards the safety boundary against future drift."""
    import inspect, agents.job_applier.resolver as r
    src = inspect.getsource(r)
    for forbidden in ("httpx", "playwright", "llm(", "requests"):
        assert forbidden not in src, f"resolver must stay pure; found {forbidden}"
```

- [ ] **Steps 2-4:** run red, implement, run green, commit.

---

## Task 4: DOM locator for Lever / Ashby (+ Greenhouse fallback)

**Files:** Create `agents/job_applier/locate_dom.py`, fixtures `tests/fixtures/ats/{lever,ashby,greenhouse}-form.html`; Test: `tests/test_applier_locate.py`

Lever and Ashby publish no form schema, so questions are discovered from the page. Match on the **accessible label**, not CSS classes — labels are what a human reads and are far more stable than generated class names.

- [x] **Step 1: Capture form fixtures (dev probe)** — save the real application pages for one Lever,
one Ashby, and one Greenhouse posting into `tests/fixtures/ats/`. Record which URLs and when, in a
comment at the top of the test.

> **Measured 2026-07-31 — a plain GET is NOT enough for two of the three.** This plan originally
> assumed fetching the apply URL would yield a usable form. It does not:
>
> **⚠️ SUPERSEDED — the Greenhouse and Lever rows below are WRONG. See the correction that follows.**
>
> | ATS | GET result | Capture method |
> |---|---|---|
> | Lever | server-rendered — 1 `<form>`, 69 `<input>`, 50 `<label>`, 3 `<textarea>` | plain GET |
> | Greenhouse | JS shell — **0 inputs** in 254 KB | render in a browser, dump `page.content()` |
> | Ashby | JS shell — **0 inputs** in 42 KB | render in a browser, dump `page.content()` |
>
> So the capture probe needs Playwright (Task 1's `launch_context`) for Greenhouse and Ashby.
> That is a dev probe only — it loads a public page read-only and MUST NOT type into or submit
> anything. The tests themselves stay pure: they parse the saved HTML and never open a browser.
>
> Note this also reinforces the Greenhouse split: its form *schema* comes from the questions API
> without rendering at all, and the browser is needed only to LOCATE elements on the live page.

> **CORRECTION, measured 2026-08-01 during Task 4 execution.** The Greenhouse row above is
> reproducible only if the 301 is not followed. `boards.greenhouse.io/<org>/jobs/<id>` 301s to
> `job-boards.greenhouse.io/...`, and that host **is** server-rendered: 67 KB, 18 `<input>`,
> 16 `<label>`. So a plain GET is sufficient for 2 of 3, not 1 of 3. Greenhouse was still captured
> rendered, because rendering is what the live locator sees and it picks up the JS-injected
> `aria-required` / `role="group"` attributes the locator depends on. Ashby genuinely needs a browser
> (41 KB shell, 0 inputs). Capture probe committed as `scripts/capture_ats_fixtures.py`
> (`--verify` re-runs the locator against the live pages, read-only, `.count()` only).
>
> The Lever row was wrong too: it is **74** `<input>`, **51** `<label>`, **8** `<textarea>`. Both
> errors were mine, from a probe that didn't follow the 301 and counts I then repeated without
> re-measuring. Corrected table:
>
> | ATS | plain GET of the apply URL | capture used |
> |---|---|---|
> | Lever | server-rendered — 1 `<form>`, 74 `<input>`, 51 `<label>`, 8 `<textarea>` | httpx GET |
> | Greenhouse | server-rendered after the 301 — 1 `<form>`, 18 `<input>`, 16 `<label>` | rendered anyway¹ |
> | Ashby | JS shell — 0 inputs in 41 KB | rendered (required) |
>
> ¹ Greenhouse is captured rendered even though a GET would suffice, because rendering is what the
> live locator sees and it picks up the JS-injected `aria-required` / `role="group"` attributes the
> locator depends on.

- [x] **Step 2: Failing tests** — from each fixture, discover the identity questions (name/email/phone/resume) with correct `kind`; assert a label-matched lookup finds the right element for each; assert an unmatched label returns `None` rather than a wrong guess; assert matching is case- and punctuation-insensitive ("Email" / "Email Address" / "E-mail *").

- [x] **Step 3: Implement using Playwright's `get_by_label` semantics, with an explicit fallback chain** (label → `aria-label` → `placeholder` → `name` attribute), each step documented. Never fall back to positional indexing — a wrong element gets a wrong value typed into it.

> **Task 4 decisions worth carrying into Tasks 5-7.**
> - Matching is **exact-or-token-prefix only**, ambiguity-checked per tier. "Email" finds
>   "Email Address"; "mail" does not find "Email"; "Name" does not find "First Name". Suffix/infix
>   matching is deliberately absent — that is how a full name lands in a first-name box.
> - `Control.selector` is built from `id` or `name` only, and is `None` when neither is unique.
>   `None` means "the human fills this one", which is the safe outcome (Ashby's location combobox
>   has no `id` and no `name` at all).
> - Radio/checkbox controls sharing a `name` collapse to ONE `Question` with the members as
>   `options`. A radio group's `kind` is `select` (single choice from a fixed list), reusing Task 2's
>   five-value vocabulary rather than inventing a sixth.
> - **Lever's custom "card" questions — RESOLVED in `5aceb86`; the paragraph that stood here was
>   superseded and is preserved below only so its reasoning can be audited.** It read: *"Lever's
>   custom card questions keep their title in a sibling `div.application-label` — reachable only via
>   a generated class name, which the label-not-classes rule forbids… 12 of Lever's 65 controls
>   therefore fall through to a `name`-derived label like `cards[<uuid>][field0]` and are effectively
>   human-fill-only. Fixing that needs a DOM-proximity heuristic; that is a guess."*
>
>   Two premises were wrong. `application-label`/`application-field` are **not** generated names —
>   they appear in Lever's own stylesheet selectors — and the relationship is **containment**, not
>   proximity: the question text and its control are the two children of one wrapper inside a single
>   `li.application-question`. That is the same class of evidence as a wrapping `<label>`. I
>   authorized it, scoped to that ancestor, ranked above the `placeholder` and `name` tiers.
>
>   What the old text got badly wrong was calling the fallthrough "human-fill-only". The `placeholder`
>   and `name` tiers do not fail safe — they emit **confident wrong labels**. Two distinct questions
>   both surfaced as `'Type your response'`; a textarea whose real label is "High School Name"
>   surfaced as `cards[d54adf7b-…][field0]`. And the work-authorization and sponsorship questions —
>   both in `BLOCKING_KINDS` — came back with `label=''`, so they never reached `resolver.blocking()`
>   and would have been absent from Task 7's blocking list. Measured after the fix: 29 answerable
>   (was 24), 0 unreadable (was 5), 0 `name`-labelled (was 12), 0 `placeholder`-labelled (was 2),
>   pinned by `test_the_name_tier_no_longer_fires_on_any_real_board`.
> - **Task 6 MUST locate by `Question.label`, never a literal string.** `find_control` with a short
>   query prefix-matches with no ambiguity to detect: on the Lever fixture `find_control("Name")`
>   returns the *pronunciation* field. A token-count cap was considered and rejected — it would break
>   `"Resume"` → `"Resume/CV and supporting documents"` — so the rule is structural: drive filling
>   from the `Question` objects `discover_questions` returns. Short queries are diagnostics only.
> - **`resolver.py` gained a `name_meta` class** (`18f0e70`), because reading Lever's labels correctly
>   exposed that `'Name Pronunciation | How do you pronounce your name?'` classified as `full_name`
>   and auto-filled the applicant's name — a wrong value, unflagged. The principle generalizes: *a
>   label that asks something ABOUT an attribute is not asking FOR it.* Note `pronounc\w*` is
>   deliberately not `pronoun\w*`, so a pronouns question is not quietly relabelled as a name
>   question.
> - **Also available to Tasks 5-7:** `unreadable_questions()` (questions with no readable label —
>   surface these to the human rather than dropping them) and `find_group_options()` (per-option
>   selectors for radio/checkbox groups, whose members share one `name`). `find_control` on a group
>   heading correctly returns `None`; a group has no single element.
> - `discover_questions` now applies the SAME EEO content screen as
>   `schema_greenhouse.parse_questions`, via a new public `is_eeo_label`. Greenhouse hands its
>   demographic questions over in a separate array Task 2 never reads; Lever and Ashby have no such
>   separation, so the label screen was the only thing between a protected-characteristic question
>   and the resolver. Withheld questions stay retrievable via `locate_dom.excluded_eeo_questions`.

---

## Task 5: Drafting free-text answers, always marked

**Files:** Create `agents/job_applier/drafting.py`; Test: `tests/test_applier_drafting.py`

- [x] Model-written answers are **always** returned with `source="drafted"` and a visible marker so the handoff can flag them. Ground them in the JD plus `company_research` when available rather than letting the model invent facts about the company.
- [x] Tests: a drafted answer is marked `drafted`; a model failure yields `blank` with a reason, never a fabricated answer; work-authorization and identity questions are **never** routed to drafting (assert the router refuses them); the prompt includes the JD but not the user's phone/email.

> **Task 5 decisions carried into Tasks 6-8.**
> - The marker lives **in the returned text**, not only in metadata: values start with
>   `DRAFT_MARKER`. Use the exported `is_marked()` rather than re-hardcoding the literal, and do NOT
>   strip it automatically — it is what a recruiter sees if the human misses the review step, which
>   is the fail-loud outcome we want.
> - Drafting is **default-deny**. Only `motivation` and `experience` topics draft; `unknown` does
>   not. On the real Lever form that is 2 of 7 free-text questions. Widen it by adding positive
>   topic rules, never by making `unknown` draftable.
> - Refusal is verified by **absence of a model call**, not by a blank answer. Refusal tests install
>   a deliberately fabricating model and assert it was never called — the first version raised
>   `AssertionError`, which `draft_one`'s `except Exception` swallowed into an innocent-looking
>   blank, and a mutation routing a refused question to the model passed all 56 tests.
> - **The hermeticity guard is suite-wide** (`tests/conftest.py`), not file-local. Tasks 6-8 get a
>   loud `ModelCalledInTest` if they forget to stub. Do not shadow it with a same-named fixture.
> - `Question.section` (new, additive) carries the enclosing section's heading, read by
>   **containment** — nearest `<section>`/`.section` ancestor, then that container's own first
>   heading. Measured: Lever 29 questions / 14 sections, Greenhouse and Ashby blank (neither has
>   heading elements to read; none was invented). A positional "nearest preceding heading" rule
>   differs on 0/65 Lever controls but 20/20 Ashby (stamping the widget title `Autofill from
>   resume`) and 15/15 Greenhouse (`Apply for this job`), pinned by
>   `test_what_the_positional_section_rule_would_have_produced`.
> - Section-scoped non-prose rules are a strict **subset** of label-scoped ones, derived from one
>   list so they cannot drift: video/recording, URL/link and file refuse a whole section; duration
>   and number/date are label-only, because a heading may mention a duration in passing.
> - The PII scrub runs over the **whole assembled prompt**, not a profile-field allowlist — the
>   experience corpus IS the master résumé and its header carries the phone and email. Its limits
>   are documented as Guaranteed / Best-effort / Not-attempted; a *third-party* number written
>   without separators survives, and closing that means redacting arbitrary digit runs, which is
>   what ate `$5,550,100` in the first attempt.

---

## Task 6: Fill executor with per-field verification

> **Prerequisite carried from Task 1:** no headed Chromium has actually been launched yet — Task
> 1's five tests are all monkeypatched. Before writing fill logic, launch `browser.launch_context()`
> ONCE against a saved local fixture page (`file://` URL from `tests/fixtures/ats/`), confirm the
> persistent context opens and accepts typed input, then close it. Never against a live ATS.

**Files:** Create `agents/job_applier/nodes/fill.py`; Test: extend `tests/test_applier_locate.py`

- [x] After typing, **read the value back** and confirm it landed. React-controlled inputs frequently swallow programmatic input; a fill that silently did nothing is worse than one that failed loudly.
- [x] A field that will not accept its value is reported as `blank` with the reason, not retried indefinitely.
- [x] **Assert no submit control is ever clicked** — a test that scans the executor's source for a click on anything matching `submit|apply now|send application` and fails if present.

> **RULING 2026-08-01 (Kayla) — the agent DOES attach the résumé, and it goes LAST.**
> The spec's own note (`resolver.py`: *"attach the file yourself in the open browser window;
> nothing is uploaded automatically"*) contradicted the original intent — *"use that resume to
> submit to the posting"* — so this settles it.
>
> - `resolver.py` keeps classifying `file_upload` as blocking and **stays pure**. It cannot answer
>   a file question with text, and that is the right refusal. The attach is a **separate explicit
>   path in the executor**, driven by the résumé the user picked, not a resolver decision.
> - **Ordered last.** Greenhouse and Lever frequently run a parse-and-prefill on upload that
>   overwrites fields already filled. Attaching last means the agent's values win.
> - **Verify by filename read-back**, the same discipline as every other field.
> - **Label-matched, and refuse rather than guess.** A form may carry several file inputs (résumé,
>   cover letter, transcript, portfolio). Attach only to the one whose label identifies it as a
>   résumé/CV; leave the others for the human. If the match is ambiguous, attach nothing and say so
>   in the handoff — a résumé in the transcript slot is a worse outcome than an empty slot.
> - THE ONE RULE is unchanged: attaching a file is not submitting. No code path may click submit.

> **Task 6 outcome — decisions carried into Tasks 7-10.**
> - **THE ONE RULE had a live hole, and it was not a click.** HTML implicit submission: Enter in a
>   text input inside a `<form>` submits it, and Playwright's `press_sequentially` sends Enter for
>   BOTH `\n` and `\r` (driver alias map: `["Enter", ["\n", "\r"]]`). The typing retry could have
>   submitted a real application with no click anywhere and a green source scan. `_ENTER_CHARS` now
>   bans both in anything but a `<textarea>`, and the pair is provably exhaustive because that alias
>   map is the only place a character becomes a key.
> - **The source guard is honest, not complete.** `evaluate`, `dispatch_event`, `keyboard`/`mouse`/
>   `touchscreen` and constructed selectors are now scanned; a selector held in a plain variable and
>   dynamic `getattr` access cannot be. Both are backstopped at RUNTIME by `_single_locator` — the
>   only caller of `page.locator` — which refuses any control `_is_submitish` matches. The residue is
>   written down in `_UNSCANNABLE`; do not let a later change imply the scan is a proof.
> - **`FillReport` has FOUR statuses** — `filled`, `blank`, `changed`, `attached` — plus
>   `superseded`. `changed` exists because a phone mask rewriting `5550100` as `(555) 0100` really
>   did accept the value, and calling that `blank` is untrue. `needs_review` groups blank + changed
>   + drafted. **Task 7 must render all four and honour `superseded`.**
> - **A revert is not a reformat.** The pre-write value is captured, so a field reading back as what
>   was already there is a swallowed write (retry; report the stale string as NOT your value), not a
>   reformat. Pre-populated forms are common — browser autofill, ATS session restore, apply-with-
>   LinkedIn.
> - **The résumé slot is reported once.** The resolver emits a `file_upload` answer for the same DOM
>   field the attach path fills; that outcome is suppressed for the control the attach CLAIMED, so a
>   cover-letter slot keeps its own answer. Tracked in `FillReport.superseded`.
> - A file input's `input_value()` returns `C:\fakepath\<name>` — Windows separator, on macOS, from
>   a `file://` page. Verification reads `el.files[0].name`, parsing the fake path only as fallback.
> - **UNRESOLVED, needs the first live run:** whether Ashby's radios pass `is_visible()`. Its hiding
>   rule lives in an external CDN stylesheet the fixture does not contain. Lever's radios are 17-20px
>   (visible); Greenhouse has no hiding rule. No speculative exemption was added — Playwright's
>   `check()` cannot tick a hidden element either, so `_gate` only turns a timeout into an explained
>   blank.

> **Task 6 decisions carried into Tasks 7-8.** (Full write-up:
> `.superpowers/sdd/2026-07-31-jobs-autoapply-phase-b/task-6-report.md`.)
> - **The headed-browser prerequisite is done.** `browser.launch_context()` was launched once
>   against a `file://` copy of the Lever fixture, with all non-`file://` requests aborted at the
>   route level (the saved page would otherwise have made five: hCaptcha, Lever's CDN ×3, GTM).
>   The persistent context opened, `fill()` / `press_sequentially()` / `set_input_files()` all
>   worked, read-back worked, and teardown left zero processes. Measured there and encoded as a
>   test: a file input's `input_value()` returns **`C:\fakepath\<name>`** — the spec's fake path,
>   Windows separator, on macOS — so filename verification reads `el.files[0].name` and only falls
>   back to parsing that. The obvious `input_value() == path` check would have failed every
>   successful attach.
> - **There is a FOURTH status: `changed`.** Filled / blank / changed / attached. A phone mask that
>   rewrites `5550100` as `(555) 0100` did accept the value, so reporting it `blank` is untrue, and
>   an untrue note costs trust in every other note. `FillReport.needs_review` groups
>   blank + changed + drafted for the handoff.
> - **Retry policy: `MAX_ATTEMPTS = 2`, and attempt 2 must be a different mechanism** (typing, not
>   a second `fill()`). Selects and checkboxes get no retry — the only other strategy is clicking
>   the option, and this module clicks nothing.
> - **HTML implicit submission is a ONE-RULE hazard that a click-scanning guard does not see.**
>   Enter in a text input inside a `<form>` submits it, and `press_sequentially` on a value
>   containing `\n` sends Enter. The typing retry is therefore refused for a multi-line value in
>   anything but a `<textarea>`.
> - **Blocking answers are refused even when they RESOLVED.** The resolver emits `"Yes"` from the
>   profile for work authorization; the executor does not type it, and carries it into the note as
>   a suggestion instead. Proved by a spy asserting the write list is empty — not by the outcome,
>   which is the assertion Task 5 found to be worthless.
> - **The executor's source guard is the INVERSE of the read-only one**: `.fill(` and
>   `set_input_files(` are asserted PRESENT, clicks and submit-shaped selector literals are
>   rejected. AST attribute equality, not grep, because `press` must be banned while
>   `press_sequentially` must not. Proved in both directions on synthetic sources.
> - **All ten guards were mutation-tested** and each turns a named test red. The harness itself
>   reported ten false GREENs on its first run (it grepped stdout for `failed`; this pytest config
>   prints no final summary line) — check a mutation harness before trusting it.
> - `resolver.py`'s file-upload note was corrected and is pinned by a test; the module stays pure.
> - Open for Task 7: `PageLocator` has no public `page` accessor, so `fill_form` reaches for
>   `_page` to address radio-group members. A public property would remove the one private access.
>
> **Task 6 fix round 1** (independent review: 17 mutations, 5 survived; all addressed).
> - **`\r` is an Enter alias too, and the first version missed it.** Playwright's driver has ONE
>   character→key map (`coreBundle.js`, `aliases`) whose only character entry is
>   `["Enter", ["\n", "\r"]]` — so that pair is exhaustive by construction, not a guess, and a
>   lone CR would have typed Enter into a single-line input with the source scan green. Also
>   closed the wider family: `keyboard`/`mouse`/`touchscreen`, `dispatch_event`, and
>   `evaluate`-based submits are all now banned.
> - **A source guard must state what it CANNOT see.** The old prose claimed no submit-shaped
>   string could appear in the code, which was false. `_UNSCANNABLE` now lists the four holes with
>   their backstops. `evaluate` could not be banned (fill.py needs it), so the rule is
>   literal-script-only plus a JS mutation screen; selector args allow a bare variable but reject
>   anything CONSTRUCTED, which closes the f-string bypass without breaking
>   `page.locator(control.selector)`.
> - **Guards must be parametrised over a GLOB, not a hardcoded path.** The scan covered `fill.py`
>   only, so Task 7's handoff and Task 8's graph would have got none. Now every `nodes/*.py`.
> - **The résumé field must be reported ONCE.** The resolver emits a `file_upload` answer for the
>   same DOM field the attach path fills, so the handoff said "blank, attach it yourself" about a
>   slot the agent had attached to. `fill_form` suppresses the answer for the control the attach
>   CLAIMED (keyed on the control, not the kind, so cover-letter slots survive) and records it in
>   `FillReport.superseded`.
> - **"Non-empty and different" is two opposite situations.** A field that REVERTED to what it
>   already held is a swallowed write and must retry; a field that REFORMATTED accepted the write
>   and must not. Capturing the pre-write value is what tells them apart — without it a stale
>   autofilled value was left in place and described to the user as a reformat.
> - **Bounding writes is not bounding the agent.** Reads (`input_value`, `evaluate`, `is_checked`,
>   `is_visible`) all default to Playwright's 30 s and wait for an attached element exactly as
>   writes do. All now take an explicit timeout, with a source-scanning test so a new read helper
>   cannot be added without one.
> - Measured, for Task 7: **Lever** radios are sized 17-20px and pass `is_visible()`; **Greenhouse**
>   has no hiding rule; **Ashby**'s live in an external CDN stylesheet the fixture lacks, so it is
>   UNKNOWN. Not worked around — `check()` cannot tick a hidden element either, so `_gate` only
>   turns a timeout into an explained blank. Confirm on the first live run.
> - Two harness bugs in two rounds (stdout grepping; a first-occurrence replace that edited a
>   docstring instead of the constant). **Treat mutation-harness output as a claim to verify.**

---

## Task 7: Handoff report

**Files:** Create `agents/job_applier/nodes/handoff.py`; Test: `tests/test_applier_graph.py`

- [x] Output groups answers into **filled from profile / AI-drafted, review these / left blank**, with required-and-blank listed first as blocking.
- [x] Says plainly that nothing was submitted and the browser is left open.
- [x] Tests: a blocking required field appears first; drafted answers are marked; the message never claims to have submitted.

> **Task 7 outcome — and four wrong-value defects the report EXPOSED.**
> The handoff leads with `NOTHING WAS SUBMITTED`, then a count ("20 of 29 fields need you. 14 of
> those are required and still empty"), then blockers grouped by section, then — collapsed last —
> what was filled. Structured dataclass + `render_text()`; Task 10 renders its own.
>
> Making the agent's output legible is what found these. None was found by reading code:
> 1. `name_meta` — *a label asking ABOUT an attribute is not asking FOR it.* "How do you pronounce
>    your name?" was auto-filled with the applicant's name.
> 2. `school_level` — *a label naming a specific instance of an attribute is not asking for the
>    instance the profile stores.* "High School Name" was filled with the university, and — worse,
>    because a dropdown makes it look deliberate — "Year of High School Graduation" selected the
>    university's grad year. That one only LOOKED safe in the first probe because the profile held
>    `"May 2027"`, which matches no option verbatim; with a bare `"2027"` it was selected. Saved by
>    date formatting, not by rule ordering. Hence ordered ahead of `grad_date`, not merely ahead of
>    `school`.
> 3. `graduation` vs `graduate` — *the same stem can be a noun naming an event and an adjective
>    naming a level.* ELEVEN adjectival phrasings returned the graduation date (`Graduate
>    School/program/degree/studies/coursework/institution/education/level`, `Post-graduate studies`,
>    `Grad school`, `Recent graduate?`). A new `gpa` kind is ordered first so no GPA field is
>    reachable by a date rule. Verbal uses of "graduate" are an **allowlist of clause openers** — a
>    denylist of following nouns cannot be finished.
> 4. `not_prose` — *a question can want a different MEDIUM than prose* (Task 5's video prompts).
>
> **Word boundaries have been wrong three times on this branch.** `` after `pronounc` excluded
> `mispronounce`; `` after `school` excluded `high schooling`/`schooler`; and a leading ``
> reported as a MEASURED-EQUIVALENT mutant later became load-bearing when `graduate school` joined
> the alternation, because `undergraduate school` contains it. **Whether a boundary is load-bearing
> is a property of the vocabulary, not the regex — and an equivalence measurement is only valid for
> the alternation it was measured against.**
>
> **Carried into Task 8 as a REQUIREMENT:** the resolver blanks free-text with "left for the AI
> drafting step", and drafting then DECLINES some of those (both Lever video prompts). If the merge
> keeps the resolver's answer whenever drafting blanks, the report promises a draft that is never
> coming. Policy: **drafting wins for every question it owns, refusals included.** Implemented as
> `_merge` in the handoff tests and pinned by
> `test_the_report_never_promises_a_draft_that_was_already_declined`. Lift it into the graph.
>
> **Residual, needs a SCHEMA change not a resolver change:** a user whose profile `school` IS a
> graduate school still gets it offered for "Undergraduate School". Blanking both breaks the primary
> case; fixing it needs a profile field that does not exist (a level, or a second institution).

---

## Task 8: Graph + registry

**Files:** Create `state.py`, `graph.py`, `nodes/*`; Modify: `agents/registry.py`; Test: `tests/test_applier_graph.py`

- [x] Chain: `load_profile → fetch_form → resolve → draft → fill → handoff`, each node short-circuiting on `state["error"]` the way `resume_generator` does. **One deliberate exception:** `handoff` runs even on `error`, because a run that could not open a browser still owes the user a report saying so (a silent empty report is the failure mode this task set out to prevent).
- [x] Registry entry `job_applier` with `node_order` matching the graph exactly. The test is written over the WHOLE registry, and checks three things per agent: `node_order` equals the `send=True` graph's nodes, no edge runs backwards through that order, and the `send=False` graph is a subset. All seven agents pass — the three that looked drifted (`morning_briefing`, `stock_digest`, `application_tracker`) differ only by `deliver`, which is conditional on `send`.
- [x] Tests run the whole graph against fixtures with the browser and model monkeypatched; no network, no Chromium (proved by re-running the file with `playwright` blocked at the import hook).
- [x] **Carried debt from Task 4 discharged:** `resolve(..., default_country=...)` fed from `jobs.country`. On the captured Lever form this answers exactly the two questions that name no country ("...authorized to work in the country for which you are applying?" and the sponsorship one) and changes nothing else — the eligibility kinds stay in `BLOCKING_KINDS`, "North America" still goes blank, and `UNKNOWN`/`OTHER` still name no field.
- [x] **Browser lifetime:** closed on every failing path (a node raising, a node setting `error`, a page that will not load), deliberately LEFT OPEN on success — closing the window on a successful fill would throw away every field just typed, one keystroke before the only action that matters. `graph.release_browser(state)` is exported for whoever ends that session.

> **Task 8 review findings (all fixed in `cf36f1a`) — two are worth carrying forward.**
> - **A test was found ENFORCING a false claim — a new variant of this branch's dominant defect.**
>   `NOT_SUBMITTED_HEADLINE` said "The browser window is still open on this form, waiting for you",
>   and Task 8 ran it on the routes where the window had been closed or never opened, with
>   `test_every_failure_still_tells_the_user_nothing_was_submitted` asserting it across all EIGHT
>   failure routes. Ten findings on this branch have been prose asserting a property the code lacks;
>   this was the first where the guard *protected* the bug, so the usual remedy — pin the claim —
>   was itself what locked it in. `build_report` now takes `browser_open`. "Nothing was submitted"
>   stays unconditional, because that part is true everywhere.
> - **A wrong-value path in the merge was unpinned by a fixture accident.** Mutating away the
>   `r.source == "blank"` guard in `nodes/draft.py` left all 1732 tests green, because the three
>   captured fixtures contain ZERO questions the resolver answers AND drafting claims. On a real form
>   with a textarea the resolver matches to a profile field, the user's own value would be replaced
>   with `""`. Now killed by name: `test_a_profile_answer_is_never_overwritten_by_a_draft`.
> - The ONE-RULE scan now globs the whole package recursively, closing a hole Task 8 newly exposed:
>   `browser.py` — the one module holding a live `BrowserContext` — was reachable from a node and
>   unscanned. The weaker bespoke guard in the graph test file was deleted, not patched.
> - **KNOWN LIMIT recorded in `graph.py`:** a node that REPLACES the context in state orphans the old
>   one, since `release_browser` only reads `state["browser"]`. No node does today — **Task 9's
>   re-navigate step is the obvious way to hit it.**
> - **Not wired, deliberately:** `schema_greenhouse.parse_questions` / `form_url` /
>   `demographic_questions_raw` are called only from tests. Scraped rows carry no `absolute_url` or
>   questions payload, so the API route would need a live fetch at run time, and DOM discovery covers
>   all three boards from one path. `Question`, `is_eeo_label` and `_EEO_TERMS` ARE load-bearing.
> - **STILL UNMET, and it is Kayla's original ask:** `resume_path` has no producer. Nothing persists a
>   résumé PDF *path* (`compile_tex` returns bytes from a temp dir). **Task 10 must produce the file**
>   or the auto-attach she ruled on never happens.

---

## Task 9: Confirmation detection → upgrade the tracker row

**Files:** Modify `agents/application_tracker/store.py`, `server/routers/jobs.py`, `schema.sql`, `store_db.py`, Prisma mirror; Test: `tests/test_applier_confirm.py`

Phase A records applications optimistically on the modal's confirm. Greenhouse/Lever/Ashby all land on a confirmation page after a real submit, so the agent can verify it.

- [x] Add `applications.confirmed_at TEXT` (idempotent `_migrate` guard; `_migrate` runs BEFORE `executescript`; Prisma mirror; `db:check` must pass).
- [x] Detection matches confirmation text/URL per ATS; on match, stamp `confirmed_at`. **No match must never un-confirm or delete anything** — absence of evidence is not evidence.
- [x] UI shows confirmed vs optimistic in the tracker.
- [x] Tests: each ATS's confirmation fixture stamps the row; a non-confirmation page leaves it untouched; a second detection is idempotent.

> **Task 9 as built** (`agents/job_applier/confirm.py`; full report in
> `.superpowers/sdd/.../task-9-report.md`):
> - **The confirmation fixtures are SYNTHETIC and have never been compared against a real
>   post-submit page.** Capturing one requires actually submitting an application, which THE ONE RULE
>   forbids. The *negative* evidence is real: the detector is run over the three captured live apply
>   forms and required to refuse them. Positive matches prove design intent, not board behaviour.
> - **Detection reads VISIBLE TEXT, not raw HTML**, because two of the three captured *unsubmitted*
>   forms contain confirmation-shaped strings: `lever-form.html` ships
>   `.confirmation-message {text-align: center;}` in a `<style>` (plus a card headed "AU Clearance
>   Confirmation") and `ashby-form.html` embeds `"applicationSubmittedSuccessMessage":null` in a
>   `<script>`. `"confirmation" in html` is True on an empty Lever form.
> - A stamp needs **all four** of: a recognised ATS host; a past-tense completion phrase in visible
>   text; no failure/negation phrase (checked first — "was **not** submitted" contains a completion
>   phrase); and no `<input type="file">` left on the page. The URL is recorded (`url_hint`) and
>   decides nothing — every captured URL is pre-submit, so `/thanks` et al. are the one unverifiable
>   claim here. Ashby's success view is at the same path as its form, so a URL change is not required
>   either.
> - `mark_confirmed` is the only writer of the column: write-once (a second detection keeps the FIRST
>   timestamp), never clears, touches no other column. A non-match performs **no DB access at all**.
> - **Not wired, deliberately:** nothing calls `detect_confirmation` / `stamp_if_confirmed`. The human
>   submits *after* the graph exits, so there is nothing to detect at graph-exit time — Task 10 owns
>   the trigger. No re-navigate node was added (see Task 8's KNOWN LIMIT above).
> - **The production migration already ran**, not by hand: the 08:00 `launchd` jobscraper called
>   `store_db.init_db()` with the edited files on disk. Verified read-only as correct (nullable, no
>   default, all three existing rows NULL). `npx prisma generate` has NOT been run, so the live
>   Next.js client still ignores the column and every row renders "unverified" until it is.

> **Task 9 outcome (`f65d754`) — and a process failure worth more than the feature.**
>
> **A stamp requires ALL FOUR of:** a recognised ATS host (dot-boundary suffix match on
> `greenhouse.io` / `lever.co` / `ashbyhq.com`); a past-tense completion phrase in **visible** text;
> **no** failure/negation phrase (checked first); and **no `<input type=file>` left on the page**. The
> URL is recorded as `url_hint` and decides nothing.
>
> **The asymmetry that drove every choice:** a false negative just means no stamp and the optimistic
> row still shows. A false positive tells the user an application is submitted when it is not, so she
> stops chasing a job she never applied for. Conservative by construction.
>
> **The confirmation fixtures are SYNTHETIC and have never been compared against a real post-submit
> page** — capturing one requires actually submitting an application. Only the *refusals* are
> measured, against the three captured live apply forms. **The first real submission is what
> validates the phrase lists.** Do not let a later change imply these are captured fixtures.
>
> **THE PROCESS FAILURE: production was migrated by the scheduler, not by decision.** I told the
> implementer "I will decide when the production migration runs." `launchd`'s jobscraper called
> `store_db.init_db()` with the edited `schema.sql`/`store_db.py` on disk, and
> `data/control_center.db` now has `confirmed_at`. Verified read-only: nullable, no default, all
> three rows NULL, `applications` diffs column-for-column against a temp DB built from `schema.sql`
> — the landed state is the correct one. Corroborating evidence: `-wal` mtime is 08:30 while the
> `runs` table has NO row since 2026-08-01, i.e. a process ran `init_db()` and died before recording
> a run.
>
> **THE NEAR MISS, which is the real lesson.** The mutation harness rewrites `store_db.py` IN PLACE,
> and four of its mutations make that file mis-migrate this exact column (backfill / `NOT NULL
> DEFAULT ''` / wrong order / no guard). The launchd agents were loaded the whole time. Had the
> scraper fired inside one of those windows, **production would have been migrated by a mutant.** It
> did not — both observable fingerprints of those mutants are absent from the live schema.
>
> **STANDING RULE from here on:** mutation testing must never rewrite a file in place when a loaded
> `launchd` job imports it. Copy the repo (most agents on this branch did) or unload the agents
> first. This is the controller's failure, not the implementer's — I dispatched mutation testing on
> every task without ever naming this constraint.
>
> **Pre-existing drifts found while checking, unrelated to this task:** `jobs` and `master_resume`
> have migrated columns in a different *order* than `schema.sql` declares, and production's
> `resumes.job_id` is nullable where the script says `NOT NULL`.
>
> **NOT DONE:** `npx prisma generate` has not run, so the generated client has no `confirmedAt` and
> every row renders "unverified" regardless of the column. Needs a Next restart — Kayla's call.
>
---

## Task 10: UI entry point

**Files:** Modify `web-next/src/components/jobs/ApplyModal.tsx`, `web-next/src/lib/jobs.ts`

- [ ] For `greenhouse|lever|ashby`, offer **Autofill for me** beside the existing manual path; other ATSs keep Phase A's open-the-posting flow with a one-line note saying why.
- [ ] Streams node progress via the existing `RunStream`, then shows the handoff checklist.
- [ ] Must state before starting that it fills but does not submit.
- [ ] Verify: `npm run lint`, `npx tsc --noEmit`, `npm run check:jobs`.

> **Task 10 outcome (`17188a8`) — the entry point, plus a latent bug in the server path.**
>
> **Playwright's sync API is thread-affine.** `SyncBase._sync()` switches to a greenlet created
> by `sync_playwright().start()`, and greenlets cannot cross threads — a context made on one
> thread is *unusable* from another. Two consequences nobody had noticed: (a) reading the
> handed-over page from a FastAPI handler could never have worked, so the obvious shape of the
> confirmation trigger would have shipped a button that cannot succeed; (b) `server/runner.py`
> drives graphs with `astream`, and LangGraph runs sync nodes in the event loop's **default
> executor — a pool**, so `fetch_form` opening the context on one worker and `fill` typing into
> it from another is exactly the broken case. That is a pre-existing hazard in Tasks 1/6/8's
> server path, not something Task 10 introduced.
>
> **The fix is scoped, not global.** `agents/job_applier/session.py` owns ONE dedicated worker
> thread (a plain `Thread` + `Queue`: a pool's `.submit()` is rejected by the one-rule AST scan,
> and `max_workers=1` is a configuration promise where this needs a structural one) plus the
> registry of the parked window. `server/applier_run.py` runs the whole graph on that thread with
> the sync `graph.stream`, then queues the later page read onto the same thread. Bookkeeping is
> byte-identical to `runner._drive` (`runs` row, `node_events`, same events on
> `runner.manager`, publishes via `loop.call_soon_threadsafe`), so `RunStream` and
> `/runs/{id}/events` cannot tell the difference. **`server/runner.py` is untouched** — six
> working agents were not put at risk for this. Any FUTURE agent holding a thread-affine object
> across two sync nodes still has the hazard; documented in `ARCHITECTURE.md`.
>
> **The résumé path was the real gap.** `resume_pdf.pdf_path()` derives the path from the key
> `ensure_pdf` RETURNED, never by recomputing `cache_key()` — the two disagree on a shipped path
> (a tailored résumé with no LaTeX compiles the master's source and is keyed as the master), so
> recomputing names a file that was never written. `fill.py`'s attach-last / verified-by-filename
> machinery had been dead code because nothing produced a path.
>
> **Nothing is logged until the human says she submitted.** Starting a run records no
> application. "I pressed Submit" calls the EXISTING `/data/jobs/apply` (so Undo and the pinned
> PDF key behave identically) and then `/data/jobs/confirm-submission`, which validates ownership
> by exact `job_id` BEFORE reading anything, reads the parked page on the session thread, and
> calls Task 9's detector only on a successful read. A failed read performs **no database access**;
> a read that does not vouch leaves the row **identical**. Neither is rendered as a failure, and a
> test forbids the string "not submitted" in that wording — it would be the same false claim in
> the opposite direction.
>
> **TWO MUTANTS SURVIVED FIRST, AND BOTH WERE BAD TESTS.** The errored-run test nulled the
> browser handle in its own fixture, so the "context AND no error" condition was never exercised
> and a driver that parked a window over a failed run passed. The single-thread test compared
> `thread.name`, which every worker shares, so a fresh-thread-per-call mutant passed. Both now
> assert the property they claim. All eleven mutants ran against an `rsync` copy — never in place,
> per Task 9's standing rule.
>
> **1889 passed** (1817 at `ae25350`). lint / tsc / check:jobs / db:check clean. DB fingerprint
> unchanged. No browser launched, no ATS contacted, no agent run, `npm run build` not run.
>
> **STILL NOT DONE / FOLLOW-UPS:** `npx prisma generate` still has not run, so the tracker renders
> every row "unverified" regardless of the column (Kayla's call, needs a Next restart). The
> single-thread driver has never been run against a real browser — "sync `.stream` yields the same
> `(mode, data)` shapes as `astream`" is inferred from symmetry, and the first real run is what
> validates it. Confirmation phrase lists remain validated only in the negative direction. A
> report is lost if the server restarts before the modal fetches it (rendered text survives in run
> history). Closing the modal mid-run leaves the run going with no way back to the checklist. Only
> one assisted-apply window is kept at a time, by design. The worker is a daemon thread, so a
> server shutdown can still leave Chromium running — `/data/jobs/assisted-apply/close` is the
> manual escape hatch.

---

## Done criteria

- [ ] `.venv/bin/python -m pytest tests/` — all pass, and the count exceeds whatever the branch
      started at (262 at plan time, 267 after Task 1). Re-read it; do not trust this line.
- [ ] No test contacts a live ATS or launches Chromium.
- [ ] A source scan proves no submit control is ever clicked.
- [ ] `npm run db:check` — no drift; migration verified on a copy of the live DB.
- [ ] Resolver is pure (asserted by test) and never fills an identity or work-authorization field absent from the profile.
- [ ] With the real profile still incomplete, a dry run reports the missing required fields as blocking rather than proceeding.
- [ ] DB fingerprint unchanged across the whole plan.
