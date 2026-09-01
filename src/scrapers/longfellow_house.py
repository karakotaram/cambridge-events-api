"""Scraper for Longfellow House - Washington's Headquarters National Historic Site

Loads the park's calendar page in a browser and reads the JSON its own event
service returns, rather than the cards rendered from it.

Two dead ends are worth recording, because both look like the obvious approach:

  - **Parsing the rendered `.usa-card` elements** races the page. It yielded 54
    cards run alone and 4 inside a full scrape, because under load the first
    cards exist long before the rest and any "wait for the element" strategy
    reads a partial list. Silent partial collapse — failure mode 1 in
    docs/ARCHITECTURE.md — with no error anywhere.
  - **Calling EventCalendarService.cfc directly** is correct but unusable: it
    takes ~150 seconds per page for a plain HTTP client, regardless of window
    size or paging, while the same call from inside the page returns in about
    three. Six minutes for one source is not affordable in a 12-minute pipeline.

Capturing the browser's own response gets the complete payload on the fast path
with no rendering involved.

This scraper also spent months written but never registered in `scrape.py`, so
it produced nothing and nothing complained. `src/sources.py` is the registry
now, and `test_every_scraper_module_is_registered` fails if that recurs.
"""
import html
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# The page requests a full year; trimmed to match the other sources, and
# because the calendar is mostly hourly house tours further out.
WINDOW_DAYS = 60

VENUE = "Longfellow House - Washington's Headquarters NHS"
ADDRESS = "105 Brattle St"


class LongfellowHouseScraper(BasePlaywrightScraper):
    """Scraper for Longfellow House NHS events"""

    def __init__(self):
        super().__init__(
            source_name="Longfellow House",
            source_url="https://www.nps.gov/long/planyourvisit/calendar.htm",
        )

    def scrape_events(self) -> List[EventCreate]:
        payloads: List[dict] = []

        def capture(response):
            if "EventCalendarService" not in response.url:
                return
            try:
                payloads.append(response.json())
            except Exception:
                pass

        try:
            self.page.on("response", capture)
            self.goto(self.source_url, timeout=60000)
            # The page issues its calendar request after load; give it room to
            # arrive rather than waiting on any rendered element.
            self.page.wait_for_timeout(6000)
        except Exception as e:
            logger.error(f"Could not load Longfellow House calendar: {e}")
            return []
        finally:
            self.cleanup_browser()

        if not payloads:
            logger.error("Longfellow House calendar returned no event data")
            return []

        horizon = datetime.now() + timedelta(days=WINDOW_DAYS)
        events: List[EventCreate] = []
        seen = set()
        for payload in payloads:
            for item in payload.get("data") or []:
                for event in self._parse_item(item):
                    if event.start_datetime > horizon:
                        continue
                    key = (event.source_url, event.start_datetime)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(event)

        logger.info(f"Scraped {len(events)} events from Longfellow House")
        return events

    def _parse_item(self, item: dict) -> List[EventCreate]:
        """One API record can carry several start times for the same day."""
        title = self._text(item.get("title"))
        if len(title) < 3:
            return []

        day = self._parse_date(item.get("date") or item.get("dateStart"))
        if day is None:
            # Never guess a date — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date "
                           f"({item.get('date') or item.get('dateStart')!r})")
            return []

        description = self._text(item.get("description"))
        if len(description) < 20:
            description = f"{title} at {VENUE} in Cambridge."

        image_url = None
        images = item.get("images") or []
        if images and isinstance(images[0], dict):
            url = images[0].get("url") or images[0].get("path")
            if url:
                image_url = url if url.startswith("http") else f"https://www.nps.gov{url}"

        event_id = item.get("id") or item.get("eventId")
        url = (f"https://www.nps.gov/planyourvisit/event-details.htm?id={event_id}"
               if event_id else self.source_url)

        fee = item.get("feeInfo")
        cost = "Free" if item.get("isFree") else (self._text(fee) or None)

        out = []
        for start in self._start_times(item, day):
            out.append(EventCreate(
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
                cost=cost,
                image_url=image_url,
                registration_required=bool(item.get("isRegResRequired")),
            ))
        return out

    def _start_times(self, item: dict, day: datetime) -> List[datetime]:
        """Combine the record's date with each of its start times.

        An all-day event has no times; midnight is honest for those.
        """
        if item.get("isAllDay"):
            return [day]

        starts = []
        for slot in item.get("times") or []:
            raw = (slot or {}).get("timeStart")
            if not raw:
                continue
            try:
                parsed = date_parser.parse(str(raw))
            except (ValueError, OverflowError):
                continue
            starts.append(day.replace(hour=parsed.hour, minute=parsed.minute,
                                      second=0, microsecond=0))
        return starts or [day]

    @staticmethod
    def _parse_date(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return date_parser.parse(str(value)).replace(hour=0, minute=0,
                                                         second=0, microsecond=0)
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _text(value) -> str:
        if not value:
            return ""
        text = re.sub(r"<[^>]+>", " ", str(value))
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    @staticmethod
    def _categorize(title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(w in text for w in ("concert", "music", "recital", "ensemble", "jazz")):
            return EventCategory.MUSIC
        if any(w in text for w in ("tour", "talk", "lecture", "history", "reading", "poetry")):
            return EventCategory.LECTURES
        if any(w in text for w in ("garden", "exhibit", "art", "craft")):
            return EventCategory.ARTS_CULTURE
        return EventCategory.COMMUNITY
