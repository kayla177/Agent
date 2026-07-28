# Jobs Tab Quality — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the jobs vertical — give every posting a real fit score, classify every posting by country and hide non-US/Canada roles, make the Apply button actually open the posting and record which résumé was used (undoably), and cover it all with pytest.

**Architecture:** A deterministic keyword scorer (`scoring.py`) runs on every posting so a score always exists; the LLM only *refines* it. A new `backfill` node between `dedupe` and `freshness` pulls stored rows back through the pipeline tail, which simultaneously scores the 523-row backlog, recomputes `ghost`, and refreshes `last_seen`. A new `applicant_profile` table supplies both the fit-scoring profile text and (in Phase B) typed autofill values. Apply becomes a modal that picks a résumé, compiles its PDF, opens the posting, and writes a tracker row that can be undone.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, SQLite (stdlib `sqlite3`), Next.js 16 App Router + TypeScript + Prisma 6, pytest (new dev dep), Ollama `llama3.1:8b` via LiteLLM.

**Spec:** `docs/superpowers/specs/2026-07-25-jobs-quality-and-autoapply-design.md`

## Global Constraints

- **`uv` is NOT installed.** Every Python command uses `.venv/bin/python`. Install deps with `.venv/bin/pip`. Never write `uv run` in code, docs, or docstrings — fix it where you find it.
- **Uvicorn does not reload-watch.** After adding or changing any route, restart with `.venv/bin/python -m server`.
- **`schema.sql` at the repo root is the single source of truth** for every table. Any new column ALSO needs an idempotent guard in `store_db.py:_migrate()` (schema.sql only covers fresh DBs) AND a mirror in `web-next/prisma/schema.prisma`. Then `cd web-next && npm run db:check` must pass.
- **FastAPI is the only DB writer.** Next.js reads via Prisma and mutates by POST/PATCH to `/data/*`. Never add a Prisma write.
- **`jobs.id` is a String** containing `:` (e.g. `Databricks:greenhouse:7586263002`). Mutation routes take the id in the **request body**, never a path segment, and must never apply a numeric guard.
- **The `jobs` row stores status in BOTH the mirrored `status` column and the `data` JSON blob.** Only ever change status via `agents/job_scraper/store.py:set_status`, which writes both.
- **Job statuses:** `new, viewed, applied, dismissed`. **Application statuses:** `applied, interview, offer, accepted, rejected`.
- **Fit tiers (unchanged):** hi ≥ 75 green `#7fc08a`, mid ≥ 50 amber `#e0b15a`, lo < 50 red `#e0705a`.
- **Mars theme** for the jobs tab (accent `#e07a4a`).
- **Never drop a posting on an `UNKNOWN` country.** Ambiguous locations are kept and badged.
- **The LLM must never overwrite a good baseline score with `NULL`.**
- Tests never hit the network or a live ATS site.
- **There is no JS/TS test harness in this repo and this plan does not add one.** Frontend tasks (11, 12, 13) are verified by `npm run lint`, `npm run build`, and the explicit browser checklist in the task. This matches the precedent set by `docs/superpowers/plans/2026-07-21-plan4-jobs-tab.md`. Absence of component tests in a frontend task is therefore per-spec, not a defect.

---

## File structure

```
scoring.py                                 -> agents/job_scraper/scoring.py    # NEW deterministic fit baseline
locations.py                               -> agents/job_scraper/locations.py  # NEW country_of()
profile_store.py                                                              # NEW applicant_profile store (repo root, beside store_db.py)
resume_pdf.py                              -> server/resume_pdf.py            # NEW PDF cache
agents/job_scraper/nodes/backfill.py                                          # NEW backfill node
agents/job_scraper/nodes/rank.py                                              # MOD baseline + refinement
agents/job_scraper/nodes/filter.py                                            # MOD tag country, stop location-dropping
agents/job_scraper/nodes/notify.py                                            # MOD digest filter + skip _rescored
agents/job_scraper/graph.py                                                   # MOD wire backfill
agents/job_scraper/state.py                                                   # MOD document new keys
agents/application_tracker/store.py                                           # MOD add_application gains resume params
agents/registry.py                                                            # MOD add missing latexify node
server/routers/jobs.py                                                        # MOD apply/undo-apply/status/rerank
server/routers/profile.py                                                     # NEW profile GET/PUT
server/prefs.py                                                               # MOD merge-not-replace + JOB_* keys
server/routers/prefs.py                                                       # MOD pass JOB_* through
server/app.py                                                                 # MOD include profile router
schema.sql                                                                    # MOD country, resume_pdf_key, applicant_profile
store_db.py                                                                   # MOD _migrate guards
web-next/prisma/schema.prisma                                                 # MOD mirror
web-next/src/lib/jobs.ts                                                      # MOD country types + filters
web-next/src/components/jobs/ApplyModal.tsx                                   # NEW résumé picker modal
web-next/src/components/jobs/JobsBoard.tsx                                    # MOD modal, country filter, undo, restore
web-next/src/components/jobs/JobRow.tsx                                       # MOD viewed, country badge, restore
web-next/src/components/jobs/ScoreBacklogButton.tsx                           # NEW
web-next/src/components/settings/ProfileForm.tsx                              # NEW
web-next/src/components/settings/SettingsForm.tsx                             # MOD JOB_* fields
tests/conftest.py                                                             # NEW pytest fixtures
tests/test_locations.py  test_scoring.py  test_rank.py  test_backfill.py
tests/test_jobs_router.py  test_profile.py  test_prefs.py                     # NEW
```

---

## Task 1: pytest harness + port the existing suite

**Files:**
- Create: `tests/conftest.py`
- Modify: `pyproject.toml`, `tests/test_job_scraper.py`, `tests/test_stores_sqlite.py`, `tests/test_gmail_sync.py`

**Interfaces:**
- Consumes: nothing.
- Produces: fixture `temp_db` (monkeypatches `store_db.DB_PATH` to a tmp file and calls `store_db.init_db()`); fixture `client` (FastAPI `TestClient` bound to `server.app.app`).

- [ ] **Step 1: Install pytest**

```bash
.venv/bin/pip install pytest
```

- [ ] **Step 2: Add pytest to pyproject.toml**

Append to `pyproject.toml`:

```toml
[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Write conftest.py**

```python
"""Shared pytest fixtures.

`temp_db` repoints the whole storage layer at a throwaway SQLite file, replacing
the old hand-rolled `_use_temp_store()` that mutated a module global with no
cleanup. Every store reads `store_db.DB_PATH` at call time, so monkeypatching it
is enough to isolate a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store_db  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point store_db at a fresh DB built from schema.sql; yield its path."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    store_db.init_db()
    return db


@pytest.fixture
def client(temp_db):
    """FastAPI TestClient sharing the temp DB (import is lazy: server pulls in
    litellm/google libs, which we only want loaded for router tests)."""
    from fastapi.testclient import TestClient

    from server.app import app

    with TestClient(app) as c:
        yield c
```

- [ ] **Step 4: Run the new harness to confirm it collects**

Run: `.venv/bin/python -m pytest tests/ --collect-only -q`
Expected: collects the existing `test_*` functions (they are already plain `def test_*`). The three `main()` blocks still exist and are harmless.

- [ ] **Step 5: Replace `_use_temp_store` usage with the fixture**

In `tests/test_job_scraper.py`, change `test_dedupe_cross_source()` to take the fixture and delete the `_use_temp_store` helper:

```python
def test_dedupe_cross_source(temp_db) -> None:
    filtered = [
        {"id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern", "location": "NYC", "ats": "greenhouse"},
        {"id": "Acme:lever:9", "company": "Acme", "title": "SWE  Intern", "location": "Manhattan", "ats": "lever"},
        {"id": "Acme:greenhouse:2", "company": "Acme", "title": "ML Intern", "location": "Remote", "ats": "greenhouse"},
    ]
    new = dedupe_node({"filtered": filtered})["new"]
    assert len(new) == 2
    survivor = next(p for p in new if p["title"].startswith("SWE"))
    assert "lever" in survivor.get("also_on", [])
```

Do the same for the `_use_temp_store` calls in `tests/test_stores_sqlite.py`, and convert its `check(...)` calls to bare `assert`.

- [ ] **Step 6: Fix the broken `uv run` docstrings**

In all three test files, replace `uv run python tests/...` with `.venv/bin/python -m pytest tests/...`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml tests/
git commit -m "test: adopt pytest, replace global-mutating temp store with a fixture"
```

---

## Task 2: `locations.py` — country classification

**Files:**
- Create: `agents/job_scraper/locations.py`
- Test: `tests/test_locations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `country_of(location: str) -> str` returning exactly `"US"`, `"CA"`, `"OTHER"`, or `"UNKNOWN"`. Also `COUNTRIES = ("US", "CA", "OTHER", "UNKNOWN")`.

- [ ] **Step 1: Write the failing test**

`tests/test_locations.py`:

```python
"""Country classification tests.

The Milwaukee case is the important one: a naive substring check for "uk" matches
inside "Milwaukee", which is the same false-positive trap matching.py already
documents for role keywords. Every check here must be word-boundary anchored.
"""

from __future__ import annotations

import pytest

from agents.job_scraper.locations import country_of


@pytest.mark.parametrize("loc", [
    "New York, NY", "Austin, TX", "Santa Clara, CA", "San Francisco",
    "Washington, D.C.", "Harrison, NJ, United States", "Cambridge, MA USA",
    "Milwaukee, WI", "Remote - USA", "United States", "Starbase, TX",
    "South San Francisco, California, United States", "Palo Alto, California",
])
def test_us_locations(loc):
    assert country_of(loc) == "US"


@pytest.mark.parametrize("loc", [
    "Toronto, Ontario", "Vancouver, British Columbia", "Waterloo, ON",
    "Montreal, Quebec, Canada", "Calgary, AB",
])
def test_ca_locations(loc):
    assert country_of(loc) == "CA"


@pytest.mark.parametrize("loc", [
    "London", "Singapore", "Amsterdam", "Dublin, Ireland", "Hanoi, , Vietnam",
    "IN - Bangalore, India", "Berlin, Germany", "Sydney, Australia",
    "Petaling Jaya, Selangor, Malaysia", "Beijing, China", "Eindhoven, Netherlands",
])
def test_other_locations(loc):
    assert country_of(loc) == "OTHER"


@pytest.mark.parametrize("loc", [
    "", "—", "2 Locations", "In-Office", "Stamford Hub",
    "Flexible - Any SpaceX Site", "Remote",
])
def test_unknown_locations(loc):
    assert country_of(loc) == "UNKNOWN"


def test_multi_location_keeps_north_america():
    """A posting listing a US site AND a foreign site is a real US job."""
    assert country_of("Austin, Texas, United States; South San Francisco, California") == "US"
    assert country_of("Amsterdam; Austin, TX") == "US"
    assert country_of("London / Toronto, Ontario") == "CA"


def test_india_abbreviation_is_not_indiana():
    """'IN - Bangalore, India' must not match Indiana's 'IN' postal code."""
    assert country_of("IN - Bangalore, India") == "OTHER"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.job_scraper.locations'`

- [ ] **Step 3: Write the implementation**

`agents/job_scraper/locations.py`:

```python
"""Classify a free-form ATS location string into US / Canada / elsewhere.

Deterministic, no LLM, no network. Two rules make this correct rather than
merely plausible:

1. EVERY match is word-boundary anchored. A substring check for "uk" matches
   inside "Milwaukee" — the same trap matching.py documents for role keywords.
2. Classification is PER SEGMENT, and a posting listing both a US and a foreign
   site is US. Multi-site postings like "Austin, Texas; Amsterdam" are real US
   jobs, so aggregating US/CA over segments beats classifying the whole string.

Within a segment, foreign names are checked BEFORE two-letter postal codes, so
"IN - Bangalore, India" resolves to OTHER instead of matching Indiana's "IN".

Ambiguous strings ("2 Locations", "Stamford Hub", bare "Remote") return UNKNOWN
and are never dropped by callers. Bare "London" resolves to OTHER (the UK one);
"London, ON" resolves to CA.
"""

from __future__ import annotations

import re

COUNTRIES = ("US", "CA", "OTHER", "UNKNOWN")

_SEGMENT_SPLIT = re.compile(r"[;/|]|\s+and\s+", re.IGNORECASE)

_CA_NAMES = (
    "canada", "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland", "labrador",
    "prince edward island", "yukon", "nunavut", "northwest territories",
    "toronto", "vancouver", "montreal", "ottawa", "calgary", "edmonton",
    "waterloo", "kitchener", "mississauga", "hamilton", "winnipeg", "halifax",
    "victoria", "burnaby", "markham", "vaughan", "quebec city", "gatineau",
)
_CA_ABBR = (
    "ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU",
)

_US_NAMES = (
    "united states", "usa", "u.s.a.", "u.s.", "america",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "d.c.", "dc", "puerto rico",
    "san francisco", "new york city", "nyc", "seattle", "austin", "boston",
    "chicago", "atlanta", "denver", "los angeles", "san jose", "san diego",
    "palo alto", "mountain view", "sunnyvale", "santa clara", "cupertino",
    "bellevue", "redmond", "cambridge, ma", "brooklyn", "manhattan", "bay area",
    "starbase", "peachtree corners", "morristown", "stamford", "irvine",
)
_US_ABBR = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "PR",
)

_FOREIGN_NAMES = (
    "united kingdom", "england", "scotland", "wales", "ireland", "london",
    "dublin", "edinburgh", "glasgow", "belfast", "cork", "galway", "manchester",
    "germany", "berlin", "munich", "hamburg", "frankfurt", "cologne",
    "france", "paris", "lyon", "netherlands", "amsterdam", "eindhoven",
    "rotterdam", "utrecht", "belgium", "brussels", "spain", "madrid",
    "barcelona", "portugal", "lisbon", "italy", "milan", "rome",
    "switzerland", "zurich", "geneva", "austria", "vienna", "poland", "warsaw",
    "krakow", "czech", "prague", "hungary", "budapest", "romania", "bucharest",
    "sweden", "stockholm", "norway", "oslo", "denmark", "copenhagen",
    "finland", "helsinki", "iceland", "reykjavik", "greece", "athens",
    "india", "bangalore", "bengaluru", "gurgaon", "gurugram", "hyderabad",
    "pune", "mumbai", "delhi", "chennai", "noida",
    "china", "beijing", "shanghai", "shenzhen", "guangzhou", "hong kong",
    "taiwan", "taipei", "japan", "tokyo", "osaka", "korea", "seoul",
    "singapore", "malaysia", "kuala lumpur", "selangor", "penang",
    "indonesia", "jakarta", "thailand", "bangkok", "ayutthaya",
    "vietnam", "hanoi", "ho chi minh", "philippines", "manila",
    "australia", "sydney", "melbourne", "brisbane", "perth",
    "new zealand", "auckland", "wellington",
    "israel", "tel aviv", "jerusalem", "haifa", "united arab emirates",
    "dubai", "abu dhabi", "saudi arabia", "riyadh", "qatar", "doha",
    "turkey", "istanbul", "egypt", "cairo", "kenya", "nairobi",
    "nigeria", "lagos", "south africa", "cape town", "johannesburg",
    "brazil", "sao paulo", "rio de janeiro", "argentina", "buenos aires",
    "chile", "santiago", "colombia", "bogota", "peru", "lima",
    "mexico", "mexico city", "guadalajara", "costa rica", "san jose, costa rica",
    "ukraine", "kyiv", "estonia", "tallinn", "lithuania", "vilnius",
)


def _word_re(terms) -> re.Pattern:
    """Case-insensitive alternation, word-boundary anchored, longest-first so
    'new york city' wins over 'new york'."""
    ordered = sorted(terms, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in ordered) + r")\b", re.IGNORECASE)


def _abbr_re(codes) -> re.Pattern:
    """Two-letter postal codes: word-boundary anchored AND case-SENSITIVE, so
    'in' inside a sentence never matches Indiana."""
    return re.compile(r"\b(?:" + "|".join(codes) + r")\b")


_CA_NAME_RE = _word_re(_CA_NAMES)
_US_NAME_RE = _word_re(_US_NAMES)
_FOREIGN_RE = _word_re(_FOREIGN_NAMES)
_CA_ABBR_RE = _abbr_re(_CA_ABBR)
_US_ABBR_RE = _abbr_re(_US_ABBR)


def _classify_segment(seg: str) -> str:
    """Classify ONE location segment. Foreign names are tested before postal
    codes so 'IN - Bangalore, India' is OTHER, not Indiana."""
    s = seg.strip()
    if not s or s == "—":
        return "UNKNOWN"
    if _CA_NAME_RE.search(s):
        return "CA"
    if _FOREIGN_RE.search(s):
        return "OTHER"
    if _US_NAME_RE.search(s):
        return "US"
    if _CA_ABBR_RE.search(s):
        return "CA"
    if _US_ABBR_RE.search(s):
        return "US"
    return "UNKNOWN"


def country_of(location: str) -> str:
    """Classify a free-form location as US | CA | OTHER | UNKNOWN.

    North America wins over foreign: a posting listing both a US site and a
    foreign site is a real US job, so any US/CA segment decides the result.
    """
    text = (location or "").strip()
    if not text:
        return "UNKNOWN"
    results = [_classify_segment(seg) for seg in _SEGMENT_SPLIT.split(text)]
    if "US" in results:
        return "US"
    if "CA" in results:
        return "CA"
    if "OTHER" in results:
        return "OTHER"
    return "UNKNOWN"
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`
Expected: all PASS. If a case fails, adjust the term lists — do NOT loosen the word-boundary anchoring or make the abbreviation regex case-insensitive.

- [ ] **Step 5: Sanity-check against real data**

Run:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import store_db
from collections import Counter
from agents.job_scraper.locations import country_of
with store_db.connect() as c:
    rows=c.execute(\"SELECT location FROM jobs WHERE status='new'\").fetchall()
print(Counter(country_of(r['location']) for r in rows))
"
```

Expected: roughly `US` ~95, `CA` ~5, `OTHER` ~20, `UNKNOWN` ~10 out of 130. Spot-check any `OTHER` that looks American and any `US` that looks foreign, and fix the lists.

- [ ] **Step 6: Commit**

```bash
git add agents/job_scraper/locations.py tests/test_locations.py
git commit -m "feat(jobs): word-boundary country classifier (US/CA/OTHER/UNKNOWN)"
```

---

## Task 3: `scoring.py` — deterministic fit baseline

**Files:**
- Create: `agents/job_scraper/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `extract_keywords(profile: str) -> list[str]`
  - `score_baseline(keywords: list[str], posting: dict) -> tuple[int, str]` — returns `(0-100, reason)`
  - `is_baseline_reason(reason: str) -> bool` — True when a `fit_reason` was written by the baseline (not the LLM). This is how Task 5's backfill finds rows still needing LLM refinement, so it must stay in sync with the reason strings this module produces.

- [ ] **Step 1: Write the failing test**

`tests/test_scoring.py`:

```python
"""Deterministic fit-baseline tests.

The baseline exists so fit_score is NEVER null: the 8B model returns a null
score for roughly 60% of postings even with a profile set, and nulls sink to the
bottom of sort-by-fit, hiding good roles.
"""

from __future__ import annotations

from agents.job_scraper.scoring import extract_keywords, is_baseline_reason, score_baseline


def test_extract_keywords_pulls_skill_terms():
    kws = extract_keywords("3rd-year CS undergrad. Python, TypeScript, React, SQL and LangGraph.")
    assert "python" in kws
    assert "typescript" in kws
    assert "react" in kws
    # Stopwords and filler must not become keywords.
    assert "and" not in kws
    assert "3rd" not in kws


def test_score_rises_with_overlap():
    kws = ["python", "react", "sql"]
    none_matched = score_baseline(kws, {"title": "Chef Intern", "description": "Cook food."})[0]
    one_matched = score_baseline(kws, {"title": "Intern", "description": "You will use Python."})[0]
    all_matched = score_baseline(kws, {"title": "SWE Intern", "description": "Python, React, SQL."})[0]
    assert none_matched < one_matched < all_matched
    assert 0 <= none_matched and all_matched <= 100


def test_reason_names_the_matches():
    _, reason = score_baseline(["python", "sql"], {"title": "X", "description": "Python and SQL work"})
    assert "Python" in reason or "python" in reason
    assert is_baseline_reason(reason)


def test_no_keywords_gives_neutral_score_and_marked_reason():
    score, reason = score_baseline([], {"title": "SWE Intern", "description": "anything"})
    assert score == 50
    assert is_baseline_reason(reason)


def test_word_boundary_no_false_positive():
    """'r' must not match inside 'Research'; 'go' must not match 'Going'.

    Both postings share the SAME title so the target-role title bonus is held
    constant and only the description varies — otherwise this compares 35 to 25
    and fails for a reason that has nothing to do with word boundaries.
    """
    kws = ["go", "r"]
    tricky = score_baseline(kws, {"title": "Research Intern", "description": "Going deep."})[0]
    clean = score_baseline(kws, {"title": "Research Intern", "description": "Nothing here."})[0]
    assert tricky == clean


def test_target_role_title_bonus():
    kws = ["python"]
    intern = score_baseline(kws, {"title": "Software Engineer Intern", "description": "Python"})[0]
    unclear = score_baseline(kws, {"title": "Software Engineer II", "description": "Python"})[0]
    assert intern > unclear


def test_llm_reason_is_not_baseline():
    assert not is_baseline_reason("Strong Python background, great fit for the team")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.job_scraper.scoring'`

- [ ] **Step 3: Write the implementation**

`agents/job_scraper/scoring.py`:

```python
"""Deterministic fit scoring — the baseline that guarantees a score exists.

Measured on 2026-07-25: llama3.1:8b returned fit_score=null for 3 of 5 postings
even WITH a candidate profile set. Since null sorts to the bottom of sort-by-fit,
a partly-null column actively hides good roles. So every posting gets a cheap,
explainable keyword-overlap score here, and the LLM in rank.py only OVERRIDES it
when it returns a usable integer.

Matching is word-boundary anchored (see locations.py for the same rule and why).
"""

from __future__ import annotations

import re

from agents.job_scraper.matching import is_target_role

# Words that are never useful as skill keywords.
_STOPWORDS = frozenset("""
a an and are as at be but by for from had has have i in into is it its of on or
that the to was were will with you your my me our we they this these those am
year years yr yrs student undergrad undergraduate currently seeking looking role
roles job jobs work working experience skills using use used strong good great
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*", re.IGNORECASE)

_NO_KEYWORDS_REASON = "no profile keywords set"
_MATCH_PREFIX = "matched: "

# Score shape: a floor so an unmatched posting is still comparable, most of the
# range driven by overlap, and a small bonus for an explicitly early-career title.
#
# Overlap SATURATES at _SATURATE matches rather than dividing by the keyword
# count. A real profile carries non-skill tokens ("waterloo", "us", "canada",
# "co", "op"), so hits/len(keywords) punishes a descriptive profile: measured on
# a realistic 13-keyword profile, a posting matching Python+React+SQL scored 49
# — mid-tier for what is plainly a strong match. Saturating puts that at 71 and a
# five-skill match at 95, which lands correctly across the hi/mid/lo tiers.
_FLOOR = 25
_OVERLAP_RANGE = 60
_TITLE_BONUS = 10
_SATURATE = 5


def extract_keywords(profile: str) -> list[str]:
    """Skill-ish terms from the free-text profile, lowercased and deduped."""
    seen: list[str] = []
    for raw in _TOKEN_RE.findall(profile or ""):
        term = raw.lower().strip(".")
        # Skip anything starting with a digit: "3rd-year" tokenizes to "3rd",
        # which is ordinal noise, not a skill. `term.isdigit()` alone misses it.
        if len(term) < 2 or term in _STOPWORDS or term[0].isdigit():
            continue
        if term not in seen:
            seen.append(term)
    return seen


def _matches(keywords: list[str], haystack: str) -> list[str]:
    """Keywords present in the haystack, word-boundary anchored."""
    hits: list[str] = []
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack, re.IGNORECASE):
            hits.append(kw)
    return hits


def score_baseline(keywords: list[str], posting: dict) -> tuple[int, str]:
    """Return (0-100 score, explainable reason) for one posting.

    With no keywords there is nothing to compare, so every posting scores a
    neutral 50 — never null, which is the whole point of this module.
    """
    title = posting.get("title") or ""
    if not keywords:
        return 50, _NO_KEYWORDS_REASON

    haystack = f"{title}\n{posting.get('description') or ''}"
    hits = _matches(keywords, haystack)

    score = _FLOOR + round(_OVERLAP_RANGE * min(1.0, len(hits) / _SATURATE))
    if is_target_role(title):
        score += _TITLE_BONUS
    score = max(0, min(100, score))

    if hits:
        shown = ", ".join(h.title() if h.islower() else h for h in hits[:5])
        reason = f"{_MATCH_PREFIX}{shown}"
    else:
        reason = f"{_MATCH_PREFIX}none"
    return score, reason


def is_baseline_reason(reason: str) -> bool:
    """True if `reason` was written by this module rather than the LLM.

    rank.py overwrites fit_reason when the model returns a usable score, so a
    still-baseline reason marks a row that has not been LLM-refined yet. The
    backfill node selects on exactly this.
    """
    r = (reason or "").strip()
    return r.startswith(_MATCH_PREFIX) or r == _NO_KEYWORDS_REASON
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_scoring.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/job_scraper/scoring.py tests/test_scoring.py
git commit -m "feat(jobs): deterministic fit baseline so fit_score is never null"
```

---

## Task 4: Schema — `country`, `resume_pdf_key`, `applicant_profile`

**Files:**
- Modify: `schema.sql`, `store_db.py:_migrate`, `web-next/prisma/schema.prisma`, `ARCHITECTURE.md`
- Test: `tests/test_schema.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `jobs.country TEXT NOT NULL DEFAULT ''`; `applications.resume_pdf_key TEXT`; table `applicant_profile` with the columns listed below.

- [ ] **Step 1: Write the failing test**

`tests/test_schema.py`:

```python
"""Schema guards — every new column must exist in a fresh DB AND after a
migration of a pre-existing one (schema.sql only covers fresh databases)."""

from __future__ import annotations

import store_db


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_jobs_has_country(temp_db):
    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")


def test_applications_has_resume_pdf_key(temp_db):
    with store_db.connect() as conn:
        assert "resume_pdf_key" in _cols(conn, "applications")


def test_applicant_profile_table(temp_db):
    with store_db.connect() as conn:
        cols = _cols(conn, "applicant_profile")
    for c in ("full_name", "email", "phone", "location", "linkedin_url",
              "github_url", "portfolio_url", "school", "degree", "grad_date",
              "us_work_auth", "ca_work_auth", "needs_sponsorship", "summary",
              "updated_at"):
        assert c in cols, c


def test_migrate_adds_columns_to_preexisting_tables(tmp_path, monkeypatch):
    """Simulate an old DB: create jobs/applications WITHOUT the new columns,
    then prove init_db() adds them."""
    db = tmp_path / "old.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    with store_db.connect() as conn:
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT)")
    store_db.init_db()
    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")
        assert "resume_pdf_key" in _cols(conn, "applications")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: FAIL — `country` not in jobs columns.

- [ ] **Step 3: Add the column to `schema.sql`**

In the `jobs` table, after the `also_on` line, add:

```sql
    country      TEXT    NOT NULL DEFAULT '',  -- US | CA | OTHER | UNKNOWN (see locations.py)
```

In the `applications` table, after `resume_job_id`, add:

```sql
    resume_pdf_key TEXT           -- pins the exact cached PDF sent (see server/resume_pdf.py); NULL if none
```

Add an index and the new table at the end of the file:

```sql
CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);

-- ---------------------------------------------------------------------------
-- Applicant profile — one row (id is always 1, enforced by profile_store).
-- Typed fields exist so NO LLM ever invents a phone number or a work-
-- authorization answer into a submitted application form. `summary` is the
-- free-text candidate description that drives job fit scoring.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applicant_profile (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name         TEXT    NOT NULL DEFAULT '',
    email             TEXT    NOT NULL DEFAULT '',
    phone             TEXT    NOT NULL DEFAULT '',
    location          TEXT    NOT NULL DEFAULT '',
    linkedin_url      TEXT    NOT NULL DEFAULT '',
    github_url        TEXT    NOT NULL DEFAULT '',
    portfolio_url     TEXT    NOT NULL DEFAULT '',
    school            TEXT    NOT NULL DEFAULT '',
    degree            TEXT    NOT NULL DEFAULT '',
    grad_date         TEXT    NOT NULL DEFAULT '',   -- ISO YYYY-MM
    us_work_auth      TEXT    NOT NULL DEFAULT '',   -- citizen|permanent_resident|f1_opt|tn_eligible|needs_sponsorship
    ca_work_auth      TEXT    NOT NULL DEFAULT '',
    needs_sponsorship INTEGER NOT NULL DEFAULT 0,
    summary           TEXT    NOT NULL DEFAULT '',
    updated_at        TEXT    NOT NULL DEFAULT ''
);
```

- [ ] **Step 4: Reorder `init_db` so migration runs BEFORE the schema script**

**This ordering change is mandatory and must be done before adding the column guards.** `init_db()`
currently runs `executescript(schema.sql)` and *then* `_migrate(conn)`. That order cannot work for a
new **indexed** column: `CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country)` guards the
index *name*, not the column, so on a pre-existing `jobs` table it raises
`sqlite3.OperationalError: no such column: country` before `_migrate` ever gets a chance to add it.
Verified against a replica of the live schema:

```
executescript FIRST (current order):  fresh DB OK  |  existing DB FAIL "no such column: country"
_migrate FIRST      (required order): fresh DB OK  |  existing DB OK
```

`init_db()` runs on FastAPI startup and inside every agent store, so getting this wrong takes the
whole app down against the real database.

In `store_db.py`, swap the two calls:

```python
def init_db() -> None:
    """Create every table/index if absent, from the canonical schema.sql.
    Safe to call repeatedly (CREATE TABLE IF NOT EXISTS).

    `_migrate` runs FIRST: it adds columns to tables that already exist, and
    schema.sql may declare an INDEX over a newly-added column. `CREATE INDEX IF
    NOT EXISTS` only guards the index name, not the column, so running the script
    first would raise "no such column" on a pre-existing table.
    """
    with connect() as conn:
        _migrate(conn)
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
```

- [ ] **Step 5: Add the `_migrate` guards (every check guarded on table existence)**

Because `_migrate` now runs before any table is created, **every** check must tolerate a missing
table — `PRAGMA table_info` on a nonexistent table returns no rows, so an unguarded
`if "col" not in cols` would try to `ALTER` a table that does not exist yet and fail on a fresh DB.
The existing `applications` check lacks that guard; add it.

Change the existing `applications` block to:

```python
    cols = {r[1] for r in conn.execute("PRAGMA table_info(applications)")}
    if cols and "resume_job_id" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN resume_job_id TEXT")
    # Pins which cached résumé PDF was actually sent with an application.
    if cols and "resume_pdf_key" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN resume_pdf_key TEXT")
```

and append:

```python
    # Country classification for the jobs board's US/Canada filter.
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if job_cols and "country" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN country TEXT NOT NULL DEFAULT ''")
```

The `master_resume` and `resumes` checks already carry the `if <cols> and` guard — leave them as is.

- [ ] **Step 6: Add the migration-path test**

Append to `tests/test_schema.py`:

```python
def test_indexed_new_column_survives_a_preexisting_table(tmp_path, monkeypatch):
    """Regression: schema.sql declares idx_jobs_country over a column that only
    _migrate adds. CREATE INDEX IF NOT EXISTS guards the index NAME, not the
    column, so if the schema script ran before the migration this raised
    'no such column: country' against every already-existing database."""
    db = tmp_path / "preexisting.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    with store_db.connect() as conn:
        # A `jobs` table shaped like the live one, WITHOUT `country`.
        conn.execute(
            "CREATE TABLE jobs (id TEXT NOT NULL PRIMARY KEY, company TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'new', location TEXT NOT NULL DEFAULT '', "
            "data TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute("CREATE INDEX idx_jobs_company ON jobs(company)")

    store_db.init_db()  # must not raise

    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")
        idx = {r[1] for r in conn.execute("PRAGMA index_list(jobs)")}
        assert "idx_jobs_country" in idx
```

- [ ] **Step 7: Run the test**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: all PASS.

- [ ] **Step 8: Mirror in Prisma and verify no drift**

In `web-next/prisma/schema.prisma`, add to `model jobs` after `also_on`:

```prisma
  country      String  @default("")
```

and the index inside the same model:

```prisma
  @@index([country], map: "idx_jobs_country")
```

Add to `model applications`:

```prisma
  resume_pdf_key String?
```

Add the new model:

```prisma
model applicant_profile {
  id                Int    @id @default(autoincrement())
  full_name         String @default("")
  email             String @default("")
  phone             String @default("")
  location          String @default("")
  linkedin_url      String @default("")
  github_url        String @default("")
  portfolio_url     String @default("")
  school            String @default("")
  degree            String @default("")
  grad_date         String @default("")
  us_work_auth      String @default("")
  ca_work_auth      String @default("")
  needs_sponsorship Int    @default(0)
  summary           String @default("")
  updated_at        String @default("")
}
```

Run: `cd web-next && npm run db:check && npx prisma generate`
Expected: drift check PASSES.

- [ ] **Step 9: Update ARCHITECTURE.md**

In the "Data model & schema ownership" table, change "Nine tables" to "Ten tables" and add:

```
| `applicant_profile` | `profile_store.py` (via `PUT /data/profile`) | both sides |
```

- [ ] **Step 10: Commit**

```bash
git add schema.sql store_db.py web-next/prisma/schema.prisma ARCHITECTURE.md tests/test_schema.py
git commit -m "feat(db): jobs.country, applications.resume_pdf_key, applicant_profile table"
```

---

## Task 5: `profile_store.py` + profile API

**Files:**
- Create: `profile_store.py`, `server/routers/profile.py`
- Modify: `server/app.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: `temp_db` / `client` fixtures (Task 1); the `applicant_profile` table (Task 4).
- Produces:
  - `profile_store.get_profile() -> dict` — always returns a dict with every field (empty strings when unset).
  - `profile_store.upsert_profile(**fields) -> dict` — writes only the supplied fields, returns the full row.
  - `profile_store.FIELDS: tuple[str, ...]` — the editable field names, excluding `id`/`updated_at`.
  - `GET /data/profile` → `{"profile": {...}}`; `PUT /data/profile` → `{"profile": {...}}`.

- [ ] **Step 1: Write the failing test**

`tests/test_profile.py`:

```python
from __future__ import annotations

import profile_store


def test_get_profile_empty_default(temp_db):
    p = profile_store.get_profile()
    assert p["full_name"] == ""
    assert p["needs_sponsorship"] == 0


def test_upsert_is_partial_and_single_row(temp_db):
    profile_store.upsert_profile(full_name="Kayla Li", email="k@example.com")
    profile_store.upsert_profile(phone="555-0100")
    p = profile_store.get_profile()
    assert p["full_name"] == "Kayla Li"      # preserved by the second call
    assert p["email"] == "k@example.com"
    assert p["phone"] == "555-0100"
    assert p["id"] == 1
    assert p["updated_at"]


def test_upsert_rejects_unknown_field(temp_db):
    try:
        profile_store.upsert_profile(nickname="oops")
    except ValueError as exc:
        assert "nickname" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown field")


def test_profile_api_roundtrip(client):
    assert client.get("/data/profile").json()["profile"]["full_name"] == ""
    res = client.put("/data/profile", json={"full_name": "Kayla Li", "summary": "CS undergrad"})
    assert res.status_code == 200
    assert res.json()["profile"]["full_name"] == "Kayla Li"
    assert client.get("/data/profile").json()["profile"]["summary"] == "CS undergrad"


def test_profile_api_rejects_empty_body(client):
    assert client.put("/data/profile", json={}).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'profile_store'`

- [ ] **Step 3: Write `profile_store.py`**

```python
"""Single-row store for the applicant profile (`applicant_profile` table).

Lives at the project root beside store_db.py because BOTH the agents (job fit
scoring reads `summary`; Phase B's autofill reads the typed fields) and the
server layer need it, and agents must not import the server package.

One row only — `id` is always 1, enforced here rather than by a constraint, the
same pattern master_resume uses. Reads degrade gracefully: a missing table
returns the all-empty default rather than raising, so a run never dies on it.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import store_db

# Editable fields, in display order. `id` and `updated_at` are managed here.
FIELDS: tuple[str, ...] = (
    "full_name", "email", "phone", "location",
    "linkedin_url", "github_url", "portfolio_url",
    "school", "degree", "grad_date",
    "us_work_auth", "ca_work_auth", "needs_sponsorship",
    "summary",
)

# Accepted work-authorization values (free text elsewhere would defeat the
# point of typed fields — an autofill must never guess this answer).
WORK_AUTH = ("", "citizen", "permanent_resident", "f1_opt", "tn_eligible", "needs_sponsorship")

_INT_FIELDS = frozenset({"needs_sponsorship"})


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _empty() -> dict:
    row = {f: 0 if f in _INT_FIELDS else "" for f in FIELDS}
    row["id"] = 1
    row["updated_at"] = ""
    return row


def get_profile() -> dict:
    """The profile row, or an all-empty default if absent."""
    try:
        with store_db.connect() as conn:
            row = conn.execute("SELECT * FROM applicant_profile WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return _empty()
    return dict(row) if row else _empty()


def upsert_profile(**fields) -> dict:
    """Write the supplied fields (partial update) and return the full row.

    Raises ValueError on an unknown field or an invalid work-auth value, so a
    typo in a request body can never silently land in the DB.
    """
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown profile field(s): {', '.join(sorted(unknown))}")
    for key in ("us_work_auth", "ca_work_auth"):
        if key in fields and str(fields[key]) not in WORK_AUTH:
            raise ValueError(f"{key} must be one of {WORK_AUTH}")

    store_db.init_db()
    clean = {
        k: (1 if str(v) not in ("", "0", "False", "false") else 0) if k in _INT_FIELDS else str(v).strip()
        for k, v in fields.items()
    }

    with store_db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM applicant_profile WHERE id = 1").fetchone()
        if exists is None:
            cols = ["id", "updated_at", *clean]
            vals = [1, _now(), *clean.values()]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO applicant_profile ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        else:
            assigns = ", ".join(f"{k} = ?" for k in clean)
            conn.execute(
                f"UPDATE applicant_profile SET {assigns}, updated_at = ? WHERE id = 1",
                [*clean.values(), _now()],
            )
    return get_profile()


def fit_profile_text() -> str:
    """The free-text candidate description used for job fit scoring ('' if unset)."""
    return (get_profile().get("summary") or "").strip()
```

- [ ] **Step 4: Write `server/routers/profile.py`**

```python
"""Applicant-profile read/write — the single writer for `applicant_profile`.

Typed fields (name, email, phone, links, school, work authorization) feed Phase
B's form autofill deterministically, and `summary` is the candidate description
that drives job fit scoring.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import profile_store

router = APIRouter()


class ProfileEdit(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    school: str | None = None
    degree: str | None = None
    grad_date: str | None = None
    us_work_auth: str | None = None
    ca_work_auth: str | None = None
    needs_sponsorship: bool | None = None
    summary: str | None = None


@router.get("/data/profile")
def get_profile():
    return {"profile": profile_store.get_profile()}


@router.put("/data/profile")
def put_profile(body: ProfileEdit):
    supplied = {k: v for k, v in body.model_dump().items() if v is not None}
    if not supplied:
        return JSONResponse({"error": "Nothing to update."}, status_code=400)
    try:
        profile = profile_store.upsert_profile(**supplied)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"profile": profile}
```

- [ ] **Step 5: Register the router**

In `server/app.py`, change the import to include `profile` and add the include:

```python
from server.routers import applications, jobs, prefs, profile, resume, runs, stocks  # noqa: E402
```

```python
app.include_router(profile.router)
```

- [ ] **Step 6: Confirm the proxy already covers the new route (no edit expected)**

`web-next/next.config.ts:13` already rewrites `/data/:path*` → `127.0.0.1:8001`, so `/data/profile` is proxied with no change. Verify:

```bash
grep -n 'data/:path' web-next/next.config.ts
```

Expected: one match. If it is missing, add
`{ source: "/data/:path*", destination: "http://127.0.0.1:8001/data/:path*" }` to `rewrites()`.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_profile.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add profile_store.py server/routers/profile.py server/app.py tests/test_profile.py web-next/next.config.ts
git commit -m "feat(profile): applicant_profile store + GET/PUT /data/profile"
```

---

## Task 6: prefs — merge instead of replace, expose the JOB_* keys

**Files:**
- Modify: `server/prefs.py`, `server/routers/prefs.py`, `web-next/src/components/settings/SettingsForm.tsx`
- Test: `tests/test_prefs.py`

**Interfaces:**
- Consumes: `temp_db` / `client` fixtures.
- Produces: `JOB_PROFILE` (str), `JOB_MIN_FIT` (int 0–100), `JOB_MAX_AGE_DAYS` (int ≥ 1), `JOB_DROP_GHOSTS` (bool), `JOB_COUNTRIES` (list of `"US"`/`"CA"`/`"OTHER"`) as editable prefs; `save_prefs` merges over the existing overlay.

- [ ] **Step 1: Write the failing test**

`tests/test_prefs.py`:

```python
"""Prefs overlay tests.

The merge behavior is a bug fix: save_prefs used to write ONLY its validated
subset and os.replace the whole file, so any key it did not know about (a
hand-added JOB_PROFILE, for example) was silently destroyed on the next save.
"""

from __future__ import annotations

import json

import pytest

import config
from server import prefs as prefstore


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """Redirect the prefs overlay at a temp file and restore config after.

    `monkeypatch.undo()` MUST come before `config.refresh()`. Fixture finalizers
    run in reverse setup order, so `monkeypatch` (set up first, as a dependency)
    is torn down LAST — meaning a bare `config.refresh()` here would re-read the
    temp overlay and leave those values loaded in `config` for every later test.
    Verified: without the undo, teardown's refresh reads the tmp_path file.
    """
    path = tmp_path / "prefs.json"
    monkeypatch.setattr(config, "PREFS_FILE", path)
    yield path
    monkeypatch.undo()
    config.refresh()


def test_save_preserves_unknown_keys(overlay):
    overlay.write_text(json.dumps({"SOME_FUTURE_KEY": "keep me"}), encoding="utf-8")
    prefstore.save_prefs({"WEATHER_TIMEZONE": "America/Toronto"})
    saved = json.loads(overlay.read_text(encoding="utf-8"))
    assert saved["SOME_FUTURE_KEY"] == "keep me"
    assert saved["WEATHER_TIMEZONE"] == "America/Toronto"


def test_job_profile_is_editable(overlay):
    prefstore.save_prefs({"JOB_PROFILE": "CS undergrad, Python + React"})
    assert json.loads(overlay.read_text(encoding="utf-8"))["JOB_PROFILE"] == "CS undergrad, Python + React"
    assert config.JOB_PROFILE == "CS undergrad, Python + React"


def test_job_min_fit_range_validated(overlay):
    with pytest.raises(ValueError):
        prefstore.save_prefs({"JOB_MIN_FIT": 500})


def test_job_countries_validated(overlay):
    prefstore.save_prefs({"JOB_COUNTRIES": ["US", "CA"]})
    assert config.JOB_COUNTRIES == ["US", "CA"]
    with pytest.raises(ValueError):
        prefstore.save_prefs({"JOB_COUNTRIES": ["MARS"]})


def test_drop_ghosts_bool(overlay):
    prefstore.save_prefs({"JOB_DROP_GHOSTS": True})
    assert config.JOB_DROP_GHOSTS is True


def test_current_exposes_job_keys(overlay):
    cur = prefstore.current()
    for k in ("JOB_PROFILE", "JOB_MIN_FIT", "JOB_MAX_AGE_DAYS", "JOB_DROP_GHOSTS", "JOB_COUNTRIES"):
        assert k in cur
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_prefs.py -v`
Expected: FAIL — `SOME_FUTURE_KEY` missing, and `JOB_COUNTRIES` not on `config`.

- [ ] **Step 3: Add `JOB_COUNTRIES` to config.py**

In `_apply_prefs()`, add `JOB_COUNTRIES` to the `global` list beside `JOB_MIN_FIT`, and after the `JOB_MIN_FIT` assignment add:

```python
    # Job scraper — which countries the board and digest show. Postings are
    # always classified and stored (see locations.py); this only controls
    # visibility, so narrowing it never loses data.
    JOB_COUNTRIES = _pref("JOB_COUNTRIES", ["US", "CA"])
```

- [ ] **Step 4: Update `server/prefs.py`**

Add to the key groups near the top:

```python
STR_KEYS = ("WEATHER_TIMEZONE", "WEATHER_TEMP_UNIT", "COMMUTE_ORIGIN", "COMMUTE_DESTINATION", "JOB_PROFILE")
INT_KEYS = ("NEWS_MAX_ITEMS_PER_TOPIC",)
BOOL_KEYS = ("JOB_DROP_GHOSTS",)
_VALID_COUNTRIES = {"US", "CA", "OTHER"}
```

`JOB_MIN_FIT` and `JOB_MAX_AGE_DAYS` need their own ranges, so validate them explicitly rather than adding them to `INT_KEYS`. Inside `_validate`, before the `return out`:

```python
    for k in BOOL_KEYS:
        if k in prefs:
            out[k] = bool(prefs[k]) and str(prefs[k]).lower() not in ("0", "false", "off", "")

    if "JOB_MIN_FIT" in prefs and prefs["JOB_MIN_FIT"] != "":
        n = int(prefs["JOB_MIN_FIT"])
        if not 0 <= n <= 100:
            raise ValueError("JOB_MIN_FIT must be between 0 and 100")
        out["JOB_MIN_FIT"] = n

    if "JOB_MAX_AGE_DAYS" in prefs and prefs["JOB_MAX_AGE_DAYS"] != "":
        n = int(prefs["JOB_MAX_AGE_DAYS"])
        if n < 1:
            raise ValueError("JOB_MAX_AGE_DAYS must be >= 1")
        out["JOB_MAX_AGE_DAYS"] = n

    if "JOB_COUNTRIES" in prefs:
        codes = [str(c).strip().upper() for c in prefs["JOB_COUNTRIES"] if str(c).strip()]
        bad = [c for c in codes if c not in _VALID_COUNTRIES]
        if bad:
            raise ValueError(f"unknown country code(s) {bad} (use {sorted(_VALID_COUNTRIES)})")
        out["JOB_COUNTRIES"] = codes
```

Add to `current()`:

```python
        "JOB_PROFILE": config.JOB_PROFILE,
        "JOB_MIN_FIT": config.JOB_MIN_FIT,
        "JOB_MAX_AGE_DAYS": config.JOB_MAX_AGE_DAYS,
        "JOB_DROP_GHOSTS": config.JOB_DROP_GHOSTS,
        "JOB_COUNTRIES": list(config.JOB_COUNTRIES),
```

Change `save_prefs` to merge, and update its docstring:

```python
def save_prefs(prefs: dict[str, Any]) -> dict[str, Any]:
    """Validate, MERGE into the overlay, write atomically, and refresh config.

    Merging (rather than replacing) means a key this function does not validate
    — a hand-added pref, or one added by a newer version — survives a save from
    an older form. The previous replace-everything behavior silently destroyed
    such keys.
    """
    clean = _validate(prefs)
    existing: dict[str, Any] = {}
    try:
        with config.PREFS_FILE.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            existing = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        existing = {}
    merged = {**existing, **clean}

    config.PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.PREFS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, config.PREFS_FILE)  # atomic swap
    config.refresh()
    return merged
```

- [ ] **Step 5: Pass the new keys through the router**

In `server/routers/prefs.py`, add to the `payload` dict:

```python
        "JOB_PROFILE": g("JOB_PROFILE"),
        "JOB_MIN_FIT": g("JOB_MIN_FIT"),
        "JOB_MAX_AGE_DAYS": g("JOB_MAX_AGE_DAYS"),
        "JOB_DROP_GHOSTS": str(body.get("JOB_DROP_GHOSTS", "")).strip(),
        "JOB_COUNTRIES": _lines(g("JOB_COUNTRIES")),
```

- [ ] **Step 6: Add the fields to the Settings form**

In `SettingsForm.tsx`, extend the `Prefs` type:

```tsx
  JOB_PROFILE: string; JOB_MIN_FIT: number; JOB_MAX_AGE_DAYS: number;
  JOB_DROP_GHOSTS: boolean; JOB_COUNTRIES: string[];
```

and add inputs after the `JOB_SOURCES` textarea:

```tsx
      <label>Candidate profile (drives job fit scores)
        <textarea name="JOB_PROFILE" rows={4} defaultValue={prefs.JOB_PROFILE}
          placeholder="3rd-year CS undergrad at Waterloo. Python, TypeScript, React, SQL. Seeking SWE/ML co-op." />
      </label>
      <label>Minimum fit score to keep (0 = keep all)
        <input name="JOB_MIN_FIT" defaultValue={prefs.JOB_MIN_FIT} />
      </label>
      <label>Flag postings older than (days)
        <input name="JOB_MAX_AGE_DAYS" defaultValue={prefs.JOB_MAX_AGE_DAYS} />
      </label>
      <label>Countries to show (one code per line: US, CA, OTHER)
        <textarea name="JOB_COUNTRIES" rows={3} defaultValue={prefs.JOB_COUNTRIES.join("\n")} />
      </label>
      <label className="checkbox-row">
        <input type="checkbox" name="JOB_DROP_GHOSTS" defaultChecked={prefs.JOB_DROP_GHOSTS} />
        Drop stale/ghost postings entirely (instead of just flagging them)
      </label>
```

Note: an unchecked checkbox is absent from `FormData`, which `_validate` reads as `False` — the desired behavior.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_prefs.py -v`
Expected: all PASS.

- [ ] **Step 8: Verify end to end**

Restart the server (`.venv/bin/python -m server`), then run `cd web-next && npm run dev`, open `/settings`, paste a real candidate profile, save, and confirm:

```bash
cat data/prefs.json
```

Expected: `JOB_PROFILE` present with your text.

- [ ] **Step 9: Commit**

```bash
git add config.py server/prefs.py server/routers/prefs.py web-next/src/components/settings/SettingsForm.tsx tests/test_prefs.py
git commit -m "fix(prefs): merge overlay instead of replacing it; expose JOB_* prefs in Settings"
```

---

## Task 7: `rank_node` baseline + refinement, `filter_node` country tagging

**Files:**
- Modify: `agents/job_scraper/nodes/rank.py`, `agents/job_scraper/nodes/filter.py`
- Test: `tests/test_rank.py`

**Interfaces:**
- Consumes: `score_baseline` / `is_baseline_reason` (Task 3), `country_of` (Task 2), `profile_store.fit_profile_text` (Task 5), `config.JOB_COUNTRIES` (Task 6).
- Produces: every posting leaving `rank_node` has a non-None `fit_score`; every posting leaving `filter_node` has a `country` key.

- [ ] **Step 1: Write the failing test**

`tests/test_rank.py`:

```python
"""rank_node / filter_node behavior.

The critical guarantee: a null or malformed LLM reply must never wipe out the
deterministic baseline score. That is the regression that left all 523 rows at
fit_score=NULL.
"""

from __future__ import annotations

import config
from agents.job_scraper.nodes import rank as rank_mod
from agents.job_scraper.nodes.filter import filter_node
from agents.job_scraper.nodes.rank import rank_node

_POSTINGS = [
    {"id": "a", "title": "Software Engineer Intern", "description": "Python and React.", "location": "Austin, TX"},
    {"id": "b", "title": "Data Intern", "description": "SQL dashboards.", "location": "London"},
]


def test_baseline_survives_total_llm_failure(monkeypatch):
    """Model unavailable -> every posting still has a score."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(rank_mod, "llm", boom)
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert len(out) == 2
    assert all(p["fit_score"] is not None for p in out)


def test_baseline_survives_null_llm_scores(monkeypatch):
    """Model replies but scores are null -> baseline is kept, not overwritten."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":true,"score":null},{"i":1,"eligible":true,"score":null}]',
    )
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert all(p["fit_score"] is not None for p in out)
    assert all(p["fit_reason"] for p in out)


def test_llm_score_overrides_baseline(monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":true,"score":91,"reason":"excellent match"},'
                        '{"i":1,"eligible":true,"score":42,"reason":"weak"}]',
    )
    out = {p["id"]: p for p in rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]}
    assert out["a"]["fit_score"] == 91
    assert out["a"]["fit_reason"] == "excellent match"


def test_ineligible_still_dropped(monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":false,"score":90},{"i":1,"eligible":true,"score":50}]',
    )
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert [p["id"] for p in out] == ["b"]


def test_filter_tags_country_and_keeps_everything(monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    out = filter_node({"raw": [
        {"title": "Software Engineer Intern", "location": "Austin, TX"},
        {"title": "SWE Intern", "location": "Dublin, Ireland"},
        {"title": "SWE Intern", "location": "2 Locations"},
        {"title": "Senior Engineer", "location": "Austin, TX"},   # dropped: not early-career
    ]})["filtered"]
    assert len(out) == 3, "location must NEVER drop a posting; only the role filter drops"
    assert {p["country"] for p in out} == {"US", "OTHER", "UNKNOWN"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rank.py -v`
Expected: FAIL — postings have `fit_score is None` and no `country` key.

- [ ] **Step 3: Rewrite `filter_node`**

Replace `agents/job_scraper/nodes/filter.py` entirely:

```python
"""Filter node — keep only co-op/intern/new-grad roles, and tag every posting
with a country.

Location NEVER drops a posting here. Every posting is classified (US | CA |
OTHER | UNKNOWN) and stored, and visibility is decided later — the board filters
to config.JOB_COUNTRIES and the digest omits non-matching rows. That keeps a
misclassification auditable in the database and makes "actually show me London"
a toggle rather than a re-scrape. The only drops are the role filters.
"""

from __future__ import annotations

from agents.job_scraper.locations import country_of
from agents.job_scraper.matching import is_excluded, is_target_role
from agents.job_scraper.state import JobScraperState


def filter_node(state: JobScraperState) -> JobScraperState:
    kept: list[dict] = []
    for p in state.get("raw", []):
        title = p.get("title", "")
        # Must be an early-career role AND not a senior / advanced-degree one.
        if not is_target_role(title) or is_excluded(title):
            continue
        kept.append({**p, "country": country_of(p.get("location", ""))})
    return {"filtered": kept}
```

- [ ] **Step 4: Rewrite the scoring half of `rank_node`**

In `agents/job_scraper/nodes/rank.py`, add imports:

```python
import profile_store
from agents.job_scraper.scoring import extract_keywords, score_baseline
```

Replace `_score_batch` with:

```python
def _score_batch(profile: str, keywords: list[str], batch: list[dict]) -> None:
    """Attach eligible / fit_score / fit_reason to a batch in place.

    The deterministic baseline is applied FIRST so every posting always has a
    score, then the LLM overrides it only when it returns a usable integer.
    Measured 2026-07-25: llama3.1:8b returns a null score for ~60% of postings
    even with a profile set, so treating the model as the sole source would
    leave most rows unscored — and null sinks to the bottom of sort-by-fit,
    hiding good roles.
    """
    for p in batch:
        base_score, base_reason = score_baseline(keywords, p)
        p["fit_score"] = base_score
        p["fit_reason"] = base_reason
        p["eligible"] = True

    try:
        reply = llm("local", _prompt(profile, batch), system=_SYSTEM, temperature=0.2)
    except Exception as exc:  # model unavailable / transport error -> keep baselines
        for p in batch:
            p["fit_reason"] = f"{p['fit_reason']} (unrefined: {exc})"
        return

    parsed = _parse(reply, len(batch))
    for i, p in enumerate(batch):
        hit = parsed.get(i)
        if not hit:
            continue
        p["eligible"] = hit["eligible"]
        if hit["score"] is not None:
            p["fit_score"] = hit["score"]
            if hit["reason"]:
                p["fit_reason"] = hit["reason"]
```

Replace the body of `rank_node` up to the sort with:

```python
def rank_node(state: JobScraperState) -> JobScraperState:
    new = [dict(p) for p in state.get("new", [])]
    if not new:
        return {"new": new}

    # Profile: explicit pref wins, else the applicant profile's summary.
    profile = (config.JOB_PROFILE or "").strip() or profile_store.fit_profile_text()
    keywords = extract_keywords(profile)

    for start in range(0, len(new), _BATCH):
        _score_batch(profile, keywords, new[start : start + _BATCH])

    # Drop roles the model judged not undergrad-eligible (grad-only / senior).
    new = [p for p in new if p.get("eligible", True)]

    # Optional fit threshold. Every posting now HAS a score, so there is no
    # "unscored" carve-out to make any more.
    if config.JOB_MIN_FIT > 0:
        new = [p for p in new if p["fit_score"] >= config.JOB_MIN_FIT]

    new.sort(key=lambda p: p["fit_score"], reverse=True)
    return {"new": new}
```

Also shrink the batch size, since 5 postings per prompt was measurably confusing the model:

```python
_BATCH = 3
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_rank.py tests/test_scoring.py tests/test_locations.py -v`
Expected: all PASS.

- [ ] **Step 6: Update the state docstring**

In `agents/job_scraper/state.py`, add `country` to the posting-shape comment and note that `fit_score` is now always an int:

```python
     "canonical_location": str, "dup_of": str|None, "also_on": list[str],
     "country": str,            # US | CA | OTHER | UNKNOWN (locations.py)
     "fit_score": int,          # ALWAYS set (deterministic baseline, LLM-refined)
     "fit_reason": str}
```

- [ ] **Step 7: Commit**

```bash
git add agents/job_scraper/nodes/rank.py agents/job_scraper/nodes/filter.py agents/job_scraper/state.py tests/test_rank.py
git commit -m "feat(jobs): baseline+LLM fit scoring; tag country without dropping postings"
```

---

## Task 8: `backfill` node — score the backlog, refresh ghost and last_seen

**Files:**
- Create: `agents/job_scraper/nodes/backfill.py`
- Modify: `agents/job_scraper/graph.py`, `agents/job_scraper/nodes/notify.py`, `agents/job_scraper/state.py`, `agents/registry.py`, `scripts/run.py`
- Test: `tests/test_backfill.py`

**Interfaces:**
- Consumes: `country_of` (Task 2), `is_baseline_reason` (Task 3), `jobstore.load_records` / `upsert_records`.
- Produces: `backfill_node(state) -> {"new": [...]}` where merged rows carry `_rescored: True`; `LLM_CAP = 60`; graph input key `backfill: bool` enabling the LLM pass.

- [ ] **Step 0: Persist `country` to its mirrored column (do this FIRST)**

`agents/job_scraper/store.py` never writes `country` — the word appears **zero** times in that file.
Task 4 added the column, Task 7 tags the record dict, but `_mirror()` doesn't return it and `_INSERT`
doesn't list it, so the column stays `''` forever while only the `data` JSON blob carries the value.
Prisma reads the **column**, so without this step the jobs board's US/Canada filter would silently do
nothing. Confirmed against the live DB: all 523 rows have `country = ''`.

Add to the dict returned by `_mirror()`, after `"also_on"`:

```python
        "country": rec.get("country", ""),
```

Then add it to all three parts of `_INSERT` — the column list, the `VALUES` placeholders, and the
`ON CONFLICT DO UPDATE SET` clause:

```python
_INSERT = (
    "INSERT INTO jobs (id, company, title, location, url, status, ats, posted_at, "
    "remote, compensation, department, description, fit_score, fit_reason, ghost, "
    "also_on, country, first_seen, last_seen, data) VALUES (:id, :company, :title, :location, "
    ":url, :status, :ats, :posted_at, :remote, :compensation, :department, "
    ":description, :fit_score, :fit_reason, :ghost, :also_on, :country, :first_seen, "
    ":last_seen, :data) ON CONFLICT(id) DO UPDATE SET "
    "company=excluded.company, title=excluded.title, location=excluded.location, "
    "url=excluded.url, status=excluded.status, ats=excluded.ats, "
    "posted_at=excluded.posted_at, remote=excluded.remote, "
    "compensation=excluded.compensation, department=excluded.department, "
    "description=excluded.description, fit_score=excluded.fit_score, "
    "fit_reason=excluded.fit_reason, ghost=excluded.ghost, also_on=excluded.also_on, "
    "country=excluded.country, "
    "first_seen=excluded.first_seen, last_seen=excluded.last_seen, data=excluded.data"
)
```

`set_status` also routes through `_write`, so it picks this up automatically — which keeps the
mirrored column and the blob in sync, exactly as the ARCHITECTURE doc requires.

Add this test to `tests/test_stores_sqlite.py`:

```python
def test_country_is_mirrored_to_its_column(temp_db):
    """Regression: `country` lived only in the `data` blob, so Prisma (which reads
    the COLUMN) saw '' for every row and the US/Canada filter did nothing."""
    from agents.job_scraper import store as jobstore

    jobstore.upsert_records([{
        "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
        "location": "Austin, TX", "country": "US",
    }])
    with store_db.connect() as conn:
        row = conn.execute("SELECT country FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)).fetchone()
    assert row["country"] == "US"

    # A status change must not blank it out (set_status re-writes every column).
    jobstore.set_status("Acme:greenhouse:1", "applied")
    with store_db.connect() as conn:
        row = conn.execute("SELECT country, status FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)).fetchone()
    assert row["country"] == "US"
    assert row["status"] == "applied"
```

- [ ] **Step 1: Write the failing test**

`tests/test_backfill.py`:

```python
"""backfill node — pulls stored rows back through the pipeline tail.

dedupe drops every already-seen id BEFORE rank runs, so without this node the
523 existing rows could never be scored, their ghost flag never re-evaluated,
and last_seen never refreshed.
"""

from __future__ import annotations

import pytest

import config
import profile_store
from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.backfill import backfill_node


def _seed(records):
    for rec in records:
        jobstore.replace_record(rec)


@pytest.fixture
def with_profile(monkeypatch):
    """A profile must exist for the LLM refinement pass to be queued at all, so
    any test asserting on refinement has to set one explicitly."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    return "Python React SQL"


def test_selects_rows_missing_score_or_country(temp_db):
    _seed([
        {"id": "1", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": None, "country": ""},
        {"id": "2", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": 80, "fit_reason": "great match", "country": "US"},
    ])
    out = backfill_node({"new": []})["new"]
    assert [p["id"] for p in out] == ["1"]
    assert out[0]["_rescored"] is True


def test_fills_country_deterministically(temp_db):
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Dublin, Ireland", "fit_score": None, "country": ""}])
    out = backfill_node({"new": []})["new"]
    assert out[0]["country"] == "OTHER"


def test_dismissed_rows_get_country_but_not_llm_refinement(temp_db, with_profile):
    """No inference is ever spent on a job already rejected. A profile is set so
    this test fails for the RIGHT reason — without one, every row is skipped
    anyway and the assertion would pass vacuously."""
    _seed([{"id": "d", "status": "dismissed", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""},
           {"id": "n", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = {p["id"]: p for p in backfill_node({"new": [], "backfill": True})["new"]}
    assert out["d"]["country"] == "US"
    assert out["d"]["_skip_llm"] is True, "dismissed row must not be refined"
    assert out["n"].get("_skip_llm") is not True, "new row SHOULD be refined"


def test_respects_the_llm_cap(temp_db, monkeypatch, with_profile):
    from agents.job_scraper.nodes import backfill as bf
    monkeypatch.setattr(bf, "LLM_CAP", 2)
    _seed([
        {"id": str(i), "status": "new", "title": "SWE Intern",
         "location": "Austin, TX", "fit_score": None, "country": ""}
        for i in range(5)
    ])
    out = backfill_node({"new": [], "backfill": True})["new"]
    refinable = [p for p in out if not p.get("_skip_llm")]
    assert len(refinable) == 2, "cap must bound the expensive pass"
    assert len(out) == 5, "the cheap deterministic pass still covers everything"


def test_no_llm_refinement_without_the_flag(temp_db, monkeypatch):
    """The interactive 'run scraper' button must stay fast: the cheap pass still
    runs, but nothing is queued for the ~6.5s/posting model call."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": []})["new"]
    assert out[0]["_skip_llm"] is True
    assert out[0]["country"] == "US", "the cheap deterministic pass still runs"


def test_no_llm_refinement_without_a_profile(temp_db, monkeypatch):
    """With no profile the rank prompt can only return null scores, and a null
    score leaves the baseline reason in place — so these rows stay selectable and
    would be re-queued on every scheduled run forever. Skip the expensive pass.
    Both profile sources must be neutralised, not just config."""
    monkeypatch.setattr(config, "JOB_PROFILE", "")
    monkeypatch.setattr(profile_store, "fit_profile_text", lambda: "")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": [], "backfill": True})["new"]
    assert out[0]["_skip_llm"] is True, "no profile -> no inference"
    assert out[0]["country"] == "US", "the cheap deterministic pass still runs"


def test_llm_refinement_runs_when_a_profile_exists(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": [], "backfill": True})["new"]
    assert out[0].get("_skip_llm") is not True, "a profile exists, so refine it"


def test_preserves_incoming_new_postings(temp_db):
    _seed([{"id": "stored", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    incoming = [{"id": "fresh", "title": "New Grad SWE", "location": "Toronto, Ontario"}]
    out = backfill_node({"new": incoming})["new"]
    ids = {p["id"] for p in out}
    assert ids == {"fresh", "stored"}
    assert next(p for p in out if p["id"] == "fresh").get("_rescored") is not True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: agents.job_scraper.nodes.backfill`

- [ ] **Step 3: Write the node**

`agents/job_scraper/nodes/backfill.py`:

```python
"""Backfill node — pull stored rows back through the pipeline tail.

dedupe drops every already-seen id before rank runs, so a posting scored once
(or never) is frozen forever. This node re-injects stored rows that still need
work, tagged `_rescored` so notify persists them WITHOUT announcing them as new
finds. Because they then traverse freshness and rank like any other posting, one
node fixes three separate defects at once:

  * the fit_score backlog gets scored,
  * `ghost` is recomputed for rows that have since aged past JOB_MAX_AGE_DAYS,
  * `last_seen` is refreshed (it previously froze at first sight, so a delisted
    posting was undetectable).

Cost control, measured 2026-07-25 at ~6.5s/job of local inference:
  * the deterministic half (country, baseline score) covers EVERY selected row —
    it is free,
  * only non-dismissed rows are eligible for LLM refinement (392 of 523 rows are
    dismissed, so this alone saves ~42 minutes),
  * LLM_CAP bounds one run and the skipped count is logged, so a bounded pass
    never silently reads as "covered everything". Successive runs converge.
"""

from __future__ import annotations

import config
import profile_store
from agents.job_scraper.locations import country_of
from agents.job_scraper.scoring import is_baseline_reason
from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import load_records

# Max rows handed to the LLM in one run (~6.5s each).
LLM_CAP = 60


def _needs_country(rec: dict) -> bool:
    return not (rec.get("country") or "").strip()


def _needs_score(rec: dict) -> bool:
    if rec.get("fit_score") is None:
        return True
    # Baseline-only reason means the LLM has not refined this row yet.
    return is_baseline_reason(rec.get("fit_reason", ""))


def backfill_node(state: JobScraperState) -> JobScraperState:
    incoming = list(state.get("new", []))
    incoming_ids = {p.get("id") for p in incoming}

    selected: list[dict] = []
    for rec in load_records().values():
        pid = rec.get("id")
        if not pid or pid in incoming_ids:
            continue
        if not (_needs_country(rec) or _needs_score(rec)):
            continue
        selected.append(rec)

    # Deterministic, free: fill country for every selected row.
    for rec in selected:
        if _needs_country(rec):
            rec["country"] = country_of(rec.get("location", ""))
        rec["_rescored"] = True

    # Expensive: LLM refinement, only when this run opted in. The launchd runs
    # pass backfill=True (nobody is waiting) and the "score backlog" button does
    # too; the interactive "run scraper" button does not, so it stays fast.
    #
    # ALSO gated on a profile existing. With no profile the rank prompt tells the
    # model to return a null score for every role, so refinement cannot improve
    # anything — and because a null score leaves the baseline reason in place,
    # those rows stay selectable and would be re-queued on EVERY run. Measured on
    # the live data that is 60 rows x ~6.5s = ~6.5 min of inference twice daily,
    # forever, producing nothing. Skip it until a profile exists.
    has_profile = bool(
        (config.JOB_PROFILE or "").strip() or profile_store.fit_profile_text()
    )
    refine = bool(state.get("backfill")) and has_profile
    if state.get("backfill") and not has_profile:
        print(
            "ℹ️ backfill: no candidate profile set, so LLM refinement is skipped "
            "(it could only return null scores). Set one on the Settings page."
        )
    budget = LLM_CAP if refine else 0
    over_cap = 0
    for rec in selected:
        if rec.get("status") == "dismissed" or not _needs_score(rec):
            rec["_skip_llm"] = True
        elif budget > 0:
            budget -= 1
        else:
            rec["_skip_llm"] = True
            if refine:
                over_cap += 1  # only "over the cap" when refinement was actually on

    if over_cap:
        print(f"ℹ️ backfill: {over_cap} row(s) over the {LLM_CAP}-row LLM cap, deferred to a later run")

    return {"new": incoming + selected}
```

- [ ] **Step 4: Make `rank_node` honor `_skip_llm`**

In `agents/job_scraper/nodes/rank.py:rank_node`, split the batching so skipped rows only get the baseline. Replace the batching loop:

```python
    refinable = [p for p in new if not p.get("_skip_llm")]
    baseline_only = [p for p in new if p.get("_skip_llm")]

    for p in baseline_only:
        p["fit_score"], p["fit_reason"] = score_baseline(keywords, p)
        p.setdefault("eligible", True)

    for start in range(0, len(refinable), _BATCH):
        _score_batch(profile, keywords, refinable[start : start + _BATCH])
```

- [ ] **Step 5: Wire the node into the graph**

In `agents/job_scraper/graph.py`, add the import, register the node, and re-chain so `backfill` sits between `dedupe` and `freshness`:

```python
from agents.job_scraper.nodes.backfill import backfill_node
```

```python
    g.add_node("backfill", backfill_node)
```

```python
    g.add_edge("dedupe", "backfill")
    g.add_edge("backfill", "freshness")
```

Delete the old `g.add_edge("dedupe", "freshness")`. Update the module docstring's pipeline line to `fetch -> filter -> dedupe -> backfill -> freshness -> rank -> notify`.

- [ ] **Step 6: Keep rescored rows out of the digest**

In `agents/job_scraper/nodes/notify.py:notify_node`, persist everything but announce only genuine finds, and respect the country filter:

```python
    def notify_node(state: JobScraperState) -> JobScraperState:
        all_rows = state.get("new", [])
        warnings = state.get("warnings", [])

        # Announce only genuinely new postings in the shown countries. Rescored
        # backlog rows are persisted but never re-announced.
        shown = set(config.JOB_COUNTRIES)
        announce = [
            p for p in all_rows
            if not p.get("_rescored") and (p.get("country", "UNKNOWN") in shown or p.get("country") == "UNKNOWN")
        ]
        message = _format_message(announce, warnings)

        try:
            # Strip the transient pipeline tags before persisting.
            upsert_records([{k: v for k, v in p.items() if not k.startswith("_")} for p in all_rows])
        except Exception as exc:
            print(f"⚠️ Could not persist job records: {exc}")

        if send:
            try:
                send_message(message)
            except Exception as exc:
                print(f"⚠️ Discord delivery failed: {exc}")

        return {"message": message}
```

Add `import config` at the top of `notify.py`.

- [ ] **Step 7: Document the new state keys**

In `agents/job_scraper/state.py`, add to the posting-shape docstring:

```python
     # transient pipeline tags (stripped before persistence):
     "_rescored": bool,   # re-injected backlog row; persisted, never announced
     "_skip_llm": bool,   # baseline score only; no inference spent on this row
```

- [ ] **Step 8: Fix the registry node_order (two bugs)**

In `agents/registry.py`, update `job_scraper` to include the new node and `resume_generator` to include the long-missing `latexify`:

```python
        node_order=("fetch", "filter", "dedupe", "backfill", "freshness", "rank", "notify"),
```

```python
        node_order=("gather", "research", "keywords", "draft", "latexify", "save"),
```

- [ ] **Step 9: Run the tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 10: Run the real pipeline once and verify the backlog moves**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import store_db
with store_db.connect() as c:
    print('before:', c.execute('SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL').fetchone()[0], 'unscored')
"
.venv/bin/python scripts/run.py job_scraper
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import store_db
with store_db.connect() as c:
    print('after: ', c.execute('SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL').fetchone()[0], 'unscored')
    print('countries:', dict(c.execute('SELECT country, COUNT(*) FROM jobs GROUP BY country').fetchall()))
"
```

Expected: unscored drops toward 0, and `country` is populated for every row. This run performs real inference — expect several minutes.

- [ ] **Step 11: Commit**

```bash
git add agents/job_scraper/ agents/registry.py tests/test_backfill.py
git commit -m "feat(jobs): backfill node — score backlog, recompute ghost, refresh last_seen"
```

---

## Task 9: Résumé PDF cache

**Files:**
- Create: `server/resume_pdf.py`
- Modify: `.gitignore`
- Test: `tests/test_resume_pdf.py`

**Interfaces:**
- Consumes: `agents.resume_generator.store.get_resume` / `get_master_resume`, `agents.resume_generator.latex.compile_tex`.
- Produces:
  - `cache_key(job_id: str, updated_at: str) -> str` — the filename stem, safe for a `job_id` containing `:` and `/`.
  - `ensure_pdf(job_id: str | None) -> tuple[str, bytes]` — returns `(key, pdf_bytes)`; `job_id=None` means the master résumé. Raises `CompileError` on a LaTeX failure.
  - `PDF_DIR: Path`

- [ ] **Step 1: Write the failing test**

`tests/test_resume_pdf.py`:

```python
"""Résumé PDF cache tests.

Compiling is only ~2s, so this cache is about PROVENANCE, not speed: the key is
content-versioned and an application pins the key it used, so "what exactly did
I send Stripe?" stays answerable after the résumé is edited.
"""

from __future__ import annotations

from server import resume_pdf


def test_cache_key_is_filesystem_safe():
    key = resume_pdf.cache_key("Databricks:greenhouse:7586263002", "2026-07-25T10:00:00")
    assert ":" not in key
    assert "/" not in key
    assert "Databricks" in key


def test_cache_key_changes_with_content_version():
    a = resume_pdf.cache_key("j", "2026-07-25T10:00:00")
    b = resume_pdf.cache_key("j", "2026-07-26T10:00:00")
    assert a != b, "editing a résumé must produce a new key, preserving the old file"


def test_master_key_is_stable_and_distinct():
    assert resume_pdf.cache_key(None, "2026-07-25T10:00:00").startswith("master")


def test_resume_without_latex_gets_the_MASTER_key(temp_db, monkeypatch, tmp_path):
    """A résumé with no tailored LaTeX falls back to the master's content, so it
    must also get the master's KEY. Pinning a job-specific key would claim a
    tailored résumé was sent when the generic master actually was — and the
    pinned key exists precisely to answer 'what did this company receive?'."""
    from agents.resume_generator import store as rstore

    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(resume_pdf, "compile_tex", lambda tex: b"%PDF-1.5 fake")

    rstore.upsert_master_resume(None, latex="\\documentclass{article}\\begin{document}M\\end{document}")
    rstore.upsert_resume("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                         markdown="md", latex="", keywords=[], status="draft")

    master_key, _ = resume_pdf.ensure_pdf(None)
    fallback_key, _ = resume_pdf.ensure_pdf("Acme:greenhouse:1")
    assert fallback_key == master_key, "fallback content must carry the master's key"
    assert fallback_key.startswith("master")


def test_resume_with_its_own_latex_keeps_a_job_specific_key(temp_db, monkeypatch, tmp_path):
    from agents.resume_generator import store as rstore

    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(resume_pdf, "compile_tex", lambda tex: b"%PDF-1.5 fake")

    rstore.upsert_master_resume(None, latex="\\documentclass{article}\\begin{document}M\\end{document}")
    rstore.upsert_resume("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                         markdown="md", keywords=[], status="draft",
                         latex="\\documentclass{article}\\begin{document}TAILORED\\end{document}")

    key, _ = resume_pdf.ensure_pdf("Acme:greenhouse:1")
    assert key.startswith("Acme_greenhouse_1__")
    assert not key.startswith("master")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resume_pdf.py -v`
Expected: FAIL — `ImportError: cannot import name 'resume_pdf'`

- [ ] **Step 3: Write the module**

`server/resume_pdf.py`:

```python
"""Content-versioned résumé PDF cache.

Compiling with Tectonic takes ~2.0s (measured 2026-07-25), so this exists for
PROVENANCE rather than speed. The key embeds the résumé's `updated_at`, so
editing a résumé writes a NEW file and leaves the old one intact; an application
row pins the key it used (`applications.resume_pdf_key`), which keeps "what
exactly did this company receive?" answerable forever.

Files land in data/resumes/ (gitignored). Nothing evicts them — a few dozen KB
each is a price worth paying for an auditable record.
"""

from __future__ import annotations

import re

import config
from agents.resume_generator.latex import compile_tex
from agents.resume_generator.store import get_master_resume, get_resume

PDF_DIR = config.PROJECT_ROOT / "data" / "resumes"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_key(job_id: str | None, updated_at: str) -> str:
    """Filesystem-safe, content-versioned filename stem.

    `job_id` contains ':' (e.g. 'Databricks:greenhouse:7586263002') and can
    contain '/', so both are collapsed to '_'.
    """
    stem = "master" if not job_id else _UNSAFE.sub("_", job_id)
    version = _UNSAFE.sub("_", (updated_at or "0"))
    return f"{stem}__{version}"


def ensure_pdf(job_id: str | None) -> tuple[str, bytes]:
    """Return (cache_key, pdf_bytes) for a résumé, compiling only on a miss.

    `job_id=None` selects the master résumé. Raises LookupError when the résumé
    does not exist or has no LaTeX, and CompileError when LaTeX fails (the
    caller surfaces the engine log so the UI can offer the raw .tex).
    """
    # `key_job` is what the cache key is built from, and it must describe the
    # CONTENT actually compiled. A tailored résumé that was never latexified
    # falls back to the master's LaTeX (the same fallback the latexify node
    # uses) — and in that case the key must be the MASTER's key, not the job's.
    # Otherwise an application pins e.g. "Snowflake_..." while the bytes sent
    # were the generic master résumé, and the pinned key — whose entire purpose
    # is answering "what exactly did this company receive?" — would lie.
    key_job: str | None = job_id or None

    if job_id:
        rec = get_resume(job_id)
        if rec is None:
            raise LookupError(f"no résumé for job {job_id}")
        tex, updated_at = rec.get("latex", ""), rec.get("updated_at", "")
        if not tex.strip():
            master = get_master_resume()
            tex, updated_at = master.get("latex", ""), master.get("updated_at", "")
            key_job = None  # these bytes ARE the master résumé
    else:
        master = get_master_resume()
        tex, updated_at = master.get("latex", ""), master.get("updated_at", "")

    if not tex.strip():
        raise LookupError("no LaTeX résumé available — set your master résumé first")

    key = cache_key(key_job, updated_at)
    path = PDF_DIR / f"{key}.pdf"
    if path.exists():
        return key, path.read_bytes()

    pdf = compile_tex(tex)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return key, pdf
```

- [ ] **Step 4: Gitignore the cache**

Append to `.gitignore`:

```
# Compiled résumé PDFs (content-versioned cache; regenerable from the DB)
data/resumes/
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_resume_pdf.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add server/resume_pdf.py .gitignore tests/test_resume_pdf.py
git commit -m "feat(resume): content-versioned PDF cache for apply provenance"
```

---

## Task 10: Apply / undo / status / rerank endpoints

**Files:**
- Modify: `server/routers/jobs.py`, `agents/application_tracker/store.py`
- Test: `tests/test_jobs_router.py`

**Interfaces:**
- Consumes: `resume_pdf.ensure_pdf` (Task 9), `jobstore.set_status`, `appstore`.
- Produces:
  - `appstore.add_application(company, role, url="", notes="", status="applied", resume_job_id=None, resume_pdf_key=None) -> dict`
  - `POST /data/jobs/apply` body `{id, resume_job_id?}` → `{"ok": True, "application_id": int, "resume_pdf_key": str|None}`
  - `POST /data/jobs/undo-apply` body `{id, application_id}` → `{"ok": True}`
  - `POST /data/jobs/status` body `{id, status}` → `{"ok": True}`
  - `GET /data/jobs/resume-pdf?job_id=...` → PDF bytes (`job_id` omitted ⇒ master)

- [ ] **Step 1: Write the failing test**

`tests/test_jobs_router.py`:

```python
"""Jobs router tests.

The behavior under test is the fix for the worst bug in the tab: Apply used to
create an application and mark the job applied WITHOUT opening the posting and
WITHOUT recording which résumé was used, so the tracker asserted things that had
never happened.
"""

from __future__ import annotations

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

_JOB = {
    "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
    "location": "Austin, TX", "url": "https://acme.example/jobs/1",
    "status": "new", "country": "US", "fit_score": 80, "fit_reason": "matched: Python",
}


def test_apply_records_resume_link(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/apply", json={"id": _JOB["id"], "resume_job_id": "some:job:1"})
    assert res.status_code == 200
    app_id = res.json()["application_id"]

    apps = appstore.load_all()
    assert len(apps) == 1
    assert apps[0]["id"] == app_id
    assert apps[0]["resume_job_id"] == "some:job:1"
    assert apps[0]["company"] == "Acme"
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_apply_without_resume_is_allowed(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert res.status_code == 200
    assert appstore.load_all()[0]["resume_job_id"] is None


def test_apply_unknown_job_404(client):
    assert client.post("/data/jobs/apply", json={"id": "nope"}).status_code == 404


def test_undo_apply_removes_row_and_reverts_status(client):
    jobstore.replace_record(dict(_JOB))
    app_id = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()["application_id"]

    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_id})
    assert res.status_code == 200
    assert appstore.load_all() == []
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"


def test_status_endpoint_sets_viewed_and_restores_dismissed(client):
    jobstore.replace_record(dict(_JOB))
    assert client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "viewed"}).status_code == 200
    assert jobstore.load_records()[_JOB["id"]]["status"] == "viewed"

    client.post("/data/jobs/dismiss", json={"id": _JOB["id"]})
    assert jobstore.load_records()[_JOB["id"]]["status"] == "dismissed"
    client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "new"})
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"


def test_status_endpoint_rejects_bad_status(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "banana"})
    assert res.status_code == 400


def test_job_id_with_colons_is_not_mangled(client):
    """jobs.id contains ':' — ids travel in the BODY, never a path segment."""
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/status", json={"id": "Acme:greenhouse:1", "status": "viewed"})
    assert res.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_jobs_router.py -v`
Expected: FAIL — `apply` returns no `application_id`; `/data/jobs/undo-apply` and `/data/jobs/status` are 404.

- [ ] **Step 3: Extend `add_application`**

In `agents/application_tracker/store.py`, replace `add_application`:

```python
def add_application(
    company: str,
    role: str,
    url: str = "",
    notes: str = "",
    status: str = "applied",
    resume_job_id: str | None = None,
    resume_pdf_key: str | None = None,
) -> dict:
    """Create and persist a new application; returns the stored record.

    `resume_job_id` links the résumé used (-> resumes.job_id) and
    `resume_pdf_key` pins the exact compiled PDF that was sent, so editing that
    résumé later cannot destroy the record of what the company received.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(company, role, url, status, applied_date, updated_date, notes, "
            " auto_detected, resume_job_id, resume_pdf_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?) RETURNING *",
            (company.strip(), role.strip(), url.strip(), status, today, today,
             notes.strip(), (resume_job_id or "").strip() or None,
             (resume_pdf_key or "").strip() or None),
        )
        row = cur.fetchone()
    return _row(row)
```

- [ ] **Step 4: Rewrite `server/routers/jobs.py`**

```python
"""Job write endpoints — the single writer for `jobs` status changes.

Everything goes through agents.job_scraper.store.set_status, which updates BOTH
the mirrored `status` column and the `data` JSON blob, keeping the Python
pipeline and the Prisma reads in sync.

Apply is deliberately more than a status flip: it records WHICH résumé was used
and pins the exact compiled PDF, because the old one-click version marked a job
"applied" without ever opening the posting or noting the résumé — the tracker
asserted things that had not happened.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore
from agents.resume_generator.latex import CompileError
from server import resume_pdf

router = APIRouter()


class JobRef(BaseModel):
    id: str = ""


class ApplyBody(BaseModel):
    id: str = ""
    resume_job_id: str | None = None


class UndoBody(BaseModel):
    id: str = ""
    application_id: int = 0


class StatusBody(BaseModel):
    id: str = ""
    status: str = ""


def _load(jid: str):
    return jobstore.load_records().get(jid)


@router.post("/data/jobs/apply")
def apply_to_job(body: ApplyBody):
    """Record an application for a scraped job, linking the résumé used.

    The caller (the apply modal) opens the posting itself and offers Undo; this
    endpoint only writes. A PDF-compile failure is NOT fatal — the application
    is still recorded, just without a pinned PDF key.
    """
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    rec = _load(jid)
    if rec is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)

    resume_job_id = (body.resume_job_id or "").strip() or None
    pdf_key = None
    try:
        # resume_job_id=None selects the master résumé.
        pdf_key, _ = resume_pdf.ensure_pdf(resume_job_id)
    except (LookupError, CompileError) as exc:
        # Not fatal: still log the application, just without a pinned PDF.
        print(f"⚠️ Could not prepare résumé PDF for {jid}: {exc}")

    app = appstore.add_application(
        rec.get("company", ""), rec.get("title", ""),
        url=rec.get("url", ""), status="applied",
        resume_job_id=resume_job_id, resume_pdf_key=pdf_key,
    )
    jobstore.set_status(jid, "applied")
    return {"ok": True, "application_id": int(app["id"]), "resume_pdf_key": pdf_key}


@router.post("/data/jobs/undo-apply")
def undo_apply(body: UndoBody):
    """Reverse an apply: delete the tracker row and put the job back to `new`."""
    jid = body.id.strip()
    if not jid or not body.application_id:
        return JSONResponse({"error": "id and application_id are required."}, status_code=400)
    if _load(jid) is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    appstore.delete_application(body.application_id)
    jobstore.set_status(jid, "new")
    return {"ok": True}


@router.post("/data/jobs/dismiss")
def dismiss_job(body: JobRef):
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    if jobstore.set_status(jid, "dismissed") is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {"ok": True}


@router.post("/data/jobs/status")
def set_job_status(body: StatusBody):
    """Generic status setter — powers 'viewed' on expand and Restore on a
    dismissed row."""
    jid = body.id.strip()
    status = body.status.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    if status not in jobstore.STATUSES:
        return JSONResponse(
            {"error": f"status must be one of {', '.join(jobstore.STATUSES)}"}, status_code=400
        )
    if jobstore.set_status(jid, status) is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {"ok": True}


@router.get("/data/jobs/resume-pdf")
def get_resume_pdf(job_id: str = ""):
    """Compiled PDF for a résumé (master when job_id is omitted), so the apply
    modal can hand the user the exact file to upload."""
    try:
        key, pdf = resume_pdf.ensure_pdf(job_id.strip() or None)
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except CompileError as exc:
        return JSONResponse({"error": str(exc), "log": exc.log}, status_code=422)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{key}.pdf"'},
    )
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_jobs_router.py -v`
Expected: all PASS.

- [ ] **Step 6: Restart the server and smoke-test**

```bash
.venv/bin/python -m server &
sleep 3
curl -s -X POST localhost:8001/data/jobs/status \
  -H 'Content-Type: application/json' \
  -d '{"id":"Databricks:greenhouse:7586263002","status":"viewed"}'
```

Expected: `{"ok":true}`

- [ ] **Step 8: Commit**

```bash
git add server/routers/jobs.py agents/application_tracker/store.py tests/test_jobs_router.py
git commit -m "feat(jobs): apply records résumé + PDF key; add undo-apply and status endpoints"
```

---

## Task 11: Apply modal, country filter, viewed, restore

**Files:**
- Create: `web-next/src/components/jobs/ApplyModal.tsx`
- Modify: `web-next/src/lib/jobs.ts`, `web-next/src/components/jobs/JobsBoard.tsx`, `web-next/src/components/jobs/JobRow.tsx`, `web-next/src/app/(hub)/jobs/page.tsx`, `web-next/src/app/globals.css`

**Interfaces:**
- Consumes: `POST /data/jobs/{apply,undo-apply,dismiss,status}` (Task 10), `GET /data/jobs/resume-pdf` (Task 10), `jobs.country` (Task 4).
- Produces: `Job` type gains `country: string`; `COUNTRY_LABEL`; `ResumeChoice` type `{ job_id: string | null; label: string }`.

- [ ] **Step 1: Extend `lib/jobs.ts`**

Add to the `Job` type after `also_on`:

```ts
  country: string;    // US | CA | OTHER | UNKNOWN
```

Append:

```ts
export const COUNTRY_LABEL: Record<string, string> = {
  US: "US", CA: "Canada", OTHER: "intl", UNKNOWN: "?",
};

// Countries shown by default. Non-North-America roles stay in the DB (so a
// misclassification is auditable) and are hidden here instead.
export const DEFAULT_COUNTRIES = ["US", "CA", "UNKNOWN"];

export function inCountries(job: Job, allowed: string[]): boolean {
  return allowed.includes(job.country || "UNKNOWN");
}
```

- [ ] **Step 2: Add `country` to the page's select**

In `web-next/src/app/(hub)/jobs/page.tsx`, add `country: true,` to `JOB_SELECT`. Also load the résumé choices the modal needs:

```tsx
export default async function JobsPage() {
  const [jobs, resumes, master] = await Promise.all([
    prisma.jobs.findMany({ select: JOB_SELECT }) as Promise<Job[]>,
    prisma.resumes.findMany({ select: { job_id: true, company: true, role: true } }),
    prisma.master_resume.findFirst({ select: { latex: true } }),
  ]);
  return (
    <>
      <div className="jobs-header">
        <h1>jobs</h1>
        <div className="jobs-header-actions">
          <ScoreBacklogButton />
          <RunScraperButton />
        </div>
      </div>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet — run the scraper above to pull fresh roles.</p>
      ) : (
        <JobsBoard jobs={jobs} resumes={resumes} hasMaster={Boolean(master?.latex?.trim())} />
      )}
    </>
  );
}
```

Import `ScoreBacklogButton` from `@/components/jobs/ScoreBacklogButton` (built in Task 12).

- [ ] **Step 3: Write `ApplyModal.tsx`**

```tsx
"use client";
import { useState } from "react";

export type ResumeRow = { job_id: string; company: string; role: string };

// Apply is a two-part action: choose the résumé, THEN open the posting. The old
// one-click Apply marked a job applied without ever opening it, so the tracker
// claimed applications that had never happened.
export default function ApplyModal({
  jobId, jobTitle, jobCompany, jobUrl, resumes, hasMaster, onClose, onApplied,
}: {
  jobId: string;
  jobTitle: string;
  jobCompany: string;
  jobUrl: string;
  resumes: ResumeRow[];
  hasMaster: boolean;
  onClose: () => void;
  onApplied: (applicationId: number) => void;
}) {
  // "" means the master résumé.
  const tailored = resumes.find((r) => r.job_id === jobId) ?? null;
  const [choice, setChoice] = useState<string>(tailored ? tailored.job_id : "");
  const [busy, setBusy] = useState(false);
  const [genPhase, setGenPhase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function generateTailored() {
    setGenPhase("starting");
    setError(null);
    const res = await fetch("/agents/resume_generator/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: { job_id: jobId } }),
    });
    if (!res.ok) {
      setGenPhase(null);
      setError("Could not start résumé generation.");
      return;
    }
    const { run_id } = await res.json();
    const es = new EventSource(`/runs/${run_id}/events`);
    es.addEventListener("node", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setGenPhase(`${d.node} ${d.status === "finish" ? "✓" : "…"}`);
    });
    es.addEventListener("done", () => {
      es.close();
      setGenPhase(null);
      setChoice(jobId); // the tailored résumé now exists under this job id
    });
    es.addEventListener("failed", (e) => {
      es.close();
      setGenPhase(null);
      setError(JSON.parse((e as MessageEvent).data).error || "Résumé generation failed.");
    });
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    const res = await fetch("/data/jobs/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: jobId, resume_job_id: choice || null }),
    });
    setBusy(false);
    if (!res.ok) {
      setError("Could not record the application.");
      return;
    }
    const { application_id } = await res.json();

    // Hand over the exact PDF to upload, then open the posting.
    const q = choice ? `?job_id=${encodeURIComponent(choice)}` : "";
    window.open(`/data/jobs/resume-pdf${q}`, "_blank", "noopener");
    if (jobUrl) window.open(jobUrl, "_blank", "noopener");

    onApplied(application_id);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal apply-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Apply — {jobTitle}</h2>
        <p className="muted">{jobCompany}</p>

        <label>
          Résumé to use
          <select value={choice} onChange={(e) => setChoice(e.target.value)} disabled={busy}>
            <option value="" disabled={!hasMaster}>
              {hasMaster ? "master résumé" : "master résumé (not set)"}
            </option>
            {tailored ? (
              <option value={tailored.job_id}>tailored for this job ★</option>
            ) : null}
            {resumes
              .filter((r) => r.job_id !== jobId)
              .map((r) => (
                <option key={r.job_id} value={r.job_id}>
                  reuse: {r.role || "(untitled)"}{r.company ? ` @ ${r.company}` : ""}
                </option>
              ))}
          </select>
        </label>

        {!tailored ? (
          <p className="muted">
            No résumé tailored for this role yet.{" "}
            <button className="link" onClick={generateTailored} disabled={busy || genPhase !== null}>
              {genPhase ? `generating… ${genPhase}` : "Generate one"}
            </button>{" "}
            — or apply with the master résumé now.
          </p>
        ) : null}

        {error ? <p className="banner err">{error}</p> : null}

        <div className="modal-actions">
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button className="primary" onClick={confirm} disabled={busy || (!hasMaster && !choice)}>
            {busy ? "Recording…" : "Download résumé, open posting & log it"}
          </button>
        </div>
        <p className="muted small">
          Opens the posting and your résumé PDF in new tabs, and logs the application. You can undo it.
        </p>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Update `JobsBoard.tsx`**

Add the country filter, the modal, undo, and viewed. Replace the component body's state and `mutate` with:

```tsx
  const [countries, setCountries] = useState<string[]>(DEFAULT_COUNTRIES);
  const [applyFor, setApplyFor] = useState<Job | null>(null);
  const [undo, setUndo] = useState<{ jobId: string; applicationId: number } | null>(null);

  const list = useMemo(() => {
    let l = jobs.slice();
    l = statusFilter ? l.filter((j) => j.status === statusFilter) : l.filter((j) => j.status !== "dismissed");
    if (companyFilter) l = l.filter((j) => j.company === companyFilter);
    if (hideGhost) l = l.filter((j) => j.ghost !== 1);
    l = l.filter((j) => inCountries(j, countries));
    l.sort(sort === "date" ? byDateDesc : sort === "fit" ? byFitDesc : byPriority);
    return l;
  }, [jobs, statusFilter, companyFilter, hideGhost, countries, sort]);

  async function post(path: string, body: Record<string, unknown>) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  }

  async function dismiss(id: string) {
    setBusyId(id);
    const ok = await post("/data/jobs/dismiss", { id });
    setBusyId(null);
    if (ok) router.refresh();
  }

  async function restore(id: string) {
    setBusyId(id);
    const ok = await post("/data/jobs/status", { id, status: "new" });
    setBusyId(null);
    if (ok) router.refresh();
  }

  // Expanding a row marks it viewed so you stop re-reading the same postings.
  function toggle(id: string) {
    setExpandedId((cur) => {
      const next = cur === id ? null : id;
      const job = jobs.find((j) => j.id === id);
      if (next === id && job?.status === "new") {
        void post("/data/jobs/status", { id, status: "viewed" }).then(() => router.refresh());
      }
      return next;
    });
  }

  async function doUndo() {
    if (!undo) return;
    const ok = await post("/data/jobs/undo-apply", { id: undo.jobId, application_id: undo.applicationId });
    setUndo(null);
    if (ok) router.refresh();
  }
```

Add the country selector into the `job-filters` div:

```tsx
        <select
          value={countries.includes("OTHER") ? "all" : "na"}
          onChange={(e) => setCountries(e.target.value === "all" ? ["US", "CA", "OTHER", "UNKNOWN"] : DEFAULT_COUNTRIES)}
        >
          <option value="na">US &amp; Canada</option>
          <option value="all">all countries</option>
        </select>
```

Render the undo banner and the modal at the top of the returned fragment:

```tsx
      {undo ? (
        <div className="banner ok undo-banner">
          Application logged. <button className="link" onClick={doUndo}>Undo</button>
        </div>
      ) : null}

      {applyFor ? (
        <ApplyModal
          jobId={applyFor.id}
          jobTitle={applyFor.title}
          jobCompany={applyFor.company}
          jobUrl={applyFor.url}
          resumes={resumes}
          hasMaster={hasMaster}
          onClose={() => setApplyFor(null)}
          onApplied={(applicationId) => {
            setApplyFor(null);
            setUndo({ jobId: applyFor.id, applicationId });
            router.refresh();
          }}
        />
      ) : null}
```

Change the props signature and wire the row callbacks:

```tsx
export default function JobsBoard({ jobs, resumes, hasMaster }: {
  jobs: Job[]; resumes: ResumeRow[]; hasMaster: boolean;
}) {
```

```tsx
            onApply={(id) => setApplyFor(jobs.find((j) => j.id === id) ?? null)}
            onDismiss={dismiss}
            onRestore={restore}
            onToggle={toggle}
```

And for the hero: `onApply={(id) => setApplyFor(jobs.find((j) => j.id === id) ?? null)}`.

Update the imports at the top:

```tsx
import { type Job, JOB_STATUSES, byPriority, byFitDesc, byDateDesc, bestMatch, inCountries, DEFAULT_COUNTRIES } from "@/lib/jobs";
import ApplyModal, { type ResumeRow } from "./ApplyModal";
```

`COUNTRY_LABEL` is used by `JobRow`, not here — importing it in `JobsBoard` too would fail `npm run lint` as an unused import.

- [ ] **Step 5: Update `JobRow.tsx`**

Add `onRestore` to the props type and render a country badge plus a Restore button:

```tsx
  onRestore: (id: string) => void;
```

In the badges row, after the `ats` badge:

```tsx
            {job.country && job.country !== "US" ? (
              <span className="job-badge">{COUNTRY_LABEL[job.country] ?? job.country}</span>
            ) : null}
```

Import `COUNTRY_LABEL` from `@/lib/jobs`. In `job-actions`:

```tsx
          {dismissed ? (
            <button disabled={busy} onClick={() => onRestore(job.id)}>Restore</button>
          ) : null}
```

- [ ] **Step 6: Add the CSS**

Append to `web-next/src/app/globals.css`:

```css
/* Apply modal + undo banner (jobs tab, Mars accent) */
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.modal {
  background: #14161c; border: 1px solid #2a2e39; border-radius: 10px;
  padding: 1.25rem 1.5rem; min-width: 420px; max-width: 560px;
}
.apply-modal h2 { margin: 0 0 0.25rem; font-size: 1.05rem; }
.apply-modal label { display: block; margin: 1rem 0 0.5rem; }
.apply-modal select { width: 100%; }
.modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.25rem; }
.modal-actions .primary { background: #e07a4a; border-color: #e07a4a; }
.muted.small { font-size: 0.8rem; margin-top: 0.75rem; }
.undo-banner { display: flex; gap: 0.5rem; align-items: center; }
button.link { background: none; border: none; color: #e07a4a; cursor: pointer; text-decoration: underline; padding: 0; }
.jobs-header-actions { display: flex; gap: 0.5rem; align-items: center; }
```

- [ ] **Step 7: Verify in the browser**

```bash
cd web-next && npm run dev
```

Open `http://localhost:3000/jobs` and confirm:
1. Fit badges show real numbers (not `—`) and the best-match hero appears.
2. The country filter defaults to "US & Canada" and Ireland/Vietnam/Singapore roles are hidden; switching to "all countries" reveals them with an `intl` badge.
3. Clicking **Apply** opens the modal, not an immediate mutation.
4. Confirming opens two tabs (PDF + posting), logs the application, and shows the Undo banner; Undo removes the tracker row.
5. Expanding a `new` row flips its pill to `viewed`.
6. Filtering to `dismissed` shows a working **Restore** button.

- [ ] **Step 8: Lint and commit**

```bash
cd web-next && npm run lint
cd .. && git add web-next/src
git commit -m "feat(jobs): apply modal with résumé picker, undo, country filter, viewed, restore"
```

---

## Task 12: "Score backlog" button

**Files:**
- Create: `web-next/src/components/jobs/ScoreBacklogButton.tsx`
- Modify: `server/routers/runs.py`, `scripts/run.py`

**Interfaces:**
- Consumes: `POST /agents/job_scraper/run` with `{"input": {"backfill": true}}` (already supported — `runs.py` forwards any `input` dict, and parameterized runs bypass the 60s throttle).
- Produces: a jobs-tab button that starts a backfill run and streams progress.

- [ ] **Step 1: Confirm the run endpoint already forwards the input**

Run:

```bash
.venv/bin/python -m server &
sleep 3
curl -s -X POST 'localhost:8001/agents/job_scraper/run?send=0' \
  -H 'Content-Type: application/json' -d '{"input":{"backfill":true}}'
```

Expected: `{"run_id": <n>}` with no `"reused": true`, because a parameterized run is always fresh (`runs.py:65`).

- [ ] **Step 2: Write the button**

`web-next/src/components/jobs/ScoreBacklogButton.tsx`:

```tsx
"use client";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import RunStream from "@/components/dashboard/RunStream";

// Kicks off an LLM refinement pass over the unscored backlog. Kept SEPARATE from
// "run scraper" because refinement costs ~6.5s per posting: the scraper button
// must stay fast, while this one is an explicit, slow, opt-in action.
export default function ScoreBacklogButton() {
  const router = useRouter();
  const [runId, setRunId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  async function run() {
    setStarting(true);
    const res = await fetch("/agents/job_scraper/run?send=0", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: { backfill: true } }),
    });
    setStarting(false);
    if (res.ok) setRunId((await res.json()).run_id);
  }

  const onDone = useCallback(() => {
    setRunId(null);
    router.refresh();
  }, [router]);

  const busy = starting || runId !== null;

  return (
    <div className="run-scraper">
      <button onClick={run} disabled={busy} title="Score unscored postings with the local model (slow)">
        {starting ? "starting…" : runId !== null ? "scoring…" : "◔ score backlog"}
      </button>
      {runId !== null ? <RunStream runId={runId} onDone={onDone} /> : null}
    </div>
  );
}
```

- [ ] **Step 3: Declare the input flag on the state**

`backfill_node` already reads `state.get("backfill")` (Task 8), so this step only declares the key.
Add `backfill: bool` to `JobScraperState` in `agents/job_scraper/state.py`:

```python
    # Input flag: when True this run also LLM-refines stored rows (slow).
    backfill: bool
```

- [ ] **Step 4: Pass the flag from launchd**

In `scripts/run.py`, add the argument and seed it into the graph input:

```python
    parser.add_argument("--send", action="store_true", help="deliver to Discord")
    parser.add_argument(
        "--backfill", action="store_true",
        help="also LLM-refine stored postings that lack a real fit score "
             "(job_scraper only; ~6.5s per posting, so the scheduled runs use "
             "it and interactive runs do not)",
    )
    args = parser.parse_args()

    graph = get_spec(args.agent).build_graph(send=args.send)
    final = graph.invoke({"backfill": True} if args.backfill else {})
```

Then add `--backfill` to the plist's `ProgramArguments`, after `--send`:

```xml
        <string>job_scraper</string>
        <string>--send</string>
        <string>--backfill</string>
```

Also update the plist's comment line to
`<!-- Run: .venv/bin/python scripts/run.py job_scraper --send --backfill -->`.

Note: passing `{"backfill": True}` to an agent whose state lacks that key is harmless — LangGraph
ignores unknown keys on a `TypedDict` state — but only `job_scraper` acts on it.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Reload launchd and commit**

```bash
launchctl unload ops/com.kayla.daily-agents.jobscraper.plist 2>/dev/null
launchctl load ops/com.kayla.daily-agents.jobscraper.plist
git add web-next/src/components/jobs/ScoreBacklogButton.tsx agents/job_scraper/ scripts/run.py ops/ tests/test_backfill.py
git commit -m "feat(jobs): opt-in backlog scoring button; scheduled runs refine, interactive stays fast"
```

---

## Task 13: Profile UI + docs

**Files:**
- Create: `web-next/src/components/settings/ProfileForm.tsx`
- Modify: `web-next/src/app/settings/page.tsx`, `ARCHITECTURE.md`, `README.md`

**Interfaces:**
- Consumes: `GET|PUT /data/profile` (Task 5).
- Produces: a Profile section on `/settings`.

- [ ] **Step 1: Write the form**

`web-next/src/components/settings/ProfileForm.tsx`:

```tsx
"use client";
import { useState } from "react";

export type Profile = {
  full_name: string; email: string; phone: string; location: string;
  linkedin_url: string; github_url: string; portfolio_url: string;
  school: string; degree: string; grad_date: string;
  us_work_auth: string; ca_work_auth: string; needs_sponsorship: number;
  summary: string;
};

const WORK_AUTH = [
  { v: "", l: "— not set —" },
  { v: "citizen", l: "citizen" },
  { v: "permanent_resident", l: "permanent resident" },
  { v: "f1_opt", l: "F-1 / OPT" },
  { v: "tn_eligible", l: "TN eligible" },
  { v: "needs_sponsorship", l: "needs sponsorship" },
];

// Typed fields, deliberately. Phase B's autofill maps these straight into ATS
// forms, so no model ever invents a phone number or a work-authorization answer
// into something you are about to submit.
export default function ProfileForm({ profile }: { profile: Profile }) {
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setMsg(null);
    const fd = new FormData(e.currentTarget);
    const body = {
      ...Object.fromEntries(fd),
      needs_sponsorship: fd.get("needs_sponsorship") !== null,
    };
    setPending(true);
    const res = await fetch("/data/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setPending(false);
    if (res.ok) setMsg({ ok: true, text: "Profile saved." });
    else setMsg({ ok: false, text: (await res.json().catch(() => ({}))).error ?? "Save failed." });
  }

  return (
    <form className="settings-form" onSubmit={onSubmit}>
      <h2>profile</h2>
      {msg ? <div className={`banner ${msg.ok ? "ok" : "err"}`}>{msg.text}</div> : null}

      <label>Full name<input name="full_name" defaultValue={profile.full_name} /></label>
      <label>Email<input name="email" type="email" defaultValue={profile.email} /></label>
      <label>Phone<input name="phone" defaultValue={profile.phone} /></label>
      <label>Location<input name="location" defaultValue={profile.location} placeholder="Waterloo, ON, Canada" /></label>
      <label>LinkedIn URL<input name="linkedin_url" defaultValue={profile.linkedin_url} /></label>
      <label>GitHub URL<input name="github_url" defaultValue={profile.github_url} /></label>
      <label>Portfolio URL<input name="portfolio_url" defaultValue={profile.portfolio_url} /></label>
      <label>School<input name="school" defaultValue={profile.school} /></label>
      <label>Degree<input name="degree" defaultValue={profile.degree} /></label>
      <label>Graduation (YYYY-MM)<input name="grad_date" defaultValue={profile.grad_date} placeholder="2027-04" /></label>

      <label>US work authorization
        <select name="us_work_auth" defaultValue={profile.us_work_auth}>
          {WORK_AUTH.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </label>
      <label>Canada work authorization
        <select name="ca_work_auth" defaultValue={profile.ca_work_auth}>
          {WORK_AUTH.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </label>
      <label className="checkbox-row">
        <input type="checkbox" name="needs_sponsorship" defaultChecked={profile.needs_sponsorship === 1} />
        Will need visa sponsorship
      </label>

      <label>Summary (also drives job fit scores)
        <textarea name="summary" rows={4} defaultValue={profile.summary}
          placeholder="3rd-year CS undergrad at Waterloo. Python, TypeScript, React, SQL. Seeking SWE/ML co-op." />
      </label>

      <button type="submit" className="primary" disabled={pending}>
        {pending ? "Saving…" : "Save profile"}
      </button>
    </form>
  );
}
```

- [ ] **Step 2: Render it on the settings page**

Replace `web-next/src/app/settings/page.tsx` with:

```tsx
import ProfileForm, { type Profile } from "@/components/settings/ProfileForm";
import SettingsForm from "@/components/settings/SettingsForm";
import { AGENT_SERVICE_URL } from "@/lib/agent-service";

export const dynamic = "force-dynamic";

async function loadJson(path: string) {
  try {
    const res = await fetch(`${AGENT_SERVICE_URL}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const [data, profileData] = await Promise.all([loadJson("/prefs"), loadJson("/data/profile")]);
  return (
    <>
      <h1>settings</h1>
      {!data ? (
        <p className="muted">Agent service offline — start it with <code>python -m server</code> (port 8001).</p>
      ) : (
        <>
          {profileData ? <ProfileForm profile={profileData.profile as Profile} /> : null}
          <SettingsForm prefs={data.prefs} secrets={data.secrets} />
        </>
      )}
    </>
  );
}
```

This reuses the existing `AGENT_SERVICE_URL` server-side fetch pattern already in this file — the
proxy rewrites only apply to browser requests, so a server component must call `:8001` directly.

- [ ] **Step 3: Verify in the browser**

Open `/settings`, fill in the profile including the summary, save, then confirm:

```bash
sqlite3 -header data/control_center.db "SELECT full_name, email, us_work_auth, substr(summary,1,40) FROM applicant_profile;"
```

Expected: one row with your values.

- [ ] **Step 4: Confirm the summary drives fit scoring**

Run:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import profile_store
from agents.job_scraper.scoring import extract_keywords
print('profile text:', profile_store.fit_profile_text()[:80])
print('keywords:', extract_keywords(profile_store.fit_profile_text())[:12])
"
```

Expected: non-empty text and a sensible keyword list.

- [ ] **Step 5: Update the docs**

In `ARCHITECTURE.md`, add to the FastAPI endpoint list:

```
- `GET|PUT /data/profile` — applicant profile (typed autofill fields + fit-scoring summary)
- `POST /data/jobs/{apply,dismiss,status,undo-apply}` — job status + application logging
- `GET /data/jobs/resume-pdf` — compiled résumé PDF (content-versioned cache)
```

Update the "Agent execution flow" section to mention that `job_scraper` accepts a `backfill`
input flag, and change the pipeline description to
`fetch → filter → dedupe → backfill → freshness → rank → notify`.

In `README.md`, replace any `uv run` invocation with `.venv/bin/python`, and document the test
command as `.venv/bin/python -m pytest tests/`.

- [ ] **Step 6: Full verification**

```bash
.venv/bin/python -m pytest tests/ -v
cd web-next && npm run db:check && npm run lint && npm run build
```

Expected: tests pass, no schema drift, lint clean, build succeeds.

- [ ] **Step 7: Commit**

```bash
git add web-next/src ARCHITECTURE.md README.md
git commit -m "feat(profile): Profile section on Settings; document new endpoints and pipeline"
```

---

## Task 15: Make `tests/test_job_scraper.py`'s assertions actually assert

**Execute this BEFORE Task 14** — until it lands, roughly 40 assertions covering the scraper's core
logic cannot fail, so no later task's green suite means what it appears to.

**Why.** `tests/test_job_scraper.py` still uses the repo's pre-pytest `check(name, cond)` helper,
which only appends to a module-level `_failures` list and prints. The `main()` that used to inspect
that list was deleted in Task 1 when pytest was adopted — and under pytest it was never called
anyway. So a failing `check()` is silent. Proved by injecting `check("DELIBERATELY FALSE", 1 == 2)`:
all 8 tests in the file still reported PASS.

This is my own scoping error from Task 1, which converted only `tests/test_stores_sqlite.py`.

Affected coverage, all currently decorative: ATS field helpers (`_strip_html`, `_to_iso_date`,
`_has_remote`, compensation formatting), `matching.age_days` / `canonical_location`, the
undergrad relevance filter, freshness/ghost flagging, and LLM-reply parsing.

**Files:**
- Modify: `tests/test_job_scraper.py`

- [ ] **Step 1: Prove the defect before changing anything**

```bash
cp tests/test_job_scraper.py /tmp/tjs.bak
python3 - <<'PY'
import pathlib
p = pathlib.Path("tests/test_job_scraper.py"); s = p.read_text()
s = s.replace('check("intern is target", is_target_role("Software Engineer Intern"))',
              'check("DELIBERATELY FALSE", 1 == 2)')
p.write_text(s)
PY
.venv/bin/python -m pytest tests/test_job_scraper.py -q
cp /tmp/tjs.bak tests/test_job_scraper.py && rm /tmp/tjs.bak
```

Expected: the suite reports PASS despite the false assertion. Record that output in your report — it
is the evidence this task exists.

- [ ] **Step 2: Convert every `check(...)` call to a real assert**

Mechanically rewrite each `check("<name>", <cond>)` as `assert <cond>, "<name>"`. Keep the message
text — it is the only documentation of intent for several of these. Do not change any condition, and
do not "fix" a condition that now fails; if any assertion fails once enforced, STOP and report it as
a genuine pre-existing bug rather than adjusting the test to match the code.

Then delete the now-unused `check` function, the `_failures` list, and any `print` lines that only
existed to label the hand-rolled runner's output.

Preserve the one test the Task 8 fix round added, which already uses real asserts.

- [ ] **Step 3: Run the suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all tests pass. If any newly-enforced assertion fails, that is a real bug the decorative
helper was hiding — report it and stop rather than editing the assertion.

- [ ] **Step 4: Re-prove enforcement**

Repeat Step 1's injection, but as a real `assert 1 == 2, "DELIBERATELY FALSE"`. The suite must now
FAIL. Restore the file and confirm `git status --short` is clean. Record both outputs.

- [ ] **Step 5: Confirm no other test file has the same problem**

```bash
grep -rn "def check\|_failures" tests/
```

Expected: no matches anywhere. Report what you find.

- [ ] **Step 6: Commit**

```bash
git add tests/test_job_scraper.py
git commit -m "test(jobs): enforce assertions in test_job_scraper.py (check() was silent)"
```

---

## Task 14: Accurate `last_seen` + real delisting detection

**Execute this immediately after Task 8** — it edits the same nodes and closes the one defect from
the original audit that Task 8 turned out not to fix.

**Why.** The audit found `last_seen` frozen on 393 rows, so a posting pulled down from a board was
undetectable. Task 8 was supposed to fix that by re-stamping rows it re-injected, but review showed
that is actively wrong: backfill re-injects rows this scrape never observed, so stamping them
destroys the very signal. The real cause is upstream — `dedupe` removes already-seen postings before
`notify` persists, so a posting that IS still listed never gets its `last_seen` refreshed.

The fix does not infer delisting from staleness. `fetch_node` already knows which sources it read
successfully, so if a board was fetched without error and a stored posting was **not in the
response**, that is a direct observation that the posting is gone — no threshold, no grace period,
no new pref.

**Files:**
- Modify: `agents/job_scraper/nodes/fetch.py`, `nodes/freshness.py`, `nodes/notify.py`, `state.py`, `store.py`
- Test: `tests/test_delisting.py` (create)

**Interfaces:**
- Consumes: `store.load_records`, `store._write`.
- Produces: state keys `observed_ids: set[str]` (every posting id returned by any source this run)
  and `fetched_ok: set[str]` (company names whose every configured source fetched without error);
  `store.touch_last_seen(ids) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_delisting.py`:

```python
"""last_seen accuracy + delisting detection.

The audit found 393 rows with a frozen last_seen: `dedupe` drops already-seen
postings before `notify` persists, so a posting that is STILL listed never gets
re-stamped. Delisting is then detected directly rather than inferred from age —
if a board was fetched successfully and a stored posting was not in the response,
it is gone.
"""

from __future__ import annotations

import datetime as dt

from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.fetch import fetch_node
from agents.job_scraper.nodes.freshness import freshness_node


def _yesterday() -> str:
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def test_touch_last_seen_updates_column_and_blob(temp_db):
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
        "status": "new", "first_seen": _yesterday(), "last_seen": _yesterday(),
    })
    n = jobstore.touch_last_seen(["Acme:greenhouse:1"])
    assert n == 1

    today = dt.date.today().isoformat()
    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT last_seen, data FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)
        ).fetchone()
    import json
    assert row["last_seen"] == today, "mirrored column must be stamped"
    assert json.loads(row["data"])["last_seen"] == today, "blob must be stamped too"


def test_touch_last_seen_ignores_unknown_ids(temp_db):
    assert jobstore.touch_last_seen(["nope"]) == 0
    assert jobstore.touch_last_seen([]) == 0


def test_touch_last_seen_preserves_everything_else(temp_db):
    jobstore.replace_record({
        "id": "a", "company": "Acme", "title": "SWE Intern", "status": "applied",
        "country": "US", "fit_score": 91, "fit_reason": "great match",
        "first_seen": "2026-01-01", "last_seen": _yesterday(),
    })
    jobstore.touch_last_seen(["a"])
    rec = jobstore.load_records()["a"]
    assert rec["status"] == "applied"
    assert rec["country"] == "US"
    assert rec["fit_score"] == 91
    assert rec["first_seen"] == "2026-01-01", "first_seen must never move"


def test_fetch_reports_observed_ids_and_healthy_sources(monkeypatch):
    """A company counts as fetched_ok only if EVERY one of its sources succeeded."""
    from agents.job_scraper.nodes import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Acme", "ats": "greenhouse", "token": "acme"},
        {"company": "Beta", "ats": "greenhouse", "token": "beta"},
        {"company": "Beta", "ats": "lever", "token": "beta"},
    ])

    def fake_fetch(source):
        if source["ats"] == "lever":
            raise RuntimeError("bad token")
        return [{"id": f"{source['company']}:{source['ats']}:1", "company": source["company"]}]

    monkeypatch.setattr(fetch_mod, "fetch_source", fake_fetch)
    out = fetch_node({})

    assert out["observed_ids"] == {"Acme:greenhouse:1", "Beta:greenhouse:1"}
    assert out["fetched_ok"] == {"Acme"}, "Beta had a failing source, so it is not trustworthy"
    assert len(out["warnings"]) == 1


def test_delisting_flags_only_unobserved_rows_from_healthy_boards(temp_db):
    rows = [
        # still on the board -> not delisted
        {"id": "Acme:greenhouse:1", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board read fine, posting absent -> DELISTED
        {"id": "Acme:greenhouse:2", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board failed this run -> must NOT be called delisted
        {"id": "Beta:lever:9", "company": "Beta", "posted_at": "2026-07-01", "_rescored": True},
    ]
    out = freshness_node({
        "new": rows,
        "observed_ids": {"Acme:greenhouse:1"},
        "fetched_ok": {"Acme"},
    })["new"]
    by = {p["id"]: p for p in out}

    assert by["Acme:greenhouse:1"]["ghost"] is False
    assert by["Acme:greenhouse:2"]["ghost"] is True
    assert "delisted" in by["Acme:greenhouse:2"]["ghost_reason"]
    assert by["Beta:lever:9"]["ghost"] is False, "a failed fetch must never imply delisting"


def test_delisting_never_applies_to_freshly_scraped_postings(temp_db):
    """A brand-new posting is by definition observed; it must never be flagged."""
    out = freshness_node({
        "new": [{"id": "Acme:greenhouse:3", "company": "Acme", "posted_at": "2026-07-20"}],
        "observed_ids": {"Acme:greenhouse:3"},
        "fetched_ok": {"Acme"},
    })["new"]
    assert out[0]["ghost"] is False


def test_freshness_without_the_new_state_keys_is_unchanged(temp_db):
    """Backwards safety: absent observed_ids/fetched_ok, nothing is called delisted."""
    out = freshness_node({"new": [
        {"id": "x", "company": "Acme", "posted_at": "2026-07-20", "_rescored": True},
    ]})["new"]
    assert out[0]["ghost"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_delisting.py -v`
Expected: FAIL — `store.touch_last_seen` does not exist and `fetch_node` returns no `observed_ids`.

- [ ] **Step 3: Have `fetch_node` report what it saw and which boards were healthy**

Replace the body of `agents/job_scraper/nodes/fetch.py:fetch_node`:

```python
def fetch_node(state: JobScraperState) -> JobScraperState:
    raw: list[dict] = []
    warnings: list[str] = []
    ok: set[str] = set()
    failed: set[str] = set()

    for source in get_sources():
        company = source.get("company", "?")
        label = f"{company}/{source.get('ats', '?')}"
        try:
            postings = fetch_source(source)
            raw.extend(postings)
            ok.add(company)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
            failed.add(company)

    # A company is only trustworthy for delisting decisions when EVERY one of its
    # sources succeeded. If a company is on two boards and one 404s, a posting
    # missing from the other could easily still be live.
    return {
        "raw": raw,
        "warnings": warnings,
        "observed_ids": {p.get("id", "") for p in raw if p.get("id")},
        "fetched_ok": ok - failed,
    }
```

- [ ] **Step 4: Add `touch_last_seen` to the store**

Append to `agents/job_scraper/store.py`:

```python
def touch_last_seen(ids: list[str] | set[str]) -> int:
    """Stamp `last_seen` = today on postings observed in this scrape.

    Writes through `_write`, so the mirrored column and the `data` blob stay in
    sync (the dual-write rule). Ids absent from the table are skipped. Nothing
    else on the record is altered — notably `first_seen` and `status`.

    This is what makes `last_seen` mean "observed on a board", which is the
    prerequisite for detecting a delisted posting: `dedupe` removes already-seen
    postings before `notify` persists, so without this they would never be
    re-stamped.
    """
    ids = [i for i in ids if i]
    if not ids:
        return 0
    store_db.init_db()
    today = _today()
    touched = 0
    with store_db.connect() as conn:
        for pid in ids:
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            try:
                record = json.loads(row["data"]) if row["data"] else {}
            except json.JSONDecodeError:
                record = {}
            record["id"] = pid
            record["last_seen"] = today
            _write(conn, record)
            touched += 1
    return touched
```

- [ ] **Step 5: Detect delisting in `freshness`**

In `agents/job_scraper/nodes/freshness.py`, give `_ghost_reason` access to the two new state keys
and check delisting FIRST, since it is a direct observation rather than an age heuristic:

```python
def _ghost_reason(p: dict, observed_ids: set[str], fetched_ok: set[str]) -> str:
    """Return a short reason string if the posting looks like a ghost, else ""."""
    # Direct observation beats every heuristic: the board was read successfully
    # this run and this posting was not in it, so it is gone. Only trust this for
    # companies whose every source succeeded (see fetch_node).
    if p.get("company") in fetched_ok and p.get("id") not in observed_ids:
        return f"delisted (not on {p.get('company')}'s board)"

    age = p.get("age_days")
    if age is not None and age > config.JOB_MAX_AGE_DAYS:
        return f"stale ({age}d old)"

    deadline = (p.get("deadline") or "")[:10]
    if deadline:
        try:
            if dt.date.fromisoformat(deadline) < dt.date.today():
                return f"deadline passed ({deadline})"
        except ValueError:
            pass

    if p.get("listed") is False:  # Ashby-only signal; absent elsewhere
        return "delisted by source"

    return ""
```

and in `freshness_node`, read the keys defensively so a caller that omits them (older tests, a
partial run) can never mark anything delisted:

```python
def freshness_node(state: JobScraperState) -> JobScraperState:
    observed_ids = state.get("observed_ids") or set()
    fetched_ok = state.get("fetched_ok") or set()
    kept: list[dict] = []
    for p in state.get("new", []):
        p = {**p, "age_days": age_days(p.get("posted_at", ""))}
        reason = _ghost_reason(p, observed_ids, fetched_ok)
        p["ghost"] = bool(reason)
        p["ghost_reason"] = reason
        if reason and config.JOB_DROP_GHOSTS and not p.get("_rescored"):
            continue  # hard-drop mode, but never discard a re-injected row's update
        kept.append(p)
    return {"new": kept}
```

Note the `not p.get("_rescored")` guard — it is the Task 8 fix and must be preserved here.

- [ ] **Step 6: Refresh `last_seen` in `notify`**

In `notify_node`, after the `upsert_records(...)` call, stamp everything observed this run and log
the count (silent truncation reads as "covered everything"):

```python
        try:
            touched = touch_last_seen(state.get("observed_ids") or set())
            if touched:
                print(f"ℹ️ refreshed last_seen on {touched} still-listed posting(s)")
        except Exception as exc:
            print(f"⚠️ Could not refresh last_seen: {exc}")
```

Import it alongside the existing store import:

```python
from agents.job_scraper.store import touch_last_seen, upsert_records
```

Order matters: this runs AFTER `upsert_records`, so a row that is both re-injected and still listed
ends up correctly stamped with today.

- [ ] **Step 7: Document the new state keys**

In `agents/job_scraper/state.py`, add to `JobScraperState`:

```python
    # fetch: every posting id returned by any source this run. A stored posting
    # absent from this set, whose company is in `fetched_ok`, has been delisted.
    observed_ids: set[str]
    # fetch: companies whose EVERY configured source fetched without error. Only
    # these can be trusted for a delisting decision.
    fetched_ok: set[str]
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS, including the pre-existing freshness tests (which pass no `observed_ids` and so
must be unaffected).

- [ ] **Step 8b: Close two convergence gaps the Task 8 re-review surfaced**

Both are in the same "loops forever burning inference" class the Task 8 fix round closed.

**(i) A usable score with an EMPTY reason still reads as baseline.** In
`agents/job_scraper/nodes/rank.py:_score_batch`, the reason is written only `if hit["reason"]`. The
model's prompt asks for a `<=12 word` reason but nothing guarantees one, and `_parse` normalizes a
missing reason to `""`. So a reply carrying a good score but no reason leaves the baseline reason in
place — `is_baseline_reason` stays True, the row is re-selected every run forever, and each pass
re-clobbers the refined score via the unconditional baseline-first write at the top of the function.

Fix: when the model returns a usable score, always leave a non-baseline reason. Use the model's
reason when present, otherwise an explicit fallback:

```python
        if hit["score"] is not None:
            p["fit_score"] = hit["score"]
            # Always overwrite the reason when the score was refined. Leaving the
            # baseline reason in place would make is_baseline_reason() True, so the
            # row would be re-selected (and re-clobbered) on every future run.
            p["fit_reason"] = hit["reason"] or "refined (no reason given)"
```

Add a test asserting that a reply with a usable score and an empty reason yields a `fit_reason` for
which `is_baseline_reason()` is False, and that a second pass does not re-select the row.

**(ii) `upsert_records` honouring a record's own `last_seen` has no guarding test.** Reverting that
line to an unconditional `today` leaves the whole suite green. Add this to
`tests/test_delisting.py` (it already imports `datetime as dt` and `store as jobstore`):

```python
def test_upsert_preserves_an_explicit_last_seen(temp_db):
    """backfill must not be able to bump last_seen. Without this guard, a
    re-injected row that this scrape never observed gets today's date, which
    destroys the only signal that detects a delisted posting."""
    stale = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    jobstore.upsert_records([{
        "id": "a", "company": "Acme", "title": "SWE Intern", "last_seen": stale,
    }])
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT last_seen FROM jobs WHERE id = 'a'").fetchone()
    assert row["last_seen"] == stale

    # A posting genuinely observed this run still gets stamped, via touch_last_seen.
    jobstore.touch_last_seen(["a"])
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT last_seen FROM jobs WHERE id = 'a'").fetchone()
    assert row["last_seen"] == dt.date.today().isoformat()
```

- [ ] **Step 9: Correct the docs Task 8 had to walk back**

`agents/job_scraper/nodes/backfill.py`'s docstring was amended in Task 8 to say delisting detection
is NOT solved. Update it: `last_seen` is now refreshed by `notify` from `observed_ids`, and
`freshness` flags genuinely delisted postings. Backfill itself still must NOT stamp `last_seen`.

In `ARCHITECTURE.md`, note under the agent flow that the scraper detects delisted postings by
comparing stored ids against what each healthy board returned.

- [ ] **Step 10: Commit**

```bash
git add agents/job_scraper/ tests/test_delisting.py ARCHITECTURE.md
git commit -m "feat(jobs): accurate last_seen + detect delisted postings from healthy boards"
```

---

## Done criteria

- [ ] `.venv/bin/python -m pytest tests/` — all pass.
- [ ] `cd web-next && npm run db:check` — no drift.
- [ ] `cd web-next && npm run build` — succeeds.
- [ ] `SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL` returns **0** after backlog scoring.
- [ ] `SELECT COUNT(*) FROM jobs WHERE country = ''` returns **0**.
- [ ] The jobs board shows real fit numbers and a best-match hero.
- [ ] Ireland / Vietnam / Singapore roles are hidden by default and revealed by "all countries".
- [ ] Apply opens a modal, opens the posting + résumé PDF, logs the application with
      `resume_job_id` and `resume_pdf_key` populated, and is undoable.
- [ ] Expanding a `new` row marks it `viewed`; dismissed rows can be restored.
- [ ] Saving Settings twice in a row does not lose `JOB_PROFILE`.
- [ ] After a scrape, `SELECT COUNT(*) FROM jobs WHERE last_seen = date('now')` is greater than 0
      and matches the still-listed postings — not every row.
- [ ] A posting removed from a healthy board is flagged `ghost` with a `delisted` reason; a posting
      whose board failed to fetch is never flagged.
