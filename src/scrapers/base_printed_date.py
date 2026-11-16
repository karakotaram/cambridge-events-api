"""Dates and times as venues print them for people, not machines.

"FRIDAY, DECEMBER 4, 2026 at 7:30pm", "Sat, Nov 21, 2026", "Wednesday,
December 9th at 7pm", "12:00 Noon". Several sources have nothing better; these
helpers read such text and refuse anything ambiguous rather than guessing
(CLAUDE.md, rule 1):

  - a printed weekday must agree with the date it is printed beside. A
    mismatch means one of the two is a typo, and there is no telling which;
  - a time needs a meridiem (or noon/midnight). "10:00" alone could be either;
  - a year that is not printed is taken only from a weekday that pins it down
    among the candidate years, never assumed.
"""
import calendar
import re
from datetime import date, datetime
from typing import Iterable, Optional, Tuple

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9

WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
WEEKDAYS.update({name.lower(): i for i, name in enumerate(calendar.day_abbr)})
WEEKDAYS.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})

# "7:30pm", "8PM", "7 p.m.", "12:00 Noon", "noon", "midnight"
CLOCK = re.compile(
    r"(?:(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|noon|midnight)(?![a-z])|\b(noon|midnight)\b)",
    re.I,
)


def month_number(text: str) -> Optional[int]:
    return MONTHS.get((text or "").strip(" .").lower())


def weekday_number(text: str) -> Optional[int]:
    return WEEKDAYS.get((text or "").strip(" .,").lower())


def clock(text: str) -> Optional[Tuple[int, int]]:
    """The first time of day in `text` as (hour, minute), or None.

    Only a time that says which half of the day it is in counts.
    """
    match = CLOCK.search(text or "")
    if not match:
        return None
    word = (match.group(3) or match.group(4) or "").lower().replace(".", "")
    if match.group(4):
        return (12, 0) if word == "noon" else (0, 0)
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    if word == "noon":
        return (12, minute) if hour == 12 else None
    if word == "midnight":
        return (0, minute) if hour == 12 else None
    if word == "am":
        return (0 if hour == 12 else hour, minute)
    return (12 if hour == 12 else hour + 12, minute)


def dated(year: int, month: int, day: int, weekday: Optional[int] = None) -> Optional[date]:
    """date(year, month, day) if it exists and, when a weekday is printed, falls on it."""
    try:
        when = date(year, month, day)
    except ValueError:
        return None
    if weekday is not None and when.weekday() != weekday:
        return None
    return when


def year_for_weekday(month: int, day: int, weekday: int, years: Iterable[int]) -> Optional[int]:
    """The one candidate year in which month/day falls on `weekday`, else None."""
    matches = [y for y in years if dated(y, month, day, weekday)]
    return matches[0] if len(matches) == 1 else None


def at(day: date, time: Optional[Tuple[int, int]]) -> datetime:
    """A naive wall-clock start, on the minute; midnight when there is no time."""
    hour, minute = time or (0, 0)
    return datetime(day.year, day.month, day.day, hour, minute)
