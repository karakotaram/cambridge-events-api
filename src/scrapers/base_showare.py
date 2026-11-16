"""accesso ShoWare ticketing storefronts, read through their performance list.

A ShoWare storefront's "Upcoming Performances" list is drawn by
PerformanceList.js from `/include/widgets/events/performancelist.asp?action=perf`,
which returns JSON: every on-sale performance with its event name, subtitle,
venue name and address, start and end, price range and description. Pages are
requested until the response says `endOfList`.

**Times.** `PerformanceDateTime` is written out in words ("Tuesday, November
17, 2026 8:00:00 PM") in the venue's zone, which `TimeZone` names. A
performance labelled with anything other than an Eastern zone is skipped,
not converted. The printed weekday must agree with the date.

A storefront is a ticket seller, not a venue: it can sell for rooms that have
sources of their own. A subclass lists those in COVERED_ELSEWHERE so the same
show does not arrive twice under two titles and two times; see
twenty_four_hour_music.py.
"""
import logging
from datetime import datetime
from typing import FrozenSet, List, Optional

import requests

from src.models.event import EventCreate
from src.scrapers.base_feed import BaseFeedScraper

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 10
EASTERN_LABELS = {"EST", "EDT", "ET", "EST5EDT"}
STAMP = "%A, %B %d, %Y %I:%M:%S %p"


def street_of(address: Optional[str]) -> Optional[str]:
    """ShoWare joins two address lines, usually identical: '247 Elm St 247 Elm St'."""
    words = (address or "").split()
    half = len(words) // 2
    if half and len(words) % 2 == 0 and words[:half] == words[half:]:
        words = words[:half]
    return " ".join(words) or None


class BaseShoWareScraper(BaseFeedScraper):
    """One ShoWare storefront."""

    # Venue names (as the storefront spells them) whose performances another
    # source already publishes.
    COVERED_ELSEWHERE: FrozenSet[str] = frozenset()

    def __init__(self, source_name: str, site: str):
        super().__init__(source_name=source_name, source_url=f"{site.rstrip('/')}/")
        self.site = site.rstrip("/")

    def fetch_page(self, page: int) -> dict:
        response = requests.get(
            f"{self.site}/include/widgets/events/performancelist.asp",
            params={"action": "perf", "page": page, "listPageSize": PAGE_SIZE},
            headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        events, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            data = self.fetch_page(page)
            if "performance" not in data:
                raise ValueError(f"{self.source_name}: ShoWare response has no 'performance' list")
            for performance in data["performance"]:
                if performance.get("PerformanceID") in seen:
                    continue
                seen.add(performance.get("PerformanceID"))
                event = self.parse_performance(performance)
                if event:
                    events.append(event)
            if not data["performance"] or (data.get("settings") or {}).get("endOfList", True):
                break
        else:
            logger.warning(f"{self.source_name}: stopped after {MAX_PAGES} pages; the list has more")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one performance -------------------------------------------------- #

    @staticmethod
    def wall_clock(stamp: Optional[str]) -> Optional[datetime]:
        """'Tuesday, November 17, 2026 8:00:00 PM' -> datetime, if the weekday agrees."""
        if not stamp:
            return None
        try:
            parsed = datetime.strptime(" ".join(str(stamp).split()), STAMP)
        except ValueError:
            return None
        if parsed.strftime("%A").lower() != str(stamp).split(",")[0].strip().lower():
            return None
        return parsed

    def cost(self, performance: dict) -> Optional[str]:
        low, high = performance.get("PerformanceMinPrice"), performance.get("PerformanceMaxPrice")
        try:
            low, high = float(low or 0), float(high or 0)
        except (TypeError, ValueError):
            return None
        if high <= 0:
            return None
        return f"${low:.2f}" if low == high else f"${low:.2f}–${high:.2f}"

    def parse_performance(self, performance: dict) -> Optional[EventCreate]:
        title = self.text(performance.get("Event"))
        if not self.is_event(title):
            return None
        url = f"{self.site}/eventperformances.asp?evt={performance['EventID']}" \
            if performance.get("EventID") else self.source_url

        venue = self.text(performance.get("Venue"))
        if venue in self.COVERED_ELSEWHERE:
            return None

        if performance.get("TimeZone") not in EASTERN_LABELS:
            logger.warning(f"Skipping '{title}' - performance time zone is "
                           f"{performance.get('TimeZone')!r}, not Eastern ({url})")
            return None
        start = self.wall_clock(performance.get("PerformanceDateTime"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date "
                           f"({performance.get('PerformanceDateTime')!r}) ({url})")
            return None
        end = self.wall_clock(performance.get("PerformanceEndDateTime"))
        if end is not None and end <= start:
            end = None

        where = self.place(title, venue, street_of(performance.get("VenueAddress")),
                           performance.get("VenueCity"), performance.get("VenueZipCode"))
        if where is None:
            return None

        subtitle = self.text(performance.get("PerformanceName"))
        body = self.text(performance.get("Description"))
        description = " ".join(p for p in (f"{subtitle}." if subtitle else "", body) if p)
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
            cost=self.cost(performance),
            source_name=self.source_name,
            source_url=url,
        )
