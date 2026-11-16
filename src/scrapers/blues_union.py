"""Blues Union Boston — the weekly blues dance at Dance Union, Union Square.

bluesunionboston.com describes the night in prose ("every Thursday, 7-11pm")
and lists no dates; prose is never turned into dates (CLAUDE.md, rule 1). The
site's "Boston blues calendar" page embeds a public Google Calendar, and that
calendar's iCal feed is where the dated schedule lives: Blues Union is one
recurring event (RRULE weekly on Thursdays) whose breaks the organizers enter
as exceptions — EXDATEs (Thanksgiving) and replacement entries titled
"No Blues Union - June break". The rule is expanded the way a calendar app
shows it, with those exceptions applied, as theatre_at_first does for its
Google Calendar.

**Bounds.** The rule has no end, so it is expanded only a short way ahead —
LOOKAHEAD_DAYS from the moment Google generated the feed (its DTSTAMP), not
from the scraper's clock, so a saved feed parses the same on any day. Breaks
are entered weeks ahead (the Thanksgiving EXDATE was in place by Oct 30); a
year of projected Thursdays would outrun them.

**Whose events.** The calendar is the Boston blues dance community's, and
also carries other groups' nights (Bluesy Tuesy, a monthly Burren night).
Only Blues Union's own room, 16 Bow St, is read: those other entries are
not Blues Union's to vouch for, and the Burren series has not been touched
since March 2025. (Bluesy Tuesy reaches the calendar through NESFA, whose
clubhouse hosts it.)
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import pytz
from dateutil.rrule import rrulestr
import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

CALENDAR_ID = "bluesdanceboston@gmail.com"
ICAL_URL = f"https://calendar.google.com/calendar/ical/{CALENDAR_ID.replace('@', '%40')}/public/basic.ics"

LOOKBACK_DAYS = 1
LOOKAHEAD_DAYS = 60

# Blues Union's room, as the calendar's LOCATION spells it: "16 Bow St, Somerville, MA 02143, USA".
HOME_STREET = re.compile(r"^\s*16\s+Bow\s+St(reet)?\b", re.I)

# Entries that announce a night is off: "*No Blues Union*", "No Blues Union - June break".
CANCELLED = re.compile(r"^\W*no\b|\bcancel+ed\b", re.I)

Property = Tuple[str, dict, str]          # (NAME, {PARAM: value}, value)


class BluesUnionScraper(BaseFeedScraper):
    HOME = Place("Blues Union at Dance Union", "16 Bow Street", "Somerville", "02143")
    # Lesson plus social dancing — filed as Boston Swing Central's nights are.
    CATEGORY = EventCategory.SPORTS

    def __init__(self):
        super().__init__(source_name="Blues Union Boston", source_url="https://bluesunionboston.com/")

    def categorize(self, title: str) -> EventCategory:
        return self.CATEGORY

    def fetch_feed(self) -> str:
        response = requests.get(ICAL_URL, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        if "BEGIN:VCALENDAR" not in response.text[:200]:
            raise ValueError(f"{self.source_name}: {ICAL_URL} did not return a calendar")
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_feed(self.fetch_feed())

    def parse_feed(self, raw: str) -> List[EventCreate]:
        components = [self.component(block) for block in raw.split("BEGIN:VEVENT")[1:]]
        stamps = [self.when(p)[0] for props in components for p in props if p[0] == "DTSTAMP"]
        stamps = [s for s in stamps if s is not None]
        if not stamps:
            raise ValueError(f"{self.source_name}: feed has no DTSTAMP — cannot tell which dates are current")
        as_of = max(stamps)
        window = (as_of - timedelta(days=LOOKBACK_DAYS), as_of + timedelta(days=LOOKAHEAD_DAYS))

        # Occurrences that a separate entry replaces (same UID, RECURRENCE-ID
        # naming the original slot) — including the "No Blues Union" notices.
        replaced = set()
        for props in components:
            rid = self.first(props, "RECURRENCE-ID")
            if rid:
                original, _ = self.when(rid)
                if original is not None:
                    replaced.add((self.value(props, "UID"), original))

        events: List[EventCreate] = []
        seen = set()
        for props in components:
            title = self.ical_text(self.value(props, "SUMMARY"))
            if not self.is_event(title):
                continue
            if CANCELLED.search(title) or self.value(props, "STATUS").upper() == "CANCELLED":
                continue
            location = self.ical_text(self.value(props, "LOCATION"))
            if not HOME_STREET.match(location):
                continue

            start, all_day = self.when(self.first(props, "DTSTART"))
            if start is None:
                logger.warning(f"Skipping '{title}' - no parseable DTSTART ({ICAL_URL})")
                continue
            end, _ = self.when(self.first(props, "DTEND"))
            duration = end - start if end is not None and end > start else None

            description = self.ical_text(self.value(props, "DESCRIPTION"))
            if len(description) < 20:
                description = self.fallback_description(title, self.HOME)

            for occurrence in self.occurrences(props, title, start, window, replaced):
                if (title, occurrence) in seen:
                    continue
                seen.add((title, occurrence))
                events.append(EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=occurrence,
                    end_datetime=occurrence + duration if duration else None,
                    all_day=all_day,
                    venue_name=self.HOME.venue,
                    street_address=self.HOME.street,
                    city=self.HOME.city,
                    state="MA",
                    zip_code=self.HOME.zip_code,
                    category=self.categorize(title),
                    source_name=self.source_name,
                    source_url=self.source_url,
                ))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- recurrence -------------------------------------------------------- #

    def occurrences(self, props: List[Property], title: str, start: datetime,
                    window: Tuple[datetime, datetime], replaced: set) -> List[datetime]:
        lo, hi = window
        rule = self.first(props, "RRULE")
        if rule is None or self.first(props, "RECURRENCE-ID"):
            return [start] if lo <= start <= hi else []
        try:
            candidates = rrulestr(self.local_rule(rule[2]), dtstart=start).between(lo, hi, inc=True)
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not expand '{title}' RRULE {rule[2]!r}: {e}")
            return [start] if lo <= start <= hi else []

        excluded = set()
        for _, params, value in (p for p in props if p[0] == "EXDATE"):
            for part in value.split(","):
                when, _ = self.parse_datetime(part, params)
                if when is not None:
                    excluded.add(when)
        uid = self.value(props, "UID")
        return [o for o in candidates if o not in excluded and (uid, o) not in replaced]

    @staticmethod
    def local_rule(rule: str) -> str:
        """Restate a UTC UNTIL as naive Eastern, to match the naive DTSTART."""
        def convert(match):
            utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return "UNTIL=" + to_eastern_naive(utc).strftime("%Y%m%dT%H%M%S")
        return re.sub(r"UNTIL=(\d{8}T\d{6})Z", convert, rule)

    # -- iCalendar --------------------------------------------------------- #

    @staticmethod
    def component(block: str) -> List[Property]:
        """One VEVENT as (NAME, params, value) triples, folded lines joined."""
        block = block.split("END:VEVENT", 1)[0]
        block = re.sub(r"BEGIN:VALARM.*?END:VALARM", "", block, flags=re.DOTALL)
        lines: List[str] = []
        for line in block.splitlines():
            if line.startswith((" ", "\t")):
                if lines:
                    lines[-1] += line[1:]
                continue
            lines.append(line)
        props: List[Property] = []
        for line in lines:
            if ":" not in line:
                continue
            head, value = line.split(":", 1)
            name, *raw_params = head.split(";")
            params = dict(p.split("=", 1) for p in raw_params if "=" in p)
            props.append((name.strip().upper(), {k.upper(): v for k, v in params.items()}, value.strip()))
        return props

    @staticmethod
    def first(props: List[Property], name: str) -> Optional[Property]:
        return next((p for p in props if p[0] == name), None)

    @classmethod
    def value(cls, props: List[Property], name: str) -> str:
        prop = cls.first(props, name)
        return prop[2] if prop else ""

    @classmethod
    def when(cls, prop: Optional[Property]) -> Tuple[Optional[datetime], bool]:
        if prop is None:
            return None, False
        return cls.parse_datetime(prop[2], prop[1])

    @staticmethod
    def parse_datetime(value: str, params: Optional[dict] = None) -> Tuple[Optional[datetime], bool]:
        """DATE or DATE-TIME -> (naive Eastern, is_date_only)."""
        value = (value or "").strip()
        params = params or {}
        try:
            if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
                return datetime.strptime(value[:8], "%Y%m%d"), True
            if value.endswith("Z"):
                utc = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return to_eastern_naive(utc), False
            local = datetime.strptime(value, "%Y%m%dT%H%M%S")
            tzid = params.get("TZID")
            if tzid and tzid != "America/New_York":
                local = to_eastern_naive(pytz.timezone(tzid).localize(local))
            return local, False
        except (ValueError, pytz.UnknownTimeZoneError):
            return None, False

    def ical_text(self, value: str) -> str:
        text = (value or "").replace("\\n", "\n").replace("\\N", "\n")
        text = text.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
        return self.text(text)
