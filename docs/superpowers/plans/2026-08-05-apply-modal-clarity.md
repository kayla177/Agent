# Apply Modal Clarity + Charcoal Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the jobs-tab Apply modal legible and unambiguous, and lift the app's surface ramp from pure black to charcoal without dropping any text below an accessible contrast floor.

**Architecture:** Two independently shippable phases. Phase 1 (Tasks 1–4) touches only `ApplyModal.tsx` and modal-scoped CSS — it ships and is useful on its own. Phase 2 (Tasks 5–7) is the app-wide theme change, gated behind a new contrast-guard script written *first* so the token flip has a test that fails before it and passes after. Frontend behaviour is verified the way this repo already does it: Python source-scans in `tests/test_applier_ui.py` and plain-node checks in `web-next/scripts/*.mjs`.

**Tech Stack:** Next.js 16.2.10 (React 19), plain CSS in one stylesheet (`web-next/src/app/globals.css`), pytest for source-scan guards, plain-node `assert` scripts run via npm. **There is no JS test framework in this project and adding one is out of scope** — this is a standing constraint, stated in `web-next/scripts/check-jobs-filters.mjs:11`.

---

## Global Constraints

Every task's requirements implicitly include this section.

**THE ONE RULE: no code path may ever submit an application.** `tests/test_applier_ui.py` source-scans `ApplyModal.tsx` to enforce it. All ten assertions must stay true at every commit:

1. `"It does not submit it"` appears **before** `"Open the form & autofill it"` in **file order** (:1040–1042)
2. `"It fills the form."` present (:1043)
3. `"not submit"` appears **≥ 2** times (:1045)
4. `"Work-authorization"` and `"self-identification"` present (:1052–1053)
5. No `<form`, no `type="submit"`, no `formAction`, no `onSubmit` (:1104–1107)
6. No handler named `submitForm`, `doSubmit`, `submitApplication`, `autoSubmit` (:1109–1110)
7. Every `fetch()` target inside the `_ALLOWED_FETCHES` allowlist (:1080)
8. Every `window.open()` target inside its allowlist (:1092)
9. `showOutput={false}` and `handoffGroup(report` present; `render_text` and `dangerouslySetInnerHTML` absent (:1117–1120)
10. `autofillUnavailableNote` keeps `${board}` **and** the phrase `fill it in yourself` (:1184–1190)

Note on #5: the string `<form` must not appear. Prose like `"this form"` is fine — it has no `<`. Radio inputs are permitted; only `type="submit"` is banned.

Note on #3: the pre-run copy supplies one `"not submit"` and the untouched in-run banner supplies the second. **Do not remove the pre-run occurrence.**

**Other standing constraints:**

- The single footer primary button keeps `.modal-actions .primary`'s existing Mars orange `#e07a4a` with `#04122b` text (6.26:1). Do not change it to blue.
- `autofillUnavailableNote()`'s returned string is unchanged. Only its presentation changes.
- The dashboard hero stays black. `.hero` (`globals.css:307`) hardcodes `#000` in its own `background` shorthand and never reads `--bg`, so **no edit is required** to achieve this. Do not "tidy" it into `var(--bg)`.
- The in-run and post-run branches of `ApplyModal.tsx` (`RunStream`, `Handoff`, "I pressed Submit — log it & verify", "Close the browser window") are **out of scope**. Do not restructure them.
- `.venv/bin/python -m pytest` **prints no summary line** (`addopts = "-q"`). Count with `--junitxml` and read the exit code. Baseline is **1944 passed**.
- `uv` is installed but this repo's Python is `.venv/bin/python`. Use the absolute venv path.
- The `:3000` server runs `npm run start`, a **production build** — a restart alone changes nothing. Rebuild first.

---

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `web-next/src/components/jobs/ApplyModal.tsx` | Modify. Pre-run branch only: title, ATS pill, labelled picker, radio path group, single footer action, unsupported-board note. | 1 |
| `web-next/src/app/globals.css` | Modify. `.apply-modal h2` reset, base form-control rule, `.apply-path*` + `.apply-unavailable` rules (Phase 1); `:root` tokens, `body` background, hardcoded darks (Phase 2). | 1, 2 |
| `tests/test_applier_ui.py` | Modify. Add source-scan guards for the new structure alongside the existing ten. | 1 |
| `web-next/scripts/check-contrast.mjs` | **Create.** Asserts the surface ramp in `globals.css` keeps every text/background pair above the contrast floor. | 2 |
| `web-next/package.json` | Modify. Add `"check:contrast"` script. | 2 |

---

# PHASE 1 — Apply modal clarity

Ships independently. No theme change; the modal is on black throughout Phase 1.

---

### Task 1: Readable dialog title + ATS pill

The global `h2` at `globals.css:79` is a decorative rule for section labels (`letter-spacing: 5px; text-transform: lowercase; color: var(--muted)`). `.apply-modal h2` (:481) overrides only `margin` and `font-size`, so a job title renders lowercase, grey and widely tracked. Add the missing resets. Also surface the board as a pill so it is visible *why* autofill is or is not offered.

**Files:**
- Modify: `web-next/src/app/globals.css:481`
- Modify: `web-next/src/components/jobs/ApplyModal.tsx:315-316`
- Test: `tests/test_applier_ui.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the CSS class `.apply-subhead` (used by no later task); the `<h2>`/company markup that Task 3 leaves untouched above the radio group. The board pill reuses the existing `.job-badge.src` (`globals.css:147`) rather than adding a class.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_applier_ui.py`:

```python
def test_the_dialog_title_is_not_styled_like_a_decorative_section_label():
    """globals.css defines a global `h2` for section labels — lowercase, muted,
    5px tracking. That is right for the word "jobs" and wrong for
    "Apply — U.S. Public Policy and AI Innovation Intern (Fall 2026)".
    `.apply-modal h2` must reset all three, or the leak is invisible in review
    because the rule it inherits from lives 400 lines away."""
    css = (WEB / "app" / "globals.css").read_text()
    start = css.index(".apply-modal h2")
    block = css[start : css.index("}", start)]
    assert "text-transform: none" in block, "the job title must not be lowercased"
    assert "letter-spacing: normal" in block, "5px tracking belongs on section labels"
    assert "color: var(--text)" in block, "a dialog title is not muted secondary text"


def test_the_modal_shows_which_board_the_posting_is_on():
    """The board decides whether autofill is offered at all. Showing it means the
    absence of the autofill option is explained by something visible."""
    assert "job-badge src" in MODAL_SOURCE, "reuse the board pill the list already uses"
    assert "{jobAts}" in MODAL_SOURCE
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/test_applier_ui.py -k "decorative_section_label or which_board" -v
```

Expected: **2 failed**. The first on `text-transform: none` not in the block; the second on `job-badge src` not in the source.

- [ ] **Step 3: Fix the CSS**

Replace `web-next/src/app/globals.css:481`:

```css
.apply-modal h2 { margin: 0 0 0.25rem; font-size: 1.05rem; }
```

with:

```css
/* A dialog title is a sentence, not a section label. The global `h2` (see the
   typography block near the top of this file) is decorative — lowercase, muted,
   5px tracking — which is right for "jobs" and unreadable for a job title. */
.apply-modal h2 {
  margin: 0 0 0.25rem; font-size: 1.05rem;
  letter-spacing: normal; text-transform: none; color: var(--text);
}
.apply-modal .apply-subhead { display: flex; align-items: center; gap: .5rem; margin: 0 0 .25rem; }
```

- [ ] **Step 4: Add the pill to the markup**

Replace `ApplyModal.tsx:315-316`:

```tsx
        <h2>Apply — {jobTitle}</h2>
        <p className="muted">{jobCompany}</p>
```

with:

```tsx
        <h2>Apply — {jobTitle}</h2>
        <p className="muted apply-subhead">
          {jobCompany}
          <span className="job-badge src">{jobAts}</span>
        </p>
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -k "decorative_section_label or which_board" -v
```

Expected: **2 passed**.

- [ ] **Step 6: Run the full suite and the type/lint gates**

```bash
.venv/bin/python -m pytest tests/ --junitxml=/tmp/j.xml -q; echo "exit=$?"
python3 -c "import xml.etree.ElementTree as E;r=E.parse('/tmp/j.xml').getroot();s=r if r.tag=='testsuite' else r[0];print(s.get('tests'),'tests',s.get('failures'),'failures')"
cd web-next && npx tsc --noEmit && npx eslint src/components/jobs/ApplyModal.tsx
```

Expected: exit 0, **1946 tests, 0 failures**, tsc and eslint clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/globals.css web-next/src/components/jobs/ApplyModal.tsx tests/test_applier_ui.py
git commit -m "fix(jobs): the apply dialog title was wearing a section label's styling

globals.css defines a decorative global h2 -- lowercase, muted, 5px tracking --
for section headings like \"jobs\". .apply-modal h2 overrode only margin and
font-size, so a full job title inherited all three and rendered as unreadable
grey lowercase. Resets the transform, tracking and colour, scoped to the modal so
every decorative heading elsewhere is untouched. Also surfaces the board as a
pill, so the absence of the autofill option has a visible cause."
```

---

### Task 2: A résumé picker that looks like a control

The stylesheet has **no base `select` rule**. Five call sites each style their own — `.form-grid select` (:106), `.job-filters select` (:160), `.settings-form select` (:214), `.pool-add-head select` (:257), `.generate-row select` (:288) — and `.apply-modal select` (:483) sets only `width: 100%`, so it renders at the user-agent default: no border, no background, no padding. Add the floor that was missing.

**Files:**
- Modify: `web-next/src/app/globals.css` (insert base rule before line 106; edit :483)
- Modify: `web-next/src/components/jobs/ApplyModal.tsx:322-339`
- Test: `tests/test_applier_ui.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: the CSS class `.apply-field-label`, used by Task 3's radio-group heading.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_applier_ui.py`:

```python
def test_form_controls_have_a_base_style_so_none_can_render_unstyled():
    """The apply modal's <select> looked like plain text because the stylesheet had
    NO base rule for form controls — five call sites each styled their own, and
    this one was missed. A per-site pattern has no floor: every new control is one
    omission away from invisible. The base rule must come BEFORE the local rules,
    because `input[type="text"]` (0,1,1) TIES with `.settings-form input` (0,1,1)
    and source order breaks a tie."""
    css = (WEB / "app" / "globals.css").read_text()
    base = css.index("/* BASE FORM CONTROLS")
    assert base < css.index(".form-grid input"), "base rule must precede local overrides"
    assert base < css.index(".settings-form input")
    block = css[base : css.index("}", base)]
    for prop in ("background:", "border:", "color:", "padding:"):
        assert prop in block, f"a control with no {prop} is invisible on a dark panel"


def test_the_resume_picker_is_labelled_as_a_control():
    assert "apply-field-label" in MODAL_SOURCE
    assert "Résumé to use" in MODAL_SOURCE
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -k "base_style or picker_is_labelled" -v
```

Expected: **2 failed** — `ValueError: substring not found` on `/* BASE FORM CONTROLS`, and `apply-field-label` missing.

- [ ] **Step 3: Add the base rule**

Insert into `web-next/src/app/globals.css` immediately **before** the `.form-grid input, .form-grid select, table.apps select {` rule at line 106:

```css
/* BASE FORM CONTROLS — the floor. Every control gets a visible box even when
   nobody wrote a rule for its call site; the apply modal's <select> rendered at
   the user-agent default (no border, no background) for exactly that reason.
   This MUST stay above the per-site rules below: `select` is a type selector
   (0,0,1) and loses to `.form-grid select` (0,1,1) on specificity, but
   `input[type="text"]` is (0,1,1) and TIES with `.settings-form input`, where
   source order is what decides. */
select,
input[type="text"], input[type="date"], input[type="number"],
textarea {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 10px; padding: .5rem .6rem; font: inherit;
}
```

- [ ] **Step 4: Add the label style and the picker markup**

Add near the other `.apply-modal` rules (after line 483):

```css
.apply-field-label {
  display: block; margin: 1rem 0 .35rem; font-size: .72rem; letter-spacing: .9px;
  text-transform: uppercase; color: var(--muted);
}
.apply-modal select { width: 100%; }
```

Then in `ApplyModal.tsx`, replace the `<label>` wrapper (lines 322-339) with:

```tsx
            <label className="apply-field-label" htmlFor="apply-resume">Résumé to use</label>
            <select
              id="apply-resume"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              disabled={busy}
            >
              <option value="" disabled={!hasMaster}>
                {hasMaster ? "master résumé" : "master résumé (not set)"}
              </option>
              {tailored ? (
                <option value={tailored.job_id}>tailored for this job ★</option>
              ) : null}
              {resumes
                .filter((r) => r.job_id !== jobId)
                .map((r) => (
                  <option key={r.job_id} value={r.job_id}>
                    reuse: {r.role || "(untitled)"}{r.company ? ` @ ${r.company}` : ""}
                  </option>
                ))}
            </select>
```

Note: the old markup nested the `<select>` inside `<label>`, which is why removing the wrapper needs the explicit `htmlFor`/`id` pair to keep the label associated.

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -k "base_style or picker_is_labelled" -v
```

Expected: **2 passed**.

- [ ] **Step 6: Confirm no other page changed appearance**

```bash
cd web-next && npx tsc --noEmit && npx eslint src && npm run check:jobs
```

Expected: clean, `check:jobs` 14/14. Then **manually** load `/settings`, `/resume` and `/jobs` and confirm the existing selects and inputs look unchanged — the base rule is a floor, and if anything shifted, the ordering in Step 3 is wrong.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/globals.css web-next/src/components/jobs/ApplyModal.tsx tests/test_applier_ui.py
git commit -m "fix(jobs): the résumé picker looked like text because selects had no base style

Five call sites each styled their own select; .apply-modal select set only
width:100%, so it rendered at the user-agent default -- no border, no background,
no padding -- and read as body text on a dark panel. Kayla found it was
interactive by clicking it by accident.

The per-site pattern IS the defect: it has no floor, so every new control is one
omission away from invisible. Adds a base rule for select/input/textarea, placed
above the local rules because input[type=\"text\"] ties with .settings-form input
on specificity and source order breaks the tie. Also gives the picker a real
uppercase label instead of a bare word."
```

---

### Task 3: Layout C — pick a path, then one primary button

Two `button.primary`s currently compete (autofill inside `.apply-autofill`, manual in `.modal-actions`), and the sentence explaining the footer button sits *after* it. Replace with a radio group and exactly one action whose label follows the selection.

**Files:**
- Modify: `web-next/src/components/jobs/ApplyModal.tsx` (add state near :51; replace :351-385)
- Modify: `web-next/src/app/globals.css` (replace the `.apply-autofill` rules)
- Test: `tests/test_applier_ui.py`

**Interfaces:**
- Consumes: `.apply-field-label` from Task 2.
- Produces: state `path: "autofill" | "manual"`; CSS classes `.apply-paths`, `.apply-path`, `.apply-path.on`, `.apply-never`. Task 4 renders inside the same footer.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_applier_ui.py`:

```python
def test_exactly_one_primary_button_in_the_pre_run_branch():
    """Two `primary` buttons is no hierarchy. The pre-run branch ends where the
    in-run branch begins, at the banner that repeats the guarantee."""
    pre = MODAL_SOURCE[: MODAL_SOURCE.index("The agent is filling this form")]
    assert pre.count('className="primary"') == 1, "one action, one primary"


def test_the_path_choice_is_a_radiogroup_and_not_a_form():
    assert 'role="radiogroup"' in MODAL_SOURCE
    assert 'type="radio"' in MODAL_SOURCE
    # THE ONE RULE: a <form> would make Enter submit. Re-asserted HERE because
    # this task is the one that introduces inputs.
    assert "<form" not in MODAL_SOURCE
    assert 'type="submit"' not in MODAL_SOURCE


def test_the_guarantee_still_precedes_the_button_after_the_restructure():
    """Duplicates the existing ordering assertion on purpose. Layout C satisfies it
    BY CONSTRUCTION -- the radio descriptions sit above the footer -- and this test
    is what makes a future revision that moves the action upward fail loudly."""
    assert MODAL_SOURCE.index("It does not submit it") < MODAL_SOURCE.index(
        "Open the form & autofill it"
    )
    assert MODAL_SOURCE.count("not submit") >= 2
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -k "exactly_one_primary or radiogroup or guarantee_still_precedes" -v
```

Expected: **2 failed, 1 passed** — the primary count is 2, `role="radiogroup"` is absent, and the ordering test already passes (it must keep passing).

- [ ] **Step 3: Add the path state**

In `ApplyModal.tsx`, after the `const [choice, setChoice] = ...` line (:51), add:

```tsx
  // Which of the two equal paths is selected. Kayla's ruling: these are equal
  // choices, not a primary with a fallback — she uses both depending on the
  // posting. Defaults to autofill where the board supports it.
  const [path, setPath] = useState<"autofill" | "manual">("autofill");
```

- [ ] **Step 4: Replace the autofill block and the actions**

Replace `ApplyModal.tsx:351-385` (from `{canAutofill ? (` through the closing `</p>` after `.modal-actions`) with:

```tsx
            {canAutofill ? (
              <>
                <span className="apply-field-label">How do you want to apply?</span>
                <div className="apply-paths" role="radiogroup" aria-label="How to apply">
                  <label className={`apply-path ${path === "autofill" ? "on" : ""}`}>
                    <input
                      type="radio"
                      name="apply-path"
                      checked={path === "autofill"}
                      onChange={() => setPath("autofill")}
                      disabled={busy}
                    />
                    <span>
                      <strong>Let the agent fill the form</strong>
                      {/* Said BEFORE anything starts, not only in the report
                          afterwards. A user who learns this after a browser
                          window has appeared has already been surprised by it —
                          which is why a test pins this sentence ABOVE the
                          button that starts the run. */}
                      <span className="muted small">
                        Opens this {jobAts} form in a browser window on your screen and fills
                        what it can from your profile, attaching the résumé above last.
                      </span>
                      <span className="apply-never">
                        It fills the form. It does not submit it — and it never will.
                        Nothing is sent until you read every field yourself and press Submit
                        in that window. Work-authorization and self-identification questions
                        are always left for you.
                      </span>
                    </span>
                  </label>
                  <label className={`apply-path ${path === "manual" ? "on" : ""}`}>
                    <input
                      type="radio"
                      name="apply-path"
                      checked={path === "manual"}
                      onChange={() => setPath("manual")}
                      disabled={busy}
                    />
                    <span>
                      <strong>I&rsquo;ll fill it in myself</strong>
                      <span className="muted small">
                        Opens the posting and your résumé PDF in new tabs, and logs the
                        application. You can undo it.
                      </span>
                    </span>
                  </label>
                </div>
              </>
            ) : (
              <p className="muted small">{autofillUnavailableNote(jobAts)}</p>
            )}

            {error ? <p className="banner err">{error}</p> : null}

            <div className="modal-actions">
              <button onClick={dismiss} disabled={busy}>Cancel</button>
              <button
                className="primary"
                onClick={path === "autofill" && canAutofill ? startAutofill : confirm}
                disabled={busy || (path === "manual" && !hasMaster && !choice)}
              >
                {busy
                  ? path === "autofill" && canAutofill ? "Starting…" : "Recording…"
                  : path === "autofill" && canAutofill
                    ? "Open the form & autofill it"
                    : "Download résumé, open posting & log it"}
              </button>
            </div>
```

The `&& canAutofill` guard matters: `path` defaults to `"autofill"`, so on an unsupported board without it the footer button would call `startAutofill` for a form the agent cannot read. Task 4 makes that state visible; this makes it correct.

- [ ] **Step 5: Replace the CSS**

Replace the `.apply-autofill` rules (the three lines near the end of the assisted-apply block) with:

```css
.apply-paths { display: flex; flex-direction: column; gap: .5rem; }
.apply-path {
  display: flex; gap: .6rem; align-items: flex-start; cursor: pointer;
  padding: .6rem .7rem; border: 1px solid var(--border-soft); border-radius: 10px;
  background: var(--panel-2);
}
.apply-path.on { border-color: var(--accent); background: rgba(74, 144, 255, .09); }
.apply-path input { margin: .25rem 0 0; accent-color: var(--accent); flex: none; }
.apply-path strong { display: block; margin-bottom: .15rem; }
.apply-path .muted.small { display: block; margin-top: 0; }
/* The guarantee. Green because it is reassurance, not a warning — and never
   colour ALONE: the sentence says it in words too. */
.apply-never { display: block; margin-top: .35rem; font-size: .8rem; color: var(--green); }
```

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -v
```

Expected: all pass, including the ten pre-existing THE ONE RULE assertions.

- [ ] **Step 7: Full gates**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/ --junitxml=/tmp/j.xml -q; echo "exit=$?"
cd web-next && npx tsc --noEmit && npx eslint src
```

Expected: exit 0, **1951 tests, 0 failures**, tsc and eslint clean.

- [ ] **Step 8: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/jobs/ApplyModal.tsx web-next/src/app/globals.css tests/test_applier_ui.py
git commit -m "feat(jobs): one action in the apply modal, chosen from two equal paths

Two \`primary\` buttons competed for the same slot -- \"Open the form & autofill
it\" inside the autofill card and \"Download résumé, open posting & log it\" in the
footer -- so neither read as the action. Worse, the sentence explaining the footer
button sat AFTER it in the DOM: you met the button before its description.

Kayla's ruling is that the two paths are EQUAL, not primary-and-fallback, so they
become a radio group with one footer button whose label follows the selection.

The guarantee still precedes the button that starts the run -- by construction,
because the radio descriptions sit above the footer, and now with its own test so
a later revision that moves the action upward fails loudly instead of quietly
disarming a THE ONE RULE guard."
```

---

### Task 4: The unsupported-board state

On `smartrecruiters` and every other non-autofill board the labelled card collapses to one unheaded grey sentence, leaving a limitation floating with nothing to attach it to. Give it a heading and a border, place it **below** the action, and render no dead control.

**Files:**
- Modify: `web-next/src/components/jobs/ApplyModal.tsx` (the `: (` branch from Task 3, and after `.modal-actions`)
- Modify: `web-next/src/app/globals.css`
- Test: `tests/test_applier_ui.py`

**Interfaces:**
- Consumes: `path` state and the footer from Task 3.
- Produces: CSS class `.apply-unavailable`. Nothing later depends on it.

- [ ] **Step 1: Write the failing test**

```python
def test_an_unsupported_board_gets_an_explained_limit_not_a_dead_button():
    """No disabled autofill control: a dead button invites a click and answers
    nothing. The limit is explained instead, and the note keeps the wording
    `autofillUnavailableNote` is pinned to (see the ${board} / "fill it in
    yourself" test)."""
    assert "apply-unavailable" in MODAL_SOURCE
    assert "Autofill isn&rsquo;t available for this posting" in MODAL_SOURCE
    assert "autofillUnavailableNote(jobAts)" in MODAL_SOURCE
    # The note sits BELOW the action it qualifies.
    assert MODAL_SOURCE.index("modal-actions") < MODAL_SOURCE.index("apply-unavailable")
    assert "disabled autofill" not in MODAL_SOURCE
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -k "unsupported_board_gets_an_explained" -v
```

Expected: **1 failed** on `apply-unavailable` missing.

- [ ] **Step 3: Move the note below the actions**

In the Task 3 markup, change the `canAutofill` else-branch from

```tsx
            ) : (
              <p className="muted small">{autofillUnavailableNote(jobAts)}</p>
            )}
```

to

```tsx
            ) : null}
```

and insert immediately **after** the closing `</div>` of `.modal-actions`:

```tsx
            {!canAutofill ? (
              <div className="apply-unavailable">
                <strong>Autofill isn&rsquo;t available for this posting</strong>
                <span className="muted small">{autofillUnavailableNote(jobAts)}</span>
              </div>
            ) : null}
```

- [ ] **Step 4: Add the CSS**

```css
.apply-unavailable {
  margin-top: .75rem; padding: .6rem .75rem; border-radius: 10px;
  border: 1px dashed var(--border); background: var(--panel);
}
.apply-unavailable strong { display: block; margin-bottom: .15rem; }
.apply-unavailable .muted.small { display: block; margin-top: 0; }
```

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_applier_ui.py -v
```

Expected: all pass.

- [ ] **Step 6: Verify both states in the real UI**

```bash
cd /Users/kayla.li/.superset/Agent
kill $(lsof -ti tcp:8001) 2>/dev/null; nohup .venv/bin/python -m server > /tmp/agent-server.log 2>&1 &
cd web-next && cp -R .next /tmp/next-backup && kill $(lsof -ti tcp:3000) 2>/dev/null
npm run build && nohup npm run start > /tmp/next-server.log 2>&1 &
```

Then on `/jobs`, open Apply on **one greenhouse or lever posting** (radio group, one orange button, label follows the selection) and **one smartrecruiters posting** (no radio group, manual action is the only button, dashed note below it naming the board). Confirm the guarantee sentence is visible before you touch anything.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/jobs/ApplyModal.tsx web-next/src/app/globals.css tests/test_applier_ui.py
git commit -m "fix(jobs): explain the autofill limit instead of orphaning it

On smartrecruiters -- and every other board the applier cannot read -- the labelled
autofill card collapsed to a single unheaded grey sentence sitting above the
actions, attached to nothing. It now sits BELOW the action it qualifies, with a
heading and a border, so it reads as an explained limitation rather than stray
text. No disabled control: a dead button invites a click and answers nothing.

The note's wording is unchanged -- autofillUnavailableNote() is pinned to name the
board and to say \"fill it in yourself\" -- only its presentation moved."
```

---

# PHASE 2 — Charcoal theme

Ships independently of Phase 1. **Task 5 must land before Task 6**: it is the test that fails before the token flip and passes after.

---

### Task 5: A contrast guard, before the theme moves

Nothing in this repo tests `globals.css` today. Measured, `--muted` on the modal falls from 5.87:1 to **4.68:1** on charcoal — clearing WCAG AA (4.5:1) by 0.18, in a modal that is mostly `.muted.small` at 12.8px. Write the guard first so the flip has a real red-to-green cycle.

**Policy:** the floor is **5.0:1**, not 4.5:1. AA is 4.5; the extra 0.5 is deliberate headroom so a future token nudge has to be a decision rather than an accident. State that in the file — a reader who thinks 5.0 is the standard will "correct" it.

**Files:**
- Create: `web-next/scripts/check-contrast.mjs`
- Modify: `web-next/package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `npm run check:contrast`. Task 6 runs it; Task 7 keeps it green.

- [ ] **Step 1: Write the check**

Create `web-next/scripts/check-contrast.mjs`:

```js
#!/usr/bin/env node
// Contrast guard for the surface ramp.
//
// The theme moved from pure black to charcoal, which LOWERS every text contrast
// ratio at once — the background got closer to the text, not further from it.
// Measured before the move, --muted inside the modal sat at 5.87:1; on charcoal
// the same token gives 4.68:1, which passes WCAG AA (4.5:1) by 0.18 in a modal
// that is mostly 12.8px muted text. That is not a margin, it is a coincidence.
//
// FLOOR is 5.0, NOT 4.5. AA is 4.5; the extra 0.5 is deliberate headroom so the
// next token nudge has to be a decision instead of an accident. Do not "correct"
// this to 4.5 — it is a project policy, not a standard.
//
// There is no JS test framework in this project (adding one is out of scope), so
// this follows the plain-node precedent of check-schema-drift.mjs.
//
// Run: npm run check:contrast
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const FLOOR = 5.0;
const CSS = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");

// The ramp, mirrored from globals.css. Every value is asserted to appear in the
// stylesheet below, so this file cannot silently drift from it.
const TOKEN = {
  "--bg": "#14171d",
  "--panel": "#1c2027",
  "--panel-2": "#22262f",
  "--muted": "#98a0b2",
  "--text": "#eef1f6",
};
const MODAL_BG = "#252a33";

function luminance(hex) {
  const h = hex.replace("#", "");
  const parts = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const [r, g, b] = parts.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

let passed = 0;
let failed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    failed += 1;
    console.error(`✗ ${name}\n  ${err.message}`);
  }
}

// 1. The stylesheet really holds these values.
for (const [token, value] of Object.entries(TOKEN)) {
  check(`${token} is ${value} in globals.css`, () => {
    assert.ok(
      CSS.includes(`${token}: ${value}`),
      `globals.css does not declare "${token}: ${value}" — this script has drifted from the theme`,
    );
  });
}
check(`.modal background is ${MODAL_BG}`, () => {
  assert.ok(CSS.includes(MODAL_BG), `globals.css does not mention ${MODAL_BG}`);
});

// 2. The body honours the token instead of hardcoding a colour. --bg was dead
//    weight before this: body set `background-color: #000` directly, so changing
//    the token changed nothing at all.
check("body background uses var(--bg), not a literal", () => {
  const body = CSS.slice(CSS.indexOf("\nbody {"), CSS.indexOf("\na {"));
  assert.ok(body.includes("var(--bg)"), "body must read --bg");
  assert.ok(!body.includes("#000"), "body must not hardcode black");
});

// 3. Every text-on-surface pair clears the floor.
const PAIRS = [
  ["--muted on page", TOKEN["--muted"], TOKEN["--bg"]],
  ["--muted on panel", TOKEN["--muted"], TOKEN["--panel"]],
  ["--muted on panel-2", TOKEN["--muted"], TOKEN["--panel-2"]],
  ["--muted in modal", TOKEN["--muted"], MODAL_BG],
  ["--text on page", TOKEN["--text"], TOKEN["--bg"]],
  ["--text in modal", TOKEN["--text"], MODAL_BG],
];
for (const [name, fg, bg] of PAIRS) {
  check(`${name} >= ${FLOOR}:1`, () => {
    const r = ratio(fg, bg);
    assert.ok(
      r >= FLOOR,
      `${name} is ${r.toFixed(2)}:1, below the ${FLOOR}:1 floor (${fg} on ${bg})`,
    );
  });
}

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Register the script**

In `web-next/package.json`, add to `"scripts"` after `"check:jobs"`:

```json
    "check:contrast": "node scripts/check-contrast.mjs",
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
cd web-next && npm run check:contrast
```

Expected: **exit 1**, `7 passed, 6 failed`. The six failures are `--bg`, `--panel`, `--panel-2`, `--muted`, `.modal background is #252a33`, and `body background uses var(--bg)` — the stylesheet still says `#000000`, `rgba(255,255,255,0.04)`, `rgba(255,255,255,0.06)`, `#8b93a6`, `#14161c`, and `background-color: #000`. `--text: #eef1f6` already matches, so it passes. The six contrast pairs also pass, because they are computed from the constants in this file rather than parsed from CSS — the point of this run is that the *stylesheet* does not match them yet, which is what Task 6 fixes.

- [ ] **Step 4: Commit the failing guard**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/scripts/check-contrast.mjs web-next/package.json
git commit -m "test(web): a contrast guard for the surface ramp, red before the theme moves

Nothing tested globals.css. Lifting the page off pure black lowers EVERY text
contrast ratio at once, and --muted inside the modal lands at 4.68:1 -- passing AA
by 0.18, in a modal that is mostly 12.8px muted text. That is a coincidence, not a
margin.

The floor here is 5.0, not the 4.5 the standard requires, so the next token nudge
has to be a decision rather than an accident. Committed RED: it fails until the
tokens actually move."
```

---

### Task 6: Flip the tokens to charcoal

**Files:**
- Modify: `web-next/src/app/globals.css` (`:root` :3-10; `body` :38; `.modal` :477-480)

**Interfaces:**
- Consumes: `npm run check:contrast` from Task 5.
- Produces: the charcoal ramp every later visual check reads.

- [ ] **Step 1: Make `body` honour the token**

This is the step that makes the rest have any effect. Replace `globals.css:38`:

```css
  background-color: #000;
```

with:

```css
  background-color: var(--bg);
```

- [ ] **Step 2: Flip `:root`**

In the `:root` block, replace these four declarations:

```css
  --bg: #000000;
  --panel: rgba(255, 255, 255, 0.04);
  --panel-2: rgba(255, 255, 255, 0.06);
  --muted: #8b93a6;
```

with:

```css
  --bg: #14171d;
  /* Opaque, not translucent white. rgba(255,255,255,.04) over charcoal is muddier
     than over black, and nested panels compound it unpredictably. */
  --panel: #1c2027;
  --panel-2: #22262f;
  /* #8b93a6 measured 4.68:1 inside the modal on charcoal. See check:contrast. */
  --muted: #98a0b2;
```

- [ ] **Step 3: Lift the modal**

Replace the `.modal` background and border (:477-480):

```css
  background: #14161c; border: 1px solid #2a2e39;
```

with:

```css
  background: #252a33; border: 1px solid #3b4250;
```

- [ ] **Step 4: Run the guard and confirm it passes**

```bash
cd web-next && npm run check:contrast
```

Expected: **exit 0**, `13 passed, 0 failed`.

- [ ] **Step 5: Prove the bump was load-bearing**

Temporarily set `--muted` back to `#8b93a6`, re-run, and confirm it **fails** on `--muted in modal is 4.68:1, below the 5.0:1 floor`. Then restore `#98a0b2`. This verifies the guard measures what it claims rather than passing vacuously.

- [ ] **Step 6: Full gates**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/ --junitxml=/tmp/j.xml -q; echo "exit=$?"
cd web-next && npx tsc --noEmit && npx eslint src && npm run check:jobs && npm run check:contrast && npm run db:check
```

Expected: all green, test count unchanged from Task 4.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/globals.css
git commit -m "feat(web): charcoal surface ramp -- and --bg was doing nothing at all

Kayla asked for a less strong background. The obvious change would have had no
visible effect: body hardcoded \`background-color: #000\` and never read --bg,
which was referenced in exactly one rule (.version-md) in the whole stylesheet.
So this points body at the token first, THEN moves it.

Panels go from translucent white to opaque hex deliberately -- rgba(255,255,255,.04)
over charcoal is muddier than over black, and nested panels compound it.

--muted moves to #98a0b2 because the old value measured 4.68:1 inside the modal on
charcoal, clearing AA by 0.18 in a modal that is mostly 12.8px muted text.
check:contrast now enforces 5.0:1 and was verified to FAIL when reverted."
```

---

### Task 7: Sweep the hardcoded darks

Six places set a dark colour without the token. Each was invisible against black and is now a visible patch. The hero is deliberately **not** among them.

**Files:**
- Modify: `web-next/src/app/globals.css` (:39-46, :59, :363)

**Interfaces:**
- Consumes: the charcoal ramp from Task 6.
- Produces: nothing. Terminal task.

- [ ] **Step 1: Inventory, and confirm nothing was missed**

```bash
cd web-next
grep -nE "#000|rgba\(0, *0, *0" src/app/globals.css
grep -rnE "#000|rgba\(0, *0, *0" src --include="*.tsx"
```

Expected hits and their verdicts — the `.tsx` grep returns nothing:

| Line | What | Action |
|---|---|---|
| :310 | `.hero` background `#000` | **LEAVE.** Kayla chose a black hero; this is what implements it. |
| :332, :338, :342 | planet `inset` shadows `rgba(0,0,0,…)` | **LEAVE.** Shading on a sphere, not a page surface. |
| :474 | `.modal-backdrop` `rgba(0,0,0,.6)` | **LEAVE.** A dimming scrim should be black. |
| :39-46 | `body` starfield dots | Step 2 |
| :59 | `.topbar` `rgba(0,0,0,.55)` | Step 3 |
| :363 | `.pill-nav a.on` `rgba(0,0,0,.9)` | Step 4 |

- [ ] **Step 2: Rescue the starfield**

The six star dots are `rgba(255,255,255,.4)`–`.7` and the nebula is `rgba(30,60,140,.18)`. All were tuned against `#000`; on `#14171d` the dimmest wash out. Raise the floor so every star still reads, and lift the nebula slightly:

```css
  background-image:
    radial-gradient(1px 1px at 20% 30%, rgba(255,255,255,.85), transparent),
    radial-gradient(1px 1px at 75% 15%, rgba(255,255,255,.7), transparent),
    radial-gradient(1.5px 1.5px at 50% 60%, rgba(255,255,255,.8), transparent),
    radial-gradient(1px 1px at 12% 78%, rgba(255,255,255,.65), transparent),
    radial-gradient(1px 1px at 88% 68%, rgba(255,255,255,.7), transparent),
    radial-gradient(1px 1px at 35% 88%, rgba(255,255,255,.6), transparent),
    radial-gradient(1200px 700px at 50% -10%, rgba(30,60,140,.26), transparent 70%);
```

- [ ] **Step 3: Make the topbar a lift, not a hole**

`rgba(0,0,0,.55)` over charcoal reads as a dark band. A sticky bar with `backdrop-filter: blur(12px)` should sit *above* the page, so tint it with the page colour instead of black. Replace :59:

```css
  background: rgba(20, 23, 29, 0.72);
```

- [ ] **Step 4: Fix the active nav pill**

Replace :363:

```css
.pill-nav a.on { background: rgba(0,0,0,.55); color: #fff; text-decoration-color: rgba(255,255,255,.5); }
```

`.9` black on charcoal is a near-black lozenge that now looks like a hole; `.55` keeps the "pressed" read without punching through.

- [ ] **Step 5: Re-run every gate**

```bash
cd /Users/kayla.li/.superset/Agent
.venv/bin/python -m pytest tests/ --junitxml=/tmp/j.xml -q; echo "exit=$?"
cd web-next && npx tsc --noEmit && npx eslint src
npm run check:jobs && npm run check:contrast && npm run db:check
```

Expected: all green.

- [ ] **Step 6: Look at every page**

Rebuild and walk the app. This is the step that catches what no assertion can.

```bash
cd /Users/kayla.li/.superset/Agent
kill $(lsof -ti tcp:8001) 2>/dev/null; nohup .venv/bin/python -m server > /tmp/agent-server.log 2>&1 &
cd web-next && cp -R .next /tmp/next-backup && kill $(lsof -ti tcp:3000) 2>/dev/null
npm run build && nohup npm run start > /tmp/next-server.log 2>&1 &
```

Check each of `/`, `/jobs`, `/resume`, `/tracker`, `/history`, `/settings`, `/stocks`, and `/jobs` at a 800px window (the `@media (max-width: 820px)` breakpoint reshapes the hero). Look for:

- **`/` (dashboard)** — hero still black and now visibly framed; starfield still legible around it.
- **`/stocks`** — the highest-risk page. Chart fills, donut segments, verdict badges, index rows and mover labels were all tuned against black. Anything that has vanished or gone muddy needs its own token.
- **`/tracker`** — `.confirmed-mark` green `#7fc08a` (measured 6.73:1 on the modal, fine) and the `table.apps` row borders.
- **All** — any panel that now looks the same shade as the page.

- [ ] **Step 7: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/globals.css
git commit -m "fix(web): the six hardcoded darks the charcoal page exposed

Every one was invisible against #000 and is a visible patch against #14171d:

  * the body starfield's dimmest dots (.4 alpha) washed out -- floors raised, and
    the nebula lifted .18 -> .26 to survive the lighter base.
  * .topbar was rgba(0,0,0,.55): a blurred sticky bar should sit ABOVE the page,
    so it is tinted with the page colour now, not with black.
  * .pill-nav a.on was rgba(0,0,0,.9), a near-black lozenge that read as a hole.

Deliberately left alone: .hero's #000 (Kayla chose a black hero and this is what
implements it), the planets' inset shadows (shading on a sphere, not a surface),
and .modal-backdrop's scrim (a dimmer should be black)."
```

---

## Done when

- `1952 passed, 0 failures` from `.venv/bin/python -m pytest tests/` (1944 baseline + 8 new guards: 2 in Task 1, 2 in Task 2, 3 in Task 3, 1 in Task 4).
- `npm run check:contrast` green, and verified red when `--muted` is reverted.
- `check:jobs` 14/14, `db:check` no drift, `tsc --noEmit` and `eslint` clean.
- All ten THE ONE RULE assertions still passing.
- Apply modal checked by hand on a **greenhouse/lever** posting and a **smartrecruiters** posting.
- All seven pages walked at full width and at 800px.
