# Apply modal clarity + charcoal theme — design

**Written 2026-08-05.** Kayla reported that the jobs-tab Apply modal is hard to read and hard to
understand. Three complaints, all reproduced and traced to specific causes, plus a theme change she
asked for while we were in there.

- **Component:** `web-next/src/components/jobs/ApplyModal.tsx`
- **Styles:** `web-next/src/app/globals.css`
- **Helpers:** `web-next/src/lib/jobs.ts`
- **Guard tests:** `tests/test_applier_ui.py` (source-scans the modal — see §7)

---

## 1. The three problems, and what actually causes each

**1. The title renders lowercase, grey, and widely letter-spaced.** Not a modal bug. `globals.css:79`
defines a global decorative `h2` for section labels in the space theme:

```css
h2 { font-family: var(--serif); font-size: 1rem; letter-spacing: 5px;
     text-transform: lowercase; color: var(--muted); margin: 2.25rem 0 1rem; }
```

`.apply-modal h2` (`globals.css:481`) overrides only `margin` and `font-size`, so the transform, the
5px tracking and the muted colour leak onto a dialog title that happens to contain a job title:
"Apply — U.S. Public Policy and AI Innovation Intern (Fall 2026)". A decorative rule for the word
"jobs" is being applied to a sentence.

**2. The résumé `<select>` looks like static text.** The stylesheet has **no base `select` rule.**
Five call sites each style their own — `.form-grid select` (:106), `.job-filters select` (:160),
`.settings-form select` (:214), `.pool-add-head select` (:257), `.generate-row select` (:288) — and
`.apply-modal select` (:483) sets only `width: 100%`. So it renders with the raw user-agent default:
no background, no border, no padding. On a dark panel that is indistinguishable from body text, and
Kayla found it was interactive only by clicking it accidentally. The per-site pattern is the defect:
it has no floor, so any new control is one omission away from invisible.

**3. Two competing primary buttons and explanations below their controls.** When the board supports
autofill the modal renders `button.primary` twice — "Open the form & autofill it" inside
`.apply-autofill`, and "Download résumé, open posting & log it" in `.modal-actions` (orange via
`.modal-actions .primary`). Two primaries means no hierarchy. Worse, the sentence explaining the
footer button sits *after* it in the DOM (`ApplyModal.tsx:383`), so the button is met before its
description. On an unsupported board (`smartrecruiters`, Kayla's Cloudflare example) the labelled
`.apply-autofill` card collapses to one unheaded grey sentence, leaving a bare limitation floating
above the actions with nothing to attach it to.

---

## 2. Decisions

Chosen by Kayla from mockups on 2026-08-05:

| Decision | Choice |
|---|---|
| Action layout | **C** — pick a path, then exactly one primary button |
| Theme | **3, Charcoal** — page `#14171d` |
| Dashboard hero | **A** — stays black (zero change; it already hardcodes `#000`) |
| Path weighting | The two apply paths are **equal choices**, not primary/fallback |

The equal-weighting ruling is load-bearing: it rules out making autofill the hero action and the
manual path a de-emphasised link. Kayla uses both depending on the posting.

---

## 3. Modal structure

One shared résumé choice, then a path selection, then a single action. Reading order top to bottom:

```
Apply — <job title>                     <- readable dialog title
<company>  [<ats> pill]

RÉSUMÉ TO USE                           <- uppercase label
[ master résumé                    ▾ ]  <- visibly a control
No résumé tailored for this role yet. Generate one

HOW DO YOU WANT TO APPLY?
( ) Let the agent fill the form
    Opens this <ats> form in a window on your screen and fills what it
    can from your profile, attaching the résumé above last.
    It fills the form. It does not submit it — and it never will.
    Work-authorization and self-identification questions are always
    left for you.
( ) I'll fill it in myself
    Opens the posting and your résumé PDF in new tabs, and logs the
    application. You can undo it.

Cancel                    [ <label follows the selection> ]
```

The single footer button's label is the selected path's action: `Open the form & autofill it` or
`Download résumé, open posting & log it`. It keeps `.modal-actions .primary`'s existing Mars orange
`#e07a4a` — the jobs tab's accent, already shipping, measured at 6.26:1 against its `#04122b` text.
(The mockups drew it blue; orange is correct, because the button now lives in `.modal-actions`.)

**Radio group, not a segmented control or tabs.** Two mutually exclusive options where each needs a
sentence of explanation is exactly what radios are for, and a native `role="radiogroup"` gets arrow
keys and screen-reader semantics free. `<input type="radio">` is safe here — the source scan forbids
`type="submit"`, not radios — but the group must **not** be wrapped in a `<form>` (§7).

### State: unsupported board

When `supportsAutofill(jobAts)` is false there is no choice to make, so the radio group is not
rendered at all. The manual action becomes the sole primary button, and the limitation moves to a
bordered note **below** it:

```
[ Download résumé, open posting & log it ]

┌ Autofill isn't available for this posting ───────────────┐
│ Autofill can only read greenhouse, lever, ashby          │
│ application forms, and this posting is on                │
│ smartrecruiters. Open the posting and fill it in         │
│ yourself.                                                │
└──────────────────────────────────────────────────────────┘
```

The note keeps `autofillUnavailableNote()`'s existing string verbatim — it is test-pinned to contain
`${board}` and `"fill it in yourself"` (`tests/test_applier_ui.py:1184`). Only the presentation
changes: it gains a heading and a border so it reads as an explained limitation rather than orphaned
grey text. **No disabled button** is rendered for the unavailable path; a dead control invites a
click and answers nothing.

### States that do not change

The in-run and post-run branches (`RunStream`, the `Handoff` checklist, "I pressed Submit — log it &
verify", "Close the browser window") keep their current structure and copy. They are not what Kayla
reported, and the handoff report's band ordering was designed deliberately. This spec touches the
pre-run branch, the title, and the stylesheet only.

---

## 4. Stylesheet changes

**Dialog title reset** — scoped to the modal so the global decorative `h2` is untouched everywhere
else:

```css
.apply-modal h2 {
  margin: 0 0 .25rem; font-size: 1.05rem;
  letter-spacing: normal; text-transform: none; color: var(--text);
}
```

**Base form-control rule** — the floor that was missing. It must be placed **before** the five
existing local rules in the file, and the reason is specificity arithmetic, not tidiness:

- `select` and `textarea` are type selectors (0,0,1), so `.form-grid select` (0,1,1) and friends beat
  them on specificity regardless of order.
- `input[type="text"]` is (0,1,1) — an attribute selector counts the same as a class — which **ties**
  with `.settings-form input` (0,1,1). On a tie, source order decides. So if the base rule came last
  it would silently override the settings page's own input styling.

With that ordering, no existing page changes appearance:

```css
select, input[type="text"], input[type="date"], input[type="number"], textarea {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 10px; padding: .5rem .6rem; font: inherit;
}
```

**Charcoal ramp** — a `:root` change, so it affects every page:

| token | now | new |
|---|---|---|
| `--bg` | `#000000` | `#14171d` |
| `--panel` | `rgba(255,255,255,.04)` | `#1c2027` |
| `--panel-2` | `rgba(255,255,255,.06)` | `#22262f` |
| `.modal` background | `#14161c` | `#252a33` |
| `.modal` border | `#2a2e39` | `#3b4250` |
| `--muted` | `#8b93a6` | `#98a0b2` |

The panel tokens move from translucent white to opaque hex deliberately: `rgba(255,255,255,.04)` over
charcoal produces a different, muddier result than over black, and layered translucency over a
non-black base compounds unpredictably where panels nest.

**`--muted` is not cosmetic.** Measured with the WCAG relative-luminance formula:

| text | on black | on charcoal |
|---|---|---|
| `--muted` on page | 6.82:1 | 5.83:1 |
| `--muted` on panel | 6.43:1 | 5.31:1 |
| **`--muted` in modal** | **5.87:1** | **4.68:1** |
| `--text` in modal | 15.97:1 | 12.72:1 |

4.68:1 clears AA (4.5:1) by 0.18 — no headroom, in a modal that is mostly `.muted.small` at 0.8rem
(12.8px, which counts as normal text, not large). `#98a0b2` restores the modal case to **5.49:1**.
Accents were checked too and all pass on `#252a33`: link orange 4.84:1, confirm green 6.73:1.

**The hero needs no change.** `.hero` (`globals.css:307`) hardcodes `#000` in its own `background`
shorthand and never reads `--bg`, so it stays black for free. Its `border: 1px solid
var(--border-soft)` now reads as an intentional frame against the lighter page.

---

## 5. Out of scope

- The in-run / post-run branches and the `Handoff` checklist.
- `autofillUnavailableNote()`'s wording (presentation only).
- Any change to the applier agent, its endpoints, or the résumé pipeline.
- A light theme. This is a dark theme that got lighter, not a theme system.

---

## 6. Risks

**The charcoal ramp is app-wide and this spec only mocked jobs + dashboard.** Résumé, tracker,
history, settings, and the stocks desk all inherit it. The stocks desk in particular has its own
chart colours, verdict badges and donut fills tuned against black. Every page needs a visual pass,
and any hardcoded `#000`/`#0a0a0c` outside `:root` needs finding — the tokens alone will not catch
them.

**Restructuring the modal's JSX can silently disarm a THE ONE RULE guard.** See §7. The failure mode
is a passing-looking refactor that moves the safety sentence below the button.

---

## 7. Test constraints — read before touching the JSX

`tests/test_applier_ui.py` source-scans `ApplyModal.tsx`. These are not style checks; they encode
THE ONE RULE (no code path ever submits an application). The restructure must keep every one true.

| Assertion | Where |
|---|---|
| `"It does not submit it"` appears **before** `"Open the form & autofill it"` **in file order** | :1040-1042 |
| `"It fills the form."` present | :1043 |
| `"not submit"` appears **≥ 2** times | :1045 |
| `"Work-authorization"` and `"self-identification"` present | :1052-1053 |
| No `<form`, `type="submit"`, `formAction`, `onSubmit` | :1104-1107 |
| No handler named `submitForm` / `doSubmit` / `submitApplication` / `autoSubmit` | :1109-1110 |
| `fetch()` targets confined to the allowlist | :1080 |
| `window.open()` targets confined to the allowlist | :1092 |
| `showOutput={false}`, `handoffGroup(report`, no `render_text`, no `dangerouslySetInnerHTML` | :1117-1120 |
| `autofillUnavailableNote` keeps `${board}` and `"fill it in yourself"` | :1184-1190 |

Layout C satisfies the ordering assertion because the radio option's description (carrying the
guarantee) precedes the footer button in the DOM. **This is a property of the chosen layout, not an
accident to preserve by luck** — if a later revision moves the action above the descriptions, the
test fails, and that failure is correct.

The `"not submit" >= 2` count is currently met by the pre-run copy plus the in-run banner. Since the
in-run branch is untouched, the pre-run copy must still contain one occurrence.

---

## 8. Verification

- `.venv/bin/python -m pytest tests/` — baseline **1944 passed**. This pytest prints no summary line
  (`addopts = "-q"`); count with `--junitxml` and read the exit code.
- `npm run build` in `web-next/` (the :3000 server runs a production build, so a restart alone
  changes nothing), plus `tsc --noEmit` and `eslint`.
- `npm run db:check` — unaffected, but it is the drift guard and should stay green.
- **Manual**: one supported posting (greenhouse or lever) and one unsupported (smartrecruiters) with
  the modal open, at 1× and at 820px where the `@media` breakpoint reshapes the hero.
- **Manual**: every page — dashboard, jobs, résumé, tracker, history, settings, stocks — for
  charcoal regressions and hardcoded blacks.
