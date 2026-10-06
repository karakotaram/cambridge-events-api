"""All-day events: a date with no time, shown and exported as such.

Scrapers used to invent a time for date-only listings (midnight, 7 PM, 8 PM),
which Rule 1 forbids. They now emit `all_day=True` at 00:00 instead, and the API
has to carry that through: the slim list must say so, the upcoming filter must
not drop the event the moment its day begins, and the calendar export must not
put it on someone's calendar at midnight.
"""
from datetime import datetime

from src.api.main import EventSlim, _all_day_still_on, generate_ics
from src.models.event import Event, EventCreate


def make(start, end=None, all_day=True):
    return Event.from_create(EventCreate(
        title="Midwinter Revels", description="A seasonal celebration.",
        start_datetime=start, end_datetime=end, all_day=all_day,
        source_name="Sanders Theatre", source_url="https://example.org/revels"))


def test_an_all_day_run_stays_upcoming_through_its_last_day():
    run = make(datetime(2026, 12, 11), datetime(2026, 12, 28))
    assert _all_day_still_on(run, datetime(2026, 12, 11, 15, 0))
    assert _all_day_still_on(run, datetime(2026, 12, 28, 23, 0))
    assert not _all_day_still_on(run, datetime(2026, 12, 29, 0, 1))


def test_a_timed_event_is_not_affected():
    show = make(datetime(2026, 12, 11, 19, 30), all_day=False)
    assert not _all_day_still_on(show, datetime(2026, 12, 11, 15, 0))


def test_slim_events_carry_the_flag():
    assert "all_day" in EventSlim.model_fields


def test_calendar_export_uses_dates_not_midnight():
    ics = generate_ics(make(datetime(2026, 12, 11), datetime(2026, 12, 28)))
    assert "DTSTART;VALUE=DATE:20261211" in ics
    assert "DTEND;VALUE=DATE:20261229" in ics, "ICS DTEND is exclusive"
    assert "T000000" not in ics

    timed = generate_ics(make(datetime(2026, 12, 11, 19, 30), all_day=False))
    assert "DTSTART:20261211T193000" in timed
