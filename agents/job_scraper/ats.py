"""ATS adapters — fetch postings from public job-board JSON APIs (no key).

Each adapter takes a board `token`, calls the provider's public endpoint, and
returns a list of postings normalized to the common shape:

    {"company": str, "ats": str, "title": str, "location": str,
     "url": str, "id": str}

`id` is prefixed with "<company>:<ats>:<native id>" so ids are globally unique
across companies and providers (two firms can share a native posting id).

Adapters raise on transport/parse failure; the fetch node wraps each call in
try/except so one bad source never kills the run.
"""

from __future__ import annotations

import httpx

_TIMEOUT = 20


def _gid(company: str, ats: str, native_id: object) -> str:
    """Build a globally unique posting id."""
    return f"{company}:{ats}:{native_id}"


def fetch_greenhouse(company: str, token: str) -> list[dict]:
    """Greenhouse: GET boards-api.greenhouse.io/v1/boards/<token>/jobs."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = httpx.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name") or "—"
        out.append(
            {
                "company": company,
                "ats": "greenhouse",
                "title": (j.get("title") or "").strip(),
                "location": loc,
                "url": j.get("absolute_url") or "",
                "id": _gid(company, "greenhouse", j.get("id")),
            }
        )
    return out


def fetch_lever(company: str, token: str) -> list[dict]:
    """Lever: GET api.lever.co/v0/postings/<token>?mode=json (returns a list)."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = httpx.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Lever response was not a JSON list")
    out: list[dict] = []
    for j in data:
        loc = (j.get("categories") or {}).get("location") or "—"
        out.append(
            {
                "company": company,
                "ats": "lever",
                "title": (j.get("text") or "").strip(),
                "location": loc,
                "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                "id": _gid(company, "lever", j.get("id")),
            }
        )
    return out


def fetch_ashby(company: str, token: str) -> list[dict]:
    """Ashby: GET api.ashbyhq.com/posting-api/job-board/<token>.

    NOTE: Ashby's public posting-api responds to GET. (A POST with a body, as
    some docs suggest, returns HTTP 401 on this endpoint.) The board `token`
    is case-sensitive.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    # includeCompensation passed as a query param; harmless if ignored.
    resp = httpx.get(url, params={"includeCompensation": "false"}, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobs", []):
        out.append(
            {
                "company": company,
                "ats": "ashby",
                "title": (j.get("title") or "").strip(),
                "location": j.get("location") or "—",
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "id": _gid(company, "ashby", j.get("id")),
            }
        )
    return out


# Dispatch table: ats name -> adapter fn.
ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def fetch_source(source: dict) -> list[dict]:
    """Fetch + normalize one source dict {company, ats, token}.

    Raises KeyError/ValueError for an unknown ATS or malformed source so the
    caller can log a warning and move on.
    """
    ats = source.get("ats", "")
    adapter = ADAPTERS.get(ats)
    if adapter is None:
        raise ValueError(f"unknown ATS '{ats}' (expected one of {sorted(ADAPTERS)})")
    return adapter(source["company"], source["token"])
