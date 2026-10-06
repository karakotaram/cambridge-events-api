"""Grolier Poetry Book Shop — a Squarespace page of reading blocks.

Two saved pages: `upcoming-events.html.gz` (captured 2026-09-01, 18 readings)
and `upcoming-events-2026-10-06.html.gz` (11 readings).
"""
from __future__ import annotations

from datetime import datetime

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def _scrape(html: str):
    scraper = BY_NAME["Grolier Poetry Book Shop"].load()
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    return scraper.scrape_events()


def _errors(events):
    return [v for v in check_invariants([e.model_dump(mode="json") for e in events])
            if v.severity == "error"]


def test_a_saved_page_parses_the_same_on_any_day(offline):
    """The scraper dropped readings earlier than datetime.now(), so the
    September fixture lost one a week: 18 readings on capture, 8 by
    2026-10-06, and test_grolier_reads_the_renamed_page failed. Staleness is
    EventValidator's job; the scraper reports every reading on the page."""
    events = _scrape(read_fixture("grolier", "upcoming-events.html.gz"))

    assert len(events) == 18
    assert min(e.start_datetime for e in events) == datetime(2026, 9, 2, 19, 0)
    assert max(e.start_datetime for e in events) == datetime(2026, 12, 10, 19, 0)
    assert not _errors(events)


def test_readings_that_name_the_shop_are_kept(offline):
    """A substring skip on "grolier" (meant for the page's own heading) also
    dropped any reading whose title names the shop — its press's book
    launches, and every series it co-presents. The live page listed 11 on
    2026-10-06 and the scraper kept 8."""
    events = _scrape(read_fixture("grolier", "upcoming-events-2026-10-06.html.gz"))
    titles = [e.title for e in events]

    assert len(events) == 11
    assert any(t.startswith("Pawn Shop of Coincidence") and "(Grolier Poetry Press)" in t for t in titles)
    assert any(t.startswith("Cardboard House Press and the Grolier Poetry Book Shop present") for t in titles)
    assert any(t.startswith("Arrowsmith Press, The Harvard Ukrainian Research Institute, and Grolier") for t in titles)

    pawn_shop = next(e for e in events if e.title.startswith("Pawn Shop"))
    assert pawn_shop.start_datetime == datetime(2026, 10, 22, 19, 0)
    assert not pawn_shop.all_day
    assert not _errors(events)


def test_page_chrome_is_skipped_and_an_untimed_reading_is_all_day(offline):
    """Navigation headings are matched whole. A reading with a date and no
    time is published as all-day rather than at an invented hour."""
    html = """
    <div class="sqs-html-content"><h2>Upcoming Events</h2>
      <p>October 1, 2026</p><p>Readings every week</p></div>
    <div class="sqs-html-content"><h2>Grolier Poetry Book Shop</h2>
      <p>October 1, 2026</p><p>6 Plympton St</p></div>
    <div class="sqs-html-content"><h2>A Reading With No Time Yet</h2>
      <p>November 20, 2026</p><p>Details to come</p></div>
    """
    events = _scrape(html)

    assert [e.title for e in events] == ["A Reading With No Time Yet"]
    assert events[0].all_day
    assert events[0].start_datetime == datetime(2026, 11, 20, 0, 0)
