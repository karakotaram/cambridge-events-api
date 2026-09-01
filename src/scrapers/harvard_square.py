"""Scraper for Harvard Square Business Association events

An aggregator: it lists what other Harvard Square venues are doing, so it runs
last (see `KIND_ORDER` in src/sources.py) and loses deduplication to the
original sources.

Reads the site's Tribe Events REST API. It previously walked
`/events/YYYY-MM-DD/` one day at a time for 30 days with a 0.5s pause between
each — those per-day URLs stopped returning listings, so the scraper spent 127
seconds per run to produce nothing, without erroring. The API returns the same
window in a couple of seconds.
"""
import html
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

API_URL = "https://harvardsquare.com/wp-json/tribe/events/v1/events"
PER_PAGE = 50
WINDOW_DAYS = 60
MAX_PAGES = 20

# Tribe category -> our schema. Anything unlisted falls through to keyword matching.
CATEGORY_MAP = {
    "author events": EventCategory.LECTURES,
    "lectures": EventCategory.LECTURES,
    "talks": EventCategory.LECTURES,
    "music": EventCategory.MUSIC,
    "live music": EventCategory.MUSIC,
    "concerts": EventCategory.MUSIC,
    "theater": EventCategory.THEATER,
    "theatre": EventCategory.THEATER,
    "film": EventCategory.ARTS_CULTURE,
    "art": EventCategory.ARTS_CULTURE,
    "arts": EventCategory.ARTS_CULTURE,
    "museums": EventCategory.ARTS_CULTURE,
    "food": EventCategory.FOOD_DRINK,
    "dining": EventCategory.FOOD_DRINK,
    "food & drink": EventCategory.FOOD_DRINK,
    "sports": EventCategory.SPORTS,
    "fitness": EventCategory.SPORTS,
    "community": EventCategory.COMMUNITY,
}


class HarvardSquareScraper(BaseScraper):
    """Scraper for Harvard Square Business Association events"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Square",
            source_url="https://harvardsquare.com/events/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        end_date = (datetime.now() + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

        for page in range(1, MAX_PAGES + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={"per_page": PER_PAGE, "start_date": "now",
                            "end_date": end_date, "page": page},
                    timeout=30,
                    headers=self.get_browser_headers(),
                )
                if response.status_code == 400:
                    break          # Tribe returns 400 past the last page
                response.raise_for_status()
                payload = response.json()
            except Exception as e:
                logger.error(f"Error fetching Harvard Square API page {page}: {e}")
                break

            batch = payload.get("events", [])
            if not batch:
                break

            for item in batch:
                event = self._parse_event(item)
                if event is None:
                    continue
                key = (event.source_url, event.start_datetime)
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)

            if page >= payload.get("total_pages", 1):
                break

        logger.info(f"Scraped {len(events)} events from Harvard Square")
        return events

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        if not title or len(title) < 3:
            return None

        start = self._parse_datetime(item.get("start_date"))
        if start is None:
            # Never guess — a wrong date lands the event on someone else's day.
            logger.warning(f"Skipping '{title}' - no parseable start date")
            return None

        venue = self._as_dict(item.get("venue"))
        venue_name = self._text(venue.get("venue")) or "Harvard Square"

        description = self._text(item.get("excerpt") or item.get("description") or "")
        if len(description) < 20:
            description = f"{title} at {venue_name} in Harvard Square, Cambridge."

        image = self._as_dict(item.get("image"))
        cost = self._text(item.get("cost"))

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=self._parse_datetime(item.get("end_date")),
            source_url=item.get("url") or self.source_url,
            source_name=self.source_name,
            venue_name=venue_name[:200],
            street_address=self._text(venue.get("address"))[:200] or None,
            city=self._text(venue.get("city")) or "Cambridge",
            state="MA",
            zip_code=self._text(venue.get("zip")) or None,
            category=self._categorize(item, title, description),
            cost=cost or None,
            image_url=image.get("url"),
            website_url=self._text(venue.get("website")) or None,
        )

    @staticmethod
    def _as_dict(value) -> dict:
        """Tribe returns venue/image as a dict, an empty list, or a list of dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return date_parser.parse(str(value))
        except (ValueError, OverflowError, TypeError):
            return None

    @staticmethod
    def _text(value) -> str:
        """Strip HTML and decode entities — the API returns both."""
        if not value:
            return ""
        text = re.sub(r"<[^>]+>", " ", str(value))
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    def _categorize(self, item: dict, title: str, description: str) -> EventCategory:
        for category in item.get("categories") or []:
            mapped = CATEGORY_MAP.get(self._text(category.get("name")).lower())
            if mapped:
                return mapped

        text = f"{title} {description}".lower()
        if any(w in text for w in ("concert", "live music", "band", "jazz", "dj")):
            return EventCategory.MUSIC
        if any(w in text for w in ("author", "reading", "book talk", "lecture", "panel")):
            return EventCategory.LECTURES
        if any(w in text for w in ("theater", "theatre", "play", "improv", "comedy")):
            return EventCategory.THEATER
        if any(w in text for w in ("film", "screening", "exhibit", "gallery", "museum")):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("tasting", "brunch", "dinner", "menu", "restaurant")):
            return EventCategory.FOOD_DRINK
        return EventCategory.COMMUNITY
