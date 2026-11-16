"""The Burren, Davis Square — the pub's own hand-built music calendar.

burren.com/music.html is one long table, edited by hand: a day header
("MONDAY NOVEMBER 16", or "SATURDAY JANUARY 2, 2027" once the year turns), then
for each act a room ("THE FRONT ROOM" / "THE BACK ROOM"), a time, the act in
capitals, a line about it, and sometimes a 24 Hour Music ticket link. Reading
the cells in document order recovers that sequence.

This page, not the 24 Hour Music ticketing feed, is the source: it also lists
the weekly sessions, trivia and free shows in both rooms, which are never
ticketed, and its times are the venue's. The two have disagreed — the
Dambuilders' 20 Nov 2026 show was 7pm here and 6:15pm in the ticketing feed.
Acts still booked as "TBD" are skipped.

**Dates.** A header without a year is resolved by its weekday: the year near
today in which that date falls on that weekday (art.py). A header with a year
must agree with its weekday, or its acts are skipped.

**Times** come in several hand-typed shapes, each read literally:
"8:30pm:", "2-5pm:" (start 2pm, end 5pm), "5pm & 7:30pm:" and
"10:30am, 12:30pm, 2:30pm, & 4:30pm:" (one listing per time). An act whose
time cannot be read is skipped, never given a guessed hour.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.art import MONTHS, WEEKDAYS, year_for_weekday
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

# Slots booked but not yet filled ("TBD" / Cover Band).
PLACEHOLDER = re.compile(r"^\W*(tbd|tba|to be (announced|determined))\W*$", re.I)
HEADER = re.compile(r"^([A-Za-z]+)\s+([A-Za-z]+)\s+(\d{1,2})(?:\s*,\s*(\d{4}))?$")
ONE_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|noon)?$", re.I)


def _hour(hour: int, meridiem: str) -> int:
    return hour % 12 + (12 if meridiem == "pm" else 0)


def parse_times(text: str) -> Optional[Tuple[List[Tuple[int, int]], Optional[Tuple[int, int]]]]:
    """'2-5pm:' -> ([(14, 0)], (17, 0)); '5pm & 7:30pm:' -> ([(17, 0), (19, 30)], None).

    Returns None if any part cannot be read.
    """
    text = " ".join(text.lower().replace("noon", "12pm").split()).rstrip(":").strip()
    if not text:
        return None

    if "-" in text or "–" in text:
        parts = re.split(r"\s*[-–]\s*", text)
        if len(parts) != 2:
            return None
        a, b = ONE_TIME.match(parts[0]), ONE_TIME.match(parts[1])
        if not (a and b and b.group(3)):
            return None
        end = (_hour(int(b.group(1)), b.group(3)), int(b.group(2) or 0))
        meridiem = a.group(3)
        if meridiem is None:
            # "2-5pm" is 2pm; "11-2pm" is 11am.
            meridiem = b.group(3)
            if _hour(int(a.group(1)), meridiem) > end[0]:
                meridiem = "am"
        start = (_hour(int(a.group(1)), meridiem), int(a.group(2) or 0))
        return [start], end

    tokens = [t for t in re.split(r"\s*(?:,|&|\band\b)\s*", text) if t]
    matches = [ONE_TIME.match(t) for t in tokens]
    if not tokens or not all(matches):
        return None
    # "7 & 9pm": a bare hour takes the meridiem of the next one that has one.
    meridiems, following = [], None
    for m in reversed(matches):
        following = m.group(3) or following
        meridiems.append(following)
    meridiems.reverse()
    if None in meridiems:
        return None
    return [(_hour(int(m.group(1)), mer), int(m.group(2) or 0)) for m, mer in zip(matches, meridiems)], None


class BurrenScraper(BaseFeedScraper):
    HOME = Place("The Burren", "247 Elm Street", "Somerville", "02144")
    CATEGORY = EventCategory.MUSIC

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="The Burren", source_url="https://www.burren.com/music.html")
        # Only the year is inferred from it, and only through the weekday match.
        self.today = today

    def fetch_page(self) -> str:
        response = requests.get(self.source_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        events = self.parse_page(self.fetch_page(), self.today or date.today())
        if not events:
            raise ValueError(f"{self.source_name}: no acts read from {self.source_url} — has the table changed?")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def is_event(self, title: str) -> bool:
        if PLACEHOLDER.match(title):
            return False
        return super().is_event(title)

    # -- the page --------------------------------------------------------- #

    def parse_day(self, text: str, reference: date) -> Optional[date]:
        m = HEADER.match(self.clean_text(text).replace(".", ""))
        if not m:
            return None
        weekday = WEEKDAYS.get(m.group(1).lower())
        month = MONTHS.get(m.group(2).lower())
        if weekday is None or month is None:
            return None
        day = int(m.group(3))
        if m.group(4):
            try:
                printed = date(int(m.group(4)), month, day)
            except ValueError:
                return None
            return printed if printed.weekday() == weekday else None
        year = year_for_weekday(month, day, weekday, reference)
        return date(year, month, day) if year else None

    def parse_page(self, page: str, reference: date) -> List[EventCreate]:
        soup = self.parse_html(page)
        cells = soup.find_all(lambda tag: (
            (tag.name == "td" and ("HEADER" in (tag.get("class") or []) or "Room" in (tag.get("class") or [])))
            or (tag.name == "span" and set(tag.get("class") or []) & {"Time", "BAND", "Text"})
            or (tag.name == "a" and "24hourmusic.com" in (tag.get("href") or ""))
        ))

        acts: List[dict] = []
        day: Optional[date] = None
        header = room = time_text = None
        for cell in cells:
            classes = set(cell.get("class") or [])
            text = self.clean_text(cell.get_text(" "))
            if "HEADER" in classes:
                header, day = text, self.parse_day(text, reference)
                if day is None:
                    logger.warning(f"{self.source_name}: cannot date the header {text!r}; its acts are skipped")
                room = time_text = None
            elif "Room" in classes:
                room, time_text = text, None
            elif "Time" in classes:
                time_text = text
            elif "BAND" in classes:
                acts.append({"day": day, "header": header, "room": room, "time": time_text,
                             "title": text, "text": "", "url": None})
                time_text = None
            elif "Text" in classes and acts:
                acts[-1]["text"] = f"{acts[-1]['text']} {text}".strip()
            elif cell.name == "a" and acts and not acts[-1]["url"]:
                acts[-1]["url"] = cell["href"]

        events = []
        for act in acts:
            events.extend(self.parse_act(act))
        return events

    def parse_act(self, act: dict) -> List[EventCreate]:
        title = act["title"]
        if not self.is_event(title):
            return []
        if act["day"] is None:
            logger.warning(f"Skipping '{title}' - no parseable date (header {act['header']!r}) ({self.source_url})")
            return []
        times = parse_times(act["time"] or "")
        if times is None:
            logger.warning(f"Skipping '{title}' on {act['day']} - unreadable time {act['time']!r} ({self.source_url})")
            return []
        starts, end_clock = times

        room = re.sub(r"^the\s+", "", act["room"] or "", flags=re.I).title() or None
        where = f"{room}, the Burren, Davis Square." if room else "The Burren, Davis Square."
        description = f"{act['text']} — {where}" if act["text"] else f"{title}. {where}"

        events = []
        for hour, minute in starts:
            start = datetime.combine(act["day"], datetime.min.time()).replace(hour=hour, minute=minute)
            end = None
            if end_clock is not None:
                end = start.replace(hour=end_clock[0], minute=end_clock[1])
                if end <= start:
                    end += timedelta(days=1)
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end,
                venue_name=self.HOME.venue,
                street_address=self.HOME.street,
                city=self.HOME.city,
                state="MA",
                zip_code=self.HOME.zip_code,
                category=self.categorize(title),
                tags=[room.lower()] if room else [],
                source_name=self.source_name,
                source_url=act["url"] or self.source_url,
            ))
        return events
