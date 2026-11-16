"""Green Cambridge — its Tockify calendar, read through Tockify's public API.

greencambridge.org/calendar is a Squarespace page that embeds a Tockify
calendar ("green.cambridge.public"). The embed's own data source,
api.tockify.com/api/ngevent, returns every event as JSON with its start as
epoch milliseconds plus the zone (America/New_York), an address line, and a
status. The API picks the window itself (from a few weeks back); no clock is
needed here.

What is kept: scheduled events at a Cambridge or Somerville address — walks at
Alewife, Groundwork Somerville's harvest festival, workshops. What is not:
  - events with no address (HOME is None: Green Cambridge's office is not where
    its events happen), which on 2026-11-16 included its trash cleanups and
    the all-day observances ("NATIONAL ARBOR DAY");
  - meetings — the city's Climate Committee and Committee on Public Planting,
    which City of Cambridge already lists with their rooms, and Green
    Cambridge's own board meeting.
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper
from src.scrapers.base_ical import split_address

logger = logging.getLogger(__name__)

API = "https://api.tockify.com/api/ngevent"
CALENDAR = "green.cambridge.public"

MEETING = re.compile(r"\b(meeting|mtg|committee)\b", re.I)


class GreenCambridgeScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Green Cambridge", source_url="https://www.greencambridge.org/calendar")

    def fetch(self) -> dict:
        response = requests.get(API, params={"calname": CALENDAR, "max": 200},
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        payload = self.fetch()
        if "events" not in payload:
            raise ValueError(f"{self.source_name}: Tockify payload has no 'events' — has the API changed?")
        if (payload.get("metaData") or {}).get("hasNext"):
            logger.warning(f"{self.source_name}: Tockify has more events than one page; reading the first")
        events = [e for e in (self.parse_item(i) for i in payload["events"]) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def instant(when: dict) -> Optional[datetime]:
        ms = (when or {}).get("millis")
        if not isinstance(ms, (int, float)):
            return None
        return to_eastern_naive(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        content = item.get("content") or {}
        title = self.text((content.get("summary") or {}).get("text"))
        if not self.is_event(title):
            return None
        if (item.get("status") or {}).get("name", "").lower() != "scheduled":
            logger.info(f"{self.source_name}: skipping '{title}' - status {item.get('status')}")
            return None
        if MEETING.search(title):
            logger.info(f"{self.source_name}: skipping meeting '{title}'")
            return None

        when = item.get("when") or {}
        start, end = self.instant(when.get("start")), self.instant(when.get("end"))
        if start is None or start.second or start.microsecond:
            logger.warning(f"Skipping '{title}' - no usable start ({when.get('start')})")
            return None
        all_day = bool(when.get("allDay"))
        if all_day:
            # An all-day event's millis is local midnight in its tzid; keep the date only.
            start, end = start.replace(hour=0, minute=0), None
        if end is not None and end <= start:
            end = None

        venue, street, city, zip_code = split_address(content.get("address"))
        where = self.place(title, venue, street, city, zip_code)
        if where is None:
            return None

        uid = (item.get("eid") or {}).get("uid")
        tid = (item.get("eid") or {}).get("tid")
        url = f"https://tockify.com/{CALENDAR}/detail/{uid}/{tid}" if uid and tid else self.source_url

        description = self.text((content.get("description") or {}).get("text"))
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
        )
