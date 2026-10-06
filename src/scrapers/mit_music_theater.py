"""MIT Music and MIT Theater Arts, read from their WordPress calendar APIs

The department this source was written for (mta.mit.edu) split into
music.mit.edu and theater.mit.edu; mta.mit.edu/events now redirects to a
landing page, and this scraper never produced an event from it. Each new site
publishes its whole calendar, past and upcoming, at

    https://{music,theater}.mit.edu/wp-json/calendar/v1/events

as a JSON list. The fields used:

    title           HTML-escaped ("Concert Choir &amp; Symphony Orchestra")
    link            the event's own page
    date            "2026-10-09"           local date
    time            "20:00:00" or ""       local wall clock; "" means date only
    teaser          short HTML description, often empty
    ticket_pricing  "$15 General Admission | Free for MIT ID Holders",
                    "Free and open to the public", or a note that is not a price

`timestamp` is ignored: it is `date` + `time` written as if they were UTC (an
8 PM concert carries 20:00Z), so treating it as an instant would move every
event four or five hours.

Neither endpoint takes paging or filter arguments.
"""
import html
import logging
import re
from datetime import datetime
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

USER_AGENT = "CambridgeCalendar/1.0 (+https://cambridgecalendar.com)"

# (site, default category for a performance)
SITES = (
    ("https://music.mit.edu", EventCategory.MUSIC),
    ("https://theater.mit.edu", EventCategory.THEATER),
)

# Calendar categories that make an event a talk rather than a performance
TALK_CATEGORIES = {"speaker", "panel", "symposium", "conference", "salon", "masterclass", "workshop"}

PRICE = re.compile(r"\$\d|\bfree\b", re.IGNORECASE)


class MITMusicTheaterScraper(BaseScraper):
    """MIT Music and MIT Theater Arts events"""

    def __init__(self):
        super().__init__(
            source_name="MIT Music & Theater",
            source_url="https://music.mit.edu/events/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        for site, category in SITES:
            # Either site failing fails the source: a half-scraped result would
            # read as one department having cancelled everything.
            for event in self.parse_items(self._fetch(site), category):
                key = (event.title.lower(), event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def _fetch(self, site: str) -> list:
        response = requests.get(
            f"{site}/wp-json/calendar/v1/events",
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise ValueError(f"{site} calendar API returned {type(items).__name__}, expected a list")
        return items

    def parse_items(self, items: Iterable[dict], category: EventCategory) -> List[EventCreate]:
        events = []
        for item in items:
            try:
                event = self.parse_item(item, category)
            except Exception as e:
                logger.warning(f"Failed to parse MIT calendar item {item.get('id')}: {e}")
                continue
            if event:
                events.append(event)
        return events

    def parse_item(self, item: dict, category: EventCategory) -> Optional[EventCreate]:
        title = self._text(item.get("title"))
        link = (item.get("link") or "").strip()
        if not title or not link.startswith("http"):
            return None

        when = self.read_start(item.get("date"), item.get("time"))
        if when is None:
            logger.warning(f"Skipping '{title}' - unreadable date/time "
                           f"{item.get('date')!r} {item.get('time')!r} ({link})")
            return None
        start, all_day = when

        end = None
        if not all_day and item.get("end_time"):
            end_when = self.read_start(item.get("date"), item.get("end_time"))
            if end_when and end_when[0] > start:
                end = end_when[0]

        subtitle = self._text(item.get("secondary_title"))
        teaser = self._text(item.get("teaser"))
        description = teaser or (f"{title} - {subtitle}" if subtitle else "") or f"{title} at MIT"

        pricing = self._text(item.get("ticket_pricing"))
        cost = pricing if pricing and PRICE.search(pricing) else None

        slugs = {c for c in (item.get("categories") or []) if isinstance(c, str)}
        if "performance" not in slugs and slugs & TALK_CATEGORIES:
            category = EventCategory.LECTURES

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name="MIT",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=category,
            cost=cost,
            source_url=link,
            source_name=self.source_name,
            image_url=(item.get("thumbnail") or None),
        )

    @staticmethod
    def read_start(date_value, time_value) -> Optional[tuple]:
        """(start, all_day) from "2026-10-09" and "20:00:00" (or "" for none)."""
        try:
            day = datetime.strptime((date_value or "").strip(), "%Y-%m-%d")
        except ValueError:
            return None
        time_value = (time_value or "").strip()
        if not time_value:
            return day, True
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                clock = datetime.strptime(time_value, fmt)
            except ValueError:
                continue
            if clock.second:
                return None
            return day.replace(hour=clock.hour, minute=clock.minute), False
        return None

    @staticmethod
    def _text(value) -> str:
        """Plain text from an HTML-escaped, sometimes HTML-bearing field."""
        if not value or not isinstance(value, str):
            return ""
        text = BeautifulSoup(value, "html.parser").get_text(" ") if "<" in value else value
        return " ".join(html.unescape(text).split())
