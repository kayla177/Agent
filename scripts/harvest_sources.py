"""Harvest company → (ats, token) sources from the zapplyjobs job-list repos.

Their READMEs are markdown tables of curated early-career roles whose "Apply"
links are DIRECT ATS board URLs. We parse those links to recover each company's
ATS provider + board token for the adapters we support (greenhouse / ashby /
lever / smartrecruiters / workable / workday), dedupe, and write them to
`agents/job_scraper/sources_seed.json` — a committed list we OWN, so we keep the
companies (and keep scraping them ourselves via ats.py) even if the upstream
repo changes format or disappears.

Run:  python scripts/harvest_sources.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REPOS = [
    "zapplyjobs/Internships-2027",
    "zapplyjobs/New-Grad-Jobs-2027",
    "zapplyjobs/New-Grad-Software-Engineering-Jobs-2027",
    "zapplyjobs/New-Grad-Data-Science-Jobs-2027",
]
_SEED = Path(__file__).resolve().parent.parent / "agents" / "job_scraper" / "sources_seed.json"

# ATS providers to EXCLUDE from the committed seed. Workday tokens are composite
# "tenant/wd/board" strings (high-entropy → tripped secret scanners as false
# positives) and its boards were the least reliable (many 404'd). The adapter
# still supports Workday — add specific boards via JOB_SOURCES in settings.
_SKIP_ATS = {"workday"}

# One markdown table row: | **Company** | … | [<img …>](apply_url) |
_ROW_RE = re.compile(
    r"^\|\s*\*\*(?P<company>[^*|]+?)\*\*\s*\|.*?\]\((?P<url>https?://[^)\s]+)\)",
    re.MULTILINE,
)
_WD_RE = re.compile(r"^([^.]+)\.(wd\d+)\.myworkdayjobs\.com$")


def _ats_token(url: str) -> tuple[str, str] | None:
    """Map a direct ATS apply URL to (ats, board_token); None if unsupported."""
    m = re.match(r"https?://([^/]+)/([^/?#]+)", url)
    if not m:
        return None
    host, first = m.group(1).lower(), m.group(2)
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        return ("greenhouse", first)
    if host == "jobs.ashbyhq.com":
        return ("ashby", first)
    if host == "jobs.lever.co":
        return ("lever", first)
    if host == "jobs.smartrecruiters.com":
        return ("smartrecruiters", first)
    if host == "apply.workable.com":
        return ("workable", first)
    wd = _WD_RE.match(host)
    if wd:
        tenant, sub = wd.group(1), wd.group(2)
        return ("workday", f"{tenant}/{sub}/{first}")  # first path seg = board
    return None


def harvest() -> list[dict]:
    seen: dict[tuple[str, str], str] = {}   # (ats, token) -> company (first wins)
    skipped: dict[str, int] = {}
    for repo in _REPOS:
        url = f"https://raw.githubusercontent.com/{repo}/main/README.md"
        try:
            md = httpx.get(url, timeout=30, follow_redirects=True).text
        except Exception as exc:
            print(f"  ! {repo}: {exc}")
            continue
        added = 0
        for m in _ROW_RE.finditer(md):
            company = re.sub(r"\s+", " ", m.group("company")).strip()
            at = _ats_token(m.group("url"))
            if at is None:
                host = re.match(r"https?://([^/]+)", m.group("url")).group(1)
                skipped[host] = skipped.get(host, 0) + 1
                continue
            if at[0] in _SKIP_ATS:
                continue
            if at not in seen:
                seen[at] = company
                added += 1
        print(f"  {repo}: +{added} new sources")

    sources = [{"company": c, "ats": a, "token": t} for (a, t), c in seen.items()]
    sources.sort(key=lambda s: (s["ats"], s["company"].lower()))

    top_skipped = sorted(skipped.items(), key=lambda x: -x[1])[:8]
    print(f"\nHarvested {len(sources)} unique sources across {len(_REPOS)} repos.")
    by_ats: dict[str, int] = {}
    for s in sources:
        by_ats[s["ats"]] = by_ats.get(s["ats"], 0) + 1
    print("  by ATS:", dict(sorted(by_ats.items(), key=lambda x: -x[1])))
    print("  skipped unsupported hosts (top):", dict(top_skipped))
    return sources


def main() -> int:
    sources = harvest()
    _SEED.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote {len(sources)} sources → {_SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
