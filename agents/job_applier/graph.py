"""LangGraph definition for the job-applier agent — Phase B's assembly point.

Linear pipeline, one node per stage:

    load_profile -> fetch_form -> resolve -> draft -> fill -> handoff

`agents/registry.py`'s `node_order` for `job_applier` must be exactly that
tuple; `test_the_registry_node_order_matches_every_graph` walks the compiled
graph's edges and fails if the two ever drift (Phase A found two registries that
already had).

Each stage is wrapped by `_step`, which gives the whole graph three properties
that individual nodes would otherwise each have to remember:

  1. **Short-circuit on `error`**, the way `resume_generator` does — the failing
     node sets `error` (a code) plus `message` (a sentence) and everything after
     it returns `{}`. The single exception is `handoff`, wrapped with
     `run_on_error=True`: a run that could not open a browser still owes the
     user a report saying so. Pinned by
     `test_a_run_that_cannot_open_a_browser_still_produces_a_handoff`.
  2. **An exception becomes a report, not a traceback.** Anything a node raises
     is turned into `error` + a sentence naming the step, and the run continues
     to `handoff`. `BaseException` is deliberately NOT converted — the suite's
     `ModelCalledInTest` guard derives from it precisely so it cannot be
     swallowed into an innocent-looking blank.
  3. **The browser is closed on every failing path**, including one where a node
     raised mid-graph. See below.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module opens and closes a browser and routes state; it never touches a
control.

Browser lifetime, stated explicitly because it is the one place where "always
close it" and "the product" pull in opposite directions
-------------------------------------------------------------------------------
`fetch_form` opens a headed, persistent Chromium and puts the
`ManagedBrowserContext` in the state. From then on:

  * **Every failing path closes it**, and then clears the handle out of the
    state, so "is there a window on screen" is answerable by a later node or a
    UI rather than being a stale object nobody can interpret. Four paths, each
    with its own pin:
      - a node raising mid-graph
        (`test_a_node_raising_mid_graph_closes_the_browser`);
      - the LAST node raising, which the handoff's own teardown cannot cover
        (`test_a_raise_in_the_final_node_still_closes_the_browser`);
      - a node returning `error` without raising
        (`test_the_browser_is_closed_when_a_node_after_it_sets_an_error`);
      - `fetch_form` getting a context but no readable page, which it must clean
        up itself because the context is not in the state yet
        (`test_a_page_that_cannot_be_read_closes_the_browser_it_opened`).
    That is what stops a crash from leaking a visible window plus an invisible
    Playwright driver subprocess into a long-lived server process.

  * **The successful path deliberately leaves it OPEN**, and that is not an
    oversight. The entire product is "the agent fills the form and stops so the
    human reviews it and presses Submit themselves" — closing the window on a
    successful fill would throw away every field the agent just typed, one
    keystroke before the only action that matters. So on success the graph hands
    the open window to the human and says so in the report.
    `release_browser(final_state)` is exported for whoever ends that session
    (Task 9's confirmation detection, or a UI teardown).
    (`test_a_successful_run_leaves_the_window_open_for_the_human`.)

  * **The report is told which of the two happened**, via
    `HandoffReport.browser_open`. It used to be unconditional prose — "the
    browser window is still open on this form, waiting for you", printed on the
    paths where the graph had just closed the window or had never opened one.
    (`test_a_failed_run_never_claims_a_browser_window_is_open`.)

KNOWN LIMIT: `release_browser` can only close the context it finds under
`state["browser"]`. A future node that REPLACES that context — a re-navigate step
for Task 9's confirmation detection is the obvious candidate — would orphan the
one it displaced, and nothing here would notice. No node does today; one that
needs a second context must close the first itself, or the state has to grow a
list.

Note on the docstring citations above:
`test_every_test_the_graph_docstring_cites_actually_exists` catches a name that
no longer exists. It does NOT catch a citation naming a real test that does not
pin the claim beside it — a mis-citation reads as green. This file shipped two of
those in Task 8; they are corrected above.
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agents.job_applier import browser
from agents.job_applier.nodes.draft import draft_node
from agents.job_applier.nodes.fetch_form import fetch_form_node
from agents.job_applier.nodes.fill import fill_node
from agents.job_applier.nodes.handoff import handoff_node
from agents.job_applier.nodes.load_profile import load_profile_node
from agents.job_applier.nodes.resolve import resolve_node
from agents.job_applier.state import ApplierState

#: The chain, in order. `graph.py` and `agents/registry.py` both read from a
#: tuple rather than repeating a list of strings, so the two cannot disagree by
#: a typo — but the registry still gets an independent test against the COMPILED
#: graph, because "they import the same constant" is a weaker claim than "the
#: graph really executes these nodes in this order".
NODE_ORDER: tuple[str, ...] = (
    "load_profile", "fetch_form", "resolve", "draft", "fill", "handoff",
)


def release_browser(state: Any) -> bool:
    """Close the run's browser if it has one. Returns whether it did anything.

    Idempotent and total: `ManagedBrowserContext.close()` is itself idempotent,
    a state with no browser is fine, and `browser.close_quietly` swallows a
    teardown failure — that failure must not become the thing the user hears
    about instead of their half-filled form.

    It also clears the handle out of the mapping it was given, best-effort, so a
    closed context cannot be mistaken for a live one. The authoritative clear is
    the `{"browser": None}` the graph's own steps return; this covers a caller
    that holds the final state and closes it by hand.
    """
    context = (state or {}).get("browser")
    closed = browser.close_quietly(context)
    if context is not None:
        try:
            state["browser"] = None
        except Exception:
            pass  # a read-only mapping is not a reason to fail a teardown
    return closed


def _step(
    name: str, fn: Callable[[dict], dict], *, run_on_error: bool = False
) -> Callable[[dict], dict]:
    """Wrap one node with the error/short-circuit/teardown convention."""

    def node(state: dict) -> dict:
        if state.get("error") and not run_on_error:
            return {}
        try:
            out = fn(state) or {}
        except Exception as exc:
            release_browser(state)
            return {
                "error": f"{name}_failed",
                "message": (
                    f"The agent stopped at the “{name}” step "
                    f"({type(exc).__name__}: {exc})."
                ),
                # The handle is closed; say so in the state, or the next reader
                # cannot tell a live window from a dead one.
                "browser": None,
            }
        except BaseException:
            # Not converted into a report: KeyboardInterrupt, and the test
            # suite's ModelCalledInTest, both mean "stop", not "degrade". The
            # browser still gets closed on the way out.
            release_browser(state)
            raise
        if run_on_error and (state.get("error") or out.get("error")):
            release_browser(state)
            return {**out, "browser": None}
        return out

    node.__name__ = name
    return node


def build_job_applier_graph(*, send: bool = False):
    """Compile and return the job-applier graph (`send` is accepted, unused).

    `send` is the kwarg the agent registry passes to every builder; this agent
    delivers nothing to Discord — its whole output is the handoff the user reads
    — so the flag is accepted and ignored, exactly as `resume_generator` does.

    Invoke with `{"job_id": "<id>"}`, optionally plus `resume_path` and
    `form_url`.
    """
    g = StateGraph(ApplierState)

    g.add_node("load_profile", _step("load_profile", load_profile_node))
    g.add_node("fetch_form", _step("fetch_form", fetch_form_node))
    g.add_node("resolve", _step("resolve", resolve_node))
    g.add_node("draft", _step("draft", draft_node))
    g.add_node("fill", _step("fill", fill_node))
    g.add_node("handoff", _step("handoff", handoff_node, run_on_error=True))

    g.add_edge(START, "load_profile")
    g.add_edge("load_profile", "fetch_form")
    g.add_edge("fetch_form", "resolve")
    g.add_edge("resolve", "draft")
    g.add_edge("draft", "fill")
    g.add_edge("fill", "handoff")
    g.add_edge("handoff", END)

    return g.compile()
