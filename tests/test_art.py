"""American Repertory Theater — show pages saved 2026-10-06.

1972: A Rock Opera (Farkas Hall, Oct 27 - Nov 22), Mexodus (Loeb Drama Center,
Dec 4 - Jan 10, 2027) and Life on Mars (Goel Center, May 18 - Jul 3, 2027),
plus the /shows-events/ listing that links them.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.art import AmericanRepertoryTheaterScraper, year_for_weekday
from tests.conftest import read_fixture

BASE = "https://americanrepertorytheater.org"
PAGES = {
    f"{BASE}/shows-events/": "listing-2026-10-06.html.gz",
    f"{BASE}/shows-events/1972-a-rock-opera/": "show-1972-a-rock-opera.html.gz",
    f"{BASE}/shows-events/mexodus/": "show-mexodus.html.gz",
    f"{BASE}/shows-events/life-on-mars/": "show-life-on-mars.html.gz",
}


def _run(today: date):
    scraper = AmericanRepertoryTheaterScraper(today=today)
    scraper.fetch_page = lambda url: read_fixture("art", PAGES[url])     # type: ignore[method-assign]
    return scraper.scrape_events()


@pytest.fixture
def events(offline):
    return _run(date(2026, 10, 6))


def test_every_performance_is_read(events):
    assert Counter(e.title for e in events) == {
        "Life on Mars": 52, "Mexodus": 43, "1972: A Rock Opera": 30}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_each_show_is_in_its_own_building(events):
    """Every performance was stamped Loeb Drama Center, which was wrong for 82
    of 125: 1972 plays Farkas Hall, Life on Mars the Goel Center in Allston."""
    where = {(e.title, e.venue_name, e.street_address, e.city, e.zip_code) for e in events}
    assert where == {
        ("1972: A Rock Opera", "Farkas Hall", "12 Holyoke Street", "Cambridge", "02138"),
        ("Mexodus", "Loeb Drama Center", "64 Brattle Street", "Cambridge", "02138"),
        ("Life on Mars", "Goel Center for Creativity & Performance", "175 N. Harvard Street", "Boston", "02134"),
    }


def test_year_comes_from_the_printed_weekday(offline):
    """The year was taken from the clock: a month at or after this one meant
    this year. Run in June 2026, that put Life on Mars's June and July 2027
    performances in 2026. "Saturday July 3" is 2027 because Jul 3 2026 is a
    Friday, whenever the scrape runs."""
    for today in (date(2026, 6, 1), date(2026, 10, 6), date(2027, 3, 1)):
        mars = sorted(e.start_datetime for e in _run(today) if e.title == "Life on Mars")
        assert (mars[0], mars[-1]) == (datetime(2027, 5, 18, 19, 30), datetime(2027, 7, 3, 19, 30)), today

    assert year_for_weekday(7, 3, 5, date(2026, 6, 1)) == 2027
    assert year_for_weekday(7, 3, 4, date(2026, 6, 1)) == 2026
    assert year_for_weekday(7, 3, 6, date(2026, 6, 1)) is None


def test_a_weekday_that_matches_no_year_is_skipped(offline):
    scraper = AmericanRepertoryTheaterScraper(today=date(2026, 10, 6))
    instance = BeautifulSoup(
        '<div class="c-booking-instance"><p class="c-booking-instance__day">Monday</p>'
        '<p class="c-booking-instance__month">July 3</p>'
        '<p class="c-booking-instance__times">7:30PM ET</p></div>', "html.parser").div
    assert scraper.performance_start(instance, date(2026, 10, 6)) is None
    instance.find(class_="c-booking-instance__day").string = "Saturday"
    assert scraper.performance_start(instance, date(2026, 10, 6)) == datetime(2027, 7, 3, 19, 30)
    instance.find(class_="c-booking-instance__times").string = "Times TBA"
    assert scraper.performance_start(instance, date(2026, 10, 6)) is None, "no default 7 PM"


def test_titles_carry_no_time(events):
    """Every title had its time appended ("Life on Mars - 7:30 PM")."""
    assert not [e.title for e in events if " PM" in e.title or " AM" in e.title]


def test_times_and_links(events):
    mexodus = sorted((e for e in events if e.title == "Mexodus"), key=lambda e: e.start_datetime)
    assert mexodus[0].start_datetime == datetime(2026, 12, 4, 19, 30)
    assert mexodus[0].source_url.startswith("https://ticket.americanrepertorytheater.org/")
    assert mexodus[-1].start_datetime == datetime(2027, 1, 10, 14, 0)
    assert mexodus[-1].cost == "Sold Out"
    asl = [e for e in events if "ASL Interpreted" in e.tags]
    assert {e.title for e in asl} == {"Mexodus", "1972: A Rock Opera"}
