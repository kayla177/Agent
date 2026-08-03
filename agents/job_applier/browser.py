"""Browser bootstrap for the job_applier agent.

Opens a **visible** (headed) Chromium window with a persistent profile so the
user can watch every fill happen and intervene at any point, and so logins /
cookies survive between runs. Playwright itself is an optional, heavy
(~150MB of Chromium) dependency: importing this module must never require it
— the import happens lazily, inside the functions that need it — so the rest
of the platform keeps working when Playwright/Chromium aren't installed.

THE ONE RULE for all of Phase B: no code path here may ever click a submit
button. This module only opens a browser; it does not fill or submit anything.
"""

from __future__ import annotations

from typing import Any

import config

# Persistent profile directory. `data/` is already gitignored at the repo
# root, so this is never committed — it holds real cookies/session state.
PROFILE_DIR = config.PROJECT_ROOT / "data" / "browser_profile"

# Mirrors the shape of agents/gmail_sync/nodes/scan_gmail.py's _AUTH_HINT: a
# short, static message a node can surface instead of raising. Two install
# steps are named explicitly because "playwright missing" and "playwright
# installed but Chromium missing" are fixed by two different commands, and a
# vague "install playwright" hint would leave the user stuck after the first.
PLAYWRIGHT_MISSING_HINT = (
    "⚠️ Playwright is not installed. Run:\n"
    "  .venv/bin/pip install playwright\n"
    "  .venv/bin/python -m playwright install chromium\n"
    "then retry."
)

# Distinct hint for the case where the `playwright` package imports fine but
# the Chromium binary itself hasn't been downloaded yet — a different failure
# from the package being absent, fixed by a different (shorter) command.
CHROMIUM_MISSING_HINT = (
    "⚠️ Playwright is installed but the Chromium browser binary is missing. Run:\n"
    "  .venv/bin/python -m playwright install chromium\n"
    "then retry."
)


def is_available() -> bool:
    """Cheaply answer "is the Playwright import satisfiable" — nothing more.

    Must not launch a browser or touch the filesystem beyond the import
    machinery itself.
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class ManagedBrowserContext:
    """A persistent Playwright `BrowserContext` bundled with the driver
    process that started it, so both are torn down exactly once.

    `launch_persistent_context()` spawns two things: the visible Chromium
    window (the `BrowserContext`) and an invisible driver subprocess (owned by
    the `sync_playwright()` handle). Closing the context closes the visible
    window, but does **not** stop the driver — calling only `.close()` on the
    raw context leaks a driver process per call. This wrapper is the single
    enforced cleanup contract: callers either use it as a context manager
    (`with browser.launch_context() as ctx: ...`), which is the intended and
    tested usage, or call `.close()` themselves — idempotent either way, and
    attribute access (`.new_page()`, `.pages`, ...) delegates transparently to
    the underlying `BrowserContext`.
    """

    def __init__(self, context: Any, playwright: Any) -> None:
        self._context = context
        self._playwright = playwright
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def __enter__(self) -> "ManagedBrowserContext":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the browser context and stop the Playwright driver. Safe to
        call more than once — a second call is a no-op."""
        if self._closed:
            return
        self._closed = True
        try:
            self._context.close()
        finally:
            self._playwright.stop()


def close_quietly(context: Any) -> bool:
    """Close `context` if there is one, swallowing any teardown failure.

    Returns whether it actually closed. Lives here rather than in the graph
    because two modules need identical semantics at four call sites, and the
    module that owns the browser's lifetime is the right one to own "close it and
    do not let the teardown become the thing the user hears about".

    `ManagedBrowserContext.close()` PROPAGATES a context-close error (its own
    `try/finally` guarantees the driver is stopped, not that `.close()` is
    quiet). A bare call at a cleanup site therefore replaces an actionable
    message — "the application form at <url> could not be read" — with a generic
    teardown traceback, and can skip a second cleanup attempt entirely. `None` is
    accepted so callers do not each repeat the check.
    """
    if context is None:
        return False
    try:
        context.close()
    except Exception:
        return False
    return True


def launch_context() -> ManagedBrowserContext:
    """Launch a visible, persistent Chromium context for the applier agent.

    Deliberately headed (`headless=False`, non-negotiable): the human must be
    able to watch the fill happen and intervene. Uses a persistent profile at
    ``data/browser_profile`` (isolated from the user's everyday browser, but
    durable across runs so logins/cookies survive).

    Returns a `ManagedBrowserContext`, not a raw Playwright `BrowserContext`:
    use it as a context manager (`with browser.launch_context() as ctx:`) so
    both the visible window and the invisible Playwright driver process are
    torn down when the caller's body exits or raises. Calling `.close()`
    directly instead of using `with` also works and is idempotent.

    Raises RuntimeError with a clear, actionable instruction if Playwright or
    the Chromium binary isn't available — never a raw ImportError/traceback.
    """
    if not is_available():
        raise RuntimeError(PLAYWRIGHT_MISSING_HINT)

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
        )
    except PlaywrightError as exc:
        playwright.stop()
        # "Executable doesn't exist" is the one substring Playwright always
        # uses specifically for "the binary isn't downloaded yet" — it is
        # what actually distinguishes this failure from any other launch
        # error, so it's the only thing we key on.
        if "Executable doesn't exist" in str(exc):
            raise RuntimeError(CHROMIUM_MISSING_HINT) from exc
        raise
    except Exception:
        playwright.stop()
        raise

    return ManagedBrowserContext(context, playwright)
