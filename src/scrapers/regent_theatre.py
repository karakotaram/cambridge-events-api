"""Scraper for the Regent Theatre in Arlington

The schedule runs on EventON, which loads its listings over AJAX — the initial
HTML contains only empty `.eventon_events_list` shells behind loading bars. The
previous scraper parsed that empty page and returned nothing, silently.

Waiting for the network to settle gets the rendered list, and each event then
carries full schema.org microdata (`itemprop="startDate"` and friends), which is
a far better source than the visible text: the human-readable date is written
"thu03sep8:00 pm" with no year.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

VENUE = "Regent Theatre"
ADDRESS = "7 Medford St"


class RegentTheatreScraper(BasePlaywrightScraper):
    """Scraper for Regent Theatre events"""

    def __init__(self):
        super().__init__(
            source_name=VENUE,
            # /schedule redirects here; going straight there avoids the hop
            source_url="https://regenttheatre.com/schedule/list/",
        )

    def scrape_events(self) -> List[EventCreate]:
        try:
            # EventON fetches its listings after load, so domcontentloaded is
            # far too early — the page looks like it has no events at all.
            self.goto(self.source_url, wait_until="networkidle", timeout=60000)
            self.wait_for_stable_count(".eventon_list_event", timeout=25000)
            soup = self.get_soup()
        except Exception as e:
            logger.error(f"Could not load Regent Theatre schedule: {e}")
            return []

        events: List[EventCreate] = []
        seen = set()
        for node in soup.find_all(class_="eventon_list_event"):
            event = self._parse_event(node)
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _parse_event(self, node) -> Optional[EventCreate]:
        title_el = node.find(class_="evcal_event_title")
        title = self.clean_text(title_el.get_text()) if title_el else ""
        if len(title) < 3:
            return None

        start = self._microdata_datetime(node, "startDate") or self._from_data_time(node)
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable start date")
            return None

        link = node.find("a", href=True)
        image = node.find(attrs={"itemprop": "image"})

        return EventCreate(
            title=title[:200],
            description=f"{title} at the {VENUE} in Arlington."[:2000],
            start_datetime=start,
            end_datetime=self._microdata_datetime(node, "endDate"),
            source_url=link["href"] if link else self.source_url,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Arlington",
            state="MA",
            zip_code="02474",
            category=self._categorize(title),
            image_url=image.get("content") if image else None,
        )

    @staticmethod
    def _microdata_datetime(node, prop: str) -> Optional[datetime]:
        """Read itemprop="startDate" content="2026-9-3T20:00-4:00".

        Note the unpadded month and day — dateutil handles it, `strptime` would
        not. The offset is stripped to naive Eastern by the Event model.
        """
        el = node.find(attrs={"itemprop": prop})
        value = el.get("content") if el else None
        if not value:
            return None
        try:
            return date_parser.parse(value)
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _from_data_time(node) -> Optional[datetime]:
        """Fallback: data-time="1788480000-1788494340" is a start-end epoch pair."""
        raw = (node.get("data-time") or "").split("-")[0]
        if not raw.isdigit():
            return None
        try:
            return datetime.fromtimestamp(int(raw)).replace(second=0, microsecond=0)
        except (ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _categorize(title: str) -> EventCategory:
        text = title.lower()
        if any(w in text for w in ("comedy", "comedian", "stand-up", "standup", "improv")):
            return EventCategory.THEATER
        if any(w in text for w in ("film", "movie", "screening", "cinema")):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("tribute", "band", "concert", "live", "music", "orchestra")):
            return EventCategory.MUSIC
        return EventCategory.MUSIC
