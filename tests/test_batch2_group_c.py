"""Batch 2, group C — music clubs, comedy, bookshops.

Each fixture is what the source served on 2026-11-16 (the Passim festival and
summer-series pages trimmed to their <main>, the ShoWare descriptions cut
short, the Wix page cut to its warm-up data). One more is the Passim calendar
as the Wayback Machine captured it on 2026-07-17, which held the off-site shows
the November page does not. The summer-series pages hold only last season's
leftovers, so those tests check dates in the past.

Scrapers are run with their fetch methods pointed at the fixtures; the
`offline` fixture fails any real request. Tests assert behaviour — what is
kept, dropped, placed and timed — not counts read off a live site.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime

import pytest

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.scrapers.burren import parse_times
from src.scrapers.club_passim import SERIES, SITE
from src.sources import BY_NAME
from tests.conftest import read_fixture

TODAY = date(2026, 11, 16)
STAMP = TODAY.isoformat()
LOUNGE, UNDERGROUND = "968ae511-dae2-43a9-a916-15ae44ef47b8", "6126dd7d-0e07-4aad-92ef-3b52c0955bd4"


def fixture_json(module: str, name: str):
    return json.loads(read_fixture(module, name))


# --------------------------------------------------------------------------- #
# Offline runners, one per source
# --------------------------------------------------------------------------- #

def run_passim(main: str = f"live-music-{STAMP}.html.gz", calls: list | None = None):
    pages = {
        f"{SITE}/live-music/": read_fixture("club_passim", main),
        f"{SITE}/live-music/events/boston-celtic-music-festival/":
            read_fixture("club_passim", f"boston-celtic-music-festival-{STAMP}.html.gz"),
    }
    scraper = BY_NAME["Club Passim"].load()

    def fetch(url):
        if calls is not None:
            calls.append(url)
        return pages[url]

    scraper.fetch_page = fetch
    return scraper.scrape_events()


def run_passim_series():
    pages = {f"{SITE}{path}": read_fixture("club_passim", f"series-{path.rstrip('/').split('/')[-1]}-{STAMP}.html.gz")
             for path, _, _ in SERIES}
    scraper = BY_NAME["Passim Summer Series"].load()
    scraper.today = TODAY
    scraper.fetch_page = lambda url: pages[url]
    return scraper.scrape_events()


def run_cantab(lounge: dict | None = None, calls: list | None = None):
    data = {LOUNGE: lounge or fixture_json("cantab_lounge", f"shows-lounge-{STAMP}.json.gz"),
            UNDERGROUND: fixture_json("cantab_lounge", f"shows-underground-{STAMP}.json.gz")}
    scraper = BY_NAME["The Cantab Lounge"].load()
    scraper.today = TODAY

    def fetch(venue_id, starts_after, offset):
        if calls is not None:
            calls.append((venue_id, starts_after, offset))
        return data[venue_id]

    scraper.fetch_shows = fetch
    return scraper.scrape_events()


def run_burren(page: str | None = None):
    scraper = BY_NAME["The Burren"].load()
    scraper.today = TODAY
    scraper.fetch_page = lambda: page if page is not None else read_fixture("burren", f"music-{STAMP}.html.gz")
    return scraper.scrape_events()


def run_24hm(data: dict | None = None):
    payload = data or fixture_json("twenty_four_hour_music", f"performancelist-{STAMP}.json.gz")
    scraper = BY_NAME["24 Hour Music"].load()
    scraper.fetch_page = lambda page: payload
    return scraper.scrape_events()


def run_goofs(data: dict | None = None, calls: list | None = None):
    payload = data or fixture_json("goofs", f"shows-{STAMP}.json.gz")
    scraper = BY_NAME["Goofs Comedy Club"].load()
    scraper.today = TODAY

    def fetch(after):
        if calls is not None:
            calls.append(after)
        return payload

    scraper.fetch_shows = fetch
    return scraper.scrape_events()


def run_manray(page: str | None = None, calls: list | None = None):
    scraper = BY_NAME["ManRay"].load()
    scraper.fetch_page = lambda: page if page is not None else read_fixture("manray", f"monthly-calendar-{STAMP}.html.gz")

    def fetch_month(calendar_id, year, month):
        if calls is not None:
            calls.append((calendar_id, year, month))
        return read_fixture("manray", f"grid-2026-12-{STAMP}.html.gz")

    scraper.fetch_month = fetch_month
    return scraper.scrape_events()


def run_lovestruck(data: dict | None = None, calls: list | None = None):
    payload = data or fixture_json("lovestruck", f"events-v2-{STAMP}.json.gz")
    scraper = BY_NAME["Lovestruck Books"].load()
    scraper.today = TODAY

    def fetch(start, end):
        if calls is not None:
            calls.append((start, end))
        return payload

    scraper.fetch_events = fetch
    return scraper.scrape_events()


def run_pandemonium(data: dict | None = None):
    payload = data or fixture_json("pandemonium", f"schedule-{STAMP}.json.gz")
    scraper = BY_NAME["Pandemonium Books & Games"].load()
    scraper.today = TODAY
    scraper.fetch_schedule = lambda start, end: payload
    return scraper.scrape_events()


def run_all_she_wrote(page: str | None = None):
    scraper = BY_NAME["All She Wrote Books"].load()
    scraper.fetch_page = lambda: page if page is not None else read_fixture("all_she_wrote", f"events-{STAMP}.html.gz")
    return scraper.scrape_events()


RUNNERS = {
    "Club Passim": run_passim,
    "Passim Summer Series": run_passim_series,
    "The Cantab Lounge": run_cantab,
    "The Burren": run_burren,
    "24 Hour Music": run_24hm,
    "Goofs Comedy Club": run_goofs,
    "ManRay": run_manray,
    "Lovestruck Books": run_lovestruck,
    "Pandemonium Books & Games": run_pandemonium,
    "All She Wrote Books": run_all_she_wrote,
}


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted(RUNNERS))
def test_source_parses_its_fixture_clean(source_name, offline):
    events = RUNNERS[source_name]()
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.end_datetime and e.end_datetime <= e.start_datetime]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title]


# --------------------------------------------------------------------------- #
# Club Passim
# --------------------------------------------------------------------------- #

def test_passim_reads_each_performance_once_at_its_printed_show_time(offline):
    """The page repeats its calendar array once per month shown; the `start`
    field says +0000 but holds the printed local show time."""
    page = read_fixture("club_passim", f"live-music-{STAMP}.html.gz")
    assert page.count('{title: "Bruce Molsky"') > 1

    events = run_passim()
    starts = [(e.title, e.start_datetime) for e in events]
    assert len(starts) == len(set(starts))
    # Detail page: "Sunday, November 22, 2026 Doors 1PM | Show 2PM".
    assert ("Bruce Molsky", datetime(2026, 11, 22, 14, 0)) in starts
    assert ("Zachariah Hickman’s Power Outage PRE-Party Jam Session!", datetime(2026, 11, 16, 19, 30)) in starts
    assert not [e for e in events if "closed" in e.title.lower()], "'club closed' nights are not events"


def test_passim_places_shows_whose_title_names_the_room(offline):
    by_title = {e.title: e for e in run_passim()}
    tall = by_title["Tall Heights at Arrow Street Arts"]
    assert (tall.venue_name, tall.city) == ("Arrow Street Arts", "Cambridge")
    fiddle = by_title["Boston Fiddle Funk Folk Festival at The Armory"]
    assert (fiddle.venue_name, fiddle.city) == ("Arts at the Armory", "Somerville")
    assert by_title["Bruce Molsky"].venue_name == "Club Passim"


def test_passim_places_festival_nights_from_the_festival_schedule(offline):
    """BCMFest's one post carries three performances over two nights in two
    buildings; its page says 'Friday, January 15th, 8pm-10pm … @ Arts at the
    Armory'."""
    calls: list = []
    events = run_passim(calls=calls)
    festival = {e.start_datetime: e.venue_name for e in events if e.title == "Boston Celtic Music Festival"}
    assert festival == {
        datetime(2027, 1, 14, 19, 0): "Club Passim",
        datetime(2027, 1, 15, 19, 0): "Club Passim",
        datetime(2027, 1, 15, 20, 0): "Arts at the Armory",
    }
    assert calls.count(f"{SITE}/live-music/events/boston-celtic-music-festival/") == 1


def test_passim_skips_shows_it_cannot_place(offline):
    """From the July calendar: hikes sold by the Trustees name no place, and
    the Crane Estate is in Ipswich. Crystal Ballroom is in Somerville."""
    events = run_passim(main="live-music-2026-07-17-wayback.html.gz")
    titles = {e.title for e in events}
    assert not [t for t in titles if t.startswith("Scenic Songs")]
    assert not [t for t in titles if "Crane Estate" in t]
    carsie = next(e for e in events if e.title == "Carsie Blanton at Crystal Ballroom")
    assert carsie.city == "Somerville"


def test_passim_start_must_agree_with_the_printed_showtime(offline):
    scraper = BY_NAME["Club Passim"].load()
    record = {"title": "Test Show", "postID": "1", "date": "2026-12-01", "showtime": "8PM",
              "start": "2026-12-01T20:00:00+0000", "link": f"{SITE}/live-music/events/test/",
              "ticketButton": '<a href="https://passim.my.salesforce-sites.com/ticket/">Tickets</a>'}
    assert scraper.parse_record(record).start_datetime == datetime(2026, 12, 1, 20, 0)
    assert scraper.parse_record({**record, "start": "2026-12-01T19:00:00+0000"}) is None
    assert scraper.parse_record({**record, "showtime": "TBA"}) is None


# --------------------------------------------------------------------------- #
# Passim summer series
# --------------------------------------------------------------------------- #

def test_passim_series_takes_the_year_from_the_printed_weekday(offline):
    """'Wednesday Aug 26' is 2026; Palmer Street's page still shows 2025's
    'Thursday Aug 14'; Science Plaza's 'Tuesday Oct 8' (2024) matches no
    nearby year and is skipped."""
    events = run_passim_series()
    starts = {(e.venue_name, e.start_datetime) for e in events}
    assert ("Urban Park Roof Garden, Kendall Square", datetime(2026, 8, 26, 12, 0)) in starts
    assert ("Danehy Park", datetime(2026, 8, 25, 18, 0)) in starts
    assert ("Palmer Street, Harvard Square", datetime(2025, 8, 14, 17, 30)) in starts
    assert not [e for e in events if e.venue_name == "Harvard Science Center Plaza"]
    assert all(e.title.startswith("Passim Presents: ") and e.cost == "Free" for e in events)


# --------------------------------------------------------------------------- #
# The Cantab Lounge
# --------------------------------------------------------------------------- #

def test_cantab_reads_both_rooms_and_converts_utc(offline):
    calls: list = []
    events = run_cantab(calls=calls)
    assert {c[0] for c in calls} == {LOUNGE, UNDERGROUND}
    assert {c[1] for c in calls} == {"2026-11-16T05:00:00.000Z"}, "midnight Eastern, as UTC"
    assert {e.venue_name for e in events} == {"Cantab Lounge", "Cantab Underground"}
    starts = {(e.title, e.start_datetime) for e in events}
    # startsAt 2026-11-17T01:30:00Z is 8:30 PM on the 16th.
    assert ("Jazz Jam", datetime(2026, 11, 16, 20, 30)) in starts
    # "Dancehall - TIX ON EVENTBRITE" loses its ticketing note.
    assert ("Dancehall", datetime(2026, 11, 21, 22, 0)) in starts


def test_cantab_skips_a_show_on_a_non_eastern_clock(offline):
    everything = run_cantab()
    lounge = fixture_json("cantab_lounge", f"shows-lounge-{STAMP}.json.gz")
    lounge["data"][0]["timezone"] = "America/Los_Angeles"
    events = run_cantab(lounge=lounge)
    assert len(events) == len(everything) - 1
    assert not [e for e in events if e.source_url.endswith("/" + lounge["data"][0]["slug"])]


# --------------------------------------------------------------------------- #
# The Burren
# --------------------------------------------------------------------------- #

def test_burren_dates_and_times(offline):
    events = run_burren()
    starts = {(e.title, e.start_datetime, e.end_datetime) for e in events}
    # "MONDAY NOVEMBER 16" -> 2026 by weekday; "SATURDAY JANUARY 2, 2027" printed.
    assert ("IRISH SESSION", datetime(2026, 11, 16, 20, 30), None) in starts
    assert ("DOUBLE BARRELL, MILL STREET", datetime(2027, 1, 2, 19, 0), None) in starts
    # "2-5pm" is a span.
    assert ("BLUEGRASS JAM", datetime(2026, 11, 21, 14, 0), datetime(2026, 11, 21, 17, 0)) in starts
    # "5pm & 7:30pm" is two listings.
    voices = sorted(e.start_datetime for e in events if "VOICES OF IRELAND" in e.title)
    assert voices == [datetime(2026, 11, 15, 17, 0), datetime(2026, 11, 15, 19, 30)]
    assert not [e for e in events if e.title.upper() in ("TBD", "TBA")]
    assert {e.tags[0] for e in events} == {"front room", "back room"}


def test_burren_ticketed_acts_link_to_their_box_office_page(offline):
    which = next(e for e in run_burren() if e.title.startswith("WHICH SIDE?"))
    assert which.source_url == "https://tickets.24hourmusic.com/eventperformances.asp?evt=1779"
    assert which.start_datetime == datetime(2026, 11, 17, 20, 0)


def test_burren_skips_a_header_whose_weekday_contradicts_its_year(offline):
    page = """
    <table><tr><td class="HEADER">FRIDAY JANUARY 2, 2027</td></tr>
    <tr><td class="Room">THE BACK ROOM</td></tr><tr><td><span class="Time">7pm:</span></td></tr>
    <tr><td><span class="BAND">WRONG DAY</span></td></tr>
    <tr><td class="HEADER">SATURDAY JANUARY 2, 2027</td></tr>
    <tr><td class="Room">THE BACK ROOM</td></tr><tr><td><span class="Time">7pm:</span></td></tr>
    <tr><td><span class="BAND">RIGHT DAY</span></td></tr></table>"""
    assert [(e.title, e.start_datetime) for e in run_burren(page)] == [("RIGHT DAY", datetime(2027, 1, 2, 19, 0))]


@pytest.mark.parametrize("text, expected", [
    ("8:30pm:", ([(20, 30)], None)),
    ("2-5pm:", ([(14, 0)], (17, 0))),
    ("11-1pm", ([(11, 0)], (13, 0))),
    ("12-3pm", ([(12, 0)], (15, 0))),
    ("10pm-1am", ([(22, 0)], (1, 0))),
    ("5pm & 7:30pm:", ([(17, 0), (19, 30)], None)),
    ("10:30am, 12:30pm, 2:30pm, & 4:30pm:", ([(10, 30), (12, 30), (14, 30), (16, 30)], None)),
    ("7 & 9pm", ([(19, 0), (21, 0)], None)),
    ("TBA", None),
    ("7", None),
])
def test_burren_time_shapes(text, expected):
    assert parse_times(text) == expected


# --------------------------------------------------------------------------- #
# 24 Hour Music (ShoWare)
# --------------------------------------------------------------------------- #

def test_24hm_keeps_only_rooms_without_their_own_source(offline):
    data = fixture_json("twenty_four_hour_music", f"performancelist-{STAMP}.json.gz")
    assert {p["Venue"] for p in data["performance"]} >= {"The Burren", "Arts At The Armory", "Boston Harbor Distillery"}

    events = run_24hm(data)
    assert {e.venue_name for e in events} == {"McCarthy's"}
    assert {e.street_address for e in events} == {"1920 Massachusetts Avenue"}, "ShoWare doubles the address line"
    teen = next(e for e in events if e.title.startswith("Teen Singer-Songwriter Showcase"))
    assert teen.start_datetime == datetime(2026, 11, 22, 11, 0)
    assert teen.cost == "$16.95"


def test_24hm_rejects_a_stamp_whose_weekday_disagrees(offline):
    data = fixture_json("twenty_four_hour_music", f"performancelist-{STAMP}.json.gz")
    mccarthys = [p for p in data["performance"] if p["Venue"] == "McCarthy's"]
    broken = copy.deepcopy(mccarthys[0])
    broken["PerformanceDateTime"] = broken["PerformanceDateTime"].replace("Sunday", "Monday")
    broken["PerformanceID"] = -1
    events = run_24hm({**data, "performance": [broken]})
    assert events == []


# --------------------------------------------------------------------------- #
# Goofs
# --------------------------------------------------------------------------- #

def test_goofs_reads_printed_date_and_time(offline):
    calls: list = []
    events = run_goofs(calls=calls)
    assert calls == ["2026-11-15"]
    fresh = next(e for e in events if e.source_url.endswith("/p/show/2026-11-18-2000"))
    assert (fresh.title, fresh.start_datetime, fresh.cost) == ("Fresh Goofs", datetime(2026, 11, 18, 20, 0), "$20")
    assert {e.venue_name for e in events} == {"Goofs Comedy Club"}
    assert {e.category for e in events} <= {EventCategory.THEATER.value, EventCategory.LECTURES.value}


def test_goofs_skips_a_show_whose_slug_disagrees(offline):
    data = fixture_json("goofs", f"shows-{STAMP}.json.gz")
    show = {**data["shows"][0], "time": "7:00 PM"}   # slug still says 20:00
    assert run_goofs({"shows": [show]}) == []


# --------------------------------------------------------------------------- #
# ManRay
# --------------------------------------------------------------------------- #

def test_manray_uses_the_instant_not_the_halifax_clock(offline):
    """The page prints 'Karakoe Noir … 9:00 pm' on a site set to Halifax; the
    venue says 'Doors at 8pm'."""
    page = read_fixture("manray", f"monthly-calendar-{STAMP}.html.gz")
    assert 'data-timezone="America/Halifax"' in page

    events = run_manray()
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Karakoe Noir", datetime(2026, 11, 16, 20, 0)) in starts
    assert ("HEROES", datetime(2026, 10, 31, 21, 0)) in starts
    assert len(starts) == len(events), "nights drawn in two day cells appear once"


def test_manray_reads_the_month_after_the_one_shown(offline):
    calls: list = []
    events = run_manray(calls=calls)
    assert calls == [("1298", 2026, 12)]
    assert any(e.start_datetime.month == 12 for e in events)


def test_manray_skips_a_night_whose_stamp_and_epoch_disagree(offline):
    page = read_fixture("manray", f"monthly-calendar-{STAMP}.html.gz")
    tampered = page.replace('content="2026-11-02T21:00:00-04:00"', 'content="2026-11-02T21:00:00-05:00"')
    assert tampered != page
    events = run_manray(page=tampered)
    assert not [e for e in events if e.start_datetime.date() == date(2026, 11, 2)]


# --------------------------------------------------------------------------- #
# Lovestruck Books
# --------------------------------------------------------------------------- #

def test_lovestruck_wall_clock_and_all_day(offline):
    calls: list = []
    events = run_lovestruck(calls=calls)
    assert calls == [(TODAY, date(2027, 3, 16))]
    by_title = {e.title: e for e in events}
    # Matches the event's Eventbrite page: 2026-11-18 18:00–20:00 at 44 Brattle St.
    daedalus = next(e for t, e in by_title.items() if t.startswith("What Happens When AI"))
    assert (daedalus.start_datetime, daedalus.end_datetime) == (datetime(2026, 11, 18, 18, 0), datetime(2026, 11, 18, 20, 0))
    assert daedalus.venue_name == "Lovestruck Books"
    party = by_title["Mass Center for the Book Holiday Party"]
    assert party.all_day and party.start_datetime == datetime(2026, 12, 2, 0, 0)
    assert by_title["People Pleasing with Hannah Bonham-Young"].venue_name == "Online"


@pytest.mark.parametrize("location, venue", [
    ("", "Lovestruck Books"),
    ("The First Parish", "First Parish in Cambridge"),
    ("Virtual", "Online"),
    ("WBUR CitySpace", None),
    ("Encore Boston Harbor", None),
])
def test_lovestruck_places_only_what_it_knows(location, venue, offline):
    row = {"title": "Author Talk", "date": "20261201", "start_time": "19:00:00", "end_time": "21:00:00",
           "all_day": False, "location_text": location, "description": "<p>An evening with an author.</p>"}
    events = run_lovestruck({"rows": [row]})
    assert [e.venue_name for e in events] == ([venue] if venue else [])


# --------------------------------------------------------------------------- #
# Pandemonium
# --------------------------------------------------------------------------- #

def test_pandemonium_reads_store_wall_clock(offline):
    events = run_pandemonium()
    modern = sorted(e.start_datetime for e in events if e.title == "Tuesday Night Modern")
    assert modern[0] == datetime(2026, 11, 17, 18, 30)
    first = next(e for e in events if e.title == "Tuesday Night Modern")
    assert first.end_datetime == datetime(2026, 11, 17, 22, 30)
    assert first.source_url == "https://pandemoniumbooks.com/products/tuesday-night-modern"
    assert all(e.registration_required for e in events)


def test_pandemonium_skips_a_start_with_seconds(offline):
    data = fixture_json("pandemonium", f"schedule-{STAMP}.json.gz")
    item = {**data["schedule"][0], "start": [2026, 11, 17, 18, 30, 17]}
    assert run_pandemonium({"schedule": [item]}) == []


# --------------------------------------------------------------------------- #
# All She Wrote Books (Wix)
# --------------------------------------------------------------------------- #

def test_all_she_wrote_keeps_somerville_and_drops_boston(offline):
    page = read_fixture("all_she_wrote", f"events-{STAMP}.html.gz")
    assert "Boston" in page and "Dani's Queer Bar" in page

    events = run_all_she_wrote()
    assert {e.city for e in events} == {"Somerville"}
    saturday = next(e for e in events if e.title == "Small Business Saturday 2026")
    assert (saturday.start_datetime, saturday.end_datetime) == (datetime(2026, 11, 28, 11, 0), datetime(2026, 11, 28, 19, 0))
    assert saturday.source_url == "https://www.allshewrotebooks.com/event-details/small-business-saturday-2026"


def test_wix_start_must_match_the_printed_time(offline):
    page = read_fixture("all_she_wrote", f"events-{STAMP}.html.gz")
    tampered = page.replace('"startTimeFormatted":"11:00 AM"', '"startTimeFormatted":"10:00 AM"')
    assert tampered != page
    assert not [e for e in run_all_she_wrote(tampered) if e.title == "Small Business Saturday 2026"]


def test_wix_page_without_events_fails_loudly(offline):
    """The Dante Alighieri Society's events page is Wix, but its events are
    poster images; a page like that must not report a quiet zero."""
    page = '<script type="application/json" id="wix-warmup-data">{"appsWarmupData": {}}</script>'
    with pytest.raises(ValueError, match="no Wix Events data"):
        run_all_she_wrote(page)
