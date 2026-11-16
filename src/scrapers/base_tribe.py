"""WordPress sites running The Events Calendar, read through its REST API.

`/wp-json/tribe/events/v1/events` returns every upcoming event as JSON, paged,
with venue, cost and image — no browser and no HTML parsing. A subclass names
its site, page, home and category; see base_feed for the shared rules.

**Times.** Each event carries `start_date` (wall clock in the site's configured
timezone), `utc_start_date`, and that `timezone`. When the timezone is Eastern,
`start_date` is already what CLAUDE.md rule 2 wants. When it is anything else
the site is misconfigured, because every venue here is in Massachusetts, and
the two fields disagree about which is right:

  - Kendall Center's site is set to UTC+0. Its events read 18:00 Community Yoga,
    19:30 Sofar Sounds, and the matching `utc_start_date` is identical. Those
    are local times typed into a site that thinks it is in London; converting
    the "UTC" would move every event four or five hours early.

So a non-Eastern site is not guessed at. Its events are skipped, loudly, unless
the subclass sets WALL_CLOCK_IS_EASTERN after someone has checked — as above —
that the wall clock is what the venue means.
"""
import logging
from datetime import datetime
from typing import List, Optional

import requests

from src.models.event import EventCreate
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper

logger = logging.getLogger(__name__)

PER_PAGE = 50
MAX_PAGES = 10


class BaseTribeScraper(BaseFeedScraper):
    """One site's Tribe Events REST API."""

    # Set only after checking that the site's wall clock is the venue's
    # Eastern time despite a non-Eastern timezone setting. See module docstring.
    WALL_CLOCK_IS_EASTERN = False

    def __init__(self, source_name: str, site: str, page: str):
        super().__init__(source_name=source_name, source_url=f"{site}{page}")
        self.site = site.rstrip("/")

    @property
    def api_url(self) -> str:
        return f"{self.site}/wp-json/tribe/events/v1/events"

    def fetch_page(self, page: int) -> Optional[dict]:
        response = requests.get(
            self.api_url,
            params={"per_page": PER_PAGE, "start_date": "now", "status": "publish", "page": page},
            headers=self.get_browser_headers(),
            timeout=30,
        )
        if page > 1 and response.status_code in (400, 404):
            return None  # past the last page: 400 on some Tribe versions, 404 on others
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        for page in range(1, MAX_PAGES + 1):
            data = self.fetch_page(page)
            if data is None:
                break
            items = data.get("events") or []
            for item in items:
                if item.get("id") in seen:
                    continue
                seen.add(item.get("id"))
                event = self.parse_item(item)
                if event:
                    events.append(event)
            if not items or page >= int(data.get("total_pages") or 1):
                break
        else:
            logger.warning(f"{self.source_name}: stopped after {MAX_PAGES} pages; the feed has more")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    @staticmethod
    def _as_dict(value) -> dict:
        """Tribe returns venue/image as a dict, an empty list, or a list of dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    def _wall_clock(self, item: dict, key: str) -> Optional[datetime]:
        raw = item.get(key)
        if not raw:
            return None
        if item.get("timezone") not in EASTERN_ZONES and not self.WALL_CLOCK_IS_EASTERN:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        url = item.get("url") or self.source_url

        all_day = bool(item.get("all_day"))
        start = self._wall_clock(item, "start_date")
        end = None if all_day else self._wall_clock(item, "end_date")
        if start is None:
            # Never guess a date (CLAUDE.md rule 1) — and a time in an unknown
            # zone is a guess. See the module docstring.
            logger.warning(f"Skipping '{title}' - no Eastern start "
                           f"(start_date={item.get('start_date')!r}, timezone={item.get('timezone')!r}) ({url})")
            return None
        if all_day:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if end is not None and end < start:
            end = None

        venue = self._as_dict(item.get("venue"))
        where = self.place(title, venue.get("venue"), venue.get("address"), venue.get("city"), venue.get("zip"))
        if where is None:
            return None

        description = self.text(item.get("description")) or self.text(item.get("excerpt"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            cost=self.text(item.get("cost")) or None,
            source_name=self.source_name,
            source_url=url,
            image_url=self._as_dict(item.get("image")).get("url") or None,
        )
