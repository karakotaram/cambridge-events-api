"""Scraper for Skip the Small Talk events

Skip the Small Talk is a national organisation. Since its mid-September 2026
redesign, every city's events are products in one Squarespace store, `/store`
(the old `/public-events?category=Boston` now redirects to
`/store#city=boston`). The scraper kept looking for the old page's
`summary-item` blocks, found none, and returned nothing from Sep 16 on.

This reads the store's own JSON (`/store?category=Boston&format=json`), which
is what Squarespace renders the page from. Each product carries:

  - `categories`, the cities it is offered in. Only Boston-area products are
    kept, filtered here as well as in the URL: an unknown category once
    returned the unfiltered national list, and events in Raleigh, Baltimore
    and Chicago were published on a Cambridge calendar.
  - `tags`, which the page joins into the line it shows under each listing:
    "Wednesday, October 7, 2026, 7:00 pm, Open to Everyone, Trident Books".
  - an excerpt with "WHERE | Aeronaut, 14 Tyler St, Somerville, MA 02143".

The city now comes from that address. Everything used to be marked "Boston",
including Aeronaut in Somerville and Porter Square Books in Cambridge.

A date is used only when its printed weekday is right and the product title,
which repeats the date, agrees. There is no date-only fallback: the scraper
once invented an 18:30 start for anything it could not fully read.
"""
import html
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper, USER_AGENT

logger = logging.getLogger(__name__)

BASE = "https://www.skipthesmalltalk.com"
STORE_JSON = f"{BASE}/store"
# The category this calendar covers. Online sessions are also listed under it.
LOCAL_CATEGORY = "Boston"

# An honest client identity. Never a browser's — see CLAUDE.md "Traps".

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# "Wednesday, October 7, 2026"
DATE = re.compile(r"\b(?P<weekday>(?:mon|tues|wednes|thurs|fri|satur|sun)day),?\s+"
                  r"(?P<month>[a-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\b", re.I)
# "7:00 pm", "8:00pm"
TIME = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<mer>[ap])\.?m\b", re.I)
# "Aeronaut, 14 Tyler St, Somerville, MA 02143" / "..., 48 Waters Ave #1, Everett MA 02149"
WHERE = re.compile(r"WHERE\s*\|\s*(?P<where>[^|]*?)\s*(?:\(\s*map\s*\)|[A-Z][A-Z /&-]{3,}\s*\||$)")
ADDRESS = re.compile(r"^(?P<venue>[^,]+),\s*(?P<street>.+?),\s*(?P<city>[A-Za-z .'-]+?),?\s+"
                     r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})\b")


def _text(value) -> str:
    """Strip HTML and decode entities."""
    if not value or not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def read_date(text: str) -> Optional[datetime]:
    """The first "Weekday, Month D, YYYY" in `text`, if its weekday is right."""
    m = DATE.search(text or "")
    if not m or m["month"].lower() not in _MONTHS:
        return None
    try:
        day = datetime(int(m["year"]), _MONTHS.index(m["month"].lower()) + 1, int(m["day"]))
    except ValueError:
        return None
    if day.weekday() != _WEEKDAYS.index(m["weekday"].lower()):
        return None
    return day


def read_time(text: str) -> Optional[Tuple[int, int]]:
    m = TIME.search(text or "")
    if not m:
        return None
    hour, minute = int(m["hour"]), int(m["minute"])
    if not (1 <= hour <= 12 and minute < 60):
        return None
    return hour % 12 + (12 if m["mer"].lower() == "p" else 0), minute


class SkipSmallTalkScraper(BaseScraper):
    """Scraper for Skip the Small Talk - conversation events"""

    def __init__(self):
        super().__init__(
            source_name="Skip the Small Talk",
            source_url=f"{BASE}/store",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        """Read the Boston-area products. One request; errors are not swallowed,
        so a block fails the source rather than reading as no events."""
        response = requests.get(
            STORE_JSON,
            params={"category": LOCAL_CATEGORY, "format": "json"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        events = self.parse_items(items)
        logger.info(f"Scraped {len(events)} events from Skip the Small Talk ({len(items)} listed)")
        return events

    def parse_items(self, items: List[dict]) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        elsewhere = 0
        for item in items:
            if LOCAL_CATEGORY not in (item.get("categories") or []):
                elsewhere += 1
                continue
            event = self._parse_item(item)
            if event is None:
                continue
            key = (event.source_url, event.start_datetime)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
        if elsewhere:
            logger.info(f"Skipped {elsewhere} listings not offered in {LOCAL_CATEGORY}")
        return events

    def _parse_item(self, item: dict) -> Optional[EventCreate]:
        raw_title = _text(item.get("title"))
        if len(raw_title) < 3 or re.search(r"\bcancel+ed\b", raw_title, re.I):
            return None

        # The line the page shows under each listing
        meta = ", ".join(_text(t) for t in item.get("tags") or [])
        excerpt = _text(item.get("excerpt"))

        start = self._start(meta, raw_title, excerpt)
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{raw_title}' - no reliable date and time ({meta[:60]!r})")
            return None

        online = "Online" in (item.get("categories") or []) or re.search(r"\bonline\b", raw_title, re.I)
        venue, street, city, zip_code = self._where(excerpt)
        if online and not venue:
            venue = "Online"

        # "Millennial Speed-Dating (Monogamous): Monday, October 12, 2026"
        name = re.sub(r"\s*:\s*[A-Z][a-z]+day,.*$", "", raw_title).strip() or raw_title
        title = name if not venue or venue.lower() in name.lower() else f"{name} at {venue}"

        description = excerpt
        if len(description) < 20:
            description = (f"{title}. Skip the Small Talk events help strangers get to know each "
                           "other through structured conversation.")

        url = item.get("fullUrl") or ""
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=venue,
            street_address=street,
            city=city,
            state="MA" if city else None,
            zip_code=zip_code,
            category=EventCategory.COMMUNITY,
            source_name=self.source_name,
            source_url=f"{BASE}{url}" if url.startswith("/") else (url or self.source_url),
            image_url=item.get("assetUrl") or None,
        )

    @staticmethod
    def _start(meta: str, title: str, excerpt: str) -> Optional[datetime]:
        """Date from the listing line, cross-checked against the title's copy.

        Products are cloned from one another, and a clone's leftovers are real:
        one Chicago listing's URL still says "august-5" while its title and tags
        say August 19. Where the two dates on the page disagree, neither is used.
        """
        day = read_date(meta)
        if day is None:
            return None
        titled = read_date(title)
        if titled is not None and titled != day:
            logger.warning(f"Skipping '{title}' - listing says {day:%Y-%m-%d}, title says {titled:%Y-%m-%d}")
            return None
        clock = read_time(meta[DATE.search(meta).end():])
        if clock is None:
            when = re.search(r"START-END(?: TIME)?\s*\|\s*([^|]+)", excerpt)
            clock = read_time(when.group(1)) if when else None
        if clock is None:
            return None
        return day.replace(hour=clock[0], minute=clock[1])

    @staticmethod
    def _where(excerpt: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """(venue, street, city, zip) from "WHERE | Aeronaut, 14 Tyler St, Somerville, MA 02143"."""
        m = WHERE.search(excerpt or "")
        if not m:
            return None, None, None, None
        where = m["where"].strip(" ,")
        address = ADDRESS.match(where)
        if not address or address["state"] != "MA":
            return (where.split(",")[0].strip() or None), None, None, None
        return (address["venue"].strip(), address["street"].strip(),
                address["city"].strip(), address["zip"])
