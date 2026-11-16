"""iCalendar (.ics) feeds, read without a calendar library.

Two kinds of publisher in this repo serve one:

  - WordPress **Events Manager** at `/events.ics` (Somerville Arts Council,
    SomArt Space; Arts at the Armory reads the same plugin by hand). Each
    upcoming event is one VEVENT; the plugin expands its own recurrences, so a
    feed has no RRULE.
  - **Google Calendar**'s public `basic.ics` (Somerville Bike Kitchen). Here a
    weekly series is one VEVENT with an RRULE, and a changed or moved instance
    is a second VEVENT carrying RECURRENCE-ID.

**What is read.** One-off VEVENTs, and RECURRENCE-ID overrides of a series
whose summary differs from the series' own (Bike Kitchen's "SBK Friendsgiving"
replaces one Thursday's open shop). A series master with an RRULE is *not*
expanded: on the calendars here those are standing hours and internal meetings
("Open Shop", "SBK Monthly Staff Meeting"), and expanding RRULE + EXDATE +
overrides correctly is a calendar engine's job — getting it slightly wrong
would publish nights the shop is closed. An override that keeps the series'
summary is a moved instance of those standing hours, and is skipped with them.

**Times.** CLAUDE.md rule 2 wants naive Eastern wall clock. A DTSTART is
  - `TZID=<zone>:YYYYMMDDTHHMMSS` — wall clock in that zone, converted to Eastern;
  - `YYYYMMDDTHHMMSSZ` — UTC, converted to Eastern (Google writes most one-off
    events this way, e.g. 20261208T003000Z is 7:30 PM on Dec 7);
  - `VALUE=DATE:YYYYMMDD` — an all-day event;
  - floating (no zone) — taken as the calendar's X-WR-TIMEZONE if that is
    Eastern, otherwise skipped rather than guessed.
A start carrying seconds is skipped: real listings are on the minute (rule 1).

**Places.** LOCATION is one comma-separated line — Events Manager writes
`Name, street, City, MA, 02145, United States`, Google writes
`Name, street, City, MA 02143, USA`. `split_address` reads either by anchoring
on the state; base_feed's `place()` then applies the Cambridge/Somerville rule.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import EASTERN_ZONES, TITLE_CATEGORIES, BaseFeedScraper

logger = logging.getLogger(__name__)

# NAME -> list of (params, value); a property may repeat (CATEGORIES, ATTACH).
VEvent = Dict[str, List[Tuple[Dict[str, str], str]]]

_STATE = re.compile(r"^(MA|Mass\.?|Massachusetts)(?:\s+(\d{5})(?:-\d{4})?)?$", re.I)
_ZIP_ONLY = re.compile(r"^(\d{5})(?:-\d{4})?$")
_COUNTRY = re.compile(r"^(USA|US|United States( of America)?)$", re.I)


def split_address(text: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """'Name, street, City, MA 02143, USA' -> (venue, street, city, zip).

    Anchors on the Massachusetts state token: the part before it is the city,
    the one before that the street, anything earlier the venue's name. With no
    state token the whole line is returned as the venue and the city is left
    for base_feed to resolve (or refuse).
    """
    parts = [p.strip() for p in (text or "").split(",")]
    parts = [p for p in parts if p]
    while parts and _COUNTRY.match(parts[-1]):
        parts.pop()
    if not parts:
        return None, None, None, None
    for i, part in enumerate(parts):
        m = _STATE.match(part)
        if not m or i == 0:
            continue
        zip_code = m.group(2)
        if not zip_code and i + 1 < len(parts) and _ZIP_ONLY.match(parts[i + 1]):
            zip_code = parts[i + 1][:5]
        city = parts[i - 1]
        street = parts[i - 2] if i >= 2 else None
        venue = ", ".join(parts[: i - 2]) if i >= 3 else None
        return venue, street, city, zip_code
    return ", ".join(parts), None, None, None


def unescape(value: str) -> str:
    """RFC 5545 TEXT escapes."""
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_property(line: str) -> Optional[Tuple[str, Dict[str, str], str]]:
    """'DTSTART;TZID=America/New_York:20261116T183000' -> (name, params, value).

    The first colon outside double quotes ends the name-and-parameters part;
    parameter values may quote a colon (Apple's X-APPLE-STRUCTURED-LOCATION).
    """
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *raw_params = head.split(";")
    params = {}
    for p in raw_params:
        key, _, val = p.partition("=")
        params[key.strip().upper()] = val.strip().strip('"')
    return name.strip().upper(), params, value


def parse_calendar(text: str) -> Tuple[Dict[str, str], List[VEvent]]:
    """(calendar-level properties, VEVENTs) from an iCalendar document."""
    lines: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)

    calendar: Dict[str, str] = {}
    events: List[VEvent] = []
    depth: List[str] = []
    current: Optional[VEvent] = None
    for line in lines:
        prop = _split_property(line)
        if prop is None:
            continue
        name, params, value = prop
        if name == "BEGIN":
            depth.append(value.upper())
            if value.upper() == "VEVENT":
                current = {}
            continue
        if name == "END":
            if value.upper() == "VEVENT" and current is not None:
                events.append(current)
                current = None
            if depth:
                depth.pop()
            continue
        if current is not None and depth and depth[-1] == "VEVENT":
            current.setdefault(name, []).append((params, value))
        elif depth == ["VCALENDAR"]:
            calendar.setdefault(name, value)
    return calendar, events


class BaseICalScraper(BaseFeedScraper):
    """One iCalendar feed."""

    # Events lasting longer than this are standing listings, not events:
    # Somerville Arts Council's phone-box art installations run 2015-2030.
    MAX_DAYS: Optional[int] = None

    # ICS CATEGORIES that mark a listing as not a public event (lowercase).
    SKIP_CATEGORIES: frozenset = frozenset()

    # ICS CATEGORIES -> our category, consulted when the title names no kind.
    CATEGORY_MAP: Dict[str, EventCategory] = {}

    def __init__(self, source_name: str, source_url: str, feed_url: str):
        super().__init__(source_name=source_name, source_url=source_url)
        self.feed_url = feed_url
        self.floating_zone: Optional[str] = None

    def fetch_feed(self) -> str:
        response = requests.get(self.feed_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        text = self.fetch_feed()
        if "BEGIN:VCALENDAR" not in text:
            raise ValueError(f"{self.source_name}: {self.feed_url} did not return an iCalendar feed")
        calendar, vevents = parse_calendar(text)
        self.floating_zone = calendar.get("X-WR-TIMEZONE") or calendar.get("TZID")

        # Summaries of RRULE series, so an override can be compared to its series.
        series = {self.value(v, "UID"): self.text(unescape(self.value(v, "SUMMARY") or ""))
                  for v in vevents if "RRULE" in v}

        events = []
        for vevent in vevents:
            event = self.parse_vevent(vevent, series)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- fields ----------------------------------------------------------- #

    @staticmethod
    def value(vevent: VEvent, name: str) -> Optional[str]:
        found = vevent.get(name)
        return found[0][1] if found else None

    @staticmethod
    def categories(vevent: VEvent) -> List[str]:
        out = []
        for _, value in vevent.get("CATEGORIES", []):
            out.extend(c.strip() for c in unescape(value).split(",") if c.strip())
        return out

    def when(self, vevent: VEvent, name: str) -> Tuple[Optional[datetime], bool]:
        """DTSTART/DTEND -> (naive Eastern datetime, is_all_day), or (None, False)."""
        found = vevent.get(name)
        if not found:
            return None, False
        params, raw = found[0]
        raw = raw.strip()
        try:
            if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", raw):
                return datetime.strptime(raw[:8], "%Y%m%d"), True
            if raw.endswith("Z"):
                instant = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return to_eastern_naive(instant), False
            local = datetime.strptime(raw, "%Y%m%dT%H%M%S")
        except ValueError:
            return None, False
        zone = params.get("TZID") or self.floating_zone
        if zone in EASTERN_ZONES:
            return local, False
        try:
            return to_eastern_naive(local.replace(tzinfo=ZoneInfo(zone))), False
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return None, False

    def location(self, vevent: VEvent) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        return split_address(unescape(self.value(vevent, "LOCATION") or ""))

    def category_for(self, title: str, vevent: VEvent) -> EventCategory:
        for category, pattern in TITLE_CATEGORIES:
            if pattern.search(title):
                return category
        for name in self.categories(vevent):
            if name.lower() in self.CATEGORY_MAP:
                return self.CATEGORY_MAP[name.lower()]
        return self.CATEGORY

    # -- hooks ------------------------------------------------------------ #

    def keep(self, title: str, vevent: VEvent) -> bool:
        """Subclass filter for listings that are not public events."""
        return True

    # -- one event -------------------------------------------------------- #

    def parse_vevent(self, vevent: VEvent, series: Dict[Optional[str], str]) -> Optional[EventCreate]:
        title = self.text(unescape(self.value(vevent, "SUMMARY") or ""))
        url = (self.value(vevent, "URL") or "").strip() or self.source_url

        if "RRULE" in vevent or "RDATE" in vevent:
            logger.info(f"{self.source_name}: not expanding recurring series '{title}' (see base_ical)")
            return None
        if "RECURRENCE-ID" in vevent and series.get(self.value(vevent, "UID")) == title:
            return None  # a moved instance of standing hours; see module docstring
        if (self.value(vevent, "STATUS") or "").upper() == "CANCELLED":
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None
        if (self.value(vevent, "CLASS") or "PUBLIC").upper() in ("PRIVATE", "CONFIDENTIAL"):
            return None
        if not self.is_event(title):
            return None
        if {c.lower() for c in self.categories(vevent)} & self.SKIP_CATEGORIES:
            logger.info(f"{self.source_name}: skipping '{title}' - category {self.categories(vevent)}")
            return None
        if not self.keep(title, vevent):
            return None

        start, all_day = self.when(vevent, "DTSTART")
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable start "
                           f"({vevent.get('DTSTART')}) ({url})")
            return None
        if start.second or start.microsecond:
            logger.warning(f"Skipping '{title}' - start {start} is not on the minute ({url})")
            return None
        end, _ = self.when(vevent, "DTEND")
        if self.MAX_DAYS is not None and end is not None and end - start > timedelta(days=self.MAX_DAYS):
            logger.info(f"{self.source_name}: skipping '{title}' - runs {start:%Y-%m-%d} to {end:%Y-%m-%d}, "
                        f"a standing listing rather than an event")
            return None
        if all_day or (end is not None and end <= start):
            end = None

        venue, street, city, zip_code = self.location(vevent)
        where = self.place(title, venue, street, city, zip_code)
        if where is None:
            return None

        description = self.text(unescape(self.value(vevent, "DESCRIPTION") or ""))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        image = None
        for params, value in vevent.get("ATTACH", []):
            if params.get("FMTTYPE", "").startswith("image/") and value.startswith("http"):
                image = value
                break

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
            category=self.category_for(title, vevent),
            source_name=self.source_name,
            source_url=url,
            image_url=image,
        )
