"""Batch 2, group D — Harvard sources.

Fixtures are the pages and feed as served on 2026-11-16:

  harvard_gazette/        the Trumba "gazette" feed, 60 days, trimmed to the
                          fields read (descriptions cut to 400 characters)
  woodberry_poetry_room/  the events page, and each card's LibCal page trimmed
                          to its JSON-LD and description
  hmsc/                   the Upcoming Programs page, and each program's page
                          trimmed to its header and body
  radcliffe_institute/    the events page
  harvard_radcliffe_orchestra/  the concerts page

The tests assert behaviour — what is kept, dropped, placed and timed — not
counts typed from a live site.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES
from src.scrapers.base_trumba import parse_location
from src.sources import BY_NAME
from tests.conftest import read_fixture

DAY = "2026-11-16"

WOODBERRY_PAGES = {
    "https://library.harvard.edu/libraries/woodberry-poetry-room/events": f"events-{DAY}.html.gz",
    **{f"https://libcal.library.harvard.edu/event/{n}": f"libcal-{n}-{DAY}.html.gz"
       for n in (17597369, 17692626, 17598286, 17687206, 17705326)},
}
HMSC_SLUGS = (
    "animal-stories-from-the-past-teen-saturdays-workshop", "fossil-day", "painting-with-plants",
    "sketching-mindfulness-2026", "the-festivals-of-drunkenness-in-the-temple-of-mut",
    "the-life-of-george-b-schaller-and-the-future-of-conservation",
)
HMSC_PAGES = {
    "https://hmsc.harvard.edu/programs/upcoming-programs/": f"upcoming-programs-{DAY}.html.gz",
    **{f"https://hmsc.harvard.edu/calendar_event/{s}/": f"event-{s}-{DAY}.html.gz" for s in HMSC_SLUGS},
}
PAGES = {
    "Woodberry Poetry Room": ("woodberry_poetry_room", WOODBERRY_PAGES),
    "Harvard Museums of Science & Culture": ("hmsc", HMSC_PAGES),
    "Radcliffe Institute": ("radcliffe_institute", {
        "https://www.radcliffe.harvard.edu/events-and-exhibitions": f"events-and-exhibitions-{DAY}.html.gz"}),
    "Harvard-Radcliffe Orchestra": ("harvard_radcliffe_orchestra", {
        "https://www.harvardradcliffeorchestra.org/concerts": f"concerts-{DAY}.html.gz"}),
}
GAZETTE = "Harvard Gazette"


def html_scraper(source_name: str, edit=lambda url, html: html):
    """A registered HTML source whose fetches are served from its fixtures."""
    module, pages = PAGES[source_name]
    scraper = BY_NAME[source_name].load()
    scraper.requested = []

    def fetch(url, *a, **k):
        scraper.requested.append(url)
        return edit(url, read_fixture(module, pages[url]))

    scraper.fetch_html = fetch  # type: ignore[method-assign]
    return scraper


def gazette_items() -> list:
    return json.loads(read_fixture("harvard_gazette", f"feed-{DAY}.json.gz"))


class _Response:
    def __init__(self, body):
        self._body, self.status_code = body, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def scrape_gazette(monkeypatch, items=None, calls=None):
    items = gazette_items() if items is None else items

    def fake_get(url, params=None, **kwargs):
        if calls is not None:
            calls.append((url, dict(params or {})))
        return _Response(items)

    monkeypatch.setattr(requests, "get", fake_get)
    return BY_NAME[GAZETTE].load().scrape_events()


def scrape(source_name: str, monkeypatch):
    if source_name == GAZETTE:
        return scrape_gazette(monkeypatch)
    return html_scraper(source_name).scrape_events()


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted([*PAGES, GAZETTE]))
def test_source_parses_clean(source_name, monkeypatch, offline):
    events = scrape(source_name, monkeypatch)
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES for e in events), {e.city for e in events}
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert all(e.start_datetime.tzinfo is None for e in events)
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title or "<" in e.title]


def test_registry_kinds():
    assert BY_NAME[GAZETTE].kind == "aggregator", "it lists other venues' events; venues must win dedup"
    for name in PAGES:
        assert BY_NAME[name].kind == "requests"


# --------------------------------------------------------------------------- #
# Harvard Gazette (Trumba)
# --------------------------------------------------------------------------- #

def test_gazette_asks_for_a_fixed_window(monkeypatch, offline):
    calls = []
    scrape_gazette(monkeypatch, calls=calls)
    assert calls == [("https://www.trumba.com/calendars/gazette.json", {"days": 60})]


def test_gazette_keeps_occurrences_and_drops_exhibitions_and_runs(monkeypatch, offline):
    titles = {e.title for e in scrape_gazette(monkeypatch)}
    # Exhibitions, one item per open day: classified, or unclassified but daily 9-5.
    for run in ("Rubies", "Alfonso Ossorio: Line and Lineage", "Pop Quiz: 1776", "50 Shades of Modern",
                "From Iran: A Visual Testimony"):
        assert run not in titles
    # A.R.T.'s shows, one all-day item per day of the run
    assert "Mexodus" not in titles and "1972" not in titles
    # Occurrences, including ones listed under an exhibition or repeated often
    assert "Curator-Led Tour: Cooking Up Change" in titles
    assert "Peabody Museum Tours Led by Harvard Students" in titles
    assert "Morning Prayers" in titles
    assert "Midwinter Revels 2026: American Crossroads - A Solstice Celebration" in titles


def test_gazette_drops_events_not_open_to_the_public(monkeypatch, offline):
    items = gazette_items()
    keyed = {i["title"] for i in items
             if any(f["label"] == "Harvard Key Required" and f["value"] == "Yes" for f in i["customFields"])}
    huid = {i["title"] for i in items if "open to HUID holders only" in (i["description"] or "")}
    assert keyed and huid, "fixture should include both kinds of restriction"

    titles = {e.title for e in scrape_gazette(monkeypatch, items)}
    assert not (keyed | huid) & titles


def test_gazette_keeps_cambridge_and_online_and_drops_elsewhere(monkeypatch, offline):
    events = scrape_gazette(monkeypatch)
    titles = {e.title for e in events}
    # Longwood (Boston), Allston (Boston), Concord
    assert "BIDMC Cancer Center Symposium" not in titles
    assert "Harvard Climate and Sustainability Career Expo" not in titles
    assert not [t for t in titles if t.startswith("Whose Stories Does the Landscape Hold?")]
    online = [e for e in events if e.venue_name == "Online"]
    assert online and {e.city for e in online} == {"Cambridge"}
    cswr = [e for e in events if e.venue_name == "Common Room, CSWR"]
    assert cswr and {(e.street_address, e.city) for e in cswr} == {("42 Francis Ave.", "Cambridge")}


def test_gazette_times_are_the_printed_wall_clock(monkeypatch, offline):
    """'Monday, November 16, 2026, 12 – 1:30pm EST' and an evening that must not
    cross into the next day."""
    by_title = {}
    for e in scrape_gazette(monkeypatch):
        by_title.setdefault(e.title, []).append(e)
    talk = by_title["Public Research Talk: Transcendentalism, Memorialization, and Harvard's Slavery History"][0]
    assert (talk.start_datetime, talk.end_datetime) == (datetime(2026, 11, 16, 12, 0), datetime(2026, 11, 16, 13, 30))
    assert datetime(2026, 12, 11, 19, 0) in {
        e.start_datetime for e in by_title["Midwinter Revels 2026: American Crossroads - A Solstice Celebration"]}
    maths = by_title["Current Developments in Mathematics 2026"][0]
    assert maths.all_day and maths.start_datetime == datetime(2026, 11, 19, 0, 0)


def test_gazette_skips_items_in_another_zone_or_with_a_contradicted_date(monkeypatch, offline):
    items = gazette_items()
    target = next(i for i in items if i["title"] == "Nowhere to Go")
    elsewhere = dict(target, startTimeZoneOffset="+0000")
    moved = dict(target, startDateTime="2026-11-17T19:00:00")   # still printed as Monday the 16th
    for bad in (elsewhere, moved):
        titles = {e.title for e in scrape_gazette(monkeypatch, [bad])}
        assert "Nowhere to Go" not in titles
    assert {e.title for e in scrape_gazette(monkeypatch, [target])} == {"Nowhere to Go"}


def test_gazette_category_falls_back_to_its_classification(monkeypatch, offline):
    by_title = {e.title: e for e in scrape_gazette(monkeypatch)}
    assert by_title["Nowhere to Go"].category == EventCategory.FILM.value           # Film
    assert by_title["Morning Prayers"].category == EventCategory.COMMUNITY.value    # Religion before Music


@pytest.mark.parametrize("raw, expected", [
    ('<a href="http://maps.google.com/?q=32+Quincy+St%2C+Cambridge%2C+MA+02138" target="_blank">'
     'Harvard Art Museums<br />32 Quincy St.<br />Cambridge</a>',
     ("Harvard Art Museums", "32 Quincy St.", "Cambridge", "02138")),
    ("Peabody Museum of Archaeology &amp; Ethnology<br />11 Divinity Ave<br />Cambridge",
     ("Peabody Museum of Archaeology & Ethnology", "11 Divinity Ave", "Cambridge", None)),
    ("Goodwin Forum, Main Library<br />129 Main Street, Concord, MA, 01742",
     ("Goodwin Forum, Main Library", "129 Main Street", "Concord", "01742")),
    ('<a href="http://maps.google.com/?q=1557+Massachusetts+Ave+4th+Floor%2C+Cambridge%2C+MA+02138">'
     '4th Floor<br />1557 Massachusetts Ave<br />Cambridge</a>',
     ("4th Floor", "1557 Massachusetts Ave", "Cambridge", "02138")),
    # Radcliffe's hybrid rooms: named alone, as its own source names them, so the two merge
    ('<a href="http://maps.google.com/?q=18+Mason+St%2C+Cambridge%2C+MA+02138">'
     'Knafel Center OR Online on Zoom<br />10 Garden St. <br />Cambridge</a>',
     ("Knafel Center", "10 Garden St.", "Cambridge", "02138")),
    ("Location details will be shared with registrants.",
     ("Location details will be shared with registrants.", None, None, None)),
    ("", (None, None, None, None)),
])
def test_trumba_location(raw, expected):
    assert parse_location(raw) == expected


# --------------------------------------------------------------------------- #
# Woodberry Poetry Room
# --------------------------------------------------------------------------- #

def test_woodberry_dates_come_from_libcal_and_match_the_cards(offline):
    scraper = html_scraper("Woodberry Poetry Room")
    events = scraper.scrape_events()
    assert scraper.requested == list(WOODBERRY_PAGES)
    when = {e.title: (e.start_datetime, e.end_datetime, e.venue_name) for e in events}
    assert when["WAX POETICS: Shane McCrae on John Berryman"] == (
        datetime(2026, 11, 18, 18, 0), datetime(2026, 11, 18, 19, 30), "Forum Room, Lamont Library")
    assert when["VOCARIUM READING SERIES: Brandon Shimoda & Mai Der Vang"][0] == datetime(2026, 12, 3, 18, 0)
    # A card naming the room itself gets its address
    moon = next(e for e in events if e.title.startswith("MOON COUNTRY"))
    assert (moon.venue_name, moon.street_address, moon.city) == ("Woodberry Poetry Room", "11 Quincy Street", "Cambridge")
    ccva = next(e for e in events if e.title.startswith("ORAL HISTORY"))
    assert (ccva.venue_name, ccva.street_address) == ("CCVA Auditorium", "24 Quincy Street")


def test_woodberry_card_that_disagrees_with_libcal_is_skipped(offline):
    def edit(url, html):
        if url.endswith("17687206"):
            return html.replace("2026-11-18T18:00:00-05:00", "2026-11-19T18:00:00-05:00")
        return html
    titles = {e.title for e in html_scraper("Woodberry Poetry Room", edit).scrape_events()}
    assert "WAX POETICS: Shane McCrae on John Berryman" not in titles
    assert len(titles) == len(WOODBERRY_PAGES) - 2


def test_woodberry_card_without_a_libcal_link_is_skipped_not_dated(offline):
    def edit(url, html):
        return html.replace('href="https://libcal.library.harvard.edu/event/17705326"', 'href="/poetry"')
    scraper = html_scraper("Woodberry Poetry Room", edit)
    titles = {e.title for e in scraper.scrape_events()}
    assert not [t for t in titles if "Brandon Shimoda" in t]
    assert "https://libcal.library.harvard.edu/event/17705326" not in scraper.requested


# --------------------------------------------------------------------------- #
# Harvard Museums of Science & Culture
# --------------------------------------------------------------------------- #

def test_hmsc_reads_listing_dates_and_program_locations(offline):
    scraper = html_scraper("Harvard Museums of Science & Culture")
    events = {e.title: e for e in scraper.scrape_events()}
    assert len(events) == len(HMSC_SLUGS)
    mut = events["The Festivals of Drunkenness in the Temple of Mut"]
    assert (mut.start_datetime, mut.end_datetime) == (datetime(2026, 11, 18, 18, 0), datetime(2026, 11, 18, 19, 0))
    assert (mut.venue_name, mut.street_address, mut.city, mut.zip_code) == (
        "Geological Lecture Hall", "24 Oxford Street", "Cambridge", "02138")
    assert mut.category == EventCategory.LECTURES.value and mut.cost == "Free"
    teens = events["Animal Stories from the Past: Teen Saturdays Workshop"]
    assert teens.start_datetime == datetime(2026, 12, 12, 13, 0)
    assert teens.venue_name == "Peabody Museum of Archaeology & Ethnology"
    assert events["Sketching & Mindfulness"].venue_name == "Online"       # "Online Workshop", no Location
    assert events["Painting with Plants"].cost == "$45 members / $55 nonmembers"


def test_hmsc_program_whose_page_disagrees_with_the_listing_is_skipped(offline):
    def edit(url, html):
        if url.endswith("/fossil-day/"):
            return html.replace("November 21, 2026", "November 22, 2026")
        return html
    titles = {e.title for e in html_scraper("Harvard Museums of Science & Culture", edit).scrape_events()}
    assert "Fossil Day" not in titles and len(titles) == len(HMSC_SLUGS) - 1


# --------------------------------------------------------------------------- #
# Radcliffe Institute
# --------------------------------------------------------------------------- #

def test_radcliffe_skips_exhibitions_and_places_hybrid_events_at_the_room(offline):
    events = html_scraper("Radcliffe Institute").scrape_events()
    by_title = {e.title: e for e in events}
    exhibitions = ("Cooking Up Change: Women’s Agency and Community Building Through Cookbooks",
                   "Carmen Winant: Double Jeopardy")
    assert not set(exhibitions) & set(by_title)
    autor = by_title["The Expertise Economy"]
    assert autor.start_datetime == datetime(2026, 12, 3, 16, 0)
    assert (autor.venue_name, autor.street_address, autor.city, autor.zip_code) == (
        "Knafel Center", "10 Garden Street", "Cambridge", "02138")
    hagwon = by_title["American Hagwon: Novelist Min Jin Lee Goes Back to School"]
    assert hagwon.venue_name == "Online" and hagwon.start_datetime == datetime(2026, 11, 19, 16, 0)
    assert by_title["Radcliffe Day 2027"].start_datetime == datetime(2027, 5, 28, 10, 0)
    assert by_title["Curator-Led Tour: Cooking Up Change"].category == EventCategory.ARTS_CULTURE.value


def test_radcliffe_drops_road_events_and_contradicted_weekdays(offline):
    def edit(url, html):
        html = html.replace("Monday, February 1, 2027", "Tuesday, February 1, 2027")   # Next in Quantum Computing
        return html.replace("8 Garden Street <br/> Cambridge, MA 02138", "8 Garden Street <br/> San Francisco, CA 94108")
    titles = {e.title for e in html_scraper("Radcliffe Institute", edit).scrape_events()}
    assert "Next in Quantum Computing" not in titles
    assert not [t for t in titles if t.startswith("Pre-Lecture Curator-Led Tour")], "moved to San Francisco"
    assert "The Expertise Economy" in titles


# --------------------------------------------------------------------------- #
# Harvard-Radcliffe Orchestra
# --------------------------------------------------------------------------- #

def test_hro_concerts_at_sanders(offline):
    events = html_scraper("Harvard-Radcliffe Orchestra").scrape_events()
    assert {(e.title, e.start_datetime) for e in events} >= {
        ("Harvard-Radcliffe Orchestra: Season 219 Concert 3", datetime(2027, 3, 6, 20, 0)),
        ("Harvard-Radcliffe Orchestra: Season 219 Concert 4", datetime(2027, 4, 24, 20, 0)),
    }
    assert {(e.venue_name, e.city) for e in events} == {("Sanders Theatre", "Cambridge")}
    don = next(e for e in events if e.title.endswith("Concert 3"))
    assert "Mozart" in don.description and "Don Giovanni" in don.description


def test_hro_concert_elsewhere_is_not_placed_at_home(offline):
    def edit(url, html):
        return html.replace("April 24, 2027<br/>8:00pm<br/>Sanders Theatre",
                            "April 24, 2027<br/>8:00pm<br/>Jordan Hall")
    titles = {e.title for e in html_scraper("Harvard-Radcliffe Orchestra", edit).scrape_events()}
    assert "Harvard-Radcliffe Orchestra: Season 219 Concert 4" not in titles
    assert "Harvard-Radcliffe Orchestra: Season 219 Concert 3" in titles
