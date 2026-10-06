"""MIT Music & Theater — the two department calendar APIs that replaced mta.mit.edu.

Fixtures are the live payloads of 2026-10-06: music.mit.edu (32 items, 26
upcoming) and theater.mit.edu (20 items, 4 upcoming). Each endpoint returns its
whole calendar, so both include past events; staleness is EventValidator's job.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.mit_music_theater import MITMusicTheaterScraper
from tests.conftest import read_fixture

NOW = datetime(2026, 10, 6)


@pytest.fixture
def api(monkeypatch, offline):
    """Serve each site's saved payload from requests.get."""
    import requests

    payloads = {
        "music.mit.edu": json.loads(read_fixture("mit_music_theater", "music-events-api-2026-10-06.json.gz")),
        "theater.mit.edu": json.loads(read_fixture("mit_music_theater", "theater-events-api-2026-10-06.json.gz")),
    }
    requested = []

    def fake_get(url, *args, **kwargs):
        requested.append((url, kwargs.get("headers", {})))
        body = next(p for host, p in payloads.items() if f"//{host}/" in url)
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


def test_reads_both_departments(api):
    """mta.mit.edu/events redirects to a landing page since the department
    split, and the Playwright scraper pointed at it never produced an event.
    Both new sites are read, every item comes through at its listed time, and
    nothing is invented."""
    events = MITMusicTheaterScraper().scrape_events()

    assert [u for u, _ in api] == ["https://music.mit.edu/wp-json/calendar/v1/events",
                                   "https://theater.mit.edu/wp-json/calendar/v1/events"]
    assert len(events) == 52
    upcoming = [e for e in events if e.start_datetime >= NOW]
    assert len(upcoming) == 30

    by_title = {e.title: e for e in events}
    concert = by_title["Stone and Earth: Edward Cohen Memorial Concert"]
    assert concert.start_datetime == datetime(2026, 10, 9, 20, 0)
    assert concert.source_url == "https://music.mit.edu/event/stone-and-earth-edward-cohen-memorial-concert/"
    assert concert.cost == "$15 General Admission | Free for MIT ID Holders"
    assert concert.category == EventCategory.MUSIC.value

    assert by_title["Performing Memory & Shaping Transformation"].category in (
        EventCategory.THEATER.value, EventCategory.LECTURES.value)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=NOW)
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_no_fabricated_dates_or_urls(api):
    """The old scraper fell back to today at 19:00 when a date did not parse,
    invented 19:00 for date-only listings, and replaced every absolute event
    URL with the listing URL. Now: the time is the API's or the event is
    all-day; each event links to its own page."""
    events = MITMusicTheaterScraper().scrape_events()

    supra = next(e for e in events if e.title == "Supra-Organism")
    assert supra.all_day and supra.start_datetime == datetime(2026, 10, 3, 0, 0)
    assert supra.cost is None, "'Part of MIT Future Fest' is not a price"

    assert len({e.source_url for e in events}) > 30
    assert all(e.source_url.startswith(("https://music.mit.edu/event/", "https://theater.mit.edu/event/"))
               for e in events)


def test_titles_and_descriptions_are_plain_text(api):
    """Titles arrive HTML-escaped ("Concert Choir &amp; Symphony Orchestra",
    "Cup o&#8217; Carnatic") and teasers carry <br /> tags."""
    events = MITMusicTheaterScraper().scrape_events()
    for e in events:
        for text in (e.title, e.description, e.cost or ""):
            assert "&amp;" not in text and "&#" not in text and "<br" not in text, text
    assert any(e.title.startswith("MIT Concert Choir & MIT Symphony Orchestra") for e in events)


def test_timestamp_field_is_not_an_instant():
    """`timestamp` is date+time written as UTC (an 8 PM concert is 20:00Z).
    The scraper reads `date` and `time`; this pins why."""
    item = json.loads(read_fixture("mit_music_theater", "music-events-api-2026-10-06.json.gz"))[0]
    assert item["date"] == "2026-08-19" and item["time"] == "17:30:00"
    as_utc = datetime.fromtimestamp(item["timestamp"], tz=timezone.utc).replace(tzinfo=None)
    assert as_utc == datetime(2026, 8, 19, 17, 30)


def test_read_start():
    read = MITMusicTheaterScraper.read_start
    assert read("2026-10-09", "20:00:00") == (datetime(2026, 10, 9, 20, 0), False)
    assert read("2026-10-09", "") == (datetime(2026, 10, 9), True)
    assert read("2026-10-09", "8pm") is None
    assert read("", "20:00:00") is None
    assert read("October 9", "20:00:00") is None


def test_identifies_itself():
    """Never spoof a browser user-agent."""
    from src.scrapers.mit_music_theater import USER_AGENT
    assert "Mozilla" not in USER_AGENT
