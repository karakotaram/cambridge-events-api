"""Scraper for Sanders Theatre, via the Harvard Box Office

The old source, calendar.college.harvard.edu/sanders_theatre, now 404s. Sanders
listings live on the Harvard Box Office, an AudienceView storefront that renders
its "Upcoming Events" widget client-side.

Two things that make this awkward, both handled below:

  - The page never reaches `networkidle` (something keeps a connection open), so
    waiting for it times out. Wait for the widget's own markup instead.
  - The box office sells for several Harvard venues, so listings are filtered to
    Sanders Theatre. Nineteen of twenty current listings are Sanders; the rest
    are at places like the Bright-Landry Hockey Center and are not ours.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://www.boxoffice.harvard.edu/Online"
EVENTS_URL = f"{BASE}/default.asp?BOparam::WScontent::loadArticle::permalink=events"

VENUE = "Sanders Theatre"
ADDRESS = "45 Quincy St"

# "Sunday, September 27, 2026 - 3:00pm", or a run: "September 11-12, 2026 - 7:00pm"
DATE_LINE = re.compile(
    r"([A-Z][a-z]+day,\s+)?"                     # optional weekday
    r"([A-Z][a-z]+)\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,\s*(\d{4})"   # month day[-day], year
    r"(?:\s*[-–]\s*(\d{1,2}:\d{2}\s*[ap]m))?",   # optional time
    re.I)


class SandersTheatreScraper(BasePlaywrightScraper):
    """Scraper for Sanders Theatre events"""

    def __init__(self):
        super().__init__(source_name=VENUE, source_url=EVENTS_URL)

    def scrape_events(self) -> List[EventCreate]:
        try:
            # domcontentloaded, not networkidle — this page never goes idle.
            self.goto(self.source_url, timeout=60000)
            self.wait_for_stable_count(".item-description", timeout=30000)
            soup = self.get_soup()
        except Exception as e:
            logger.error(f"Could not load Harvard Box Office events: {e}")
            return []

        events: List[EventCreate] = []
        seen = set()
        for block in soup.find_all(class_="item-description"):
            event = self._parse_block(block)
            if event is None:
                continue
            key = (event.title, event.start_datetime)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _parse_block(self, block) -> Optional[EventCreate]:
        link = block.find("a", class_="more-info", href=True)
        name = block.find(class_="item-name")
        title = self.clean_text((link or name).get_text()) if (link or name) else ""
        if len(title) < 3:
            return None

        teaser = block.find(class_="item-teaser")
        if not teaser:
            return None
        # The teaser is <br>-separated: date/time, venue, then the blurb.
        lines = [self.clean_text(part) for part in teaser.stripped_strings]
        lines = [line for line in lines if line]
        if len(lines) < 2:
            return None

        venue_line = lines[1]
        if VENUE.lower() not in venue_line.lower():
            # A different Harvard venue — not this source's event.
            return None

        start = self._parse_start(lines[0])
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date ({lines[0]!r})")
            return None

        description = " ".join(lines[2:]).strip()
        if len(description) < 20:
            description = f"{title} at {VENUE}, Harvard University."

        url = link["href"] if link else self.source_url
        if not url.startswith("http"):
            url = f"{BASE}/{url.lstrip('/')}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            source_url=url,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Cambridge",
            state="MA",
            zip_code="02138",
            category=self._categorize(title, description),
        )

    @staticmethod
    def _parse_start(text: str) -> Optional[datetime]:
        """Parse the teaser's first line.

        A multi-day run ("September 11-12, 2026") becomes its first performance;
        the others are separate listings on the box office anyway.
        """
        match = DATE_LINE.search(text or "")
        if not match:
            return None
        _, month, day, year, time_text = match.groups()
        try:
            parsed = date_parser.parse(f"{month} {day} {year} {time_text or ''}".strip())
        except (ValueError, OverflowError):
            return None
        return parsed.replace(second=0, microsecond=0)

    @staticmethod
    def _categorize(title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(w in text for w in ("orchestra", "symphony", "chamber", "concert",
                                   "music", "opera", "choir", "recital")):
            return EventCategory.MUSIC
        if any(w in text for w in ("lecture", "presents vibe", "talk", "conversation")):
            return EventCategory.LECTURES
        return EventCategory.MUSIC
