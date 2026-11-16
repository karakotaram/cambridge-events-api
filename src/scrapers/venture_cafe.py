"""Venture Café Cambridge — the weekly Thursday Gathering at CIC, One Broadway.

The programme page lists the gatherings it has scheduled, each as a row
linking to its page on the GatherUs community site:

    <a href="https://community.venturecafecambridge.org/events/1349" class="vc-event-row">
      <div class="vc-event-date"><div class="wd">Thursday,</div><div class="d">19</div>
                                 <div class="m">NOV</div></div>
      <h3>Healthtech Adoption, Hiring Realities, and Open Networking</h3>
      <div class="vc-event-tags"><span>4:30 PM</span> ...

Only the rows in the "upcoming" pane are read — the page lists a gathering only
when one is scheduled (Thanksgiving week has none), and nothing is inferred
from "Every Thursday".

**Year.** A row prints weekday, day and month but no year. The year is the one
near today in which that date falls on that weekday, as art.py does; adjacent
years put a date on different weekdays, so at most one matches. A row whose
weekday matches none of them is skipped, and so is one that resolves to a
date outside the coming year — a mistyped "Friday, 19 NOV" would otherwise
land on 2027. Today is only the reference for that match, never a date.

**Where.** The programme page names its venue ("Venture Cafe Cambridge · CIC
Cambridge"); GatherUs gives the address, One Broadway, 5th floor. The page
must still say CIC Cambridge, or the scrape fails rather than send readers to
a room the gatherings have left. Rows must link to the Cambridge, MA community
site — Venture Café also runs a "Cambridge Connect" in England.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.art import WEEKDAYS, year_for_weekday
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

COMMUNITY_HOST = "community.venturecafecambridge.org"
VENUE_MARK = "CIC Cambridge"

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([AP])M$", re.I)
# An "upcoming" row resolves to a date in this window around today
WINDOW_BEFORE_DAYS = 7
WINDOW_AFTER_DAYS = 300


class VentureCafeScraper(BaseFeedScraper):
    HOME = Place("Venture Café at CIC Cambridge", "One Broadway, 5th floor", "Cambridge", "02142")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Venture Café Cambridge",
                         source_url="https://venturecafecambridge.org/programs/thursday-gathering/")
        # Reference for the weekday-to-year match only; injectable for tests.
        self.today = today

    def scrape_events(self) -> List[EventCreate]:
        events = self.parse_page(self.fetch_html(self.source_url))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_page(self, html: str) -> List[EventCreate]:
        soup = BeautifulSoup(html, "html.parser")
        meta = soup.select_one(".vc-entry-meta")
        if meta is None or VENUE_MARK not in meta.get_text(" "):
            raise ValueError(f"{self.source_name}: the programme page no longer names {VENUE_MARK}; "
                             f"check where the gatherings are before publishing them there")
        pane = soup.select_one('[data-vc-events-pane="upcoming"]')
        if pane is None:
            raise ValueError(f"{self.source_name}: no upcoming-events pane on {self.source_url}")
        lead = soup.select_one(".vc-lead")
        about = self.clean_text(lead.get_text(" ")) if lead else ""
        reference = self.today or date.today()
        return [e for e in (self.parse_row(row, reference, about) for row in pane.select("a.vc-event-row")) if e]

    def parse_row(self, row, reference: date, about: str = "") -> Optional[EventCreate]:
        title = self.clean_text(row.h3.get_text(" ")) if row.h3 else ""
        url = row.get("href") or ""
        if not self.is_event(title):
            return None
        if urlparse(url).hostname != COMMUNITY_HOST:
            logger.warning(f"Skipping '{title}' - links to {url}, not the Cambridge, MA community site")
            return None

        start = self.read_start(row, reference)
        if start is None:
            logger.warning(f"Skipping '{title}' - unreadable date or time ({url})")
            return None

        tags = [self.clean_text(t.get_text(" ")) for t in row.select(".vc-tagpill")]
        image = row.select_one(".vc-event-thumb img")
        where = self.HOME
        description = f"Venture Café Thursday Gathering: {title}. {about}".strip()
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            tags=tags,
            source_name=self.source_name,
            source_url=url,
            image_url=(image.get("src") if image else None) or None,
        )

    def read_start(self, row, reference: date) -> Optional[datetime]:
        """'Thursday,' + '19' + 'NOV' + '4:30 PM' -> the year whose Nov 19 is a Thursday."""
        def part(cls):
            node = row.select_one(f".vc-event-date .{cls}")
            return self.clean_text(node.get_text(" ")).strip(",").lower() if node else ""

        weekday = WEEKDAYS.get(part("wd"))
        month = MONTHS.get(part("m")[:3])
        day = part("d")
        clock = next((TIME.match(self.clean_text(s.get_text(" ")))
                      for s in row.select(".vc-event-tags span") if TIME.match(self.clean_text(s.get_text(" ")))),
                     None)
        if weekday is None or month is None or not day.isdigit() or clock is None:
            return None
        year = year_for_weekday(month, int(day), weekday, reference)
        if year is None:
            return None
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3).upper() == "P" else 0)
        try:
            start = datetime(year, month, int(day), hour, int(clock.group(2)))
        except ValueError:
            return None
        # A mistyped weekday can match the wrong year ("Friday, 19 NOV" is
        # 2027). The page lists gatherings weeks ahead, not a year.
        if not timedelta(days=-WINDOW_BEFORE_DAYS) <= start.date() - reference <= timedelta(days=WINDOW_AFTER_DAYS):
            logger.warning(f"{self.source_name}: {part('wd')} {day} {part('m')} resolves to {start:%Y-%m-%d}, "
                           f"not an upcoming date")
            return None
        return start
