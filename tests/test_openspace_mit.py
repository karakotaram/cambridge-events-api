"""MIT Open Space — the Squarespace calendar's own JSON (`/calendar?format=json`).

The fixture is the live payload of 2026-10-06, trimmed to the fields the
scraper reads; each `body` is cut to its first 600 characters of text. It
holds 13 upcoming and 30 past events.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.openspace_mit import OpenSpaceMITScraper
from tests.conftest import read_fixture


def _payload() -> dict:
    return json.loads(read_fixture("openspace_mit", "calendar-format-json-2026-10-06.json.gz"))


@pytest.fixture
def api(monkeypatch, offline):
    import requests

    payload = _payload()
    requested = []

    def fake_get(url, params=None, **kwargs):
        requested.append((url, params))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: payload})()

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


def test_every_upcoming_event_at_its_listed_time(api):
    """The HTML scraper fell back to datetime.now() at 18:00 when no date
    parsed and invented 18:00 for a date without a time. startDate is epoch
    milliseconds in UTC with creation-time noise (15:30:00.167Z); it is floored
    to the minute and converted to Eastern, across the DST change."""
    events = OpenSpaceMITScraper().scrape_events()

    assert api == [("https://www.openspace.mit.edu/calendar", {"format": "json"})]
    assert len(events) == 13
    by_title = {}
    for e in events:
        by_title.setdefault(e.title, []).append(e)

    trucks = by_title["Food Truck Wednesdays"]
    assert [e.start_datetime for e in trucks][:2] == [datetime(2026, 10, 7, 11, 30), datetime(2026, 10, 14, 11, 30)]
    assert trucks[0].end_datetime == datetime(2026, 10, 7, 14, 0)
    # 23:00Z on Nov 6 is 6 PM EST (DST ended Nov 1), not 7 PM
    assert by_title["Reel Rock"][0].start_datetime == datetime(2026, 11, 6, 18, 0)

    assert not [e for e in events if e.start_datetime.second or e.start_datetime.tzinfo]
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_fields_are_plain_text_and_venue_comes_from_the_listing(api):
    """Titles are entity-encoded ("Music &amp; Soup") and excerpts are HTML.
    Events at the Welcome Center are not placed at the Open Space."""
    events = OpenSpaceMITScraper().scrape_events()
    soup = next(e for e in events if e.title.startswith("Midday Music & Soup"))
    assert soup.venue_name == "MIT Welcome Center"
    assert soup.street_address == "292 Main Street" and soup.zip_code == "02142"
    assert soup.source_url == "https://www.openspace.mit.edu/calendar/midday-music-soup-joseph-borsellino-november-2026"
    for e in events:
        assert "&amp;" not in e.title and "<p" not in e.description and "&nbsp;" not in e.description


def test_rescheduled_notices_are_skipped_but_rescheduled_events_kept(offline):
    """The venue renames the original listing "RESCHEDULED: <title>" on the
    old date and posts the new date as a separate listing. The old skip of
    any title *containing* "rescheduled" was right about the notice but would
    also drop a real event that mentions the word. Only a leading marker is a
    notice."""
    past = _payload()["past"]
    wiz = [i for i in past if "The Wiz" in i["title"]]
    assert {i["title"] for i in wiz} == {"Outdoor Movie: The Wiz", "RESCHEDULED: Outdoor Movie: The Wiz"}
    notice = next(i for i in wiz if i["title"].startswith("RESCHEDULED"))
    assert "rescheduled to Thursday, September 3" in notice["body"]

    scraper = OpenSpaceMITScraper()
    events = scraper.parse_collection({"upcoming": wiz})
    assert [(e.title, e.start_datetime) for e in events] == [("Outdoor Movie: The Wiz", datetime(2026, 9, 3, 18, 30))]

    mention = dict(wiz[0], title="Midday Music: the rescheduled July concert")
    assert scraper.parse_collection({"upcoming": [mention]}), "a title that mentions the word is an event"
    for marker in ("RESCHEDULED – Midday Music", "Cancelled: Fall Party", "POSTPONED - Reel Rock"):
        assert scraper.parse_collection({"upcoming": [dict(wiz[0], title=marker)]}) == []


def test_unreadable_start_is_skipped_not_now():
    scraper = OpenSpaceMITScraper()
    item = {"title": "Something", "fullUrl": "/calendar/something", "excerpt": "<p>An event that has no date.</p>"}
    assert scraper.parse_item(dict(item)) is None
    assert scraper.parse_item(dict(item, startDate="soon")) is None
    assert scraper.parse_item(dict(item, startDate=None)) is None
    assert OpenSpaceMITScraper.read_instant(1791387000167) == datetime(2026, 10, 7, 11, 30)
