"""Squarespace events collections, read as JSON.

Any Squarespace events page answers `?format=json` with every upcoming event
under `upcoming` — epoch-millisecond start and end, the post body, a location
block, an image. (`past` holds recent past events and is not read.) A subclass
names its site, collection path, home and category; see base_feed for the
shared rules.

**Times.** Squarespace turns the time an editor types into an epoch using the
site's timezone setting, which the payload reports as `website.timeZone`. On an
Eastern site the epoch is the true instant. On a site set to anything else the
epoch is off by the difference — Native Plant Community Gardeners' site is set
to America/Los_Angeles, so its "10:00 AM" is stored as 1 PM Eastern. A
non-Eastern site is refused unless the subclass sets WALL_CLOCK_IS_EASTERN,
in which case the epoch is read back as the site's wall clock and that wall
clock is taken as Eastern.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCreate, to_eastern_naive
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper

logger = logging.getLogger(__name__)


class BaseSquarespaceScraper(BaseFeedScraper):
    """One Squarespace events collection."""

    # See module docstring. Set only after checking a non-Eastern site's
    # displayed times against what the venue means.
    WALL_CLOCK_IS_EASTERN = False

    def __init__(self, source_name: str, site: str, collection: str):
        super().__init__(source_name=source_name, source_url=f"{site}{collection}")
        self.site = site.rstrip("/")
        self.site_zone: Optional[str] = None

    def fetch_collection(self) -> dict:
        response = requests.get(self.source_url, params={"format": "json"},
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        payload = self.fetch_collection()
        if "upcoming" not in payload:
            raise ValueError(f"{self.source_name}: Squarespace JSON has no 'upcoming' list "
                             f"— is {self.source_url} still an events collection?")

        self.site_zone = (payload.get("website") or {}).get("timeZone")
        if self.site_zone not in EASTERN_ZONES and not self.WALL_CLOCK_IS_EASTERN:
            raise ValueError(f"{self.source_name}: site timezone is {self.site_zone!r}, not Eastern; "
                             f"its times cannot be trusted (see base_squarespace)")

        events = []
        for item in payload["upcoming"]:
            event = self.parse_item(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    def wall_clock(self, ms) -> Optional[datetime]:
        """Epoch milliseconds -> naive Eastern wall clock, on the minute.

        Squarespace adds noise in the milliseconds (6 PM arrives as ...800772),
        and EventValidator rejects any start with sub-minute precision.
        """
        if not isinstance(ms, (int, float)) or ms <= 0:
            return None
        instant = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        if self.site_zone in EASTERN_ZONES or not self.site_zone:
            local = to_eastern_naive(instant)
        else:
            local = instant.astimezone(ZoneInfo(self.site_zone)).replace(tzinfo=None)
        return local.replace(second=0, microsecond=0)

    def body_text(self, body_html: Optional[str]) -> str:
        """The post's text blocks; Squarespace bodies also carry layout markup."""
        soup = BeautifulSoup(body_html or "", "html.parser")
        blocks = [self.text(str(b)) for b in soup.select(".sqs-html-content")]
        return self.clean_text(" ".join(b for b in blocks if b)) or self.text(body_html)

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        url = f"{self.site}{item['fullUrl']}" if item.get("fullUrl") else self.source_url

        start = self.wall_clock(item.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end = self.wall_clock(item.get("endDate"))
        if end is not None and end < start:
            end = None

        loc = item.get("location") or {}
        where = self.place(title, loc.get("addressTitle"), loc.get("addressLine1"), loc.get("addressLine2"))
        if where is None:
            return None

        description = self.body_text(item.get("body")) or self.text(item.get("excerpt"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("assetUrl") or None,
        )
