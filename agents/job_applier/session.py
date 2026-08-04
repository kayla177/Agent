"""The browser the agent hands to the human, and the one thread it may be touched from.

Phase B ends with a **visible browser window left open on a filled form** — see
`graph.py`'s "Browser lifetime" section, which explains why closing it on the
successful path would throw away every field the agent just typed. Task 9 then
built a detector that can tell whether the human's submission actually went
through, and deliberately wired it to nothing: at graph-exit time the human has
not pressed Submit yet, so there is nothing to detect.

This module is the missing handle between the two. It parks the finished run's
page + context under its job id, and it owns the single worker thread that is the
only place either object may be used from.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module reads exactly two things off the page — `page.content()` and
`page.url` — and closes contexts. It calls nothing that can dispatch a click, a
keystroke, or a script; the package-wide AST scan
(`test_every_applier_module_obeys_the_one_rule`, which globs this package
recursively) covers this file automatically and is what keeps that true.

Why a dedicated thread, and why it is not optional
==================================================
Playwright's **sync** API is thread-affine, structurally. Every call goes through
`SyncBase._sync()`, which does `self._dispatcher_fiber.switch()` on a greenlet
created when `sync_playwright().start()` ran, and hands work to that same
object's event loop with a bare `loop.create_task`. Greenlets cannot be switched
to across threads (`greenlet.error: cannot switch to a different thread`), so a
context created on thread A is unusable from thread B — not slower, not racy:
unusable.

That matters here for two separate reasons:

  1. **The later read.** A FastAPI request handler runs on the event loop thread
     or on a Starlette threadpool worker. Reading the handed-over page directly
     from there could never work. So the read is queued back onto the thread that
     opened the browser (`read`, via `run_now`).
  2. **The run itself.** `server/runner.py` drives graphs with
     `graph.astream(...)`, and LangGraph runs sync nodes in the event loop's
     default executor — a POOL. `fetch_form` opening the context on one pool
     worker and `fill` typing into it from another is exactly the cross-thread
     case above, and which worker picks up a queued call is not deterministic.
     So `server/applier_run.py` does not use that driver: it runs the whole
     applier graph, start to finish, on this module's single thread, and then
     queues the confirmation read onto the same one.

     (This is a real hazard for the generic path, not a hypothetical: any agent
     holding a thread-affine object across two sync nodes has it. The applier is
     the only agent that does.

     This paragraph used to say the applier "now avoids the generic driver
     entirely". That was true of every route the UI offers and false of the API:
     `POST /agents/job_applier/run` looked up the applier's spec like any other
     agent and handed it to `runner.start_run`, i.e. to exactly the pooled
     `astream` driver described above — and, with a JSON body, seeded its state
     too. It is now a **refusal** rather than a convention: `_WRONG_ENTRY_POINT`
     in `server/routers/runs.py` returns 409 for this agent key before a `runs`
     row is created, and says which endpoint to use. `server/runner.py` itself is
     still deliberately left alone.)

The worker is a plain `Thread` draining a `Queue` rather than a
`ThreadPoolExecutor`, for two reasons: a pool's `max_workers=1` is a
configuration promise where this needs a structural one, and
`ThreadPoolExecutor.submit()` is an attribute call named `submit`, which the ONE
RULE scan rejects on sight — correctly, since it cannot know what a `.submit()`
in this package means. Not worth teaching the guard an exception for.

One window at a time
====================
`hand_over` closes every other live session first. Two visible Chromium windows
on one screen, each with a half-filled application for a different company, is a
way to submit the wrong form; and an unbounded registry of retained contexts
leaks a browser process per run into a long-lived server. The cap is one.
"""

from __future__ import annotations

import datetime as dt
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from agents.job_applier import browser

#: How long a caller waiting on the session thread will wait before giving up.
#: The thread is busy for the whole of an applier run (a minute or more), and a
#: request that blocks that long is worse than one that says "still working".
DEFAULT_TIMEOUT_SECONDS = 10.0

_THREAD_NAME = "job-applier-session"

_QUEUE: queue.Queue = queue.Queue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()

_SESSIONS: dict[str, "LiveSession"] = {}
_REGISTRY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# The single thread
# ---------------------------------------------------------------------------


def _worker_loop() -> None:
    """Drain the queue forever, one call at a time, never dying on an exception.

    A worker that dies takes the browser's only usable thread with it, and then
    every later `run_now` times out with a message about the session thread being
    busy — which would be a lie. Each item carries its own result box, so a raise
    is reported to that caller and the loop continues.
    """
    while True:
        item = _QUEUE.get()
        if item is None:  # only used by the tests' shutdown helper
            return
        fn, args, kwargs, box, done = item
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
            box["error"] = exc
        finally:
            if done is not None:
                done.set()


def _ensure_worker() -> threading.Thread:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(
                target=_worker_loop, name=_THREAD_NAME, daemon=True
            )
            _WORKER.start()
        return _WORKER


def on_session_thread() -> bool:
    """Are we already on the thread that owns the browser?

    `run_now` needs this: a single-threaded worker that waits on itself is a
    deadlock, so a nested call runs inline instead.
    """
    return threading.current_thread().name == _THREAD_NAME


def run_soon(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Queue `fn` on the session thread and return immediately.

    Used to start a run: the HTTP request that triggers an application must not
    wait for a browser to open, and the caller follows progress over SSE.
    """
    _ensure_worker()
    _QUEUE.put((fn, args, kwargs, {}, None))


def run_now(
    fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_SECONDS, **kwargs: Any
) -> Any:
    """Run `fn` on the session thread and return its result.

    Raises `TimeoutError` if the thread is still busy (an applier run in
    progress), and re-raises whatever `fn` raised. Runs inline when already on
    the session thread, so a nested call cannot deadlock against itself.
    """
    if on_session_thread():
        return fn(*args, **kwargs)
    _ensure_worker()
    box: dict[str, Any] = {}
    done = threading.Event()
    _QUEUE.put((fn, args, kwargs, box, done))
    if not done.wait(timeout):
        raise TimeoutError(
            "the browser session thread is still busy (an assisted-apply run is "
            "probably still filling the form)"
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------------------
# What was handed over
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSession:
    """One filled form waiting for a human, and the objects behind it.

    `page` and `browser` may only be used from the session thread (see the module
    docstring). `run_id` is kept so a UI can tie the window back to the run whose
    handoff report describes it.
    """

    job_id: str
    run_id: int
    form_url: str
    page: Any
    browser: Any
    opened_at: str = ""


@dataclass(frozen=True)
class PageRead:
    """The outcome of reading a live page: two strings, or an honest failure.

    `ok=False` is not "the application failed" and must never be shown as one —
    it means nothing could be read, so nothing is known either way. The
    confirmation detector is never called on a failed read, which is the same
    "absence of evidence is not evidence" rule `confirm.stamp_if_confirmed`
    follows.
    """

    ok: bool
    html: str = ""
    url: str = ""
    error: str = ""


def now() -> str:
    """UTC instant a window was handed over, to seconds. Same shape as
    `applications.confirmed_at`, so the two read alike in a UI."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def hand_over(session: LiveSession) -> None:
    """Park a finished run's window under its job id, closing any other one.

    Must be called from the session thread — closing a displaced context is a
    Playwright call, and `close` on the wrong thread is the failure this module
    exists to prevent. `server/applier_run.py` calls this at the end of the run
    it just drove, which is on that thread by construction.
    """
    if not on_session_thread():
        raise RuntimeError("hand_over must run on the session thread")
    with _REGISTRY_LOCK:
        displaced = [s for jid, s in _SESSIONS.items() if jid != session.job_id]
        stale = _SESSIONS.get(session.job_id)
        _SESSIONS.clear()
        _SESSIONS[session.job_id] = session
    for old in displaced + ([stale] if stale is not None else []):
        browser.close_quietly(old.browser)


def get(job_id: str) -> LiveSession | None:
    with _REGISTRY_LOCK:
        return _SESSIONS.get(job_id)


def open_sessions() -> tuple[LiveSession, ...]:
    with _REGISTRY_LOCK:
        return tuple(_SESSIONS.values())


def _read(session: LiveSession) -> PageRead:
    """The page's HTML and URL. Never raises, never acts on a control.

    Two separate `try`s on purpose: `content()` is the one that matters and it is
    what fails when the human closed the window, while `.url` is a cheap
    attribute that should not be able to lose an HTML body we already have.
    """
    page = session.page
    if page is None:
        return PageRead(False, error="this run never had a readable page")
    try:
        html = page.content()
    except Exception as exc:  # noqa: BLE001
        return PageRead(
            False,
            error=(
                f"the browser window could not be read — it was probably closed "
                f"({type(exc).__name__})"
            ),
        )
    try:
        url = str(page.url or "")
    except Exception:  # noqa: BLE001
        url = session.form_url
    return PageRead(True, html=str(html or ""), url=url or session.form_url)


def read(job_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> PageRead:
    """Read the window handed over for `job_id`, on the thread that owns it.

    Returns a failed `PageRead` — never raises — for every reason the read cannot
    happen: no session (a fresh server process, or a run that failed before the
    browser opened), the thread still busy, or the window already gone. Each one
    is a *don't know*, and the caller must treat it as such.
    """
    session = get(job_id)
    if session is None:
        return PageRead(
            False,
            error=(
                "no assisted-apply browser window is being held for this job, so "
                "the form could not be re-read"
            ),
        )
    try:
        return run_now(_read, session, timeout=timeout)
    except TimeoutError as exc:
        return PageRead(False, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return PageRead(False, error=f"could not read the form ({type(exc).__name__}: {exc})")


def _close(sessions: tuple[LiveSession, ...]) -> int:
    return sum(1 for s in sessions if browser.close_quietly(s.browser))


def close(job_id: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Close the window for `job_id` (or every one), returning how many closed.

    Dropped from the registry FIRST, then closed: a context that failed to close
    is still not one a later read should be handed, and `close_quietly` already
    guarantees a teardown error is not what the user hears about.
    """
    with _REGISTRY_LOCK:
        if job_id is None:
            doomed = tuple(_SESSIONS.values())
            _SESSIONS.clear()
        else:
            session = _SESSIONS.pop(job_id, None)
            doomed = (session,) if session is not None else ()
    if not doomed:
        return 0
    try:
        return int(run_now(_close, doomed, timeout=timeout))
    except Exception:  # noqa: BLE001
        # The registry no longer references them, so the state is consistent even
        # when the close itself could not be performed. Nothing here is worth
        # failing a request over.
        return 0
