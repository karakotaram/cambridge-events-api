"""MIT Open Space programming, read from the Squarespace calendar's JSON

openspace.mit.edu is Squarespace. Any collection page returns its data as JSON
with `?format=json`; for the calendar, `upcoming[]` holds every upcoming event
with `startDate`/`endDate` as epoch milliseconds (UTC). The milliseconds carry
noise from when the event was created (15:30:00.167Z), so instants are floored
to the minute before conversion to Eastern.

The HTML scraper this replaces fell back to datetime.now() at 18:00 whenever a
date did not parse, and invented 18:00 for a date without a time.

Rescheduling: the venue renames the original listing "RESCHEDULED: <title>" and
leaves it on its old date, then publishes the new date as a separate listing
under the plain title. "RESCHEDULED: Outdoor Movie: The Wiz" sits on Aug 28
saying "this event will be rescheduled to Thursday, September 3"; "Outdoor
Movie: The Wiz" is on Sep 3. So a title that *starts* with RESCHEDULED,
CANCELLED or POSTPONED is a notice, not an event; one that merely mentions the
word is kept.
"""
import html
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE = "https://www.openspace.mit.edu"
USER_AGENT = "CambridgeCalendar/1.0 (+https://cambridgecalendar.com)"

NOTICE = re.compile(r"^\s*(?:re-?scheduled|cancell?ed|postponed)\b", re.IGNORECASE)
CITY_ZIP = re.compile(r"^\s*([^,]+),.*?(\d{5})?\s*$")


class OpenSpaceMITScraper(BaseScraper):
    """Scraper for MIT Open Space Programming events"""

    def __init__(self):
        super().__init__(
            source_name="MIT Open Space",
            source_url=f"{BASE}/calendar",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        response = requests.get(
            self.source_url,
            params={"format": "json"},
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        events = self.parse_collection(response.json())
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_collection(self, body: dict) -> List[EventCreate]:
        upcoming = body.get("upcoming")
        if not isinstance(upcoming, list):
            raise ValueError("Squarespace calendar JSON has no upcoming[] list")
        events = []
        for item in upcoming:
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"Failed to parse MIT Open Space item {item.get('id')}: {e}")
                continue
            if event:
                events.append(event)
        return events

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        path = item.get("fullUrl") or ""
        if not title or not path:
            return None
        url = path if path.startswith("http") else f"{BASE}{path}"

        if NOTICE.search(title):
            # The venue's notice on the old date; the new date is its own item
            logger.info(f"Skipping notice '{title}' ({url})")
            return None

        start = self.read_instant(item.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - unreadable startDate {item.get('startDate')!r} ({url})")
            return None
        end = self.read_instant(item.get("endDate"))
        if end is not None and end <= start:
            end = None

        description = (self._text(item.get("excerpt"))
                       or self._text(item.get("body"))[:600]
                       or f"{title} at MIT Open Space")

        venue, street, city, zip_code, lat, lng = self._location(item.get("location") or {})

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=venue,
            street_address=street,
            city=city,
            state="MA" if city else None,
            zip_code=zip_code,
            latitude=lat,
            longitude=lng,
            category=self._detect_category(title, description),
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("assetUrl") or None,
        )

    @staticmethod
    def read_instant(epoch_ms) -> Optional[datetime]:
        """Epoch milliseconds (UTC) -> naive Eastern wall clock, on the minute."""
        if not isinstance(epoch_ms, (int, float)) or isinstance(epoch_ms, bool) or epoch_ms <= 0:
            return None
        instant = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return to_eastern_naive(instant.replace(second=0, microsecond=0))

    def _location(self, loc: dict) -> tuple:
        """(venue, street, city, zip, lat, lng) from a Squarespace location."""
        venue = self.clean_text(loc.get("addressTitle") or "") or None
        street = self.clean_text(loc.get("addressLine1") or "") or None
        city = zip_code = None
        match = CITY_ZIP.match(loc.get("addressLine2") or "")
        if match:
            city = match.group(1).strip() or None
            zip_code = match.group(2)
        lat, lng = loc.get("markerLat") or loc.get("mapLat"), loc.get("markerLng") or loc.get("mapLng")
        if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
            lat = lng = None
        return venue, street, city, zip_code, lat, lng

    @staticmethod
    def _text(value) -> str:
        if not value or not isinstance(value, str):
            return ""
        text = BeautifulSoup(value, "html.parser").get_text(" ") if "<" in value else value
        return " ".join(html.unescape(text).split())

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'band', 'jazz', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['art', 'exhibit', 'gallery', 'opening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['comedy', 'improv', 'standup']):
            return EventCategory.THEATER
        elif any(word in text for word in ['lecture', 'talk', 'speaker']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['game', 'trivia', 'social']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['film', 'movie', 'screening']):
            return EventCategory.ARTS_CULTURE

        return EventCategory.COMMUNITY
