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

- [ ] After typing, **read the value back** and confirm it landed. React-controlled inputs frequently swallow programmatic input; a fill that silently did nothing is worse than one that failed loudly.
- [ ] A field that will not accept its value is reported as `blank` with the reason, not retried indefinitely.
- [ ] **Assert no submit control is ever clicked** — a test that scans the executor's source for a click on anything matching `submit|apply now|send application` and fails if present.

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

---

## Task 7: Handoff report

**Files:** Create `agents/job_applier/nodes/handoff.py`; Test: `tests/test_applier_graph.py`

- [ ] Output groups answers into **filled from profile / AI-drafted, review these / left blank**, with required-and-blank listed first as blocking.
- [ ] Says plainly that nothing was submitted and the browser is left open.
- [ ] Tests: a blocking required field appears first; drafted answers are marked; the message never claims to have submitted.

---

## Task 8: Graph + registry

**Files:** Create `state.py`, `graph.py`, `nodes/*`; Modify: `agents/registry.py`; Test: `tests/test_applier_graph.py`

- [ ] Chain: `load_profile → fetch_form → resolve → draft → fill → handoff`, each node short-circuiting on `state["error"]` the way `resume_generator` does.
- [ ] Registry entry `job_applier` with `node_order` matching the graph exactly (Phase A found two registries drifted from their graphs — add a test asserting they match).
- [ ] Tests run the whole graph against fixtures with the browser and model monkeypatched; no network, no Chromium.

---

## Task 9: Confirmation detection → upgrade the tracker row

**Files:** Modify `agents/application_tracker/store.py`, `server/routers/jobs.py`, `schema.sql`, `store_db.py`, Prisma mirror; Test: `tests/test_applier_confirm.py`

Phase A records applications optimistically on the modal's confirm. Greenhouse/Lever/Ashby all land on a confirmation page after a real submit, so the agent can verify it.

- [ ] Add `applications.confirmed_at TEXT` (idempotent `_migrate` guard; `_migrate` runs BEFORE `executescript`; Prisma mirror; `db:check` must pass).
- [ ] Detection matches confirmation text/URL per ATS; on match, stamp `confirmed_at`. **No match must never un-confirm or delete anything** — absence of evidence is not evidence.
- [ ] UI shows confirmed vs optimistic in the tracker.
- [ ] Tests: each ATS's confirmation fixture stamps the row; a non-confirmation page leaves it untouched; a second detection is idempotent.

---

## Task 10: UI entry point

**Files:** Modify `web-next/src/components/jobs/ApplyModal.tsx`, `web-next/src/lib/jobs.ts`

- [ ] For `greenhouse|lever|ashby`, offer **Autofill for me** beside the existing manual path; other ATSs keep Phase A's open-the-posting flow with a one-line note saying why.
- [ ] Streams node progress via the existing `RunStream`, then shows the handoff checklist.
- [ ] Must state before starting that it fills but does not submit.
- [ ] Verify: `npm run lint`, `npx tsc --noEmit`, `npm run check:jobs`.

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
