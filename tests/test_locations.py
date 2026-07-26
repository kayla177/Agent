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
