"""BostonShows.org — an aggregator whose rows name the venue two ways.

The fixture is the live homepage of 2026-10-06 trimmed to the first three days
plus every day holding a Cambridge/Somerville row whose venue is plain text.
"""
from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.scrapers.bostonshows import BostonShowsScraper
from tests.conftest import read_fixture


def _events():
    scraper = BostonShowsScraper()
    soup = BeautifulSoup(read_fixture("bostonshows", "listing-2026-10-06.html.gz"), "html.parser")
    return scraper, soup, scraper.parse_listing(soup)


def test_plain_text_venues_are_read_not_unknown(offline):
    """Venues without a page on bostonshows.org (Davis Square Plaza, the library
    branches) are plain text, not a link. Reading the second `<a>` published 17
    of them as "Unknown Venue"; the row's `data-venue` attribute names them all."""
    _, soup, events = _events()

    plain = [r for r in soup.select("tr.event")
             if r.get("data-city") in ("Cambridge", "Somerville")
             and not r.select_one("div.event-venue a")]
    assert len(plain) >= 15, "fixture should contain plain-text venues"

    assert not [e for e in events if not e.venue_name or e.venue_name == "Unknown Venue"]
    venues = {e.venue_name for e in events}
    assert "Davis Square Plaza" in venues
    assert "Cambridge Public Library — O'Connell" in venues
    plaza = next(e for e in events if e.venue_name == "Davis Square Plaza")
    assert plaza.cost == "Free" and "(Davis Square)" in plaza.description


def test_only_cambridge_and_somerville_and_not_excluded_venues(offline):
    """The page covers Greater Boston. Only Cambridge and Somerville rows belong
    here, minus venues scraped directly (Middle East, Sonia, Lilypad)."""
    _, soup, events = _events()
    assert {e.city for e in events} == {"Cambridge", "Somerville"}
    assert len(soup.select("tr.event")) > 2 * len(events), "fixture should contain other cities"
    assert not [e for e in events
                if any(x in e.venue_name.lower() for x in BostonShowsScraper.EXCLUDED_VENUES)]


def test_unreadable_time_skips_instead_of_defaulting_to_8pm():
    """A row whose time did not parse used to be published at 8 PM. In
    data/events.json on 2026-10-05, 300 BostonShows events sat at 20:00; any
    that came from the fallback were fabricated."""
    combine = BostonShowsScraper._combine_date_time
    day = datetime(2026, 10, 6)
    assert combine(day, "8:00pm") == datetime(2026, 10, 6, 20, 0)
    assert combine(day, "12:00pm") == datetime(2026, 10, 6, 12, 0)
    assert combine(day, "12:30am") == datetime(2026, 10, 6, 0, 30)
    assert combine(day, "TBA") is None
    assert combine(day, "") is None
    assert combine(day, "8pm") is None


def test_output_satisfies_invariants(offline):
    """Every row is dated from the container's ISO `data-date` plus its own
    visible time; nothing is inferred and nothing carries seconds."""
    _, _, events = _events()
    assert len(events) > 150
    assert {e.source_name for e in events} == {"BostonShows.org"}
    assert min(e.start_datetime for e in events).date() == datetime(2026, 10, 6).date()
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
