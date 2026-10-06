"""Mount Auburn Cemetery — the site's Tribe Events REST API.

Fixture: events-api-page1.json.gz is `/wp-json/tribe/events/v1/events
?per_page=50&start_date=now&status=publish&page=1`, captured 2026-10-06.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture
def payload():
    return json.loads(read_fixture("mount_auburn", "events-api-page1.json.gz"))


@pytest.fixture
def events(payload, monkeypatch, offline):
    import requests

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        body = payload if calls["n"] == 1 else {"events": [], "total_pages": 1}
        return type("_R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    result = BY_NAME["Mount Auburn Cemetery"].load().scrape_events()
    assert calls["n"] == 1, "total_pages is 1, so one call is enough"
    return result


def test_mount_auburn_reads_the_whole_programme(events):
    """The list page's JSON-LD carries only its first ten events, eleven days
    ahead. The API returns 39, reaching to October 2027."""
    assert len(events) == 39
    assert max(e.start_datetime for e in events) >= datetime(2027, 10, 1)
    assert {e.source_name for e in events} == {"Mount Auburn Cemetery"}

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_mount_auburn_titles_are_unescaped(events):
    """"Outdoor Watercolor &#038; Sketch Class" was published with the entity."""
    titles = {e.title for e in events}
    assert "Outdoor Watercolor & Sketch Class" in titles
    assert not [t for t in titles if "&#" in t or "&amp;" in t]
    assert not [e for e in events if "&#" in e.description or "<p" in e.description]


def test_mount_auburn_times_are_eastern_wall_clock(events):
    """Read from the UTC field and converted, so DST is handled by the
    conversion rather than by trusting a naive local string."""
    by_title = {(e.title, e.start_datetime.date()): e for e in events}
    death_cafe = by_title[("Death Café", datetime(2027, 3, 19).date())]
    assert death_cafe.start_datetime == datetime(2027, 3, 19, 18, 0)   # EDT
    tour = by_title[("Discover Mount Auburn Walking Tour", datetime(2026, 12, 5).date())]
    assert tour.start_datetime == datetime(2026, 12, 5, 13, 0)        # EST
    assert all(e.start_datetime.tzinfo is None for e in events)


def test_mount_auburn_tolerates_tribe_field_shapes():
    """Tribe returns `venue` and `image` as a dict, an empty list, or a list of
    dicts; this payload has both dict and [] venues."""
    scraper = BY_NAME["Mount Auburn Cemetery"].load()
    assert scraper._as_dict({"venue": "X"}) == {"venue": "X"}
    assert scraper._as_dict([]) == {}
    assert scraper._as_dict([{"venue": "Y"}]) == {"venue": "Y"}
    assert scraper._as_dict(None) == {}


def test_mount_auburn_virtual_event_is_not_placed_at_the_cemetery(events):
    """A "[Virtual]" panel has no venue in Tribe; it is online, not at 580
    Mount Auburn Street."""
    virtual = [e for e in events if e.title.startswith("[Virtual]")]
    assert virtual and all(e.venue_name == "Online" and e.street_address is None for e in virtual)
