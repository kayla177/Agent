"""Fetch node — open the application form in a visible browser and read it.

The only node in the graph that touches a browser. It opens Task 1's headed,
persistent Chromium, navigates to the posting's application URL, waits for a
form control to exist, and hands the rest of the graph a `PageLocator` over
that one DOM snapshot.

Failure is LOUD, never silent. Playwright missing, Chromium missing, a URL that
will not load, a page with no form on it at all — each sets `error` plus a
sentence saying what happened, and the handoff node still runs and still tells
the user nothing was submitted. A run that could not open a browser must not
come back as an empty report.

If anything after the launch raises, the context is closed here before the
exception leaves the node: at that point the browser is not yet in the graph
state, so `graph.release_browser()` cannot see it and this is the only place
that can.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module navigates and reads; it never types, never clicks, never uploads.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from agents.job_applier import browser
from agents.job_applier.locate_dom import PageLocator

#: Navigation budget. Generous compared with the executor's 5s per-field
#: timeout, because this one covers a cold browser start plus a real page load
#: over the network — but finite, so a board that hangs fails with a report
#: instead of pinning a run open forever.
NAV_TIMEOUT_MS = 60_000

#: "the form has rendered" — the same signal `scripts/capture_ats_fixtures.py`
#: waits on, so what the live locator sees matches what the fixtures captured.
FORM_READY_SELECTOR = "input, textarea, select"

#: Where each board's form lives relative to the posting URL. MEASURED from the
#: capture script's own targets (`scripts/capture_ats_fixtures.py`), which are
#: the URLs the committed fixtures were taken from:
#:   * Lever  jobs.lever.co/<org>/<id>       -> + "/apply"
#:   * Ashby  jobs.ashbyhq.com/<org>/<id>    -> + "/application"
#:   * Greenhouse                            -> the posting page IS the form
#: Keyed on the HOST as well as the board, because a scraped `url` is often the
#: company's own careers-site redirector (a real row in this DB points at
#: `databricks.com/company/careers/...?gh_jid=`), and bolting "/apply" onto that
#: produces a 404 instead of a form.
_APPLY_PATH = {
    "lever": ("jobs.lever.co", "/apply"),
    "ashby": ("jobs.ashbyhq.com", "/application"),
}


def apply_url(job: dict, override: str = "") -> str:
    """The URL to open for `job` — pure, so it is testable without a browser.

    `override` (the caller's `state["form_url"]`) always wins: it is the escape
    hatch for a posting whose saved URL is a redirector.
    """
    if (override or "").strip():
        return override.strip()
    url = str((job or {}).get("url") or "").strip()
    if not url:
        return ""
    ats = str((job or {}).get("ats") or "").strip().lower()
    host, suffix = _APPLY_PATH.get(ats, ("", ""))
    if not host:
        return url
    parts = urlsplit(url)
    if parts.netloc.lower().removeprefix("www.") != host:
        return url  # a redirector or a mirrored posting: take it as it stands
    path = parts.path.rstrip("/")
    if path.endswith(suffix):
        return url
    return url.split("?")[0].rstrip("/") + suffix


#: Said when the browser cannot be opened at all. Carries Task 1's install hint
#: verbatim rather than paraphrasing it — the hint names the two different
#: commands for the two different failures, and a paraphrase would lose that.
def _unavailable_message() -> str:
    return (
        "The application form could not be opened because the browser is not "
        "available on this machine, so nothing on the form was filled in.\n\n"
        + browser.PLAYWRIGHT_MISSING_HINT
    )


def fetch_form_node(state: dict) -> dict:
    job = state.get("job") or {}

    if not browser.is_available():
        return {"error": "no_browser", "message": _unavailable_message()}

    url = apply_url(job, state.get("form_url") or "")
    if not url:
        return {
            "error": "no_url",
            "message": "This posting has no URL saved, so there is no form to open.",
        }

    try:
        context = browser.launch_context()
    except Exception as exc:
        # `launch_context` already stopped its own driver on this path; there is
        # nothing here to close. Its RuntimeError text is the actionable install
        # instruction, so it is passed through rather than summarised away.
        return {
            "error": "no_browser",
            "message": (
                f"The application form could not be opened, so nothing on it was "
                f"filled in.\n\n{exc}"
            ),
        }

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_selector(FORM_READY_SELECTOR, timeout=NAV_TIMEOUT_MS)
        locator = PageLocator(page)
        questions = locator.questions()
    except Exception as exc:
        # `close_quietly`, not a bare `.close()`. `ManagedBrowserContext.close()`
        # propagates a context-close error, and raising it from here would
        # replace this actionable sentence with a generic teardown traceback —
        # and the graph's own guard could not retry the close, because the
        # context is not in the state yet.
        browser.close_quietly(context)
        return {
            "error": "form_unreachable",
            "message": (
                f"The application form at {url} could not be read "
                f"({type(exc).__name__}: {exc}), so nothing on it was filled in."
            ),
        }
    except BaseException:
        # KeyboardInterrupt / SystemExit during `goto` or `wait_for_selector`,
        # i.e. Ctrl-C while a slow board loads. Still must not leak a headed
        # window and a driver subprocess.
        browser.close_quietly(context)
        raise

    # An empty `questions` list is NOT an error. The browser is open on the real
    # form and the human can finish it by hand, which is strictly better than
    # closing the window on them — and the handoff already renders exactly that
    # case ("the agent could not read anything on it, so all of it is yours").
    return {
        "browser": context,
        "page": page,
        "locator": locator,
        "questions": questions,
        "form_url": url,
    }
