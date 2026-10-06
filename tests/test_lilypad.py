"""The Lily Pad — read from Squarespace's `?format=json`, not a browser.

The fixture is that payload as served on 2026-10-06, reduced to the fields the
scraper reads (site chrome dropped, each body cut to its text blocks).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
import requests

from src.quality.invariants import check_invariants
from src.scrapers.lilypad import LilyPadScraper, epoch_ms_to_eastern
from tests.conftest import read_fixture


@pytest.fixture
def payload():
    return json.loads(read_fixture("lilypad", "events-2026-10-06.json.gz"))


@pytest.fixture
def events(payload, monkeypatch, offline):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, params))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: payload})()

    monkeypatch.setattr(requests, "get", fake_get)
    out = LilyPadScraper().scrape_events()
    assert calls == [("https://www.lilypadinman.com/", {"format": "json"})], "one request, no detail pages"
    return out


def test_every_upcoming_show_is_read(payload, events):
    """The Selenium scraper opened each detail page with a 2 s sleep, so it was
    capped at 30 of the ~120 shows listed, and its upcoming filter tested
    `eventlist--upcoming`, a class that does not exist (the real one is
    `eventlist-event--upcoming`). Every upcoming item but the private booking
    must come through, and nothing from `past`."""
    upcoming = payload["upcoming"]
    assert len(upcoming) == 115
    assert len(events) == 114
    assert not [e for e in events if "Private" in e.title]
    past_urls = {p["fullUrl"] for p in payload["past"]}
    assert not [e for e in events if e.source_url.replace("https://www.lilypadinman.com", "") in past_urls]
    assert max(e.start_datetime for e in events) >= datetime(2027, 1, 1)


def test_output_satisfies_invariants(events):
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"The Lily Pad"}


def test_epoch_milliseconds_become_eastern_wall_clock_on_the_minute(events):
    """startDate is UTC epoch milliseconds with noise in the milliseconds
    (1791329400563). Read naively it is either 23:30 or carries .563 seconds,
    which EventValidator rejects as a clock reading."""
    assert epoch_ms_to_eastern(1791329400563) == datetime(2026, 10, 6, 19, 30)    # EDT
    assert epoch_ms_to_eastern(1797724800123) == datetime(2026, 12, 19, 19, 0)    # EST
    assert epoch_ms_to_eastern(None) is None
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]

    by_title = {e.title: e for e in events}
    montvales = by_title["The Montvales"]
    assert montvales.start_datetime == datetime(2026, 10, 6, 19, 30), "page says Tue Oct 6, 7:30 PM"
    assert montvales.end_datetime == datetime(2026, 10, 6, 21, 30)
    late = by_title["The AJ Foss Band / The Glory Dogs / Madmax and the Perpetrators"]
    assert late.start_datetime == datetime(2026, 10, 8, 22, 0), "02:00 UTC is 10 PM the evening before"


def test_descriptions_are_the_post_body_without_editor_placeholders(events):
    """Bodies carry Squarespace's "Double-click to edit..." placeholder; titles
    carry trailing padding and HTML entities."""
    assert not [e for e in events if "Double-click" in e.description or "&amp;" in e.description]
    assert all(e.title == e.title.strip() for e in events)
    gill = next(e for e in events if e.title == "Gill Aharon Trio")
    assert "cartoon music" in gill.description
    assert gill.cost == "$10", "the '/ 8:15 start' after the price is not part of it"
