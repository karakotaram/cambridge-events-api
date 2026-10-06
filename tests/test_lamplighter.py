"""Lamplighter Brewing — the Shopify "events" collection.

`events-2026-10-06.html.gz` is the live collection page (20 events).
"""
from __future__ import annotations

from datetime import datetime

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def _scrape(html: str):
    scraper = BY_NAME["Lamplighter Brewing"].load()
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    scraper.fetch_event_details = lambda url: ("", None)   # detail pages are a second fetch
    return scraper.scrape_events()


def _card(slug: str, text: str) -> str:
    return f'<a href="/products/{slug}">{text}</a>'


def test_the_collection_page(offline):
    """Every product on the page, with ranged and unranged times read."""
    events = _scrape(read_fixture("lamplighter", "events-2026-10-06.html.gz"))
    when = {(e.title, e.start_datetime) for e in events}

    assert len(events) == 20
    assert ("Survivor Watch Party", datetime(2026, 10, 7, 20, 0)) in when
    assert ("Brewery Yoga", datetime(2026, 10, 25, 12, 30)) in when          # "12:30pm - 1:30pm"
    assert ("Brewery Tour & Tasting", datetime(2026, 12, 5, 16, 0)) in when  # "4pm - 5pm"
    assert not any(e.all_day for e in events)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_more_than_thirty_events_are_all_kept(offline):
    """The scraper sliced the product links to [:30]. The collection peaked at
    28, so the 31st event would have vanished without a trace."""
    cards = "".join(_card(f"trivia-{d}", f"Broadway Trivia October {d:02d}, 2026 7 pm - 9 pm 284 Broadway")
                    for d in range(1, 32))
    events = _scrape(f"<html><body>{cards}</body></html>")

    assert len(events) == 31
    assert max(e.start_datetime for e in events) == datetime(2026, 10, 31, 19, 0)


def test_a_missing_time_is_never_midnight(offline):
    """A card without a readable time used to start at 00:00. A date-only
    card is an all-day event; a card with a time we cannot read is skipped."""
    cards = (
        _card("one-time", "Release Party October 10, 2026 7 pm Lamplighter CX")
        + _card("date-only", "Fall Beer Garden October 17, 2026 284 Broadway")
        + _card("unreadable", "Late Show October 18, 2026 10:00 Lamplighter CX")
    )
    events = {e.title: e for e in _scrape(f"<html><body>{cards}</body></html>")}

    assert events["Release Party"].start_datetime == datetime(2026, 10, 10, 19, 0)
    assert not events["Release Party"].all_day
    assert events["Fall Beer Garden"].all_day
    assert events["Fall Beer Garden"].start_datetime == datetime(2026, 10, 17, 0, 0)
    assert "Late Show" not in events
