"""Harvard Athletics — gocrimson.com's Sidearm calendar service.

Fixture: calendar-10-weeks-from-2026-10-06.json.gz is a list of the ten
weekly responses (`date=10/6/2026`, `10/13/2026`, …) captured 2026-10-06.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture
def weeks():
    return json.loads(read_fixture("harvard_athletics", "calendar-10-weeks-from-2026-10-06.json.gz"))


def _by_title(events):
    return {(e.title, e.start_datetime.date().isoformat()): e for e in events}


def test_harvard_athletics_reads_ten_weeks_not_one(weeks, monkeypatch, offline):
    """One call returns one seven-day window. Reading only that gave 4 home
    events; the next two weeks alone hold Football vs Holy Cross and vs
    Princeton, volleyball, soccer and the start of ice hockey."""
    import requests

    from src.scrapers.harvard_athletics import WEEKS

    asked = []

    def fake_get(url, params=None, **kwargs):
        asked.append(params["date"])
        body = weeks[len(asked) - 1]
        return type("_R", (), {"raise_for_status": lambda s: None, "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    events = BY_NAME["Harvard Athletics"].load().scrape_events()

    assert len(asked) == WEEKS == len(weeks)
    assert len(set(asked)) == WEEKS, "every call must ask for a different week"
    first_week = [e for e in events if e.start_datetime < datetime(2026, 10, 13)]
    assert len(first_week) == 4
    assert len(events) >= 50
    titles = {e.title for e in events}
    assert {"Harvard Football vs Holy Cross", "Harvard Football vs Princeton University"} <= titles


def test_harvard_athletics_lists_a_multi_day_event_once(weeks, offline):
    """A multi-day event repeats in every day group it spans — the ITA regional
    appears in six of the first week's seven groups. It is one event."""
    events = BY_NAME["Harvard Athletics"].load().parse_groups(g for week in weeks for g in week)

    regionals = [e for e in events if "ITA New England Regionals" in e.title]
    assert len(regionals) == 1
    assert len({(e.title, e.start_datetime) for e in events}) == len(events)


def test_harvard_athletics_starts_at_the_displayed_time(weeks, offline):
    """The item's ISO `date` carries a time that disagrees with the one the
    site shows. Rugby vs Navy was published at 1 PM; gocrimson.com says
    11:00 AM. Women's Soccer vs Yale: 1 PM published, 2:00 PM shown."""
    events = _by_title(BY_NAME["Harvard Athletics"].load().parse_groups(g for week in weeks for g in week))

    assert events[("Harvard Women's Rugby vs Navy", "2026-10-10")].start_datetime == datetime(2026, 10, 10, 11, 0)
    assert events[("Harvard Women's Soccer vs Yale University", "2026-10-17")].start_datetime == datetime(2026, 10, 17, 14, 0)


def test_harvard_athletics_tba_is_all_day_not_midnight(weeks, offline):
    """"TBA" was published as a midnight start. An event with no set time is an
    all-day event on its date."""
    events = BY_NAME["Harvard Athletics"].load().parse_groups(g for week in weeks for g in week)

    tennis = next(e for e in events if "ITA New England Regionals" in e.title)
    assert tennis.all_day and tennis.start_datetime == datetime(2026, 10, 8, 0, 0)
    assert tennis.title == "Harvard Men's Tennis: ITA New England Regionals"
    assert "{" not in tennis.description, "the tournament was printed as a dict repr"
    assert not [e for e in events if not e.all_day and (e.start_datetime.hour, e.start_datetime.minute) == (0, 0)]


def test_harvard_athletics_keeps_only_confirmed_home_games(weeks, offline):
    """Away and neutral-site games are dropped — "at MIT" is in Cambridge but
    is MIT's event. So are home-marked placeholders sited "TBD": the FCS
    playoff rounds happen only if Harvard qualifies and hosts."""
    raw = {item["id"]: item for week in weeks for g in week for item in (g.get("events") or [])}
    events = BY_NAME["Harvard Athletics"].load().parse_groups(g for week in weeks for g in week)

    home_sited = [i for i in raw.values()
                  if i["location_indicator"] == "H" and i["location"].strip() not in ("TBD", "TBA")]
    assert len(events) == len(home_sited)
    assert not [e for e in events if "FCS" in e.title]

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


@pytest.mark.parametrize("shown,expected", [
    ("7:00 PM", (datetime(2026, 11, 7, 19, 0), False)),
    ("5:00 PM ", (datetime(2026, 11, 7, 17, 0), False)),
    ("1:00 PM ET/12:00 PM CT", (datetime(2026, 11, 7, 13, 0), False)),
    ("12:00 PM", (datetime(2026, 11, 7, 12, 0), False)),
    ("TBA", (datetime(2026, 11, 7), True)),
    ("All Day", (datetime(2026, 11, 7), True)),
    ("", (datetime(2026, 11, 7), True)),
    ("after game 2", (None, False)),
])
def test_harvard_athletics_time_strings(shown, expected):
    """Every shape of `time` seen across ten weeks; anything else is skipped."""
    scraper = BY_NAME["Harvard Athletics"].load()
    assert scraper.parse_start("2026-11-07T13:00:00", shown) == expected
