"""Central Square Theater — EventON's month endpoint, read without a browser.

Fixtures, saved 2026-10-06: the calendar page (EventON's nonces and settings,
and the nav links to each show page), the endpoint's responses for October,
November and December 2026 (December has no events), and two show pages cut
down to their og: metas, title and main content.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.central_square import CentralSquareTheaterScraper, show_key
from tests.conftest import read_fixture

SITE = "https://www.centralsquaretheater.org"


@pytest.fixture
def run(offline):
    months = [json.loads(read_fixture("central_square", f"month-2026-{m}.json.gz")) for m in ("10", "11", "12")]
    pages = {
        f"{SITE}/calendar/": read_fixture("central_square", "calendar-2026-10-06.html.gz"),
        f"{SITE}/shows/eleanor/": read_fixture("central_square", "show-eleanor.html.gz"),
        f"{SITE}/shows/the-getaway-driver/": read_fixture("central_square", "show-the-getaway-driver.html.gz"),
    }
    scraper = CentralSquareTheaterScraper()
    calls = []

    def fetch_page(url):
        if url not in pages:
            raise OSError(f"404 {url}")              # other show pages: not in the fixture
        return pages[url]

    def fetch_month(params, sc, direction):
        calls.append((direction, sc.get("fixed_month"), sc.get("fixed_year"), params.get("n")))
        return months[min(len(calls) - 1, 2)]

    scraper.fetch_page = fetch_page                   # type: ignore[method-assign]
    scraper.fetch_month = fetch_month                 # type: ignore[method-assign]
    return scraper.scrape_events(), calls


def test_every_month_is_requested_without_clicking(run):
    """The "next month" click failed on every run because a Mailmunch popup
    iframe intercepted it, so two of four months were read. Months now come
    from EventON's endpoint, each request carrying the settings the previous
    response returned - exactly what the page's arrow sends."""
    events, calls = run
    assert calls[0] == ("none", "10", "2026", "1fd33b3650")
    assert [c[0] for c in calls[1:]] == ["next"] * 11
    assert calls[1][1:3] == ("10", "2026") and calls[2][1:3] == ("11", "2026"), \
        "each request must send the settings the last response returned"
    assert {e.start_datetime.month for e in events} == {10, 11}
    assert len(events) == 17 + 21


def test_performances_get_the_shows_synopsis_not_their_title(run):
    """EventON's schema description is the title in quotes ("'Eleanor'"), which
    EventValidator rejects as too short: all 12 October performances of
    Eleanor were dropped, and Arcadia and Working would follow."""
    events, _ = run
    eleanor = [e for e in events if e.title == "Eleanor"]
    assert len(eleanor) == 12
    assert all(e.description.startswith("In an intimate portrait of Eleanor Roosevelt") for e in eleanor)
    captioned = next(e for e in events if e.title == "The Getaway Driver (captioned)")
    assert captioned.description.startswith("Captioned performance. Based on the true story")
    assert not [e for e in events if len(e.description) < 20 or e.description.strip("'\" ") == e.title]


def test_descriptions_are_text_not_markup(run):
    """Talk descriptions arrive as raw `<p><span style="font-weight: 400;">`,
    which was published as-is."""
    events, _ = run
    assert not [e for e in events if "<" in e.description or "&#" in e.description or "&amp;" in e.description]
    talk = next(e for e in events if e.title == "The Hidden Lives of Caregivers")
    assert talk.description.startswith("Nearly every single person will play the role of caregiver")


def test_talks_link_somewhere_real(run):
    """Talks have href="#" in the listing, and three were published with
    source_url "#". A talk tied to a show links to the show; one that is not
    links to its own page on the theater's site."""
    events, _ = run
    assert not [e for e in events if not e.source_url.startswith("http")]
    social = next(e for e in events if e.title == "Scholar Social for The Getaway Driver")
    assert social.source_url == f"{SITE}/shows/the-getaway-driver/"
    talk = next(e for e in events if e.title == "The Hidden Lives of Caregivers")
    assert talk.source_url == f"{SITE}/event-on/the-hidden-lives-of-caregivers/"
    show = next(e for e in events if e.title == "Eleanor")
    assert show.source_url.startswith("https://ci.ovationtix.com/"), "performances keep their ticket links"


def test_times_are_eastern_and_agree_with_the_listing(run):
    """data-time is Unix seconds; the schema repeats the wall clock. Both are
    read, and they must agree."""
    events, _ = run
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Eleanor", datetime(2026, 10, 6, 19, 30)) in starts
    assert ("Eleanor", datetime(2026, 10, 10, 14, 0)) in starts
    assert ("Scholar Social for The Getaway Driver", datetime(2026, 11, 5, 21, 0)) in starts, \
        "after the clocks change: 02:00 UTC is 9 PM EST"

    scraper = CentralSquareTheaterScraper()
    assert scraper.schema_wall_clock("2027-2-4T19:30-4:00") == datetime(2027, 2, 4, 19, 30)


def test_output_satisfies_invariants(run):
    events, _ = run
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"Central Square Theater"}


def test_show_key_groups_variants_with_their_show():
    assert show_key("The Getaway Driver (captioned)") == show_key("The Getaway Driver")
    assert show_key("Scholar Social for Arcadia") == show_key("Arcadia")
    assert show_key("Artists &#038; Audiences for Working") == show_key("Working")
