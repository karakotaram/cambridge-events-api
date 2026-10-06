"""Longy School of Music: the Tribe REST API instead of the first calendar page.

The fixture is one response from /wp-json/tribe/events/v1/events, captured
2026-10-06: 34 events on one page.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.longy import LongyScraper
from tests.conftest import read_fixture


def _payload() -> dict:
    return json.loads(read_fixture("longy", "events-api-2026-10-06.json.gz"))


class _Response:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        if not isinstance(self._body, dict):
            raise ValueError("not JSON")
        return self._body


def test_reads_the_whole_season_in_one_request(monkeypatch, offline):
    """The browser scraper read /calendar/ page 1 and always returned exactly
    10. The API lists the season, and one page must cost one request: the site
    rate-limits into an Imunify360 challenge."""
    import requests

    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or _Response(200, _payload()))

    events = LongyScraper().scrape_events()

    assert len(events) == 34
    assert len(calls) == 1
    assert calls[0][0].endswith("/wp-json/tribe/events/v1/events")
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"], "never a browser's user-agent"


@pytest.mark.parametrize("response", [
    _Response(403, "<html>Forbidden</html>"),
    _Response(200, "<html>Imunify360 bot protection</html>"),
])
def test_a_refusal_or_a_challenge_fails_the_source(response, monkeypatch, offline):
    """Zero events from a block must be a failure, so the run keeps Longy's
    existing listings instead of recording a school with nothing on."""
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: response)
    with pytest.raises((requests.HTTPError, ValueError)):
        LongyScraper().run()


def test_times_are_eastern_from_the_utc_fields(offline):
    """Starts come from Tribe's utc_* fields, converted explicitly — across the
    November clock change too — and agree with its local start_date."""
    items = _payload()["events"]
    events = {e.source_url: e for e in LongyScraper().parse_items(items)}

    for item in items:
        assert str(events[item["url"]].start_datetime) == item["start_date"], item["title"]

    vivo = events["https://longy.edu/calendar/vivo-avery-gagliano/"]
    assert (vivo.start_datetime, vivo.end_datetime) == (datetime(2026, 10, 8, 19, 30), datetime(2026, 10, 8, 21, 0))
    assert not any(e.all_day for e in events.values())


def test_all_day_events_are_dated_not_timed():
    """Tribe's all_day flag means the venue gave no time; publish the date only."""
    item = {"title": "Open House", "status": "publish", "all_day": True,
            "start_date": "2026-11-14 00:00:00", "utc_start_date": "2026-11-14 05:00:00",
            "utc_end_date": "2026-11-15 04:59:59", "url": "https://longy.edu/calendar/open-house/"}
    event = LongyScraper()._parse_event(item)
    assert (event.start_datetime, event.all_day, event.end_datetime) == (datetime(2026, 11, 14), True, None)


def test_text_is_clean_and_venues_are_specific(offline):
    """Titles carry HTML entities and descriptions carry WPBakery shortcodes;
    off-site concerts keep their own venue."""
    events = LongyScraper().parse_items(_payload()["events"])

    assert not [e.title for e in events if "&#" in e.title or "&amp;" in e.title]
    assert not [e.title for e in events if "[vc_" in e.description or "&#" in e.description]
    assert all(len(e.description) >= 20 for e in events)

    regattabar = [e for e in events if "Regattabar" in e.venue_name]
    assert regattabar and regattabar[0].street_address == "One Bennett Street"
    assert all(e.street_address for e in events)
    assert {e.source_name for e in events} == {"Longy School of Music"}

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
