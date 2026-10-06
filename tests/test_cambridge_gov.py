"""City of Cambridge — paging through a Week view, and times with no meridiem.

The paging fixtures are the live Week view of 2026-10-04 (178 events) requested
at 50 per page, trimmed to `section#calendar`. The scraper asks for 200 per page,
so this is the same pager exercised four times instead of once.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.cambridge_gov import CambridgeGovScraper
from tests.conftest import read_fixture

WEEK = datetime(2026, 10, 5)


def _page(n: int) -> str:
    return read_fixture("cambridge_gov", f"week-2026-10-04-50pp-page{n}.html.gz")


def test_a_week_larger_than_one_page_is_read_to_the_end(offline):
    """Each week was fetched once with resultsperpage=200 and page 2 was never
    requested. The busiest week so far is 178, so nothing has been lost yet, but
    the first week past 200 would have dropped its tail without a sound. The
    "Displaying 51-100 of 178 results" note now drives the pager."""
    scraper = CambridgeGovScraper()
    scraper.PAGE_SIZE = 50
    requested = []

    def fake_fetch(url, *a, **k):
        requested.append(url)
        return _page(int(parse_qs(urlparse(url).query)["page"][0]))

    scraper.fetch_html = fake_fetch
    events = scraper.scrape_week(WEEK)

    assert [parse_qs(urlparse(u).query)["page"][0] for u in requested] == ["1", "2", "3", "4"]
    assert all("resultsperpage=50" in u for u in requested)
    assert len(events) >= 170, f"178 listed, {len(events)} parsed"
    days = {e.start_datetime.date() for e in events}
    assert min(days) == datetime(2026, 10, 4).date() and max(days) == datetime(2026, 10, 10).date()

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 5))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_paging_stops_when_the_note_says_everything_is_shown(offline):
    """One page holding every result must cost one request, not two."""
    scraper = CambridgeGovScraper()
    requested = []

    def fake_fetch(url, *a, **k):
        requested.append(url)
        return _page(4)  # "Displaying 151-178 of 178 results"

    scraper.fetch_html = fake_fetch
    scraper.scrape_week(WEEK)
    assert len(requested) == 1


def test_displaying_note_is_read():
    scraper = CambridgeGovScraper()
    soup = BeautifulSoup(_page(2), "html.parser")
    assert scraper._displaying(soup) == (100, 178)
    assert scraper._displaying(BeautifulSoup("<div></div>", "html.parser")) is None


def test_a_time_with_no_meridiem_is_skipped_not_read_from_the_attribute():
    """The `datetime` attribute is a 12-hour clock with no meridiem: 5 PM is
    `05:00:00`. When the visible text also lacked AM/PM, the attribute's time
    was kept, so a 5 PM event would publish at 5 AM. The time is unknowable;
    the row is skipped. The same goes for the heading fallback, which would
    otherwise read a bare "5:00" as morning or an empty text as midnight."""
    scraper = CambridgeGovScraper()

    def row(html):
        return BeautifulSoup(html, "html.parser").find("li")

    week = datetime(2026, 9, 14)
    assert scraper.parse_item_datetime(
        row('<li class="eventItem"><time datetime="2026-09-16 05:00:00">5:00</time></li>'),
        "Wednesday September 16", week) is None
    assert scraper.parse_item_datetime(
        row('<li class="eventItem"><time datetime="2026-09-16 05:00:00"></time></li>'),
        "Wednesday September 16", week) is None
    assert scraper.parse_item_datetime(
        row('<li class="eventItem"><time>5:00</time></li>'), "Wednesday September 16", week) is None
    assert scraper.parse_item_datetime(
        row('<li class="eventItem"><time>TBD</time></li>'), "Wednesday September 16", week) is None
    # The normal case is unchanged
    assert scraper.parse_item_datetime(
        row('<li class="eventItem"><time datetime="2026-09-16 05:00:00">5:00 PM</time></li>'),
        "Wednesday September 16", week) == datetime(2026, 9, 16, 17, 0)
