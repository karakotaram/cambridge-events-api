"""Regent Theatre: EventON's two machine-readable times, and which zone they are in.

The fixture is the rendered /schedule/list/ page, captured 2026-10-06 — 34
listings, 15 of them in standard time (Nov 1 to Mar 14).
"""
from __future__ import annotations

import time
from datetime import datetime
from functools import lru_cache

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.regent_theatre import RegentTheatreScraper
from tests.conftest import read_fixture


@lru_cache(maxsize=1)
def _nodes():
    soup = BeautifulSoup(read_fixture("regent_theatre", "schedule-list-2026-10-06.html.gz"), "html.parser")
    return soup.find_all(class_="eventon_list_event")


def _parse_all():
    """Times are computed here, per call, so each machine zone is exercised."""
    scraper = RegentTheatreScraper()
    nodes = _nodes()
    return nodes, [e for e in (scraper._parse_event(n) for n in nodes) if e]


@pytest.fixture
def machine_zone(monkeypatch):
    """Run under a given process time zone, as CI (UTC) does."""
    def use(zone: str):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Tokyo"])
def test_times_do_not_depend_on_the_machine_zone(zone, machine_zone, offline):
    """`datetime.fromtimestamp()` without a zone reads Unix time in the
    machine's zone, which is UTC in CI. The same page must give the same
    Eastern times wherever it is parsed."""
    machine_zone(zone)
    _, events = _parse_all()
    by_title = {(e.title, e.start_datetime.date()): e.start_datetime for e in events}
    assert by_title[("Scott Damgaard's 16th Annual JOHN LENNON Night", datetime(2026, 10, 9).date())] == datetime(2026, 10, 9, 20, 0)
    assert by_title[("Monster Ft. Gurleen Pannu Standup Comedy", datetime(2026, 11, 1).date())] == datetime(2026, 11, 1, 19, 0)


def test_every_start_matches_the_time_on_the_card(offline):
    """EventON's microdata writes a -4:00 offset year-round. Reading it put all
    15 events in standard time an hour early ("7:00 pm (GMT-05:00)" published
    as 6:00 pm). The time printed on each card is the arbiter."""
    nodes, events = _parse_all()
    assert len(events) == 34

    card_times = {}
    for node in nodes:
        title = node.find(class_="evcal_event_title")
        shown = node.select_one(".evcal_cblock em.time")
        if title and shown:
            card_times.setdefault(title.get_text(strip=True), set()).add(shown.get_text(strip=True))

    for e in events:
        assert e.start_datetime.strftime("%-I:%M %p").lower() in card_times[e.title], e.title

    standard_time = [e for e in events
                     if datetime(2026, 11, 1, 2, 0) <= e.start_datetime < datetime(2027, 3, 14, 2, 0)]
    assert len(standard_time) == 15, "the fixture should exercise the offset bug"


def test_eventon_placeholder_end_is_not_published(offline):
    """EventON stores 23:59 when no end time was set; the venue never said that."""
    _, events = _parse_all()
    assert not [e for e in events if e.end_datetime and e.end_datetime.strftime("%H:%M") == "23:59"]
    ends = sorted((e.start_datetime, e.end_datetime) for e in events if e.end_datetime)
    assert ends == [(datetime(2026, 10, 25, 16, 30), datetime(2026, 10, 25, 18, 30)),
                    (datetime(2026, 10, 25, 19, 0), datetime(2026, 10, 25, 21, 0))]


def test_disagreeing_renderings_are_skipped():
    """If the epoch and the microdata wall clock ever disagree, one of them
    changed meaning; the event is dropped rather than resolved by guessing."""
    scraper = RegentTheatreScraper()
    node = BeautifulSoup(
        '<div class="eventon_list_event" data-time="1793577600-1793595540">'
        '<span class="evcal_event_title">A Show</span>'
        '<meta itemprop="startDate" content="2026-11-1T18:00-5:00"/></div>',
        "html.parser").div
    assert scraper._parse_event(node) is None

    node["data-time"] = "1793574000-1793595540"      # 18:00 EST
    assert scraper._parse_event(node).start_datetime == datetime(2026, 11, 1, 18, 0)


def test_output_satisfies_invariants(offline):
    _, events = _parse_all()
    assert {e.source_name for e in events} == {"Regent Theatre"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_a_failed_page_load_fails_the_source():
    """A load that timed out used to return [] and be recorded as "ok, 0 events"."""
    class _Page:
        def goto(self, *a, **k):
            raise TimeoutError("Timeout 60000ms exceeded")

    scraper = RegentTheatreScraper()
    scraper._page = _Page()
    with pytest.raises(TimeoutError):
        scraper.scrape_events()
