"""Scraper for Longy School of Music events

Reads the site's The Events Calendar (Tribe) REST API with plain HTTP. One
request returns the whole season — 34 events on 2026-10-06, a single page.

It used to drive headless Chromium to `/calendar/` and read that page's
JSON-LD. That page shows the first ten upcoming events, so whenever the
scraper worked it returned exactly 10. By 2026-10-05 headless Chromium was
refused outright (HTTP 403). The API answers an honestly identified client.

The site rate-limits into an Imunify360 challenge page under repeated
requests, so this makes as few as it can: one page per 50 events, no retries.
A challenge or an error status raises, and the source is recorded as failed
instead of as a school with nothing scheduled.
"""
import html
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper, USER_AGENT

logger = logging.getLogger(__name__)

API_URL = "https://longy.edu/wp-json/tribe/events/v1/events"
PER_PAGE = 50
MAX_PAGES = 4

# An honest client identity. Never a browser's — see CLAUDE.md "Traps".

# WPBakery layout markup left in the description: "[vc_row type=...]", "[/vc_column]"
SHORTCODE = re.compile(r"\[/?[a-z][a-z0-9_]*(?:\s[^\]]*)?\]")

VENUE = "Longy School of Music"
ADDRESS = "27 Garden Street"


class LongyScraper(BaseScraper):
    """Scraper for Longy School of Music of Bard College"""

    def __init__(self):
        super().__init__(
            source_name="Longy School of Music",
            source_url="https://longy.edu/calendar/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        items: List[dict] = []
        for page in range(1, MAX_PAGES + 1):
            # No retries and no try/except: a second attempt is exactly what
            # trips the rate limit, and a refusal must fail the source.
            response = requests.get(
                API_URL,
                params={"start_date": "now", "per_page": PER_PAGE, "page": page},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            items += payload.get("events") or []
            if page >= payload.get("total_pages", 1):
                break

        events = self.parse_items(items)
        logger.info(f"Scraped {len(events)} events from Longy ({len(items)} listed)")
        return events

    def parse_items(self, items: List[dict]) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        for item in items:
            event = self._parse_event(item)
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
        return events

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        if item.get("status", "publish") != "publish" or item.get("hide_from_listings"):
            return None

        title = self._text(item.get("title"))
        if len(title) < 3:
            return None

        all_day = bool(item.get("all_day"))
        start = self._start(item, all_day)
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable start ({item.get('start_date')!r})")
            return None
        end = None if all_day else self._utc(item.get("utc_end_date"))
        if end is not None and end <= start:
            end = None

        venue = self._as_dict(item.get("venue"))
        venue_name = self._text(venue.get("venue")) or VENUE
        street = self._text(venue.get("address")) or ADDRESS
        city = self._text(venue.get("city")) or "Cambridge"
        zip_code = self._text(venue.get("zip")) or "02138"

        description = self._prose(item.get("excerpt")) or self._prose(item.get("description"))
        if len(description) < 20:
            description = f"{title} at {VENUE}, Cambridge."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=venue_name[:150],
            street_address=street[:200],
            city=city,
            state="MA",
            zip_code=zip_code,
            category=EventCategory.MUSIC,
            cost=self._text(item.get("cost")) or None,
            source_name=self.source_name,
            source_url=item.get("url") or self.source_url,
            image_url=self._as_dict(item.get("image")).get("url"),
        )

    def _start(self, item: dict, all_day: bool) -> Optional[datetime]:
        """The UTC start, converted to Eastern; an all-day event's local date."""
        if all_day:
            local = self._naive(item.get("start_date"))
            return local.replace(hour=0, minute=0) if local else None
        return self._utc(item.get("utc_start_date"))

    @classmethod
    def _utc(cls, value) -> Optional[datetime]:
        """Tribe's utc_* fields are UTC wall clock with no offset written."""
        naive = cls._naive(value)
        if naive is None:
            return None
        return to_eastern_naive(naive.replace(tzinfo=timezone.utc))

    @staticmethod
    def _naive(value) -> Optional[datetime]:
        """Tribe writes "2026-10-08 19:30:00"."""
        if not value:
            return None
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return parsed.replace(second=0, microsecond=0)

    @staticmethod
    def _as_dict(value) -> dict:
        """Tribe returns venue/image as a dict, an empty list, or a list of dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    @classmethod
    def _prose(cls, value) -> str:
        """A description without its page-builder shortcodes ("[vc_row ...]")."""
        return re.sub(r"\s+", " ", SHORTCODE.sub(" ", cls._text(value))).strip()

    @staticmethod
    def _text(value) -> str:
        """Strip HTML and decode entities — the API returns both ("&#8220;")."""
        if not value or not isinstance(value, str):
            return ""
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()
