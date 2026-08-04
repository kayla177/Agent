"""Resolve node — profile + questions -> one deterministic answer each.

A three-line adapter over `agents.job_applier.resolver`, and that is the point:
the resolver is PURE and stays that way, so the DB read (`load_profile`) and
the DOM read (`fetch_form`) both happen before it and are handed in as plain
data.

The one decision made here is passing `default_country`, which `load_profile`
derived from the posting row's validated `country` column. It exists because
several real eligibility questions name no country at all ("Are you legally
authorized to work in the country for which you are applying?") and the
resolver used to blank them for exactly that reason while the posting row knew
the answer all along. It does NOT weaken the refusal: the eligibility kinds are
all in `resolver.BLOCKING_KINDS`, so every one of them is still surfaced for the
human to confirm, and never auto-typed on the strength of a guess.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module writes no browser code at all.
"""

from __future__ import annotations

from agents.job_applier import resolver


def resolve_node(state: dict) -> dict:
    return {
        "resolved": resolver.resolve(
            state.get("questions") or [],
            state.get("profile") or {},
            default_country=state.get("default_country") or "",
        )
    }
