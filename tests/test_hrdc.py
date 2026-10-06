"""Harvard-Radcliffe Dramatic Club — the publicity month grid.

Fixture: calendar-2026-10.html.gz is /publicity/calendar/2026/10/, captured
2026-10-06. (September's grid is covered in test_scrapers_against_fixtures.py.)
"""
from __future__ import annotations

from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture
def october(offline):
    scraper = BY_NAME["Harvard-Radcliffe Dramatic Club"].load()
    soup = BeautifulSoup(read_fixture("hrdc", "calendar-2026-10.html.gz"), "html.parser")
    listed, events = [], []
    for cell in soup.find_all("td"):
        day = scraper._day_number(cell)
        if day is None or not 1 <= day <= 31:
            continue
        for item in cell.find_all(class_="calendar-show-item"):
            listed.append(scraper.clean_text(item.find(class_="calendar-show-title").get_text()))
            event = scraper._parse_item(item, 2026, 10, day)
            if event:
                events.append(event)
    return listed, events


def test_hrdc_does_not_publish_a_deadline(october):
    """"Agassiz Theater Apps Due" at 11:59 PM is an application deadline on the
    club's calendar, and was published as an event."""
    listed, events = october
    assert "Agassiz Theater Apps Due" in listed, "fixture should contain the deadline"
    assert not [e.title for e in events if "Due" in e.title]
    assert len(events) == len(listed) - 1


def test_hrdc_october_shows_keep_their_times(october):
    listed, events = october
    starts = sorted(e.start_datetime for e in events if e.title == "macbitches")
    assert starts == [datetime(2026, 10, 8, 19, 30), datetime(2026, 10, 9, 19, 30),
                      datetime(2026, 10, 10, 14, 0), datetime(2026, 10, 10, 19, 30),
                      datetime(2026, 10, 11, 14, 0)]
    assert not [e for e in events if e.all_day]
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


@pytest.mark.parametrize("time_text,expected", [
    ("9 PM", (datetime(2026, 10, 8, 21, 0), False)),
    ("7:30 PM", (datetime(2026, 10, 8, 19, 30), False)),
    ("12 PM", (datetime(2026, 10, 8, 12, 0), False)),
    ("12 AM", (datetime(2026, 10, 8, 0, 0), False)),
    ("", (datetime(2026, 10, 8), True)),
    ("TBA", (None, False)),
    ("after the show", (None, False)),
])
def test_hrdc_missing_time_is_all_day_and_unreadable_is_skipped(time_text, expected):
    """Both used to come back as midnight: an empty time returned the bare
    date, and an unparseable one fell back to it."""
    scraper = BY_NAME["Harvard-Radcliffe Dramatic Club"].load()
    assert scraper._parse_start(2026, 10, 8, time_text) == expected


def test_hrdc_reads_the_tooltip_as_served(offline):
    """The venue note is in the info icon's `title`. Bootstrap renames it to
    `data-original-title` in a browser, which is the only place the old
    selector looked, so plain HTTP never saw a note."""
    scraper = BY_NAME["Harvard-Radcliffe Dramatic Club"].load()
    soup = BeautifulSoup(read_fixture("hrdc", "calendar-2026-09.html.gz"), "html.parser")
    item = soup.find(class_="calendar-show-item")
    event = scraper._parse_item(item, 2026, 9, 1)
    assert "Agassiz House and Loeb Drama Center" in event.description
