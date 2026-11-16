"""Batch 2, group B: WordPress music and arts sources that run no events plugin.

Each source reads its own site's pages or feeds as served on 2026-11-16 (page
chrome — scripts, styles — stripped; Hub Comics' posts trimmed to the fields
read). Tests assert behaviour — what is kept, dropped, placed, timed — and
derive anything countable from the fixture rather than typing it.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pytest
import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.scrapers.base_printed_date import clock, year_for_weekday
from src.sources import BY_NAME
from tests.conftest import read_fixture

DAY = "2026-11-16"

# source name -> {URL the scraper requests: (fixture module, fixture file)}
ROUTES = {
    "Boston Early Music Festival": {
        "https://bemf.org/concert-season/": ("bemf", f"season-{DAY}.html.gz"),
        "https://bemf.org/concert-season/concert-venues/": ("bemf", f"venues-{DAY}.html.gz"),
    },
    "Cambridge Society for Early Music": {
        "https://csem.org/concerts/": ("csem", f"concerts-{DAY}.html.gz"),
        "https://csem.org/concerts/trio-langostino/": ("csem", f"trio-langostino-{DAY}.html.gz"),
        "https://csem.org/concerts/solace/": ("csem", f"solace-{DAY}.html.gz"),
        "https://csem.org/concerts/schubertiade/": ("csem", f"schubertiade-{DAY}.html.gz"),
    },
    "Blues Union Boston": {
        "https://calendar.google.com/calendar/ical/bluesdanceboston%40gmail.com/public/basic.ics":
            ("blues_union", f"calendar-{DAY}.ics.gz"),
    },
    "Dave Bryant Third Thursdays": {
        "https://dbryantmusic.com/?feed=gigpress": ("dave_bryant", f"gigpress-{DAY}.xml.gz"),
    },
    "NESFA": {
        "https://www.nesfa.org/events/": ("nesfa", f"events-{DAY}.html.gz"),
    },
    "Hub Comics": {
        "https://hubcomics.com/wp-json/wp/v2/categories": ("hub_comics", f"categories-{DAY}.json.gz"),
        "https://hubcomics.com/wp-json/wp/v2/posts": ("hub_comics", f"posts-{DAY}.json.gz"),
    },
    "Crystal Ballroom": {
        "https://www.crystalballroomboston.com/events/": ("crystal_ballroom", f"events-{DAY}.html.gz"),
    },
}


def fixture(source_name: str, url: str) -> str:
    return read_fixture(*ROUTES[source_name][url])


class _Response:
    def __init__(self, text: str, status: int = 200):
        self.text, self.status_code = text, status
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.text)


def scrape(source_name: str, monkeypatch, edit=None):
    """Run a registered source offline over its fixtures; return (events, calls).

    `edit(url, text) -> text` alters a served page, for tests of one rule.
    """
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        if url not in ROUTES[source_name]:
            return _Response("", 404)
        text = fixture(source_name, url)
        return _Response(edit(url, text) if edit else text)

    monkeypatch.setattr(requests, "get", fake_get)
    return BY_NAME[source_name].load().scrape_events(), calls


def by_title(events):
    return {e.title: e for e in events}


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted(ROUTES))
def test_source_parses_clean(source_name, monkeypatch, offline):
    events, calls = scrape(source_name, monkeypatch)
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    source = BY_NAME[source_name]
    assert source.kind == "requests"
    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES and e.state == "MA" for e in events)
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.end_datetime and e.end_datetime <= e.start_datetime]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title]
    assert {url for url, _ in calls} <= set(ROUTES[source_name]), "requested a page with no fixture"


# --------------------------------------------------------------------------- #
# Boston Early Music Festival
# --------------------------------------------------------------------------- #

def test_bemf_keeps_cambridge_performances_only(monkeypatch, offline):
    events, _ = scrape("Boston Early Music Festival", monkeypatch)
    starts = {(e.title.split(":")[0], e.start_datetime) for e in events}
    assert ("The Tallis Scholars", datetime(2026, 12, 4, 19, 30)) in starts
    assert ("Ensemble Correspondances", datetime(2026, 12, 13, 16, 0)) in starts
    # Jordan Hall (Boston) and Troy, NY are on the page; neither is kept.
    page = fixture("Boston Early Music Festival", "https://bemf.org/concert-season/")
    assert "Jordan Hall" in page and "Troy, NY" in page
    assert not [e for e in events if "Pyramus" in e.title or "Faust" in e.title]
    assert {e.city for e in events} == {"Cambridge"}


def test_bemf_reads_addresses_from_its_venue_list(monkeypatch, offline):
    """The season page prints 'First Church in Cambridge, Congregational' with no
    city; BEMF's venue list gives the address."""
    events, _ = scrape("Boston Early Music Festival", monkeypatch)
    first_church = [e for e in events if e.venue_name.startswith("First Church in Cambridge")]
    assert first_church and {e.street_address for e in first_church} == {"11 Garden Street"}


def test_bemf_skips_virtual_premieres(monkeypatch, offline):
    events, _ = scrape("Boston Early Music Festival", monkeypatch)
    # Tallis Scholars stream from Fri Dec 11, 2026 at 8pm.
    assert datetime(2026, 12, 11, 20, 0) not in {e.start_datetime for e in events}
    assert not [e for e in events if "Les Arts Florissants" in e.title]


def test_bemf_files_every_program_as_music(monkeypatch, offline):
    """'A Musical Journey' in a title reads as theater to base_feed."""
    events, _ = scrape("Boston Early Music Festival", monkeypatch)
    assert {e.category for e in events} == {EventCategory.MUSIC.value}
    assert [e for e in events if "Musical Journey" in e.title]


def test_bemf_skips_a_date_whose_weekday_disagrees(monkeypatch, offline):
    def typo(url, text):
        return text.replace("FRIDAY, DECEMBER 4, 2026", "SATURDAY, DECEMBER 4, 2026")

    events, _ = scrape("Boston Early Music Festival", monkeypatch, edit=typo)
    assert not [e for e in events if e.title.startswith("The Tallis Scholars")]
    assert [e for e in events if e.title.startswith("Ensemble Correspondances")]


# --------------------------------------------------------------------------- #
# Cambridge Society for Early Music
# --------------------------------------------------------------------------- #

def test_csem_reads_each_program_page_and_keeps_cambridge_nights(monkeypatch, offline):
    events, calls = scrape("Cambridge Society for Early Music", monkeypatch)
    assert {url for url, _ in calls} == set(ROUTES["Cambridge Society for Early Music"])
    assert {e.city for e in events} == {"Cambridge"}
    tavern = by_title(events)["Tiny Glass Tavern: 'A Pilgrim's Solace'"]
    assert tavern.start_datetime == datetime(2027, 1, 15, 19, 30)
    assert tavern.street_address == "11 Garden Street"
    # The Schubertiade plays Beverly Farms, Boston and Weston only.
    assert not [e for e in events if "Schubertiade" in e.title]
    # Weston and Beverly Farms nights of the same programs are dropped.
    assert len({e.source_url for e in events}) == len(events)


def test_csem_fails_rather_than_publishing_part_of_the_season(monkeypatch, offline):
    """A program page that does not load holds the only copy of its dates; a
    partial result would delete them, a failed run preserves them."""
    routes = ROUTES["Cambridge Society for Early Music"]
    monkeypatch.setitem(ROUTES, "Cambridge Society for Early Music",
                        {u: f for u, f in routes.items() if "solace" not in u})
    with pytest.raises(requests.HTTPError):
        scrape("Cambridge Society for Early Music", monkeypatch)


# --------------------------------------------------------------------------- #
# Blues Union Boston
# --------------------------------------------------------------------------- #

BLUES_ICS = ROUTES["Blues Union Boston"]


def test_blues_union_expands_thursdays_with_the_organizers_exceptions(monkeypatch, offline):
    events, _ = scrape("Blues Union Boston", monkeypatch)
    starts = [e.start_datetime for e in events]
    assert all(s.weekday() == 3 and (s.hour, s.minute) == (19, 0) for s in starts)
    assert all(e.end_datetime.hour == 23 for e in events)
    # EXDATE for Thanksgiving.
    raw = fixture("Blues Union Boston", next(iter(BLUES_ICS)))
    assert "EXDATE;TZID=America/New_York:20261126T190000" in raw
    assert datetime(2026, 11, 26, 19, 0) not in starts
    assert datetime(2026, 11, 19, 19, 0) in starts


def test_blues_union_window_comes_from_the_feed_not_the_clock(monkeypatch, offline):
    """Expansion runs from the feed's DTSTAMP (20261116T163624Z), so the saved
    feed gives the same dates on any day the test runs."""
    from src.scrapers.blues_union import LOOKAHEAD_DAYS, LOOKBACK_DAYS

    feed_time = datetime(2026, 11, 16, 11, 36, 24)
    events, _ = scrape("Blues Union Boston", monkeypatch)
    starts = [e.start_datetime for e in events]
    assert min(starts) >= feed_time - timedelta(days=LOOKBACK_DAYS)
    assert max(starts) <= feed_time + timedelta(days=LOOKAHEAD_DAYS)


def test_blues_union_drops_a_night_the_organizers_call_off(monkeypatch, offline):
    """Breaks are entered as replacement entries titled 'No Blues Union ...'."""
    raw = fixture("Blues Union Boston", next(iter(BLUES_ICS)))
    master = next(b for b in raw.split("BEGIN:VEVENT")
                  if re.search(r"^RRULE:FREQ=WEEKLY;BYDAY=TH\r?$", b, re.M)
                  and re.search(r"^SUMMARY:Blues Union\r?$", b, re.M))
    master_uid = re.search(r"^UID:(\S+)", master, re.M).group(1)
    notice = ("BEGIN:VEVENT\r\nDTSTART;TZID=America/New_York:20261203T190000\r\n"
              "DTEND;TZID=America/New_York:20261203T230000\r\nDTSTAMP:20261116T163624Z\r\n"
              f"UID:{master_uid}\r\nRECURRENCE-ID;TZID=America/New_York:20261203T190000\r\n"
              "LOCATION:16 Bow St\\, Somerville\\, MA 02143\\, USA\r\n"
              "SUMMARY:No Blues Union - holiday break\r\nEND:VEVENT\r\n")

    events, _ = scrape("Blues Union Boston", monkeypatch,
                       edit=lambda url, text: text.replace("END:VCALENDAR", notice + "END:VCALENDAR"))
    starts = {e.start_datetime for e in events}
    assert datetime(2026, 12, 3, 19, 0) not in starts
    assert datetime(2026, 12, 10, 19, 0) in starts
    assert not [e for e in events if e.title.lower().startswith("no ")]


def test_blues_union_reads_only_its_own_room(monkeypatch, offline):
    """The calendar also carries Bluesy Tuesy and a monthly Burren night."""
    raw = fixture("Blues Union Boston", next(iter(BLUES_ICS)))
    assert "SUMMARY:Bluesy Tuesy" in raw and "The Burren" in raw
    events, _ = scrape("Blues Union Boston", monkeypatch)
    assert {e.street_address for e in events} == {"16 Bow Street"}
    assert {e.title for e in events} == {"Blues Union"}


# --------------------------------------------------------------------------- #
# Dave Bryant Third Thursdays
# --------------------------------------------------------------------------- #

GIGPRESS = "https://dbryantmusic.com/?feed=gigpress"


def test_dave_bryant_reads_printed_time_not_the_utc_stamp(monkeypatch, offline):
    """pubDate says 20:00 +0000 — 3 PM Eastern. The show is at 8 PM."""
    assert "20:00:00 +0000" in fixture("Dave Bryant Third Thursdays", GIGPRESS)
    events, _ = scrape("Dave Bryant Third Thursdays", monkeypatch)
    show = by_title(events)["Third Thursdays: Dave Bryant"]
    assert show.start_datetime == datetime(2026, 11, 19, 20, 0)
    assert (show.venue_name, show.street_address, show.zip_code) == \
        ("Harvard-Epworth Church", "1555 Massachusetts Ave", "02138")
    assert show.cost == "$10"


def test_dave_bryant_drops_gigs_outside_the_area(monkeypatch, offline):
    events, _ = scrape("Dave Bryant Third Thursdays", monkeypatch,
                       edit=lambda url, text: re.sub(r"Cambridge, MA(?=</li>|\s)", "Boston, MA", text))
    assert events == []


def test_dave_bryant_skips_a_date_whose_weekday_disagrees(monkeypatch, offline):
    events, _ = scrape("Dave Bryant Third Thursdays", monkeypatch,
                       edit=lambda url, text: text.replace("Thursday, November 19th 2026", "Friday, November 19th 2026"))
    assert events == []


# --------------------------------------------------------------------------- #
# NESFA
# --------------------------------------------------------------------------- #

def test_nesfa_keeps_only_clubhouse_entries(monkeypatch, offline):
    events, _ = scrape("NESFA", monkeypatch)
    titles = {e.title for e in events}
    page = fixture("NESFA", "https://www.nesfa.org/events/")
    for elsewhere in ("Philcon 2026", "The Somerville Sci-Fi/Fantasy Book Club", "New Years Eve Party/OM",
                      "Boskone Meeting (No Game Day Today)", "Boskone 64"):
        assert elsewhere in BeautifulSoup(page, "html.parser").get_text()
        assert elsewhere not in titles
    assert {(e.venue_name, e.street_address, e.city) for e in events} == \
        {("NESFA Clubhouse", "504 Medford Street", "Somerville")}


def test_nesfa_skips_convention_work_sessions(monkeypatch, offline):
    events, _ = scrape("NESFA", monkeypatch)
    assert not [e for e in events if "work session" in e.title.lower()]


def test_nesfa_times(monkeypatch, offline):
    events, _ = scrape("NESFA", monkeypatch)
    starts = {(e.title, e.start_datetime): e for e in events}
    tuesy = starts[("Bluesy Tuesy", datetime(2026, 11, 17, 19, 30))]
    assert tuesy.end_datetime == datetime(2026, 11, 17, 22, 30)
    # "12:00 Noon to whenever"
    library = starts[("Open Library and Social Hours", datetime(2026, 11, 18, 12, 0))]
    assert library.end_datetime is None
    # "12:00 Noon to 10:00" — an end with no meridiem is dropped, not guessed.
    movie = starts[("Turkey Fest Movie Day", datetime(2026, 11, 28, 12, 0))]
    assert movie.end_datetime is None


@pytest.mark.parametrize("text, expected", [
    ("Tuesday, November 17, 2026 • 7:00pm to 9:00pm",
     (datetime(2026, 11, 17, 19), datetime(2026, 11, 17, 21), False)),
    ("Friday, February 12 - Sunday, February 14, 2027", (datetime(2027, 2, 12), None, True)),
    ("Thursday, December 31 - Thursday, December 31, 2026 • 6:00pm to whenever",
     (datetime(2026, 12, 31, 18), None, False)),
    # A range across New Year prints only the end's year.
    ("Thursday, December 31 - Saturday, January 2, 2027", (datetime(2026, 12, 31), None, True)),
    # Friday puts Dec 31 in 2027, after the range ends: not a date.
    ("Friday, December 31 - Saturday, January 2, 2027", None),
    ("Saturday, November 17, 2026 • 7:00pm to 9:00pm", None),      # Nov 17, 2026 is a Tuesday
    ("Sometime soon", None),
])
def test_nesfa_when(text, expected):
    from src.scrapers.nesfa import NESFAScraper
    assert NESFAScraper().when(text) == expected


# --------------------------------------------------------------------------- #
# Hub Comics
# --------------------------------------------------------------------------- #

def test_hub_comics_finds_the_events_category_then_its_posts(monkeypatch, offline):
    _, calls = scrape("Hub Comics", monkeypatch)
    category = json.loads(fixture("Hub Comics", "https://hubcomics.com/wp-json/wp/v2/categories"))[0]
    assert calls[0] == ("https://hubcomics.com/wp-json/wp/v2/categories", {"slug": "events"})
    assert calls[1][0] == "https://hubcomics.com/wp-json/wp/v2/posts"
    assert calls[1][1]["categories"] == category["id"]


def test_hub_comics_dates_from_prose(monkeypatch, offline):
    events, _ = scrape("Hub Comics", monkeypatch)
    titles = by_title(events)
    # Posted 2026-11-12: "Join us Wednesday, December 9th at 7pm!"
    assert titles["BOOK CLHUB – December 2026"].start_datetime == datetime(2026, 12, 9, 19, 0)
    # Posted 2025-12-11 about "Wednesday, January 21st": the year rolls over.
    assert titles["BOOK CLHUB – January 2026 / new date!"].start_datetime.date() == date(2026, 1, 21)
    # "on Sunday, May 31st from 2pm to 4pm"
    signing = titles["Jared Sarnie + Violet Kitchen – May 31st"]
    assert (signing.start_datetime, signing.end_datetime) == (datetime(2026, 5, 31, 14), datetime(2026, 5, 31, 16))
    # "Saturday, May 2nd" three times, with no time beside it: one all-day listing.
    fcbd = titles["Free Comic Book Day 2026 – May 2nd!"]
    assert fcbd.all_day and fcbd.start_datetime == datetime(2026, 5, 2)


def test_hub_comics_skips_a_post_with_two_dates():
    from src.scrapers.hub_comics import HubComicsScraper
    post = {"title": {"rendered": "Signing"}, "date": "2026-11-01T10:00:00", "link": "https://hubcomics.com/x/",
            "content": {"rendered": "<p>Pre-order by Friday, November 20th. Signing Saturday, December 5th at 2pm.</p>"}}
    assert HubComicsScraper().parse_post(post) is None


def test_hub_comics_skips_a_weekday_that_fits_no_year():
    from src.scrapers.hub_comics import HubComicsScraper
    # December 9 is a Wednesday in 2026 and a Thursday in 2027 — never a Monday.
    post = {"title": {"rendered": "Book club"}, "date": "2026-11-12T08:00:00", "link": "https://hubcomics.com/y/",
            "content": {"rendered": "<p>Join us Monday, December 9th at 7pm!</p>"}}
    assert HubComicsScraper().parse_post(post) is None


# --------------------------------------------------------------------------- #
# Crystal Ballroom
# --------------------------------------------------------------------------- #

CRYSTAL = "https://www.crystalballroomboston.com/events/"


def test_crystal_ballroom_reads_every_listing_but_passes(monkeypatch, offline):
    from src.scrapers.crystal_ballroom import TICKET_ONLY

    soup = BeautifulSoup(fixture("Crystal Ballroom", CRYSTAL), "html.parser")
    titles = [a.get_text(strip=True) for a in soup.select("#event-listings article.event .entry-title a")]
    passes = [t for t in titles if TICKET_ONLY.search(t)]
    assert passes, "fixture should include the BCMFest festival pass"

    events, _ = scrape("Crystal Ballroom", monkeypatch)
    assert len(events) == len(titles) - len(passes)
    assert not [e for e in events if TICKET_ONLY.search(e.title)]


def test_crystal_ballroom_times(monkeypatch, offline):
    events, _ = scrape("Crystal Ballroom", monkeypatch)
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Rucks N’ Tucks", datetime(2026, 11, 21, 20, 0)) in starts
    assert ("Small Mart", datetime(2026, 11, 21, 11, 0)) in starts
    assert ("The Sweetback Sisters", datetime(2026, 12, 19, 20, 0)) in starts
    assert not [e for e in events if e.image_url and "no-photo" in e.image_url]


def test_crystal_ballroom_skips_a_date_whose_weekday_disagrees(monkeypatch, offline):
    events, _ = scrape("Crystal Ballroom", monkeypatch,
                       edit=lambda url, text: text.replace("Sat, Dec 19, 2026", "Fri, Dec 19, 2026"))
    assert "The Sweetback Sisters" not in {e.title for e in events}


# --------------------------------------------------------------------------- #
# base_printed_date
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, expected", [
    ("7:30pm", (19, 30)), ("8PM", (20, 0)), ("7 p.m.", (19, 0)), ("12:00 Noon", (12, 0)),
    ("noon", (12, 0)), ("12:30am", (0, 30)), ("Show 11:00 am", (11, 0)),
    ("10:00", None), ("8 amazing bands", None), ("", None),
])
def test_clock(text, expected):
    assert clock(text) == expected


def test_year_for_weekday_needs_exactly_one_match():
    assert year_for_weekday(12, 9, 2, (2026, 2027)) == 2026       # Wednesday
    assert year_for_weekday(12, 9, 0, (2026, 2027)) is None       # Monday in neither
