"""Arrow Street Arts, Harvard Square — its Tockify calendar, read as iCalendar.

The /shows page is an empty Squarespace page that embeds a Tockify calendar
(`data-tockify-calendar="arrowstarts"`). Tockify publishes the same calendar as
an iCalendar feed: UTC start and end stamps, title, description, a LOCATION of
"<room>, 2 Arrow St, Cambridge, MA 02138, USA", and the event's Tockify page.
The feed also keeps about a month of past events; EventValidator drops what is
too old.

Times are UTC (`...Z`) and converted to Eastern. Checked on 2026-11-16 against
the show pages the descriptions link to: Antje Duvekot 8 PM, Troy Roberts
7:30 PM, Gregg Allman 7:30 PM and both Darlingside nights 7 PM agree. Two do
not: Tall Heights is 7 PM here and in Passim's own listing but "8 PM" on its
Arrow show page, and William Basinski's installation day is entered as
9 AM-9 PM around a 7 PM performance. The calendar is the venue's own listing,
so it is used as published.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

FEED = "https://tockify.com/api/feeds/ics/arrowstarts"


def unfold(block: str) -> dict:
    """One VEVENT as {NAME: (params, value)}, with RFC 5545 folded lines joined."""
    fields: dict = {}
    current = None
    for line in block.splitlines():
        if line.startswith((" ", "\t")):
            if current:
                params, value = fields[current]
                fields[current] = (params, value + line[1:])
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        key, _, params = name.partition(";")
        current = key.strip().upper()
        fields[current] = (params, value.rstrip("\r"))
    return fields


def ical_text(value: str) -> str:
    text = value.replace("\\n", "\n").replace("\\N", "\n")
    return text.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def ical_time(params: str, value: str) -> Tuple[Optional[datetime], bool]:
    """(naive Eastern datetime, is_all_day). Only UTC stamps and plain dates are
    trusted; Tockify writes nothing else, and a floating time has no zone."""
    value = (value or "").strip()
    try:
        if "VALUE=DATE" in params.upper() and "T" not in value:
            return datetime.strptime(value[:8], "%Y%m%d"), True
        if value.endswith("Z"):
            instant = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return to_eastern_naive(instant), False
    except ValueError:
        pass
    return None, False


class ArrowStreetArtsScraper(BaseFeedScraper):
    HOME = Place("Arrow Street Arts", "2 Arrow Street", "Cambridge", "02138")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Arrow Street Arts", source_url="https://www.arrowstarts.org/shows")

    def fetch_calendar(self) -> str:
        response = requests.get(FEED, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        if "BEGIN:VCALENDAR" not in response.text[:200]:
            raise ValueError(f"{self.source_name}: {FEED} is not an iCalendar feed")
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        raw = self.fetch_calendar()
        events = []
        for chunk in raw.split("BEGIN:VEVENT")[1:]:
            event = self.parse_vevent(unfold(chunk.split("END:VEVENT", 1)[0]))
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_vevent(self, fields: dict) -> Optional[EventCreate]:
        def get(name):
            return fields.get(name, ("", ""))

        title = self.text(ical_text(get("SUMMARY")[1]))
        if not self.is_event(title):
            return None
        url = get("URL")[1].strip() or self.source_url

        start, all_day = ical_time(*get("DTSTART"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end, _ = ical_time(*get("DTEND"))
        if all_day or (end is not None and end <= start):
            end = None

        # "The Studio at Arrow Street Arts, 2 Arrow St, Cambridge, MA 02138, USA"
        parts = [p.strip() for p in ical_text(get("LOCATION")[1]).split(",")]
        venue = parts[0] if parts else ""
        street = parts[1] if len(parts) > 1 else ""
        city = ", ".join(parts[2:]) if len(parts) > 2 else ""
        where = self.place(title, venue, street, city)
        if where is None:
            return None

        description = self.text(ical_text(get("DESCRIPTION")[1]))
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
            source_url=url,
            image_url=get("X-TKF-FEATURED-IMAGE")[1].strip() or None,
        )
