"""Target companies and their Applicant Tracking System (ATS) job boards.

============================================================================
HOW TO USE YOUR OWN COMPANIES
============================================================================
Replace the entries in SOURCES below with the companies YOU care about. Each
entry is a dict: {"company": <display name>, "ats": <"greenhouse"|"lever"|
"ashby"|"smartrecruiters"|"workable"|"workday">, "token": <the board token>}.

How to find a company's ATS and token:
  - Visit the company's careers page and look at the job-listing URL.
  - GREENHOUSE: URL looks like  boards.greenhouse.io/<token>  or the page
    embeds  boards-api.greenhouse.io/v1/boards/<token>/jobs . The <token> is
    the slug (usually the lowercase company name, e.g. "cloudflare").
    Verify:  https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  - LEVER: URL looks like  jobs.lever.co/<token> . The <token> is that slug
    (e.g. "palantir").
    Verify:  https://api.lever.co/v0/postings/<token>?mode=json
  - ASHBY: URL looks like  jobs.ashbyhq.com/<token> . The <token> is that
    slug and is CASE-SENSITIVE (e.g. "Ramp", not "ramp").
    Verify:  https://api.ashbyhq.com/posting-api/job-board/<token>
  - SMARTRECRUITERS: URL looks like  jobs.smartrecruiters.com/<token> . The
    <token> is the company identifier (e.g. "McDonaldsCorporation").
    Verify:  https://api.smartrecruiters.com/v1/companies/<token>/postings
  - WORKABLE: URL looks like  apply.workable.com/<token> . The <token> is the
    account subdomain slug.
    Verify:  https://apply.workable.com/api/v1/widget/accounts/<token>?details=true
  - WORKDAY: custom host like  <tenant>.<wd>.myworkdayjobs.com/<board> . The
    <token> encodes all three as "tenant/wd/board" (e.g.
    "nvidia/wd5/NVIDIAExternalCareerSite").
    Verify (POST):  https://<tenant>.<wd>.myworkdayjobs.com/wday/cxs/<tenant>/<board>/jobs

If the verify URL returns JSON with jobs/postings, the token is good.
============================================================================

The entries below are REAL, working public boards verified at build time;
they are placeholders/examples. Swap them for your real targets.
"""

from __future__ import annotations

import json
from pathlib import Path

import config

_SEED_FILE = Path(__file__).resolve().parent / "sources_seed.json"

# Module defaults (REAL, working public boards verified at build time). The web
# settings page can override these via the prefs overlay (config.JOB_SOURCES);
# an empty overlay falls back to these, so CLI/launchd runs behave as before.
_DEFAULT_SOURCES: list[dict] = [
    # --- From Kayla's target list (verified reachable) ---
    {"company": "Carta", "ats": "greenhouse", "token": "carta"},
    {"company": "Databricks", "ats": "greenhouse", "token": "databricks"},
    {"company": "Pinterest", "ats": "greenhouse", "token": "pinterest"},
    {"company": "Snowflake", "ats": "ashby", "token": "snowflake"},
    # --- Popular tech companies (verified reachable at build time) ---
    {"company": "Stripe", "ats": "greenhouse", "token": "stripe"},
    {"company": "Figma", "ats": "greenhouse", "token": "figma"},
    {"company": "Airbnb", "ats": "greenhouse", "token": "airbnb"},
    {"company": "Coinbase", "ats": "greenhouse", "token": "coinbase"},
    {"company": "Robinhood", "ats": "greenhouse", "token": "robinhood"},
    {"company": "Dropbox", "ats": "greenhouse", "token": "dropbox"},
    {"company": "Reddit", "ats": "greenhouse", "token": "reddit"},
    {"company": "Discord", "ats": "greenhouse", "token": "discord"},
    {"company": "Cloudflare", "ats": "greenhouse", "token": "cloudflare"},
    {"company": "Datadog", "ats": "greenhouse", "token": "datadog"},
    {"company": "Brex", "ats": "greenhouse", "token": "brex"},
    {"company": "Notion", "ats": "ashby", "token": "notion"},
    {"company": "Plaid", "ats": "ashby", "token": "plaid"},
    {"company": "Ramp", "ats": "ashby", "token": "ramp"},
    # NOTE: Apple, Google, Amazon, NVIDIA, AMD, Sony, Rippling use Workday or
    # custom career systems. These are now reachable via the "workday" adapter
    # (token = "tenant/wd/board"); add them here once you confirm the tenant/board
    # from the careers URL. Amazon/Google use bespoke systems still not covered.
]

# Harvested companies (agents/job_scraper/sources_seed.json), built by
# scripts/harvest_sources.py from the zapplyjobs job-list repos. Committed so we
# own the list even if upstream changes; regenerate any time by re-running it.
def _load_seed() -> list[dict]:
    try:
        data = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("ats") and s.get("token")]


def _merge(*groups: list[dict]) -> list[dict]:
    """Dedupe sources by (ats, token); the first group to name a board wins."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for group in groups:
        for s in group:
            key = (s.get("ats", ""), s.get("token", ""))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            out.append({"company": s.get("company", ""), "ats": key[0], "token": key[1]})
    return out


_SEED_SOURCES = _load_seed()


def get_sources() -> list[dict]:
    """Live source list: curated defaults + the harvested seed + any user
    JOB_SOURCES from settings — ADDITIVE and deduped (a custom list adds to, not
    replaces, the curated companies). Read at call time so a settings save
    (followed by config.refresh()) is reflected on the next run."""
    return _merge(_DEFAULT_SOURCES, _SEED_SOURCES, config.JOB_SOURCES or [])


# Snapshot constant (used by CLI/launchd; computed once at import).
SOURCES: list[dict] = get_sources()
