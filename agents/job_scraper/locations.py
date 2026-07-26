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
    "starbase", "peachtree corners", "morristown", "irvine",
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
    "mexico", "mexico city", "guadalajara", "queretaro", "costa rica",
    "san jose, costa rica",
    "ukraine", "kyiv", "estonia", "tallinn", "lithuania", "vilnius",
    "russia", "moscow", "perm", "serbia", "belo horizonte",
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
