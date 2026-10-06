"""Somerville Theatre — dates read from the plugin's own elements, never guessed.

The fixture is the live /events/ page of 2026-10-06 (40 performances).
"""
from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.somerville_theatre import SomervilleTheatreScraper
from tests.conftest import read_fixture


def _card(date_text: str, time_html: str) -> str:
    return (
        '<div class="wp_theatre_event">'
        '<figure><a href="https://www.somervilletheatre.com/production/x/"></a></figure>'
        '<div class="wp_theatre_event_title">Test Show</div>'
        '<div class="wp_theatre_event_datetime">'
        f'<div class="wp_theatre_event_date wp_theatre_event_startdate">{date_text}</div>'
        f'{time_html}</div></div>'
    )


def _scrape(html: str):
    scraper = SomervilleTheatreScraper()
    scraper.fetch_html = lambda *a, **k: html
    return scraper.scrape_events()


def test_every_listed_performance_comes_through(offline):
    """All 40 performances on the page, each at its listed time. The page
    concatenates date and time when flattened ("October 9, 20267:00 pm"), so
    they are read from their own elements."""
    events = _scrape(read_fixture("somerville_theatre", "events-2026-10-06.html.gz"))
    assert len(events) == 40
    assert {e.source_name for e in events} == {"Somerville Theatre"}
    assert not [e for e in events if e.all_day]

    by_title = {}
    for e in events:
        by_title.setdefault(e.title, []).append(e.start_datetime)
    assert by_title["Oteil Burbridge with LaMP - Wish Benefit Tour"] == [datetime(2026, 10, 9, 19, 0)]
    # Two shows on one night stay two events
    assert by_title["Andrea Gibson's Love Letter from the Afterlife"] == [
        datetime(2026, 11, 13, 18, 30), datetime(2026, 11, 13, 22, 0)]
    assert datetime(2026, 12, 12, 14, 0) in by_title["The Slutcracker"]

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_a_missing_time_is_not_8pm(offline):
    """A performance with no readable time used to be published at 8:00 pm.
    A date with no time element is a date-only listing (all-day); time text
    that is not a clock reading is skipped."""
    no_time = _scrape(_card("October 20, 2026", ""))
    assert len(no_time) == 1
    assert no_time[0].all_day and no_time[0].start_datetime == datetime(2026, 10, 20, 0, 0)

    empty_time = _scrape(_card("October 20, 2026", '<div class="wp_theatre_event_time wp_theatre_event_starttime"> </div>'))
    assert empty_time[0].all_day

    assert _scrape(_card("October 20, 2026", '<div class="wp_theatre_event_time wp_theatre_event_starttime">TBA</div>')) == []
    assert _scrape(_card("Coming soon", '<div class="wp_theatre_event_time wp_theatre_event_starttime">8:00 pm</div>')) == []

    eight = _scrape(_card("October 20, 2026", '<div class="wp_theatre_event_time wp_theatre_event_starttime">8 pm</div>'))
    assert eight[0].start_datetime == datetime(2026, 10, 20, 20, 0) and not eight[0].all_day


def test_no_clock_filter_on_past_performances(offline):
    """The scraper compared each start with datetime.now() and dropped past
    ones. Scrapers are pure; EventValidator owns staleness. A past date must
    parse like any other."""
    events = _scrape(_card("September 2, 2026", '<div class="wp_theatre_event_time wp_theatre_event_starttime">7:00 pm</div>'))
    assert [e.start_datetime for e in events] == [datetime(2026, 9, 2, 19, 0)]


def test_identifies_itself_instead_of_posing_as_safari():
    """The site 403s requests' default user-agent. The fix used to be a Safari
    UA claiming macOS; an honest one is served just the same."""
    ua = SomervilleTheatreScraper().get_browser_headers()["User-Agent"]
    assert "Mozilla" not in ua and "Safari" not in ua and "python-requests" not in ua


def test_markup_drift_yields_nothing_rather_than_page_chrome(offline):
    """There used to be a fallback onto any element whose class mentioned
    "event" or "production". Zero events is the loud failure; chrome is not."""
    html = BeautifulSoup(read_fixture("somerville_theatre", "events-2026-10-06.html.gz"), "html.parser")
    for div in html.find_all("div", class_="wp_theatre_event"):
        div["class"] = ["renamed_event"]
    assert _scrape(str(html)) == []
