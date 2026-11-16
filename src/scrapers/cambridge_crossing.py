"""Cambridge Crossing (CX) — the neighbourhood's own events on The Common.

WordPress, but not The Events Calendar: the events page is filled in the
browser by the theme's "neoscape-calendar" plugin from a hand-maintained file,

    /wp-content/themes/neo-cambridgecrossing/_uploads/events.json

a list of entries like

    {"title": "CX Summer Nights Feat. Jill McCracken + The Far Out",
     "start_date": "20260626", "end_date": "20260626",
     "start_time": "5:30&nbsp;PM", "end_time": "8:30&nbsp;PM",
     "link": "https://www.eventbrite.com/e/...", "content": "...", ...}

Read from that file:

  - one day with a start time: an event at that time;
  - one day with no time: an all-day event;
  - a span of days ("CX Fit", 20260606–20260926, "every Wednesday at 6PM and
    Saturday at 10AM") is a season, not an occasion. It is skipped: the
    schedule is prose, and turning prose into dates is guessing;
  - no date at all is a signpost to another venue's calendar (the
    restaurants, Lamplighter) and is skipped.

`start_date_str` and `start_datetime` are midnight-ish epochs of the date and
carry no time, so they are not read. The structured time is trusted over the
prose: the 2026 World Cup watch party's entry says 3:00 PM in its field and
"4:00 PM" in its text.

Seasonal: summer concerts and fitness. Nothing is listed between seasons.
"""
import html
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://cambridgecrossing.com"
FEED = f"{SITE}/wp-content/themes/neo-cambridgecrossing/_uploads/events.json"
TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([AP])\.?M\.?$", re.I)


class CambridgeCrossingScraper(BaseFeedScraper):
    # Lamplighter's taproom at CX is 110 N First St, 02141
    HOME = Place("Cambridge Crossing", None, "Cambridge", "02141")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Cambridge Crossing", source_url=f"{SITE}/events/")

    def fetch_feed(self) -> list:
        response = requests.get(FEED, headers={**self.get_browser_headers(), "Accept": "application/json"},
                                timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"{self.source_name}: {FEED} returned {type(data).__name__}, expected a list")
        return data

    def scrape_events(self) -> List[EventCreate]:
        events = [e for e in (self.parse_item(item) for item in self.fetch_feed()) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def read_time(value) -> Optional[Tuple[int, int]]:
        text = " ".join(html.unescape(str(value or "")).split())
        m = TIME.match(text)
        if not m:
            return None
        return int(m.group(1)) % 12 + (12 if m.group(3).upper() == "P" else 0), int(m.group(2))

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        first, last = str(item.get("start_date") or ""), str(item.get("end_date") or "")
        if not first:
            logger.info(f"{self.source_name}: skipping '{title}' - no date (a pointer to another calendar)")
            return None
        if last and last != first:
            logger.info(f"{self.source_name}: skipping '{title}' - spans {first}-{last}; a season, not one occasion")
            return None
        try:
            day = datetime.strptime(first, "%Y%m%d")
        except ValueError:
            logger.warning(f"Skipping '{title}' - unreadable date {first!r} ({FEED})")
            return None

        start_hm = self.read_time(item.get("start_time"))
        if item.get("start_time") and start_hm is None:
            logger.warning(f"Skipping '{title}' - unreadable time {item.get('start_time')!r} ({FEED})")
            return None
        end_hm = self.read_time(item.get("end_time"))
        all_day = start_hm is None
        start = day if all_day else day.replace(hour=start_hm[0], minute=start_hm[1])
        end = day.replace(hour=end_hm[0], minute=end_hm[1]) if (end_hm and not all_day) else None

        where = self.HOME
        description = self.text(item.get("content"))
        if len(description) < 20:
            description = self.fallback_description(title, where)
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            all_day=all_day,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=item.get("link") or self.source_url,
            image_url=item.get("img_url") or None,
        )
