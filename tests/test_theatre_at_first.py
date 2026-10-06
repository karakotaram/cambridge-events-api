"""Theatre@First — the company's public Google Calendar feed.

`basic-2026-10-06.ics.gz` is the whole feed as served on 2026-10-06: 596
VEVENTs back to 2009, 91 of them recurring. Past productions are the only
recurring ones, so the recurrence tests read the feed "as of" a date when
one was running. `parse_feed(as_of=...)` takes that instead of a clock.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture
def feed():
    return read_fixture("theatre_at_first", "basic-2026-10-06.ics.gz")


@pytest.fixture
def scraper():
    return BY_NAME["Theatre at First"].load()


def _errors(events, now):
    return [v for v in check_invariants([e.model_dump(mode="json") for e in events], now=now)
            if v.severity == "error"]


def test_the_current_window_comes_from_the_feed_not_the_clock(scraper, feed, offline):
    """The window was [now - 1 day, now + 365 days], so the saved feed parsed
    differently every day. It is now anchored on the feed's own DTSTAMP
    (2026-10-06 13:41:38Z). Nothing public is scheduled after Oct 11."""
    scraper.fetch_html = lambda *a, **k: feed          # type: ignore[method-assign]
    events = scraper.scrape_events()

    assert [(e.title, e.start_datetime, e.end_datetime) for e in events] == [
        ("Iphigenia", datetime(2026, 10, 10, 16, 0), datetime(2026, 10, 10, 18, 30)),
        ("Iphigenia", datetime(2026, 10, 11, 16, 0), datetime(2026, 10, 11, 18, 30)),
    ]
    assert not _errors(events, datetime(2026, 10, 6))


def test_a_recurring_run_lists_every_performance(scraper, feed):
    """"None Escape" (March 2025) is one VEVENT: weekly Thu-Sun at 8 PM until
    Mar 29, minus an EXDATE on Thu Mar 20, with the closing Saturday moved to
    a 2 PM matinee by a RECURRENCE-ID override. Reading DTSTART alone listed
    opening night and the matinee — two of nine performances."""
    as_of = datetime(2025, 3, 1)
    run = sorted(e.start_datetime for e in scraper.parse_feed(feed, as_of=as_of)
                 if e.title == "None Escape")

    assert run == [datetime(2025, 3, d, 20, 0) for d in (14, 15, 16, 21, 22, 23, 27, 28)] + [
        datetime(2025, 3, 29, 14, 0)]


def test_an_outdoor_run_keeps_its_exclusions_and_end_time(scraper, feed):
    """"The Tempest" (June 2025): Fri-Sun at 7 PM until Jun 21 at Nathan Tufts
    Park, with Jun 7 and Jun 14 excluded. UNTIL is a UTC instant
    (20250622T035959Z, i.e. 23:59:59 EDT on the 21st), so Sun Jun 22 is out."""
    as_of = datetime(2025, 6, 1)
    events = [e for e in scraper.parse_feed(feed, as_of=as_of) if e.title == "The Tempest"]

    assert sorted(e.start_datetime.day for e in events) == [6, 8, 13, 15, 20, 21]
    assert all((e.start_datetime.hour, e.start_datetime.minute) == (19, 0) for e in events)
    assert all((e.end_datetime - e.start_datetime).total_seconds() == 90 * 60 for e in events)
    assert {e.venue_name for e in events} == {"Nathan Tufts Park"}
    assert not _errors(scraper.parse_feed(feed, as_of=as_of), as_of)


def test_a_date_only_event_is_all_day(scraper, feed):
    """VALUE=DATE events have no time; they are all-day, not midnight starts."""
    events = {e.title: e for e in scraper.parse_feed(feed, as_of=datetime(2010, 1, 1))}

    performance = events["Bare Bones Performance"]
    assert performance.all_day
    assert performance.start_datetime == datetime(2010, 3, 27, 0, 0)


def test_an_alarm_does_not_overwrite_the_event(scraper):
    """A VEVENT can nest a VALARM with its own UID and DESCRIPTION. Read as
    flat key/values, the alarm's lines replaced the event's — and a replaced
    UID would detach an override from its series."""
    feed = "\r\n".join([
        "BEGIN:VCALENDAR",
        "BEGIN:VEVENT",
        "DTSTART;TZID=America/New_York:20261105T193000",
        "DTEND;TZID=America/New_York:20261105T213000",
        "RRULE:FREQ=DAILY;COUNT=3",
        "DTSTAMP:20261006T134138Z",
        "UID:show@google.com",
        "DESCRIPTION:A new play in three performances at Unity Somerville.",
        "SUMMARY:Test Play",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:This is an event reminder",
        "UID:ALARM-1",
        "TRIGGER:-P0DT0H30M0S",
        "END:VALARM",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "DTSTART;TZID=America/New_York:20261107T140000",
        "DTEND;TZID=America/New_York:20261107T160000",
        "RECURRENCE-ID;TZID=America/New_York:20261107T193000",
        "DTSTAMP:20261006T134138Z",
        "UID:show@google.com",
        "SUMMARY:Test Play",
        "END:VEVENT",
        "END:VCALENDAR",
    ])
    events = scraper.parse_feed(feed)

    assert [e.start_datetime for e in events] == [
        datetime(2026, 11, 5, 19, 30), datetime(2026, 11, 6, 19, 30), datetime(2026, 11, 7, 14, 0)]
    assert events[0].description == "A new play in three performances at Unity Somerville."
