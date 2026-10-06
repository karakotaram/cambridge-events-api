"""Multicultural Arts Center — an Elementor site that rate-limits hard.

Fixtures, captured 2026-10-06:
  events-2026-10-06.html.gz          the /events/ listing (Upcoming + Past grids)
  event-pages-2026-10-06.json.gz     three event pages, keyed by URL: a timed
                                     range ("6:00 - 8:00 PM"), a three-day run
                                     with per-day times, and a run with no time
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture

LISTING = "https://multiculturalartscenter.org/events/"


@pytest.fixture
def listing():
    return read_fixture("multicultural_arts", "events-2026-10-06.html.gz")


@pytest.fixture
def pages():
    return json.loads(read_fixture("multicultural_arts", "event-pages-2026-10-06.json.gz"))


@pytest.fixture
def scraper():
    s = BY_NAME["Multicultural Arts Center"].load()
    s.REQUEST_DELAY = 0
    return s


def test_mac_requests_only_the_upcoming_events(listing, pages, scraper, offline):
    """The scraper walked /events/ pages 1-3 — which repeat the same grids —
    and fetched an event page for every card, past ones included: ~40
    requests, and the site answers 429 well before that. One listing page
    plus one page per upcoming card is seven."""
    fetched = []

    def fetch(url, *a, **k):
        fetched.append(url)
        if url == LISTING:
            return listing
        if url in pages:
            return pages[url]
        raise IOError(f"not in fixture: {url}")

    scraper.fetch_html = fetch                      # type: ignore[method-assign]
    events = scraper.scrape_events()

    assert len(fetched) == 7
    assert fetched[0] == LISTING and not [u for u in fetched if "/page/" in u]
    assert len(events) == 3, "three event pages are in the fixture; the other three fail to load and are skipped"
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_mac_dates_never_come_from_the_upcoming_sidebar(pages, scraper, offline):
    """Every event page embeds an "Upcoming Events" grid of other events'
    dates. The old date search ran over the whole page and took the first
    "Month D, YYYY" it met — the sidebar's — so a past event ("The X-tet")
    was published on another event's date. A card date that appears only in
    that grid must not be confirmed by it."""
    page = BeautifulSoup(pages["https://multiculturalartscenter.org/barbed-wire/"], "html.parser")
    assert "October 6, 2026" in page.get_text(), "fixture should carry the sidebar grid"

    assert scraper.parse_detail(page, "Barbed Wire", "https://multiculturalartscenter.org/barbed-wire/",
                                "October 6, 2026") is None


def test_mac_reads_the_time_under_the_date(pages, scraper, offline):
    """"6:00 - 8:00 PM" was published as 8 PM: the old search took the first
    "N pm" anywhere on the page. The start borrows the end's meridiem."""
    url = "https://multiculturalartscenter.org/redefining-divinityb/"
    event = scraper.parse_detail(BeautifulSoup(pages[url], "html.parser"),
                                 "Redefining Divinity Workshop with Megha Nair", url, "October 6, 2026")
    assert event.start_datetime == datetime(2026, 10, 6, 18, 0)
    assert event.end_datetime == datetime(2026, 10, 6, 20, 0)
    assert not event.all_day
    assert event.description.startswith("What if we were to reimagine"), "og:description is only the date"


def test_mac_a_run_without_one_start_is_all_day(pages, scraper, offline):
    """The M³ Festival (May 20–22) states no time and was published at a
    fabricated 10 AM; Barbed Wire runs three days at 8pm, 8pm and 2pm and was
    published at 8 PM on day one. Both are all-day listings on their first
    day, with the venue's own schedule kept in the description."""
    url = "https://multiculturalartscenter.org/mac-movement-makers/"
    festival = scraper.parse_detail(BeautifulSoup(pages[url], "html.parser"),
                                    "MAC Movement Makers: The M³ Festival", url, "May 20–22, 2027")
    assert festival.all_day and festival.start_datetime == datetime(2027, 5, 20)
    assert festival.end_datetime == datetime(2027, 5, 22)

    url = "https://multiculturalartscenter.org/barbed-wire/"
    run = scraper.parse_detail(BeautifulSoup(pages[url], "html.parser"), "Barbed Wire", url, "January 15–17, 2027")
    assert run.all_day and run.start_datetime == datetime(2027, 1, 15)
    assert "8pm (Fri + Sat) and 2pm (Sun)" in run.description


@pytest.mark.parametrize("text,expected", [
    ("7:30 PM", (datetime(2026, 10, 7, 19, 30), None)),
    ("12:00 PM", (datetime(2026, 10, 7, 12, 0), None)),
    ("6:00 - 8:00 PM", (datetime(2026, 10, 7, 18, 0), datetime(2026, 10, 7, 20, 0))),
    ("11:00 - 1:00 PM", (datetime(2026, 10, 7, 11, 0), datetime(2026, 10, 7, 13, 0))),
    ("8pm", (datetime(2026, 10, 7, 20, 0), None)),
    ("7:30", None),
    ("Doors open soon", None),
])
def test_mac_time_text(text, expected, scraper):
    """No meridiem anywhere is unreadable, and unreadable means skipped —
    never the old 7 PM default."""
    assert scraper.parse_time_text(text, datetime(2026, 10, 7)) == expected


@pytest.mark.parametrize("text,expected", [
    ("October 7, 2026", (datetime(2026, 10, 7), None)),
    ("Sept 4, 2026", (datetime(2026, 9, 4), None)),
    ("January 15–17, 2027", (datetime(2027, 1, 15), datetime(2027, 1, 17))),
    ("October 31 – November 2, 2026", (datetime(2026, 10, 31), datetime(2026, 11, 2))),
    ("Coming soon", None),
])
def test_mac_card_dates(text, expected, scraper):
    assert scraper.parse_date_text(text) == expected
