"""Target companies and their Applicant Tracking System (ATS) job boards.

============================================================================
HOW TO USE YOUR OWN COMPANIES
============================================================================
Replace the entries in SOURCES below with the companies YOU care about. Each
entry is a dict: {"company": <display name>, "ats": <"greenhouse"|"lever"|
"ashby">, "token": <the board token>}.

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

If the verify URL returns JSON with jobs/postings, the token is good.
============================================================================

The entries below are REAL, working public boards verified at build time;
they are placeholders/examples. Swap them for your real targets.
"""

from __future__ import annotations

import config

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
    # custom career systems with no open API — not reachable via this scraper.
    # Supporting them would require a separate Workday adapter (future work).
]

# company display name, ATS provider, and the board token.
# Snapshot constant (used by CLI/launchd; computed once at import).
SOURCES: list[dict] = config.JOB_SOURCES or _DEFAULT_SOURCES


def get_sources() -> list[dict]:
    """Live source list — read config at CALL time so a web settings save
    (followed by config.refresh()) is reflected on the next run."""
    return config.JOB_SOURCES or _DEFAULT_SOURCES
