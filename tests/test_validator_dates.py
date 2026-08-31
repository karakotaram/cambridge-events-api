"""The validator is the last line of defence against clock-derived event dates."""
from datetime import datetime, timedelta

from src.models.event import EventCreate
from src.utils.validator import EventValidator


def make_event(start_datetime):
    return EventCreate(
        title="Danehy Park Family Day",
        description="Activities, arts and crafts, entertainment, free food.",
        start_datetime=start_datetime,
        source_url="https://www.cambridgema.gov/citycalendar/view.aspx?guid=abc",
        source_name="City of Cambridge",
    )


def test_published_times_are_accepted():
    soon = (datetime.now() + timedelta(days=14)).replace(hour=11, minute=0, second=0, microsecond=0)
    assert EventValidator.validate_event(make_event(soon)) == (True, None)


def test_scrape_timestamps_are_rejected():
    """`datetime.now() + timedelta(weeks=2)` is what put 117 events on one day."""
    stamped = datetime.now() + timedelta(days=14)
    assert stamped.microsecond  # what a real clock reading looks like
    is_valid, error = EventValidator.validate_event(make_event(stamped))
    assert not is_valid
    assert "scrape timestamp" in error
