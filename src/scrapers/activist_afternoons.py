"""Activist Afternoons — a Mobilize event feed, read through Mobilize's public API.

mobilize.us/activist-afternoons is the feed of the "Swing Blue Alliance 2"
organization (id 33342). It promotes events from many Massachusetts groups,
almost all outside Cambridge and Somerville. `api.mobilize.us/v1` is public and
documented; `timeslot_start=gte_now` lets Mobilize decide what is upcoming, and
each event lists only its upcoming timeslots — one EventCreate per timeslot.

Kept: public, in-person events whose public address is in Cambridge or
Somerville (on 2026-11-16 none were; through 2026 they were mostly the
"Cambridge Letter Team" postcard-writing afternoons). Not kept:
  - virtual events — Swing Blue Alliance's Zoom "Frontline Update" gives
    Cambridge 02140 as its location, which is the host's ZIP, not a venue;
  - events whose address is private until sign-up;
  - every event type, not only "community". The owner's link filters to
    event_type=5 (COMMUNITY), but no community-type event in this feed was
    local in all of 2026; the local ones were postcard writing and standouts.
    Set EVENT_TYPES to narrow it.
"""
import logging
from datetime import datetime, timezone
from typing import FrozenSet, List, Optional

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper

logger = logging.getLogger(__name__)

ORG_ID = 33342
API = f"https://api.mobilize.us/v1/organizations/{ORG_ID}/events"
MAX_PAGES = 10


class ActivistAfternoonsScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    # Mobilize event_type values to keep; empty keeps all.
    EVENT_TYPES: FrozenSet[str] = frozenset()

    def __init__(self):
        super().__init__(source_name="Activist Afternoons",
                         source_url="https://www.mobilize.us/activist-afternoons/")

    def fetch(self, url: str, params: Optional[dict]) -> dict:
        response = requests.get(url, params=params, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        url, params = API, {"timeslot_start": "gte_now", "per_page": 100}
        for _ in range(MAX_PAGES):
            payload = self.fetch(url, params)
            if "data" not in payload:
                raise ValueError(f"{self.source_name}: Mobilize payload has no 'data' — has the API changed?")
            for item in payload["data"]:
                events.extend(self.parse_item(item))
            url, params = payload.get("next"), None
            if not url:
                break
        else:
            logger.warning(f"{self.source_name}: stopped after {MAX_PAGES} pages; the feed has more")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_item(self, item: dict) -> List[EventCreate]:
        title = self.text(item.get("title"))
        url = item.get("browser_url") or self.source_url
        if not self.is_event(title):
            return []
        if item.get("visibility") != "PUBLIC" or item.get("is_virtual"):
            return []
        if self.EVENT_TYPES and item.get("event_type") not in self.EVENT_TYPES:
            return []
        if item.get("address_visibility") != "PUBLIC":
            logger.info(f"{self.source_name}: skipping '{title}' - address private until sign-up")
            return []

        loc = item.get("location") or {}
        lines = [line for line in (loc.get("address_lines") or []) if line]
        where = self.place(title, loc.get("venue"), lines[0] if lines else None,
                           loc.get("locality"), loc.get("postal_code"))
        if where is None:
            return []

        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)
        geo = loc.get("location") or {}

        out = []
        for slot in item.get("timeslots") or []:
            start = self.instant(slot.get("start_date"))
            if start is None or start.second:
                logger.warning(f"Skipping a timeslot of '{title}' - unusable start {slot.get('start_date')!r} ({url})")
                continue
            end = self.instant(slot.get("end_date"))
            if end is not None and end <= start:
                end = None
            out.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end,
                venue_name=where.venue,
                street_address=where.street,
                city=where.city,
                state="MA",
                zip_code=where.zip_code,
                latitude=geo.get("latitude"),
                longitude=geo.get("longitude"),
                category=self.categorize(title),
                source_name=self.source_name,
                source_url=url,
                image_url=item.get("featured_image_url") or None,
            ))
        return out

    @staticmethod
    def instant(epoch) -> Optional[datetime]:
        """Mobilize timeslots are UTC epoch seconds."""
        if not isinstance(epoch, (int, float)) or epoch <= 0:
            return None
        return to_eastern_naive(datetime.fromtimestamp(epoch, tz=timezone.utc))
