"""Pandemonium Books & Games, Central Square — its BookThatApp event calendar.

The store's calendar page (/apps/bookthatapp/calendar on its Shopify site) is
drawn by BookThatApp from `availability/schedule.json`: every bookable
occurrence in a date range — game nights, tournaments, D&D sessions, the
occasional author event — with its start and end as [Y, M, D, h, m, s] on the
store's wall clock. The calendar page declares the store's zone as
America/New_York, so those arrays are Eastern as written.

The same page also overlays `calendar/events.json`, which holds notices
("We are CLOSED for Thanksgiving", ban-list announcements), not events; it is
not read.
"""
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SCHEDULE = "https://pandemonium-books-games.bookthatapp.com/availability/schedule.json"
SHOP = "https://pandemoniumbooks.com"
WINDOW_DAYS = 60


class PandemoniumScraper(BaseFeedScraper):
    HOME = Place("Pandemonium Books & Games", "4 Pleasant Street", "Cambridge", "02139")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Pandemonium Books & Games",
                         source_url=f"{SHOP}/apps/bookthatapp/calendar")
        # Only bounds the query window.
        self.today = today

    def fetch_schedule(self, start: date, end: date) -> dict:
        response = requests.get(SCHEDULE, params={
            "start": start.isoformat(), "end": end.isoformat(),
            "calendar": "true", "show_all_course_occurrences": "false",
        }, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        today = self.today or date.today()
        data = self.fetch_schedule(today, today + timedelta(days=WINDOW_DAYS))
        if "schedule" not in data:
            raise ValueError(f"{self.source_name}: schedule.json has no 'schedule' list")
        events, seen = [], set()
        for item in data["schedule"]:
            event = self.parse_item(item)
            if event and (event.title, event.start_datetime) not in seen:
                seen.add((event.title, event.start_datetime))
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def wall_clock(parts) -> Optional[datetime]:
        if not isinstance(parts, list) or len(parts) < 5:
            return None
        try:
            return datetime(*[int(p) for p in parts[:5]])
        except (TypeError, ValueError):
            return None

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        handle = item.get("handle")
        url = f"{SHOP}/products/{handle}" if handle else self.source_url

        start = self.wall_clock(item.get("start"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (start={item.get('start')!r}) ({url})")
            return None
        if item.get("start") and len(item["start"]) > 5 and int(item["start"][5] or 0):
            logger.warning(f"Skipping '{title}' - start carries seconds {item['start']!r} ({url})")
            return None
        all_day = bool(item.get("allDay"))
        end = None if all_day else self.wall_clock(item.get("end"))
        if end is not None and end <= start:
            end = None

        if item.get("locations"):
            logger.info(f"{self.source_name}: skipping '{title}' - booked at {item['locations']!r}, not the store")
            return None

        notes = ["Fully booked." if item.get("bookedOut") else "",
                 "Registration through the store's booking calendar."]
        description = f"{title} at Pandemonium Books & Games, Central Square. " + " ".join(n for n in notes if n)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            category=self.categorize(title),
            registration_required=True,
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("image") or None,
        )
