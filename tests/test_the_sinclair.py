"""The Sinclair — a listing page plus a "Load More" endpoint.

Fixture: events-and-load-more-2026-10-06.json.gz maps each URL the scraper
fetched on 2026-10-06 to its body: /events/ (HTML) and four
/events/events_ajax/{20,40,60,80} responses (HTML encoded as a JSON string).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from src.utils.validator import EventValidator
from tests.conftest import read_fixture


@pytest.fixture
def bodies():
    return json.loads(read_fixture("the_sinclair", "events-and-load-more-2026-10-06.json.gz"))


@pytest.fixture
def scraped(bodies, offline):
    fetched = []
    scraper = BY_NAME["The Sinclair"].load()

    def fetch(url, *a, **k):
        fetched.append(url)
        return bodies[url]

    scraper.fetch_html = fetch                      # type: ignore[method-assign]
    return scraper.scrape_events(), fetched


def test_sinclair_follows_load_more_to_the_end(scraped):
    """Only the 20 entries rendered on /events/ were read, ending 10/22. The
    rest arrive through "Load More", 20 per call, until a short page."""
    events, fetched = scraped

    assert len(fetched) == 5
    assert [u.split("events_ajax/")[1].split("?")[0] for u in fetched[1:]] == ["20", "40", "60", "80"]
    assert len(events) == 67
    assert max(e.start_datetime for e in events) >= datetime(2027, 3, 1)
    assert len({(e.source_url, e.start_datetime) for e in events}) == len(events)


def test_sinclair_decodes_the_json_wrapped_html():
    """The endpoint's body is a JSON string literal, escaped quotes and all;
    parsing it as HTML directly finds no entries."""
    scraper = BY_NAME["The Sinclair"].load()
    assert scraper.decode_ajax('"<div class=\\"entry sinclair\\"><\\/div>"') == '<div class="entry sinclair"></div>'
    assert scraper.decode_ajax('""') == ""


def test_sinclair_bare_headliner_still_passes_the_validator(scraped):
    """"Tricky" has no support act or tour name, so its description was just
    "Tricky" and the validator rejected it as too short."""
    events, _ = scraped
    tricky = next(e for e in events if e.title == "Tricky")

    assert tricky.description == "Tricky at The Sinclair, Cambridge"
    assert tricky.start_datetime == datetime(2026, 10, 12, 19, 0)
    # Only the content rules: the validator's staleness rule reads the clock,
    # and this fixture will age past it.
    rejected = [(e.title, why) for e in events for ok, why in [EventValidator.validate_event(e)]
                if not ok and "past" not in why]
    assert not rejected


def test_sinclair_output_satisfies_invariants(scraped):
    events, _ = scraped
    assert {e.source_name for e in events} == {"The Sinclair"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


@pytest.mark.parametrize("date_text,time_text,expected", [
    ("Sat, Oct 31, 2026", "Doors 9:00 PM", (datetime(2026, 10, 31, 21, 0), False)),
    ("Sat, Nov 7, 2026", "Doors 12:00 PM", (datetime(2026, 11, 7, 12, 0), False)),
    ("Sat, Nov 7, 2026", "", (datetime(2026, 11, 7), True)),
    ("Sat, Nov 7, 2026", "Doors TBA", (None, False)),
    ("Nov 7", "Doors 7:00 PM", (None, False)),
])
def test_sinclair_never_places_a_show_at_midnight(date_text, time_text, expected):
    """Without a readable time the old parser returned the bare date — a
    midnight start. Now: no time text is all-day; unreadable text is skipped.
    A date without a year is never completed from the clock."""
    assert BY_NAME["The Sinclair"].load()._parse_date(date_text, time_text) == expected
