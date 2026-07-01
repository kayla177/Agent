"""ATS adapters — fetch postings from public job-board JSON APIs (no key).

Each adapter takes a board `token`, calls the provider's public endpoint, and
returns a list of postings normalized to the common shape:

    {"company": str, "ats": str, "title": str, "location": str,
     "url": str, "id": str,
     # enrichment (best-effort; "" / None when the provider omits it)
     "posted_at": str,      # ISO date the role was first published (YYYY-MM-DD)
     "updated_at": str,     # ISO date last updated
     "deadline": str,       # application deadline, if any
     "remote": bool | None, # True/False when known, else None
     "department": str,
     "compensation": str,   # human-readable pay, when the board exposes it
     "description": str}    # plain-text JD (truncated), used by fit-ranking

All enrichment fields come from the SAME response the adapter already fetches —
no extra HTTP calls. `id` is prefixed with "<company>:<ats>:<native id>" so ids
are globally unique across companies and providers.

Adapters raise on transport/parse failure; the fetch node wraps each call in
try/except so one bad source never kills the run.
"""

from __future__ import annotations

import datetime as dt
import html
import re

import httpx

_TIMEOUT = 20
# Cap stored descriptions so the seen-store / LLM prompts stay small.
_DESC_MAX = 1200

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def _gid(company: str, ats: str, native_id: object) -> str:
    """Build a globally unique posting id."""
    return f"{company}:{ats}:{native_id}"


def _strip_html(raw: str | None) -> str:
    """Turn an HTML job description into readable plain text (truncated)."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub("\n", text).strip()
    return text[:_DESC_MAX]


def _to_iso_date(value: object) -> str:
    """Normalize an ISO string or epoch-ms int to a YYYY-MM-DD date string."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            # Lever gives epoch milliseconds.
            return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).date().isoformat()
        s = str(value).strip().replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def _has_remote(text: str | None) -> bool | None:
    """Infer remote from a location/workplace string; None if uninformative."""
    if not text:
        return None
    return True if "remote" in text.lower() else None


def fetch_greenhouse(company: str, token: str) -> list[dict]:
    """Greenhouse: GET boards-api.greenhouse.io/v1/boards/<token>/jobs."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = httpx.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name") or "—"
        depts = j.get("departments") or []
        out.append(
            {
                "company": company,
                "ats": "greenhouse",
                "title": (j.get("title") or "").strip(),
                "location": loc,
                "url": j.get("absolute_url") or "",
                "id": _gid(company, "greenhouse", j.get("id")),
                "posted_at": _to_iso_date(j.get("first_published")),
                "updated_at": _to_iso_date(j.get("updated_at")),
                "deadline": _to_iso_date(j.get("application_deadline")),
                "remote": _has_remote(loc),
                "department": (depts[0].get("name") if depts else "") or "",
                "compensation": None,
                "description": _strip_html(j.get("content")),
            }
        )
    return out


def _fmt_lever_comp(salary: object) -> str | None:
    if not isinstance(salary, dict):
        return None
    lo, hi = salary.get("min"), salary.get("max")
    cur = salary.get("currency") or ""
    if lo and hi:
        return f"{cur} {lo:,}–{hi:,}".strip()
    return None


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
        cats = j.get("categories") or {}
        loc = cats.get("location") or "—"
        workplace = (j.get("workplaceType") or "").lower()
        remote = True if workplace == "remote" else _has_remote(loc)
        out.append(
            {
                "company": company,
                "ats": "lever",
                "title": (j.get("text") or "").strip(),
                "location": loc,
                "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                "id": _gid(company, "lever", j.get("id")),
                "posted_at": _to_iso_date(j.get("createdAt")),
                "updated_at": "",
                "deadline": "",
                "remote": remote,
                "department": cats.get("team") or "",
                "compensation": _fmt_lever_comp(j.get("salaryRange")),
                "description": (j.get("descriptionPlain") or "")[:_DESC_MAX],
            }
        )
    return out


def _fmt_ashby_comp(comp: object) -> str | None:
    if not isinstance(comp, dict):
        return None
    for key in ("compensationTierSummary", "scrapeableCompensationSalarySummary"):
        val = comp.get(key)
        if val:
            return str(val)
    return None


def fetch_ashby(company: str, token: str) -> list[dict]:
    """Ashby: GET api.ashbyhq.com/posting-api/job-board/<token>.

    NOTE: Ashby's public posting-api responds to GET. (A POST with a body, as
    some docs suggest, returns HTTP 401 on this endpoint.) The board `token`
    is case-sensitive.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    resp = httpx.get(url, params={"includeCompensation": "true"}, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobs", []):
        loc = j.get("location") or "—"
        remote = j.get("isRemote")
        if remote is None:
            workplace = (j.get("workplaceType") or "").lower()
            remote = True if workplace == "remote" else _has_remote(loc)
        out.append(
            {
                "company": company,
                "ats": "ashby",
                "title": (j.get("title") or "").strip(),
                "location": loc,
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "id": _gid(company, "ashby", j.get("id")),
                "posted_at": _to_iso_date(j.get("publishedAt")),
                "updated_at": "",
                "deadline": "",
                "remote": remote,
                "department": j.get("department") or j.get("team") or "",
                "compensation": _fmt_ashby_comp(j.get("compensation")),
                "description": (j.get("descriptionPlain") or "")[:_DESC_MAX],
                # Ashby-only signal used by the freshness/ghost node.
                "listed": j.get("isListed"),
            }
        )
    return out


def fetch_smartrecruiters(company: str, token: str) -> list[dict]:
    """SmartRecruiters: GET api.smartrecruiters.com/v1/companies/<token>/postings.

    Public, keyless. `token` is the company identifier (e.g. "McDonaldsCorporation").
    The postings list has no full description, so `description` is left empty —
    title + location still drive matching and fit-ranking.
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    resp = httpx.get(url, params={"limit": 100}, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("content", []):
        loc = j.get("location") or {}
        dept = j.get("department") or {}
        pid = j.get("id")
        out.append(
            {
                "company": company,
                "ats": "smartrecruiters",
                "title": (j.get("name") or "").strip(),
                "location": loc.get("fullLocation")
                or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
                or "—",
                "url": f"https://jobs.smartrecruiters.com/{token}/{pid}",
                "id": _gid(company, "smartrecruiters", pid),
                "posted_at": _to_iso_date(j.get("releasedDate")),
                "updated_at": "",
                "deadline": "",
                "remote": bool(loc.get("remote")) if "remote" in loc else None,
                "department": dept.get("label") or dept.get("name") or "",
                "compensation": None,
                "description": "",
            }
        )
    return out


def fetch_workable(company: str, token: str) -> list[dict]:
    """Workable: GET apply.workable.com/api/v1/widget/accounts/<token>?details=true.

    Public careers widget (keyless). `token` is the account subdomain slug. The
    spi/v3 API requires an API key; this widget endpoint does not.
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}"
    resp = httpx.get(url, params={"details": "true"}, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobs", []):
        loc = j.get("location") or {}
        parts = [loc.get("city"), loc.get("region"), loc.get("country")]
        location = ", ".join(x for x in parts if x) or "—"
        remote = loc.get("telecommuting")
        out.append(
            {
                "company": company,
                "ats": "workable",
                "title": (j.get("title") or "").strip(),
                "location": location,
                "url": j.get("url") or j.get("application_url") or "",
                "id": _gid(company, "workable", j.get("shortcode") or j.get("id")),
                "posted_at": _to_iso_date(j.get("published_on") or j.get("created_at")),
                "updated_at": "",
                "deadline": "",
                "remote": bool(remote) if remote is not None else _has_remote(location),
                "department": j.get("department") or "",
                "compensation": None,
                "description": _strip_html(j.get("description")),
            }
        )
    return out


def fetch_workday(company: str, token: str) -> list[dict]:
    """Workday: POST <tenant>.<wd>.myworkdayjobs.com/wday/cxs/<tenant>/<board>/jobs.

    Unlocks the big custom-career-system boards (Apple/NVIDIA-class) that have no
    Greenhouse/Lever/Ashby feed. `token` encodes the per-tenant host + board as
    "tenant/wd/board" (e.g. "nvidia/wd5/NVIDIAExternalCareerSite"). Workday's
    postedOn field is a relative string ("Posted 5 Days Ago"), not a date, so
    `posted_at` is left empty.
    """
    try:
        tenant, wd, board = token.split("/", 2)
    except ValueError:
        raise ValueError(
            f"workday token must be 'tenant/wd/board', got {token!r}"
        ) from None
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    url = f"{host}/wday/cxs/{tenant}/{board}/jobs"
    resp = httpx.post(
        url,
        json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
        headers={"Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    out: list[dict] = []
    for j in resp.json().get("jobPostings", []):
        path = j.get("externalPath") or ""
        out.append(
            {
                "company": company,
                "ats": "workday",
                "title": (j.get("title") or "").strip(),
                "location": j.get("locationsText") or "—",
                "url": f"{host}/{board}{path}",
                "id": _gid(company, "workday", path or j.get("bulletFields", [""])[0]),
                "posted_at": "",
                "updated_at": "",
                "deadline": "",
                "remote": _has_remote(j.get("locationsText")),
                "department": "",
                "compensation": None,
                "description": "",
            }
        )
    return out


# Dispatch table: ats name -> adapter fn.
ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
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
