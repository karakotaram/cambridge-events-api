"""Scraper for First Parish in Cambridge (Unitarian Universalist)

Reads the Squarespace events collection as JSON (`?format=json`), which splits
its own listings into `upcoming` and `past`. That is more reliable than parsing
the rendered page, where past and upcoming events look identical apart from an
`eventlist-event--past` class.

Note for whoever finds this source contributing nothing: as of 2026-09-01 the
venue genuinely has **no upcoming events published** — its own JSON reports
`upcoming: 0`, with the most recent listing on 2026-08-30. That is a quiet
venue, not a broken scraper. `cal scrape "First Parish in Cambridge"` will show
zero events and clean invariants, which is the difference.
"""
import html
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

JSON_URL = "https://www.firstparishcambridge.org/events?format=json"

VENUE = "First Parish in Cambridge"
ADDRESS = "3 Church St"

CATEGORY_MAP = {
    "worship": EventCategory.COMMUNITY,
    "music": EventCategory.MUSIC,
    "concert": EventCategory.MUSIC,
    "social justice": EventCategory.COMMUNITY,
    "community": EventCategory.COMMUNITY,
    "religious education": EventCategory.LECTURES,
    "lecture": EventCategory.LECTURES,
    "arts": EventCategory.ARTS_CULTURE,
}


class FirstParishScraper(BaseScraper):
    """Scraper for First Parish in Cambridge events"""

    def __init__(self):
        super().__init__(
            source_name=VENUE,
            source_url="https://www.firstparishcambridge.org/events/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        try:
            payload = json.loads(self.fetch_html(JSON_URL))
        except Exception as e:
            logger.error(f"Could not read First Parish events JSON: {e}")
            return []

        # Squarespace separates these for us; `items` is the fallback shape for
        # collections that do not split them.
        upcoming = payload.get("upcoming") or payload.get("items") or []
        if not upcoming:
            logger.info("First Parish has no upcoming events published")
            return []

        events = []
        for item in upcoming:
            event = self._parse_event(item)
            if event is not None:
                events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        if not title or len(title) < 3:
            return None

        start = self._from_epoch_ms(item.get("startDate"))
        if start is None:
            # Never guess a date — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable start date")
            return None

        description = self._text(item.get("excerpt") or item.get("body") or "")
        if len(description) < 20:
            description = f"{title} at {VENUE}, Harvard Square."

        url = item.get("fullUrl") or ""
        if url.startswith("/"):
            url = f"https://www.firstparishcambridge.org{url}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=self._from_epoch_ms(item.get("endDate")),
            source_url=url or self.source_url,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Cambridge",
            state="MA",
            zip_code="02138",
            category=self._categorize(item.get("categories") or [], title, description),
            image_url=item.get("assetUrl") or None,
        )

    @staticmethod
    def _from_epoch_ms(value) -> Optional[datetime]:
        """Squarespace timestamps are epoch milliseconds in the site's timezone."""
        if not value:
            return None
        try:
            # Sub-second precision is an artefact of the format, not a published
            # time; EventValidator rejects start times carrying seconds.
            return datetime.fromtimestamp(int(value) / 1000).replace(second=0, microsecond=0)
        except (ValueError, TypeError, OSError, OverflowError):
            return None

    @staticmethod
    def _text(value) -> str:
        if not value:
            return ""
        text = re.sub(r"<[^>]+>", " ", str(value))
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    def _categorize(self, categories: list, title: str, description: str) -> EventCategory:
        for name in categories:
            mapped = CATEGORY_MAP.get(self._text(name).lower())
            if mapped:
                return mapped
        text = f"{title} {description}".lower()
        if any(w in text for w in ("concert", "music", "choir", "organ", "recital")):
            return EventCategory.MUSIC
        if any(w in text for w in ("lecture", "talk", "forum", "discussion", "class")):
            return EventCategory.LECTURES
        return EventCategory.COMMUNITY
