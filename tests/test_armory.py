"""Arts at the Armory — read from the Events Manager iCal feed.

The fixture is /events.ics as served on 2026-10-06. It lists exactly the 81
events that the "All" tab of /upcoming-events/ shows across seven ?pno= pages,
at the same times.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.armory import ArtsAtTheArmoryScraper, ical_datetime
from tests.conftest import read_fixture


def _response(status: int, text: str):
    return type("_R", (), {"status_code": status, "text": text})()


@pytest.fixture
def feed():
    return read_fixture("armory", "events-2026-10-06.ics.gz")


@pytest.fixture
def events(feed, monkeypatch, offline):
    monkeypatch.setattr(requests, "get", lambda url, **k: _response(200, feed))
    return ArtsAtTheArmoryScraper().scrape_events()


def test_every_event_is_read_not_one_per_category(events):
    """The old scraper looped over the category tabs (ev-all, ev-music, ...)
    and took the first heading in each, then capped at 20: about one event per
    category, 12 in all. The venue lists 81, through Sep 2027."""
    assert len(events) == 81
    assert max(e.start_datetime for e in events) == datetime(2027, 9, 28, 18, 30)
    assert Counter(e.title for e in events)["The Moth: Boston StorySLAM"] >= 9


def test_output_satisfies_invariants(events):
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"Arts at the Armory"}


def test_times_match_the_listing(events):
    """The listing shows "Fri. Oct. 09, 2026 | 8:00 pm" for Nebula Night and
    "Tue. Oct. 06, 2026 | 7:30 pm - 9:30 pm" for Smut Slam."""
    by_title = {}
    for e in sorted(events, key=lambda e: e.start_datetime):
        by_title.setdefault(e.title, e)
    nebula = by_title["The Nova Comedy Collective Presents: Nebula Night"]
    assert nebula.start_datetime == datetime(2026, 10, 9, 20, 0)
    assert nebula.end_datetime is None, "DTEND equal to DTSTART means no end was given"
    smut = by_title["Smut Slam"]
    assert (smut.start_datetime, smut.end_datetime) == (datetime(2026, 10, 6, 19, 30),
                                                        datetime(2026, 10, 6, 21, 30))


def test_descriptions_are_prose_not_the_ticket_buttons_script(events):
    """41 of 81 descriptions embed `$('#getTixButton').click(function() {
    fbq('track', 'Purchase', ...) });` as text."""
    assert not [e for e in events if "fbq" in e.description or "$(" in e.description]
    assert not [e for e in events if "&amp;" in e.description or "\\," in e.description]
    clap = next(e for e in events if e.title.startswith("Arts at the Armory Spotlight Series"))
    assert clap.title.endswith("Clap Your Hands Say Yeah - Piano & Voice"), "the feed's title is not truncated"
    assert "Alec Ounsworth" in clap.description


def test_categories_come_from_the_feed(events):
    moth = next(e for e in events if e.title == "The Moth: Boston StorySLAM")
    assert moth.category == EventCategory.ARTS_CULTURE.value
    assert "Literary Art" in moth.tags


def test_a_blocked_request_falls_back_to_the_browser(feed, monkeypatch, offline):
    """The host blocked GitHub's IP ranges for plain requests in Dec 2025. A
    refusal must route through the browser, not end the source."""
    monkeypatch.setattr(requests, "get", lambda url, **k: _response(403, "<html>Forbidden</html>"))
    scraper = ArtsAtTheArmoryScraper()
    used = []
    scraper.fetch_calendar_in_browser = lambda: used.append(True) or feed   # type: ignore[method-assign]
    assert len(scraper.scrape_events()) == 81
    assert used


def test_ical_datetime_forms():
    assert ical_datetime("TZID=America/New_York", "20261006T193000") == (datetime(2026, 10, 6, 19, 30), False)
    assert ical_datetime("", "20261007T001500Z") == (datetime(2026, 10, 6, 20, 15), False)
    assert ical_datetime("VALUE=DATE", "20261010") == (datetime(2026, 10, 10), True)
    assert ical_datetime("TZID=America/New_York", "not a date") == (None, False)
