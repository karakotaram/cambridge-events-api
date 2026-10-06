"""Scraper for Mount Auburn Cemetery events.

Reads the site's Tribe Events REST API. The /events/ list page embeds JSON-LD
for only its first ten events — eleven days ahead on 2026-10-06 — while the
API returns the whole programme, ~39 events reaching a year out.
"""
import html
import logging
from datetime import datetime, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://mountauburn.org/wp-json/tribe/events/v1/events"
PER_PAGE = 50
MAX_PAGES = 5


class MountAuburnScraper(BaseScraper):
    """Scraper for Mount Auburn Cemetery events"""

    def __init__(self):
        super().__init__(
            source_name="Mount Auburn Cemetery",
            source_url="https://mountauburn.org/events/",
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen: set = set()
        for page in range(1, MAX_PAGES + 1):
            response = requests.get(
                API_URL,
                params={"per_page": PER_PAGE, "start_date": "now", "status": "publish", "page": page},
                timeout=30,
                headers=self.get_browser_headers(),
            )
            if page > 1 and response.status_code == 400:
                break  # Tribe answers 400 for a page past the last
            response.raise_for_status()
            data = response.json()

            items = data.get("events") or []
            for item in items:
                if item.get("id") in seen:
                    continue
                seen.add(item.get("id"))
                event = self.parse_item(item)
                if event:
                    events.append(event)

            if not items or page >= int(data.get("total_pages") or 1):
                break

        logger.info(f"Scraped {len(events)} events from the Mount Auburn API")
        return events

    @staticmethod
    def _as_dict(value) -> dict:
        """Tribe returns venue/image as a dict, an empty list, or a list of dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    def _text(self, value) -> str:
        """Plain text from Tribe's HTML-and-entities fields."""
        if not value:
            return ""
        text = BeautifulSoup(str(value), "html.parser").get_text(" ")
        return self.clean_text(html.unescape(text))

    def _start(self, item: dict, key: str) -> Optional[datetime]:
        """A Tribe start/end as naive Eastern wall clock.

        `utc_start_date` is unambiguous; `start_date` is the venue's local time,
        and only safe to read as Eastern when the event says it is.
        """
        utc = item.get(f"utc_{key}")
        try:
            if utc:
                return to_eastern_naive(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc))
            if item.get(key) and item.get("timezone") in (None, "", "America/New_York"):
                return datetime.fromisoformat(item[key])
        except ValueError:
            pass
        return None

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        if not title:
            return None

        all_day = bool(item.get("all_day"))
        if all_day:
            try:
                start = datetime.fromisoformat(str(item.get("start_date"))[:10])
            except ValueError:
                start = None
            end = None
        else:
            start = self._start(item, "start_date")
            end = self._start(item, "end_date")
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable start ({item.get('url')})")
            return None
        if end is not None and end < start:
            end = None

        description = self._text(item.get("excerpt")) or self._text(item.get("description"))
        if len(description) < 20:
            description = f"{title} at Mount Auburn Cemetery"

        venue = self._as_dict(item.get("venue"))
        online = title.lower().startswith(("[virtual]", "virtual:", "[online]"))
        if online:
            venue_name, street, zip_code = "Online", None, None
        else:
            venue_name = self._text(venue.get("venue")) or "Mount Auburn Cemetery"
            street = self._text(venue.get("address")) or "580 Mount Auburn Street"
            zip_code = self._text(venue.get("zip")) or "02138"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=venue_name[:150],
            street_address=street,
            city="Cambridge",
            state="MA",
            zip_code=zip_code,
            category=self._detect_category(title, description),
            cost=self._text(item.get("cost")) or None,
            source_name=self.source_name,
            source_url=item.get("url") or self.source_url,
            image_url=self._as_dict(item.get("image")).get("url"),
        )

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['tour', 'walk', 'hike', 'explore']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['bird', 'nature', 'wildlife', 'garden', 'flora']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['lecture', 'talk', 'presentation', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['art', 'exhibit', 'sculpture', 'gallery']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['workshop', 'class']):
            return EventCategory.COMMUNITY

        return EventCategory.COMMUNITY
