"""Portico Brewing — read from Squarespace's `?format=json`, not a browser.

The fixture is that payload as served on 2026-10-06, reduced to the fields the
scraper reads (site chrome dropped, `past` cut to three items, each body cut to
its text blocks).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
import requests

from src.quality.invariants import check_invariants
from src.scrapers.portico import PorticoScraper
from tests.conftest import read_fixture


@pytest.fixture
def payload():
    return json.loads(read_fixture("portico", "events-2026-10-06.json.gz"))


@pytest.fixture
def events(payload, monkeypatch, offline):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, params))
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: payload})()

    monkeypatch.setattr(requests, "get", fake_get)
    out = PorticoScraper().scrape_events()
    assert calls == [("https://porticobrewing.com/upcoming-events", {"format": "json"})]
    return out


def test_reads_upcoming_and_only_upcoming(payload, events):
    """The Selenium scraper took the first 30 `.eventlist-event` articles with
    no upcoming filter. The page lists 28 upcoming then 30 past, so the cap
    was all that kept most of the past out, and two past events were read
    every run. Every upcoming item must come through, and nothing from `past`."""
    assert len(events) == len(payload["upcoming"]) == 28
    past_urls = {f"https://porticobrewing.com{p['fullUrl']}" for p in payload["past"]}
    assert past_urls and not past_urls & {e.source_url for e in events}


def test_output_satisfies_invariants(events):
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"Portico Brewing"}
    assert all(e.city == "Somerville" for e in events)


def test_times_are_eastern_wall_clock(events):
    """Epoch milliseconds in UTC with noise in the millis; the rendered page
    says Fri Oct 9, 6:00-8:00 PM and Thu Dec 31, 6:30-9:00 PM."""
    by_start = {(e.title, e.start_datetime): e for e in events}
    bikes = by_start[("Bikes, Books & Beats", datetime(2026, 10, 9, 18, 0))]
    assert bikes.end_datetime == datetime(2026, 10, 9, 20, 0)
    assert ("Portico Point to Pint Runners", datetime(2026, 12, 31, 18, 30)) in by_start
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]


def test_trivia_prizes_are_not_admission(events):
    trivia = [e for e in events if "Trivia" in e.title]
    assert trivia and all(e.cost is None for e in trivia)
    assert not [e for e in events if "&amp;" in e.title]
