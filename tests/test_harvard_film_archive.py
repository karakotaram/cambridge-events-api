"""Harvard Film Archive — screenings from the paged calendar.

Saved 2026-10-24: `calendar-2026-10-24.html.gz` (26 screenings to Nov 16, with
a "View more" link) and `calendar-page2-2026-10-24.html.gz` (15 more to Dec 14,
no further link), plus one screening's detail page.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture

PAGES = {
    "https://harvardfilmarchive.org/calendar": "calendar-2026-10-24.html.gz",
    "https://harvardfilmarchive.org/calendar?page=2": "calendar-page2-2026-10-24.html.gz",
}


def _scraper(edit=lambda html: html):
    scraper = BY_NAME["Harvard Film Archive"].load()
    scraper.requested = []

    def fetch(url, *a, **k):
        scraper.requested.append(url)
        return edit(read_fixture("harvard_film_archive", PAGES[url]))

    scraper.fetch_html = fetch                     # type: ignore[method-assign]
    scraper._fetch_details = lambda url: {}        # type: ignore[method-assign]
    return scraper


def test_both_pages_are_read_and_paging_stops(offline):
    scraper = _scraper()
    events = scraper.scrape_events()

    assert scraper.requested == list(PAGES)
    assert len(events) == 41
    assert min(e.start_datetime for e in events) == datetime(2026, 10, 24, 14, 0)
    assert max(e.start_datetime for e in events) == datetime(2026, 12, 14, 19, 0)


def test_screenings_land_on_their_listed_day_and_time(offline):
    when = {(e.title, e.start_datetime) for e in _scraper().scrape_events()}
    assert ("Je t’aime, je t’aime", datetime(2026, 10, 24, 14, 0)) in when
    assert ("Experiment in Terror", datetime(2026, 11, 7, 21, 15)) in when
    # Fourth screening of Nov 7, on a wrapped row whose day heading is empty
    assert ("Invasion of the Body Snatchers", datetime(2026, 11, 7, 23, 30)) in when
    # Same film twice, two screenings
    assert ("Marie Antoinette", datetime(2026, 10, 29, 18, 0)) in when
    assert ("Marie Antoinette", datetime(2026, 11, 15, 15, 0)) in when


def test_every_screening_is_a_film_at_the_archive(offline):
    events = _scraper().scrape_events()
    assert {e.category for e in events} == {EventCategory.FILM.value}
    assert {(e.venue_name, e.street_address, e.zip_code) for e in events} == {
        ("Harvard Film Archive", "24 Quincy Street", "02138")}
    assert all(e.source_url.startswith("https://harvardfilmarchive.org/calendar/") for e in events)


def test_labels_reach_cost_and_description(offline):
    by_start = {(e.title, e.start_datetime): e for e in _scraper().scrape_events()}
    marie = by_start[("Marie Antoinette", datetime(2026, 10, 29, 18, 0))]
    assert marie.cost == "Free"
    assert marie.description.startswith("Directed by Sofia Coppola, 2006.")
    assert "Director in person." in marie.description
    assert "Screening on film." in by_start[("Hunted", datetime(2026, 10, 26, 19, 0))].description


def test_a_start_that_disagrees_with_its_day_heading_is_skipped(offline):
    """The Oct 25 screening's attribute is moved to Oct 26 while it stays under
    the Oct 25 heading. Neither date can be trusted, so it is dropped."""
    moved = _scraper(lambda html: html.replace(
        'datetime="2026-10-25 19:00:00"', 'datetime="2026-10-26 19:00:00"'))
    titles = [e.title for e in moved.scrape_events()]
    assert len(titles) == 40
    assert "It Always Rains on Sunday" not in titles


@pytest.mark.parametrize("attribute, label, expected", [
    ("2026-10-24 19:00:00", "7:00 pm", datetime(2026, 10, 24, 19, 0)),
    ("2026-10-24 12:00:00", "12:00 pm", datetime(2026, 10, 24, 12, 0)),
    ("2026-10-24 00:30:00", "12:30 am", datetime(2026, 10, 24, 0, 30)),
    ("2026-10-24 07:00:00", "7:00 pm", None),   # a 12-hour attribute, as Cambridge.gov's was
    ("2026-10-24 19:00:00", "", None),          # no label to check against
    ("2026-10-24", "7:00 pm", None),            # a day, not a start
])
def test_attribute_and_label_must_agree(attribute, label, expected):
    assert BY_NAME["Harvard Film Archive"].load().parse_start(attribute, label) == expected


def test_detail_page_gives_series_and_blurb(offline):
    scraper = BY_NAME["Harvard Film Archive"].load()
    scraper.fetch_html = lambda *a, **k: read_fixture(  # type: ignore[method-assign]
        "harvard_film_archive", "detail-je-taime-je-taime-2026-10.html.gz")
    details = scraper._fetch_details("https://harvardfilmarchive.org/calendar/je-taime-je-taime-2026-10")
    assert details["series"] == "Nostalgia, A Harvard Undergraduate Cinematheque Program"
    assert details["description"].startswith("Deserving a secure place within the science fiction canon")


def test_a_missing_detail_page_does_not_lose_the_screening(offline):
    scraper = _scraper()
    del scraper._fetch_details                     # the real one, against a failing fetch
    pages = scraper.fetch_html

    def fetch(url, *a, **k):
        if url in PAGES:
            return pages(url)
        raise ConnectionError("detail page down")

    scraper.fetch_html = fetch                     # type: ignore[method-assign]
    assert len(scraper.scrape_events()) == 41


def test_output_satisfies_invariants(offline):
    events = _scraper().scrape_events()
    assert {e.source_name for e in events} == {"Harvard Film Archive"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 10, 24))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
