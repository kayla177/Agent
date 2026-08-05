"""Task 10 — the entry point: the résumé path, the session thread, the endpoints, the UI.

Nine tasks of machinery were unreachable until this task existed. What it adds is
the only part a human touches, so the properties worth pinning are the ones a
user could be misled by:

  1. **The résumé the agent attaches is the file that is actually on disk.**
     `resume_pdf.pdf_path` derives the path from the key `ensure_pdf` RETURNED,
     never by recomputing `cache_key()`. The two genuinely disagree on a shipped
     path (a tailored résumé with no LaTeX of its own compiles the MASTER's source
     and is keyed as the master), and recomputing would hand the agent a path to a
     file that does not exist.
  2. **Playwright's sync API is thread-affine**, so the browser is opened, filled
     and later re-read on ONE thread. Asserted as a property of the code, not
     assumed: `agents/job_applier/session.py` is the only place either object may
     be touched from, and the tests below check that two calls really do land on
     the same non-main thread.
  3. **Nothing submits.** The AST guard the whole `agents/job_applier/` package is
     held to is re-run here over the two new modules that could break it, and the
     React modal is scanned for a "submit for me" affordance and for the promise
     it has to make BEFORE the user starts anything.
  4. **A non-match never un-confirms anything.** `/data/jobs/confirm-submission`
     is the only caller of Task 9's detector, and the endpoint is exercised on a
     real captured form page (which must not stamp) as well as the synthetic
     confirmation page (which must).

Hermetic, like the rest of the suite: no browser, no network, no model. Every
graph in here is a fake handed to `applier_run._graph_factory`, which exists for
exactly that purpose — the real factory imports the Playwright-backed nodes.
"""

from __future__ import annotations

import pathlib
import re
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import store_db  # noqa: E402
from agents.application_tracker import store as appstore  # noqa: E402
from agents.job_applier import session  # noqa: E402
from agents.job_applier.nodes import handoff as handoff_mod  # noqa: E402
from agents.job_applier.nodes.load_profile import SUPPORTED_ATS  # noqa: E402
from agents.job_scraper import store as jobstore  # noqa: E402
from agents.registry import REGISTRY  # noqa: E402
from server import applier_run, resume_pdf  # noqa: E402
from server.routers import runs as runs_router  # noqa: E402

# The canonical ONE-RULE guard, imported rather than re-implemented: a second copy
# would drift, and the point is that these files are held to the SAME rules as the
# package. `tests/` is on sys.path under pytest (there is no tests/__init__.py).
from test_applier_locate import _submit_click_violations  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
WEB = ROOT / "web-next" / "src"
MODAL = WEB / "components" / "jobs" / "ApplyModal.tsx"
JOBS_LIB = WEB / "lib" / "jobs.ts"
SESSION_MODULE = ROOT / "agents" / "job_applier" / "session.py"
DRIVER_MODULE = ROOT / "server" / "applier_run.py"
JOBS_ROUTER = ROOT / "server" / "routers" / "jobs.py"

_GREENHOUSE_JOB = {
    "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
    "location": "Austin, TX", "url": "https://boards.greenhouse.io/acme/jobs/1",
    "status": "new", "country": "US", "ats": "greenhouse",
}
_WORKDAY_JOB = {
    "id": "Big:workday:2", "company": "Big", "title": "Intern",
    "location": "Remote", "url": "https://big.wd5.myworkdayjobs.com/x/job/2",
    "status": "new", "country": "US", "ats": "workday",
}

LEVER_FORM_URL = "https://jobs.lever.co/palantir/395a4483/apply"
LEVER_THANKS_URL = "https://jobs.lever.co/palantir/395a4483/thanks"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_session_state():
    """No test may leak a parked window or a cached report into the next one.

    The registry is cleared directly rather than through `session.close()`: the
    fakes below have no real context to tear down, and a cleanup that depended on
    the worker thread would make an unrelated failure look like a hang.
    """
    yield
    session._SESSIONS.clear()
    applier_run._REPORTS.clear()
    applier_run._graph_factory = None


@pytest.fixture
def run_db(temp_db, monkeypatch):
    """`temp_db`, plus `server.db`'s independent copy of the path.

    `server/db.py` caches `DB_PATH = config.DB_PATH` at import (see
    tests/conftest.py), so the driver's `create_run` / `finish_run` would write to
    the REAL data/control_center.db without this.
    """
    from server import db as server_db

    monkeypatch.setattr(server_db, "DB_PATH", temp_db)
    server_db.init_db()
    return server_db


class _FakeLoop:
    """Stands in for the event loop the endpoint captures.

    `call_soon_threadsafe` is invoked inline, which is all the driver's contract
    requires (it must not call `manager.publish` directly from the browser thread).
    """

    def __init__(self) -> None:
        self.events: list = []

    def call_soon_threadsafe(self, fn, *args):
        self.events.append(args[-1] if args else None)
        fn(*args)


class _FakeBrowser:
    def __init__(self, fail: bool = False) -> None:
        self.closed = 0
        self.fail = fail

    def close(self) -> None:
        self.closed += 1
        if self.fail:
            raise RuntimeError("teardown exploded")


class _FakePage:
    """A page that answers the only two things `session._read` asks it."""

    def __init__(self, html: str = "<html></html>", url: str = LEVER_FORM_URL,
                 raise_on_content: bool = False) -> None:
        self._html = html
        self.url = url
        self._raise = raise_on_content

    def content(self) -> str:
        if self._raise:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._html


class _FakeGraph:
    """Yields a canned `(mode, data)` stream, exactly as LangGraph would."""

    def __init__(self, events) -> None:
        self.events = events
        self.payload = None

    def stream(self, payload, stream_mode=None):
        self.payload = payload
        self.stream_mode = stream_mode
        yield from self.events


def _node_pair(name: str, delta: dict | None = None, error: str | None = None):
    events = [("tasks", {"name": name})]
    if error:
        events.append(("tasks", {"name": name, "result": [], "error": error}))
    else:
        events.append(("tasks", {"name": name, "result": []}))
    if delta is not None:
        events.append(("updates", {name: delta}))
    return events


def _report(**kw):
    return handoff_mod.build_report(None, **kw)


def _drive_on_session_thread(run_id, job_id, resume_path, loop):
    """Run the driver where it really runs — on the session thread.

    `_hand_over_or_close` calls `session.hand_over`, which REFUSES to run
    anywhere else, so calling `_drive` directly from the test thread would be
    testing a path production never takes.
    """
    return session.run_now(
        applier_run._drive, run_id, job_id, resume_path, loop, timeout=15
    )


# ===========================================================================
# 1. The résumé path — the gap between "a PDF exists" and "the agent has a path"
# ===========================================================================


def _master(monkeypatch, tmp_path, latex="\\documentclass{article}\\begin{document}M\\end{document}"):
    from agents.resume_generator import store as rstore

    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(resume_pdf, "compile_tex", lambda tex: b"%PDF-1.5 fake")
    rstore.upsert_master_resume(None, latex=latex)


def test_the_resume_path_is_the_file_ensure_pdf_actually_wrote(temp_db, monkeypatch, tmp_path):
    _master(monkeypatch, tmp_path)
    key, path = resume_pdf.pdf_path(None)
    assert path == resume_pdf.PDF_DIR / f"{key}.pdf"
    assert path.is_file(), "the agent is handed a path; it has to exist"
    assert path.read_bytes().startswith(b"%PDF")


def test_the_resume_path_follows_the_master_fallback_instead_of_recomputing_the_key(
    temp_db, monkeypatch, tmp_path
):
    """The reason this accessor exists rather than a `PDF_DIR / cache_key(...)` at
    the call site. A tailored résumé with no LaTeX of its own compiles the MASTER's
    source, and `ensure_pdf` deliberately returns the master's key for it — so
    `cache_key(job_id, ...)` names a file that was never written, and the agent
    would be handed a path to nothing."""
    from agents.resume_generator import store as rstore

    _master(monkeypatch, tmp_path)
    rstore.upsert_resume("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                         markdown="md", keywords=[], status="draft", latex="")

    key, path = resume_pdf.pdf_path("Acme:greenhouse:1")
    assert key.startswith("master"), "these bytes ARE the master résumé"
    assert path.is_file()
    # What recomputing the key would have produced.
    naive = resume_pdf.PDF_DIR / f"{resume_pdf.cache_key('Acme:greenhouse:1', '')}.pdf"
    assert not naive.exists()


def test_the_resume_path_stays_inside_the_pdf_directory(temp_db, monkeypatch, tmp_path):
    _master(monkeypatch, tmp_path)
    _, path = resume_pdf.pdf_path(None)
    assert path.resolve().parent == resume_pdf.PDF_DIR.resolve()


def test_no_resume_raises_rather_than_returning_a_path_to_nothing(temp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    with pytest.raises(LookupError):
        resume_pdf.pdf_path(None)


# ===========================================================================
# 2. The session thread — Playwright's sync API is thread-affine
# ===========================================================================


def test_the_session_runs_every_call_on_one_non_main_thread():
    """The property the whole design rests on: Playwright's sync API cannot be
    used from any thread but the one that created the context.

    Compared by `get_ident()`, NOT by thread NAME — every worker this module
    spawns carries the same name, so a mutant that started a fresh thread per call
    passed a name-based version of this test.
    """
    first = session.run_now(threading.get_ident)
    second = session.run_now(threading.get_ident)
    assert first == second, "two calls must land on the SAME thread, not a pool"
    assert first != threading.get_ident()
    alive = [t for t in threading.enumerate() if t.name == session._THREAD_NAME]
    assert len(alive) == 1, f"exactly one session worker, found {len(alive)}"


def test_a_call_made_from_the_session_thread_runs_inline_instead_of_deadlocking():
    """A single worker that waits on itself is a hang. `session.read` is reachable
    from `_drive` (which is already on that thread), so this is not academic."""
    inner = session.run_now(lambda: session.run_now(lambda: "nested", timeout=1))
    assert inner == "nested"


def test_a_raising_call_is_reported_to_its_caller_and_leaves_the_worker_alive():
    """A worker that dies takes the browser's only usable thread with it, and every
    later read then times out with a message about being busy — a lie."""
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        session.run_now(boom)
    assert session.run_now(lambda: "still here") == "still here"


def test_reading_a_job_with_no_window_is_a_dont_know_not_a_failure():
    read = session.read("Acme:greenhouse:1")
    assert read.ok is False
    assert read.html == "" and read.url == ""
    assert "no assisted-apply browser window" in read.error


def test_reading_a_live_page_returns_its_html_and_url():
    page = _FakePage(html="<html>filled</html>", url=LEVER_THANKS_URL)
    session.run_now(session.hand_over, session.LiveSession(
        job_id="j", run_id=1, form_url=LEVER_FORM_URL, page=page, browser=_FakeBrowser(),
    ))
    read = session.read("j")
    assert read.ok is True
    assert read.html == "<html>filled</html>"
    assert read.url == LEVER_THANKS_URL


def test_a_page_that_cannot_be_read_says_so_instead_of_raising():
    """The overwhelmingly common case once the human is done: she closed the
    window. That must not become an exception in an HTTP handler, and it must not
    read as "your application failed"."""
    session.run_now(session.hand_over, session.LiveSession(
        job_id="j", run_id=1, form_url=LEVER_FORM_URL,
        page=_FakePage(raise_on_content=True), browser=_FakeBrowser(),
    ))
    read = session.read("j")
    assert read.ok is False
    assert "probably closed" in read.error


def test_a_session_with_no_page_at_all_is_handled():
    session.run_now(session.hand_over, session.LiveSession(
        job_id="j", run_id=1, form_url="", page=None, browser=_FakeBrowser(),
    ))
    assert session.read("j").ok is False


def test_a_page_whose_url_cannot_be_read_falls_back_to_the_form_url():
    """The HTML is the evidence; the URL only narrows which board it is. Losing an
    HTML body we already have because `.url` threw would turn a readable page into
    a don't-know."""
    class _NoUrl:
        def content(self):
            return "<html>ok</html>"

        @property
        def url(self):
            raise RuntimeError("gone")

    session.run_now(session.hand_over, session.LiveSession(
        job_id="j", run_id=1, form_url=LEVER_FORM_URL, page=_NoUrl(), browser=_FakeBrowser(),
    ))
    read = session.read("j")
    assert read.ok is True
    assert read.url == LEVER_FORM_URL


def test_handing_over_a_second_window_closes_the_first():
    """One visible window at a time. Two half-filled applications for two
    companies on one screen is a way to submit the wrong one, and an unbounded
    registry leaks a Chromium process per run into a long-lived server."""
    first, second = _FakeBrowser(), _FakeBrowser()
    session.run_now(session.hand_over, session.LiveSession(
        job_id="a", run_id=1, form_url="", page=_FakePage(), browser=first))
    session.run_now(session.hand_over, session.LiveSession(
        job_id="b", run_id=2, form_url="", page=_FakePage(), browser=second))
    assert first.closed == 1
    assert second.closed == 0
    assert [s.job_id for s in session.open_sessions()] == ["b"]


def test_handing_over_the_same_job_twice_closes_the_displaced_window():
    first, second = _FakeBrowser(), _FakeBrowser()
    for browser_obj in (first, second):
        session.run_now(session.hand_over, session.LiveSession(
            job_id="a", run_id=1, form_url="", page=_FakePage(), browser=browser_obj))
    assert first.closed == 1
    assert second.closed == 0


def test_hand_over_refuses_to_run_anywhere_but_the_session_thread():
    """Closing a displaced context is a Playwright call, so parking a window from
    the wrong thread is the exact bug this module exists to prevent."""
    with pytest.raises(RuntimeError):
        session.hand_over(session.LiveSession(
            job_id="a", run_id=1, form_url="", page=None, browser=_FakeBrowser()))


def test_close_drops_the_session_even_when_the_teardown_fails():
    """`close_quietly` swallows a teardown error, and the registry must not keep a
    context a later read would be handed."""
    session.run_now(session.hand_over, session.LiveSession(
        job_id="a", run_id=1, form_url="", page=_FakePage(), browser=_FakeBrowser(fail=True)))
    session.close("a")
    assert session.open_sessions() == ()
    assert session.get("a") is None


def test_closing_nothing_is_a_no_op():
    assert session.close("never-existed") == 0
    assert session.close() == 0


def test_close_with_no_job_id_closes_every_window():
    a, b = _FakeBrowser(), _FakeBrowser()
    session.run_now(session.hand_over, session.LiveSession(
        job_id="a", run_id=1, form_url="", page=_FakePage(), browser=a))
    # `hand_over` caps the registry at one, so seed the second one directly.
    session._SESSIONS["b"] = session.LiveSession(
        job_id="b", run_id=2, form_url="", page=_FakePage(), browser=b)
    assert session.close() == 2
    assert session.open_sessions() == ()


# ===========================================================================
# 3. The driver — same bookkeeping as server/runner.py, on the browser's thread
# ===========================================================================


def test_the_driver_streams_node_events_and_finishes_the_run(run_db):
    events = _node_pair("load_profile", {"job": {"title": "X"}}) + \
        _node_pair("handoff", {"message": "NOTHING WAS SUBMITTED."})
    applier_run._graph_factory = lambda: _FakeGraph(events)
    loop = _FakeLoop()

    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "Acme:greenhouse:1", "", loop)

    logged = [(e["node"], e["status"]) for e in run_db.get_node_events(run_id)]
    assert logged == [
        ("load_profile", "start"), ("load_profile", "finish"),
        ("handoff", "start"), ("handoff", "finish"),
    ]
    run = run_db.get_run(run_id)
    assert run["status"] == "success"
    assert run["output_message"] == "NOTHING WAS SUBMITTED."
    # The sentinel is last, so an SSE subscriber closes rather than hanging.
    assert loop.events[-1] is None
    assert {"type": "run_finished", "message": "NOTHING WAS SUBMITTED."} in loop.events


def test_the_driver_reports_a_node_error_as_that_nodes_status(run_db):
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("fetch_form", None, error="boom"))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "j", "", _FakeLoop())
    assert [e["status"] for e in run_db.get_node_events(run_id)] == ["start", "error"]


def test_the_driver_passes_the_resume_path_to_the_graph(run_db):
    graph = _FakeGraph(_node_pair("load_profile", {}))
    applier_run._graph_factory = lambda: graph
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "Acme:greenhouse:1", "/tmp/cv.pdf", _FakeLoop())
    assert graph.payload == {"job_id": "Acme:greenhouse:1", "resume_path": "/tmp/cv.pdf"}


def test_the_driver_omits_resume_path_entirely_when_there_is_none(run_db):
    """`resume_path: ""` and a missing key mean the same thing to the state, but
    only the missing key keeps `ApplierState`'s "nothing here ever invents a path"
    literally true of what the graph is handed."""
    graph = _FakeGraph(_node_pair("load_profile", {}))
    applier_run._graph_factory = lambda: graph
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "j", "", _FakeLoop())
    assert graph.payload == {"job_id": "j"}


def test_a_successful_run_hands_the_window_to_the_human(run_db):
    """The whole product: the agent stops with the form filled and the window on
    screen. Closing it here would throw away every field it just typed."""
    page, ctx = _FakePage(), _FakeBrowser()
    applier_run._graph_factory = lambda: _FakeGraph(_node_pair(
        "fetch_form", {"browser": ctx, "page": page, "form_url": LEVER_FORM_URL}))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "job-1", "", _FakeLoop())

    held = session.get("job-1")
    assert held is not None
    assert held.page is page and held.browser is ctx
    assert held.form_url == LEVER_FORM_URL
    assert held.run_id == run_id
    assert ctx.closed == 0, "a successful run must not close the window"


def test_a_run_that_errored_closes_the_window_it_still_holds(run_db):
    """The state here carries a LIVE context AND an error, which is the only shape
    that tests the condition: `handoff_node` decides "is a window on screen" as
    "a context AND no error" precisely because it must not depend on whether the
    graph's teardown ran before or after it. A driver that keyed on the handle
    alone would park a window over a failed run — and a version of this test whose
    fixture nulled the handle out passed that mutation."""
    ctx = _FakeBrowser()
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("fetch_form", {"browser": ctx, "page": _FakePage()})
        + _node_pair("handoff", {"error": "fill_failed"}))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "job-1", "", _FakeLoop())
    assert session.get("job-1") is None, "a failed run hands over nothing"
    assert ctx.closed == 1, "and the window it opened is closed, not leaked"


def test_a_run_whose_browser_was_already_torn_down_parks_nothing(run_db):
    """The other shape: the graph's own `_step` nulls the handle on every failing
    path, so `final_state["browser"]` is None by the time the driver looks."""
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("fetch_form", {"browser": _FakeBrowser()})
        + _node_pair("handoff", {"error": "fetch_form_failed", "browser": None}))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "job-1", "", _FakeLoop())
    assert session.get("job-1") is None


def test_a_driver_crash_marks_the_run_as_error_and_still_closes_the_stream(run_db):
    class _Exploding:
        def stream(self, payload, stream_mode=None):
            raise RuntimeError("langgraph is unhappy")

    applier_run._graph_factory = _Exploding
    loop = _FakeLoop()
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "j", "", loop)

    run = run_db.get_run(run_id)
    assert run["status"] == "error"
    assert "langgraph is unhappy" in (run["error"] or "")
    assert loop.events[-1] is None


def test_a_previous_window_is_closed_before_a_new_run_opens_one(run_db):
    old = _FakeBrowser()
    session.run_now(session.hand_over, session.LiveSession(
        job_id="old", run_id=1, form_url="", page=_FakePage(), browser=old))
    applier_run._graph_factory = lambda: _FakeGraph(_node_pair("load_profile", {}))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "new", "", _FakeLoop())
    assert old.closed == 1, "closed BEFORE the new one opens, never after"
    assert session.get("old") is None


def test_a_failure_to_park_the_window_still_closes_the_event_stream(run_db, monkeypatch):
    """An SSE subscriber that never gets the sentinel waits forever, and the user
    watches a finished run report nothing."""
    def explode(*a, **k):
        raise RuntimeError("registry broke")

    monkeypatch.setattr(session, "hand_over", explode)
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("fetch_form", {"browser": _FakeBrowser(), "page": _FakePage()}))
    loop = _FakeLoop()
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "j", "", loop)
    assert loop.events[-1] is None


# --- the report goes to the UI as data -------------------------------------


def test_the_report_payload_is_structured_data_not_rendered_text(run_db):
    report = _report(
        questions=[], unreadable=[], withheld_eeo=[],
        job_title="SWE Intern", company="Acme", form_url=LEVER_FORM_URL,
        browser_open=True,
    )
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("handoff", {"report": report, "message": report.render_text()}))
    run_id = run_db.create_run("job_applier", False)
    _drive_on_session_thread(run_id, "j", "", _FakeLoop())

    payload = applier_run.report_payload(run_id)
    assert isinstance(payload["items"], list)
    assert payload["job_title"] == "SWE Intern"
    assert payload["submitted"] is False
    assert payload["browser_open"] is True
    # The computed fields a UI must not have to re-derive or reword.
    assert payload["headline"].startswith(handoff_mod.NOT_SUBMITTED_HEADLINE)
    assert payload["instruction"] == report.instruction()
    assert payload["summary_line"] == report.summary_line()
    assert set(payload["counts"]) == set(handoff_mod.GROUPS)
    assert payload["total"] == report.total


def test_every_report_item_field_survives_the_json_round_trip(run_db):
    """The UI's `HandoffItem` type has to be able to rely on the whole dataclass
    arriving, not on whichever fields happened to be non-empty."""
    from agents.job_applier.schema_greenhouse import Question

    q = Question(key="q1", label="Are you authorised to work in the US?",
                 kind="select", required=True, section="Eligibility",
                 options=("Yes", "No"))
    report = handoff_mod.build_report(None, questions=[q], unreadable=[q], withheld_eeo=[])
    applier_run._remember_report(7, report)
    item = applier_run.report_payload(7)["items"][0]
    for field in ("key", "label", "group", "reason", "section", "required",
                  "status", "value", "intended", "suggestion", "note", "drafted",
                  "kind"):
        assert field in item, field


def test_a_report_for_an_unknown_run_is_absent_not_empty():
    assert applier_run.report_payload(999) is None


def test_the_report_cache_is_bounded(run_db):
    for i in range(applier_run._REPORT_CACHE_SIZE + 3):
        applier_run._remember_report(i, _report())
    assert len(applier_run._REPORTS) == applier_run._REPORT_CACHE_SIZE
    assert applier_run.report_payload(0) is None, "oldest evicted first"
    assert applier_run.report_payload(applier_run._REPORT_CACHE_SIZE + 2) is not None


# ===========================================================================
# 4. THE ONE RULE, over the new code that could break it
# ===========================================================================


def test_the_session_module_never_touches_a_control():
    """Also covered automatically by `test_every_applier_module_obeys_the_one_rule`
    (which globs this package recursively). Asserted here as well because this is
    the only module that touches a page AFTER the graph has ended, when nothing
    else is watching it."""
    assert _submit_click_violations(SESSION_MODULE.read_text()) == []


def test_the_session_module_is_covered_by_the_package_wide_scan():
    """The guard above is belt; this is braces. If the package glob ever stopped
    including this file, the recursive scan would go quietly green over it."""
    import test_applier_locate as loc

    assert SESSION_MODULE in set(loc._GUARDED_MODULES)


def test_the_driver_obeys_the_one_rule():
    """`server/applier_run.py` sits outside `agents/job_applier/`, so the package
    glob does not reach it — and it is the module that decides what happens to a
    live browser. Same four rules, applied explicitly."""
    assert _submit_click_violations(DRIVER_MODULE.read_text()) == []


def test_the_jobs_router_obeys_the_one_rule():
    assert _submit_click_violations(JOBS_ROUTER.read_text()) == []


def test_neither_new_server_module_imports_playwright():
    for path in (DRIVER_MODULE, JOBS_ROUTER):
        source = path.read_text()
        assert not re.search(r"^\s*(?:import|from)\s+playwright", source, re.M), path


def test_the_session_module_does_not_import_playwright_either():
    """Same property every other module in the package has: it is HANDED a page,
    it never constructs one — which is why the whole suite runs with no Chromium."""
    source = SESSION_MODULE.read_text()
    assert not re.search(r"^\s*(?:import|from)\s+playwright", source, re.M)


def test_the_new_modules_only_cite_tests_that_exist():
    """Eleven findings across Tasks 4-9 were prose asserting a property the code
    lacked. A citation that names a dead test reads as green forever, so every
    `test_*` named in these files must exist SOMEWHERE in tests/."""
    defined: set[str] = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_[a-z0-9_]+)", path.read_text(), re.M))
    for path in (SESSION_MODULE, DRIVER_MODULE, JOBS_ROUTER, MODAL, JOBS_LIB):
        # `(?!\.py)` so a reference to a test FILE ("tests/test_applier_ui.py")
        # is not read as a citation of a function called `test_applier_ui`.
        cited = set(re.findall(r"\b(test_[a-z0-9_]+)\b(?!\.py)", path.read_text()))
        missing = sorted(cited - defined)
        assert not missing, f"{path.name} cites tests that do not exist: {missing}"


# ===========================================================================
# 5. The endpoints
# ===========================================================================


def _seed(job: dict) -> dict:
    jobstore.replace_record(dict(job))
    return job


def test_assisted_apply_starts_a_run_and_records_no_application(client, monkeypatch):
    """The ordering that keeps the tracker honest. The agent fills a form and
    stops; whether an application exists depends on a human pressing Submit
    afterwards, so nothing is logged here."""
    _seed(_GREENHOUSE_JOB)
    started: list = []
    monkeypatch.setattr(
        applier_run, "start",
        lambda job_id, resume_path, loop: started.append((job_id, resume_path)) or 42,
    )
    res = client.post("/data/jobs/assisted-apply", json={"id": _GREENHOUSE_JOB["id"]})
    assert res.status_code == 200
    assert res.json()["run_id"] == 42
    assert started == [(_GREENHOUSE_JOB["id"], "")]
    assert appstore.load_all() == [], "nothing is applied until the human says so"
    assert jobstore.load_records()[_GREENHOUSE_JOB["id"]]["status"] == "new"


def test_the_generic_run_endpoint_refuses_the_applier_and_says_where_to_go(client):
    """The other door into this agent, closed.

    `POST /agents/{key}/run` resolved the applier's spec like any other agent and
    handed it to `runner.start_run`, i.e. to the pooled `astream` driver that
    `agents/job_applier/session.py` documents as a hard cross-thread failure for
    a graph holding a live Playwright context. Three separate things were wrong
    with that, and only the first is a crash:

      * `greenlet.error` when `fill` touches a browser `fetch_form` opened on a
        different pool worker;
      * nothing registers the run in `session._SESSIONS`, so the window it leaves
        open can never be closed by `/data/jobs/assisted-apply/close`;
      * a JSON body on this route is passed straight through as the graph's
        initial state, which for this agent includes `form_url` — the URL a
        browser carrying her live ATS cookies navigates to.

    409, not 400: the request is well-formed, it is the entry point that is
    wrong, and the message has to say which one is right or this is just a wall.
    """
    for body in (None, {"input": {"job_id": "Palantir:lever:1"}},
                 {"input": {"form_url": "https://evil.example.invalid/collect"}}):
        res = client.post("/agents/job_applier/run",
                          **({} if body is None else {"json": body}))
        assert res.status_code == 409, body
        error = res.json()["error"]
        assert "/data/jobs/assisted-apply" in error
        assert "run_id" not in res.json(), "a refused request must not create a run"
    # Not a blanket refusal of the endpoint: every other agent still starts here,
    # which is what makes the test above about `job_applier` and not about routing.
    assert set(runs_router._WRONG_ENTRY_POINT) == {"job_applier"}
    assert "job_applier" in REGISTRY, "the key has to be real for the refusal to bite"


def test_the_refused_run_leaves_no_row_in_the_runs_table(client, run_db):
    """The refusal happens before `db.create_run`, so a rejected call is not
    visible in the run history at all — a run row with no driver behind it shows
    up in the UI as an agent that started and never finished."""
    before = run_db.latest_run("job_applier")
    assert client.post("/agents/job_applier/run").status_code == 409
    assert run_db.latest_run("job_applier") == before


def test_assisted_apply_refuses_a_board_the_agent_cannot_read(client, monkeypatch):
    """Refused BEFORE a browser opens, so the UI can offer the manual path instead
    of showing the user a failed run."""
    _seed(_WORKDAY_JOB)
    monkeypatch.setattr(applier_run, "start", lambda *a, **k: pytest.fail("must not start"))
    res = client.post("/data/jobs/assisted-apply", json={"id": _WORKDAY_JOB["id"]})
    assert res.status_code == 400
    body = res.json()
    assert body["ats"] == "workday"
    for board in SUPPORTED_ATS:
        assert board in body["error"]


def test_assisted_apply_rejects_a_missing_or_unknown_job(client, monkeypatch):
    monkeypatch.setattr(applier_run, "start", lambda *a, **k: pytest.fail("must not start"))
    assert client.post("/data/jobs/assisted-apply", json={"id": ""}).status_code == 400
    assert client.post("/data/jobs/assisted-apply", json={"id": "nope"}).status_code == 404


def test_assisted_apply_refuses_a_job_already_applied_to(client, monkeypatch):
    _seed({**_GREENHOUSE_JOB, "status": "applied"})
    monkeypatch.setattr(applier_run, "start", lambda *a, **k: pytest.fail("must not start"))
    res = client.post("/data/jobs/assisted-apply", json={"id": _GREENHOUSE_JOB["id"]})
    assert res.status_code == 409


def test_assisted_apply_passes_a_real_resume_path_to_the_agent(
    client, monkeypatch, tmp_path
):
    """Kayla's auto-attach ruling only takes effect if the agent is handed a file.
    The path is a real one on disk, and the response names only its basename —
    the handoff goes to some length never to show the user a home directory."""
    _seed(_GREENHOUSE_JOB)
    _master(monkeypatch, tmp_path)
    seen: list = []
    monkeypatch.setattr(
        applier_run, "start",
        lambda job_id, resume_path, loop: seen.append(resume_path) or 7,
    )
    res = client.post("/data/jobs/assisted-apply", json={"id": _GREENHOUSE_JOB["id"]})
    assert res.status_code == 200
    assert res.json()["pdf_error"] is None
    assert pathlib.Path(seen[0]).is_file()
    assert res.json()["resume_filename"].endswith(".pdf")
    assert "/" not in res.json()["resume_filename"]


def test_assisted_apply_still_runs_when_the_resume_cannot_be_prepared(client, monkeypatch):
    """Not fatal — everything except the attach still gets filled — but reported,
    for the same reason /data/jobs/apply reports it: a green banner over a
    silently missing résumé is a lie."""
    _seed(_GREENHOUSE_JOB)
    seen: list = []
    monkeypatch.setattr(
        applier_run, "start",
        lambda job_id, resume_path, loop: seen.append(resume_path) or 7,
    )
    res = client.post("/data/jobs/assisted-apply", json={"id": _GREENHOUSE_JOB["id"]})
    assert res.status_code == 200
    assert res.json()["pdf_error"], "no master résumé exists in a temp DB"
    assert seen == [""], "the run proceeds with no résumé rather than not at all"


def test_assisted_apply_drives_a_real_run_end_to_end(client, monkeypatch):
    """The endpoint, the session thread and the driver together, with a fake graph.
    This is the wiring `RunStream` depends on: a run row, node events, and a report
    fetchable from the report endpoint."""
    _seed(_GREENHOUSE_JOB)
    report = _report(job_title="SWE Intern", company="Acme", browser_open=False)
    applier_run._graph_factory = lambda: _FakeGraph(
        _node_pair("load_profile", {})
        + _node_pair("handoff", {"report": report, "message": report.render_text()}))

    run_id = client.post(
        "/data/jobs/assisted-apply", json={"id": _GREENHOUSE_JOB["id"]}
    ).json()["run_id"]

    # The run is queued on the session thread; wait for it the way a UI would.
    session.run_now(lambda: None, timeout=15)
    from server import db as server_db

    assert server_db.get_run(run_id)["status"] == "success"
    payload = client.get(f"/data/jobs/assisted-apply/report?run_id={run_id}").json()
    assert payload["job_title"] == "SWE Intern"
    assert payload["submitted"] is False
    assert [e["node"] for e in server_db.get_node_events(run_id)] == [
        "load_profile", "load_profile", "handoff", "handoff",
    ]


def test_the_report_endpoint_404s_when_nothing_is_held(client):
    assert client.get("/data/jobs/assisted-apply/report?run_id=123").status_code == 404


# --- the confirmation trigger ---------------------------------------------


def _logged_application(client, job=_GREENHOUSE_JOB) -> int:
    _seed(job)
    res = client.post("/data/jobs/apply", json={"id": job["id"]})
    assert res.status_code == 200
    return int(res.json()["application_id"])


def _row(app_id: int) -> dict:
    return next(a for a in appstore.load_all() if a["id"] == app_id)


def _park(job_id: str, html: str, url: str) -> None:
    session.run_now(session.hand_over, session.LiveSession(
        job_id=job_id, run_id=1, form_url=url,
        page=_FakePage(html=html, url=url), browser=_FakeBrowser(),
    ))


def test_confirm_submission_stamps_only_a_real_confirmation_page(client):
    app_id = _logged_application(client)
    _park(_GREENHOUSE_JOB["id"], (FIXTURES / "lever-confirmation.html").read_text(),
          LEVER_THANKS_URL)
    res = client.post("/data/jobs/confirm-submission",
                      json={"id": _GREENHOUSE_JOB["id"], "application_id": app_id})
    body = res.json()
    assert body["checked"] is True and body["confirmed"] is True
    assert _row(app_id)["confirmed_at"]


def test_confirm_submission_on_the_real_form_page_leaves_the_row_identical(client):
    """The half of Task 9's evidence that is real: `lever-form.html` is captured
    from a live posting and contains confirmation-shaped strings in its CSS and in
    a question heading. Not "leaves it unconfirmed" — leaves it IDENTICAL."""
    app_id = _logged_application(client)
    before = _row(app_id)
    _park(_GREENHOUSE_JOB["id"], (FIXTURES / "lever-form.html").read_text(),
          LEVER_FORM_URL)
    body = client.post("/data/jobs/confirm-submission",
                       json={"id": _GREENHOUSE_JOB["id"], "application_id": app_id}).json()
    assert body["checked"] is True and body["confirmed"] is False
    assert body["reason"]
    assert _row(app_id) == before


def test_confirm_submission_with_no_window_is_a_dont_know_and_writes_nothing(
    client, monkeypatch
):
    app_id = _logged_application(client)
    monkeypatch.setattr(
        appstore, "mark_confirmed",
        lambda *a, **k: pytest.fail("a don't-know must not write"),
    )
    body = client.post("/data/jobs/confirm-submission",
                       json={"id": _GREENHOUSE_JOB["id"], "application_id": app_id}).json()
    assert body["checked"] is False and body["confirmed"] is False
    assert _row(app_id)["confirmed_at"] is None


def test_confirm_submission_never_un_confirms_an_already_verified_row(client):
    """The ruling, at the endpoint: a later non-match must not undo an earlier
    verification. The store's UPDATE is write-once and a non-match never reaches
    it, so this holds on both paths."""
    app_id = _logged_application(client)
    appstore.mark_confirmed(app_id, when="2026-08-02T12:00:00+00:00")
    _park(_GREENHOUSE_JOB["id"], (FIXTURES / "lever-form.html").read_text(),
          LEVER_FORM_URL)
    client.post("/data/jobs/confirm-submission",
                json={"id": _GREENHOUSE_JOB["id"], "application_id": app_id})
    assert _row(app_id)["confirmed_at"] == "2026-08-02T12:00:00+00:00"


def test_confirm_submission_keeps_the_first_timestamp_on_a_second_check(client):
    app_id = _logged_application(client)
    appstore.mark_confirmed(app_id, when="2026-08-02T12:00:00+00:00")
    _park(_GREENHOUSE_JOB["id"], (FIXTURES / "lever-confirmation.html").read_text(),
          LEVER_THANKS_URL)
    client.post("/data/jobs/confirm-submission",
                json={"id": _GREENHOUSE_JOB["id"], "application_id": app_id})
    assert _row(app_id)["confirmed_at"] == "2026-08-02T12:00:00+00:00"


def test_confirm_submission_refuses_an_application_belonging_to_another_job(client):
    """Same exact-`job_id` ownership check `undo_apply` makes, and for the same
    reason: company/role cannot tell two open reqs apart, and stamping the wrong
    row marks an application verified that nothing has looked at.

    Verified BEFORE the page is read, so a mismatched pair cannot even produce a
    verdict to be confused by."""
    app_id = _logged_application(client)
    _seed(_WORKDAY_JOB)
    _park(_WORKDAY_JOB["id"], (FIXTURES / "lever-confirmation.html").read_text(),
          LEVER_THANKS_URL)
    res = client.post("/data/jobs/confirm-submission",
                      json={"id": _WORKDAY_JOB["id"], "application_id": app_id})
    assert res.status_code == 409
    assert _row(app_id)["confirmed_at"] is None


def test_confirm_submission_validates_its_inputs(client):
    assert client.post("/data/jobs/confirm-submission", json={"id": ""}).status_code == 400
    assert client.post(
        "/data/jobs/confirm-submission", json={"id": "x", "application_id": 0}
    ).status_code == 400
    assert client.post(
        "/data/jobs/confirm-submission", json={"id": "x", "application_id": 999}
    ).status_code == 404


def test_the_close_endpoint_closes_the_window_and_is_idempotent(client):
    ctx = _FakeBrowser()
    session.run_now(session.hand_over, session.LiveSession(
        job_id="j", run_id=1, form_url="", page=_FakePage(), browser=ctx))
    assert client.post("/data/jobs/assisted-apply/close", json={"id": "j"}).json()["closed"] == 1
    assert client.post("/data/jobs/assisted-apply/close", json={"id": "j"}).json()["closed"] == 0
    assert ctx.closed == 1


# ===========================================================================
# 6. The UI — what it promises, and what it cannot do
# ===========================================================================

MODAL_SOURCE = MODAL.read_text()
JOBS_LIB_SOURCE = JOBS_LIB.read_text()


def _ts_string_array(source: str, name: str) -> list[str]:
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]", source, re.S)
    assert match, f"{name} not found"
    return re.findall(r'"([^"]+)"', match.group(1))


def _ts_object_keys(source: str, name: str) -> list[str]:
    match = re.search(rf"{name}[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert match, f"{name} not found"
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1), re.M)


def _ts_type_keys(source: str, name: str) -> list[str]:
    """Field names of an `export type Name = { … };` literal, comments ignored."""
    match = re.search(rf"export type {name} = \{{(.*?)\n\}};", source, re.S)
    assert match, f"type {name} not found"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.M)


def test_the_ui_handoff_item_type_carries_every_field_the_report_emits():
    """The two field lists are in two languages, so the mirror needs a test.

    A Python field with no TS counterpart is a signal the UI cannot see — which
    is exactly how `label_source` would have been added: the payload would carry
    it, `tsc` would be perfectly happy, and the "LABEL UNVERIFIED" warning would
    simply never render. Enumerated from the dataclass rather than listed by hand,
    so the NEXT field is covered without anyone remembering to come back here.
    """
    import dataclasses

    python_fields = [f.name for f in dataclasses.fields(handoff_mod.ReportItem)]
    assert "label_source" in python_fields, "the field this test was added for"
    assert set(_ts_type_keys(JOBS_LIB_SOURCE, "HandoffItem")) == set(python_fields)


def test_the_ui_handoff_report_type_carries_every_computed_field_too():
    """Same mirror for the report-level payload, which is the dataclass PLUS the
    computed fields `report_payload` adds — `required_caveat` among them, and a
    caveat the UI does not know about is a caveat that is never shown."""
    import dataclasses

    stored = [f.name for f in dataclasses.fields(handoff_mod.HandoffReport)]
    computed = ["headline", "instruction", "summary_line", "required_caveat",
                "counts", "needs_you", "total"]
    # `required_unknown` is the raw count behind `required_caveat`; the UI renders
    # the sentence, not the number, so it is deliberately not in the TS type.
    expected = (set(stored) | set(computed)) - {"required_unknown"}
    assert set(_ts_type_keys(JOBS_LIB_SOURCE, "HandoffReport")) == expected


def test_the_boards_the_ui_offers_autofill_for_are_the_ones_the_agent_supports():
    """A fourth board in the UI would be a button that always errors at
    `load_profile`'s first gate; a missing one hides a working feature. The lists
    are in two languages, so the mirror needs a test rather than a comment."""
    assert _ts_string_array(JOBS_LIB_SOURCE, "AUTOFILL_ATS") == list(SUPPORTED_ATS)


def test_the_ui_bands_match_the_report_bands():
    assert _ts_string_array(JOBS_LIB_SOURCE, "HANDOFF_GROUPS") == list(handoff_mod.GROUPS)


def test_the_ui_bands_are_in_the_order_the_report_defines():
    """`handoff.GROUPS` IS the ordering — blocking first because it is the only
    band that costs her the application if she misses it. A UI that reordered them
    would bury that."""
    ui = _ts_string_array(JOBS_LIB_SOURCE, "HANDOFF_GROUPS")
    assert ui[0] == handoff_mod.BLOCKING
    assert ui[-1] == handoff_mod.DONE


def test_the_ui_knows_every_reason_the_report_can_emit():
    """Only the KEYS are mirrored: the wording is the UI's own (a badge has less
    room than a sentence). A reason with no entry would render as a bare code."""
    assert set(_ts_object_keys(JOBS_LIB_SOURCE, "HANDOFF_REASON_TAG")) == set(
        handoff_mod.REASONS
    )


def test_the_modal_says_it_does_not_submit_before_anything_starts():
    """The brief's requirement, and the one that is easiest to satisfy in the wrong
    place: a promise made only in the report afterwards comes after a browser
    window has already appeared on her screen. So the sentence has to sit ABOVE
    the control that starts the run, in the file as well as on the page."""
    promise = MODAL_SOURCE.index("It does not submit it")
    button = MODAL_SOURCE.index("Open the form & autofill it")
    assert promise < button, "the guarantee must precede the button that starts it"
    assert "It fills the form." in MODAL_SOURCE
    # And it is repeated while the run is in flight, which is when it matters most.
    assert MODAL_SOURCE.count("not submit") >= 2


def test_the_modal_names_what_the_agent_always_leaves_to_the_user():
    """Work authorization is never guessed (`resolver.BLOCKING_KINDS`). Saying so
    up front is the difference between a blank field she looks for and a blank
    field she trusts was left deliberately."""
    assert "Work-authorization" in MODAL_SOURCE
    assert "self-identification" in MODAL_SOURCE


#: Every backend call the modal is allowed to make. An allowlist rather than a
#: "no submit endpoint" pattern: the harmful change is a NEW endpoint, and a
#: pattern only catches the ones somebody thought to name.
_ALLOWED_FETCHES = {
    "/agents/resume_generator/run",
    "/data/jobs/apply",
    "/data/jobs/assisted-apply",
    "/data/jobs/assisted-apply/report",
    "/data/jobs/assisted-apply/close",
    "/data/jobs/confirm-submission",
    "/data/jobs/resume-pdf",
}


def test_the_modal_can_only_reach_the_endpoints_it_is_supposed_to():
    """THE ONE RULE, on the UI side. There is no endpoint that submits an
    application, and this is what stops one being reached from here — including via
    a URL built out of a template or held in a variable, which is rejected outright
    because this scan cannot read its value.

    `window.open` is checked too, and only against a literal: the one variable it
    is allowed is `jobUrl`, the posting's own address straight off the `jobs` row.
    """
    called = set()
    for match in re.finditer(r"""\bfetch\(\s*(.)""", MODAL_SOURCE):
        quote = match.group(1)
        assert quote in "\"'`", f"a URL this scan cannot read: {match.group(0)!r}"
        tail = MODAL_SOURCE[match.end():]
        literal = tail[: tail.index(quote)]
        # Template literals are allowed only for a query string / path parameter
        # on an already-allowed route, so cut at the first interpolation.
        called.add(literal.split("${")[0].split("?")[0])
    assert len(called) >= 5, "the scan stopped finding the modal's calls"
    unexpected = {u for u in called if u not in _ALLOWED_FETCHES}
    assert not unexpected, f"the modal reaches unexpected endpoints: {unexpected}"

    opened = re.findall(r"""window\.open\(\s*(?:(["'`])([^"'`]*)|(\w+))""", MODAL_SOURCE)
    for quote, literal, variable in opened:
        if variable:
            assert variable == "jobUrl", f"window.open on an opaque {variable!r}"
            continue
        assert literal.split("${")[0].split("?")[0] in _ALLOWED_FETCHES, literal


def test_the_modal_offers_no_submit_affordance():
    """No form, no submit-typed control, no handler named for submitting. The word
    "Submit" appears in prose ("press Submit yourself") on purpose, which is why
    this looks at CONTROLS rather than at the text."""
    assert "<form" not in MODAL_SOURCE
    assert 'type="submit"' not in MODAL_SOURCE
    assert "formAction" not in MODAL_SOURCE
    assert "onSubmit" not in MODAL_SOURCE
    # No handler promises to do the submitting.
    for banned in ("submitForm", "doSubmit", "submitApplication", "autoSubmit"):
        assert banned not in MODAL_SOURCE, banned


def test_the_modal_renders_the_structured_report_not_the_rendered_text():
    """Ruling: use the structured data, not `render_text()` parsed. The report's
    text form is deliberately suppressed in the run stream (`showOutput={false}`)
    so the checklist is not printed twice."""
    assert "showOutput={false}" in MODAL_SOURCE
    assert "report.items" in JOBS_LIB_SOURCE or "items.filter" in JOBS_LIB_SOURCE
    assert "handoffGroup(report" in MODAL_SOURCE
    assert "render_text" not in MODAL_SOURCE
    assert "dangerouslySetInnerHTML" not in MODAL_SOURCE


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


def test_the_modal_shows_the_bands_in_the_reports_own_order():
    assert "HANDOFF_GROUPS.map" in MODAL_SOURCE
    # `done` is collapsed rather than dropped: it is spot-check material, and
    # dropping it would hide what the agent actually typed.
    assert "<details>" in MODAL_SOURCE


def test_the_modal_distinguishes_a_verified_application_from_an_optimistic_one():
    assert "verificationLine" in MODAL_SOURCE
    line_source = JOBS_LIB_SOURCE[JOBS_LIB_SOURCE.index("export function verificationLine"):]
    body = line_source[: line_source.index("\n}")]
    assert "Verified" in body
    assert "not verified" in body
    assert "not a failure" in body, "an unverified row is a don't-know, not a failure"
    assert "not submitted" not in body, (
        "saying 'not submitted' would be the same false claim in the other "
        "direction: nothing has checked most of these rows at all"
    )


def _modal_functions(source: str) -> list[str]:
    """Split the component into its handler bodies.

    Crude on purpose, and it is what makes the fetch-handling test below
    non-vacuous: a proximity check around each `fetch(` passed on this file only
    because two of the calls happen to sit near their `try`, and would have missed
    the second `fetch` in `logAndVerify` entirely.
    """
    parts = re.split(r"\n  (?:async function |function |const \w+ = useCallback)", source)
    return [p for p in parts if "fetch(" in p]


def test_every_fetch_in_the_modal_handles_its_own_failure():
    """`await fetch` with no error handling is a known pre-existing weakness in
    this app — nine unprotected sites in other tabs are documented follow-ups.
    This task does not add a tenth."""
    chunks = _modal_functions(MODAL_SOURCE)
    assert len(chunks) >= 4, "the splitter stopped finding the handlers"
    assert sum(c.count("fetch(") for c in chunks) == MODAL_SOURCE.count("fetch("), (
        "a fetch outside any handler this test can see"
    )
    for chunk in chunks:
        head = chunk.split("(")[0]
        assert "try {" in chunk, f"unguarded fetch in {head}"
        assert "catch" in chunk, f"no catch in {head}"


def test_the_modal_tells_the_user_when_the_agent_service_is_unreachable():
    assert "is FastAPI on :8001 running?" in MODAL_SOURCE


def test_the_board_hands_the_modal_the_scraped_ats_rather_than_guessing():
    """`agents/job_scraper/ats.py` decided the board when the posting was fetched.
    A second detector in the UI guessing from the URL is how the UI and the agent
    end up disagreeing about what a form is."""
    board = (WEB / "components" / "jobs" / "JobsBoard.tsx").read_text()
    assert "jobAts={applyFor.ats}" in board
    assert "greenhouse" not in board, "no ATS list belongs in the board"


def test_the_unsupported_board_note_names_the_board_and_the_way_forward():
    note_source = JOBS_LIB_SOURCE[
        JOBS_LIB_SOURCE.index("export function autofillUnavailableNote"):
    ]
    body = note_source[: note_source.index("\n}")]
    assert "${board}" in body, "say which board it is"
    assert "fill it in yourself" in body, "and what to do instead"


def test_the_run_stream_still_shows_its_output_for_every_other_caller():
    """`showOutput` defaults to true. A prop added for one caller that silently
    changed the other four would have removed every agent's report from the
    dashboard."""
    stream = (WEB / "components" / "dashboard" / "RunStream.tsx").read_text()
    assert "showOutput = true" in stream
    for caller in WEB.rglob("*.tsx"):
        text = caller.read_text()
        if "<RunStream" not in text or caller == MODAL:
            continue
        assert "showOutput" not in text, f"{caller.name} should keep the default"


def test_form_controls_have_a_base_style_so_none_can_render_unstyled():
    """The apply modal's <select> looked like plain text because the stylesheet had
    NO base rule for form controls — five call sites each styled their own, and
    this one was missed. A per-site pattern has no floor: every new control is one
    omission away from invisible. This test only checks the four declarations
    this rule promises (background/border/color/padding) and that the rule's
    block appears before the two local-override blocks in source order — it
    cannot verify the selector actually matches any real element in the DOM.
    An earlier version of this rule listed `input[type="text"]` etc., which
    matched nothing: most <input>s here carry no `type` attribute at all, so
    React never puts that attribute in the DOM for React to select on. The
    selector must also stay a plain type selector — via `:where()`, which adds
    zero specificity — so it keeps losing to `.form-grid input` /
    `.settings-form input` (0,1,1) instead of starting to win over them."""
    css = (WEB / "app" / "globals.css").read_text()
    base = css.index("/* BASE FORM CONTROLS")
    assert base < css.index(".form-grid input"), "base rule must precede local overrides"
    assert base < css.index(".settings-form input")
    block = css[base : css.index("}", base)]
    for prop in ("background:", "border:", "color:", "padding:"):
        assert prop in block, f"a control with no {prop} is invisible on a dark panel"
    assert ":where(" in block, "zero-specificity guard: :not() alone would outrank the per-site rules"
    assert 'input[type="text"]' not in block, "type-scoped clauses match nothing; React omits an unspecified type"


def test_the_resume_picker_is_labelled_as_a_control():
    assert "apply-field-label" in MODAL_SOURCE
    assert "Résumé to use" in MODAL_SOURCE


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
