"""Custom scraper for The Dance Complex

Reads the venue's Tribe Events REST API. It previously parsed the site's iCal
feed, which began returning HTTP 200 with an empty body at some point before
2026-09-01 — every variant of the endpoint does. The scraper handled that by
returning zero events, silently, so the source simply vanished from the calendar.

The HTML page is no help either: Tribe v6 renders it a day at a time in
JavaScript, so there is nothing to parse server-side. The REST API is the only
complete, machine-readable view.
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

API_URL = "https://www.dancecomplex.org/wp-json/tribe/events/v1/events"
PER_PAGE = 50
# The venue publishes recurring classes indefinitely — 1,527 of them at the time
# of writing. 60 days matches what the old iCal scraper kept and yields ~420.
WINDOW_DAYS = 60
MAX_PAGES = 20


class DanceComplexScraper(BaseScraper):
    """Custom scraper for The Dance Complex events via the Tribe Events API"""

    def __init__(self):
        super().__init__(
            source_name="The Dance Complex",
            source_url="https://www.dancecomplex.org/events-calendar/",
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
                logger.error(f"Error fetching Dance Complex API page {page}: {e}")
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

        logger.info(f"Scraped {len(events)} events from The Dance Complex")
        return events

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        if not title or len(title) < 3:
            return None

        start = self._parse_datetime(item.get("start_date"))
        if start is None:
            # Never guess a date — a wrong one lands the class on someone
            # else's day. See docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable start date")
            return None

        venue = self._as_dict(item.get("venue"))
        studio = self._text(venue.get("venue"))

        description = self._text(item.get("description") or item.get("excerpt") or "")
        if len(description) < 20:
            description = f"{title} at The Dance Complex" + (f", {studio}" if studio else "")

        image_url = self._as_dict(item.get("image")).get("url")

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=self._parse_datetime(item.get("end_date")),
            source_url=item.get("url") or self.source_url,
            source_name=self.source_name,
            # The API's "venue" is the studio room; the building is the venue
            venue_name="The Dance Complex",
            street_address="536 Massachusetts Ave" + (f", {studio}" if studio else ""),
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=self._categorize(title, description),
            cost=self._text(item.get("cost")) or None,
            image_url=image_url,
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

    @staticmethod
    def _categorize(title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(w in text for w in ("performance", "showcase", "recital", "concert")):
            return EventCategory.THEATER
        if any(w in text for w in ("class", "workshop", "training", "conditioning",
                                   "pilates", "yoga", "barre", "fitness")):
            return EventCategory.SPORTS
        return EventCategory.ARTS_CULTURE
