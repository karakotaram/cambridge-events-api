"""The Jungle, Somerville — its Eventbrite listings, via the site's SociableKit widget.

thejunglemusicclub.com/events-calendar embeds a SociableKit "Eventbrite Events"
widget (id 87910). The widget's data is one public JSON file that SociableKit
refreshes from the venue's Eventbrite organizer page: each event with its local
start (`start_time_raw`, "2026-11-17 20:00:00"), that `timezone`, the address
and the Eventbrite link. The widget shows those same strings ("8:00 pm").

The venue keeps template events on its calendar ("Ticketed - Dummy event to
copy", "Private Event - Dummy to copy") and blocks nights as "Private Event"
or "Closed at 8pm". Those are dropped by title.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

FEED = "https://data.accentapi.com/feed/87910.json"
TEMPLATE = re.compile(r"\bdummy\b", re.I)


class JungleScraper(BaseFeedScraper):
    HOME = Place("The Jungle", "6 Sanborn Court", "Somerville", "02143")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="The Jungle", source_url="https://www.thejunglemusicclub.com/events-calendar")

    def fetch_feed(self) -> dict:
        response = requests.get(FEED, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("events"), list):
            raise ValueError(f"{self.source_name}: {FEED} has no 'events' list")
        return payload

    def scrape_events(self) -> List[EventCreate]:
        events = []
        for item in self.fetch_feed()["events"]:
            event = self.parse_item(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def is_event(self, title: str) -> bool:
        if TEMPLATE.search(title):
            logger.info(f"{self.source_name}: skipping template listing '{title}'")
            return False
        return super().is_event(title)

    @staticmethod
    def local_time(raw) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(str(raw).replace(" ", "T")).replace(second=0, microsecond=0)
        except ValueError:
            return None

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("name"))
        if not self.is_event(title):
            return None
        url = item.get("ticket_uri") or item.get("event_fb_link") or self.source_url

        if item.get("timezone") not in EASTERN_ZONES:
            logger.warning(f"Skipping '{title}' - timezone {item.get('timezone')!r} is not Eastern ({url})")
            return None
        start = self.local_time(item.get("start_time_raw"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end = self.local_time(item.get("end_time_raw"))
        if end is not None and end <= start:
            end = None

        # "The Jungle, 6 Sanborn Court, Somerville, MA 02143"
        parts = [p.strip() for p in self.text(item.get("location")).split(",")]
        where = self.place(title, parts[0], parts[1] if len(parts) > 1 else "", ", ".join(parts[2:]))
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
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            cost=self.text(item.get("price_range")) or None,
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("pic_big") or None,
        )
