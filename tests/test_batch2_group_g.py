"""Batch 2, group G: Somerville community, nature, and Eventbrite-hosted organizers.

Each fixture is the source as served on 2026-11-16 — iCalendar feeds verbatim,
JSON feeds with long bodies cut, HTML pages with styles and executable scripts
removed (JSON-LD and Next.js page data kept). Two sources had nothing local
upcoming that day, so they are also tested on recent past data served as if
upcoming: Farrington Nature Linc (its `past` list) and Activist Afternoons
(Mobilize's 2026 events within three miles of 02139, captured the same day).

The tests assert behaviour — what is kept, dropped, placed and timed — not
counts typed from a live site.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.scrapers.base_ical import parse_calendar, split_address
from src.sources import BY_NAME
from tests.conftest import read_fixture

DAY = "2026-11-16"

SBK_FEED = ("https://calendar.google.com/calendar/ical/admin%40somervillebikekitchen.org/public/basic.ics"
            "?futureevents=true")
AUDUBON = "https://www.massaudubon.org/programs/magazine-beach"
QGIV = "https://secure.qgiv.com/for/blackhistoryinactionforcambridgeport/event/"

# source -> (fixture directory, {requested URL: fixture file})
ROUTES = {
    "Somerville Arts Council": ("somerville_arts_council", {
        "https://somervilleartscouncil.org/events.ics": f"events-{DAY}.ics.gz"}),
    "SomArt Space": ("somart", {"https://somartspace.org/events.ics": f"events-{DAY}.ics.gz"}),
    "Somerville Bike Kitchen": ("somerville_bike_kitchen", {SBK_FEED: f"calendar-{DAY}.ics.gz"}),
    "Farrington Nature Linc": ("farrington", {
        "https://www.farringtonnaturelinc.org/community-events": f"feed-{DAY}.json.gz"}),
    "Mass Audubon Magazine Beach": ("mass_audubon_magazine_beach", {
        AUDUBON: f"page1-{DAY}.html.gz", f"{AUDUBON}?page=2": f"page2-{DAY}.html.gz"}),
    "Green Cambridge": ("green_cambridge", {"https://api.tockify.com/api/ngevent": f"feed-{DAY}.json.gz"}),
    "Earthwise Aware": ("earthwise_aware", {
        "https://www.eventbrite.com/cc/ewa-urban-nature-participatory-science-1116609": f"collection-{DAY}.html.gz"}),
    "CambridgeSide": ("cambridgeside", {
        "https://www.cambridgeside.com/events": f"events-{DAY}.html.gz",
        "https://www.eventbrite.com/o/cambridgeside-107372334671": f"organizer-{DAY}.html.gz"}),
    "Black History in Action": ("bhac", {
        "https://www.bhacambridge.org/events": f"events-{DAY}.html.gz",
        f"{QGIV}heartofthecultureanightofjazzwithtiniqua": f"qgiv-tiniqua-{DAY}.html.gz",
        f"{QGIV}feelingrightexhibitionopeningandperformance": f"qgiv-feeling-right-{DAY}.html.gz"}),
    "Activist Afternoons": ("activist_afternoons", {
        "https://api.mobilize.us/v1/organizations/33342/events": f"feed-{DAY}.json.gz"}),
}

# Sources whose 2026-11-16 data has nothing local upcoming, and the variant that does.
VARIANTS = {
    "Farrington Nature Linc": "past-as-upcoming",
    "Activist Afternoons": "nearby-2026",
}


class _Response:
    def __init__(self, body: str, status: int = 200):
        self.text, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.text)


def fixture(source: str, name: str) -> str:
    return read_fixture(ROUTES[source][0], name)


def serve(monkeypatch, source: str, overrides: dict | None = None) -> list:
    """Answer the source's requests from its fixtures; return the list of calls."""
    directory, routes = ROUTES[source]
    bodies = {url.rstrip("/"): read_fixture(directory, name) for url, name in routes.items()}
    bodies.update({url.rstrip("/"): body for url, body in (overrides or {}).items()})
    calls: list = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        body = bodies.get(url.rstrip("/"))
        return _Response(body) if body is not None else _Response("not found", 404)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def scrape(source: str, monkeypatch, overrides: dict | None = None, variant: str | None = None):
    overrides = dict(overrides or {})
    if variant == "past-as-upcoming":
        data = json.loads(fixture(source, f"feed-{DAY}.json.gz"))
        overrides["https://www.farringtonnaturelinc.org/community-events"] = json.dumps({**data, "upcoming": data["past"]})
    elif variant == "nearby-2026":
        overrides["https://api.mobilize.us/v1/organizations/33342/events"] = fixture(source, f"nearby-2026-{DAY}.json.gz")
    calls = serve(monkeypatch, source, overrides)
    return BY_NAME[source].load().scrape_events(), calls


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", sorted(ROUTES))
def test_source_parses_clean(source, monkeypatch, offline):
    events, _ = scrape(source, monkeypatch, variant=VARIANTS.get(source))
    assert events, f"{source}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    registered = BY_NAME[source]
    assert registered.module.rsplit(".", 1)[-1] == ROUTES[source][0]
    assert {e.source_name for e in events} == {source}
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.venue_name == "Online"], "no group G source keeps online events"
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.start_datetime.tzinfo is not None]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert all(e.end_datetime is None or e.end_datetime > e.start_datetime for e in events)


@pytest.mark.parametrize("source", sorted(VARIANTS))
def test_sources_with_nothing_local_today_return_nothing(source, monkeypatch, offline):
    """On 2026-11-16 Farrington's upcoming events were all in Lincoln and the
    Mobilize feed's were all elsewhere in the state or virtual."""
    events, _ = scrape(source, monkeypatch)
    assert events == []


# --------------------------------------------------------------------------- #
# base_ical
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, expected", [
    ("Community Path at Cedar St, Community Path at Cedar St, Somerville, MA, 02145, United States",
     ("Community Path at Cedar St", "Community Path at Cedar St", "Somerville", "02145")),
    ("Somerville Bike Kitchen, 15 Properzi Way, Somerville, MA 02143, USA",
     ("Somerville Bike Kitchen", "15 Properzi Way", "Somerville", "02143")),
    ("2 Acorn Park Dr, Cambridge, MA 02140, USA", (None, "2 Acorn Park Dr", "Cambridge", "02140")),
    ("The Center for Arts at the Armory, Performance Hall #1A, 191 Highland Ave, Somerville, MA 02144",
     ("The Center for Arts at the Armory, Performance Hall #1A", "191 Highland Ave", "Somerville", "02144")),
    ("Davis Square", ("Davis Square", None, None, None)),
    ("", (None, None, None, None)),
])
def test_split_address(text, expected):
    assert split_address(text) == expected


def test_parse_calendar_unfolds_lines_and_keeps_parameters():
    _, events = parse_calendar(fixture("SomArt Space", f"events-{DAY}.ics.gz"))
    first = events[0]
    assert first["DTSTART"][0][0] == {"TZID": "America/New_York"}
    assert "\n" not in first["LOCATION"][0][1] and first["LOCATION"][0][1].endswith("United States")


def test_arts_council_drops_its_phone_box_installations(monkeypatch, offline):
    """The feed lists art installations dated 2015-2030 with no location."""
    text = fixture("Somerville Arts Council", f"events-{DAY}.ics.gz")
    _, vevents = parse_calendar(text)
    installations = [v for v in vevents if "Phone Art Project" in v.get("CATEGORIES", [({}, "")])[0][1]]
    assert installations

    events, _ = scrape("Somerville Arts Council", monkeypatch)
    titles = {e.title for e in events}
    assert not titles & {v["SUMMARY"][0][1].replace("\\,", ",") for v in installations}
    parade = next(e for e in events if e.title == "Somerville Lantern Parade")
    assert (parade.start_datetime, parade.end_datetime) == (datetime(2026, 12, 17, 18, 0), datetime(2026, 12, 17, 19, 30))
    assert (parade.venue_name, parade.city, parade.zip_code) == ("Community Path at Cedar St", "Somerville", "02145")


def test_somart_drops_walkthroughs_and_converts_utc_starts(monkeypatch, offline):
    _, vevents = parse_calendar(fixture("SomArt Space", f"events-{DAY}.ics.gz"))
    walkthroughs = [v for v in vevents if "Walkthrough" in v.get("CATEGORIES", [({}, "")])[0][1]]
    assert walkthroughs

    events, _ = scrape("SomArt Space", monkeypatch)
    assert len(events) == len(vevents) - len(walkthroughs)
    assert not [e for e in events if "Walkthrough" in e.title]
    # DTSTART:20270107T233000Z — the only UTC-stamped event — is 6:30 PM Eastern.
    meet = next(e for e in events if e.title == "MEET v11")
    assert meet.start_datetime == datetime(2027, 1, 7, 18, 30)
    assert {e.venue_name for e in events} == {"SomArt @ the Hive", "Diamond Factory"}
    jazz = next(e for e in events if e.title.startswith("Jazz Sunday"))
    assert jazz.category == EventCategory.MUSIC


def test_bike_kitchen_reads_utc_workshops_and_renamed_instances(monkeypatch, offline):
    events, _ = scrape("Somerville Bike Kitchen", monkeypatch)
    starts = {e.title: e.start_datetime for e in events}
    # DTSTART:20261117T003000Z is Monday Nov 16, 7:30 PM in Somerville.
    assert starts["SBK Winter Workshop: Wheel Hub Bearing Overhauls"] == datetime(2026, 11, 16, 19, 30)
    # An override of the Thursday open-shop series, renamed: a one-off event.
    assert starts["SBK Friendsgiving"] == datetime(2026, 11, 19, 18, 0)
    # The series themselves are not expanded.
    assert not [t for t in starts if t in ("Open Shop", "SBK Monthly Staff Meeting")]
    assert {(e.venue_name, e.street_address) for e in events} == {("Somerville Bike Kitchen", "15 Properzi Way")}


def test_bike_kitchen_drops_closures_and_internal_business(monkeypatch, offline):
    """The full calendar (2023 onward) shows what the filters are for."""
    everything = fixture("Somerville Bike Kitchen", f"calendar-all-{DAY}.ics.gz")
    assert re.search(r"SUMMARY:SBK CLOSED", everything) and re.search(r"SUMMARY:Sbk lease call", everything)
    events, _ = scrape("Somerville Bike Kitchen", monkeypatch, overrides={SBK_FEED: everything})
    assert events
    bad = re.compile(r"closed|cancel|staff meeting|lease call|retreat|no open shop", re.I)
    assert not [e.title for e in events if bad.search(e.title)]
    assert "Open Shop" not in {e.title for e in events}
    elsewhere = {e.venue_name for e in events} - {"Somerville Bike Kitchen"}
    assert elsewhere and all(e.city in LOCAL_CITIES for e in events)


def test_ical_floating_times_need_an_eastern_calendar(monkeypatch, offline):
    from src.scrapers.somart import SomArtScraper

    template = ("BEGIN:VCALENDAR\nX-WR-TIMEZONE:{zone}\nBEGIN:VEVENT\nUID:1\nSUMMARY:Floating Show\n"
                "DTSTART:20261201T190000\nLOCATION:Diamond Factory\\, 260 Elm Street\\, Somerville\\, MA\\, 02144\n"
                "END:VEVENT\nEND:VCALENDAR\n")
    url = "https://somartspace.org/events.ics"
    for zone, expected in (("America/New_York", datetime(2026, 12, 1, 19, 0)),
                           ("America/Los_Angeles", datetime(2026, 12, 1, 22, 0))):
        serve(monkeypatch, "SomArt Space", {url: template.format(zone=zone)})
        assert [e.start_datetime for e in SomArtScraper().scrape_events()] == [expected]
    serve(monkeypatch, "SomArt Space", {url: template.replace("X-WR-TIMEZONE:{zone}\n", "")})
    assert SomArtScraper().scrape_events() == []


# --------------------------------------------------------------------------- #
# Farrington (Squarespace)
# --------------------------------------------------------------------------- #

def test_farrington_asks_for_the_list_view_and_keeps_only_local_events(monkeypatch, offline):
    data = json.loads(fixture("Farrington Nature Linc", f"feed-{DAY}.json.gz"))
    local = [i for i in data["past"] if "Somerville" in i["location"]["addressLine2"]]
    assert local and len(local) < len(data["past"])

    events, calls = scrape("Farrington Nature Linc", monkeypatch, variant="past-as-upcoming")
    assert calls[0][1] == {"format": "json", "view": "list"}
    assert len(events) == len(local)
    # "Nature Trivia Night at Aeronaut": 2026-03-25T00:00Z is 8 PM on March 24.
    assert datetime(2026, 3, 24, 20, 0) in {e.start_datetime for e in events}
    assert {e.city for e in events} == {"Somerville"}


# --------------------------------------------------------------------------- #
# Mass Audubon
# --------------------------------------------------------------------------- #

def test_audubon_keeps_cambridge_programs_and_times_them_from_the_card(monkeypatch, offline):
    events, calls = scrape("Mass Audubon Magazine Beach", monkeypatch)
    assert [c[0] for c in calls] == [AUDUBON, f"{AUDUBON}?page=2"]
    pages = fixture("Mass Audubon Magazine Beach", f"page1-{DAY}.html.gz") + \
        fixture("Mass Audubon Magazine Beach", f"page2-{DAY}.html.gz")
    assert "T-Stop Trek" in pages and not [e for e in events if "T-Stop" in e.title]
    assert {e.venue_name for e in events} == {"Magazine Beach Park Nature Center"}
    spans = {e.title: (e.start_datetime, e.end_datetime) for e in events}
    assert spans["Late Autumn Bird Walk"] == (datetime(2026, 11, 21, 8, 0), datetime(2026, 11, 21, 10, 0))
    assert spans["Early Spring Migration Bird Walk"] == (datetime(2027, 3, 25, 17, 30), datetime(2027, 3, 25, 19, 0))
    assert all(e.latitude and e.longitude for e in events)


def test_audubon_skips_a_card_whose_stamp_and_epoch_disagree(monkeypatch, offline):
    page1 = fixture("Mass Audubon Magazine Beach", f"page1-{DAY}.html.gz")
    shifted = page1.replace('data-next_meeting_ts="1795266000"', 'data-next_meeting_ts="1795269600"')
    assert shifted != page1
    events, _ = scrape("Mass Audubon Magazine Beach", monkeypatch, overrides={AUDUBON: shifted})
    assert "Late Autumn Bird Walk" not in {e.title for e in events}


@pytest.mark.parametrize("text, expected", [
    ("8:00-10:00am", ((8, 0), (10, 0))),
    ("5:30-7:00pm", ((17, 30), (19, 0))),
    ("11:00-1:00pm", ((11, 0), (13, 0))),
    ("11:00am-1:00pm", ((11, 0), (13, 0))),
    ("12:00-1:30pm", ((12, 0), (13, 30))),
    ("TBA", None),
])
def test_audubon_printed_range(text, expected):
    from src.scrapers.mass_audubon_magazine_beach import printed_range
    assert printed_range(text) == expected


# --------------------------------------------------------------------------- #
# Green Cambridge (Tockify)
# --------------------------------------------------------------------------- #

def test_green_cambridge_drops_meetings_and_unplaced_events(monkeypatch, offline):
    data = json.loads(fixture("Green Cambridge", f"feed-{DAY}.json.gz"))
    titles = [e["content"]["summary"]["text"] for e in data["events"]]
    assert any("Committee" in t for t in titles) and any(not e["content"].get("address") for e in data["events"])

    events, _ = scrape("Green Cambridge", monkeypatch)
    assert not [e for e in events if re.search(r"meeting|committee|mtg", e.title, re.I)]
    starts = {e.title: e.start_datetime for e in events}
    # millis 1794855600000 is Monday Nov 16, 2 PM EST.
    assert starts["Our Work in the Alewife Walk"] == datetime(2026, 11, 16, 14, 0)
    assert "Alewife Brook Reservation Trash Cleanup" not in starts, "no address, and no single home venue"


# --------------------------------------------------------------------------- #
# Eventbrite (base_eventbrite)
# --------------------------------------------------------------------------- #

EWA = "https://www.eventbrite.com/cc/ewa-urban-nature-participatory-science-1116609"


def test_eventbrite_collection_keeps_upcoming_local_events_only(monkeypatch, offline):
    from src.scrapers.base_eventbrite import jsonld_events, next_data

    html = fixture("Earthwise Aware", f"collection-{DAY}.html.gz")
    ld = jsonld_events(html)
    collection = next_data(html)["props"]["pageProps"]["serverData"]["events_in_collection"]
    upcoming_ids = {str(e["id"]) for e in collection["upcoming"]["events"]}
    past_ids = {str(e["id"]) for e in collection["past"]["events"]}
    assert len(ld) == len(upcoming_ids | past_ids), "the JSON-LD mixes past and upcoming"

    events, _ = scrape("Earthwise Aware", monkeypatch)
    ids = {e.source_url.rstrip("/").rsplit("-", 1)[-1] for e in events}
    assert ids <= upcoming_ids and not ids & past_ids
    assert {e.venue_name for e in events} == {"Somerville Community Growing Center"}
    assert {e.street_address for e in events} == {"22 Vinal Avenue"}


def test_eventbrite_times_convert_from_their_offsets(monkeypatch, offline):
    """JSON-LD carries -0500 in winter and -0400 in summer; both are 2 PM sessions."""
    events, _ = scrape("Earthwise Aware", monkeypatch)
    starts = {e.start_datetime for e in events}
    assert datetime(2026, 12, 12, 14, 0) in starts      # 2026-12-12T14:00:00-0500
    assert datetime(2027, 4, 10, 14, 0) in starts       # 2027-04-10T14:00:00-0400
    assert all(e.start_datetime.hour == 14 for e in events)


def test_eventbrite_collection_refuses_disagreeing_copies(monkeypatch, offline):
    """If the JSON-LD and the page data name different instants, skip the event."""
    html = fixture("Earthwise Aware", f"collection-{DAY}.html.gz")
    bent = html.replace('"startDate":"2026-12-12T14:00:00-0500"', '"startDate":"2026-12-12T15:00:00-0500"', 1)
    assert bent != html
    events, _ = scrape("Earthwise Aware", monkeypatch, overrides={EWA: bent})
    assert datetime(2026, 12, 12, 14, 0) not in {e.start_datetime for e in events}
    assert datetime(2026, 12, 12, 15, 0) not in {e.start_datetime for e in events}


def test_eventbrite_page_without_page_data_fails_loudly(monkeypatch, offline):
    """Without Eventbrite's upcoming/past split the JSON-LD alone would publish past events."""
    html = fixture("Earthwise Aware", f"collection-{DAY}.html.gz")
    stripped = re.sub(r'<script id="__NEXT_DATA__".*?</script>', "", html, flags=re.S)
    with pytest.raises(ValueError, match="no Eventbrite page data"):
        scrape("Earthwise Aware", monkeypatch, overrides={EWA: stripped})


def test_eventbrite_organizer_times_are_converted_from_their_timezone(monkeypatch, offline):
    """Organizer records give a local start and its zone: 6 PM Chicago is 7 PM here."""
    url = "https://www.eventbrite.com/o/cambridgeside-107372334671"
    html = fixture("CambridgeSide", f"organizer-{DAY}.html.gz")
    events, _ = scrape("CambridgeSide", monkeypatch)
    assert {e.title: e.start_datetime for e in events}["Trivia Night"] == datetime(2026, 11, 19, 18, 0)

    chicago = html.replace('"timezone":"America/New_York"', '"timezone":"America/Chicago"')
    assert chicago != html
    events, _ = scrape("CambridgeSide", monkeypatch, overrides={url: chicago})
    assert {e.title: e.start_datetime for e in events}["Trivia Night"] == datetime(2026, 11, 19, 19, 0)


def test_cambridgeside_merges_its_page_with_eventbrite(monkeypatch, offline):
    events, calls = scrape("CambridgeSide", monkeypatch)
    titles = [e.title for e in events]
    assert len(titles) == len(set(titles))
    # One-off events from the mall's own page, timed from UTC:
    starts = {e.title: e.start_datetime for e in events}
    assert starts["Pumpkin Pie: A Cake Decorating Experience"] == datetime(2026, 11, 24, 18, 30)
    assert starts["Holiday Movie Day: Polar Express"] == datetime(2026, 12, 12, 11, 0)
    # Eventbrite copies of those are dropped; its dated series nights are kept.
    assert "Holiday Movie Day: The Polar Express" not in starts
    assert starts["Music Bingo"] == datetime(2026, 11, 18, 18, 0)
    # Standing listings on the mall page are not events.
    assert not [t for t in titles if "Events Calendar" in t or "Mahjong" in t or "ZARA" in t or "Gaming" in t]
    assert {(e.venue_name, e.street_address, e.zip_code) for e in events} == \
        {("CambridgeSide", "100 CambridgeSide Place", "02141")}
    trivia = next(e for e in events if e.title == "Trivia Night")
    assert "Thursday" in trivia.description, "described by the mall's series entry that links it"


def test_bhac_follows_only_cards_the_site_marks_upcoming(monkeypatch, offline):
    events, calls = scrape("Black History in Action", monkeypatch)
    assert len(calls) == 3, "the listing plus one ticket page per upcoming card"
    spans = {e.title: (e.start_datetime, e.end_datetime, e.venue_name, e.city) for e in events}
    assert spans["Heart of the Culture: A Night of Jazz with Tiniqua"] == \
        (datetime(2026, 11, 29, 16, 0), datetime(2026, 11, 29, 18, 0), "Congregational Eitz Chayim", "Cambridge")
    assert spans['"Feeling Right" Exhibition Opening and Performance'][0] == datetime(2026, 12, 8, 18, 30)


def test_bhac_skips_a_card_whose_printed_date_disagrees(monkeypatch, offline):
    listing = fixture("Black History in Action", f"events-{DAY}.html.gz")
    moved = listing.replace("November 29, 2026", "November 28, 2026")
    assert moved != listing
    events, _ = scrape("Black History in Action", monkeypatch,
                       overrides={"https://www.bhacambridge.org/events": moved})
    assert not [e for e in events if "Tiniqua" in e.title]
    assert [e for e in events if "Feeling Right" in e.title]


# --------------------------------------------------------------------------- #
# Mobilize
# --------------------------------------------------------------------------- #

def test_activist_afternoons_keeps_local_in_person_timeslots(monkeypatch, offline):
    data = json.loads(fixture("Activist Afternoons", f"nearby-2026-{DAY}.json.gz"))
    local = [e for e in data["data"] if e["location"].get("locality") in LOCAL_CITIES
             and not e["is_virtual"] and e["address_visibility"] == "PUBLIC" and e["visibility"] == "PUBLIC"]
    virtual_local = [e for e in data["data"] if e["location"].get("locality") in LOCAL_CITIES and e["is_virtual"]]
    assert local and virtual_local

    events, calls = scrape("Activist Afternoons", monkeypatch, variant="nearby-2026")
    assert calls[0][1] == {"timeslot_start": "gte_now", "per_page": 100}
    assert len(events) == sum(len(e["timeslots"]) for e in local)
    assert not {e.title for e in events} & {e["title"] for e in virtual_local}
    postcards = next(e for e in events if e.title == "Virginia Referendum Postcards (mid-Cambridge)")
    # timeslot start 1774188000 is 10 AM on Sunday March 22, 2026 (EDT).
    assert postcards.start_datetime == datetime(2026, 3, 22, 10, 0)
    assert postcards.street_address == "336 Harvard St"
