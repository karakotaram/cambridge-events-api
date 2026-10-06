"""The Mad Monkfish — a BentoBox site whose cards carry no reliable time.

Fixtures, captured 2026-10-06:
  listing-2026-10-06-pages.json.gz  the four `?p=N` listing pages, keyed by URL
  events-2026-10-06-page1.json.gz   the ten event pages linked from page 1
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture

LISTING = "https://www.themadmonkfish.com/jazz-schedule/"


@pytest.fixture
def pages():
    return json.loads(read_fixture("mad_monkfish", "listing-2026-10-06-pages.json.gz"))


@pytest.fixture
def event_pages():
    return json.loads(read_fixture("mad_monkfish", "events-2026-10-06-page1.json.gz"))


def _serving(scraper, bodies: dict, log: list):
    def fetch(url, *a, **k):
        log.append(url)
        if url not in bodies:
            raise IOError(f"404 {url}")
        return bodies[url]
    scraper.fetch_html = fetch                      # type: ignore[method-assign]
    return scraper


def test_mad_monkfish_reads_every_page_of_the_pager(pages, offline):
    """Only page 1 was ever read: ten events, four days. The forward pager is an
    `<a>` labelled "Previous" and "Load More Events" is a `<button>`, so a search
    for a "Load More|Next" link found nothing and paging stopped. Pages 2-4 add
    27 more shows, five weeks out."""
    fetched: list = []
    scraper = _serving(BY_NAME["The Mad Monkfish"].load(), pages, fetched)

    listed = scraper.read_listing()

    assert len(listed) == 37
    assert len({url for url, _, _ in listed}) == 37
    assert any("1112-sheila-jordan" in url for url, _, _ in listed), "page 4 was not reached"
    assert fetched == [LISTING] + [f"{LISTING}?p={n}" for n in (2, 3, 4)], (
        "page 4 links to no page 5, so nothing past it should be requested")


def test_mad_monkfish_stops_when_the_pager_wraps(pages, offline):
    """Past its last page the site serves page 1 again. Paging must stop on the
    first page that adds nothing new, not loop or double-count."""
    wrapped = dict(pages)
    wrapped[f"{LISTING}?p=3"] = pages[LISTING]
    fetched: list = []
    scraper = _serving(BY_NAME["The Mad Monkfish"].load(), wrapped, fetched)

    listed = scraper.read_listing()

    assert len(listed) == 20
    assert fetched[-1] == f"{LISTING}?p=3"


def test_mad_monkfish_dates_each_show_from_its_event_page(pages, event_pages, offline):
    """Seven of page 1's ten titles name no time, and every one of them was
    published at a defaulted 7 PM — "Nick Brust Late Night Jam" among them,
    which the venue lists at 10 PM. "10/9 The Midnight Hour … 12-1am" became
    Oct 9 1 AM with the title cut to "…Quintero 12-"; the venue means the
    midnight that starts Oct 10. The event page states both correctly.

    Only page 1's event pages are in the fixture; the other 27 fail to load
    here and must be skipped, not dated from their titles."""
    scraper = _serving(BY_NAME["The Mad Monkfish"].load(), {**pages, **event_pages}, [])

    events = scraper.scrape_events()
    by_url = {e.source_url.rsplit("/event/", 1)[1].strip("/"): e for e in events}

    assert len(events) == 10
    jam = by_url["1010-nick-brust-late-night-jam-session"]
    assert jam.start_datetime == datetime(2026, 10, 10, 22, 0)
    assert jam.end_datetime == datetime(2026, 10, 11, 0, 0), "'until 12:00 AM' ends at midnight, not before it starts"

    midnight = by_url["109-the-midnight-hour-wcamila-quintero-12-1am"]
    assert midnight.start_datetime == datetime(2026, 10, 10, 0, 0)
    assert midnight.title == "The Midnight Hour w/Camila Quintero"

    assert not [e.title for e in events if any(ch.isdigit() for ch in e.title.split()[-1])], \
        "a time fragment was left on the end of a title"
    assert {e.source_name for e in events} == {"The Mad Monkfish"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_mad_monkfish_skips_a_show_it_cannot_time(event_pages, offline):
    """No time on the event page means no event, never a 7 PM guess."""
    from bs4 import BeautifulSoup

    scraper = BY_NAME["The Mad Monkfish"].load()
    url = "https://www.themadmonkfish.com/event/1010-nick-brust-late-night-jam-session/"
    soup = BeautifulSoup(event_pages[url], "html.parser")
    strong = soup.find("article").find("strong")
    strong.string = "October 10, 2026"

    assert scraper.parse_detail(soup, url, "10/10 Nick Brust Late Night Jam Session") is None


@pytest.mark.parametrize("raw,clean", [
    ("10/9 The Midnight Hour w/Camila Quintero 12-1am", "The Midnight Hour w/Camila Quintero"),
    ("10/18 Jazz Vocal Jam Session feat. Nelly Denurra (3pm-6pm)", "Jazz Vocal Jam Session feat. Nelly Denurra"),
    ("10/8 Niccole Meza Quartet 7pm (Mad Monkfish Concert Series)", "Niccole Meza Quartet (Mad Monkfish Concert Series)"),
    ("10/29 100 Miles at Monkfish 7pm", "100 Miles at Monkfish"),
])
def test_mad_monkfish_title_loses_its_date_and_time(raw, clean):
    """Card titles embed the date and time; neither belongs in the title."""
    assert BY_NAME["The Mad Monkfish"].load().clean_title(raw) == clean
