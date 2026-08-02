"""Shared state for the job-applier graph.

Linear chain: `load_profile -> fetch_form -> resolve -> draft -> fill -> handoff`.
`load_profile` seeds the posting row, the profile row and the grounding text;
each later node reads what it needs and writes its own key. If a node cannot
proceed it sets `error` (a short machine code) plus `message` (a human sentence),
and every later node short-circuits on `error` — the same convention
`agents/resume_generator/state.py` uses.

Two things are NOT the same as the resume generator, both deliberate:

  * **`handoff` runs even when `error` is set.** A run that dies before the
    browser opens still has to tell the user what happened AND that nothing was
    submitted; a silent empty report is the failure mode Task 8's brief calls
    out by name. So `handoff` is the one node that does not short-circuit: it
    folds `message` into the report's `error` field and then overwrites
    `message` with the rendered handoff.

  * **The state carries live objects** — a `ManagedBrowserContext`, a Playwright
    `Page`, a `PageLocator`. There is no checkpointer on this graph, so nothing
    ever tries to serialize them. `graph.release_browser()` is what closes the
    browser; see `graph.py` for who calls it and when.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module is a TypedDict and holds no code at all.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ApplierState(TypedDict, total=False):
    # -- input -------------------------------------------------------------
    #: The scraped posting's id (from `agents.job_scraper.store`). Required.
    job_id: str
    #: Absolute path to the résumé PDF to attach, if the caller has one. Empty
    #: is normal and supported: the handoff then says, once, that no résumé was
    #: attached. Nothing here ever invents a path.
    resume_path: str
    #: Overrides the URL `fetch_form` would derive from the posting row. Exists
    #: for the case where the row's `url` is a company redirector rather than
    #: the ATS form itself.
    form_url: str

    # -- load_profile ------------------------------------------------------
    job: dict
    profile: dict
    #: "us" / "ca" / "" — the posting's country, normalised for the resolver's
    #: `default_country`. See `nodes/load_profile.py`.
    default_country: str
    #: Grounding text for the drafting step (master résumé + experience pool).
    #: Empty is not an error: drafting then declines the experience questions
    #: with a note saying why, which is the honest outcome.
    experience: str

    # -- fetch_form --------------------------------------------------------
    browser: Any        # agents.job_applier.browser.ManagedBrowserContext
    page: Any           # playwright Page
    locator: Any        # agents.job_applier.locate_dom.PageLocator
    questions: list     # list[schema_greenhouse.Question]

    # -- resolve / draft ---------------------------------------------------
    resolved: list      # list[resolver.Answer], one per question
    drafted: list       # list[resolver.Answer], one per question
    #: The merged list actually handed to the executor. Drafting wins for every
    #: question it owns, refusals included — see `nodes/draft.merge_answers`.
    answers: list

    # -- fill --------------------------------------------------------------
    fill_report: Any    # nodes.fill.FillReport

    # -- handoff -----------------------------------------------------------
    report: Any         # nodes.handoff.HandoffReport
    #: The rendered handoff. This is the registry's `output_key`.
    message: str

    # -- control -----------------------------------------------------------
    #: Short code set by the first node that could not proceed; halts the chain.
    #: The human sentence that goes with it is in `message`, which the handoff
    #: node then folds into the report as its "stopped early" line.
    error: str
