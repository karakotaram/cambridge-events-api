"""Batch 2, group H: aggregators, dance, classes and the rest.

Every fixture is the source as served on 2026-11-16, trimmed where noted in
the fixture itself (the Boston Calendar days keep every Cambridge/Somerville
record plus a sample of the rest of Greater Boston; the Wix pages keep only
their events data). Two sources had nothing current that day, so their tests
run on the most recent past: Thursday Morning Talks (the 2026 season, Jan-Feb)
and Boston Lindy Hop (socials of July-October 2026).

The tests assert behaviour — what is kept, dropped, placed and timed — not
counts typed from a live site.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, timedelta

import pytest

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES
from src.sources import BY_NAME
from tests.conftest import read_fixture

BC_DAYS = ("2026-11-16", "2026-11-21", "2026-11-24")


def serve(scraper, pages: dict, calls: list | None = None):
    """Answer the scraper's fetch_html from fixtures, by URL; anything else fails."""
    def fetch(url, *args, **kwargs):
        if calls is not None:
            calls.append(url)
        for key, body in pages.items():
            if key(url) if callable(key) else key == url:
                return body
        raise AssertionError(f"unexpected fetch {url}")
    scraper.fetch_html = fetch
    return scraper


def bc_rows(day: str) -> list:
    return json.loads(read_fixture("boston_calendar", f"day-{day}.json.gz"))


def bc_listing(day: str) -> str:
    return read_fixture("boston_calendar", f"day-{day}.html.gz")


def boston_calendar_events(scraper=None):
    scraper = scraper or BY_NAME["The Boston Calendar"].load()
    rows, shown = {}, {}
    for day in BC_DAYS:
        for row in bc_rows(day):
            rows.setdefault(row["id"], row)
        shown.update(scraper.parse_listing(bc_listing(day)))
    return scraper, list(rows.values()), shown, scraper.parse_rows(rows.values(), shown)


def lindy_pages():
    return {
        "/events/2026/October/blh-presents-somerville-stomp-october-2026/":
            read_fixture("boston_lindy_hop", "event-somerville-stomp-oct-2026-11-16.html.gz"),
        "/events/2026/August/blh-presents-bayou-bounce/":
            read_fixture("boston_lindy_hop", "event-bayou-bounce-2026-11-16.html.gz"),
    }


def lindy_feed(name="feed-jul-oct-2026-11-16.json.gz", only_pages=True) -> list:
    """The feed, minus socials whose pages were not captured (July/August Stomps)."""
    items = json.loads(read_fixture("boston_lindy_hop", name))
    pages = lindy_pages()
    return [i for i in items
            if not only_pages or not i["url"].startswith("/events/") or i["url"] in pages or "Break" in i["title"]]


def lindy_scraper(items: list, calls: list | None = None):
    scraper = BY_NAME["Boston Lindy Hop"].load()
    scraper.today = date(2026, 7, 1)
    pages = {lambda u: "/events/feed/json/" in u: json.dumps(items)}
    pages.update({f"https://www.bostonlindyhop.com{path}": html for path, html in lindy_pages().items()})
    return serve(scraper, pages, calls)


def scrape(source_name: str):
    """Run a registered source offline over its fixture."""
    scraper = BY_NAME[source_name].load()
    if source_name == "The Boston Calendar":
        return boston_calendar_events(scraper)[3]
    if source_name == "New School of Music":
        page = read_fixture("new_school_of_music", "calendar-2026-11-16.html.gz")
        return serve(scraper, {scraper.source_url: page}).scrape_events()
    if source_name == "MetaMovements":
        page = read_fixture("metamovements", "home-2026-11-16.html.gz")
        return serve(scraper, {scraper.source_url: page}).scrape_events()
    if source_name == "Thursday Morning Talks":
        data = read_fixture("thursday_morning_talks", "data-2026-11-16.yml.gz")
        return serve(scraper, {"https://thursdaymorningtalks.org/data.yml": data}).scrape_events()
    if source_name == "American Classics":
        page = read_fixture("american_classics", "current-season-2026-11-16.html.gz")
        return serve(scraper, {scraper.source_url: page}).scrape_events()
    if source_name == "Have to Dance":
        page = read_fixture("have_to_dance", "calendar-2026-11-16.html.gz")
        return serve(scraper, {scraper.source_url: page}).scrape_events()
    if source_name == "Boston Lindy Hop":
        return lindy_scraper(lindy_feed()).scrape_events()
    raise KeyError(source_name)


GROUP_H = ("The Boston Calendar", "New School of Music", "MetaMovements", "Thursday Morning Talks",
           "American Classics", "Have to Dance", "Boston Lindy Hop")


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", GROUP_H)
def test_source_parses_clean(source_name, offline):
    events = scrape(source_name)
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events],
                                          now=datetime(2026, 11, 16))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert all(e.start_datetime.tzinfo is None for e in events)
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title]


def test_aggregators_are_registered_as_aggregators():
    """A listings site that outranks the venues it copies takes their credit
    in deduplication (CLAUDE.md, traps)."""
    assert BY_NAME["The Boston Calendar"].kind == "aggregator"
    assert BY_NAME["Have to Dance"].kind == "aggregator"


# --------------------------------------------------------------------------- #
# The Boston Calendar
# --------------------------------------------------------------------------- #

def test_boston_calendar_keeps_local_occurrences_and_no_standing_listings(offline):
    """Most records are elsewhere in Greater Boston, and most local ones are
    standing listings ("Faro Cafe | Cambridge", 2023-01-01 to 2026-12-31)
    printed under whatever day was asked for. Only dated occurrences remain."""
    scraper, rows, _, events = boston_calendar_events()
    elsewhere = [r for r in rows if r["city"].strip().title() not in LOCAL_CITIES]
    standing = [r for r in rows if r["city"].strip().title() in LOCAL_CITIES
                and (date.fromisoformat(r["end_date"]) - date.fromisoformat(r["event_date"])).days > 1]
    assert elsewhere and standing, "fixture should contain both"

    by_url = {e.source_url.rsplit("/", 1)[-1]: e for e in events}
    assert not set(by_url) & {r["slug"] for r in elsewhere + standing}
    rows_by_slug = {r["slug"]: r for r in rows}
    for slug, event in by_url.items():
        assert event.start_datetime.date().isoformat() == rows_by_slug[slug]["event_date"]
    assert {e.start_datetime.date() for e in events} == {date.fromisoformat(d) for d in BC_DAYS}


def test_boston_calendar_skips_a_record_its_own_listing_contradicts(offline):
    """On 2026-11-16 the record for "7PM Detention" on Nov 21 said 8:00pm at
    Cambridge Community Center, Cambridge; the listing and the event page said
    7:00p at Goofs Comedy Club, Somerville."""
    scraper, rows, shown, events = boston_calendar_events()
    slug = "8pm-detention-standup-comedy-show-and-4-drinks--86"
    record = next(r for r in rows if r["slug"] == slug)
    assert (record["start_time"], record["address1"], record["city"]) == \
        ("8:00pm", "Cambridge Community Center", "Cambridge")
    assert shown[slug] == (date(2026, 11, 21), (19, 0), "Goofs Comedy Club")
    assert not [e for e in events if e.source_url.endswith(slug)]

    # The same record, agreeing with its listing, is published.
    agreeing = dict(shown, **{slug: (date(2026, 11, 21), (20, 0), "Cambridge Community Center")})
    assert scraper.parse_row(record, agreeing).start_datetime == datetime(2026, 11, 21, 20, 0)


def test_boston_calendar_leaves_records_the_listing_does_not_show(offline):
    """A record created after the listing was cached is picked up next run,
    not published unconfirmed."""
    scraper, rows, shown, events = boston_calendar_events()
    published = events[0].source_url.rsplit("/", 1)[-1]
    record = next(r for r in rows if r["slug"] == published)
    assert scraper.parse_row(record, {k: v for k, v in shown.items() if k != published}) is None


def test_boston_calendar_overnight_kept_two_day_run_dropped(offline):
    scraper, rows, shown, events = boston_calendar_events()
    by_slug = {e.source_url.rsplit("/", 1)[-1]: e for e in events}

    karaoke = by_slug["drop-the-mic-karaoke--135"]
    assert (karaoke.start_datetime, karaoke.end_datetime) == \
        (datetime(2026, 11, 24, 23, 0), datetime(2026, 11, 25, 2, 0))

    # Noon Saturday to 5pm Sunday: which hours are open is not said.
    run = next(r for r in rows if r["slug"] == "brickbottom-arts-district-open-studios")
    assert (run["event_date"], run["end_date"]) == ("2026-11-21", "2026-11-22")
    assert "brickbottom-arts-district-open-studios" not in by_slug


def test_boston_calendar_midnight_start_means_no_time_given(offline):
    scraper, rows, shown, events = boston_calendar_events()
    record = copy.deepcopy(next(r for r in rows if r["slug"] == "drop-the-mic-karaoke--135"))
    record.update(start_time="12:00am", end_time="11:30pm", end_date=record["event_date"])
    listed = {record["slug"]: (date(2026, 11, 24), (0, 0), record["address1"])}
    event = scraper.parse_row(record, listed)
    assert event.all_day and event.start_datetime == datetime(2026, 11, 24) and event.end_datetime is None


def test_boston_calendar_fetches_one_record_set_and_one_listing_per_day(offline):
    """WINDOW_DAYS days, each read twice: the JSON records and the HTML listing.
    Captured days beyond the window are not asked for, so their events drop out."""
    from src.scrapers.boston_calendar import WINDOW_DAYS

    scraper = BY_NAME["The Boston Calendar"].load()
    scraper.today = date(2026, 11, 16)
    pages = {}
    for offset in range(WINDOW_DAYS):
        day = scraper.today + timedelta(days=offset)
        query = f"day={day.day}&month={day.month}&year={day.year}"
        captured = day.isoformat() in BC_DAYS
        pages[f"https://www.thebostoncalendar.com/events.json?{query}"] = \
            json.dumps(bc_rows(day.isoformat())) if captured else "[]"
        pages[f"https://www.thebostoncalendar.com/events?{query}"] = \
            bc_listing(day.isoformat()) if captured else '<ul id="events"></ul>'
    calls: list = []
    events = serve(scraper, pages, calls).scrape_events()

    assert len(calls) == 2 * WINDOW_DAYS and set(calls) == set(pages)
    last = scraper.today + timedelta(days=WINDOW_DAYS - 1)
    in_window = [e for e in boston_calendar_events()[3] if e.start_datetime.date() <= last]
    assert in_window and len(events) == len(in_window)


@pytest.mark.parametrize("text, clock", [
    ("7:30pm", (19, 30)), ("7:00p", (19, 0)), ("12:00am", (0, 0)), ("12:30pm", (12, 30)),
    ("11:00a", (11, 0)), ("", None), ("7pm", None), ("19:30", None), ("13:00pm", None),
])
def test_boston_calendar_clock(text, clock):
    from src.scrapers.boston_calendar import read_clock
    assert read_clock(text) == clock


def test_boston_calendar_cost_and_category(offline):
    _, _, _, events = boston_calendar_events()
    by_slug = {e.source_url.rsplit("/", 1)[-1]: e for e in events}
    assert by_slug["blind-tasting-at-bonde-wine-shop--12"].cost == "$19"
    assert by_slug["encore-america-sings-250-with-harvard-radcliffe-chorus"].category == EventCategory.MUSIC
    assert all(e.image_url is None or e.image_url.startswith("https://media.thebostoncalendar.com/images/")
               for e in events)


# --------------------------------------------------------------------------- #
# Wix Events pages: New School of Music, MetaMovements
# --------------------------------------------------------------------------- #

def wix_page(module: str, name: str) -> str:
    return read_fixture(module, name)


def wix_with(module: str, name: str, edit) -> str:
    """The fixture page with its warmup JSON passed through edit(events)."""
    page = wix_page(module, name)
    match = re.search(r'(<script type="application/json" id="wix-warmup-data">)(.*)(</script>)', page, re.S)
    data = json.loads(match.group(2))
    for widget in data["appsWarmupData"]["140603ad-af8d-84a5-2c80-a0f60cb47351"].values():
        edit(widget["events"]["events"])
    return page[:match.start(2)] + json.dumps(data) + page[match.end(2):]


def test_wix_reads_the_printed_address_not_the_stale_structured_street(offline):
    """NSM's concert at First Church prints "11 Garden St, Cambridge"; its
    structured street still says the school's own 25 Lowell Street."""
    events = scrape("New School of Music")
    heron = next(e for e in events if e.title == "NSM Choirs & Blue Heron")
    assert (heron.venue_name, heron.street_address, heron.city) == ("First Church in Cambridge", "11 Garden St", "Cambridge")
    # An evening concert stays on its own day: 7:30 PM Dec 19 is 00:30 UTC Dec 20.
    assert heron.start_datetime == datetime(2026, 12, 19, 19, 30)
    at_school = [e for e in events if e.venue_name == "New School of Music"]
    assert at_school and {e.street_address for e in at_school} == {"25 Lowell Street"}


def test_wix_skips_an_instant_that_disagrees_with_the_printed_time(offline):
    def shift(events):
        for e in events:
            if e["title"] == "Holiday Sing-Along":
                e["scheduling"]["startTimeFormatted"] = "5:30 PM"
    page = wix_with("new_school_of_music", "calendar-2026-11-16.html.gz", shift)
    scraper = serve(BY_NAME["New School of Music"].load(),
                    {"https://www.newschoolofmusic.org/calendar": page})
    titles = {e.title for e in scraper.scrape_events()}
    assert "Holiday Sing-Along" not in titles and "Rooted and Rising" in titles


def test_wix_skips_canceled_tbd_and_non_eastern_events(offline):
    def spoil(events):
        for e in events:
            if e["title"] == "Rooted and Rising":
                e["status"] = 3
            elif e["title"] == "Holiday Sing-Along":
                e["scheduling"]["config"]["timeZoneId"] = "America/Los_Angeles"
            elif e["title"] == "Free Faculty Concert Series: Peter Paetkau, violin":
                e["scheduling"]["config"]["scheduleTbd"] = True
    page = wix_with("new_school_of_music", "calendar-2026-11-16.html.gz", spoil)
    scraper = serve(BY_NAME["New School of Music"].load(),
                    {"https://www.newschoolofmusic.org/calendar": page})
    titles = {e.title for e in scraper.scrape_events()}
    assert not titles & {"Rooted and Rising", "Holiday Sing-Along",
                         "Free Faculty Concert Series: Peter Paetkau, violin"}
    assert "NSM Choirs & Blue Heron" in titles


def test_metamovements_keeps_only_cambridge_and_somerville(offline):
    page = wix_page("metamovements", "home-2026-11-16.html.gz")
    assert "Leominster" in page and "Roxbury" in page, "fixture should include classes elsewhere"
    events = scrape("MetaMovements")
    assert {e.city for e in events} == {"Cambridge", "Somerville"}
    rumbazo = next(e for e in events if e.title == "Afro Cuban Workshops & Rumbazo!")
    assert (rumbazo.start_datetime, rumbazo.end_datetime) == (datetime(2026, 12, 5, 14), datetime(2026, 12, 5, 17))
    assert rumbazo.source_url == "https://www.metamovements.com/event-details/afro-cuban-workshops-rumbazo"


# --------------------------------------------------------------------------- #
# Thursday Morning Talks
# --------------------------------------------------------------------------- #

def tmt(data: str):
    scraper = BY_NAME["Thursday Morning Talks"].load()
    return serve(scraper, {"https://thursdaymorningtalks.org/data.yml": data}).scrape_events()


def test_thursday_morning_talks_season(offline):
    """The 2026 season, still posted in November: eight Thursdays, 10:15-12:00."""
    events = scrape("Thursday Morning Talks")
    assert events and all(e.start_datetime.weekday() == 3 for e in events)
    assert {(e.start_datetime.time(), e.end_datetime.time()) for e in events} == \
        {(datetime(2026, 1, 1, 10, 15).time(), datetime(2026, 1, 1, 12, 0).time())}
    assert {(e.venue_name, e.street_address) for e in events} == {("Holy Trinity Armenian Church", "145 Brattle Street")}
    assert datetime(2026, 1, 8, 10, 15) in {e.start_datetime for e in events}


def test_thursday_morning_talks_skips_a_date_that_is_not_a_thursday(offline):
    data = read_fixture("thursday_morning_talks", "data-2026-11-16.yml.gz")
    events = tmt(data.replace('date: "2026-01-15"', 'date: "2026-01-16"'))
    assert len(events) == len(scrape("Thursday Morning Talks")) - 1
    assert date(2026, 1, 16) not in {e.start_datetime.date() for e in events}


def test_thursday_morning_talks_fails_loudly_if_the_venue_moves(offline):
    data = read_fixture("thursday_morning_talks", "data-2026-11-16.yml.gz")
    with pytest.raises(ValueError, match="145 Brattle"):
        tmt(data.replace("145 Brattle Street", "1 Elsewhere Street"))


# --------------------------------------------------------------------------- #
# American Classics
# --------------------------------------------------------------------------- #

def test_american_classics_keeps_cambridge_drops_bedford(offline):
    page = read_fixture("american_classics", "current-season-2026-11-16.html.gz")
    assert page.count("Bedford, MA") >= 3, "fixture should include the Bedford repeats"
    events = scrape("American Classics")
    assert {e.city for e in events} == {"Cambridge"}
    assert {e.start_datetime for e in events} == {
        datetime(2026, 11, 13, 19, 30), datetime(2027, 2, 19, 19, 30), datetime(2027, 4, 16, 19, 30)}
    fall = next(e for e in events if e.start_datetime.month == 11)
    assert fall.cost == "$25 - $30" and fall.website_url.startswith("https://www.eventbrite.com/")
    # The Winter concert's ticket link is commented out in the page; not read.
    winter = next(e for e in events if e.start_datetime.month == 2)
    assert winter.website_url is None and winter.cost is None


def test_american_classics_skips_a_date_whose_weekday_disagrees(offline):
    page = read_fixture("american_classics", "current-season-2026-11-16.html.gz")
    scraper = BY_NAME["American Classics"].load()
    events = scraper.parse_page(page.replace("Friday, Feb. 19, 2027", "Saturday, Feb. 19, 2027"))
    assert datetime(2027, 2, 19, 19, 30) not in {e.start_datetime for e in events}
    assert len(events) == 2


# --------------------------------------------------------------------------- #
# Have to Dance
# --------------------------------------------------------------------------- #

def test_have_to_dance_places_rows_from_their_address(offline):
    page = read_fixture("have_to_dance", "calendar-2026-11-16.html.gz")
    assert "Chestnut Hill" in page, "fixture should include a row elsewhere"
    events = scrape("Have to Dance")
    assert {(e.venue_name, e.street_address, e.city) for e in events} == \
        {("West Cambridge Youth Center", "680 Huron Avenue", "Cambridge")}
    # Unclosed <tr>s nest each row in the one before; each is read once.
    assert len({(e.title, e.start_datetime) for e in events}) == len(events)
    assert datetime(2026, 12, 12, 21, 0) in {e.start_datetime for e in events}
    assert all(e.source_url.startswith("http://havetodance.com/calendar.html#") for e in events)


@pytest.mark.parametrize("text, start", [
    ("9:00pm-midnight (lesson: 8:00)", (21, 0)),
    ("7:00-9:00pm, lesson 6:00.", (19, 0)),
    ("8:00pm", (20, 0)),
    ("(lesson: 8:00)", None),
    ("8-midnight", None),
    ("11:00-1:00pm", None),
])
def test_have_to_dance_start_time(text, start):
    from src.scrapers.have_to_dance import read_start
    assert read_start(text) == start


# --------------------------------------------------------------------------- #
# Boston Lindy Hop
# --------------------------------------------------------------------------- #

def test_boston_lindy_hop_reads_socials_not_classes(offline):
    items = lindy_feed()
    classes = [i for i in items if i["url"].startswith("/classes/")]
    assert classes, "fixture should include class sessions"

    calls: list = []
    events = lindy_scraper(items, calls).scrape_events()
    assert len(calls) == 1 + len(lindy_pages()), "one feed, then one page per social; no class pages"
    places = {(e.title, e.venue_name, e.city, e.start_datetime) for e in events}
    assert places == {
        ("BLH Presents: Somerville Stomp - October 2026", "Samba Bar", "Somerville", datetime(2026, 10, 21, 19, 0)),
        ("BLH Presents: Bayou Bounce", "First Church in Cambridge", "Cambridge", datetime(2026, 8, 17, 19, 30)),
    }
    assert not [e for e in events if "Break" in e.title]


def test_boston_lindy_hop_skips_a_feed_start_its_page_contradicts(offline):
    items = lindy_feed()
    for item in items:
        if "Somerville Stomp" in item["title"]:
            item["start"] = item["start"].replace("T19:00", "T18:00")
    titles = {e.title for e in lindy_scraper(items).scrape_events()}
    assert titles == {"BLH Presents: Bayou Bounce"}


def test_boston_lindy_hop_upcoming_window_of_classes_only(offline):
    """On 2026-11-16 the next four months held only class sessions."""
    items = lindy_feed("feed-upcoming-2026-11-16.json.gz", only_pages=False)
    assert items and all(i["url"].startswith("/classes/") for i in items)
    calls: list = []
    assert lindy_scraper(items, calls).scrape_events() == []
    assert len(calls) == 1
