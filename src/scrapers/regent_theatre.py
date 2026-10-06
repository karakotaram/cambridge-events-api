"""Scraper for the Regent Theatre in Arlington

The schedule runs on EventON, which loads its listings over AJAX — the initial
HTML contains only empty `.eventon_events_list` shells behind loading bars. The
previous scraper parsed that empty page and returned nothing, silently.

Waiting for the network to settle gets the rendered list. The visible date is
written "thu03sep8:00 pm" with no year, so each event's start is read from two
machine-readable renderings instead, and kept only when they agree:

  - `data-time="1791590400-1791604740"`, a start-end pair of Unix times. These
    are UTC instants and are converted to Eastern explicitly. They used to go
    through `datetime.fromtimestamp()` with no zone, which reads them in the
    machine's zone — UTC in CI, four or five hours off.
  - `itemprop="startDate" content="2026-11-1T19:00-4:00"`. The wall clock is
    right; the offset is not. EventON writes -4:00 (daylight time) year-round,
    so reading the offset put every event in standard time an hour early:
    "Monster" on Nov 1 shows "7:00 pm (GMT-05:00)" on the card and was
    published at 6:00 pm, along with 14 others of 34.

EventON fills a missing end time with 23:59, which is not something the venue
published, so that end is dropped.
"""
import logging
from datetime import datetime, time, timezone
from typing import List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory, to_eastern_naive

logger = logging.getLogger(__name__)

VENUE = "Regent Theatre"
ADDRESS = "7 Medford St"


class RegentTheatreScraper(BasePlaywrightScraper):
    """Scraper for Regent Theatre events"""

    def __init__(self):
        super().__init__(
            source_name=VENUE,
            # /schedule redirects here; going straight there avoids the hop
            source_url="https://regenttheatre.com/schedule/list/",
        )

    def scrape_events(self) -> List[EventCreate]:
        # EventON fetches its listings after load, so domcontentloaded is far
        # too early — the page looks like it has no events at all.
        # A failed load raises. It used to return [], which the run recorded as
        # "ok, 0 events", indistinguishable from an empty schedule.
        self.goto(self.source_url, wait_until="networkidle", timeout=60000)
        self.wait_for_stable_count(".eventon_list_event", timeout=25000)
        soup = self.get_soup()

        events: List[EventCreate] = []
        seen = set()
        for node in soup.find_all(class_="eventon_list_event"):
            event = self._parse_event(node)
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    def _parse_event(self, node) -> Optional[EventCreate]:
        title_el = node.find(class_="evcal_event_title")
        title = self.clean_text(title_el.get_text()) if title_el else ""
        if len(title) < 3:
            return None

        epoch = self._epoch(node, 0)
        wall_clock = self._microdata_wall_clock(node, "startDate")
        if epoch and wall_clock and epoch != wall_clock:
            # One of the two renderings changed meaning; do not pick a side.
            logger.warning(f"Skipping '{title}' - data-time says {epoch}, "
                           f"microdata says {wall_clock}")
            return None
        start = epoch or wall_clock
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable start date")
            return None

        end = self._epoch(node, 1)
        if end is not None and (end.time() == time(23, 59) or end <= start):
            end = None      # EventON's stand-in for "no end time given"

        link = node.find("a", href=True)
        image = node.find(attrs={"itemprop": "image"})

        return EventCreate(
            title=title[:200],
            description=f"{title} at the {VENUE} in Arlington."[:2000],
            start_datetime=start,
            end_datetime=end,
            source_url=link["href"] if link else self.source_url,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Arlington",
            state="MA",
            zip_code="02474",
            category=self._categorize(title),
            image_url=image.get("content") if image else None,
        )

    @staticmethod
    def _microdata_wall_clock(node, prop: str) -> Optional[datetime]:
        """Read itemprop="startDate" content="2026-11-1T19:00-4:00" as wall clock.

        The offset is discarded: EventON writes -4:00 even in standard time.
        Note the unpadded month and day — dateutil handles it, `strptime` would
        not.
        """
        el = node.find(attrs={"itemprop": prop})
        value = el.get("content") if el else None
        if not value:
            return None
        try:
            return date_parser.parse(value).replace(tzinfo=None, second=0, microsecond=0)
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _epoch(node, index: int) -> Optional[datetime]:
        """data-time="1788480000-1788494340" is a start-end pair of Unix times.

        Unix time is UTC by definition; convert it to Eastern wall clock rather
        than to whatever zone the scraping machine happens to be in.
        """
        parts = (node.get("data-time") or "").split("-")
        raw = parts[index] if len(parts) > index else ""
        if not raw.isdigit():
            return None
        try:
            instant = datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
        return to_eastern_naive(instant).replace(second=0, microsecond=0)

    @staticmethod
    def _categorize(title: str) -> EventCategory:
        text = title.lower()
        if any(w in text for w in ("comedy", "comedian", "stand-up", "standup", "improv")):
            return EventCategory.THEATER
        if any(w in text for w in ("film", "movie", "screening", "cinema")):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("tribute", "band", "concert", "live", "music", "orchestra")):
            return EventCategory.MUSIC
        return EventCategory.MUSIC
