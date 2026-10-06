"""First Parish in Cambridge — Squarespace epoch timestamps.

The payload is the venue's own `/events?format=json`, captured 2026-10-06.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture(params=["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def machine_tz(request, monkeypatch):
    """Run the scraper as if the machine's local zone were something else.

    `monkeypatch.context()` restores TZ when the block exits; tzset() has to be
    called again afterwards so the process forgets the temporary zone too.
    """
    with monkeypatch.context() as m:
        m.setenv("TZ", request.param)
        time.tzset()
        yield request.param
    time.tzset()


def _scrape():
    scraper = BY_NAME["First Parish in Cambridge"].load()
    payload = read_fixture("first_parish", "events-2026-10-06.json.gz")
    scraper.fetch_html = lambda *a, **k: payload          # type: ignore[method-assign]
    return scraper.scrape_events()


def test_services_are_at_their_eastern_time_whatever_the_machine_zone(machine_tz, offline):
    """Squarespace stamps 10:30 AM services as epoch milliseconds. Reading those
    with `datetime.fromtimestamp()` and no tz uses the machine's zone, and CI
    runs on UTC: the services were published at 14:30 in October and 15:30
    after the clocks change. Every machine must see 10:30 to 11:30."""
    assert time.strftime("%Z") != "EDT", "the test must run outside Eastern time"

    events = _scrape()

    assert len(events) == 3, f"fixture lists three upcoming services, got {len(events)}"
    # One before and two after the 2026-11-01 change back to standard time
    assert [e.start_datetime for e in events] == [
        datetime(2026, 10, 18, 10, 30),
        datetime(2026, 11, 1, 10, 30),
        datetime(2026, 11, 15, 10, 30),
    ], f"wrong under TZ={machine_tz}"
    assert all(e.end_datetime == e.start_datetime.replace(hour=11) for e in events)


def test_output_satisfies_invariants(offline):
    """Timestamps end in .263 ms; none of that may reach a start time."""
    events = _scrape()
    assert {e.source_name for e in events} == {"First Parish in Cambridge"}
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
