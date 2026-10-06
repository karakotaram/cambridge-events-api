"""Skip the Small Talk: the redesigned Squarespace store, read as JSON.

The fixture is /store?category=Boston&format=json, captured 2026-10-06: 39
Boston-area products, 18 of them upcoming.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.skip_small_talk import SkipSmallTalkScraper
from tests.conftest import read_fixture


def _items() -> list:
    return json.loads(read_fixture("skip_small_talk", "store-boston-2026-10-06.json.gz"))["items"]


def _by_title(fragment: str) -> dict:
    return copy.deepcopy(next(i for i in _items() if fragment in i["title"]))


def test_reads_the_redesigned_store(offline):
    """The site moved its listings into a Squarespace store in September; the
    old selector found nothing and the source returned 0 from Sep 16 on."""
    events = SkipSmallTalkScraper().parse_items(_items())

    assert len(events) == 39
    assert len([e for e in events if e.start_datetime >= datetime(2026, 10, 6)]) == 18
    assert {e.source_name for e in events} == {"Skip the Small Talk"}
    assert all(e.source_url.startswith("https://www.skipthesmalltalk.com/store/") for e in events)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_city_comes_from_the_venue_address(offline):
    """Everything used to be marked "Boston", including Aeronaut (Somerville)
    and Porter Square Books (Cambridge)."""
    events = SkipSmallTalkScraper().parse_items(_items())
    cities = {e.venue_name: e.city for e in events}

    assert cities["Aeronaut"] == "Somerville"
    assert cities["Porter Square Books"] == "Cambridge"
    assert cities["Cafe Zing"] == "Cambridge"
    assert cities["Trident Books"] == "Boston"
    assert cities["Park-9 Dog Bar"] == "Everett"     # no comma before "MA"
    assert cities["Online"] is None

    psb = next(e for e in events if e.venue_name == "Porter Square Books")
    assert psb.start_datetime == datetime(2026, 10, 14, 19, 0)
    assert psb.street_address == "1815 Massachusetts Ave, Suite 118-119"
    assert psb.title == "Skip the Small Talk Night at Porter Square Books"


def test_only_the_boston_category_is_kept(offline):
    """The store is national. An unknown category once returned every city's
    listings, and Raleigh and Chicago events reached a Cambridge calendar — so
    the category is checked per product, not only in the URL."""
    chicago = _by_title("Aeronaut")
    chicago["categories"] = ["Chicago"]
    assert SkipSmallTalkScraper().parse_items([chicago]) == []


def test_a_date_without_a_time_is_skipped_not_invented():
    """The scraper used to give anything it could not fully read an 18:30 start."""
    item = _by_title("Trident Books: Wednesday, October 7")
    item["tags"] = ["Wednesday", "October 7", "2026"]
    item["excerpt"] = "<p>WHERE | Trident Books, 338 Newbury St, Boston, MA 02115</p>"
    assert SkipSmallTalkScraper().parse_items([item]) == []

    # The listing's own start-end line is an acceptable second source for the time
    item["excerpt"] = "<p>EVENT START-END TIME | 7:00 pm - 9:00 pm ET</p>" + item["excerpt"]
    [event] = SkipSmallTalkScraper().parse_items([item])
    assert event.start_datetime == datetime(2026, 10, 7, 19, 0)


def test_disagreeing_or_impossible_dates_are_skipped():
    """Products are cloned from one another (one Chicago URL still says
    "august-5" for an August 19 event). A weekday that does not fit the date,
    or a title that disagrees with the listing line, means one of them is
    stale; neither is used."""
    item = _by_title("Porter Square Books")
    wrong_weekday = copy.deepcopy(item)
    wrong_weekday["tags"] = ["Thursday", "October 14", "2026", "7:00 pm"]
    stale_title = copy.deepcopy(item)
    stale_title["title"] = "Skip the Small Talk Night at Porter Square Books: Wednesday, October 7, 2026"

    assert SkipSmallTalkScraper().parse_items([wrong_weekday, stale_title]) == []


def test_one_request_with_an_honest_identity_and_loud_failure(monkeypatch, offline):
    import requests

    calls = []

    class _Response:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} Client Error")

        def json(self):
            return self._body

    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: calls.append((url, kw)) or _Response(200, {"items": _items()}))
    assert len(SkipSmallTalkScraper().scrape_events()) == 39
    assert len(calls) == 1
    assert calls[0][1]["params"] == {"category": "Boston", "format": "json"}
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"]

    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(403, None))
    with pytest.raises(requests.HTTPError):
        SkipSmallTalkScraper().run()
