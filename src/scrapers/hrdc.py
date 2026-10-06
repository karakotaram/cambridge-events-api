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
from typing import List, Optional, Tuple

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://my.hrdctheater.org"
MONTHS_AHEAD = 3

VENUE = "Harvard-Radcliffe Dramatic Club"
# HRDC produces across several Harvard theaters; the Loeb is the primary one.
ADDRESS = "64 Brattle St"

# The calendar also carries the club's own deadlines ("Agassiz Theater Apps
# Due", 11:59 PM). A deadline is not something a reader can attend.
DEADLINE = re.compile(
    r"\bdeadline\b"
    r"|\b(?:apps?|applications?|submissions?|proposals?|pitches|forms?|materials?|registrations?)\b.*\bdue\b",
    re.I)

_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$", re.I)


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
        if DEADLINE.search(title):
            logger.info(f"Skipping '{title}' - a deadline, not an event")
            return None

        time_el = item.find(class_="calendar-show-time")
        time_text = self.clean_text(time_el.get_text()) if time_el else ""
        start, all_day = self._parse_start(year, month, day, time_text)
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - unreadable time {time_text!r} on {year}-{month:02d}-{day:02d}")
            return None

        link = title_el.find("a", href=True)
        url = link["href"] if link else self.source_url
        if url.startswith("/"):
            url = f"{BASE}{url}"

        # The info icon's tooltip carries the venue/notes for the show. The
        # served markup has it in `title`; Bootstrap moves it to
        # `data-original-title` only once the page's script has run.
        note = item.find("i", attrs={"title": True}) or item.find("i", attrs={"data-original-title": True})
        detail = self.clean_text(note.get("title") or note.get("data-original-title") or "") if note else ""

        description = f"{title} — {VENUE}."
        if detail:
            description += f" {detail}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=all_day,
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
    def _parse_start(year: int, month: int, day: int, time_text: str) -> Tuple[Optional[datetime], bool]:
        """(start, all_day): the date from the calendar cell, the time from "9 PM".

        No time text means the cell lists the item for the day without a time:
        an all-day listing. Time text that cannot be read gives (None, False)
        and the item is skipped. Both used to become midnight.
        """
        try:
            date = datetime(year, month, day)
        except ValueError:
            return None, False
        text = " ".join((time_text or "").split())
        if not text:
            return date, True
        if text.lower() == "noon":
            return date.replace(hour=12), False
        m = _CLOCK.match(text)
        if not m:
            return None, False
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (1 <= hour <= 12 and minute < 60):
            return None, False
        hour = hour % 12 + (12 if m.group(3).lower() == "p" else 0)
        return date.replace(hour=hour, minute=minute), False
