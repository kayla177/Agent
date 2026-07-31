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


def launch_context() -> Any:
    """Launch a visible, persistent Chromium context for the applier agent.

    Deliberately headed (`headless=False`, non-negotiable): the human must be
    able to watch the fill happen and intervene. Uses a persistent profile at
    ``data/browser_profile`` (isolated from the user's everyday browser, but
    durable across runs so logins/cookies survive).

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
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
        )
    except PlaywrightError as exc:
        playwright.stop()
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise RuntimeError(CHROMIUM_MISSING_HINT) from exc
        raise
    except Exception:
        playwright.stop()
        raise
