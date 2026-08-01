#!/usr/bin/env python
"""Dev probe: capture real ATS application-form HTML into tests/fixtures/ats/.

Run by hand, ONCE, when the fixtures need refreshing:

    .venv/bin/python scripts/capture_ats_fixtures.py            # re-capture
    .venv/bin/python scripts/capture_ats_fixtures.py --verify    # live check

This is NOT part of the test suite. `tests/test_applier_locate.py` parses the
saved HTML this writes and never opens a browser or touches the network.

    The render path uses Task 1's PERSISTENT browser profile, which accumulates
    cookies/session state across runs. Re-scan any new capture for leaked PII
    before committing it — the 2026-08-01 captures were checked and contain
    none (no personal name/email, no prefilled control values, no cookie or
    CSRF token; every "cookie"/"session" hit is banner copy or a class name).

    Re-capturing will change the label/required-signal counts asserted by
    `test_which_label_sources_the_real_fixtures_actually_use` and
    `test_which_required_signals_the_real_fixtures_actually_use`. Those two
    tests deliberately pin measured reality, so update them to the new numbers
    rather than loosening them.

THE ONE RULE for all of Phase B — no code path may ever click a submit button —
applies here too, and this probe is deliberately stricter than that: it is
**read-only**. It navigates, waits for the form to render, dumps
`page.content()`, and closes. It never types, never clicks anything, never
uploads a file, never submits. Grep this file: there is no `.fill(`, `.click(`,
`.type(`, `.press(`, or `set_input_files(` anywhere in it, by design.
`--verify` only calls `.count()` on the located elements.

Why two capture methods. All figures below are re-measured from the committed
fixtures, not inherited — the plan's original table said Lever was
69 inputs / 50 labels / 3 textareas, which is wrong:

    | ATS        | plain GET of the apply URL                      | used here |
    |------------|-------------------------------------------------|-----------|
    | Lever      | server-rendered: 1 form, 74 input, 51 label,    | httpx GET |
    |            | 8 textarea, 5 select                            |           |
    | Ashby      | JS shell: 41 KB, 0 input, 0 label               | rendered  |
    | Greenhouse | see below                                       | rendered  |

Measured 2026-08-01, correcting the Task 4 brief: `boards.greenhouse.io/<org>/
jobs/<id>` now 301s to `job-boards.greenhouse.io/...`, and *that* host serves a
server-rendered form (67 KB, 18 inputs, 16 labels). The brief's "254 KB JS
shell, 0 inputs" reproduces only if the redirect is not followed. Greenhouse is
nonetheless captured rendered here: rendering is what the live locator will see,
it is free once the browser is up for Ashby anyway, and it picks up the
JS-injected `aria-required` / `role="group"` attributes the locator relies on.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ats"

# Real, public postings. Kept here (rather than only in the test's header
# comment) so a re-capture is a one-command operation. The Greenhouse posting
# is the SAME job whose questions API is already saved as
# tests/fixtures/ats/greenhouse-questions.json, so the schema fixture (Task 2)
# and the DOM fixture (Task 4) describe one identical form.
TARGETS = [
    ("lever-form.html", "https://jobs.lever.co/palantir/395a4483-fc3d-4b77-a500-501923fd0976/apply", "get"),
    ("ashby-form.html", "https://jobs.ashbyhq.com/snowflake/41e65c6c-a01e-4f40-af14-ae75d3b95e27/application", "render"),
    ("greenhouse-form.html", "https://boards.greenhouse.io/cloudflare/jobs/8077075", "render"),
]

# What "the form has rendered" means per board, for the render path. Waiting on
# a real form control (not a timeout) is what makes the capture deterministic.
FORM_READY_SELECTOR = "input, textarea, select"


def capture_get(url: str) -> str:
    """Plain GET — used only where the server already renders the form."""
    resp = httpx.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    return resp.text


def capture_rendered(url: str) -> str:
    """Render `url` in the Task 1 browser and dump the resulting DOM.

    Read-only: goto → wait for a form control to exist → `page.content()` →
    close. Nothing is typed, clicked, or submitted.
    """
    from agents.job_applier import browser

    with browser.launch_context() as ctx:
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector(FORM_READY_SELECTOR, timeout=60_000)
            # Client-rendered forms mount their inputs in waves (Ashby renders
            # the file-upload widget after the text fields). Settling on the
            # network is a cheap way to catch the later waves too.
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass  # networkidle is a nice-to-have, not a correctness gate
            return page.content()
        finally:
            page.close()


# Identity labels the locator must resolve on every board, for `--verify`.
VERIFY_LABELS = ("Full name", "Email", "Phone", "Resume", "Resume/CV", "First Name")


def verify_live() -> int:
    """Point `PageLocator` at the live pages and report what it resolves.

    Read-only: the only thing called on a resolved element is `.count()`. This
    exists because the unit tests prove the *pure* layer against saved HTML, and
    something has to prove the ~20-line Playwright shim actually addresses real
    elements before Task 6 starts typing into them.
    """
    from agents.job_applier import browser
    from agents.job_applier.locate_dom import PageLocator

    with browser.launch_context() as ctx:
        page = ctx.new_page()
        try:
            for name, url, _ in TARGETS:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_selector(FORM_READY_SELECTOR, timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                locator = PageLocator(page)
                print(f"\n{name}  {url}")
                print(f"  {len(locator.controls)} controls, {len(locator.questions())} questions")
                for label in VERIFY_LABELS:
                    found = locator.locator_for_label(label)
                    if found is None:
                        print(f"    {label!r:14} -> None")
                    else:
                        print(f"    {label!r:14} -> {found.count()} element(s)")
        finally:
            page.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv and "--verify" in argv:
        return verify_live()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for name, url, how in TARGETS:
        print(f"[{how:>6}] {url}")
        html = capture_get(url) if how == "get" else capture_rendered(url)
        path = OUT_DIR / name
        path.write_text(html)
        print(
            f"         -> {path.relative_to(OUT_DIR.parent.parent.parent)} "
            f"({len(html) / 1024:.0f} KB, captured {stamp})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
