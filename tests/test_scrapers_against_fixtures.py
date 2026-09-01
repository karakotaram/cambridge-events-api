"""Scrapers parsed against saved pages — offline, deterministic, fast.

Before these existed, the "scraper test" workflow fetched two live pages and
printed the first five events for a human to look at. It asserted nothing, so it
could not fail, so it never told anyone anything — including on the day the
Cambridge.gov scraper started stamping 117 events with a clock reading.

The rule that makes this accretive: **every scraper bug leaves a fixture
behind.** The page that caused the Sept 14 incident is in this repo now, and
that specific failure can never come back silently.

Capture a new one with:  cal scrape "<source>" --save-fixture
"""
from __future__ import annotations

from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def parse_with(source_name: str, html: str):
    """Run a scraper's parsing over saved HTML, with no network involved."""
    scraper = BY_NAME[source_name].load()
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    return scraper


# --------------------------------------------------------------------------- #
# City of Cambridge — the regression that started all of this
# --------------------------------------------------------------------------- #

@pytest.fixture
def cambridge_week(fixture_html):
    return BeautifulSoup(fixture_html("cambridge_gov", "week-2026-09-14.html.gz"), "html.parser")


def test_reported_events_parse_to_their_real_dates(cambridge_week, offline):
    """The two events a reader emailed about.

    Both were published on 2026-09-14. Danehy Park Family Day is actually on the
    19th; the Comedy Studio show is on the 16th at 5 PM.
    """
    scraper = BY_NAME["City of Cambridge"].load()
    week_start = datetime(2026, 9, 14)

    found = {}
    heading = None
    for node in cambridge_week.find_all("li", class_=["date", "eventItem"]):
        if "date" in (node.get("class") or []):
            heading = scraper.clean_text(node.get_text())
            continue
        link = node.find("a", href=lambda x: x and "/citycalendar/view.aspx?guid=" in x if x else False)
        if not link:
            continue
        title = scraper.clean_text(link.get_text())
        if "Danehy Park Family Day" in title or "Comedy Studio & Black Girl Magic" in title:
            found[title] = scraper.parse_item_datetime(node, heading, week_start)

    danehy = next(v for k, v in found.items() if "Danehy" in k)
    comedy = next(v for k, v in found.items() if "Comedy Studio" in k)
    assert danehy == datetime(2026, 9, 19, 11, 0)
    assert comedy == datetime(2026, 9, 16, 17, 0), (
        "5 PM must come from the visible text; the site's datetime attribute "
        "writes it as 05:00:00 on a 12-hour clock with no meridiem"
    )


def test_no_event_lands_on_a_clock_reading(cambridge_week, offline):
    """The exact failure: every undated row taking the scrape time plus N weeks.

    117 events shared 2026-09-14T13:29:26.288025. Nothing may carry sub-minute
    precision, and no two rows may collapse onto one timestamp in bulk.
    """
    from collections import Counter

    scraper = BY_NAME["City of Cambridge"].load()
    parsed, heading = [], None
    for node in cambridge_week.find_all("li", class_=["date", "eventItem"]):
        if "date" in (node.get("class") or []):
            heading = scraper.clean_text(node.get_text())
            continue
        when = scraper.parse_item_datetime(node, heading, datetime(2026, 9, 14))
        if when:
            parsed.append(when)

    assert len(parsed) > 100, f"fixture should yield a full week, got {len(parsed)}"
    assert not [p for p in parsed if p.second or p.microsecond]
    worst_time, worst_count = Counter(parsed).most_common(1)[0]
    assert worst_count <= 20, f"{worst_count} events collapsed onto {worst_time}"


def test_the_week_spans_seven_days(cambridge_week, offline):
    """A crashed browser used to truncate the run; the span is the tell."""
    scraper = BY_NAME["City of Cambridge"].load()
    days, heading = set(), None
    for node in cambridge_week.find_all("li", class_=["date", "eventItem"]):
        if "date" in (node.get("class") or []):
            heading = scraper.clean_text(node.get_text())
            continue
        when = scraper.parse_item_datetime(node, heading, datetime(2026, 9, 14))
        if when:
            days.add(when.date())
    assert len(days) >= 7, f"expected a full week of dates, got {sorted(days)}"


# --------------------------------------------------------------------------- #
# Every source with a fixture: parse it and hold it to the invariants
# --------------------------------------------------------------------------- #

FIXTURE_SOURCES = [
    ("Brattle Theatre", "brattle", "listing.html.gz"),
    ("Harvard Art Museums", "harvard_art_museums", "listing.html.gz"),
]


@pytest.mark.parametrize("source_name,module,filename", FIXTURE_SOURCES)
def test_scraper_output_satisfies_invariants(source_name, module, filename,
                                             fixture_html, offline):
    """Parse a saved page and hold the result to the same absolute rules the
    gate applies. Catches a scraper regressing into fabricated dates, chrome
    titles, or tz-aware datetimes without needing the venue to be online."""
    scraper = parse_with(source_name, fixture_html(module, filename))
    events = scraper.scrape_events()

    assert events, f"{source_name} parsed 0 events from its fixture — selector drift?"

    violations = check_invariants([e.model_dump(mode="json") for e in events])
    errors = [v for v in violations if v.severity == "error"]
    assert not errors, f"{source_name}:\n  " + "\n  ".join(str(v) for v in errors)


@pytest.mark.parametrize("source_name,module,filename", FIXTURE_SOURCES)
def test_scraper_declares_the_name_it_emits(source_name, module, filename,
                                            fixture_html, offline):
    """Events must land under the name the registry monitors."""
    scraper = parse_with(source_name, fixture_html(module, filename))
    events = scraper.scrape_events()
    assert {e.source_name for e in events} == {source_name}


# --------------------------------------------------------------------------- #
# The Rockwell — a JSON API rather than HTML
# --------------------------------------------------------------------------- #

def test_api_backed_scraper_satisfies_invariants(fixture_html, monkeypatch, offline):
    """Not every source is HTML. The Rockwell reads a Tribe Events REST API, so
    its fixture is the JSON body and its seam is requests.get, not fetch_html.

    Worth keeping as a distinct case: the first version of this test patched
    fetch_html, which this scraper never calls, and the `offline` fixture caught
    it reaching the real API instead of quietly passing over live data.
    """
    import json as _json

    import requests

    payload = _json.loads(read_fixture("rockwell", "events-api-page1.json.gz"))

    class _Response:
        status_code = 200

        def raise_for_status(self): ...

        def json(self): return payload

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        # Page 2 comes back empty so the scraper's paging loop terminates.
        return _Response() if calls["n"] == 1 else type(
            "_Empty", (), {"status_code": 200, "raise_for_status": lambda s: None,
                           "json": lambda s: {"events": []}})()

    monkeypatch.setattr(requests, "get", fake_get)

    events = BY_NAME["The Rockwell"].load().scrape_events()
    assert events, "parsed 0 events from the API fixture"
    assert {e.source_name for e in events} == {"The Rockwell"}

    violations = check_invariants([e.model_dump(mode="json") for e in events])
    errors = [v for v in violations if v.severity == "error"]
    assert not errors, "The Rockwell:\n  " + "\n  ".join(str(v) for v in errors)


# --------------------------------------------------------------------------- #
# The scrapers revived on 2026-09-01
#
# Each of these produced zero events in production, silently, for weeks. The
# page or payload that exposed each failure is committed alongside, so the
# specific breakage cannot return unnoticed.
# --------------------------------------------------------------------------- #

def test_grolier_reads_the_renamed_page(fixture_html, offline):
    """The venue renamed /upcoming-readings to /upcoming-events. The markup was
    unchanged, so the only symptom was a 404 and an empty source."""
    scraper = parse_with("Grolier Poetry Book Shop",
                         fixture_html("grolier", "upcoming-events.html.gz"))
    events = scraper.scrape_events()
    assert len(events) >= 10, f"expected a full reading series, got {len(events)}"
    assert not [v for v in check_invariants([e.model_dump(mode="json") for e in events])
                if v.severity == "error"]
    assert scraper.source_url.endswith("/upcoming-events")


def test_hrdc_reads_dates_from_the_month_grid(fixture_html, offline):
    """A `<td>` holds a `.calendar-day` number and the day's shows; the month and
    year come from the URL, so no year is ever inferred."""
    from datetime import datetime

    from bs4 import BeautifulSoup

    scraper = BY_NAME["Harvard-Radcliffe Dramatic Club"].load()
    soup = BeautifulSoup(fixture_html("hrdc", "calendar-2026-09.html.gz"), "html.parser")

    found = []
    for cell in soup.find_all("td"):
        day = scraper._day_number(cell)
        if day is None or not 1 <= day <= 30:
            continue
        for item in cell.find_all(class_="calendar-show-item"):
            event = scraper._parse_item(item, 2026, 9, day)
            if event:
                found.append(event)

    assert found, "parsed no shows from the September grid"
    assert all(e.start_datetime.year == 2026 and e.start_datetime.month == 9 for e in found)
    assert {e.start_datetime.day for e in found} == {1, 2, 3, 4}


@pytest.mark.parametrize("source_name,module", [
    ("The Dance Complex", "dance_complex"),
    ("Harvard Square", "harvard_square"),
])
def test_tribe_api_scrapers(source_name, module, monkeypatch, offline):
    """Both venues moved onto their site's Tribe REST API — one because its iCal
    feed went empty, the other because its per-day URLs stopped listing anything.

    The payload is the fixture. Tribe's polymorphic `venue`/`image` fields are
    covered separately by test_tribe_venue_field_shapes.
    """
    import json as _json

    import requests

    payload = _json.loads(read_fixture(module, "events-api-page1.json.gz"))
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        body = payload if calls["n"] == 1 else {"events": [], "total_pages": 1}
        return type("_R", (), {"status_code": 200,
                               "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)

    events = BY_NAME[source_name].load().scrape_events()
    assert events, f"{source_name} parsed nothing from its API fixture"
    assert {e.source_name for e in events} == {source_name}

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, f"{source_name}:\n  " + "\n  ".join(str(v) for v in errors)


@pytest.mark.parametrize("source_name", ["The Dance Complex", "Harvard Square"])
def test_tribe_venue_field_shapes(source_name):
    """Tribe returns `venue` and `image` as a dict, an empty list, or a list of
    dicts, depending on the event. Assuming a dict raised AttributeError and
    killed the whole run on the first event that had no venue attached."""
    scraper = BY_NAME[source_name].load()
    assert scraper._as_dict({"venue": "X"}) == {"venue": "X"}
    assert scraper._as_dict([]) == {}
    assert scraper._as_dict([{"venue": "Y"}, {"venue": "Z"}]) == {"venue": "Y"}
    assert scraper._as_dict(None) == {}
    assert scraper._as_dict("not a mapping") == {}
