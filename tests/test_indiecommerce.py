"""Porter Square Books and Harvard Book Store: IndieCommerce month calendars.

Both were retired for Cloudflare blocks and revived on 2026-10-06 reading the
same pages in an ordinary visible browser. These parse the October calendars
as that browser received them, and pin down the honesty constraints the
revival was agreed on.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.base_playwright_scraper import ScrapeRefusedError
from src.scrapers.harvard_book_store import HarvardBookStoreScraper
from src.scrapers.porter import PorterSquareBooksScraper
from tests.conftest import read_fixture


@pytest.fixture
def porter_html():
    return read_fixture("porter", "calendar-2026-10.html.gz")


@pytest.fixture
def harvard_html():
    return read_fixture("harvard_book_store", "calendar-2026-10.html.gz")


def _errors(events):
    return [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]


def test_porter_reads_the_whole_month_with_real_cities(porter_html, offline):
    """The old scraper set city="Cambridge" on everything, including events at
    the store's Boston branch."""
    scraper = PorterSquareBooksScraper()
    events = scraper.parse_calendar(porter_html)

    assert len(events) == 45
    assert not _errors(events)
    assert all((e.start_datetime.year, e.start_datetime.month) == (2026, 10) for e in events)
    assert {e.city for e in events} == {"Cambridge", "Boston"}
    story = next(e for e in events if e.title == "PSB Story Hour!")
    assert story.start_datetime == datetime(2026, 10, 1, 10, 0)
    assert story.venue_name == "Porter Square Books Cambridge Edition"
    assert scraper.next_month_path(porter_html) == "/events/calendar/2026/11"


def test_harvard_places_events_tagged_with_the_store(harvard_html, offline):
    """Some events have no place block, only the store's own location tag."""
    events = HarvardBookStoreScraper().parse_calendar(harvard_html)

    assert len(events) == 45
    assert not _errors(events)
    assert all(e.city for e in events)
    boyd = next(e for e in events if e.title.startswith("danah boyd"))
    assert (boyd.start_datetime, boyd.street_address, boyd.cost) == (
        datetime(2026, 10, 1, 19, 0), "1256 Massachusetts Ave", "Free")


def test_a_start_that_disagrees_with_the_printed_date_is_skipped(porter_html, offline):
    """The ISO start is believed only when the teaser prints the same moment."""
    scraper = PorterSquareBooksScraper()
    soup = BeautifulSoup(porter_html, "html.parser")
    settings = scraper._settings(soup)
    options = settings["fullCalendarView"][0]["calendar_options"]
    options = json.loads(options) if isinstance(options, str) else options
    item = dict(options["events"][0])

    assert scraper._parse_item(item) is not None
    assert scraper._parse_item({**item, "start": "2026-10-02T10:00:00-04:00"}) is None, "wrong day"
    assert scraper._parse_item({**item, "start": "2026-10-01T22:00:00-04:00"}) is None, "wrong time"


class _StuckPage:
    """A page that never gets past a bot check."""
    url = "https://example.org/events"

    def title(self):
        return "Just a moment..."

    def wait_for_timeout(self, ms):
        pass


def test_a_challenge_that_does_not_clear_fails_the_source():
    """Nothing interacts with a challenge. If it does not clear on its own the
    source fails loudly, rather than parsing an interstitial as no events."""
    scraper = PorterSquareBooksScraper()
    scraper._page = _StuckPage()
    with pytest.raises(ScrapeRefusedError, match="bot-check"):
        scraper.wait_past_challenge(timeout_s=2)


@pytest.mark.parametrize("name", ["Porter Square Books", "Harvard Book Store", "Aeronaut Brewing"])
def test_visible_browser_sources_stay_honest_and_local(name):
    """The terms these were revived on: an ordinary visible browser with its
    own user-agent and no automation-hiding flags, run locally only."""
    import inspect

    from src.sources import BY_NAME

    source = BY_NAME[name]
    scraper = source.load()
    assert scraper.headless is False
    assert scraper.user_agent is None, "the browser's own user-agent"
    assert source.runs_in_ci is False, "a window needs a display"
    code = inspect.getsource(inspect.getmodule(type(scraper)))
    for flag in ("AutomationControlled", "enable-automation", "navigator.webdriver", "stealth"):
        assert flag not in code
