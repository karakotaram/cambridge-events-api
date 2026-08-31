"""Regression tests for Cambridge.gov event dates.

The city calendar once put 117 events on a single day: the scraper fell back to
`datetime.now() + N weeks` whenever a detail page failed to load. These tests
pin the two rules that prevent it:

  1. dates come from the listing markup, never from the clock
  2. an event with no readable date is skipped, not guessed
"""
from datetime import datetime

from bs4 import BeautifulSoup

from src.scrapers.cambridge_gov import CambridgeGovScraper


WEEK_START = datetime(2026, 9, 14)


def row(html: str):
    return BeautifulSoup(html, "html.parser").find("li")


def test_pm_time_uses_visible_text_not_the_attribute():
    """The site writes PM times on a 12-hour clock: 5 PM is `05:00:00`."""
    item = row(
        '<li class="eventItem">'
        '<time datetime="2026-09-16 05:00:00">5:00 PM</time>'
        '<div class="eventDesc"><h3><a href="/citycalendar/view.aspx?guid=abc">'
        'Cambridge NITES: The Comedy Studio &amp; Black Girl Magic</a></h3></div>'
        "</li>"
    )
    parsed = CambridgeGovScraper().parse_item_datetime(item, "Wednesday September 16", WEEK_START)
    assert parsed == datetime(2026, 9, 16, 17, 0)


def test_am_time_is_left_alone():
    item = row('<li class="eventItem"><time datetime="2026-09-19 11:00:00">11:00 AM</time></li>')
    parsed = CambridgeGovScraper().parse_item_datetime(item, "Saturday September 19", WEEK_START)
    assert parsed == datetime(2026, 9, 19, 11, 0)


def test_midnight_and_noon():
    scraper = CambridgeGovScraper()
    midnight = row('<li class="eventItem"><time datetime="2026-09-14 12:00:00">12:00 AM</time></li>')
    noon = row('<li class="eventItem"><time datetime="2026-09-14 12:30:00">12:30 PM</time></li>')
    assert scraper.parse_item_datetime(midnight, None, WEEK_START) == datetime(2026, 9, 14, 0, 0)
    assert scraper.parse_item_datetime(noon, None, WEEK_START) == datetime(2026, 9, 14, 12, 30)


def test_falls_back_to_the_day_heading_when_the_attribute_is_missing():
    item = row('<li class="eventItem"><time>6:30 PM</time></li>')
    parsed = CambridgeGovScraper().parse_item_datetime(item, "Monday September 14", WEEK_START)
    assert parsed == datetime(2026, 9, 14, 18, 30)


def test_day_heading_fallback_rolls_over_into_the_new_year():
    item = row('<li class="eventItem"><time>7:00 PM</time></li>')
    parsed = CambridgeGovScraper().parse_item_datetime(
        item, "Friday January 1", datetime(2026, 12, 28)
    )
    assert parsed == datetime(2027, 1, 1, 19, 0)


def test_unreadable_row_returns_none_rather_than_a_guess():
    """The bug: no date must mean no event, never "now plus a few weeks"."""
    scraper = CambridgeGovScraper()
    assert scraper.parse_item_datetime(row('<li class="eventItem"></li>'), "Monday September 14", WEEK_START) is None
    assert scraper.parse_item_datetime(row('<li class="eventItem"><time>TBD</time></li>'), None, WEEK_START) is None


def test_no_parsed_date_is_ever_the_scrape_time():
    """A date carrying seconds or microseconds came from the clock, not the page."""
    item = row('<li class="eventItem"><time datetime="2026-09-14 01:00:00">1:00 PM</time></li>')
    parsed = CambridgeGovScraper().parse_item_datetime(item, "Monday September 14", WEEK_START)
    assert parsed.second == 0 and parsed.microsecond == 0
