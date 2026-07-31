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
    # Found in real `status='new'` data during Step 5 tuning: these bare city
    # names fell through to UNKNOWN before their terms were added. UNKNOWN
    # postings are never hidden by callers, so a real foreign job classified
    # UNKNOWN instead of OTHER would leak into the North-America-only UI.
    "Serbia", "Moscow", "Perm", "Queretaro", "BELO HORIZONTE",
])
def test_other_locations_from_real_data(loc):
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


# --- Regression tests for the Task 2 review findings -----------------------
#
# Root cause of the review's Critical defect: `_classify_segment` checked
# `_FOREIGN_RE` before US names/postal codes, so any US location that shares
# a word with a foreign city or country (Vienna/Dublin/Athens/Manchester/
# Rome/Glasgow/Cairo/Moscow are all also foreign place names) silently
# classified as OTHER and would have been hidden from the board. Every
# string below is real or review-specified; each one is a case that was
# either wrong before the fix or is a load-bearing edge the fix must not
# regress.

@pytest.mark.parametrize("loc", [
    "Vienna, VA", "Vienna, Virginia", "Dublin, OH", "Athens, GA",
    "Manchester, NH", "Rome, GA", "Glasgow, KY", "Cairo, IL", "Moscow, ID",
])
def test_us_state_qualifier_beats_foreign_name_collision(loc):
    """A US state qualifier (postal code or full name) wins over a
    same-spelled foreign place name. Before the fix, all nine of these
    classified as OTHER because the foreign-name check ran first."""
    assert country_of(loc) == "US"


def test_multi_site_us_state_qualifier_beats_foreign_segment():
    """The module's headline multi-site guarantee must survive the fix: a
    real US state qualifier plus a foreign segment is still US."""
    assert country_of("Vienna, VA; Amsterdam") == "US"


def test_london_on_is_canada_not_uk():
    """Verifies the module docstring's own claim by execution: bare "London"
    is the UK (OTHER); "London, ON" is the Ontario one (CA)."""
    assert country_of("London") == "OTHER"
    assert country_of("London, ON") == "CA"


@pytest.mark.parametrize("loc, expected", [
    ("Tbilisi, Georgia", "OTHER"),  # the country, not the US state
    ("Atlanta, GA", "US"),
    ("Savannah, Georgia", "US"),
])
def test_georgia_state_vs_country_disambiguation(loc, expected):
    """"Georgia" alone is genuinely ambiguous between the US state and the
    country (Tbilisi), so it is deliberately absent from both name lists;
    disambiguation comes from unambiguous city names (savannah, tbilisi) or
    the "GA" postal code instead."""
    assert country_of(loc) == expected


@pytest.mark.parametrize("loc", [
    "US-CA-Menlo Park", "US-DE-Wilmington", "US-WA-Bellevue",
    "USA - CA - Los Angeles - 13031 W. Jefferson Blvd #400",
])
def test_us_prefixed_dash_delimited_state_code(loc):
    """Real ATS format found in the live DB: an explicit "US"/"USA" token
    followed by a dash- or space-delimited state code."""
    assert country_of(loc) == "US"


def test_state_code_before_the_comma():
    """"Jefferson Hills PA, 15025, ..." — the code sits directly before the
    comma rather than after it; state-position recognizes both sides."""
    assert country_of("Jefferson Hills PA, 15025, 565 Coal Valley Road") == "US"


def test_leading_state_code_dash_fallback():
    """"MI - Detroit Sales Office" has no comma and no US/USA prefix — only
    the last-resort leading-code-plus-dash fallback recognizes it, and only
    because the foreign-name check already had a chance to veto it first."""
    assert country_of("MI - Detroit Sales Office") == "US"


@pytest.mark.parametrize("loc", [
    "DE-Berlin-Trion Building", "DE-Munich-MSO",
])
def test_bare_country_code_prefix_is_not_delaware(loc):
    """A bare leading "DE-" with no "US"/"USA" token must NOT be read as
    Delaware: these are Germany's Berlin/Munich offices. The foreign-name
    match on "berlin"/"munich" must win before the leading-code fallback
    (which has no way to tell "DE-" the Delaware prefix from "DE-" the
    German ISO country-code prefix) is ever tried."""
    assert country_of(loc) == "OTHER"
