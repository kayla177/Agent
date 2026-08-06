"""Classify a free-form ATS location string into US / Canada / elsewhere.

Deterministic, no LLM, no network. Rules that make this correct rather than
merely plausible:

1. EVERY match is word-boundary anchored. A substring check for "uk" matches
   inside "Milwaukee" — the same trap matching.py documents for role keywords.

2. Classification is PER SEGMENT, and a posting listing both a US and a foreign
   site is US. Multi-site postings like "Austin, Texas; Amsterdam" are real US
   jobs, so aggregating US/CA over segments beats classifying the whole string.

3. Two-letter postal codes are only trusted in "state position" — immediately
   adjacent to a comma (either side: "Vienna, VA" and "Jefferson Hills PA,
   15025" both count), or immediately after an explicit "US"/"USA" token
   ("US-CA-Menlo Park", "USA - CA - Los Angeles"). A bare two-letter token
   floating in running text is NOT trusted as a postal code — that's what lets
   "IN - Bangalore, India" resolve to OTHER instead of Indiana's "IN": the "IN"
   there is not comma-adjacent and has no "US" prefix, so it is never read as a
   code, and the segment falls through to the foreign-name check, which finds
   "india".

4. Within a segment, US names are checked BEFORE foreign names, because a
   full US state/city name is a stronger, unambiguous signal than a foreign
   name that happens to share a word with a US place — e.g. "Vienna, Virginia"
   must be US even though "vienna" also names the Austrian capital: the US
   abbreviation/name checks run first and short-circuit the foreign check.
   The reverse case ("Dublin, Ireland", "Vienna" alone, "Manchester" alone)
   still resolves to OTHER because no US signal is present to short-circuit.

5. A last-resort check recognizes a bare two-letter code at the very START of
   a segment followed by a dash ("MI - Detroit Sales Office") as a US state,
   but only AFTER the foreign-name check has already had a chance to veto —
   so "DE-Berlin-Trion Building" and "DE-Munich-MSO" (Germany, not Delaware)
   are caught by the "berlin"/"munich" foreign-name match before this
   fallback is ever reached. This fallback is a residual-risk trade-off: an
   uncovered foreign city not in `_FOREIGN_NAMES` that also starts with a
   letter-pair matching a US postal code (e.g. "OH - <unlisted city>") would
   still misresolve to US. Accepted because the alternative (never resolving
   `"MI - Detroit Sales Office"` and its like) is worse for the common case,
   and the miss is safe-by-default (see point 6) if it goes the OTHER way,
   not this way, only for names that survive the foreign-name allowlist gap.

6. Ambiguous strings ("2 Locations", "Stamford Hub", bare "Remote") return
   UNKNOWN and are never dropped by callers — only OTHER is hidden downstream.
   That asymmetry is why point 5 is placed after the foreign check: a false
   MISS (foreign job mistakenly landing on UNKNOWN) is safe, but a false HIT
   (a real US job mistakenly landing on OTHER) causes real data loss, so
   ambiguous signals are resolved in the direction that avoids the second
   failure mode.

Verified by execution: bare "London" resolves to OTHER (the UK one);
"London, ON" resolves to CA (the "ON" is comma-adjacent, checked before any
foreign name).
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
    "connecticut", "delaware", "florida", "hawaii", "idaho",
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
    # "georgia" (the US state) is intentionally NOT listed here: it is
    # indistinguishable, as a bare word, from the country Georgia (Tbilisi).
    # Real Georgia-state postings are resolved by unambiguous city names
    # instead (below), or by the "GA" postal code in state position, or by
    # a co-occurring "usa"/"united states" token.
    "norcross", "savannah",
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
    "ukraine", "kyiv", "estonia", "tallinn", "lithuania", "vilnius",
    "russia", "moscow", "perm", "serbia", "belo horizonte",
    # Country Georgia, distinct from the US state (see _US_NAMES comment
    # above) — resolved via its unambiguous capital rather than the bare,
    # overloaded word "georgia".
    "tbilisi",
)


def _word_re(terms) -> re.Pattern:
    """Case-insensitive alternation, word-boundary anchored, longest-first so
    'new york city' wins over 'new york'."""
    ordered = sorted(terms, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in ordered) + r")\b", re.IGNORECASE)


def _comma_position_abbr_re(codes) -> re.Pattern:
    """Two-letter postal codes recognized only in "state position": directly
    adjacent to a comma, on either side ("Vienna, VA" and "Jefferson Hills
    PA, 15025" both match). Word-boundary anchored AND case-SENSITIVE, so
    'in' inside a sentence never matches Indiana, and a bare code floating
    elsewhere in the string (e.g. the "IN" in "IN - Bangalore, India") is
    never read as a postal code — it isn't next to a comma."""
    alt = "|".join(codes)
    return re.compile(rf"(?:,\s*(?:{alt})\b)|(?:\b(?:{alt})\b\s*,)")


def _us_prefixed_abbr_re(codes) -> re.Pattern:
    """US state abbreviations recognized after an explicit US/USA token,
    dash- or space-delimited: "US-CA-Menlo Park", "US-DE-Wilmington",
    "USA - CA - Los Angeles - ...". The explicit US/USA token is the
    disambiguator that lets this differ from a bare foreign country-code
    prefix like "DE-Berlin" (Germany's Trion office), which must NOT be read
    as Delaware — that string has no "US"/"USA" token to license the read.
    Case-sensitive for the same reason as the comma-position check."""
    alt = "|".join(codes)
    return re.compile(rf"\bUSA?\b[\s-]+(?:{alt})\b")


def _us_leading_abbr_re(codes) -> re.Pattern:
    """Last-resort: a bare two-letter US postal code at the very START of a
    segment, followed by a dash ("MI - Detroit Sales Office"). Only
    consulted AFTER the foreign-name check (see module docstring point 5),
    so "DE-Berlin-Trion Building" / "DE-Munich-MSO" are already caught as
    OTHER via "berlin"/"munich" before this pattern is ever tried."""
    alt = "|".join(codes)
    return re.compile(rf"^(?:{alt})\b\s*-")


_CA_NAME_RE = _word_re(_CA_NAMES)
_US_NAME_RE = _word_re(_US_NAMES)
_FOREIGN_RE = _word_re(_FOREIGN_NAMES)
_CA_ABBR_POSITION_RE = _comma_position_abbr_re(_CA_ABBR)
_US_ABBR_POSITION_RE = _comma_position_abbr_re(_US_ABBR)
_US_ABBR_PREFIX_RE = _us_prefixed_abbr_re(_US_ABBR)
_US_ABBR_LEADING_RE = _us_leading_abbr_re(_US_ABBR)


def _classify_segment(seg: str) -> str:
    """Classify ONE location segment. See module docstring for the full
    ordering rationale; in short: CA name -> CA postal code in state
    position -> US postal code in state position -> US name -> foreign name
    -> US postal code as a last-resort leading token -> UNKNOWN.

    US names and CA/US state-position codes are checked before foreign
    names so "Vienna, VA" / "Vienna, Virginia" / "Dublin, OH" / "London, ON"
    resolve correctly even though "vienna"/"dublin"/"london" are also
    foreign place names. "IN - Bangalore, India" still resolves to OTHER
    because the "IN" there is not comma-adjacent and has no US prefix, so no
    US signal fires before the foreign-name check finds "india".
    """
    s = seg.strip()
    if not s or s == "—":
        return "UNKNOWN"
    if _CA_NAME_RE.search(s):
        return "CA"
    if _CA_ABBR_POSITION_RE.search(s):
        return "CA"
    if _US_ABBR_POSITION_RE.search(s) or _US_ABBR_PREFIX_RE.search(s):
        return "US"
    if _US_NAME_RE.search(s):
        return "US"
    if _FOREIGN_RE.search(s):
        return "OTHER"
    if _US_ABBR_LEADING_RE.search(s):
        return "US"
    return "UNKNOWN"


#: Only ever applied to a TITLE, never to a location. See `country_of`.
_TITLE_US_RE = re.compile(r"\bU\.S\.(?:A\.?)?(?=\s|$|[,\-–—:])", re.IGNORECASE)


def _classify_text(text: str) -> str:
    """US | CA | OTHER | UNKNOWN for one free-form string.

    North America wins over foreign: a posting listing both a US site and a
    foreign site is a real US job, so any US/CA segment decides the result.
    """
    text = (text or "").strip()
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


def country_of(location: str, title: str = "") -> str:
    """Classify a posting as US | CA | OTHER | UNKNOWN.

    `location` decides it whenever it can. `title` is consulted ONLY when the
    location classifies as UNKNOWN, and it exists because some boards publish a
    WORK ARRANGEMENT where a place is expected: Cloudflare's Greenhouse postings
    carry "In-Office", "Hybrid; In-Office" or "Remote" as their location, so
    there is no geography there to read, while the title says "U.S. Public Policy
    and AI Innovation Intern" or "... - Austin, TX".

    That gap is not cosmetic. `country` is what `default_country_of` hands the
    applier's eligibility rules, so an UNKNOWN posting makes every sponsorship
    and work-authorization question unanswerable — which is exactly how Kayla's
    first real assisted-apply run came back with "this question does not name a
    single country" on a job whose title begins "U.S.".

    The fallback may only ADD information, never overturn it: this module's rule
    is that a false MISS is safe and a false HIT is not, and a title is weaker
    evidence than a location field. So a location that classifies at all wins,
    including when it says OTHER.
    """
    from_location = _classify_text(location)
    if from_location != "UNKNOWN":
        return from_location
    # Title-only signal. "U.S." WITH periods is unambiguous in a job title —
    # the pronoun is never written that way — whereas the bare token "us" is the
    # pronoun far more often than the country, which is why `_classify_segment`
    # rightly refuses it. Kept here rather than widened into the location
    # classifier so the stricter rule that guards every OTHER caller is untouched.
    if _TITLE_US_RE.search(title or ""):
        return "US"
    return _classify_text(title)
