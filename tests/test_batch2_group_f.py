"""Batch 2, group F — Cambridge civic and community organisations.

Each fixture is the HTTP a source's scraper made on 2026-11-16, recorded as
(method, url, params, json body) -> response and replayed here offline. Pages
were trimmed to what the scrapers read (the page's <main>, JSON-LD, og:image;
the Foundry's signed image links; all but three entries of the KiOSK's past
"Load More" list), and the capture checked that the trimmed replay produced
exactly the events the live run did.

Tests assert behaviour — what is kept, dropped, placed, timed — against facts
in the fixtures, not counts typed from a live site.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.sources import BY_NAME
from tests.conftest import read_fixture

FIXTURE = "http-2026-11-16.json.gz"
CAPTURED = datetime(2026, 11, 16, 12, 0)

SOURCES = {
    "Cambridge Foundry": "cambridge_foundry",
    "Central Square BID": "central_square_bid",
    "Cambridge KiOSK": "cambridge_kiosk",
    "Cambridge Center for Adult Education": "ccae",
    "Cambridge Art Association": "cambridge_art_association",
}


def exchanges(source_name: str) -> list:
    return json.loads(read_fixture(SOURCES[source_name], FIXTURE))


def _key(method, url, params, body):
    return method, url, json.dumps(params, sort_keys=True), json.dumps(body, sort_keys=True)


class _Response:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return json.loads(self.text)


def serve(monkeypatch, recorded: list) -> list:
    """Answer requests.get/post from recorded exchanges; return the calls made."""
    table = {_key(e["method"], e["url"], e["params"], e["json"]): e for e in recorded}
    calls: list = []

    def answer(method, url, params=None, body=None):
        calls.append((method, url))
        hit = table.get(_key(method, url, params, body))
        if hit is None:
            raise AssertionError(f"unrecorded request {method} {url} params={params} json={body}")
        return _Response(hit["status"], hit["body"])

    monkeypatch.setattr(requests, "get", lambda url, params=None, **kw: answer("GET", url, params))
    monkeypatch.setattr(requests, "post", lambda url, json=None, **kw: answer("POST", url, None, json))
    return calls


def load(source_name: str):
    scraper = BY_NAME[source_name].load()
    if source_name == "Central Square BID":
        scraper.first_day = date(2026, 11, 16)  # the day the fixture was captured
    return scraper


def scrape(source_name: str, monkeypatch, recorded: list | None = None):
    calls = serve(monkeypatch, recorded if recorded is not None else exchanges(source_name))
    return load(source_name).scrape_events(), calls


def edited(source_name: str, url_part: str, old: str, new: str) -> list:
    """The fixture with one response body edited, to exercise a refusal."""
    recorded = exchanges(source_name)
    hits = [e for e in recorded if url_part in e["url"] and old in e["body"]]
    assert len(hits) == 1, f"expected one {url_part} response containing {old!r}"
    hits[0]["body"] = hits[0]["body"].replace(old, new)
    return recorded


# --------------------------------------------------------------------------- #
# Every source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_source_parses_clean(source_name, monkeypatch, offline):
    events, _ = scrape(source_name, monkeypatch)
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=CAPTURED)
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert BY_NAME[source_name].kind == "requests"
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.end_datetime and e.end_datetime <= e.start_datetime]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert all(e.source_url.startswith("https://") for e in events)


# --------------------------------------------------------------------------- #
# Cambridge Foundry — Softr lists
# --------------------------------------------------------------------------- #

def _foundry_rows() -> list:
    rows = []
    for e in exchanges("Cambridge Foundry"):
        if e["url"].endswith("/records"):
            rows += [item["fields"] for item in json.loads(e["body"])["items"]]
    return rows


def test_foundry_converts_utc_to_the_times_the_embed_shows(monkeypatch, offline):
    """The community-led embed displayed these on 2026-11-16 (UTC in the API)."""
    events, _ = scrape("Cambridge Foundry", monkeypatch)
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Bring the Babies Workout for Busy Moms", datetime(2026, 11, 16, 10, 0)) in starts   # 15:00Z
    assert ("IDOH Korean Speaking Club", datetime(2026, 11, 16, 17, 0)) in starts                # 22:00Z
    assert ("Cambridge Local Showcase", datetime(2026, 11, 20, 17, 0)) in starts                 # 22:00Z
    assert {e.venue_name for e in events} == {"The Foundry"}
    assert {e.street_address for e in events} == {"101 Rogers Street"}


def test_foundry_reads_every_page_of_both_lists(monkeypatch, offline):
    """Community-led held more rows than one page of 100."""
    events, calls = scrape("Cambridge Foundry", monkeypatch)
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 3 and len(set(posts)) == 2
    assert {e.title for e in events} >= {"Introduction to Glass Fusing", "Zumba with Darren"}


def test_foundry_skips_runs_and_closures(monkeypatch, offline):
    rows = _foundry_rows()

    def span(r):
        start = datetime.fromisoformat(r["8EB5s"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(r["zEQvV"].replace("Z", "+00:00"))
        return (end - start).total_seconds() / 3600

    runs = {r["kqYSz"] for r in rows if r.get("zEQvV") and span(r) > 24}
    closures = [r for r in rows if NOT_AN_EVENT.search(r["kqYSz"])]
    assert {"Jimmy's Café", "& again", "Foundry Festival 2027!"} <= runs
    assert closures, "fixture should hold 'Closed for ...' rows"

    events, _ = scrape("Cambridge Foundry", monkeypatch)
    titles = {e.title for e in events}
    assert not titles & {"Jimmy's Café", "& again", "Foundry Festival 2027!"}
    long_rows = [r for r in rows if r.get("zEQvV") and span(r) > 24]
    assert len(events) == len(rows) - len(long_rows) - len(closures)


def test_foundry_keeps_a_start_whose_end_was_typed_wrong(monkeypatch, offline):
    """The 2 PM Sugarplum Fairy show ends, per the table, the day before."""
    events, _ = scrape("Cambridge Foundry", monkeypatch)
    show = [e for e in events if e.title == "The Sugarplum Fairy & Her Friends"
            and e.start_datetime == datetime(2026, 11, 28, 14, 0)]
    assert len(show) == 1 and show[0].end_datetime is None


def test_foundry_categories_fall_back_to_its_own_labels(monkeypatch, offline):
    events, _ = scrape("Cambridge Foundry", monkeypatch)
    by_title = {e.title: e.category for e in events}
    assert by_title["Zumba with Darren"] == EventCategory.SPORTS                         # "Movement"
    assert by_title["Improv Comedy: Expanding Your Viewpoint"] == EventCategory.THEATER  # title
    assert by_title["Introduction to Glass Fusing"] == EventCategory.ARTS_CULTURE       # maker class


def test_foundry_fails_loudly_when_a_list_loses_its_fields(monkeypatch, offline):
    recorded = edited("Cambridge Foundry", "c199b253-107f-4a6b-a7aa-380cf21599e5/metadata",
                      '"name":"Start"', '"name":"Begins"')
    with pytest.raises(ValueError, match="Start"):
        scrape("Cambridge Foundry", monkeypatch, recorded)


# --------------------------------------------------------------------------- #
# Central Square BID — one request per day
# --------------------------------------------------------------------------- #

def test_bid_asks_for_each_day_of_its_window(monkeypatch, offline):
    from src.scrapers.central_square_bid import WINDOW_DAYS

    _, calls = scrape("Central Square BID", monkeypatch)
    days = [url.rsplit("=", 1)[-1] for _, url in calls]
    assert days[0] == "2026-11-16" and len(days) == WINDOW_DAYS == len(set(days))


def test_bid_times_and_places(monkeypatch, offline):
    events, _ = scrape("Central Square BID", monkeypatch)
    by_title = {e.title: e for e in events}
    assert by_title["Live Performance : Komai"].start_datetime == datetime(2026, 11, 20, 12, 0)
    assert by_title["Central On Air presents: Nomadik"].start_datetime == datetime(2026, 11, 20, 17, 0)
    # A location link whose target is the street address
    assert by_title["Pizza w/ CARE Team"].street_address == "632 Massachusetts Ave"
    assert by_title["Pizza w/ CARE Team"].venue_name == "Carl Barron Plaza"
    # A location given as plain text, with no link
    assert by_title["Claude's House Community Support Table"].street_address == "620 MASSACHUSETTS AVENUE SUITE 3"
    assert {e.city for e in events} == {"Cambridge"}


def test_bid_refuses_a_list_headed_with_another_day(monkeypatch, offline):
    """If the server answers with some other day's list, its events stay off."""
    recorded = edited("Central Square BID", "day=2026-11-20", "Friday, November 20, 2026",
                      "Friday, November 27, 2026")
    events, _ = scrape("Central Square BID", monkeypatch, recorded)
    assert not [e for e in events if e.start_datetime.date() == date(2026, 11, 20)]
    assert [e for e in events if e.start_datetime.date() == date(2026, 11, 21)]


def test_bid_listing_without_a_time_is_all_day_and_a_garbled_one_is_skipped(monkeypatch, offline):
    recorded = edited("Central Square BID", "day=2026-11-20", "12:00pm", "")
    events, _ = scrape("Central Square BID", monkeypatch, recorded)
    komai = [e for e in events if e.title == "Live Performance : Komai"]
    assert len(komai) == 1 and komai[0].all_day and komai[0].start_datetime == datetime(2026, 11, 20)

    recorded = edited("Central Square BID", "day=2026-11-20", "12:00pm", "12 noon-ish 13:00")
    events, _ = scrape("Central Square BID", monkeypatch, recorded)
    assert not [e for e in events if e.title == "Live Performance : Komai"]


# --------------------------------------------------------------------------- #
# Cambridge KiOSK
# --------------------------------------------------------------------------- #

def test_kiosk_reads_each_upcoming_page_and_skips_the_installation(monkeypatch, offline):
    events, calls = scrape("Cambridge KiOSK", monkeypatch)
    pages = [url for _, url in calls[1:]]
    assert "https://cambridgekiosk.org/events/harvard-square-gift-shop/" in pages  # read ...
    assert "Harvard square gift shop" not in {e.title for e in events}               # ... and skipped
    # The same two listings the City of Cambridge calendar carries, at its times
    starts = {(e.title.lower(), e.start_datetime) for e in events}
    assert ("radical collage collective", datetime(2026, 11, 18, 17, 0)) in starts
    assert ("out of town sounds", datetime(2026, 11, 20, 18, 0)) in starts
    assert {e.venue_name for e in events} == {"Cambridge KiOSK"}


def test_kiosk_skips_an_event_whose_calendar_link_disagrees(monkeypatch, offline):
    recorded = edited("Cambridge KiOSK", "cousins-at-the-kiosk", "dates=20261129T220000Z",
                      "dates=20261129T230000Z")
    events, _ = scrape("Cambridge KiOSK", monkeypatch, recorded)
    assert "Cousins at the kiosk" not in {e.title for e in events}
    assert len(events) == 2


def test_kiosk_follows_the_load_more_list(monkeypatch, offline):
    """Upcoming events past the first four are in a JSON array, not links."""
    from src.scrapers.cambridge_kiosk import CambridgeKioskScraper

    listing = next(e["body"] for e in exchanges("Cambridge KiOSK") if e["url"].endswith("/events/"))
    upcoming_id = listing.split('id="event-list-', 1)[1].split('"', 1)[0]
    extra = '[{"permalink":"https:\\/\\/cambridgekiosk.org\\/events\\/one-more\\/","date":"December 4"}]'
    listing = listing.replace(f"const additionalEvents_{upcoming_id} = [];",
                              f"const additionalEvents_{upcoming_id} = {extra};")
    links = CambridgeKioskScraper().upcoming_links(listing)
    assert links[-1] == "https://cambridgekiosk.org/events/one-more/"
    assert "https://cambridgekiosk.org/events/busking-at-the-kiosk/" not in links  # a past event


# --------------------------------------------------------------------------- #
# Cambridge Center for Adult Education
# --------------------------------------------------------------------------- #

def test_ccae_dates_come_from_each_event_pages_json_ld(monkeypatch, offline):
    events, calls = scrape("Cambridge Center for Adult Education", monkeypatch)
    # Two listings, then each event page once though both listings link the readings
    assert len(calls) == len({url for _, url in calls})
    starts = {e.title: e.start_datetime for e in events}
    assert starts["Blacksmith House Poetry Series - Jennifer Barber and David Orr"] == datetime(2026, 11, 16, 20, 0)
    assert starts["Blacksmith House Poetry Series - John Skoyles and Clay Ventre"] == datetime(2026, 11, 23, 20, 0)
    # Listed with no year ("January 8"); the event page says 2027
    jan = [t for t in starts if t.startswith("Community Event: A Message for You")]
    assert jan and starts[jan[0]] == datetime(2027, 1, 8, 18, 0)


def test_ccae_places_the_readings_at_the_blacksmith_house(monkeypatch, offline):
    events, _ = scrape("Cambridge Center for Adult Education", monkeypatch)
    readings = [e for e in events if e.title.startswith("Blacksmith House Poetry Series")]
    others = [e for e in events if e not in readings]
    assert readings and {(e.venue_name, e.street_address) for e in readings} == {("Blacksmith House", "56 Brattle Street")}
    assert others and {e.street_address for e in others} == {"42 Brattle Street"}
    assert {e.category for e in readings} == {EventCategory.LECTURES}


def test_ccae_skips_a_start_without_an_offset(monkeypatch, offline):
    recorded = edited("Cambridge Center for Adult Education", "skoyles-and-ventre",
                      '"startDate":"2026-11-23T20:00:00-05:00"', '"startDate":"2026-11-23T20:00:00"')
    events, _ = scrape("Cambridge Center for Adult Education", monkeypatch, recorded)
    assert not [e for e in events if "Skoyles" in e.title]
    assert [e for e in events if "Barber" in e.title]


# --------------------------------------------------------------------------- #
# Cambridge Art Association — NeonCRM RSS
# --------------------------------------------------------------------------- #

def test_caa_reads_one_occurrence_per_item_and_its_gallery(monkeypatch, offline):
    events, calls = scrape("Cambridge Art Association", monkeypatch)
    assert len(calls) == 1
    by_title = {e.title: e for e in events}
    # The prose lists nine Tuesdays; the feed names one, and only that one is listed.
    drawing = [e for e in events if e.title == "Fall 2026 Figure Drawing"]
    assert [e.start_datetime for e in drawing] == [datetime(2026, 12, 1, 18, 30)]
    assert drawing[0].end_datetime == datetime(2026, 12, 1, 20, 30)
    assert drawing[0].venue_name == "Kathryn Schultz Gallery"
    market = by_title["Meet the Artists and Visit the Canal District Kendall Winter Market"]
    assert market.venue_name == "CAA Canal Gallery" and market.start_datetime == datetime(2026, 12, 12, 11, 0)


def test_caa_skips_an_item_whose_pubdate_disagrees(monkeypatch, offline):
    recorded = edited("Cambridge Art Association", "feed", "Wed, 02 Dec 2026 18:00:00 EST",
                      "Wed, 02 Dec 2026 19:00:00 EST")
    events, _ = scrape("Cambridge Art Association", monkeypatch, recorded)
    assert "miniCRITS - FREE" not in {e.title for e in events}
    assert len(events) == 2


def test_caa_skips_an_item_at_neither_gallery(monkeypatch, offline):
    recorded = edited("Cambridge Art Association", "feed", "LocationKathryn Schultz Gallery, 25 Lowell St.",
                      "LocationSomewhere Else, 1 Main St.")
    events, _ = scrape("Cambridge Art Association", monkeypatch, recorded)
    assert "Fall 2026 Figure Drawing" not in {e.title for e in events}
