"""Custom scraper for Harvard-Radcliffe Dramatic Club

The publicity calendar is a plain month grid: a `table.calendar-table` whose
`<td>` cells each hold a `.calendar-day` number and any `.calendar-show-item`
entries for that day. Month and year come from the URL, not the page, so no year
has to be inferred.

Plain HTTP is enough — the previous version drove Selenium and then fetched a
detail page per show, which was slow and, once the markup changed, silently
produced nothing at all.
"""
import logging
import re
from calendar import monthrange
from datetime import datetime
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://my.hrdctheater.org"
MONTHS_AHEAD = 3

VENUE = "Harvard-Radcliffe Dramatic Club"
# HRDC produces across several Harvard theaters; the Loeb is the primary one.
ADDRESS = "64 Brattle St"


class HRDCScraper(BaseScraper):
    """Custom scraper for HRDC theater events"""

    def __init__(self):
        super().__init__(
            source_name=VENUE,
            source_url=f"{BASE}/publicity/calendar/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()

        for year, month in self._months():
            url = f"{BASE}/publicity/calendar/{year}/{month}/"
            try:
                soup = self.parse_html(self.fetch_html(url))
            except Exception as e:
                logger.warning(f"Could not fetch {url}: {e}")
                continue

            last_day = monthrange(year, month)[1]
            for cell in soup.find_all("td"):
                day = self._day_number(cell)
                # Grid cells spill into the neighbouring months; those days are
                # covered by their own page, so skip anything out of range.
                if day is None or not 1 <= day <= last_day:
                    continue
                for item in cell.find_all(class_="calendar-show-item"):
                    event = self._parse_item(item, year, month, day)
                    if event is None:
                        continue
                    key = (event.title, event.start_datetime)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _months(self) -> List[tuple]:
        now = datetime.now()
        out = []
        for offset in range(MONTHS_AHEAD):
            month = now.month + offset
            out.append((now.year + (month - 1) // 12, (month - 1) % 12 + 1))
        return out

    def _day_number(self, cell) -> Optional[int]:
        node = cell.find(class_="calendar-day")
        if not node:
            return None
        text = self.clean_text(node.get_text())
        return int(text) if text.isdigit() else None

    def _parse_item(self, item, year: int, month: int, day: int) -> Optional[EventCreate]:
        title_el = item.find(class_="calendar-show-title")
        if not title_el:
            return None
        title = self.clean_text(title_el.get_text())
        if len(title) < 3:
            return None

        time_el = item.find(class_="calendar-show-time")
        start = self._parse_start(year, month, day,
                                  self.clean_text(time_el.get_text()) if time_el else "")
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date for {year}-{month:02d}-{day:02d}")
            return None

        link = title_el.find("a", href=True)
        url = link["href"] if link else self.source_url
        if url.startswith("/"):
            url = f"{BASE}{url}"

        # The info icon's tooltip carries the venue/notes for the show
        note = item.find("i", attrs={"data-original-title": True})
        detail = self.clean_text(note["data-original-title"]) if note else ""

        description = f"{title} — {VENUE}."
        if detail:
            description += f" {detail}"

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
            category=EventCategory.THEATER,
        )

    @staticmethod
    def _parse_start(year: int, month: int, day: int, time_text: str) -> Optional[datetime]:
        """Date from the calendar cell, time from "9 PM" style text."""
        try:
            date = datetime(year, month, day)
        except ValueError:
            return None
        if not time_text:
            return date
        try:
            parsed = date_parser.parse(time_text, fuzzy=True)
        except (ValueError, OverflowError):
            return date
        return date.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
