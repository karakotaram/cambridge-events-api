"""Aeronaut Brewing — the events page as a browser rendered it on 2026-10-06."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.aeronaut import AeronautScraper
from tests.conftest import read_fixture


@pytest.fixture
def soup():
    return BeautifulSoup(read_fixture("aeronaut", "events-2026-10-06.html.gz"), "html.parser")


def test_listing_parses_and_satisfies_invariants(soup, offline):
    events = AeronautScraper().parse_listing(soup, date(2026, 10, 6))
    assert len(events) == 9
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"Aeronaut Brewing"}
    assert {(e.venue_name, e.street_address, e.city) for e in events} == {
        ("Aeronaut Brewing Co.", "14 Tyler St", "Somerville")}

    by_title = {e.title: e for e in events}
    assert by_title["Freeride Screening"].start_datetime == datetime(2026, 10, 7, 19, 0)
    assert by_title["Orkest"].start_datetime == datetime(2026, 10, 14, 19, 30)
    assert by_title["Freeride Screening"].source_url.startswith("https://www.tickettailor.com/"), \
        "the ticket link wins over a trailer"


def test_an_event_already_under_way_stays_in_this_year(soup, offline):
    """Any event earlier than the scrape's clock - including one that started
    an hour ago - was rolled into next year. "Wed, October 7 at 7PM" read on
    the evening of Oct 7 is still Oct 7, 2026: that date is a Wednesday only
    in 2026."""
    events = AeronautScraper().parse_listing(soup, date(2026, 10, 7))
    assert min(e.start_datetime for e in events) == datetime(2026, 10, 7, 19, 0)
    assert all(e.start_datetime.year == 2026 for e in events)


def test_dates_follow_the_printed_weekday():
    when = AeronautScraper.parse_when
    ref = date(2026, 10, 6)
    assert when("Tue, October 6 at 7PM", ref) == (datetime(2026, 10, 6, 19, 0), False)
    assert when("Sat, January 9 at 7:30PM", ref) == (datetime(2027, 1, 9, 19, 30), False)
    assert when("Thu, October 6 at 7PM", ref) == (None, False), "Oct 6 is a Mon/Tue/Wed in 2025-27"
    assert when("Sat, October 31", ref) == (datetime(2026, 10, 31), True), "a date with no time is all-day, not 7 PM"
    assert when("TBA", ref) == (None, False)
