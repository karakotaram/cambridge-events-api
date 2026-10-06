"""Sanders Theatre: listings from the Harvard Box Office, some of them with no time.

The fixture is the rendered box-office events page, captured 2026-10-06.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.sanders_theatre import SandersTheatreScraper
from tests.conftest import read_fixture


def _events():
    scraper = SandersTheatreScraper()
    soup = BeautifulSoup(read_fixture("sanders_theatre", "boxoffice-events-2026-10-06.html.gz"), "html.parser")
    return [e for e in (scraper._parse_block(b) for b in soup.find_all(class_="item-description")) if e]


def test_a_run_with_no_time_is_all_day(offline):
    """"Midwinter Revels" is listed "December 11-28, 2026" with no time. It was
    published as a Dec 11 00:00 start with all_day=False, so it showed as 12:00 AM."""
    revels = [e for e in _events() if "Midwinter Revels" in e.title]
    assert len(revels) == 1
    assert (revels[0].start_datetime, revels[0].all_day) == (datetime(2026, 12, 11), True)


def test_a_listed_time_is_kept_and_not_all_day(offline):
    events = _events()
    timed = [e for e in events if "Midwinter Revels" not in e.title]
    assert timed and not [e.title for e in timed if e.all_day]
    assert not [e.title for e in timed if (e.start_datetime.hour, e.start_datetime.minute) == (0, 0)]

    kso = next(e for e in events if e.title == "Kendall Square Orchestra presents Voices of Freedom")
    assert kso.start_datetime == datetime(2026, 10, 9, 19, 30)


def test_parse_start_reports_whether_a_time_was_given():
    parse = SandersTheatreScraper._parse_start
    assert parse("Sunday, September 27, 2026 - 3:00pm") == (datetime(2026, 9, 27, 15, 0), True)
    assert parse("September 11-12, 2026 - 7:00pm") == (datetime(2026, 9, 11, 19, 0), True)
    assert parse("December 11-28, 2026") == (datetime(2026, 12, 11), False)
    assert parse("Coming soon") is None


def test_only_sanders_listings_and_invariants_hold(offline):
    """The box office sells for several Harvard venues; only Sanders is ours."""
    events = _events()
    assert len(events) == 23
    assert {e.venue_name for e in events} == {"Sanders Theatre"}
    assert {e.source_name for e in events} == {"Sanders Theatre"}

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_a_failed_page_load_fails_the_source():
    """A load that timed out used to return [] and be recorded as "ok, 0 events"."""
    class _Page:
        def goto(self, *a, **k):
            raise TimeoutError("Timeout 60000ms exceeded")

    scraper = SandersTheatreScraper()
    scraper._page = _Page()
    with pytest.raises(TimeoutError):
        scraper.scrape_events()
