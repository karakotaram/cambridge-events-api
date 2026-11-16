"""Batch 2, group E: MIT, Kendall Square and university sources.

Fixtures are each site's pages or feeds as served on 2026-11-16, trimmed where
noted. Three sources had little or nothing upcoming that day, so they are also
tested against recent past listings served as if current:

  - MIT Literature: its only upcoming entries were the excluded Lit Tea and a
    Boston event; `feed-fall-past` is September-November 2026.
  - MIT Art, Culture and Technology: one upcoming lecture; `feed-fall-past`
    holds the fall's earlier three, with their pages.
  - Cambridge Crossing: a summer programme; the file holds 2025-2026 seasons.

Global Arts Live's fixtures are every page one live run read (the season list,
venue pages, runs' pages), trimmed to their content blocks, with a manifest
from URL to file. MIT Museum's is the listing and program data one live run
fetched through the browser, trimmed to the fields read; no browser starts in
these tests.

The tests assert behaviour (what is kept, dropped, placed and timed), not
counts typed from a live site.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT
from src.sources import BY_NAME
from tests.conftest import FIXTURES, read_fixture

SOURCES = (
    "MIT Art, Culture and Technology",
    "MIT Literature",
    "MIT List Visual Arts Center",
    "Venture Café Cambridge",
    "Cambridge Crossing",
    "Global Arts Live",
)


class _Response:
    def __init__(self, body=None, status=200, text=""):
        self._body, self.status_code, self.text = body, status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


def assert_clean(events, source_name):
    assert events, f"{source_name}: fixture yielded no events"
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
    assert BY_NAME[source_name].name == source_name
    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if e.start_datetime.tzinfo is not None]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title]


def tribe_pages(module, name):
    return json.loads(read_fixture(module, name))["pages"]


def serve_tribe(monkeypatch, pages, html_by_url=None, calls=None):
    """Fake requests.get for a Tribe API (paged) plus any event pages."""
    calls = calls if calls is not None else []
    html_by_url = html_by_url or {}

    def fake_get(url, params=None, **kwargs):
        calls.append((url, dict(params or {}), kwargs))
        if "/wp-json/tribe/events/v1/events" in url:
            n = int((params or {}).get("page", 1))
            return _Response(pages[n - 1]) if n <= len(pages) else _Response({}, 404)
        if url in html_by_url:
            return _Response(text=html_by_url[url])
        return _Response(status=404)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


@pytest.mark.parametrize("source_name", SOURCES)
def test_every_group_e_source_is_registered_as_plain_http(source_name):
    source = BY_NAME[source_name]
    assert source.kind == "requests"
    assert source.load().source_name == source_name


# --------------------------------------------------------------------------- #
# MIT Art, Culture and Technology
# --------------------------------------------------------------------------- #

ACT_PAGES = {
    "https://act.mit.edu/event/carolyn-christov-bakargiev/": "event-carolyn-christov-bakargiev.html.gz",
    "https://act.mit.edu/event/library-stack/": "event-library-stack.html.gz",
    "https://act.mit.edu/event/paola-bertucci-invented-pasts-past-inventions/":
        "event-paola-bertucci-invented-pasts-past-inventions.html.gz",
}


def act_html(edit=lambda url, html: html):
    pages = {url: edit(url, read_fixture("mit_act", name)) for url, name in ACT_PAGES.items()}
    for page in tribe_pages("mit_act", "feed-fall-past-2026-11-16.json.gz"):
        for item in page["events"]:
            if item["title"].startswith("META 5"):
                pages[item["url"]] = edit(item["url"], read_fixture("mit_act", "event-meta-5.html.gz"))
    return pages


def test_act_lecture_today_is_placed_from_its_page(monkeypatch, offline):
    from src.scrapers.mit_act import CA_CHAIN

    calls = serve_tribe(monkeypatch, tribe_pages("mit_act", "feed-2026-11-16.json.gz"), act_html())
    events = BY_NAME["MIT Art, Culture and Technology"].load().scrape_events()
    assert_clean(events, "MIT Art, Culture and Technology")

    [lecture] = events
    assert (lecture.title, lecture.start_datetime) == ("Carolyn Christov-Bakargiev", datetime(2026, 11, 16, 18, 0))
    assert (lecture.venue_name, lecture.street_address, lecture.city, lecture.zip_code) == \
        ("MIT E15-070, Wiesner Building", "20 Ames Street", "Cambridge", "02142")
    assert lecture.category == EventCategory.LECTURES.value

    api = [c for c in calls if "/wp-json/" in c[0]]
    assert api and all(c[1]["categories"] == "lecture-series" for c in api)
    # act.mit.edu omits its intermediate certificate: every request verifies
    # against the shipped chain, never with verification off.
    assert all(c[2].get("verify") == CA_CHAIN for c in calls)
    assert Path(CA_CHAIN).read_text().count("BEGIN CERTIFICATE") == 2


def test_act_reads_rooms_with_and_without_a_city_line(monkeypatch, offline):
    serve_tribe(monkeypatch, tribe_pages("mit_act", "feed-fall-past-2026-11-16.json.gz"), act_html())
    events = BY_NAME["MIT Art, Culture and Technology"].load().scrape_events()
    assert_clean(events, "MIT Art, Culture and Technology")

    where = {e.title: (e.venue_name, e.street_address, e.city) for e in events}
    assert where["Library Stack"] == ("Bartos Theatre, MIT E15-070, Wiesner Building", "20 Ames Street", "Cambridge")
    # "MIT Building 12 / Room 0168" and "Sidara Auditorium (W41- 1216), MET
    # Warehouse / 134 Massachusetts Ave" print no city; both name MIT rooms.
    assert where["Paola Bertucci | Invented Pasts, Past Inventions"] == \
        ("MIT Building 12, Room 0168", None, "Cambridge")
    meta = next(v for k, v in where.items() if k.startswith("META 5"))
    assert meta == ("Sidara Auditorium (W41- 1216), MET Warehouse", "134 Massachusetts Ave", "Cambridge")

    starts = {e.title: e.start_datetime for e in events}
    assert starts["Paola Bertucci | Invented Pasts, Past Inventions"] == datetime(2026, 10, 26, 16, 30)


def test_act_skips_a_lecture_whose_place_is_not_visibly_mit(monkeypatch, offline):
    def strip_mit(url, html):
        return html.replace("MIT Building 12", "Somewhere Hall") if "bertucci" in url else html

    serve_tribe(monkeypatch, tribe_pages("mit_act", "feed-fall-past-2026-11-16.json.gz"), act_html(strip_mit))
    titles = {e.title for e in BY_NAME["MIT Art, Culture and Technology"].load().scrape_events()}
    assert "Library Stack" in titles
    assert "Paola Bertucci | Invented Pasts, Past Inventions" not in titles


# --------------------------------------------------------------------------- #
# MIT Literature
# --------------------------------------------------------------------------- #

def test_literature_drops_the_endless_lit_tea_and_the_boston_series(monkeypatch, offline):
    pages = tribe_pages("mit_literature", "feed-2026-11-16.json.gz")
    items = [e for p in pages for e in p["events"]]
    teas = [e for e in items if any(c["slug"] == "tea-time" for c in e["categories"])]
    assert teas and max(e["start_date"] for e in teas) > "2027-12", "the tea recurs without end"
    assert len(teas) < len(items)

    calls = serve_tribe(monkeypatch, pages)
    events = BY_NAME["MIT Literature"].load().scrape_events()
    assert events == []
    assert [c[1]["page"] for c in calls] == list(range(1, len(pages) + 1))


def test_literature_keeps_cambridge_talks(monkeypatch, offline):
    pages = tribe_pages("mit_literature", "feed-fall-past-2026-11-16.json.gz")
    serve_tribe(monkeypatch, pages)
    events = BY_NAME["MIT Literature"].load().scrape_events()
    assert_clean(events, "MIT Literature")

    items = [e for p in pages for e in p["events"]]
    elsewhere = [e for e in items if isinstance(e["venue"], dict) and e["venue"].get("city") in ("Boston", "Brookline")]
    assert elsewhere, "fixture should include the French Library and Coolidge Corner events"

    titles = {e.title for e in events}
    assert not [t for t in titles if "French Library" in t or "Coolidge" in t or "Lit Tea" in t]
    by_title = {e.title: e for e in events}
    seminar = by_title["Global France Seminar: The Suicide Archive"]
    assert (seminar.start_datetime, seminar.venue_name, seminar.city) == \
        (datetime(2026, 9, 28, 17, 15), "MIT Building 14E-304", "Cambridge")
    # Entities decoded: the feed writes "Ancient &#038; Medieval"
    assert any(t.startswith("Ancient & Medieval Studies Colloquium") for t in titles)


def test_literature_lists_a_virtual_venue_online(monkeypatch, offline):
    pages = tribe_pages("mit_literature", "feed-fall-past-2026-11-16.json.gz")
    item = next(e for e in pages[0]["events"] if e["title"].startswith("Oedipus"))
    item["venue"] = {"venue": "Virtual", "address": None, "city": None, "zip": None}
    serve_tribe(monkeypatch, pages)
    events = BY_NAME["MIT Literature"].load().scrape_events()
    oedipus = next(e for e in events if e.title.startswith("Oedipus"))
    assert (oedipus.venue_name, oedipus.city) == ("Online", None)


# --------------------------------------------------------------------------- #
# MIT List Visual Arts Center
# --------------------------------------------------------------------------- #

LIST_PAGES = {
    "https://listart.mit.edu/calendar": "calendar-2026-11-16.html.gz",
    "https://listart.mit.edu/calendar/curator-tour-uri-aran-hao-jingban": "event-curator-tour-uri-aran-hao-jingban.html.gz",
    "https://listart.mit.edu/calendar/art-break-8": "event-art-break-8.html.gz",
    "https://listart.mit.edu/calendar/images-work-nonperformance-camera": "event-images-work-nonperformance-camera.html.gz",
    "https://listart.mit.edu/calendar/west-campus-public-art-tour-4": "event-west-campus-public-art-tour-4.html.gz",
}


def list_scraper(cls=None, edit=lambda url, html: html):
    scraper = (cls or BY_NAME["MIT List Visual Arts Center"].load().__class__)()
    scraper.requested = []

    def fetch(url, *a, **k):
        scraper.requested.append(url)
        if url not in LIST_PAGES:
            raise requests.HTTPError(f"no fixture for {url}")
        return edit(url, read_fixture("mit_list_center", LIST_PAGES[url]))

    scraper.fetch_html = fetch
    return scraper


def test_list_center_occurrences_and_times(offline):
    scraper = list_scraper()
    events = scraper.scrape_events()
    assert_clean(events, "MIT List Visual Arts Center")

    when = sorted((e.title, e.start_datetime, e.end_datetime) for e in events)
    assert when == [
        ("Art Break", datetime(2026, 12, 9, 12, 0), datetime(2026, 12, 9, 19, 0)),
        ("Art Break", datetime(2026, 12, 10, 12, 0), datetime(2026, 12, 10, 19, 0)),
        ("Art Break", datetime(2026, 12, 11, 12, 0), datetime(2026, 12, 11, 18, 0)),
        ("Curator Tour: Uri Aran and Hao Jingban", datetime(2026, 12, 3, 17, 30), datetime(2026, 12, 3, 18, 30)),
    ]
    tour = next(e for e in events if e.title.startswith("Curator Tour"))
    assert (tour.venue_name, tour.street_address, tour.zip_code) == \
        ("MIT List Visual Arts Center", "20 Ames Street", "02139")
    assert tour.registration_required and tour.cost == "Free, but registration required."


def test_list_center_skips_members_previews_without_fetching_them(offline):
    scraper = list_scraper()
    cards = scraper.parse_listing(read_fixture("mit_list_center", "calendar-2026-11-16.html.gz"))
    preview = next(c for c in cards if c["title"].startswith("Preview"))
    assert "Member Event" in preview["types"]

    events = scraper.scrape_events()
    assert preview["url"] not in scraper.requested
    assert not [e for e in events if e.title.startswith("Preview")]
    # Only the upcoming section is read; the page also shows recent past events
    assert not [c for c in cards if c["title"] == "Sound Performance: Eric Wong"]


def test_list_center_past_tour_at_a_street_address_and_a_span_skipped(offline):
    from src.scrapers.mit_list_center import MITListCenterScraper

    class Past(MITListCenterScraper):
        SECTION = ".section--view--events-past"

    scraper = list_scraper(Past)
    cards = {c["url"]: c for c in scraper.parse_listing(read_fixture("mit_list_center", "calendar-past-2026-11-16.html.gz"))}

    tour_url = "https://listart.mit.edu/calendar/west-campus-public-art-tour-4"
    [tour] = scraper.parse_event(cards[tour_url], scraper.fetch_html(tour_url))
    assert (tour.start_datetime, tour.end_datetime) == (datetime(2026, 8, 13, 17, 30), datetime(2026, 8, 13, 18, 30))
    assert (tour.venue_name, tour.street_address, tour.city, tour.zip_code) == \
        ("77 Massachusetts Ave", "77 Massachusetts Ave", "Cambridge", "02139")
    assert tour.category == EventCategory.ARTS_CULTURE.value

    # "June 23 - July 12, all day" is a programme's run, not one occasion
    span_url = "https://listart.mit.edu/calendar/images-work-nonperformance-camera"
    assert scraper.parse_event(cards[span_url], scraper.fetch_html(span_url)) == []


def test_list_center_refuses_a_time_its_card_contradicts(offline):
    def shift(url, html):
        if "curator" not in url:
            return html
        assert ">December 3, 5:30<" in html
        return html.replace(">December 3, 5:30<", ">December 3, 6:30<").replace(">6:30PM<", ">7:30PM<")

    events = list_scraper(edit=shift).scrape_events()
    assert not [e for e in events if e.title.startswith("Curator Tour")]
    assert [e for e in events if e.title == "Art Break"]


@pytest.mark.parametrize("text, expected", [
    ("5:30 - 6:30PM", ((17, 30), (18, 30))),
    ("12 - 7PM", ((12, 0), (19, 0))),
    ("11AM - 1PM", ((11, 0), (13, 0))),
    ("11 - 1PM", ((11, 0), (13, 0))),
    ("12:15 - 1:15PM", ((12, 15), (13, 15))),
    ("6PM", ((18, 0), None)),
    ("all day", (None, None)),
    ("6", None),
    ("6 - 7", None),
])
def test_list_center_smart_date_times(text, expected):
    from src.scrapers.mit_list_center import parse_times
    assert parse_times(text) == expected


# --------------------------------------------------------------------------- #
# Venture Café Cambridge
# --------------------------------------------------------------------------- #

def venture(today=date(2026, 11, 16), edit=lambda html: html):
    from src.scrapers.venture_cafe import VentureCafeScraper
    scraper = VentureCafeScraper(today=today)
    scraper.fetch_html = lambda url, *a, **k: edit(read_fixture("venture_cafe", "programme-2026-11-16.html.gz"))
    return scraper


def test_venture_cafe_lists_only_the_scheduled_gatherings(offline):
    events = venture().scrape_events()
    assert_clean(events, "Venture Café Cambridge")
    assert [e.start_datetime for e in events] == [
        datetime(2026, 11, 19, 16, 30), datetime(2026, 12, 3, 16, 30),
        datetime(2026, 12, 10, 16, 30), datetime(2026, 12, 17, 16, 30),
    ], "no Thanksgiving gathering, and the past pane is not read"
    assert {(e.venue_name, e.street_address, e.zip_code) for e in events} == \
        {("Venture Café at CIC Cambridge", "One Broadway, 5th floor", "02142")}
    assert all(e.source_url.startswith("https://community.venturecafecambridge.org/events/") for e in events)


def test_venture_cafe_year_comes_from_the_weekday_not_the_reference(offline):
    """Read in January 2027, the same rows are still the 2026 Thursdays."""
    events = venture(today=date(2026, 12, 20)).scrape_events()
    assert datetime(2026, 12, 17, 16, 30) in {e.start_datetime for e in events}


def test_venture_cafe_skips_a_row_whose_weekday_lands_outside_the_coming_year(offline):
    # Nov 19 is a Thursday in 2026 and a Friday in 2027
    events = venture(edit=lambda html: html.replace('<div class="wd">Thursday,</div>\n\t\t<div class="d">19</div>',
                                                    '<div class="wd">Friday,</div>\n\t\t<div class="d">19</div>', 1)
                     ).scrape_events()
    assert datetime(2026, 11, 19, 16, 30) not in {e.start_datetime for e in events}
    assert not [e for e in events if e.start_datetime.year == 2027]
    assert len(events) == 3


def test_venture_cafe_fails_loudly_if_the_venue_changes(offline):
    with pytest.raises(ValueError, match="CIC Cambridge"):
        venture(edit=lambda html: html.replace("CIC Cambridge", "Somewhere Else")).scrape_events()


# --------------------------------------------------------------------------- #
# Cambridge Crossing
# --------------------------------------------------------------------------- #

def crossing_events(monkeypatch, data=None):
    data = data if data is not None else json.loads(read_fixture("cambridge_crossing", "events-2026-11-16.json.gz"))
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(data))
    return BY_NAME["Cambridge Crossing"].load().scrape_events(), data


def test_cambridge_crossing_reads_single_days_and_skips_seasons(monkeypatch, offline):
    events, data = crossing_events(monkeypatch)
    assert_clean(events, "Cambridge Crossing")

    spans = [i for i in data if i["start_date"] and i["end_date"] != i["start_date"]]
    signposts = [i for i in data if not i["start_date"]]
    assert spans and signposts
    assert len(events) == len(data) - len(spans) - len(signposts)
    assert not [e for e in events if "CX Fit" in e.title], "a season described in prose is not dated"

    when = {e.title: (e.start_datetime, e.end_datetime) for e in events}
    assert when["CX Summer Nights Feat. Jill McCracken + The Far Out"] == \
        (datetime(2026, 6, 26, 17, 30), datetime(2026, 6, 26, 20, 30))
    # The field says 3:00 PM; the blurb says 4:00 PM. The field is read.
    assert when["World Cup Watch Party at CX"][0] == datetime(2026, 7, 9, 15, 0)
    assert {(e.venue_name, e.city, e.zip_code) for e in events} == {("Cambridge Crossing", "Cambridge", "02141")}


def test_cambridge_crossing_unreadable_time_is_skipped_not_made_all_day(monkeypatch, offline):
    data = json.loads(read_fixture("cambridge_crossing", "events-2026-11-16.json.gz"))
    target = next(i for i in data if i["start_time"])
    target["start_time"] = "evening"
    events, _ = crossing_events(monkeypatch, data)
    assert not [e for e in events if e.title == target["title"].replace("&#8211;", "–")]


# --------------------------------------------------------------------------- #
# Global Arts Live
# --------------------------------------------------------------------------- #

GAL_DIR = "global_arts_live"
GAL_MANIFEST = json.loads((FIXTURES / GAL_DIR / "pages-2026-11-16.json").read_text())


def gal(edit=lambda url, html: html, missing=()):
    scraper = BY_NAME["Global Arts Live"].load()
    scraper.PAUSE = 0
    scraper.requested = []

    def fetch(url, *a, **k):
        scraper.requested.append(url)
        if url in missing or url not in GAL_MANIFEST:
            raise requests.HTTPError(f"no page for {url}")
        return edit(url, read_fixture(GAL_DIR, GAL_MANIFEST[url]))

    scraper.fetch_html = fetch
    return scraper


def test_global_arts_live_keeps_only_cambridge_and_somerville(offline):
    scraper = gal()
    events = scraper.scrape_events()
    assert_clean(events, "Global Arts Live")

    assert {e.city for e in events} == {"Cambridge", "Somerville"}
    assert not [e for e in events if "Berklee" in e.venue_name or "Majestic" in e.venue_name]
    assert {"https://www.globalartslive.org/content/venue/berklee-performance-center",
            "https://www.globalartslive.org/venue/emerson-cutler-majestic-theatre"} <= set(scraper.requested)
    # One read per venue, however many shows it has
    venue_reads = [u for u in scraper.requested if "/venue/" in u]
    assert len(venue_reads) == len(set(venue_reads))


def test_global_arts_live_reads_every_performance_of_a_run(offline):
    events = gal().scrape_events()
    cirque = sorted(e.start_datetime for e in events if e.title.startswith("CIRQUE KALABANT"))
    # The row prints only "Jan 29 - Jan 31"; the 30th is on the show's page
    assert cirque == [datetime(2027, 1, 29, 20, 0), datetime(2027, 1, 30, 20, 0), datetime(2027, 1, 31, 15, 0)]
    redman = sorted(e.start_datetime for e in events if e.title == "JOSHUA REDMAN QUARTET")
    assert redman == [datetime(2027, 1, 17, 16, 0), datetime(2027, 1, 17, 19, 30)]
    scofield = next(e for e in events if e.title.startswith("John Scofield"))
    assert (scofield.start_datetime, scofield.venue_name, scofield.street_address, scofield.city) == \
        (datetime(2026, 11, 19, 19, 30), "Somerville Theatre", "55 Davis Square", "Somerville")


def test_global_arts_live_places_both_platform_stages_at_the_platform(offline):
    events = gal().scrape_events()
    platform = [e for e in events if e.venue_name == "The Platform"]
    assert platform
    assert {(e.street_address, e.city, e.zip_code) for e in platform} == {("315 Kendall St", "Cambridge", "02142")}
    emden = next(e for e in platform if e.title == "NICOLÁS EMDEN")
    assert emden.start_datetime == datetime(2026, 11, 20, 18, 30)
    assert emden.description.endswith("(Tempo Stage at The Platform)")
    assert emden.cost == "Free"


def test_global_arts_live_skips_a_run_it_cannot_read_rather_than_half_list_it(offline):
    cirque_page = "https://www.globalartslive.org/content/event_page/10510/"
    assert cirque_page in GAL_MANIFEST
    events = gal(missing={cirque_page}).scrape_events()
    assert not [e for e in events if e.title.startswith("CIRQUE KALABANT")]
    assert [e for e in events if e.title == "JOSHUA REDMAN QUARTET"]


def test_global_arts_live_skips_a_run_whose_page_disagrees_with_its_row(offline):
    def drop_last_night(url, html):
        return html.replace('content="2027-01-31T15:00:00-05:00"', 'content="2027-01-30T15:00:00-05:00"') \
            if url.endswith("/10510/") else html

    events = gal(edit=drop_last_night).scrape_events()
    assert not [e for e in events if e.title.startswith("CIRQUE KALABANT")]


def test_global_arts_live_fixture_manifest_points_at_real_files():
    for name in GAL_MANIFEST.values():
        assert (FIXTURES / GAL_DIR / name).exists()
        gzip.decompress((FIXTURES / GAL_DIR / name).read_bytes())


# --------------------------------------------------------------------------- #
# MIT Museum
# --------------------------------------------------------------------------- #

def museum(today=date(2026, 11, 16), edit=lambda record: record):
    """The scraper over the recorded listing and program data; no browser starts."""
    from src.scrapers.mit_museum import MITMuseumScraper

    record = edit(json.loads(read_fixture("mit_museum", "programs-2026-11-16.json.gz")))
    scraper = MITMuseumScraper(today=today)
    scraper.fetched = []
    scraper.fetch_listing = lambda: record["listing"]

    def program(build_id, uri):
        scraper.fetched.append((build_id, uri))
        return record["programs"].get(uri)

    scraper.fetch_program = program
    return scraper, record


def test_mit_museum_is_a_headless_browser_source():
    source = BY_NAME["MIT Museum"]
    assert source.kind == "playwright"
    assert source.load().headless, "never a visible browser"


def test_mit_museum_one_event_per_dated_session(offline):
    scraper, record = museum()
    events = scraper.scrape_events()
    assert_clean(events, "MIT Museum")

    programs = record["listing"]["props"]["pageProps"]["programs"]
    assert [uri for _, uri in scraper.fetched] == [p["uri"] for p in programs]
    assert {b for b, _ in scraper.fetched} == {record["listing"]["buildId"]}

    when = {(e.title, e.start_datetime, e.end_datetime) for e in events}
    assert ("Author Talk | A Pox on Fools", datetime(2026, 11, 18, 18, 0), datetime(2026, 11, 18, 19, 0)) in when
    # "Thursday November 19" and "Friday January 29": two different years
    assert ("Science Paint Night: Seascapes", datetime(2026, 11, 19, 18, 0), datetime(2026, 11, 19, 20, 30)) in when
    assert ("Science Paint Night: Seascapes", datetime(2027, 1, 29, 18, 0), datetime(2027, 1, 29, 20, 30)) in when
    assert {(e.venue_name, e.street_address, e.city, e.zip_code) for e in events} == \
        {("MIT Museum", "314 Main St", "Cambridge", "02142")}


def test_mit_museum_old_sessions_never_borrow_this_years_calendar(offline):
    """Gallery Talks and Making Studio keep years of past sessions with no year
    printed ("Saturday March 21", "Monday December 15"). Each matches its own
    weekday only in a past year, so none is published."""
    scraper, record = museum()
    events = scraper.scrape_events()
    gallery = [s for s in record["programs"]["programs/gallery-talks"]["pageProps"]["entry"]["sessions"]
               if s["sessionStartDate"]]
    assert len(gallery) > 3
    published = [e for e in events if e.title.startswith("Gallery Talks")]
    assert [e.start_datetime for e in published] == [datetime(2026, 12, 4, 11, 0)]
    assert all(datetime(2026, 11, 9) <= e.start_datetime <= datetime(2027, 9, 12) for e in events)


def test_mit_museum_prose_schedule_is_not_dated(offline):
    """'Weekly, Wednesdays and Saturdays' on the card produces nothing by itself."""
    def drop_sessions(record):
        record["programs"]["programs/gallery-talks"]["pageProps"]["entry"]["sessions"] = []
        return record

    scraper, record = museum(edit=drop_sessions)
    card = next(p for p in record["listing"]["props"]["pageProps"]["programs"] if p["uri"] == "programs/gallery-talks")
    assert card["programDate"].startswith("Weekly")
    assert not [e for e in scraper.scrape_events() if e.title.startswith("Gallery Talks")]


def test_mit_museum_program_data_refused_skips_that_program_only(offline):
    def refuse(record):
        record["programs"]["programs/a-pox-on-fools"] = None
        return record

    scraper, _ = museum(edit=refuse)
    titles = {e.title for e in scraper.scrape_events()}
    assert "Author Talk | A Pox on Fools" not in titles
    assert "Science Paint Night: Seascapes" in titles
