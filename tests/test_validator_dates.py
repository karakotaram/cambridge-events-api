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


# --------------------------------------------------------------------------- #
# AM/PM typed wrong by the venue: "12:30 AM" meaning half past noon
# --------------------------------------------------------------------------- #

def _at(hh_mm: str, end: str = None, *, next_day_end=False, all_day=False):
    """An event a fortnight out, starting at hh:mm and optionally ending at end."""
    day = (datetime.now() + timedelta(days=14)).replace(second=0, microsecond=0)
    h, m = map(int, hh_mm.split(":"))
    start = day.replace(hour=h, minute=m)
    stop = None
    if end:
        eh, em = map(int, end.split(":"))
        stop = day.replace(hour=eh, minute=em) + (timedelta(days=1) if next_day_end else timedelta())
    event = make_event(start)
    return event.model_copy(update={"end_datetime": stop, "all_day": all_day})


def test_twelve_thirty_am_is_refused():
    """City of Cambridge, 2026-11-16: a senior-centre social group printed
    as "12:30 AM - 1:30 AM"."""
    is_valid, error = EventValidator.validate_event(_at("00:30", "01:30"))
    assert not is_valid and "12:xx AM" in error


def test_a_start_before_dawn_that_ends_in_the_afternoon_is_refused():
    """Harvard Art Museums printed a gallery talk as "12:30am - 1:00pm"; MIT
    Events carried "Change The Lens" at 00:00-12:50."""
    assert not EventValidator.validate_event(_at("00:30", "13:00"))[0]
    is_valid, error = EventValidator.validate_event(_at("00:00", "12:50"))
    assert not is_valid and "AM/PM" in error


def test_midnight_as_a_date_only_marker_is_kept():
    """Many sources stamp an event with no time at 00:00."""
    assert EventValidator.validate_event(_at("00:00")) == (True, None)
    assert EventValidator.validate_event(_at("00:00", "23:59")) == (True, None)


def test_real_late_night_and_overnight_listings_are_kept():
    """The Mad Monkfish's "Midnight Hour" set starts at 1 AM; club nights run
    past midnight from the evening before."""
    assert EventValidator.validate_event(_at("01:00", "02:30")) == (True, None)
    assert EventValidator.validate_event(_at("23:00", "02:00", next_day_end=True)) == (True, None)


def test_noon_thirty_and_all_day_events_are_untouched():
    assert EventValidator.validate_event(_at("12:30", "13:30")) == (True, None)
    assert EventValidator.validate_event(_at("00:30", all_day=True)) == (True, None)
