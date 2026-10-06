"""Boston Swing Central — WordPress posts on the home page.

`home-2026-10-06.html.gz` is the live page: two Friday dances and the
Sunday Swing Boot Camp. The site blocks GitHub's IP ranges, so this fixture
is the only way CI ever sees it.
"""
from __future__ import annotations

from datetime import datetime

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def _scrape(html: str):
    scraper = BY_NAME["Boston Swing Central"].load()
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    return scraper.scrape_events()


def _entry(month_day: str, year: str, title: str, body: str) -> str:
    return (f'<div class="entry"><div class="date"><p><span class="month">{month_day}</span>'
            f'<span class="day"> {year} </span></p></div>'
            f'<h3 class="etitle"><a href="https://www.bostonswingcentral.org/?p=1">{title}</a></h3>'
            f'<div class="ebody">{body}</div></div>')


def test_boot_camp_starts_at_eleven_and_links_this_seasons_form(offline):
    """The boot camp states its time under "TIME + PRICE:", not "SCHEDULE",
    so no time was read and it fell back to midnight. Its blurb above that
    heading says "11am-1:30pm": scanning the whole post with the old
    colon-only pattern finds "1:30pm" first, which is why the search starts
    at the heading. Its signup link was hard-coded to the 2025 form; the
    live one is ...-20262027."""
    events = {e.title: e for e in _scrape(read_fixture("boston_swing", "home-2026-10-06.html.gz"))}

    boot_camp = events["Swing Boot Camp – Sunday Oct 18"]
    assert boot_camp.start_datetime == datetime(2026, 10, 18, 11, 0)
    assert boot_camp.source_url == "https://bostonswingcentral.wufoo.com/forms/bsc-swing-boot-camp-20262027/"


def test_dances_start_at_their_evening_schedule(offline):
    events = _scrape(read_fixture("boston_swing", "home-2026-10-06.html.gz"))
    when = {e.title: e.start_datetime for e in events}

    assert len(events) == 3
    assert when["Annie & The Fur Trappers at Epic Ballroom"] == datetime(2026, 10, 9, 20, 0)
    assert when["The Swing Legacy – Oct 16"] == datetime(2026, 10, 16, 20, 0)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_year_is_read_from_the_page_and_a_missing_time_is_skipped(offline):
    """The year was inferred from the clock (a month earlier than this one
    meant next year), so a January dance listed in December would be fine and
    a September one read in October would jump a year. The page prints the
    year beside every date. An event whose time cannot be found is skipped,
    not published at 00:00."""
    html = (
        _entry("Jan, 8", "2027", "New Year Dance",
               "<p>🗓 EVENING SCHEDULE</p><p>8:00pm – 11:00pm: Swing Dancing</p>")
        + _entry("Sep, 4", "2026", "September Dance",
                 "<p>🗓 EVENING SCHEDULE</p><p>7:30pm – 11:00pm: Swing Dancing</p>")
        + _entry("Oct, 30", "2026", "Halloween Social",
                 "<p>Costumes encouraged! Details to follow.</p>")
    )
    when = {e.title: e.start_datetime for e in _scrape(html)}

    assert when == {
        "New Year Dance": datetime(2027, 1, 8, 20, 0),
        "September Dance": datetime(2026, 9, 4, 19, 30),
    }
