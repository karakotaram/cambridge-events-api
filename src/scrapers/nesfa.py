"""NESFA (New England Science Fiction Association) — the events calendar.

WordPress with a custom theme and no events plugin or feed. /events/ prints
every upcoming entry server-side, one `div.event-inside` each:

    <strong>Tuesday, November 17, 2026 • 7:30pm to 10:30pm</strong>
    <span class="the-date">Bluesy Tuesy</span>
    Weekly blues dance. ...  At the <a href=".../the-clubhouse/">clubhouse</a>.

**Where.** NESFA's clubhouse is in Somerville. The calendar also lists
conventions elsewhere (Philcon in New Jersey, Worldcon in Montreal, Boskone at a
Boston hotel), meetings on Zoom, and parties at members' homes. Only entries
that say they are at the clubhouse are kept; anything else is skipped rather
than placed by guesswork.

**When.** Printed times run "12:00 Noon to whenever" and, once, "12:00 Noon to
10:00" — an end without a meridiem, which is dropped rather than guessed.
Multi-day entries ("Friday, February 12 - Sunday, February 14, 2027") print
the year once; the start's year is the one that puts its printed weekday on
its date.
"""
import logging
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number, year_for_weekday

logger = logging.getLogger(__name__)

SITE = "https://www.nesfa.org"

DAY = r"((?:mon|tues|wednes|thurs|fri|satur|sun)day),?\s+([a-z]+)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?"
WHEN = re.compile(rf"^\s*{DAY}(?:\s*-\s*{DAY})?\s*(?:•\s*(.*))?$", re.I)

# Volunteer work for the convention, not programming for the public.
INTERNAL = re.compile(r"\bwork session\b|\bbusiness meeting\b|\bcommittee\b", re.I)


class NESFAScraper(BaseFeedScraper):
    HOME = Place("NESFA Clubhouse", "504 Medford Street", "Somerville", "02145")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="NESFA", source_url=f"{SITE}/events/")

    def fetch(self) -> str:
        response = requests.get(self.source_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch(), "html.parser")
        entries = soup.select("#allEvents div.event-inside")
        if not entries:
            raise ValueError(f"{self.source_name}: no entries in #allEvents on {self.source_url} — layout changed?")
        events = []
        seen = set()
        for entry in entries:
            event = self.parse_entry(entry)
            if event and (event.title, event.start_datetime) not in seen:
                seen.add((event.title, event.start_datetime))
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_entry(self, entry) -> Optional[EventCreate]:
        name = entry.select_one(".the-date")
        title = self.text(name.get_text(" ")) if name else ""
        if not self.is_event(title):
            return None
        if INTERNAL.search(title):
            logger.info(f"{self.source_name}: skipping internal '{title}'")
            return None
        if not entry.find("a", href=re.compile(r"/events/the-clubhouse/?$")):
            logger.info(f"{self.source_name}: skipping '{title}' - not at the clubhouse")
            return None

        when_el = entry.find("strong")
        when_text = self.text(when_el.get_text(" ")) if when_el else ""
        times = self.when(when_text)
        if times is None:
            logger.warning(f"Skipping '{title}' - no parseable date in {when_text!r} ({self.source_url})")
            return None
        start, end, all_day = times

        description = self.description(entry, when_el, name)
        where = self.HOME
        if len(description) < 20:
            description = self.fallback_description(title, where)
        link = name.find("a", href=True) if name else None
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=self.source_url,
            website_url=link["href"] if link else None,
        )

    def when(self, text: str):
        """'Tuesday, November 17, 2026 • 7:30pm to 10:30pm' -> (start, end, all_day), or None."""
        match = WHEN.match(text)
        if not match:
            return None
        wd1, mon1, day1, year1, wd2, mon2, day2, year2, times = match.groups()
        month1, month2 = month_number(mon1), month_number(mon2) if mon2 else None
        if month1 is None:
            return None
        if year1:
            first = dated(int(year1), month1, int(day1), weekday_number(wd1))
        elif year2 and month2:
            # A range prints its year once, at the end. The start's year is the
            # one that fits its weekday, and the range must then run forwards.
            last = dated(int(year2), month2, int(day2), weekday_number(wd2))
            year = year_for_weekday(month1, int(day1), weekday_number(wd1), (int(year2) - 1, int(year2)))
            first = dated(year, month1, int(day1)) if year else None
            if last is None or (first and first > last):
                first = None
        else:
            first = None
        if first is None:
            return None

        if not times:
            return at(first, None), None, True
        parts = re.split(r"\bto\b", times, maxsplit=1)
        start_time = clock(parts[0])
        if start_time is None:
            return at(first, None), None, True
        start = at(first, start_time)
        end_time = clock(parts[1]) if len(parts) > 1 else None
        end = at(first, end_time) if end_time else None
        if end is not None and end <= start:
            end = None
        return start, end, False

    def description(self, entry, when_el, name) -> str:
        body = BeautifulSoup(str(entry.select_one(".the-event") or entry), "html.parser")
        for el in body.find_all(["strong", "span"], limit=2):
            if el.name == "strong" or "the-date" in (el.get("class") or []):
                el.decompose()
        text = self.text(body.get_text(" "))
        return re.sub(r"\s*At the clubhouse\s*\.?\s*$", "", text, flags=re.I).strip()
