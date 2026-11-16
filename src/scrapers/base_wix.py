"""Wix Events pages, read from the data Wix writes into the page.

A Wix site's events page is rendered server-side, and the Wix Events widget's
data rides along in `<script type="application/json" id="wix-warmup-data">`:
each event with `scheduling.config.startDate` (a UTC instant), its
`timeZoneId`, the date and time the page prints (`startDateFormatted`,
`startTimeFormatted`), a location with a structured address, and a slug for
`/event-details/<slug>`. No browser is needed.

**Times.** The instant is converted to Eastern only when the event's
`timeZoneId` is Eastern, and the result must match the date and time the page
prints; otherwise the event is skipped. `scheduleTbd` events have no date and
are skipped.

**Where.** Wix gives a structured city for every venue, so base_feed's rules
apply directly: a Boston book club at a Boston bar is dropped, an event at the
shop is kept. Location type 1 is Wix's "online".

Not every Wix site that lists events uses Wix Events — the Dante Alighieri
Society's "events" page is a column of poster images — so a page whose
warm-up data holds no events fails loudly rather than reporting zero.
"""
import json
import logging
import re
from datetime import datetime
from typing import Iterator, List, Optional

import requests

from src.models.event import EventCreate, to_eastern_naive
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

WARMUP = re.compile(r'<script type="application/json" id="wix-warmup-data">(.*?)</script>', re.S)
WIX_ONLINE = 1
WIX_CANCELED = 3


def wix_events(page: str) -> Iterator[dict]:
    """Every Wix Events record in the page's warm-up data."""
    for block in WARMUP.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "scheduling" in node and "title" in node and "id" in node:
                    yield node
                    continue
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)


class BaseWixEventsScraper(BaseFeedScraper):
    """One Wix site's events page."""

    def __init__(self, source_name: str, site: str, page: str):
        super().__init__(source_name=source_name, source_url=f"{site.rstrip('/')}{page}")
        self.site = site.rstrip("/")

    def fetch_page(self) -> str:
        response = requests.get(self.source_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        records, seen = [], set()
        for record in wix_events(self.fetch_page()):
            if record["id"] not in seen:
                seen.add(record["id"])
                records.append(record)
        if not records:
            raise ValueError(f"{self.source_name}: no Wix Events data on {self.source_url}")
        events = [e for e in (self.parse_record(r) for r in records) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    @staticmethod
    def instant(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return to_eastern_naive(moment) if moment.tzinfo else None

    def start_of(self, scheduling: dict) -> Optional[datetime]:
        config = scheduling.get("config") or {}
        if config.get("scheduleTbd") or config.get("timeZoneId") not in EASTERN_ZONES:
            return None
        start = self.instant(config.get("startDate"))
        if start is None:
            return None
        printed = f"{scheduling.get('startDateFormatted')} {scheduling.get('startTimeFormatted')}"
        try:
            shown = datetime.strptime(printed, "%B %d, %Y %I:%M %p")
        except ValueError:
            return None
        return start if shown == start else None

    def parse_record(self, record: dict) -> Optional[EventCreate]:
        title = self.text(record.get("title"))
        if not self.is_event(title) or record.get("status") == WIX_CANCELED:
            return None
        url = f"{self.site}/event-details/{record['slug']}" if record.get("slug") else self.source_url

        scheduling = record.get("scheduling") or {}
        start = self.start_of(scheduling)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({scheduling.get('formatted')!r}, "
                           f"zone {(scheduling.get('config') or {}).get('timeZoneId')!r}) ({url})")
            return None
        end = self.instant((scheduling.get("config") or {}).get("endDate"))
        if end is not None and end <= start:
            end = None

        location = record.get("location") or {}
        if location.get("type") == WIX_ONLINE:
            where = Place("Online", None, self.HOME.city if self.HOME else None, None)
        else:
            address = location.get("fullAddress") or {}
            street = (address.get("streetAddress") or {})
            line = " ".join(str(street.get(k) or "") for k in ("number", "name")).strip() or None
            where = self.place(title, location.get("name"), line,
                               address.get("city") or location.get("address"), address.get("postalCode"))
        if where is None:
            return None

        description = " ".join(p for p in (self.text(record.get("description")), self.text(record.get("about"))) if p)
        if len(description) < 20:
            description = self.fallback_description(title, where)

        ticketing = (record.get("registration") or {}).get("ticketing") or {}
        low, high = ticketing.get("lowestPrice"), ticketing.get("highestPrice")
        cost = (low if low == high else f"{low}–{high}") if low and high else (low or None)

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
            cost=cost,
            source_name=self.source_name,
            source_url=url,
            image_url=(record.get("mainImage") or {}).get("url") or None,
        )
