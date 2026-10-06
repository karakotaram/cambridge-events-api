"""Brattle Theatre — showtimes from the coming-soon page.

Two saved pages: `listing.html.gz` (captured 2026-08-31, 87 showtimes to
Oct 21) and `coming-soon-2026-10-06.html.gz` (73 showtimes to Nov 23, crossing
the Nov 1 change back to standard time).
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture

MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1)}


def _scrape(fixture: str):
    scraper = BY_NAME["Brattle Theatre"].load()
    html = read_fixture("brattle", fixture)
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    scraper._fetch_film_description = lambda url, title: f"{title} at Brattle Theatre"
    return scraper.scrape_events()


@pytest.fixture(params=["UTC", "America/New_York", "Pacific/Honolulu"])
def machine_tz(request, monkeypatch):
    """Pretend the machine runs in another zone; restore it afterwards."""
    with monkeypatch.context() as m:
        m.setenv("TZ", request.param)
        time.tzset()
        yield request.param
    time.tzset()


def test_an_old_page_still_parses_in_full(offline):
    """Showtimes earlier than datetime.now() were dropped, so the August page
    yielded 3 showtimes on 2026-10-06 and would yield none after Oct 21 —
    failing the shared fixture tests. Staleness is EventValidator's job."""
    events = _scrape("listing.html.gz")

    assert len(events) == 87
    assert min(e.start_datetime for e in events) == datetime(2026, 8, 31, 21, 45)
    assert max(e.start_datetime for e in events) == datetime(2026, 10, 21, 19, 30)


def test_every_showtime_lands_on_its_listed_day_in_any_machine_zone(machine_tz, offline):
    """`data-date` is an epoch second near midnight Pacific. The scraper read it
    with datetime.fromtimestamp() in the machine's zone, which happened to give
    the right day on UTC and Eastern machines and the day before anywhere west
    of Pacific. The page's own visible date list ("Wed, Oct 7") is the
    reference, including the Nov 1 changeover where the value is 01:00 PDT."""
    scraper = BY_NAME["Brattle Theatre"].load()
    soup = BeautifulSoup(read_fixture("brattle", "coming-soon-2026-10-06.html.gz"), "html.parser")

    labelled = {}
    for li in soup.select("ul.datelist li[data-date]"):
        month, day = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", li.get_text(" ", strip=True)).groups()
        labelled[int(li["data-date"])] = (MONTHS[month], int(day))
    stamps = {int(li["data-date"]) for li in soup.select("div.showtimes-container li[data-date]")}
    assert stamps <= labelled.keys()

    for ts in stamps:
        day = scraper.listing_day(ts)
        assert day is not None, f"{ts} rejected"
        assert (day.month, day.day) == labelled[ts], f"{ts} read as {day} under TZ={machine_tz}"

    events = _scrape("coming-soon-2026-10-06.html.gz")
    assert len(events) == 73
    when = {(e.title, e.start_datetime) for e in events}
    # Evening showtimes either side of the clock change, as listed on the page
    assert ("Lovestruck Books Presents: Practical Magic on Screen & in Print", datetime(2026, 10, 6, 21, 0)) in when
    assert ("Split Rock", datetime(2026, 10, 31, 22, 0)) in when
    assert ("Behemoth!", datetime(2026, 11, 4, 20, 0)) in when
    assert ("Persepolis", datetime(2026, 11, 23, 18, 30)) in when
    assert sum(1 for e in events if e.start_datetime.date() == date(2026, 11, 1)) == 4


def test_a_value_that_is_not_a_midnight_is_refused():
    """If the attribute changes meaning, skip rather than shift a day."""
    read = BY_NAME["Brattle Theatre"].load().listing_day
    assert read(1791356400) == date(2026, 10, 7)        # 07:00 UTC, midnight PDT
    assert read(1793865600) == date(2026, 11, 5)        # 08:00 UTC, midnight PST
    assert read(1791399600) is None                     # 19:00 UTC — an evening, not a day
    assert read(1791356400 + 90) is None                # off the hour


@pytest.mark.parametrize("fixture", ["listing.html.gz", "coming-soon-2026-10-06.html.gz"])
def test_output_satisfies_invariants(fixture, offline):
    events = _scrape(fixture)
    assert {e.source_name for e in events} == {"Brattle Theatre"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
