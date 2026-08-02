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

  * **Every failing path closes it.** A node that raises, a node that sets
    `error`, a `fetch_form` that got a page but could not read it — all of them
    end with `release_browser`, which is idempotent and never raises. That is
    what stops a crash from leaking a visible window plus an invisible
    Playwright driver subprocess into a long-lived server process.
    (`test_a_node_raising_mid_graph_closes_the_browser`,
    `test_the_browser_is_closed_when_a_node_after_it_sets_an_error`.)

  * **The successful path deliberately leaves it OPEN**, and that is not an
    oversight. The entire product is "the agent fills the form and stops so the
    human reviews it and presses Submit themselves" — closing the window on a
    successful fill would throw away every field the agent just typed, one
    keystroke before the only action that matters. So on success the graph hands
    the open window to the human and says so in the report.
    `release_browser(final_state)` is exported for whoever ends that session
    (Task 9's confirmation detection, or a UI teardown).
    (`test_a_successful_run_leaves_the_window_open_for_the_human`.)
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

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
    a state with no browser is fine, and an exception while closing is
    swallowed — a teardown failure must not become the thing the user hears
    about instead of their half-filled form.
    """
    context = (state or {}).get("browser")
    if context is None:
        return False
    try:
        context.close()
    except Exception:
        return False
    return True


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
            }
        except BaseException:
            # Not converted into a report: KeyboardInterrupt, and the test
            # suite's ModelCalledInTest, both mean "stop", not "degrade". The
            # browser still gets closed on the way out.
            release_browser(state)
            raise
        if run_on_error and (state.get("error") or out.get("error")):
            release_browser(state)
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
