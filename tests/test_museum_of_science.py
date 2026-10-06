"""Museum of Science: the listing JSON, hand-written date text, and missing years.

The fixture is the body of the `/api/v1/event-listing/...` request the museum's
/events page makes, captured 2026-10-06: eleven listings, four of them with
prose in the date field.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from src.quality.invariants import check_invariants
from src.scrapers.museum_of_science import MuseumOfScienceScraper, parse_when
from tests.conftest import read_fixture

CAPTURED = date(2026, 10, 6)


def _payload() -> dict:
    return json.loads(read_fixture("museum_of_science", "event-listing-api-2026-10-06.json.gz"))


def test_a_missing_year_is_decided_by_the_printed_weekday():
    """The live bug: a passed date was rolled into next year regardless of its weekday.

    "Strange Land" was listed "Wednesday, September 23 | 7:30 pm". That is 2026,
    already over, and it was published as 2027-09-23 — a Thursday.
    """
    assert parse_when("Wednesday, September 23 | 7:30 pm", CAPTURED) == (datetime(2026, 9, 23, 19, 30), False)
    # The same day and month on a Thursday really is next year
    assert parse_when("Thursday, September 23 | 7:30 pm", CAPTURED) == (datetime(2027, 9, 23, 19, 30), False)
    # A weekday that fits none of last year, this year or next: skip, never guess
    assert parse_when("Monday, September 23 | 7:30 pm", CAPTURED) is None
    # A printed year must agree with the printed weekday too
    assert parse_when("Friday, November 14, 2026 | 10:00 am", CAPTURED) is None


def test_no_year_and_no_weekday_is_not_dated():
    """With neither printed, nothing on the page decides the year."""
    assert parse_when("October 11 | 6:00 pm", CAPTURED) is None
    assert parse_when("October 11, 2026 | 6:00 pm", CAPTURED) == (datetime(2026, 10, 11, 18, 0), False)


@pytest.mark.parametrize("text,expected", [
    # The four the previous parser rejected outright
    ("Friday, October 16 | Doors open at 7:30 pm; Performance starts at 8:00 pm", datetime(2026, 10, 16, 19, 30)),
    ("Sunday, October 18 | Family-friendly presentation included with Exhibit Halls admission 1:30 pm; "
     "Fireside chat at 6:30 pm", datetime(2026, 10, 18, 13, 30)),
    ("Thursday, October 29 at 7:30 pm; Friday, October 30 at 6:30 pm and 8:00 pm", datetime(2026, 10, 29, 19, 30)),
    ("Masked Access Hours, Saturday, November 7th | 8–9 am", datetime(2026, 11, 7, 8, 0)),
    # A range start borrows the end's meridiem, unless that puts it after the end
    ("Sunday, October 11 | 6:00 – 9:00 pm", datetime(2026, 10, 11, 18, 0)),
    ("Saturday, November 7 | 11:00 – 1:00 pm", datetime(2026, 11, 7, 11, 0)),
    ("Saturday, November 14, 2026 | 10:00 am – 4:00 pm", datetime(2026, 11, 14, 10, 0)),
])
def test_the_date_and_first_time_are_read_out_of_prose(text, expected):
    """Handing dateutil the whole string failed on any prose; four of eleven
    listings were dropped. The date and the first time are found separately."""
    assert parse_when(text, CAPTURED) == (expected, False)


def test_a_date_with_no_time_is_all_day():
    """A date with no time is published as all-day, not as a midnight start."""
    assert parse_when("Saturday, November 7th | All day", CAPTURED) == (datetime(2026, 11, 7), True)


def test_every_listing_in_the_payload_is_dated(offline):
    """All eleven listings parse, on the weekday they were printed with."""
    cards = [r["event_listing"] for r in _payload()["results"]]
    events = MuseumOfScienceScraper().parse_cards(cards, CAPTURED)

    assert len(events) == len(cards) == 11
    assert {e.source_name for e in events} == {"Museum of Science"}
    assert all(e.source_url.startswith("https://www.mos.org/events/") for e in events)
    assert all(e.start_datetime.year == 2026 for e in events)

    collier = next(e for e in events if "Jacob Collier" in e.title)
    assert (collier.start_datetime, collier.all_day) == (datetime(2026, 10, 16, 19, 30), False)

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=datetime(2026, 10, 6))
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_scrape_reads_the_api_and_a_refusal_fails_loudly(monkeypatch, offline):
    """The scraper reads the JSON the page fetches, and does not swallow errors:
    a challenge page or a 403 must fail the source, not read as zero events."""
    import requests

    payload = _payload()
    calls = []

    class _Response:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} Client Error")

        def json(self):
            if not isinstance(self._body, dict):
                raise ValueError("not JSON")
            return self._body

    monkeypatch.setattr(MuseumOfScienceScraper, "today", staticmethod(lambda: CAPTURED))
    responses = iter([_Response(200, payload)])
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or next(responses))
    events = MuseumOfScienceScraper().scrape_events()
    assert len(events) == 11
    assert len(calls) == 1, "one page of results should take one request"
    assert "/api/v1/event-listing/" in calls[0][0]
    assert "Mozilla" not in calls[0][1]["headers"]["User-Agent"]

    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(403, "<html>Just a moment...</html>"))
    with pytest.raises(requests.HTTPError):
        MuseumOfScienceScraper().run()

    monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(200, "<html>Just a moment...</html>"))
    with pytest.raises(ValueError):
        MuseumOfScienceScraper().run()
