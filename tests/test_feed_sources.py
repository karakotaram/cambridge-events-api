"""Sources read through the shared feed readers: base_tribe and base_squarespace.

Each fixture is the venue's own feed as served on 2026-11-16, trimmed to the
fields the scrapers read (descriptions and bodies cut short). Two sources had
nothing upcoming that day — Kendall Center (summer only) and First Street
Market (between postings) — so their fixtures hold recent past events, served
to the scraper as if upcoming.

Recapture with the same shape if a feed changes; the tests assert behaviour
(what is kept, dropped, placed, timed), not counts typed from a live site.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
import requests

from src.models.event import EventCategory
from src.quality.invariants import check_invariants
from src.scrapers.base_feed import LOCAL_CITIES, NOT_AN_EVENT, BaseFeedScraper, city_of
from src.sources import BY_NAME
from tests.conftest import read_fixture

FIXTURE = "feed-2026-11-16.json.gz"

TRIBE = {
    "Lizard Lounge": "lizard_lounge",
    "Assembly Row": "assembly_row",
    "Blue Heron": "blue_heron",
    "Gallery 263": "gallery_263",
    "Kendall Center": "kendall_center",
}
SQUARESPACE = {
    "Cambridge Symphony Orchestra": "cambridge_symphony",
    "Canal District Kendall": "canal_district",
    "Somerville Museum": "somerville_museum",
    "Somerville Music Spaces": "somerville_music_spaces",
    "Brickbottom Gallery": "brickbottom",
    "Café Zing": "cafe_zing",
    "Remnant Brewing": "remnant",
    "First Street Market": "first_street_market",
}
# Fixtures captured from past events because nothing was upcoming.
FROM_PAST = {"First Street Market"}


def payload(module: str) -> dict:
    return json.loads(read_fixture(module, FIXTURE))


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


def serve_tribe(monkeypatch, data: dict, calls: list):
    pages = data["pages"]

    def fake_get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        n = int((params or {}).get("page", 1))
        return _Response(pages[n - 1]) if n <= len(pages) else _Response({}, 404)

    monkeypatch.setattr(requests, "get", fake_get)


def serve_squarespace(monkeypatch, data: dict, calls: list):
    def fake_get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        return _Response(data)

    monkeypatch.setattr(requests, "get", fake_get)


def scrape(source_name: str, monkeypatch, data: dict | None = None, scraper=None):
    """Run a registered feed source offline over its fixture; return (events, calls)."""
    calls: list = []
    if source_name in TRIBE:
        serve_tribe(monkeypatch, data or payload(TRIBE[source_name]), calls)
    else:
        data = data or payload(SQUARESPACE[source_name])
        if source_name in FROM_PAST:
            data = {**data, "upcoming": data["past"]}
        serve_squarespace(monkeypatch, data, calls)
    scraper = scraper or BY_NAME[source_name].load()
    return scraper.scrape_events(), calls


# --------------------------------------------------------------------------- #
# Every feed source
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source_name", sorted({**TRIBE, **SQUARESPACE}))
def test_feed_source_parses_clean(source_name, monkeypatch, offline):
    events, _ = scrape(source_name, monkeypatch)
    assert events, f"{source_name}: fixture yielded no events"

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)

    assert {e.source_name for e in events} == {source_name}
    assert all(e.city in LOCAL_CITIES for e in events if e.venue_name != "Online")
    assert not [e for e in events if e.start_datetime.second or e.start_datetime.microsecond]
    assert not [e for e in events if NOT_AN_EVENT.search(e.title)]
    assert all(len(e.description) >= 20 for e in events)
    assert not [e for e in events if "&#" in e.title or "&amp;" in e.title]


# --------------------------------------------------------------------------- #
# Tribe
# --------------------------------------------------------------------------- #

def test_lizard_lounge_reads_every_page_and_drops_placeholders(monkeypatch, offline):
    """241 API entries on 2026-11-16, of which 210 were all-day
    'No Event Tonight-Check Our Calendar For Upcoming Events' fillers."""
    data = payload("lizard_lounge")
    items = [e for page in data["pages"] for e in page["events"]]
    real = [e for e in items if "no event tonight" not in e["title"].lower()]
    assert len(real) < len(items)

    events, calls = scrape("Lizard Lounge", monkeypatch, data)
    assert [c[1]["page"] for c in calls] == list(range(1, len(data["pages"]) + 1))
    assert len(events) == len(real)
    assert {e.venue_name for e in events} == {"Lizard Lounge"}


def test_blue_heron_keeps_cambridge_and_drops_its_new_york_dates(monkeypatch, offline):
    data = payload("blue_heron")
    items = data["pages"][0]["events"]
    new_york = {(e["title"], e["start_date"]) for e in items if (e["venue"] or {}).get("city") == "New York"}
    assert new_york, "fixture should include the touring dates"

    events, _ = scrape("Blue Heron", monkeypatch, data)
    assert len(events) == len(items) - len(new_york)
    assert {e.city for e in events} == {"Cambridge"}
    # Evening concerts stay on their own day: 8 PM Dec 18 is 01:00 UTC Dec 19.
    assert datetime(2026, 12, 18, 20, 0) in {e.start_datetime for e in events}


def test_gallery_263_uses_its_own_address_and_lists_virtual_workshops_online(monkeypatch, offline):
    events, _ = scrape("Gallery 263", monkeypatch)
    virtual = [e for e in events if e.title.upper().startswith("VIRTUAL")]
    assert virtual and all(e.venue_name == "Online" and e.street_address is None for e in virtual)
    at_gallery = [e for e in events if e.venue_name == "Gallery 263"]
    assert at_gallery and all(e.city == "Cambridge" and e.street_address == "263 Pearl Street" for e in at_gallery), \
        "the feed's venue record spells it 'Cambride'"


def test_kendall_center_reads_its_utc_labelled_wall_clock_as_eastern(monkeypatch, offline):
    """The site is set to UTC+0 but its editors type local times."""
    events, _ = scrape("Kendall Center", monkeypatch)
    starts = {(e.title, e.start_datetime) for e in events}
    assert ("Community Yoga", datetime(2025, 7, 23, 18, 0)) in starts
    assert ("Sofar Sounds @ Urban Park", datetime(2025, 7, 24, 19, 30)) in starts


def test_tribe_site_in_a_foreign_timezone_is_not_guessed(monkeypatch, offline):
    """Without WALL_CLOCK_IS_EASTERN, a non-Eastern site's events are skipped."""
    from src.scrapers.kendall_center import KendallCenterScraper

    class Unverified(KendallCenterScraper):
        WALL_CLOCK_IS_EASTERN = False

    events, _ = scrape("Kendall Center", monkeypatch, scraper=Unverified())
    assert events == []


# --------------------------------------------------------------------------- #
# Squarespace
# --------------------------------------------------------------------------- #

def test_cambridge_symphony_keeps_only_local_concerts(monkeypatch, offline):
    data = payload("cambridge_symphony")
    elsewhere = [i for i in data["upcoming"]
                 if city_of(i["location"]["addressLine2"]) not in LOCAL_CITIES]
    assert elsewhere, "fixture should include Boston/Arlington/Watertown concerts"

    events, calls = scrape("Cambridge Symphony Orchestra", monkeypatch, data)
    assert calls == [("https://www.cambridgesymphony.org/concerts", {"format": "json"})]
    assert len(events) == len(data["upcoming"]) - len(elsewhere)
    assert all(e.city in LOCAL_CITIES for e in events)
    # The title states the time: epoch ms in UTC must land on it.
    by_title = {e.title: e.start_datetime for e in events}
    assert by_title["Family Concert (2:00 PM)"].hour == 14
    assert by_title["Family Concert (4:00 PM)"].hour == 16


def test_remnant_names_each_room_and_drops_closures(monkeypatch, offline):
    data = payload("remnant")
    closures = [i for i in data["upcoming"] if NOT_AN_EVENT.search(i["title"])]
    assert closures, "fixture should include closure notices"

    events, _ = scrape("Remnant Brewing", monkeypatch, data)
    assert len(events) == len(data["upcoming"]) - len(closures)
    assert {e.city for e in events} <= {"Cambridge", "Somerville"}
    assert "Satellite" in {e.venue_name for e in events}


def test_single_venue_sources_fall_back_to_home(monkeypatch, offline):
    """Brickbottom and Café Zing give no location at all."""
    for name, street in (("Brickbottom Gallery", "1 Fitchburg Street"), ("Café Zing", "25 White Street")):
        events, _ = scrape(name, monkeypatch)
        assert events and {e.street_address for e in events} == {street}


def test_squarespace_site_in_a_foreign_timezone_is_refused(monkeypatch, offline):
    """Native Plant Community Gardeners' site is set to America/Los_Angeles,
    which shifts every typed time by three hours. Fail loudly rather than
    publish those times."""
    data = payload("somerville_museum")
    data["website"]["timeZone"] = "America/Los_Angeles"
    with pytest.raises(ValueError, match="not Eastern"):
        scrape("Somerville Museum", monkeypatch, data)


def test_squarespace_payload_without_upcoming_fails_loudly(monkeypatch, offline):
    with pytest.raises(ValueError, match="no 'upcoming'"):
        scrape("Café Zing", monkeypatch, {"website": {"timeZone": "America/New_York"}, "items": []})


# --------------------------------------------------------------------------- #
# base_feed rules
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title", [
    "No Event Tonight-Check Our Calendar For Upcoming Events",
    "SATELLITE: Closed for a Private Event from 2 - 9 pm",
    "REMNANT TAPROOM: Closed for Thanksgiving!",
    "Closed",
    "Private Party",
    "Members-only preview",
])
def test_non_events_are_recognised(title):
    assert NOT_AN_EVENT.search(title)


@pytest.mark.parametrize("title", [
    "Behind Closed Doors",
    "Legends of Mary",
    "Private Lives (a comedy by Noël Coward)",
])
def test_real_titles_are_not_mistaken_for_non_events(title):
    assert not NOT_AN_EVENT.search(title)


@pytest.mark.parametrize("title, category", [
    ("Thanksgiving Improv Comedy Show", EventCategory.THEATER),
    ("Skip the Small Talk: All Ages!", EventCategory.COMMUNITY),
    ("Artist Talk: Threads That Bind", EventCategory.LECTURES),
    ("Songwriters in the Round", EventCategory.MUSIC),
    ("Opening Reception - Figure Drawing", EventCategory.ARTS_CULTURE),
    ("Legends of Mary", EventCategory.COMMUNITY),
])
def test_category_comes_from_the_title(title, category):
    assert BaseFeedScraper.categorize(type("S", (), {"CATEGORY": EventCategory.COMMUNITY})(), title) == category


@pytest.mark.parametrize("text, city", [
    ("Cambridge, MA, 02139", "Cambridge"),
    ("Somerville", "Somerville"),
    ("Boston, MA 02115", "Boston"),
    ("Cambridge, Massachusetts 02142", "Cambridge"),
    ("", None),
])
def test_city_of(text, city):
    assert city_of(text) == city
