"""Load node — the posting row, the applicant profile, and the grounding text.

This is the pipeline's fail-fast gate, the same role `resume_generator`'s
`gather` plays: it refuses to proceed (setting `error`, which every later node
honors) when there is no job id, the id is unknown, the row has no URL to open,
or the board is one the locator was never built for.

It reads the DB and nothing else — no browser, no network, no model.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module writes no browser code at all.
"""

from __future__ import annotations

import profile_store
from agents.job_scraper import store as jobstore
from agents.resume_generator import store as resume_store

#: Boards Phase B knows how to read. Everything in `locate_dom` was written and
#: measured against these three captured forms; a fourth board would be a guess
#: about a DOM nobody has looked at, typed into a real employer's form. The
#: honest answer for anything else is "open it yourself", which is exactly what
#: Phase A's manual flow already does.
SUPPORTED_ATS: tuple[str, ...] = ("greenhouse", "lever", "ashby")

#: `jobs.country` -> the resolver's `default_country`. "UNKNOWN" and "OTHER" map
#: to "" on purpose: they name no profile field, and the resolver treats "" as
#: "the posting tells me nothing", which leaves unnamed-country eligibility
#: questions blank exactly as they were before Task 8.
_COUNTRY_TO_PROFILE_FIELD = {"US": "us", "CA": "ca"}


def default_country_of(job: dict) -> str:
    """"us", "ca", or "" for a posting row. Never guesses from the location
    string: `jobs.country` is a derived, validated column and the free-text
    location is not ("Cambridge" is in two countries)."""
    return _COUNTRY_TO_PROFILE_FIELD.get(str((job or {}).get("country") or "").strip().upper(), "")


def _grounding_text() -> str:
    """The master résumé plus the experience pool, as one block of text.

    Same two sources `resume_generator`'s gather node uses, and for the same
    reason: they are the only place this system holds facts about the user that
    a drafted answer may be built from. Unlike that node, an empty result is NOT
    fatal here — drafting declines the experience questions with a note and the
    rest of the form still gets filled.
    """
    parts: list[str] = []
    try:
        master = resume_store.get_master_resume()
        latex = (master.get("latex") or "").strip()
        markdown = (master.get("markdown") or "").strip()
        if latex:
            parts.append(f"### MASTER RESUME (LaTeX source)\n{latex}")
        elif markdown:
            parts.append(f"### MASTER RESUME\n{markdown}")
        pool = resume_store.load_experience_text()
        if pool:
            parts.append(pool)
    except Exception:
        # A missing/*empty* résumé table is not a reason to abandon an
        # application: everything except the two draftable free-text questions
        # is resolved from the profile, and those degrade to "write this one
        # yourself" rather than to a fabrication.
        return ""
    return "\n\n".join(parts).strip()


def load_profile_node(state: dict) -> dict:
    job_id = (state.get("job_id") or "").strip()
    if not job_id:
        return {
            "error": "no_job",
            "message": (
                "Assisted apply is per-job. Start it from a specific job "
                "(pass --job-id on the CLI)."
            ),
        }

    job = jobstore.load_records().get(job_id)
    if job is None:
        return {"error": "no_job", "message": f"No scraped job found for id '{job_id}'."}

    ats = str(job.get("ats") or "").strip().lower()
    if ats not in SUPPORTED_ATS:
        return {
            "error": "unsupported_ats",
            "message": (
                f"This posting is on “{ats or 'an unknown board'}”, and assisted "
                f"apply only knows how to read {', '.join(SUPPORTED_ATS)} forms. "
                f"Open the posting and fill it in yourself."
            ),
        }

    if not str(state.get("form_url") or job.get("url") or "").strip():
        return {
            "error": "no_url",
            "message": (
                f"“{job.get('title') or job_id}” has no URL saved, so there is no "
                f"form to open."
            ),
        }

    return {
        "job": job,
        "profile": profile_store.get_profile(),
        "default_country": default_country_of(job),
        "experience": _grounding_text(),
    }
