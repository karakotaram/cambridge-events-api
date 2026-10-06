"""Scraper for The Middle East Restaurant & Nightclub (and Sonia, its sister room).

mideastclub.com is WordPress with the TicketWeb plugin, rendered on the server:
20 shows per page at `/page/{n}/`, in date order, until a page says "Currently
no scheduled events". Plain requests read it; no browser is needed.

Each row prints the month and day ("10.8") and the weekday ("Thu") but no year,
so the year is the one whose calendar puts that date on that weekday. A row
whose weekday matches no nearby year is skipped, never guessed.
"""
import logging
import re
import time
from datetime import date, datetime
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Rooms on the Mass Ave block share one address; Sonia is around the corner.
MIDDLE_EAST_ADDRESS = ("472-480 Massachusetts Ave", "02139")
ROOM_ADDRESSES = {
    "sonia": ("10 Brookline St", "02139"),
}

# ~230 shows today is 12 pages. The cap only stops a loop on a site that
# starts repeating its last page forever.
MAX_PAGES = 40
PAGE_DELAY_S = 1.5
RETRY_DELAY_S = 10

# Says who is asking. Not a browser's, so it contradicts nothing.
USER_AGENT = "cambridgecalendar-scraper/1.0 (+https://cambridgecalendar.com)"

CANCELLED = re.compile(r"\bcancel+ed\b", re.I)


def year_for_weekday(month: int, day: int, weekday: int, reference: date) -> Optional[int]:
    """The year near `reference` in which month/day falls on `weekday`.

    Adjacent years put a date on different weekdays, so at most one of the
    three candidates matches. None means the printed weekday contradicts every
    nearby year, and the row should be skipped rather than guessed.
    """
    for year in (reference.year, reference.year + 1, reference.year - 1):
        try:
            if date(year, month, day).weekday() == weekday:
                return year
        except ValueError:          # Feb 29 in a common year
            continue
    return None


class MideastClubScraper(BaseScraper):
    """The Middle East's TicketWeb listing, every page of it."""

    def __init__(self, today: Optional[date] = None):
        super().__init__(
            source_name="The Middle East",
            source_url="https://mideastclub.com/",
            use_selenium=False,
        )
        # Only the year is inferred from it, and only through the weekday match.
        self.today = today

    def page_url(self, n: int) -> str:
        return self.source_url if n == 1 else f"{self.source_url}page/{n}/"

    def fetch_page(self, url: str) -> str:
        """Plain requests: the site needs no browser.

        The host (WP Engine behind Cloudflare) refuses requests' default
        user-agent on pages it has not cached - page 11 answered 403 to
        "python-requests/2.31.0" and 200 to curl. A user-agent that names this
        scraper is honest and accepted. Pages are paced, and a refusal is
        retried before the run gives up.
        """
        for attempt in range(3):
            time.sleep(PAGE_DELAY_S if attempt == 0 else RETRY_DELAY_S * attempt)
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if response.status_code not in (403, 429, 502, 503):
                break
            logger.warning(f"{self.source_name}: {response.status_code} for {url} (attempt {attempt + 1}/3)")
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        repeats = 0
        reference = self.today or date.today()
        for n in range(1, MAX_PAGES + 1):
            rows = self.rows(self.parse_html(self.fetch_page(self.page_url(n))))
            if not rows:
                break               # "Currently no scheduled events": past the last page
            fresh = [r for r in rows if self.clean_text(r.get_text(" ")) not in seen]
            if not fresh:
                # The site's cache has served page 1 for a later page; on
                # 2026-10-06 /page/12/ did so for a few minutes, then served
                # its own shows. Skip it, and stop if it keeps happening.
                repeats += 1
                logger.warning(f"{self.source_name}: page {n} repeats earlier pages")
                if repeats >= 2:
                    break
                continue
            repeats = 0
            seen.update(self.clean_text(r.get_text(" ")) for r in fresh)
            events.extend(self.parse_rows(fresh, reference))
        else:
            logger.warning(f"{self.source_name}: stopped at {MAX_PAGES} pages; the listing may be longer")
        return events

    @staticmethod
    def rows(soup: BeautifulSoup) -> list:
        listing = soup.find("div", class_="tw-plugin-upcoming-event-list")
        return listing.find_all("div", class_="tw-section") if listing else []

    def parse_rows(self, rows: Iterable, reference: date) -> List[EventCreate]:
        events = []
        for row in rows:
            try:
                event = self.parse_row(row, reference)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse a row: {e}")
                continue
            if event:
                events.append(event)
        return events

    def _text(self, row, cls: str) -> str:
        node = row.find(class_=cls)
        return self.clean_text(node.get_text(" ")) if node else ""

    def parse_row(self, row, reference: date) -> Optional[EventCreate]:
        name = row.find("div", class_="tw-name")
        link = name.find("a") if name else None
        title = self.clean_text(link.get_text()) if link else ""
        if len(title) < 3:
            return None

        if CANCELLED.search(title):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        full_text = self.clean_text(row.get_text(" ")).lower()
        if any(k in full_text for k in ("private party", "private event", "closed to public",
                                        "invite only", "members only", "by invitation")):
            return None

        start = self.parse_start(row, reference)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({self.source_url})")
            return None

        room = re.sub(r"^@\s*", "", self._text(row, "tw-venue-name"))
        venue_name = room or "The Middle East"
        street, zip_code = ROOM_ADDRESSES.get(room.lower(), MIDDLE_EAST_ADDRESS)

        presenter = self._text(row, "tw-prefix").rstrip(":")
        age = self._text(row, "tw-age-restriction")
        parts = [f"{title} at {venue_name}, Cambridge, MA."]
        if presenter:
            parts.append(f"{presenter}.")
        if age:
            parts.append(f"{age}.")

        price = self._text(row, "tw-price")
        cost = price if "$" in price else None

        img = row.find("img")
        href = link.get("href") or self.source_url

        return EventCreate(
            title=title[:200],
            description=" ".join(parts)[:2000],
            start_datetime=start,
            source_url=href,
            source_name=self.source_name,
            venue_name=venue_name[:150],
            street_address=street,
            city="Cambridge",
            state="MA",
            zip_code=zip_code,
            category=EventCategory.MUSIC,
            age_restrictions=age or None,
            cost=cost,
            image_url=img.get("src") if img and img.get("src") else None,
        )

    def parse_start(self, row, reference: date) -> Optional[datetime]:
        """Month.day from the row, year from the printed weekday, time from "Show:"."""
        md = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", self._text(row, "tw-event-date"))
        dow = WEEKDAYS.get(self._text(row, "tw-day-of-week")[:3].lower())
        tm = re.search(r"(\d{1,2}):(\d{2})\s*([AP]M)", self._text(row, "tw-event-time"), re.I)
        if not (md and dow is not None and tm):
            return None

        month, day = int(md.group(1)), int(md.group(2))
        year = year_for_weekday(month, day, dow, reference)
        if year is None:
            return None

        hour = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "PM" else 0)
        return datetime(year, month, day, hour, int(tm.group(2)))
