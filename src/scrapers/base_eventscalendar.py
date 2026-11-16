"""Calendars embedded from eventscalendar.co (Inffuse), read as JSON.

A site embeds the calendar as `<div data-events-calendar-app
data-project-id="proj_...">` plus `dist.eventscalendar.co/embed.js`. The embed
makes two requests, and so do we:

  - the project's settings, which hold the calendar's `timezone`;
  - `/api/v0.1/projects/<id>/data/public/events`, every event the project has
    ever held, past and future, as JSON.

**Times.** An event's day is `startDate` ("2026-11-18") and its time is
`startHour`/`startMinutes`, a wall clock in the calendar's timezone. (`start`
is that day's UTC midnight and carries no time at all.) On 2026-11-16 the
rendered Bow Market calendar showed "Mingle Mayhem ... Wednesday, November 18,
2026 ... 7:00pm-9:00pm" for startDate 2026-11-18, startHour 19. A calendar set
to anything but Eastern is refused, as in base_squarespace. An event with no
`startDate` is skipped rather than dated from the `start` stamp, whose day the
embed derives by rules we have not seen.

`allday` events have no time. Neither do `dateonly` ones: the embed shows their
date alone ("Somerville Bookshop Crawl ... Saturday, November 28, 2026", no
time) although startHour holds a leftover 12. Both become all-day events.

**Recurring events** carry a `repeat` rule (weekly on given days, monthly on
the nth weekday, with `overrides` and `exceptions` for skipped dates). Only the
first occurrence — the date an editor typed — is emitted. Expanding the rule
means reproducing the embed's handling of end dates and removed occurrences,
and a misreading there invents dates (CLAUDE.md rule 1); a missing occurrence
only costs a listing.

**Where.** `location` is free text: "Aeronaut, 14 Tyler St, Somerville, MA
02143", "Upstairs at Bow", "Somerville, MA", or nothing. The city comes from a
ZIP code, from "<City>, MA", or from the bare words Cambridge or Somerville
(not "Somerville Ave"). Text that names no city is the source's HOME if it has
one, and is otherwise skipped. A subclass may set HOME_MARKER, a pattern that
identifies its home in the text ("Upstairs at Bow", "1 Bow Mkt Wy").
"""
import logging
import re
from datetime import datetime
from typing import List, Optional, Pattern, Tuple
from zoneinfo import ZoneInfo

import requests

from src.models.event import EventCreate
from src.scrapers.base_feed import EASTERN_ZONES, LOCAL_CITIES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

API = "https://inffuse.eventscalendar.co"

LOCAL_ZIPS = {
    "02138": "Cambridge", "02139": "Cambridge", "02140": "Cambridge", "02141": "Cambridge",
    "02142": "Cambridge", "02238": "Cambridge",
    "02143": "Somerville", "02144": "Somerville", "02145": "Somerville",
}

STATE = r"(?i:MA|Mass\.?(?!\s*Ave)|Massachusetts)"
MA_ZIP = re.compile(r"\b(0[12]\d{3})\b")
CITY_BEFORE_STATE = re.compile(r"([A-Za-z][A-Za-z.]*(?:\s+[A-Za-z][A-Za-z.]*)?)\s*,?\s+" + STATE + r"\b")
OTHER_STATE = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|"
    r"NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
LOCAL_WORD = re.compile(
    r"\b(Cambridge|Somerville)\b(?!\s+(?:st|street|ave|avenue|rd|road|pl|place|ct|court)\b\.?)", re.I)
STREET = re.compile(
    r"\b\d+[A-Za-z]?\s+(?:[A-Z][\w.']*\s+){1,3}?"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Ct|Court|Way|Wy|Pl|Place|Sq|Square|Blvd|Pkwy|Parkway|Ter|Terrace|"
    r"Ln|Lane|Dr|Drive|Hwy|Highway)\b\.?", re.I)


TRAILING_CITY = re.compile(r"(^|\s)(Cambridge|Somerville)(\s+" + STATE + r")?(\s+0\d{4})?\s*$", re.I)


def local_city(text: str) -> Tuple[Optional[str], bool]:
    """(city, named) for free-text location.

    `named` is True when the text names a city at all; the city is None when
    that city is outside the calendar's area. (None, False) means the text
    names no city.
    """
    if OTHER_STATE.search(text):
        return None, True
    zips = MA_ZIP.findall(text)
    if zips:
        return LOCAL_ZIPS.get(zips[-1]), True
    before_state = CITY_BEFORE_STATE.findall(text)
    if before_state:
        last = before_state[-1].split()[-1].title()
        return (last if last in LOCAL_CITIES else None), True
    word = LOCAL_WORD.findall(text)
    if word:
        return word[-1].title(), True
    return None, False


def venue_and_street(text: str) -> Tuple[Optional[str], Optional[str]]:
    """'Boston Figurative Arts Center, 285 Washington St, Somerville, MA' ->
    ('Boston Figurative Arts Center', '285 Washington St')."""
    street_match = STREET.search(text)
    street = street_match.group(0).strip().rstrip(".,") if street_match else None
    head = text[:street_match.start()] if street_match else text
    # The name is what precedes the street, or the first comma/pipe segment,
    # less a trailing "Somerville MA 02143" ("Somerville, MA" alone is no name).
    head = re.split(r"\s*[,|]\s*", head.strip(" ,|:-"))[0]
    head = TRAILING_CITY.sub("", re.sub(r"\s+", " ", head)).strip(" ,|:-")
    return (head or None), street


class BaseEventsCalendarScraper(BaseFeedScraper):
    """One eventscalendar.co project embedded on a venue's page."""

    # A pattern that, found in an event's location text, means HOME.
    HOME_MARKER: Optional[Pattern] = None

    def __init__(self, source_name: str, page_url: str, project: str):
        super().__init__(source_name=source_name, source_url=page_url)
        self.project = project

    # -- fetch ------------------------------------------------------------ #

    def fetch_settings(self) -> dict:
        form = {"exclude": "events", "id": self.project, "platform": "web", "app": "calendar"}
        response = requests.post(f"{API}/js/v0.1/calendar/data", params=form, data=form,
                                 headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return ((response.json().get("project") or {}).get("data") or {}).get("settings") or {}

    def fetch_events(self) -> list:
        response = requests.get(f"{API}/api/v0.1/projects/{self.project}/data/public/events",
                                params={"app": "calendar"}, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("value"), list):
            raise ValueError(f"{self.source_name}: eventscalendar.co returned no event list for {self.project}")
        return payload["value"]

    def scrape_events(self) -> List[EventCreate]:
        zone = self.fetch_settings().get("timezone")
        if zone not in EASTERN_ZONES:
            raise ValueError(f"{self.source_name}: calendar timezone is {zone!r}, not Eastern; "
                             f"its times cannot be trusted (see base_eventscalendar)")
        events = []
        for item in self.fetch_events():
            event = self.parse_item(item, zone)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    def wall_clock(self, day, hour, minute) -> Optional[datetime]:
        try:
            date = datetime.strptime(str(day), "%Y-%m-%d")
        except ValueError:
            return None
        if hour is None and minute is None:
            return date
        if not (isinstance(hour, int) and isinstance(minute, int) and 0 <= hour < 24 and 0 <= minute < 60):
            return None
        return date.replace(hour=hour, minute=minute)

    def first_occurrence_removed(self, item: dict, start: datetime, zone: str) -> bool:
        repeat = item.get("repeat") or {}
        override = (repeat.get("overrides") or {}).get(item.get("startDate")) or {}
        if override.get("removed"):
            return True
        epoch_ms = int(start.replace(tzinfo=ZoneInfo(zone)).timestamp() * 1000)
        exception = (repeat.get("exceptions") or {}).get(str(epoch_ms)) or {}
        return bool(exception.get("deleted"))

    def locate(self, title: str, location) -> Optional[Place]:
        text = self.text(location)
        if not text:
            if self.HOME is None:
                logger.info(f"{self.source_name}: skipping '{title}' - no location and no single home venue")
            return self.HOME
        if self.HOME and self.HOME_MARKER and self.HOME_MARKER.search(text):
            return self.HOME
        city, named = local_city(text)
        if named and city is None:
            logger.info(f"{self.source_name}: skipping '{title}' - '{text}' is outside the calendar's area")
            return None
        if city is None:
            if self.HOME is None:
                logger.info(f"{self.source_name}: skipping '{title}' - location '{text}' names no city")
            return self.HOME
        venue, street = venue_and_street(text)
        zip_match = MA_ZIP.findall(text)
        return Place((venue or street or city)[:150], street, city, zip_match[-1] if zip_match else None)

    def link(self, item: dict) -> Optional[str]:
        for key in ("buttons", "links"):
            for entry in item.get(key) or []:
                url = (entry or {}).get("url") if isinstance(entry, dict) else None
                if url and url.startswith("http"):
                    return url
        return None

    def parse_item(self, item: dict, zone: str) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None

        event_zone = item.get("timezone") or zone
        if event_zone not in EASTERN_ZONES:
            logger.warning(f"Skipping '{title}' - event timezone {event_zone!r} is not Eastern")
            return None
        if item.get("start_time"):
            # Events synced from an outside calendar carry an instant instead;
            # none of ours do, and the embed's handling of them is unseen.
            logger.warning(f"Skipping '{title}' - synced event with start_time, not read")
            return None

        all_day = bool(item.get("allday") or item.get("dateonly"))
        day = item.get("startDate")
        if all_day:
            start = self.wall_clock(day, None, None)
        else:
            start = self.wall_clock(day, item.get("startHour"), item.get("startMinutes"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (startDate={day!r}, "
                           f"startHour={item.get('startHour')!r}) ({self.source_url})")
            return None

        end = None
        if not all_day and item.get("endDate"):
            end = self.wall_clock(item.get("endDate"), item.get("endHour"), item.get("endMinutes"))
            if end is not None and end <= start:
                end = None

        if item.get("repeat"):
            if self.first_occurrence_removed(item, start, event_zone):
                return None
            logger.info(f"{self.source_name}: '{title}' repeats; listing its first date ({day}) only")

        where = self.locate(title, item.get("location"))
        if where is None:
            return None

        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

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
            website_url=self.link(item),
            image_url=(item.get("image") or {}).get("url") if isinstance(item.get("image"), dict) else None,
        )
