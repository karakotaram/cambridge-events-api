"""The Middle East — a paginated TicketWeb listing with no year on any row.

Pages saved 2026-10-06: page 1 (the next five days, including a show printed
"– CANCELLED – REFUNDS AT POINT OF PURCHASE"), page 12 (the last shows, Feb–May
2027), and page 13 (past the end: "Currently no scheduled events").
"""
from __future__ import annotations

from datetime import date, datetime

from src.quality.invariants import check_invariants
from src.scrapers.mideast import MideastClubScraper, year_for_weekday
from tests.conftest import read_fixture

TODAY = date(2026, 10, 6)


def _scraper_over(pages: dict[int, str]) -> MideastClubScraper:
    scraper = MideastClubScraper(today=TODAY)
    fetched = []

    def fetch(url, *a, **k):
        n = 1 if url == scraper.source_url else int(url.rstrip("/").rsplit("/", 1)[1])
        fetched.append(n)
        return pages.get(n, pages[13])

    scraper.fetch_page = fetch                         # type: ignore[method-assign]
    scraper.fetched = fetched
    return scraper


def test_reads_every_page_not_just_the_first(offline):
    """The scraper read page 1 only and then capped it at 30, so the calendar
    held 20 shows through Oct 10 while the club listed ~228 through May 2027.
    It must follow /page/N/ until a page lists nothing, and keep what is there."""
    scraper = _scraper_over({
        1: read_fixture("mideast", "page1-2026-10-06.html.gz"),
        2: read_fixture("mideast", "page12-2026-10-06.html.gz"),
        13: read_fixture("mideast", "page13-2026-10-06.html.gz"),
    })
    events = scraper.scrape_events()

    assert scraper.fetched == [1, 2, 3], "should stop at the first page with no shows"
    assert len(events) == 19 + 7, "20 rows on page 1 less one cancellation, plus 7 on the last page"
    assert max(e.start_datetime for e in events) == datetime(2027, 5, 1, 19, 0)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert {e.source_name for e in events} == {"The Middle East"}


def test_a_page_that_repeats_ends_the_loop(offline):
    """If the site ever serves page 1 for every N, the loop must not run to the cap."""
    page = read_fixture("mideast", "page1-2026-10-06.html.gz")
    scraper = _scraper_over({n: page for n in range(1, 14)})
    events = scraper.scrape_events()
    assert scraper.fetched == [1, 2, 3]
    assert len(events) == 19


def test_one_repeated_page_is_skipped_not_the_end(offline):
    """On 2026-10-06 the site's cache served page 1's shows at /page/12/ for a
    few minutes. Stopping there would have dropped the last page of the
    season; the page after it is still read."""
    page1 = read_fixture("mideast", "page1-2026-10-06.html.gz")
    scraper = _scraper_over({1: page1, 2: page1,
                             3: read_fixture("mideast", "page12-2026-10-06.html.gz"),
                             13: read_fixture("mideast", "page13-2026-10-06.html.gz")})
    events = scraper.scrape_events()
    assert scraper.fetched == [1, 2, 3, 4]
    assert len(events) == 19 + 7


def test_cancelled_shows_are_not_published(offline):
    """"Kidd G, Grey Oakes – CANCELLED – REFUNDS AT POINT OF PURCHASE" was live
    on the calendar as if it were happening."""
    scraper = _scraper_over({1: read_fixture("mideast", "page1-2026-10-06.html.gz"),
                             13: read_fixture("mideast", "page13-2026-10-06.html.gz")})
    titles = [e.title for e in scraper.scrape_events()]
    assert not [t for t in titles if "CANCEL" in t.upper() or "Kidd G" in t]


def test_year_comes_from_the_printed_weekday(offline):
    """Rows print "Thu 2.25" with no year. The old code guessed from the clock
    (month earlier than this month means next year); the weekday decides it."""
    scraper = _scraper_over({1: read_fixture("mideast", "page12-2026-10-06.html.gz"),
                             13: read_fixture("mideast", "page13-2026-10-06.html.gz")})
    by_title = {e.title: e for e in scraper.scrape_events()}
    assert by_title["Surfer Girl, South Summit, JOBY!"].start_datetime == datetime(2027, 2, 25, 19, 0)

    assert year_for_weekday(10, 6, 1, TODAY) == 2026           # Tue 10.6
    assert year_for_weekday(2, 25, 3, TODAY) == 2027           # Thu 2.25
    assert year_for_weekday(10, 6, 2, TODAY) == 2027           # Wed 10.6 is next year's
    assert year_for_weekday(10, 6, 3, TODAY) is None           # Thu 10.6: no nearby year, so skip


def test_sonia_gets_its_own_address(offline):
    """Sonia is the club's room at 10 Brookline St; every row used to carry the
    Mass Ave address and the venue name "The Middle East"."""
    scraper = _scraper_over({1: read_fixture("mideast", "page1-2026-10-06.html.gz"),
                             13: read_fixture("mideast", "page13-2026-10-06.html.gz")})
    events = scraper.scrape_events()
    sonia = [e for e in events if e.venue_name == "Sonia"]
    assert sonia and all(e.street_address == "10 Brookline St" for e in sonia)
    upstairs = next(e for e in events if e.title.startswith("Supersuckers"))
    assert upstairs.venue_name == "Middle East - Upstairs"
    assert upstairs.start_datetime == datetime(2026, 10, 6, 19, 0)
    assert upstairs.cost == "$29.35"
