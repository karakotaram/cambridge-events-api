"""Harvard Art Museums — the calendar's embedded `initialEvents` JSON.

Two saved pages: `listing.html.gz` (captured 2026-09-05) and
`calendar-2026-10-06.html.gz` (trimmed to the script that carries the JSON).
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def _scraper(fixture: str = None):
    scraper = BY_NAME["Harvard Art Museums"].load()
    if fixture:
        html = read_fixture("harvard_art_museums", fixture)
        scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    return scraper


def _listing(fixture: str) -> list:
    html = read_fixture("harvard_art_museums", fixture)
    return json.loads(re.search(r"var initialEvents = \[\]\.concat\((\[.*?\])\);", html, re.DOTALL).group(1))


def test_an_old_page_still_parses_in_full(offline):
    """Events before datetime.now() were dropped, so the September page lost
    events as the days passed. Staleness is EventValidator's job."""
    events = _scraper("listing.html.gz").scrape_events()

    assert len(events) == 73
    assert min(e.start_datetime for e in events) == datetime(2026, 9, 5, 11, 0)


@pytest.mark.parametrize("utc,listed,expected", [
    # 8:30 PM EDT is 00:30 UTC the next day
    ("2026-10-15T00:30:39.000000Z", "8:30 PM", datetime(2026, 10, 14, 20, 30)),
    # In standard time the boundary is 7 PM
    ("2026-12-03T00:00:39.000000Z", "7:00 PM", datetime(2026, 12, 2, 19, 0)),
    # A daytime start is unaffected
    ("2026-10-07T22:00:31.000000Z", "6:00 PM", datetime(2026, 10, 7, 18, 0)),
])
def test_an_evening_start_stays_on_its_eastern_date(utc, listed, expected):
    """The scraper put the local `start_time` onto the date of the *UTC*
    `date`, so anything at or after 8 PM EDT (7 PM EST) landed a day late.
    None did on 2026-10-06; the first late talk would have."""
    item = dict(_listing("calendar-2026-10-06.html.gz")[2], date=utc, start_time=listed, end_date=None)
    event = _scraper()._parse_event(item, set())

    assert event is not None
    assert event.start_datetime == expected


def test_disagreeing_renderings_are_dropped_not_guessed():
    """`date` and `start_time` describe the same moment. If they ever
    disagree, one changed meaning, and the event is skipped."""
    item = dict(_listing("calendar-2026-10-06.html.gz")[2],
                date="2026-10-07T22:00:31.000000Z", start_time="2:00 PM")
    assert _scraper()._parse_event(item, set()) is None


def test_todays_page(offline):
    """78 listings on 2026-10-06, of which 7 are "Museums Closed" notices.
    Every remaining start agrees with its own start_time text."""
    events = _scraper("calendar-2026-10-06.html.gz").scrape_events()
    by_title = {(e.title, e.start_datetime): e for e in events}

    assert len(events) == 71
    talk = by_title[("American Art Curators in Conversation", datetime(2026, 10, 7, 18, 0))]
    assert talk.end_datetime == datetime(2026, 10, 7, 19, 15)
    recital = by_title[("Midday Organ Recital: Nataly Pak", datetime(2026, 10, 8, 12, 15))]
    assert recital.end_datetime == datetime(2026, 10, 8, 12, 45)


@pytest.mark.parametrize("fixture", ["listing.html.gz", "calendar-2026-10-06.html.gz"])
def test_output_satisfies_invariants(fixture, offline):
    """`date` carries stray seconds (15:00:49); none may reach a start time."""
    events = _scraper(fixture).scrape_events()
    assert {e.source_name for e in events} == {"Harvard Art Museums"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
