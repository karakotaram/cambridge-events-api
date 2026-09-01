"""Scraper for the Museum of Science special events

The museum's `/events` page lists a handful of dated special events — the rest of
its programme is daily exhibits and shows, which are not calendar entries. Five
listings is the real number, not a truncated one.

Dates read as "Saturday, September 26, 2026 | 10:00 am – 4:00 pm", and sometimes
"Sunday, September 27 | 6:00 – 10:00 pm" with the year left off. A missing year
is inferred forward from today — never backward — because the page only ever
advertises upcoming events.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://www.mos.org"
VENUE = "Museum of Science"
ADDRESS = "1 Science Park"

# "Saturday, September 26, 2026 | 10:00 am – 4:00 pm" -> date part, time part
DATE_TIME_SPLIT = re.compile(r"\s*\|\s*")
# Ranges use an en dash more often than a hyphen
TIME_RANGE_SPLIT = re.compile(r"\s*(?:–|—|-|to)\s*")


class MuseumOfScienceScraper(BasePlaywrightScraper):
    """Scraper for Museum of Science events"""

    def __init__(self):
        super().__init__(source_name=VENUE, source_url=f"{BASE}/events")

    def scrape_events(self) -> List[EventCreate]:
        try:
            self.goto(self.source_url)
            self.wait_for_stable_count(".listing-item", timeout=25000)
            soup = self.get_soup()
        except Exception as e:
            logger.error(f"Could not load Museum of Science events: {e}")
            return []

        events: List[EventCreate] = []
        seen = set()
        for item in soup.find_all(class_="listing-item"):
            event = self._parse_item(item)
            if event is None:
                continue
            if event.source_url in seen:
                continue
            seen.add(event.source_url)
            events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _parse_item(self, item) -> Optional[EventCreate]:
        link = item.find("a", class_="listing-item__image", href=True) or item.find("a", href=True)
        if not link:
            return None
        path = link["href"]
        if "/events/" not in path:
            return None

        title = ""
        title_el = item.find(class_=re.compile(r"listing-item__(title|heading)"))
        if title_el:
            title = self.clean_text(title_el.get_text())
        if not title:
            # Fall back to the anchor whose text is not the image alt
            for anchor in item.find_all("a", href=True):
                text = self.clean_text(anchor.get_text())
                if text and text.lower() != "image":
                    title = text
                    break
        if len(title) < 3:
            return None

        date_el = item.find(class_="listing-item__date")
        start = self._parse_start(self.clean_text(date_el.get_text()) if date_el else "")
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date "
                           f"({self.clean_text(date_el.get_text()) if date_el else None!r})")
            return None

        body = item.find(class_=re.compile(r"listing-item__(summary|description|content-body)"))
        description = self.clean_text(body.get_text()) if body else ""
        if len(description) < 20:
            description = f"{title} at the {VENUE}, Science Park, Boston."

        image = item.find("img", src=True)
        image_url = image["src"] if image else None
        if image_url and image_url.startswith("/"):
            image_url = f"{BASE}{image_url}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            source_url=f"{BASE}{path}" if path.startswith("/") else path,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Boston",
            state="MA",
            zip_code="02114",
            category=EventCategory.ARTS_CULTURE,
            image_url=image_url,
        )

    @staticmethod
    def _parse_start(text: str) -> Optional[datetime]:
        if not text:
            return None
        parts = DATE_TIME_SPLIT.split(text, maxsplit=1)
        date_part = parts[0].strip()
        time_part = TIME_RANGE_SPLIT.split(parts[1].strip())[0] if len(parts) > 1 else ""

        # An end-of-range time like "4:00 pm" carries the meridiem the start may
        # be missing ("6:00 – 10:00 pm"); borrow it when the start has none.
        if time_part and not re.search(r"[ap]\.?m", time_part, re.I) and len(parts) > 1:
            meridiem = re.search(r"([ap]\.?m)", parts[1], re.I)
            if meridiem:
                time_part = f"{time_part} {meridiem.group(1)}"

        now = datetime.now()
        try:
            parsed = date_parser.parse(f"{date_part} {time_part}".strip(),
                                       default=now.replace(hour=0, minute=0, second=0, microsecond=0))
        except (ValueError, OverflowError):
            return None

        # The page only advertises upcoming events, so a date that lands in the
        # past means the year was omitted — roll forward, never backward.
        if parsed < now and not re.search(r"\d{4}", date_part):
            try:
                parsed = parsed.replace(year=parsed.year + 1)
            except ValueError:      # 29 Feb
                return None
        return parsed.replace(second=0, microsecond=0)
