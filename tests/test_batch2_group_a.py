"""Batch 2, group A: sources on Squarespace sites whose events live somewhere else.

Each site's events page is a Squarespace page that embeds, or links to, the
real calendar. The fixtures are those calendars' feeds as served on
2026-11-16, trimmed to the fields the scrapers read:

  Arrow Street Arts               Tockify iCalendar feed
  Bow Market                      eventscalendar.co settings + events JSON
  Union Square Main Streets       eventscalendar.co settings + events JSON
  The Jungle                      SociableKit's Eventbrite feed JSON
  Spectrum Singers                OvationTix Production + CalendarProductions
  McCarthy's Toad                 Elfsight widget boot JSON
  Native Plant Community Gardeners  Squarespace events JSON
  Sarasa Ensemble                 home page link, season page, program pages
  Non-Event                       Squarespace blog JSON (its Cambridge posts are past)

Native Plant Community Gardeners had nothing upcoming (it is seasonal), so its
recent past events are served to it as upcoming. Recapture with the same shape
if a feed changes; the tests assert behaviour, not counts typed from a site.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
import requests

from src.quality.invariants import check_invariants
from src.scrapers.base_eventscalendar import local_city, venue_and_street
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.sources import BY_NAME
from tests.conftest import read_fixture

FIXTURE_DAY = datetime(2026, 11, 16)

SOURCES = {
    "Arrow Street Arts": ("arrow_street_arts", "ics"),
    "Bow Market": ("bow_market", "json"),
    "Union Square Main Streets": ("union_square_main_streets", "json"),
    "The Jungle": ("jungle", "json"),
    "Spectrum Singers": ("spectrum_singers", "json"),
    "McCarthy's Toad": ("mccarthys_toad", "json"),
    "Native Plant Community Gardeners": ("native_plant_gardeners", "json"),
    "Sarasa Ensemble": ("sarasa", "json"),
    "Non-Event": ("nonevent", "json"),
}
# Served from its recent past events: within the invariants' two-year window.
RECENT = int(datetime(2025, 1, 1).timestamp() * 1000)


def fixture(source_name: str):
    module, ext = SOURCES[source_name]
    raw = read_fixture(module, f"feed-2026-11-16.{ext}.gz")
    return raw if ext == "ics" else json.loads(raw)


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    @property
    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self._body) if isinstance(self._body, str) else self._body


def routes_for(source_name: str, data) -> list:
    """(method, url fragment, body) for each request the source makes."""
    if source_name == "Arrow Street Arts":
        return [("GET", "tockify.com/api/feeds/ics/arrowstarts", data)]
    if source_name in ("Bow Market", "Union Square Main Streets"):
        return [("POST", "/js/v0.1/calendar/data", data["settings"]),
                ("GET", "/data/public/events", data["events"])]
    if source_name == "The Jungle":
        return [("GET", "data.accentapi.com/feed/87910.json", data)]
    if source_name == "Spectrum Singers":
        return [("GET", "/rest/Production", data["productions"]),
                ("GET", "/rest/CalendarProductions", data["calendar"])]
    if source_name == "McCarthy's Toad":
        return [("GET", "core.service.elfsight.com/p/boot/", data)]
    if source_name == "Native Plant Community Gardeners":
        recent = [i for i in data["past"] if i["startDate"] >= RECENT]
        return [("GET", "nativeplantcommunitygardeners.org/events", {**data, "upcoming": recent})]
    if source_name == "Sarasa Ensemble":
        # The home page's path, "/", is in every URL: match it last.
        return sorted((("GET", f"sarasamusic.org{path}", body) for path, body in data.items()),
                      key=lambda route: route[1] == "sarasamusic.org/")
    if source_name == "Non-Event":
        return [("GET", "nonevent.org/concerts", data)]
    raise KeyError(source_name)


def serve(monkeypatch, routes: list, calls: list):
    def handler(method):
        def fake(url, params=None, headers=None, **kwargs):
            calls.append((method, url, dict(params or {}), dict(headers or {})))
            for m, fragment, body in routes:
                if m == method and fragment in url:
                    return _Response(body)
            return _Response({}, 404)
        return fake

    monkeypatch.setattr(requests, "get", handler("GET"))
    monkeypatch.setattr(requests, "post", handler("POST"))


def scrape(source_name: str, monkeypatch, data=None, scraper=None):
    """Run a registered source offline over its fixture; return (events, calls)."""
    calls: list = []
    serve(monkeypatch, routes_for(source_name, fixture(source_name) if data is None else data), calls)
    scraper = scraper or BY_NAME[source_name].load()
    return scraper.scrape_events(), calls


def one(events, title):
    found = [e for e in events if e.title == title]
    assert len(found) == 1, f"{title!r}: {len(found)} events"
    return found[0]


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_source_parses_clean(source_name, monkeypatch, offline):
    events, _ = scrape(source_name, monkeypatch)
    assert events, f"{source_name}: fixture yielded no events"

    dicts = [e.model_dump(mode="json") for e in events]
    errors = [v for v in check_invariants(dicts, now=FIXTURE_DAY) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert BY_NAME[source_name].module == f"src.scrapers.{SOURCES[source_name][0]}"
    assert all(e.city in LOCAL_CITIES for e in events)
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.start_datetime.tzinfo is not None]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title or e.title != e.title.strip()]
    assert not [e for e in events if e.end_datetime is not None and e.end_datetime <= e.start_datetime]


def test_registry_kinds():
    """Union Square Main Streets is a community calendar anyone can submit to."""
    assert BY_NAME["Union Square Main Streets"].kind == "aggregator"
    assert {BY_NAME[n].kind for n in SOURCES if n != "Union Square Main Streets"} == {"requests"}
    assert all(BY_NAME[n].runs_in_ci for n in SOURCES)


# --------------------------------------------------------------------------- #
# Arrow Street Arts — Tockify iCal
# --------------------------------------------------------------------------- #

def test_arrow_reads_utc_stamps_as_eastern_wall_clock(monkeypatch, offline):
    events, _ = scrape("Arrow Street Arts", monkeypatch)
    # 20261206T000000Z is 7 PM on Dec 5 (EST); 20270324T230000Z is 7 PM (EDT).
    tall = one(events, "Passim Presents: Tall Heights - Neptune 10 Year Anniversary Tour")
    assert tall.start_datetime == datetime(2026, 12, 5, 19, 0)
    assert tall.source_url.startswith("https://tockify.com/arrowstarts/detail/")
    assert datetime(2027, 3, 24, 19, 0) in {e.start_datetime for e in events if e.title == "Passim Presents: Darlingside"}


def test_arrow_names_its_studio_and_uses_its_own_address(monkeypatch, offline):
    events, _ = scrape("Arrow Street Arts", monkeypatch)
    studio = one(events, "Troy Roberts Quartet")
    assert (studio.venue_name, studio.city) == ("The Studio at Arrow Street Arts", "Cambridge")
    main = [e for e in events if e.venue_name == "Arrow Street Arts"]
    assert main and {e.street_address for e in main} == {"2 Arrow Street"}


# --------------------------------------------------------------------------- #
# eventscalendar.co — Bow Market, Union Square Main Streets
# --------------------------------------------------------------------------- #

def test_bow_market_reads_the_day_and_its_wall_clock(monkeypatch, offline):
    """The rendered calendar showed 'Wednesday, November 18, 2026 ... 7:00pm-9:00pm'."""
    events, calls = scrape("Bow Market", monkeypatch)
    mingle = {(e.start_datetime, e.end_datetime) for e in events if e.title == "Mingle Mayhem"}
    assert (datetime(2026, 11, 18, 19, 0), datetime(2026, 11, 18, 21, 0)) in mingle
    assert [c[0] for c in calls] == ["POST", "GET"]


def test_bow_market_date_only_events_have_no_time(monkeypatch, offline):
    """'dateonly' events are shown without a time, whatever startHour holds."""
    data = fixture("Bow Market")
    item = next(i for i in data["events"]["value"] if i.get("dateonly"))
    assert item["startHour"] != 0

    events, _ = scrape("Bow Market", monkeypatch, data)
    crawl = [e for e in events if e.title == item["title"].strip()]
    assert crawl and all(e.all_day and e.start_datetime.hour == 0 and e.end_datetime is None for e in crawl)


def test_bow_market_skips_an_event_with_no_start_date(monkeypatch, offline):
    data = fixture("Bow Market")
    undated = [i["title"].strip() for i in data["events"]["value"] if not i.get("startDate")]
    assert undated, "fixture should include an event without startDate"
    events, _ = scrape("Bow Market", monkeypatch, data)
    assert not {e.title for e in events} & set(undated)


def test_bow_market_places_its_rooms_and_shops_at_the_market(monkeypatch, offline):
    events, _ = scrape("Bow Market", monkeypatch)
    at_bow = [e for e in events if e.venue_name == "Bow Market"]
    assert at_bow and {e.street_address for e in at_bow} == {"1 Bow Market Way"}
    elsewhere = [e for e in events if e.venue_name != "Bow Market"]
    assert {(e.venue_name, e.street_address, e.city) for e in elsewhere} == {("Union Square", None, "Somerville")}


def test_repeating_event_is_listed_once_at_its_first_date(monkeypatch, offline):
    data = fixture("Bow Market")
    taco = next(i for i in data["events"]["value"] if i["title"] == "Summer Taco Tuesdays")
    assert taco["repeat"]["period"] == "week"
    events, _ = scrape("Bow Market", monkeypatch, data)
    assert [e.start_datetime for e in events if e.title == "Summer Taco Tuesdays"] == [datetime(2026, 6, 23, 16, 30)]


def test_repeating_event_whose_first_date_was_removed_is_skipped():
    scraper = BY_NAME["Bow Market"].load()
    item = {"title": "Weekly Thing", "startDate": "2026-12-05", "startHour": 9, "startMinutes": 0,
            "location": "Bow Market", "description": "A weekly thing at the market, every Saturday.",
            "repeat": {"period": "week", "value": 1, "overrides": {"2026-12-05": {"removed": True}}}}
    assert scraper.parse_item(item, "America/New_York") is None
    del item["repeat"]["overrides"]
    assert scraper.parse_item(item, "America/New_York").start_datetime == datetime(2026, 12, 5, 9, 0)


def test_eventscalendar_refuses_a_calendar_not_set_to_eastern(monkeypatch, offline):
    data = fixture("Bow Market")
    data["settings"]["project"]["data"]["settings"]["timezone"] = "America/Los_Angeles"
    with pytest.raises(ValueError, match="not Eastern"):
        scrape("Bow Market", monkeypatch, data)


def test_union_square_skips_locations_that_name_no_city(monkeypatch, offline):
    data = fixture("Union Square Main Streets")
    unplaced = {i["title"].strip() for i in data["events"]["value"]
                if i.get("location") in ("116 Beacon Street", "Arts at the Armory ")}
    assert unplaced, "fixture should include events whose location names no city"
    events, _ = scrape("Union Square Main Streets", monkeypatch, data)
    assert not {e.title for e in events} & unplaced
    aeronaut = [e for e in events if e.venue_name == "Aeronaut"]
    assert aeronaut and {(e.street_address, e.city, e.zip_code) for e in aeronaut} == {("14 Tyler St", "Somerville", "02143")}


@pytest.mark.parametrize("text, city, named", [
    ("Aeronaut, 14 Tyler St, Somerville, MA 02143", "Somerville", True),
    ("Boston Figurative Arts Center, 285 Washington St, Somerville, MA", "Somerville", True),
    ("249 Washington St. Union Sq. Somerville Ma ", "Somerville", True),
    ("Somerville High School, 81 Highland Ave, Somerville", "Somerville", True),
    ("1920 Mass Ave, Cambridge", "Cambridge", True),
    ("Hope Central Church, Boston, MA 02116", None, True),
    ("Cambridge St, Boston, MA", None, True),
    ("Brattleboro Music Center, Brattleboro, VT", None, True),
    ("Somerville Ave between Church St and Union Sq", None, False),
    ("116 Beacon Street", None, False),
    ("Green Tiger & Co.", None, False),
])
def test_local_city(text, city, named):
    assert local_city(text) == (city, named)


@pytest.mark.parametrize("text, venue, street", [
    ("Boston Figurative Arts Center, 285 Washington St, Somerville, MA", "Boston Figurative Arts Center", "285 Washington St"),
    ("Arts at the Armory 191 Highland Ave, Somerville MA 02143", "Arts at the Armory", "191 Highland Ave"),
    ("Warehouse XI: 11 Sanborn Ct, Somerville, MA 02143", "Warehouse XI", "11 Sanborn Ct"),
    ("249 Washington St. Union Sq. Somerville Ma ", None, "249 Washington St"),
    ("Union Square, Somerville MA", "Union Square", None),
    ("Somerville, MA", None, None),
    ("Somerville Community Growing Center", "Somerville Community Growing Center", None),
])
def test_venue_and_street(text, venue, street):
    assert venue_and_street(text) == (venue, street)


# --------------------------------------------------------------------------- #
# The Jungle — SociableKit / Eventbrite
# --------------------------------------------------------------------------- #

def test_jungle_drops_templates_and_blocked_nights(monkeypatch, offline):
    data = fixture("The Jungle")
    items = data["events"]
    dummies = [i for i in items if "dummy" in i["name"].lower()]
    blocked = [i for i in items if NOT_AN_EVENT.search(i["name"]) and i not in dummies]
    assert dummies and blocked, "fixture should include template and blocked-night listings"

    events, _ = scrape("The Jungle", monkeypatch, data)
    assert len(events) == len(items) - len(dummies) - len(blocked)
    assert not [e for e in events if "dummy" in e.title.lower()]


def test_jungle_times_are_the_listed_local_times(monkeypatch, offline):
    events, _ = scrape("The Jungle", monkeypatch)
    kyle = one(events, "Kyle Kehoe")
    assert (kyle.start_datetime, kyle.end_datetime) == (datetime(2026, 11, 17, 20, 0), datetime(2026, 11, 17, 21, 0))
    assert (kyle.venue_name, kyle.street_address, kyle.city) == ("The Jungle", "6 Sanborn Court", "Somerville")
    assert kyle.source_url.startswith("https://www.eventbrite.com/e/")


def test_jungle_skips_an_event_in_another_timezone(monkeypatch, offline):
    data = fixture("The Jungle")
    data["events"][0]["timezone"] = "America/Los_Angeles"
    events, _ = scrape("The Jungle", monkeypatch, data)
    assert data["events"][0]["name"] not in {e.title for e in events}


# --------------------------------------------------------------------------- #
# Spectrum Singers — OvationTix
# --------------------------------------------------------------------------- #

def test_spectrum_keeps_cambridge_and_drops_brookline(monkeypatch, offline):
    data = fixture("Spectrum Singers")
    brookline = {p["productionName"] for p in data["productions"] if p["venue"]["address"]["city"] == "Brookline"}
    assert brookline, "fixture should include the Brookline concerts"

    events, calls = scrape("Spectrum Singers", monkeypatch, data)
    assert events and not [e for e in events if e.title.split(": ", 1)[1] in brookline]
    assert {(e.venue_name, e.street_address, e.city) for e in events} == {
        ("First Church in Cambridge", "11 Garden Street", "Cambridge")}
    assert all(c[3].get("clientId") == "34652" for c in calls)


def test_spectrum_times_and_description(monkeypatch, offline):
    """The choir's own page: 'Saturday, November 21, 2026 | 7:30pm'."""
    events, _ = scrape("Spectrum Singers", monkeypatch)
    reve = one(events, "Spectrum Singers: Dans un Rêve Français")
    assert reve.start_datetime == datetime(2026, 11, 21, 19, 30)
    assert "A Francophone Dreamscape" in reve.description
    assert not [e for e in events if "Add to Cart" in e.description or "NOTE:" in e.description]


# --------------------------------------------------------------------------- #
# McCarthy's Toad — Elfsight
# --------------------------------------------------------------------------- #

def test_mccarthys_rooms_share_one_address(monkeypatch, offline):
    events, _ = scrape("McCarthy's Toad", monkeypatch)
    jake = one(events, "Friday club with 'Jake Clough'")
    assert (jake.start_datetime, jake.end_datetime) == (datetime(2026, 11, 20, 18, 0), datetime(2026, 11, 20, 21, 0))
    assert jake.venue_name == "Toad"
    assert {e.venue_name for e in events} == {"Toad", "McCarthy's"}
    assert {(e.street_address, e.city, e.zip_code) for e in events} == {("1920 Massachusetts Avenue", "Cambridge", "02140")}


def test_mccarthys_lists_a_repeating_event_once(monkeypatch, offline):
    """Weekly sessions repeat without end; expanding them would need a clock."""
    data = fixture("McCarthy's Toad")
    items = next(iter(data["data"]["widgets"].values()))["data"]["settings"]["events"]
    repeating = [i for i in items if i["repeatPeriod"] != "noRepeat"]
    assert repeating, "fixture should include repeating events"

    events, _ = scrape("McCarthy's Toad", monkeypatch, data)
    # One event per item, on the item's own start date: nothing expanded.
    expected = sorted((" ".join(i["name"].split()), i["start"]["date"]) for i in items
                      if not NOT_AN_EVENT.search(i["name"]))
    assert sorted((e.title, e.start_datetime.date().isoformat()) for e in events) == expected


# --------------------------------------------------------------------------- #
# Native Plant Community Gardeners — Squarespace, Los Angeles clock
# --------------------------------------------------------------------------- #

def test_npcg_reads_its_los_angeles_epochs_as_eastern(monkeypatch, offline):
    """'BioDay at Danehy Park — Saturday, May 3, 3–5pm' displays 3:00 PM–5:00 PM."""
    events, _ = scrape("Native Plant Community Gardeners", monkeypatch)
    bioday = one(events, "BioDay at Danehy Park — Saturday, May 3, 3–5pm")
    assert (bioday.start_datetime, bioday.end_datetime) == (datetime(2025, 5, 3, 15, 0), datetime(2025, 5, 3, 17, 0))


def test_npcg_without_the_verified_flag_is_refused(monkeypatch, offline):
    from src.scrapers.native_plant_gardeners import NativePlantGardenersScraper

    class Unverified(NativePlantGardenersScraper):
        WALL_CLOCK_IS_EASTERN = False

    with pytest.raises(ValueError, match="not Eastern"):
        scrape("Native Plant Community Gardeners", monkeypatch, scraper=Unverified())


def test_npcg_places_an_unlocated_event_at_the_one_park_it_names(monkeypatch, offline):
    data = fixture("Native Plant Community Gardeners")
    soil = next(i for i in data["past"] if i["title"].startswith("Soil prep"))
    assert not any(soil["location"].values())

    events, _ = scrape("Native Plant Community Gardeners", monkeypatch, data)
    placed = one(events, soil["title"])
    assert (placed.venue_name, placed.street_address, placed.city) == ("Danehy Park", "99 Sherman Street", "Cambridge")


def test_npcg_skips_an_event_that_names_two_parks():
    """The Moth Ball was at Fresh Pond and mentions the Danehy Park garden in passing."""
    data = fixture("Native Plant Community Gardeners")
    moth = next(i for i in data["past"] if "Moth Ball" in i["title"])
    scraper = BY_NAME["Native Plant Community Gardeners"].load()
    scraper.site_zone = data["website"]["timeZone"]
    assert scraper.named_park(moth) is None
    assert scraper.parse_item(moth) is None


# --------------------------------------------------------------------------- #
# Sarasa Ensemble — dated lines on program pages
# --------------------------------------------------------------------------- #

def test_sarasa_keeps_only_its_cambridge_performances(monkeypatch, offline):
    data = fixture("Sarasa Ensemble")
    events, calls = scrape("Sarasa Ensemble", monkeypatch, data)
    assert [c[1] for c in calls[:2]] == ["https://www.sarasamusic.org/", "https://www.sarasamusic.org/202627-concerts"]
    # One Cambridge night per program; Brattleboro and Lexington dropped.
    programs = [p for p, page in data.items()
                if p not in ("/", "/202627-concerts") and "Lexington" in page.get("mainContent", "")]
    assert programs and len(events) == len(programs)
    # Written both "Friends Meeting House" and "Cambridge Friends Meeting House".
    assert {(e.venue_name, e.street_address, e.city) for e in events} == {
        ("Friends Meeting House", "5 Longfellow Park", "Cambridge")}
    caffe = one(events, "Sarasa: Le Caffe")
    assert caffe.start_datetime == datetime(2027, 1, 9, 19, 0)


def test_sarasa_skips_a_line_whose_weekday_disagrees_with_its_date(monkeypatch, offline):
    data = fixture("Sarasa Ensemble")
    page = data["/caffe"]
    assert "Saturday, January 9, 2027" in page["mainContent"]
    page["mainContent"] = page["mainContent"].replace("Saturday, January 9, 2027", "Friday, January 9, 2027")
    events, _ = scrape("Sarasa Ensemble", monkeypatch, data)
    assert events and "Sarasa: Le Caffe" not in {e.title for e in events}


# --------------------------------------------------------------------------- #
# Non-Event — blog posts
# --------------------------------------------------------------------------- #

def test_nonevent_keeps_cambridge_concerts_at_their_stated_time(monkeypatch, offline):
    data = fixture("Non-Event")
    boston = [i for i in data["items"] if (i["location"]["addressLine2"] or "").startswith("Boston")]
    assert boston, "fixture should include Boston concerts"

    events, _ = scrape("Non-Event", monkeypatch, data)
    assert {e.city for e in events} == {"Cambridge"}
    wong = one(events, "Eric Wong")    # "friday, october 23, 2026 at 6:30pm"
    assert (wong.start_datetime, wong.venue_name) == (datetime(2026, 10, 23, 18, 30), "MIT List Visual Arts Center")


@pytest.mark.parametrize("old, new", [
    ("october 23, 2026", "october 23, 2025"),     # wrong year: weekday no longer matches
    ("friday, october 23", "saturday, october 24"),  # agrees with itself, not with the post date
])
def test_nonevent_skips_a_post_whose_dates_disagree(old, new, monkeypatch, offline):
    data = fixture("Non-Event")
    post = next(i for i in data["items"] if i["title"] == "Eric Wong")
    assert old in post["body"]
    post["body"] = post["body"].replace(old, new)
    events, _ = scrape("Non-Event", monkeypatch, data)
    assert events and "Eric Wong" not in {e.title for e in events}
