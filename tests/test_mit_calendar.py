"""MIT Events — the Localist API, filtered to events open to the general public.

Fixtures are the seven pages of `api/2/events?days=60&pp=100` fetched on
2026-10-06 (665 occurrences of 392 events), trimmed to the fields the scraper
reads. Descriptions without an audience statement are cut to 300 characters.
"""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.mit_calendar import MITCalendarScraper
from tests.conftest import read_fixture

PAGES = 7


def _page(n: int) -> dict:
    return json.loads(read_fixture("mit_calendar", f"events-api-60d-page{n}.json.gz"))


@pytest.fixture
def api(monkeypatch, offline):
    import requests

    requested = []

    def fake_get(url, params=None, **kwargs):
        requested.append(dict(params or parse_qs(urlparse(url).query)))
        body = _page(int(requested[-1]["page"]))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


def _all_items():
    return [x["event"] for n in range(1, PAGES + 1) for x in _page(n)["events"]]


def test_reads_the_whole_calendar_not_the_homepage(api):
    """The Playwright scraper read the homepage JSON-LD: today plus featured,
    18 events published on 2026-10-05, about 5% of the calendar. The API is
    paged by `page.total`; every page is read, 60 days ahead, server-side."""
    events = MITCalendarScraper().scrape_events()

    assert [int(p["page"]) for p in api] == list(range(1, PAGES + 1))
    assert all(int(p["days"]) == 60 and int(p["pp"]) == 100 for p in api)
    # 169 public occurrences of 139 events; two are re-posts of one seminar
    assert len(events) == 167
    assert len({e.source_url for e in events}) == 137
    days = {e.start_datetime.date() for e in events}
    assert min(days) == datetime(2026, 10, 6).date() and max(days) >= datetime(2026, 12, 1).date()

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_only_events_open_to_the_general_public(api):
    """Most of calendar.mit.edu is internal - seminars and socials for the MIT
    community. MIT's "Events By Audience" filter says who may attend; an event
    is published if it includes "Public" or its description says it is open to
    the public, unless the description restricts it."""
    scraper = MITCalendarScraper()
    items = _all_items()
    published = {e.source_url for e in scraper.scrape_events()}

    def audience(item):
        return {a["name"] for a in item["filters"].get("event_audience", [])}

    community_only = [i for i in items if audience(i) == {"MIT Community"}
                      and "open to the public" not in (i["description_text"] or "").lower()]
    assert len(community_only) > 100, "fixture should be mostly internal"
    assert not [i for i in community_only if i["localist_url"] in published]

    # Tagged Public, but its own text says otherwise
    wreath = next(i for i in items if i["title"].startswith("Coffee Social & Holiday Wreath"))
    assert "Public" in audience(wreath)
    assert wreath["localist_url"] not in published

    # Untagged, but "Free and open to the public"
    book = next(i for i in items if i["title"].startswith("Our Own Language"))
    assert not audience(book)
    assert book["localist_url"] in published


@pytest.mark.parametrize("text,public", [
    ("Free and open to the public.", True),
    ("This event is open the MIT Community only.", False),
    ("This event is invite-only for the MIT community.", False),
    ("This lecture is not open to the public.", False),
    ("Tickets $10, free for MIT students only.", None),   # a price, not an audience
    ("MIT ID required for entry.", False),
])
def test_audience_statements(text, public):
    item = {"title": "Talk", "description_text": text, "filters": {"event_audience": []}}
    tagged = {"title": "Talk", "description_text": text,
              "filters": {"event_audience": [{"name": "Public"}]}}
    if public is None:
        assert MITCalendarScraper.is_public(tagged) is True
        assert MITCalendarScraper.is_public(item) is False
    else:
        assert MITCalendarScraper.is_public(item) is public
        if public is False:
            assert MITCalendarScraper.is_public(tagged) is False


def test_times_are_eastern_wall_clock_and_all_day_is_honored(api):
    """`start` carries an offset ("2026-10-06T18:30:00-04:00"); the model
    stores naive Eastern. An all-day instance is flagged all-day, not given a
    time."""
    events = MITCalendarScraper().scrape_events()
    assert all(e.start_datetime.tzinfo is None for e in events)

    eakin = next(e for e in events if "Emily Eakin" in e.title)
    assert eakin.start_datetime == datetime(2026, 10, 6, 18, 30)
    assert eakin.venue_name == "Boston French Library" and eakin.city == "Boston"

    whitney = [e for e in events if e.title.startswith("What Are People For")]
    assert whitney and all(e.all_day and e.start_datetime == datetime(2026, 10, 24) for e in whitney)

    assert not [e for e in events if e.all_day and e.title != whitney[0].title]


def test_reposted_events_are_one_event(api):
    """Departments re-post: "symplectic-geometry-seminar" and
    "copy-of-copy-of-copy-of-symplectic-geometry-seminar" are the same talk,
    same room, same time, under two URLs."""
    events = MITCalendarScraper().scrape_events()
    seminar = [e for e in events if e.title == "Symplectic Geometry Seminar"
               and e.start_datetime == datetime(2026, 10, 8, 16, 30)]
    assert len(seminar) == 1


def test_holidays_and_virtual_events(api):
    """Institute Holidays are closures, not events. A virtual event is
    "Online", not placed on campus."""
    events = MITCalendarScraper().scrape_events()
    assert not [e for e in events if e.title.strip() in ("Veterans Day", "Thanksgiving Day")]
    online = [e for e in events if e.venue_name == "Online"]
    assert online and all(e.street_address is None and e.latitude is None for e in online)


def test_a_start_without_an_offset_is_refused():
    """Localist always sends an offset and whole minutes. Anything else means
    the field changed meaning, and a guess would move events by hours."""
    read = MITCalendarScraper._instant
    assert read("2026-10-06T18:30:00-04:00") is not None
    assert read("2026-10-06T18:30:00") is None
    assert read("2026-10-06T18:30:17-04:00") is None
    assert read("") is None and read(None) is None
